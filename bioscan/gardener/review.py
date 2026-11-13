"""Review flow helpers (contract revision 5B): encounters (one continuous observation within a task; bursts
nest inside) and "keep this one, reject similar frames". Everything here takes an open transaction.

Encounters are suggested from time gaps and species continuity, then persisted: the machine only places
photos that have no encounter yet (a new photo joins the nearest placed one of its stretch), so a person's
split or merge is never undone by a rescan."""
from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from bioscan.gardener import identities
from bioscan.gardener import store as st
from bioscan.gardener.query import IN_TASK_REP
from bioscan.gardener.store import now

GAP_S = 600            # a pause this long ends an observation
SPECIES_GAP_S = 120    # a shorter one ends it when the species seen before and after share none
CANDIDATES = 3         # representative candidates shown for a collapsed encounter
SHORTLIST_SHARE, SHORTLIST_MIN = 0.10, 20     # AI 初选: the top 10% of a shoot, at least 20 photos (revision 9)


def shortlist(c: Any, task_id: str) -> dict[str, Any]:
    """AI 初选 (contract revision 9): the task's scored photos ranked within this task only, so a shoot that is weak
    overall still gets a shortlist. Order: not rejected, then a burst's best frame, then aesthetic score; the top
    max(10%, 20) are kept (all of them when there are fewer). Photos without a score are left out, not guessed."""
    rows = c.execute(
        "SELECT s.asset_id, s.verdict, s.score, s.burst_rank FROM current_suggestions s JOIN assets a ON a.id = s.asset_id "
        f"WHERE a.media = 'photo' AND s.score IS NOT NULL AND {IN_TASK_REP} "
        "AND EXISTS (SELECT 1 FROM task_assets ta WHERE ta.asset_id = a.id AND ta.task_id = ?)",
        (task_id, task_id)).fetchall()
    rows = sorted(rows, key=lambda r: (r["verdict"] == "reject", (r["burst_rank"] or 1) > 1, -r["score"], r["asset_id"]))
    size = min(len(rows), max(math.ceil(len(rows) * SHORTLIST_SHARE), SHORTLIST_MIN))
    return {"scored": len(rows), "size": size, "asset_ids": [r["asset_id"] for r in rows[:size]]}


def seconds(taken_at: str) -> float:
    """Camera-local capture time as seconds; an offset is dropped (times are never converted, §10)."""
    try:
        t = datetime.fromisoformat(taken_at)
    except ValueError:
        t = datetime.fromisoformat(taken_at[:19])
    return t.replace(tzinfo=None).timestamp()


def _task_bursts(c: Any, task_id: str) -> dict[str, str]:
    return {r[0]: r[1] for r in c.execute(
        "SELECT s.asset_id, s.burst_id FROM task_assets ta JOIN current_suggestions s ON s.asset_id = ta.asset_id "
        "WHERE ta.task_id = ? AND s.burst_id IS NOT NULL", (task_id,))}


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


RUN_GAP_S = 30         # groups this close in time, showing the same subject, are one subject run
RUN_SIZE = 3.0         # ... when the subject's size in the frame differs by less than this factor
RUN_SHARE = 0.6        # a group's subject: the species this share of its frames' main animals carry


