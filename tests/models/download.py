"""Fetch everything the service reads offline: model weights into ~/.cache/huggingface, the BirdNET
geo model into birdnet's app-data folder, and the all-taxa name list into ~/.cache/bioscan/names.
Prints the revision each one resolved to.

    uv run python tests/models/download.py

The all-taxa list is built from the pinned TreeOfLife-200M text vectors (3.26 GB), downloaded to
a temporary folder and deleted afterwards, and only when its cache file is missing.
"""
import tempfile
import time
from pathlib import Path

from huggingface_hub import HfApi, hf_hub_download, snapshot_download

from bioscan.service import names
from bioscan.service.adapters import bioclip, geo, owlv2, siglip2


def fetch(repo: str, **kw) -> None:
    path = snapshot_download(repo, ignore_patterns=["*.bin"], **kw)
    print(f"{repo} @ {Path(path).name}")


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
        other = names.load_all_taxa(tol_files=files)
    if other is None:
        raise SystemExit("TreeOfLife has no row for the all-taxa list")
    print(f"built {names.all_taxa_path().name}: download {t1 - t0:.0f} s, build {time.monotonic() - t1:.0f} s")
other = names.load_all_taxa(tol_files=(Path("/nonexistent"),) * 2)
print(f"{other.list_id}: {len(other.scientific)} species, matrix {other.matrix.dtype} "
      f"{other.matrix.nbytes / 2**20:.0f} MiB, cache {names.all_taxa_path().stat().st_size / 2**20:.0f} MiB")
prior = geo.GeoPrior.load()
if prior is None:
    raise SystemExit("BirdNET geo model could not be loaded")
print(f"birdnet geo {'/'.join(geo.GEO_MODEL)}: {len(prior.labels)} labels")
print("ready")
