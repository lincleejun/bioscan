"""Asset filters (contract §6.4) as one SQL condition over `assets a`; a Smart Collection's rule is
the same filter dict. Every condition is a subquery on `a`, so callers can wrap it in any SELECT."""
from __future__ import annotations

import json
from typing import Any

KEYS = ("task", "collection", "source", "media", "flag", "stars_min", "stars", "ai", "judged", "online",
        "analysis", "date_from", "date_to", "has_gps", "tag", "content", "species", "concept", "id_state", "q",
        "changed_after", "versions")
CHOICES = {"media": ("photo", "video"), "flag": ("pick", "reject", "none"), "ai": ("pick", "reject", "none"),
           "judged": ("yes", "no"), "online": ("1", "0"), "has_gps": ("1", "0"),
           "analysis": ("pending", "done", "failed", "unavailable"), "versions": ("hide", "all", "only"),
           "id_state": ("confirmed", "candidate", "unresolved")}
MAX_DEPTH = 4    # smart rules may name other collections; this bounds the chain (and cycles)

LATEST_AI = "(SELECT s.verdict FROM ai_suggestions s WHERE s.asset_id = a.id ORDER BY s.id DESC LIMIT 1)"
JUDGED = "EXISTS (SELECT 1 FROM marks m WHERE m.asset_id = a.id AND (m.flag IS NOT NULL OR m.stars > 0))"
ID_STATES = {"confirmed": "ci.level = 'species' AND ci.source = 'user'",
             "candidate": "ci.level = 'species' AND ci.source = 'ai'",
             "unresolved": "ci.level IN ('genus', 'family', 'unconfirmed')", "any": "1"}
BASENAME = "replace(a.primary_path, rtrim(a.primary_path, replace(a.primary_path, '/', '')), '')"


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
        add("a.version_of IS NULL" if versions == "hide" else "a.version_of IS NOT NULL")

    if "task" in f:
        add("EXISTS (SELECT 1 FROM task_assets ta WHERE ta.asset_id = a.id AND ta.task_id = ?)", f["task"])
    if "collection" in f:
        col = store.one("SELECT kind, rule FROM collections WHERE id = ?", (f["collection"],))
        if col is None or f["collection"] in seen or depth >= MAX_DEPTH:
            add("0")
        elif col["kind"] == "smart":
            try:
                sub, ps = where(store, json.loads(col["rule"] or "{}"), depth + 1, seen | {f["collection"]})
            except ValueError:
                sub, ps = "0", []
            add(f"({sub})", *ps)
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
        caps = "SELECT 1 FROM asset_caps c WHERE c.asset_id = a.id AND c.state"
        add({"pending": f"(EXISTS ({caps} = 'pending') OR NOT EXISTS ({caps} != 'not_enabled'))",
             "done": f"EXISTS ({caps} != 'not_enabled') AND NOT EXISTS ({caps} IN ('pending', 'failed', 'unavailable'))",
             "failed": f"EXISTS ({caps} = 'failed')",
             "unavailable": f"EXISTS ({caps} = 'unavailable')"}[f["analysis"]])
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
    if "species" in f:
        add("EXISTS (SELECT 1 FROM asset_tags at JOIN tags t ON t.id = at.tag_id WHERE at.asset_id = a.id "
            "AND t.id LIKE 'species:%' AND (instr(lower(t.id), lower(?)) > 0 OR instr(lower(t.name), lower(?)) > 0))",
            f["species"], f["species"])
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
        from bioscan.gardener.store import canon_time

        add("a.updated_at > ?", canon_time(f["changed_after"]))
    return (" AND ".join(conds) or "1"), params
