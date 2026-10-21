"""preview: thumbnails and large previews (rasters, RAW through a fake rawpy, video with ffmpeg when
installed) and metadata with and without exiftool / ffprobe."""
from __future__ import annotations

import io
import shutil
import subprocess
import sys
import types

import numpy as np
import pytest
from media_fixtures import make_jpg
from PIL import Image

from bioscan.gardener import preview
from bioscan.gardener.preview import make_preview, read_meta

needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                                  reason="ffmpeg / ffprobe not installed")


def out_paths(tmp_path):
    return tmp_path / "cache" / "ab" / "x.thumb.jpg", tmp_path / "cache" / "ab" / "x.large.jpg"


def sizes(*paths):
    return [Image.open(p).size for p in paths]


def test_jpeg_upright_and_sized(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    jpg = make_jpg(src / "a.jpg", size=(3200, 2000), orientation=6)      # stored landscape, shown portrait
    before = sorted(p.name for p in src.iterdir())
    thumb, large = out_paths(tmp_path)
    p = make_preview(jpg, "photo", thumb, large)
    assert p.ok and p.method == "image" and p.error is None
    assert sizes(thumb, large) == [(225, 360), (1000, 1600)]
    assert sorted(x.name for x in src.iterdir()) == before                # nothing written next to the original


def test_small_png_is_not_enlarged(tmp_path):
    png = tmp_path / "s.png"
    Image.new("RGB", (100, 50)).save(png)
    thumb, large = out_paths(tmp_path)
    assert make_preview(str(png), "photo", thumb, large).ok
    assert sizes(thumb, large) == [(100, 50), (100, 50)]


def test_failure_is_reported_not_raised(tmp_path):
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"nope")
    thumb, large = out_paths(tmp_path)
    p = make_preview(str(bad), "photo", thumb, large)
    assert not p.ok and p.method is None and p.error
    assert not thumb.exists() and not large.exists()
    p = make_preview(str(bad), "photo", tmp_path / "t.jpg", tmp_path / "l.jpg")      # beside the original
    assert not p.ok and "原片目录" in p.error


class FakeRaw:
    def __init__(self, thumb, flip=0):
        self.thumb, self.sizes = thumb, types.SimpleNamespace(flip=flip, width=4000, height=3000)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_thumb(self):
        if isinstance(self.thumb, Exception):
            raise self.thumb
        return self.thumb

    def postprocess(self, **kw):
        assert kw["half_size"]
        return np.zeros((300, 400, 3), np.uint8)


def fake_rawpy(monkeypatch, raw):
    jpeg, bitmap = object(), object()
    mod = types.SimpleNamespace(imread=lambda path: raw, ThumbFormat=types.SimpleNamespace(JPEG=jpeg, BITMAP=bitmap))
    monkeypatch.setitem(sys.modules, "rawpy", mod)
    return mod


def jpeg_bytes(size):
    buf = io.BytesIO()
    Image.new("RGB", size).save(buf, "JPEG")
    return buf.getvalue()


def test_raw_embedded_preview_turned_by_the_raw_flip(tmp_path, monkeypatch):
    raw = FakeRaw(None, flip=6)
    mod = fake_rawpy(monkeypatch, raw)
    raw.thumb = types.SimpleNamespace(format=mod.ThumbFormat.JPEG, data=jpeg_bytes((2000, 1500)))
    thumb, large = out_paths(tmp_path)
    p = make_preview(str(tmp_path / "x.ARW"), "photo", thumb, large)
    assert p.ok and p.method == "raw-embedded", p.error
    assert sizes(thumb, large) == [(270, 360), (1200, 1600)]


def test_raw_without_usable_thumb_is_decoded(tmp_path, monkeypatch):
    raw = FakeRaw(RuntimeError("no thumbnail"))
    fake_rawpy(monkeypatch, raw)
    thumb, large = out_paths(tmp_path)
    p = make_preview(str(tmp_path / "x.DNG"), "photo", thumb, large)
    assert p.ok and p.method == "raw-decoded" and "no thumbnail" in p.meta["embedded_error"]
    assert sizes(large) == [(400, 300)]
    mod = fake_rawpy(monkeypatch, raw)
    raw.thumb = types.SimpleNamespace(format=mod.ThumbFormat.JPEG, data=jpeg_bytes((160, 120)))   # too small
    assert make_preview(str(tmp_path / "x.DNG"), "photo", thumb, large).method == "raw-decoded"


