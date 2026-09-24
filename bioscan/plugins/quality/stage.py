"""quality as a stage: per image, on the CPU pool, the frame and subject measures and the reject
reasons (bioscan/plugins/quality/__init__.py lists them).

Sharpness and exposure are rules.quality's (the identify box's own measure). They rank frames but
cannot judge one: rules.quality divides by the crop's own contrast, so a blurred feather texture
scores about as high as a sharp one. The reject rule reads `blur` instead: the re-blur measure of
Crete et al. (2007), which blurs a region again with a 9-tap box filter and asks how much of its
pixel-to-pixel variation that removes. A sharp region loses most of it (blur near 0.1-0.3), an
already soft one little (0.5 and up); a region with almost no variation (sky, a wall) has no value.

soft_subject vs motion_or_defocus: a soft subject in a frame that is sharp somewhere else (focus
landed on the background) is soft_subject; when the sharpest detailed tile outside the subject is
soft too, the whole frame is (camera shake, subject motion blur over everything, or nothing in
focus): motion_or_defocus. A frame without a subject can only be motion_or_defocus."""
from __future__ import annotations

import math
from typing import Any

import numpy as np
from PIL import Image

from bioscan.plugin import Item, StageBase, each
from bioscan.service import rules
from bioscan.service.taxa import ANIMALS

SUBJECT_CORE = 0.7      # blur is measured on the box's central 70 % (per side), away from background at its corners
BLUR_TAPS = 9           # re-blur kernel length (Crete et al.)
SOFT_BLUR = 0.45        # blur at or above this: soft (subject, or the sharpest tile of the frame)
MIN_DETAIL = 0.004      # a region whose mean step between neighbours (luma 0-1) is below this has no blur value
TILES = 4               # the frame's sharpest part: the best of TILES x TILES tiles
TILE_SUBJECT = 0.5      # tiles at least this much inside the subject box do not count as "elsewhere"
WORK_EDGE = 1024        # every measure but the subject's rules.quality runs on a copy at most this long
CLIP_HIGH = 250         # luma (0-255) at or above: a clipped highlight
CLIP_LOW = 8            # luma at or below: a clipped shadow
# Overexposed reads the subject (else the frame): blown highlights on it lose detail for good.
# Underexposed reads the frame, and the subject must be dark too: a black bird is often correctly
# dark, so a dark subject alone never rejects. (A -2 EV frame keeps few pixels at pure black, so
# underexposure needs no clipped share; the shares are reported for review.)
OVER_EXPOSURE = 0.22    # overexposed: exposure (mean luma - 0.5) at least this ...
OVER_CLIP = 0.05        # ... and at least this share of clipped highlights
UNDER_EXPOSURE = -0.26  # underexposed: the frame's exposure at most this ...
UNDER_SUBJECT = -0.25   # ... and the subject's (when there is one) at most this
CUT_MARGIN = 0.01       # a box edge this close to the frame edge (fraction of the side) touches it
CUT_MAX_AREA = 0.5      # a box this large that touches an edge is a deliberate tight crop, not a cut
TOO_SMALL = 0.005       # subject box under this share of the frame area: subject_too_small
NO_SUBJECT_GATE = 0.5   # no box although the gate gives the animal classes this much: no_subject
PLACEMENT_NEAR = 0.08   # box centre within this (normalised distance) of a thirds point / the centre
THIRDS = [(x, y) for x in (1 / 3, 2 / 3) for y in (1 / 3, 2 / 3)]


def _r(v: float | None, n: int = 4) -> float | None:
    return None if v is None else round(float(v), n)


def luma(image: Image.Image) -> np.ndarray:
    return np.asarray(image.convert("L"), dtype=np.float32)


