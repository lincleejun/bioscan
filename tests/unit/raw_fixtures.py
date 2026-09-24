"""Tiny synthetic camera files for EXIF tests: just enough container structure to carry GPS and
capture time, no pixel data. No real camera files are committed; these are built byte by byte.

- `tiff(...)`: a TIFF with IFD0 and optional EXIF and GPS sub-IFDs, any 4-byte magic (ARW, NEF,
  DNG, CR2, PEF, SRW use the standard one; ORF and RW2 have their own).
- `cr3(...)`: ISOBMFF `ftyp crx` + `moov` > Canon `uuid` > CMT1 (IFD0) / CMT2 (EXIF) / CMT4 (GPS).
- `raf(...)`: Fujifilm header whose offset/length fields point at an embedded JPEG with EXIF.
- `jpeg(...)`: a small JPEG carrying the same EXIF.
"""
from __future__ import annotations

import io
import struct

ASCII, SHORT, LONG, RATIONAL, UNDEFINED = 2, 3, 4, 5, 7
CANON_UUID = bytes.fromhex("85c0b687820f11e08111f4ce462b6a48")

# 37°30'0.36"N 122°15'0"W, the values every fixture carries
GPS = {1: (ASCII, "N"), 2: (RATIONAL, [(37, 1), (30, 1), (36, 100)]),
       3: (ASCII, "W"), 4: (RATIONAL, [(122, 1), (15, 1), (0, 1)])}
LAT, LON = 37 + 30 / 60 + 0.36 / 3600, -(122 + 15 / 60)


def exif_ifd(when="2026:05:01 08:00:00", offset="-07:00", subsec=None) -> dict:
    out = {0x9003: (ASCII, when)}
    if offset:
        out[0x9011] = (ASCII, offset)
    if subsec is not None:
        out[0x9291] = (ASCII, subsec)
    return out


def _value(e: str, typ: int, value) -> tuple[int, bytes]:
    if typ == ASCII:
        raw = value.encode() + b"\0"
        return len(raw), raw
    if typ == UNDEFINED:
        return len(value), bytes(value)
    if typ == RATIONAL:
        flat = [x for pair in value for x in pair]
        return len(value), struct.pack(e + "L" * len(flat), *flat)
    vals = value if isinstance(value, (list, tuple)) else [value]
    return len(vals), struct.pack(e + ("H" if typ == SHORT else "L") * len(vals), *vals)


def ifd(entries: dict[int, tuple[int, object]], start: int, e: str = "<") -> bytes:
    """One IFD placed at file offset `start`, its out-of-line values right after it."""
    head, data = [struct.pack(e + "H", len(entries))], b""
    data_at = start + 2 + 12 * len(entries) + 4
    for tag in sorted(entries):
        typ, value = entries[tag]
        count, raw = _value(e, typ, value)
        if len(raw) <= 4:
            field = raw.ljust(4, b"\0")
        else:
            field = struct.pack(e + "L", data_at + len(data))
            data += raw + b"\0" * (len(raw) % 2)
        head.append(struct.pack(e + "HHL", tag, typ, count) + field)
    head.append(struct.pack(e + "L", 0))
    return b"".join(head) + data


def tiff(ifd0: dict | None = None, exif: dict | None = None, gps: dict | None = None,
         magic: bytes | None = None, big_endian: bool = False) -> bytes:
    e = ">" if big_endian else "<"
    magic = magic or (b"MM\x00*" if big_endian else b"II*\x00")
    ifd0 = dict(ifd0 or {})
    if exif is not None:
        ifd0[0x8769] = (LONG, 0)
    if gps is not None:
        ifd0[0x8825] = (LONG, 0)
    # IFD sizes do not depend on the pointer values: lay out once, then fill the pointers in
    at = 8 + len(ifd(ifd0, 8, e))
    blocks = []
    for tag, sub in ((0x8769, exif), (0x8825, gps)):
        if sub is not None:
            at += at % 2
            ifd0[tag] = (LONG, at)
            blocks.append((at, ifd(sub, at, e)))
            at += len(blocks[-1][1])
    out = bytearray(magic + struct.pack(e + "L", 8) + ifd(ifd0, 8, e))
    for pos, raw in blocks:
        out += b"\0" * (pos - len(out)) + raw
    return bytes(out)


def jpeg(exif_tiff: bytes | None = None, size=(16, 12), orientation: int | None = None) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    kw = {"exif": b"Exif\x00\x00" + exif_tiff} if exif_tiff else {}
    Image.new("RGB", size, (90, 120, 30)).save(buf, "JPEG", **kw)
    return buf.getvalue()


def box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">L", 8 + len(payload)) + kind + payload


def cr3(cmt1: bytes | None, cmt2: bytes | None, cmt4: bytes | None, large_moov: bool = False) -> bytes:
    canon = box(b"CNCV", b"CanonCR3_001/01.09.00/00.00.00")
    for kind, payload in ((b"CMT1", cmt1), (b"CMT2", cmt2), (b"CMT3", tiff({1: (SHORT, 0)})), (b"CMT4", cmt4)):
        if payload is not None:
            canon += box(kind, payload)
    uuid = struct.pack(">L", 8 + 16 + len(canon)) + b"uuid" + CANON_UUID + canon
    moov_body = box(b"uuid", b"\x00" * 16 + b"other-vendor") + uuid + box(b"trak", b"\x00" * 32)
    if large_moov:   # 64-bit box size: size field 1, then an 8-byte size
        moov = struct.pack(">L", 1) + b"moov" + struct.pack(">Q", 16 + len(moov_body)) + moov_body
    else:
        moov = box(b"moov", moov_body)
    return box(b"ftyp", b"crx \x00\x00\x00\x01crx isom") + moov + box(b"mdat", b"\x00" * 64)


def raf(embedded: bytes) -> bytes:
    head = bytearray(b"FUJIFILMCCD-RAW 0201FF383501" + b"X-T5".ljust(32, b"\0") + b"0100" + b"\0" * 20)
    at = 160
    head += struct.pack(">LL", at, len(embedded)) + b"\0" * 16          # JPEG offset/length, then CFA fields
    head += b"\0" * (at - len(head))
    return bytes(head) + embedded + b"\0" * 32
