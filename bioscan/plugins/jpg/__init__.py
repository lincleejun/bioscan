"""jpg: an upright JPEG of the decoded image. Manifest only (stdlib)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from bioscan.plugin import Manifest

OUT_DIR = "/tmp/bioscan-jpg"
EDGE = 2048


def check(o: dict[str, Any]) -> None:
    if not isinstance(o["out_dir"], str) or not Path(o["out_dir"]).is_absolute():
        raise ValueError("options.jpg.out_dir must be an absolute path")
    if not isinstance(o["edge"], int) or isinstance(o["edge"], bool) or o["edge"] < 256:
        raise ValueError("options.jpg.edge must be an integer >= 256")


MANIFEST = Manifest(
    name="jpg",
    version=1,
    description="Upright JPEG, long edge `edge` (default 2048; above that it comes from the service's detail copy, "
                "so at most its --detail-edge, 3072 by default), quality 92, written as "
                "<out_dir>/<stem>-<sha256[:8]>.jpg (same source bytes -> same file; same-named sources never collide).",
    reads=("image", "detail"),
    thread="cpu",
    options={"out_dir": {"type": "string", "format": "absolute path", "default": OUT_DIR},
             "edge": {"type": "integer", "default": EDGE, "minimum": 256}},
    output={"path": "string", "width": "int", "height": "int"},
    impl="bioscan.plugins.jpg.stage:STAGE",
    check=check,
)
