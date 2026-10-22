"""Export (docs/gardener/contract.md §7, §10 X9): one Gardener XML file describing the selection, and
with `copy` the selected assets' files copied and sha256-verified. Sources are only read: nothing
in them is moved, changed or deleted, and a destination inside a protected path is refused. The
XML is Gardener's own format, not XMP; XMP sidecars in the sources are not copied. Standard
library only."""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any

NS = "urn:gardener:export:0.1"
COMMENT = (" Gardener 导出清单 (gardener-only)。这不是 XMP，Lightroom 不能直接读取。"
           " This is not XMP and Lightroom cannot read it directly. ")
CONFLICTS = ("rename", "skip", "fail")
MAX_RENAMES = 10000
NOTES = ["未包含来源目录中的 XMP sidecar"]
_ILLEGAL = re.compile("[^\t\n\r\x20-퟿-�\U00010000-\U0010ffff]")


def _text(v: Any) -> str:
    """Any value as XML-safe text (characters XML 1.0 forbids are dropped; ElementTree escapes)."""
    if isinstance(v, bool):
        v = "true" if v else "false"
    elif isinstance(v, (dict, list)):
        v = json.dumps(v, ensure_ascii=False, sort_keys=True)
    return _ILLEGAL.sub("", str(v))


def _el(parent: ET.Element, tag: str, attrs: dict[str, Any] | None = None, text: Any = None) -> ET.Element:
    e = ET.SubElement(parent, tag, {k: _text(v) for k, v in (attrs or {}).items() if v is not None})
    if text is not None:
        e.text = _text(text)
    return e


def _sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def safe_name(title: str) -> str:
    """A folder name from a title: path separators and control characters out, never empty or dots."""
    s = re.sub(r'[\x00-\x1f/\\:*?"<>|]+', "_", _ILLEGAL.sub("", title or "")).strip(" .")
    return s[:120] or "export"


def _candidate(name: str, n: int) -> str:
    stem, ext = os.path.splitext(name)
    return name if n == 0 else f"{stem} ({n}){ext}"


def _same(dst: Path, src: str) -> bool:
    """`dst` is a regular file (not a symlink) with the content of `src`."""
    try:
        st = os.lstat(dst)
    except OSError:
        return False
    return stat.S_ISREG(st.st_mode) and st.st_size == os.path.getsize(src) and _sha(str(dst)) == _sha(src)


def _plan(sources: list[str], folder: Path, conflict: str) -> dict[str, tuple[str, Path | None]]:
    """src -> (action, dst): copy | identical | renamed | skipped | missing. `fail` raises ValueError
    before anything is copied when a destination name is taken by different content (or a symlink)."""
    plan: dict[str, tuple[str, Path | None]] = {}
    taken: dict[Path, str] = {}            # destinations claimed by this export -> their source
    for src in sources:
        if src in plan:
            continue
        if not os.path.isfile(src):
            plan[src] = ("missing", None)
            continue
        name, n = os.path.basename(src), 0
        while True:
            dst = folder / _candidate(name, n)
            other = taken.get(dst)
            if other is None and not os.path.lexists(dst):
                plan[src] = ("copy" if n == 0 else "renamed", dst)
                break
            if other is None and _same(dst, src):
                plan[src] = ("identical", dst)
                break
            if conflict == "fail":
                raise ValueError(f"导出目标已有不同内容的同名文件：{dst}")
            if conflict == "skip":
                plan[src] = ("skipped", dst)
                break
            n += 1
        if plan[src][0] in ("copy", "renamed"):
            taken[dst] = src
    return plan


def _inside(path: Path, dest: Path, protected: list[Path]) -> None:
    """ValueError unless `path`, every symlink resolved, stays inside `dest` and outside every protected root."""
    real = Path(os.path.realpath(path))
    if not real.is_relative_to(dest) or any(real.is_relative_to(p) for p in protected):
        raise ValueError(f"导出路径经符号链接指向目的地之外或来源目录之内：{path}")


