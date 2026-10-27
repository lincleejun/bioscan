"""Shared core fixtures. Not a conftest.py: tests/contract/conftest.py is imported by name
(`from conftest import ...`) across the suite, and a second conftest module would shadow it. Test
modules pull these in with `from core_fixtures import *`.

Shared Gardener fixtures: a fake Toolkit (unit 2's scanner, preview, Analyzer, grouping and export
replaced by small stand-ins with the contract §4 signatures), tiny synthetic JPEGs in tmp dirs, and a
TestClient on the real app, worker and SQLite store."""
from __future__ import annotations

import asyncio
import hashlib
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from bioscan.gardener.api import create_gardener_app
from bioscan.gardener.tasks import Toolkit

RAW, IMG, VID = {".arw", ".dng"}, {".jpg", ".jpeg", ".png"}, {".mov", ".mp4"}
BASE = "http://127.0.0.1:8770"


@dataclass
class FileInfo:
    path: str
    role: str
    ext: str
    size: int
    mtime_ns: int


@dataclass
class Discovered:
    pair_key: str
    media: str
    primary: str
    files: list[FileInfo]


@dataclass
class Skipped:
    path: str
    reason: str


@dataclass
class Preview:
    ok: bool
    method: str | None
    error: str | None
    meta: dict = field(default_factory=dict)


@dataclass
class ItemResult:
    asset_id: str
    capability: str
    status: str
    data: dict | None
    error: str | None
    version: str | None


@dataclass
class Group:
    key: str
    kind: str
    name: str
    asset_ids: list[str]
    summary: dict = field(default_factory=dict)
    parent: str | None = None


def info(path: str) -> FileInfo:
    s = os.stat(path)
    ext = Path(path).suffix.lower()
    role = "raw" if ext in RAW else "jpeg" if ext in (".jpg", ".jpeg") else "video" if ext in VID else "image"
    return FileInfo(path, role, ext, s.st_size, s.st_mtime_ns)


def discover(root: str):
    """Recursive; RAW + same-stem JPEG in one folder are one asset (contract §10 X1)."""
    found, skipped = [], []
    for d, dirs, names in os.walk(root):
        dirs.sort()
        by_stem: dict[str, list[str]] = {}
        for n in sorted(names):
            ext = Path(n).suffix.lower()
            if ext not in RAW | IMG | VID:
                skipped.append(Skipped(os.path.join(d, n), "unsupported"))
                continue
            by_stem.setdefault(Path(n).stem.lower(), []).append(os.path.join(d, n))
        for stem, paths in sorted(by_stem.items()):
            raws = [p for p in paths if Path(p).suffix.lower() in RAW]
            jpgs = [p for p in paths if Path(p).suffix.lower() in (".jpg", ".jpeg")]
            if raws and jpgs:
                found.append(Discovered(f"{d}/{stem}|rawjpeg", "photo", jpgs[0], [info(raws[0]), info(jpgs[0])]))
                paths = [p for p in paths if p not in (raws[0], jpgs[0])]
            for p in paths:
                media = "video" if Path(p).suffix.lower() in VID else "photo"
                found.append(Discovered(f"{d}/{Path(p).name.lower()}", media, p, [info(p)]))
    return found, skipped


