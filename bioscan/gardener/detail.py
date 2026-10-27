"""Original-pixel detail (AfterShooting PRD §08 第三级, AC06; contract revision 5C).

A crop of the ORIGINAL pixels of a region given as 0-1 fractions of the upright image, so a person can
judge focus beyond the 1600 px preview. Only reads originals; anything written goes under cache/detail,
so `POST /storage/clear-cache` and `GET /storage` cover it.

- RAW: a full LibRaw demosaic. LibRaw turns it upright by `sizes.flip` itself, the same as BioScan's
  decode, so the subject boxes line up. The result is kept as an uncompressed .npy (memory-mapped:
  every later crop of that photo takes milliseconds), capped at DETAIL_CACHE_BYTES, oldest first.
- JPEG / PNG / TIFF: Pillow, upright by EXIF orientation, decoded on each request (no copy needed:
  the file already is the original pixels).

Decodes run on their own pool of DETAIL_WORKERS threads (rawpy releases the GIL while decoding), so
grid and mark requests never queue behind a slow RAW; a waiting detail request holds one request thread
(ponytail: async route + asyncio.wait_for(wrap_future) if that ever matters). A queued request that a newer one for the same asset superseded is skipped; a
caller waits at most DETAIL_TIMEOUT_S, and a request still queued then is cancelled."""
from __future__ import annotations

import io
import itertools
import math
import os
import tempfile
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from bioscan.formats import is_raw
from bioscan.gardener.preview import FLIP_TO_ORIENTATION

DETAIL_WORKERS = 2
DETAIL_TIMEOUT_S = 90.0
DETAIL_CACHE_BYTES = 2 << 30          # a 24 MP RAW is ~72 MB decoded, a 61 MP one ~183 MB
DEFAULT_EDGE, MAX_EDGE = 2048, 4096   # long edge of the returned crop (never enlarged)


class Superseded(Exception):
    """A newer detail request for the same asset arrived while this one was queued."""


@dataclass
class Detail:
    jpeg: bytes
    file: str                              # the file read: raw | image
    decoder: str                           # libraw-full | pillow
    orientation: int                       # EXIF orientation turned upright (1 = none)
    full: tuple[int, int]                  # upright size of the original
    crop: tuple[int, int, int, int]        # the region in original pixels
    out: tuple[int, int]                   # size of the returned JPEG
    cache: str                             # hit | miss | none
    ms: int

    def headers(self) -> dict[str, str]:
        """What the image is, for the UI's label (contract revision 5C)."""
        cw = self.crop[2] - self.crop[0]
        return {"X-Detail-Source": "original", "X-Detail-File": self.file, "X-Detail-Decoder": self.decoder,
                "X-Detail-Orientation": str(self.orientation), "X-Detail-Full": f"{self.full[0]}x{self.full[1]}",
                "X-Detail-Crop": ",".join(map(str, self.crop)), "X-Detail-Size": f"{self.out[0]}x{self.out[1]}",
                "X-Detail-Scale": f"{self.out[0] / cw:.4f}", "X-Detail-Cache": self.cache,
                "X-Detail-Ms": str(self.ms), "Cache-Control": "no-store"}


def parse_xyxy(s: str) -> tuple[float, float, float, float]:
    """'x0,y0,x1,y1' as 0-1 fractions of the upright image, x0 < x1 and y0 < y1."""
    try:
        v = tuple(float(x) for x in s.split(","))
    except ValueError:
        v = ()
    if len(v) != 4 or not all(0 <= x <= 1 for x in v) or not (v[0] < v[2] and v[1] < v[3]):
        raise ValueError("xyxy 须为 0–1 的 x0,y0,x1,y1，且 x0<x1、y0<y1")
    return v  # type: ignore[return-value]


def pixel_box(xyxy: tuple[float, float, float, float], w: int, h: int) -> tuple[int, int, int, int]:
    """Fractions -> the covering pixel box, inside the image and at least 1 px."""
    px = [round(v * n, 6) for v, n in zip(xyxy, (w, h, w, h))]     # 0.28 * 300 is 84.00000000000001
    x0, y0 = min(math.floor(px[0]), w - 1), min(math.floor(px[1]), h - 1)
    return x0, y0, max(x0 + 1, min(w, math.ceil(px[2]))), max(y0 + 1, min(h, math.ceil(px[3])))


