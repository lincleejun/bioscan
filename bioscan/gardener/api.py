"""Gardener HTTP API (contract §6, §10) under /api/v1, plus the static web UI at "/" when present.

Endpoints are sync functions (FastAPI runs them in its thread pool); every write goes through the
store's single lock. The background worker (tasks.Worker) lives in the app's lifespan."""
from __future__ import annotations

import asyncio
import logging
import mimetypes
import os
import re
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, nullcontext
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Body, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

import bioscan
from bioscan.gardener import capabilities as capmod
from bioscan.gardener import cleanup, detail, handoff, identities, imports, parts, people, query, tasks, taxa
from bioscan.gardener import review as rv
from bioscan.gardener import store as st
from bioscan.gardener import versions as vers
from bioscan.gardener.store import Conflict, Error, NotFound, dumps, loads, now
from bioscan.naming import DATA_DIR

log = logging.getLogger("bioscan.gardener")
BUILTIN_DEFAULT = "photo"      # the default preset when the configured one is gone
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")
WEB_DIR = Path(__file__).with_name("web")
BODY = Body(...)
OPT_BODY = Body(default={})
LIMIT200, LIMIT100, OFFSET = Query(200, ge=1, le=1000), Query(100, ge=1, le=1000), Query(0, ge=0)
PREVIEW_IDS = 200      # asset ids listed per side of a rule preview (the counts are always whole)
ACTOR = Header("api", alias="X-Gardener-Actor")     # who wrote it: ui | cli | mcp | api (history rows)


def err(status: int, message: str, code: str, **extra: Any) -> JSONResponse:
    return JSONResponse({"error": message, "code": code, **extra}, status_code=status)


def strs(v: Any, name: str) -> list[str]:
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise Error(f"{name} must be a list of strings")
    return list(dict.fromkeys(v))


def text(body: dict[str, Any], key: str, *, required: bool = False, default: str = "") -> str:
    v = body.get(key, default)
    if v is None:
        v = default
    if not isinstance(v, str) or (required and not v.strip()):
        raise Error(f"{key} must be a {'non-empty ' if required else ''}string")
    return v.strip() if required else v


