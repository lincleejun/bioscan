"""A /run request as a stream of events: decode, per-chunk model turns, products, progress.

One RunQueue per server. It owns the decode pool (rebuilt when a worker dies), the single model
thread, a CPU thread pool for products that do not touch the models, and the model turn: requests
take turns on the models one chunk at a time (FIFO), so a one-image request waits for at most the
chunk in progress, not for a whole field batch. Decoding runs outside the turn, and the decode of
chunk k+1 overlaps the models on chunk k.

The interface is `events(inputs, plan, is_disconnected)` (plan: bioscan.plugin.Plan), which yields bioscan.contract
events, plus the `running` / `queued` counters that /health reports."""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from concurrent.futures import Executor, ProcessPoolExecutor, ThreadPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bioscan import contract, plugin
from bioscan.service.decode import Decoded, timed_decode

log = logging.getLogger("bioscan")


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


@dataclass
class _Image:
    """One decoded image on its way through a chunk: what the products made, what failed, and the
    ms each stage cost it."""
    inp: dict[str, Any]
    dec: Decoded
    timing: dict[str, float]
    products: dict[str, Any] = field(default_factory=dict)
    errors: list[tuple[str, str]] = field(default_factory=list)      # (product, message)


class RunQueue:
    def __init__(self, engine: Any, *, decode_pool: Executor | DecodePool | None = None, decode_workers: int = 4,
                 chunk: int = 32, detail_edge: int | None = None) -> None:
        """`decode_pool`: a DecodePool, or a plain Executor (used as is, never rebuilt); default a
        self-healing ProcessPoolExecutor of `decode_workers`. `detail_edge`: long edge of the larger
        species image, None = off."""
        self.engine, self.chunk, self.detail_edge = engine, chunk, detail_edge
        if isinstance(decode_pool, DecodePool):
            self._decode = decode_pool
        elif decode_pool is not None:
            self._decode = DecodePool(executor=decode_pool)
        else:
            self._decode = DecodePool(lambda: ProcessPoolExecutor(max_workers=decode_workers))
        self._model = ThreadPoolExecutor(max_workers=1, thread_name_prefix="gpu")   # one GPU stream
        self._cpu = ThreadPoolExecutor(max_workers=2, thread_name_prefix="cpu")     # jpg writes, off the model thread
        self._lock = asyncio.Lock()
        self.running = 0            # requests on the models right now (0 or 1)
        self.queued = 0             # requests waiting for their next chunk's turn

    async def events(self, inputs: list[dict[str, Any]], plan: plugin.Plan,
                     is_disconnected: Callable[[], Awaitable[bool]]) -> AsyncIterator[dict[str, Any]]:
        """The /run event stream for validated inputs (path, lat, lon, taken_at) and a plan. Per
        chunk: decode errors, then one progress per product, then each image's result or product
        errors; products, timings and errors are reported in plan.want order, whatever order the
        stages ran in. A client that went away is noticed between chunks, so the current chunk
        always finishes and `done` is not sent. The larger species image is decoded only when a
        planned stage reads "detail"."""
        t0 = time.perf_counter()
        total, ok, failed = len(inputs), 0, 0
        want = plan.want
        done = dict.fromkeys(want, 0)
        info = {**self.engine.info(), "detail_edge": self.detail_edge}
        plugins = self._plugin_ids(plan)
        if plugins:                     # only stages that name what they ran on (a trained head): additive
            info["plugins"] = plugins
        chunks = [inputs[i:i + self.chunk] for i in range(0, total, self.chunk)]
        edge = self.detail_edge if plan.detail else None

        ex, pending = self._submit(chunks[0], edge)
        try:
            for ci, ch in enumerate(chunks):
                decoded = await asyncio.gather(*pending, return_exceptions=True)
                if any(isinstance(d, BrokenProcessPool) for d in decoded) and self._decode.rebuild(ex):
                    decoded = await self._one_by_one(ch, edge)
                ex, pending = self._submit(chunks[ci + 1], edge) if ci + 1 < len(chunks) else (ex, [])
                images = []
                for inp, d in zip(ch, decoded):
                    if isinstance(d, BaseException):
                        failed += 1
                        yield contract.error(inp["path"], None, f"decode: {type(d).__name__}: {d}")
                        continue
                    dec, ms = d
                    images.append(_Image(inp, dec, {"decode": round(ms, 1)}))
                if images:
                    async with self._turn():
                        await self._run_products(images, plan)
                # events are sent after the turn: a slow reader never holds the models
                for p in want:
                    done[p] += len(ch)
                    yield contract.progress(p, done[p], total)
                for im in images:
                    if im.errors:
                        failed += 1
                        for product, message in sorted(im.errors, key=lambda e: want.index(e[0])):
                            yield contract.error(im.inp["path"], product, message)
                        continue
                    ok += 1
                    log.info("%s %s", Path(im.dec.path).name, im.timing)
                    yield contract.result(im.dec.path, im.dec.sha256, {"width": im.dec.width, "height": im.dec.height,
                                                                       "orientation": im.dec.orientation},
                                          info, {p: im.products[p] for p in want if p in im.products},
                                          {k: im.timing[k] for k in ("decode", *want) if k in im.timing})
                if ci + 1 < len(chunks) and await is_disconnected():
                    log.info("client disconnected; stopping after chunk %d/%d", ci + 1, len(chunks))
                    return
            yield contract.done(ok, failed, round((time.perf_counter() - t0) * 1000, 1))
        finally:
            for f in pending:
                f.cancel()

    @staticmethod
    def _plugin_ids(plan: plugin.Plan) -> dict[str, str]:
        """result.engine.plugins: {stage: id} for the stages of the plan that report one."""
        out = {}
        for name in plan.want:
            pid = getattr(plugin.load(plan.manifests[name]), "plugin_id", lambda o: None)(plan.opts[name])
            if pid is not None:
                out[name] = pid
        return out

    @asynccontextmanager
    async def _turn(self) -> AsyncIterator[None]:
        """One chunk's turn on the models. asyncio.Lock is FIFO, so turns alternate between requests."""
        self.queued += 1
        try:
            await self._lock.acquire()
        finally:
            self.queued -= 1
        self.running += 1
        try:
            yield
        finally:
            self.running -= 1
            self._lock.release()

    def _submit(self, ch: list[dict[str, Any]], edge: int | None) -> tuple[Executor, list[asyncio.Future]]:
        """Start decoding `ch`; returns the executor used, so a crash rebuilds only that one."""
        loop = asyncio.get_running_loop()
        ex = self._decode.executor
        return ex, [loop.run_in_executor(ex, timed_decode, inp["path"], edge) for inp in ch]

    async def _one_by_one(self, ch: list[dict[str, Any]], edge: int | None) -> list[Any]:
        """After a worker died: decode files singly, rebuilding the pool after each crash, so the
        file that kills the decoder fails alone."""
        out: list[Any] = []
        for inp in ch:
            ex, (fut,) = self._submit([inp], edge)
            try:
                out.append(await fut)
            except BrokenProcessPool:
                self._decode.rebuild(ex)
                out.append(RuntimeError("the decoder process crashed on this file"))
            except Exception as exc:  # noqa: BLE001
                out.append(exc)
        return out

    async def _run_products(self, images: list[_Image], plan: plugin.Plan) -> None:
        """Stage-first over one chunk, in plan.stages order; every stage sees the same Items, so what
        one provides (Item.facts) reaches the stages after it. A stage that throws on one image costs
        that image only; one that throws, or returns other than one result per image, is an error
        for every image of the chunk (the stream goes on and ends with `done`). Model work runs on the single model thread, the rest on the CPU pool. The
        shared whole-frame pass is timed under the first product (plan.want order) that uses it."""
        loop = asyncio.get_running_loop()
        vecs: Any = None
        gates: list[Any] = [None] * len(images)
        framed = [p for p in plan.want if plan.manifests[p].uses_frame]
        if framed:
            def frame() -> Any:
                t = time.perf_counter()
                out = self.engine.frame([im.dec.image for im in images])
                return out, (time.perf_counter() - t) * 1000 / len(images)
            try:
                (vecs, gates), per = await loop.run_in_executor(self._model, frame)
                for im in images:
                    im.timing[framed[0]] = round(per, 1)
            except Exception as exc:  # noqa: BLE001
                for im in images:
                    for p in framed:
                        im.errors.append((p, f"{type(exc).__name__}: {exc}"))

        items = [plugin.Item(im.dec, im.inp, None if vecs is None else vecs[i], gates[i])
                 for i, im in enumerate(images)]
        for name in plan.stages:
            manifest = plan.manifests[name]
            if manifest.uses_frame and vecs is None:
                outs: list[Any] = [None] * len(images)          # frame failed: already reported per image
            else:
                t = time.perf_counter()
                try:
                    outs = await loop.run_in_executor(self._model if manifest.thread == "model" else self._cpu,
                                                      plugin.load(manifest).run, self.engine, items, plan.opts[name])
                    if not isinstance(outs, list) or len(outs) != len(images):
                        got = len(outs) if isinstance(outs, list) else type(outs).__name__
                        outs = [RuntimeError(f"stage returned {got} results for {len(images)} images")] * len(images)
                except Exception as exc:  # noqa: BLE001 - a stage that throws costs its chunk, not the stream
                    outs = [exc] * len(images)
                per = (time.perf_counter() - t) * 1000 / len(images)
                for im in images:
                    im.timing[name] = round(im.timing.get(name, 0.0) + per, 1)
            for im, out in zip(images, outs):
                if isinstance(out, BaseException):
                    im.errors.append((name, f"{type(out).__name__}: {out}"))
                elif out is not None:
                    im.products[name] = out
