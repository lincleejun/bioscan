"""Discovery (docs/gardener/contract.md §4): the media files under a source folder, RAW+JPEG pairs as
one asset, what was skipped and why, and a quick file identity. Reads directory entries and file
stats only; never writes. Standard library only.

discover() is stateless: each Discovered lists all its files with their roles, so the caller can
match an existing asset by any registered file path. A RAW found alone and later next to its JPEG
just gets a new pair_key (<dir>/<stem>|rawjpeg) and primary (the JPEG); its files still include
the RAW."""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field

from bioscan.formats import RAW_EXT

RAW = frozenset("." + e for e in RAW_EXT)
JPEG = frozenset({".jpg", ".jpeg"})
# HEIC is left out: Pillow cannot open it without a plugin this project does not ship
PHOTO_EXT: frozenset[str] = JPEG | {".png", ".tif", ".tiff"} | RAW
VIDEO_EXT: frozenset[str] = frozenset({".mov", ".mp4", ".m4v", ".avi", ".mts", ".mkv"})
SIDECAR_EXT = frozenset({".xmp", ".json", ".aae", ".thm", ".pp3", ".dop", ".on1", ".lrv"})
SIDECAR_NAMES = frozenset({".ds_store", "thumbs.db", "desktop.ini"})
MIB = 1 << 20


@dataclass
class FileInfo:
    path: str
    role: str          # raw | jpeg | image | video
    ext: str           # lower case, with the dot
    size: int
    mtime_ns: int


@dataclass
class Discovered:
    pair_key: str      # <dir>/<file name, lower case>; a RAW+JPEG pair: <dir>/<stem, lower case>|rawjpeg
    media: str         # photo | video
    primary: str       # the file previews and analysis read (the JPEG of a pair)
    files: list[FileInfo] = field(default_factory=list)


@dataclass
class Skipped:
    path: str
    reason: str        # unsupported | sidecar | hidden | unreadable | symlink (a linked folder, not followed)


def _role(ext: str) -> str | None:
    if ext in RAW:
        return "raw"
    if ext in JPEG:
        return "jpeg"
    if ext in PHOTO_EXT:
        return "image"
    return "video" if ext in VIDEO_EXT else None


def discover(root: str) -> tuple[list[Discovered], list[Skipped]]:
    """Every media file under `root`, recursively, sorted by path. Hidden files and folders, sidecars
    and unknown types are Skipped (a hidden or symlinked folder once, not its contents). In one folder, a RAW and
    a JPEG whose stems match (case-insensitively) are one photo whose primary is the JPEG (with more
    than one of a kind, the first by extension pairs); every other file is its own asset. pair_keys
    never repeat (contract §10 X1)."""
    root = os.path.abspath(root)
    found: list[Discovered] = []
    skipped: list[Skipped] = []
    if not os.path.isdir(root):
        return [], [Skipped(root, "unreadable")]

    def unreadable(err: OSError) -> None:
        skipped.append(Skipped(err.filename or root, "unreadable"))

    for dirpath, dirnames, names in os.walk(root, onerror=unreadable):
        for d in dirnames:
            if d.startswith("."):
                skipped.append(Skipped(os.path.join(dirpath, d), "hidden"))
            elif os.path.islink(os.path.join(dirpath, d)):      # not followed: it may lead out of the source
                skipped.append(Skipped(os.path.join(dirpath, d), "symlink"))
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and not os.path.islink(os.path.join(dirpath, d)))
        stems: dict[str, list[FileInfo]] = {}
        keys: set[str] = set()
        for name in sorted(names):
            path = os.path.join(dirpath, name)
            ext = os.path.splitext(name)[1].lower()
            if name.lower() in SIDECAR_NAMES or ext in SIDECAR_EXT:
                skipped.append(Skipped(path, "sidecar"))
                continue
            if name.startswith("."):
                skipped.append(Skipped(path, "hidden"))
                continue
            role = _role(ext)
            if role is None:
                skipped.append(Skipped(path, "unsupported"))
                continue
            try:
                st = os.stat(path)
            except OSError:
                skipped.append(Skipped(path, "unreadable"))
                continue
            info = FileInfo(path, role, ext, st.st_size, st.st_mtime_ns)
            stems.setdefault(os.path.splitext(name)[0].lower(), []).append(info)
        for stem, infos in stems.items():
            by_ext = sorted(infos, key=lambda f: (f.ext, f.path))
            raw = next((f for f in by_ext if f.role == "raw"), None)
            jpeg = next((f for f in by_ext if f.role == "jpeg"), None)
            if raw and jpeg:
                key = f"{dirpath}/{stem}|rawjpeg"
                found.append(Discovered(key, "photo", jpeg.path, sorted([raw, jpeg], key=lambda f: f.path)))
            for f in infos:
                if raw and jpeg and f in (raw, jpeg):
                    continue
                key = f"{dirpath}/{os.path.basename(f.path).lower()}"
                if key in keys:                 # a case-sensitive file system: IMG.jpg next to IMG.JPG
                    key = f"{dirpath}/{os.path.basename(f.path)}|exact"
                keys.add(key)
                found.append(Discovered(key, "video" if f.role == "video" else "photo", f.path, [f]))
    found.sort(key=lambda d: (d.primary, d.pair_key))
    skipped.sort(key=lambda s: s.path)
    return found, skipped


def quick_hash(path: str) -> str:
    """sha256 over the size and the first and last MiB: a file identity that does not read the whole
    file (a changed file almost always changes one of them)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        size = os.fstat(f.fileno()).st_size
        h.update(str(size).encode())
        h.update(f.read(MIB))
        if size > MIB:
            f.seek(max(MIB, size - MIB))
            h.update(f.read(MIB))
    return h.hexdigest()


def is_online(path: str) -> bool:
    """The file exists and this process can read it (an unmounted card is offline)."""
    return os.path.isfile(path) and os.access(path, os.R_OK)


GROUP_EXT = SIDECAR_EXT | {".acr"}     # .acr: Adobe Camera Raw's newer sidecar (AI masks), kept with its photo


def sidecars(paths: list[str]) -> dict[str, list[str]]:
    """For each file in `paths`, the sidecars next to it (contract revision 5D): same folder, named
    <stem><ext> or <name><ext> (IMG_1.xmp, IMG_1.ARW.xmp), case-insensitively, <ext> in GROUP_EXT.
    discover() still skips them; this only finds them for an import plan. Each folder is listed once."""
    listed: dict[str, list[str]] = {}
    out: dict[str, list[str]] = {}
    for p in paths:
        d, name = os.path.split(p)
        if d not in listed:
            try:
                listed[d] = sorted(os.listdir(d))
            except OSError:
                listed[d] = []
        keys = {name.lower(), os.path.splitext(name)[0].lower()}
        out[p] = [os.path.join(d, n) for n in listed[d]
                  if os.path.splitext(n)[1].lower() in GROUP_EXT and os.path.splitext(n)[0].lower() in keys
                  and os.path.isfile(os.path.join(d, n))]
    return out
