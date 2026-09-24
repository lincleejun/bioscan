"""The service side of the stage plugins (bioscan.plugins.BUILTIN): request options merged with each
stage's defaults and checked by its Stage, the checks that need the loaded models, and the
GET /products document. app.py and run.py reach the stages only through here and bioscan.plugin."""
from __future__ import annotations

from typing import Any

from bioscan import plugin
from bioscan.plugins import BUILTIN, BY_NAME

# GET /products: every stage's description, options (with defaults) and output.
PRODUCTS: dict[str, Any] = {m.name: m.schema() for m in BUILTIN}


def stage(name: str) -> plugin.Stage:
    """The Stage of a built-in plugin, imported on first use."""
    return plugin.load(BY_NAME[name])


def resolve_options(options: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Defaults merged with the request, for every stage; raises ValueError on a bad value."""
    options = options or {}
    if not isinstance(options, dict):
        raise ValueError("options must be an object")
    out = {}
    for m in BUILTIN:
        given = options.get(m.name) or {}
        if not isinstance(given, dict):
            raise ValueError(f"options.{m.name} must be an object")
        defaults = m.defaults
        unknown = set(given) - set(defaults)
        if unknown:
            raise ValueError(f"unknown options.{m.name}: {sorted(unknown)}")
        out[m.name] = {**defaults, **given}
    unknown = set(options) - set(BY_NAME)
    if unknown:
        raise ValueError(f"unknown option groups: {sorted(unknown)}")
    for m in BUILTIN:
        stage(m.name).check(out[m.name])
    return out


def check_loaded(engine: Any, want: list[str], opts: dict[str, dict[str, Any]]) -> None:
    """The checks that need the loaded models or name lists (after Engine.ensure); ValueError -> 400."""
    for name in want:
        stage(name).check_loaded(engine, opts[name])


def paths(want: list[str], opts: dict[str, dict[str, Any]]) -> list[str]:
    """Every path the wanted stages would write or read besides the inputs (for allow-roots)."""
    return [p for name in want for p in (*stage(name).writes(opts[name]), *stage(name).reads_paths(opts[name]))]
