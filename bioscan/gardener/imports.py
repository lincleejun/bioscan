"""Import (PRD §06 正式导入, §09; docs/gardener/contract.md 契约修订 5D). After Review, `build` freezes a plan:
which assets are copied (by their use), every file of each asset group (RAW, same-shot JPEG, the sidecars next
to them), where each file goes and why (copy, identical, reuse, duplicate). `Runner` executes plans in its own
thread, so imports never wait behind a scan and never block review: each file is copied with export._copy
(sha256-verified, never overwriting), its size checked, and committed at once, so a rerun or a restart goes on
with what is left and never copies a verified file again. Sources are only read."""
from __future__ import annotations

import logging
import os
import re
import shutil
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from bioscan.gardener import scan
from bioscan.gardener import store as st
from bioscan.gardener.export import _copy, _inside, _make_dirs, _same, _sha, safe_name
from bioscan.gardener.store import Conflict, Error, NotFound, dumps, loads, now

log = logging.getLogger("bioscan.gardener")
NO_PLACE = "未标注地点"
MAX_SUFFIX = 1000
LIST_MAX = 200
PART = re.compile(r"^\..+\.\d+\.[0-9a-f]{8}\.gardener-part$")     # export._copy's temporary files


# ---- uses: the one place that reads the review decision ----------------------------------------------------
# The review-flow unit replaces Flag pick/reject with four uses; switch `uses_of` and `decided_since` to its field.
USES = ("record", "edit", "pending", "reject")


def uses_of(c: Any, asset_ids: list[str]) -> dict[str, str]:
    """Each asset's use (contract revision 5B, marks.use): record | edit | pending | reject; no mark = pending."""
    out = dict.fromkeys(asset_ids, "pending")
    for part in st.chunks(asset_ids):
        for r in c.execute(f"SELECT asset_id, use FROM marks WHERE asset_id IN ({st.marks_in(part)})", part):
            out[r["asset_id"]] = r["use"] or "pending"
    return out


def decided_since(c: Any, task_id: str, t: str) -> bool:
    """Whether a decision on one of the task's assets changed after `t` (a plan made before is then stale)."""
    return c.execute("SELECT 1 FROM mark_history h JOIN task_assets ta ON ta.asset_id = h.asset_id "
                     "WHERE ta.task_id = ? AND h.at > ? LIMIT 1", (task_id, t)).fetchone() is not None


# ---- planning -----------------------------------------------------------------------------------------------
def folder_name(days: list[str], place: str | None) -> str:
    """YYYY-MM-DD_地点; a batch over several days YYYY-MM-DD~YYYY-MM-DD_地点; no place: 未标注地点."""
    days = sorted(set(days)) or [datetime.now().date().isoformat()]
    span = days[0] if len(days) == 1 else f"{days[0]}~{days[-1]}"
    return safe_name(f"{span}_{(place or '').strip() or NO_PLACE}")


def renamed(name: str, stem: str, suffix: str) -> str:
    """`name` with `suffix` after the group's stem, so the group stays one: IMG_1.ARW → IMG_1_B3.ARW,
    IMG_1.ARW.xmp → IMG_1_B3.ARW.xmp."""
    n = len(stem) if name.lower().startswith(stem.lower()) else len(name.split(".", 1)[0])
    return name[:n] + suffix + name[n:]


def check_dest(dest_root: str, protected: list[Path]) -> Path:
    """The real destination root; Error unless absolute and outside every source."""
    if not isinstance(dest_root, str) or not os.path.isabs(os.path.expanduser(dest_root)):
        raise Error("dest_root must be an absolute path")
    dest = Path(os.path.realpath(os.path.expanduser(dest_root)))
    for p in protected:
        if dest.is_relative_to(os.path.realpath(p)):
            raise Error(f"导入目标位于来源目录之内，Gardener 不在来源中写入：{p}")
    return dest


def free_bytes(dest: Path) -> int:
    p = dest
    while not p.exists() and p != p.parent:
        p = p.parent
    return shutil.disk_usage(p).free


def _day(taken_at: str | None, mtime_ns: int) -> str:
    if taken_at and re.match(r"\d{4}-\d{2}-\d{2}", taken_at):
        return taken_at[:10]
    return datetime.fromtimestamp(mtime_ns / 1e9).date().isoformat()


