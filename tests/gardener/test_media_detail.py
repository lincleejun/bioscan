"""Original-pixel detail (contract revision 5C, PRD §08 第三级 / AC06): crops of the original pixels in
upright coordinates (EXIF orientation 6 / 8 JPEGs, a RAW through a fake rawpy that turns its pixels the
way LibRaw does), the decoded-RAW cache under cache/detail, supersession, the time limit, and marks that
never wait for a decode. rawpy is faked here; two real Sony A1 ARWs were measured by hand (contract revision 7D)."""
from __future__ import annotations

import sys
import threading
import time
import types
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest
from core_fixtures import *  # noqa: F403 - fixtures (env, fakes)
from core_fixtures import create_task, wait_task
from PIL import Image, ImageOps

from bioscan.gardener import detail
from bioscan.gardener.detail import Details, Superseded, parse_xyxy, pixel_box

API = "/api/v1"
RED, GREY = (220, 20, 20), (128, 128, 128)
PATCH = (0.1, 0.6, 0.3, 0.8)            # upright fractions of the red patch


def upright_image(size=(300, 400)) -> Image.Image:
    """Portrait, grey, a red patch at PATCH (upright coordinates)."""
    im = Image.new("RGB", size, GREY)
    w, h = size
    im.paste(RED, (int(PATCH[0] * w), int(PATCH[1] * h), int(PATCH[2] * w), int(PATCH[3] * h)))
    return im


STORE_AS = {6: Image.Transpose.ROTATE_90, 8: Image.Transpose.ROTATE_270, 1: None}


def oriented_jpg(path, orientation: int) -> str:
    """A JPEG stored turned, with the EXIF orientation that turns it back to upright_image()."""
    up = upright_image()
    op = STORE_AS[orientation]
    stored = up.transpose(op) if op else up
    exif = Image.Exif()
    exif[0x0112] = orientation
    stored.save(path, "JPEG", quality=95, exif=exif)
    with Image.open(path) as im:                                   # the fixture itself is right
        assert ImageOps.exif_transpose(im).size == up.size
    return str(path)


def mean(jpeg: bytes) -> tuple[float, ...]:
    import io
    return tuple(np.asarray(Image.open(io.BytesIO(jpeg)).convert("RGB")).reshape(-1, 3).mean(0))


def is_red(c) -> bool:
    return c[0] > 180 and c[1] < 70 and c[2] < 70


def is_grey(c) -> bool:
    return all(abs(x - 128) < 15 for x in c)


def inside(box, m=0.02):
    return (box[0] + m, box[1] + m, box[2] - m, box[3] - m)


def test_parse_and_pixel_box():
    assert parse_xyxy("0,0,1,1") == (0, 0, 1, 1)
    for bad in ("", "0,0,1", "0.5,0,0.4,1", "0,0,1.2,1", "a,b,c,d"):
        with pytest.raises(ValueError):
            parse_xyxy(bad)
    assert pixel_box((0.1, 0.6, 0.3, 0.8), 300, 400) == (30, 240, 90, 320)
    assert pixel_box((1.0, 1.0, 1.0, 1.0), 300, 400) == (299, 399, 300, 400)     # at least one pixel


@pytest.mark.parametrize("orientation", [1, 6, 8])
def test_jpeg_crop_is_in_upright_coordinates(tmp_path, orientation):
    jpg = oriented_jpg(tmp_path / f"o{orientation}.jpg", orientation)
    d = Details(tmp_path / "cache")
    try:
        got = d.get("as01", jpg, inside(PATCH))
        assert is_red(mean(got.jpeg)) and got.full == (300, 400) and got.orientation == orientation
        assert got.decoder == "pillow" and got.cache == "none" and got.headers()["X-Detail-Scale"] == "1.0000"
        assert is_grey(mean(d.get("as01", jpg, (0.6, 0.1, 0.9, 0.4)).jpeg))
        small = d.get("as01", jpg, (0, 0, 1, 1), edge=100)                       # downscaled, never enlarged
        assert small.out == (75, 100) and float(small.headers()["X-Detail-Scale"]) == 0.25
    finally:
        d.close()
    assert not (tmp_path / "cache" / "detail").exists()                           # rasters are not copied


class FakeLibRaw:
    """Stores the sensor image turned as the camera saw it; postprocess turns it upright by `flip` (as
    LibRaw does when user_flip is not given), at full size."""
    calls = 0

    def __init__(self, flip: int, gate: threading.Event | None = None, entered: threading.Event | None = None):
        up = np.asarray(upright_image())
        self.stored = {0: up, 6: np.rot90(up, 1), 5: np.rot90(up, -1), 3: np.rot90(up, 2)}[flip]
        self.sizes = types.SimpleNamespace(flip=flip, width=self.stored.shape[1], height=self.stored.shape[0])
        self.gate, self.entered = gate, entered

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def postprocess(self, **kw):
        assert not kw.get("half_size") and "user_flip" not in kw
        FakeLibRaw.calls += 1
        if self.entered:
            self.entered.set()
        if self.gate:
            assert self.gate.wait(10)
        k = {0: 0, 6: -1, 5: 1, 3: 2}[self.sizes.flip]
        return np.ascontiguousarray(np.rot90(self.stored, k))


