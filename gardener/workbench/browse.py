"""本地目录 (contract revision 11): a read-only browser over the folders where the owner keeps photos.

Visible: ~/Pictures, ~/Documents, ~/Desktop, ~/Downloads, every volume mounted under /Volumes and the
service's --allow-root folders, recomputed on each request (cards come and go). A path is resolved
(symlinks followed) and must land inside one of them; symlinked entries are never listed. It lists
folders and reads files only: nothing is written in a source or in the database. Previews made on
request go to <cache>/fs/, keyed by path + size + mtime and capped; clear-cache removes them with the rest."""
from __future__ import annotations

import hashlib
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse

from gardener.workbench import preview, scan
from gardener.workbench.store import Error, NotFound

HOME_DIRS = (("Pictures", "图片"), ("Documents", "文稿"), ("Desktop", "桌面"), ("Downloads", "下载"))
VOLUMES = Path("/Volumes")
COUNT_BUDGET_S = 0.3        # sub-folder counts stop (null) after this much time per listing
CACHE_CAP = 1 << 30         # bytes of fs/ previews kept; oldest go first


class Forbidden(Error):
    status, code = 403, "forbidden"


def _readable(path: str) -> bool:
    """Really list it: macOS privacy (TCC) refuses a volume that os.access calls readable."""
    try:
        with os.scandir(path) as it:
            next(it, None)
        return True
    except OSError:
        return False


def roots(allow_roots: list[Path]) -> list[dict[str, Any]]:
    """Each visible root once (by real path): {name, path, kind: home | volume | allow-root, online}.
    A symlink in /Volumes (the startup disk's "Macintosh HD" -> /) is not a volume."""
    found: list[tuple[str, str, str]] = []
    home = Path.home()
    found += [(zh, str(home / d), "home") for d, zh in HOME_DIRS]
    try:
        with os.scandir(VOLUMES) as it:
            found += [(e.name, e.path, "volume") for e in sorted(it, key=lambda e: e.name.lower())
                      if not e.name.startswith(".") and not e.is_symlink() and e.is_dir(follow_symlinks=False)]
    except OSError:
        pass
    found += [(p.name or str(p), str(p), "allow-root") for p in allow_roots]
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for name, path, kind in found:
        real = os.path.realpath(path)
        if real in seen:
            continue
        seen.add(real)
        out.append({"name": name, "path": real, "kind": kind, "online": os.path.isdir(real) and _readable(real)})
    return out


def resolve(path: str, allow_roots: list[Path]) -> tuple[str, dict[str, Any]]:
    """The real path of `path` and the root holding it. 400: not absolute or has a `..` part; 403: its
    real path is outside every root, or hidden below the root; 404: it does not exist. The root check
    comes before the existence check, so nothing outside the roots can be probed."""
    if not isinstance(path, str) or not os.path.isabs(path) or "\0" in path:
        raise Error("path 必须是绝对路径")
    if ".." in Path(path).parts:
        raise Error("路径不能含 ..")
    real = os.path.realpath(path)
    root = next((r for r in roots(allow_roots) if Path(real).is_relative_to(r["path"])), None)
    if root is None:
        raise Forbidden("不在可浏览的位置内（图片、文稿、桌面、下载、外置卷与 --allow-root）")
    if any(part.startswith(".") for part in Path(real).relative_to(root["path"]).parts):
        raise Forbidden("隐藏的文件或文件夹不可浏览")
    if not os.path.exists(real):
        raise NotFound(f"不存在：{path}")
    return real, root


