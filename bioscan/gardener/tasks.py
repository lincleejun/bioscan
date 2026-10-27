"""Task runs (contract §5, §10): the queue, the one background worker and the run state machine.

One asyncio worker executes one task run at a time, FIFO by `queued_at`. A run's phases are
discover -> preview -> analyze -> group; every phase skips work the run (or an earlier one) already
finished, so pausing, resuming and restart recovery simply execute the run again. Heavy sync work
(discovery, metadata, previews, grouping) runs in threads; Analyzer.analyze is awaited on the loop.
The scanner functions and the Analyzer come from a `Toolkit` (unit 2's modules by default, fakes in
tests). Gardener never writes inside a source directory: previews go to the data dir's cache."""
from __future__ import annotations

import asyncio
import inspect
import logging
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from bioscan.gardener import capabilities as capmod
from bioscan.gardener import identities
from bioscan.gardener import store as st
from bioscan.gardener import versions as vers
from bioscan.gardener.store import dumps, loads, now

log = logging.getLogger("bioscan.gardener")

ACTIVE = ("queued", "running", "paused")
MODEL_CAPS = tuple(c.id for c in capmod.CAPABILITIES if c.stages or c.local)
BIOSCAN_CAPS = tuple(c.id for c in capmod.CAPABILITIES if c.stages)
VIDEO_NA = "视频首版只做元数据、封面、播放与人工标记；图像分析不适用"
# what a group kind needs switched on, any of them ((): always; "other" = videos and photos with no subject information)
GROUP_NEEDS = {"person": ("people", "content"), "animal": ("content", "species"),
               "scene": ("content",), "other": (), "place": ("place_event",)}
SUBJECT_CAPS = ("content", "species", "people")
PEOPLE_EVERY = 64            # regroup after this many people results
META_BATCH = 64
FAILED_MAX = 200


def reveal(path: str) -> None:
    """Show the file in the Finder (macOS); elsewhere the containing folder."""
    if sys.platform == "darwin":
        subprocess.run(["open", "-R", path], check=False)
    else:
        subprocess.run(["xdg-open", str(Path(path).parent)], check=False)


def source_online(path: str) -> bool:
    """A task source is a folder this process can list (scan.is_online is for files: an unmounted card
    reads offline either way)."""
    return os.path.isdir(path) and os.access(path, os.R_OK | os.X_OK)


@dataclass
class Toolkit:
    """Unit 2's module-level interfaces (contract §4), injectable for tests."""
    discover: Callable[[str], tuple[list[Any], list[Any]]]
    quick_hash: Callable[[str], str]
    is_online: Callable[[str], bool]
    read_meta: Callable[[list[str]], dict[str, dict[str, Any]]]
    make_preview: Callable[[str, str, Path, Path], Any]
    analyzer: Callable[..., Any]                 # Analyzer(engine, *, decode_workers, chunk)
    suggest: Callable[[list[dict[str, Any]]], list[Any]]
    export: Callable[..., dict[str, Any]]
    reveal: Callable[[str], None] = reveal
    faces: Callable[[], Any] | None = None       # FaceModel(): .version, .ensure(), .faces(image_path)

    @classmethod
    def default(cls) -> Toolkit:
        from bioscan.gardener import analyze, faces, grouping, preview, scan
        from bioscan.gardener import export as exporting

        return cls(discover=scan.discover, quick_hash=scan.quick_hash, is_online=scan.is_online,
                   read_meta=preview.read_meta, make_preview=preview.make_preview, analyzer=analyze.Analyzer,
                   suggest=grouping.suggest, export=exporting.export, faces=faces.FaceModel)


def preview_paths(store: st.Store, asset_id: str) -> tuple[Path, Path]:
    d = store.preview_dir / asset_id[2:4]
    return d / f"{asset_id}.thumb.jpg", d / f"{asset_id}.large.jpg"


@dataclass
class LocalResult:
    """A result of a model the worker runs itself (people); the shape of analyze.ItemResult."""
    asset_id: str
    capability: str
    status: str
    data: dict | None
    error: str | None
    version: str | None


# ---- queueing (called by the API; every write under the store's lock) ----

def queue_run(c: Any, task_id: str, kind: str, *, capabilities: list[str], run_mode: str, preset: str | None,
              sources: list[str] | None = None, asset_ids: list[str] | None = None) -> str:
    rid, t = st.new_id("r"), now()
    seq = c.execute("SELECT coalesce(max(seq), 0) + 1 FROM runs WHERE task_id = ?", (task_id,)).fetchone()[0]
    tz = c.execute("SELECT timezone FROM tasks WHERE id = ?", (task_id,)).fetchone()[0]
    config = {"capabilities": capabilities, "run_mode": run_mode, "preset": preset, "timezone": tz, "versions": {},
              "options": {}}
    c.execute("INSERT INTO runs (id, task_id, seq, kind, status, config, scope, queued_at) VALUES (?, ?, ?, ?, 'queued', "
              "?, ?, ?)", (rid, task_id, seq, kind, dumps(config),
                           dumps({"sources": sources or [], "asset_ids": asset_ids}), t))
    return rid


def set_status(c: Any, run_ids: list[str], status: str) -> None:
    t = now()
    for rid in run_ids:
        if status == "queued":
            c.execute("UPDATE runs SET status = 'queued', queued_at = ? WHERE id = ?", (t, rid))
        else:
            c.execute("UPDATE runs SET status = ?, phase = CASE WHEN ? IN ('cancelled') THEN NULL ELSE phase END, "
                      "finished_at = CASE WHEN ? = 'cancelled' THEN ? ELSE finished_at END WHERE id = ?",
                      (status, status, status, t, rid))


class Worker:
    """The background loop. `request(run_id, "pause"|"stop")` asks the running run to end at the next
    item or chunk boundary; queued/paused runs change state directly in the API."""

    def __init__(self, store: st.Store, toolkit: Toolkit, get_engine: Callable[[], Any]) -> None:
        self.store, self.toolkit, self.get_engine = store, toolkit, get_engine
        self.flags: dict[str, str] = {}
        self.shutdown = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wake: asyncio.Event | None = None
        self._faces: Any = None
        self._faces_lock = threading.Lock()

    def faces(self) -> Any:
        """The face model, loaded once per process; raises when there is none or it does not load."""
        with self._faces_lock:
            if self._faces is None:
                if self.toolkit.faces is None:
                    raise RuntimeError("人物模型未配置")
                model = self.toolkit.faces()
                model.ensure()
                self._faces = model
            return self._faces

    def wake(self) -> None:
        if self._loop is not None and self._wake is not None:
            self._loop.call_soon_threadsafe(self._wake.set)

    def request(self, run_id: str, action: str) -> None:
        self.flags[run_id] = action

    def stopping(self, run_id: str) -> bool:
        return self.shutdown or run_id in self.flags

    def recover(self) -> int:
        """Restart recovery: running -> interrupted -> queued, keeping each run's place in the queue."""
        with self.store.tx() as c:
            c.execute("UPDATE runs SET status = 'interrupted' WHERE status = 'running'")
            rows = c.execute("SELECT id, counts FROM runs WHERE status = 'interrupted'").fetchall()
            for r in rows:
                k = loads(r["counts"], {})
                k["interruptions"] = k.get("interruptions", 0) + 1
                c.execute("UPDATE runs SET status = 'queued', counts = ? WHERE id = ?", (dumps(k), r["id"]))
        return len(rows)

    def claim(self) -> dict[str, Any] | None:
        with self.store.tx() as c:
            r = c.execute("SELECT id FROM runs WHERE status = 'queued' ORDER BY queued_at, seq LIMIT 1").fetchone()
            if r is None:
                return None
            c.execute("UPDATE runs SET status = 'running', started_at = coalesce(started_at, ?) WHERE id = ?",
                      (now(), r["id"]))
        return self.store.run(r["id"])

    async def run_forever(self) -> None:
        self._loop, self._wake = asyncio.get_running_loop(), asyncio.Event()
        n = await asyncio.to_thread(self.recover)
        if n:
            log.info("re-queued %d interrupted run(s)", n)
        while not self.shutdown:
            run = None
            try:
                run = await asyncio.to_thread(self.claim)
                if run is None:
                    self._wake.clear()
                    try:
                        await asyncio.wait_for(self._wake.wait(), 0.5)
                    except TimeoutError:
                        pass
                    continue
                await RunExec(self, run).execute()
            except Exception as exc:  # noqa: BLE001 - nothing may stop the queue; log, back off, go on
                log.exception("worker loop error")
                if run is not None:
                    await asyncio.to_thread(fail_plainly, self.store, run["id"], f"{type(exc).__name__}: {exc}")
                self.store.active.update(run_id=None, task_id=None, phase=None)
                await asyncio.sleep(0.5)


