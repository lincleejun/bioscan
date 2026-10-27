"""Build data/names/avilist_splits.csv: the species AviList split in one version, from the xlsx's
"Changes from previous version" tab, section "SPLITS (including descriptions of taxa that alter existing
taxa)". One row per daughter: list_version, parent_scientific, parent_concept, concept, scientific, common.
parent_concept is the parent's Avibase ID in the previous list (its CSV, column AvibaseID); one daughter may
keep it; in v2026 every split retires the parent's id instead (Willet was avibase-E7A14E91), which is why Gardener
asks again (taxa.py).

    uv run --no-project --with openpyxl python scripts/build_splits.py \\
        --xlsx AviList-v2026-20Oct2026-extended-version.xlsx --previous AviList-v2025-11Jun-extended.csv --force

Stdlib + openpyxl only (no project import), so it runs with --no-project. An existing file is never
overwritten without --force; a later list version appends its rows with --append.
"""
import argparse
import csv
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "data" / "names" / "avilist_splits.csv"
COLS = ["list_version", "parent_scientific", "parent_concept", "concept", "scientific", "common"]
SECTION = "SPLITS"
HEADER = ("#", "AVIBASE ID", "FAMILY", "SCIENTIFIC NAME: AVILIST v2026", "ENGLISH NAME: AVILIST v2026",
          "AVILIST v2025 SCIENTIFIC NAME", "AVILIST v2025 ENGLISH NAME")


def split_rows(xlsx: Path) -> list[tuple]:
    """The data rows of the SPLITS section: from its header row to the next section title."""
    import openpyxl

    ws = openpyxl.load_workbook(xlsx, read_only=True)["Changes from previous version"]
    rows = [tuple(r[:7]) for r in ws.iter_rows(values_only=True)]
    start = next(i for i, r in enumerate(rows) if isinstance(r[0], str) and r[0].startswith(SECTION))
    head = next(i for i in range(start, len(rows)) if rows[i][0] == "#")
    if rows[head] != HEADER:
        sys.exit(f"unexpected SPLITS header {rows[head]}")
    out = []
    for r in rows[head + 1:]:
        if isinstance(r[0], str) and r[0].strip():        # the next section's title
            break
        if r[1]:
            out.append(r)
    return out


def species_ids(previous: Path) -> dict[str, str]:
    """scientific name -> Avibase ID of the previous list's species rows."""
    with open(previous, newline="", encoding="utf-8") as f:
        return {r["Scientific_name"].strip(): r["AvibaseID"].strip() for r in csv.DictReader(f)
                if r["Taxon_rank"] == "species"}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--xlsx", type=Path, required=True)
    ap.add_argument("--previous", type=Path, required=True, help="the previous list version's CSV (AvibaseID column)")
    ap.add_argument("--version", default="avilist-2026", help="the list_id of the version that split")
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--append", action="store_true", help="keep the rows of other versions already in --out")
    a = ap.parse_args()
    if a.out.exists() and not (a.force or a.append):
        sys.exit(f"{a.out} exists; --force to overwrite, --append to add a version")
    ids = species_ids(a.previous)
    new = []
    for _n, avibase, _fam, sci, common, parent, _parent_common in split_rows(a.xlsx):
        if parent not in ids:
            sys.exit(f"parent {parent!r} of {sci} is not a species in {a.previous}")
        new.append({"list_version": a.version, "parent_scientific": parent, "parent_concept": f"avibase:{ids[parent]}",
                    "concept": f"avibase:{avibase}", "scientific": sci, "common": common})
    kept = []
    if a.append and a.out.exists():
        with open(a.out, newline="", encoding="utf-8") as f:
            kept = [r for r in csv.DictReader(f) if r["list_version"] != a.version]
    with open(a.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, COLS, lineterminator="\n")
        w.writeheader()
        w.writerows(kept + new)
    print(f"{a.out}: {len(new)} daughters of {len({r['parent_concept'] for r in new})} parents ({a.version})")


if __name__ == "__main__":
    main()
