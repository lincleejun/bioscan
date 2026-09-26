"""Profiles and bioscan.toml: the one resolver the CLI and the service share.

A profile is a named request template: the stages a run wants and their options. Built-in ones live
in bioscan/profiles.toml; a user's bioscan.toml may change them key by key or add new ones, and
carries the `[serve]` settings (serve_config's file layer) and a `default_profile` for the CLI.

Merge order, lowest first: each stage's defaults < profiles.toml < the user file
(~/.config/bioscan/bioscan.toml) < the project file (./bioscan.toml) < $BIOSCAN_CONFIG < the
request's options (CLI flags, or the /run body). Which profile: the CLI's --profile, else
$BIOSCAN_PROFILE, else `default_profile`, else `full`; a /run body without "profile" always gets
`full`, which no file may change, so such a request behaves as before profiles existed.
Unknown keys, stages, options and profiles are errors (ValueError) naming the file they are in.

Standard library only: the CLI imports it."""
from __future__ import annotations

import copy
import os
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bioscan import cull, plugin

PROFILES_TOML = Path(__file__).with_name("profiles.toml")
BUILTIN_LABEL = "profiles.toml"
FILE_NAME = "bioscan.toml"
FULL = "full"
DEFAULT = "default"
TOP_KEYS = ("default_profile", "serve", "profile")
PROFILE_KEYS = ("description", "stages", "reducers", "options")
SERVE_KEYS = {"host": str, "port": int, "decode_workers": int, "chunk": int, "detail_edge": int, "allow_roots": list}


def reducers() -> dict[str, plugin.Manifest]:
    """The reducers a profile may name (bioscan.plugins.REDUCERS), by name. They run in the CLI or
    offline (bioscan.cull), never in the service; their options live under options.<reducer>."""
    from bioscan.plugins import REDUCERS

    return {m.name: m for m in REDUCERS}


@dataclass(frozen=True)
class Layer:
    """One parsed config file."""
    label: str                        # "profiles.toml", "user /abs/path", "project /abs/path", "BIOSCAN_CONFIG /abs/path"
    data: dict[str, Any]


@dataclass(frozen=True)
class Config:
    """Every config layer, lowest first, plus $BIOSCAN_PROFILE."""
    layers: tuple[Layer, ...]
    env_profile: str | None = None
    files: tuple[tuple[str, str, bool], ...] = ()      # (label, path, exists) of each file looked for

    def names(self) -> list[str]:
        return sorted({n for layer in self.layers for n in layer.data.get("profile", {})})

    def default_profile(self) -> tuple[str | None, str]:
        """The CLI's profile when no --profile is given, and where it came from."""
        if self.env_profile:
            return self.env_profile, "BIOSCAN_PROFILE"
        for layer in reversed(self.layers):
            if "default_profile" in layer.data:
                return layer.data["default_profile"], f"default_profile in {layer.label}"
        return None, DEFAULT

    def serve(self) -> dict[str, tuple[Any, str]]:
        """The merged [serve] table: key -> (value, file label)."""
        out: dict[str, tuple[Any, str]] = {}
        for layer in self.layers:
            for k, v in layer.data.get("serve", {}).items():
                out[k] = (v, layer.label)
        return out