def create_gardener_app(data_dir: str | Path, *, engine: Any = None, toolkit: tasks.Toolkit | None = None,
                        allow_roots: list[str] | None = None, start_worker: bool = True,
                        allowed_hosts: tuple[str, ...] = LOCAL_HOSTS, name_lists: Path | None = DATA_DIR,
                        lightroom_dir: Path | None = None, trash: Any = None) -> FastAPI:
    """`engine`: a BioScan Engine (tests pass one built on fake Loaders); None = created on first analysis.
    `toolkit`: unit 2's scanner/Analyzer functions (default: the real modules). `allow_roots`: task sources
    must lie inside one of them (none = any directory). `name_lists`: the folder holding avilist/, mdd/ and
    names/ the name index is built from (taxa.py); None = leave the index as it is. `lightroom_dir`: where handoff
    files go for the Lightroom plugin (default handoff.default_dir()); `trash`: moves one file to the Trash
    (default cleanup.trash; tests inject one)."""
    store = st.Store(data_dir)
    if name_lists is not None:
        taxa.sync(store, name_lists)
    tk = toolkit or tasks.Toolkit.default()
    holder = tasks.EngineHolder(engine)
    worker = tasks.Worker(store, tk, holder.get)
    importer = imports.Runner(store, store.task_roots)        # 契约修订 5D: imports run beside the scan queue
    roots = [Path(r).expanduser().resolve() for r in allow_roots or []]
    details = detail.Details(store.cache_dir)       # contract revision 5C: original-pixel detail

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        job = asyncio.create_task(worker.run_forever()) if start_worker else None
        await asyncio.to_thread(importer.start)
        try:
            yield
        finally:
            await asyncio.to_thread(importer.close)
            worker.shutdown = True
            worker.wake()
            if job is not None:
                try:
                    await asyncio.wait_for(job, 60)
                except (Exception, asyncio.CancelledError):  # noqa: BLE001 - shutting down never raises
                    log.exception("worker did not stop cleanly")
            details.close()
            store.close()

    app = FastAPI(title="gardener", lifespan=lifespan)
    app.state.store, app.state.worker, app.state.toolkit, app.state.engine = store, worker, tk, holder
    app.state.importer = importer

    @app.middleware("http")
    async def local_only(request: Request, call_next: Any) -> Any:
        """Only this machine may call (§10): Host must be a loopback name; a non-GET from a foreign
        Origin (a web page elsewhere) is refused."""
        host = (request.headers.get("host") or "").rsplit(":", 1)[0].strip("[]")
        if request.headers.get("host", "").startswith("["):
            host = request.headers["host"][1:].split("]")[0]
        if host not in allowed_hosts:
            return err(403, "只接受本机访问（Host）", "forbidden")
        origin = request.headers.get("origin")
        if origin and request.method != "GET" and (urlsplit(origin).hostname or "") not in allowed_hosts:
            return err(403, "拒绝来自其他来源的写请求（Origin）", "forbidden")
        return await call_next(request)

    @app.exception_handler(Error)
    async def on_error(request: Request, exc: Error) -> JSONResponse:
        return err(exc.status, str(exc), exc.code, **exc.extra)

    @app.exception_handler(ValueError)
    async def on_value(request: Request, exc: ValueError) -> JSONResponse:
        return err(400, str(exc), "bad_request")

    @app.exception_handler(RequestValidationError)
    async def on_invalid(request: Request, exc: RequestValidationError) -> JSONResponse:
        return err(400, f"bad request: {exc.errors()[:3]}", "bad_request")

    @app.exception_handler(StarletteHTTPException)
    async def on_http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return err(exc.status_code, str(exc.detail), "not_found" if exc.status_code == 404 else "bad_request")

    r = APIRouter(prefix="/api/v1")

    def task_or_404(task_id: str, with_runs: bool = False) -> dict[str, Any]:
        t = store.task(task_id, with_runs=with_runs)
        if t is None:
            raise NotFound(f"no task {task_id}")
        return t

    def asset_or_404(asset_id: str) -> dict[str, Any]:
        a = store.one("SELECT * FROM assets WHERE id = ?", (asset_id,))
        if a is None:
            raise NotFound(f"no asset {asset_id}")
        return a

    def col_or_404(cid: str) -> dict[str, Any]:
        c = store.one("SELECT * FROM collections WHERE id = ?", (cid,))
        if c is None:
            raise NotFound(f"no collection {cid}")
        return c

    def check_assets(ids: list[str]) -> None:
        missing = [i for part in st.chunks(ids) for i in sorted(set(part) - {x["id"] for x in store.all(
            f"SELECT id FROM assets WHERE id IN ({st.marks_in(part)})", part)})]
        if missing:
            raise NotFound(f"no assets {missing[:5]}")

    def source_paths(paths: Any) -> list[str]:
        out = []
        for p in strs(paths, "sources"):
            if not os.path.isabs(p):
                raise Error(f"来源必须是绝对路径: {p}")
            rp = Path(p).expanduser().resolve()
            if not rp.is_dir():
                raise Error(f"来源不存在或不是目录: {p}")
            if roots and not any(rp.is_relative_to(x) for x in roots):
                raise Error(f"来源不在允许的目录内 (--allow-root): {p}")
            if store.data_dir.is_relative_to(rp) or rp.is_relative_to(store.data_dir):
                raise Error(f"来源与 Gardener 数据目录重叠（Gardener 不在来源中写入）: {p}")
            out.append(str(rp))
        if not out:
            raise Error("sources must not be empty")
        return list(dict.fromkeys(out))

    def caps_of(body: dict[str, Any], preset: str | None) -> list[str]:
        if body.get("capabilities") is not None:
            return capmod.expand(strs(body["capabilities"], "capabilities"))
        p = store.one("SELECT capabilities FROM presets WHERE id = ?", (preset,))
        if p is None:
            raise Error(f"unknown preset {preset}")
        return capmod.expand(loads(p["capabilities"]))

    def timezone(v: Any) -> str | None:
        """IANA name or ±HH:MM; None = unknown (times stay camera-local, never converted)."""
        if v is None or v == "":
            return None
        if isinstance(v, str) and re.fullmatch(r"[+-](0\d|1[0-4]):[0-5]\d", v):
            return v
        try:
            ZoneInfo(v)
        except (ValueError, TypeError, ZoneInfoNotFoundError):
            raise Error(f"timezone must be an IANA name or ±HH:MM: {v!r}") from None
        return v

    def run_mode(v: Any) -> str:
        if v not in capmod.RUN_MODES:
            raise Error(f"run_mode must be one of {'|'.join(capmod.RUN_MODES)}")
        return v

    # ---- 6.1 service and management ----
    @r.get("/health")
    def health() -> dict[str, Any]:
        eng = holder.engine
        return {"status": "ok", "version": bioscan.__version__, "schema_version": st.SCHEMA_VERSION,
                "data_dir": str(store.data_dir), "cache_dir": str(store.cache_dir),
                "worker": {"state": "running" if store.active["run_id"] else "idle", "run_id": store.active["run_id"],
                           "task_id": store.active["task_id"]},
                "device": getattr(eng, "device", None), "models_loaded": eng.loaded() if eng is not None else []}

    def caps_json() -> list[dict[str, Any]]:
        cs = store.setting("cap_status") or {}
        out = []
        for c in capmod.CAPABILITIES:
            s = cs.get(c.id, {})
            state = "available" if not c.stages else s.get("state", "unknown")
            out.append({"id": c.id, "name": c.name, "description": c.description, "stages": list(c.stages),
                        "requires": list(c.requires), "version": s.get("version"), "state": state,
                        "reason": s.get("reason")})
        return out

    @r.get("/capabilities")
    def get_capabilities() -> list[dict[str, Any]]:
        return caps_json()

    @r.post("/capabilities/check")
    def check_capabilities(body: dict[str, Any] = OPT_BODY) -> list[dict[str, Any]]:
        from bioscan import plugin
        from bioscan.service import stages

        ids = strs(body.get("ids") or [c.id for c in capmod.CAPABILITIES if c.stages], "ids")
        capmod.expand(ids)
        cs = store.setting("cap_status") or {}
        try:
            eng, why = holder.get(), None
        except Exception as exc:  # noqa: BLE001 - no engine: every model capability is unavailable
            eng, why = None, f"{type(exc).__name__}: {exc}"
        for cid in ids:
            if not capmod.BY_ID[cid].stages:
                continue
            reason = why
            if eng is not None:
                try:
                    eng.ensure(plugin.plan(capmod.stages_for([cid]), stages.resolve_options(None)).models)
                except Exception as exc:  # noqa: BLE001 - the reason is the answer
                    reason = f"{type(exc).__name__}: {exc}"
            cs[cid] = {"state": "unavailable" if reason else "available", "reason": reason,
                       "version": cs.get(cid, {}).get("version")}
        with store.tx() as c:
            st.Store.put_setting(c, "cap_status", cs)
        return caps_json()

    def preset_json(p: dict[str, Any]) -> dict[str, Any]:
        return {"id": p["id"], "name": p["name"], "capabilities": loads(p["capabilities"]), "builtin": bool(p["builtin"])}

    @r.get("/presets")
    def get_presets() -> list[dict[str, Any]]:
        return [preset_json(p) for p in store.all("SELECT * FROM presets ORDER BY builtin DESC, created_at")]

    @r.post("/presets", status_code=201)
    def post_preset(body: dict[str, Any] = BODY) -> dict[str, Any]:
        name, caps = text(body, "name", required=True), capmod.expand(strs(body.get("capabilities"), "capabilities"))
        pid, t = st.new_id("p"), now()
        with store.tx() as c:
            c.execute("INSERT INTO presets VALUES (?, ?, ?, 0, ?, ?)", (pid, name, dumps(caps), t, t))
        return preset_json(store.one("SELECT * FROM presets WHERE id = ?", (pid,)))

    @r.put("/presets/{pid}")
    def put_preset(pid: str, body: dict[str, Any] = BODY) -> dict[str, Any]:
        p = store.one("SELECT * FROM presets WHERE id = ?", (pid,))
        if p is None:
            raise NotFound(f"no preset {pid}")
        name = text(body, "name", required=True) if "name" in body else p["name"]
        caps = capmod.expand(strs(body["capabilities"], "capabilities")) if "capabilities" in body else \
            loads(p["capabilities"])
        with store.tx() as c:
            c.execute("UPDATE presets SET name = ?, capabilities = ?, updated_at = ? WHERE id = ?",
                      (name, dumps(caps), now(), pid))
        return preset_json(store.one("SELECT * FROM presets WHERE id = ?", (pid,)))

    @r.delete("/presets/{pid}")
    def delete_preset(pid: str) -> dict[str, Any]:
        p = store.one("SELECT * FROM presets WHERE id = ?", (pid,))
        if p is None:
            raise NotFound(f"no preset {pid}")
        if p["builtin"]:
            raise Error("内建预设不可删除")
        with store.tx() as c:
            c.execute("DELETE FROM presets WHERE id = ?", (pid,))
            if store.setting("default_preset") == pid:          # never leave the default dangling
                st.Store.put_setting(c, "default_preset", BUILTIN_DEFAULT)
        return {"ok": True, "default_preset": store.setting("default_preset")}

    def settings_json() -> dict[str, Any]:
        return {k: store.setting(k) for k in ("run_mode", "default_preset", "capability_versions")}

    @r.get("/settings")
    def get_settings() -> dict[str, Any]:
        return settings_json()

    @r.put("/settings")
    def put_settings(body: dict[str, Any] = BODY) -> dict[str, Any]:
        new = {}
        if "run_mode" in body:
            new["run_mode"] = run_mode(body["run_mode"])
        if "default_preset" in body:
            if store.one("SELECT 1 FROM presets WHERE id = ?", (body["default_preset"],)) is None:
                raise Error(f"unknown preset {body['default_preset']}")
            new["default_preset"] = body["default_preset"]
        if "capability_versions" in body:
            v = body["capability_versions"]
            if not isinstance(v, dict) or set(v) - set(capmod.BY_ID):
                raise Error("capability_versions must be {capability: version}")
            new["capability_versions"] = v
        with store.tx() as c:
            for k, v in new.items():
                st.Store.put_setting(c, k, v)
        return settings_json()

    def cache_usage() -> tuple[int, int]:
        files = size = 0
        for d, _, names in os.walk(store.cache_dir):
            for n in names:
                files += 1
                size += os.path.getsize(os.path.join(d, n))
        return files, size

    @r.get("/storage")
    def storage() -> dict[str, Any]:
        files, size = cache_usage()
        db = sum(p.stat().st_size for p in store.data_dir.glob("gardener.sqlite*"))
        return {"data_dir": str(store.data_dir), "db_path": str(store.db_path), "db_bytes": db,
                "cache_dir": str(store.cache_dir), "cache_bytes": size, "cache_files": files}

    @r.post("/storage/clear-cache")
    def clear_cache() -> dict[str, Any]:
        """Deletes only cache/; records stay, previews read pending and rebuild on request (§2)."""
        removed = freed = 0
        for d, _, names in os.walk(store.cache_dir):
            for n in names:
                p = os.path.join(d, n)
                try:                  # a running preview may rename or drop its .part meanwhile
                    size = os.path.getsize(p)
                    os.remove(p)
                except FileNotFoundError:
                    continue
                freed += size
                removed += 1
        with store.tx() as c:
            c.execute("UPDATE assets SET preview_status = 'pending', preview_method = NULL, preview_error = NULL, "
                      "updated_at = ? WHERE preview_status != 'pending'", (now(),))
        return {"removed_files": removed, "freed_bytes": freed}

    # ---- tags ----
    def tag_or_404(tid: str) -> dict[str, Any]:
        t = store.one("SELECT * FROM tags WHERE id = ?", (tid,))
        if t is None:
            raise NotFound(f"no tag {tid}")
        return t

    def tag_history(c: Any, tid: str, action: str, detail: dict[str, Any]) -> None:
        c.execute("INSERT INTO tag_history (tag_id, action, detail, at) VALUES (?, ?, ?, ?)",
                  (tid, action, dumps(detail), now()))

    def new_tag(c: Any, name: str, parent_id: str | None) -> str:
        tid, t = f"user:{st.new_id('t')[2:]}", now()
        c.execute("INSERT INTO tags (id, name, parent_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                  (tid, name, parent_id, t, t))
        tag_history(c, tid, "create", {"name": name, "parent_id": parent_id})
        return tid

    def ancestors(tid: str | None) -> list[str]:
        """`tid` and its parents up the chain (stops at a cycle already stored)."""
        out: list[str] = []
        while tid is not None and tid not in out:
            out.append(tid)
            row = store.one("SELECT parent_id FROM tags WHERE id = ?", (tid,))
            tid = row["parent_id"] if row else None
        return out

    def one_tag(tid: str) -> dict[str, Any]:
        return next(t for t in store.tags() if t["id"] == tid)

    @r.get("/tags")
    def get_tags() -> list[dict[str, Any]]:
        return store.tags()

    @r.post("/tags", status_code=201)
    def post_tag(body: dict[str, Any] = BODY) -> dict[str, Any]:
        name, parent = text(body, "name", required=True), body.get("parent_id")
        if parent is not None:
            tag_or_404(parent)
        with store.tx() as c:
            tid = new_tag(c, name, parent)
        return one_tag(tid)

    @r.patch("/tags/{tid}")
    def patch_tag(tid: str, body: dict[str, Any] = BODY) -> dict[str, Any]:
        t = tag_or_404(tid)
        changes: dict[str, Any] = {}
        if "name" in body:
            changes["name"] = text(body, "name", required=True)
        if "parent_id" in body:
            if body["parent_id"] is not None:
                tag_or_404(body["parent_id"])
                if tid in ancestors(body["parent_id"]):
                    raise Error("标签层级不能形成循环")
            changes["parent_id"] = body["parent_id"]
        if "aliases" in body:
            changes["aliases"] = dumps(strs(body["aliases"], "aliases"))
        if "active" in body:
            if not isinstance(body["active"], bool):
                raise Error("active must be true or false")
            changes["active"] = int(body["active"])
        if changes:
            with store.tx() as c:
                c.execute(f"UPDATE tags SET {', '.join(f'{k} = ?' for k in changes)}, updated_at = ? WHERE id = ?",
                          [*changes.values(), now(), tid])
                tag_history(c, tid, "update", {k: [t[k], v] for k, v in changes.items()})
                c.execute("UPDATE assets SET updated_at = ? WHERE id IN (SELECT asset_id FROM asset_tags WHERE tag_id = ?)",
                          (now(), tid))
        return one_tag(tid)

    @r.post("/tags/{tid}/merge")
    def merge_tag(tid: str, body: dict[str, Any] = BODY) -> dict[str, Any]:
        src = tag_or_404(tid)
        into = text(body, "into", required=True)
        target = tag_or_404(into)
        if into == tid:
            raise Error("cannot merge a tag into itself")
        if src["merged_into"] or target["merged_into"]:     # merges never chain, so they never cycle
            raise Error("已合并的标签不能再合并或作为合并目标")
        t = now()
        with store.tx() as c:
            c.execute("UPDATE assets SET updated_at = ? WHERE id IN (SELECT asset_id FROM asset_tags WHERE tag_id = ?)",
                      (t, tid))
            c.execute("INSERT OR IGNORE INTO asset_tags SELECT asset_id, ?, source, run_id, confidence, created_at "
                      "FROM asset_tags WHERE tag_id = ?", (into, tid))
            c.execute("DELETE FROM asset_tags WHERE tag_id = ?", (tid,))
            below = {r[0] for r in c.execute(
                "WITH RECURSIVE d(id) AS (SELECT id FROM tags WHERE parent_id = ? "
                "UNION SELECT t.id FROM tags t JOIN d ON t.parent_id = d.id) SELECT id FROM d", (tid,))}
            if into in below:          # merging into a descendant: it takes the merged tag's place first
                c.execute("UPDATE tags SET parent_id = ? WHERE id = ?", (src["parent_id"], into))
            c.execute("UPDATE tags SET parent_id = ? WHERE parent_id = ? AND id != ?", (into, tid, into))
            c.execute("UPDATE tags SET merged_into = ?, active = 0, updated_at = ? WHERE id = ?", (into, t, tid))
            tag_history(c, tid, "merge", {"into": into})
            tag_history(c, into, "merged_from", {"from": tid})
        return one_tag(into)

    @r.post("/tags/{tid}/split", status_code=201)
    def split_tag(tid: str, body: dict[str, Any] = BODY) -> list[dict[str, Any]]:
        """New child tags; `assign` puts assets on them as a person's tags (AI tags stay where they are)."""
        tag_or_404(tid)
        names = strs(body.get("names"), "names")
        assign = body.get("assign") or {}
        if not names or not isinstance(assign, dict) or set(assign) - set(names):
            raise Error("names must be non-empty; assign maps those names to asset ids")
        for ids in assign.values():
            check_assets(strs(ids, "assign"))
        t = now()
        with store.tx() as c:
            made = {n: new_tag(c, n, tid) for n in names}
            for n, ids in assign.items():
                for a in ids:
                    c.execute("INSERT OR IGNORE INTO asset_tags VALUES (?, ?, 'user', NULL, NULL, ?)", (a, made[n], t))
                    c.execute("UPDATE assets SET updated_at = ? WHERE id = ?", (t, a))
            if body.get("deactivate_source"):
                c.execute("UPDATE tags SET active = 0, updated_at = ? WHERE id = ?", (t, tid))
            tag_history(c, tid, "split", {"into": list(made.values()),
                                          "deactivated": bool(body.get("deactivate_source"))})
        return [one_tag(x) for x in made.values()]

    @r.post("/assets/tags")
    def tag_assets(body: dict[str, Any] = BODY) -> dict[str, Any]:
        ids, tid = strs(body.get("asset_ids"), "asset_ids"), text(body, "tag_id", required=True)
        tag_or_404(tid)
        check_assets(ids)
        t, n = now(), 0
        with store.tx() as c:
            for a in ids:
                if body.get("remove"):
                    n += c.execute("DELETE FROM asset_tags WHERE asset_id = ? AND tag_id = ? AND source = 'user'",
                                   (a, tid)).rowcount
                else:
                    n += c.execute("INSERT OR IGNORE INTO asset_tags VALUES (?, ?, 'user', NULL, NULL, ?)",
                                   (a, tid, t)).rowcount
                c.execute("UPDATE assets SET updated_at = ? WHERE id = ?", (t, a))
        return {"changed": n}

    # ---- 6.2 tasks and runs ----
    @r.post("/tasks", status_code=201)
    def post_task(body: dict[str, Any] = BODY) -> dict[str, Any]:
        title = text(body, "title", required=True)
        sources = source_paths(body.get("sources"))
        default = store.setting("default_preset")
        if store.one("SELECT 1 FROM presets WHERE id = ?", (default,)) is None:
            default = BUILTIN_DEFAULT
        preset = body.get("preset") or (None if body.get("capabilities") is not None else default)
        caps = caps_of(body, preset)
        mode = run_mode(body.get("run_mode") or store.setting("run_mode"))
        tz = timezone(body.get("timezone"))
        tid, t = st.new_id("t"), now()
        with store.tx() as c:
            c.execute("INSERT INTO tasks (id, title, subtitle, description, preset, capabilities, run_mode, timezone, "
                      "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                      (tid, title, text(body, "subtitle"), text(body, "description"), preset, dumps(caps), mode, tz, t, t))
            rid = tasks.queue_run(c, tid, "scan", capabilities=caps, run_mode=mode, preset=preset, sources=sources)
            for p in sources:
                c.execute("INSERT INTO sources (id, task_id, path, added_at, added_run_id) VALUES (?, ?, ?, ?, ?)",
                          (st.new_id("s"), tid, p, t, rid))
        worker.wake()
        return task_or_404(tid)

    @r.get("/tasks")
    def get_tasks(archived: str = "0") -> list[dict[str, Any]]:
        cond = {"0": "archived_at IS NULL", "1": "archived_at IS NOT NULL", "all": "1"}.get(archived)
        if cond is None:
            raise Error("archived must be 0|1|all")
        return [store.task(x["id"]) for x in store.all(f"SELECT id FROM tasks WHERE {cond} ORDER BY created_at DESC")]

    @r.get("/tasks/{tid}")
    def get_task(tid: str) -> dict[str, Any]:
        return task_or_404(tid, with_runs=True)

    @r.patch("/tasks/{tid}")
    def patch_task(tid: str, body: dict[str, Any] = BODY) -> dict[str, Any]:
        task_or_404(tid)
        changes: dict[str, Any] = {}
        if "title" in body:
            changes["title"] = text(body, "title", required=True)
        for k in ("subtitle", "description"):
            if k in body:
                changes[k] = text(body, k)
        if "capabilities" in body:
            changes["capabilities"] = dumps(capmod.expand(strs(body["capabilities"], "capabilities")))
        if "run_mode" in body:
            changes["run_mode"] = run_mode(body["run_mode"])
        if "timezone" in body:
            changes["timezone"] = timezone(body["timezone"])
        if changes:
            with store.tx() as c:
                c.execute(f"UPDATE tasks SET {', '.join(f'{k} = ?' for k in changes)}, updated_at = ?, "
                          "version = version + 1 WHERE id = ?", [*changes.values(), now(), tid])
        return task_or_404(tid, with_runs=True)

    def new_run(tid: str, kind: str, *, caps: list[str] | None = None, sources: list[str] | None = None,
                asset_ids: list[str] | None = None, add_sources: bool = False) -> dict[str, Any]:
        t = store.one("SELECT * FROM tasks WHERE id = ?", (tid,))
        if t is None:
            raise NotFound(f"no task {tid}")
        with store.tx() as c:          # the run and its source rows commit together, before the worker wakes
            rid = tasks.queue_run(c, tid, kind, capabilities=caps or loads(t["capabilities"]), run_mode=t["run_mode"],
                                  preset=t["preset"], sources=sources, asset_ids=asset_ids)
            for p in sources if add_sources else []:
                c.execute("INSERT OR IGNORE INTO sources (id, task_id, path, added_at, added_run_id) VALUES (?, ?, ?, ?, ?)",
                          (st.new_id("s"), tid, p, now(), rid))
        worker.wake()
        return store.run(rid)

    @r.post("/tasks/{tid}/sources", status_code=201)
    def add_sources(tid: str, body: dict[str, Any] = BODY) -> dict[str, Any]:
        task_or_404(tid)
        paths = source_paths(body.get("paths"))
        return new_run(tid, "append", sources=paths, add_sources=True)

    @r.post("/tasks/{tid}/pause")
    def pause_task(tid: str) -> dict[str, Any]:
        task_or_404(tid)
        tasks.pause(store, worker, tid)
        return task_or_404(tid, with_runs=True)

    @r.post("/tasks/{tid}/resume")
    def resume_task(tid: str) -> dict[str, Any]:
        task_or_404(tid)
        tasks.resume(store, worker, tid)
        return task_or_404(tid, with_runs=True)

    @r.post("/tasks/{tid}/stop")
    def stop_task(tid: str) -> dict[str, Any]:
        task_or_404(tid)
        tasks.stop(store, worker, tid)
        return task_or_404(tid, with_runs=True)

    @r.post("/tasks/{tid}/archive")
    def archive_task(tid: str) -> dict[str, Any]:
        task_or_404(tid)
        with store.tx() as c:
            c.execute("UPDATE tasks SET archived_at = coalesce(archived_at, ?) WHERE id = ?", (now(), tid))
        return task_or_404(tid)

    @r.post("/tasks/{tid}/unarchive")
    def unarchive_task(tid: str) -> dict[str, Any]:
        task_or_404(tid)
        with store.tx() as c:
            c.execute("UPDATE tasks SET archived_at = NULL WHERE id = ?", (tid,))
        return task_or_404(tid)

    @r.post("/tasks/{tid}/verify-sources")
    def verify(tid: str) -> dict[str, Any]:
        task_or_404(tid)
        rid = tasks.verify_sources(store, worker, tk, tid)
        return task_or_404(tid, with_runs=True) | {"queued_run_id": rid}

    def task_asset_ids(tid: str, ids: Any) -> list[str] | None:
        if ids is None:
            return None
        ids = strs(ids, "asset_ids")
        inside = {x["asset_id"] for x in store.all("SELECT asset_id FROM task_assets WHERE task_id = ?", (tid,))}
        if set(ids) - inside:
            raise Error(f"assets not in this task: {sorted(set(ids) - inside)[:5]}")
        return ids

    @r.post("/tasks/{tid}/rescan", status_code=201)
    def rescan(tid: str, body: dict[str, Any] = OPT_BODY) -> dict[str, Any]:
        task_or_404(tid)
        caps = capmod.expand(strs(body["capabilities"], "capabilities")) if body.get("capabilities") else None
        return new_run(tid, "rescan", caps=caps, asset_ids=task_asset_ids(tid, body.get("asset_ids")))

    @r.post("/tasks/{tid}/retry", status_code=201)
    def retry(tid: str, body: dict[str, Any] = OPT_BODY) -> dict[str, Any]:
        task_or_404(tid)
        ids = task_asset_ids(tid, body.get("asset_ids"))
        if ids is None:
            ids = [x["id"] for x in store.all(
                "SELECT a.id FROM task_assets ta JOIN assets a ON a.id = ta.asset_id WHERE ta.task_id = ? AND "
                "(a.preview_status IN ('failed', 'pending') OR EXISTS (SELECT 1 FROM asset_caps c WHERE "
                "c.asset_id = a.id AND c.state IN ('failed', 'unavailable', 'pending')))", (tid,))]
        return new_run(tid, "retry", asset_ids=ids)

    @r.get("/runs/{rid}")
    def get_run(rid: str) -> dict[str, Any]:
        run = store.run(rid)
        if run is None:
            raise NotFound(f"no run {rid}")
        return run

    # ---- 6.3 assets ----
    @r.get("/assets")
    def get_assets(request: Request, order: str = "time", limit: int = LIMIT200,
                   offset: int = OFFSET) -> dict[str, Any]:
        filters = {k: v for k, v in request.query_params.items() if k not in ("order", "limit", "offset")}
        return store.search(filters, order=order, limit=limit, offset=offset)

    @r.get("/assets/{aid}")
    def get_asset(aid: str) -> dict[str, Any]:
        return store.asset_detail(aid) | {"managed": imports.managed(store, aid)}

    def preview_file(aid: str, which: int) -> FileResponse:
        a = asset_or_404(aid)
        path = tasks.preview_paths(store, aid)[which]
        if not path.exists():
            if not a["online"]:
                raise NotFound("原片离线，且没有缓存的预览")
            if not tasks.build_preview(store, tk, aid, a["primary_path"], a["media"]) or not path.exists():
                a = asset_or_404(aid)
                raise NotFound(f"无法生成预览: {a['preview_error'] or '原片不可读'}")
        return FileResponse(path, media_type="image/jpeg")

    @r.get("/assets/{aid}/thumb")
    def thumb(aid: str) -> FileResponse:
        return preview_file(aid, 0)

    @r.get("/assets/{aid}/large")
    def large(aid: str) -> FileResponse:
        return preview_file(aid, 1)

    @r.get("/assets/{aid}/original")
    def original(aid: str) -> FileResponse:
        """The primary file as is (Range supported, for video seeking)."""
        a = asset_or_404(aid)
        if not os.path.isfile(a["primary_path"]):
            raise NotFound("原片离线或已不存在")
        return FileResponse(a["primary_path"], media_type=mimetypes.guess_type(a["primary_path"])[0]
                            or "application/octet-stream")

    # ---- contract revision 5C: original-pixel detail (detail.py) ----
    def original_of(aid: str) -> str:
        """The file detail reads: the asset's online RAW, else its primary. 400 for a video, 404 offline."""
        a = asset_or_404(aid)
        if a["media"] != "photo":
            raise Error("只有照片有原像素细节")
        files = store.all("SELECT path, role FROM asset_files WHERE asset_id = ?", (aid,))
        path = next((f["path"] for f in files if f["role"] == "raw" and os.path.isfile(f["path"])), a["primary_path"])
        if not os.path.isfile(path):
            raise NotFound("原片离线或已不存在：只有快速预览")
        return path

    @r.get("/assets/{aid}/detail")
    def asset_detail_crop(aid: str, xyxy: str = "0,0,1,1",
                          edge: int = Query(detail.DEFAULT_EDGE, ge=64, le=detail.MAX_EDGE, alias="max")) -> Response:
        """A JPEG crop of the original pixels (the RAW of a RAW+JPEG pair) for `xyxy` (0-1 of the upright
        image), long edge <= `max`; X-Detail-* headers say what it is. Off the request threads' work:
        decoded on the detail pool, superseded by a newer request for the same asset, time-limited."""
        box = detail.parse_xyxy(xyxy)
        path = original_of(aid)
        try:
            got = details.get(aid, path, box, edge)
        except detail.Superseded:
            raise Conflict("已被同一素材更新的细节请求取代") from None
        except TimeoutError:
            raise st.Unavailable("原像素解码超时，可稍后重试（解码完成后会缓存）") from None
        except Exception as exc:  # noqa: BLE001 - an unreadable original is a reason, not a crash
            raise NotFound(f"无法读取原像素：{type(exc).__name__}: {exc}") from None
        return Response(got.jpeg, media_type="image/jpeg", headers=got.headers())
    # ---- end revision 5C ----

    # ---- contract revision 7C: head / eye crop suggestions (parts.py) ----
    @r.get("/assets/{aid}/parts")
    def asset_parts(aid: str, subject: str) -> dict[str, Any]:
        """OWLv2's head and eye inside one live subject box, on its original pixels; only hits at or above
        parts.FLOOR. On demand: nothing is stored and identify does not change."""
        t0 = time.perf_counter()
        path = original_of(aid)
        row = store.one("SELECT xyxy, kind FROM subjects WHERE id = ? AND asset_id = ? AND removed_at IS NULL",
                        (subject, aid))
        if row is None:
            raise NotFound(f"no live subject {subject} on asset {aid}")
        box = [float(x) for x in loads(row["xyxy"])]
        try:
            crop, file = details.region(aid, path, tuple(box))
        except TimeoutError:
            raise st.Unavailable("原像素解码超时，可稍后重试（解码完成后会缓存）") from None
        except Exception as exc:  # noqa: BLE001
            raise NotFound(f"无法读取原像素：{type(exc).__name__}: {exc}") from None
        try:
            eng = holder.get()
        except Exception as exc:  # noqa: BLE001 - no engine: the reason is the answer
            raise st.Unavailable(f"OWLv2 不可用：{type(exc).__name__}: {exc}") from None
        with getattr(eng, "device_lock", None) or nullcontext():      # never two model callers on the device
            try:
                eng.ensure(["owlv2"])
            except Exception as exc:  # noqa: BLE001 - no detector: the reason is the answer
                raise st.Unavailable(f"OWLv2 不可用：{type(exc).__name__}: {exc}") from None
            found = parts.find(eng.owlv2, crop, box, row["kind"])
        return {"asset_id": aid, "subject": subject, "kind": row["kind"], "file": file, "parts": found,
                "floor": parts.FLOOR, "ms": round((time.perf_counter() - t0) * 1000)}
    # ---- end revision 7C ----

    @r.post("/assets/{aid}/reveal")
    def reveal(aid: str) -> dict[str, Any]:
        a = asset_or_404(aid)
        path = a["primary_path"]       # only a file this asset registered, never a path from the client
        if not os.path.exists(path):
            raise NotFound("原片离线或已不存在")
        tk.reveal(path)
        return {"ok": True}

    @r.post("/assets/{aid}/split-version")
    def split_version(aid: str) -> dict[str, Any]:
        """A person's "not the same capture": the asset stands on its own from now on and is never linked
        again; its auto collection places come back and its image capabilities read pending (retry runs them)."""
        asset_or_404(aid)
        with store.tx() as c:
            vers.set_version(c, aid, None, list(tasks.MODEL_CAPS), now())
            c.execute("UPDATE assets SET version_lock = 1 WHERE id = ?", (aid,))
        return store.asset_detail(aid)

    @r.post("/marks")
    def post_marks(body: dict[str, Any] = BODY, actor: str = ACTOR) -> dict[str, Any]:
        ids = strs(body.get("asset_ids"), "asset_ids")
        fields: dict[str, Any] = {}
        if "use" in body:              # contract revision 5B: the four uses; `flag` is the older form
            if body["use"] not in st.USES or "flag" in body:
                raise Error("use must be record|edit|pending|reject (and not sent with flag)")
            fields["use"] = None if body["use"] == "pending" else body["use"]
        if "flag" in body:             # omitted = unchanged, null = clear (§10 X5)
            if body["flag"] not in ("pick", "reject", None):
                raise Error("flag must be pick|reject|null")
            fields["flag"] = body["flag"]
        if "stars" in body:
            if not isinstance(body["stars"], int) or isinstance(body["stars"], bool) or not 0 <= body["stars"] <= 5:
                raise Error("stars must be 0..5")
            fields["stars"] = body["stars"]
        if not ids or not fields:
            raise Error("asset_ids and use, flag or stars are required")
        expect = body.get("expect")
        if expect is not None and (not isinstance(expect, dict) or not all(isinstance(v, int) for v in expect.values())):
            raise Error("expect must be {asset_id: version}")
        reason = body.get("reason")
        return store.set_marks(ids, fields, actor=actor, reason=reason if isinstance(reason, str) else None,
                               expect=expect)

    @r.post("/marks/undo")
    def undo_marks(body: dict[str, Any] = BODY, actor: str = ACTOR) -> dict[str, Any]:
        return store.undo(text(body, "batch_id", required=True), actor)

    @r.post("/identifications")
    def post_identifications(body: dict[str, Any] = BODY, actor: str = ACTOR) -> dict[str, Any]:
        """A person's species claim on subjects (contract revision 4, S3)."""
        sids = strs(body.get("subject_ids") or [], "subject_ids")
        new = body.get("new_subjects") or []
        if not isinstance(new, list) or not all(
                isinstance(n, dict) and isinstance(n.get("asset_id"), str) and isinstance(n.get("xyxy"), list)
                and len(n["xyxy"]) == 4 and all(isinstance(v, (int, float)) for v in n["xyxy"])
                and 0 <= n["xyxy"][0] < n["xyxy"][2] <= 1 and 0 <= n["xyxy"][1] < n["xyxy"][3] <= 1 for n in new):
            raise Error("new_subjects must be [{asset_id, xyxy: [x0, y0, x1, y1] within 0..1}]")
        if not sids and not new:
            raise Error("subject_ids or new_subjects is required")
        claim = body.get("claim")
        if not isinstance(claim, dict):
            raise Error("claim is required")
        if not claim.get("accept"):
            level = claim.get("level")
            if level not in identities.LEVELS:
                raise Error(f"claim.level must be one of {', '.join(identities.LEVELS)}, or claim.accept")
            if level == "species" and not (isinstance(claim.get("concept"), str) and isinstance(claim.get("taxon"), str)):
                raise Error("a species claim needs concept and taxon (the scientific name)")
            if level in ("genus", "family") and not isinstance(claim.get("taxon"), str):
                raise Error(f"a {level} claim needs taxon")
            if level not in ("species", "genus", "family"):
                claim = {"level": level, "concept": None, "taxon": None, "common": None}
            elif level != "species":
                claim = claim | {"concept": None, "common": None}
        scope = body.get("scope", "subject")
        if scope not in ("subject", "burst", "encounter"):
            raise Error("scope must be subject|burst|encounter")
        reason = body.get("reason")
        with store.tx() as c:
            for n in new:
                if not c.execute("SELECT 1 FROM assets WHERE id = ?", (n["asset_id"],)).fetchone():
                    raise NotFound(f"no asset {n['asset_id']}")
            return identities.identify(c, sids, new, claim, scope=scope, actor=actor,
                                       reason=reason if isinstance(reason, str) else None,
                                       dry_run=bool(body.get("dry_run")))

    @r.post("/identifications/undo")
    def undo_identifications(body: dict[str, Any] = BODY) -> dict[str, Any]:
        with store.tx() as c:
            return identities.undo(c, text(body, "batch_id", required=True))

    @r.get("/species")
    def get_species(task: str | None = None, hidden: str = "0") -> dict[str, Any]:
        """Species cards derived from identifications (contract revision 4, S4); task= narrows to one task."""
        if task is not None:
            task_or_404(task)
        return identities.species(store.conn(), task, include_hidden=hidden == "1")

    @r.get("/names")
    def get_names(q: str = "", limit: int = LIMIT100) -> list[dict[str, Any]]:
        """Taxa by any of their names (scientific, English, synonyms, replaced names, the person's Chinese)."""
        return taxa.search(store.conn(), q, min(limit, 100))

    @r.patch("/species/{concept}")
    def patch_species(concept: str, body: dict[str, Any] = BODY) -> dict[str, Any]:
        kw: dict[str, Any] = {}
        if "cover_asset_id" in body:
            cover = body["cover_asset_id"]
            if cover is not None and not store.one("SELECT 1 FROM current_ids WHERE asset_id = ? AND concept = ?",
                                                   (cover, concept)):
                raise Error("cover_asset_id must be a photo of this species (or null)")
            kw["cover_asset_id"] = cover
        if "hidden" in body:
            if not isinstance(body["hidden"], bool):
                raise Error("hidden must be true|false")
            kw["hidden"] = body["hidden"]
        if not kw:
            raise Error("cover_asset_id or hidden is required")
        with store.tx() as c:
            identities.set_prefs(c, concept, **kw)
            card = next((x for k in ("species", "to_confirm") for x in identities.species(c, include_hidden=True)[k]
                         if x["concept"] == concept), None)
            if card is None:                                  # raised inside: the prefs row rolls back
                raise NotFound(f"no photo of {concept}")
        return card

    # ---- contract revision 5B: keep-best, encounters ----
    @r.post("/marks/keep-best")
    def keep_best(body: dict[str, Any] = BODY, actor: str = ACTOR) -> dict[str, Any]:
        """保留此张并淘汰相似帧: dry_run first shows the reach (rejected and skipped frames)."""
        use = body.get("keep_use", "record")
        if use not in ("record", "edit"):
            raise Error("keep_use must be record|edit")
        expect = body.get("expect")
        if expect is not None and (not isinstance(expect, dict) or not all(isinstance(v, int) for v in expect.values())):
            raise Error("expect must be {asset_id: version}")
        with store.tx() as c:
            return rv.keep_best(c, text(body, "asset_id", required=True), keep_use=use, actor=actor,
                                    dry_run=bool(body.get("dry_run")), expect=expect)

    @r.get("/encounters")
    def get_encounters(task: str | None = None, concept: str | None = None) -> dict[str, Any]:
        if not task and not concept:
            raise Error("task or concept is required")
        if task:
            task_or_404(task)
        return rv.encounters(store.conn(), task_id=task, concept=concept)

    @r.post("/encounters/{eid}/split", status_code=201)
    def split_encounter(eid: str, body: dict[str, Any] = BODY, actor: str = ACTOR) -> dict[str, Any]:
        """{at: asset_id} moves that photo and the later ones, {asset_ids} exactly those, to a new encounter."""
        at = body.get("at")
        ids = strs(body["asset_ids"], "asset_ids") if body.get("asset_ids") is not None else None
        if (at is None) == (ids is None) or (at is not None and not isinstance(at, str)):
            raise Error("one of at (asset id) or asset_ids is required")
        with store.tx() as c:
            new = rv.split_encounter(c, eid, at=at, asset_ids=ids, actor=actor)
        return {"id": new, "from": eid}

    @r.post("/encounters/merge")
    def merge_encounters(body: dict[str, Any] = BODY, actor: str = ACTOR) -> dict[str, Any]:
        with store.tx() as c:
            return {"id": rv.merge_encounters(c, strs(body.get("ids"), "ids"), actor=actor)}

    @r.get("/marks/history")
    def mark_history(asset_id: str | None = None, batch_id: str | None = None, actor: str | None = None,
                     limit: int = LIMIT100) -> list[dict[str, Any]]:
        conds, ps = [], []
        for k, v in (("asset_id", asset_id), ("batch_id", batch_id), ("actor", actor)):
            if v:
                conds.append(f"{k} = ?")
                ps.append(v)
        rows = store.all(f"SELECT * FROM mark_history WHERE {' AND '.join(conds) or '1'} ORDER BY id DESC LIMIT ?",
                         [*ps, limit])
        return [{"id": x["id"], "batch_id": x["batch_id"], "asset_id": x["asset_id"], "field": x["field"],
                 "old": loads(x["old"]), "new": loads(x["new"]), "actor": x["actor"], "reason": x["reason"],
                 "undone": bool(x["undone"]), "at": x["at"]} for x in rows]

    # ---- 6.5 collections ----
    def check_rule(rule: Any, self_id: str | None = None) -> dict[str, Any]:
        if not isinstance(rule, dict):
            raise Error("rule must be a JSON object of asset filters")
        query.where(store, rule)
        if query.cycle(store, rule, self_id):
            raise Error("规则引用的集合形成循环（或嵌套过深）")
        return rule

    @r.post("/collections/{cid}/preview-rule")
    def preview_rule(cid: str, body: dict[str, Any] = BODY) -> dict[str, Any]:
        """Contract revision 5B (PRD AC13): what a changed rule would hold, against the current members."""
        if col_or_404(cid)["kind"] != "smart":
            raise Error("only a smart collection has a rule")
        rule = check_rule(body.get("rule"), cid)
        current, hits = store.ids_where({"collection": cid}), store.ids_where(rule)
        now_set, hit_set = set(current), set(hits)
        added, removed = [i for i in hits if i not in now_set], [i for i in current if i not in hit_set]
        return {"total": len(hits), "current": len(current), "added_count": len(added), "removed_count": len(removed),
                "added": added[:PREVIEW_IDS], "removed": removed[:PREVIEW_IDS]}

    @r.get("/collections")
    def get_collections(task: str | None = None, kind: str | None = None, deleted: str = "0") -> list[dict[str, Any]]:
        return store.collections(task=task, kind=kind, deleted=deleted == "1")

    @r.get("/collections/{cid}")
    def get_collection(cid: str) -> dict[str, Any]:
        return store.collection(cid)

    @r.post("/collections", status_code=201)
    def post_collection(body: dict[str, Any] = BODY, actor: str = ACTOR) -> dict[str, Any]:
        kind = body.get("kind", "manual")
        if kind not in ("manual", "smart"):
            raise Error("kind must be manual|smart")
        name = text(body, "name", required=True)
        rule = check_rule(body.get("rule")) if kind == "smart" else None
        ids = strs(body.get("asset_ids") or [], "asset_ids") if kind == "manual" else []
        check_assets(ids)
        task_id = body.get("task_id")
        if task_id is not None:
            task_or_404(task_id)
        with store.tx() as c:
            cid = st.create_collection(c, name=name, kind=kind, task_id=task_id, description=text(body, "description"),
                                       rule=rule, asset_ids=ids, actor=actor)
            touch(c, ids)
        return store.collection(cid)

    def touch(c: Any, ids: list[str]) -> None:
        t = now()
        for i in ids:
            c.execute("UPDATE assets SET updated_at = ? WHERE id = ?", (t, i))

    @r.patch("/collections/{cid}")
    def patch_collection(cid: str, body: dict[str, Any] = BODY, actor: str = ACTOR) -> dict[str, Any]:
        col = col_or_404(cid)
        if "version" in body and body["version"] != col["version"]:
            raise Conflict("集合已被其他客户端修改", version=col["version"])
        if "name" in body and col["group_kind"] == "person" and col["origin"] == "auto":
            named = name_person_group(cid, people.check_name(body["name"]), actor)
            if named is not None:
                if "description" in body:
                    with store.tx() as c:
                        c.execute("UPDATE collections SET description = ?, updated_at = ? WHERE id = ?",
                                  (text(body, "description"), now(), named))
                return store.collection(named)
        changes: dict[str, Any] = {}
        if "name" in body:
            changes["name"] = text(body, "name", required=True)
            changes["user_edited"] = 1
        if "description" in body:
            changes["description"] = text(body, "description")
        if "rule" in body:
            if col["kind"] != "smart":
                raise Error("only a smart collection has a rule")
            changes["rule"] = dumps(check_rule(body["rule"], cid))
        if changes:
            with store.tx() as c:
                n = c.execute(f"UPDATE collections SET {', '.join(f'{k} = ?' for k in changes)}, updated_at = ?, "
                              "version = version + 1 WHERE id = ? AND version = ?",
                              [*changes.values(), now(), cid, col["version"]]).rowcount
                if not n:
                    raise Conflict("集合已被其他客户端修改")
                st.collection_history(c, cid, "rule" if "rule" in changes else "rename",
                                      {k: v for k, v in changes.items() if k != "user_edited"}, actor)
        return store.collection(cid)

    def name_person_group(cid: str, name: str, actor: str) -> str | None:
        """A person group gets a name: its faces become that person's confirmed faces (people.py) and
        every task regroups, so this person's other photos join it. None: not a group of one face cluster."""
        row = store.one("SELECT task_id, auto_key FROM collections WHERE id = ?", (cid,))
        k = row["auto_key"] or ""
        if k.startswith("person:named:"):
            with store.tx() as c:
                pid = people.rename(c, k.removeprefix("person:named:").split("#")[0], name, actor)
        elif k.startswith("person:") and not k.startswith("person:other"):
            from bioscan.gardener import faces

            found = tasks.people_faces(store, row["task_id"])
            per_asset = {a: [f["vec"] for f in found[a]] for a in store.ids_where({"collection": cid}) if a in found}
            if not per_asset:
                return None
            pick = faces.shared_face(per_asset)
            with store.tx() as c:
                pid = people.person_of(c, name)
                people.confirm(c, pid, [(a, i, per_asset[a][i]) for a, i in pick.items()], "collection")
                people.adopt(c, cid, pid, actor)
        else:
            return None
        regroup_people()
        live = store.one("SELECT id FROM collections WHERE task_id = ? AND auto_key = ? AND deleted_at IS NULL",
                         (row["task_id"], people.key(pid)))
        return live["id"] if live else cid

    def regroup_people() -> None:
        """Regroup every task that finds people, so a name reaches all of them now, not at the next scan."""
        for t in store.all("SELECT id, capabilities, timezone FROM tasks WHERE archived_at IS NULL"):
            caps = set(loads(t["capabilities"], []))
            if "people" in caps:
                tasks.regroup(store, tk, t["id"], caps, tz=t["timezone"], final=True)

    @r.get("/people")
    def get_people() -> list[dict[str, Any]]:
        return store.all("SELECT p.id, p.name, (SELECT count(*) FROM person_faces f WHERE f.person_id = p.id) faces "
                         "FROM people p ORDER BY p.name")

    def face_of(aid: str, idx: int) -> str:
        asset_or_404(aid)
        res = store.one("SELECT data FROM results WHERE asset_id = ? AND capability = 'people' AND status = 'done' "
                        "ORDER BY id DESC LIMIT 1", (aid,))
        fs = [f for f in (loads(res["data"], {}) or {}).get("faces", []) if f.get("vec")] if res else []
        if not 0 <= idx < len(fs):
            raise NotFound(f"asset {aid} has no face {idx}")
        return fs[idx]["vec"]

    @r.post("/assets/{aid}/faces/{idx}")
    def name_face(aid: str, idx: int, body: dict[str, Any] = BODY) -> dict[str, Any]:
        """Name one face: it is that person from now on, in every task (a new name creates the person)."""
        vec, name = face_of(aid, idx), people.check_name(body.get("name"))
        with store.tx() as c:
            people.confirm(c, people.person_of(c, name), [(aid, idx, vec)], "face")
        regroup_people()
        return store.asset_detail(aid)

    @r.delete("/assets/{aid}/faces/{idx}")
    def unname_face(aid: str, idx: int) -> dict[str, Any]:
        face_of(aid, idx)
        with store.tx() as c:
            c.execute("DELETE FROM person_faces WHERE asset_id = ? AND face = ?", (aid, idx))
            c.execute("UPDATE assets SET updated_at = ? WHERE id = ?", (now(), aid))
        regroup_people()
        return store.asset_detail(aid)

    def manual(cid: str, what: str) -> dict[str, Any]:
        col = col_or_404(cid)
        if col["kind"] != "manual":
            raise Error(f"Smart Collection 不能{what}；可先另存为 Manual")
        return col

    @r.post("/collections/{cid}/members")
    def members(cid: str, body: dict[str, Any] = BODY, actor: str = ACTOR) -> dict[str, Any]:
        manual(cid, "直接调整成员")
        add, rem = strs(body.get("add") or [], "add"), strs(body.get("remove") or [], "remove")
        check_assets(add)
        with store.tx() as c:
            n_add = st.add_members(c, cid, add, "user")
            n_rem = st.remove_members(c, cid, rem, "user")
            st.collection_history(c, cid, "members", {"add": add, "remove": rem}, actor)
            touch(c, add + rem)
        return store.collection(cid) | {"added": n_add, "removed": n_rem}

    def common_task(cols: list[dict[str, Any]]) -> str | None:
        ts = {c["task_id"] for c in cols}
        return ts.pop() if len(ts) == 1 else None

    @r.post("/collections/{cid}/merge", status_code=201)
    def merge(cid: str, body: dict[str, Any] = BODY, actor: str = ACTOR) -> dict[str, Any]:
        cols = [col_or_404(x) for x in dict.fromkeys([cid, *strs(body.get("ids"), "ids")])]
        if len(cols) < 2 or any(c["deleted_at"] for c in cols):
            raise Error("merge needs two or more live collections")
        ids = list(dict.fromkeys(i for c in cols for i in store.ids_where({"collection": c["id"]})))
        name = text(body, "name") or " + ".join(c["name"] for c in cols)
        t = now()
        with store.tx() as c:
            new = st.create_collection(c, name=name, task_id=common_task(cols), asset_ids=ids, actor=actor)
            for col in cols:
                c.execute("UPDATE collections SET deleted_at = ?, updated_at = ?, version = version + 1 WHERE id = ?",
                          (t, t, col["id"]))
                st.collection_history(c, col["id"], "merge", {"into": new}, actor)
            st.collection_history(c, new, "merge", {"from": [x["id"] for x in cols]}, actor)
            touch(c, ids)
        return store.collection(new)

    @r.post("/collections/{cid}/split", status_code=201)
    def split(cid: str, body: dict[str, Any] = BODY, actor: str = ACTOR) -> dict[str, Any]:
        col = manual(cid, "拆分")
        ids, name = strs(body.get("asset_ids"), "asset_ids"), text(body, "name", required=True)
        current = set(store.ids_where({"collection": cid}))
        if not ids or set(ids) - current:
            raise Error("asset_ids must be members of the collection")
        with store.tx() as c:
            new = st.create_collection(c, name=name, task_id=col["task_id"], asset_ids=ids, actor=actor)
            st.remove_members(c, cid, ids, "user")
            st.collection_history(c, cid, "split", {"into": new, "asset_ids": ids}, actor)
            touch(c, ids)
        return store.collection(new)

    @r.post("/collections/{cid}/save-as-manual", status_code=201)
    def save_as_manual(cid: str, body: dict[str, Any] = OPT_BODY, actor: str = ACTOR) -> dict[str, Any]:
        col = col_or_404(cid)
        ids = store.ids_where({"collection": cid})
        with store.tx() as c:
            new = st.create_collection(c, name=text(body, "name") or f"{col['name']}（另存）", task_id=col["task_id"],
                                       asset_ids=ids, actor=actor)
            touch(c, ids)
        return store.collection(new)

    def review(c: Any, cid: str, action: str, ids: list[str], unhandled: int, actor: str,
               resolution: str | None = None, note: str | None = None) -> None:
        c.execute("INSERT INTO collection_reviews (collection_id, action, members, unhandled, resolution, actor, note, at) "
                  "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (cid, action, dumps(ids), unhandled, resolution, actor, note, now()))

    @r.post("/collections/{cid}/complete")
    def complete(cid: str, body: dict[str, Any] = OPT_BODY, actor: str = ACTOR) -> dict[str, Any]:
        col = store.collection(cid)
        with store.tx() as c:
            review(c, cid, "complete", store.ids_where({"collection": cid}), col["review"]["unhandled"], actor,
                   note=text(body, "note") or None)
        return store.collection(cid)

    @r.post("/collections/{cid}/zero")
    def zero(cid: str, body: dict[str, Any] = OPT_BODY, actor: str = ACTOR) -> dict[str, Any]:
        """本组不留: a global Pick inside is an explicit conflict, cleared (to null) only on request (§10 X6)."""
        col = store.collection(cid)
        resolution = body.get("resolution")
        if resolution not in (None, "keep_picks", "clear_picks"):
            raise Error("resolution must be keep_picks|clear_picks")
        ids = store.ids_where({"collection": cid})
        picks = [i for i in ids if store.one("SELECT 1 FROM marks WHERE asset_id = ? AND flag = 'pick'", (i,))]
        if picks and resolution is None:
            raise Conflict("本组有已选（Pick）素材，请选择保留或清除", picks=[
                {"id": i, "collections": [x for x in store.collections_of(i) if x["id"] != cid]} for i in picks])
        batch = None
        with store.tx() as c:
            if picks and resolution == "clear_picks":
                batch = st.apply_marks(c, picks, {"use": None}, actor=actor, reason=f"zero:{cid}")["batch_id"]
            review(c, cid, "zero", ids, col["review"]["unhandled"], actor, resolution=resolution,
                   note=text(body, "note") or None)
        return store.collection(cid) | {"batch_id": batch}

    @r.post("/collections/{cid}/reopen")
    def reopen(cid: str, actor: str = ACTOR) -> dict[str, Any]:
        col = store.collection(cid)
        with store.tx() as c:
            review(c, cid, "reopen", store.ids_where({"collection": cid}), col["review"]["unhandled"], actor)
        return store.collection(cid)

    @r.delete("/collections/{cid}")
    def delete_collection(cid: str, actor: str = ACTOR) -> dict[str, Any]:
        col_or_404(cid)
        t = now()
        with store.tx() as c:
            c.execute("UPDATE collections SET deleted_at = coalesce(deleted_at, ?), updated_at = ?, version = version + 1 "
                      "WHERE id = ?", (t, t, cid))
            st.collection_history(c, cid, "delete", {}, actor)
        return store.collection(cid)

    @r.post("/collections/{cid}/restore")
    def restore_collection(cid: str, actor: str = ACTOR) -> dict[str, Any]:
        col_or_404(cid)
        with store.tx() as c:
            c.execute("UPDATE collections SET deleted_at = NULL, updated_at = ?, version = version + 1 WHERE id = ?",
                      (now(), cid))
            st.collection_history(c, cid, "restore", {}, actor)
        return store.collection(cid)

    @r.get("/coverage")
    def coverage(task: str | None = None) -> list[dict[str, Any]]:
        out = []
        for col in store.collections(task=task):
            if col["group_kind"] in (None, "all"):
                continue
            state = "covered" if col["picks"] else "zeroed" if col["review"]["state"] == "zeroed" else "uncovered"
            out.append({"collection_id": col["id"], "name": col["name"], "group_kind": col["group_kind"],
                        "size": col["size"], "picks": col["picks"], "state": state})
        return out

    # ---- 6.6 export ----
    @r.post("/export")
    def export(body: dict[str, Any] = BODY, actor: str = ACTOR) -> dict[str, Any]:
        scope = body.get("scope", "picked")
        if scope not in ("picked", "all", "selected"):
            raise Error("scope must be picked|all|selected")
        conflict = body.get("conflict", "rename")
        if conflict not in ("rename", "skip", "fail"):
            raise Error("conflict must be rename|skip|fail")
        copy = body.get("copy", False)
        if not isinstance(copy, bool):
            raise Error("copy must be true or false")
        if body.get("collection_id"):
            col = store.collection(body["collection_id"])
            kind, value, title, ids = "collection", col["id"], col["name"], store.ids_where({"collection": col["id"]})
        elif body.get("asset_ids") is not None:
            ids = strs(body["asset_ids"], "asset_ids")
            check_assets(ids)
            kind, value, title = "asset_ids", ids, "筛选结果"
        elif body.get("rule") is not None:
            kind, value, title = "rule", check_rule(body["rule"]), "筛选结果"
            ids = store.ids_where(value)
        else:
            raise Error("one of collection_id, asset_ids, rule is required")
        if scope == "selected":
            sel = strs(body.get("selected") or body.get("asset_ids") or [], "selected")
            ids = [i for i in ids if i in set(sel)]
        assets = [store.asset_detail(i) for i in ids]
        if scope == "picked":
            assets = [a for a in assets if a["flag"] == "pick"]
        dest = Path(body.get("dest_dir") or store.exports_dir).expanduser()
        if not dest.is_absolute():
            raise Error("dest_dir must be an absolute path")
        protected = [Path(p).resolve() for p in store.task_roots()]
        if any(dest.resolve().is_relative_to(p) for p in protected):
            raise Error("导出目的地位于任务来源目录之内，Gardener 不在来源中写入")
        col_ids = list(dict.fromkeys(([value] if kind == "collection" else [])
                                     + [c["id"] for a in assets for c in a["collections"]]))
        collections = [store.collection(c) | {"member_ids": store.ids_where({"collection": c})} for c in col_ids]
        report = tk.export(dest, title=title, scope={"kind": kind, "value": value, "scope": scope},
                           collections=collections, assets=assets, copy=copy, conflict=conflict, protected=protected)
        with store.tx() as c:
            c.execute("INSERT INTO exports VALUES (?, ?, ?, ?)",
                      (st.new_id("e"), now(), dumps(body | {"actor": actor}), dumps(report)))
        return report

    @r.get("/exports")
    def exports() -> list[dict[str, Any]]:
        return [{"id": x["id"], "created_at": x["created_at"], "request": loads(x["request"]),
                 "report": loads(x["report"])} for x in store.all("SELECT * FROM exports ORDER BY created_at DESC")]

    # ---- import (契约修订 5D, PRD §06 正式导入 / §09) ----
    @r.post("/tasks/{tid}/import-plan", status_code=201)
    def import_plan(tid: str, body: dict[str, Any] = BODY) -> dict[str, Any]:
        task_or_404(tid)
        place = text(body, "place").strip() or None
        pending = body.get("include_pending", True)
        if not isinstance(pending, bool):
            raise Error("include_pending must be true or false")
        pid = imports.build(store, tid, body.get("dest_root"), place, pending, store.task_roots())
        return imports.plan_json(store, pid)

    @r.get("/imports")
    def get_imports(task: str | None = None) -> list[dict[str, Any]]:
        return imports.plans(store, task)

    @r.get("/imports/{pid}")
    def get_import(pid: str) -> dict[str, Any]:
        return imports.plan_json(store, pid)

    @r.post("/imports/{pid}/run")
    def run_import(pid: str) -> dict[str, Any]:
        return imports.start(store, importer, pid, store.task_roots())

    # ---- 契约修订 5E: Lightroom handoff and cleanup plans (handoff.py, cleanup.py) ----
    lr_dir = Path(lightroom_dir) if lightroom_dir is not None else handoff.default_dir()
    trash_fn = trash or cleanup.trash

    def scope_of(body: dict[str, Any]) -> tuple[dict[str, Any], list[str], str]:
        """(scope as recorded, asset ids, a name for it) from collection_id | concept | asset_ids | rule."""
        if body.get("collection_id"):
            col = store.collection(body["collection_id"])
            return {"collection_id": col["id"]}, store.ids_where({"collection": col["id"]}), col["name"]
        if body.get("concept"):
            k = text(body, "concept", required=True)
            ids = store.ids_where({"concept": k})
            shown = taxa.display(store.conn(), [k]).get(k) or {}
            row = store.one("SELECT taxon, common FROM current_ids WHERE concept = ? LIMIT 1", (k,)) or {}
            return {"concept": k}, ids, shown.get("zh") or shown.get("common") or row.get("common") or row.get("taxon") or k
        if body.get("asset_ids") is not None:
            ids = strs(body["asset_ids"], "asset_ids")
            check_assets(ids)
            return {"asset_ids": ids}, ids, f"选中的 {len(ids)} 张"
        if body.get("rule") is not None:
            rule = check_rule(body["rule"])
            return {"rule": rule}, store.ids_where(rule), "筛选结果"
        return {}, [], ""

    @r.post("/handoffs", status_code=201)
    def post_handoff(body: dict[str, Any] = BODY, actor: str = ACTOR) -> dict[str, Any]:
        """Freeze a collection, species or selection for Lightroom and write its handoff file (PRD §12)."""
        scope, ids, name = scope_of(body)
        if not scope:
            raise Error("one of collection_id, concept, asset_ids, rule is required")
        uses = body.get("uses")
        if uses is not None:
            uses = tuple(strs(uses, "uses"))
        elif "asset_ids" not in scope:
            uses = handoff.HANDOFF_USES
        name = text(body, "name") or name
        with store.tx() as c:
            hid = handoff.create(c, ids, name=name, scope=scope | {"uses": uses}, uses=uses, actor=actor,
                                 lightroom_dir=lr_dir, lists_dir=name_lists)
            return handoff.get(c, hid)

    @r.get("/handoffs")
    def get_handoffs(limit: int = LIMIT100) -> list[dict[str, Any]]:
        with store.tx() as c:
            ids = [x[0] for x in c.execute("SELECT id FROM handoffs ORDER BY created_at DESC LIMIT ?", (limit,))]
            for hid in ids:
                handoff.ingest(c, hid)
            return [{k: v for k, v in handoff.get(c, hid).items() if k != "items"} for hid in ids]

    @r.get("/handoffs/{hid}")
    def get_handoff(hid: str) -> dict[str, Any]:
        """The handoff with each photo's real result once the plugin wrote one (read back on request)."""
        with store.tx() as c:
            if c.execute("SELECT 1 FROM handoffs WHERE id = ?", (hid,)).fetchone():
                handoff.ingest(c, hid)
            return handoff.get(c, hid)

    @r.post("/cleanup-plans", status_code=201)
    def post_cleanup_plan(body: dict[str, Any] = OPT_BODY, actor: str = ACTOR) -> dict[str, Any]:
        """The rejected assets in a scope (default: the whole library) with every file and species impact."""
        scope, ids, _ = scope_of(body)
        if not scope:
            ids = store.ids_where({"flag": "reject"})
        with store.tx() as c:
            return cleanup.get(c, cleanup.create(c, ids, scope=scope, actor=actor))

    @r.get("/cleanup-plans")
    def get_cleanup_plans(limit: int = LIMIT100) -> list[dict[str, Any]]:
        rows = store.all("SELECT id FROM cleanup_plans ORDER BY created_at DESC LIMIT ?", (limit,))
        return [{k: v for k, v in cleanup.get(store.conn(), x["id"]).items() if k != "items"} for x in rows]

    @r.get("/cleanup-plans/{pid}")
    def get_cleanup_plan(pid: str) -> dict[str, Any]:
        return cleanup.get(store.conn(), pid)

    @r.post("/cleanup-plans/{pid}/run")
    def run_cleanup_plan(pid: str, body: dict[str, Any] = BODY) -> dict[str, Any]:
        """Move the plan's files to the Trash after a person confirmed, re-checking every item first."""
        if body.get("confirm") is not True:
            raise Error("清理需要人确认：confirm 必须为 true")
        report = cleanup.run(store, pid, trash_fn)
        return cleanup.get(store.conn(), pid) | {"report": report}
    # ---- end 契约修订 5E ----

    app.include_router(r)
    if (WEB_DIR / "index.html").exists():
        from fastapi.staticfiles import StaticFiles

        @app.middleware("http")
        async def revalidate_ui(request: Request, call_next: Any) -> Any:
            """The UI files revalidate (ETag) on every load, so an upgraded service never runs a stale app.js."""
            response = await call_next(request)
            if not request.url.path.startswith("/api/"):
                response.headers["Cache-Control"] = "no-cache"
            return response

        app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    return app
