"""An in-memory fake of the Gardener HTTP API (docs/gardener/contract.md §6), for unit 3's UI/CLI/MCP tests only.

It answers with the §6 shapes and the 409 paths clients branch on; it does not scan, analyse or touch files.
`start()` serves it with uvicorn on a free port in a daemon thread (the CLI and MCP speak urllib to a real
socket). Run as a script to browse the web UI against it: `uv run python tests/gardener/fake_api.py 8771`."""
from __future__ import annotations

import io
import itertools
import socket
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from bioscan.gardener.capabilities import CAPABILITIES, PRESETS, expand

WEB = Path(__file__).resolve().parents[2] / "bioscan" / "gardener" / "web"
T0 = datetime(2026, 10, 20, 8, 0, tzinfo=timezone.utc)


def _jpeg(color: tuple[int, int, int]) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (48, 32), color).save(buf, "JPEG")
    return buf.getvalue()


class Err(Exception):
    def __init__(self, status: int, error: str, code: str, **extra):
        self.status, self.body = status, {"error": error, "code": code, **extra}


class Fake:
    def __init__(self, seed: bool = True):
        self._clock = itertools.count(1)
        self._ids = itertools.count(1)
        self.actors: list[str] = []            # X-Gardener-Actor of every request, in order
        self.tasks: dict[str, dict] = {}
        self.runs: dict[str, dict] = {}
        self.assets: dict[str, dict] = {}
        self.cols: dict[str, dict] = {}
        self.history: list[dict] = []          # mark history rows with batch_id
        self.exports: list[dict] = []
        self.mark_bodies: list[dict] = []      # every POST /marks body as received (X5: omitted key = unchanged)
        self.presets = [{"id": k, "name": v["name"], "capabilities": list(v["capabilities"]), "builtin": True}
                        for k, v in PRESETS.items()]
        self.settings = {"run_mode": "balanced", "default_preset": "photo",
                         "capability_versions": {"content": "scene@ab12;siglip2@0f3e9a1c2b7d"}}
        self.tags = [{"id": "scene:wildlife", "name": "野生动物", "parent_id": None, "aliases": [], "active": True,
                      "merged_into": None, "count": 0}]
        self.cache_files = 12
        if seed:
            self._seed()

    # ---- helpers ----
    def now(self) -> str:
        # §10 X7: fixed-width UTC, string-comparable
        return (T0 + timedelta(seconds=next(self._clock))).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    def nid(self, p: str) -> str:
        return f"{p}_{next(self._ids):04d}"

    def task(self, tid: str) -> dict:
        if tid not in self.tasks:
            raise Err(404, "没有这个任务", "not_found")
        return self.tasks[tid]

    def asset(self, aid: str) -> dict:
        if aid not in self.assets:
            raise Err(404, "没有这个素材", "not_found")
        return self.assets[aid]

    def col(self, cid: str) -> dict:
        if cid not in self.cols:
            raise Err(404, "没有这个集合", "not_found")
        return self.cols[cid]

    # ---- seed ----
    def _seed(self):
        t = self.new_task({"title": "高黎贡山 · 雨季徒步", "subtitle": "百花岭 · 2026-07", "description": "两张卡",
                           "sources": ["/Volumes/SD1/DCIM", "/Volumes/SD2/DCIM"], "preset": "wildlife"}, status="partial")
        t["sources"][1]["online"] = False
        specs = [  # name, media, ai verdict/score/reasons, content, species, burst
            ("IMG_0001.ARW", "photo", ("pick", 0.82, ["主体清晰", "构图平衡"]), "鸟类", ("Garrulax", "画眉"), ("b1", 1, 3)),
            ("IMG_0002.ARW", "photo", ("none", 0.55, []), "鸟类", ("Garrulax", "画眉"), ("b1", 2, 3)),
            ("IMG_0003.ARW", "photo", ("reject", 0.21, ["轻微模糊"]), "鸟类", None, ("b1", 3, 3)),
            ("IMG_0004.JPG", "photo", ("reject", 0.18, ["雨雾低对比"]), "山谷", None, None),
            ("IMG_0005.JPG", "photo", None, "山谷", None, None),
            ("MVI_0006.MOV", "video", None, None, None, None),
        ]
        ids = []
        for i, (name, media, ai, content, sp, burst) in enumerate(specs):
            a = self.new_asset(t, name, media, online=(i != 4))
            a["ai"] = {"verdict": ai[0], "score": ai[1], "reasons": ai[2]} if ai else None
            a["content_label_zh"] = content
            a["species"] = {"taxon": sp[0], "common": sp[1], "p": 0.71} if sp else None
            a["burst"] = {"id": f"{t['id']}:{burst[0]}", "rank": burst[1], "size": burst[2]} if burst else None
            a["analysis"] = "done" if ai else ("failed" if media == "photo" else "partial")
            a["lat"], a["lon"] = (25.3, 98.8) if i < 3 else (None, None)
            a["caps"] = {c.id: {"state": "not_enabled", "version": None, "error": None, "data": None}
                         for c in CAPABILITIES}
            for c in t["capabilities"]:
                a["caps"][c] = {"state": "done", "version": "v1", "error": None, "data": None}
            if i == 0:
                a["caps"]["subject"]["data"] = {"class": "bird", "boxes": [
                    {"xyxy": [0.3, 0.2, 0.7, 0.8], "score": 0.9, "kind": "bird"}]}
            if i == 4:
                a["caps"]["aesthetic"] = {"state": "failed", "version": "v1", "error": "解码超时", "data": None}
            if i == 2:                          # content changed on disk: the service cleared the old judgment
                self.mark({"asset_ids": [a["id"]], "flag": "pick", "stars": 3}, "ui")
                self.mark({"asset_ids": [a["id"]], "flag": None, "stars": 0}, "system", reason="content_changed")
                a["meta"]["content_changed_at"] = a["updated_at"]
            if i == 3:
                a["meta"]["missing"] = True
                a["files"][0]["online"] = False
            if i == 5:
                a["caps"]["species"] = {"state": "unrecognized", "version": "v1", "error": None, "data": None}
            if ai:
                a["ai_history"] = [{"run_id": t["scan"]["run_id"], "verdict": ai[0], "score": ai[1],
                                    "reasons": ai[2], "version": "v1", "at": self.now()}]
            ids.append(a["id"])
        self.new_col({"name": "全部素材", "kind": "manual", "asset_ids": ids, "task_id": t["id"]},
                     origin="auto", group_kind="all")
        self.new_col({"name": "林鸟 · 画眉", "kind": "manual", "asset_ids": ids[:3], "task_id": t["id"]},
                     origin="auto", group_kind="species")
        c = self.new_col({"name": "雨中山谷", "kind": "manual", "asset_ids": ids[3:5], "task_id": t["id"]},
                         origin="auto", group_kind="scene")
        self.review(c, "completed")
        c["snapshot"] = [ids[3], ids[0]]        # ids[4] arrived after completion, ids[0] left
        c["changed"] = [ids[3]]
        self.assets[ids[0]].update(flag="pick", stars=4, judged=True, mark_version=2)
        t2 = self.new_task({"title": "洱海湿地 · 越冬水鸟", "sources": ["/Volumes/SD3/DCIM"], "preset": "quick"},
                           status="running")
        self.new_asset(t2, "DSC_1001.NEF", "photo")

    def new_task(self, body: dict, status: str = "queued") -> dict:
        tid = self.nid("t")
        caps = expand(body.get("capabilities") or next(
            (p["capabilities"] for p in self.presets if p["id"] == body.get("preset")), ["meta"]))
        t = {"id": tid, "title": body["title"], "subtitle": body.get("subtitle") or "",
             "description": body.get("description") or "", "preset": body.get("preset"), "capabilities": caps,
             "run_mode": body.get("run_mode") or self.settings["run_mode"], "archived": False,
             "created_at": self.now(), "sources": [], "counts": {}, "scan": {}, "review": {}}
        self.tasks[tid] = t
        for p in body["sources"]:
            t["sources"].append({"id": self.nid("s"), "path": p, "online": True})
        self.new_run(t, "scan", status=status)
        return t

    def new_run(self, t: dict, kind: str, status: str = "queued", scope: dict | None = None) -> dict:
        rid = self.nid("r")
        seq = 1 + sum(r["task_id"] == t["id"] for r in self.runs.values())
        done = status in ("completed", "partial", "failed", "stopped")
        r = {"id": rid, "task_id": t["id"], "seq": seq, "kind": kind, "status": status,
             "phase": "analyze" if status == "running" else None,
             "config": {"capabilities": t["capabilities"], "run_mode": t["run_mode"], "versions": {}},
             "scope": scope or {"sources": [s["path"] for s in t["sources"]]}, "counts": {},
             "summary": {"discovered": 6, "files": 7, "bytes": 1 << 26, "by_media": {"photo": 5, "video": 1},
                         "previewed": 5, "preview_failed": 1, "analyzed": {"content": 5},
                         "failed": [{"asset_id": "a_0000", "capability": "aesthetic", "error": "解码超时"}],
                         "unavailable": {"species": "模型未下载"}, "not_run": ["burst"],
                         "offline_sources": ["/Volumes/SD2/DCIM"], "unsupported": 2, "elapsed_s": 41.5}
             if done else None,
             "error": None, "queued_at": self.now(), "started_at": None, "finished_at": self.now() if done else None}
        self.runs[rid] = r
        t["scan"]["run_id"] = rid
        return r

    def new_asset(self, t: dict, name: str, media: str, online: bool = True) -> dict:
        aid = self.nid("a")
        src = t["sources"][0]["path"]
        a = {"id": aid, "media": media, "name": name, "taken_at": self.now(), "camera": "SONY ILCE-7RM5",
             "width": 6000, "height": 4000, "duration_s": 12.5 if media == "video" else None, "online": online,
             "preview": "ready", "flag": None, "stars": 0, "mark_version": 1, "judged": False, "ai": None,
             "content_label_zh": None, "species": None, "burst": None, "analysis": "pending", "task_ids": [t["id"]],
             "updated_at": self.now(),
             "files": [{"path": f"{src}/{name}", "role": "video" if media == "video" else "raw",
                        "size": 25_000_000, "online": online}],
             "meta": {"lens": "FE 200-600mm"}, "lat": None, "lon": None, "caps": {}, "ai_history": [],
             "mark_history": [], "tags": []}
        self.assets[aid] = a
        return a

    def new_col(self, body: dict, origin: str = "user", group_kind: str | None = None) -> dict:
        cid = self.nid("c")
        c = {"id": cid, "kind": body.get("kind", "manual"), "origin": origin, "group_kind": group_kind,
             "task_id": body.get("task_id"), "name": body["name"], "description": body.get("description") or "",
             "rule": body.get("rule"), "version": 1, "deleted_at": None, "members": list(body.get("asset_ids") or []),
             "state": "open", "at": None, "snapshot": [], "changed": [], "user_edited": origin == "user"}
        self.cols[cid] = c
        return c

    # ---- derived views ----
    def match(self, a: dict, f: dict) -> bool:
        g = f.get
        if g("task") and g("task") not in a["task_ids"]:
            return False
        if g("collection") and a["id"] not in self.members(self.col(g("collection"))):
            return False
        if g("media") and a["media"] != g("media"):
            return False
        if g("flag") and (a["flag"] or "none") != g("flag"):
            return False
        if g("stars_min") not in (None, "") and a["stars"] < int(g("stars_min")):
            return False
        if g("stars") not in (None, "") and a["stars"] != int(g("stars")):
            return False
        if g("ai") and ((a["ai"] or {}).get("verdict") or "none") != g("ai"):
            return False
        if g("judged") and a["judged"] != (g("judged") == "yes"):
            return False
        if g("online") not in (None, "") and a["online"] != (str(g("online")) == "1"):
            return False
        if g("analysis") and a["analysis"] != g("analysis"):
            return False
        if g("has_gps") not in (None, "") and (a["lat"] is not None) != (str(g("has_gps")) == "1"):
            return False
        if g("content") and g("content") not in (a["content_label_zh"] or ""):
            return False
        if g("species") and g("species").lower() not in " ".join(
                (a["species"] or {}).get(k) or "" for k in ("taxon", "common")).lower():
            return False
        if g("q") and g("q").lower() not in a["name"].lower():
            return False
        if g("date_from") and (a["taken_at"] or "") < g("date_from"):
            return False
        if g("date_to") and (a["taken_at"] or "")[:10] > g("date_to"):
            return False
        if g("changed_after") and a["updated_at"] <= g("changed_after"):
            return False
        return True

    def members(self, c: dict) -> list[str]:
        if c["kind"] == "smart":
            return [a["id"] for a in self.assets.values() if self.match(a, c["rule"] or {})]
        return [m for m in c["members"] if m in self.assets]

    def item(self, a: dict) -> dict:
        drop = {"files", "meta", "lat", "lon", "caps", "ai_history", "mark_history", "tags"}
        return {k: v for k, v in a.items() if k not in drop}

    def detail(self, a: dict) -> dict:
        cols = [{"id": c["id"], "name": c["name"]} for c in self.cols.values()
                if not c["deleted_at"] and a["id"] in self.members(c)]
        return {**a, "collections": cols}

    def col_out(self, c: dict) -> dict:
        ms = [self.assets[m] for m in self.members(c)]
        ai = [(a["ai"] or {}).get("verdict") for a in ms]
        judged = sum(a["judged"] for a in ms)
        closed, cur = c["state"] != "open", set(self.members(c))
        return {**{k: c[k] for k in ("id", "kind", "origin", "group_kind", "task_id", "name", "description", "rule",
                                     "version", "deleted_at", "user_edited")},
                "size": len(ms), "cover_ids": [a["id"] for a in ms[:4]], "judged": judged,
                "picks": sum(a["flag"] == "pick" for a in ms), "rejects": sum(a["flag"] == "reject" for a in ms),
                "ai_picks": ai.count("pick"), "ai_rejects": ai.count("reject"),
                "low_score": bool(ms) and all(v == "reject" for v in ai if v) and ai.count("reject") > 0,
                "review": {"state": c["state"], "at": c["at"], "unhandled": len(ms) - judged,
                           "new_since": len(cur - set(c["snapshot"])) if closed else 0,
                           "removed_since": len(set(c["snapshot"]) - cur) if closed else 0,
                           "changed_since": len(set(c["changed"]) & cur) if closed else 0}}

    def task_out(self, t: dict, runs: bool = False) -> dict:
        ms = [a for a in self.assets.values() if t["id"] in a["task_ids"]]
        r = self.runs[t["scan"]["run_id"]]
        cols = [c for c in self.cols.values() if c["task_id"] == t["id"] and not c["deleted_at"]]
        out = {**t, "scan": {"status": r["status"], "run_id": r["id"], "phase": r["phase"], "discovered": len(ms),
                             "previewed": sum(a["preview"] == "ready" for a in ms),
                             "analyzed": sum(a["analysis"] == "done" for a in ms),
                             "failed": sum(a["analysis"] == "failed" for a in ms), "total_analysis": len(ms),
                             "progress": 1.0 if r["finished_at"] else 0.4},
               "review": {"judged": sum(a["judged"] for a in ms), "total": len(ms),
                          "collections_done": sum(c["state"] != "open" for c in cols), "collections_total": len(cols)},
               "counts": {"assets": len(ms), "photos": sum(a["media"] == "photo" for a in ms),
                          "videos": sum(a["media"] == "video" for a in ms), "files": len(ms) + 1,
                          "bytes": 25_000_000 * len(ms)}}
        if runs:
            out["runs"] = sorted((x for x in self.runs.values() if x["task_id"] == t["id"]), key=lambda x: x["seq"])
        return out

    def review(self, c: dict, state: str):
        c["state"], c["at"], c["snapshot"], c["changed"] = state, self.now(), self.members(c), []

    def mark(self, body: dict, actor: str, reason: str | None = None) -> dict:
        ids = body["asset_ids"]
        for aid in ids:
            self.asset(aid)
        expect = body.get("expect") or {}
        bad = [{"id": i, "version": self.assets[i]["mark_version"]} for i, v in expect.items()
               if self.assets[i]["mark_version"] != v]
        if bad:
            raise Err(409, "素材已被其他客户端修改", "conflict", conflicts=bad)
        batch, at, out = self.nid("b"), self.now(), []
        for aid in ids:
            a = self.assets[aid]
            for field in ("flag", "stars"):
                if field in body and body[field] != a[field]:
                    row = {"batch_id": batch, "asset_id": aid, "field": field, "old": a[field], "new": body[field],
                           "actor": actor, "reason": reason or body.get("reason"), "at": at, "undone": False,
                           "version": a["mark_version"] + 1}
                    self.history.append(row)
                    a["mark_history"].append({k: row[k] for k in ("batch_id", "field", "old", "new", "actor",
                                                                  "reason", "at")})
                    a[field] = body[field]
                    a["mark_version"] += 1
            a["judged"] = bool(a["flag"] or a["stars"])
            a["updated_at"] = at
            out.append({"id": aid, "flag": a["flag"], "stars": a["stars"], "version": a["mark_version"]})
        return {"batch_id": batch, "assets": out}

    def undo(self, batch: str) -> dict:
        rows = [r for r in self.history if r["batch_id"] == batch and not r["undone"]]
        if not rows:
            raise Err(404, "没有这个批次或已撤销", "not_found")
        later = [r["asset_id"] for r in rows if self.assets[r["asset_id"]]["mark_version"] > max(
            x["version"] for x in rows if x["asset_id"] == r["asset_id"])]
        if later:
            raise Err(409, "该批之后素材又被修改，无法撤销", "conflict", ids=sorted(set(later)))
        for r in reversed(rows):
            a = self.assets[r["asset_id"]]
            a[r["field"]] = r["old"]
            a["mark_version"] += 1
            a["judged"] = bool(a["flag"] or a["stars"])
            a["updated_at"] = self.now()
            r["undone"] = True
        return {"undone": len({r["asset_id"] for r in rows})}


