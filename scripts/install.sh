#!/bin/sh
# From a fresh clone to a running album on this Mac: the Python environment only (about 1.3 GB on disk).
# The models 自动归类 uses (about 7 GB) are fetched the first time you ask for it, from the page or with
# `uv run gardener models --download`; to fetch them now: `uv run python tests/models/download.py`.
set -eu
cd "$(dirname "$0")/.."
command -v uv >/dev/null || { echo "Install uv first: https://docs.astral.sh/uv/getting-started/installation/"; exit 1; }
uv sync
echo "Ready. Start it with:  uv run gardener serve   then open http://127.0.0.1:8770"
