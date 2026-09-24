"""What a stage plugin is: a stdlib `Manifest` the CLI, profiles and /products read without loading
anything heavy, and a `Stage` (its `impl`, imported only when a run needs it) the service calls once
per chunk. `Item` is one decoded image on its way through a chunk, as a stage sees it. `plan` turns
the stages a run wants and their options into a `Plan`: the run order, the models to load and
whether the run needs the frame pass and the detail copy, all decided before anything loads.

Standard library only: the CLI imports this module. Design and migration steps:
docs/research/2026-09-24-plugin-architecture.md."""
from __future__ import annotations

import copy
import importlib
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

FRAME_FACTS = ("vec", "gate")       # what the shared whole-frame SigLIP2 pass provides
# Facts every stage can read without a providing stage: the decoded image and its detail copy, the
# capture time and place (request, else EXIF), and the frame pass. A stage may still provide one of
# them (geotag provides "place" for photos without one); readers then run after it.
BASE_FACTS = ("image", "detail", "time", "place", *FRAME_FACTS)


@dataclass(frozen=True)
class Metric:
    """A rate the harness reports per plugin (report.json `plugin_metrics[plugin][scope]`, with a
    Wilson interval). `row` sees one image: the plugin's output in the result (None when the result
    has none) and its ground-truth row; True is a hit, False a miss, None leaves the image out."""
    name: str
    description: str
    row: Callable[[Any, dict[str, str]], bool | None]


@dataclass(frozen=True)
class Manifest:
    """What a plugin declares, importable without its dependencies."""
    name: str                                   # also the product key in result.products
    version: int                                # bump when the output's meaning changes
    description: str                            # served by GET /products
    reads: tuple[str, ...] = ()                 # facts it needs: BASE_FACTS, or what another stage provides
    provides: tuple[str, ...] = ()              # facts it adds to Item.facts for later stages
    # model names needed under these options: engine.MODELS, a key of `loaders`, or of Loaders.extra;
    # a stage that reads vec/gate lists "siglip2"
    models: Callable[[dict[str, Any]], tuple[str, ...]] = lambda opts: ()
    thread: Literal["model", "cpu"] = "cpu"     # the single model thread, or the CPU pool
    options: dict[str, Any] | None = None       # {name: JSON-schema-ish with "default"}, served by /products
    output: dict[str, Any] | None = None        # description of the output, served by /products
    impl: str = ""                              # "package.module:ATTR", the Stage, imported lazily
    loaders: dict[str, str] | None = None       # model name -> "package.module:FUNC" (device -> adapter), lazily
    # checks (and may normalise) the merged options, stdlib only: ValueError -> 400. Lives in the
    # manifest so a request is validated without importing any stage's service code.
    check: Callable[[dict[str, Any]], None] = lambda opts: None
    metrics: tuple[Metric, ...] = ()            # rates for `bioscan bench` (stdlib, from the output alone)

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


@dataclass
class Item:
    """One decoded image on its way through a chunk (what a stage's `run` sees). The same Item
    goes to every stage of the chunk, so a stage adds what it `provides` to `facts` for the
    stages after it (identify: "boxes"; geotag: "place" as (lat, lon))."""
    dec: Any                        # decode.Decoded
    inp: dict[str, Any]             # the request's input: path, lat, lon, taken_at
    vec: Any = None                 # whole-frame SigLIP2 vector (stages that read "vec")
    gate: dict[str, float] | None = None
    facts: dict[str, Any] = field(default_factory=dict)

    def _place(self, i: int) -> float | None:
        place = self.facts.get("place")
        return None if place is None else place[i]

    @property
    def lat(self) -> float | None:
        """The request's, else EXIF's, else a stage-provided place's (geotag)."""
        for v in (self.inp["lat"], self.dec.lat, self._place(0)):
            if v is not None:
                return v
        return None

    @property
    def lon(self) -> float | None:
        for v in (self.inp["lon"], self.dec.lon, self._place(1)):
            if v is not None:
                return v
        return None

    @property
    def taken_at(self) -> str | None:
        return self.inp["taken_at"] or self.dec.taken_at


class Stage(Protocol):
    """The service side of a plugin. May import numpy / torch at module level. Imported only for
    stages in a run's plan (option values are checked by Manifest.check)."""

    def check_loaded(self, engine: Any, opts: dict[str, Any]) -> None: ...  # after the models load; ValueError -> 400
    def writes(self, opts: dict[str, Any]) -> list[str]: ...                # paths checked against allow-roots
    def reads_paths(self, opts: dict[str, Any]) -> list[str]: ...           # files it reads besides the inputs
    def settings(self) -> dict[str, Any]: ...                               # its output-changing constants
    def run(self, engine: Any, items: list[Item], opts: dict[str, Any]) -> list[Any]: ...  # output | Exception