class RunExec:
    """One execution of one task run."""

    def __init__(self, worker: Worker, run: dict[str, Any]) -> None:
        self.w, self.store, self.tk = worker, worker.store, worker.toolkit
        self.run, self.id, self.task_id = run, run["id"], run["task_id"]
        self.caps: list[str] = run["config"]["capabilities"]
        self.mode = capmod.RUN_MODES.get(run["config"].get("run_mode"), capmod.RUN_MODES[capmod.DEFAULT_MODE])
        self.counts: dict[str, Any] = dict(run["counts"])
        self.analyzer: Any = None
        self.analyzer_error: str | None = None
        self.versions: dict[str, str] = run["config"].get("versions") or {}
        self._saved = 0.0
        task = self.store.one("SELECT capabilities FROM tasks WHERE id = ?", (self.task_id,))
        self.enabled = set(self.caps) | set(loads(task["capabilities"], []))

    def stopping(self) -> bool:
        return self.w.stopping(self.id)

    # ---- lifecycle ----
    async def execute(self) -> None:
        self.store.active.update(run_id=self.id, task_id=self.task_id, phase=None)
        error = None
        try:
            if self.run["kind"] in ("scan", "append"):
                await self.phase("discover", self.discover)
            else:
                await asyncio.to_thread(self.seed_scope)
            if not self.stopping():
                await self.phase("preview", self.preview)
            if not self.stopping():
                self.set_phase("analyze")
                await self.analyze()
            if not self.stopping():
                await self.phase("group", self.group)
            elif self.w.flags.get(self.id) == "stop":       # a stopped run keeps its results: group them fully
                await asyncio.to_thread(self.regroup, True)
        except Exception as exc:  # noqa: BLE001 - a run failure is reported, never kills the worker
            log.exception("run %s failed", self.id)
            error = f"{type(exc).__name__}: {exc}"
        cleanup = None
        try:
            if self.analyzer is not None:
                await asyncio.to_thread(self.analyzer.close)
        except Exception as exc:  # noqa: BLE001 - a cleanup failure is reported on the run, not raised
            log.exception("run %s: closing the analyzer failed", self.id)
            cleanup = f"{type(exc).__name__}: {exc}"
        try:
            await asyncio.to_thread(self.finish, error, cleanup)
        except Exception as exc:  # noqa: BLE001 - the summary failed: still end the run honestly
            log.exception("run %s: finishing failed", self.id)
            self.fail_plainly(f"{type(exc).__name__}: {exc}")
        finally:
            self.store.active.update(run_id=None, task_id=None, phase=None)
            self.w.flags.pop(self.id, None)

    def fail_plainly(self, error: str) -> None:
        fail_plainly(self.store, self.id, error)

    async def phase(self, name: str, fn: Callable[[], None]) -> None:
        self.set_phase(name)
        await asyncio.to_thread(fn)

    def set_phase(self, name: str) -> None:
        self.store.active["phase"] = name
        with self.store.tx() as c:
            c.execute("UPDATE runs SET phase = ? WHERE id = ?", (name, self.id))

    def save_counts(self, force: bool = False) -> None:
        """Live progress for GET /tasks, recounted from the database (throttled)."""
        if not force and time.monotonic() - self._saved < 0.3:
            return
        self._saved = time.monotonic()
        k = self.store.one(
            "SELECT count(*) discovered, coalesce(sum(a.preview_status = 'ready'), 0) previewed, "
            "coalesce(sum(a.preview_status = 'failed'), 0) preview_failed FROM run_assets ra "
            "JOIN assets a ON a.id = ra.asset_id WHERE ra.run_id = ?", (self.id,))
        r = self.store.one("SELECT count(*) analyzed, coalesce(sum(status = 'failed'), 0) failed FROM results "
                           "WHERE run_id = ? AND capability NOT LIKE 'raw:%' AND capability NOT IN ('preview', 'meta')", (self.id,))
        self.counts.update(k, analyzed=r["analyzed"], failed=r["failed"] + k["preview_failed"])
        with self.store.tx() as c:
            c.execute("UPDATE runs SET counts = ? WHERE id = ?", (dumps(self.counts), self.id))

    # ---- discover ----
    def seed_scope(self) -> None:
        """rescan / retry: the scope is the given assets, else every asset of the task."""
        for src in self.store.all("SELECT * FROM sources WHERE task_id = ?", (self.task_id,)):
            if not source_online(src["path"]):
                mark_offline(self.store, src["id"], src["path"])
        ids = self.run["scope"].get("asset_ids")
        if ids is None:
            ids = [r["asset_id"] for r in self.store.all("SELECT asset_id FROM task_assets WHERE task_id = ?",
                                                         (self.task_id,))]
        with self.store.tx() as c:
            c.executemany("INSERT OR IGNORE INTO run_assets VALUES (?, ?)", [(self.id, i) for i in ids])

    def discover(self) -> None:
        # missing files are found once (they read offline afterwards): keep what an earlier execution counted
        offline, unsupported, reused, missing = [], 0, 0, self.counts.get("missing", 0)
        for root in self.run["scope"]["sources"]:
            if self.stopping():
                return
            src = self.ensure_source(root)
            if not source_online(root):
                mark_offline(self.store, src["id"], root)
                offline.append(root)
                continue
            with self.store.tx() as c:
                c.execute("UPDATE sources SET online = 1, checked_at = ? WHERE id = ?", (now(), src["id"]))
            found, skipped = self.tk.discover(root)
            unsupported += sum(1 for s in skipped if s.reason == "unsupported")
            seen: set[str] = set()
            for d in found:
                if self.stopping():
                    return
                seen.update(f.path for f in d.files)
                reused += self.upsert(d, src["id"])
                self.save_counts()
            missing += mark_missing(self.store, root, seen)
            self.counts["missing"] = missing
        self.counts.update(offline_sources=offline, unsupported=unsupported, reused=reused, missing=missing)
        self.save_counts(force=True)

    def ensure_source(self, root: str) -> dict[str, Any]:
        """The task's source row for `root` (created if a caller queued the run without one)."""
        src = self.store.one("SELECT * FROM sources WHERE task_id = ? AND path = ?", (self.task_id, root))
        if src is None:
            with self.store.tx() as c:
                c.execute("INSERT OR IGNORE INTO sources (id, task_id, path, added_at, added_run_id) "
                          "VALUES (?, ?, ?, ?, ?)", (st.new_id("s"), self.task_id, root, now(), self.id))
            src = self.store.one("SELECT * FROM sources WHERE task_id = ? AND path = ?", (self.task_id, root))
        return src

    def upsert(self, d: Any, source_id: str) -> int:
        """One discovered asset into the records; 1 when it is unchanged and already previewed (reused)."""
        old = {f["path"]: f for f in self.store.all(
            f"SELECT * FROM asset_files WHERE path IN ({st.marks_in(d.files)})", [f.path for f in d.files])}
        hashes = {}
        for f in d.files:          # outside the lock: size/mtime moved, so read the content identity
            o = old.get(f.path)
            if o is not None and (o["size"], o["mtime_ns"]) != (f.size, f.mtime_ns):
                hashes[f.path] = self.tk.quick_hash(f.path)
        t = now()
        with self.store.tx() as c:
            # identity: a registered file first (a RAW that gains a JPEG keeps its asset), the pair key second
            known = [old[f.path]["asset_id"] for f in d.files if f.path in old]
            row = (c.execute("SELECT * FROM assets WHERE id = ?", (known[0],)).fetchone() if known else
                   c.execute("SELECT * FROM assets WHERE pair_key = ?", (d.pair_key,)).fetchone())
            if row is None:
                aid = st.new_id("a")
                c.execute("INSERT INTO assets (id, media, pair_key, primary_path, created_at, updated_at) "
                          "VALUES (?, ?, ?, ?, ?, ?)", (aid, d.media, d.pair_key, d.primary, t, t))
            else:
                aid = row["id"]
                for other in set(known) - {aid}:      # two assets that are now one pair: the file moves over
                    meta = loads(c.execute("SELECT meta FROM assets WHERE id = ?", (other,)).fetchone()[0], {})
                    c.execute("UPDATE assets SET online = 0, meta = ?, updated_at = ? WHERE id = ?",
                              (dumps(meta | {"merged_into": aid}), t, other))
                    c.execute("UPDATE asset_files SET asset_id = ? WHERE asset_id = ? AND path IN (%s)"
                              % st.marks_in(d.files), [aid, other, *[f.path for f in d.files]])
                    old = {p: o | {"asset_id": aid} for p, o in old.items()}
                if row["pair_key"] != d.pair_key and not c.execute(
                        "SELECT 1 FROM assets WHERE pair_key = ? AND id != ?", (d.pair_key, aid)).fetchone():
                    c.execute("UPDATE assets SET pair_key = ?, media = ? WHERE id = ?", (d.pair_key, d.media, aid))
            changed = added = False
            for f in d.files:
                o = old.get(f.path)
                if o is None or o["asset_id"] != aid or row is not None and row["primary_path"] != d.primary:
                    added = row is not None
                if o is None:
                    c.execute("INSERT INTO asset_files (path, asset_id, role, ext, size, mtime_ns, online, seen_at) "
                              "VALUES (?, ?, ?, ?, ?, ?, 1, ?)", (f.path, aid, f.role, f.ext, f.size, f.mtime_ns, t))
                    continue
                if f.path in hashes and (o["quick_hash"] is None or hashes[f.path] != o["quick_hash"]):
                    changed = True
                c.execute("UPDATE asset_files SET size = ?, mtime_ns = ?, quick_hash = coalesce(?, quick_hash), "
                          "online = 1, seen_at = ? WHERE path = ?",
                          (f.size, f.mtime_ns, hashes.get(f.path), t, f.path))
            meta = loads(row["meta"], {}) if row is not None else {}
            meta.pop("missing", None)
            if changed:
                content_changed(c, aid, meta)
            elif added:
                c.execute("UPDATE assets SET preview_status = 'pending' WHERE id = ?", (aid,))
                c.execute("UPDATE asset_caps SET state = 'pending', updated_at = ? WHERE asset_id = ? "
                          "AND state != 'not_enabled'", (t, aid))
            was_online = row is not None and row["online"]
            c.execute("UPDATE assets SET primary_path = ?, online = 1, meta = ?, updated_at = CASE WHEN ? THEN "
                      "updated_at ELSE ? END WHERE id = ?",
                      (d.primary, dumps(meta), bool(was_online and not changed and not added), t, aid))
            if not c.execute("SELECT 1 FROM task_assets WHERE task_id = ? AND asset_id = ?", (self.task_id, aid)).fetchone():
                ord_ = c.execute("SELECT coalesce(max(ord), -1) + 1 FROM task_assets WHERE task_id = ?",
                                 (self.task_id,)).fetchone()[0]
                c.execute("INSERT INTO task_assets VALUES (?, ?, ?, ?, ?)", (self.task_id, aid, source_id, self.id, ord_))
            c.execute("INSERT OR IGNORE INTO run_assets VALUES (?, ?)", (self.id, aid))
            all_cid = ensure_all_collection(c, self.task_id)
            if all_cid:
                st.add_members(c, all_cid, [aid], "auto")
            ready = c.execute("SELECT preview_status FROM assets WHERE id = ?", (aid,)).fetchone()[0] == "ready"
        return int(row is not None and not changed and not added and ready)

    # ---- preview (metadata first, then the thumbnails; versions of one capture once previews exist) ----
    def preview(self) -> None:
        rows = self.store.all(
            "SELECT a.id, a.primary_path, a.media, a.preview_status, a.meta, (SELECT state FROM asset_caps c "
            "WHERE c.asset_id = a.id AND c.capability = 'meta') meta_state FROM run_assets ra JOIN assets a "
            "ON a.id = ra.asset_id WHERE ra.run_id = ? AND a.online = 1 ORDER BY a.taken_at, a.primary_path",
            (self.id,))
        need_meta = [r for r in rows if r["meta_state"] != "done"]
        for i in range(0, len(need_meta), META_BATCH):
            if self.stopping():
                return
            self.read_meta(need_meta[i:i + META_BATCH])
        self.regroup()
        retry = self.run["kind"] == "retry"
        todo = [r for r in rows if r["preview_status"] == "pending" or (retry and r["preview_status"] == "failed")]
        self.counts["to_preview"] = len(rows)
        self.estimate_analysis(self.units())
        with ThreadPoolExecutor(int(self.mode["preview_workers"])) as pool:
            futs = {pool.submit(self.make_preview, r): r for r in todo}
            for fut in as_completed(futs):
                fut.result()
                self.save_counts()
                if self.stopping():
                    pool.shutdown(cancel_futures=True)
                    break
        self.save_counts(force=True)
        if not self.stopping():
            self.link_versions()

    def link_versions(self) -> None:
        """Versions of one capture (versions.py): analysed and grouped through their representative."""
        self.counts["versions"] = vers.link(self.store, self.task_id, lambda aid: preview_paths(self.store, aid)[0],
                                            [c for c in MODEL_CAPS if c in self.enabled])
        self.save_counts(force=True)

    def read_meta(self, rows: list[dict[str, Any]]) -> None:
        got = self.tk.read_meta([r["primary_path"] for r in rows])
        t = now()
        with self.store.tx() as c:
            for r in rows:
                m = dict(got.get(r["primary_path"]) or {})
                keep = {k: v for k, v in loads(r["meta"], {}).items() if k in ("missing", "content_changed_at")}
                c.execute("UPDATE assets SET taken_at = ?, camera = ?, lens = ?, width = ?, height = ?, orientation = ?, "
                          "lat = ?, lon = ?, duration_s = ?, meta = ?, updated_at = ? WHERE id = ?",
                          (m.get("taken_at"), m.get("camera"), m.get("lens"), m.get("width"), m.get("height"),
                           m.get("orientation"), m.get("lat"), m.get("lon"), m.get("duration_s"), dumps(m | keep), t,
                           r["id"]))
                record(c, r["id"], self.id, "meta", "done", m, None, None)

    def make_preview(self, r: dict[str, Any]) -> None:
        if self.stopping():
            return
        for f in self.store.all("SELECT path FROM asset_files WHERE asset_id = ? AND quick_hash IS NULL", (r["id"],)):
            try:
                h = self.tk.quick_hash(f["path"])
            except OSError:
                continue
            with self.store.tx() as c:
                c.execute("UPDATE asset_files SET quick_hash = ? WHERE path = ?", (h, f["path"]))
        build_preview(self.store, self.tk, r["id"], r["primary_path"], r["media"], run_id=self.id)

    # ---- analyze ----
    def units(self) -> dict[str, list[str]]:
        """asset id -> the model capabilities this run still has to run on it."""
        kind = self.run["kind"]
        caps = [c for c in self.caps if c in MODEL_CAPS]
        if not caps:
            return {}
        rows = self.store.all(
            f"SELECT ra.asset_id, c.capability, c.state, c.run_id FROM run_assets ra JOIN assets a ON a.id = ra.asset_id "
            f"LEFT JOIN asset_caps c ON c.asset_id = ra.asset_id AND c.capability IN ({st.marks_in(caps)}) "
            "WHERE ra.run_id = ? AND a.online = 1 AND a.media = 'photo' AND a.version_of IS NULL",
            [*caps, self.id])   # videos: records only; versions: through their representative
        have: dict[str, dict[str, dict[str, Any]]] = {}
        for r in rows:
            have.setdefault(r["asset_id"], {})
            if r["capability"]:
                have[r["asset_id"]][r["capability"]] = r
        out: dict[str, list[str]] = {}
        for aid, by_cap in have.items():
            for cap in caps:
                r = by_cap.get(cap)
                state = r["state"] if r else "pending"
                if r and r["run_id"] == self.id and state != "pending":
                    continue                       # finished in this run (resume / recovery)
                if (kind == "rescan" or (kind == "retry" and state in ("failed", "unavailable", "pending"))
                        or (kind in ("scan", "append") and state not in ("done", "unrecognized"))):
                    out.setdefault(aid, []).append(cap)
        return out

    def ensure_analyzer(self) -> Any:
        if self.analyzer is None and self.analyzer_error is None:
            try:
                self.analyzer = self.tk.analyzer(self.w.get_engine(), decode_workers=int(self.mode["decode_workers"]),
                                                 chunk=int(self.mode["chunk"]))
                self.save_versions(self.analyzer.versions([c for c in self.caps if c in BIOSCAN_CAPS]))
            except Exception as exc:  # noqa: BLE001 - no engine/analyzer = every model capability unavailable
                log.exception("analyzer unavailable")
                self.analyzer_error = f"{type(exc).__name__}: {exc}"
        return self.analyzer

    def save_versions(self, versions: dict[str, str]) -> None:
        self.versions = self.versions | versions
        run = self.store.run(self.id)
        with self.store.tx() as c:
            c.execute("UPDATE runs SET config = ? WHERE id = ?", (dumps(run["config"] | {"versions": self.versions}),
                                                                  self.id))

    async def analyze(self) -> None:
        units = await asyncio.to_thread(self.units)
        await asyncio.to_thread(self.mark_pending, units)
        self.estimate_analysis(units)
        if not units:
            return
        bio = {aid: [c for c in caps if c in BIOSCAN_CAPS] for aid, caps in units.items()}
        bio = {aid: caps for aid, caps in bio.items() if caps}
        people = [aid for aid, caps in units.items() if "people" in caps]
        if bio:
            await self.analyze_bioscan(bio)
        if people and not self.stopping():
            await self.analyze_people(people)
        await asyncio.to_thread(self.regroup)
        self.save_counts(force=True)

    async def analyze_bioscan(self, units: dict[str, list[str]]) -> None:
        analyzer = await asyncio.to_thread(self.ensure_analyzer)
        if analyzer is None:
            await asyncio.to_thread(self.all_unavailable, units, self.analyzer_error or "analyzer unavailable")
            return
        by_caps: dict[tuple[str, ...], list[str]] = {}
        for aid, caps in units.items():
            by_caps.setdefault(tuple(caps), []).append(aid)
        chunk = int(self.mode["chunk"])
        for caps, ids in by_caps.items():
            items = []
            for part in st.chunks(ids):
                items += [{"asset_id": r["id"], "path": r["primary_path"], "lat": r["lat"], "lon": r["lon"],
                           "taken_at": r["taken_at"]} for r in self.store.all(
                    f"SELECT id, primary_path, lat, lon, taken_at FROM assets WHERE id IN ({st.marks_in(part)}) "
                    "ORDER BY taken_at, primary_path", part)]
            n = 0
            async for res in analyzer.analyze(items, list(caps), self.stopping):
                await asyncio.to_thread(self.record_result, res)
                if not res.capability.startswith("raw:"):
                    n += 1
                    if n % (chunk * len(caps)) == 0:
                        await asyncio.to_thread(self.regroup)
                        if self.mode["pause_s"]:
                            await asyncio.sleep(float(self.mode["pause_s"]))
            if self.stopping():
                break

    async def analyze_people(self, ids: list[str]) -> None:
        """Faces on each photo's upright preview (faces.py); a model that does not load makes people
        unavailable with the reason."""
        try:
            model = await asyncio.to_thread(self.w.faces)
        except Exception as exc:  # noqa: BLE001
            log.exception("face model unavailable")
            await asyncio.to_thread(self.all_unavailable, {aid: ["people"] for aid in ids},
                                    f"人物模型加载失败：{type(exc).__name__}: {exc}")
            return
        await asyncio.to_thread(self.save_versions, {"people": model.version})
        for n, aid in enumerate(ids, 1):
            if self.stopping():
                break
            await asyncio.to_thread(self.record_result, await asyncio.to_thread(self.find_faces, model, aid))
            if n % PEOPLE_EVERY == 0:
                await asyncio.to_thread(self.regroup)
                if self.mode["pause_s"]:
                    await asyncio.sleep(float(self.mode["pause_s"]))

    def find_faces(self, model: Any, aid: str) -> LocalResult:
        large = preview_paths(self.store, aid)[1]
        if not large.is_file():                 # e.g. after clearing the cache: rebuild from the original
            a = self.store.one("SELECT primary_path, media FROM assets WHERE id = ?", (aid,))
            build_preview(self.store, self.tk, aid, a["primary_path"], a["media"], run_id=self.id)
        if not large.is_file():
            return LocalResult(aid, "people", "failed", None, "没有可用预览，无法做人物识别", model.version)
        try:
            found = model.faces(large)
        except Exception as exc:  # noqa: BLE001 - one unreadable image fails only itself
            return LocalResult(aid, "people", "failed", None, f"{type(exc).__name__}: {exc}", model.version)
        return LocalResult(aid, "people", "done" if found else "unrecognized",
                           {"faces": found, "count": len(found), "input": "preview"}, None, model.version)

    def estimate_analysis(self, units: dict[str, list[str]]) -> None:
        """total_analysis = what this run already analysed + what it still has to (known from preview on)."""
        done = self.store.one("SELECT count(*) n FROM results WHERE run_id = ? AND capability NOT LIKE 'raw:%' "
                              "AND capability NOT IN ('preview', 'meta')", (self.id,))["n"]
        self.counts["total_analysis"] = done + sum(len(v) for v in units.values())
        self.save_counts(force=True)

    def mark_pending(self, units: dict[str, list[str]]) -> None:
        t = now()
        with self.store.tx() as c:
            # v0.1 manages a video as a whole clip (metadata, cover, playback, marks); its image capabilities read 不适用
            for cap in (x for x in self.caps if x in MODEL_CAPS):
                c.execute("INSERT INTO asset_caps (asset_id, capability, state, run_id, error, updated_at) "
                          "SELECT ra.asset_id, ?, 'not_applicable', ?, ?, ? FROM run_assets ra JOIN assets a "
                          "ON a.id = ra.asset_id WHERE ra.run_id = ? AND a.media = 'video' "
                          "ON CONFLICT(asset_id, capability) DO UPDATE SET state = 'not_applicable', "
                          "run_id = excluded.run_id, error = excluded.error, updated_at = excluded.updated_at",
                          (cap, self.id, VIDEO_NA, t, self.id))
            for aid, caps in units.items():
                for cap in caps:
                    c.execute("INSERT INTO asset_caps (asset_id, capability, state, run_id, updated_at) "
                              "VALUES (?, ?, 'pending', ?, ?) ON CONFLICT(asset_id, capability) DO UPDATE SET "
                              "state = 'pending', updated_at = excluded.updated_at", (aid, cap, self.id, t))
                c.execute("UPDATE assets SET updated_at = ? WHERE id = ?", (t, aid))
            for cap in capmod.BY_ID:                 # what this task never switched on reads 未启用
                if cap not in self.enabled:
                    c.execute("INSERT OR IGNORE INTO asset_caps (asset_id, capability, state, updated_at) "
                              "SELECT asset_id, ?, 'not_enabled', ? FROM run_assets WHERE run_id = ?", (cap, t, self.id))

    def all_unavailable(self, units: dict[str, list[str]], reason: str) -> None:
        with self.store.tx() as c:
            for aid, caps in units.items():
                for cap in caps:
                    record(c, aid, self.id, cap, "unavailable", None, reason, None)

    def record_result(self, r: Any) -> None:
        with self.store.tx() as c:
            record(c, r.asset_id, self.id, r.capability, r.status, r.data, r.error, r.version)
            if r.capability == "raw:identify" and r.status == "done":
                identities.ingest(c, r.asset_id, self.id, r.version, r.data)
            if r.capability in ("content", "species"):
                ai_tags(c, r.asset_id, self.id, r.capability, r.data if r.status == "done" else None)
        self.save_counts()

    # ---- group ----
    def group(self) -> None:
        caps = [c for c in ("burst", "aesthetic") if c in self.caps]
        if caps and self.ensure_analyzer() is not None:
            unavailable = {r["capability"] for r in self.store.all(
                "SELECT DISTINCT capability FROM results WHERE run_id = ? AND status = 'unavailable'", (self.id,))}
            caps = [c for c in caps if c not in unavailable]
            rows = self.raw_rows()
            if caps and rows:
                self.save_suggestions(self.analyzer.reduce(rows, caps))
        self.regroup(final=True)

    def raw_rows(self) -> list[dict[str, Any]]:
        """Every photo of the task with its latest raw:<stage> products (reduce sees the whole task, §10 X3)."""
        rows: dict[str, dict[str, Any]] = {}
        for r in self.store.all(
                "SELECT a.id, a.primary_path, a.taken_at, a.camera, x.capability, x.data FROM task_assets ta "
                "JOIN assets a ON a.id = ta.asset_id JOIN results x ON x.id IN (SELECT max(id) FROM results "
                "WHERE asset_id = a.id AND capability LIKE 'raw:%' AND status = 'done' GROUP BY capability) "
                "WHERE ta.task_id = ? AND a.media = 'photo' AND a.version_of IS NULL ORDER BY a.taken_at, a.primary_path",
                (self.task_id,)):
            row = rows.setdefault(r["id"], {"asset_id": r["id"], "path": r["primary_path"], "taken_at": r["taken_at"],
                                            "camera": r["camera"], "products": {}})
            row["products"][r["capability"].removeprefix("raw:")] = loads(r["data"])
        return list(rows.values())

    def save_suggestions(self, reduced: dict[str, dict[str, Any]]) -> None:
        t = now()
        version = ";".join(v for k, v in sorted(self.versions.items()) if k in ("aesthetic", "burst")) or None
        with self.store.tx() as c:
            for aid, v in reduced.items():
                b, s = v.get("burst"), v.get("suggestion") or {}
                c.execute("INSERT INTO ai_suggestions (asset_id, run_id, verdict, score, rank, reasons, burst_id, "
                          "burst_rank, burst_size, version, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                          (aid, self.id, s.get("verdict") or "none", s.get("score"), s.get("rank"),
                           dumps(s.get("reasons") or []), None if not b else f"{self.task_id}:{b['id']}",
                           b and b.get("rank"), b and b.get("size"), version, t))
                c.execute("UPDATE assets SET updated_at = ? WHERE id = ?", (t, aid))

    def regroup(self, final: bool = False) -> None:
        """Non-final while the run still analyses (grouping.suggest `final`); the group phase is final."""
        self.counts["groups_skipped_deleted"] = regroup(self.store, self.tk, self.task_id, self.enabled, run_id=self.id,
                                                        tz=self.run["config"].get("timezone"), final=final)

    # ---- end ----
    def finish(self, error: str | None, cleanup: str | None = None) -> None:
        flag = self.w.flags.get(self.id)
        summary = self.summary()
        if cleanup:
            summary["cleanup_error"] = cleanup
        if error:
            status = "failed"
        elif flag == "stop":
            status = "stopped"
        elif flag == "pause":
            status = "paused"
        elif self.w.shutdown:
            status = "interrupted"
        elif (summary["failed"] or summary["preview_failed"] or summary["unavailable"] or summary["offline_sources"]
              or summary["missing"] or summary["not_run"] or cleanup):     # a reused asset's old failure counts too
            status = "partial"
        else:
            status = "completed"
        ended = status in st.TERMINAL
        self.save_counts(force=True)
        with self.store.tx() as c:
            c.execute("UPDATE runs SET status = ?, phase = CASE WHEN ? THEN NULL ELSE phase END, summary = ?, "
                      "error = ?, finished_at = CASE WHEN ? THEN ? ELSE NULL END WHERE id = ?",
                      (status, ended, dumps(summary), error, ended, now(), self.id))
            cs = self.store.setting("cap_status") or {}
            for cap in self.caps:
                if cap in summary["unavailable"]:
                    cs[cap] = {"state": "unavailable", "reason": summary["unavailable"][cap],
                               "version": self.versions.get(cap)}
                elif summary["analyzed"].get(cap):
                    cs[cap] = {"state": "available", "reason": None, "version": self.versions.get(cap)}
            st.Store.put_setting(c, "cap_status", cs)
        log.info("run %s %s: %s", self.id, status, {k: v for k, v in summary.items() if k != "failed"})

    def summary(self) -> dict[str, Any]:
        s = self.store
        k = s.one("SELECT count(*) discovered, coalesce(sum(a.media = 'photo'), 0) photo, "
                  "coalesce(sum(a.media = 'video'), 0) video, coalesce(sum(a.preview_status = 'ready'), 0) previewed, "
                  "coalesce(sum(a.preview_status = 'failed'), 0) preview_failed, "
                  "coalesce(sum(json_extract(a.meta, '$.missing') = 1), 0) missing "
                  "FROM run_assets ra JOIN assets a ON a.id = ra.asset_id WHERE ra.run_id = ?", (self.id,))
        f = s.one("SELECT count(*) files, coalesce(sum(f.size), 0) bytes FROM run_assets ra "
                  "JOIN asset_files f ON f.asset_id = ra.asset_id WHERE ra.run_id = ?", (self.id,))
        analyzed = {r["capability"]: r["n"] for r in s.all(
            "SELECT capability, count(*) n FROM results WHERE run_id = ? AND status IN ('done', 'unrecognized') "
            "AND capability NOT LIKE 'raw:%' GROUP BY capability", (self.id,))}
        failed = [{"asset_id": r["asset_id"], "capability": r["capability"], "error": r["error"]} for r in s.all(
            "SELECT asset_id, capability, error FROM results WHERE run_id = ? AND status = 'failed' "
            "AND capability NOT LIKE 'raw:%' ORDER BY id LIMIT ?", (self.id, FAILED_MAX))]
        unavailable = {r["capability"]: r["error"] for r in s.all(
            "SELECT capability, min(error) error FROM results WHERE run_id = ? AND status = 'unavailable' "
            "GROUP BY capability", (self.id,))}
        not_run = [r["capability"] for r in s.all(
            "SELECT DISTINCT c.capability FROM run_assets ra JOIN asset_caps c ON c.asset_id = ra.asset_id "
            "JOIN assets a ON a.id = ra.asset_id WHERE ra.run_id = ? AND c.state = 'pending' AND a.online = 1 "
            "ORDER BY c.capability", (self.id,)) if r["capability"] in self.caps]
        offline = [r["path"] for r in s.all("SELECT path FROM sources WHERE task_id = ? AND online = 0 ORDER BY path",
                                            (self.task_id,))
                   if r["path"] in self.run["scope"]["sources"] or self.run["kind"] in ("rescan", "retry")]
        started = self.store.one("SELECT started_at FROM runs WHERE id = ?", (self.id,))["started_at"]
        elapsed = (datetime.fromisoformat(now()) - datetime.fromisoformat(started)).total_seconds() if started else 0
        return {"discovered": k["discovered"], "files": f["files"], "bytes": f["bytes"],
                "by_media": {"photo": k["photo"], "video": k["video"]}, "previewed": k["previewed"],
                "preview_failed": k["preview_failed"], "analyzed": analyzed, "failed": failed,
                "unavailable": unavailable, "not_run": not_run, "offline_sources": offline, "missing": k["missing"] + self.counts.get("missing", 0),
                "unsupported": self.counts.get("unsupported", 0), "reused": self.counts.get("reused", 0),
                "groups_skipped_deleted": self.counts.get("groups_skipped_deleted", 0),
                "versions": self.counts.get("versions") or {"same_capture": 0, "export": 0},
                "elapsed_s": round(elapsed, 3)}