def _check_layer(data: dict[str, Any], label: str, registry: Sequence[plugin.Manifest]) -> dict[str, Any]:
    """Unknown keys and wrong types in one file are errors naming the file."""
    def fail(msg: str) -> ValueError:
        return ValueError(f"{label}: {msg}")

    unknown = sorted(set(data) - set(TOP_KEYS))
    if unknown:
        raise fail(f"unknown keys {unknown} (allowed: {', '.join(TOP_KEYS)})")
    if "default_profile" in data and not isinstance(data["default_profile"], str):
        raise fail("default_profile must be a string")
    serve = data.get("serve", {})
    if not isinstance(serve, dict):
        raise fail("[serve] must be a table")
    for k, v in serve.items():
        if k not in SERVE_KEYS:
            raise fail(f"unknown key serve.{k} (allowed: {', '.join(SERVE_KEYS)})")
        want = SERVE_KEYS[k]
        if isinstance(v, bool) or not isinstance(v, want) or (want is list and not all(isinstance(x, str) for x in v)):
            raise fail(f"serve.{k} must be {'a list of paths' if want is list else want.__name__}")
    profiles = data.get("profile", {})
    if not isinstance(profiles, dict):
        raise fail("[profile] must be a table of profiles")
    by_name = {m.name: m for m in registry}
    for name, prof in profiles.items():
        where = f"profile.{name}"
        if name == FULL and label != BUILTIN_LABEL:
            raise fail(f"[{where}] is built in and fixed (a request without a profile gets it); "
                       "define a profile of your own")
        if not isinstance(prof, dict):
            raise fail(f"[{where}] must be a table")
        unknown = sorted(set(prof) - set(PROFILE_KEYS))
        if unknown:
            raise fail(f"unknown keys {where}.{unknown} (allowed: {', '.join(PROFILE_KEYS)})")
        for key in ("stages", "reducers"):
            v = prof.get(key, [])
            if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                raise fail(f"{where}.{key} must be a list of names")
        if "stages" in prof and not prof["stages"]:
            raise fail(f"{where}.stages must not be empty")
        bad = sorted(set(prof.get("stages", [])) - set(by_name))
        if bad:
            raise fail(f"{where}.stages: unknown stages {bad} (known: {', '.join(by_name)})")
        known_reducers = reducers()
        bad = sorted(set(prof.get("reducers", [])) - set(known_reducers))
        if bad:
            raise fail(f"{where}.reducers: unknown reducers {bad} (known: {', '.join(known_reducers)})")
        opts = prof.get("options", {})
        if not isinstance(opts, dict):
            raise fail(f"{where}.options must be a table of stages")
        units = {**by_name, **known_reducers}
        for stage, given in opts.items():
            if stage not in units:
                raise fail(f"unknown stage {where}.options.{stage} (known: {', '.join(units)})")
            if not isinstance(given, dict):
                raise fail(f"{where}.options.{stage} must be a table")
            unknown = sorted(set(given) - set(units[stage].defaults))
            if unknown:
                raise fail(f"unknown options {where}.options.{stage}: {unknown}")
    return data


def _read(path: Path, label: str, registry: Sequence[plugin.Manifest]) -> Layer:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ValueError(f"{label}: {e}") from e
    return Layer(label, _check_layer(data, label, registry))


def config_files(env: Mapping[str, str] = os.environ, cwd: Path | None = None,
                 home: Path | None = None) -> list[tuple[str, Path]]:
    """The bioscan.toml files looked for, lowest first: user, project, $BIOSCAN_CONFIG."""
    home = home if home is not None else Path(os.path.expanduser("~"))
    config_home = Path(env["XDG_CONFIG_HOME"]) if env.get("XDG_CONFIG_HOME") else home / ".config"
    files = [("user", config_home / "bioscan" / FILE_NAME), ("project", (cwd or Path.cwd()) / FILE_NAME)]
    if env.get("BIOSCAN_CONFIG"):
        files.append(("BIOSCAN_CONFIG", Path(env["BIOSCAN_CONFIG"]).expanduser()))
    return files


def builtin(registry: Sequence[plugin.Manifest] | None = None) -> Config:
    """profiles.toml alone: what a service created without files uses (tests, create_app's default)."""
    return Config((_read(PROFILES_TOML, BUILTIN_LABEL, plugin._registry(registry)),))


def load(files: Sequence[tuple[str, Path]] | None = None, env: Mapping[str, str] = os.environ,
         registry: Sequence[plugin.Manifest] | None = None) -> Config:
    """profiles.toml plus every bioscan.toml that exists (`files`: default config_files(env)). A
    missing user or project file is skipped; a missing $BIOSCAN_CONFIG file is an error."""
    registry = plugin._registry(registry)
    layers = [_read(PROFILES_TOML, BUILTIN_LABEL, registry)]
    seen = []
    for kind, path in config_files(env) if files is None else files:
        exists = path.is_file()
        seen.append((kind, str(path), exists))
        if not exists:
            if kind == "BIOSCAN_CONFIG":
                raise ValueError(f"BIOSCAN_CONFIG: no such file {path}")
            continue
        layers.append(_read(path, f"{kind} {path}", registry))
    return Config(tuple(layers), env.get("BIOSCAN_PROFILE") or None, tuple(seen))


