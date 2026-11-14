"""Asset filters (contract §6.4) as one SQL condition over `assets a`; a Smart Collection's rule is
the same filter dict. Every condition is a subquery on `a`, so callers can wrap it in any SELECT."""
from __future__ import annotations

import json
from typing import Any

KEYS = ("task", "collection", "source", "media", "flag", "stars_min", "stars", "ai", "judged", "online",
        "analysis", "date_from", "date_to", "has_gps", "tag", "content", "species", "concept", "id_state", "q",
        "changed_after", "versions", "use", "pile")
CHOICES = {"media": ("photo", "video"), "flag": ("pick", "reject", "none"), "ai": ("pick", "reject", "none"),
           "judged": ("yes", "no"), "online": ("1", "0"), "has_gps": ("1", "0"),
           "analysis": ("pending", "done", "failed", "unavailable", "partial"), "versions": ("hide", "all", "only"),
           "id_state": ("confirmed", "candidate", "unresolved", "reconfirm"), "use": ("record", "edit", "pending", "reject"), "pile": ("good", "bin")}
MAX_DEPTH = 4    # smart rules may name other collections; this bounds the chain (and cycles)

LATEST_AI = "(SELECT s.verdict FROM current_suggestions s WHERE s.asset_id = a.id)"
JUDGED = "EXISTS (SELECT 1 FROM marks m WHERE m.asset_id = a.id AND (m.flag IS NOT NULL OR m.stars > 0))"
ID_STATES = {"confirmed": "ci.level = 'species' AND ci.source = 'user' AND ci.concept IS NOT NULL",
             "candidate": "ci.level = 'species' AND ci.source = 'ai'",
             "unresolved": "ci.level IN ('genus', 'family', 'unconfirmed')",
             "reconfirm": "ci.qualifier = 'slash'", "any": "1"}      # a person's claim a list split reopened
# 好片 / 不好 (ADR 0004 修订 21). 不好: a person's reject, or the AI's where no person has marked the photo.
# 好片: 选入修图 or a star, or the AI's pick unless a person rejected the photo: 完成 keeps what is left undecided
# (use record), and that is not a person saying the photo is ordinary.
TOUCHED = "EXISTS (SELECT 1 FROM marks m WHERE m.asset_id = a.id)"
GOOD_SHARE = 0.10    # owner 2026-11-13: about a tenth of a scan's photos are 好片
# the AI's good photos: its picks, then the best frames of the other bursts in rank order, up to GOOD_SHARE of the run
# (a run's size comes from one grouped subquery: counted again for every photo it made 好片 quadratic, 0.37 s at
# 3,700 photos and 10 s at ten times that)
AI_GOOD = ("EXISTS (SELECT 1 FROM current_suggestions s WHERE s.asset_id = a.id AND s.verdict != 'reject' AND "
           "(s.verdict = 'pick' OR (s.rank IS NOT NULL AND s.rank <= ? * (SELECT r.n FROM (SELECT run_id, count(*) n "
           "FROM current_suggestions GROUP BY run_id) r WHERE r.run_id = s.run_id))))")
PILES = {"bin": f"(EXISTS (SELECT 1 FROM marks m WHERE m.asset_id = a.id AND m.use = 'reject') OR "
                f"(NOT {TOUCHED} AND {LATEST_AI} = 'reject'))",
         "good": f"(EXISTS (SELECT 1 FROM marks m WHERE m.asset_id = a.id AND (m.use = 'edit' OR m.stars > 0)) OR "
                 f"({AI_GOOD} AND NOT EXISTS (SELECT 1 FROM marks m WHERE m.asset_id = a.id AND m.use = 'reject')))"}


def _cap(cond: str) -> str:
    return ("EXISTS (SELECT 1 FROM asset_caps c WHERE c.asset_id = a.id AND c.state NOT IN ('not_enabled', "
            f"'not_applicable') AND {cond})")


# pending, done and partial are the states `store.analysis_state` shows on a photo (the run-in-progress part aside).
# failed / unavailable ask a wider question on purpose: "has a capability that failed / could not run", which is
# what a person retries; a photo they find may read 部分完成 when its other capabilities are done.
_ANY, _PENDING, _NOT_DONE = _cap("1"), _cap("c.state = 'pending'"), _cap("c.state NOT IN ('done', 'unrecognized')")
ANALYSIS = {"pending": f"(NOT {_ANY} OR {_PENDING})", "done": f"({_ANY} AND NOT {_NOT_DONE})",
            "failed": _cap("c.state = 'failed'"), "unavailable": _cap("c.state = 'unavailable'"),
            "partial": (f"({_ANY} AND NOT {_PENDING} AND {_NOT_DONE} AND " + _cap("c.state != 'failed'") + " AND "
                        + _cap("c.state != 'unavailable'") + ")")}