def fake_rawpy(monkeypatch, flip=6, crop=None, **kw):
    FakeLibRaw.calls = 0
    monkeypatch.setitem(sys.modules, "rawpy", types.SimpleNamespace(imread=lambda path: FakeLibRaw(flip, **kw)))
    monkeypatch.setattr(detail, "camera_crop", lambda path: crop)


@pytest.mark.parametrize("flip", [0, 3, 5, 6])
def test_camera_crop_maps_through_the_flip(flip):
    """The camera's default crop is given in the sensor frame; cut from LibRaw's upright decode it must be the
    same pixels as the sensor crop turned upright (real A1 RAWs: centre offset 12,12 -> 0,0, contract 7D)."""
    sensor = np.arange(30 * 40).reshape(30, 40)                       # h 30, w 40
    k = {0: 0, 6: -1, 5: 1, 3: 2}[flip]                               # LibRaw's turn (FakeLibRaw)
    x, y, cw, ch = 3, 2, 30, 25
    box = detail.upright_crop((x, y, cw, ch), 40, 30, flip)
    assert np.array_equal(np.rot90(sensor, k)[box[1]:box[3], box[0]:box[2]], np.rot90(sensor[y:y + ch, x:x + cw], k))
    assert detail.upright_crop((0, 0, 40, 30), 40, 30, flip) is None and detail.upright_crop(None, 40, 30, flip) is None


def test_raw_detail_is_cut_to_the_camera_crop(tmp_path, monkeypatch):
    fake_rawpy(monkeypatch, 6, crop=(10, 20, 380, 270))      # sensor 400 x 300 (flip 6 stores the portrait turned)
    (tmp_path / "x.ARW").write_bytes(b"raw")
    d = Details(tmp_path / "cache")
    try:
        out = d.get("as01", str(tmp_path / "x.ARW"), (0, 0, 1, 1))
        assert out.full == (270, 380) and out.orientation == 6
    finally:
        d.close()


@pytest.mark.parametrize("flip,orientation", [(6, 6), (5, 8), (0, 1)])
def test_raw_full_decode_upright_and_cached(tmp_path, monkeypatch, flip, orientation):
    fake_rawpy(monkeypatch, flip)
    raw = tmp_path / "x.ARW"
    raw.write_bytes(b"raw")
    d = Details(tmp_path / "cache")
    try:
        first = d.get("as01", str(raw), inside(PATCH))
        assert is_red(mean(first.jpeg)) and first.full == (300, 400) and first.orientation == orientation
        assert (first.file, first.decoder, first.cache) == ("raw", "libraw-full", "miss")
        again = d.get("as01", str(raw), (0.6, 0.1, 0.9, 0.4))
        assert again.cache == "hit" and is_grey(mean(again.jpeg)) and FakeLibRaw.calls == 1
        assert [p.name.split(".")[-2] for p in (tmp_path / "cache" / "detail").rglob("*.npy")] == [f"o{orientation}"]
        raw.write_bytes(b"changed raw")                                   # content changed: decoded again
        assert d.get("as01", str(raw), PATCH).cache == "miss" and FakeLibRaw.calls == 2
        assert len(list((tmp_path / "cache" / "detail").rglob("*.npy"))) == 1
    finally:
        d.close()


def test_cache_cap_evicts_oldest(tmp_path, monkeypatch):
    fake_rawpy(monkeypatch, 0)
    one = 300 * 400 * 3 + 128
    d = Details(tmp_path / "cache", cap_bytes=2 * one)
    try:
        for i in range(3):
            (tmp_path / f"{i}.ARW").write_bytes(b"r")
            d.get(f"as0{i}", str(tmp_path / f"{i}.ARW"), PATCH)
            time.sleep(0.02)
        names = sorted(p.name.split(".")[0] for p in (tmp_path / "cache" / "detail").rglob("*.npy"))
        assert names == ["as01", "as02"]
    finally:
        d.close()


def test_newer_request_supersedes_a_queued_one_and_timeout_cancels(tmp_path, monkeypatch):
    gate, entered = threading.Event(), threading.Event()
    fake_rawpy(monkeypatch, 0, gate=gate, entered=entered)
    (tmp_path / "busy.ARW").write_bytes(b"r")
    jpg = oriented_jpg(tmp_path / "a.jpg", 6)
    d = Details(tmp_path / "cache", workers=1)
    pool = ThreadPoolExecutor(4)
    try:
        busy = pool.submit(d.get, "asbusy", str(tmp_path / "busy.ARW"), PATCH)     # holds the only worker
        assert entered.wait(5)
        older = pool.submit(d.get, "as01", jpg, PATCH)
        time.sleep(0.05)
        newer = pool.submit(d.get, "as01", jpg, inside(PATCH))
        time.sleep(0.05)
        with pytest.raises(TimeoutError):
            d.get("as02", jpg, PATCH, timeout=0.05)                 # queued past its limit: cancelled
        gate.set()
        assert busy.result(5).cache == "miss"
        with pytest.raises(Superseded):
            older.result(5)
        assert is_red(mean(newer.result(5).jpeg))
    finally:
        gate.set()
        pool.shutdown()
        d.close()


