"""Fetch everything the service reads offline: model weights into ~/.cache/huggingface and the
BirdNET geo model into birdnet's app-data folder. Prints the revision each one resolved to.

    uv run python tests/models/download.py
"""
from pathlib import Path

from huggingface_hub import HfApi, snapshot_download

from bioscan.service import names
from bioscan.service.adapters import bioclip, geo, owlv2, siglip2


def fetch(repo: str, **kw) -> None:
    path = snapshot_download(repo, ignore_patterns=["*.bin"], **kw)
    print(f"{repo} @ {Path(path).name}")


fetch(siglip2.MODEL_ID, revision=siglip2.REVISION)
fetch(owlv2.MODEL_ID, revision=owlv2.REVISION)
fetch(bioclip.MODEL_ID.removeprefix("hf-hub:"), revision=bioclip.REVISION)
# TreeOfLife-200M text vectors (3.26 GB) are only needed to rebuild the name cache (names.py pins
# TOL_REVISION); report whether upstream has moved since.
main = HfApi().list_repo_commits(names.TOL_REPO, repo_type="dataset")[0].commit_id
print(f"{names.TOL_REPO} pinned {names.TOL_REVISION[:12]}, upstream main {main[:12]}")
prior = geo.GeoPrior.load()
if prior is None:
    raise SystemExit("BirdNET geo model could not be loaded")
print(f"birdnet geo {'/'.join(geo.GEO_MODEL)}: {len(prior.labels)} labels")
print("ready")
