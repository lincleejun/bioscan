"""The three products (identify / embed / jpg): option defaults, the /products schema, and the
model-free triage rules migrated from PhotoOS scan/triage.py + library/_curation.py."""
from __future__ import annotations

import base64
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from bioscan.service.adapters import geo as geo_mod
from bioscan.service.adapters.owlv2 import VOCAB, Detection
from bioscan.service.adapters.siglip2 import MODEL_NAME

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

DEFAULTS: dict[str, dict[str, Any]] = {
    "identify": {"top_k": 5, "geo": True, "species": True},
    "embed": {"format": "list"},
    "jpg": {"out_dir": "/tmp/bioscan-jpg"},
}

PRODUCTS: dict[str, Any] = {
    "identify": {
        "description": "Scene gate (SigLIP2), animal boxes (OWLv2 + crop gate), per-box quality and species "
                       "(BioCLIP 2.5 Huge zero-shot, optional BirdNET geo prior for birds).",
        "options": {"top_k": {"type": "integer", "minimum": 1, "maximum": 50, "default": 5},
                    "geo": {"type": "boolean", "default": True},
                    "species": {"type": "boolean", "default": True}},
        "output": {"gate": {"class": "bird|mammal|other_animal|person|none", "probs": "{class: float}"},
                   "boxes": [{"id": "int", "xyxy": "[x0,y0,x1,y1] normalised 0-1, upright image",
                              "score": "float", "kind": "bird|mammal|other_animal",
                              "quality": {"sharpness": "float", "exposure": "float, mean luma - 0.5"},
                              "species": "null | {list, level: species|genus|family|unconfirmed, "
                                         "top: [{scientific, common, taxonomy[7], p_visual, p_geo, posterior}]}"}]},
    },
    "embed": {
        "description": "SigLIP2 whole-frame image vector (same forward pass as the gate).",
        "options": {"format": {"type": "string", "enum": ["list", "f16_base64"], "default": "list"}},
        "output": {"model": "siglip2-base-patch16-224", "dim": 768, "vector": "list[float] | base64 float16 LE"},
    },
    "jpg": {
        "description": "Upright JPEG, long edge 2048, quality 92, written as <out_dir>/<stem>-<sha256[:8]>.jpg "
                       "(same source bytes -> same file; same-named sources never collide).",
        "options": {"out_dir": {"type": "string", "format": "absolute path", "default": DEFAULTS["jpg"]["out_dir"]}},
        "output": {"path": "string", "width": "int", "height": "int"},
    },
}


