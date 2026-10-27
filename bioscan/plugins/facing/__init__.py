"""facing: which way the subject looks, for select's lead-room flag (#27 step 2). Manifest only
(stdlib); the stage is stage.py, on the model thread.

The subject is identify's best box by score (as quality picks it). OWLv2 is asked once more, on that
box's crop of the decoded frame, for the kind's head and eye ("bird head", "bird eye"; mammal; other
animals "animal head"). The head's centre (the eye's when no head is found) left or right of the box
centre by at least FACING_OFFSET of the box width gives the side it faces; nearer the centre (looking at
the camera or away) or with neither part found, the side is null. One extra OWLv2 call per photo with a
box; photos without one cost nothing. Thresholds are UPPER_CASE constants in stage.py, in
Stage.settings() and so in result.engine.plugins["facing"]; guesses, unverified until #28."""
from __future__ import annotations

from bioscan.plugin import Manifest

_PART = {"xyxy": "[x0, y0, x1, y1] normalised to the frame (0-1, upright)", "score": "float, OWLv2 confidence",
         "_": "null when OWLv2 found none above the floor"}

MANIFEST = Manifest(
    name="facing",
    version=1,
    description="Which way the subject (identify's best box) faces: OWLv2 asked for the kind's head and eye "
                "inside the box; the head's offset from the box centre (else the eye's) gives the side. "
                "select's no_lead_room flag reads it.",
    reads=("image", "boxes"),
    models=lambda opts: ("owlv2",),
    thread="model",
    output={"box": "int, identify box id | null: no box",
            "side": "left|right | null: facing the camera or away, no part found, or no box",
            "offset": "float, the part's centre minus the box centre, a share of the box width (+ = right) | null",
            "head": _PART, "eye": _PART},
    impl="bioscan.plugins.facing.stage:STAGE",
    fingerprinted=True,
)
