"""Lightroom handoff (契约修订 5E, PRD §12): freeze a set of assets, write one handoff file the bioscan
Lightroom Classic plugin applies (extensions/lightroom/bioscan.lrplugin/Handoff.lua), and read the plugin's
per-photo result back. Gardener never writes into photo folders for this; the plugin works on the catalog.

The field-conflict rules (`decide_stars`, `decide_keywords`) are mirrored line for line in Decide.lua; the tests
run both on the same cases. Everything taking `c` runs inside an open store transaction."""
from __future__ import annotations

import functools
import json
import os
from pathlib import Path
from typing import Any

from bioscan.gardener import store as st
from bioscan.gardener.store import dumps, loads, now

DOC_SCHEMA = 2                 # latest.json (`bioscan lr open`) is schema 1
EDITOR = "lightroom"
ROOT_SET = "Gardener"          # the Lightroom collection set handoff collections go in
ROLE_ORDER = {"managed": 0, "raw": 1, "jpeg": 2, "image": 3, "video": 4}   # the file Lightroom should hold first


def default_dir() -> Path:
    return Path.home() / "Library" / "Application Support" / "bioscan" / "lightroom"


def uses_of(c: Any, asset_ids: list[str]) -> dict[str, str]:
    """asset id -> its use (contract revision 5B, marks.use): record | edit | pending | reject; no mark = pending."""
    out = dict.fromkeys(asset_ids, "pending")
    for part in st.chunks(asset_ids):
        for r in c.execute(f"SELECT asset_id, use FROM marks WHERE asset_id IN ({st.marks_in(part)})", part):
            out[r[0]] = r[1] or "pending"
    return out


HANDOFF_USES = ("edit",)          # PRD §12: by default only 选入修图 goes to Lightroom


# ---- field conflict rules (mirrored in Decide.lua) ----
def decide_stars(current: int | None, baseline: int | None, wanted: int) -> str:
    """same | none | write | conflict. `baseline` = the rating Gardener wrote last time (None = never)."""
    current = current or 0
    if wanted == 0 and not baseline:
        return "none"                  # Gardener has no stars and none of its own are there: Lightroom's stays
    if wanted == current:
        return "same"
    if baseline is None:
        return "write" if current == 0 else "conflict"
    return "write" if current == baseline else "conflict"


def decide_keywords(current: list[str], baseline: list[str] | None,
                    wanted: list[str]) -> tuple[str, list[str], list[str]]:
    """(same | write | conflict, chains to add, chains to remove). Chains are 'Aves|Order|Family|Species'.
    `current` = every keyword chain on the photo; only chains Gardener wrote (baseline) are ever removed, so a
    person's own keywords are never touched. A baseline chain missing from the photo = changed in Lightroom."""
    cur, base, want = set(current), set(baseline or []), set(wanted)
    if base - cur:
        return "conflict", [], []
    add, remove = sorted(want - cur), sorted(base - want)
    return ("write" if add or remove else "same"), add, remove


# 契约修订 7B: develop preset and export step (mirrored in Decide.lua)
def decide_develop(wanted: str | None, base_uuid: str | None, base_fp: str | None, current_fp: str | None) -> str:
    """none | same | apply | conflict for a develop preset (`wanted` = its uuid). `base_*` = the preset Gardener
    applied last time and the photo's develop fingerprint right after; a different fingerprint now = edited in
    Lightroom since, which a preset would overwrite: skipped and reported. Never applied before = apply (the person
    saw the preset, count and representatives first)."""
    if not wanted:
        return "none"
    if base_fp is not None and current_fp != base_fp:
        return "conflict"
    if base_uuid == wanted:
        return "same"
    return "apply"


def decide_export(wanted: bool, status: str, develop_action: str | None) -> str:
    """none | skip | export: a photo is exported only when it is in Lightroom (status ok) and its wanted preset did
    not fail (a conflict exports the person's own edit)."""
    if not wanted:
        return "none"
    if status != "ok" or develop_action == "failed":
        return "skip"
    return "export"
# ---- end field conflict rules ----


@functools.lru_cache(maxsize=2)
def _ranks(lists_dir: str) -> dict[str, list[str]]:
    """concept (and name:<scientific>) -> [class, order, family] from the bird and mammal lists; {} without them."""
    from bioscan.service import names as lists

    out: dict[str, list[str]] = {}
    for sub, read in (("avilist", lists.read_avilist), ("mdd", lists.read_mdd)):
        files = sorted((Path(lists_dir) / sub).glob("*.csv"))
        for sci, _common, tax, concept in read(files[0]) if files else []:
            out[f"name:{sci}"] = out[concept or f"name:{sci}"] = tax[2:5]
    return out


