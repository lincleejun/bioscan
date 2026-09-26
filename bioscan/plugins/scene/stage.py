"""scene as a stage: zero-shot labels from the frame vector, on the model thread (the text encoder
runs once per label set; each chunk is then one matrix product), plus horizon tilt for landscapes."""
from __future__ import annotations

import math
import threading
from typing import Any

import numpy as np
from PIL import Image

from bioscan.plugin import Item, StageBase, each
from bioscan.plugins.scene import LANDSCAPE, MANIFEST
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
           scale: float, gate_classes: list[str], boxes: list | None = None,
           gated: list[str] | None = None) -> dict[str, float]:
    """{label: score} summing to 1. The `gated` labels (default: the one without prompts) share the
    gate's probability of `gate_classes`: one prompt-less label takes all of it, prompted ones split it
    by their own softmax. The other labels split the rest by one softmax over their similarity.
    `boxes` is identify's list when it ran (an empty list means it found nothing: the share is 0),
    None when it did not (the gate share stands)."""
    z = scale * (np.asarray(vec, dtype=np.float32) @ matrix.T)
    row = {k: i for i, k in enumerate(k for k, v in labels.items() if v)}
    gated = [k for k, v in labels.items() if not v] if gated is None else gated
    share = min(1.0, max(0.0, sum((gate or {}).get(c, 0.0) for c in gate_classes))) if gated else 0.0
    if boxes is not None and not boxes:
        share = 0.0
    rest = [k for k in labels if k not in gated]
    p = dict(zip(rest, softmax(z, [row[k] for k in rest])))
    if gated and all(labels[k] for k in gated):                  # prompted gate labels: their own softmax
        p.update(zip(gated, (share * float(v) for v in softmax(z, [row[k] for k in gated]))))
    else:
        p.update(dict.fromkeys(gated, share))
    return {k: p[k] if k in gated else (1.0 - share) * float(p[k]) for k in labels}


def softmax(z: np.ndarray, rows: list[int]) -> np.ndarray:
    """The softmax of z[rows], in their order."""
    if not rows:
        return z[:0]
    p = np.exp(z[rows] - z[rows].max())
    return p / p.sum()


def wildlife_label(inner: dict[str, float], boxes: list | None, rules: dict[str, Any]) -> str:
    """The gate group's main label (proposal §3.3), from its in-group softmax `inner` and identify's
    boxes, each rule only when the gate group has its label: herd_flock at flock_boxes boxes or
    more; bird_flight or domestic when it tops the softmax above 0.5; the best box's kind x area
    (bird/mammal _portrait at portrait_area of the frame or more, else _habitat; other_animal);
    else the softmax top."""
    top = max(inner, key=lambda k: inner[k])
    if "herd_flock" in inner and boxes and len(boxes) >= rules["flock_boxes"]:
        return "herd_flock"
    if top in ("bird_flight", "domestic") and inner[top] > 0.5:
        return top
    if boxes:
        best = max(boxes, key=lambda b: b.get("score", 0))
        x0, y0, x1, y1 = best["xyxy"]                      # normalised 0-1
        area = max(0.0, x1 - x0) * max(0.0, y1 - y0)
        kind = best.get("kind")
        name = f"{kind}_{'portrait' if area >= rules['portrait_area'] else 'habitat'}" \
            if kind in ("bird", "mammal") else kind
        if name in inner:
            return name
    return top


def grouped(sc: dict[str, float], groups: dict[str, list[str]], gate_group: str, boxes: list | None,
            rules: dict[str, Any]) -> tuple[str, str, dict[str, float]]:
    """(label, group, {group: score}): the top group by the sum of its labels' scores; its main
    label is the gate group's rule label, else its top label."""
    gs = {g: sum(sc[k] for k in members) for g, members in groups.items()}
    group = max(gs, key=lambda g: gs[g])
    members = groups[group]
    if group == gate_group:
        share = gs[group] or 1.0
        return wildlife_label({k: sc[k] / share for k in members}, boxes, rules), group, gs
    return max(members, key=lambda k: sc[k]), group, gs


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
        """The constants here and the default labels and gate classes (a request that sets its own
        sends them as options, which the preds meta line records)."""
        return {"model": MODEL_NAME, "defaults": MANIFEST.defaults,
                **{k: v for k, v in globals().items() if k.isupper() and isinstance(v, (int, float))
                   and not isinstance(v, bool)}}

    def matrix(self, engine: Any, labels: dict[str, list[str]]) -> np.ndarray:
        key = (id(engine.siglip2), tuple((k, tuple(v)) for k, v in labels.items()))
        with self._lock:
            if key not in self._text:
                self._text[key] = text_matrix(engine.siglip2, labels)
            return self._text[key]

    def run(self, engine: Any, items: list[Item], o: dict[str, Any]) -> list[Any]:
        labels, groups = o["labels"], o["groups"]
        matrix = self.matrix(engine, labels)
        attrs = {n: (values, self.matrix(engine, values)) for n, values in o["attributes"].items()}
        scale = float(getattr(engine.siglip2, "logit_scale", None) or LOGIT_SCALE)
        gated = groups[o["gate_group"]] if groups else None

        def one(it: Item) -> dict[str, Any]:
            boxes = it.facts.get("boxes")
            sc = scores(it.vec, it.gate, labels, matrix, scale, o["wildlife_gate"],
                        boxes if o["wildlife_box"] else None, gated)
            out: dict[str, Any] = {"label": max(sc, key=lambda k: sc[k])}
            if groups:
                out["label"], out["group"], gs = grouped(sc, groups, o["gate_group"], boxes, o["wildlife_rules"])
            out["scores"] = {k: round(v, 4) for k, v in sc.items()}
            if groups:
                out["group_scores"] = {k: round(v, 4) for k, v in gs.items()}
            if attrs:
                out["attributes"] = {}
                for n, (values, m) in attrs.items():
                    a = scores(it.vec, None, values, m, scale, [])
                    out["attributes"][n] = {"label": max(a, key=lambda k: a[k]),
                                            "scores": {k: round(v, 4) for k, v in a.items()}}
            group = out.get("group", out["label"])
            it.facts["scene"], it.facts["scene_group"] = out["label"], group
            out["horizon"] = horizon(it.dec.image) if group == LANDSCAPE else None
            return out
        return each(items, one)


STAGE = Scene()