class StageBase:
    """Defaults for the optional Stage methods; a stage overrides what it needs."""

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


def load(ref: Manifest | str) -> Any:
    """A manifest's Stage, or any "package.module:ATTR" reference, imported on first use."""
    ref = ref.impl if isinstance(ref, Manifest) else ref
    if ref not in _LOADED:
        module, _, attr = ref.partition(":")
        _LOADED[ref] = getattr(importlib.import_module(module), attr)
    return _LOADED[ref]


# ---- options and plan --------------------------------------------------------------------

def _registry(registry: Sequence[Manifest] | None) -> Sequence[Manifest]:
    if registry is None:
        from bioscan.plugins import BUILTIN

        return BUILTIN
    return registry


def merge_options(options: Any, registry: Sequence[Manifest] | None = None) -> dict[str, dict[str, Any]]:
    """Every stage's defaults with `options` ({stage: {name: value}}) on top; ValueError on a
    group or a name no stage declares. Values are not checked here (Stage.check does that)."""
    registry = _registry(registry)
    options = options or {}
    if not isinstance(options, dict):
        raise ValueError("options must be an object")
    out = {}
    for m in registry:
        given = options.get(m.name) or {}
        if not isinstance(given, dict):
            raise ValueError(f"options.{m.name} must be an object")
        defaults = m.defaults
        unknown = set(given) - set(defaults)
        if unknown:
            raise ValueError(f"unknown options.{m.name}: {sorted(unknown)}")
        out[m.name] = {**defaults, **given}
    unknown = set(options) - {m.name for m in registry}
    if unknown:
        raise ValueError(f"unknown option groups: {sorted(unknown)}")
    return out


@dataclass(frozen=True)
class Plan:
    """What a run will do, decided before anything loads."""
    want: tuple[str, ...]                  # the requested stages in report order (registry order)
    stages: tuple[str, ...]                # run order: providers before readers, ties by name
    opts: dict[str, dict[str, Any]]        # every stage's options (what the request resolved to)
    models: tuple[str, ...]                # the models the stages need under these options, sorted
    frame: bool                            # a stage reads the whole-frame pass (vec / gate)
    detail: bool                           # a stage reads the detail copy
    manifests: dict[str, Manifest]         # name -> manifest, for the stages in `want`


def plan(want: Iterable[str], opts: dict[str, dict[str, Any]], registry: Sequence[Manifest] | None = None) -> Plan:
    """The plan for running `want` with `opts` (merge_options' output). ValueError on an unknown
    stage, a fact a stage reads that no planned stage (or the host) provides, or a reads/provides
    cycle."""
    registry = _registry(registry)
    by_name = {m.name: m for m in registry}
    want = list(want)
    unknown = sorted(set(want) - set(by_name))
    if unknown:
        raise ValueError(f"unknown products: {unknown}")
    chosen = [m for m in registry if m.name in want]
    providers: dict[str, list[str]] = {}
    for m in chosen:
        for fact in m.provides:
            providers.setdefault(fact, []).append(m.name)
    after: dict[str, set[str]] = {m.name: set() for m in chosen}      # stage -> stages that must run first
    for m in chosen:
        for fact in m.reads:
            if fact not in BASE_FACTS and fact not in providers:
                could = sorted(r.name for r in registry if fact in r.provides)
                raise ValueError(f"stage {m.name} reads {fact!r}, which no stage of this run provides"
                                 + (f" (add {' or '.join(could)})" if could else ""))
            after[m.name] |= {p for p in providers.get(fact, ()) if p != m.name}
    order: list[str] = []
    while len(order) < len(after):
        ready = sorted(n for n, deps in after.items() if n not in order and deps <= set(order))
        if not ready:
            left = sorted(n for n in after if n not in order)
            raise ValueError(f"stages {left} read each other's facts (a reads/provides cycle)")
        order.append(ready[0])
    return Plan(want=tuple(m.name for m in chosen), stages=tuple(order), opts=opts,
                models=tuple(sorted({x for m in chosen for x in m.models(opts[m.name])})),
                frame=any(m.uses_frame for m in chosen), detail=any("detail" in m.reads for m in chosen),
                manifests={m.name: m for m in chosen})
