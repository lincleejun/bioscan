#!/bin/sh
# First start: fetch the lists, the weights and the name caches into the mounted volumes (about 10 GB, once).
set -eu
uv run --no-sync python scripts/names/fetch_lists.py     # keeps what is already there
if [ ! -f /root/.cache/bioscan/.ready ]; then
  uv run --no-sync python tests/models/download.py
  uv run --no-sync gardener-engine names stats >/dev/null
  mkdir -p /root/.cache/bioscan && touch /root/.cache/bioscan/.ready
fi
exec uv run --no-sync gardener serve --host 0.0.0.0 --port 8770