BASENAME = "replace(a.primary_path, rtrim(a.primary_path, replace(a.primary_path, '/', '')), '')"
# within one task (param: task id) an asset stands for its capture unless its representative is in that task too;
# a version whose representative only another task holds (a wider folder) is that task's business
IN_TASK_REP = ("(a.version_of IS NULL OR NOT EXISTS (SELECT 1 FROM task_assets tv WHERE tv.asset_id = a.version_of "
               "AND tv.task_id = ?))")


def clean(filters: dict[str, Any]) -> dict[str, str]:
    """Known keys with non-empty values, as strings; ValueError on an unknown key or a bad choice."""
    unknown = sorted(set(filters) - set(KEYS))
    if unknown:
        raise ValueError(f"unknown filters: {unknown}")
    out = {k: str(v) if not isinstance(v, bool) else ("1" if v else "0") for k, v in filters.items()
           if v is not None and v != ""}
    for k, allowed in CHOICES.items():
        if k in out and out[k] not in allowed:
            raise ValueError(f"{k} must be one of {'|'.join(allowed)}")
    for k in ("stars_min", "stars"):
        if k in out and not (out[k].isdigit() and 0 <= int(out[k]) <= 5):
            raise ValueError(f"{k} must be 0..5")
    return out


def cycle(store: Any, rule: dict[str, Any], self_id: str | None) -> bool:
    """Would saving `rule` on collection `self_id` make smart rules refer back to a collection on the
    chain (or nest deeper than MAX_DEPTH)?"""
    seen = {self_id} if self_id else set()
    for _ in range(MAX_DEPTH + 1):
        cid = rule.get("collection") if isinstance(rule, dict) else None
        if cid is None:
            return False
        if cid in seen:
            return True
        seen.add(cid)
        col = store.one("SELECT kind, rule FROM collections WHERE id = ?", (cid,))
        if col is None or col["kind"] != "smart":
            return False
        rule = json.loads(col["rule"] or "{}")
    return True


