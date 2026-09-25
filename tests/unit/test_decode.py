from PIL import Image

from bioscan.service import decode


def test_jpeg_orientation_resize_exif(tmp_path):
    im = Image.new("RGB", (3000, 1000), (200, 10, 10))
    exif = Image.Exif()
    exif[0x0112] = 6                                 # rotate 90 CW to display
    exif[0x0132] = "2026:05:01 08:00:00"
    p = tmp_path / "a.jpg"
    im.save(p, exif=exif)
    d = decode.decode(str(p))
    assert (d.width, d.height, d.orientation) == (1000, 3000, 6)
    assert d.image.size == (683, 2048)
    assert d.taken_at == "2026-05-01T08:00:00"
    assert len(d.sha256) == 64 and d.lat is None


def test_gps_from_ifd():
    lat, lon = decode.gps_from_ifd({1: "N", 2: (37.0, 30.0, 0.0), 3: "W", 4: (122.0, 15.0, 0.0)})
    assert abs(lat - 37.5) < 1e-9 and abs(lon + 122.25) < 1e-9
    assert decode.gps_from_ifd({}) == (None, None)


def test_taken_at_offset():
    assert decode.taken_at_from({0x9003: "2025:12:24 16:41:44", 0x9011: "-07:00"}, {}) == "2025-12-24T16:41:44-07:00"
    assert decode.taken_at_from({}, {}) is None


def test_small_image_not_upscaled():
    im = Image.new("RGB", (100, 50))
    assert decode.fit(im).size == (100, 50)


def test_detail_copy_is_larger_upright_frame(tmp_path):
    exif = Image.Exif()
    exif[0x0112] = 6
    p = tmp_path / "a.jpg"
    Image.new("RGB", (6000, 2000), (200, 10, 10)).save(p, exif=exif)
    d = decode.decode(str(p), detail_edge=3072)
    assert d.image.size == (683, 2048)
    assert d.detail is not None and d.detail.size == (1024, 3072)   # same upright frame, 1.5x the pixels


def test_detail_only_when_asked_and_larger(tmp_path):
    big, mid = tmp_path / "big.jpg", tmp_path / "mid.jpg"
    Image.new("RGB", (4000, 3000)).save(big)
    Image.new("RGB", (1800, 1200)).save(mid)
    assert decode.decode(str(big)).detail is None                      # not asked
    assert decode.decode(str(big), detail_edge=2048).detail is None    # not larger than the 2048 image
    assert decode.decode(str(mid), detail_edge=3072).detail is None    # source already fits in 2048
    assert decode.decode(str(big), detail_edge=8000).detail.size == (4000, 3000)   # never upscaled
    d, ms = decode.timed_decode(str(big), 3072)
    assert d.detail.size == (3072, 2304) and ms >= 0


def test_raw_orientation_from_libraw_flip(monkeypatch, tmp_path):
    """LibRaw rotates the pixels itself; we only report the EXIF orientation and the upright size."""
    import sys
    import types

    import numpy as np

    class Raw:
        def __init__(self, flip):
            self.sizes = types.SimpleNamespace(flip=flip, width=60, height=40)
            self.flip = flip

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def postprocess(self, **kw):
            assert kw["half_size"] and kw["use_camera_wb"] and kw["output_bps"] == 8
            h, w = (30, 20) if self.flip in (5, 6) else (20, 30)     # half size, already rotated
            return np.zeros((h, w, 3), np.uint8)

    for flip, orientation in ((0, 1), (3, 3), (5, 8), (6, 6)):
        monkeypatch.setitem(sys.modules, "rawpy", types.SimpleNamespace(imread=lambda f, flip=flip: Raw(flip)))
        p = tmp_path / f"x{flip}.ARW"
        p.write_bytes(b"not really raw")
        d = decode.decode(str(p))
        upright = (40, 60) if flip in (5, 6) else (60, 40)
        assert (d.width, d.height) == upright and d.orientation == orientation, flip
        assert d.image.size == ((20, 30) if flip in (5, 6) else (30, 20))


def test_camera_from_make_and_model():
    assert decode.camera_from({0x010F: "Canon", 0x0110: "Canon EOS R5"}) == "Canon EOS R5"      # make said once
    assert decode.camera_from({0x010F: "SONY", 0x0110: "ILCE-7RM5\x00"}) == "SONY ILCE-7RM5"
    assert decode.camera_from({0x0110: "X-T5"}) == "X-T5" and decode.camera_from({}) is None
