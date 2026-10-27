"""The name index (contract revision 4, S5): every name a taxon concept goes by -> the concept, in the table
`taxon_names`, so searches and Smart rules resolve names in SQL. Sources: the bird and mammal lists
(scientific and English names, MDD's other common names), synonyms.csv aliases (spellings and the names a
list upgrade replaced) and the person's own Chinese names in `<data dir>/names-zh.csv` (`scientific,zh`).
There is no licensed Chinese list to ship (research note §3), so none is bundled. The index is rebuilt when
any of those files change; a missing list is skipped (CI has none)."""
from __future__ import annotations

import csv
import hashlib
import logging
from pathlib import Path
from typing import Any

from bioscan import naming
from bioscan.gardener import store as st

log = logging.getLogger(__name__)
ZH_FILE = "names-zh.csv"
LISTS = (("avilist", "bird"), ("mdd", "mammal"))


def _inputs(data_dir: Path, lists_dir: Path) -> list[Path]:
    files = [p for sub, _ in LISTS for p in sorted((lists_dir / sub).glob("*.csv"))[:1]]
    return files + [p for p in (lists_dir / naming.NAMES_DIR / "synonyms.csv", data_dir / ZH_FILE) if p.is_file()]


def rows(data_dir: Path, lists_dir: Path = naming.DATA_DIR) -> tuple[list[tuple[str, str, str, str, str]], list[str]]:
    """((concept, name, lang, source, kind) ..., Chinese names that matched no concept)."""
    from bioscan.service import names as lists

    out: list[tuple[str, str, str, str, str]] = []
    by_sci: dict[str, tuple[str, str]] = {}
    for sub, kind in LISTS:
        files = sorted((lists_dir / sub).glob("*.csv"))
        if not files:
            log.warning("no %s list under %s: its names are not searchable", sub, lists_dir)
            continue
        read = lists.read_avilist if sub == "avilist" else lists.read_mdd
        for sci, common, _tax, concept in read(files[0]):
            concept = concept or f"name:{sci}"
            by_sci[naming.norm_binomial(sci)] = (concept, kind)
            out.append((concept, sci, "sci", sub, kind))
            if common:
                out.append((concept, common, "en", sub, kind))
        if sub == "mdd":
            with open(files[0], newline="", encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    hit = by_sci.get(naming.norm_binomial(r.get("sciName")))
                    for n in (r.get("otherCommonNames") or "").split("|"):
                        if hit and n.strip():
                            out.append((hit[0], n.strip(), "en", "mdd", "mammal"))
    for r in naming.read_synonyms(lists_dir / naming.NAMES_DIR / "synonyms.csv"):
        hit = by_sci.get(naming.norm_binomial(r["avilist_scientific"]))
        if hit:
            out.append((hit[0], r["alias"].strip(), "alias", "synonym", hit[1]))
            by_sci.setdefault(naming.norm_binomial(r["alias"]), hit)
    unmatched = []
    zh = data_dir / ZH_FILE
    if zh.is_file():
        with open(zh, newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                sci, name = (r.get("scientific") or "").strip(), (r.get("zh") or "").strip()
                hit = by_sci.get(naming.norm_binomial(sci))
                if hit and name:
                    out.append((hit[0], name, "zh", "user", hit[1]))
                elif name:
                    unmatched.append(sci or name)
    return list(dict.fromkeys(out)), unmatched


def sync(store: Any, lists_dir: Path = naming.DATA_DIR) -> bool:
    """Rebuild taxon_names when its inputs changed. True when it rebuilt."""
    h = hashlib.sha256()
    for p in _inputs(store.data_dir, lists_dir):
        h.update(str(p).encode() + b"\0" + p.read_bytes())
    sha = h.hexdigest()[:16]
    row = store.one("SELECT value FROM meta WHERE key = 'names_sha'")
    if row is not None and row["value"] == sha:
        apply_splits(store, lists_dir)
        return False
    new, unmatched = rows(store.data_dir, lists_dir)
    if unmatched:
        log.warning("%s: %d Chinese names match no listed species, e.g. %s", ZH_FILE, len(unmatched), unmatched[:5])
    with store.tx() as c:
        c.execute("DELETE FROM taxon_names")
        c.executemany("INSERT INTO taxon_names (concept, name, norm, lang, source, kind) VALUES (?, ?, ?, ?, ?, ?)",
                      [(k, n, norm(n), lang, src, kind) for k, n, lang, src, kind in new])
        c.execute("INSERT INTO meta VALUES ('names_sha', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (sha,))
    log.info("name index: %d names of %d concepts", len(new), len({r[0] for r in new}))
    apply_splits(store, lists_dir)
    return True


SPLITS_FILE = "avilist_splits.csv"      # scripts/build_splits.py, from AviList's changes tab


def apply_splits(store: Any, lists_dir: Path = naming.DATA_DIR) -> dict[str, str | None]:
    """Reopen a person's species claims on a concept a list version split (identities.reconfirm), once per
    version: a version applies when the name index holds all its daughters (that list is the one loaded).
    Applied versions and their undo batch live in meta `splits_applied` ({version: batch_id | null}).
    Returns the versions applied now."""
    import json

    from bioscan.gardener import identities

    path = lists_dir / naming.NAMES_DIR / SPLITS_FILE
    if not path.is_file():
        return {}
    versions: dict[str, dict[str, list[dict[str, str]]]] = {}
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            versions.setdefault(r["list_version"], {}).setdefault(r["parent_concept"], []).append(r)
    row = store.one("SELECT value FROM meta WHERE key = 'splits_applied'")
    applied: dict[str, str | None] = json.loads(row["value"]) if row else {}
    now_applied: dict[str, str | None] = {}
    for version, parents in versions.items():
        kids = sorted({d["concept"] for ds in parents.values() for d in ds})
        # ponytail: needs every daughter in the loaded list; a library that skips a list year whose next version
        # retires one of them never applies the earlier split. Apply versions in order, or on any daughter, then.
        if version in applied or store.one(f"SELECT count(DISTINCT concept) n FROM taxon_names WHERE concept IN "
                                           f"({st.marks_in(kids)})", kids)["n"] < len(kids):
            continue
        with store.tx() as c:
            out = identities.reconfirm(c, version, parents)
            applied[version] = now_applied[version] = out["batch_id"]
            c.execute("INSERT INTO meta VALUES ('splits_applied', ?) ON CONFLICT(key) DO UPDATE SET value = "
                      "excluded.value", (json.dumps(applied),))
        log.info("list split %s: %d identifications to confirm again (undo batch %s)", version, len(out["changed"]),
                 out["batch_id"])
    return now_applied


def norm(name: str) -> str:
    """The search key: naming.norm_label (case, '_', '-', spaces); Chinese passes through unchanged."""
    return naming.norm_label(name)


def search(c: Any, q: str, limit: int = 20) -> list[dict[str, Any]]:
    """Concepts whose names contain `q`: exact names first, then prefixes, then the rest; within each, species
    already in the library (photos > 0) first. Names photographed but not in any list (name:<scientific>
    concepts) are found through the identifications themselves."""
    key = norm(q)
    if not key:
        return []
    hits: dict[str, dict[str, Any]] = {}
    for r in c.execute("SELECT concept, name, norm, lang, kind FROM taxon_names WHERE instr(norm, ?) > 0 LIMIT 2000",
                       (key,)):
        rank = 0 if r["norm"] == key else 1 if r["norm"].startswith(key) else 2
        h = hits.setdefault(r["concept"], {"concept": r["concept"], "kind": r["kind"], "matched": r["name"], "rank": rank})
        if rank < h["rank"]:
            h.update(matched=r["name"], rank=rank)
    for r in c.execute("SELECT concept, taxon, common, kind FROM current_ids WHERE concept LIKE 'name:%' AND "
                       "(instr(lower(taxon), ?) > 0 OR instr(lower(coalesce(common, '')), ?) > 0) GROUP BY concept",
                       (key, key)):
        hits.setdefault(r["concept"], {"concept": r["concept"], "kind": r["kind"], "matched": r["taxon"], "rank": 2})
    if not hits:
        return []
    concepts = list(hits)
    marks = st.marks_in(concepts)
    names: dict[str, dict[str, str]] = {}
    for r in c.execute(f"SELECT concept, name, lang FROM taxon_names WHERE concept IN ({marks}) AND lang IN "
                       f"('sci', 'en', 'zh') ORDER BY rowid", concepts):
        names.setdefault(r["concept"], {}).setdefault(r["lang"], r["name"])
    photos = {r[0]: r[1] for r in c.execute(
        f"SELECT ci.concept, count(DISTINCT ci.asset_id) FROM current_ids ci JOIN assets a ON a.id = ci.asset_id "
        f"WHERE ci.concept IN ({marks}) AND ci.level = 'species' AND a.version_of IS NULL GROUP BY ci.concept", concepts)}
    seen_names = {r[0]: (r[1], r[2]) for r in c.execute(
        f"SELECT concept, taxon, common FROM current_ids WHERE concept IN ({marks}) GROUP BY concept", concepts)}
    out = []
    for k, h in hits.items():
        n = names.get(k, {})
        taxon, common = seen_names.get(k, (None, None))
        out.append({"concept": k, "scientific": n.get("sci") or taxon, "common": n.get("en") or common,
                    "zh": n.get("zh"), "kind": h["kind"], "matched": h["matched"], "photos": photos.get(k, 0),
                    "_rank": (h["rank"], -min(photos.get(k, 0), 1), n.get("sci") or taxon or k)})
    out.sort(key=lambda x: x.pop("_rank"))
    return out[:limit]


def display(c: Any, concepts: list[str]) -> dict[str, dict[str, str | None]]:
    """concept -> the names the current lists give it ({scientific, common, zh}); concepts no list knows are left
    out (callers keep the names stored with the identification). A rename shows the new name everywhere while
    the stored rows keep what was said at the time."""
    concepts = [k for k in dict.fromkeys(concepts) if k]
    out: dict[str, dict[str, str | None]] = {}
    for part in (concepts[i:i + 500] for i in range(0, len(concepts), 500)):
        for r in c.execute(f"SELECT concept, name, lang FROM taxon_names WHERE concept IN ({st.marks_in(part)}) AND "
                           f"lang IN ('sci', 'en', 'zh') ORDER BY rowid", part):
            key = {"sci": "scientific", "en": "common", "zh": "zh"}[r["lang"]]
            out.setdefault(r["concept"], {"scientific": None, "common": None, "zh": None})
            out[r["concept"]][key] = out[r["concept"]][key] or r["name"]
    return out