def _iso(ns: int) -> str:
    return datetime.fromtimestamp(ns / 1e9, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _entries(path: str) -> tuple[list[str], list[str]]:
    """(sub-folder names, file names) of one folder: hidden and symlinked entries left out."""
    dirs, files = [], []
    with os.scandir(path) as it:
        for e in it:
            if e.name.startswith(".") or e.is_symlink():
                continue
            if e.is_dir(follow_symlinks=False):
                dirs.append(e.name)
            elif e.is_file(follow_symlinks=False):
                files.append(e.name)
    return sorted(dirs, key=str.lower), files


def _item(d: scan.Discovered) -> dict[str, Any]:
    roles = sorted(f.role for f in d.files)
    return {"name": os.path.basename(d.primary), "path": d.primary, "media": d.media,
            "kind": "raw+jpeg" if roles == ["jpeg", "raw"] else roles[0],
            "size": sum(f.size for f in d.files), "mtime": _iso(max(f.mtime_ns for f in d.files)),
            "files": [{"name": os.path.basename(f.path), "role": f.role, "size": f.size} for f in d.files]}


def _covering(path: str, sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The tasks whose source holds `path` (a task scans its sources recursively)."""
    out = {s["task_id"]: {"id": s["task_id"], "title": s["title"]} for s in sources
           if Path(path).is_relative_to(s["path"])}
    return list(out.values())


def listing(path: str, allow_roots: list[Path], sources: list[dict[str, Any]], offset: int = 0,
            limit: int = 300) -> dict[str, Any]:
    """One folder: its sub-folders (direct media counts, by name only, until COUNT_BUDGET_S runs out; then
    null) and a page of its media, RAW+JPEG paired and filtered as scan.discover does."""
    real, root = resolve(path, allow_roots)
    if not os.path.isdir(real):
        raise Error("不是文件夹")
    try:
        dirs, files = _entries(real)
    except PermissionError:
        raise Forbidden("macOS 拒绝读取这个位置：需要在“系统设置 › 隐私与安全性”里给运行服务的程序授权") from None
    except OSError as exc:
        raise NotFound(f"无法读取：{exc.strerror or exc}") from None
    found, skipped = scan.folder(real, files)
    found.sort(key=lambda d: (d.primary.lower(), d.pair_key))
    deadline = time.monotonic() + COUNT_BUDGET_S
    subs = []
    for name in dirs:
        p = os.path.join(real, name)
        sub: dict[str, Any] = {"name": name, "path": p, "photos": None, "videos": None, "dirs": None,
                               "readable": True, "tasks": _covering(p, sources)}
        if time.monotonic() < deadline:
            try:
                kids, names = _entries(p)
                got, _ = scan.folder(p, names, stat=False)
                sub.update(photos=sum(d.media == "photo" for d in got), videos=sum(d.media == "video" for d in got),
                           dirs=len(kids))
            except OSError:
                sub["readable"] = False
        subs.append(sub)
    return {"path": real, "name": os.path.basename(real) if real != root["path"] else root["name"],
            "root": {k: root[k] for k in ("name", "path", "kind")},
            "parent": None if real == root["path"] else os.path.dirname(real), "tasks": _covering(real, sources),
            "dirs": subs, "photos": sum(d.media == "photo" for d in found),
            "videos": sum(d.media == "video" for d in found), "skipped": len(skipped), "total": len(found),
            "offset": offset, "limit": limit, "items": [_item(d) for d in found[offset:offset + limit]]}


class Thumbs:
    """Previews of browsed files under `cache_dir`/fs, keyed by real path + size + mtime (a changed file
    gets a new one), long edge 360 (thumb) or 1600 (large), capped at `cap` bytes oldest first."""

    def __init__(self, cache_dir: Path, cap: int = CACHE_CAP):
        self.dir, self.cap = Path(cache_dir) / "fs", cap
        self._lock = threading.Lock()
        self._total: int | None = None      # bytes under dir, as far as this process knows

    def get(self, real: str, size: str) -> Path:
        role = scan._role(os.path.splitext(real)[1].lower())
        if role is None or not os.path.isfile(real):
            raise Error("只能预览照片或视频文件")
        st = os.stat(real)
        key = hashlib.sha256(f"{real}\0{st.st_size}\0{st.st_mtime_ns}".encode()).hexdigest()
        thumb, large = self.dir / key[:2] / f"{key}.t.jpg", self.dir / key[:2] / f"{key}.l.jpg"
        want = large if size == "large" else thumb
        if want.exists():
            return want
        p = preview.make_preview(real, "video" if role == "video" else "photo", thumb,
                                 large if size == "large" else None)
        if not p.ok or not want.exists():
            raise NotFound(f"无法生成预览：{p.error or '原片不可读'}")
        self._grew(sum(f.stat().st_size for f in (thumb, large) if f.exists()))
        return want

    def _grew(self, n: int) -> None:
        with self._lock:
            if self._total is None:
                self._total = self._walk()[1]
            else:
                self._total += n
            if self._total > self.cap:      # over: re-read the disk (clear-cache may have emptied it) and evict
                files, self._total = self._walk()
                for _mtime, size, f in sorted(files):
                    if self._total <= self.cap * 0.9:
                        break
                    try:
                        f.unlink()
                        self._total -= size
                    except FileNotFoundError:
                        pass

    def _walk(self) -> tuple[list[tuple[float, int, Path]], int]:
        files = []
        for d, _, names in os.walk(self.dir):
            for n in names:
                p = Path(d) / n
                try:
                    s = p.stat()
                except FileNotFoundError:
                    continue
                files.append((s.st_mtime, s.st_size, p))
        return files, sum(f[1] for f in files)


def router(cache_dir: Path, allow_roots: list[Path], sources: Any) -> APIRouter:
    """GET /api/v1/fs/roots | list | thumb. `sources()` returns [{task_id, title, path}] of task sources."""
    r = APIRouter(prefix="/api/v1/fs")
    thumbs = Thumbs(cache_dir)

    @r.get("/roots")
    def get_roots() -> list[dict[str, Any]]:
        return roots(allow_roots)

    @r.get("/list")
    def get_list(path: str, offset: int = Query(0, ge=0), limit: int = Query(300, ge=1, le=2000)) -> dict[str, Any]:
        return listing(path, allow_roots, sources(), offset, limit)

    @r.get("/thumb")
    def get_thumb(path: str, size: str = Query("thumb", pattern="^(thumb|large)$")) -> FileResponse:
        real, _ = resolve(path, allow_roots)
        return FileResponse(thumbs.get(real, size), media_type="image/jpeg",
                            headers={"Cache-Control": "private, max-age=86400"})

    return r
