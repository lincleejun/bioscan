"""HTTP surface: /health, /products, /run (NDJSON stream): request validation, allow-roots, model
load (503) and streaming. The run itself (decode, per-chunk model turns, products, events) is
bioscan.service.run.RunQueue."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from collections.abc import AsyncIterator, Sequence
from concurrent.futures import Executor
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from bioscan import plugin, profile, serve_config
from bioscan.plugins import BUILTIN
from bioscan.service import stages
from bioscan.service.run import DecodePool, RunQueue

log = logging.getLogger("bioscan")


def parse_run(body: Any, registry: Sequence[plugin.Manifest] = BUILTIN,
              config: profile.Config | None = None) -> tuple[list[dict[str, Any]], plugin.Plan]:
    """Validates a /run body, expands its "profile" (none: `full`, the behaviour before profiles)
    with `config` (default: the built-in profiles) and plans the run over the stages of
    `registry`; ValueError -> 400."""
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
    want = body.get("want")
    if want is not None:
        if not isinstance(want, list) or not want or not all(isinstance(w, str) for w in want):
            raise ValueError("want must be a non-empty list of product names")
        unknown = sorted(set(want) - {m.name for m in registry})
        if unknown:
            raise ValueError(f"unknown products: {unknown}")
    resolved = profile.resolve(config or profile.builtin(registry), _profile_of(body), want,
                               body.get("options"), check=lambda m, o: plugin.load(m).check(o), registry=registry)
    return clean, resolved.plan


def _profile_of(body: dict[str, Any]) -> Any:
    name = body.get("profile")
    return profile.FULL if name is None else name


def outside_roots(inputs: list[dict[str, Any]], plan: plugin.Plan, roots: list[Path]) -> list[str]:
    """Paths the request would read or write that are not inside one of `roots` (symlinks and
    `..` resolved first, so neither escapes). No roots = everything allowed."""
    if not roots:
        return []
    paths = [inp["path"] for inp in inputs] + stages.paths(plan)
    return [p for p in paths if not any(Path(p).resolve().is_relative_to(r) for r in roots)]


def create_app(engine: Any, *, decode_pool: Executor | DecodePool | None = None, chunk: int = serve_config.CHUNK,
               decode_workers: int = serve_config.DECODE_WORKERS, detail_edge: int | None = serve_config.DETAIL_EDGE,
               allow_roots: list[str] | None = None, plugins: Sequence[plugin.Manifest] = BUILTIN,
               profiles: profile.Config | None = None) -> FastAPI:
    """`plugins`: the stages this service offers (default the built-in ones; tests add toy stages).
    `profiles`: what a request's "profile" expands with (default: bioscan/profiles.toml only)."""
    app = FastAPI(title="bioscan")
    runs = RunQueue(engine, decode_pool=decode_pool, decode_workers=decode_workers, chunk=chunk,
                    detail_edge=detail_edge)
    app.state.bioscan = runs
    roots = [Path(r).expanduser().resolve() for r in allow_roots or []]
    catalogue = stages.products(plugins)
    profiles = profiles or profile.builtin(plugins)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "device": engine.device, "models_loaded": engine.loaded(),
                "running": runs.running, "queued": runs.queued}

    @app.get("/products")
    async def list_products() -> dict[str, Any]:
        return catalogue

    @app.post("/run")
    async def run(request: Request) -> Any:
        try:
            inputs, plan = parse_run(json.loads(await request.body()), plugins, profiles)
        except (ValueError, json.JSONDecodeError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        denied = outside_roots(inputs, plan, roots)
        if denied:
            return JSONResponse({"error": f"paths outside the allowed roots: {denied[:5]}"
                                          + (f" (+{len(denied) - 5} more)" if len(denied) > 5 else "")},
                                status_code=400)
        try:
            await asyncio.get_running_loop().run_in_executor(None, engine.ensure, plan.models)
        except Exception as exc:  # noqa: BLE001
            log.exception("model load failed")
            return JSONResponse({"error": f"model load failed: {type(exc).__name__}: {exc}"}, status_code=503)
        try:
            await asyncio.get_running_loop().run_in_executor(None, stages.check_loaded, engine, plan)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

        async def stream() -> AsyncIterator[bytes]:
            async for ev in runs.events(inputs, plan, request.is_disconnected):
                yield (json.dumps(ev, ensure_ascii=False) + "\n").encode()

        return StreamingResponse(stream(), media_type="application/x-ndjson")

    return app


def serve(config: serve_config.ServeConfig) -> None:
    """Run the service with `config` until stopped (uvicorn, one worker)."""
    import uvicorn

    from bioscan.service.engine import Engine

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    log.info("decode_workers=%d chunk=%d detail_edge=%s allow_roots=%s", config.decode_workers, config.chunk,
             config.detail_edge, config.allow_roots or "any")
    if not config.allow_roots and config.host not in ("127.0.0.1", "localhost", "::1"):
        log.warning("listening on %s with no --allow-root: any client can read any file this user can read "
                    "and write JPEGs anywhere", config.host)
    try:
        profiles = profile.load()      # the service's own bioscan.toml files; a bad one stops the start
    except ValueError as e:
        raise SystemExit(f"error: {e}") from None
    log.info("profiles %s from %s", ", ".join(profiles.names()),
             ", ".join(label for label, path, exists in profiles.files if exists) or "profiles.toml only")
    app = create_app(Engine(), chunk=config.chunk, decode_workers=config.decode_workers,
                     detail_edge=config.detail_edge, allow_roots=config.allow_roots, profiles=profiles)
    uvicorn.run(app, host=config.host, port=config.port, workers=1)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="bioscan-serve")
    ap.add_argument("--host", help=f"default: [serve] host in bioscan.toml, else {serve_config.HOST}")
    ap.add_argument("--port", type=int, help=f"default: [serve] port in bioscan.toml, else {serve_config.PORT}")
    ap.add_argument("--decode-workers", type=int,
                    help=f"default: env BIOSCAN_DECODE_WORKERS, else bioscan.toml, else {serve_config.DECODE_WORKERS}")
    ap.add_argument("--chunk", type=int, help=f"default: env BIOSCAN_CHUNK, else bioscan.toml, else {serve_config.CHUNK}")
    ap.add_argument("--detail-edge", type=int,
                    help=f"long edge of the species-crop image; <= {serve_config.MAX_EDGE} turns it off. "
                         f"default: env BIOSCAN_DETAIL_EDGE, else bioscan.toml, else {serve_config.DETAIL_EDGE}")
    ap.add_argument("--allow-root", action="append",
                    help="only read inputs / write jpg under this directory (repeatable). "
                         "default: env BIOSCAN_ALLOW_ROOTS (os.pathsep-separated), else bioscan.toml, else no limit")
    args = ap.parse_args(argv)
    try:
        file = profile.load().serve()
    except ValueError as e:
        raise SystemExit(f"error: {e}") from None
    serve(serve_config.resolve(host=args.host, port=args.port, decode_workers=args.decode_workers, chunk=args.chunk,
                               detail_edge=args.detail_edge, allow_roots=args.allow_root, file=file))


if __name__ == "__main__":
    main()
