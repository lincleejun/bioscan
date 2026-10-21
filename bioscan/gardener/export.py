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
import shutil
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any

NS = "urn:gardener:export:0.1"
COMMENT = (" Gardener 导出清单 (gardener-only)。这不是 XMP，Lightroom 不能直接读取。"
           " This is not XMP and Lightroom cannot read it directly. ")
CONFLICTS = ("rename", "skip", "fail")
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


def _plan(sources: list[str], folder: Path, conflict: str) -> dict[str, tuple[str, Path | None]]:
    """src -> (action, dst): copy | identical | renamed | skipped | missing. `fail` raises ValueError
    before anything is copied when a destination holds different content."""
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
            if other is None and not dst.exists():
                plan[src] = ("copy" if n == 0 else "renamed", dst)
                break
            same = (other is None and os.path.getsize(dst) == os.path.getsize(src) and _sha(str(dst)) == _sha(src))
            if same:
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


def _copy(src: str, dst: Path) -> str:
    """Copy with sha256 of the source taken while reading and of the copy read back; the copy is
    renamed into place only when they match. Returns the hash; raises on a mismatch."""
    part = dst.with_name(dst.name + ".gardener-part")
    h = hashlib.sha256()
    try:
        with open(src, "rb") as fi, open(part, "xb") as fo:
            while chunk := fi.read(1 << 20):
                h.update(chunk)
                fo.write(chunk)
        shutil.copystat(src, part)
        if _sha(str(part)) != h.hexdigest():
            raise OSError("复制后校验不一致")
        os.replace(part, dst)
    finally:
        if part.exists():
            part.unlink()                  # only ever our own partial copy
    return h.hexdigest()


def export(dest_dir: Path, *, title: str, scope: dict, collections: list[dict], assets: list[dict],
           copy: bool, conflict: str = "rename", protected: list[Path]) -> dict:
    """Writes gardener-export-<time>.xml into dest_dir and, with `copy`, every file of each asset to
    dest_dir/files/<safe title>/. assets: AssetDetail dicts (files, flag, stars, ai, ai_history,
    collections); collections: Collection dicts with member_ids; scope {kind, value, scope}.
    ValueError when dest_dir is inside a protected path, on an unknown conflict policy, or (conflict
    "fail") when a destination file differs. Returns the ExportReport (contract §7); `verified` is
    true only for a copy in which every file was copied or found identical, sha256-checked."""
    if conflict not in CONFLICTS:
        raise ValueError(f"conflict must be one of {', '.join(CONFLICTS)}")
    dest = Path(dest_dir).expanduser().resolve()
    for p in protected:
        if dest.is_relative_to(Path(p).expanduser().resolve()):
            raise ValueError(f"导出目的地位于来源目录之内：{p}")
    report: dict[str, Any] = {"xml_path": None, "assets": len(assets), "files": 0, "copied": 0, "identical": 0,
                              "skipped": 0, "renamed": 0, "failed": [], "bytes": 0, "verified": False,
                              "notes": list(NOTES)}
    sources = [f["path"] for a in assets for f in a.get("files") or []]
    report["files"] = len(sources)
    done: dict[str, dict[str, Any]] = {}             # src -> {status, copied, sha256}
    if copy:
        folder = dest / "files" / safe_name(title)
        plan = _plan(sources, folder, conflict)       # may raise (fail) before anything is written
        folder.mkdir(parents=True, exist_ok=True)
        for src, (action, dst) in plan.items():
            if action == "missing":
                report["failed"].append({"path": src, "error": "源文件不存在或离线"})
                done[src] = {"status": "failed"}
            elif action == "skipped":
                report["skipped"] += 1
                done[src] = {"status": "skipped"}
            elif action == "identical":
                report["identical"] += 1
                done[src] = {"status": "identical", "copied": str(dst), "sha256": _sha(src)}
            else:
                try:
                    digest = _copy(src, dst)
                except OSError as exc:
                    report["failed"].append({"path": src, "error": f"{type(exc).__name__}: {exc}"})
                    done[src] = {"status": "failed"}
                    continue
                report["copied"] += 1
                report["renamed"] += action == "renamed"
                report["bytes"] += os.path.getsize(dst)
                done[src] = {"status": action, "copied": str(dst), "sha256": digest}
        report["verified"] = not report["failed"] and report["skipped"] == 0
    else:
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