CLASS_OF = {"avibase": "Aves", "mdd": "Mammalia", "bird": "Aves", "mammal": "Mammalia"}


def keyword(concept: str, taxon: str, kind: str | None, lists_dir: Path | None) -> list[str]:
    """The hierarchical keyword of a species: Class|Order|Family|Scientific, or Class|Scientific when the
    lists do not know it."""
    ranks = _ranks(str(lists_dir)).get(concept) if lists_dir else None
    if ranks:
        return [*ranks, taxon]
    return [CLASS_OF.get(concept.split(":")[0]) or CLASS_OF.get(kind or "") or "Animalia", taxon]


def _keywords(c: Any, asset_id: str, lists_dir: Path | None) -> list[dict[str, Any]]:
    """Species keywords from a person's species-level claims only (never a model's)."""
    from bioscan.gardener import identities, taxa

    claims = [(s["current"], s["kind"]) for s in identities.subjects(c, [asset_id])[asset_id]
              if s["human"] and s["current"]["level"] == "species" and s["current"]["concept"]]
    shown = taxa.display(c, [x["concept"] for x, _ in claims])
    out: dict[str, dict[str, Any]] = {}
    for x, kind in claims:
        n = shown.get(x["concept"]) or {}
        chain = keyword(x["concept"], n.get("scientific") or x["taxon"], kind, lists_dir)
        syn = [s for s in dict.fromkeys((n.get("common") or x["common"], n.get("zh"))) if s]
        out.setdefault("|".join(chain), {"chain": chain, "synonyms": syn, "concept": x["concept"]})
    return list(out.values())


def select(c: Any, ids: list[str], uses: tuple[str, ...] | None) -> list[str]:
    """`ids` filtered to `uses` (None = all but reject)."""
    use = uses_of(c, ids)
    return [a for a in ids if (use[a] in uses if uses else use[a] != "reject")]


def create(c: Any, ids: list[str], *, name: str, scope: dict[str, Any], uses: tuple[str, ...] | None,
           actor: str, lightroom_dir: Path, lists_dir: Path | None, options: dict[str, Any] | None = None) -> str:
    """Freeze `ids` (filtered to `uses`; None = all but reject), check each asset's files are online, and write
    `<lightroom_dir>/handoffs/<id>.json`. An offline asset is recorded as skipped and not sent. `options`
    (契约修订 7B): {develop, export} the plugin applies after the fields, recorded for a retry."""
    use = uses_of(c, ids)
    ids = select(c, ids, uses)
    options = options or {}
    if not ids:
        raise st.Error("范围内没有要交接的照片（默认只交接选入修图的）")
    hid, t = st.new_id("h"), now()
    links = {r["asset_id"]: r for r in c.execute(
        f"SELECT * FROM editor_links WHERE editor = ? AND asset_id IN ({st.marks_in(ids)})", [EDITOR, *ids])}
    stars = {r[0]: r[1] for r in c.execute(
        f"SELECT asset_id, stars FROM marks WHERE asset_id IN ({st.marks_in(ids)})", ids)}
    photos, rows = [], []
    for i, aid in enumerate(ids):
        files = sorted(c.execute("SELECT path, role FROM asset_files WHERE asset_id = ? UNION SELECT path, 'managed' "
                                 "FROM managed_files WHERE asset_id = ?", (aid, aid)).fetchall(),
                       key=lambda f: (ROLE_ORDER.get(f["role"], 9), f["path"]))   # the imported copy first (5D)
        paths = [f["path"] for f in files if os.path.exists(f["path"])]
        link = links.get(aid)
        if not paths:
            rows.append((hid, aid, i, None, "skipped", "offline", None))
            continue
        base = loads(link["baseline"], {}) if link else {}
        p = {"asset_id": aid, "paths": paths, "uuid": link["uuid"] if link else None, "stars": stars.get(aid, 0),
             "use": use[aid], "keywords": _keywords(c, aid, lists_dir),
             "baseline": {"stars": base.get("stars"), "keywords": base.get("keywords")}
             | ({"develop": base["develop"]} if base.get("develop") else {})}     # 7B: only once a preset was applied
        photos.append(p)
        rows.append((hid, aid, i, dumps(p), "pending", None, None))
    doc_path = Path(lightroom_dir) / "handoffs" / f"{hid}.json"
    c.execute("INSERT INTO handoffs (id, name, scope, doc_path, status, actor, created_at, options) "
              "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
              (hid, name, dumps(scope), str(doc_path), "written" if photos else "failed", actor, t, dumps(options)))
    c.executemany("INSERT INTO handoff_items VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    if photos:                         # raises inside the transaction: no handoff is recorded without its file
        doc_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = doc_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"schema": DOC_SCHEMA, "kind": "gardener-handoff", "id": hid, "name": name,
                                   "collection_set": ROOT_SET, "created_at": t, "photos": photos,
                                   "develop": options.get("develop"), "export": options.get("export")},
                                  ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, doc_path)
    return hid