def test_meta_without_exiftool(tmp_path, monkeypatch):
    monkeypatch.setattr(preview, "_tool", lambda name: None)
    jpg = make_jpg(tmp_path / "a.jpg", size=(64, 48), taken="2025:03:01 08:12:00", subsec="37", orientation=6,
                   gps=(37.5, -122.25))
    vid = tmp_path / "v.mp4"
    vid.write_bytes(b"\0")
    m = read_meta([jpg, str(vid)])
    a = m[jpg]
    assert a["taken_at"] == "2025-03-01T08:12:00.37" and a["camera"] == "SONY ILCE-7M4"
    assert (a["width"], a["height"], a["orientation"]) == (48, 64, 6)
    assert a["lat"] == pytest.approx(37.5, abs=1e-4) and a["lon"] == pytest.approx(-122.25, abs=1e-4)
    assert m[str(vid)]["playable"] is None and m[str(vid)]["duration_s"] is None
    assert set(a) == set(preview.META_KEYS)


@pytest.mark.skipif(not shutil.which("exiftool"), reason="exiftool not installed")
def test_meta_with_exiftool_matches(tmp_path):
    jpg = make_jpg(tmp_path / "a b.jpg", taken="2025:03:01 08:12:00", orientation=6, gps=(37.5, -122.25))
    a = read_meta([jpg])[jpg]
    assert a["taken_at"] == "2025-03-01T08:12:00" and a["camera"] == "SONY ILCE-7M4"
    assert (a["width"], a["height"], a["orientation"]) == (48, 64, 6)
    assert a["lat"] == pytest.approx(37.5, abs=1e-4)


@needs_ffmpeg
def test_video_frame_and_probe(tmp_path):
    vid = tmp_path / "src" / "clip.mp4"
    vid.parent.mkdir()
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10:duration=2",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(vid)], check=True)
    m = read_meta([str(vid)])[str(vid)]
    assert m["video_codec"] == "h264" and m["playable"] is True and m["fps"] == 10
    assert m["duration_s"] == pytest.approx(2, abs=0.2) and (m["width"], m["height"]) == (320, 240)
    thumb, large = out_paths(tmp_path)
    p = make_preview(str(vid), "video", thumb, large)
    assert p.ok and p.method == "video-frame", p.error
    assert sizes(thumb, large) == [(320, 240), (320, 240)]          # never enlarged
    assert sorted(x.name for x in vid.parent.iterdir()) == ["clip.mp4"]


def test_video_without_ffmpeg_fails_truthfully(tmp_path, monkeypatch):
    monkeypatch.setattr(preview, "_tool", lambda name: None)
    thumb, large = out_paths(tmp_path)
    p = make_preview(str(tmp_path / "v.mov"), "video", thumb, large)
    assert not p.ok and "ffmpeg" in p.error


def test_source_size_is_the_full_size_not_the_draft(tmp_path):
    jpg = make_jpg(tmp_path / "src.jpg", size=(4000, 3000), orientation=6)
    thumb, large = out_paths(tmp_path)
    p = make_preview(jpg, "photo", thumb, large)
    assert (p.meta["source_width"], p.meta["source_height"]) == (3000, 4000)     # upright, not draft-halved
    assert sizes(large) == [(1200, 1600)]


def test_raw_embedded_source_size_before_draft(tmp_path, monkeypatch):
    raw = FakeRaw(None, flip=6)
    mod = fake_rawpy(monkeypatch, raw)
    raw.thumb = types.SimpleNamespace(format=mod.ThumbFormat.JPEG, data=jpeg_bytes((6000, 4000)))
    thumb, large = out_paths(tmp_path)
    p = make_preview(str(tmp_path / "x.ARW"), "photo", thumb, large)
    assert p.method == "raw-embedded" and (p.meta["source_width"], p.meta["source_height"]) == (4000, 6000)
