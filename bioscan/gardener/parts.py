"""Head / eye crop suggestions inside a subject box (AfterShooting PRD §08 第三级 "提供眼部或主体裁切建议";
contract revision 7C).

There is no eye detector. OWLv2 (the detector identify already loads) is open-vocabulary, so it is asked,
on the subject box cut from the ORIGINAL pixels (detail.Details.region: the RAW's full decode, else the
image), for the kind's head and eye with the facing stage's prompts (bioscan/plugins/facing). The best hit
of each part at or above its floor is kept. On demand only (one OWLv2 forward per request): identify and
its answers do not change.

The caller holds the engine's device_lock around find(): two threads on MPS at once abort the process, and
a run's analysis holds the same lock per stage, so a request during a run waits for the stage in progress."""
from __future__ import annotations

from typing import Any

from PIL import Image

from bioscan.plugins.facing.stage import PROMPTS, best

EDGE = 960                                 # OWLv2's input side: a larger crop is shrunk first
FLOOR = {"head": 0.2, "eye": 0.2}          # measured on real photos (contract revision 7C)
MAX_SHARE = 0.6                            # a "part" covering more of the box than this is the whole animal
MIN_PX = 8                                 # a crop smaller than this on a side is not asked


def find(owlv2: Any, crop: Image.Image, box: list[float], kind: str | None) -> list[dict[str, Any]]:
    """[{name: head|eye, xyxy (0-1 of the upright frame), score}] for the subject `box` (0-1 of the frame),
    `crop` being its original pixels. Only parts at or above FLOOR and at most MAX_SHARE of the box."""
    if min(crop.size) < MIN_PX:
        return []
    s = min(1.0, EDGE / max(crop.size))
    if s < 1:
        crop = crop.resize((max(1, round(crop.width * s)), max(1, round(crop.height * s))), Image.Resampling.LANCZOS)
    prompts = PROMPTS.get(kind or "", PROMPTS["other_animal"])
    dets = owlv2.detect(crop, list(prompts), threshold=min(FLOOR.values()))
    x0, y0, x1, y1 = box
    fx, fy = (x1 - x0) / crop.width, (y1 - y0) / crop.height          # crop pixels -> frame fractions
    out = []
    for name, prompt in zip(("head", "eye"), prompts):
        d = best(dets, prompt)
        if d is None or d.confidence < FLOOR[name]:
            continue
        a, b, c, e = d.bbox
        if (c - a) * (e - b) <= MAX_SHARE * crop.width * crop.height:
            out.append({"name": name, "score": round(d.confidence, 4),
                        "xyxy": [round(x0 + a * fx, 5), round(y0 + b * fy, 5), round(x0 + c * fx, 5),
                                 round(y0 + e * fy, 5)]})
    return out