def subject_runs(c: Any, task_id: str) -> dict[str, str]:
    """asset id -> subject run (修订 13): the task's groups (bursts and single photos) in time order, where
    adjacent groups join into one run when they show the same subject: the same main species (修订 17,
    identities.main_subjects) on RUN_SHARE of each group's frames, at most RUN_GAP_S apart, the main subject's size
    within a factor of RUN_SIZE. Computed from the current identifications (so a person's correction moves it), never
    stored: a run is a hint that groups belong together, the group stays the unit a person decides on."""
    rows = [dict(r) for r in c.execute(
        "SELECT a.id, a.taken_at, s.burst_id FROM task_assets ta JOIN assets a ON a.id = ta.asset_id LEFT JOIN "
        "current_suggestions s ON s.asset_id = a.id WHERE ta.task_id = ? "
        f"AND {IN_TASK_REP} AND a.taken_at IS NOT NULL ORDER BY a.taken_at, a.id", (task_id, task_id))]
    main = {a: (r["area"], r["concept"] if r["level"] == "species" else None)    # asset -> its main subject's
            for a, r in identities.main_subjects(c, [r["id"] for r in rows]).items()}    # (area, species concept)
    groups: list[list[dict[str, Any]]] = []
    at: dict[str, int] = {}
    for r in rows:                                  # a burst's frames are one group even with another photo between
        k = r["burst_id"] or f"one:{r['id']}"
        if k not in at:
            at[k] = len(groups)
            groups.append([])
        groups[at[k]].append(r)

    def subject(g: list[dict[str, Any]]) -> tuple[str | None, float]:
        seen = [main[m["id"]] for m in g if m["id"] in main]
        names: dict[str, int] = {}
        for _, concept in seen:
            if concept:
                names[concept] = names.get(concept, 0) + 1
        top = max(names, key=lambda k: (names[k], k), default=None)
        areas = sorted(a for a, _ in seen)
        return (top if top and names[top] >= RUN_SHARE * len(seen) else None), (areas[len(areas) // 2] if areas else 0.0)

    out: dict[str, str] = {}
    run, prev = "", None
    for g in groups:
        name, area = subject(g)
        start, end = seconds(g[0]["taken_at"]), seconds(g[-1]["taken_at"])
        same = (prev is not None and name is not None and name == prev[0] and start - prev[2] <= RUN_GAP_S
                and min(area, prev[1]) > 0 and max(area, prev[1]) <= RUN_SIZE * min(area, prev[1]))
        if not same:
            run = g[0]["id"]            # a run is named by its first photo: unique across tasks (修订 14)
        prev = (name, area, end)
        for m in g:
            out[m["id"]] = run
    return out


def _rank(m: dict[str, Any]) -> tuple:
    """Representative order: a person's choice first (edit, record, stars), then the cull's burst rank and score."""
    return (m["use"] == "edit", m["use"] == "record", m["stars"], -(m["burst_rank"] or 10 ** 6), m["score"] or 0.0)


def representative(frames: list[dict[str, Any]]) -> dict[str, Any]:
    """A group's representative, its best frame (修订 15: the one rule; GET /encounters, GET /assets and a merge
    all answer with it): by _rank, the first of equals in the order given (callers pass capture order: taken_at, id).
    Each frame needs use, stars, burst_rank and score."""
    return max(frames, key=_rank)


def encounters(c: Any, *, task_id: str | None = None, concept: str | None = None,
               candidates: int = CANDIDATES) -> dict[str, Any]:
    """The encounters of a task and/or of one species (only that species' photos), each with its bursts
    (a burst's representative is its best frame), up to `candidates` representative candidates and the
    species it shows; `unassigned` = photos in scope without one (no capture time, or not grouped yet)."""
    from bioscan.gardener.identities import GONE  # photos a cleanup moved to the Trash left the library (same as cards)

    conds, ps = ([IN_TASK_REP, "em.task_id = ?"], [task_id, task_id]) if task_id else (["a.version_of IS NULL"], [])
    conds.append(GONE.removeprefix("AND "))
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
            f"SELECT asset_id, burst_id, burst_rank, score FROM current_suggestions WHERE asset_id IN ({q})", part)}
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
    runs = {t: subject_runs(c, t) for t in {e["task_id"] for e in out.values()}}     # also a species' every task (修订 14)
    result = []
    for e in out.values():
        ms = e.pop("members")
        groups: dict[Any, list[dict[str, Any]]] = {}
        for m in ms:
            groups.setdefault(m["burst_id"] or ("single", m["id"]), []).append(m)
        bursts = []
        for k, g in groups.items():
            rep = representative(g)
            bursts.append({"id": None if isinstance(k, tuple) else k, "size": len(g), "rep": rep["id"],
                           "asset_ids": [m["id"] for m in g], "_rank": _rank(rep), **_tally([m["use"] for m in g]),
                           "run": runs.get(e["task_id"], {}).get(g[0]["id"])})
        cands = [b["rep"] for b in sorted(bursts, key=lambda b: b["_rank"], reverse=True)[:candidates]]
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
    un_conds.append(GONE.removeprefix("AND "))
    if concept:
        un_conds.append("EXISTS (SELECT 1 FROM current_ids ci WHERE ci.asset_id = a.id AND ci.concept = ?)")
        un_ps.append(concept)
    placed = "em.task_id = ?" if task_id else "1"
    unassigned = [r[0] for r in c.execute(
        f"SELECT a.id FROM assets a WHERE {' AND '.join(un_conds)} AND NOT EXISTS (SELECT 1 FROM encounter_members "
        f"em WHERE em.asset_id = a.id AND {placed}) ORDER BY a.taken_at, a.id", [*un_ps, *([task_id] if task_id else [])])]
    uses = {r[0]: r[1] or "pending" for part in st.chunks(unassigned)
            for r in c.execute(f"SELECT asset_id, use FROM marks WHERE asset_id IN ({st.marks_in(part)})", part)}
    return {"encounters": result, "unassigned": unassigned, "unassigned_use": {i: uses.get(i, "pending") for i in unassigned}}


