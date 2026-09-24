"""scene as a stage: zero-shot labels from the frame vector, on the model thread (the text encoder
runs once per label set; each chunk is then one matrix product), plus horizon tilt for landscapes."""
from __future__ import annotations

import math
import threading
from typing import Any

import numpy as np
from PIL import Image

from bioscan.plugin import Item, StageBase, each
from bioscan.plugins.scene import LANDSCAPE
from bioscan.service.adapters.siglip2 import MODEL_NAME

LOGIT_SCALE = 100.0          # when the text model reports none (SigLIP2's learned scale is used when it does)
HORIZON_EDGE = 256           # the Hough runs on a copy this long
HORIZON_MAX_DEG = 15.0       # near-horizontal lines only: tilts within +-this
HORIZON_STEP_DEG = 0.25
HORIZON_EDGES = 0.9          # strongest 10 % of vertical gradients vote
HORIZON_MIN_STRENGTH = 0.1   # the best line's share of the vote weight; below: no horizon


def text_matrix(model: Any, labels: dict[str, list[str]]) -> np.ndarray:
    """One unit row per label with prompts: the mean of its prompts' text vectors."""
    rows = []
    for prompts in labels.values():
        if prompts:
            m = np.asarray(model.embed_texts(list(prompts)), dtype=np.float32).mean(axis=0)
            rows.append(m / (np.linalg.norm(m) or 1.0))
    return np.stack(rows)


def scores(vec: np.ndarray, gate: dict[str, float] | None, labels: dict[str, list[str]], matrix: np.ndarray,
           scale: float, gate_classes: list[str]) -> dict[str, float]:
    """{label: score} summing to 1: the gate label (no prompts) gets the gate's share of
    `gate_classes`, the text labels a softmax over their similarity of the rest."""
    z = scale * (np.asarray(vec, dtype=np.float32) @ matrix.T)
    p = np.exp(z - z.max())
    p = p / p.sum()
    gated = [k for k, v in labels.items() if not v]
    share = min(1.0, max(0.0, sum((gate or {}).get(c, 0.0) for c in gate_classes))) if gated else 0.0
    it = iter(p)
    return {k: (share if not v else (1.0 - share) * float(next(it))) for k, v in labels.items()}


def horizon(image: Image.Image) -> dict[str, float] | None:
    """The dominant near-horizontal straight line: a Hough transform over the strongest vertical
    gradients of a small grey copy, angles within +-HORIZON_MAX_DEG. None when no line holds
    HORIZON_MIN_STRENGTH of the vote."""
    s = HORIZON_EDGE / max(image.size)
    small = image.convert("L").resize((max(8, round(image.width * s)), max(8, round(image.height * s))),
                                      Image.Resampling.BILINEAR)
    g = np.asarray(small, dtype=np.float32)
    gy = g[2:, 1:-1] - g[:-2, 1:-1]
    gx = g[1:-1, 2:] - g[1:-1, :-2]
    mag = np.hypot(gx, gy)
    vertical = np.abs(gy) > 2 * np.abs(gx)                     # an edge across a near-horizontal line
    if not vertical.any():
        return None
    cut = np.quantile(mag[vertical], HORIZON_EDGES)
    ys, xs = np.nonzero(vertical & (mag >= cut) & (mag > 0))
    if len(ys) < 10:
        return None
    w = mag[ys, xs]
    xs = xs - g.shape[1] / 2
    total = float(w.sum())
    best, best_t = 0.0, 0.0
    for t in np.arange(-HORIZON_MAX_DEG, HORIZON_MAX_DEG + 1e-9, HORIZON_STEP_DEG):
        r = np.radians(t)
        # the line y = c + x * tan(t) through each point: its intercept c, in 1 px bins
        c = np.round(ys - xs * math.tan(r)).astype(np.int64)
        c -= c.min()
        votes = np.bincount(c, weights=w)
        # a line is 2 px wide on the small copy: sum neighbouring bins
        peak = float((votes[:-1] + votes[1:]).max()) if len(votes) > 1 else float(votes.max())
        if peak > best:
            best, best_t = peak, float(t)
    strength = best / total if total else 0.0
    if strength < HORIZON_MIN_STRENGTH:
        return None
    return {"tilt_deg": round(-best_t, 2), "strength": round(strength, 4)}   # image y grows downwards


class Scene(StageBase):
    def __init__(self) -> None:
        self._text: dict[tuple, np.ndarray] = {}
        self._lock = threading.Lock()

    def settings(self) -> dict[str, Any]:
        return {"model": MODEL_NAME, **{k: v for k, v in globals().items() if k.isupper()
                                        and isinstance(v, (int, float)) and not isinstance(v, bool)}}

    def matrix(self, engine: Any, labels: dict[str, list[str]]) -> np.ndarray:
        key = (id(engine.siglip2), tuple((k, tuple(v)) for k, v in labels.items()))
        with self._lock:
            if key not in self._text:
                self._text[key] = text_matrix(engine.siglip2, labels)
            return self._text[key]

    def run(self, engine: Any, items: list[Item], o: dict[str, Any]) -> list[Any]:
        labels = o["labels"]
        matrix = self.matrix(engine, labels)
        scale = float(getattr(engine.siglip2, "logit_scale", None) or LOGIT_SCALE)

        def one(it: Item) -> dict[str, Any]:
            sc = scores(it.vec, it.gate, labels, matrix, scale, o["wildlife_gate"])
            top = max(sc, key=lambda k: sc[k])
            it.facts["scene"] = top
            return {"label": top, "scores": {k: round(v, 4) for k, v in sc.items()},
                    "horizon": horizon(it.dec.image) if top == LANDSCAPE else None}
        return each(items, one)


STAGE = Scene()
