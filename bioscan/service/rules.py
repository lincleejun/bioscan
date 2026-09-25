"""Model-free rules of identify: box geometry, the crop-gate verdict, species level, quality.
Pure functions and the thresholds they use; pipeline.py wires them to the models."""
from __future__ import annotations

from collections import defaultdict
from functools import lru_cache
from statistics import NormalDist
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
# Range veto: where the place is known and the list has a prior, a top candidate with p_geo below
# RANGE_EPS cannot be graded species; a congener among the candidates with p_geo >= RANGE_TAU is
# listed first instead. EPS: the geo model says "not here"; TAU: the geo-gaps "genus present" bar.
RANGE_EPS = 0.01
RANGE_TAU = 0.05
# Kind check: the box's species evidence (BioCLIP over every kind-check list) outvotes the gate and
# crop check. Each kind's evidence is the probability of its KIND_TOP best rows, the same number for
# every list: that removes most of the size effect between the curated lists (AviList 11k, MDD 7k),
# but not between them and the ~366k-row all-taxa list, whose best rows sit higher by chance alone
# (expected top-5 of N random scores: ~3.5 sd at 7-11k, ~4.4 sd at 366k). Hence the all-taxa list
# only competes for other_animal boxes (taxa.ONE_WAY). The trial option kind_size_correct subtracts
# that chance level per list first (kind_evidence_logits); off by default until measured on real photos. With less than KIND_SURE of the evidence on
# the winning kind, a box whose kind moved is graded unconfirmed (any name above that would assert
# a kind the evidence cannot).
KIND_TOP = 5
KIND_SURE = 0.75


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


def species_level(cands: list[dict[str, Any]], species_ok: bool = True) -> str:
    """species when top-1 is clear (and `species_ok`), else the genus or family the top-5 agree on,
    else unconfirmed."""
    if not cands:
        return "unconfirmed"
    ordered = sorted(cands, key=lambda c: -c["posterior"])
    p1 = ordered[0]["posterior"]
    p2 = ordered[1]["posterior"] if len(ordered) > 1 else 0.0
    if species_ok and p1 >= SPECIES_P and p1 - p2 >= SPECIES_MARGIN:
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


def range_veto(cands: list[dict[str, Any]], direct: list[bool] | None = None) -> tuple[list[dict[str, Any]], bool]:
    """(candidates, vetoed). `cands` ranked by posterior; `direct[i]` says whether cands[i]'s p_geo
    is evidence about that species itself (None = all are). Vetoed when the top one has a direct
    p_geo (the place is known and its list has a prior) below RANGE_EPS: it may not be graded
    species, and the first congener with a direct p_geo >= RANGE_TAU, if any, moves to the front.
    The Raven ranked below a Philippine crow in California comes first again; the level is then
    the genus the two share. A p_geo borrowed from the genus (unlabelled policy "genus") vetoes
    nothing: a Californian jackrabbit is not absent because BirdNET's labelled hares are."""
    direct = direct if direct is not None else [True] * len(cands)
    if not cands or cands[0]["p_geo"] is None or not direct[0] or cands[0]["p_geo"] >= RANGE_EPS:
        return cands, False
    genus = cands[0]["taxonomy"][5]
    mate = next((c for c, d in zip(cands[1:], direct[1:]) if d and c["taxonomy"][5] == genus
                 and c["p_geo"] is not None and c["p_geo"] >= RANGE_TAU), None)
    return ([mate, *(c for c in cands if c is not mate)] if mate else cands), True


def kind_evidence(probs: np.ndarray, rows_of: dict[str, slice]) -> dict[str, float]:
    """Each kind's share of the evidence from one box's probabilities over the stacked kind-check
    lists (`rows_of` = each list's rows): the sum of its KIND_TOP highest, normalised over kinds.
    Equivalent to comparing log-sum-exp of each list's top KIND_TOP logits; rows beyond a list's
    best KIND_TOP never count, so padding a list with irrelevant names changes nothing."""
    top = {k: float(np.sort(probs[rows])[-KIND_TOP:].sum()) for k, rows in rows_of.items()}
    total = sum(top.values())
    return {k: v / total if total > 0 else 1.0 / len(top) for k, v in top.items()}


def kind_evidence_logits(logits: dict[str, np.ndarray], size_correct: bool = False) -> dict[str, float]:
    """kind_evidence from each list's own scaled similarities (BioCLIP logits over its rows, or the
    rows candidates leave it): exp of its KIND_TOP highest, summed, normalised over kinds. The joint
    softmax's denominator cancels in that normalisation, so this equals kind_evidence over the
    stacked lists without ever stacking them (the all-taxa list would make that ~1.9 GB).

    size_correct (identify option "kind_size_correct", a trial): the i-th best logit of a list of N
    rows first loses sigma * E[i-th best of N standard normals], sigma = the spread of that box's
    logits over the list: what a list of that size and spread scores by chance alone. The evidence
    is then what each list holds above chance, so a longer list no longer wins by size."""
    top = {}
    for k, z in logits.items():
        z = np.asarray(z, dtype=np.float64)
        t = _top(z)
        if size_correct and len(z) > 1:
            t = t - float(z.std()) * np.asarray(chance_top(len(z))[-len(t):])
        top[k] = t
    peak = max(float(t.max()) for t in top.values() if len(t))
    mass = {k: float(np.exp(t - peak).sum()) for k, t in top.items()}
    total = sum(mass.values())
    return {k: v / total if total > 0 else 1.0 / len(mass) for k, v in mass.items()}


@lru_cache(maxsize=256)
def chance_top(n: int) -> tuple[float, ...]:
    """Expected KIND_TOP highest of n standard normals, ascending (Blom's approximation of the
    normal order statistics: the i-th highest sits at the (n - i + 0.625) / (n + 0.25) quantile)."""
    inv = NormalDist().inv_cdf
    return tuple(inv((n - i + 0.625) / (n + 0.25)) for i in range(min(KIND_TOP, n), 0, -1))


def _top(z: np.ndarray) -> np.ndarray:
    """The KIND_TOP highest values, ascending (a partition first: the all-taxa list has ~366k rows)."""
    return np.sort(np.partition(z, -KIND_TOP)[-KIND_TOP:] if len(z) > KIND_TOP else z)


def kind_of(mass: dict[str, float]) -> tuple[str, bool]:
    """(kind, sure) from the share of species evidence each kind-check list holds (kind_evidence):
    the list with the most, and whether that is at least KIND_SURE."""
    kind = max(mass, key=lambda k: mass[k])
    return kind, mass[kind] >= KIND_SURE


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