KEPT, OUT = ("record", "edit"), ("reject",)     # a frame is decided when a person kept or rejected it (修订 16)


def _tally(uses: list[str | None]) -> dict[str, int]:
    return {"kept": sum(u in KEPT for u in uses), "rejected": sum(u in OUT for u in uses)}


def species_progress(c: Any, task_id: str) -> dict[str, dict[str, int]]:
    """concept -> {groups, decided, started} within a task (契约修订 16), in one query. The groups are the ones
    `encounters(task_id=, concept=)` lists: this species' frames of one burst within one encounter, or one frame
    (also each photo of the task without an encounter). A group is decided when every one of those frames is
    marked record / edit / reject, started when at least one is; a species is complete when all its groups are.
    Photos a cleanup moved to the Trash are left out, as in `encounters` and the species cards."""
    from bioscan.gardener.identities import GONE

    groups: dict[str, dict[Any, list[str | None]]] = {}
    for r in c.execute(
            "SELECT DISTINCT ci.concept, a.id, em.encounter_id, s.burst_id, m.use FROM task_assets ta "
            "JOIN assets a ON a.id = ta.asset_id JOIN current_ids ci ON ci.asset_id = a.id "
            "LEFT JOIN encounter_members em ON em.asset_id = a.id AND em.task_id = ta.task_id "
            "LEFT JOIN current_suggestions s ON s.asset_id = a.id LEFT JOIN marks m ON m.asset_id = a.id "
            f"WHERE ta.task_id = ? AND {IN_TASK_REP} AND ci.level = 'species' AND ci.concept IS NOT NULL {GONE}",
            (task_id, task_id)):
        key = (r[2], r[3] or r[1]) if r[2] else (None, r[1])
        groups.setdefault(r[0], {}).setdefault(key, []).append(r[4])
    out = {}
    for concept, gs in groups.items():
        ts = [(_tally(u), len(u)) for u in gs.values()]
        out[concept] = {"groups": len(ts), "decided": sum(t["kept"] + t["rejected"] == n for t, n in ts),
                        "started": sum(t["kept"] + t["rejected"] > 0 for t, _ in ts)}
    return out