def where(store: Any, filters: dict[str, Any], depth: int = 0,
          seen: frozenset[str] = frozenset()) -> tuple[str, list[Any]]:
    """(sql, params) selecting the assets that match every filter; `store.one` resolves a collection.
    A stored smart rule that is cyclic, too deep or invalid selects nothing instead of failing reads.
    Versions of a capture (assets.version_of) are hidden unless `versions` says otherwise; a manual
    collection shows exactly its members."""
    f = clean(filters)
    conds: list[str] = []
    params: list[Any] = []

    def add(sql: str, *ps: Any) -> None:
        conds.append(sql)
        params.extend(ps)

    versions = f.get("versions") or ("all" if "collection" in f else "hide")
    if versions != "all":
        own, ps = (IN_TASK_REP, [f["task"]]) if "task" in f else ("a.version_of IS NULL", [])
        add(own if versions == "hide" else f"NOT {own}", *ps)

    if "task" in f:
        add("EXISTS (SELECT 1 FROM task_assets ta WHERE ta.asset_id = a.id AND ta.task_id = ?)", f["task"])
    if "collection" in f:
        col = store.one("SELECT kind, rule FROM collections WHERE id = ?", (f["collection"],))
        if col is None or f["collection"] in seen or depth >= MAX_DEPTH:
            add("0")
        elif col["kind"] == "smart":       # PRD §04: (rule hits ∪ a person's pins) − a person's exclusions
            try:
                sub, ps = where(store, json.loads(col["rule"] or "{}"), depth + 1, seen | {f["collection"]})
            except ValueError:
                sub, ps = "0", []
            add(f"((({sub}) OR EXISTS (SELECT 1 FROM collection_members cm WHERE cm.asset_id = a.id AND "
                "cm.collection_id = ? AND cm.removed_at IS NULL)) AND NOT EXISTS (SELECT 1 FROM collection_members cx "
                "WHERE cx.asset_id = a.id AND cx.collection_id = ? AND cx.removed_at IS NOT NULL))",
                *ps, f["collection"], f["collection"])
        else:
            add("EXISTS (SELECT 1 FROM collection_members cm WHERE cm.asset_id = a.id AND cm.collection_id = ? "
                "AND cm.removed_at IS NULL)", f["collection"])
    if "source" in f:
        add("EXISTS (SELECT 1 FROM task_assets ta WHERE ta.asset_id = a.id AND ta.source_id = ?)", f["source"])
    if "media" in f:
        add("a.media = ?", f["media"])
    if "flag" in f:
        if f["flag"] == "none":
            add("NOT EXISTS (SELECT 1 FROM marks m WHERE m.asset_id = a.id AND m.flag IS NOT NULL)")
        else:
            add("EXISTS (SELECT 1 FROM marks m WHERE m.asset_id = a.id AND m.flag = ?)", f["flag"])
    if "use" in f:          # contract revision 5B; pending = no use set
        if f["use"] == "pending":
            add("NOT EXISTS (SELECT 1 FROM marks m WHERE m.asset_id = a.id AND m.use IS NOT NULL)")
        else:
            add("EXISTS (SELECT 1 FROM marks m WHERE m.asset_id = a.id AND m.use = ?)", f["use"])
    if "pile" in f:
        add(PILES[f["pile"]], *([GOOD_SHARE] if f["pile"] == "good" else []))
    if "stars_min" in f:
        add("coalesce((SELECT m.stars FROM marks m WHERE m.asset_id = a.id), 0) >= ?", int(f["stars_min"]))
    if "stars" in f:
        add("coalesce((SELECT m.stars FROM marks m WHERE m.asset_id = a.id), 0) = ?", int(f["stars"]))
    if "ai" in f:
        add(f"coalesce({LATEST_AI}, 'none') = ?", f["ai"])
    if "judged" in f:
        add(JUDGED if f["judged"] == "yes" else f"NOT {JUDGED}")
    if "online" in f:
        add("a.online = ?", int(f["online"]))
    if "analysis" in f:
        add(ANALYSIS[f["analysis"]])
    if "date_from" in f:
        add("a.taken_at >= ?", f["date_from"])
    if "date_to" in f:       # a date-only bound includes that whole day
        add("substr(a.taken_at, 1, length(?)) <= ?", f["date_to"], f["date_to"])
    if "has_gps" in f:
        add("a.lat IS NOT NULL AND a.lon IS NOT NULL" if f["has_gps"] == "1" else "(a.lat IS NULL OR a.lon IS NULL)")
    if "tag" in f:
        add("EXISTS (SELECT 1 FROM asset_tags at JOIN tags t ON t.id = at.tag_id WHERE at.asset_id = a.id "
            "AND (t.id = ? OR t.name = ?))", f["tag"], f["tag"])
    if "content" in f:
        add("EXISTS (SELECT 1 FROM asset_tags at JOIN tags t ON t.id = at.tag_id WHERE at.asset_id = a.id "
            "AND t.id LIKE 'scene:%' AND (t.id = 'scene:' || ? OR t.name = ?))", f["content"], f["content"])
    if "species" in f:      # any name of the concept a subject currently has (taxon_names), or its shown names
        from gardener.workbench.taxa import norm

        key = norm(f["species"])
        add("EXISTS (SELECT 1 FROM current_ids ci WHERE ci.asset_id = a.id AND ci.level = 'species' AND (ci.concept "
            "IN (SELECT concept FROM taxon_names WHERE instr(norm, ?) > 0) OR instr(lower(ci.taxon), ?) > 0 "
            "OR instr(lower(coalesce(ci.common, '')), ?) > 0))", key, key, key)
    if "concept" in f or "id_state" in f:             # one subject must satisfy both (contract revision 4, S4)
        sub, ps = [], []
        if "concept" in f:
            sub.append("ci.concept = ?")
            ps.append(f["concept"])
        sub.append(ID_STATES[f.get("id_state", "any")])
        add(f"EXISTS (SELECT 1 FROM current_ids ci WHERE ci.asset_id = a.id AND {' AND '.join(sub)})", *ps)
    if "q" in f:
        add(f"instr(lower({BASENAME}), lower(?)) > 0", f["q"])
    if "changed_after" in f:
        from gardener.workbench.store import canon_time

        add("a.updated_at > ?", canon_time(f["changed_after"]))
    return (" AND ".join(conds) or "1"), params