def blur(gray: np.ndarray) -> float | None:
    """Re-blur measure of one region (luma 0-255): 0 sharp .. 1 soft, the worse of the two axes;
    None when the region is too small or too flat to tell."""
    g = gray.astype(np.float64) / 255.0
    if min(g.shape) < BLUR_TAPS + 2:
        return None
    worst = None
    for axis in (0, 1):
        a = np.moveaxis(g, axis, 0)
        pad = np.pad(a, ((BLUR_TAPS // 2 + 1, BLUR_TAPS // 2), (0, 0)), mode="edge")
        c = np.cumsum(pad, axis=0)
        smooth = (c[BLUR_TAPS:] - c[:-BLUR_TAPS]) / BLUR_TAPS
        d_f = np.abs(np.diff(a, axis=0))
        if float(d_f.mean()) < MIN_DETAIL:
            return None
        d_b = np.abs(np.diff(smooth, axis=0))
        lost = float(np.maximum(0.0, d_f - d_b).sum())
        b = 1.0 - lost / float(d_f.sum())
        worst = b if worst is None else max(worst, b)
    return worst


def clipped(gray: np.ndarray) -> tuple[float, float]:
    """(share of clipped highlights, share of clipped shadows)."""
    if not gray.size:
        return 0.0, 0.0
    return float((gray >= CLIP_HIGH).mean()), float((gray <= CLIP_LOW).mean())


def _core(bbox: tuple[float, float, float, float]) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = bbox
    cx, cy, hw, hh = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) * SUBJECT_CORE / 2, (y1 - y0) * SUBJECT_CORE / 2
    return int(cx - hw), int(cy - hh), math.ceil(cx + hw), math.ceil(cy + hh)


def sharpest_tile(gray: np.ndarray, subject: tuple[float, float, float, float] | None) -> float | None:
    """The lowest blur among the frame's TILES x TILES tiles with detail, leaving out tiles that are
    mostly inside `subject` (pixels); None when no tile has detail."""
    h, w = gray.shape
    best = None
    for i in range(TILES):
        for j in range(TILES):
            x0, x1, y0, y1 = w * j // TILES, w * (j + 1) // TILES, h * i // TILES, h * (i + 1) // TILES
            if subject is not None:
                ix = max(0.0, min(x1, subject[2]) - max(x0, subject[0]))
                iy = max(0.0, min(y1, subject[3]) - max(y0, subject[1]))
                if ix * iy >= TILE_SUBJECT * (x1 - x0) * (y1 - y0):
                    continue
            b = blur(gray[y0:y1, x0:x1])
            if b is not None and (best is None or b < best):
                best = b
    return best


def exposure_reasons(frame: dict[str, Any], subject: dict[str, Any] | None) -> list[str]:
    """overexposed from the subject (else the frame); underexposed from the frame, with the subject
    (when there is one) dark as well."""
    out = []
    bright = subject or frame
    if bright["exposure"] >= OVER_EXPOSURE and bright["clip_high"] >= OVER_CLIP:
        out.append("overexposed")
    if frame["exposure"] <= UNDER_EXPOSURE and (subject is None or subject["exposure"] <= UNDER_SUBJECT):
        out.append("underexposed")
    return out


def placement(cx: float, cy: float) -> tuple[str, float, float]:
    thirds = min(math.dist((cx, cy), p) for p in THIRDS)
    centre = math.dist((cx, cy), (0.5, 0.5))
    where = "centre" if centre <= PLACEMENT_NEAR else "thirds" if thirds <= PLACEMENT_NEAR else "off"
    return where, thirds, centre


def working_copy(image: Image.Image) -> Image.Image:
    """The frame at most WORK_EDGE long: blur, exposure and clipping are measured on it, so a
    threshold means the same for a 500 px and a 2048 px frame (and costs a quarter)."""
    if max(image.size) <= WORK_EDGE:
        return image
    s = WORK_EDGE / max(image.size)
    return image.resize((max(1, round(image.width * s)), max(1, round(image.height * s))), Image.Resampling.BILINEAR)


def assess(image: Image.Image, boxes: list[dict[str, Any]], gate: dict[str, float] | None,
           taken_at: str | None = None, camera: str | None = None) -> dict[str, Any]:
    """The quality output of one upright image (the decoded frame) and identify's boxes."""
    small = working_copy(image)
    w, h = small.size
    gray = luma(small)
    q_frame = rules.quality(small, (0, 0, w, h))
    high, low = clipped(gray)
    frame: dict[str, Any] = {"sharpness": q_frame["sharpness"], "exposure": _r(float(gray.mean()) / 255.0 - 0.5),
                             "clip_high": high, "clip_low": low}
    best = max(boxes, key=lambda b: b.get("score", 0)) if boxes else None
    reasons: list[str] = []
    subject = None
    if best is not None:
        x0, y0, x1, y1 = best["xyxy"]
        px = (x0 * w, y0 * h, x1 * w, y1 * h)
        # rules.quality of the best box on the decoded frame: identify's box already carries it
        q = best.get("quality") or rules.quality(image, (x0 * image.width, y0 * image.height, x1 * image.width,
                                                         y1 * image.height))
        cx0, cy0, cx1, cy1 = _core(px)
        sb = blur(gray[max(0, cy0):min(h, cy1), max(0, cx0):min(w, cx1)])
        region = gray[max(0, int(px[1])):min(h, math.ceil(px[3])), max(0, int(px[0])):min(w, math.ceil(px[2]))]
        high, low = clipped(region)
        area = max(0.0, x1 - x0) * max(0.0, y1 - y0)
        edges = [side for side, near in (("left", x0 <= CUT_MARGIN), ("top", y0 <= CUT_MARGIN),
                                         ("right", x1 >= 1 - CUT_MARGIN), ("bottom", y1 >= 1 - CUT_MARGIN)) if near]
        cut = bool(edges) and area < CUT_MAX_AREA
        where, thirds, centre = placement((x0 + x1) / 2, (y0 + y1) / 2)
        elsewhere = sharpest_tile(gray, px)
        frame["blur"] = _r(elsewhere)
        subject = {"box": best.get("id"), "kind": best.get("kind"), "sharpness": q["sharpness"],
                   "exposure": q["exposure"], "blur": _r(sb), "clip_high": _r(high), "clip_low": _r(low),
                   "area": _r(area, 6), "edges": edges, "cut": cut, "placement": where, "thirds_dist": _r(thirds),
                   "centre_dist": _r(centre)}
        if sb is not None and sb >= SOFT_BLUR:
            reasons.append("motion_or_defocus" if elsewhere is not None and elsewhere >= SOFT_BLUR else "soft_subject")
        reasons += exposure_reasons(frame, subject)
        if cut:
            reasons.append("subject_cut")
        if area < TOO_SMALL:
            reasons.append("subject_too_small")
    else:
        sharpest = sharpest_tile(gray, None)
        frame["blur"] = _r(sharpest)
        if sharpest is not None and sharpest >= SOFT_BLUR:
            reasons.append("motion_or_defocus")
        reasons += exposure_reasons(frame, None)
        if gate and sum(gate.get(k, 0.0) for k in ANIMALS) >= NO_SUBJECT_GATE:
            reasons.append("no_subject")
    frame["clip_high"], frame["clip_low"] = _r(frame["clip_high"]), _r(frame["clip_low"])
    frame = {k: frame[k] for k in ("sharpness", "exposure", "blur", "clip_high", "clip_low")}
    return {"frame": frame, "subject": subject, "reject_reasons": reasons,
            "capture": {"taken_at": taken_at, "camera": camera}}


class Quality(StageBase):
    def settings(self) -> dict[str, Any]:
        """Every UPPER_CASE number here (the fingerprint in result.engine.plugins["quality"])."""
        return {k: v for k, v in globals().items() if k.isupper() and isinstance(v, (int, float, list))
                and not isinstance(v, bool)}

    def run(self, engine: Any, items: list[Item], o: dict[str, Any]) -> list[Any]:
        return each(items, lambda it: assess(it.dec.image, it.facts.get("boxes") or [], it.gate, it.taken_at,
                                             getattr(it.dec, "camera", None)))


STAGE = Quality()
