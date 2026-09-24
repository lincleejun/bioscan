"""select: a reducer (bioscan/cull.py) that keeps the best frame of each burst and the top
per_category frames of each scene category, skipping near-duplicates. Reads quality, scene, burst
and, when a run has it, aesthetics (which only reorders). Manifest only (stdlib)."""
from __future__ import annotations

from bioscan import cull
from bioscan.plugin import Manifest

MANIFEST = Manifest(
    name="select",
    version=1,
    description="Best of each burst (not rejected, subject sharpness, not cut off, exposure, aesthetic if "
                "present), then the top per_category of each scene category, near-duplicates skipped.",
    reads=("quality", "scene", "burst", "embed"),
    options={"per_category": {"type": "integer", "minimum": 0, "default": 10, "description": "0 = no limit"},
             "dup_cosine": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.95,
                            "description": "a keeper this alike (frame vectors) to a pick is a duplicate"},
             "sharp_tie": {"type": "number", "minimum": 0, "default": 0.03,
                           "description": "sharpness (1 - blur) within this of a burst's sharpest counts as equal"},
             "exposure_ok": {"type": "number", "minimum": 0, "default": 0.2,
                             "description": "|exposure| up to this is within limits"},
             "waive": {"type": "object", "default": {"night": ["underexposed"]},
                       "description": "category -> reject reasons that do not reject there"}},
    output={"status": "|".join(cull.STATUSES), "keep": "bool: status pick", "reasons": "quality's reasons, "
            "minus waived", "waived": "[reason]", "category": "scene label | uncategorised",
            "rank": "in its category, best first | null", "burst_rank": "1 = best of its burst",
            "duplicate_of": "path | null", "sharpness": "1 - blur | null", "aesthetic": "products.aesthetics.score | null"},
    impl="bioscan.cull:SELECT",
    check=cull.check_select,
    kind="reducer",
)
