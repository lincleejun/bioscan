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


LEVELS = ("species", "genus", "family", "unconfirmed", "not_animal")


def _bursts(c: Any, asset_ids: list[str]) -> dict[str, str]:
    """asset id -> its latest burst id (task-scoped, ai_suggestions), for the assets that have one."""
    marks = st.marks_in(asset_ids)
    return {r[0]: r[1] for r in c.execute(
        f"SELECT asset_id, burst_id FROM ai_suggestions WHERE id IN (SELECT max(id) FROM ai_suggestions "
        f"WHERE asset_id IN ({marks}) GROUP BY asset_id) AND burst_id IS NOT NULL", asset_ids)}


def _resolve(target: dict[str, Any], claim: dict[str, Any]) -> dict[str, Any] | None:
    """The fields a person's claim writes for one subject; `accept` takes the subject's current machine claim."""
    if claim.get("accept"):
        ai = target["ai"]
        return None if ai is None else {k: ai[k] for k in ("concept", "taxon", "common", "level")}
    return {k: claim.get(k) for k in ("concept", "taxon", "common", "level")}


def identify(c: Any, subject_ids: list[str], new_subjects: list[dict[str, Any]], claim: dict[str, Any], *,
             scope: str, actor: str, reason: str | None, dry_run: bool) -> dict[str, Any]:
    """A person's identification of subjects (contract revision 4, S3). `new_subjects` = boxes a person drew
    ({asset_id, xyxy}). scope=burst also writes the same claim to the other photos of each target's burst:
    there, the subjects the model named as the target's model claim did (or the photo's only subject), never
    one a person already identified (those come back in `skipped`). dry_run reports and writes nothing."""
    t, batch = now(), st.new_id("b")
    targets: list[dict[str, Any]] = []
    for n in new_subjects:
        targets.append({"id": None, "asset_id": n["asset_id"], "xyxy": n["xyxy"], "ai": None, "current": None,
                        "human": False})
    if subject_ids:
        rows = {r["id"]: r["asset_id"] for r in c.execute(
            f"SELECT id, asset_id FROM subjects WHERE id IN ({st.marks_in(subject_ids)}) AND removed_at IS NULL",
            subject_ids)}
        missing = [s for s in subject_ids if s not in rows]
        if missing:
            raise st.NotFound(f"no subject {', '.join(missing)}")
        live = subjects(c, sorted(set(rows.values())))
        by_id = {s["id"]: s | {"asset_id": a} for a, ss in live.items() for s in ss}
        targets += [by_id[s] for s in subject_ids]
    changed, skipped, done = [], [], set()

    def write(s: dict[str, Any], fields: dict[str, Any], via: str) -> None:
        sid = s["id"]
        if sid is None and not dry_run:
            sid = st.new_id("s")
            c.execute("INSERT INTO subjects (id, asset_id, xyxy, kind, origin, created_at) VALUES (?, ?, ?, NULL, "
                      "'user', ?)", (sid, s["asset_id"], dumps(s["xyxy"]), t))
        changed.append({"subject_id": sid, "asset_id": s["asset_id"], "via": via,
                        "from": None if s["current"] is None else
                        {k: s["current"][k] for k in ("concept", "taxon", "common", "level", "source")},
                        "to": fields})
        if dry_run:
            return
        c.execute("INSERT INTO identifications (subject_id, asset_id, concept, taxon, common, level, source, batch_id, "
                  "actor, reason, created_at) VALUES (?, ?, ?, ?, ?, ?, 'user', ?, ?, ?, ?)",
                  (sid, s["asset_id"], fields["concept"], fields["taxon"], fields["common"], fields["level"], batch,
                   actor, reason, t))
        c.execute("UPDATE assets SET updated_at = ? WHERE id = ?", (t, s["asset_id"]))

    for s in targets:
        fields = _resolve(s, claim)
        if fields is None:
            skipped.append({"subject_id": s["id"], "asset_id": s["asset_id"], "reason": "no_machine_claim"})
            continue
        write(s, fields, "target")
        done.add(s["id"])
        if scope != "burst":
            continue
        burst = _bursts(c, [s["asset_id"]]).get(s["asset_id"])
        if burst is None:
            continue
        sibs = [r[0] for r in c.execute(
            "SELECT asset_id FROM ai_suggestions WHERE id IN (SELECT max(id) FROM ai_suggestions GROUP BY asset_id) "
            "AND burst_id = ? AND asset_id != ? ORDER BY asset_id", (burst, s["asset_id"]))]
        model = (s["ai"] or {}).get("concept")
        for a, ss in subjects(c, sibs).items():
            same = [x for x in ss if model is not None and (x["ai"] or {}).get("concept") == model]
            pick = same or (ss if len(ss) == 1 else [])
            if not pick:
                skipped.append({"subject_id": None, "asset_id": a, "reason": "no_matching_subject"})
            for x in pick:
                if x["id"] in done:
                    continue
                if x["human"]:
                    skipped.append({"subject_id": x["id"], "asset_id": a, "reason": "human"})
                    continue
                write(x | {"asset_id": a}, fields, "burst")
                done.add(x["id"])
    return {"batch_id": None if dry_run or not changed else batch, "dry_run": dry_run, "changed": changed,
            "skipped": skipped}


def undo(c: Any, batch_id: str) -> dict[str, Any]:
    """Undo one identification batch. 409 when a person identified one of its subjects again afterwards.
    A subject a person drew in that batch, left with no live claim, is retired."""
    rows = c.execute("SELECT id, subject_id, asset_id FROM identifications WHERE batch_id = ? AND undone = 0",
                     (batch_id,)).fetchall()
    if not rows:
        raise st.NotFound(f"no identification batch {batch_id} to undo")
    later = [r["subject_id"] for r in rows if c.execute(
        "SELECT 1 FROM identifications WHERE subject_id = ? AND source = 'user' AND undone = 0 AND id > ?",
        (r["subject_id"], r["id"])).fetchone()]
    if later:
        raise st.Conflict("这些主体在该批之后又被人工修改，不能撤销", subjects=sorted(set(later)))
    t = now()
    c.execute("UPDATE identifications SET undone = 1 WHERE batch_id = ?", (batch_id,))
    for r in rows:
        c.execute("UPDATE subjects SET removed_at = ? WHERE id = ? AND origin = 'user' AND NOT EXISTS (SELECT 1 FROM "
                  "identifications WHERE subject_id = ? AND undone = 0)", (t, r["subject_id"], r["subject_id"]))
        c.execute("UPDATE assets SET updated_at = ? WHERE id = ?", (t, r["asset_id"]))
    return {"undone": len(rows)}