def result_path(doc_path: str | Path) -> Path:
    p = Path(doc_path)
    return p.with_name(p.stem + ".result.json")


def ingest(c: Any, hid: str) -> bool:
    """Read the plugin's result file once (if it is there): per-photo status, and the Lightroom identity and new
    baseline of each photo into editor_links. A field skipped for a conflict keeps its old baseline."""
    h = c.execute("SELECT * FROM handoffs WHERE id = ?", (hid,)).fetchone()
    rp = result_path(h["doc_path"])
    if h["result_at"] is not None or not rp.is_file():
        return False
    t = now()
    try:
        doc = json.loads(rp.read_text(encoding="utf-8"))
    except ValueError as exc:
        doc = {"error": f"结果文件无法读取: {exc}", "photos": []}
    sent = {r["asset_id"]: loads(r["sent"]) for r in c.execute(
        "SELECT asset_id, sent FROM handoff_items WHERE handoff_id = ? AND sent IS NOT NULL", (hid,))}
    for r in doc.get("photos") or []:
        aid = r.get("asset_id")
        if aid not in sent:
            continue
        status = r.get("status") if r.get("status") in ("ok", "skipped", "failed") else "failed"
        c.execute("UPDATE handoff_items SET status = ?, reason = ?, result = ? WHERE handoff_id = ? AND asset_id = ?",
                  (status, r.get("reason"), dumps(r), hid, aid))
        if status != "ok":
            continue
        old = sent[aid]["baseline"]
        base = {k: (r[k].get("value") if (r.get(k) or {}).get("action") in ("write", "same") else old.get(k))
                for k in ("stars", "keywords")}
        link = c.execute("SELECT baseline, progress FROM editor_links WHERE asset_id = ? AND editor = ?",
                         (aid, EDITOR)).fetchone()
        dev = r.get("develop") or {}
        base["develop"] = ({"uuid": dev.get("preset"), "fingerprint": dev.get("fingerprint")}
                           if dev.get("action") in ("applied", "same") and dev.get("fingerprint") else
                           loads(link["baseline"], {}).get("develop") if link else None)
        progress = progress_after(loads(link["progress"], {}) if link else {}, r, hid, t)
        c.execute("INSERT INTO editor_links (asset_id, editor, photo_id, uuid, path, baseline, handoff_id, updated_at, "
                  "progress) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(asset_id, editor) DO UPDATE SET "
                  "photo_id = excluded.photo_id, uuid = excluded.uuid, path = excluded.path, baseline = excluded.baseline, "
                  "handoff_id = excluded.handoff_id, updated_at = excluded.updated_at, progress = excluded.progress",
                  (aid, EDITOR, None if r.get("photo_id") is None else str(r["photo_id"]), r.get("uuid"), r.get("path"),
                   dumps(base), hid, t, dumps(progress)))
    c.execute("UPDATE handoff_items SET status = 'failed', reason = ? WHERE handoff_id = ? AND status = 'pending'",
              (doc.get("error") or "插件未返回结果", hid))
    states = [r[0] for r in c.execute("SELECT status FROM handoff_items WHERE handoff_id = ?", (hid,))]
    status = "applied" if all(s == "ok" for s in states) else "partial" if "ok" in states else "failed"
    c.execute("UPDATE handoffs SET status = ?, result_at = ? WHERE id = ?", (status, t, hid))
    return True


