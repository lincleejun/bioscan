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
FLOCK_MIN = 5            # this many boxes of one kind in a photo are a flock: not named one by one (Merlin's restraint)
FLOCK = "flock"          # the reason on a flock box's ai identification


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


def _rank(sp: dict[str, Any], level: str) -> str | None:
    """The genus or family of a box's top answer (its taxonomy, else the binomial's first word for genus)."""
    first = (sp.get("top") or [{}])[0]
    tax = first.get("taxonomy") or []
    if level == "genus":
        return tax[5] if len(tax) > 5 else (first.get("scientific") or "").split(" ")[0] or None
    return tax[4] if len(tax) > 4 else None


def flock(claims: list[dict[str, Any]], species: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A flock's claims, one level up: the genus all top answers share, else their shared family, else
    unconfirmed; concept and common go, candidates stay, reason `flock`. Several species named in one flock
    are mostly the classifier guessing per box, and each would become its own card."""
    for level in ("genus", "family"):
        names = {_rank(sp, level) for sp in species}
        if len(names) == 1 and None not in names:
            taxon = names.pop()
            break
    else:
        level, taxon = "unconfirmed", None
    return [f | {"concept": None, "common": None, "level": level, "taxon": taxon, "p": None, "reason": FLOCK}
            for f in claims]


def ingest(c: Any, asset_id: str, run_id: str | None, version: str | None, product: dict[str, Any] | None) -> int:
    """One raw:identify product -> subjects matched or made, one ai identification per box that carries a
    species result (none when species was off). An ai-only subject this run no longer sees is retired;
    one a person identified stays. FLOCK_MIN or more boxes of one kind are a flock, claimed at a coarser level
    (flock()), unless every box names the same species: then they keep it (owner, 2026-10-27; on 300 real photos
    11 such flocks were all correct). Returns the number of identifications written."""
    t, written = now(), 0
    boxes = (product or {}).get("boxes") or []
    claims: dict[int, dict[str, Any]] = {i: claim(b["species"] or {}) for i, b in enumerate(boxes) if "species" in b}
    for kind in {boxes[i].get("kind") for i in claims}:
        group = [i for i in claims if boxes[i].get("kind") == kind]
        agree = {(claims[i]["level"], claims[i]["concept"]) for i in group}
        if len(group) >= FLOCK_MIN and not (len(agree) == 1 and next(iter(agree))[0] == "species"):
            claims.update(zip(group, flock([claims[i] for i in group], [boxes[i]["species"] or {} for i in group])))
    live = [dict(r) for r in c.execute(
        "SELECT s.id, s.xyxy, EXISTS(SELECT 1 FROM identifications i WHERE i.subject_id = s.id AND i.source = 'user' "
        "AND i.undone = 0) AS human FROM subjects s WHERE s.asset_id = ? AND s.removed_at IS NULL", (asset_id,))]
    for s in live:
        s["xyxy"] = loads(s["xyxy"])
    seen: set[str] = set()
    for n, box in enumerate(boxes):
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
        if n not in claims:                            # species off: a box, no claim about it
            continue
        f = claims[n]
        c.execute("INSERT INTO identifications (subject_id, asset_id, concept, taxon, common, level, candidates, p, "
                  "source, run_id, version, reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'ai', ?, ?, ?, ?)",
                  (sid, asset_id, f["concept"], f["taxon"], f["common"], f["level"], dumps(f["candidates"]), f["p"],
                   run_id, version, f.get("reason"), t))
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
            "run_id": r["run_id"], "version": r["version"], "actor": r["actor"], "reason": r["reason"], "batch_id": r["batch_id"],
            "at": r["created_at"]}


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
    _rename([x for ss in out.values() for s in ss for x in (s["current"], s["ai"]) if x and x["concept"]
             and x["level"] == "species"], c)
    return out


def _rename(claims: list[dict[str, Any]], c: Any) -> None:
    """Show each species claim under its concept's current names (taxa.display), keeping the stored ones as
    `said` when they differ; adds `zh`."""
    from bioscan.gardener import taxa

    names = taxa.display(c, [x["concept"] for x in claims])
    for x in claims:
        n = names.get(x["concept"])
        x.setdefault("zh", None)
        if not n:
            continue
        if n["scientific"] and n["scientific"] != x["taxon"]:
            x["said"] = {"taxon": x["taxon"], "common": x["common"]}
        x["taxon"], x["common"], x["zh"] = n["scientific"] or x["taxon"], n["common"] or x["common"], n["zh"]


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
    ({asset_id, xyxy}). scope=burst (or encounter) also writes the same claim to the other photos of each target's
    burst (or encounter):
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
        if scope == "burst":
            burst = _bursts(c, [s["asset_id"]]).get(s["asset_id"])
            if burst is None:
                continue
            # ponytail: scans every asset's latest suggestion per target; a burst_id index on a "latest" table if a
            # library-wide batch of many targets gets slow.
            sibs = [r[0] for r in c.execute(
                "SELECT asset_id FROM ai_suggestions WHERE id IN (SELECT max(id) FROM ai_suggestions GROUP BY asset_id) "
                "AND burst_id = ? AND asset_id != ? ORDER BY asset_id", (burst, s["asset_id"]))]
        elif scope == "encounter":       # contract revision 5B: the other photos of the target's encounter(s)
            sibs = [r[0] for r in c.execute(
                "SELECT DISTINCT asset_id FROM encounter_members WHERE encounter_id IN (SELECT encounter_id FROM "
                "encounter_members WHERE asset_id = ?) AND asset_id != ? ORDER BY asset_id", (s["asset_id"], s["asset_id"]))]
        else:
            continue
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
                write(x | {"asset_id": a}, fields, scope)
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


KIND_OF_PREFIX = {"avibase": "bird", "mdd": "mammal"}
# A photo a confirmed cleanup plan moved to the Trash, every file of it, has left the library (PRD §03, §11: counts
# change only once a photo is actually removed); an offline disk never counts as removed (AC12).
GONE = ("AND NOT (json_extract(a.meta, '$.trashed') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM asset_files f "
        "WHERE f.asset_id = a.id AND f.online = 1))")


def species(c: Any, task_id: str | None = None, include_hidden: bool = False) -> dict[str, Any]:
    """The library's species cards (contract revision 4, S4), derived from current identifications of
    representatives (versions of a capture never count): one card per concept, no minimum size. A photo
    counts once per concept however many of its subjects show it. `confirmed` = photos a person confirmed;
    `candidates` = photos only the model names it on. `species` holds the cards with a confirmation (their
    number is "N species photographed", PRD §03), `to_confirm` the model-only ones; `unresolved` counts
    photos whose subjects stop at genus/family or are unconfirmed. `task_id` narrows it to one task."""
    from bioscan.gardener.query import IN_TASK_REP

    # The library counts each capture once (versions never count); a task counts its own representatives,
    # which include a version whose representative lies in another task (#102).
    rule, scope = (IN_TASK_REP, "AND a.id IN (SELECT asset_id FROM task_assets WHERE task_id = ?)") if task_id \
        else ("a.version_of IS NULL", "")
    rows = c.execute(f"SELECT ci.*, a.taken_at, coalesce(m.stars, 0) stars, (SELECT group_concat(task_id) FROM "
                     f"task_assets WHERE asset_id = a.id) tasks FROM current_ids ci JOIN assets a ON "
                     f"a.id = ci.asset_id LEFT JOIN marks m ON m.asset_id = a.id WHERE {rule} {scope} {GONE} "
                     f"ORDER BY ci.created_at", [task_id, task_id] if task_id else []).fetchall()
    prefs = {r["concept"]: r for r in c.execute("SELECT * FROM species_prefs")}
    cards: dict[str, dict[str, Any]] = {}
    unresolved: dict[str, set[str]] = {"genus_family": set(), "unconfirmed": set()}
    reconfirm: set[str] = set()
    for r in rows:
        if r["qualifier"] == "slash":
            reconfirm.add(r["asset_id"])
        if r["level"] in ("genus", "family"):
            unresolved["genus_family"].add(r["asset_id"])
        elif r["level"] == "unconfirmed":
            unresolved["unconfirmed"].add(r["asset_id"])
        if r["level"] != "species" or not r["concept"]:
            continue
        k = cards.setdefault(r["concept"], {"concept": r["concept"], "taxon": r["taxon"], "common": r["common"],
                                            "kinds": {}, "confirmed": set(), "seen": {}, "named_by": "ai"})
        if r["source"] == "user" or k["named_by"] == "ai":     # a person's naming wins, else the latest
            k["taxon"], k["common"] = r["taxon"], r["common"] or k["common"]
            k["named_by"] = r["source"]
        if r["kind"]:
            k["kinds"][r["kind"]] = k["kinds"].get(r["kind"], 0) + 1
        if r["source"] == "user":
            k["confirmed"].add(r["asset_id"])
        k["seen"][r["asset_id"]] = (r["taken_at"], r["stars"])
        k.setdefault("tasks", set()).update((r["tasks"] or "").split(",") if r["tasks"] else ())
    encs = _species_encounters(c, rule, scope, task_id)          # 契约修订 7A
    out: dict[str, list[dict[str, Any]]] = {"species": [], "to_confirm": []}
    for concept, k in cards.items():
        pref = prefs.get(concept)
        if pref is not None and pref["hidden"] and not include_hidden:
            continue
        times = sorted(t for t, _ in k["seen"].values() if t)
        pool = k["confirmed"] or set(k["seen"])
        best = max(pool, key=lambda a: (k["seen"][a][1], k["seen"][a][0] or "", a))
        pinned = pref is not None and pref["cover_asset_id"] in k["seen"]
        kind = max(k["kinds"], key=k["kinds"].get) if k["kinds"] else KIND_OF_PREFIX.get(concept.split(":")[0])
        card = {"concept": concept, "taxon": k["taxon"], "common": k["common"], "kind": kind,
                "confirmed": len(k["confirmed"]), "candidates": len(set(k["seen"]) - k["confirmed"]),
                "photos": len(k["seen"]), "only_one": len(k["seen"]) == 1,
                "first_taken_at": times[0] if times else None, "last_taken_at": times[-1] if times else None,
                "cover_id": pref["cover_asset_id"] if pinned else best, "cover_pinned": pinned,
                "hidden": bool(pref is not None and pref["hidden"]), "encounters": encs.get(concept, 0),
                "years": sorted({t[:4] for t in times}), "task_ids": sorted(k.get("tasks", ()))}
        out["species" if k["confirmed"] else "to_confirm"].append(card)
    from bioscan.gardener import taxa

    names = taxa.display(c, [x["concept"] for v in out.values() for x in v])
    for v in out.values():
        for x in v:
            n = names.get(x["concept"]) or {}
            x["taxon"], x["common"], x["zh"] = n.get("scientific") or x["taxon"], n.get("common") or x["common"], n.get("zh")
        v.sort(key=lambda x: (x["last_taken_at"] or "", x["concept"]), reverse=True)
    return out | {"confirmed_total": len(out["species"]), "to_reconfirm": len(reconfirm),
                  "unresolved": {k: len(v) for k, v in unresolved.items()}}


def _species_encounters(c: Any, rule: str, scope: str, task_id: str | None) -> dict[str, int]:
    """concept -> how many encounters hold a photo of it (契约修订 7A): the same photos the cards count, and with
    `task_id` only that task's encounters. Library-wide, a photo in two tasks counts once per task's encounter."""
    em = "AND em.task_id = ?" if task_id else ""
    return {r[0]: r[1] for r in c.execute(
        "SELECT ci.concept, count(DISTINCT em.encounter_id) FROM current_ids ci JOIN assets a ON a.id = ci.asset_id "
        f"JOIN encounter_members em ON em.asset_id = a.id WHERE {rule} {scope} {GONE} {em} AND ci.level = 'species' "
        "AND ci.concept IS NOT NULL GROUP BY ci.concept", [task_id] * 3 if task_id else [])}


def set_prefs(c: Any, concept: str, *, cover_asset_id: Any = ..., hidden: bool | None = None) -> None:
    """Pin (or clear with None) a species card's cover and hide or show the card; display only."""
    row = c.execute("SELECT cover_asset_id, hidden FROM species_prefs WHERE concept = ?", (concept,)).fetchone()
    cover = (row["cover_asset_id"] if row else None) if cover_asset_id is ... else cover_asset_id
    hide = (bool(row["hidden"]) if row else False) if hidden is None else hidden
    c.execute("INSERT INTO species_prefs (concept, cover_asset_id, hidden, updated_at) VALUES (?, ?, ?, ?) "
              "ON CONFLICT(concept) DO UPDATE SET cover_asset_id = excluded.cover_asset_id, hidden = excluded.hidden, "
              "updated_at = excluded.updated_at", (concept, cover, int(hide), now()))


# ---- list splits (contract revision 4, S7 part 2) -------------------------------------------------------------


def reconfirm(c: Any, version: str, parents: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """A list version split the species `parents` (parent concept -> its daughters [{concept, scientific,
    common}]): every subject whose current identification is a person's species claim on a parent gets a new
    user row (actor system, reason split:<version>, one batch, undoable) that names no concept, qualifier
    slash, the daughters as candidates. It then counts for no species until a person claims one again.
    No auto-resolution by place: the location prior lives in the BioScan engine, not here."""
    t, batch, changed = now(), st.new_id("b"), []
    marks = st.marks_in(list(parents))
    for r in c.execute(f"SELECT * FROM current_ids WHERE source = 'user' AND level = 'species' AND concept IN "
                       f"({marks}) ORDER BY identification_id", list(parents)).fetchall():
        kids = [{"concept": d["concept"], "scientific": d["scientific"], "common": d["common"], "p": None}
                for d in parents[r["concept"]]]
        c.execute("INSERT INTO identifications (subject_id, asset_id, concept, taxon, common, level, qualifier, "
                  "candidates, source, batch_id, actor, reason, created_at) VALUES (?, ?, NULL, ?, ?, 'species', "
                  "'slash', ?, 'user', ?, 'system', ?, ?)",
                  (r["subject_id"], r["asset_id"], r["taxon"], r["common"], dumps(kids), batch, f"split:{version}", t))
        c.execute("UPDATE assets SET updated_at = ? WHERE id = ?", (t, r["asset_id"]))
        changed.append({"subject_id": r["subject_id"], "asset_id": r["asset_id"], "from": r["concept"]})
    return {"batch_id": batch if changed else None, "changed": changed}


GEO_REASON = "split-geo:"       # + the list version of the split it settles (contract revision 7D)


def resolve_splits(c: Any, p_geo_of: Any) -> dict[str, Any]:
    """Settle split reconfirm rows (reconfirm) by place (contract revision 7D): for each subject still waiting on
    one, in a photo with GPS, `p_geo_of(concepts, lat, lon, taken_at)` gives the location prior of the daughters it
    labels directly ({concept: p_geo}, None without a prior). When exactly one daughter is plausible (p_geo >=
    RANGE_TAU, BioScan's in-range bar) and every other is clearly absent (p_geo < RANGE_EPS, its "not here" bar),
    a user row (actor system, reason split-geo:<version>, one batch for this call, undoable) names that daughter;
    otherwise the subject stays for the person. A subject is tried once per split: after an undo it is left alone."""
    from bioscan.service.rules import RANGE_EPS, RANGE_TAU

    t, batch, changed = now(), st.new_id("b"), []
    for r in c.execute(
            "SELECT ci.subject_id, ci.asset_id, i.candidates, i.taxon, i.reason, a.lat, a.lon, a.taken_at FROM current_ids "
            "ci JOIN identifications i ON i.id = ci.identification_id JOIN assets a ON a.id = ci.asset_id WHERE "
            "ci.qualifier = 'slash' AND i.reason LIKE 'split:%' AND a.lat IS NOT NULL AND a.lon IS NOT NULL AND NOT "
            "EXISTS (SELECT 1 FROM identifications g WHERE g.subject_id = ci.subject_id AND g.reason = ? || "
            "substr(i.reason, 7)) ORDER BY ci.identification_id", (GEO_REASON,)).fetchall():
        kids = loads(r["candidates"], [])
        p = p_geo_of([k["concept"] for k in kids], r["lat"], r["lon"], r["taken_at"])
        if not p or any(k["concept"] not in p for k in kids):
            continue                                   # no prior here, or a daughter it cannot speak for
        here = [k for k in kids if p[k["concept"]] >= RANGE_TAU]
        if len(here) != 1 or any(p[k["concept"]] >= RANGE_EPS for k in kids if k is not here[0]):
            continue
        k, version = here[0], r["reason"].removeprefix("split:")
        c.execute("INSERT INTO identifications (subject_id, asset_id, concept, taxon, common, level, candidates, source, "
                  "batch_id, actor, reason, created_at) VALUES (?, ?, ?, ?, ?, 'species', ?, 'user', ?, 'system', ?, ?)",
                  (r["subject_id"], r["asset_id"], k["concept"], k["scientific"], k["common"],
                   dumps([x | {"p": round(p[x["concept"]], 4)} for x in kids]), batch, GEO_REASON + version, t))
        c.execute("UPDATE assets SET updated_at = ? WHERE id = ?", (t, r["asset_id"]))
        changed.append({"subject_id": r["subject_id"], "asset_id": r["asset_id"], "to": k["concept"],
                        "p_geo": round(p[k["concept"]], 4)})
    return {"batch_id": batch if changed else None, "changed": changed}
