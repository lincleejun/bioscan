"""burst: a reducer (gardener/cull.py) that chains frames of one camera taken at most max_gap_s apart
whose SigLIP2 frame vectors are alike. Needs quality's capture (time, camera) and embed's vector in
the results; frames without either stay alone. Manifest only (stdlib)."""
from __future__ import annotations

from gardener import cull
from gardener.plugin import Manifest, Metric

ROW = "gardener.cull:row_burst"
METRICS = (Metric("burst_pair_precision", "pair_precision", ROW, description="pairs grouped that the truth groups"),
           Metric("burst_pair_recall", "pair_recall", ROW, description="truth pairs that were grouped"),
           Metric("burst_pair_f1", "pair_f1", ROW, description="harmonic mean of the two"))

MANIFEST = Manifest(
    name="burst",
    version=1,
    description="Bursts: frames of one camera (EXIF Make/Model) at most max_gap_s apart (sub-second capture "
                "time) with SigLIP2 frame-vector cosine at least min_cosine, chained in time order; frames at most "
                "shutter_gap_s apart (0 = off) are chained whatever the vectors say.",
    reads=("quality", "embed"),
    options={"max_gap_s": {"type": "number", "minimum": 0, "default": 1.5},
             "min_cosine": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.92},
             "shutter_gap_s": {"type": "number", "minimum": 0, "default": 0}},
    output={"id": "b0001... | null (in no burst)", "size": "frames in the burst", "index": "0-based, in time order",
            "gap_s": "seconds since the camera's previous frame | null", "cosine": "to that frame | null"},
    impl="gardener.cull:BURST",
    check=cull.check_burst,
    kind="reducer",
    metrics=METRICS,
)