def create_app(fake: Fake | None = None, static: bool = True) -> FastAPI:
    fk = fake or Fake()
    app = FastAPI()
    app.state.fake = fk
    P = "/api/v1"

    @app.middleware("http")
    async def record_actor(request: Request, call_next):
        if request.url.path.startswith(P):
            fk.actors.append(request.headers.get("x-gardener-actor", "api"))
        return await call_next(request)

    @app.exception_handler(Err)
    async def err(_, e: Err):
        return JSONResponse(e.body, status_code=e.status)

    # ---- 6.1 ----
    @app.get(P + "/health")
    def health():
        return {"status": "ok", "version": "0.1.0", "schema_version": 2, "data_dir": "/tmp/gardener",
                "cache_dir": "/tmp/gardener/cache", "worker": {"state": "idle", "run_id": None, "task_id": None},
                "device": "cpu", "models_loaded": []}

    def caps_out():
        unavailable = {"species": "BioCLIP 模型未下载"}
        return [{"id": c.id, "name": c.name, "description": c.description, "stages": list(c.stages),
                 "requires": list(c.requires), "version": fk.settings["capability_versions"].get(c.id),
                 "state": "unavailable" if c.id in unavailable else "available" if not c.stages else "unknown",
                 "reason": unavailable.get(c.id)} for c in CAPABILITIES]

    @app.get(P + "/capabilities")
    def caps():
        return caps_out()

    @app.post(P + "/capabilities/check")
    def caps_check(body: dict | None = None):
        return caps_out()

    @app.get(P + "/presets")
    def presets():
        return fk.presets

    @app.post(P + "/presets", status_code=201)
    def preset_new(body: dict):
        p = {"id": fk.nid("p"), "name": body["name"], "capabilities": expand(body["capabilities"]), "builtin": False}
        fk.presets.append(p)
        return p

    def preset(pid):
        p = next((p for p in fk.presets if p["id"] == pid), None)
        if not p:
            raise Err(404, "没有这个预设", "not_found")
        if p["builtin"]:
            raise Err(400, "内建预设不可修改", "bad_request")
        return p

    @app.put(P + "/presets/{pid}")
    def preset_put(pid: str, body: dict):
        p = preset(pid)
        p.update({k: body[k] for k in ("name", "capabilities") if k in body})
        return p

    @app.delete(P + "/presets/{pid}")
    def preset_del(pid: str):
        fk.presets.remove(preset(pid))
        return {"ok": True}

    @app.get(P + "/settings")
    def settings():
        return fk.settings

    @app.put(P + "/settings")
    def settings_put(body: dict):
        fk.settings.update(body)
        return fk.settings

    @app.get(P + "/storage")
    def storage():
        return {"data_dir": "/tmp/gardener", "db_path": "/tmp/gardener/gardener.sqlite", "db_bytes": 1 << 20,
                "cache_dir": "/tmp/gardener/cache", "cache_bytes": fk.cache_files * 40_000,
                "cache_files": fk.cache_files}

    @app.post(P + "/storage/clear-cache")
    def clear_cache():
        n, fk.cache_files = fk.cache_files, 0
        return {"removed_files": n, "freed_bytes": n * 40_000}

    @app.get(P + "/tags")
    def tags():
        return fk.tags

    @app.post(P + "/tags", status_code=201)
    def tag_new(body: dict):
        t = {"id": fk.nid("user"), "name": body["name"], "parent_id": body.get("parent_id"), "aliases": [],
             "active": True, "merged_into": None, "count": 0}
        fk.tags.append(t)
        return t

    def tag(tid):
        t = next((t for t in fk.tags if t["id"] == tid), None)
        if not t:
            raise Err(404, "没有这个标签", "not_found")
        return t

    @app.post(P + "/tags/{tid}/split")
    def tag_split(tid: str, body: dict):
        src = tag(tid)
        new = [tag_new({"name": n, "parent_id": tid}) for n in body["names"]]
        if body.get("deactivate_source"):
            src["active"] = False
        return new

    @app.patch(P + "/tags/{tid}")
    def tag_patch(tid: str, body: dict):
        t = tag(tid)
        t.update({k: body[k] for k in ("name", "parent_id", "aliases", "active") if k in body})
        return t

    @app.post(P + "/tags/{tid}/merge")
    def tag_merge(tid: str, body: dict):
        t = tag(tid)
        tag(body["into"])
        t.update(active=False, merged_into=body["into"])
        return t

    @app.post(P + "/assets/tags")
    def asset_tags(body: dict):
        return {"ok": True, "assets": len(body["asset_ids"])}

    # ---- 6.2 ----
    @app.post(P + "/tasks", status_code=201)
    def task_new(body: dict):
        if not body.get("title") or not body.get("sources"):
            raise Err(400, "需要标题和至少一个来源", "bad_request")
        for p in body["sources"]:
            if not str(p).startswith("/"):
                raise Err(400, f"来源须为绝对路径：{p}", "bad_request")
        return fk.task_out(fk.new_task(body))

    @app.get(P + "/tasks")
    def tasks(archived: str = "0"):
        keep = {"0": [False], "1": [True], "all": [False, True]}[archived]
        return [fk.task_out(t) for t in fk.tasks.values() if t["archived"] in keep]

    @app.get(P + "/tasks/{tid}")
    def task_get(tid: str):
        return fk.task_out(fk.task(tid), runs=True)

    @app.patch(P + "/tasks/{tid}")
    def task_patch(tid: str, body: dict):
        t = fk.task(tid)
        t.update({k: body[k] for k in ("title", "subtitle", "description", "capabilities", "run_mode") if k in body})
        return fk.task_out(t)

    @app.post(P + "/tasks/{tid}/sources")
    def task_sources(tid: str, body: dict):
        t = fk.task(tid)
        for p in body["paths"]:
            t["sources"].append({"id": fk.nid("s"), "path": p, "online": True})
        return fk.new_run(t, "append", scope={"sources": body["paths"]})

    @app.post(P + "/tasks/{tid}/{action}")
    def task_action(tid: str, action: str, body: dict | None = None):
        t, body = fk.task(tid), body or {}
        r = fk.runs[t["scan"]["run_id"]]
        if action == "rescan":
            return fk.new_run(t, "rescan", scope={k: body[k] for k in ("capabilities", "asset_ids") if body.get(k)})
        if action == "retry":
            return fk.new_run(t, "retry", scope={"asset_ids": body.get("asset_ids") or []})
        moves = {"pause": ({"running", "queued"}, "paused"), "resume": ({"paused"}, "queued"),
                 "stop": ({"running", "queued", "paused"}, "stopped")}
        if action in moves:
            allowed, to = moves[action]
            if r["status"] not in allowed:
                raise Err(409, f"当前状态 {r['status']} 不能{action}", "conflict")
            r["status"] = to
        elif action in ("archive", "unarchive"):
            t["archived"] = action == "archive"
        elif action == "verify-sources":
            for s in t["sources"]:
                s["online"] = True
        else:
            raise Err(404, "未知操作", "not_found")
        return fk.task_out(t)

    @app.get(P + "/runs/{rid}")
    def run_get(rid: str):
        if rid not in fk.runs:
            raise Err(404, "没有这个运行", "not_found")
        return fk.runs[rid]

    # ---- 6.3 ----
    @app.get(P + "/assets")
    def assets(request: Request, order: str = "time", limit: int = 200, offset: int = 0):
        f = dict(request.query_params)
        items = [a for a in fk.assets.values() if fk.match(a, f)]
        key = {"time": lambda a: a["taken_at"] or "", "path": lambda a: a["name"], "discovered": lambda a: 0,
               "score": lambda a: -((a["ai"] or {}).get("score") or 0)}[order]
        items.sort(key=lambda a: (key(a), a["id"]))      # ids are allocated in discovery order
        return {"total": len(items), "items": [fk.item(a) for a in items[offset:offset + limit]], "offset": offset}

    @app.post(P + "/assets/{aid}/reveal")
    def reveal(aid: str):
        fk.asset(aid)
        return {"ok": True}

    @app.post(P + "/assets/{aid}/split-version")
    def split_version(aid: str):
        a = fk.asset(aid)
        return fk.detail(a) | {"original": None, "version_lock": True, "versions": []}

    @app.get(P + "/people")
    def people():
        return []

    @app.post(P + "/assets/{aid}/faces/{idx}")
    def name_face(aid: str, idx: int):
        return fk.detail(fk.asset(aid))

    @app.delete(P + "/assets/{aid}/faces/{idx}")
    def unname_face(aid: str, idx: int):
        return fk.detail(fk.asset(aid))

    @app.get(P + "/assets/{aid}/{kind}")
    def asset_media(aid: str, kind: str):
        a = fk.asset(aid)
        if kind == "original":
            if not a["online"]:
                raise Err(404, "原片离线", "not_found")
            return Response(b"\0" * 64, media_type="video/quicktime")
        if kind not in ("thumb", "large"):
            raise Err(404, "未知资源", "not_found")
        if a["preview"] != "ready":
            raise Err(404, "预览尚未生成", "not_found")
        return Response(_jpeg((60 + 30 * (int(aid[2:]) % 5), 110, 90)), media_type="image/jpeg")

    @app.get(P + "/assets/{aid}")
    def asset_get(aid: str):
        return fk.detail(fk.asset(aid))

    @app.post(P + "/marks")
    def marks(body: dict, request: Request):
        fk.mark_bodies.append(body)
        return fk.mark(body, request.headers.get("x-gardener-actor", "api"))

    @app.get(P + "/marks/history")
    def marks_history(asset_id: str = "", batch_id: str = "", actor: str = "", limit: int = 200):
        rows = [r for r in reversed(fk.history) if (not asset_id or r["asset_id"] == asset_id)
                and (not batch_id or r["batch_id"] == batch_id) and (not actor or r["actor"] == actor)]
        return [{k: v for k, v in r.items() if k != "version"} for r in rows[:limit]]

    @app.post(P + "/marks/undo")
    def marks_undo(body: dict):
        return fk.undo(body["batch_id"])

    # ---- 6.5 ----
    @app.get(P + "/collections")
    def cols(task: str = "", kind: str = "", deleted: str = "0"):
        return [fk.col_out(c) for c in fk.cols.values()
                if (not task or c["task_id"] == task) and (not kind or c["kind"] == kind)
                and bool(c["deleted_at"]) == (deleted == "1")]

    @app.get(P + "/collections/{cid}")
    def col_get(cid: str):
        return fk.col_out(fk.col(cid))

    @app.post(P + "/collections", status_code=201)
    def col_new(body: dict):
        if body.get("kind") == "smart" and not isinstance(body.get("rule"), dict):
            raise Err(400, "Smart Collection 需要 rule", "bad_request")
        return fk.col_out(fk.new_col(body))

    @app.patch(P + "/collections/{cid}")
    def col_patch(cid: str, body: dict):
        c = fk.col(cid)
        if body.get("version") != c["version"]:
            raise Err(409, "集合已被修改", "conflict", version=c["version"])
        c.update({k: body[k] for k in ("name", "description", "rule") if k in body})
        c["version"] += 1
        c["user_edited"] = True
        return fk.col_out(c)

    @app.post(P + "/collections/{cid}/members")
    def col_members(cid: str, body: dict):
        c = fk.col(cid)
        if c["kind"] == "smart":
            raise Err(400, "Smart Collection 成员由规则决定", "bad_request")
        c["members"] = [m for m in c["members"] if m not in (body.get("remove") or [])]
        c["members"] += [m for m in body.get("add") or [] if m not in c["members"]]
        return fk.col_out(c)

    @app.post(P + "/collections/{cid}/merge")
    def col_merge(cid: str, body: dict):
        srcs = [fk.col(cid)] + [fk.col(i) for i in body["ids"]]
        ids = list(dict.fromkeys(m for c in srcs for m in fk.members(c)))
        new = fk.new_col({"name": body.get("name") or srcs[0]["name"], "asset_ids": ids, "task_id": srcs[0]["task_id"]})
        for c in srcs:
            c["deleted_at"] = fk.now()
        return fk.col_out(new)

    @app.post(P + "/collections/{cid}/split")
    def col_split(cid: str, body: dict):
        c = fk.col(cid)
        c["members"] = [m for m in c["members"] if m not in body["asset_ids"]]
        return fk.col_out(fk.new_col({"name": body["name"], "asset_ids": body["asset_ids"], "task_id": c["task_id"]}))

    @app.post(P + "/collections/{cid}/save-as-manual")
    def col_save(cid: str, body: dict):
        c = fk.col(cid)
        return fk.col_out(fk.new_col({"name": body["name"], "asset_ids": fk.members(c), "task_id": c["task_id"]}))

    @app.post(P + "/collections/{cid}/complete")
    def col_complete(cid: str, body: dict | None = None):
        c = fk.col(cid)
        fk.review(c, "completed")
        return fk.col_out(c)

    @app.post(P + "/collections/{cid}/zero")
    def col_zero(cid: str, request: Request, body: dict | None = None):
        c, res = fk.col(cid), (body or {}).get("resolution")
        picks = [m for m in fk.members(c) if fk.assets[m]["flag"] == "pick"]
        if picks and not res:
            raise Err(409, "本组含已 Pick 的素材", "conflict", picks=[
                {"id": m, "collections": [{"id": o["id"], "name": o["name"]} for o in fk.cols.values()
                                          if o["id"] != cid and not o["deleted_at"] and m in fk.members(o)]}
                for m in picks])
        batch = None
        if picks and res == "clear_picks":     # X6: flag -> null through the marks path, undoable
            batch = fk.mark({"asset_ids": picks, "flag": None}, request.headers.get("x-gardener-actor", "api"),
                            reason=f"zero:{cid}")["batch_id"]
        fk.review(c, "zeroed")
        return {**fk.col_out(c), "batch_id": batch}

    @app.post(P + "/collections/{cid}/reopen")
    def col_reopen(cid: str):
        c = fk.col(cid)
        c["state"], c["at"] = "open", fk.now()
        return fk.col_out(c)

    @app.delete(P + "/collections/{cid}")
    def col_delete(cid: str):
        c = fk.col(cid)
        c["deleted_at"] = fk.now()
        return fk.col_out(c)

    @app.post(P + "/collections/{cid}/restore")
    def col_restore(cid: str):
        c = fk.col(cid)
        c["deleted_at"] = None
        return fk.col_out(c)

    @app.get(P + "/coverage")
    def coverage(task: str = ""):
        out = []
        for c in fk.cols.values():
            if c["deleted_at"] or c["group_kind"] in (None, "all") or (task and c["task_id"] != task):
                continue
            o = fk.col_out(c)
            state = "zeroed" if c["state"] == "zeroed" else "covered" if o["picks"] else "uncovered"
            out.append({"collection_id": c["id"], "name": c["name"], "group_kind": c["group_kind"],
                        "size": o["size"], "picks": o["picks"], "state": state})
        return out

    # ---- 6.6 ----
    @app.post(P + "/export")
    def export(body: dict):
        if body.get("collection_id"):
            ids = fk.members(fk.col(body["collection_id"]))
        elif body.get("asset_ids"):
            ids = body["asset_ids"]
        else:
            ids = [a["id"] for a in fk.assets.values() if fk.match(a, body.get("rule") or {})]
        if body.get("scope") == "picked":
            ids = [i for i in ids if fk.assets[i]["flag"] == "pick"]
        copy = bool(body.get("copy"))
        rep = {"xml_path": f"{body.get('dest_dir') or '/tmp/gardener/exports'}/gardener-export-{len(fk.exports)}.xml",
               "assets": len(ids), "files": len(ids) if copy else 0, "copied": len(ids) if copy else 0,
               "identical": 0, "skipped": 0, "renamed": 0, "failed": [], "bytes": 25_000_000 * len(ids) if copy else 0,
               "verified": copy, "notes": ["未包含来源目录中的 XMP sidecar"]}
        fk.exports.append({"id": fk.nid("e"), "created_at": fk.now(), "request": body, "report": rep})
        return rep

    @app.get(P + "/exports")
    def exports():
        return fk.exports

    if static and WEB.is_dir():
        app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
    return app


def start(fake: Fake | None = None) -> tuple[str, Fake, object]:
    """Serve a fake on 127.0.0.1:<free port> in a daemon thread; returns (base url, fake, uvicorn server)."""
    import uvicorn
    fk = fake or Fake()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(fk), host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.02)
    return f"http://127.0.0.1:{port}", fk, server


if __name__ == "__main__":
    import sys

    import uvicorn
    uvicorn.run(create_app(), host="127.0.0.1", port=int(sys.argv[1]) if len(sys.argv) > 1 else 8771)
