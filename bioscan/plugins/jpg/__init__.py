"""jpg: an upright JPEG of the decoded image. Manifest only (stdlib)."""
from __future__ import annotations

from bioscan.plugin import Manifest

OUT_DIR = "/tmp/bioscan-jpg"

MANIFEST = Manifest(
    name="jpg",
    version=1,
    description="Upright JPEG, long edge 2048, quality 92, written as <out_dir>/<stem>-<sha256[:8]>.jpg "
                "(same source bytes -> same file; same-named sources never collide).",
    reads=("image",),
    thread="cpu",
    options={"out_dir": {"type": "string", "format": "absolute path", "default": OUT_DIR}},
    output={"path": "string", "width": "int", "height": "int"},
    impl="bioscan.plugins.jpg.stage:STAGE",
)
