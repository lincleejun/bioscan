"""Which photo files bioscan reads: the one list the decoder and the folder scans (`bioscan run`,
`bioscan gt folders`) share, and the scan itself.

Standard library only: the CLI imports it without Pillow, rawpy or the service.
"""
from __future__ import annotations

import os

# Camera RAW, decoded by rawpy/LibRaw. Everything else goes through Pillow.
RAW_EXT = frozenset({"arw", "cr2", "cr3", "dng", "nef", "nrw", "orf", "pef", "raf", "rw2", "srw"})
# Rasters a folder scan picks up. Pillow decodes more (PNG, TIFF), but only by explicit --ext.
RASTER_EXT = frozenset({"jpg", "jpeg"})
SCAN_EXT = RAW_EXT | RASTER_EXT
DEFAULT_EXT = ",".join(sorted(SCAN_EXT))    # the --ext default


def parse_ext(spec: str) -> set[str]:
    """--ext 'ARW, .jpg' -> {'arw', 'jpg'}: case, spaces and a leading dot do not matter."""
    return {e.strip().lower().lstrip(".") for e in spec.split(",") if e.strip()}


def subsec(value) -> str:
    """EXIF SubSecTime* ('37', '370 ', 37) -> '.37' / '.370', the fraction to put after the seconds
    of an ISO capture time; '' when absent or not digits. Burst frames differ only here."""
    s = str(value).strip() if value is not None else ""
    return "." + s if s and s.isascii() and s.isdigit() else ""


def is_raw(path: str) -> bool:
    return os.path.splitext(path)[1].lower().lstrip(".") in RAW_EXT


def list_images(root: str, exts: set[str] | frozenset[str], recursive: bool) -> list[str]:
    """Absolute paths of files under root (or root itself) whose extension, in any case, is in
    `exts` (lower case, no dot); sorted, dotfiles skipped."""
    root = os.path.abspath(root)
    if os.path.isfile(root):
        return [root]
    out = []
    for dirpath, dirnames, files in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".")) if recursive else []
        out += [os.path.join(dirpath, f) for f in files
                if not f.startswith(".") and f.rsplit(".", 1)[-1].lower() in exts]
    return sorted(out)