def _known(c: Any, f: dict[str, Any]) -> tuple[str, str] | None:
    """(action, existing copy) when this file needs no copy: imported before from the same, unchanged
    source (reuse), or the same content imported from another file (duplicate: size + quick hash, then sha256).
    ponytail: per file, so a group with one new sidecar after an earlier batch spans two folders; keep it in the
    earlier batch's folder when that shows up."""
    for m in c.execute("SELECT * FROM managed_files WHERE source_path = ? ORDER BY imported_at", (f["src"],)):
        if ((m["size"], m["source_mtime_ns"]) == (f["size"], f["mtime_ns"]) and os.path.isfile(m["path"])
                and os.path.getsize(m["path"]) == m["size"]):
            return "reuse", m["path"]
    if f["quick_hash"] is None:
        return None
    sha = None
    for m in c.execute("SELECT * FROM managed_files WHERE size = ? AND quick_hash = ? AND source_path != ?",
                       (f["size"], f["quick_hash"], f["src"])):
        if os.path.isfile(m["path"]) and os.path.getsize(m["path"]) == m["size"]:
            sha = sha or _sha(f["src"])
            if sha == m["sha256"]:
                return "duplicate", m["path"]
    return None


def build(store: st.Store, task_id: str, dest_root: str, place: str | None, include_pending: bool,
          protected: list[Path]) -> str:
    """A new plan for the task's assets (representative versions only); returns its id. Copies record + edit
    (+ pending when `include_pending`), never reject. Names are fixed here: a group whose name is taken by
    other content in the destination folder (or by another group of this plan) gets the batch suffix _B<seq>
    on every file, so RAW, JPEG and sidecars keep one stem; nothing is ever overwritten."""
    dest = check_dest(dest_root, protected)
    c = store.conn()
    rows = store.all("SELECT a.id, a.primary_path, a.taken_at, a.version_of FROM task_assets ta "
                     "JOIN assets a ON a.id = ta.asset_id WHERE ta.task_id = ? "
                     "AND json_extract(a.meta, '$.merged_into') IS NULL ORDER BY ta.ord", (task_id,))
    in_task = {r["id"] for r in rows}             # per-task representatives (query.IN_TASK_REP, #102)
    reps = [r for r in rows if r["version_of"] is None or r["version_of"] not in in_task]
    uses = uses_of(c, [r["id"] for r in reps])
    take = {"record", "edit"} | ({"pending"} if include_pending else set())
    chosen = [r for r in reps if uses[r["id"]] in take]
    files: dict[str, list[dict[str, Any]]] = {}
    for part in st.chunks([r["id"] for r in chosen]):
        for f in store.all(f"SELECT * FROM asset_files WHERE asset_id IN ({st.marks_in(part)}) ORDER BY path", part):
            files.setdefault(f["asset_id"], []).append(
                {"src": f["path"], "role": f["role"], "size": f["size"], "mtime_ns": f["mtime_ns"],
                 "quick_hash": f["quick_hash"], "online": bool(f["online"])})
    side = scan.sidecars([f["src"] for fs in files.values() for f in fs])
    seen = {f["src"] for fs in files.values() for f in fs}
    for fs in files.values():
        for f in list(fs):
            for s in side.get(f["src"], []):
                if s not in seen:
                    seen.add(s)
                    stt = os.stat(s)
                    fs.append({"src": s, "role": "sidecar", "size": stt.st_size, "mtime_ns": stt.st_mtime_ns,
                               "quick_hash": None, "online": True})
    days = [_day(r["taken_at"], files[r["id"]][0]["mtime_ns"]) for r in chosen if files.get(r["id"])]
    folder_rel = folder_name(days, place)
    folder = dest / folder_rel
    seq = (store.one("SELECT coalesce(max(seq), 0) + 1 n FROM import_plans") or {"n": 1})["n"]
    try:
        on_disk = {n.lower(): n for n in os.listdir(folder)}
    except OSError:
        on_disk = {}
    claimed: set[str] = set()
    items: list[tuple[Any, ...]] = []
    for r in chosen:
        todo = []
        for f in files.get(r["id"], []):
            known = _known(c, f) if os.path.isfile(f["src"]) else None
            if known:
                items.append((r["id"], f, known[1], known[0]))
            else:
                todo.append(f)
        stem = Path(r["primary_path"]).stem
        for k in range(MAX_SUFFIX):
            suffix = "" if k == 0 else f"_B{seq}" if k == 1 else f"_B{seq}-{k}"
            placed = []
            for f in todo:
                name = renamed(os.path.basename(f["src"]), stem, suffix) if suffix else os.path.basename(f["src"])
                low = name.lower()
                if low in claimed:
                    break
                if low in on_disk:
                    if not (os.path.isfile(f["src"]) and _same(folder / on_disk[low], f["src"])):
                        break
                    placed.append((f, folder / on_disk[low], "identical"))
                else:
                    placed.append((f, folder / name, "copy"))
            if len(placed) == len(todo):
                break
        else:
            raise Error(f"找不到可用的文件名：{r['primary_path']}")
        for f, dst, action in placed:
            claimed.add(dst.name.lower())
            items.append((r["id"], f, str(dst), action))
    by_use = {u: sum(uses[r["id"]] == u for r in reps) for u in USES}
    summary = {
        "uses": by_use | {"total": len(reps)},
        "not_copied": {"reject": by_use["reject"], "pending": 0 if include_pending else by_use["pending"],
                       "versions": len(rows) - len(reps)},
        "unfinished": {"offline": sum(1 for fs in (files.get(r["id"], []) for r in chosen)
                                      if any(not f["online"] or not os.path.isfile(f["src"]) for f in fs)),
                       "analysis_pending": _analysis_pending(store, [r["id"] for r in chosen]),
                       "scan_active": bool(store.one("SELECT 1 FROM runs WHERE task_id = ? AND status IN "
                                                     "('queued', 'running', 'paused')", (task_id,)))}}
    pid, t = st.new_id("ip"), now()
    with store.tx() as w:
        w.execute("INSERT INTO import_plans (id, task_id, seq, status, dest_root, folder, place, include_pending, "
                  "summary, created_at) VALUES (?, ?, ?, 'planned', ?, ?, ?, ?, ?, ?)",
                  (pid, task_id, seq, str(dest), folder_rel, place or None, int(include_pending), dumps(summary), t))
        w.executemany("INSERT INTO import_items (plan_id, asset_id, src, dst, role, size, mtime_ns, quick_hash, action, "
                      "state) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')",
                      [(pid, aid, f["src"], dst, f["role"], f["size"], f["mtime_ns"], f["quick_hash"], action)
                       for aid, f, dst, action in items])
    return pid


