"""The /run wire format, in one place: product names, event types, required fields, schema version.

Standard library only. The service builds its events with the constructors below; the CLI
(render, eval) reads them with the same names; `missing_fields` is what the contract tests check
every emitted event against. Bump SCHEMA when a field is removed or changes meaning (adding one is
not a bump).
"""
from __future__ import annotations

from typing import Any, Literal, TypedDict

SCHEMA = 1
PRODUCTS = ("identify", "embed", "jpg")            # also the order products run in

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
