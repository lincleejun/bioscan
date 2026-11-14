#!/bin/sh
# One step from a fresh clone to a running Gardener on this Mac (native, uses the GPU). About 6.5 GB of downloads, once.
set -eu
cd "$(dirname "$0")/.."
command -v uv >/dev/null || { echo "Install uv first: https://docs.astral.sh/uv/getting-started/installation/"; exit 1; }
uv sync
uv run python tests/models/download.py            # model weights (5.7 GB) and the all-taxa name list (0.8 GB)
echo "Ready. Start it with:  uv run gardener serve   then open http://127.0.0.1:8770"