# ---- through the service ----

def task_assets(c, src):
    t = wait_task(c, create_task(c, [str(src)], caps=[])["id"])
    return {a["name"]: a for a in c.get(f"{API}/assets", params={"task": t["id"], "limit": 50}).json()["items"]}


def test_detail_endpoint_reports_what_it_is(env, tmp_path, monkeypatch):
    fake_rawpy(monkeypatch, 6)
    src = tmp_path / "card"
    src.mkdir()
    oriented_jpg(src / "rot6.jpg", 6)
    oriented_jpg(src / "rot8.jpg", 8)
    oriented_jpg(src / "PAIR.jpg", 1)
    (src / "PAIR.ARW").write_bytes(b"raw")
    with env.client() as c:
        assets = task_assets(c, src)
        for name, o in (("rot6.jpg", "6"), ("rot8.jpg", "8")):
            r = c.get(f"{API}/assets/{assets[name]['id']}/detail", params={"xyxy": "0.12,0.62,0.28,0.78"})
            assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
            assert r.headers["x-detail-source"] == "original" and r.headers["x-detail-orientation"] == o
            assert r.headers["x-detail-full"] == "300x400" and r.headers["x-detail-crop"] == "36,248,84,312"
            assert is_red(mean(r.content))
        pair = next(a for n, a in assets.items() if n.lower().startswith("pair"))
        r = c.get(f"{API}/assets/{pair['id']}/detail", params={"xyxy": "0.12,0.62,0.28,0.78", "max": 64})
        assert r.status_code == 200, r.text
        assert r.headers["x-detail-file"] == "raw" and r.headers["x-detail-decoder"] == "libraw-full"
        assert is_red(mean(r.content)) and r.headers["x-detail-size"] == "48x64"
        assert list((env.data / "cache" / "detail").rglob("*.npy"))
        assert c.post(f"{API}/storage/clear-cache").json()["removed_files"] >= 1
        assert not list((env.data / "cache" / "detail").rglob("*.npy"))
        assert c.get(f"{API}/assets/{pair['id']}/detail").headers["x-detail-cache"] == "miss"
        rid = assets["rot6.jpg"]["id"]
        assert c.get(f"{API}/assets/{rid}/detail", params={"xyxy": "0,0,2,1"}).status_code == 400
        assert c.get(f"{API}/assets/{rid}/detail", params={"max": 10}).status_code == 400
        assert c.get(f"{API}/assets/nope/detail").status_code == 404
        (src / "rot6.jpg").unlink()
        r = c.get(f"{API}/assets/{rid}/detail")
        assert r.status_code == 404 and "快速预览" in r.json()["error"]
    assert sorted(p.name for p in src.iterdir()) == ["PAIR.ARW", "PAIR.jpg", "rot8.jpg"]   # nothing written


def test_marks_do_not_wait_for_a_decode(env, tmp_path, monkeypatch):
    gate, entered = threading.Event(), threading.Event()
    fake_rawpy(monkeypatch, 0, gate=gate, entered=entered)
    src = tmp_path / "card"
    src.mkdir()
    (src / "slow.ARW").write_bytes(b"raw")
    oriented_jpg(src / "b.jpg", 1)
    with env.client() as c:
        assets = task_assets(c, src)
        raw_id = next(a["id"] for n, a in assets.items() if n.endswith(".ARW"))
        with ThreadPoolExecutor(1) as pool:
            pending = pool.submit(c.get, f"{API}/assets/{raw_id}/detail")
            try:
                assert entered.wait(5)
                t0 = time.perf_counter()
                r = c.post(f"{API}/marks", json={"asset_ids": [assets["b.jpg"]["id"]], "flag": "pick"})
                assert r.status_code == 200 and time.perf_counter() - t0 < 2
                assert c.get(f"{API}/assets/{assets['b.jpg']['id']}/thumb").status_code == 200
                assert not pending.done()
            finally:
                gate.set()
            assert pending.result(10).status_code == 200


def test_detail_timeout_is_503(env, tmp_path, monkeypatch):
    gate = threading.Event()
    fake_rawpy(monkeypatch, 0, gate=gate)
    monkeypatch.setattr(detail, "DETAIL_TIMEOUT_S", 0.05)
    src = tmp_path / "card"
    src.mkdir()
    (src / "slow.ARW").write_bytes(b"raw")
    with env.client() as c:
        rid = next(iter(task_assets(c, src).values()))["id"]
        try:
            r = c.get(f"{API}/assets/{rid}/detail")
            assert r.status_code == 503 and r.json()["code"] == "unavailable"
        finally:
            gate.set()
