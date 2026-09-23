"""Scientific-name normalisation, the synonyms table and the committed data paths.

Standard library only: the CLI (eval, gt) and the service share these without the CLI importing
numpy or torch.
"""
from __future__ import annotations

import csv
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
NAMES_DIR = "names"                 # under data_dir: synonyms.csv, avilist_map.csv
SYNONYMS_CSV = DATA_DIR / NAMES_DIR / "synonyms.csv"
AVILIST_MAP_CSV = DATA_DIR / NAMES_DIR / "avilist_map.csv"


def norm(s: str | None) -> str:
    """Lower case, '-' -> ' ', "'" dropped, whitespace collapsed."""
    return " ".join((s or "").lower().replace("-", " ").replace("'", "").split())


def norm_binomial(name: str | None) -> str:
    """'Corvus_corax', ' corvus  Corax ' -> 'corvus corax': the one match key for scientific names."""
    return norm((name or "").replace("_", " "))


def read_synonyms(path: Path = SYNONYMS_CSV) -> list[dict[str, str]]:
    """synonyms.csv rows (avilist_scientific, alias, source, note); [] when the file is absent."""
    if not Path(path).is_file():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return [r for r in csv.DictReader(f) if r.get("avilist_scientific") and r.get("alias")]


def aliases(synonyms: list[dict[str, str]], sources: tuple[str, ...]) -> dict[str, list[str]]:
    """norm(list name) -> aliases usable on one side. tol/birdnet/inat apply only to that side;
    spelling applies everywhere."""
    out: dict[str, list[str]] = {}
    for r in synonyms:
        if r["source"].strip() in sources:
            out.setdefault(norm_binomial(r["avilist_scientific"]), []).append(r["alias"].strip())
    return out


def map_problems(amap: dict[str, dict[str, str]], synonyms: list[dict[str, str]]) -> list[str]:
    """Synonyms the committed avilist_map.csv does not reflect: edited synonyms.csv without
    re-running scripts/build_name_map.py, or an alias the target side does not know. [] = in sync.
    Only bird-side sources (tol, birdnet, spelling) are checked; inat applies to truth labels."""
    side = {"tol": ("tol_name", "tol_how"), "birdnet": ("birdnet_label", "birdnet_how")}
    by_key = {norm_binomial(k): v for k, v in amap.items()}
    out = []
    for r in synonyms:
        source, sci, alias = r["source"].strip(), r["avilist_scientific"].strip(), r["alias"].strip()
        if source not in ("tol", "birdnet", "spelling"):
            continue
        row = by_key.get(norm_binomial(sci))
        if row is None:
            if source != "spelling":          # spelling rows may name an MDD mammal
                out.append(f"{sci}: not in avilist_map.csv")
            continue
        sides = side.values() if source == "spelling" else [side[source]]
        hits = [norm_binomial(row[name].split("_", 1)[0]) == norm_binomial(alias) and row[how] == "synonym"
                for name, how in sides]
        if not any(hits):
            out.append(f"{sci} <- {alias} ({source}): not applied in avilist_map.csv; re-run scripts/build_name_map.py")
    return out
