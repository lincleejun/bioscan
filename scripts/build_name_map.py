"""Build the committed label maps under data/names/:

- birds (default): avilist_map.csv (AviList row -> TreeOfLife name, BirdNET label) and
  candidates.csv (near-miss spellings for a human to review; never adopted here).
  Match order per side: normalised exact -> data/names/synonyms.csv (tol/birdnet + spelling) -> none.
- mammals (`--list mammal`): mdd_map.csv (MDD row -> BirdNET label(s); TreeOfLife stays matched at
  build time in names.py). Only BirdNET labels of class Mammalia count. Match order: normalised
  exact -> the MDD synonym table (Species_Syn_Current_*.csv from the MDD zip) -> none, where a
  BirdNET name the synonym table gives two MDD species for must be settled in REVIEWED. MDD lumps
  species BirdNET keeps apart, so one row may carry several labels joined by geo.LABEL_SEP; the
  location prior takes their max.

    uv run python scripts/build_name_map.py
    uv run python scripts/build_name_map.py --list mammal --mdd-synonyms MDD/Species_Syn_Current_v2.5.csv

Labels default to the BirdNET geo model the service loads (geo.GEO_MODEL); `--labels FILE` (one
"Scientific_Common" label per line, the model's order) builds without it. Mammal labels need the
class of each label: `--birdnet-taxonomy` (default: the taxonomy CSV the birdnet package downloaded).
"""
import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bioscan import naming  # noqa: E402
from bioscan.service import names  # noqa: E402
from bioscan.service.adapters import geo  # noqa: E402

OUT = naming.DATA_DIR / naming.NAMES_DIR
MAP_COLS = ["scientific", "common", "order", "family", "tol_name", "tol_how", "birdnet_label", "birdnet_how"]
MDD_MAP_COLS = ["scientific", "common", "order", "family", "birdnet_label", "birdnet_how"]
CAND_COLS = ["side", "scientific", "candidate", "distance", "candidate_in_avilist"]
BIRDNET_TAXONOMY = Path("~/.local/share/birdnet/taxonomy-v3/98b27fc4a77c/taxonomy_v0.2-Jun2026.csv")

# BirdNET mammal names the MDD v2.5 synonym table assigns to more than one MDD species, settled by
# hand (2026-09-24): the MDD species whose name is the same species, not a lump partner.
REVIEWED = {
    "Rattus lutreolus": "Rattus lutreola",           # MDD spells the epithet lutreola (gender agreement)
    "Pipistrellus abramus": "Alionoctula abramus",   # same epithet; A. paterculus is a separate species
}


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
        g, _, e = naming.norm_binomial(sci).partition(" ")
        for te, name in by_genus.get(g, ()):
            if 0 < (d := edit_distance(e, te)) <= max_d:
                out.append((sci, name, d))
    return out


def build(rows, tol_names, birdnet_labels, synonyms):
    """rows from names.read_avilist -> (map rows, candidate rows)."""
    scis = [r[0] for r in rows]
    tol_t = names.tol_targets(tol_names, "Aves")
    bn_t = {naming.norm_binomial(lab.split("_", 1)[0]): lab for lab in birdnet_labels}
    bn_sci = {k: lab.split("_", 1)[0] for k, lab in bn_t.items()}
    tol = names.match_names(scis, tol_t, naming.aliases(synonyms, ("tol", "spelling")))
    bn_names = names.match_names(scis, bn_sci, naming.aliases(synonyms, ("birdnet", "spelling")))
    bn = [(bn_t[naming.norm_binomial(n)] if n else "", how) for n, how in bn_names]
    mapped = [dict(zip(MAP_COLS, (sci, common, tax[3], tax[4], t, th, b, bh)))
              for (sci, common, tax), (t, th), (b, bh) in zip(rows, tol, bn)]
    in_list = {naming.norm_binomial(s) for s in scis}
    cands = []
    for side, targets, got in (("tol", tol_t, tol), ("birdnet", bn_sci, bn_names)):
        missing = [s for s, (_n, how) in zip(scis, got) if how == "none"]
        cands += [{"side": side, "scientific": s, "candidate": c, "distance": d,
                   "candidate_in_avilist": naming.norm_binomial(c) in in_list}
                  for s, c, d in near_misses(missing, targets)]
    cands.sort(key=lambda c: (c["candidate_in_avilist"], c["distance"], c["side"], c["scientific"]))
    return mapped, cands


def mdd_synonym_index(syn_rows) -> dict[str, set[str]]:
    """norm(name) -> MDD species it is a synonym of, from the MDD synonym table: each synonym's
    original combination (as published and normalised) and its epithet in the accepted genus."""
    out: dict[str, set[str]] = defaultdict(set)
    for s in syn_rows:
        accepted = (s.get("MDD_species") or "").replace("_", " ").strip()
        if not accepted or accepted == "NA":
            continue
        for col in ("MDD_original_combination", "MDD_normalized_original_combination"):
            parts = (s.get(col) or "").split()
            if len(parts) >= 2 and s[col] != "NA":
                out[naming.norm_binomial(" ".join(parts[:2]))].add(accepted)
        root = (s.get("MDD_root_name") or "").strip()
        if root and root != "NA":
            out[naming.norm_binomial(f"{accepted.split(' ')[0]} {root}")].add(accepted)
    return out


