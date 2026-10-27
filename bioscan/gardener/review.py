"""Review flow helpers (contract revision 5B): encounters (one continuous observation within a task; bursts
nest inside) and "keep this one, reject similar frames". Everything here takes an open transaction.

Encounters are suggested from time gaps and species continuity, then persisted: the machine only places
photos that have no encounter yet (a new photo joins the nearest placed one of its stretch), so a person's
split or merge is never undone by a rescan."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from bioscan.gardener import store as st
from bioscan.gardener.query import IN_TASK_REP
from bioscan.gardener.store import now

GAP_S = 600            # a pause this long ends an observation
SPECIES_GAP_S = 120    # a shorter one ends it when the species seen before and after share none
CANDIDATES = 3         # representative candidates shown for a collapsed encounter
LATEST = "SELECT max(id) FROM ai_suggestions GROUP BY asset_id"


def seconds(taken_at: str) -> float:
    """Camera-local capture time as seconds; an offset is dropped (times are never converted, §10)."""
    try:
        t = datetime.fromisoformat(taken_at)
    except ValueError:
        t = datetime.fromisoformat(taken_at[:19])
    return t.replace(tzinfo=None).timestamp()


def _task_bursts(c: Any, task_id: str) -> dict[str, str]:
    return {r[0]: r[1] for r in c.execute(
        f"SELECT s.asset_id, s.burst_id FROM ai_suggestions s JOIN task_assets ta ON ta.asset_id = s.asset_id "
        f"WHERE ta.task_id = ? AND s.burst_id IS NOT NULL AND s.id IN ({LATEST})", (task_id,))}


def _task_species(c: Any, task_id: str) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for r in c.execute("SELECT ci.asset_id, ci.concept FROM current_ids ci JOIN task_assets ta ON ta.asset_id = "
                       "ci.asset_id WHERE ta.task_id = ? AND ci.level = 'species' AND ci.concept IS NOT NULL",
                       (task_id,)):
        out.setdefault(r[0], set()).add(r[1])
    return out


def assign_encounters(c: Any, task_id: str) -> int:
    """Place the task's representatives that have a capture time and no encounter yet; returns how many.
    Stretches are cut at a GAP_S pause, or a SPECIES_GAP_S pause between photos whose species share none,
    never inside a burst. A stretch with placed photos hands each new photo the encounter of its nearest
    placed neighbour in time; a stretch with none becomes a new encounter."""
    rows = sorted(((seconds(r[1]), r[0]) for r in c.execute(
        "SELECT a.id, a.taken_at FROM task_assets ta JOIN assets a ON a.id = ta.asset_id WHERE ta.task_id = ? "
        f"AND {IN_TASK_REP} AND a.taken_at IS NOT NULL", (task_id, task_id))))
    placed = {r[0]: r[1] for r in c.execute("SELECT asset_id, encounter_id FROM encounter_members WHERE task_id = ?",
                                            (task_id,))}
    if all(a in placed for _, a in rows):
        return 0
    bursts, species = _task_bursts(c, task_id), _task_species(c, task_id)
    stretches: list[list[tuple[float, str]]] = []
    for i, (t, a) in enumerate(rows):
        if i:
            pt, pa = rows[i - 1]
            same_burst = a in bursts and bursts.get(pa) == bursts[a]
            other_species = bool(species.get(a) and species.get(pa) and not species[a] & species[pa])
            if not same_burst and (t - pt > GAP_S or (t - pt > SPECIES_GAP_S and other_species)):
                stretches.append([])
        if not stretches:
            stretches.append([])
        stretches[-1].append((t, a))
    ts, n = now(), 0
    for part in stretches:
        have = [(t, placed[a]) for t, a in part if a in placed]
        new = [(t, a) for t, a in part if a not in placed]
        if not new:
            continue
        eid = None
        if not have:
            eid = st.new_id("e")
            c.execute("INSERT INTO encounters (id, task_id, origin, created_at, updated_at) VALUES (?, ?, 'auto', ?, ?)",
                      (eid, task_id, ts, ts))
        for t, a in new:
            to = eid or min(have, key=lambda h: abs(h[0] - t))[1]
            c.execute("INSERT INTO encounter_members (task_id, asset_id, encounter_id, source) VALUES (?, ?, ?, 'auto')",
                      (task_id, a, to))
            c.execute("UPDATE assets SET updated_at = ? WHERE id = ?", (ts, a))     # pollers see it (§10 X7)
            n += 1
    return n


def _rank(m: dict[str, Any]) -> tuple:
    """Representative order: a person's choice first (edit, record, stars), then the cull's burst rank and score."""
    return (m["use"] == "edit", m["use"] == "record", m["stars"], -(m["burst_rank"] or 10 ** 6), m["score"] or 0.0)


def encounters(c: Any, *, task_id: str | None = None, concept: str | None = None) -> dict[str, Any]:
    """The encounters of a task and/or of one species (only that species' photos), each with its bursts
    (a burst's representative is its best frame), up to CANDIDATES representative candidates and the
    species it shows; `unassigned` = photos in scope without one (no capture time, or not grouped yet)."""
    conds, ps = ([IN_TASK_REP, "em.task_id = ?"], [task_id, task_id]) if task_id else (["a.version_of IS NULL"], [])
    if concept:
        conds.append("EXISTS (SELECT 1 FROM current_ids ci WHERE ci.asset_id = a.id AND ci.concept = ?)")
        ps.append(concept)
    rows = [dict(r) for r in c.execute(
        "SELECT em.encounter_id, em.task_id, e.origin, a.id, a.taken_at, m.use, coalesce(m.stars, 0) stars "
        "FROM encounter_members em JOIN encounters e ON e.id = em.encounter_id JOIN assets a ON a.id = em.asset_id "
        f"LEFT JOIN marks m ON m.asset_id = a.id WHERE {' AND '.join(conds)} ORDER BY a.taken_at, a.id", ps)]
    ids = sorted({r["id"] for r in rows})
    ai: dict[str, Any] = {}
    named: dict[str, dict[str, dict[str, Any]]] = {}
    for part in st.chunks(ids):
        q = st.marks_in(part)
        ai |= {r[0]: r for r in c.execute(
            f"SELECT asset_id, burst_id, burst_rank, score FROM ai_suggestions WHERE id IN (SELECT max(id) FROM "
            f"ai_suggestions WHERE asset_id IN ({q}) GROUP BY asset_id)", part)}
        for r in c.execute(f"SELECT asset_id, concept, taxon, common FROM current_ids WHERE asset_id IN ({q}) "
                           "AND level = 'species' AND concept IS NOT NULL", part):
            named.setdefault(r[0], {})[r[1]] = {"concept": r[1], "taxon": r[2], "common": r[3]}
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        s = ai.get(r["id"])
        r |= {"use": r["use"] or "pending", "burst_id": s and s[1], "burst_rank": s and s[2], "score": s and s[3]}
        e = out.setdefault(r["encounter_id"], {"id": r["encounter_id"], "task_id": r["task_id"], "origin": r["origin"],
                                               "members": [], "species": {}})
        e["members"].append(r)
        e["species"] |= named.get(r["id"], {})
    from bioscan.gardener import taxa

    shown = taxa.display(c, sorted({k for e in out.values() for k in e["species"]}))
    result = []
    for e in out.values():
        ms = e.pop("members")
        groups: dict[Any, list[dict[str, Any]]] = {}
        for m in ms:
            groups.setdefault(m["burst_id"] or ("single", m["id"]), []).append(m)
        bursts = []
        for k, g in groups.items():
            rep = max(g, key=_rank)
            bursts.append({"id": None if isinstance(k, tuple) else k, "size": len(g), "rep": rep["id"],
                           "asset_ids": [m["id"] for m in g], "_rank": _rank(rep)})
        cands = [b["rep"] for b in sorted(bursts, key=lambda b: b["_rank"], reverse=True)[:CANDIDATES]]
        for b in bursts:
            b.pop("_rank")
        sp = []
        for k, v in sorted(e.pop("species").items()):
            n = shown.get(k) or {}
            sp.append(v | {"taxon": n.get("scientific") or v["taxon"], "common": n.get("common") or v["common"],
                           "zh": n.get("zh")})
        result.append(e | {"start": ms[0]["taken_at"], "end": ms[-1]["taken_at"], "photos": len(ms),
                           "asset_ids": [m["id"] for m in ms], "bursts": bursts, "candidates": cands, "species": sp})
    result.sort(key=lambda e: (e["start"] or "", e["id"]))
    un_conds, un_ps = (([IN_TASK_REP, "a.id IN (SELECT asset_id FROM task_assets WHERE task_id = ?)"], [task_id, task_id])
                       if task_id else (["a.version_of IS NULL"], []))
    if concept:
        un_conds.append("EXISTS (SELECT 1 FROM current_ids ci WHERE ci.asset_id = a.id AND ci.concept = ?)")
        un_ps.append(concept)
    placed = "em.task_id = ?" if task_id else "1"
    unassigned = [r[0] for r in c.execute(
        f"SELECT a.id FROM assets a WHERE {' AND '.join(un_conds)} AND NOT EXISTS (SELECT 1 FROM encounter_members "
        f"em WHERE em.asset_id = a.id AND {placed}) ORDER BY a.taken_at, a.id", [*un_ps, *([task_id] if task_id else [])])]
    return {"encounters": result, "unassigned": unassigned}


def _live(c: Any, eid: str) -> dict[str, Any]:
    e = c.execute("SELECT * FROM encounters WHERE id = ?", (eid,)).fetchone()
    if e is None:
        raise st.NotFound(f"no encounter {eid}")
    if e["merged_into"]:
        raise st.Error(f"拍摄段 {eid} 已并入 {e['merged_into']}")
    return dict(e)


def split_encounter(c: Any, eid: str, *, at: str | None = None, asset_ids: list[str] | None = None,
                    actor: str) -> str:
    """A person's split: `at` moves that photo and every later one to a new encounter, `asset_ids` moves
    exactly those. Both parts must keep photos. Returns the new encounter's id."""
    e = _live(c, eid)
    members = sorted((seconds(r[1]) if r[1] else float("inf"), r[0]) for r in c.execute(
        "SELECT a.id, a.taken_at FROM encounter_members em JOIN assets a ON a.id = em.asset_id "
        "WHERE em.encounter_id = ?", (eid,)))
    ids = [a for _, a in members]
    if at is not None:
        if at not in ids:
            raise st.Error("at must be a photo of this encounter")
        moving = ids[ids.index(at):]
    else:
        moving = [a for a in ids if a in set(asset_ids or [])]
        if set(asset_ids or []) - set(ids):
            raise st.Error("asset_ids must be photos of this encounter")
    if not moving or len(moving) == len(ids):
        raise st.Error("拆分后两段都要有照片")
    new, t = st.new_id("e"), now()
    c.execute("INSERT INTO encounters (id, task_id, origin, actor, created_at, updated_at) VALUES (?, ?, 'user', ?, ?, ?)",
              (new, e["task_id"], actor, t, t))
    for part in st.chunks(moving):
        c.execute(f"UPDATE encounter_members SET encounter_id = ?, source = 'user' WHERE encounter_id = ? AND asset_id "
                  f"IN ({st.marks_in(part)})", [new, eid, *part])
        c.execute(f"UPDATE assets SET updated_at = ? WHERE id IN ({st.marks_in(part)})", [t, *part])
    c.execute("UPDATE encounters SET updated_at = ?, actor = ? WHERE id = ?", (t, actor, eid))
    return new


def merge_encounters(c: Any, ids: list[str], *, actor: str) -> str:
    """A person's merge: every photo of `ids` joins the first; the others record `merged_into`."""
    ids = list(dict.fromkeys(ids))
    es = [_live(c, i) for i in ids]
    if len(es) < 2 or len({e["task_id"] for e in es}) != 1:
        raise st.Error("merge needs two or more encounters of one task")
    t = now()
    for e in es[1:]:
        c.execute("UPDATE assets SET updated_at = ? WHERE id IN (SELECT asset_id FROM encounter_members WHERE "
                  "encounter_id = ?)", (t, e["id"]))
        c.execute("UPDATE encounter_members SET encounter_id = ?, source = 'user' WHERE encounter_id = ?",
                  (ids[0], e["id"]))
        c.execute("UPDATE encounters SET merged_into = ?, updated_at = ?, actor = ? WHERE id = ?", (ids[0], t, actor, e["id"]))
    c.execute("UPDATE encounters SET updated_at = ?, actor = ? WHERE id = ?", (t, actor, ids[0]))
    return ids[0]


def keep_best(c: Any, asset_id: str, *, keep_use: str, actor: str, dry_run: bool,
              expect: dict[str, int] | None = None) -> dict[str, Any]:
    """保留此张并淘汰相似帧 (PRD §08): `asset_id` gets `keep_use`, the other frames of its burst get reject,
    one batch (one undo). Skipped: frames a person already protected (record, edit or stars: reason human)
    and frames that would leave a species with no photo that is not rejected (reason only_record; every
    frame showing it is kept). dry_run reports the reach, with the versions to send back as `expect`."""
    if not c.execute("SELECT 1 FROM assets WHERE id = ?", (asset_id,)).fetchone():
        raise st.NotFound(f"no asset {asset_id}")
    row = c.execute(f"SELECT burst_id FROM ai_suggestions WHERE id IN ({LATEST}) AND asset_id = ?", (asset_id,)).fetchone()
    if row is None or row[0] is None:
        raise st.Error("这张照片不在连拍中")
    sibs = [r[0] for r in c.execute(
        f"SELECT a.id FROM ai_suggestions s JOIN assets a ON a.id = s.asset_id WHERE s.id IN ({LATEST}) "
        f"AND s.burst_id = ? AND a.id != ? AND {IN_TASK_REP} ORDER BY a.taken_at, a.id",   # bursts are per task
        (row[0], asset_id, row[0].split(":", 1)[0]))]
    marks = {i: st.mark_row(c, i) for i in [asset_id, *sibs]}
    skipped, cand = [], []
    for i in sibs:
        m = marks[i]
        if m["use"] == "reject":
            continue
        if m["use"] in ("record", "edit") or m["stars"]:
            skipped.append({"id": i, "reason": "human"})
        else:
            cand.append(i)
    shows: dict[str, set[str]] = {}
    for part in st.chunks(cand):
        for r in c.execute(f"SELECT asset_id, concept FROM current_ids WHERE asset_id IN ({st.marks_in(part)}) "
                           "AND level = 'species' AND concept IS NOT NULL", part):
            shows.setdefault(r[1], set()).add(r[0])
    protect: dict[str, list[str]] = {}
    for concept, holders in shows.items():
        others = c.execute(
            "SELECT ci.asset_id FROM current_ids ci JOIN assets a ON a.id = ci.asset_id LEFT JOIN marks m ON "
            "m.asset_id = a.id WHERE ci.concept = ? AND ci.level = 'species' AND a.version_of IS NULL AND "
            "(ci.asset_id = ? OR coalesce(m.use, '') != 'reject')", (concept, asset_id)).fetchall()
        if not {r[0] for r in others} - holders:
            for i in holders:
                protect.setdefault(i, []).append(concept)
    skipped += [{"id": i, "reason": "only_record", "concepts": sorted(protect[i])} for i in cand if i in protect]
    reject = [i for i in cand if i not in protect]
    out = {"dry_run": dry_run, "burst_id": row[0], "keep": {"id": asset_id, "use": keep_use}, "reject": reject,
           "skipped": skipped, "expect": {i: marks[i]["version"] for i in [asset_id, *reject]}, "batch_id": None}
    if dry_run:
        return out
    batch = st.new_id("b")
    st.apply_marks(c, [asset_id], {"use": keep_use}, actor=actor, reason="keep_best", expect=expect, batch=batch)
    if reject:
        st.apply_marks(c, reject, {"use": "reject"}, actor=actor, reason="keep_best", expect=expect, batch=batch)
    if c.execute("SELECT 1 FROM mark_history WHERE batch_id = ?", (batch,)).fetchone():
        out["batch_id"] = batch
    return out