def resolve_options(options: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Defaults merged with the request; raises ValueError on a bad value."""
    options = options or {}
    if not isinstance(options, dict):
        raise ValueError("options must be an object")
    out = {}
    for name, defaults in DEFAULTS.items():
        given = options.get(name) or {}
        if not isinstance(given, dict):
            raise ValueError(f"options.{name} must be an object")
        unknown = set(given) - set(defaults)
        if unknown:
            raise ValueError(f"unknown options.{name}: {sorted(unknown)}")
        out[name] = {**defaults, **given}
    unknown = set(options) - set(DEFAULTS)
    if unknown:
        raise ValueError(f"unknown option groups: {sorted(unknown)}")
    top_k = out["identify"]["top_k"]
    if isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= 50:
        raise ValueError("options.identify.top_k must be an integer 1-50")
    for key in ("geo", "species"):
        if not isinstance(out["identify"][key], bool):
            raise ValueError(f"options.identify.{key} must be a boolean")
    if out["embed"]["format"] not in ("list", "f16_base64"):
        raise ValueError("options.embed.format must be list or f16_base64")
    if not isinstance(out["jpg"]["out_dir"], str) or not Path(out["jpg"]["out_dir"]).is_absolute():
        raise ValueError("options.jpg.out_dir must be an absolute path")
    return out


# ---- boxes -------------------------------------------------------------------------------

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
    if winner in ("none", "person") and crop_gate[winner] >= VETO:
        return None
    if kind != "bird" and crop_gate.get("bird", 0.0) >= BIRD_PROMOTE:
        return "bird"
    if kind != "bird" and crop_gate.get("mammal", 0.0) + crop_gate.get("other_animal", 0.0) < MAMMAL_SUPPORT:
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


def quality(image: Image.Image, bbox: tuple[float, ...]) -> dict[str, float]:
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
        return {"sharpness": 0.0, "exposure": exposure}
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
    return {"sharpness": round(float(strongest.mean()) / amplitude, 4), "exposure": exposure}


# ---- identify ----------------------------------------------------------------------------

def _detect(engine: Any, image: Image.Image, vocab: dict[str, float], second_pass: bool) -> list[Detection]:
    floor = SECOND_PASS_FLOOR if second_pass else min(vocab.values())
    dets = engine.owlv2.detect(image, list(vocab), threshold=floor)
    return dets if second_pass else [d for d in dets if d.confidence >= vocab[d.prompt]]


def _judged(engine: Any, image: Image.Image, dets: list[Detection], kind: str) -> list[tuple[Detection, str]]:
    if not dets:
        return []
    crops = [crop_with_context(image, d.bbox) for d in dets]
    gates = engine.siglip2.gate(engine.siglip2.embed_images(crops))
    return [(d, k) for d, g in zip(dets, gates) if (k := judge(kind, g)) is not None]


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


def _species(engine: Any, image: Image.Image, boxes: list[dict[str, Any]], bboxes: list[tuple[float, ...]],
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
        index = engine.geo_index.get(kind)
        if opts["geo"] and index is not None and engine.geo is not None and lat is not None and lon is not None:
            try:
                p_geo = geo_mod.align(engine.geo.probs(lat, lon, geo_mod.week_of(taken_at)), index)
            except Exception:  # noqa: BLE001 - an optional prior must not discard the visual result
                p_geo = None
        for start in range(0, len(idx), engine.BIOCLIP_BATCH):
            part = idx[start:start + engine.BIOCLIP_BATCH]
            feats = engine.bioclip.encode_images(species_crops(image, [bboxes[i] for i in part], detail))
            probs = engine.bioclip.probs(feats, engine.name_matrix(kind))
            for i, row in zip(part, probs):
                row = np.asarray(row, dtype=np.float64)
                post = geo_mod.posterior(row, p_geo)
                top = [
                    {"scientific": names.scientific[j], "common": names.common[j] or None,
                     "taxonomy": list(names.taxonomy[j]), "p_visual": round(float(row[j]), 6),
                     "p_geo": None if p_geo is None else round(float(p_geo[j]), 6),
                     "posterior": round(float(post[j]), 6)}
                    for j in np.argsort(-post, kind="stable")[:opts["top_k"]]]
                boxes[i]["species"] = {"list": names.list_id, "level": species_level(top), "top": top}


def identify(engine: Any, image: Image.Image, gate: dict[str, float], lat: float | None, lon: float | None,
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


# ---- embed / jpg -------------------------------------------------------------------------

def embed(vec: np.ndarray, fmt: str) -> dict[str, Any]:
    v = np.asarray(vec, dtype=np.float32)
    vector: Any = ([round(float(x), 6) for x in v] if fmt == "list"
                   else base64.b64encode(v.astype("<f2").tobytes()).decode("ascii"))
    return {"model": MODEL_NAME, "dim": int(v.shape[0]), "vector": vector}


def jpg(image: Image.Image, src_path: str, out_dir: str, sha256: str) -> dict[str, Any]:
    """`<stem>-<sha8>.jpg`: DSC0001.ARW from two cards, or a RAW+JPG pair, get two files; a re-run
    rewrites the same one."""
    target = Path(out_dir) / f"{Path(src_path).stem}-{sha256[:8]}.jpg"
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target, "JPEG", quality=92)
    return {"path": str(target), "width": image.width, "height": image.height}
