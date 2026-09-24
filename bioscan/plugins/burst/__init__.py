"""burst: a reducer (bioscan/cull.py) that chains frames of one camera taken at most max_gap_s apart
whose SigLIP2 frame vectors are alike. Needs quality's capture (time, camera) and embed's vector in
the results; frames without either stay alone. Manifest only (stdlib)."""
from __future__ import annotations

from bioscan import cull
from bioscan.plugin import Manifest

MANIFEST = Manifest(
    name="burst",
    version=1,
    description="Bursts: frames of one camera (EXIF Make/Model) at most max_gap_s apart (sub-second capture "
                "time) with SigLIP2 frame-vector cosine at least min_cosine, chained in time order.",
    reads=("quality", "embed"),
    options={"max_gap_s": {"type": "number", "minimum": 0, "default": 1.5},
             "min_cosine": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.92}},
    output={"id": "b0001... | null (in no burst)", "size": "frames in the burst", "index": "0-based, in time order",
            "gap_s": "seconds since the camera's previous frame | null", "cosine": "to that frame | null"},
    impl="bioscan.cull:BURST",
    check=cull.check_burst,
    kind="reducer",
)
