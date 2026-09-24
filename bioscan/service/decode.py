"""RAW/JPG -> upright 2048 px RGB image + EXIF GPS/time + sha256 of the original bytes, and on
request a larger "detail" copy of the same frame for species crops.

Runs in a ProcessPoolExecutor worker, so everything here is a plain picklable function.
RAW decode is rawpy (LibRaw applies the camera's rotation itself); half_size demosaic is
plenty for a 2048 px long edge and ~3x faster than a full one.
"""
from __future__ import annotations

import hashlib
import io
import math
import struct
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

from bioscan.formats import is_raw, subsec
from bioscan.serve_config import MAX_EDGE

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
    camera: str | None = None           # EXIF Make + Model (camera_from); None when the file has neither


def _ratio(v) -> float:
    return float(v[0]) / float(v[1]) if isinstance(v, tuple) else float(v)


def gps_from_ifd(gps: dict) -> tuple[float | None, float | None]:
    """EXIF GPS IFD (tags 1-4) -> signed decimal degrees, or (None, None) when missing, not finite
    or out of range."""
    try:
        lat = sum(_ratio(x) / 60 ** i for i, x in enumerate(gps[2]))
        lon = sum(_ratio(x) / 60 ** i for i, x in enumerate(gps[4]))
    except (KeyError, TypeError, ValueError, ZeroDivisionError, IndexError):
        return None, None
    if str(gps.get(1, "N")).upper().startswith("S"):
        lat = -lat
    if str(gps.get(3, "E")).upper().startswith("W"):
        lon = -lon
    if not (math.isfinite(lat) and math.isfinite(lon) and abs(lat) <= 90 and abs(lon) <= 180):
        return None, None                              # 0/0 rationals give NaN; never pass that to the prior
    return lat, lon


def taken_at_from(exif_ifd: dict, base: dict) -> str | None:
    """DateTimeOriginal (else IFD0 DateTime) as ISO 8601, with its sub-seconds and UTC offset when
    the file has them: '2026-05-01T08:00:00.37-07:00'. Burst frames differ only in the fraction."""
    raw, sub = exif_ifd.get(0x9003), exif_ifd.get(0x9291)       # SubSecTimeOriginal
    if not raw:
        raw, sub = base.get(0x0132), exif_ifd.get(0x9290)        # DateTime, SubSecTime
    if not raw or len(str(raw)) < 19:
        return None
    s = str(raw).strip()
    iso = f"{s[0:4]}-{s[5:7]}-{s[8:10]}T{s[11:19]}" + subsec(sub)
    offset = exif_ifd.get(0x9011)
    return iso + (str(offset).strip() if offset else "")


# ---- metadata containers -----------------------------------------------------------
# Every supported format keeps its EXIF as TIFF IFDs somewhere; these find them. Standard
# library + Pillow only (LibRaw through rawpy exposes a timestamp but no GPS).

_TIFF = (b"II*\x00", b"MM\x00*")
# Olympus ORF and Panasonic RW2: TIFF with their own magic number; Pillow wants the standard one.
_TIFF_VARIANT = {b"IIRO": b"II*\x00", b"IIRS": b"II*\x00", b"MMOR": b"MM\x00*", b"IIU\x00": b"II*\x00"}
_CANON_UUID = bytes.fromhex("85c0b687820f11e08111f4ce462b6a48")   # CR3 moov/uuid box with CMT1-4
_TIFF_SCAN = 4_000_000       # a TIFF RAW's IFDs sit at the front; don't copy the whole file


def container(data: bytes) -> str:
    """Where a file keeps its EXIF: tiff (ARW, NEF, DNG, CR2, PEF, SRW, NRW), orf, rw2, cr3,
    raf (embedded JPEG), or image (whatever Pillow opens: JPEG, PNG, ...)."""
    if data[:4] in _TIFF:
        return "tiff"
    if data[:4] in _TIFF_VARIANT:
        return "rw2" if data[:4] == b"IIU\x00" else "orf"
    if data[4:12] == b"ftypcrx ":
        return "cr3"
    if data[:16] == b"FUJIFILMCCD-RAW ":
        return "raf"
    return "image"


