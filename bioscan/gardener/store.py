"""Gardener persistence (contract §2): one SQLite file in WAL mode, a connection per thread, one write
lock (`Store.tx`) that serialises every write; reads run concurrently. The JSON shapes of §6
(Task, Run, AssetItem, AssetDetail, Collection) and the human-layer writes (marks, collections, tags,
presets) live here; the worker (tasks.py) writes machine results through the same `tx`."""
from __future__ import annotations

import json
import secrets
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bioscan.gardener import capabilities as capmod
from bioscan.gardener import query

SCHEMA_VERSION = 3                 # 2: subjects + identifications (ADR 0002), backfilled from raw:identify
                                   # 3: four uses replace Pick/Reject (marks.use) + encounters (contract revision 5B)
SCHEMA = Path(__file__).with_name("schema.sql")
DEFAULT_SETTINGS: dict[str, Any] = {"run_mode": capmod.DEFAULT_MODE, "default_preset": "quick",
                                    "capability_versions": {}, "cap_status": {}}
TERMINAL = ("completed", "partial", "failed", "stopped", "cancelled")
ADDED_COLUMNS = (("ai_suggestions", "burst_rank", "INTEGER"), ("ai_suggestions", "burst_size", "INTEGER"),
                 ("tasks", "timezone", "TEXT"), ("collections", "summary", "TEXT"), ("collections", "parent_id", "TEXT"),
                 ("assets", "version_of", "TEXT"), ("assets", "version_reason", "TEXT"),
                 ("assets", "version_lock", "INTEGER NOT NULL DEFAULT 0"), ("marks", "use", "TEXT"),
                 ("import_plans", "collection_id", "TEXT"),                     # 契约修订 7B: the batch collection
                 ("handoffs", "options", "TEXT"), ("editor_links", "progress", "TEXT"))   # 7B: presets/export, 修图进度
REVIEW_STATE = {"complete": "completed", "zero": "zeroed", "reopen": "open"}
# The four uses of a photo (PRD §05, contract revision 5B); NULL in marks.use = pending. marks.flag is derived
# from it for older clients: record and edit read as pick.
USES = ("record", "edit", "pending", "reject")
FLAG_OF_USE = {"record": "pick", "edit": "pick", "reject": "reject", None: None}


class Error(Exception):
    """An API-visible failure: HTTP status, contract code and extra body fields."""
    status, code = 400, "bad_request"

    def __init__(self, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.extra = extra


class NotFound(Error):
    status, code = 404, "not_found"


class Conflict(Error):
    status, code = 409, "conflict"


class Unavailable(Error):
    status, code = 503, "unavailable"


TIME_FMT = "%Y-%m-%dT%H:%M:%S.%fZ"     # fixed width, so record times compare as strings (§10 X7)


def now() -> str:
    return datetime.now(UTC).strftime(TIME_FMT)


def canon_time(s: str) -> str:
    """Any ISO 8601 time (Z, offset or naive = UTC) in the form `now()` writes, so strings compare."""
    try:
        t = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        raise Error(f"not an ISO 8601 time: {s!r}") from None
    return (t if t.tzinfo else t.replace(tzinfo=UTC)).astimezone(UTC).strftime(TIME_FMT)


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(6)}"


def dumps(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False)


def loads(s: str | None, default: Any = None) -> Any:
    return default if s is None else json.loads(s)


def marks_in(ids: list[str]) -> str:
    return ",".join("?" * len(ids))


def chunks(ids: list[str], n: int = 500) -> Iterator[list[str]]:
    for i in range(0, len(ids), n):
        yield ids[i:i + n]


