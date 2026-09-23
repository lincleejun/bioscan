"""Fetch everything the service reads offline: model weights into ~/.cache/huggingface and the
BirdNET geo model into birdnet's app-data folder. Prints the revision each one resolved to.

    uv run python tests/models/download.py
"""
from pathlib import Path

from huggingface_hub import HfApi, snapshot_download

from bioscan.service.adapters import bioclip, geo, owlv2, siglip2


def fetch(repo: str, **kw) -> None:
    path = snapshot_download(repo, ignore_patterns=["*.bin"], **kw)
    print(f"{repo} @ {Path(path).name}")


fetch(siglip2.MODEL_ID, revision=getattr(siglip2, "REVISION", None))
fetch(owlv2.MODEL_ID, revision=owlv2.REVISION)
fetch(bioclip.MODEL_ID.removeprefix("hf-hub:"), revision=getattr(bioclip, "REVISION", None))
# TreeOfLife-200M text vectors (3.26 GB) are only needed to rebuild the name cache; report the
# commit data/README.md names (snapshot 5f2dc493) so it can be pinned.
commits = HfApi().list_repo_commits("imageomics/TreeOfLife-200M", repo_type="dataset")
print("imageomics/TreeOfLife-200M 5f2dc493* ->", [c.commit_id for c in commits if c.commit_id.startswith("5f2dc493")],
      "main ->", commits[0].commit_id)
prior = geo.GeoPrior.load()
if prior is None:
    raise SystemExit("BirdNET geo model could not be loaded")
print(f"birdnet geo {'/'.join(geo.GEO_MODEL)}: {len(prior.labels)} labels")
print("ready")
