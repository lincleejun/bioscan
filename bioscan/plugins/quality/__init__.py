"""quality: per-image technical checks and rule-based, explainable reject reasons. Manifest only
(stdlib); the stage is stage.py (numpy, on the CPU pool).

It reads identify's boxes (the best box by score is the subject) and the whole-frame gate, so a run
with quality needs identify; species may be off (album). Every threshold is an UPPER_CASE constant
in stage.py, in Stage.settings() and so in result.engine.plugins["quality"]. Rejects are rules only:
a photo is never rejected for taste (aesthetics only reorders, in the select reducer)."""
from __future__ import annotations

from bioscan import cull
from bioscan.plugin import Manifest, Metric

# every reason quality can give, in the order it reports them (CONTEXT.md "reject reason")
REASONS = cull.REJECT_REASONS
# `bioscan bench` (album tier): scopes all, soft (soft_subject, motion or defocus), each reason and motion_or_defocus
# (the name before the split, still accepted in waivers and ground truth: motion or defocus)
METRICS = (
    Metric("reject_precision", "rate", "bioscan.cull:row_reject_precision",
           description="images rejected for the scope's reason(s) whose truth has it"),
    Metric("reject_recall", "rate", "bioscan.cull:row_reject_recall",
           description="images whose truth has the scope's reason(s) that were rejected for it"),
    Metric("keepers_lost", "rate", "bioscan.cull:row_keepers_lost", lower_is_better=True,
           description="keep-labelled images a rule rejected"),
)

_REGION = {"sharpness": "float, rules.quality (ranks, does not judge)", "exposure": "float, mean luma - 0.5",
           "blur": "float 0 sharp - 1 soft (re-blur measure) | null: too little detail to tell",
           "clip_high": "share of pixels at luma >= 250/255", "clip_low": "share of pixels at luma <= 8/255"}

MANIFEST = Manifest(
    name="quality",
    version=1,
    description="Frame and subject technical quality and reject reasons. The subject is identify's best box: "
                "its sharpness and exposure (rules.quality, as in identify's boxes), blur (re-blur measure on "
                "its core), clipped highlight and shadow shares, area share of the frame, whether it touches the "
                "frame edge (cut off) and its placement (thirds / centre). Reasons: " + ", ".join(REASONS) + ".",
    reads=("image", "boxes", "gate", "time"),
    thread="cpu",
    output={"frame": {**_REGION, "blur": "blur of the sharpest detailed tile outside the subject | null"},
            "subject": {"box": "int, identify box id", "kind": "bird|mammal|other_animal", **_REGION,
                        "area": "box area / frame area", "edges": "[left|top|right|bottom] within 1% of the frame edge",
                        "cut": "bool: touches an edge and is not frame-filling",
                        "placement": "thirds|centre|off", "thirds_dist": "float", "centre_dist": "float",
                        "_": "null when identify found no box"},
            "reject_reasons": f"[{'|'.join(REASONS)}], empty = no rule rejects it",
            "capture": {"taken_at": "ISO 8601 | null (request, else EXIF)", "camera": "EXIF Make Model | null"}},
    impl="bioscan.plugins.quality.stage:STAGE",
    metrics=METRICS,
    fingerprinted=True,
)
