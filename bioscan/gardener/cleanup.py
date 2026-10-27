"""Cleanup plans (契约修订 5E, PRD §11, ADR 0003): the rejected assets in a scope with every file of each group
(registered files plus the sidecars next to them), what each costs in bytes and whether it is a species' last
photo. Running a plan re-checks every item against the files on disk and moves them to the macOS Trash; it
never deletes anything (the person empties the Trash). Files of an asset that is no longer rejected, or whose
files changed, are never touched; an offline file is skipped, never counted as deleted."""
from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from bioscan.gardener import store as st
from bioscan.gardener.handoff import uses_of
from bioscan.gardener.store import dumps, loads, now

SIDECARS = (".xmp", ".dop", ".pp3")    # Lightroom/ACR, DxO, RawTherapee edits next to the original
TRASH_NOTE = ("移到废纸篓的文件仍占用磁盘空间，可在废纸篓中恢复；在 Finder 中清空废纸篓后才真正释放。"
              "Gardener 不做永久删除。")


def trash(path: str) -> None:
    """Move one file to the macOS Trash (Finder's "Move to Trash"; /usr/bin/trash on macOS 15+, else Finder via
    AppleScript). Raises OSError when it did not happen."""
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
    assets) is left out. A missing registered file has `exists: False`."""
    reg = c.execute("SELECT path, role FROM asset_files WHERE asset_id = ? ORDER BY path", (asset_id,)).fetchall()
    out, seen = [], set()
    for f in reg:
        st_ = os.stat(f["path"]) if os.path.isfile(f["path"]) else None
        out.append({"path": f["path"], "role": f["role"], "exists": st_ is not None,
                    "size": st_.st_size if st_ else 0, "mtime_ns": st_.st_mtime_ns if st_ else 0})
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
                out.append({"path": str(p), "role": "sidecar", "exists": True, "size": s.st_size,
                            "mtime_ns": s.st_mtime_ns})
    return out


def _like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _impact(c: Any, ready: set[str]) -> dict[str, list[dict[str, Any]]]:
    """asset id -> the species (current species-level identifications of representatives) it shows, each with
    the photos of that species left outside the plan; `last_photo` when none are."""
    from bioscan.gardener import taxa

    members: dict[str, set[str]] = {}
    names: dict[str, tuple[str, str | None]] = {}
    for r in c.execute("SELECT ci.concept, ci.asset_id, ci.taxon, ci.common FROM current_ids ci JOIN assets a ON "
                       "a.id = ci.asset_id WHERE ci.level = 'species' AND ci.concept IS NOT NULL AND a.version_of IS NULL"):
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


def create(c: Any, ids: list[str], *, scope: dict[str, Any], actor: str) -> str:
    """A plan over the rejected assets among `ids`."""
    use = uses_of(c, ids)
    rejected = [a for a in ids if use[a] == "reject"]
    pid, t = st.new_id("p"), now()
    items, rows = [], []
    for aid in rejected:
        a = c.execute("SELECT primary_path FROM assets WHERE id = ?", (aid,)).fetchone()
        files = _files(c, aid)
        state = "ready" if files and all(f["exists"] for f in files) else "offline"
        versions = [{"id": v[0], "path": v[1]} for v in c.execute(
            "SELECT id, primary_path FROM assets WHERE version_of = ? ORDER BY primary_path", (aid,))]
        items.append({"asset_id": aid, "name": Path(a["primary_path"]).name, "state": state,
                      "bytes": sum(f["size"] for f in files) if state == "ready" else 0, "versions": versions,
                      "files": [{k: f[k] for k in ("path", "role", "size", "exists")} for f in files]})
        if state == "ready":
            rows += [(pid, aid, f["path"], f["role"], f["size"], f["mtime_ns"]) for f in files]
    impact = _impact(c, {i["asset_id"] for i in items if i["state"] == "ready"})
    for i in items:
        i["species"] = impact.get(i["asset_id"], [])
    items.sort(key=lambda i: (not any(s["last_photo"] for s in i["species"]), i["state"] != "ready", i["name"]))
    last = {s["concept"]: s for i in items for s in i["species"] if s["last_photo"]}
    ready = [i for i in items if i["state"] == "ready"]
    summary = {"in_scope": len(ids), "rejected": len(rejected), "ready": len(ready),
               "offline": len(items) - len(ready), "files": len(rows), "bytes": sum(i["bytes"] for i in ready),
               "last_species": sorted(last.values(), key=lambda s: s["taxon"] or s["concept"])}
    c.execute("INSERT INTO cleanup_plans (id, scope, plan, status, actor, created_at) VALUES (?, ?, ?, 'planned', ?, ?)",
              (pid, dumps(scope), dumps({"items": items, "summary": summary}), actor, t))
    c.executemany("INSERT INTO cleanup_files (plan_id, asset_id, path, role, size, mtime_ns) VALUES (?, ?, ?, ?, ?, ?)",
                  rows)
    return pid


def recheck(c: Any, pid: str) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    """(asset id -> its files to move now, [{asset_id, reason}] skipped). An asset goes only as a whole group:
    still rejected, the same files (none added, none gone), each with the planned size and mtime. Files already
    moved by an earlier run of this plan are not moved again."""
    rows = [dict(r) for r in c.execute("SELECT * FROM cleanup_files WHERE plan_id = ? ORDER BY asset_id, path", (pid,))]
    by: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by.setdefault(r["asset_id"], []).append(r)
    use = uses_of(c, list(by))
    go, skipped = {}, []
    for aid, files in by.items():
        todo = [f for f in files if f["result"] != "trashed"]
        if not todo:
            continue
        if use[aid] != "reject":
            skipped.append({"asset_id": aid, "reason": "not_rejected"})
            continue
        now_files = {f["path"]: f for f in _files(c, aid) if f["exists"]}
        if any(not os.path.lexists(f["path"]) for f in todo):
            skipped.append({"asset_id": aid, "reason": "offline"})
            continue
        planned = {f["path"] for f in files}
        if set(now_files) - planned or any((now_files[f["path"]]["size"], now_files[f["path"]]["mtime_ns"])
                                           != (f["size"], f["mtime_ns"]) for f in todo):
            skipped.append({"asset_id": aid, "reason": "changed"})
            continue
        go[aid] = todo
    return go, skipped


def run(store: st.Store, pid: str, trash_fn: Callable[[str], None]) -> dict[str, Any]:
    """Re-check, then move each file to the Trash outside the write lock; each file's result is committed as it
    happens, so a crash leaves a true record."""
    with store.tx() as c:
        if not c.execute("SELECT 1 FROM cleanup_plans WHERE id = ?", (pid,)).fetchone():
            raise st.NotFound(f"no cleanup plan {pid}")
        go, skipped = recheck(c, pid)
        t = now()
        for s in skipped:
            c.execute("UPDATE cleanup_files SET result = 'skipped', reason = ?, at = ? WHERE plan_id = ? AND "
                      "asset_id = ? AND coalesce(result, '') != 'trashed'", (s["reason"], t, pid, s["asset_id"]))
    failed = []
    for aid, files in go.items():
        for f in files:
            try:
                trash_fn(f["path"])
                result, reason = "trashed", None
            except Exception as exc:  # noqa: BLE001 - every failure is reported per file
                result, reason = "failed", str(exc) or type(exc).__name__
                failed.append({"asset_id": aid, "path": f["path"], "error": reason})
            with store.tx() as c:
                t = now()
                c.execute("UPDATE cleanup_files SET result = ?, reason = ?, at = ? WHERE plan_id = ? AND path = ?",
                          (result, reason, t, pid, f["path"]))
                if result == "trashed":
                    c.execute("UPDATE asset_files SET online = 0 WHERE path = ?", (f["path"],))
                    meta = loads(c.execute("SELECT meta FROM assets WHERE id = ?", (aid,)).fetchone()[0], {})
                    meta["trashed"] = {"at": t, "plan": pid}
                    online = c.execute("SELECT 1 FROM asset_files WHERE asset_id = ? AND online = 1", (aid,)).fetchone()
                    c.execute("UPDATE assets SET meta = ?, online = ?, updated_at = ? WHERE id = ?",
                              (dumps(meta), 1 if online else 0, t, aid))
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
            f["result"], f["reason"] = (r["result"], r["reason"]) if r else (None, None)
    return {"id": p["id"], "scope": loads(p["scope"]), "status": p["status"], "actor": p["actor"],
            "created_at": p["created_at"], "ran_at": p["ran_at"], "report": loads(p["report"]), **plan,
            "note": TRASH_NOTE}