def fail_plainly(store: st.Store, run_id: str, error: str) -> None:
    """A run that is still `running` after an unexpected error ends `failed` with that error."""
    try:
        with store.tx() as c:
            c.execute("UPDATE runs SET status = 'failed', phase = NULL, error = ?, finished_at = ? "
                      "WHERE id = ? AND status = 'running'", (error, now(), run_id))
    except Exception:  # noqa: BLE001
        log.exception("run %s: could not record the failure", run_id)


# ---- shared writers (each takes an open transaction unless it opens its own) ----

def record(c: Any, asset_id: str, run_id: str, cap: str, status: str, data: Any, error: str | None,
           version: str | None) -> None:
    """A machine result into the history; the current state into asset_caps (never for raw:* products)."""
    t = now()
    c.execute("INSERT INTO results (asset_id, run_id, capability, status, version, data, error, created_at) "
              "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
              (asset_id, run_id, cap, status, version, None if data is None else dumps(data), error, t))
    if cap.startswith("raw:") or cap == "preview":
        return
    c.execute("INSERT INTO asset_caps (asset_id, capability, state, run_id, version, error, updated_at) "
              "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(asset_id, capability) DO UPDATE SET state = excluded.state, "
              "run_id = excluded.run_id, version = excluded.version, error = excluded.error, "
              "updated_at = excluded.updated_at", (asset_id, cap, status, run_id, version, error, t))
    c.execute("UPDATE assets SET updated_at = ? WHERE id = ?", (t, asset_id))


