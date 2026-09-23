"""Serve config: the settings `bioscan serve`, `bioscan-serve` and the launchd plist share, and the
one place where command line beats BIOSCAN_* environment beats default is decided.

Stdlib only, so the CLI can import it without pulling in the service."""
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

HOST = "127.0.0.1"
PORT = 8765
DECODE_WORKERS = 4   # decode processes; about 4 is best for a USB hard disk
CHUNK = 32           # images per pipeline chunk (one turn on the models)
MAX_EDGE = 2048      # long edge of the image every model sees
DETAIL_EDGE = 3072   # default long edge of the species-crop image; <= MAX_EDGE turns it off


@dataclass(frozen=True)
class ServeConfig:
    host: str
    port: int
    decode_workers: int
    chunk: int
    detail_edge: int | None     # None: no larger species image, crops come from the MAX_EDGE image
    allow_roots: list[str]      # empty: no limit


def resolve(*, host: str = HOST, port: int = PORT, decode_workers: int | None = None, chunk: int | None = None,
            detail_edge: int | None = None, allow_roots: list[str] | None = None,
            env: Mapping[str, str] = os.environ) -> ServeConfig:
    """The service's settings from its flags (None = not given) and `env`.

    decode_workers / chunk: flag, else BIOSCAN_DECODE_WORKERS / BIOSCAN_CHUNK, else DECODE_WORKERS /
    CHUNK; below 1 is SystemExit("decode_workers must be >= 1") / ("chunk must be >= 1").
    detail_edge: flag, else BIOSCAN_DETAIL_EDGE, else DETAIL_EDGE; <= MAX_EDGE turns it off (None).
    allow_roots: flag (repeatable), else BIOSCAN_ALLOW_ROOTS split on os.pathsep, else no limit.
    host / port have no variable. An empty variable counts as unset; a variable that is not an
    integer raises ValueError."""
    def count(given: int | None, var: str, default: int) -> int:
        value = given if given is not None else int(env.get(var) or default)
        if value < 1:
            raise SystemExit(f"{var.removeprefix('BIOSCAN_').lower()} must be >= 1")
        return value

    workers = count(decode_workers, "BIOSCAN_DECODE_WORKERS", DECODE_WORKERS)
    chunk = count(chunk, "BIOSCAN_CHUNK", CHUNK)
    edge = detail_edge if detail_edge is not None else int(env.get("BIOSCAN_DETAIL_EDGE") or DETAIL_EDGE)
    roots = list(allow_roots) if allow_roots else \
        [r for r in (env.get("BIOSCAN_ALLOW_ROOTS") or "").split(os.pathsep) if r.strip()]
    return ServeConfig(host=host, port=port, decode_workers=workers, chunk=chunk,
                       detail_edge=edge if edge > MAX_EDGE else None, allow_roots=roots)
