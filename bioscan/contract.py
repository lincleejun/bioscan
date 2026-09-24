"""The /run wire format, in one place: product names (from the plugin manifests), event types, required fields, schema version,
and the identify payload (gate, boxes, quality, species, candidates).

Standard library only. The service builds its events and its identify output with the constructors
below; the CLI (render, eval) reads them through the readers below; `missing_fields` and
`identify_problems` are what the contract tests check every emitted event and identify payload
against. Bump SCHEMA when a field is removed or changes meaning (adding one is not a bump).
"""
from __future__ import annotations

from typing import Any, Literal, TypedDict

SCHEMA = 1
# PRODUCTS: the product names, in the order they are reported, derived from bioscan.plugins.BUILTIN
# (module __getattr__ below: the plugin manifests import this module).

PROGRESS, RESULT, ERROR, DONE = "progress", "result", "error", "done"
EventType = Literal["progress", "result", "error", "done"]


class ProgressEvent(TypedDict):
    type: Literal["progress"]
    product: str
    done: int
    total: int


class ErrorEvent(TypedDict):
    type: Literal["error"]
    path: str
    product: str | None          # None = decode failed
    message: str


class ResultEvent(TypedDict):
    type: Literal["result"]
    schema: int
    path: str
    sha256: str
    image: dict[str, int]        # width, height, orientation
    engine: dict[str, Any]       # version, settings, models, detail_edge
    products: dict[str, Any]     # product name -> that product's output
    timing_ms: dict[str, float]


class DoneEvent(TypedDict):
    type: Literal["done"]
    schema: int
    ok: int
    failed: int
    elapsed_ms: float


def __getattr__(name: str) -> Any:
    if name == "PRODUCTS":
        from bioscan.plugins import BUILTIN

        return tuple(m.name for m in BUILTIN)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


REQUIRED: dict[str, tuple[str, ...]] = {
    t: tuple(k for k in cls.__annotations__) for t, cls in
    ((PROGRESS, ProgressEvent), (RESULT, ResultEvent), (ERROR, ErrorEvent), (DONE, DoneEvent))
}


def progress(product: str, done: int, total: int) -> ProgressEvent:
    return {"type": PROGRESS, "product": product, "done": done, "total": total}


def error(path: str, product: str | None, message: str) -> ErrorEvent:
    return {"type": ERROR, "path": path, "product": product, "message": message}


def result(path: str, sha256: str, image: dict[str, int], engine: dict[str, Any], products: dict[str, Any],
           timing_ms: dict[str, float]) -> ResultEvent:
    return {"type": RESULT, "schema": SCHEMA, "path": path, "sha256": sha256, "image": image, "engine": engine,
            "products": products, "timing_ms": timing_ms}


def done(ok: int, failed: int, elapsed_ms: float) -> DoneEvent:
    return {"type": DONE, "schema": SCHEMA, "ok": ok, "failed": failed, "elapsed_ms": elapsed_ms}


def missing_fields(ev: dict[str, Any]) -> list[str]:
    """Required fields absent from one event; ['type'] for an unknown type."""
    need = REQUIRED.get(ev.get("type"))  # type: ignore[arg-type]
    return ["type"] if need is None else [k for k in need if k not in ev]


# ---- identify: result.products.identify ---------------------------------------------------
# The constructors only assemble: values arrive already rounded (gate probs 4 places, xyxy 5,
# score 4, candidate probabilities 6), and key order is the order of the fields below.

Level = Literal["species", "genus", "family", "unconfirmed"]

Gate = TypedDict("Gate", {"class": str, "probs": dict[str, float]})   # "class" is a keyword


class Quality(TypedDict):
    sharpness: float
    exposure: float              # mean luma - 0.5


class Candidate(TypedDict):
    scientific: str
    common: str | None
    taxonomy: list[str]          # 7 ranks: kingdom phylum class order family genus species
    p_visual: float
    p_geo: float | None          # None = no location prior applied
    posterior: float


class Species(TypedDict):
    list: str                    # name list id: avilist-2025 | mdd-2025 | tol200m-animalia
    level: Level
    top: list[Candidate]         # by posterior, highest first; a range-vetoed first may cede to a congener


class _BoxFields(TypedDict):
    id: int
    xyxy: list[float]            # normalised 0-1, upright image
    score: float
    kind: str                    # bird | mammal | other_animal (the kind check or candidates may move it)
    quality: Quality


