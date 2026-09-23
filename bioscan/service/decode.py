"""RAW/JPG -> upright 2048 px RGB image + EXIF GPS/time + sha256 of the original bytes, and on
request a larger "detail" copy of the same frame for species crops.

Runs in a ProcessPoolExecutor worker, so everything here is a plain picklable function.
RAW decode is rawpy (LibRaw applies the camera's rotation itself); half_size demosaic is
plenty for a 2048 px long edge and ~3x faster than a full one.
"""
from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

MAX_EDGE = 2048
DETAIL_EDGE = 3072   # default long edge of the species-crop image; <= MAX_EDGE turns it off
RAW_EXT = {".arw", ".cr2", ".cr3", ".nef", ".nrw", ".dng", ".raf", ".orf", ".rw2", ".pef", ".srw"}
# LibRaw `sizes.flip` -> EXIF orientation value
_FLIP_TO_ORIENTATION = {0: 1, 3: 3, 5: 8, 6: 6}


@dataclass
class Decoded:
    path: str
    sha256: str
    image: Image.Image          # upright RGB, long edge <= MAX_EDGE
    width: int                  # upright full-resolution size
    height: int
    orientation: int            # EXIF orientation value of the original
    lat: float | None
    lon: float | None
    taken_at: str | None        # ISO 8601
    detail: Image.Image | None = None   # same frame, long edge <= detail_edge; None = no larger copy


def _ratio(v) -> float:
    return float(v[0]) / float(v[1]) if isinstance(v, tuple) else float(v)


def gps_from_ifd(gps: dict) -> tuple[float | None, float | None]:
    """EXIF GPS IFD (tags 1-4) -> signed decimal degrees, or (None, None)."""
    try:
        lat = sum(_ratio(x) / 60 ** i for i, x in enumerate(gps[2]))
        lon = sum(_ratio(x) / 60 ** i for i, x in enumerate(gps[4]))
    except (KeyError, TypeError, ValueError, ZeroDivisionError, IndexError):
        return None, None
    if str(gps.get(1, "N")).upper().startswith("S"):
        lat = -lat
    if str(gps.get(3, "E")).upper().startswith("W"):
        lon = -lon
    return lat, lon


def taken_at_from(exif_ifd: dict, base: dict) -> str | None:
    raw = exif_ifd.get(0x9003) or base.get(0x0132)
    if not raw or len(str(raw)) < 19:
        return None
    s = str(raw).strip()
    iso = f"{s[0:4]}-{s[5:7]}-{s[8:10]}T{s[11:19]}"
    offset = exif_ifd.get(0x9011)
    return iso + (str(offset).strip() if offset else "")


def read_exif(data: bytes) -> tuple[float | None, float | None, str | None]:
    exif = Image.Exif()
    try:
        if data[:4] in (b"II*\x00", b"MM\x00*"):     # TIFF-based RAW (ARW, NEF, DNG, ...)
            exif.load(data[:4_000_000])
        else:
            with Image.open(io.BytesIO(data)) as im:
                exif = im.getexif()
        lat, lon = gps_from_ifd(dict(exif.get_ifd(0x8825)))
        return lat, lon, taken_at_from(dict(exif.get_ifd(0x8769)), dict(exif))
    except Exception:  # noqa: BLE001 - missing metadata is normal, never a decode failure
        return None, None, None


def _raw(data: bytes) -> tuple[Image.Image, int, int, int]:
    import rawpy

    with rawpy.imread(io.BytesIO(data)) as raw:
        flip = raw.sizes.flip
        w, h = raw.sizes.width, raw.sizes.height
        pixels = raw.postprocess(use_camera_wb=True, half_size=True, output_bps=8)
    if flip in (5, 6):
        w, h = h, w
    return Image.fromarray(pixels).convert("RGB"), w, h, _FLIP_TO_ORIENTATION.get(flip, 1)


def _raster(data: bytes) -> tuple[Image.Image, int, int, int]:
    with Image.open(io.BytesIO(data)) as im:
        orientation = int(im.getexif().get(0x0112, 1) or 1)
        upright = ImageOps.exif_transpose(im).convert("RGB")
    return upright, upright.width, upright.height, orientation


def fit(image: Image.Image, max_edge: int = MAX_EDGE) -> Image.Image:
    scale = max_edge / max(image.size)
    if scale >= 1:
        return image
    return image.resize((round(image.width * scale), round(image.height * scale)), Image.Resampling.LANCZOS)


def decode(path: str, detail_edge: int | None = None) -> Decoded:
    """`detail_edge` asks for a second, larger copy (long edge <= detail_edge) for species crops;
    none is made when it would not be larger than the 2048 image."""
    data = Path(path).read_bytes()
    image, w, h, orientation = (_raw if Path(path).suffix.lower() in RAW_EXT else _raster)(data)
    lat, lon, taken_at = read_exif(data)
    small = fit(image)
    detail = None
    if detail_edge and detail_edge > MAX_EDGE and max(image.size) > max(small.size):
        detail = fit(image, detail_edge)
    return Decoded(path=path, sha256=hashlib.sha256(data).hexdigest(), image=small, width=w, height=h,
                   orientation=orientation, lat=lat, lon=lon, taken_at=taken_at, detail=detail)


def timed_decode(path: str, detail_edge: int | None = None) -> tuple[Decoded, float]:
    """decode() plus its wall time in ms; what the process pool runs."""
    import time

    t = time.perf_counter()
    d = decode(path, detail_edge)
    return d, (time.perf_counter() - t) * 1000
