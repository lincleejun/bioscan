#!/bin/sh
# First start: fetch the model weights and the all-taxa name list into the mounted volume (about 6.5 GB, once).
set -eu
if [ ! -f /root/.cache/bioscan/.ready ]; then
  uv run --no-sync python tests/models/download.py
  mkdir -p /root/.cache/bioscan && touch /root/.cache/bioscan/.ready
fi
exec uv run --no-sync gardener serve --host 0.0.0.0 --port 8770