def quick_hash(path: str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_meta(paths: list[str]) -> dict[str, dict]:
    """Capture time from the file's mtime, except for names with "-export" (an export that lost its EXIF)."""
    out = {}
    for p in paths:
        m: dict[str, Any] = {"taken_at": None if "-export" in Path(p).stem else
                             datetime.fromtimestamp(os.stat(p).st_mtime).isoformat(timespec="seconds")}
        if Path(p).suffix.lower() in VID:
            m |= {"duration_s": 1.5, "playable": True}
        else:
            try:
                with Image.open(p) as im:
                    m |= {"width": im.width, "height": im.height}
            except OSError:
                pass
        out[p] = m
    return out


def make_preview(primary: str, media: str, thumb: Path, large: Path) -> Preview:
    if not os.path.exists(primary):
        return Preview(False, None, "file missing")
    try:
        im = Image.new("RGB", (32, 24)) if media == "video" else Image.open(primary)
        im.convert("RGB").save(large, "JPEG")
        im.thumbnail((16, 16))
        im.convert("RGB").save(thumb, "JPEG")
    except OSError as exc:
        return Preview(False, None, f"unreadable: {exc}")
    return Preview(True, "video-frame" if media == "video" else "image", None)


DATA = {"content": {"group": "wildlife", "group_zh": "野生动物", "label": "bird", "label_zh": "鸟", "score": 0.9,
                    "top": [["bird", 0.9]]},
        "subject": {"class": "bird", "boxes": [{"xyxy": [1, 2, 3, 4], "score": 0.8, "kind": "bird"}]},
        "species": {"taxon": "Megascops asio", "common": "Eastern Screech-Owl", "level": "species", "p": 0.8,
                    "top": [], "uncertain": False},
        "burst": {"vector_dim": 768},
        "quality": {"sharpness": 0.5, "exposure": 0.0, "reasons": [], "reasons_zh": []},
        "aesthetic": {"score": 5.0}}


class Fakes:
    """What the fake Analyzer saw and the knobs a test turns: `gate` (cleared = analysis waits per
    item), `analyzed` ((asset path, caps) per item), `exports` (export calls), `revealed`, `boxes`
    (file name -> the raw:identify boxes with species on; default one Eastern Screech-Owl box)."""

    def __init__(self) -> None:
        self.gate = threading.Event()
        self.gate.set()
        self.analyzed: list[tuple[str, tuple[str, ...]]] = []
        self.exports: list[dict[str, Any]] = []
        self.revealed: list[str] = []
        self.version = "fake1"
        self.tz_seen: str | None = None
        self.finals: list[bool] = []
        self.faces_seen: list[str] = []
        self.boxes: dict[str, list[dict[str, Any]]] = {}

    def analyzer(self, engine, *, decode_workers=2, chunk=16, detail_edge=None):
        return FakeAnalyzer(self, engine)

    def faces(self):
        return FakeFaces(self)

    def suggest(self, assets, *, tz=None, final=True, names=None):
        """Contract §4 shape of grouping.suggest: settled assets only; a group per person key (named with
        `names` when given), a subject per asset (species, else the content group, else 未分类)."""
        self.tz_seen = tz
        self.finals.append(final)
        names = names or {}
        by: dict[str, list[dict]] = {}
        for a in assets:
            if not a.get("settled", True):
                continue
            for p in a.get("people") or []:
                by.setdefault(f"person:{p}", []).append(a)
            if a["species"] and a["species"]["level"] == "species":
                key = f"species:{a['species']['taxon']}"
            elif a["content"]:
                key = f"scene:{a['content']['group']}"
            else:
                key = "other:none"
            by.setdefault(key, []).append(a)
        return [Group(k, k.split(":")[0], names.get(k.removeprefix("person:")) or k.split(":", 1)[1],
                      [a["id"] for a in ms], {"time_basis": "task_timezone" if tz else "camera_local"})
                for k, ms in by.items()]

    def export(self, dest_dir, *, title, scope, collections, assets, copy, conflict="rename", protected):
        dest_dir.mkdir(parents=True, exist_ok=True)
        xml = dest_dir / "gardener-export-test.xml"
        xml.write_text(f"<gardener-export n='{len(assets)}'/>")
        self.exports.append({"title": title, "scope": scope, "collections": collections, "assets": assets,
                             "protected": protected, "copy": copy})
        return {"xml_path": str(xml), "assets": len(assets), "files": 0, "copied": 0, "identical": 0, "skipped": 0,
                "renamed": 0, "failed": [], "bytes": 0, "verified": True, "notes": []}

    def toolkit(self) -> Toolkit:
        return Toolkit(discover=discover, quick_hash=quick_hash, is_online=os.path.exists, read_meta=read_meta,
                       make_preview=make_preview, analyzer=self.analyzer, suggest=self.suggest, export=self.export,
                       reveal=self.revealed.append, faces=self.faces)


class FakeFaces:
    """faces.FaceModel's shape. A preview whose green channel is above 180 shows one face, whose person
    the red channel decides: below 80 person A, below 160 person C, else person B (make_jpg(color=FACE_A /
    FACE_C / FACE_B)); other images show none."""

    def __init__(self, fakes: Fakes) -> None:
        self.fakes, self.version = fakes, "people@fake1"

    def ensure(self):
        pass

    def faces(self, image_path):
        from bioscan.gardener.faces import encode

        self.fakes.faces_seen.append(str(image_path))
        with Image.open(image_path) as im:
            r, g, _ = im.convert("RGB").resize((1, 1)).getpixel((0, 0))
        if g <= 180:
            return []
        axis = 0 if r < 80 else 2 if r < 160 else 1
        vec = [0.0] * 128
        vec[axis] = 1.0
        return [{"box": [0.2, 0.2, 0.6, 0.7], "score": 0.95, "vec": encode(vec)}]


FACE_A, FACE_B, FACE_C = (20, 220, 20), (220, 220, 20), (120, 220, 20)


class FakeAnalyzer:
    """Contract §4 Analyzer: per item and capability one result, plus raw:<stage> products; a path
    containing "fail" fails; an engine whose models do not load makes every capability unavailable."""

    def __init__(self, fakes: Fakes, engine) -> None:
        self.fakes, self.engine = fakes, engine

    def versions(self, caps):
        return {c: f"{c}@{self.fakes.version}" for c in caps}

    async def analyze(self, items, caps, should_stop):
        try:
            self.engine.ensure(["siglip2"])
        except Exception as exc:  # noqa: BLE001
            for it in items:
                for c in caps:
                    yield ItemResult(it["asset_id"], c, "unavailable", None, f"model load failed: {exc}", None)
            return
        for it in items:
            while not self.fakes.gate.is_set():
                if should_stop():
                    return
                await asyncio.sleep(0.01)
            if should_stop():
                return
            self.fakes.analyzed.append((it["path"], tuple(caps)))
            for c in caps:
                if "fail" in Path(it["path"]).name:
                    yield ItemResult(it["asset_id"], c, "failed", None, "decode failed", f"{c}@{self.fakes.version}")
                else:
                    yield ItemResult(it["asset_id"], c, "done", DATA[c], None, f"{c}@{self.fakes.version}")
            yield ItemResult(it["asset_id"], "raw:aesthetics", "done", {"score": 5.0}, None, None)
            if "species" in caps and "fail" not in Path(it["path"]).name:
                boxes = self.fakes.boxes.get(Path(it["path"]).name, [owl_box()])
                yield ItemResult(it["asset_id"], "raw:identify", "done", {"gate": {"class": "bird"}, "boxes": boxes},
                                 None, f"identify@{self.fakes.version}")

    def reduce(self, rows, caps):
        return {r["asset_id"]: {"burst": {"id": "b1", "rank": i + 1, "size": len(rows)},
                                "suggestion": {"verdict": "pick" if i == 0 else "reject", "score": 5.0 - i,
                                               "rank": i + 1, "reasons": ["测试理由"]}}
                for i, r in enumerate(rows)}

    def close(self):
        pass


def owl_box(xyxy=(0.1, 0.1, 0.5, 0.5), scientific="Megascops asio", common="Eastern Screech-Owl",
            concept="avibase:test-asio", level="species", p=0.8) -> dict[str, Any]:
    """One raw:identify box with its species result (contract.Species), as BioScan returns it."""
    return {"id": 0, "xyxy": list(xyxy), "score": 0.8, "kind": "bird",
            "species": {"list": "fake", "level": level, "taxon": scientific if level == "species" else "Megascops",
                        "top": [{"scientific": scientific, "concept": concept, "common": common, "posterior": p}]}}


def make_jpg(path: Path, size=(64, 48), color=(90, 120, 30), when: float | None = None) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path)
    if when is not None:
        os.utime(path, (when, when))
    return str(path)


