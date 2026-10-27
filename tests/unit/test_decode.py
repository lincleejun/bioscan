import pytest
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


def test_gps_null_island_and_void_fix_are_none():
    """(0, 0) is what cameras write without a fix, 'V' marks a void one: neither is a place."""
    zero = {1: "N", 2: (0.0, 0.0, 0.0), 3: "E", 4: (0.0, 0.0, 0.0)}
    assert decode.gps_from_ifd(zero) == (None, None)
    here = {1: "N", 2: (37.0, 30.0, 0.0), 3: "W", 4: (122.0, 15.0, 0.0)}
    assert decode.gps_from_ifd({**here, 9: "V"}) == (None, None)
    assert decode.gps_from_ifd({**here, 9: "A"}) == (37.5, -122.25)
    assert decode.gps_from_ifd({**zero, 4: (0.0, 0.0, 1.0)})[0] == 0.0     # only exactly (0, 0)


def test_blank_or_zero_date_falls_back_to_ifd0_with_its_offset():
    base = {0x0132: "2026:05:01 07:59:59"}
    for junk in ("    :  :     :  :  ", "0000:00:00 00:00:00", "2026:00:00 00:00:00", "garbage-garbage-garbage"):
        assert decode.taken_at_from({0x9003: junk, 0x9011: "-07:00", 0x9010: "+01:00"}, base) \
            == "2026-05-01T07:59:59+01:00", junk                      # IFD0 DateTime pairs with OffsetTime
        assert decode.taken_at_from({0x9003: junk}, {0x0132: junk}) is None
    assert decode.taken_at_from({0x9003: "2026:05:01 08:00:00\x00"}, {}) == "2026-05-01T08:00:00"
    # only IFD0 DateTime and OffsetTimeOriginal: the offset is still used
    assert decode.taken_at_from({0x9011: "-07:00"}, base) == "2026-05-01T07:59:59-07:00"


@pytest.mark.parametrize("raw", ["2026-05-01 08:00:00", "2026/05/01 08:00:00", "2026:05:01T08:00:00",
                                 "2026-05-01T08:00:00+02:00", "  2026:05:01 08:00:00  "])
def test_non_exif_date_separators_still_read(raw):
    """Writers that use '-', '/' or 'T' gave a time before the blank/zero check; they still do."""
    assert decode.taken_at_from({0x9003: raw, 0x9011: "-07:00"}, {}) == "2026-05-01T08:00:00-07:00"


def test_16_bit_greyscale_keeps_its_range(tmp_path):
    """convert('RGB') on I;16 clips every value over 255 to white; the ramp must stay a ramp."""
    import numpy as np

    ramp = np.linspace(0, 65535, 4 * 256).reshape(4, 256).astype(np.uint16)
    for ext in ("png", "tif"):
        p = tmp_path / f"ramp.{ext}"
        Image.fromarray(ramp).save(p)
        with Image.open(p) as im:
            assert im.mode.startswith("I")
        px = np.asarray(decode.decode(str(p)).image)
        assert px.min() == 0 and px.max() == 255 and (px == 255).mean() < 0.01, ext
        assert abs(int(px[0, 128, 0]) - 32) <= 1, ext          # mid-row value scaled by 1/256, not clipped


def test_32_bit_greyscale_with_8_bit_values_is_not_darkened(tmp_path):
    import numpy as np

    ramp = np.tile(np.arange(256, dtype=np.int32), (4, 1))
    p = tmp_path / "ramp.tif"
    Image.fromarray(ramp).save(p)
    with Image.open(p) as im:
        assert im.mode == "I"
    px = np.asarray(decode.decode(str(p)).image)
    assert px.min() == 0 and px.max() == 255 and int(px[0, 128, 0]) == 128


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
