"""The service side of the stage plugins (bioscan.plugins.BUILTIN): request options merged with each
stage's defaults and checked by its Stage, the checks that need the loaded models, the paths a plan
touches, and the GET /products document. app.py and run.py reach the stages only through here and
bioscan.plugin."""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from bioscan import plugin
from bioscan.plugins import BUILTIN


def products(registry: Sequence[plugin.Manifest] = BUILTIN) -> dict[str, Any]:
    """GET /products: every stage's description, options (with defaults) and output."""
    return {m.name: m.schema() for m in registry}


PRODUCTS: dict[str, Any] = products()


def resolve_options(options: Any, registry: Sequence[plugin.Manifest] = BUILTIN) -> dict[str, dict[str, Any]]:
    """Defaults merged with the request, for every stage, each checked by its manifest (no stage
    code is imported); raises ValueError on a bad value."""
    out = plugin.merge_options(options, registry)
    for m in registry:
        m.check(out[m.name])
    return out


def check_loaded(engine: Any, plan: plugin.Plan) -> None:
    """The checks that need the loaded models or name lists (after Engine.ensure); ValueError -> 400."""
    for name in plan.want:
        plugin.load(plan.manifests[name]).check_loaded(engine, plan.opts[name])


def fingerprints(plan: plugin.Plan) -> dict[str, str]:
    """result.engine.plugins: what each planned stage ran with, in plan.want (report) order. A stage
    whose Stage.plugin_id(opts) names something (e.g. aesthetics: `v1@<head sha>`, the file its
    output depends on) reports that; otherwise a manifest that is `fingerprinted` reports
    `plugin.fingerprint(version, Stage.settings())`; otherwise the stage reports nothing. plugin_id
    wins over the fingerprint, and a stage with neither (identify, embed, jpg, geotag, aesthetics
    with head "off") is left out. {} when no stage reports, and then the engine block is as before."""
    out: dict[str, str] = {}
    for name in plan.want:
        m = plan.manifests[name]
        s = plugin.load(m)
        pid = getattr(s, "plugin_id", lambda o: None)(plan.opts[name])
        if pid is not None:
            out[name] = pid
        elif m.fingerprinted:
            out[name] = plugin.fingerprint(m.version, s.settings())
    return out


def paths(plan: plugin.Plan) -> list[str]:
    """Every path the plan's stages would write or read besides the inputs (for allow-roots)."""
    out: list[str] = []
    for name in plan.want:
        s = plugin.load(plan.manifests[name])
        out += [*s.writes(plan.opts[name]), *s.reads_paths(plan.opts[name])]
    return out