def _make_dirs(dest: Path, parts: list[str], protected: list[Path]) -> Path:
    """dest/parts..., created one component at a time; a component that is a symlink or not a
    directory is refused, so nothing below dest_dir can lead into a source."""
    cur = dest
    for part in parts:
        cur = cur / part
        try:
            st = os.lstat(cur)
        except FileNotFoundError:
            os.mkdir(cur)
            st = os.lstat(cur)
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            raise ValueError(f"导出目录中有符号链接或非目录：{cur}")
        _inside(cur, dest, protected)
    return cur


_NO_LINKS: set[int] = set()        # st_dev of destinations where hard links failed (exFAT, FAT, some shares)
_EXCL = os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view):]


def _fd_sha(fd: int) -> str:
    """sha256 of what the open file holds, read back through its own descriptor."""
    os.lseek(fd, 0, os.SEEK_SET)
    h = hashlib.sha256()
    while chunk := os.read(fd, 1 << 20):
        h.update(chunk)
    return h.hexdigest()


def _is_ours(path: Path, fd: int) -> bool | None:
    """Whether `path` is, right now, the file `fd` has open (same device, inode, size and mtime, both
    read at this moment: exFAT gives an empty file a temporary inode number, so a number kept from
    creation proves nothing). None when the path is gone."""
    try:
        b = os.lstat(path)
    except FileNotFoundError:
        return None
    a = os.fstat(fd)
    return os.path.samestat(a, b) and (a.st_size, a.st_mtime_ns) == (b.st_size, b.st_mtime_ns)


def _discard(path: Path, fd: int) -> str | None:
    """Remove `path` while it is still the file `fd` has open, then close `fd`. A path that is now
    another file (someone replaced it) is left alone: the message says so."""
    try:
        ours = _is_ours(path, fd)
        if ours:
            os.unlink(path)
        elif ours is False:
            return f"未删除：{path} 已被替换，不是本次导出写入的文件"
        return None
    except OSError as e:
        return f"清理失败：{path}: {e}"
    finally:
        os.close(fd)


def _fail(exc: BaseException, msg: str | None) -> None:
    """Re-raise `exc`, with the cleanup problem `msg` added when there is one."""
    if msg:
        raise OSError(f"{exc}；{msg}") from exc
    raise exc


def _same_folder(dst: Path, real: str) -> None:
    """MB1 at publish time: the folder is no symlink and resolves where it did when the copy began."""
    if os.path.islink(dst.parent) or os.path.realpath(dst.parent) != real:
        raise OSError(f"导出目录在复制期间被替换：{dst.parent}")


def _publish_by_copy(part_fd: int, dst: Path, digest: str, real: str, times: tuple[int, int]) -> None:
    """Where hard links are not supported: create `dst` with O_EXCL|O_NOFOLLOW (FileExistsError when
    the name is taken: never overwritten), re-check the folder, copy the verified temporary file in
    through its descriptor, fsync and sha256-check it. On any failure `dst` is removed only while it
    is still the file this call has open."""
    _same_folder(dst, real)
    fd = os.open(dst, _EXCL, 0o644)
    try:
        _same_folder(dst, real)
        os.lseek(part_fd, 0, os.SEEK_SET)
        while chunk := os.read(part_fd, 1 << 20):
            _write_all(fd, chunk)
        os.fsync(fd)
        os.utime(fd, ns=times)
        if _fd_sha(fd) != digest:
            raise OSError("复制后校验不一致")
        if not _is_ours(dst, fd):
            raise OSError(f"导出目标在复制期间被替换：{dst}")
    except BaseException as exc:
        _fail(exc, _discard(dst, fd))
    os.close(fd)


