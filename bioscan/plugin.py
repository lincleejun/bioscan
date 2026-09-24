"""What a stage plugin is: a stdlib `Manifest` the CLI, profiles and /products read without loading
anything heavy, and a `Stage` (its `impl`, imported only when a run needs it) the service calls once
per chunk. `Item` is one decoded image on its way through a chunk, as a stage sees it.

Standard library only: the CLI imports this module. Design and migration steps:
docs/research/2026-09-24-plugin-architecture.md."""
from __future__ import annotations

import copy
import importlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol

FRAME_FACTS = ("vec", "gate")       # what the shared whole-frame SigLIP2 pass provides


@dataclass(frozen=True)
class Manifest:
    """What a plugin declares, importable without its dependencies."""
    name: str                                   # also the product key in result.products
    version: int                                # bump when the output's meaning changes
    description: str                            # served by GET /products
    reads: tuple[str, ...] = ()                 # facts it needs: image, detail, time, place, vec, gate, ...
    provides: tuple[str, ...] = ()              # facts it adds for later stages
    models: Callable[[dict[str, Any]], tuple[str, ...]] = lambda opts: ()   # model names under these options
    thread: Literal["model", "cpu"] = "cpu"     # the single model thread, or the CPU pool
    options: dict[str, Any] | None = None       # {name: JSON-schema-ish with "default"}, served by /products
    output: dict[str, Any] | None = None        # description of the output, served by /products
    impl: str = ""                              # "package.module:ATTR", the Stage, imported lazily

    @property
    def defaults(self) -> dict[str, Any]:
        """Each option's default, in declaration order (a fresh copy: callers may change it)."""
        return {k: copy.deepcopy(spec["default"]) for k, spec in (self.options or {}).items()}

    @property
    def uses_frame(self) -> bool:
        """Whether it needs the whole-frame SigLIP2 pass (the vector or the gate)."""
        return any(f in self.reads for f in FRAME_FACTS)

    def schema(self) -> dict[str, Any]:
        """Its entry in GET /products."""
        return {"description": self.description, "options": self.options or {}, "output": self.output or {}}


@dataclass(frozen=True)
class Item:
    """One decoded image on its way through a chunk (what a stage's `run` sees)."""
    dec: Any                        # decode.Decoded
    inp: dict[str, Any]             # the request's input: path, lat, lon, taken_at
    vec: Any = None                 # whole-frame SigLIP2 vector (stages that read "vec")
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


class Stage(Protocol):
    """The service side of a plugin. May import numpy / torch at module level."""

    def check(self, opts: dict[str, Any]) -> None: ...                      # ValueError -> 400
    def check_loaded(self, engine: Any, opts: dict[str, Any]) -> None: ...  # after the models load; ValueError -> 400
    def writes(self, opts: dict[str, Any]) -> list[str]: ...                # paths checked against allow-roots
    def reads_paths(self, opts: dict[str, Any]) -> list[str]: ...           # files it reads besides the inputs
    def settings(self) -> dict[str, Any]: ...                               # its output-changing constants
    def run(self, engine: Any, items: list[Item], opts: dict[str, Any]) -> list[Any]: ...  # output | Exception


class StageBase:
    """Defaults for the optional Stage methods; a stage overrides what it needs."""

    def check(self, opts: dict[str, Any]) -> None:
        return None

    def check_loaded(self, engine: Any, opts: dict[str, Any]) -> None:
        return None

    def writes(self, opts: dict[str, Any]) -> list[str]:
        return []

    def reads_paths(self, opts: dict[str, Any]) -> list[str]:
        return []

    def settings(self) -> dict[str, Any]:
        return {}


def each(items: list[Item], fn: Callable[[Item], Any]) -> list[Any]:
    """`fn` per item: a stage body for per-image work. An exception costs that item only (the run
    reports it as that image's error)."""
    out: list[Any] = []
    for it in items:
        try:
            out.append(fn(it))
        except Exception as exc:  # noqa: BLE001
            out.append(exc)
    return out


_LOADED: dict[str, Any] = {}


def load(manifest: Manifest) -> Stage:
    """The manifest's Stage, imported on first use."""
    if manifest.impl not in _LOADED:
        module, _, attr = manifest.impl.partition(":")
        _LOADED[manifest.impl] = getattr(importlib.import_module(module), attr)
    return _LOADED[manifest.impl]
