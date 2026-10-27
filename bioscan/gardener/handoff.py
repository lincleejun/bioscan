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


# ---- uses adapter: the one place to switch when the four uses (record / edit / pending / reject) land ----
def uses_of(c: Any, asset_ids: list[str]) -> dict[str, str]:
    """asset id -> its use. Until the four-uses field exists: pick -> record, reject -> reject, else pending."""
    out = dict.fromkeys(asset_ids, "pending")
    for part in st.chunks(asset_ids):
        for r in c.execute(f"SELECT asset_id, flag FROM marks WHERE asset_id IN ({st.marks_in(part)})", part):
            out[r[0]] = {"pick": "record", "reject": "reject"}.get(r[1], "pending")
    return out


# ponytail: today a pick (-> record) is the only positive human choice, so it is what a handoff takes by default;
# switch to ("edit",) when the four uses land (PRD §12: only 选入修图 goes to Lightroom by default).
HANDOFF_USES = ("record",)
# ---- end uses adapter ----


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


def create(c: Any, ids: list[str], *, name: str, scope: dict[str, Any], uses: tuple[str, ...] | None,
           actor: str, lightroom_dir: Path, lists_dir: Path | None) -> str:
    """Freeze `ids` (filtered to `uses`; None = all but reject), check each asset's files are online, and write
    `<lightroom_dir>/handoffs/<id>.json`. An offline asset is recorded as skipped and not sent."""
    use = uses_of(c, ids)
    ids = [a for a in ids if (use[a] in uses if uses else use[a] != "reject")]
    if not ids:
        raise st.Error("范围内没有要交接的照片（默认只交接已 Pick 的）")
    hid, t = st.new_id("h"), now()
    links = {r["asset_id"]: r for r in c.execute(
        f"SELECT * FROM editor_links WHERE editor = ? AND asset_id IN ({st.marks_in(ids)})", [EDITOR, *ids])}
    stars = {r[0]: r[1] for r in c.execute(
        f"SELECT asset_id, stars FROM marks WHERE asset_id IN ({st.marks_in(ids)})", ids)}
    photos, rows = [], []
    for i, aid in enumerate(ids):
        files = sorted(c.execute("SELECT path, role FROM asset_files WHERE asset_id = ?", (aid,)).fetchall(),
                       key=lambda f: (ROLE_ORDER.get(f["role"], 9), f["path"]))
        paths = [f["path"] for f in files if os.path.exists(f["path"])]
        link = links.get(aid)
        if not paths:
            rows.append((hid, aid, i, None, "skipped", "offline", None))
            continue
        base = loads(link["baseline"], {}) if link else {}
        p = {"asset_id": aid, "paths": paths, "uuid": link["uuid"] if link else None, "stars": stars.get(aid, 0),
             "use": use[aid], "keywords": _keywords(c, aid, lists_dir),
             "baseline": {"stars": base.get("stars"), "keywords": base.get("keywords")}}
        photos.append(p)
        rows.append((hid, aid, i, dumps(p), "pending", None, None))
    doc_path = Path(lightroom_dir) / "handoffs" / f"{hid}.json"
    c.execute("INSERT INTO handoffs (id, name, scope, doc_path, status, actor, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
              (hid, name, dumps(scope), str(doc_path), "written" if photos else "failed", actor, t))
    c.executemany("INSERT INTO handoff_items VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    if photos:                         # raises inside the transaction: no handoff is recorded without its file
        doc_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = doc_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"schema": DOC_SCHEMA, "kind": "gardener-handoff", "id": hid, "name": name,
                                   "collection_set": ROOT_SET, "created_at": t, "photos": photos},
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
    doc = json.loads(rp.read_text(encoding="utf-8"))
    t = now()
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
        c.execute("INSERT INTO editor_links (asset_id, editor, photo_id, uuid, path, baseline, handoff_id, updated_at) "
                  "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(asset_id, editor) DO UPDATE SET photo_id = excluded.photo_id, "
                  "uuid = excluded.uuid, path = excluded.path, baseline = excluded.baseline, "
                  "handoff_id = excluded.handoff_id, updated_at = excluded.updated_at",
                  (aid, EDITOR, None if r.get("photo_id") is None else str(r["photo_id"]), r.get("uuid"), r.get("path"),
                   dumps(base), hid, t))
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
        items.append({"asset_id": r["asset_id"], "name": Path(r["primary_path"]).name, "status": r["status"],
                      "reason": r["reason"], "conflicts": conflicts, "sent": loads(r["sent"]), "result": res or None})
    counts = {k: sum(i["status"] == k for i in items) for k in ("ok", "skipped", "failed", "pending")}
    counts["conflicts"] = sum(bool(i["conflicts"]) for i in items)
    return {"id": h["id"], "name": h["name"], "scope": loads(h["scope"]), "status": h["status"],
            "doc_path": h["doc_path"], "result_path": str(result_path(h["doc_path"])), "actor": h["actor"],
            "created_at": h["created_at"], "result_at": h["result_at"], "counts": counts, "items": items}