def _analysis_pending(store: st.Store, ids: list[str]) -> int:
    n = 0
    for part in st.chunks(ids):
        n += store.one(f"SELECT count(DISTINCT asset_id) n FROM asset_caps WHERE asset_id IN ({st.marks_in(part)}) "
                       "AND state IN ('pending', 'running')", part)["n"]
    return n


# ---- the plan as the API shows it ---------------------------------------------------------------------------
def plan_json(store: st.Store, pid: str, species: bool = True) -> dict[str, Any]:
    """The plan; `species` adds the completion page's species stats (契约修订 7B), left out of lists."""
    p = store.one("SELECT * FROM import_plans WHERE id = ?", (pid,))
    if p is None:
        raise NotFound(f"no import plan {pid}")
    items = store.all("SELECT * FROM import_items WHERE plan_id = ? ORDER BY rowid", (pid,))
    s = loads(p["summary"], {})
    assets: dict[str, list[str]] = {}
    for i in items:
        assets.setdefault(i["asset_id"], []).append(i["state"])
    done = [i for i in items if i["state"] == "done"]
    to_copy = [i for i in items if i["action"] == "copy"]
    dups = [i for i in items if i["action"] != "copy"]
    ren = [i for i in items if os.path.basename(i["dst"]) != os.path.basename(i["src"]) and i["action"] == "copy"]
    needed = sum(i["size"] for i in to_copy if i["state"] != "done")
    dest = Path(p["dest_root"])
    free = free_bytes(dest)
    return {
        "id": p["id"], "task_id": p["task_id"], "seq": p["seq"], "status": p["status"], "dest_root": p["dest_root"],
        "folder": p["folder"], "dest_dir": str(dest / p["folder"]), "place": p["place"],
        "include_pending": bool(p["include_pending"]), "created_at": p["created_at"], "started_at": p["started_at"],
        "finished_at": p["finished_at"], "error": p["error"], "interruptions": p["interruptions"],
        "stale": p["status"] == "planned" and decided_since(store.conn(), p["task_id"], p["created_at"]),
        "uses": s.get("uses", {}), "not_copied": s.get("not_copied", {}), "unfinished": s.get("unfinished", {}),
        "assets": len(assets), "files": len(items), "bytes": sum(i["size"] for i in items),
        "sidecars": sum(i["role"] == "sidecar" for i in items),
        "copy_files": len(to_copy), "copy_bytes": sum(i["size"] for i in to_copy),
        "needed_bytes": needed, "free_bytes": free, "enough_space": needed <= free,
        "duplicates": {"identical": sum(i["action"] == "identical" for i in dups),
                       "reuse": sum(i["action"] == "reuse" for i in dups),
                       "duplicate": sum(i["action"] == "duplicate" for i in dups),
                       "items": [{"asset_id": i["asset_id"], "path": i["src"], "existing": i["dst"],
                                  "kind": i["action"]} for i in dups[:LIST_MAX]]},
        "renamed": {"files": len(ren), "items": [{"asset_id": i["asset_id"], "path": i["src"], "to": i["dst"]}
                                                 for i in ren[:LIST_MAX]]},
        "progress": {"files_total": len(items), "files_done": len(done),
                     "files_failed": sum(i["state"] == "failed" for i in items),
                     "bytes_total": sum(i["size"] for i in items), "bytes_done": sum(i["size"] for i in done),
                     "assets_total": len(assets), "assets_done": sum(all(x == "done" for x in v) for v in assets.values()),
                     "assets_failed": sum("failed" in v for v in assets.values())},
        "failed": [{"asset_id": i["asset_id"], "path": i["src"], "error": i["error"]}
                   for i in items if i["state"] == "failed"][:LIST_MAX],
        "collection_id": p["collection_id"]} | ({"species": species_stats(store, pid)} if species else {})


