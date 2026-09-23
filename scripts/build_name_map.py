"""Build data/names/avilist_map.csv (AviList row -> TreeOfLife name, BirdNET label) and
data/names/candidates.csv (near-miss spellings for a human to review; never adopted here).

Match order per side: normalised exact -> data/names/synonyms.csv (tol/birdnet + spelling) -> none.

    uv run python scripts/build_name_map.py
"""
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bioscan.service import names  # noqa: E402

OUT = names.DATA_DIR / names.NAMES_DIR
MAP_COLS = ["scientific", "common", "order", "family", "tol_name", "tol_how", "birdnet_label", "birdnet_how"]
CAND_COLS = ["side", "scientific", "candidate", "distance", "candidate_in_avilist"]


def edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def near_misses(scis: list[str], targets: dict[str, str], max_d: int = 2) -> list[tuple[str, str, int]]:
    """(list name, target name, distance) for same genus and species epithet within `max_d` edits."""
    by_genus = defaultdict(list)
    for key, name in targets.items():
        g, _, e = key.partition(" ")
        by_genus[g].append((e, name))
    out = []
    for sci in scis:
        g, _, e = names.norm_binomial(sci).partition(" ")
        for te, name in by_genus.get(g, ()):
            if 0 < (d := edit_distance(e, te)) <= max_d:
                out.append((sci, name, d))
    return out


def build(rows, tol_names, birdnet_labels, synonyms):
    """rows from names.read_avilist -> (map rows, candidate rows)."""
    scis = [r[0] for r in rows]
    tol_t = names.tol_targets(tol_names, "Aves")
    bn_t = {names.norm_binomial(lab.split("_", 1)[0]): lab for lab in birdnet_labels}
    bn_sci = {k: lab.split("_", 1)[0] for k, lab in bn_t.items()}
    tol = names.match_names(scis, tol_t, names.aliases(synonyms, ("tol", "spelling")))
    bn_names = names.match_names(scis, bn_sci, names.aliases(synonyms, ("birdnet", "spelling")))
    bn = [(bn_t[names.norm_binomial(n)] if n else "", how) for n, how in bn_names]
    mapped = [dict(zip(MAP_COLS, (sci, common, tax[3], tax[4], t, th, b, bh)))
              for (sci, common, tax), (t, th), (b, bh) in zip(rows, tol, bn)]
    in_list = {names.norm_binomial(s) for s in scis}
    cands = []
    for side, targets, got in (("tol", tol_t, tol), ("birdnet", bn_sci, bn_names)):
        missing = [s for s, (_n, how) in zip(scis, got) if how == "none"]
        cands += [{"side": side, "scientific": s, "candidate": c, "distance": d,
                   "candidate_in_avilist": names.norm_binomial(c) in in_list}
                  for s, c, d in near_misses(missing, targets)]
    cands.sort(key=lambda c: (c["candidate_in_avilist"], c["distance"], c["side"], c["scientific"]))
    return mapped, cands


def write(path: Path, cols, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, cols, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def main():
    from huggingface_hub import hf_hub_download

    from bioscan.service.adapters import geo

    rows = names.read_avilist(names._list_file(names.DATA_DIR, "avilist"))
    with open(hf_hub_download(names.TOL_REPO, names.TOL_FILES[0], repo_type="dataset", revision=names.TOL_REVISION),
              encoding="utf-8") as f:
        tol_names = json.load(f)
    prior = geo.GeoPrior.load()                 # the same BirdNET model (geo.GEO_MODEL) the service loads
    if prior is None:
        raise SystemExit("BirdNET geo model unavailable")
    labels = prior.labels
    mapped, cands = build(rows, tol_names, labels, names.read_synonyms(OUT / "synonyms.csv"))
    write(OUT / "avilist_map.csv", MAP_COLS, mapped)
    write(OUT / "candidates.csv", CAND_COLS, cands)
    for side in ("tol", "birdnet"):
        counts = {h: sum(m[f"{side}_how"] == h for m in mapped) for h in ("exact", "synonym", "none")}
        print(f"{side}: {counts} of {len(mapped)}")
    print(f"{len(cands)} candidates -> {OUT / 'candidates.csv'}")


if __name__ == "__main__":
    main()
