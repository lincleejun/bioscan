"""Species identity per subject (ADR 0002, contract revision 4). A subject is one animal in a photo: a box
the detector found (origin ai) or one a person drew (origin user), with an id that outlives reruns. Each
identification row is one claim about one subject, from a run (source ai) or a person (source user); rows
are only appended. The current identification of a subject is its latest live user row, else its latest
ai row. Everything here takes an open transaction."""
from __future__ import annotations

from typing import Any

from bioscan.gardener import store as st
from bioscan.gardener.store import dumps, loads, now

MATCH_IOU = 0.5          # a new box is the same subject as an earlier one at this overlap or more


def iou(a: list[float], b: list[float]) -> float:
    w = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    h = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = w * h
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def claim(sp: dict[str, Any]) -> dict[str, Any]:
    """A BioScan species result (contract.Species) -> the identification fields. Only a species-level
    answer names a concept; genus/family keep the rolled-up taxon without one."""
    top = sp.get("top") or []
    level = sp.get("level") or "unconfirmed"
    first = top[0] if top else {}
    named = level == "species" and first
    return {"concept": (first.get("concept") or f"name:{first['scientific']}") if named else None,
            "taxon": sp.get("taxon"), "common": first.get("common") if named else None, "level": level,
            "p": first.get("posterior"),
            "candidates": [{"concept": c.get("concept") or f"name:{c['scientific']}", "scientific": c["scientific"],
                            "common": c.get("common"), "p": c.get("posterior")} for c in top]}


def ingest(c: Any, asset_id: str, run_id: str | None, version: str | None, product: dict[str, Any] | None) -> int:
    """One raw:identify product -> subjects matched or made, one ai identification per box that carries a
    species result (none when species was off). An ai-only subject this run no longer sees is retired;
    one a person identified stays. Returns the number of identifications written."""
    t, written = now(), 0
    live = [dict(r) for r in c.execute(
        "SELECT s.id, s.xyxy, EXISTS(SELECT 1 FROM identifications i WHERE i.subject_id = s.id AND i.source = 'user' "
        "AND i.undone = 0) AS human FROM subjects s WHERE s.asset_id = ? AND s.removed_at IS NULL", (asset_id,))]
    for s in live:
        s["xyxy"] = loads(s["xyxy"])
    seen: set[str] = set()
    for box in (product or {}).get("boxes") or []:
        xyxy = [float(v) for v in box["xyxy"]]
        best = max((s for s in live if s["id"] not in seen), key=lambda s: iou(s["xyxy"], xyxy), default=None)
        if best is not None and iou(best["xyxy"], xyxy) >= MATCH_IOU:
            sid = best["id"]
            c.execute("UPDATE subjects SET xyxy = ?, kind = ?, last_run_id = ? WHERE id = ?",
                      (dumps(xyxy), box.get("kind"), run_id, sid))
        else:
            sid = st.new_id("s")
            c.execute("INSERT INTO subjects (id, asset_id, xyxy, kind, origin, last_run_id, created_at) "
                      "VALUES (?, ?, ?, ?, 'ai', ?, ?)", (sid, asset_id, dumps(xyxy), box.get("kind"), run_id, t))
        seen.add(sid)
        if "species" not in box:                       # species off: a box, no claim about it
            continue
        f = claim(box["species"] or {})
        c.execute("INSERT INTO identifications (subject_id, asset_id, concept, taxon, common, level, candidates, p, "
                  "source, run_id, version, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'ai', ?, ?, ?)",
                  (sid, asset_id, f["concept"], f["taxon"], f["common"], f["level"], dumps(f["candidates"]), f["p"],
                   run_id, version, t))
        written += 1
    for s in live:
        if s["id"] not in seen and not s["human"]:
            c.execute("UPDATE subjects SET removed_at = ? WHERE id = ?", (t, s["id"]))
    c.execute("UPDATE assets SET updated_at = ? WHERE id = ?", (t, asset_id))
    return written


def backfill(c: Any) -> int:
    """Subjects and ai identifications from every stored raw:identify result, oldest first (schema 1 -> 2)."""
    n = 0
    for r in c.execute("SELECT asset_id, run_id, version, data FROM results WHERE capability = 'raw:identify' "
                       "AND status = 'done' ORDER BY id").fetchall():
        n += ingest(c, r[0], r[1], r[2], loads(r[3]))
    return n


def _row(r: Any) -> dict[str, Any]:
    return {"id": r["id"], "concept": r["concept"], "taxon": r["taxon"], "common": r["common"], "level": r["level"],
            "qualifier": r["qualifier"], "p": r["p"], "candidates": loads(r["candidates"]), "source": r["source"],
            "run_id": r["run_id"], "version": r["version"], "actor": r["actor"], "at": r["created_at"]}


def subjects(c: Any, asset_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    """asset id -> its live subjects, each with `current` (the identification that counts, or None), `ai`
    (the latest machine claim) and `human` (bool: current comes from a person)."""
    if not asset_ids:
        return {}
    marks = st.marks_in(asset_ids)
    rows = c.execute(f"SELECT * FROM identifications WHERE asset_id IN ({marks}) AND undone = 0 ORDER BY id",
                     asset_ids).fetchall()
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        latest[(r["subject_id"], r["source"])] = _row(r)
    out: dict[str, list[dict[str, Any]]] = {a: [] for a in asset_ids}
    for s in c.execute(f"SELECT * FROM subjects WHERE asset_id IN ({marks}) AND removed_at IS NULL "
                       f"ORDER BY rowid", asset_ids).fetchall():
        user, ai = latest.get((s["id"], "user")), latest.get((s["id"], "ai"))
        out[s["asset_id"]].append({"id": s["id"], "xyxy": loads(s["xyxy"]), "kind": s["kind"], "origin": s["origin"],
                                   "current": user or ai, "ai": ai, "human": user is not None})
    return out