def plans(store: st.Store, task_id: str | None) -> list[dict[str, Any]]:
    rows = store.all("SELECT id FROM import_plans WHERE ? IS NULL OR task_id = ? ORDER BY seq DESC", (task_id, task_id))
    return [plan_json(store, r["id"], species=False) for r in rows]


# ---- 契约修订 7B: species stats of the completion page (PRD §09) ---------------------------------------------
def species_stats(store: st.Store, pid: str) -> dict[str, Any]:
    """What this batch does to the species library, live from current identifications (a confirmation after
    planning shows at once). The batch = the task's representatives, rejects included; the library = every
    representative not in the Trash (the species cards' rule). `new`: species a person confirmed here with no
    confirmed photo outside this task; `added`: confirmed here and elsewhere too; `to_confirm`: species only the
    model names on photos here; `sole`: photos that are a species' only photo in the library (as the card's
    "仅 1 张" and cleanup's last photo count it), `imported` false = left on the card, only in the source."""
    from bioscan.gardener import taxa
    from bioscan.gardener.identities import GONE
    from bioscan.gardener.query import IN_TASK_REP

    c = store.conn()
    p = c.execute("SELECT task_id FROM import_plans WHERE id = ?", (pid,)).fetchone()
    importing = {r[0] for r in c.execute("SELECT DISTINCT asset_id FROM import_items WHERE plan_id = ?", (pid,))}
    task_ids = {r[0] for r in c.execute("SELECT asset_id FROM task_assets WHERE task_id = ?", (p["task_id"],))}
    batch: dict[str, dict[str, bool]] = {}           # concept -> asset id -> confirmed by a person
    names: dict[str, tuple[str, str]] = {}
    for r in c.execute(f"SELECT ci.asset_id, ci.concept, ci.source, ci.taxon, ci.common FROM current_ids ci JOIN assets a "
                       f"ON a.id = ci.asset_id WHERE ci.level = 'species' AND ci.concept IS NOT NULL AND {IN_TASK_REP} "
                       f"AND a.id IN (SELECT asset_id FROM task_assets WHERE task_id = ?) "
                       f"AND json_extract(a.meta, '$.merged_into') IS NULL {GONE}", (p["task_id"], p["task_id"])):
        k = batch.setdefault(r["concept"], {})
        k[r["asset_id"]] = k.get(r["asset_id"], False) or r["source"] == "user"
        names.setdefault(r["concept"], (r["taxon"], r["common"]))
    library: dict[str, dict[str, bool]] = {}
    for part in st.chunks(list(batch)):
        for r in c.execute(f"SELECT ci.asset_id, ci.concept, ci.source FROM current_ids ci JOIN assets a ON a.id = ci.asset_id "
                           f"WHERE ci.level = 'species' AND ci.concept IN ({st.marks_in(part)}) AND a.version_of IS NULL "
                           f"{GONE}", part):
            k = library.setdefault(r["concept"], {})
            k[r["asset_id"]] = k.get(r["asset_id"], False) or r["source"] == "user"
    shown = taxa.display(c, list(batch))
    uses = uses_of(c, sorted({a for k in batch.values() for a in k}))
    paths = {r[0]: r[1] for part in st.chunks(list(uses))
             for r in c.execute(f"SELECT id, primary_path FROM assets WHERE id IN ({st.marks_in(part)})", part)}

    def name(k: str) -> dict[str, Any]:
        n = shown.get(k) or {}
        return {"concept": k, "taxon": n.get("scientific") or names[k][0], "common": n.get("common") or names[k][1],
                "zh": n.get("zh")}

    out: dict[str, list[dict[str, Any]]] = {"new": [], "added": [], "to_confirm": [], "sole": []}
    for k, here in batch.items():
        mine = [a for a, ok in here.items() if ok]
        before = sum(ok for a, ok in library.get(k, {}).items() if a not in task_ids)
        if mine:
            out["added" if before else "new"].append(name(k) | {"photos": len(mine), "importing": len(importing & set(mine)),
                                                                 "before": before})
        if len(mine) < len(here):
            ai = [a for a, ok in here.items() if not ok]
            out["to_confirm"].append(name(k) | {"photos": len(ai), "importing": len(importing & set(ai))})
        everywhere = library.get(k, {})
        if len(everywhere) == 1 and next(iter(everywhere)) in here:
            aid = next(iter(everywhere))
            out["sole"].append(name(k) | {"asset_id": aid, "name": Path(paths.get(aid, "")).name, "use": uses[aid],
                                          "confirmed": everywhere[aid], "imported": aid in importing})
    for v in out.values():
        v.sort(key=lambda x: (x.get("imported", True), x["taxon"] or x["concept"]))
    counts = {k: len(v) for k, v in out.items()} | {"sole_left": sum(not x["imported"] for x in out["sole"])}
    return {k: v[:LIST_MAX] for k, v in out.items()} | {"counts": counts}