def species_picks(c: Any, task_id: str, per_species: int) -> dict[str, Any]:
    """AI 选鸟 (契约修订 19): for each species of a task, the best frame (`representative`) of up to `per_species`
    different groups, the groups being the ones `species_progress` counts, over the photos whose **main species**
    it is (a small second bird on a frame does not make it a pick of that species). Best groups first, a group of an
    encounter not yet picked from before a second group of the same encounter, so the picks are different moments.
    A frame a person rejected is never picked. Read-only: the caller puts the ids in a collection."""
    from bioscan.gardener.identities import GONE, main_subjects

    groups: dict[str, dict[Any, list[dict[str, Any]]]] = {}
    rows = c.execute(
            "SELECT DISTINCT ci.concept, a.id, em.encounter_id, s.burst_id, m.use, coalesce(m.stars, 0) stars, "
            "s.burst_rank, s.score, a.taken_at FROM task_assets ta "
            "JOIN assets a ON a.id = ta.asset_id JOIN current_ids ci ON ci.asset_id = a.id "
            "LEFT JOIN encounter_members em ON em.asset_id = a.id AND em.task_id = ta.task_id "
            "LEFT JOIN current_suggestions s ON s.asset_id = a.id LEFT JOIN marks m ON m.asset_id = a.id "
            f"WHERE ta.task_id = ? AND {IN_TASK_REP} AND ci.level = 'species' AND ci.concept IS NOT NULL {GONE} "
            "AND coalesce(m.use, '') != 'reject' ORDER BY a.taken_at, a.id", (task_id, task_id)).fetchall()
    main = main_subjects(c, sorted({r["id"] for r in rows}))
    for r in rows:
        if main.get(r["id"], {}).get("concept") != r["concept"]:
            continue
        key = (r["encounter_id"], r["burst_id"] or r["id"]) if r["encounter_id"] else (None, r["id"])
        groups.setdefault(r["concept"], {}).setdefault(key, []).append(dict(r))
    picks: dict[str, None] = {}
    for concept in sorted(groups):
        reps = sorted(((key[0], representative(g)) for key, g in groups[concept].items()),
                      key=lambda x: _rank(x[1]), reverse=True)
        seen: set[Any] = set()
        first = [x for x in reps if x[0] is None or (x[0] not in seen and not seen.add(x[0]))]
        rest = [x for x in reps if not any(x is y for y in first)]
        picks |= {rep["id"]: None for _, rep in (first + rest)[:per_species]}
    return {"species": len(groups), "per_species": per_species, "asset_ids": list(picks)}


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
              expect: dict[str, int] | None = None, within: list[str] | None = None) -> dict[str, Any]:
    """保留此张并淘汰相似帧 (PRD §08): `asset_id` gets `keep_use`, the other frames of its burst get reject,
    one batch (one undo). Skipped: frames a person already protected (record, edit or stars: reason human)
    and frames that would leave a species with no photo that is not rejected (reason only_record; every
    frame showing it is kept). dry_run reports the reach, with the versions to send back as `expect`.
    `within` limits the frames that may be rejected to those ids (修订 13: the group on screen holds one species'
    frames of the burst, and a key press must not reject a frame the person is not looking at)."""
    if not c.execute("SELECT 1 FROM assets WHERE id = ?", (asset_id,)).fetchone():
        raise st.NotFound(f"no asset {asset_id}")
    row = c.execute("SELECT burst_id FROM current_suggestions WHERE asset_id = ?", (asset_id,)).fetchone()
    if row is None or row[0] is None:
        raise st.Error("这张照片不在连拍中")
    sibs = [r[0] for r in c.execute(
        "SELECT a.id FROM current_suggestions s JOIN assets a ON a.id = s.asset_id "
        f"WHERE s.burst_id = ? AND a.id != ? AND {IN_TASK_REP} ORDER BY a.taken_at, a.id",   # bursts are per task
        (row[0], asset_id, row[0].split(":", 1)[0]))]
    if within is not None:
        sibs = [i for i in sibs if i in set(within)]
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
            "(ci.asset_id = ? OR coalesce(m.use, '') != 'reject') "
            "AND ci.asset_id IN (SELECT asset_id FROM identifications WHERE concept = ?)",   # via identifications_concept
            (concept, asset_id, concept)).fetchall()
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


# ---- 契约修订 7A: undoable encounter split/merge, a person's burst split -----------------------------------------


def _enc_state(c: Any, eids: list[str]) -> dict[str, dict[str, Any]]:
    """encounter id -> {members: {asset_id: source}, merged_into}."""
    out = {e: {"members": {}, "merged_into": None} for e in eids}
    for part in st.chunks(eids):
        q = st.marks_in(part)
        for r in c.execute(f"SELECT id, merged_into FROM encounters WHERE id IN ({q})", part):
            out[r[0]]["merged_into"] = r[1]
        for r in c.execute(f"SELECT encounter_id, asset_id, source FROM encounter_members WHERE encounter_id IN ({q})",
                           part):
            out[r[0]]["members"][r[1]] = r[2]
    return out