def ai_tags(c: Any, asset_id: str, run_id: str, cap: str, data: dict[str, Any] | None) -> None:
    """Replace the asset's current AI tags of this kind (history stays in results, §10)."""
    prefix = "scene:" if cap == "content" else "species:"
    c.execute("DELETE FROM asset_tags WHERE asset_id = ? AND source = 'ai' AND tag_id LIKE ?", (asset_id, prefix + "%"))
    if not data:
        return
    t, tags = now(), []
    if cap == "content" and data.get("label"):
        parent = st.ensure_tag(c, f"scene:{data['group']}", data.get("group_zh") or data["group"]) \
            if data.get("group") else None
        tags.append((parent, data.get("score")))
        tags.append((st.ensure_tag(c, f"scene:{data['label']}", data.get("label_zh") or data["label"], parent),
                     data.get("score")))
    elif cap == "species" and data.get("taxon"):
        tags.append((st.ensure_tag(c, f"species:{data['taxon']}", data.get("common") or data["taxon"]), data.get("p")))
    for tag_id, conf in tags:
        if tag_id:
            c.execute("INSERT OR REPLACE INTO asset_tags VALUES (?, ?, 'ai', ?, ?, ?)", (asset_id, tag_id, run_id, conf, t))
    c.execute("UPDATE assets SET updated_at = ? WHERE id = ?", (t, asset_id))