def build_mdd(rows, mammal_labels, synonym_index, reviewed=REVIEWED):
    """rows from names.read_mdd, BirdNET labels of class Mammalia -> (map rows, report lines).
    A label whose name is an MDD species goes to that row (exact); else to the MDD species the
    synonym table names (synonym). Raises on an unreviewed ambiguous synonym."""
    by_key = {naming.norm_binomial(r[0]): i for i, r in enumerate(rows)}
    got: dict[int, list[tuple[str, str]]] = defaultdict(list)
    report = []
    for lab in mammal_labels:
        sci = lab.split("_", 1)[0]
        key = naming.norm_binomial(sci)
        if key in by_key:
            got[by_key[key]].append((lab, "exact"))
            continue
        hits = {naming.norm_binomial(a) for a in synonym_index.get(key, ())} & set(by_key)
        if sci in reviewed:
            hits = {naming.norm_binomial(reviewed[sci])}
        if len(hits) > 1:
            raise SystemExit(f"{sci}: MDD synonym of {sorted(hits)}; settle it in REVIEWED")
        if not hits:
            report.append(f"no MDD row: {lab}")
            continue
        row = by_key[hits.pop()]
        got[row].append((lab, "synonym"))
        report.append(f"synonym: {lab} -> {rows[row][0]}")
    mapped = []
    for i, (sci, common, tax) in enumerate(rows):
        labs = sorted(got.get(i, []), key=lambda lh: (lh[1] != "exact", lh[0]))   # own name first
        if len(labs) > 1:
            report.append(f"lump: {sci} <- {', '.join(lab for lab, _h in labs)}")
        how = labs[0][1] if labs else "none"
        mapped.append(dict(zip(MDD_MAP_COLS, (sci, common, tax[3], tax[4],
                                              geo.LABEL_SEP.join(lab for lab, _h in labs), how))))
    return mapped, report


def mammal_labels_of(labels: list[str], taxonomy_csv: Path) -> list[str]:
    """The labels whose scientific name the BirdNET taxonomy files under class mammalia."""
    with open(taxonomy_csv, newline="", encoding="utf-8") as f:
        mammals = {r["sci_name"].strip() for r in csv.DictReader(f) if r.get("class_name") == "mammalia"}
    return [lab for lab in labels if lab.split("_", 1)[0] in mammals]


def write(path: Path, cols, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, cols, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def birdnet_labels(path: str | None) -> list[str]:
    if path:
        return [x for x in Path(path).read_text(encoding="utf-8").splitlines() if x]
    prior = geo.GeoPrior.load()                 # the same BirdNET model (geo.GEO_MODEL) the service loads
    if prior is None:
        raise SystemExit("BirdNET geo model unavailable (or pass --labels FILE)")
    return prior.labels


def main_birds(a):
    from huggingface_hub import hf_hub_download

    rows = names.read_avilist(names._list_file(naming.DATA_DIR, "avilist"))
    with open(hf_hub_download(names.TOL_REPO, names.TOL_FILES[0], repo_type="dataset", revision=names.TOL_REVISION),
              encoding="utf-8") as f:
        tol_names = json.load(f)
    mapped, cands = build(rows, tol_names, birdnet_labels(a.labels), naming.read_synonyms(OUT / "synonyms.csv"))
    write(OUT / "avilist_map.csv", MAP_COLS, mapped)
    write(OUT / "candidates.csv", CAND_COLS, cands)
    for side in ("tol", "birdnet"):
        counts = {h: sum(m[f"{side}_how"] == h for m in mapped) for h in ("exact", "synonym", "none")}
        print(f"{side}: {counts} of {len(mapped)}")
    print(f"{len(cands)} candidates -> {OUT / 'candidates.csv'}")


def main_mammals(a):
    if not a.mdd_synonyms:
        raise SystemExit("--mdd-synonyms Species_Syn_Current_*.csv (from the MDD zip) is required for mammals")
    rows = names.read_mdd(Path(a.mdd) if a.mdd else names._list_file(naming.DATA_DIR, "mdd"))
    labels = mammal_labels_of(birdnet_labels(a.labels), Path(a.birdnet_taxonomy).expanduser())
    with open(a.mdd_synonyms, newline="", encoding="utf-8") as f:
        index = mdd_synonym_index(csv.DictReader(f))
    mapped, report = build_mdd(rows, labels, index)
    write(OUT / names.LISTS["mammal"].label_map, MDD_MAP_COLS, mapped)
    print("\n".join(report))
    used = sum(len(m["birdnet_label"].split(geo.LABEL_SEP)) for m in mapped if m["birdnet_label"])
    counts = {h: sum(m["birdnet_how"] == h for m in mapped) for h in ("exact", "synonym", "none")}
    print(f"birdnet: {counts} of {len(mapped)} rows; {used} of {len(labels)} mammal labels used")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--list", choices=["bird", "mammal"], default="bird")
    p.add_argument("--labels", help="BirdNET labels, one per line (default: load the BirdNET geo model)")
    p.add_argument("--mdd", help="MDD species CSV (default: the one under data/mdd)")
    p.add_argument("--mdd-synonyms", help="MDD Species_Syn_Current_*.csv (mammals)")
    p.add_argument("--birdnet-taxonomy", default=str(BIRDNET_TAXONOMY), help="BirdNET taxonomy CSV (mammals)")
    a = p.parse_args(argv)
    (main_mammals if a.list == "mammal" else main_birds)(a)


if __name__ == "__main__":
    main()