# ---- end 契约修订 7B species stats ----


# ---- 契约修订 7B: an import batch becomes a manual collection (PRD §09 后续整理) --------------------------------
def batch_collection(store: st.Store, pid: str, *, actor: str, recreate: bool = True) -> str | None:
    """The batch's manual collection, named after its folder, holding every asset whose files are all imported.
    Made when a run finishes (and on request); a later run of the same plan adds what it finished to the same
    collection. Members join as `auto`, so one a person removed stays removed. A collection the person deleted
    is made again only on request (`recreate`). origin=user: regrouping never touches it."""
    with store.tx() as c:
        p = c.execute("SELECT * FROM import_plans WHERE id = ?", (pid,)).fetchone()
        if p is None:
            raise NotFound(f"no import plan {pid}")
        done = [r[0] for r in c.execute("SELECT asset_id FROM import_items WHERE plan_id = ? GROUP BY asset_id "
                                        "HAVING sum(state != 'done') = 0 ORDER BY min(rowid)", (pid,))]
        col = c.execute("SELECT id, deleted_at FROM collections WHERE id = ?", (p["collection_id"],)).fetchone() \
            if p["collection_id"] else None
        if col is not None and col["deleted_at"] is None:
            have = {r[0] for r in c.execute("SELECT asset_id FROM collection_members WHERE collection_id = ?",
                                            (col["id"],))}
            new = [a for a in done if a not in have]       # a removed member keeps its tombstone row: not re-added
            if st.add_members(c, col["id"], new, "auto"):
                st.collection_history(c, col["id"], "members", {"add": new, "remove": [], "import_plan": pid}, actor)
            return col["id"]
        if (col is not None and not recreate) or not done:
            return None
        cid = st.create_collection(c, name=p["folder"], task_id=p["task_id"], asset_ids=done, actor=actor,
                                   description=f"导入批次 {p['seq']} · {Path(p['dest_root']) / p['folder']}")
        c.execute("UPDATE import_plans SET collection_id = ? WHERE id = ?", (cid, pid))
        t = now()
        for part in st.chunks(done):
            c.execute(f"UPDATE assets SET updated_at = ? WHERE id IN ({st.marks_in(part)})", [t, *part])
        return cid