def logged(c: Any, action: str, eids: list[str], edit: Any, *, actor: str) -> tuple[str, str]:
    """Run one encounter split or merge (`edit()` returns the encounter it made or kept) and record it as one
    undoable batch: every photo that moved (from, to, its earlier source) and the involved encounters right after.
    Returns (encounter id, batch id)."""
    before = _enc_state(c, eids)
    out = edit()
    after = _enc_state(c, sorted(set(eids) | {out}))
    where = {a: e for e, s in after.items() for a in s["members"]}
    moves = [[a, e, where[a], src] for e, s in before.items() for a, src in s["members"].items() if where.get(a, e) != e]
    batch = st.new_id("b")
    detail = {"moves": moves, "before": {e: s["merged_into"] for e, s in before.items()},
              "after": {e: {"members": sorted(s["members"]), "merged_into": s["merged_into"]} for e, s in after.items()}}
    c.execute("INSERT INTO encounter_history (batch_id, action, detail, actor, at) VALUES (?, ?, ?, ?, ?)",
              (batch, action, st.dumps(detail), actor, now()))
    return out, batch


def undo_encounters(c: Any, batch_id: str) -> dict[str, Any]:
    """Undo one encounter split or merge: its photos go back where they were, an encounter it made is folded back
    (merged_into its origin), one it merged away is live again. 409 when any involved encounter changed since."""
    h = c.execute("SELECT * FROM encounter_history WHERE batch_id = ? AND undone = 0", (batch_id,)).fetchone()
    if h is None:
        raise st.NotFound(f"no encounter batch {batch_id} to undo")
    d = st.loads(h["detail"])
    cur = _enc_state(c, list(d["after"]))
    changed = sorted(e for e, s in d["after"].items()
                     if sorted(cur[e]["members"]) != s["members"] or cur[e]["merged_into"] != s["merged_into"])
    if changed:
        raise st.Conflict("这些拍摄段在之后又被修改，不能撤销", encounters=changed)
    t, first = now(), next(iter(d["before"]))
    for a, frm, to, src in d["moves"]:
        c.execute("UPDATE encounter_members SET encounter_id = ?, source = ? WHERE encounter_id = ? AND asset_id = ?",
                  (frm, src, to, a))
        c.execute("UPDATE assets SET updated_at = ? WHERE id = ?", (t, a))         # pollers see it (§10 X7)
    for e in d["after"]:                       # an encounter the edit made has no "before": fold it into the first
        c.execute("UPDATE encounters SET merged_into = ?, updated_at = ? WHERE id = ?",
                  (d["before"].get(e, first), t, e))
    c.execute("UPDATE encounter_history SET undone = 1 WHERE batch_id = ?", (batch_id,))
    return {"undone": len(d["moves"]), "action": h["action"]}


USER_RUN = "user"   # ai_suggestions rows a person's burst split restamped (copies of the latest machine row)


def _best_first(c: Any, asset_ids: list[str]) -> dict[str, int]:
    """asset id -> place in one burst's order, best first, by the cull's burst order over the frames' stored
    quality (analyze.burst_order); {} when that cannot be had, so the caller keeps the earlier order."""
    from bioscan.gardener import analyze

    rows: dict[str, dict[str, Any]] = {a: {"asset_id": a, "products": {}} for a in asset_ids}
    for part in st.chunks(asset_ids):
        for r in c.execute(
                "SELECT asset_id, capability, data FROM results WHERE id IN (SELECT max(id) FROM results WHERE asset_id IN "
                f"({st.marks_in(part)}) AND capability LIKE 'raw:%' AND capability != 'raw:embed' AND status = 'done' "
                "GROUP BY asset_id, capability)", part):
            rows[r[0]]["products"][r[1].removeprefix("raw:")] = st.loads(r[2])
    try:
        return {a: i for i, a in enumerate(analyze.burst_order(list(rows.values())))}
    except Exception:  # noqa: BLE001 - an odd stored product must not block a person's merge
        return {}


