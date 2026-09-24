"""Serve config: the settings `bioscan serve`, `bioscan-serve` and the launchd plist share, and the
one place where command line beats BIOSCAN_* environment beats the `[serve]` table of bioscan.toml
beats default is decided.

Stdlib only, so the CLI can import it without pulling in the service."""
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

HOST = "127.0.0.1"
PORT = 8765
DECODE_WORKERS = 4   # decode processes; about 4 is best for a USB hard disk
CHUNK = 32           # images per pipeline chunk (one turn on the models)
MAX_EDGE = 2048      # long edge of the image every model sees
DETAIL_EDGE = 3072   # default long edge of the species-crop image; <= MAX_EDGE turns it off
ENV = {"decode_workers": "BIOSCAN_DECODE_WORKERS", "chunk": "BIOSCAN_CHUNK", "detail_edge": "BIOSCAN_DETAIL_EDGE",
       "allow_roots": "BIOSCAN_ALLOW_ROOTS"}      # host and port have no variable


@dataclass(frozen=True)
class ServeConfig:
    host: str
    port: int
    decode_workers: int
    chunk: int
    detail_edge: int | None     # None: no larger species image, crops come from the MAX_EDGE image
    allow_roots: list[str]      # empty: no limit


def resolve(*, host: str | None = None, port: int | None = None, decode_workers: int | None = None,
            chunk: int | None = None, detail_edge: int | None = None, allow_roots: list[str] | None = None,
            env: Mapping[str, str] = os.environ, file: Mapping[str, Any] | None = None) -> ServeConfig:
    """The service's settings from its flags (None = not given), `env` and `file` (the [serve]
    table of bioscan.toml, profile.Config.serve(): key -> value or (value, source)).

    Each setting: flag, else its BIOSCAN_* variable, else the file, else the default here.
    decode_workers / chunk below 1 is SystemExit("decode_workers must be >= 1") / ("chunk must be >= 1").
    detail_edge <= MAX_EDGE turns it off (None). allow_roots: flag (repeatable), else
    BIOSCAN_ALLOW_ROOTS split on os.pathsep, else the file's list (~ expanded), else no limit.
    host / port have no variable. An empty variable counts as unset; a variable that is not an
    integer raises ValueError."""
    return resolve_sources(host=host, port=port, decode_workers=decode_workers, chunk=chunk, detail_edge=detail_edge,
                           allow_roots=allow_roots, env=env, file=file)[0]


def resolve_sources(*, host: str | None = None, port: int | None = None, decode_workers: int | None = None,
                    chunk: int | None = None, detail_edge: int | None = None, allow_roots: list[str] | None = None,
                    env: Mapping[str, str] = os.environ, file: Mapping[str, Any] | None = None,
                    ) -> tuple[ServeConfig, dict[str, str]]:
    """resolve(), plus where each setting came from: "flag", "env BIOSCAN_*", the file's label, "default"."""
    file = {k: v if isinstance(v, tuple) else (v, "bioscan.toml") for k, v in (file or {}).items()}
    flags = {"host": host, "port": port, "decode_workers": decode_workers, "chunk": chunk, "detail_edge": detail_edge,
             "allow_roots": allow_roots or None}
    defaults = {"host": HOST, "port": PORT, "decode_workers": DECODE_WORKERS, "chunk": CHUNK,
                "detail_edge": DETAIL_EDGE, "allow_roots": []}
    values: dict[str, Any] = {}
    sources: dict[str, str] = {}
    for key, default in defaults.items():
        var = ENV.get(key)
        if flags[key] is not None:
            values[key], sources[key] = flags[key], "flag"
        elif var and env.get(var):
            raw = env[var]
            values[key] = ([r for r in raw.split(os.pathsep) if r.strip()] if key == "allow_roots" else int(raw))
            sources[key] = f"env {var}"
        elif key in file:
            values[key], sources[key] = file[key]
            if key == "allow_roots":
                values[key] = [os.path.expanduser(r) for r in values[key]]
        else:
            values[key], sources[key] = default, "default"
    for key in ("decode_workers", "chunk"):
        if values[key] < 1:
            raise SystemExit(f"{key} must be >= 1")
    edge = values["detail_edge"]
    return (ServeConfig(host=values["host"], port=values["port"], decode_workers=values["decode_workers"],
                        chunk=values["chunk"], detail_edge=edge if edge > MAX_EDGE else None,
                        allow_roots=list(values["allow_roots"])), sources)
