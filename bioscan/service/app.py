"""HTTP surface: /health, /products, /run (NDJSON stream), the model lock and queue.

Requests take turns on the models one chunk at a time (FIFO), so a one-image request waits for at
most the chunk in progress, not for a whole field batch. Decoding runs outside the lock."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from concurrent.futures import Executor, ProcessPoolExecutor, ThreadPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from contextlib import AbstractAsyncContextManager, asynccontextmanager, nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from bioscan import contract, serve_config
from bioscan.service import products
from bioscan.service.decode import timed_decode

log = logging.getLogger("bioscan")
ORDER = contract.PRODUCTS


@dataclass
class State:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    running: int = 0            # requests on the models right now (0 or 1)
    queued: int = 0             # requests waiting for their next chunk's turn


@asynccontextmanager
async def hold_models(state: State) -> AsyncIterator[None]:
    """One chunk's turn on the models. asyncio.Lock is FIFO, so turns alternate between requests."""
    state.queued += 1
    try:
        await state.lock.acquire()
    finally:
        state.queued -= 1
    state.running += 1
    try:
        yield
    finally:
        state.running -= 1
        state.lock.release()


class DecodePool:
    """The decode executor, rebuilt when a worker dies (a RAW that crashes LibRaw would otherwise
    leave a BrokenProcessPool behind for every later request). A plain Executor handed in by a
    caller is used as is and never rebuilt."""

    def __init__(self, factory: Callable[[], Executor] | None = None, executor: Executor | None = None) -> None:
        self.factory = factory
        self.executor = executor if executor is not None else factory()  # type: ignore[misc]

    def rebuild(self, broken: Executor) -> bool:
        """Replace `broken` if it is still the current executor. Several requests can see the same
        crash; only the first rebuilds, the others just move on to the new executor. Nothing is
        cancelled: a broken pool has already failed its own futures."""
        if self.factory is None:
            return False
        if self.executor is broken:
            self.executor = self.factory()
            broken.shutdown(wait=False)
            log.warning("decode pool rebuilt after a worker died")
        return True


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


async def run_events(engine: Any, inputs: list[dict[str, Any]], want: list[str], opts: dict[str, dict[str, Any]],
                     *, decode_pool: Executor | DecodePool, gpu: Executor, chunk: int,
                     is_disconnected: Callable[[], Awaitable[bool]],
                     detail_edge: int | None = None, cpu: Executor | None = None,
                     hold: Callable[[], AbstractAsyncContextManager[Any]] | None = None
                     ) -> AsyncIterator[dict[str, Any]]:
    """The /run event stream. Decode of chunk k+1 overlaps the models on chunk k; a client that
    went away is noticed between chunks, so the current chunk always finishes. The larger species
    image (`detail_edge`) is decoded only when identify is wanted."""
    loop = asyncio.get_running_loop()
    t0 = time.perf_counter()
    total, ok, failed = len(inputs), 0, 0
    done = dict.fromkeys(want, 0)
    info = {**engine.info(), "detail_edge": detail_edge}
    chunks = [inputs[i:i + chunk] for i in range(0, total, chunk)]
    edge = detail_edge if "identify" in want else None

    pool = decode_pool if isinstance(decode_pool, DecodePool) else DecodePool(executor=decode_pool)

    def submit(ch: list[dict[str, Any]]) -> tuple[Executor, list[asyncio.Future]]:
        ex = pool.executor
        return ex, [loop.run_in_executor(ex, timed_decode, inp["path"], edge) for inp in ch]

    async def one_by_one(ch: list[dict[str, Any]]) -> list[Any]:
        """After a worker died: decode files singly, rebuilding the pool after each crash, so the
        file that kills the decoder fails alone."""
        out: list[Any] = []
        for inp in ch:
            ex, (fut,) = submit([inp])
            try:
                out.append(await fut)
            except BrokenProcessPool:
                pool.rebuild(ex)
                out.append(RuntimeError("the decoder process crashed on this file"))
            except Exception as exc:  # noqa: BLE001
                out.append(exc)
        return out

    ex, pending = submit(chunks[0])
    try:
        for ci, ch in enumerate(chunks):
            decoded = await asyncio.gather(*pending, return_exceptions=True)
            if any(isinstance(d, BrokenProcessPool) for d in decoded) and pool.rebuild(ex):
                decoded = await one_by_one(ch)
            ex, pending = submit(chunks[ci + 1]) if ci + 1 < len(chunks) else (ex, [])
            items = []
            for inp, d in zip(ch, decoded):
                if isinstance(d, BaseException):
                    failed += 1
                    yield contract.error(inp["path"], None, f"decode: {type(d).__name__}: {d}")
                    continue
                dec, ms = d
                items.append({"inp": inp, "dec": dec, "products": {}, "errors": [],
                              "timing": {"decode": round(ms, 1)}})
            if items:
                async with hold() if hold is not None else nullcontext():
                    # collected under the lock, sent after it: a slow reader never holds the models
                    evs = [ev async for ev in _run_chunk(engine, items, want, opts, loop, gpu, len(ch), done,
                                                         total, cpu)]
                for ev in evs:
                    yield ev
            for it in items:
                if it["errors"]:
                    failed += 1
                    for product, message in it["errors"]:
                        yield contract.error(it["inp"]["path"], product, message)
                    continue
                ok += 1
                dec = it["dec"]
                log.info("%s %s", Path(dec.path).name, it["timing"])
                yield contract.result(dec.path, dec.sha256,
                                      {"width": dec.width, "height": dec.height, "orientation": dec.orientation},
                                      info, it["products"], it["timing"])
            if not items:
                for p in want:
                    done[p] += len(ch)
                    yield contract.progress(p, done[p], total)
            if ci + 1 < len(chunks) and await is_disconnected():
                log.info("client disconnected; stopping after chunk %d/%d", ci + 1, len(chunks))
                return
        yield contract.done(ok, failed, round((time.perf_counter() - t0) * 1000, 1))
    finally:
        for f in pending:
            f.cancel()


