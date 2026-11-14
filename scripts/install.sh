#!/bin/sh
# One step from a fresh clone to a running Gardener on this Mac (native, uses the GPU). About 10 GB of downloads, once.
set -eu
cd "$(dirname "$0")/.."
command -v uv >/dev/null || { echo "Install uv first: https://docs.astral.sh/uv/getting-started/installation/"; exit 1; }
uv sync
uv run python scripts/names/fetch_lists.py        # bird and mammal lists, 26 MB
uv run python tests/models/download.py            # pinned model weights, about 7 GB
uv run gardener-engine names stats >/dev/null     # name caches (3.26 GB download, once)
echo "Ready. Start it with:  uv run gardener serve   then open http://127.0.0.1:8770"
