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