def select(config: Config, name: str | None, given_by: str = "--profile") -> tuple[str, str]:
    """The CLI's profile: `name` (from `given_by`), else config.default_profile(), else full."""
    if name:
        return name, given_by
    chosen, source = config.default_profile()
    return (chosen, source) if chosen else (FULL, DEFAULT)


@dataclass(frozen=True)
class Resolved:
    """A profile expanded and merged with a request, planned."""
    profile: str
    want: list[str]                                  # as the request gave it, else the profile's stages
    want_source: str
    options: dict[str, dict[str, Any]]               # every stage's options
    sources: dict[str, dict[str, str]]               # stage -> option -> where its value came from
    reducers: list[str]
    plan: plugin.Plan
    # every reducer's options (defaults < profile layers < reducer_options) and their sources; the
    # CLI runs res.reducers with them, the service ignores both
    reducer_options: dict[str, dict[str, Any]] = field(default_factory=dict)
    reducer_sources: dict[str, dict[str, str]] = field(default_factory=dict)

    def reducer_run(self) -> dict[str, dict[str, Any]]:
        """{reducer: options} of the profile's reducers, what bioscan.cull.apply takes."""
        return {r: self.reducer_options[r] for r in self.reducers}


def resolve(config: Config, profile: str, want: list[str] | None = None, options: Any = None, *,
            source: str = "request", check: Callable[[plugin.Manifest, dict[str, Any]], None] | None = None,
            registry: Sequence[plugin.Manifest] | None = None,
            reducer_options: dict[str, dict[str, Any]] | None = None) -> Resolved:
    """Expand `profile` and merge `options` (the request's {stage: {name: value}}, highest layer,
    labelled `source`) over it; `want` (the request's stages) replaces the profile's. `check`
    (the service's Stage.check) sees every stage's merged options before the plan is made.
    `reducer_options` ({reducer: {name: value}}, the CLI's flags) go over the profile's reducer
    options, which each reducer's check then sees. ValueError on an unknown profile, key, stage,
    reducer or option, or a plan that cannot run."""
    registry = plugin._registry(registry)
    if not isinstance(profile, str) or not profile:
        raise ValueError("profile must be a string naming a profile")
    if profile not in config.names():
        raise ValueError(f"unknown profile {profile!r} (known: {', '.join(config.names())})")
    given = plugin.merge_options(options, registry)            # the request's own errors, as before profiles
    options = options or {}
    merged = {m.name: m.defaults for m in registry}
    sources = {m.name: dict.fromkeys(merged[m.name], DEFAULT) for m in registry}
    known = reducers()
    r_merged = {name: m.defaults for name, m in known.items()}
    r_sources = {name: dict.fromkeys(r_merged[name], DEFAULT) for name in known}
    stages: list[str] | None = None
    stages_source = DEFAULT
    chosen: list[str] = []
    for layer in config.layers:
        prof = layer.data.get("profile", {}).get(profile)
        if prof is None:
            continue
        if "stages" in prof:
            stages, stages_source = list(prof["stages"]), layer.label
        chosen = list(prof.get("reducers", chosen))
        for stage, values in prof.get("options", {}).items():
            into, src = (r_merged, r_sources) if stage in known and stage not in merged else (merged, sources)
            for k, v in values.items():
                into[stage][k], src[stage][k] = copy.deepcopy(v), layer.label
    for stage, values in options.items():
        for k in values or {}:
            merged[stage][k], sources[stage][k] = given[stage][k], source
    for name, values in (reducer_options or {}).items():
        if name not in known:
            raise ValueError(f"unknown reducer {name!r} (known: {', '.join(known)})")
        bad = sorted(set(values) - set(r_merged[name]))
        if bad:
            raise ValueError(f"unknown options {name}: {bad}")
        for k, v in values.items():
            r_merged[name][k], r_sources[name][k] = copy.deepcopy(v), source
    for name, m in known.items():
        m.check(r_merged[name])
    if want is None:
        want, want_source = list(stages or ["identify"]), stages_source
    else:
        want_source = source
    if check is not None:
        for m in registry:
            check(m, merged[m.name])
    if r_sources["select"]["waive"] != DEFAULT:          # the default may name a label a request's own labels lack
        cull.check_waive_keys(r_merged["select"]["waive"], merged.get("scene"))
    return Resolved(profile, list(want), want_source, merged, sources, chosen,
                    plugin.plan(want, merged, registry), r_merged, r_sources)