def content_changed(c: Any, asset_id: str, meta: dict[str, Any]) -> None:
    """Same path, new content (§10 X4): same asset id, Flag/Star cleared as an undoable system batch,
    previews and capabilities back to pending; the old results stay as history."""
    m = st.mark_row(c, asset_id)
    if m["flag"] is not None or m["stars"]:
        st.apply_marks(c, [asset_id], {"flag": None, "stars": 0}, actor="system", reason="content_changed")
    t = now()
    meta["content_changed_at"] = t
    c.execute("UPDATE assets SET preview_status = 'pending', preview_method = NULL, preview_error = NULL, "
              "updated_at = ? WHERE id = ?", (t, asset_id))
    c.execute("UPDATE asset_caps SET state = 'pending', updated_at = ? WHERE asset_id = ? AND state != 'not_enabled'",
              (t, asset_id))


def mark_offline(store: st.Store, source_id: str, root: str) -> None:
    """A source that is not reachable: its records and previews stay; files and assets read offline."""
    t = now()
    with store.tx() as c:
        c.execute("UPDATE sources SET online = 0, checked_at = ? WHERE id = ?", (t, source_id))
        prefix = root.rstrip("/") + "/"
        c.execute("UPDATE asset_files SET online = 0 WHERE substr(path, 1, length(?)) = ?", (prefix, prefix))
        c.execute("UPDATE assets SET online = 0, updated_at = ? WHERE online = 1 AND substr(primary_path, 1, length(?)) = ?",
                  (t, prefix, prefix))


