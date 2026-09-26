"""HTTP surface: /health, /products, /run (NDJSON stream), /apply (the review page's copy / delete,
token-gated): request validation, allow-roots, model load (503) and streaming. The run itself (decode, per-chunk model turns, products, events) is
bioscan.service.run.RunQueue."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from collections.abc import AsyncIterator, Sequence
from concurrent.futures import Executor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from bioscan import apply as applying
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
    # absent: the profile's stages (full: identify); null only means that next to a "profile"
    if want is not None or ("want" in body and body.get("profile") is None):
        if not isinstance(want, list) or not want or not all(isinstance(w, str) for w in want):
            raise ValueError("want must be a non-empty list of product names")
        unknown = sorted(set(want) - {m.name for m in registry})
        if unknown:
            raise ValueError(f"unknown products: {unknown}")
    resolved = profile.resolve(config or profile.builtin(registry), _profile_of(body), want,
                               body.get("options"), check=lambda m, o: m.check(o), registry=registry)
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
               profiles: profile.Config | None = None, token: str | None = None) -> FastAPI:
    """`plugins`: the stages this service offers (default the built-in ones; tests add toy stages).
    `profiles`: what a request's "profile" expands with (default: bioscan/profiles.toml only).
    `token`: what POST /apply requires in X-Bioscan-Token (default: the machine's serve-token file,
    made when missing); the review page opens as file://, so any origin may call, the token gates."""
    runs = RunQueue(engine, decode_pool=decode_pool, decode_workers=decode_workers, chunk=chunk,
                    detail_edge=detail_edge)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        runs.close()

    app = FastAPI(title="bioscan", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST"],
                       allow_headers=["content-type", "x-bioscan-token"])
    app.state.bioscan = runs
    token = token or applying.token(create=True)
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

    @app.post("/apply")
    async def apply_marks(request: Request) -> Any:
        """{keep: [paths], drop: [paths], keep_to: DIR | null, delete: bool}: copies the keeps to keep_to,
        deletes the drops when `delete`; each with its XMP sidecars. Every path must be inside the
        allowed roots. Returns apply.apply's counts."""
        if request.headers.get("x-bioscan-token") != token:
            return JSONResponse({"error": "missing or wrong X-Bioscan-Token"}, status_code=403)
        try:
            body = json.loads(await request.body())
            keep, drop, keep_to, delete = parse_apply(body)
        except (ValueError, json.JSONDecodeError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        paths = keep + drop + ([keep_to] if keep_to else [])
        denied = [q for q in paths if roots and not any(Path(q).resolve().is_relative_to(r) for r in roots)]
        if denied:
            return JSONResponse({"error": f"paths outside the allowed roots: {denied[:5]}"}, status_code=400)
        result = await asyncio.get_running_loop().run_in_executor(
            None, lambda: applying.apply(keep, drop, keep_to=keep_to, delete=delete))
        log.info("apply: %s", {k: v if isinstance(v, int) else len(v) for k, v in result.items()})
        return result

    return app


def parse_apply(body: Any) -> tuple[list[str], list[str], str | None, bool]:
    """Validates an /apply body; ValueError -> 400."""
    if not isinstance(body, dict):
        raise ValueError("body must be a JSON object")
    lists = {}
    for key in ("keep", "drop"):
        v = body.get(key) or []
        if not isinstance(v, list) or not all(isinstance(x, str) and Path(x).is_absolute() for x in v):
            raise ValueError(f"{key} must be a list of absolute paths")
        lists[key] = v
    keep_to = body.get("keep_to")
    if keep_to is not None and (not isinstance(keep_to, str) or not Path(keep_to).is_absolute()):
        raise ValueError("keep_to must be an absolute path")
    delete = body.get("delete", False)
    if not isinstance(delete, bool):
        raise ValueError("delete must be true or false")
    if lists["keep"] and not keep_to:
        raise ValueError("keep needs keep_to")
    if lists["drop"] and not delete:
        raise ValueError("drop needs delete: true (the service never moves drops; bioscan aesthetic apply --drop-to does)")
    return lists["keep"], lists["drop"], keep_to, delete


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
