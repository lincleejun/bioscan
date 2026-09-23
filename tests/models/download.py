"""Fetch the model weights the service reads from ~/.cache/huggingface (same as README install).

    uv run python tests/models/download.py
"""
from huggingface_hub import snapshot_download

from bioscan.service.adapters import bioclip, owlv2, siglip2

snapshot_download(siglip2.MODEL_ID)
snapshot_download(owlv2.MODEL_ID, revision=owlv2.REVISION, ignore_patterns=["*.bin"])
snapshot_download(bioclip.MODEL_ID.removeprefix("hf-hub:"), ignore_patterns=["*.bin"])
print("weights ready")