def _copy(src: str, dst: Path) -> str:
    """Copy `src` to the new name `dst`, never overwriting: a temporary file of our own (O_EXCL,
    O_NOFOLLOW) is sha256-checked against the source as read, then hard-linked to `dst`; where the
    file system has no hard links (exFAT), copied into a `dst` created with O_EXCL. Before publishing
    the folder must still resolve where it did when the copy began (MB1). The temporary file stays
    open until it is removed, and is removed only while it is still that file. FileExistsError when
    `dst` appeared meanwhile; OSError on a mismatch. Returns the hash."""
    part = dst.with_name(f".{dst.name}.{os.getpid()}.{secrets.token_hex(4)}.gardener-part")
    real = os.path.realpath(dst.parent)
    st = os.stat(src)
    fd = os.open(part, _EXCL, 0o644)
    try:
        h = hashlib.sha256()
        with open(src, "rb") as fi:
            while chunk := fi.read(1 << 20):
                h.update(chunk)
                _write_all(fd, chunk)
        os.utime(fd, ns=(st.st_atime_ns, st.st_mtime_ns))
        digest = h.hexdigest()
        if _fd_sha(fd) != digest:
            raise OSError("复制后校验不一致")
        _same_folder(dst, real)
        dev = os.fstat(fd).st_dev
        linked = False
        if dev not in _NO_LINKS:
            try:
                os.link(part, dst, follow_symlinks=False)     # fails, never overwrites, when dst exists
                linked = True
            except FileExistsError:
                raise
            except OSError:                                   # EPERM, ENOTSUP, EXDEV, ...: no hard links here
                _NO_LINKS.add(dev)
        if not linked:
            _publish_by_copy(fd, dst, digest, real, (st.st_atime_ns, st.st_mtime_ns))
    except BaseException as exc:
        _fail(exc, _discard(part, fd))
    msg = _discard(part, fd)                                  # the copy is published: the temporary file goes
    if msg:
        raise OSError(msg)
    return digest


def export(dest_dir: Path, *, title: str, scope: dict, collections: list[dict], assets: list[dict],
           copy: bool, conflict: str = "rename", protected: list[Path]) -> dict:
    """Writes gardener-export-<time>.xml into dest_dir and, with `copy`, every file of each asset to
    dest_dir/files/<safe title>/. assets: AssetDetail dicts (files, flag, stars, ai, ai_history,
    collections); collections: Collection dicts with member_ids; scope {kind, value, scope}.
    ValueError when dest_dir is inside a protected path or a folder under it is a symlink, on an
    unknown conflict policy, or (conflict "fail") when a destination file differs; nothing is
    overwritten. Returns the ExportReport (contract §7); `verified` is true only for a copy in
    which every file was copied or found identical, sha256-checked."""
    if conflict not in CONFLICTS:
        raise ValueError(f"conflict must be one of {', '.join(CONFLICTS)}")
    dest = Path(os.path.realpath(Path(dest_dir).expanduser()))
    roots = [Path(os.path.realpath(Path(p).expanduser())) for p in protected]
    for p in roots:
        if dest.is_relative_to(p):
            raise ValueError(f"导出目的地位于来源目录之内：{p}")
    report: dict[str, Any] = {"xml_path": None, "assets": len(assets), "files": 0, "copied": 0, "identical": 0,
                              "skipped": 0, "renamed": 0, "failed": [], "bytes": 0, "verified": False,
                              "notes": list(NOTES)}
    sources = [f["path"] for a in assets for f in a.get("files") or []]
    report["files"] = len(sources)
    done: dict[str, dict[str, Any]] = {}             # src -> {status, copied, sha256}
    if copy:
        parts = ["files", safe_name(title)]
        for i in range(len(parts)):                  # refuse symlinked folders before planning reads them
            q = dest.joinpath(*parts[:i + 1])
            if os.path.islink(q) or (os.path.lexists(q) and not q.is_dir()):
                raise ValueError(f"导出目录中有符号链接或非目录：{q}")
        plan = _plan(sources, dest.joinpath(*parts), conflict)   # may raise (fail) before anything is written
        dest.mkdir(parents=True, exist_ok=True)             # after planning: fail leaves nothing behind
        folder = _make_dirs(dest, parts, roots)
        for src, (action, dst) in plan.items():
            if action == "missing":
                report["failed"].append({"path": src, "error": "源文件不存在或离线"})
                done[src] = {"status": "failed"}
                continue
            if action == "skipped":
                report["skipped"] += 1
                done[src] = {"status": "skipped"}
                continue
            n = 0
            while True:
                if action != "copy" and _same(dst, src):     # identical re-checked now, not trusted from planning
                    report["identical"] += 1
                    done[src] = {"status": "identical", "copied": str(dst), "sha256": _sha(src)}
                    break
                try:
                    _inside(folder, dest, roots)          # _copy re-checks it has not moved when publishing
                    digest = _copy(src, dst)
                except FileExistsError:                   # appeared after planning: the policy again
                    if _same(dst, src):
                        action = "identical"
                        continue
                    if conflict == "skip":
                        report["skipped"] += 1
                        done[src] = {"status": "skipped"}
                        break
                    if conflict == "fail":
                        report["failed"].append({"path": src, "error": f"导出目标已被占用：{dst}"})
                        done[src] = {"status": "failed"}
                        break
                    n += 1
                    if n > MAX_RENAMES:
                        report["failed"].append({"path": src, "error": "找不到可用的导出文件名"})
                        done[src] = {"status": "failed"}
                        break
                    dst, action = folder / _candidate(os.path.basename(src), n), "renamed"
                    while os.path.lexists(dst) and not _same(dst, src):
                        n += 1
                        dst = folder / _candidate(os.path.basename(src), n)
                    continue
                except (OSError, ValueError) as exc:
                    report["failed"].append({"path": src, "error": f"{type(exc).__name__}: {exc}"})
                    done[src] = {"status": "failed"}
                    break
                report["copied"] += 1
                report["renamed"] += action == "renamed"
                report["bytes"] += os.path.getsize(dst)
                done[src] = {"status": action, "copied": str(dst), "sha256": digest}
                break
        report["verified"] = not report["failed"] and report["skipped"] == 0
    dest.mkdir(parents=True, exist_ok=True)
    report["xml_path"] = str(_write_xml(dest, title, scope, collections, assets, copy, done))
    return report