def managed(store: st.Store, asset_id: str) -> list[dict[str, Any]]:
    """The verified managed copies of an asset (AssetDetail.managed)."""
    return [{"path": m["path"], "source_path": m["source_path"], "role": m["role"], "size": m["size"],
             "sha256": m["sha256"], "plan_id": m["plan_id"], "imported_at": m["imported_at"]}
            for m in store.all("SELECT * FROM managed_files WHERE asset_id = ? ORDER BY path", (asset_id,))]


def start(store: st.Store, runner: Runner, pid: str, protected: list[Path]) -> dict[str, Any]:
    """Queue a plan (again). A queued, running or completed plan is left as it is; otherwise failed files go back
    to pending. 409 when what is left to copy does not fit on the destination."""
    p = plan_json(store, pid)
    if p["status"] in ("queued", "running", "completed"):
        return p
    check_dest(p["dest_root"], protected)
    if not p["enough_space"]:
        raise Conflict("目标磁盘空间不足", needed_bytes=p["needed_bytes"], free_bytes=p["free_bytes"])
    with store.tx() as c:
        c.execute("UPDATE import_items SET state = 'pending', error = NULL WHERE plan_id = ? AND state = 'failed'", (pid,))
        c.execute("UPDATE import_plans SET status = 'queued', error = NULL, finished_at = NULL WHERE id = ?", (pid,))
    runner.wake()
    return plan_json(store, pid)