def _renumber(rows: list[dict[str, Any]], c: Any = None, rerank: set[str] | None = None) -> None:
    """Rank and size each burst of `rows` (by their burst_id) anew, keeping the earlier rank order; the bursts in
    `rerank` (merged ones: two rank lists) are ordered by quality instead. A burst of one is no burst (id null),
    as the reducer reports it."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        groups.setdefault(r["burst_id"], []).append(r)
    for b, g in groups.items():
        pos = _best_first(c, [r["asset_id"] for r in g]) if c is not None and b in (rerank or ()) else {}
        g.sort(key=lambda r: (pos.get(r["asset_id"], 10 ** 6), r["burst_rank"] or 10 ** 6, r["asset_id"]))
        for i, r in enumerate(g):
            r.update(burst_id=r["burst_id"] if len(g) > 1 else None, burst_rank=i + 1 if len(g) > 1 else None,
                     burst_size=len(g) if len(g) > 1 else None)


def split_burst(c: Any, *, at: str | None = None, asset_ids: list[str] | None = None, actor: str) -> dict[str, Any]:
    """A person splits a wrong burst (PRD §07, AC07): `at` moves that frame and the later ones (capture time),
    `asset_ids` exactly those, to a new burst. Both parts keep frames. The moved frames are written to
    burst_overrides, which every later run applies over the reducer's bursts (apply_burst_overrides); the
    burst fields shown now are restamped as new ai_suggestions rows (run_id USER_RUN). Species identity is
    per subject and untouched."""
    ids = [at] if at is not None else list(dict.fromkeys(asset_ids or []))
    latest = {r["asset_id"]: dict(r) for part in st.chunks(ids) for r in c.execute(
        f"SELECT * FROM current_suggestions WHERE asset_id IN ({st.marks_in(part)})", part)}
    bursts = {(latest.get(i) or {}).get("burst_id") for i in ids}
    if not ids or len(bursts) != 1 or None in bursts:
        raise st.Error("要拆的照片须在同一个连拍里")
    b = bursts.pop()
    members = [dict(r) for r in c.execute(
        "SELECT s.*, a.taken_at FROM current_suggestions s JOIN assets a ON a.id = s.asset_id "
        "WHERE s.burst_id = ? ORDER BY a.taken_at, a.id", (b,))]
    order = [m["asset_id"] for m in members]
    moving = order[order.index(at):] if at is not None else [a for a in order if a in set(ids)]
    if not moving or len(moving) == len(order):
        raise st.Error("拆分后两组都要有照片")
    task_id, new, t = b.split(":", 1)[0], f"{b.split('~')[0]}~{st.new_id('p')}", now()
    for a in moving:
        c.execute("INSERT INTO burst_overrides (task_id, asset_id, burst_id, actor, created_at) VALUES (?, ?, ?, ?, ?) "
                  "ON CONFLICT(task_id, asset_id) DO UPDATE SET burst_id = excluded.burst_id, actor = excluded.actor, "
                  "created_at = excluded.created_at", (task_id, a, new, actor, t))
    for m in members:
        if m["asset_id"] in moving:
            m["burst_id"] = new
    _renumber(members)
    _restamp(c, members, t)
    return {"burst_id": new if len(moving) > 1 else None, "from": b, "moved": moving}


MERGED = "merged"   # <task>:merged~<part>: a burst a person merged (修订 13)


def _restamp(c: Any, members: list[dict[str, Any]], t: str) -> None:
    for m in members:
        c.execute("INSERT INTO ai_suggestions (asset_id, run_id, verdict, score, rank, reasons, burst_id, burst_rank, "
                  "burst_size, version, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                  (m["asset_id"], USER_RUN, m["verdict"], m["score"], m["rank"], m["reasons"], m["burst_id"],
                   m["burst_rank"], m["burst_size"], m["version"], t))
        c.execute("UPDATE assets SET updated_at = ? WHERE id = ?", (t, m["asset_id"]))   # pollers see it (§10 X7)


def merge_bursts(c: Any, task_id: str, asset_ids: list[str], *, actor: str) -> dict[str, Any]:
    """A person merges groups (修订 13): the given photos, and every frame of the bursts they are in, become one
    burst, best frame by quality. Written to burst_overrides for every member, so later runs keep it; the
    burst fields shown now are restamped (run_id USER_RUN). A single photo can be merged in. Species identity
    is per subject and untouched here (the caller runs identities.burst_consensus)."""
    ids = list(dict.fromkeys(asset_ids))
    inside = {r[0] for part in st.chunks(ids) for r in c.execute(
        f"SELECT asset_id FROM task_assets WHERE task_id = ? AND asset_id IN ({st.marks_in(part)})", [task_id, *part])}
    if len(ids) < 2 or set(ids) - inside:
        raise st.Error("合并需要同一任务里的两张以上照片")
    latest = {r["asset_id"]: dict(r) for part in st.chunks(ids) for r in c.execute(
        f"SELECT * FROM current_suggestions WHERE asset_id IN ({st.marks_in(part)})", part)}
    if set(ids) - set(latest):
        raise st.Error("这些照片还没有分组结果，分析完成后再合并")
    old = sorted({m["burst_id"] for m in latest.values() if m["burst_id"]})
    members = dict(latest)
    for b in old:
        members |= {r["asset_id"]: dict(r) for r in c.execute(
            "SELECT * FROM current_suggestions WHERE burst_id = ?", (b,))}
    if len(old) == 1 and len(members) == len([m for m in members.values() if m["burst_id"] == old[0]]):
        raise st.Error("这些照片已经在同一组里")
    new, t = f"{task_id}:{MERGED}~{st.new_id('p')}", now()
    for m in members.values():
        m["burst_id"] = new
        c.execute("INSERT INTO burst_overrides (task_id, asset_id, burst_id, actor, created_at) VALUES (?, ?, ?, ?, ?) "
                  "ON CONFLICT(task_id, asset_id) DO UPDATE SET burst_id = excluded.burst_id, actor = excluded.actor, "
                  "created_at = excluded.created_at", (task_id, m["asset_id"], new, actor, t))
    rows = list(members.values())
    _renumber(rows, c, {new})
    _restamp(c, rows, t)
    mine = {r[0]: (r[1], r[2]) for part in st.chunks(list(members)) for r in c.execute(
        f"SELECT asset_id, use, coalesce(stars, 0) FROM marks WHERE asset_id IN ({st.marks_in(part)})", part)}
    rep = representative([m | dict(zip(("use", "stars"), mine.get(m["asset_id"], (None, 0)))) for m in rows])
    return {"burst_id": new, "from": old, "size": len(rows), "rep": rep["asset_id"]}   # ranks are unique: no tie


def apply_burst_overrides(c: Any, task_id: str, run_id: str) -> int:
    """After a run saved its suggestions: frames a person split off or merged get their override burst, and the
    bursts that lost or gained frames are ranked and sized anew (a merged one by quality). A run that made no
    burst at all (no burst capability) moves none. Returns the frames moved.
    ponytail: a frame added later that the reducer groups with a split burst joins the machine part, never the
    person's; the person splits again (an override per part's members, not per frame, if that comes up)."""
    over = {r[0]: r[1] for r in c.execute("SELECT asset_id, burst_id FROM burst_overrides WHERE task_id = ?", (task_id,))}
    if not over:
        return 0
    rows = [dict(r) for r in c.execute("SELECT id, asset_id, burst_id, burst_rank FROM ai_suggestions WHERE run_id = ?",
                                       (run_id,))]
    if not any(r["burst_id"] for r in rows):
        return 0
    hit = {r["id"] for r in rows if r["asset_id"] in over}
    touched = {r["burst_id"] for r in rows if r["id"] in hit and r["burst_id"]}
    group = [r for r in rows if r["id"] in hit or (r["burst_id"] and r["burst_id"] in touched)]
    for r in group:
        if r["id"] in hit:
            r["burst_id"] = over[r["asset_id"]]
    _renumber(group, c, {b for b in over.values() if f":{MERGED}~" in b})
    for r in group:
        c.execute("UPDATE ai_suggestions SET burst_id = ?, burst_rank = ?, burst_size = ? WHERE id = ?",
                  (r["burst_id"], r["burst_rank"], r["burst_size"], r["id"]))
    return len(hit)