def _write_xml(dest: Path, title: str, scope: dict, collections: list[dict], assets: list[dict], copy: bool,
               done: dict[str, dict[str, Any]]) -> Path:
    now = datetime.now().astimezone()
    root = ET.Element("gardener-export", {"xmlns": NS, "version": "0.1", "compatibility": "gardener-only",
                                          "created": now.isoformat(timespec="seconds"), "title": _text(title),
                                          "copy": _text(copy)})
    root.append(ET.Comment(COMMENT))
    s = _el(root, "scope", {k: v for k, v in scope.items() if not isinstance(v, (dict, list))})
    for k, v in scope.items():
        if isinstance(v, (dict, list)):
            _el(s, k, text=v)
    cols = _el(root, "collections")
    for c in collections:
        review = c.get("review") or {}
        ce = _el(cols, "collection", {"id": c.get("id"), "name": c.get("name"), "kind": c.get("kind"),
                                      "group_kind": c.get("group_kind"), "review": review.get("state"),
                                      "review_at": review.get("at")})
        for mid in c.get("member_ids") or []:
            _el(ce, "member", {"asset": mid})
    ae = _el(root, "assets")
    for a in assets:
        el = _el(ae, "asset", {"id": a.get("id"), "media": a.get("media"), "name": a.get("name"),
                               "taken_at": a.get("taken_at"), "flag": a.get("flag") or "none",
                               "stars": a.get("stars") or 0, "judged": a.get("judged")})
        files = _el(el, "files")
        for f in a.get("files") or []:
            d = done.get(f["path"], {})
            _el(files, "file", {"path": f["path"], "role": f.get("role"), "size": f.get("size"),
                                "status": d.get("status"), "copied": d.get("copied"), "sha256": d.get("sha256")})
        ai = a.get("ai")
        if ai:
            history = a.get("ai_history") or [{}]
            ai_el = _el(el, "ai", {"verdict": ai.get("verdict"), "score": ai.get("score"),
                                   "version": ai.get("version") or history[0].get("version")})
            for r in ai.get("reasons") or []:
                _el(ai_el, "reason", text=r)
        ce = _el(el, "collections")
        for c in a.get("collections") or []:
            _el(ce, "collection", {"id": c.get("id"), "name": c.get("name")})
    ET.indent(root)
    stamp = now.strftime("%Y%m%d-%H%M%S")
    for n in range(1000):
        path = dest / f"gardener-export-{stamp}{f'-{n}' if n else ''}.xml"
        try:
            with open(path, "xb") as f:
                ET.ElementTree(root).write(f, encoding="utf-8", xml_declaration=True)
            return path
        except FileExistsError:
            continue
    raise OSError("无法创建导出文件名")