def ok_engine():
    return SimpleNamespace(device="cpu", loaded=lambda: [], ensure=lambda models: None)


class Env:
    """A Gardener service on `data` with the fake toolkit; `client()` opens (and restarts) it."""

    def __init__(self, tmp_path: Path, fakes: Fakes, engine=None) -> None:
        self.tmp, self.fakes = tmp_path, fakes
        self.data = tmp_path / "data"
        self.engine = engine or ok_engine()

    def client(self, **kw) -> TestClient:
        app = create_gardener_app(self.data, engine=self.engine, toolkit=self.fakes.toolkit(), **kw)
        return TestClient(app, base_url=BASE)


def wait_until(fn, timeout: float = 15.0, what: str = "condition"):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {what}")


def wait_task(client: TestClient, task_id: str, statuses=("completed", "partial", "failed", "stopped", "cancelled"),
              timeout: float = 15.0) -> dict:
    return wait_until(lambda: (t := client.get(f"/api/v1/tasks/{task_id}").json())["scan"]["status"] in statuses and t,
                      timeout, f"task {task_id} in {statuses}")


def photos(root: Path, n: int = 3, start: float = 1_700_000_000.0) -> list[str]:
    return [make_jpg(root / f"IMG_{i:04d}.jpg", color=(10 * i, 100, 30), when=start + 60 * i) for i in range(n)]


@pytest.fixture
def fakes() -> Fakes:
    return Fakes()


@pytest.fixture
def env(tmp_path, fakes) -> Env:
    return Env(tmp_path, fakes)


def create_task(client: TestClient, sources: list[str], caps=None, preset="wildlife", **kw) -> dict:
    body = {"title": "测试任务", "sources": sources, **kw}
    body |= {"capabilities": caps} if caps is not None else {"preset": preset}
    r = client.post("/api/v1/tasks", json=body)
    assert r.status_code == 201, r.text
    return r.json()