def mark_missing(store: st.Store, root: str, seen: set[str]) -> int:
    """Files under an online `root` that discovery no longer finds: offline + meta.missing, never deleted."""
    prefix = root.rstrip("/") + "/"
    gone = [r for r in store.all("SELECT path, asset_id FROM asset_files WHERE substr(path, 1, length(?)) = ? "
                                 "AND online = 1", (prefix, prefix)) if r["path"] not in seen]
    if not gone:
        return 0
    t = now()
    with store.tx() as c:
        for r in gone:
            c.execute("UPDATE asset_files SET online = 0 WHERE path = ?", (r["path"],))
        for aid in {r["asset_id"] for r in gone}:
            if not c.execute("SELECT 1 FROM asset_files WHERE asset_id = ? AND online = 1", (aid,)).fetchone():
                meta = loads(c.execute("SELECT meta FROM assets WHERE id = ?", (aid,)).fetchone()[0], {})
                c.execute("UPDATE assets SET online = 0, meta = ?, updated_at = ? WHERE id = ?",
                          (dumps(meta | {"missing": True}), t, aid))
    return len({r["asset_id"] for r in gone})


def build_preview(store: st.Store, tk: Toolkit, asset_id: str, primary: str, media: str,
                  run_id: str | None = None) -> bool:
    """Make (or rebuild) the asset's previews in the cache and record the outcome."""
    thumb, large = preview_paths(store, asset_id)
    thumb.parent.mkdir(parents=True, exist_ok=True)
    try:
        p = tk.make_preview(primary, media, thumb, large)
        ok, method, err = p.ok, p.method, p.error
    except Exception as exc:  # noqa: BLE001 - make_preview should not raise; a raise is a failed preview
        ok, method, err = False, None, f"{type(exc).__name__}: {exc}"
    online = ok or tk.is_online(primary)
    t = now()
    with store.tx() as c:
        if not online:                  # the file went away: a missing asset, not a broken preview
            c.execute("UPDATE asset_files SET online = 0 WHERE asset_id = ?", (asset_id,))
            meta = loads(c.execute("SELECT meta FROM assets WHERE id = ?", (asset_id,)).fetchone()[0], {})
            c.execute("UPDATE assets SET online = 0, meta = ?, updated_at = ? WHERE id = ?",
                      (dumps(meta | {"missing": True}), t, asset_id))
            return False
        c.execute("UPDATE assets SET preview_status = ?, preview_method = ?, preview_error = ?, updated_at = ? "
                  "WHERE id = ?", ("ready" if ok else "failed", method, err, t, asset_id))
        if not ok and run_id:
            record(c, asset_id, run_id, "preview", "failed", None, err, None)
    return ok