class Store:
    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.db_path = self.data_dir / "gardener.sqlite"
        self.cache_dir = self.data_dir / "cache"
        self.preview_dir = self.cache_dir / "previews"
        self.exports_dir = self.data_dir / "exports"
        self.logs_dir = self.data_dir / "logs"
        for d in (self.preview_dir, self.exports_dir, self.logs_dir):
            d.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self._local = threading.local()
        self._conns: list[sqlite3.Connection] = []
        self._conns_lock = threading.Lock()
        self.active: dict[str, Any] = {"run_id": None, "task_id": None, "phase": None}   # set by the worker
        self.migrate()

    # ---- connections ----
    def conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "c", None)
        if c is None:
            c = sqlite3.connect(self.db_path, isolation_level=None, check_same_thread=False, timeout=30)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA foreign_keys = ON")
            c.execute("PRAGMA busy_timeout = 30000")
            c.execute("PRAGMA synchronous = NORMAL")
            self._local.c = c
            with self._conns_lock:
                self._conns.append(c)
        return c

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """The single write lock and one transaction; never hold it across an await."""
        c = self.conn()
        with self.lock:
            c.execute("BEGIN IMMEDIATE")
            try:
                yield c
            except BaseException:
                c.execute("ROLLBACK")
                raise
            c.execute("COMMIT")

    def all(self, sql: str, params: Any = ()) -> list[dict[str, Any]]:
        return [dict(r) for r in self.conn().execute(sql, params).fetchall()]

    def one(self, sql: str, params: Any = ()) -> dict[str, Any] | None:
        r = self.conn().execute(sql, params).fetchone()
        return None if r is None else dict(r)

    def close(self) -> None:
        with self._conns_lock:
            for c in self._conns:
                c.close()
            self._conns.clear()
        self._local = threading.local()

    def migrate(self) -> None:
        c = self.conn()
        c.execute("PRAGMA journal_mode = WAL")
        with self.lock:
            c.executescript(SCHEMA.read_text())
            for table, col, decl in ADDED_COLUMNS:      # databases made before a column was added
                if col not in {r[1] for r in c.execute(f"PRAGMA table_info({table})")}:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
        with self.tx() as c:
            row = c.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
            if row is None:
                c.execute("INSERT INTO meta VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
            elif int(row[0]) > SCHEMA_VERSION:
                raise RuntimeError(f"{self.db_path} has schema {row[0]}; this Gardener reads {SCHEMA_VERSION}")
            elif int(row[0]) < SCHEMA_VERSION:
                if int(row[0]) < 2:
                    from bioscan.gardener import identities

                    identities.backfill(c)
                migrate_uses(c)
                from bioscan.gardener import review

                for t in c.execute("SELECT id FROM tasks").fetchall():
                    review.assign_encounters(c, t[0])
                c.execute("UPDATE meta SET value = ? WHERE key = 'schema_version'", (str(SCHEMA_VERSION),))
            t = now()
            for pid, p in capmod.PRESETS.items():
                c.execute("INSERT OR IGNORE INTO presets VALUES (?, ?, ?, 1, ?, ?)",
                          (pid, p["name"], dumps(p["capabilities"]), t, t))
            for k, v in DEFAULT_SETTINGS.items():
                c.execute("INSERT OR IGNORE INTO settings VALUES (?, ?)", (k, dumps(v)))

    # ---- settings ----
    def setting(self, key: str) -> Any:
        row = self.one("SELECT value FROM settings WHERE key = ?", (key,))
        return loads(row["value"]) if row else DEFAULT_SETTINGS.get(key)

    @staticmethod
    def put_setting(c: sqlite3.Connection, key: str, value: Any) -> None:
        c.execute("INSERT INTO settings VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                  (key, dumps(value)))

    # ---- tasks and runs ----
    def run(self, run_id: str) -> dict[str, Any] | None:
        r = self.one("SELECT * FROM runs WHERE id = ?", (run_id,))
        return None if r is None else run_json(r)

    def task(self, task_id: str, *, with_runs: bool = False) -> dict[str, Any] | None:
        t = self.one("SELECT * FROM tasks WHERE id = ?", (task_id,))
        if t is None:
            return None
        sources = [{"id": s["id"], "path": s["path"], "online": bool(s["online"])} for s in
                   self.all("SELECT id, path, online FROM sources WHERE task_id = ? ORDER BY added_at, path", (task_id,))]
        last = self.one("SELECT * FROM runs WHERE task_id = ? ORDER BY seq DESC LIMIT 1", (task_id,))
        n = self.one("SELECT count(*) assets, coalesce(sum(a.media = 'photo'), 0) photos, "
                     f"coalesce(sum(a.media = 'video'), 0) videos, coalesce(sum(NOT {query.IN_TASK_REP}), 0) versions "
                     "FROM task_assets ta JOIN assets a ON a.id = ta.asset_id WHERE ta.task_id = ?", (task_id, task_id))
        f = self.one("SELECT count(*) files, coalesce(sum(f.size), 0) bytes FROM task_assets ta "
                     "JOIN asset_files f ON f.asset_id = ta.asset_id WHERE ta.task_id = ?", (task_id,))
        judged = self.one("SELECT count(*) n FROM task_assets ta JOIN marks m ON m.asset_id = ta.asset_id "
                          f"JOIN assets a ON a.id = ta.asset_id WHERE ta.task_id = ? AND {query.IN_TASK_REP} "
                          "AND (m.use IS NOT NULL OR m.stars > 0)", (task_id, task_id))["n"]
        acts = [r["act"] for r in self.all(
            "SELECT (SELECT r.action FROM collection_reviews r WHERE r.collection_id = c.id ORDER BY r.id DESC LIMIT 1) act "
            "FROM collections c WHERE c.task_id = ? AND c.deleted_at IS NULL", (task_id,))]
        scan: dict[str, Any] = {"status": None, "run_id": None, "phase": None, "discovered": 0, "previewed": 0,
                                "analyzed": 0, "failed": 0, "total_analysis": 0, "progress": 0.0}
        if last:
            k = loads(last["counts"], {})
            scan = {"status": last["status"], "run_id": last["id"], "phase": last["phase"],
                    "discovered": k.get("discovered", 0), "previewed": k.get("previewed", 0),
                    "analyzed": k.get("analyzed", 0), "failed": k.get("failed", 0),
                    "total_analysis": k.get("total_analysis", 0), "progress": progress(last["status"], k)}
        out = {"id": t["id"], "title": t["title"], "subtitle": t["subtitle"], "description": t["description"],
               "preset": t["preset"], "capabilities": loads(t["capabilities"]), "run_mode": t["run_mode"],
               "timezone": t["timezone"],
               "archived": t["archived_at"] is not None, "created_at": t["created_at"], "sources": sources,
               "scan": scan,
               "review": {"judged": judged, "total": n["assets"] - n["versions"],
                          "collections_done": sum(a in ("complete", "zero") for a in acts),
                          "collections_total": len(acts)},
               "counts": {"assets": n["assets"], "photos": n["photos"], "videos": n["videos"],
                          "versions": n["versions"], "files": f["files"], "bytes": f["bytes"]}}
        imp = self.one("SELECT id, status FROM import_plans WHERE task_id = ? ORDER BY seq DESC LIMIT 1", (task_id,))
        out["import"] = {"plan_id": imp["id"], "status": imp["status"]} if imp else None   # 待整理的「导入」一步
        if with_runs:
            out["runs"] = [run_json(r) for r in self.all("SELECT * FROM runs WHERE task_id = ? ORDER BY seq", (task_id,))]
        return out

    def task_roots(self) -> list[Path]:
        """Every source directory of every task: the places Gardener never writes into."""
        return [Path(r["path"]) for r in self.all("SELECT DISTINCT path FROM sources")]

    # ---- assets ----
    def asset_items(self, ids: list[str]) -> list[dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for part in chunks(ids):
            q = marks_in(part)
            rows = self.all(f"SELECT a.*, m.flag, m.use, coalesce(m.stars, 0) stars, coalesce(m.version, 0) mark_version "
                            f"FROM assets a LEFT JOIN marks m ON m.asset_id = a.id WHERE a.id IN ({q})", part)
            ai = {r["asset_id"]: r for r in self.all(
                f"SELECT * FROM ai_suggestions WHERE id IN (SELECT max(id) FROM ai_suggestions WHERE asset_id IN ({q}) "
                "GROUP BY asset_id)", part)}
            data: dict[tuple[str, str], Any] = {(r["asset_id"], r["capability"]): loads(r["data"]) for r in self.all(
                f"SELECT asset_id, capability, data FROM results WHERE id IN (SELECT max(id) FROM results WHERE "
                f"asset_id IN ({q}) AND capability IN ('content', 'species') AND status = 'done' "
                "GROUP BY asset_id, capability)", part)}
            states: dict[str, list[str]] = {}
            for r in self.all(f"SELECT asset_id, state FROM asset_caps WHERE asset_id IN ({q}) "
                              "AND state != 'not_enabled'", part):
                states.setdefault(r["asset_id"], []).append(r["state"])
            tasks: dict[str, list[str]] = {}
            for r in self.all(f"SELECT asset_id, task_id FROM task_assets WHERE asset_id IN ({q}) ORDER BY task_id", part):
                tasks.setdefault(r["asset_id"], []).append(r["task_id"])
            versions = {r["version_of"]: r["n"] for r in self.all(
                f"SELECT version_of, count(*) n FROM assets WHERE version_of IN ({q}) GROUP BY version_of", part)}
            named: dict[str, dict[str, Any]] = {}            # the photo's species shown on its tile (revision 4)
            for r in self.all(f"SELECT asset_id, concept, taxon, common, source, p FROM current_ids WHERE asset_id IN "
                              f"({q}) AND level = 'species' AND concept IS NOT NULL", part):
                key = (r["source"] == "user", r["p"] or 0.0)
                if r["asset_id"] not in named or key > named[r["asset_id"]]["_key"]:
                    named[r["asset_id"]] = {"taxon": r["taxon"], "common": r["common"], "p": r["p"],
                                            "confirmed": r["source"] == "user", "_key": key, "_concept": r["concept"]}
            from bioscan.gardener import taxa

            shown = taxa.display(self.conn(), [n["_concept"] for n in named.values()])
            for n in named.values():
                cur = shown.get(n.pop("_concept")) or {}
                n.update(taxon=cur.get("scientific") or n["taxon"], common=cur.get("common") or n["common"],
                         zh=cur.get("zh"))
            for a in rows:
                s = ai.get(a["id"])
                content = data.get((a["id"], "content")) or {}
                sp = data.get((a["id"], "species"))
                in_run = (self.active["phase"] == "analyze" and self.active["task_id"] in tasks.get(a["id"], []))
                out[a["id"]] = {
                    "id": a["id"], "media": a["media"], "name": Path(a["primary_path"]).name, "taken_at": a["taken_at"],
                    "camera": a["camera"], "width": a["width"], "height": a["height"], "duration_s": a["duration_s"],
                    "online": bool(a["online"]), "preview": a["preview_status"], "flag": a["flag"], "stars": a["stars"],
                    "use": a["use"] or "pending",
                    "mark_version": a["mark_version"], "judged": a["use"] is not None or a["stars"] > 0,
                    "ai": None if s is None else {"verdict": s["verdict"], "score": s["score"],
                                                  "reasons": loads(s["reasons"], [])},
                    "content_label_zh": content.get("label_zh"),
                    "species": ({k: v for k, v in named[a["id"]].items() if k != "_key"} if a["id"] in named else
                                None if not sp else {"taxon": sp.get("taxon"), "common": sp.get("common"),
                                                     "p": sp.get("p"), "confirmed": False}),
                    "burst": None if s is None or s["burst_id"] is None else
                    {"id": s["burst_id"], "rank": s["burst_rank"], "size": s["burst_size"]},
                    "analysis": analysis_state(states.get(a["id"], []), in_run),
                    "version_of": a["version_of"], "versions": versions.get(a["id"], 0),
                    "task_ids": tasks.get(a["id"], []), "updated_at": a["updated_at"]}
        return [out[i] for i in ids if i in out]

    def asset_detail(self, asset_id: str) -> dict[str, Any]:
        items = self.asset_items([asset_id])
        if not items:
            raise NotFound(f"no asset {asset_id}")
        a = self.one("SELECT * FROM assets WHERE id = ?", (asset_id,))
        files = [{"path": f["path"], "role": f["role"], "size": f["size"], "online": bool(f["online"])}
                 for f in self.all("SELECT * FROM asset_files WHERE asset_id = ? ORDER BY path", (asset_id,))]
        done = {r["capability"]: loads(r["data"]) for r in self.all(
            "SELECT capability, data FROM results WHERE id IN (SELECT max(id) FROM results WHERE asset_id = ? "
            "AND status = 'done' GROUP BY capability)", (asset_id,))}
        caps = {r["capability"]: {"state": r["state"], "version": r["version"], "error": r["error"],
                                  "data": done.get(r["capability"])}
                for r in self.all("SELECT * FROM asset_caps WHERE asset_id = ?", (asset_id,))}
        if (caps.get("people") or {}).get("data"):           # identity features stay in the database
            p = caps["people"]["data"]
            caps["people"]["data"] = p | {"faces": self.face_people(asset_id, p.get("faces", []))}
        of = self.one("SELECT id, primary_path FROM assets WHERE id = ?", (a["version_of"],)) if a["version_of"] else None
        versions = [{"id": v["id"], "name": Path(v["primary_path"]).name, "path": v["primary_path"],
                     "reason": v["version_reason"], "preview": v["preview_status"], "online": bool(v["online"])}
                    for v in self.all("SELECT * FROM assets WHERE version_of = ? ORDER BY primary_path", (asset_id,))]
        ai_history = [{"run_id": r["run_id"], "verdict": r["verdict"], "score": r["score"],
                       "reasons": loads(r["reasons"], []), "version": r["version"], "at": r["created_at"]}
                      for r in self.all("SELECT * FROM ai_suggestions WHERE asset_id = ? AND run_id != 'user' "
                                        "ORDER BY id", (asset_id,))]      # 7A: not a burst split's restamp
        mark_history = [{"batch_id": r["batch_id"], "field": r["field"], "old": loads(r["old"]), "new": loads(r["new"]),
                         "actor": r["actor"], "reason": r["reason"], "undone": bool(r["undone"]), "at": r["at"]}
                        for r in self.all("SELECT * FROM mark_history WHERE asset_id = ? ORDER BY id", (asset_id,))]
        tags = self.all("SELECT t.id, t.name, at.source FROM asset_tags at JOIN tags t ON t.id = at.tag_id "
                        "WHERE at.asset_id = ? ORDER BY t.id, at.source", (asset_id,))
        from bioscan.gardener import identities

        subjects = identities.subjects(self.conn(), [asset_id])[asset_id]
        return items[0] | {"subjects": subjects, "files": files, "meta": loads(a["meta"], {}), "lat": a["lat"], "lon": a["lon"], "caps": caps,
                           "ai_history": ai_history, "mark_history": mark_history,
                           "collections": self.collections_of(asset_id), "tags": tags,
                           "original": None if of is None else {"id": of["id"], "name": Path(of["primary_path"]).name,
                                                                "reason": a["version_reason"]},
                           "version_lock": bool(a["version_lock"]), "versions": versions,
                           "encounters": self.all(          # 7A: the identify scope "same encounter" needs its size
                               "SELECT em.encounter_id id, em.task_id, (SELECT count(*) FROM encounter_members x WHERE "
                               "x.encounter_id = em.encounter_id) photos FROM encounter_members em WHERE em.asset_id = ? "
                               "ORDER BY em.task_id", (asset_id,))}

    def face_people(self, asset_id: str, faces: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Faces without their features, each with `person`: {id, name, confirmed} — confirmed when a user
        named that face, else the named person it matches (faces.nearest), else None."""
        fixed = {r["face"]: r for r in self.all("SELECT f.face, p.id, p.name FROM person_faces f JOIN people p "
                                                "ON p.id = f.person_id WHERE f.asset_id = ?", (asset_id,))}
        refs: dict[str, list[str]] = {}
        names: dict[str, str] = {}
        if any(i not in fixed for i in range(len(faces))):
            for r in self.all("SELECT f.person_id, f.vec, p.name FROM person_faces f JOIN people p ON p.id = f.person_id"):
                refs.setdefault(r["person_id"], []).append(r["vec"])
                names[r["person_id"]] = r["name"]
        cents = {}
        if refs:
            from bioscan.gardener import faces as facemod

            cents = {pid: facemod.centroid(vs) for pid, vs in refs.items()}
        out = []
        for i, f in enumerate(faces):
            person = None
            if i in fixed:
                person = {"id": fixed[i]["id"], "name": fixed[i]["name"], "confirmed": True}
            elif cents and f.get("vec") and (pid := facemod.nearest(f["vec"], cents)):
                person = {"id": pid, "name": names[pid], "confirmed": False}
            out.append({k: v for k, v in f.items() if k != "vec"} | {"person": person})
        return out

    def collections_of(self, asset_id: str) -> list[dict[str, Any]]:
        """Live collections (manual by membership, smart by rule) that hold the asset."""
        out = self.all("SELECT c.id, c.name FROM collections c JOIN collection_members cm ON cm.collection_id = c.id "
                       "WHERE cm.asset_id = ? AND cm.removed_at IS NULL AND c.deleted_at IS NULL AND c.kind = 'manual' "
                       "ORDER BY c.created_at", (asset_id,))
        for c in self.all("SELECT id, name FROM collections WHERE kind = 'smart' AND deleted_at IS NULL ORDER BY created_at"):
            sql, ps = query.where(self, {"collection": c["id"]})
            if self.one(f"SELECT 1 FROM assets a WHERE a.id = ? AND {sql}", [asset_id, *ps]):
                out.append(c)
        return out

    def search(self, filters: dict[str, Any], *, order: str = "time", limit: int = 200,
               offset: int = 0) -> dict[str, Any]:
        sql, ps = query.where(self, filters)
        disc = "(SELECT min(ta.ord) FROM task_assets ta WHERE ta.asset_id = a.id" + (
            " AND ta.task_id = ?)" if filters.get("task") else ")")
        by = {"time": "a.taken_at IS NULL, a.taken_at, a.primary_path",
              "path": "a.primary_path",
              "discovered": disc,
              "score": "(SELECT s.score FROM ai_suggestions s WHERE s.asset_id = a.id ORDER BY s.id DESC LIMIT 1) "
                       "IS NULL, (SELECT s.score FROM ai_suggestions s WHERE s.asset_id = a.id ORDER BY s.id DESC "
                       "LIMIT 1) DESC, a.taken_at, a.primary_path"}.get(order)
        if by is None:
            raise Error("order must be discovered|time|path|score")
        total = self.one(f"SELECT count(*) n FROM assets a WHERE {sql}", ps)["n"]
        ops = [filters["task"]] if order == "discovered" and filters.get("task") else []
        ids = [r["id"] for r in self.all(f"SELECT a.id FROM assets a WHERE {sql} ORDER BY {by}, a.id LIMIT ? OFFSET ?",
                                         [*ps, *ops, limit, offset])]
        return {"total": total, "items": self.asset_items(ids), "offset": offset}

    def ids_where(self, filters: dict[str, Any]) -> list[str]:
        sql, ps = query.where(self, filters)
        return [r["id"] for r in self.all(f"SELECT a.id FROM assets a WHERE {sql} ORDER BY a.taken_at, a.primary_path, a.id",
                                          ps)]

    # ---- marks (human layer, global per asset) ----
    def set_marks(self, ids: list[str], fields: dict[str, Any], *, actor: str, reason: str | None = None,
                  expect: dict[str, int] | None = None) -> dict[str, Any]:
        with self.tx() as c:
            return apply_marks(c, ids, fields, actor=actor, reason=reason, expect=expect)

    def undo(self, batch_id: str, actor: str) -> dict[str, Any]:
        with self.tx() as c:
            rows = [dict(r) for r in c.execute("SELECT * FROM mark_history WHERE batch_id = ? AND undone = 0 ORDER BY id",
                                               (batch_id,))]
            if not rows:
                raise NotFound(f"no batch {batch_id} to undo")
            last: dict[str, int] = {}
            for r in rows:
                last[r["asset_id"]] = max(last.get(r["asset_id"], 0), r["id"])
            conflicts = [{"id": a, "version": mark_row(c, a)["version"]} for a, top in last.items()
                         if c.execute("SELECT 1 FROM mark_history WHERE asset_id = ? AND id > ? AND undone = 0",
                                      (a, top)).fetchone()]
            if conflicts:
                raise Conflict("这些素材在该批之后又被修改，不能撤销", conflicts=conflicts)
            new_batch, t = new_id("b"), now()
            for a in last:
                cur = mark_row(c, a)
                vals = {"use": cur["use"], "stars": cur["stars"]}
                for r in rows:
                    if r["asset_id"] == a:          # a "flag" row predates the four uses (schema < 3)
                        vals["use" if r["field"] == "flag" else r["field"]] = (
                            USE_OF_FLAG.get(loads(r["old"])) if r["field"] == "flag" else loads(r["old"]))
                        c.execute("INSERT INTO mark_history (batch_id, asset_id, field, old, new, actor, reason, undone, at) "
                                  "VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)",
                                  (new_batch, a, r["field"], r["new"], r["old"], actor, f"撤销 {batch_id}", t))
                write_mark(c, a, vals["use"], vals["stars"], cur["version"] + 1, t)
            c.execute("UPDATE mark_history SET undone = 1 WHERE batch_id = ?", (batch_id,))
            return {"undone": len(last), "batch_id": new_batch}

    # ---- collections ----
    def collection(self, cid: str) -> dict[str, Any]:
        row = self.one("SELECT * FROM collections WHERE id = ?", (cid,))
        if row is None:
            raise NotFound(f"no collection {cid}")
        return self.collection_json(row)

    def collection_json(self, row: dict[str, Any]) -> dict[str, Any]:
        sql, ps = query.where(self, {"collection": row["id"]})
        s = self.one("SELECT count(*) size, coalesce(sum(m.flag IS NOT NULL OR coalesce(m.stars, 0) > 0), 0) judged, "
                     "coalesce(sum(m.flag = 'pick'), 0) picks, coalesce(sum(m.flag = 'reject'), 0) rejects "
                     f"FROM assets a LEFT JOIN marks m ON m.asset_id = a.id WHERE {sql}", ps)
        ai = {r["verdict"]: r["n"] for r in self.all(
            "SELECT verdict, count(*) n FROM ai_suggestions WHERE id IN (SELECT max(s.id) FROM ai_suggestions s "
            f"JOIN assets a ON a.id = s.asset_id WHERE {sql} GROUP BY s.asset_id) GROUP BY verdict", ps)}
        covers = [r["id"] for r in self.all(
            f"SELECT a.id FROM assets a WHERE {sql} ORDER BY a.preview_status != 'ready', a.taken_at, a.primary_path "
            "LIMIT 4", ps)]
        rev = self.one("SELECT * FROM collection_reviews WHERE collection_id = ? ORDER BY id DESC LIMIT 1", (row["id"],))
        summ = loads(row.get("summary"), {}) or {}
        state = REVIEW_STATE[rev["action"]] if rev else "open"
        new_since = removed_since = changed_since = 0
        if rev and state != "open":
            snap = set(loads(rev["members"], []))
            now_ids = set(self.ids_where({"collection": row["id"]}))
            new_since, removed_since = len(now_ids - snap), len(snap - now_ids)
            changed_since = sum(1 for part in chunks(sorted(now_ids & snap)) for _ in self.all(
                f"SELECT 1 FROM assets WHERE id IN ({marks_in(part)}) "
                "AND json_extract(meta, '$.content_changed_at') > ?", [*part, rev["at"]]))
        return {"id": row["id"], "kind": row["kind"], "origin": row["origin"], "group_kind": row["group_kind"],
                "parent_id": row.get("parent_id"),
                "task_id": row["task_id"], "name": row["name"], "description": row["description"],
                "rule": loads(row["rule"]), "version": row["version"], "deleted_at": row["deleted_at"],
                "size": s["size"], "cover_ids": covers, "judged": s["judged"], "picks": s["picks"],
                "rejects": s["rejects"], "ai_picks": ai.get("pick", 0), "ai_rejects": ai.get("reject", 0),
                "low_score": s["size"] > 0 and ai.get("reject", 0) == s["size"],
                "review": {"state": state, "at": rev["at"] if rev else None, "unhandled": s["size"] - s["judged"],
                           "new_since": new_since, "removed_since": removed_since,
                           "changed_since": changed_since},
                "user_edited": bool(row["user_edited"]),
                "time_basis": summ.get("time_basis"), "uncertain": len(summ.get("uncertain_ids") or []),
                "label": summ.get("label"), "created_at": row["created_at"], "updated_at": row["updated_at"],
                "batch": bool(self.one("SELECT 1 FROM import_plans WHERE collection_id = ?", (row["id"],)))}

    def collections(self, *, task: str | None = None, kind: str | None = None, deleted: bool = False) -> list[dict]:
        conds, ps = ["deleted_at IS NOT NULL" if deleted else "deleted_at IS NULL"], []
        if task:
            conds.append("task_id = ?")
            ps.append(task)
        if kind:
            conds.append("kind = ?")
            ps.append(kind)
        rows = self.all(f"SELECT * FROM collections WHERE {' AND '.join(conds)} ORDER BY "
                        "group_kind != 'all', created_at, name", ps)
        return [self.collection_json(r) for r in rows]

    # ---- tags ----
    def tags(self) -> list[dict[str, Any]]:
        return [{"id": t["id"], "name": t["name"], "parent_id": t["parent_id"], "aliases": loads(t["aliases"], []),
                 "active": bool(t["active"]), "merged_into": t["merged_into"], "count": t["n"]}
                for t in self.all("SELECT t.*, (SELECT count(DISTINCT asset_id) FROM asset_tags x WHERE x.tag_id = t.id) n "
                                  "FROM tags t ORDER BY t.id")]


# ---- helpers shared with tasks.py and api.py (they take an open transaction) ----

def run_json(r: dict[str, Any]) -> dict[str, Any]:
    return {"id": r["id"], "task_id": r["task_id"], "seq": r["seq"], "kind": r["kind"], "status": r["status"],
            "phase": r["phase"], "config": loads(r["config"], {}), "scope": loads(r["scope"], {}),
            "counts": loads(r["counts"], {}), "summary": loads(r["summary"]), "error": r["error"],
            "queued_at": r["queued_at"], "started_at": r["started_at"], "finished_at": r["finished_at"]}


def progress(status: str, k: dict[str, Any]) -> float:
    if status in ("completed", "partial"):
        return 1.0
    total = k.get("to_preview", 0) + k.get("total_analysis", 0)
    done = k.get("previewed", 0) + k.get("preview_failed", 0) + k.get("analyzed", 0)
    return round(min(0.99, done / total), 3) if total else 0.0     # 1.0 only once the run has ended


def analysis_state(states: list[str], in_run: bool) -> str:
    states = [x for x in states if x != "not_applicable"]     # a video's image capabilities
    if not states or "pending" in states:
        return "running" if states and in_run else "pending"
    if all(s in ("done", "unrecognized") for s in states):
        return "done"
    for s in ("unavailable", "failed"):
        if all(x == s for x in states):
            return s
    return "partial"


def mark_row(c: sqlite3.Connection, asset_id: str) -> dict[str, Any]:
    r = c.execute("SELECT use, stars, version FROM marks WHERE asset_id = ?", (asset_id,)).fetchone()
    return {"use": None, "stars": 0, "version": 0} if r is None else dict(r)


USE_OF_FLAG = {"pick": "record", "reject": "reject", None: None}


def write_mark(c: sqlite3.Connection, asset_id: str, use: str | None, stars: int, version: int, t: str) -> None:
    """The only writer of marks: `flag` is derived from `use` here, so the two never disagree."""
    c.execute("INSERT INTO marks (asset_id, use, flag, stars, updated_at, version) VALUES (?, ?, ?, ?, ?, ?) "
              "ON CONFLICT(asset_id) DO UPDATE SET use = excluded.use, flag = excluded.flag, stars = excluded.stars, "
              "updated_at = excluded.updated_at, version = excluded.version",
              (asset_id, use, FLAG_OF_USE[use], stars, t, version))
    c.execute("UPDATE assets SET updated_at = ? WHERE id = ?", (t, asset_id))


def apply_marks(c: sqlite3.Connection, ids: list[str], fields: dict[str, Any], *, actor: str,
                reason: str | None = None, expect: dict[str, int] | None = None,
                batch: str | None = None) -> dict[str, Any]:
    """Set use (record|edit|reject|None = pending) and/or stars on `ids` as one batch (`batch` joins an
    existing one); Conflict when an `expect`ed version is stale. `flag` is the older form: pick = record
    (an edit photo stays edit), reject = reject, None = pending."""
    ids = list(dict.fromkeys(ids))
    found = {r[0] for part in chunks(ids) for r in c.execute(
        f"SELECT id FROM assets WHERE id IN ({marks_in(part)})", part)}
    missing = [i for i in ids if i not in found]
    if missing:
        raise NotFound(f"no assets {missing[:5]}")
    cur = {i: mark_row(c, i) for i in ids}
    conflicts = [{"id": i, "version": cur[i]["version"]} for i, v in (expect or {}).items()
                 if i in cur and cur[i]["version"] != v]
    if conflicts:
        raise Conflict("素材已被其他客户端修改", conflicts=conflicts)
    batch, t = batch or new_id("b"), now()
    out = []
    for i in ids:
        old = cur[i]
        use = old["use"]
        if "use" in fields:
            use = fields["use"]
        elif "flag" in fields:
            use = old["use"] if fields["flag"] == "pick" and old["use"] in ("record", "edit") else \
                USE_OF_FLAG[fields["flag"]]
        new = {"use": use, "stars": fields["stars"] if "stars" in fields else old["stars"]}
        changed = [f for f in ("use", "stars") if new[f] != old[f]]
        version = old["version"] + (1 if changed else 0)
        if changed:
            for f in changed:
                c.execute("INSERT INTO mark_history (batch_id, asset_id, field, old, new, actor, reason, at) "
                          "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (batch, i, f, dumps(old[f]), dumps(new[f]), actor, reason, t))
            write_mark(c, i, new["use"], new["stars"], version, t)
        out.append({"id": i, "use": new["use"] or "pending", "flag": FLAG_OF_USE[new["use"]], "stars": new["stars"],
                    "version": version})
    return {"batch_id": batch, "assets": out}


def migrate_uses(c: sqlite3.Connection) -> int:
    """Schema 3 (PRD §15): a Pick becomes record, a Reject reject, the rest stay pending; each change is a
    system mark_history row (reason migrate:flag) in one batch, so where a use came from stays visible."""
    batch, t, n = new_id("b"), now(), 0
    for r in c.execute("SELECT asset_id, flag FROM marks WHERE use IS NULL AND flag IS NOT NULL").fetchall():
        use = USE_OF_FLAG[r["flag"]]
        c.execute("UPDATE marks SET use = ? WHERE asset_id = ?", (use, r["asset_id"]))
        c.execute("INSERT INTO mark_history (batch_id, asset_id, field, old, new, actor, reason, at) "
                  "VALUES (?, ?, 'use', NULL, ?, 'system', ?, ?)", (batch, r["asset_id"], dumps(use),
                                                                     f"migrate:flag:{r['flag']}", t))
        n += 1
    return n


def collection_history(c: sqlite3.Connection, cid: str, action: str, detail: dict[str, Any], actor: str) -> None:
    c.execute("INSERT INTO collection_history (collection_id, action, detail, actor, at) VALUES (?, ?, ?, ?, ?)",
              (cid, action, dumps(detail), actor, now()))


def create_collection(c: sqlite3.Connection, *, name: str, kind: str = "manual", origin: str = "user",
                      group_kind: str | None = None, auto_key: str | None = None, task_id: str | None = None,
                      description: str = "", rule: dict[str, Any] | None = None, asset_ids: list[str] = (),
                      actor: str = "api") -> str:
    cid, t = new_id("c"), now()
    c.execute("INSERT INTO collections (id, kind, origin, group_kind, auto_key, task_id, name, description, rule, "
              "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
              (cid, kind, origin, group_kind, auto_key, task_id, name, description,
               None if rule is None else dumps(rule), t, t))
    add_members(c, cid, list(asset_ids), "auto" if origin == "auto" else "user")
    collection_history(c, cid, "create", {"name": name, "kind": kind, "size": len(asset_ids)}, actor)
    return cid


def add_members(c: sqlite3.Connection, cid: str, ids: list[str], by: str) -> int:
    """Add `ids`; a person's add revives a tombstone, an auto add never does. Returns how many joined."""
    t, n = now(), 0
    for i in ids:
        row = c.execute("SELECT removed_at FROM collection_members WHERE collection_id = ? AND asset_id = ?",
                        (cid, i)).fetchone()
        if row is None:
            c.execute("INSERT INTO collection_members (collection_id, asset_id, added_by, added_at) VALUES (?, ?, ?, ?)",
                      (cid, i, by, t))
        elif row[0] is not None and by == "user":
            c.execute("UPDATE collection_members SET removed_at = NULL, removed_by = NULL, added_by = 'user', "
                      "added_at = ? WHERE collection_id = ? AND asset_id = ?", (t, cid, i))
        else:
            continue
        n += 1
    return n


def remove_members(c: sqlite3.Connection, cid: str, ids: list[str], by: str = "user") -> int:
    t = now()
    return sum(c.execute("UPDATE collection_members SET removed_at = ?, removed_by = ? WHERE collection_id = ? "
                         "AND asset_id = ? AND removed_at IS NULL", (t, by, cid, i)).rowcount for i in ids)


def ensure_tag(c: sqlite3.Connection, tag_id: str, name: str, parent_id: str | None = None) -> str | None:
    """The tag an AI label lands on: created on first use, followed through merges; None if switched off."""
    t = now()
    if parent_id == tag_id:
        parent_id = None
    c.execute("INSERT OR IGNORE INTO tags (id, name, parent_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
              (tag_id, name, parent_id, t, t))
    for _ in range(16):
        row = c.execute("SELECT active, merged_into FROM tags WHERE id = ?", (tag_id,)).fetchone()
        if row["merged_into"]:
            tag_id = row["merged_into"]
            continue
        return tag_id if row["active"] else None
    return None