class Box(_BoxFields, total=False):
    species: Species | None      # absent when species is off; None for a kind with no name list


class Identify(TypedDict):
    gate: Gate
    boxes: list[Box]             # by score, highest first


def gate(cls: str, probs: dict[str, float]) -> Gate:
    return {"class": cls, "probs": probs}


def quality(sharpness: float, exposure: float) -> Quality:
    return {"sharpness": sharpness, "exposure": exposure}


def candidate(scientific: str, common: str | None, taxonomy: list[str], p_visual: float, p_geo: float | None,
              posterior: float) -> Candidate:
    return {"scientific": scientific, "common": common, "taxonomy": taxonomy, "p_visual": p_visual, "p_geo": p_geo,
            "posterior": posterior}


def species(list_id: str, level: Level, top: list[Candidate]) -> Species:
    return {"list": list_id, "level": level, "top": top}


def box(box_id: int, xyxy: list[float], score: float, kind: str, quality: Quality) -> Box:
    """Without "species": identify fills it in afterwards, and only when species is on."""
    return {"id": box_id, "xyxy": xyxy, "score": score, "kind": kind, "quality": quality}


def identify(gate: Gate, boxes: list[Box]) -> Identify:
    return {"gate": gate, "boxes": boxes}


# Readers, for the CLI. They accept partial or older payloads the way the renderer and eval always
# have: a missing product, gate, list or species reads as None / [].

def identify_of(ev: dict[str, Any]) -> dict[str, Any] | None:
    """A result event's identify output; None when identify was not requested."""
    return (ev.get("products") or {}).get("identify")


def gate_class_of(ident: dict[str, Any] | None) -> str | None:
    return ((ident or {}).get("gate") or {}).get("class")


def boxes_of(ident: dict[str, Any] | None) -> list[dict[str, Any]]:
    return (ident or {}).get("boxes") or []


def species_of(box: dict[str, Any]) -> dict[str, Any] | None:
    """None both when species was off (absent) and for a kind with no name list (null)."""
    return box.get("species")


def level_of(sp: dict[str, Any] | None) -> str | None:
    return (sp or {}).get("level")


def top_of(sp: dict[str, Any] | None) -> list[dict[str, Any]]:
    return (sp or {}).get("top") or []


def _problems(obj: Any, cls: Any, where: str) -> list[str]:
    if not isinstance(obj, dict):
        return [f"{where.rstrip('.') or 'identify'}: not an object"]
    return ([f"missing {where}{k}" for k in cls.__required_keys__ if k not in obj]
            + [f"unexpected {where}{k}" for k in obj if k not in cls.__annotations__])


def identify_problems(out: Any) -> list[str]:
    """Every way one identify output departs from the fields above (missing, unexpected, not an
    object); [] when it conforms. Checks names and nesting, not value types."""
    found = _problems(out, Identify, "")
    if found and not isinstance(out, dict):
        return found
    if "gate" in out:
        found += _problems(out["gate"], Gate, "gate.")
    boxes = out.get("boxes")
    if "boxes" in out and not isinstance(boxes, list):
        return found + ["boxes: not a list"]
    for i, b in enumerate(boxes or []):
        where = f"boxes[{i}]."
        found += _problems(b, Box, where)
        if not isinstance(b, dict):
            continue
        if "quality" in b:
            found += _problems(b["quality"], Quality, where + "quality.")
        sp = b.get("species")
        if sp is None:
            continue
        found += _problems(sp, Species, where + "species.")
        if isinstance(sp, dict):
            found += [p for j, c in enumerate(sp.get("top") or []) for p in _problems(c, Candidate, f"{where}species.top[{j}].")]
    return found


# What GET /products serves as identify's "output" (a description for people, kept next to the
# fields it describes; tests/unit/test_identify_contract.py holds the two together).
IDENTIFY_OUTPUT: dict[str, Any] = {
    "gate": {"class": "bird|mammal|other_animal|person|none", "probs": "{class: float}"},
    "boxes": [{"id": "int", "xyxy": "[x0,y0,x1,y1] normalised 0-1, upright image",
               "score": "float", "kind": "bird|mammal|other_animal",
               "quality": {"sharpness": "float", "exposure": "float, mean luma - 0.5"},
               "species": "null | {list, level: species|genus|family|unconfirmed, "
                          "top: [{scientific, common, taxonomy[7], p_visual, p_geo, posterior}]}"}]}