def ensure_all_collection(c: Any, task_id: str) -> str | None:
    """The task's 全部素材 collection (created once; None once a person deleted it)."""
    row = c.execute("SELECT id, deleted_at FROM collections WHERE task_id = ? AND auto_key = 'all'", (task_id,)).fetchone()
    if row is None:
        return st.create_collection(c, name="全部素材", origin="auto", group_kind="all", auto_key="all",
                                    task_id=task_id, actor="system")
    return None if row["deleted_at"] else row["id"]


def people_faces(store: st.Store, task_id: str) -> dict[str, list[dict[str, Any]]]:
    """asset id -> its faces (latest people result) for every asset of the task that has some."""
    rows = store.all(
        "SELECT asset_id, data FROM results WHERE id IN (SELECT max(x.id) FROM results x JOIN task_assets ta "
        "ON ta.asset_id = x.asset_id WHERE ta.task_id = ? AND x.capability = 'people' AND x.status = 'done' "
        "GROUP BY x.asset_id)", (task_id,))
    return {r["asset_id"]: fs for r in rows if (fs := [f for f in (loads(r["data"], {}) or {}).get("faces", [])
                                                       if f.get("vec")])}


def _people(store: st.Store, task_id: str, reps: set[str],
            portraits: set[str] = frozenset()) -> tuple[dict[str, list[str]], dict[str, str]]:
    """(asset id -> person keys, named key -> name). A face a user named belongs to that person; another
    face joins the named person whose confirmed faces it matches (faces.nearest); the remaining strong
    faces are clustered (faces.cluster, key = the person's first asset id). Then leftover faces (a cluster
    under MIN_GROUP photos, or a weak face: profile, sunglasses, small) join the established person
    (named, or a cluster of MIN_GROUP+ photos) they most likely are (faces.attach). A weak face counts
    only on a photo whose content label is people (`portraits`), so an animal never becomes a person."""
    found = people_faces(store, task_id)
    if not found:
        return {}, {}
    from bioscan.gardener import faces
    from bioscan.gardener.grouping import MIN_GROUP

    named = {f"named:{r['id']}": r["name"] for r in store.all("SELECT id, name FROM people")}
    refs: dict[str, list[str]] = {}
    fixed: dict[tuple[str, int], str] = {}
    for r in store.all("SELECT asset_id, face, person_id, vec FROM person_faces"):
        refs.setdefault(f"named:{r['person_id']}", []).append(r["vec"])
        fixed[(r["asset_id"], r["face"])] = f"named:{r['person_id']}"
    cents = {k: faces.centroid(vs) for k, vs in refs.items()}
    out: dict[str, list[str]] = {}

    def put(aid: str, key: str) -> None:
        if key not in out.setdefault(aid, []):
            out[aid].append(key)

    strong, weak = [], []
    for aid in sorted(found):
        if aid not in reps:
            continue
        for i, f in enumerate(found[aid]):
            key = fixed.get((aid, i)) or (faces.nearest(f["vec"], cents) if faces.strong(f) else None)
            if key:
                put(aid, key)
            elif faces.strong(f):
                strong.append((aid, f["vec"], (f["box"][2] - f["box"][0]) * (f["box"][3] - f["box"][1]) * f["score"]))
            elif aid in portraits:
                weak.append((aid, f["vec"]))
    vecs = {}
    leftover = list(weak)
    for ids in faces.cluster(strong):
        if len(ids) >= MIN_GROUP:
            key = min(ids)
            vecs[key] = [v for a, v, _ in strong if a in ids]
            for aid in ids:
                put(aid, key)
        else:
            leftover += [(a, v) for a, v, _ in strong if a in ids]
    established = cents | {k: faces.centroid(vs) for k, vs in vecs.items()}
    for aid, vec in leftover:
        put(aid, faces.attach(vec, established) or aid)    # unattached: a person of its own (folds into 人物 · 其他)
    return out, named


def _base_key(key: str | None) -> str:
    return (key or "").split("#", 1)[0]