async def _run_chunk(engine: Any, items: list[dict[str, Any]], want: list[str], opts: dict[str, dict[str, Any]],
                     loop: asyncio.AbstractEventLoop, gpu: Executor, n_chunk: int, done: dict[str, int],
                     total: int, cpu: Executor | None = None) -> AsyncIterator[dict[str, Any]]:
    """Product-first over one chunk (products.REGISTRY order); a product that throws on one image
    costs that image only. Model work runs on the single model thread, the rest on `cpu`."""
    vecs: Any = None
    gates: list[Any] = [None] * len(items)
    framed = [p for p in want if products.REGISTRY[p].uses_frame]
    if framed:
        def frame() -> Any:
            t = time.perf_counter()
            out = engine.frame([it["dec"].image for it in items])
            return out, (time.perf_counter() - t) * 1000 / len(items)
        try:
            (vecs, gates), per = await loop.run_in_executor(gpu, frame)
            for it in items:
                it["timing"][framed[0]] = round(per, 1)
        except Exception as exc:  # noqa: BLE001
            for it in items:
                for p in framed:
                    it["errors"].append((p, f"{type(exc).__name__}: {exc}"))

    for name in want:
        product = products.REGISTRY[name]
        if product.uses_frame and vecs is None:
            outs: list[Any] = [None] * len(items)          # frame failed: already reported per image
        else:
            batch = [products.Item(it["dec"], it["inp"], None if vecs is None else vecs[i], gates[i])
                     for i, it in enumerate(items)]
            t = time.perf_counter()
            outs = await loop.run_in_executor(gpu if product.on_model_thread else cpu, product.run, engine, batch,
                                              opts[name])
            per = (time.perf_counter() - t) * 1000 / len(items)
            for it in items:
                it["timing"][name] = round(it["timing"].get(name, 0.0) + per, 1)
        for it, out in zip(items, outs):
            if isinstance(out, BaseException):
                it["errors"].append((name, f"{type(out).__name__}: {out}"))
            elif out is not None:
                it["products"][name] = out
        done[name] += n_chunk
        yield contract.progress(name, done[name], total)


def outside_roots(inputs: list[dict[str, Any]], want: list[str], opts: dict[str, dict[str, Any]],
                  roots: list[Path]) -> list[str]:
    """Paths the request would read or write that are not inside one of `roots` (symlinks and
    `..` resolved first, so neither escapes). No roots = everything allowed."""
    if not roots:
        return []
    paths = [inp["path"] for inp in inputs] + [w for p in want for w in products.REGISTRY[p].writes(opts[p])]
    return [p for p in paths if not any(Path(p).resolve().is_relative_to(r) for r in roots)]


def create_app(engine: Any, *, decode_pool: Executor | DecodePool | None = None, chunk: int = serve_config.CHUNK,
               decode_workers: int = serve_config.DECODE_WORKERS, detail_edge: int | None = serve_config.DETAIL_EDGE,
               allow_roots: list[str] | None = None) -> FastAPI:
    app = FastAPI(title="bioscan")
    state = State()
    pool = decode_pool or DecodePool(lambda: ProcessPoolExecutor(max_workers=decode_workers))
    gpu = ThreadPoolExecutor(max_workers=1, thread_name_prefix="gpu")   # one GPU stream
    cpu = ThreadPoolExecutor(max_workers=2, thread_name_prefix="cpu")   # jpg writes, off the model thread
    app.state.bioscan = state
    roots = [Path(r).expanduser().resolve() for r in allow_roots or []]

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "device": engine.device, "models_loaded": engine.loaded(),
                "running": state.running, "queued": state.queued}

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
            async for ev in run_events(engine, inputs, want, opts, decode_pool=pool, gpu=gpu, chunk=chunk,
                                       is_disconnected=request.is_disconnected, detail_edge=detail_edge, cpu=cpu,
                                       hold=lambda: hold_models(state)):
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
    app = create_app(Engine(), chunk=config.chunk, decode_workers=config.decode_workers,
                     detail_edge=config.detail_edge, allow_roots=config.allow_roots)
    uvicorn.run(app, host=config.host, port=config.port, workers=1)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="bioscan-serve")
    ap.add_argument("--host", default=serve_config.HOST)
    ap.add_argument("--port", type=int, default=serve_config.PORT)
    ap.add_argument("--decode-workers", type=int,
                    help=f"default: env BIOSCAN_DECODE_WORKERS, else {serve_config.DECODE_WORKERS}")
    ap.add_argument("--chunk", type=int, help=f"default: env BIOSCAN_CHUNK, else {serve_config.CHUNK}")
    ap.add_argument("--detail-edge", type=int,
                    help=f"long edge of the species-crop image; <= {serve_config.MAX_EDGE} turns it off. "
                         f"default: env BIOSCAN_DETAIL_EDGE, else {serve_config.DETAIL_EDGE}")
    ap.add_argument("--allow-root", action="append",
                    help="only read inputs / write jpg under this directory (repeatable). "
                         "default: env BIOSCAN_ALLOW_ROOTS (os.pathsep-separated), else no limit")
    args = ap.parse_args(argv)
    serve(serve_config.resolve(host=args.host, port=args.port, decode_workers=args.decode_workers, chunk=args.chunk,
                               detail_edge=args.detail_edge, allow_roots=args.allow_root))


if __name__ == "__main__":
    main()
