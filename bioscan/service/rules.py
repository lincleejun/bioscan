"""Model-free rules of identify: box geometry, the crop-gate verdict, species level, quality.
Pure functions and the thresholds they use; pipeline.py wires them to the models."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np
from PIL import Image

from bioscan import contract
from bioscan.service.adapters.owlv2 import Detection
from bioscan.service.taxa import ANIMALS, NOT_ANIMAL, PROMOTE_TO

VETO = 0.8              # crop gate this sure of "none"/"person" throws the box away
MAMMAL_SUPPORT = 0.3    # a non-bird box needs crop-gate mammal + other_animal at least this
BIRD_PROMOTE = 0.5      # crop gate this sure of bird turns a non-bird box into a bird box
MIN_CROP = 320
SPECIES_P = 0.5
SPECIES_MARGIN = 0.3
ROLLUP = 0.6
SECOND_PASS_FLOOR = 0.1
SECOND_PASS_TOP = 3
IOU_SAME = 0.5
RESCUE = 0.25           # gate says none/person but bird+mammal+other_animal >= this: still look (first pass only)


def iou(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    x0, y0, x1, y1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def dedupe(dets: list[Detection], against: list[Detection] = ()) -> list[Detection]:
    """Two prompts claiming one object: the stronger claim wins, whatever its word."""
    kept: list[Detection] = []
    for d in sorted(dets, key=lambda d: -d.confidence):
        if all(iou(d.bbox, k.bbox) < IOU_SAME for k in [*against, *kept]):
            kept.append(d)
    return kept


def crop_with_context(image: Image.Image, bbox: tuple[float, ...], min_side: int = MIN_CROP) -> Image.Image:
    """The box plus 10 % context, squared, never smaller than `min_side` (a 40 px bird fed to a
    224 px encoder is a smudge), shifted to stay inside the frame."""
    width, height = image.size
    x0, y0, x1, y1 = bbox
    bw, bh = x1 - x0, y1 - y0
    x0, y0, x1, y1 = x0 - bw * 0.1, y0 - bh * 0.1, x1 + bw * 0.1, y1 + bh * 0.1
    side = max(x1 - x0, y1 - y0, float(min_side))
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    x0, x1, y0, y1 = cx - side / 2, cx + side / 2, cy - side / 2, cy + side / 2
    if x0 < 0:
        x1, x0 = x1 - x0, 0.0
    if y0 < 0:
        y1, y0 = y1 - y0, 0.0
    if x1 > width:
        x0, x1 = x0 - (x1 - width), float(width)
    if y1 > height:
        y0, y1 = y0 - (y1 - height), float(height)
    return image.crop((max(0, int(x0)), max(0, int(y0)), min(width, int(x1)), min(height, int(y1))))


def judge(kind: str, crop_gate: dict[str, float]) -> str | None:
    """Final kind of one detector box from what the crop gate made of it; None = rejected."""
    winner = max(crop_gate, key=lambda k: crop_gate[k])
    if winner in NOT_ANIMAL and crop_gate[winner] >= VETO:
        return None
    if kind != PROMOTE_TO and crop_gate.get(PROMOTE_TO, 0.0) >= BIRD_PROMOTE:
        return PROMOTE_TO
    if kind != PROMOTE_TO and sum(crop_gate.get(k, 0.0) for k in ANIMALS if k != PROMOTE_TO) < MAMMAL_SUPPORT:
        return None
    return kind


def species_level(cands: list[dict[str, Any]]) -> str:
    """species when top-1 is clear, else the genus or family the top-5 agree on, else unconfirmed."""
    if not cands:
        return "unconfirmed"
    ordered = sorted(cands, key=lambda c: -c["posterior"])
    p1 = ordered[0]["posterior"]
    p2 = ordered[1]["posterior"] if len(ordered) > 1 else 0.0
    if p1 >= SPECIES_P and p1 - p2 >= SPECIES_MARGIN:
        return "species"
    genus: dict[str, float] = defaultdict(float)
    family: dict[str, float] = defaultdict(float)
    for c in ordered[:5]:
        genus[c["taxonomy"][5]] += c["posterior"]
        family[c["taxonomy"][4]] += c["posterior"]
    if max(genus.values()) >= ROLLUP:
        return "genus"
    if max(family.values()) >= ROLLUP:
        return "family"
    return "unconfirmed"


def quality(image: Image.Image, bbox: tuple[float, ...]) -> contract.Quality:
    """Sharpness and exposure of the box grown by 10 %.

    Sharpness is PhotoOS's metric, not plain Laplacian variance: variance measures how much
    detail a picture has, so a focused bird on empty sky scored below a blurred busy scene.
    Here: sigma-1 Gaussian (kills sensor-noise impulses), 4-neighbour Laplacian, mean of the
    strongest 0.1 % of responses divided by the crop's own amplitude. Ranks, does not judge.
    """
    w, h = image.size
    x0, y0, x1, y1 = bbox
    dx, dy = (x1 - x0) * 0.1, (y1 - y0) * 0.1
    crop = image.crop((max(0, int(x0 - dx)), max(0, int(y0 - dy)), min(w, round(x1 + dx)), min(h, round(y1 + dy))))
    gray = np.asarray(crop.convert("L"), dtype=np.float32) / 255.0
    exposure = round(float(gray.mean()) - 0.5, 4) if gray.size else 0.0
    if min(gray.shape) < 8:
        return contract.quality(0.0, exposure)
    height, width = gray.shape
    kernel = np.exp(-(np.arange(-2, 3) ** 2) / 2.0)
    kernel /= kernel.sum()
    padded = np.pad(gray, 2, mode="edge")
    cols = sum(float(k) * padded[i:i + height] for i, k in enumerate(kernel))
    smooth = sum(float(k) * cols[:, i:i + width] for i, k in enumerate(kernel))
    energy = np.abs(smooth[:-2, 1:-1] + smooth[2:, 1:-1] + smooth[1:-1, :-2] + smooth[1:-1, 2:]
                    - 4.0 * smooth[1:-1, 1:-1])
    strongest = energy[energy >= np.percentile(energy, 99.9)]
    amplitude = float(np.percentile(np.abs(gray - np.median(gray)), 99.5)) + 1e-3
    return contract.quality(round(float(strongest.mean()) / amplitude, 4), exposure)


def species_crops(image: Image.Image, bboxes: list[tuple[float, ...]],
                  detail: Image.Image | None = None) -> list[Image.Image]:
    """What BioCLIP sees per box: the same framing as crop_with_context on `image` (boxes are in its
    pixels), cut from `detail` when there is one -- a larger copy of the same frame, so a distant
    bird keeps the feather detail the 2048 px frame has already thrown away."""
    if detail is None or detail.size == image.size:
        return [crop_with_context(image, b) for b in bboxes]
    sx, sy = detail.width / image.width, detail.height / image.height
    return [crop_with_context(detail, (b[0] * sx, b[1] * sy, b[2] * sx, b[3] * sy), min_side=round(MIN_CROP * sx))
            for b in bboxes]
