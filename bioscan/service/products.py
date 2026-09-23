"""The products (identify / embed / jpg) as a registry: what each needs loaded, whether it uses the
whole-frame SigLIP2 pass, its options (defaults, validation, /products schema), how it runs over a
chunk of decoded images and which paths it writes. app.py and engine.py derive everything from
REGISTRY; a new product is one entry here plus its name in bioscan.contract.PRODUCTS."""
from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from bioscan import contract
from bioscan.service.adapters.siglip2 import MODEL_NAME
from bioscan.service.pipeline import Frame, _species, identify  # noqa: F401 - re-exported
from bioscan.service.rules import *  # noqa: F403 - rules and thresholds re-exported for callers of products.X
from bioscan.service.rules import MIN_CROP, crop_with_context, dedupe, judge, quality, species_crops  # noqa: F401

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


def _check_identify(o: dict[str, Any]) -> None:
    top_k = o["top_k"]
    if isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= 50:
        raise ValueError("options.identify.top_k must be an integer 1-50")
    for key in ("geo", "species"):
        if not isinstance(o[key], bool):
            raise ValueError(f"options.identify.{key} must be a boolean")


def _check_embed(o: dict[str, Any]) -> None:
    if o["format"] not in ("list", "f16_base64"):
        raise ValueError("options.embed.format must be list or f16_base64")


def _check_jpg(o: dict[str, Any]) -> None:
    if not isinstance(o["out_dir"], str) or not Path(o["out_dir"]).is_absolute():
        raise ValueError("options.jpg.out_dir must be an absolute path")


def resolve_options(options: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Defaults merged with the request; raises ValueError on a bad value."""
    options = options or {}
    if not isinstance(options, dict):
        raise ValueError("options must be an object")
    out = {}
    for name, product in REGISTRY.items():
        given = options.get(name) or {}
        if not isinstance(given, dict):
            raise ValueError(f"options.{name} must be an object")
        unknown = set(given) - set(product.defaults)
        if unknown:
            raise ValueError(f"unknown options.{name}: {sorted(unknown)}")
        out[name] = {**product.defaults, **given}
    unknown = set(options) - set(REGISTRY)
    if unknown:
        raise ValueError(f"unknown option groups: {sorted(unknown)}")
    for name, product in REGISTRY.items():
        product.check(out[name])
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


# ---- registry ----------------------------------------------------------------------------

@dataclass(frozen=True)
class Item:
    """One decoded image on its way through a chunk (what a product runner sees)."""
    dec: Any                        # decode.Decoded
    inp: dict[str, Any]             # the request's input: path, lat, lon, taken_at
    vec: Any = None                 # whole-frame SigLIP2 vector (frame products only)
    gate: dict[str, float] | None = None

    @property
    def lat(self) -> float | None:
        return self.inp["lat"] if self.inp["lat"] is not None else self.dec.lat

    @property
    def lon(self) -> float | None:
        return self.inp["lon"] if self.inp["lon"] is not None else self.dec.lon

    @property
    def taken_at(self) -> str | None:
        return self.inp["taken_at"] or self.dec.taken_at


Runner = Callable[[Any, list[Item], dict[str, Any]], list[Any]]   # (engine, items, opts) -> output | Exception


def _each(fn: Callable[[Any, Item, dict[str, Any]], Any]) -> Runner:
    """A runner that calls `fn` per item; an exception costs that item only."""
    def run(engine: Any, items: list[Item], opts: dict[str, Any]) -> list[Any]:
        out: list[Any] = []
        for it in items:
            try:
                out.append(fn(engine, it, opts))
            except Exception as exc:  # noqa: BLE001 - reported per image by app.py
                out.append(exc)
        return out
    return run


@dataclass(frozen=True)
class Product:
    name: str
    needs: tuple[str, ...]           # Engine.MODELS names
    uses_frame: bool                 # needs the whole-frame SigLIP2 pass (vector + gate)
    on_model_thread: bool            # runs on the single model thread (else a CPU thread)
    defaults: dict[str, Any]
    check: Callable[[dict[str, Any]], None]
    schema: dict[str, Any]           # served by GET /products
    run: Runner
    writes: Callable[[dict[str, Any]], list[str]] = lambda opts: []   # paths checked against allow-roots


REGISTRY: dict[str, Product] = {
    "identify": Product(
        "identify", ("siglip2", "owlv2", "bioclip"), True, True, DEFAULTS["identify"], _check_identify,
        PRODUCTS["identify"],
        lambda e, items, o: e.identify_many([Frame(it.dec.image, it.gate, it.lat, it.lon, it.taken_at, it.dec.detail)
                                             for it in items], o)),
    "embed": Product(
        "embed", ("siglip2",), True, True, DEFAULTS["embed"], _check_embed, PRODUCTS["embed"],
        _each(lambda e, it, o: embed(it.vec, o["format"]))),
    "jpg": Product(
        "jpg", (), False, False, DEFAULTS["jpg"], _check_jpg, PRODUCTS["jpg"],
        _each(lambda e, it, o: jpg(it.dec.image, it.dec.path, o["out_dir"], it.dec.sha256)),
        writes=lambda o: [o["out_dir"]]),
}
assert tuple(REGISTRY) == contract.PRODUCTS, "products.REGISTRY and contract.PRODUCTS must list the same products"
