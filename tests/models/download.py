"""Fetch everything the service reads offline: model weights into ~/.cache/huggingface, the BirdNET
geo model into birdnet's app-data folder, and the all-taxa name list into ~/.cache/bioscan/names.
Prints the revision each one resolved to.

    uv run python tests/models/download.py

The all-taxa list is built from the pinned TreeOfLife-200M text vectors (3.26 GB), downloaded to
a temporary folder and deleted afterwards, and only when its cache file is missing. A build prints
a census of TreeOfLife's names (rows per kingdom and class, rows left out per filter reason) and,
for every other-animal species of tests/models/sample.csv, its TreeOfLife rows and whether the list
keeps them; every run prints which of those species the list holds, and under what taxonomy.
"""
import csv
import json
import tempfile
import time
from pathlib import Path

from huggingface_hub import HfApi, hf_hub_download, snapshot_download

from bioscan.naming import norm_binomial
from bioscan.service import names
from bioscan.service.adapters import bioclip, geo, owlv2, siglip2

with open(Path(__file__).with_name("sample.csv"), newline="", encoding="utf-8") as f:
    OTHERS = sorted({r["scientific"] for r in csv.DictReader(f) if r["kind"] == "other_animal"})


def fetch(repo: str, **kw) -> None:
    path = snapshot_download(repo, ignore_patterns=["*.bin"], **kw)
    print(f"{repo} @ {Path(path).name}")


def census(tol_names) -> None:
    c = names.all_taxa_census(tol_names, species=OTHERS)
    print(f"== all-taxa census: {c['rows']} TreeOfLife rows, {c['kept']} kept")
    print("rows per (kingdom, class), top 40:")
    for (kingdom, cls), n in c["by_kingdom_class"][:40]:
        print(f"  {n:>8}  {kingdom or '(empty)'} / {cls}")
    print("left out per reason:", json.dumps(c["dropped"]))
    for sp, found in c["species"].items():
        print(f"  {sp}: {len(found['rows'])} row(s); {found['genus_rows']} row(s) with its genus, "
              f"e.g. {json.dumps(found['genus_example'], ensure_ascii=False)}")
        for r in found["rows"][:5]:
            print(f"    row {r['row']} raw {json.dumps(r['raw'], ensure_ascii=False)} -> kept as {r['kept']}")


fetch(siglip2.MODEL_ID, revision=siglip2.REVISION)
fetch(owlv2.MODEL_ID, revision=owlv2.REVISION)
fetch(bioclip.MODEL_ID.removeprefix("hf-hub:"), revision=bioclip.REVISION)
# TreeOfLife-200M text vectors are pinned (names.TOL_REVISION); report whether upstream has moved since.
main = HfApi().list_repo_commits(names.TOL_REPO, repo_type="dataset")[0].commit_id
print(f"{names.TOL_REPO} pinned {names.TOL_REVISION[:12]}, upstream main {main[:12]}")
if not names.all_taxa_path().is_file():
    t0 = time.monotonic()
    with tempfile.TemporaryDirectory() as tmp:          # kept out of ~/.cache/huggingface (and CI's weight cache)
        files = tuple(Path(hf_hub_download(names.TOL_REPO, f, repo_type="dataset", revision=names.TOL_REVISION,
                                           local_dir=tmp)) for f in names.TOL_FILES)
        t1 = time.monotonic()
        tol = names._read_tol(files)
        census(tol[0])
        other = names.load_all_taxa(tol=tol)
        del tol
    if other is None:
        raise SystemExit("TreeOfLife has no row for the all-taxa list")
    print(f"built {names.all_taxa_path().name}: download {t1 - t0:.0f} s, build {time.monotonic() - t1:.0f} s")
other = names.load_all_taxa(tol_files=(Path("/nonexistent"),) * 2)
print(f"{other.list_id}: {len(other.scientific)} species, matrix {other.matrix.dtype} "
      f"{other.matrix.nbytes / 2**20:.0f} MiB, cache {names.all_taxa_path().stat().st_size / 2**20:.0f} MiB")
where = {norm_binomial(s): i for i, s in enumerate(other.scientific)}
for sp in OTHERS:
    i = where.get(norm_binomial(sp))
    print(f"  smoke species {sp}: " + (f"in the list as {other.taxonomy[i]}" if i is not None else "NOT in the list"))
prior = geo.GeoPrior.load()
if prior is None:
    raise SystemExit("BirdNET geo model could not be loaded")
print(f"birdnet geo {'/'.join(geo.GEO_MODEL)}: {len(prior.labels)} labels")
print("ready")
