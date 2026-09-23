"""HTTP surface: /health, /products, /run (NDJSON stream): request validation, allow-roots, model
load (503) and streaming. The run itself (decode, per-chunk model turns, products, events) is
bioscan.service.run.RunQueue."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator
from concurrent.futures import Executor
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from bioscan import contract
from bioscan.service import products
from bioscan.service.decode import DETAIL_EDGE, MAX_EDGE
from bioscan.service.run import DecodePool, RunQueue

log = logging.getLogger("bioscan")
ORDER = contract.PRODUCTS


def parse_run(body: Any) -> tuple[list[dict[str, Any]], list[str], dict[str, dict[str, Any]]]:
    """Validates a /run body; ValueError -> 400."""
    if not isinstance(body, dict):
        raise ValueError("body must be a JSON object")
    inputs = body.get("inputs")
    if not isinstance(inputs, list) or not inputs:
        raise ValueError("inputs must be a non-empty list")
    clean = []
    for i, inp in enumerate(inputs):
        if not isinstance(inp, dict) or not isinstance(inp.get("path"), str) or not Path(inp["path"]).is_absolute():
            raise ValueError(f"inputs[{i}].path must be an absolute path")
        for key in ("lat", "lon"):
            v = inp.get(key)
            if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float))):
                raise ValueError(f"inputs[{i}].{key} must be a number")
        if inp.get("taken_at") is not None and not isinstance(inp["taken_at"], str):
            raise ValueError(f"inputs[{i}].taken_at must be a string")
        clean.append({"path": inp["path"], "lat": inp.get("lat"), "lon": inp.get("lon"), "taken_at": inp.get("taken_at")})
    want = body.get("want", ["identify"])
    if not isinstance(want, list) or not want or not all(isinstance(w, str) for w in want):
        raise ValueError("want must be a non-empty list of product names")
    unknown = sorted(set(want) - set(ORDER))
    if unknown:
        raise ValueError(f"unknown products: {unknown}")
    return clean, [p for p in ORDER if p in want], products.resolve_options(body.get("options"))


def outside_roots(inputs: list[dict[str, Any]], want: list[str], opts: dict[str, dict[str, Any]],
                  roots: list[Path]) -> list[str]:
    """Paths the request would read or write that are not inside one of `roots` (symlinks and
    `..` resolved first, so neither escapes). No roots = everything allowed."""
    if not roots:
        return []
    paths = [inp["path"] for inp in inputs] + [w for p in want for w in products.REGISTRY[p].writes(opts[p])]
    return [p for p in paths if not any(Path(p).resolve().is_relative_to(r) for r in roots)]


def create_app(engine: Any, *, decode_pool: Executor | DecodePool | None = None, chunk: int = 32,
               decode_workers: int = 4, detail_edge: int | None = DETAIL_EDGE,
               allow_roots: list[str] | None = None) -> FastAPI:
    app = FastAPI(title="bioscan")
    runs = RunQueue(engine, decode_pool=decode_pool, decode_workers=decode_workers, chunk=chunk,
                    detail_edge=detail_edge)
    app.state.bioscan = runs
    roots = [Path(r).expanduser().resolve() for r in allow_roots or []]

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "device": engine.device, "models_loaded": engine.loaded(),
                "running": runs.running, "queued": runs.queued}

    @app.get("/products")
    async def list_products() -> dict[str, Any]:
        return products.PRODUCTS

    @app.post("/run")
    async def run(request: Request) -> Any:
        try:
            inputs, want, opts = parse_run(json.loads(await request.body()))
        except (ValueError, json.JSONDecodeError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        denied = outside_roots(inputs, want, opts, roots)
        if denied:
            return JSONResponse({"error": f"paths outside the allowed roots: {denied[:5]}"
                                          + (f" (+{len(denied) - 5} more)" if len(denied) > 5 else "")},
                                status_code=400)
        try:
            await asyncio.get_running_loop().run_in_executor(None, engine.ensure, want)
        except Exception as exc:  # noqa: BLE001
            log.exception("model load failed")
            return JSONResponse({"error": f"model load failed: {type(exc).__name__}: {exc}"}, status_code=503)

        async def stream() -> AsyncIterator[bytes]:
            async for ev in runs.events(inputs, want, opts, request.is_disconnected):
                yield (json.dumps(ev, ensure_ascii=False) + "\n").encode()

        return StreamingResponse(stream(), media_type="application/x-ndjson")

    return app


def tunables(decode_workers: int | None, chunk: int | None, env=os.environ) -> tuple[int, int]:
    """Command line beats BIOSCAN_DECODE_WORKERS / BIOSCAN_CHUNK beats the defaults 4 / 32."""
    def pick(given: int | None, var: str, default: int) -> int:
        value = given if given is not None else int(env.get(var) or default)
        if value < 1:
            raise SystemExit(f"{var.removeprefix('BIOSCAN_').lower()} must be >= 1")
        return value
    return pick(decode_workers, "BIOSCAN_DECODE_WORKERS", 4), pick(chunk, "BIOSCAN_CHUNK", 32)


def detail_edge_from(given: int | None, env=os.environ) -> int | None:
    """--detail-edge beats BIOSCAN_DETAIL_EDGE beats 3072; a value <= 2048 turns the larger
    species image off (None)."""
    value = given if given is not None else int(env.get("BIOSCAN_DETAIL_EDGE") or DETAIL_EDGE)
    return value if value > MAX_EDGE else None


def allow_roots_from(given: list[str] | None, env=os.environ) -> list[str]:
    """--allow-root (repeatable) beats BIOSCAN_ALLOW_ROOTS (os.pathsep-separated); none = no limit."""
    if given:
        return given
    return [r for r in (env.get("BIOSCAN_ALLOW_ROOTS") or "").split(os.pathsep) if r.strip()]


def main(argv: list[str] | None = None) -> None:
    import uvicorn

    from bioscan.service.engine import Engine

    ap = argparse.ArgumentParser(prog="bioscan-serve")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--decode-workers", type=int, help="default: env BIOSCAN_DECODE_WORKERS, else 4")
    ap.add_argument("--chunk", type=int, help="default: env BIOSCAN_CHUNK, else 32")
    ap.add_argument("--detail-edge", type=int,
                    help="long edge of the species-crop image; <= 2048 turns it off. "
                         "default: env BIOSCAN_DETAIL_EDGE, else 3072")
    ap.add_argument("--allow-root", action="append",
                    help="only read inputs / write jpg under this directory (repeatable). "
                         "default: env BIOSCAN_ALLOW_ROOTS (os.pathsep-separated), else no limit")
    args = ap.parse_args(argv)
    workers, chunk = tunables(args.decode_workers, args.chunk)
    detail = detail_edge_from(args.detail_edge)
    roots = allow_roots_from(args.allow_root)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    log.info("decode_workers=%d chunk=%d detail_edge=%s allow_roots=%s", workers, chunk, detail, roots or "any")
    if not roots and args.host not in ("127.0.0.1", "localhost", "::1"):
        log.warning("listening on %s with no --allow-root: any client can read any file this user can read "
                    "and write JPEGs anywhere", args.host)
    app = create_app(Engine(), chunk=chunk, decode_workers=workers, detail_edge=detail, allow_roots=roots)
    uvicorn.run(app, host=args.host, port=args.port, workers=1)


if __name__ == "__main__":
    main()
