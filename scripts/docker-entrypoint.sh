#!/bin/sh
# The album starts at once; the models (about 7 GB, into the mounted volume) come the first time 自动归类 is asked for.
set -eu
exec uv run --no-sync gardener serve --host 0.0.0.0 --port 8770