def _encode(region: Image.Image, edge: int) -> tuple[bytes, tuple[int, int]]:
    if max(region.size) > edge:
        region.thumbnail((edge, edge), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    region.convert("RGB").save(buf, "JPEG", quality=92)
    return buf.getvalue(), region.size


def _decode_raw(path: str) -> tuple[np.ndarray, int]:
    """Full demosaic, upright (LibRaw applies sizes.flip), and the orientation that was applied."""
    import rawpy

    with rawpy.imread(path) as raw:
        orientation = FLIP_TO_ORIENTATION.get(raw.sizes.flip, 1)
        return raw.postprocess(use_camera_wb=True, output_bps=8), orientation


class Details:
    """The detail pool and the cache of decoded RAWs under `cache_dir`/detail."""

    def __init__(self, cache_dir: Path, *, workers: int = DETAIL_WORKERS, cap_bytes: int = DETAIL_CACHE_BYTES):
        self.dir, self.cap = Path(cache_dir) / "detail", cap_bytes
        self.pool = ThreadPoolExecutor(workers, thread_name_prefix="gardener-detail")
        self._seq, self._lock = itertools.count(1), threading.Lock()
        self._latest: dict[str, int] = {}
        self._asset_locks: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)

    def close(self) -> None:
        self.pool.shutdown(wait=False, cancel_futures=True)

    def get(self, asset_id: str, path: str, xyxy: tuple[float, float, float, float], edge: int = DEFAULT_EDGE,
            timeout: float | None = None) -> Detail:
        """The crop as JPEG. Raises Superseded, TimeoutError, or the decoder's error."""
        with self._lock:
            token = self._latest[asset_id] = next(self._seq)
        fut = self.pool.submit(self._run, asset_id, token, path, xyxy, edge)
        try:
            return fut.result(timeout=DETAIL_TIMEOUT_S if timeout is None else timeout)
        except TimeoutError:
            fut.cancel()        # still queued: never runs; already decoding: finishes and fills the cache
            raise

    def region(self, asset_id: str, path: str, xyxy: tuple[float, float, float, float],
               timeout: float | None = None) -> tuple[Image.Image, str]:
        """The original pixels of `xyxy` and the file read (raw | image), on the same pool and RAW cache as
        get() but never superseded by, nor superseding, the viewer's requests (contract revision 7C)."""
        fut = self.pool.submit(self._region, asset_id, path, xyxy)
        try:
            region, file, *_ = fut.result(timeout=DETAIL_TIMEOUT_S if timeout is None else timeout)
        except TimeoutError:
            fut.cancel()
            raise
        return region, file

    def _region(self, asset_id: str, path: str, xyxy: tuple[float, float, float, float]
                ) -> tuple[Image.Image, str, str, int, tuple[int, int], tuple[int, int, int, int], str]:
        """(region, file, decoder, orientation, upright size, pixel box, cache)."""
        if is_raw(path):
            pixels, orientation, cache = self._raw_pixels(asset_id, path)
            h, w = pixels.shape[:2]
            box = pixel_box(xyxy, w, h)
            region = Image.fromarray(np.ascontiguousarray(pixels[box[1]:box[3], box[0]:box[2]]))
            return region, "raw", "libraw-full", orientation, (w, h), box, cache
        with Image.open(path) as im:
            orientation = int(im.getexif().get(0x0112, 1) or 1)
            upright = ImageOps.exif_transpose(im)
        w, h = upright.size
        box = pixel_box(xyxy, w, h)
        return upright.crop(box), "image", "pillow", orientation, (w, h), box, "none"

    def _run(self, asset_id: str, token: int, path: str, xyxy: tuple[float, float, float, float],
             edge: int) -> Detail:
        if self._latest.get(asset_id) != token:
            raise Superseded(asset_id)
        t0 = time.perf_counter()
        region, file, decoder, orientation, (w, h), box, cache = self._region(asset_id, path, xyxy)
        jpeg, out = _encode(region, edge)
        return Detail(jpeg, file, decoder, orientation, (w, h), box, out, cache,
                      round((time.perf_counter() - t0) * 1000))

    def _raw_pixels(self, asset_id: str, path: str) -> tuple[np.ndarray, int, str]:
        """The decoded RAW from the cache (keyed by size + mtime, so a changed file decodes again), else
        decoded now and stored. One decode per asset at a time: a second request waits and hits."""
        st = os.stat(path)
        d = self.dir / asset_id[2:4]
        stem = f"{asset_id}.{st.st_size:x}-{st.st_mtime_ns:x}"
        with self._lock:
            asset_lock = self._asset_locks[asset_id]
        with asset_lock:
            for f in d.glob(f"{stem}.o*.npy"):
                try:
                    os.utime(f)                                  # recently used: evicted last
                    return np.load(f, mmap_mode="r"), int(f.name.rsplit(".o", 1)[1][:-4]), "hit"
                except (OSError, ValueError):
                    f.unlink(missing_ok=True)                    # cleared or torn: decode again
            pixels, orientation = _decode_raw(path)
            d.mkdir(parents=True, exist_ok=True)
            dest = d / f"{stem}.o{orientation}.npy"
            fd, tmp = tempfile.mkstemp(suffix=".npy.part", dir=d)
            try:
                with os.fdopen(fd, "wb") as fh:
                    np.save(fh, np.ascontiguousarray(pixels))
                os.replace(tmp, dest)
            except BaseException:
                Path(tmp).unlink(missing_ok=True)
                raise
            for old in d.glob(f"{asset_id}.*.npy"):              # an older version of the same file
                if old != dest:
                    old.unlink(missing_ok=True)
            self._evict(keep=dest)
            return pixels, orientation, "miss"

    def _evict(self, keep: Path) -> None:
        """Oldest first until the decoded RAWs fit the cap; never the one just written."""
        # ponytail: a full walk per decode; fine for the few hundred files a 2 GB cap holds
        files = []
        for f in self.dir.rglob("*.npy"):
            try:
                s = f.stat()
            except OSError:
                continue
            files.append((s.st_mtime, s.st_size, f))
        total = sum(size for _, size, _ in files)
        for _, size, f in sorted(files, key=lambda x: x[0]):
            if total <= self.cap:
                break
            if f != keep:
                f.unlink(missing_ok=True)
                total -= size
