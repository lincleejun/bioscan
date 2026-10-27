"""Build data/names/split_ranges.csv: where each daughter of an AviList split occurs, as 1-degree cells, from
GBIF occurrence records. BirdNET geo 3.0 has no label for most split daughters, so they share their parent's
label (synonyms.csv) and the location prior could not tell them apart; the location prior multiplies a
daughter's (shared) value by whether the place is inside its cells (bioscan/service/adapters/geo.py).

Names queried per daughter: its own binomial when it differs from the parent's (a daughter that keeps the parent's
name would pull in the lumped records), its subspecies in the AviList CSV (trinomials, RANK subspecies), and the
same subspecies under the parent's binomial, as GBIF's pre-split backbone has them, plus the parent's subspecies of
the daughter's epithet.
Each name must match a GBIF backbone species or subspecies exactly (a match at a higher rank is dropped). Records:
with coordinates, no geospatial issue, occurrence status present, up to MAX_RECORDS per name.

    uv run --no-project python scripts/build_split_ranges.py --avilist data/avilist/AviList-v2026-*.csv --force

Stdlib only. GBIF's API is free and needs no key; the cells are derived counts (GBIF.org occurrence search, see
data/README.md). An existing file is never overwritten without --force.
"""
import argparse
import csv
import json
import math
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPLITS = ROOT / "data" / "names" / "avilist_splits.csv"
OUT = ROOT / "data" / "names" / "split_ranges.csv"
COLS = ["list_version", "concept", "scientific", "cells", "records", "names", "gbif_keys"]
API = "https://api.gbif.org/v1"
PAGE, MAX_RECORDS = 300, 10000
# A cell counts once it holds this many records when the daughter has MIN_RECORDS_FOR_2 or more in all: a lone
# record is often a mislabelled subspecies or a vagrant (Eastern Marsh Wren "in" Seattle).
MIN_RECORDS_FOR_2 = 200
SIBLING_SHARE = 0.5          # co-dominant: at least half the leading sibling's records in that cell


def get(path: str, **params) -> dict:
    url = f"{API}/{path}?{urllib.parse.urlencode(params)}"
    for attempt in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "bioscan"}), timeout=60) as r:
                return json.load(r)
        except Exception as exc:  # noqa: BLE001 - retried, then raised
            if attempt == 3:
                raise
            print(f"  retry {url}: {exc}", file=sys.stderr)
            time.sleep(2 ** attempt)
    raise AssertionError


def match(name: str) -> int | None:
    """The GBIF backbone key of `name` as a species or subspecies, None when GBIF only knows a higher rank."""
    m = get("species/match", name=name, strict="true", kingdom="Animalia")
    ok = m.get("matchType") == "EXACT" and m.get("rank") in ("SPECIES", "SUBSPECIES") \
        and (m.get("canonicalName") or "").lower() == name.lower()
    return m.get("usageKey") if ok else None


def cells(key: int) -> tuple[dict[tuple[int, int], int], int]:
    """1-degree cells (floor lat, floor lon) -> record count, of the taxon's present, georeferenced records."""
    out: dict[tuple[int, int], int] = {}
    n, offset = 0, 0
    while offset < MAX_RECORDS:
        page = get("occurrence/search", taxonKey=key, hasCoordinate="true", hasGeospatialIssue="false",
                   occurrenceStatus="PRESENT", limit=PAGE, offset=offset)
        for r in page.get("results", []):
            lat, lon = r.get("decimalLatitude"), r.get("decimalLongitude")
            if lat is not None and lon is not None:
                cell = (math.floor(lat), math.floor(lon))
                out[cell] = out.get(cell, 0) + 1
                n += 1
        offset += PAGE
        if page.get("endOfRecords", True):
            break
    return out, n


def fetch(d: dict, subs: list[str]) -> dict:
    """GBIF record counts per 1-degree cell for one daughter: {cells: {"lat,lon": n}, records, names, keys}."""
    own = [s for s in subs if s.startswith(d["scientific"] + " ")]
    # GBIF's backbone still has the pre-split taxonomy: a daughter's subspecies live there under the parent's
    # binomial (Bubo nacurutu deserti was Bubo virginianus deserti), and a daughter itself as the parent's
    # subspecies of its epithet (Tringa inornata was Tringa semipalmata inornata).
    old = [f"{d['parent_scientific']} {x.split(' ')[2]}" for x in own] + \
        [f"{d['parent_scientific']} {d['scientific'].split(' ')[1]}"]
    names = list(dict.fromkeys(([d["scientific"]] if d["scientific"] != d["parent_scientific"] else []) + own + old))
    found: dict[str, int] = {}
    total, used, keys = 0, [], []
    for name in names:
        key = match(name)
        if key is None:
            continue
        c, n = cells(key)
        if n:
            for (la, lo), k in c.items():
                found[f"{la},{lo}"] = found.get(f"{la},{lo}", 0) + k
            total += n
            used.append(name)
            keys.append(str(key))
    return {"cells": found, "records": total, "names": used, "keys": keys}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--avilist", required=True, type=Path, help="the AviList CSV the splits belong to (RANK column)")
    ap.add_argument("--cache", type=Path, help="JSON of the raw GBIF counts: read when present, else written")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    if OUT.exists() and not a.force:
        raise SystemExit(f"{OUT} exists; --force to rebuild")
    splits = list(csv.DictReader(open(SPLITS, newline="", encoding="utf-8")))
    if a.cache and a.cache.exists():
        raw = json.loads(a.cache.read_text())
    else:
        with open(a.avilist, newline="", encoding="utf-8") as f:
            subs = [r["SCIENTIFIC NAME"].strip() for r in csv.DictReader(f) if r.get("RANK") == "subspecies"]
        raw = {d["concept"]: fetch(d, subs) for d in splits}
        if a.cache:
            a.cache.write_text(json.dumps(raw))
    by_parent: dict[str, list[dict]] = {}
    for d in splits:
        by_parent.setdefault(d["parent_scientific"], []).append(d)
    rows = []
    for d in splits:
        r = raw[d["concept"]]
        least = 2 if r["records"] >= MIN_RECORDS_FOR_2 else 1
        sibs = [raw[x["concept"]]["cells"] for x in by_parent[d["parent_scientific"]] if x["concept"] != d["concept"]]
        # a cell is the daughter's when it has SIBLING_SHARE of the most records any sibling has there: subspecies
        # labels in GBIF lean to the nominate (Willet records filed as T. s. semipalmata in North Dakota)
        keep = sorted(c for c, k in r["cells"].items()
                      if k >= least and k >= SIBLING_SHARE * max([s.get(c, 0) for s in sibs] or [0]))
        print(f"{d['scientific']}: {len(keep)} cells from {r['records']} records ({', '.join(r['names']) or 'none'})")
        if keep:
            rows.append({"list_version": d["list_version"], "concept": d["concept"], "scientific": d["scientific"],
                         "cells": ";".join(keep), "records": r["records"], "names": "|".join(r["names"]),
                         "gbif_keys": "|".join(r["keys"])})
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, COLS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} daughters with a range -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