def get(c: Any, hid: str) -> dict[str, Any]:
    h = c.execute("SELECT * FROM handoffs WHERE id = ?", (hid,)).fetchone()
    if h is None:
        raise st.NotFound(f"no handoff {hid}")
    items = []
    for r in c.execute("SELECT i.*, a.primary_path FROM handoff_items i JOIN assets a ON a.id = i.asset_id "
                       "WHERE handoff_id = ? ORDER BY ord", (hid,)):
        res = loads(r["result"]) or {}
        conflicts = [k for k in ("stars", "keywords") if (res.get(k) or {}).get("action") == "conflict"]
        if (res.get("develop") or {}).get("action") == "conflict":
            conflicts.append("develop")
        items.append({"asset_id": r["asset_id"], "name": Path(r["primary_path"]).name, "status": r["status"],
                      "reason": r["reason"], "conflicts": conflicts, "sent": loads(r["sent"]), "result": res or None,
                      "develop": res.get("develop"), "export": res.get("export")})
    counts = {k: sum(i["status"] == k for i in items) for k in ("ok", "skipped", "failed", "pending")}
    counts["conflicts"] = sum(bool(i["conflicts"]) for i in items)
    counts["preset_applied"] = sum((i["develop"] or {}).get("action") in ("applied", "same") for i in items)
    counts["exported"] = sum((i["export"] or {}).get("action") == "exported" for i in items)
    retryable = sum(i["status"] == "failed" or (i["status"] == "skipped" and i["reason"] in RETRY_SKIPS) for i in items)
    return {"id": h["id"], "name": h["name"], "scope": loads(h["scope"]), "status": h["status"],
            "options": loads(h["options"], {}), "retryable": retryable if h["result_at"] else 0,
            "doc_path": h["doc_path"], "result_path": str(result_path(h["doc_path"])), "actor": h["actor"],
            "created_at": h["created_at"], "result_at": h["result_at"], "counts": counts, "items": items}


# ---- 契约修订 7B: retry only what did not reach Lightroom -----------------------------------------------------
RETRY_SKIPS = ("offline", "file_missing")      # a skip a person can fix (bring the disk back), unlike a conflict


def retry(c: Any, hid: str, *, actor: str, lightroom_dir: Path, lists_dir: Path | None) -> str:
    """A new handoff of the failed photos of `hid` and those skipped for an offline or missing file, with the same
    name (so the same Lightroom collection), uses and options. editor_links make it find photos already in the
    catalog, so nothing is imported twice. 409 before the plugin returned a result; 400 when nothing failed."""
    h = c.execute("SELECT * FROM handoffs WHERE id = ?", (hid,)).fetchone()
    if h is None:
        raise st.NotFound(f"no handoff {hid}")
    ingest(c, hid)
    h = c.execute("SELECT * FROM handoffs WHERE id = ?", (hid,)).fetchone()
    if h["result_at"] is None:
        raise st.Conflict("Lightroom 还没有返回这次交接的结果；先刷新结果，再重试失败项")
    ids = [r[0] for r in c.execute(
        f"SELECT asset_id FROM handoff_items WHERE handoff_id = ? AND (status = 'failed' OR (status = 'skipped' AND "
        f"reason IN ({st.marks_in(list(RETRY_SKIPS))}))) ORDER BY ord", (hid, *RETRY_SKIPS))]
    if not ids:
        raise st.Error("这次交接没有失败或因离线跳过的照片")
    scope = loads(h["scope"], {})
    uses = tuple(scope["uses"]) if scope.get("uses") else None
    return create(c, ids, name=h["name"], scope={"retry_of": hid, "asset_ids": ids, "uses": uses}, uses=uses,
                  actor=actor, lightroom_dir=lightroom_dir, lists_dir=lists_dir, options=loads(h["options"], {}))


# ---- 契约修订 7B: develop presets, export, 修图进度 ------------------------------------------------------------
PROGRESS = ("in_lightroom", "preset_applied", "exported")      # 修图进度, each only from a real per-photo result
OVERWRITE = ("rename", "skip", "overwrite")                     # existing files in the export folder


def progress_after(old: dict[str, Any], r: dict[str, Any], hid: str, t: str) -> dict[str, Any]:
    """The asset's 修图进度 after one ok per-photo result: in Lightroom; preset applied when the plugin says applied or
    already there; exported only with the path the export really wrote. It never goes back (a later handoff without
    an export leaves "exported")."""
    rank = {s: i for i, s in enumerate(PROGRESS)}
    out = dict(old)
    state = max(old.get("state") or PROGRESS[0], PROGRESS[0], key=rank.__getitem__)
    dev, exp = r.get("develop") or {}, r.get("export") or {}
    if dev.get("action") in ("applied", "same"):
        state = max(state, "preset_applied", key=rank.__getitem__)
        out |= {"preset": dev.get("preset_name") or dev.get("preset"), "preset_at": t}
    if exp.get("action") == "exported" and exp.get("path"):
        state = "exported"
        out |= {"exported_path": exp["path"], "exported_at": t}
    return out | {"state": state, "handoff_id": hid}


