"""identify, orchestrated: gate -> detector -> crop gate -> species (+ location prior).

Talks to the models only through `Models` (what Engine provides), and to the rules in rules.py.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Protocol

import numpy as np
from PIL import Image

from bioscan.service.adapters import geo as geo_mod
from bioscan.service.adapters.owlv2 import Detection
from bioscan.service.rules import (
    RESCUE,
    SECOND_PASS_FLOOR,
    SECOND_PASS_TOP,
    crop_with_context,
    dedupe,
    judge,
    quality,
    species_crops,
    species_level,
)
from bioscan.service.taxa import VOCAB


class Models(Protocol):
    """The seam between identify and the models: detector, crop gate, species encoder, name lists
    and location priors per kind. Engine implements it; unit tests pass small stand-ins."""

    owlv2: Any                      # .detect(image, prompts, threshold=) -> list[Detection]
    siglip2: Any                    # .embed_images(images) -> vecs; .gate(vecs) -> [{class: p}]
    bioclip: Any                    # .encode_images(crops) -> feats; .probs(feats, matrix) -> (n, N)
    names: dict[str, Any]           # kind -> names.NameList
    priors: dict[str, Any]          # kind -> geo.PriorBinding (absent = no prior for that kind)
    BIOCLIP_BATCH: int

    def name_matrix(self, kind: str) -> Any: ...


def _detect(engine: Models, image: Image.Image, vocab: dict[str, float], second_pass: bool) -> list[Detection]:
    floor = SECOND_PASS_FLOOR if second_pass else min(vocab.values())
    dets = engine.owlv2.detect(image, list(vocab), threshold=floor)
    return dets if second_pass else [d for d in dets if d.confidence >= vocab[d.prompt]]


def _judged(engine: Models, image: Image.Image, dets: list[Detection], kind: str) -> list[tuple[Detection, str]]:
    if not dets:
        return []
    crops = [crop_with_context(image, d.bbox) for d in dets]
    gates = engine.siglip2.gate(engine.siglip2.embed_images(crops))
    return [(d, k) for d, g in zip(dets, gates) if (k := judge(kind, g)) is not None]


def _species(engine: Models, image: Image.Image, boxes: list[dict[str, Any]], bboxes: list[tuple[float, ...]],
             lat: float | None, lon: float | None, taken_at: str | None, opts: dict[str, Any],
             detail: Image.Image | None = None) -> None:
    """Fills boxes[i]["species"] in place; one BioCLIP batch per name list."""
    for b in boxes:
        b["species"] = None
    by_kind: dict[str, list[int]] = defaultdict(list)
    for i, b in enumerate(boxes):
        if b["kind"] in engine.names:
            by_kind[b["kind"]].append(i)
    for kind, idx in by_kind.items():
        names = engine.names[kind]
        p_geo = None
        prior = engine.priors.get(kind)
        if opts["geo"] and prior is not None and lat is not None and lon is not None:
            try:
                p_geo = geo_mod.align(prior.model.probs(lat, lon, geo_mod.week_of(taken_at)), prior.index)
            except Exception:  # noqa: BLE001 - an optional prior must not discard the visual result
                p_geo = None
        for start in range(0, len(idx), engine.BIOCLIP_BATCH):
            part = idx[start:start + engine.BIOCLIP_BATCH]
            feats = engine.bioclip.encode_images(species_crops(image, [bboxes[i] for i in part], detail))
            probs = engine.bioclip.probs(feats, engine.name_matrix(kind))
            for i, row in zip(part, probs):
                row = np.asarray(row, dtype=np.float64)
                post = geo_mod.posterior(row, p_geo, prior.floor if prior is not None else geo_mod.GEO_FLOOR)
                top = [
                    {"scientific": names.scientific[j], "common": names.common[j] or None,
                     "taxonomy": list(names.taxonomy[j]), "p_visual": round(float(row[j]), 6),
                     "p_geo": None if p_geo is None else round(float(p_geo[j]), 6),
                     "posterior": round(float(post[j]), 6)}
                    for j in np.argsort(-post, kind="stable")[:opts["top_k"]]]
                boxes[i]["species"] = {"list": names.list_id, "level": species_level(top), "top": top}


def identify(engine: Models, image: Image.Image, gate: dict[str, float], lat: float | None, lon: float | None,
             taken_at: str | None, opts: dict[str, Any], detail: Image.Image | None = None) -> dict[str, Any]:
    """Gate, boxes and quality on the 2048 px `image`; species crops from `detail` when given."""
    cls = max(gate, key=lambda k: gate[k])
    out = {"gate": {"class": cls, "probs": {k: round(v, 4) for k, v in gate.items()}}, "boxes": []}
    rescue = cls not in VOCAB
    if rescue:
        # A bear at night or a bobcat in brush can lose the whole-frame vote to "none" while the
        # animal classes together still hold real mass: look with the strongest animal's words.
        # The reported gate class stays what the gate said.
        if sum(gate.get(k, 0.0) for k in VOCAB) < RESCUE:
            return out
        cls = max(VOCAB, key=lambda k: gate.get(k, 0.0))
    vocab = VOCAB[cls]
    kept = _judged(engine, image, dedupe(_detect(engine, image, vocab, False)), cls)
    if not kept and not rescue:
        # The gate says an animal is there and the detector boxed none: floor 0.1, best three,
        # the crop gate still decides.
        kept = _judged(engine, image, dedupe(_detect(engine, image, vocab, True))[:SECOND_PASS_TOP], cls)
    kept.sort(key=lambda dk: -dk[0].confidence)
    w, h = image.size
    boxes = []
    for i, (d, kind) in enumerate(kept):
        x0, y0, x1, y1 = d.bbox
        boxes.append({"id": i, "xyxy": [round(x0 / w, 5), round(y0 / h, 5), round(x1 / w, 5), round(y1 / h, 5)],
                      "score": round(d.confidence, 4), "kind": kind, "quality": quality(image, d.bbox)})
    if opts["species"]:
        _species(engine, image, boxes, [d.bbox for d, _ in kept], lat, lon, taken_at, opts, detail)
    out["boxes"] = boxes
    return out
