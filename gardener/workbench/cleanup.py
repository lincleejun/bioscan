"""Cleanup plans (契约修订 5E, PRD §11, ADR 0003): the rejected assets in a scope with every file of each group
(registered files plus the sidecars next to them), what each costs in bytes and whether it is a species' last
photo. Running a plan re-checks every item against the files on disk and moves them to the macOS Trash; it
never deletes anything (the person empties the Trash). Files of an asset that is no longer rejected, or whose
files changed, are never touched; an offline file is skipped, never counted as deleted. A file another asset
still uses (a duplicate import shares one managed copy, 契约修订 10A) stays put, reason `shared`, unless that
asset is rejected in the same plan too."""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from gardener.workbench import store as st
from gardener.workbench.handoff import uses_of
from gardener.workbench.store import dumps, loads, now

SIDECARS = (".xmp", ".dop", ".pp3")    # Lightroom/ACR, DxO, RawTherapee edits next to the original
TRASH_NOTE = ("移到废纸篓的文件仍占用磁盘空间，可在废纸篓中恢复；在 Finder 中清空废纸篓后才真正释放。"
              "Gardener 不做永久删除。")


def trash(path: str) -> None:
    """Move one file to the macOS Trash (Finder's "Move to Trash"; /usr/bin/trash on macOS 15+, else Finder via
    AppleScript). Raises OSError when it did not happen. With GARDENER_TRASH_DIR set (the container image: there is
    no macOS Trash) the file moves under that folder instead, keeping its full path, so it can be put back by hand."""
    own = os.environ.get("GARDENER_TRASH_DIR")
    if own:
        dst = Path(own) / path.lstrip("/")
        if dst.exists():                                  # a second file from the same place: keep both
            dst = dst.with_name(f"{dst.name}.{time.time_ns()}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(path, dst)
        return
    if os.path.exists("/usr/bin/trash"):
        cmd = ["/usr/bin/trash", path]
    else:
        cmd = ["osascript", "-e", "on run argv", "-e", 'tell application "Finder" to delete (POSIX file (item 1 of argv))',
               "-e", "end run", path]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0 or os.path.lexists(path):
        raise OSError((p.stderr or p.stdout).strip()[-300:] or f"exit {p.returncode}")


def _files(c: Any, asset_id: str) -> list[dict[str, Any]]:
    """Every file of the asset as it is on disk now: registered files, then the sidecars named after one of them
    (`IMG_1.xmp`, `IMG_1.ARW.xmp`). A `<stem>.xmp` shared with another asset's file (ARW + DNG of one stem are two
    assets) is left out. A missing registered file has `exists: False`. `users`: the other assets that use the file
    (or, for a sidecar, the file it belongs to) as a source file or managed copy (10A)."""
    reg = c.execute("SELECT path, role FROM asset_files WHERE asset_id = ? UNION SELECT path, 'managed' FROM "
                    "managed_files WHERE asset_id = ? ORDER BY path", (asset_id, asset_id)).fetchall()   # + imported copies (5D)
    out, seen, users = [], set(), {}
    for f in reg:
        st_ = os.stat(f["path"]) if os.path.isfile(f["path"]) else None
        users[f["path"]] = _users(c, f["path"], asset_id)
        out.append({"path": f["path"], "role": f["role"], "exists": st_ is not None,
                    "size": st_.st_size if st_ else 0, "mtime_ns": st_.st_mtime_ns if st_ else 0,
                    "users": users[f["path"]]})
        seen.add(f["path"])
    for folder in sorted({os.path.dirname(f["path"]) for f in reg}):
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            continue
        mine = [Path(f["path"]) for f in reg if os.path.dirname(f["path"]) == folder]
        stems = {p.stem.lower() for p in mine}
        full = {p.name.lower() for p in mine}
        for n in names:
            p = Path(folder) / n
            if p.suffix.lower() not in SIDECARS or str(p) in seen or not p.is_file():
                continue
            base = p.stem.lower()
            if base in full or (base in stems and not c.execute(
                    "SELECT 1 FROM asset_files WHERE asset_id != ? AND path LIKE ? ESCAPE '\\'",
                    (asset_id, _like(os.path.join(folder, p.stem)) + ".%")).fetchone()):
                s = p.stat()
                owners = set().union(_users(c, str(p), asset_id), *(users[str(m)] for m in mine
                                                                     if base in (m.name.lower(), m.stem.lower())))
                out.append({"path": str(p), "role": "sidecar", "exists": True, "size": s.st_size,
                            "mtime_ns": s.st_mtime_ns, "users": owners})
    return out


def _users(c: Any, path: str, asset_id: str) -> set[str]:
    """The assets other than `asset_id` that list `path` as a source file or a managed copy."""
    return {r[0] for r in c.execute("SELECT asset_id FROM asset_files WHERE path = ? AND asset_id != ? UNION SELECT "
                                    "asset_id FROM managed_files WHERE path = ? AND asset_id != ?",
                                    (path, asset_id, path, asset_id))}


def _cleaning(c: Any, pid: str) -> set[str]:
    """The plan's ready assets that are still rejected now: the only other users a file may have and still move
    (an asset whose files were all listed under another one has no cleanup_files rows, so read the plan)."""
    plan = loads(c.execute("SELECT plan FROM cleanup_plans WHERE id = ?", (pid,)).fetchone()[0])
    ready = [i["asset_id"] for i in plan["items"] if i["state"] == "ready"]
    return {a for a, u in uses_of(c, ready).items() if u == "reject"}


def _like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _impact(c: Any, ready: set[str]) -> dict[str, list[dict[str, Any]]]:
    """asset id -> the species (current species-level identifications of representatives) it shows, each with
    the photos of that species left outside the plan; `last_photo` when none are."""
    from gardener.workbench import taxa

    members: dict[str, set[str]] = {}
    names: dict[str, tuple[str, str | None]] = {}
    from gardener.workbench.identities import GONE  # photos already in the Trash are not "left" (same as cards)

    for r in c.execute("SELECT ci.concept, ci.asset_id, ci.taxon, ci.common FROM current_ids ci JOIN assets a ON "
                       "a.id = ci.asset_id WHERE ci.level = 'species' AND ci.concept IS NOT NULL AND a.version_of IS NULL "
                       + GONE):
        members.setdefault(r["concept"], set()).add(r["asset_id"])
        names.setdefault(r["concept"], (r["taxon"], r["common"]))
    touched = [k for k, ms in members.items() if ms & ready]
    shown = taxa.display(c, touched)
    out: dict[str, list[dict[str, Any]]] = {}
    for k in touched:
        n = shown.get(k) or {}
        left = len(members[k] - ready)
        for aid in sorted(members[k] & ready):
            out.setdefault(aid, []).append({"concept": k, "taxon": n.get("scientific") or names[k][0],
                                            "common": n.get("common") or names[k][1], "zh": n.get("zh"),
                                            "photos": len(members[k]), "photos_left": left, "last_photo": left == 0})
    return out


def create(c: Any, ids: list[str], *, scope: dict[str, Any], actor: str, whole: bool = False) -> str:
    """A plan over the rejected assets among `ids`; with `whole` over all of them (the bin: the caller marks what only
    the AI rejected when the person confirms the run)."""
    use = uses_of(c, ids)
    rejected = [a for a in ids if whole or use[a] == "reject"]
    pid, t = st.new_id("p"), now()
    found = {aid: _files(c, aid) for aid in rejected}
    ready = {aid for aid, files in found.items() if files and all(f["exists"] for f in files)}
    items, rows, claimed = [], [], set()
    for aid, files in found.items():
        a = c.execute("SELECT primary_path FROM assets WHERE id = ?", (aid,)).fetchone()
        state = "ready" if aid in ready else "offline"
        files = [f for f in files if f["path"] not in claimed]   # one row per path: a file both rejected duplicates
        for f in files:                                          # use is listed (and moved) with the first of them
            if f["users"] - ready:
                f["result"], f["reason"] = "skipped", "shared"   # another asset that stays still uses it (10A)
        own = [f for f in files if "reason" not in f]
        versions = [{"id": v[0], "path": v[1]} for v in c.execute(
            "SELECT id, primary_path FROM assets WHERE version_of = ? ORDER BY primary_path", (aid,))]
        items.append({"asset_id": aid, "name": Path(a["primary_path"]).name, "state": state,
                      "bytes": sum(f["size"] for f in own) if state == "ready" else 0, "versions": versions,
                      "files": [{k: f[k] for k in ("path", "role", "size", "exists", "result", "reason") if k in f}
                                for f in files]})
        if state == "ready":
            rows += [(pid, aid, f["path"], f["role"], f["size"], f["mtime_ns"]) for f in own]
            claimed |= {f["path"] for f in own}
    impact = _impact(c, ready)
    for i in items:
        i["species"] = impact.get(i["asset_id"], [])
    items.sort(key=lambda i: (not any(s["last_photo"] for s in i["species"]), i["state"] != "ready", i["name"]))
    last = {s["concept"]: s for i in items for s in i["species"] if s["last_photo"]}
    summary = {"in_scope": len(ids), "rejected": len(rejected), "ready": len(ready),
               "offline": len(items) - len(ready), "files": len(rows), "bytes": sum(i["bytes"] for i in items),
               "shared": sum(f.get("reason") == "shared" for i in items if i["state"] == "ready" for f in i["files"]),
               "last_species": sorted(last.values(), key=lambda s: s["taxon"] or s["concept"])}
    c.execute("INSERT INTO cleanup_plans (id, scope, plan, status, actor, created_at) VALUES (?, ?, ?, 'planned', ?, ?)",
              (pid, dumps(scope), dumps({"items": items, "summary": summary}), actor, t))
    c.executemany("INSERT INTO cleanup_files (plan_id, asset_id, path, role, size, mtime_ns) VALUES (?, ?, ?, ?, ?, ?)",
                  rows)
    return pid


def recheck(c: Any, pid: str) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    """(asset id -> its files to move now, [{asset_id, reason}] skipped). An asset goes only as a whole group:
    still rejected, the same files (none added, none gone), each with the planned size and mtime. Files already
    moved by an earlier run of this plan are not moved again. A file another asset outside the plan's still
    rejected ones now uses stays, skipped with its `path` and reason `shared` (10A)."""
    rows = [dict(r) for r in c.execute("SELECT * FROM cleanup_files WHERE plan_id = ? ORDER BY asset_id, path", (pid,))]
    by: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by.setdefault(r["asset_id"], []).append(r)
    cleaning, planned = _cleaning(c, pid), {r["path"] for r in rows}
    go, skipped = {}, []
    for aid, files in by.items():
        todo = [f for f in files if f["result"] != "trashed"]
        if not todo:
            continue
        if aid not in cleaning:
            skipped.append({"asset_id": aid, "reason": "not_rejected"})
            continue
        now = _files(c, aid)
        shared = {f["path"] for f in now if f["users"] - cleaning}
        skipped += [{"asset_id": aid, "path": f["path"], "reason": "shared"} for f in todo if f["path"] in shared]
        todo = [f for f in todo if f["path"] not in shared]
        if not todo:
            continue
        now_files = {f["path"]: f for f in now if f["exists"] and f["path"] not in shared}
        if any(not os.path.lexists(f["path"]) for f in todo):
            skipped.append({"asset_id": aid, "reason": "offline"})
            continue
        if set(now_files) - planned or any(f["path"] not in now_files or (now_files[f["path"]]["size"],
                                           now_files[f["path"]]["mtime_ns"]) != (f["size"], f["mtime_ns"])
                                           for f in todo):
            skipped.append({"asset_id": aid, "reason": "changed"})
            continue
        go[aid] = todo
    return go, skipped


_RUNNING: set[str] = set()                       # plans being run in this process (one service owns the database)
_RUNNING_LOCK = threading.Lock()


def _still_rejected(c: Any, aid: str, files: list[dict[str, Any]]) -> str | None:
    """Why this group must not move now ("not_rejected", "changed"), or None. Checked again right before each group's move,
    because the person can keep a photo while a long run is going (ADR 0003: only what is still rejected moves)."""
    row = c.execute("SELECT use FROM marks WHERE asset_id = ?", (aid,)).fetchone()
    if row is None or row["use"] != "reject":
        return "not_rejected"
    for f in files:
        try:
            s_ = os.stat(f["path"])
        except OSError:
            return "changed"
        if (s_.st_size, s_.st_mtime_ns) != (f["size"], f["mtime_ns"]):
            return "changed"
    return None


def _skip(c: Any, pid: str, s: dict[str, Any], t: str) -> None:
    """Record a skip on the group's files not yet moved, or on one file when the skip names its `path`."""
    one = " AND path = ?" if "path" in s else ""
    c.execute("UPDATE cleanup_files SET result = 'skipped', reason = ?, at = ? WHERE plan_id = ? AND asset_id = ? AND "
              "coalesce(result, '') != 'trashed'" + one, (s["reason"], t, pid, s["asset_id"], *([s["path"]] if one else [])))


def run(store: st.Store, pid: str, trash_fn: Callable[[str], None]) -> dict[str, Any]:
    """Re-check, then move each file to the Trash outside the write lock; each file's result is committed as it
    happens, so a crash leaves a true record. One run per plan at a time (409 for a second, e.g. a double click)."""
    with _RUNNING_LOCK:
        if pid in _RUNNING:
            raise st.Conflict("这个清理计划正在执行")
        _RUNNING.add(pid)
    try:
        return _run(store, pid, trash_fn)
    finally:
        with _RUNNING_LOCK:
            _RUNNING.discard(pid)


def _record(c: Any, pid: str, aid: str, path: str, result: str, reason: str | None) -> None:
    """One file's outcome; a moved file also reads offline and marks its asset trashed."""
    t = now()
    c.execute("UPDATE cleanup_files SET result = ?, reason = ?, at = ? WHERE plan_id = ? AND path = ? "
              "AND coalesce(result, '') != 'trashed'", (result, reason, t, pid, path))
    if result == "trashed":
        c.execute("UPDATE asset_files SET online = 0 WHERE path = ?", (path,))
        meta = loads(c.execute("SELECT meta FROM assets WHERE id = ?", (aid,)).fetchone()[0], {})
        meta["trashed"] = {"at": t, "plan": pid}
        online = c.execute("SELECT 1 FROM asset_files WHERE asset_id = ? AND online = 1", (aid,)).fetchone()
        c.execute("UPDATE assets SET meta = ?, online = ?, updated_at = ? WHERE id = ?",
                  (dumps(meta), 1 if online else 0, t, aid))


def _settle(c: Any, pid: str) -> None:
    """Files an earlier run was moving when the process died: gone from their place means moved (record it), still
    there means the move never happened (try again)."""
    for r in c.execute("SELECT asset_id, path FROM cleanup_files WHERE plan_id = ? AND result = 'moving'", (pid,)).fetchall():
        if os.path.lexists(r["path"]):
            c.execute("UPDATE cleanup_files SET result = NULL, at = NULL WHERE plan_id = ? AND path = ?", (pid, r["path"]))
        else:
            _record(c, pid, r["asset_id"], r["path"], "trashed", None)


def _run(store: st.Store, pid: str, trash_fn: Callable[[str], None]) -> dict[str, Any]:
    with store.tx() as c:
        if not c.execute("SELECT 1 FROM cleanup_plans WHERE id = ?", (pid,)).fetchone():
            raise st.NotFound(f"no cleanup plan {pid}")
        _settle(c, pid)
        go, skipped = recheck(c, pid)
        t = now()
        for s in skipped:
            _skip(c, pid, s, t)
    failed = []
    for aid, files in go.items():
        with store.tx() as c:
            why = _still_rejected(c, aid, files)
            if why is not None:
                skipped.append({"asset_id": aid, "reason": why})
                _skip(c, pid, skipped[-1], now())
                continue
            cleaning = _cleaning(c, pid)              # 10A again right before the move: a new import may share a file
            for f in [f for f in files if _users(c, f["path"], aid) - cleaning]:
                skipped.append({"asset_id": aid, "path": f["path"], "reason": "shared"})
                _skip(c, pid, skipped[-1], now())
                files = [g for g in files if g is not f]
        for f in files:
            with store.tx() as c:          # the intent first: a crash between the move and its record is finished by
                c.execute("UPDATE cleanup_files SET result = 'moving', at = ? WHERE plan_id = ? AND path = ? AND "   # _settle
                          "coalesce(result, '') != 'trashed'", (now(), pid, f["path"]))
            try:
                trash_fn(f["path"])
                result, reason = "trashed", None
            except Exception as exc:  # noqa: BLE001 - every failure is reported per file
                result, reason = "failed", str(exc) or type(exc).__name__
                failed.append({"asset_id": aid, "path": f["path"], "error": reason})
            with store.tx() as c:
                _record(c, pid, aid, f["path"], result, reason)
    with store.tx() as c:
        rows = c.execute("SELECT result, size FROM cleanup_files WHERE plan_id = ?", (pid,)).fetchall()
        moved = [r for r in rows if r["result"] == "trashed"]
        report = {"trashed_files": len(moved), "trashed_bytes": sum(r["size"] for r in moved), "freed_bytes": 0,
                  "recoverable": True, "failed": failed, "skipped": skipped, "note": TRASH_NOTE}
        status = "ran" if all(r["result"] == "trashed" for r in rows) else "partial"
        c.execute("UPDATE cleanup_plans SET status = ?, ran_at = ?, report = ? WHERE id = ?",
                  (status, now(), dumps(report), pid))
    return report


def get(c: Any, pid: str) -> dict[str, Any]:
    p = c.execute("SELECT * FROM cleanup_plans WHERE id = ?", (pid,)).fetchone()
    if p is None:
        raise st.NotFound(f"no cleanup plan {pid}")
    plan = loads(p["plan"])
    results = {r["path"]: r for r in c.execute("SELECT path, result, reason, at FROM cleanup_files WHERE plan_id = ?",
                                               (pid,))}
    for i in plan["items"]:
        for f in i["files"]:
            r = results.get(f["path"])
            f["result"], f["reason"] = (r["result"], r["reason"]) if r else (f.get("result"), f.get("reason"))
    return {"id": p["id"], "scope": loads(p["scope"]), "status": p["status"], "actor": p["actor"],
            "created_at": p["created_at"], "ran_at": p["ran_at"], "report": loads(p["report"]), **plan,
            "note": TRASH_NOTE}