def _ifds(exif: Image.Exif) -> tuple[dict, dict, dict]:
    """(IFD0, EXIF IFD, GPS IFD) of one loaded Exif."""
    return dict(exif), dict(exif.get_ifd(0x8769)), dict(exif.get_ifd(0x8825))


def _tiff(data: bytes) -> Image.Exif:
    exif = Image.Exif()
    exif.load(data)
    return exif


def _jpeg(data: bytes) -> tuple[dict, dict, dict]:
    """An embedded preview JPEG's EXIF (RAF, RW2 JpgFromRaw)."""
    if data[:2] != b"\xff\xd8":
        raise ValueError("embedded preview is not a JPEG")
    with Image.open(io.BytesIO(data)) as im:
        return _ifds(im.getexif())


def _ifd0_blob(data: bytes, tag: int) -> bytes | None:
    """The bytes of one BYTE/UNDEFINED tag in IFD0, sliced from the file by its offset and count
    (no copy of the rest of the file); None when absent."""
    e = "<" if data[:2] == b"II" else ">"
    (at,) = struct.unpack(e + "L", data[4:8])
    (n,) = struct.unpack(e + "H", data[at:at + 2])
    for i in range(n):
        t, typ, count, offset = struct.unpack(e + "HHLL", data[at + 2 + 12 * i:at + 14 + 12 * i])
        if t == tag and typ in (1, 7) and count > 4:
            return data[offset:offset + count]
    return None


def _boxes(data: bytes, start: int, end: int):
    """(type, payload start, payload end) of the ISOBMFF boxes in data[start:end]; stops at the
    first box that does not fit."""
    i = start
    while i + 8 <= end:
        size, kind = struct.unpack(">L4s", data[i:i + 8])
        head = 8
        if size == 1:                                  # 64-bit size follows the type
            if i + 16 > end:
                return
            size, head = struct.unpack(">Q", data[i + 8:i + 16])[0], 16
        elif size == 0:                                # box runs to the end
            size = end - i
        if size < head or i + size > end:
            return
        yield kind, i + head, i + size
        i += size


def _cr3(data: bytes) -> tuple[dict, dict, dict]:
    """Canon CR3: moov > uuid(Canon) > CMT1 (IFD0), CMT2 (EXIF IFD), CMT4 (GPS IFD), each a TIFF."""
    cmt: dict[bytes, dict] = {}
    moov = next(((a, b) for kind, a, b in _boxes(data, 0, len(data)) if kind == b"moov"), None)
    for kind, a, b in _boxes(data, *moov) if moov else ():
        if kind == b"uuid" and data[a:a + 16] == _CANON_UUID:
            for k, x, y in _boxes(data, a + 16, b):
                if k in (b"CMT1", b"CMT2", b"CMT4"):
                    try:                               # one corrupt block must not lose the others
                        cmt[k] = dict(_tiff(data[x:y]))
                    except Exception:  # noqa: BLE001
                        continue
    if not cmt:
        raise ValueError("no readable Canon metadata box")
    return cmt.get(b"CMT1", {}), cmt.get(b"CMT2", {}), cmt.get(b"CMT4", {})


def _metadata(data: bytes) -> tuple[dict, dict, dict]:
    kind = container(data)
    if kind == "tiff":
        return _ifds(_tiff(data[:_TIFF_SCAN]))
    if kind in ("orf", "rw2"):
        base, exif_ifd, gps = _ifds(_tiff(_TIFF_VARIANT[data[:4]] + data[4:_TIFF_SCAN]))
        if not exif_ifd and not gps:
            preview = _ifd0_blob(data, 0x002E)         # RW2 JpgFromRaw carries the full EXIF
            if preview:
                return _jpeg(preview)
        return base, exif_ifd, gps
    if kind == "cr3":
        return _cr3(data)
    if kind == "raf":                                  # header: big-endian JPEG offset, length at 84
        offset, length = struct.unpack(">LL", data[84:92])
        return _jpeg(data[offset:offset + length])
    with Image.open(io.BytesIO(data)) as im:
        return _ifds(im.getexif())