# ---- execution ----------------------------------------------------------------------------------------------
class Runner:
    """One thread that executes queued plans in order. `close()` stops it at the next file boundary; a plan
    left running (stopped, or the process died) is queued again by `recover()` on the next start."""

    def __init__(self, store: st.Store, protected: Callable[[], list[Path]]) -> None:
        self.store, self.protected = store, protected
        self._stop, self._wake = threading.Event(), threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        self.recover()
        self.thread = threading.Thread(target=self.loop, name="gardener-import", daemon=True)
        self.thread.start()

    def close(self) -> None:
        self._stop.set()
        self._wake.set()
        if self.thread is not None:
            self.thread.join(60)

    def wake(self) -> None:
        self._wake.set()

    def recover(self) -> None:
        with self.store.tx() as c:
            c.execute("UPDATE import_plans SET status = 'queued', interruptions = interruptions + 1 "
                      "WHERE status = 'running'")
            c.execute("UPDATE import_items SET state = 'pending' WHERE state = 'copying'")

    def claim(self) -> str | None:
        with self.store.tx() as c:
            r = c.execute("SELECT id FROM import_plans WHERE status = 'queued' ORDER BY seq LIMIT 1").fetchone()
            if r is None:
                return None
            c.execute("UPDATE import_plans SET status = 'running', started_at = coalesce(started_at, ?) WHERE id = ?",
                      (now(), r["id"]))
        return r["id"]

    def loop(self) -> None:
        while not self._stop.is_set():
            pid = None
            try:
                pid = self.claim()
                if pid is None:
                    self._wake.wait(0.5)
                    self._wake.clear()
                    continue
                self.execute(pid)
            except Exception as exc:  # noqa: BLE001 - one bad plan never stops the runner
                log.exception("import runner error")
                if pid is not None:
                    with self.store.tx() as c:
                        c.execute("UPDATE import_plans SET status = 'failed', error = ?, finished_at = ? WHERE id = ?",
                                  (f"{type(exc).__name__}: {exc}", now(), pid))
                self._stop.wait(0.5)

    def execute(self, pid: str) -> None:
        p = self.store.one("SELECT * FROM import_plans WHERE id = ?", (pid,))
        protected = [Path(os.path.realpath(x)) for x in self.protected()]
        dest = check_dest(p["dest_root"], protected)
        dest.mkdir(parents=True, exist_ok=True)
        folder = _make_dirs(dest, [p["folder"]], protected)
        for n in os.listdir(folder):                 # temporary files a crash left behind (ours by their name)
            if PART.match(n):
                try:
                    os.unlink(folder / n)
                except OSError:
                    pass
        items = self.store.all("SELECT * FROM import_items WHERE plan_id = ? AND state IN ('pending', 'copying') "
                               "ORDER BY rowid", (pid,))
        for i in items:
            if self._stop.is_set():
                return                               # stays running: recover() queues it on the next start
            with self.store.tx() as c:
                c.execute("UPDATE import_items SET state = 'copying' WHERE plan_id = ? AND src = ?", (pid, i["src"]))
            try:
                sha = self.place(i, folder, dest, protected)
                err = None
            except (OSError, ValueError) as exc:
                sha, err = None, str(exc) if isinstance(exc, ImportFailure) else f"{type(exc).__name__}: {exc}"
                if not isinstance(exc, ImportFailure) and not os.path.isdir(folder):    # an unplugged disk (7D)
                    err = f"目标目录不可用（磁盘已断开？）：{folder}；重新连接后再执行一次即可继续"
            with self.store.tx() as c:
                t = now()
                c.execute("UPDATE import_items SET state = ?, sha256 = ?, error = ?, done_at = ? "
                          "WHERE plan_id = ? AND src = ?", ("failed" if err else "done", sha, err, t, pid, i["src"]))
                if err is None:
                    c.execute("INSERT OR IGNORE INTO managed_files VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                              (i["asset_id"], i["dst"], i["src"], i["role"], i["size"], i["mtime_ns"], i["quick_hash"],
                               sha, pid, t))
                    c.execute("UPDATE assets SET updated_at = ? WHERE id = ?", (t, i["asset_id"]))
        k = self.store.one("SELECT coalesce(sum(state = 'done'), 0) done, coalesce(sum(state = 'failed'), 0) failed "
                           "FROM import_items WHERE plan_id = ?", (pid,))
        status = "completed" if not k["failed"] else "partial" if k["done"] else "failed"
        with self.store.tx() as c:
            c.execute("UPDATE import_plans SET status = ?, finished_at = ? WHERE id = ?", (status, now(), pid))
        try:                                          # 契约修订 7B: the batch becomes a collection
            batch_collection(self.store, pid, actor="import", recreate=False)
        except Exception:  # noqa: BLE001 - the import itself is done; the collection can be made on demand
            log.exception("import batch collection")

    def place(self, i: dict[str, Any], folder: Path, dest: Path, protected: list[Path]) -> str:
        """Put one file where the plan says; returns the sha256 of the managed copy. ImportFailure when it
        cannot be done without overwriting or when what is there is not what the plan expects."""
        src, dst = i["src"], Path(i["dst"])
        if i["action"] in ("reuse", "duplicate"):
            row = self.store.one("SELECT sha256 FROM managed_files WHERE path = ? AND size = ?", (i["dst"], i["size"]))
            if row is None or not os.path.isfile(dst) or os.path.getsize(dst) != i["size"]:
                raise ImportFailure(f"已导入的副本不见了或已变化：{dst}；请重新生成计划")
            return row["sha256"]
        try:
            size = os.stat(src).st_size
        except OSError:
            raise ImportFailure("源文件不存在或离线") from None
        if size != i["size"]:
            raise ImportFailure("源文件在生成计划后有变化；请重新生成计划")
        if os.path.lexists(dst):                     # identical by plan, or published just before a crash
            if _same(dst, src):
                return _sha(str(dst))
            raise ImportFailure(f"目标已有不同内容的文件，未覆盖：{dst}")
        # ponytail: an exFAT copy cut by a crash leaves a partial dst that is then reported, never overwritten;
        # telling our own partial file from someone else's needs a journal of started copies.
        _inside(folder, dest, protected)
        try:
            digest = _copy(src, dst)
        except FileExistsError:
            if _same(dst, src):
                return _sha(str(dst))
            raise ImportFailure(f"目标已被占用，未覆盖：{dst}") from None
        if os.path.getsize(dst) != i["size"]:
            raise ImportFailure("复制后字节数不一致")
        return digest


class ImportFailure(OSError):
    """A file the runner did not place, with the reason shown to the user."""