def regroup(store: st.Store, tk: Toolkit, task_id: str, enabled: set[str], run_id: str | None = None,
            tz: str | None = None, final: bool = True) -> int:
    """grouping.suggest over the task's representatives (versions of a capture stay out) -> auto
    collections (§6.5, §10 X8, 单元 3). A suggested group lands on the auto collection of its kind and
    parent with its key; people and places, whose keys drift as they grow, on the one they overlap most;
    else a new one. Auto only adds members, never revives a tombstone, never renames a user-edited
    collection, never touches (or refills from) a deleted one, and makes no children under a deleted
    parent. A representative without a time or GPS of its own takes its versions'."""
    latest: dict[tuple[str, str], Any] = {(r["asset_id"], r["capability"]): loads(r["data"]) for r in store.all(
        "SELECT asset_id, capability, data FROM results WHERE id IN (SELECT max(x.id) FROM results x "
        "JOIN task_assets ta ON ta.asset_id = x.asset_id WHERE ta.task_id = ? AND x.capability IN "
        "('content') AND x.status IN ('done', 'unrecognized') GROUP BY x.asset_id, x.capability)", (task_id,))}
    rows = store.all("SELECT a.id, a.taken_at, a.lat, a.lon, a.media, a.version_of FROM task_assets ta JOIN assets a "
                     "ON a.id = ta.asset_id WHERE ta.task_id = ? ORDER BY ta.ord", (task_id,))
    subject_caps = [c for c in SUBJECT_CAPS if c in enabled]
    settled_caps: dict[str, set[str]] = {}               # a photo is grouped once each subject capability ended
    for r in store.all(f"SELECT c.asset_id, c.capability FROM asset_caps c JOIN task_assets ta ON ta.asset_id = "
                       f"c.asset_id WHERE ta.task_id = ? AND c.state NOT IN ('pending', 'running') AND c.capability "
                       f"IN ({st.marks_in(subject_caps)})", [task_id, *subject_caps]) if subject_caps else []:
        settled_caps.setdefault(r["asset_id"], set()).add(r["capability"])
    unsettled = {r["id"] for r in rows if r["media"] == "photo" and len(settled_caps.get(r["id"], ())) < len(subject_caps)}
    by_rep: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        if r["version_of"]:
            by_rep.setdefault(r["version_of"], []).append(r)
    reps = [r for r in rows if not r["version_of"]]
    from bioscan.gardener.grouping import PEOPLE

    portraits = {a["id"] for a in reps if (latest.get((a["id"], "content")) or {}).get("label") in PEOPLE}
    people, names = _people(store, task_id, {r["id"] for r in reps}, portraits) if "people" in enabled else ({}, {})
    assets = []
    for a in reps:
        own = dict(a)
        for v in by_rep.get(a["id"], []):
            own["taken_at"] = own["taken_at"] or v["taken_at"]
            if own["lat"] is None or own["lon"] is None:
                own["lat"], own["lon"] = v["lat"], v["lon"]
        ct = latest.get((a["id"], "content"))
        # Species never make task groups (contract revision 4, S6): the library's species cards, derived from
        # identifications, are the species view, with no minimum size; a task groups animals by kind.
        assets.append(own | {"content": {"group": ct.get("group"), "label": ct.get("label")} if ct else None,
                             "species": None,
                             "people": people.get(a["id"]), "settled": a["id"] not in unsettled})
    if not assets:
        return 0
    params = inspect.signature(tk.suggest).parameters      # grouping.suggest(assets, tz=..., final=..., names=...)
    kw = ({"tz": tz} if "tz" in params else {}) | ({"final": final} if "final" in params else {}) | \
        ({"names": names} if "names" in params else {})
    groups = [g for g in tk.suggest(assets, **kw) if g.kind in GROUP_NEEDS
              and (not GROUP_NEEDS[g.kind] or any(n in enabled for n in GROUP_NEEDS[g.kind]))]
    t, skipped = now(), 0
    with store.tx() as c:
        cols = [dict(r) for r in c.execute("SELECT id, group_kind, auto_key, parent_id, name, user_edited, deleted_at "
                                           "FROM collections WHERE task_id = ? AND origin = 'auto'", (task_id,))]
        members = {col["id"]: {r[0] for r in c.execute("SELECT asset_id FROM collection_members WHERE collection_id = ?",
                                                       (col["id"],))} for col in cols}
        placed: dict[str, str] = {}                         # group key -> live collection id (parents for children)
        taken: set[str] = set()                             # one collection per group in this pass
        for g in groups:
            parent = getattr(g, "parent", None)
            parent_id = placed.get(parent) if parent else None
            if parent and parent_id is None:                # parent deleted or not made: no children
                continue
            same = [col for col in cols if col["group_kind"] == g.kind and col.get("parent_id") == parent_id
                    and col["id"] not in taken]
            ids = set(g.asset_ids)
            target = next((col for col in same if col["auto_key"] == g.key), None)
            if target is None and g.kind in ("person", "place"):    # most of one side is in the other
                fits = [col for col in same if 2 * len(members[col["id"]] & ids) >= min(len(ids), len(members[col["id"]]))
                        and members[col["id"]] & ids]
                target = max(fits, key=lambda col: len(members[col["id"]] & ids), default=None)
            if target is None:
                key, n = g.key, 1
                while any(col["auto_key"] == key for col in cols):   # a key taken by another row: never reuse it
                    n += 1
                    key = f"{g.key}#{n}"
                cid = st.create_collection(c, name=g.name, origin="auto", group_kind=g.kind, auto_key=key,
                                           task_id=task_id, actor="system")
                if parent_id:
                    c.execute("UPDATE collections SET parent_id = ? WHERE id = ?", (parent_id, cid))
                target = {"id": cid, "group_kind": g.kind, "auto_key": key, "parent_id": parent_id, "name": g.name,
                          "user_edited": 0, "deleted_at": None}
                cols.append(target)
                members[cid] = set()
            if target["deleted_at"]:
                skipped += 1
                continue
            placed[g.key] = target["id"]
            taken.add(target["id"])
            gone = set().union(*[members[col["id"]] for col in same if col["deleted_at"]
                                 and (g.kind in ("person", "place") or _base_key(col["auto_key"]) == g.key)])
            new = [i for i in g.asset_ids if i not in gone and i not in members[target["id"]]]
            if st.add_members(c, target["id"], new, "auto"):
                for i in new:
                    c.execute("UPDATE assets SET updated_at = ? WHERE id = ?", (t, i))
            members[target["id"]].update(new)
            c.execute("UPDATE collections SET summary = ? WHERE id = ?", (dumps(getattr(g, "summary", None) or {}),
                                                                           target["id"]))
            if not target["user_edited"] and target["name"] != g.name:
                c.execute("UPDATE collections SET name = ?, updated_at = ? WHERE id = ?", (g.name, t, target["id"]))
                target["name"] = g.name
        if final and "people" in enabled:
            # a face cluster's identity drifts as faces arrive: an unnamed cluster group the final pass no longer
            # suggests, that no person touched (not renamed, reviewed or given members), is retired, not left as a
            # second "人物 N"
            for col in cols:
                k = col["auto_key"] or ""
                if (col["group_kind"] == "person" and col["id"] not in taken and not col["deleted_at"]
                        and not col["user_edited"] and k.startswith("person:a_")
                        and not c.execute("SELECT 1 FROM collection_reviews WHERE collection_id = ?", (col["id"],)).fetchone()
                        and not c.execute("SELECT 1 FROM collection_members WHERE collection_id = ? AND added_by = 'user'",
                                          (col["id"],)).fetchone()):
                    c.execute("UPDATE collections SET deleted_at = ?, updated_at = ?, version = version + 1, "
                              "auto_key = auto_key || '#retired-' || id WHERE id = ?", (t, t, col["id"]))
                    st.collection_history(c, col["id"], "delete", {"reason": "superseded_cluster"}, "system")
        if "place_event" in enabled and run_id:
            for r in c.execute("SELECT asset_id FROM run_assets WHERE run_id = ?", (run_id,)).fetchall():
                c.execute("INSERT INTO asset_caps (asset_id, capability, state, run_id, updated_at) VALUES "
                          "(?, 'place_event', 'done', ?, ?) ON CONFLICT(asset_id, capability) DO UPDATE SET "
                          "state = 'done', run_id = excluded.run_id, updated_at = excluded.updated_at",
                          (r[0], run_id, t))
    return skipped


# ---- task actions used by the API ----

def active_runs(store: st.Store, task_id: str) -> list[dict[str, Any]]:
    return store.all("SELECT id, status FROM runs WHERE task_id = ? AND status IN ('queued', 'running', 'paused') "
                     "ORDER BY seq", (task_id,))


def pause(store: st.Store, worker: Worker, task_id: str) -> None:
    with store.tx() as c:
        for r in active_runs(store, task_id):
            if r["status"] == "queued":
                set_status(c, [r["id"]], "paused")
            elif r["status"] == "running":
                worker.request(r["id"], "pause")


def resume(store: st.Store, worker: Worker, task_id: str) -> None:
    with store.tx() as c:
        set_status(c, [r["id"] for r in active_runs(store, task_id) if r["status"] == "paused"], "queued")
    worker.wake()


def stop(store: st.Store, worker: Worker, task_id: str) -> None:
    with store.tx() as c:
        for r in active_runs(store, task_id):
            if r["status"] == "running":
                worker.request(r["id"], "stop")
            else:
                set_status(c, [r["id"]], "cancelled")


def verify_sources(store: st.Store, worker: Worker, tk: Toolkit, task_id: str) -> str | None:
    """Re-check every source; ones back online get an append run (returned), offline ones read offline."""
    back = []
    for s in store.all("SELECT * FROM sources WHERE task_id = ?", (task_id,)):
        online = source_online(s["path"])
        if not online:
            mark_offline(store, s["id"], s["path"])
            continue
        with store.tx() as c:
            c.execute("UPDATE sources SET online = 1, checked_at = ? WHERE id = ?", (now(), s["id"]))
        if not s["online"]:
            back.append(s["path"])
    if not back:
        return None
    t = store.one("SELECT * FROM tasks WHERE id = ?", (task_id,))
    with store.tx() as c:
        rid = queue_run(c, task_id, "append", capabilities=loads(t["capabilities"]), run_mode=t["run_mode"],
                        preset=t["preset"], sources=back)
    worker.wake()
    return rid


class EngineHolder:
    """The BioScan Engine, created on first need (only analysis needs it)."""

    def __init__(self, engine: Any = None) -> None:
        self.engine = engine
        self._lock = threading.Lock()

    def get(self) -> Any:
        with self._lock:
            if self.engine is None:
                from bioscan.service.engine import Engine

                self.engine = Engine()
            return self.engine