def camera_from(base: dict) -> str | None:
    """IFD0 Make and Model as one string ("SONY ILCE-7RM5"; the make once when the model repeats
    it), or None when neither is there. The burst reducer groups frames by it."""
    make, model = (str(base.get(t) or "").strip("\x00 ") for t in (0x010F, 0x0110))
    if model.lower().startswith(make.lower()):
        make = ""
    return " ".join(x for x in (make, model) if x) or None


def read_meta(data: bytes) -> tuple[float | None, float | None, str | None, str | None]:
    """(lat, lon, taken_at, camera) from any supported file; None for whatever is missing or
    unreadable. Never raises."""
    try:
        base, exif_ifd, gps = _metadata(data)
        lat, lon = gps_from_ifd(gps)
        return lat, lon, taken_at_from(exif_ifd, base), camera_from(base)
    except Exception:  # noqa: BLE001 - missing metadata is normal, never a decode failure
        return None, None, None, None


def read_exif(data: bytes) -> tuple[float | None, float | None, str | None]:
    """(lat, lon, taken_at) from any supported file; None for whatever is missing or unreadable.
    Never raises."""
    return read_meta(data)[:3]


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
    image, w, h, orientation = (_raw if is_raw(path) else _raster)(data)
    lat, lon, taken_at, camera = read_meta(data)
    small = fit(image)
    detail = None
    if detail_edge and detail_edge > MAX_EDGE and max(image.size) > max(small.size):
        detail = fit(image, detail_edge)
    return Decoded(path=path, sha256=hashlib.sha256(data).hexdigest(), image=small, width=w, height=h,
                   orientation=orientation, lat=lat, lon=lon, taken_at=taken_at, detail=detail, camera=camera)


def timed_decode(path: str, detail_edge: int | None = None) -> tuple[Decoded, float]:
    """decode() plus its wall time in ms; what the process pool runs."""
    import time

    t = time.perf_counter()
    d = decode(path, detail_edge)
    return d, (time.perf_counter() - t) * 1000


def main(argv: list[str] | None = None) -> int:
    """`python -m bioscan.service.decode PATH... [-r]`: for each photo, where its EXIF lives and the
    lat, lon and capture time read from it, then a count per extension. Reads metadata only (no
    demosaic), so it is quick over a card of real camera files."""
    import argparse
    from collections import Counter

    from bioscan.formats import SCAN_EXT, list_images

    p = argparse.ArgumentParser(prog="python -m bioscan.service.decode",
                                description="Print the GPS and capture time bioscan reads from each photo.")
    p.add_argument("paths", nargs="+", help="files or folders")
    p.add_argument("-r", "--recursive", action="store_true")
    a = p.parse_args(argv)
    files, gps, when = Counter(), Counter(), Counter()
    print("ext\tcontainer\tlat\tlon\ttaken_at\tpath")
    for path in (f for root in a.paths for f in list_images(root, SCAN_EXT, a.recursive)):
        ext = Path(path).suffix.lstrip(".").upper()
        files[ext] += 1
        try:
            data = Path(path).read_bytes()
        except OSError as e:
            print(f"{ext}\tunreadable\t-\t-\t-\t{path}\t{e.strerror}")
            continue
        lat, lon, taken_at = read_exif(data)
        gps[ext] += lat is not None
        when[ext] += taken_at is not None
        coords = (f"{lat:.6f}", f"{lon:.6f}") if lat is not None else ("-", "-")
        print(f"{ext}\t{container(data)}\t{coords[0]}\t{coords[1]}\t{taken_at or '-'}\t{path}")
    for ext in sorted(files):
        print(f"# {ext}: {files[ext]} files, {gps[ext]} with GPS, {when[ext]} with capture time")
    return 0 if files else 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