def check_options(body: dict[str, Any], protected: list[Path]) -> dict[str, Any]:
    """{develop, export} from a POST /handoffs body; Error on anything unclear (PRD §12: the preset, template,
    destination folder and overwrite policy must be explicit). The export folder must lie outside every source."""
    from bioscan.gardener.imports import check_dest

    out: dict[str, Any] = {}
    d = body.get("develop")
    if d is not None:
        if not isinstance(d, dict) or not isinstance(d.get("uuid"), str) or not d["uuid"]:
            raise st.Error("develop 需要 {uuid, name}：Lightroom 显影预设的 uuid")
        out["develop"] = {"uuid": d["uuid"], "name": str(d.get("name") or d["uuid"])}
    e = body.get("export")
    if e is not None:
        if not isinstance(e, dict):
            raise st.Error("export 需要 {preset_path, dest_dir, overwrite}")
        path = e.get("preset_path")
        if not isinstance(path, str) or not path.endswith(".lrtemplate") or not os.path.isabs(path):
            raise st.Error("export.preset_path 必须是导出预设文件（.lrtemplate）的绝对路径")
        if e.get("overwrite") not in OVERWRITE:
            raise st.Error(f"export.overwrite 必须是 {'|'.join(OVERWRITE)} 之一（目标已有同名文件时怎么办）")
        dest = check_dest(e.get("dest_dir"), protected)
        out["export"] = {"preset": str(e.get("preset") or Path(path).stem), "preset_path": path, "dest_dir": str(dest),
                         "overwrite": e["overwrite"]}
    return out


def scope_key(scope: dict[str, Any]) -> str | None:
    """Where a chosen preset is remembered: a species or a collection (an import batch is one, 契约修订 7B)."""
    if scope.get("concept"):
        return f"concept:{scope['concept']}"
    if scope.get("collection_id"):
        return f"collection:{scope['collection_id']}"
    return None


def remembered(c: Any, key: str | None) -> dict[str, Any] | None:
    r = c.execute("SELECT * FROM editor_presets WHERE scope_key = ?", (key,)).fetchone() if key else None
    return None if r is None else {"develop": loads(r["develop"]), "export": loads(r["export"]), "updated_at": r["updated_at"]}


def remember(c: Any, key: str, options: dict[str, Any], actor: str) -> None:
    c.execute("INSERT INTO editor_presets (scope_key, develop, export, actor, updated_at) VALUES (?, ?, ?, ?, ?) "
              "ON CONFLICT(scope_key) DO UPDATE SET develop = excluded.develop, export = excluded.export, "
              "actor = excluded.actor, updated_at = excluded.updated_at",
              (key, dumps(options.get("develop")), dumps(options.get("export")), actor, now()))


def presets(lightroom_dir: Path) -> dict[str, Any]:
    """The develop and export presets the plugin found in Lightroom (it writes presets.json while it runs)."""
    p = Path(lightroom_dir) / "presets.json"
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"available": False, "at": None, "develop": [], "export": [], "path": str(p)}
    return {"available": True, "at": doc.get("at"), "develop": doc.get("develop") or [],
            "export": doc.get("export") or [], "path": str(p)}


def preview(c: Any, ids: list[str], uses: tuple[str, ...] | None, key: str | None) -> dict[str, Any]:
    """What a handoff would send, shown before it is made (PRD §12): the count, how many are offline, up to four
    representatives (most stars first) and the preset remembered for this species or collection."""
    ids = select(c, ids, uses)
    stars = {r[0]: r[1] for part in st.chunks(ids)
             for r in c.execute(f"SELECT asset_id, stars FROM marks WHERE asset_id IN ({st.marks_in(part)})", part)}
    offline = 0
    for aid in ids:
        paths = [f[0] for f in c.execute("SELECT path FROM asset_files WHERE asset_id = ? UNION SELECT path FROM "
                                         "managed_files WHERE asset_id = ?", (aid, aid))]
        offline += not any(os.path.exists(x) for x in paths)
    reps = sorted(ids, key=lambda a: (-(stars.get(a) or 0), ids.index(a)))[:4]
    return {"count": len(ids), "offline": offline, "representatives": reps, "remembered": remembered(c, key)}


def link(c: Any, asset_id: str) -> dict[str, Any] | None:
    """The asset's Lightroom link and 修图进度 (AssetDetail.lightroom), or None before any real result."""
    r = c.execute("SELECT * FROM editor_links WHERE asset_id = ? AND editor = ?", (asset_id, EDITOR)).fetchone()
    return None if r is None else {"photo_id": r["photo_id"], "uuid": r["uuid"], "path": r["path"],
                                   "handoff_id": r["handoff_id"], "progress": loads(r["progress"], {}) or None}
