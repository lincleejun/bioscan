"""GPS and capture time from every supported container, built as tiny synthetic files
(raw_fixtures.py); broken files give (None, None, None). Real camera files are checked on the
owner's Mac with `python -m bioscan.service.decode DIR -r`."""
import struct

import pytest
import raw_fixtures as F

from bioscan import formats
from bioscan.service import decode

WHEN = "2026-05-01T08:00:00-07:00"
BASE = {0x010F: (F.ASCII, "CAMERA"), 0x0132: (F.ASCII, "2026:05:01 07:59:59")}
T = F.tiff


def full(**kw):
    return T(BASE, F.exif_ifd(**kw), F.GPS)


def jpeg_with_exif(**kw):
    return F.jpeg(T({0x0132: (F.ASCII, "2026:05:01 07:59:59")}, F.exif_ifd(**kw), F.GPS))


CONTAINERS = {   # name -> (bytes, container)
    "arw/nef/cr2/pef (TIFF, little-endian)": (full(), "tiff"),
    "TIFF big-endian": (T(BASE, F.exif_ifd(), F.GPS, big_endian=True), "tiff"),
    "dng": (T({**BASE, 0xC612: (F.SHORT, [1, 4])}, F.exif_ifd(), F.GPS), "tiff"),
    "orf IIRO": (T(BASE, F.exif_ifd(), F.GPS, magic=b"IIRO"), "orf"),
    "orf IIRS": (T(BASE, F.exif_ifd(), F.GPS, magic=b"IIRS"), "orf"),
    "orf MMOR": (T(BASE, F.exif_ifd(), F.GPS, magic=b"MMOR", big_endian=True), "orf"),
    "rw2": (T(BASE, F.exif_ifd(), F.GPS, magic=b"IIU\x00"), "rw2"),
    "rw2 EXIF only in JpgFromRaw": (T({0x002E: (F.UNDEFINED, jpeg_with_exif())}, magic=b"IIU\x00"), "rw2"),
    "cr3": (F.cr3(T(BASE), T(F.exif_ifd()), T(F.GPS)), "cr3"),
    "cr3 64-bit moov size": (F.cr3(T(BASE), T(F.exif_ifd()), T(F.GPS), large_moov=True), "cr3"),
    "cr3 big-endian CMTs": (F.cr3(T(BASE, big_endian=True), T(F.exif_ifd(), big_endian=True),
                                  T(F.GPS, big_endian=True)), "cr3"),
    "raf": (F.raf(jpeg_with_exif()), "raf"),
    "jpeg": (jpeg_with_exif(), "image"),
}


@pytest.mark.parametrize("name", CONTAINERS)
def test_gps_and_time_from_every_container(name):
    data, kind = CONTAINERS[name]
    assert decode.container(data) == kind
    lat, lon, taken_at = decode.read_exif(data)
    assert lat == pytest.approx(F.LAT) and lon == pytest.approx(F.LON)
    assert taken_at == WHEN


def test_partial_metadata():
    assert decode.read_exif(F.cr3(T(BASE), T(F.exif_ifd()), None)) == (None, None, WHEN)     # no CMT4
    assert decode.read_exif(F.cr3(T(BASE), None, None)) == (None, None, "2026-05-01T07:59:59")
    assert decode.read_exif(T(BASE, None, F.GPS, magic=b"IIRO"))[2] == "2026-05-01T07:59:59"
    assert decode.read_exif(F.raf(F.jpeg())) == (None, None, None)                          # JPEG without EXIF


def _cr3_bad_box():
    data = bytearray(F.cr3(T(BASE), T(F.exif_ifd()), T(F.GPS)))
    at = data.index(b"moov") - 4
    data[at:at + 4] = struct.pack(">L", 4)          # box smaller than its own header
    return bytes(data)


def _raf_pointing(offset, length):
    data = bytearray(F.raf(jpeg_with_exif()))
    data[84:92] = struct.pack(">LL", offset, length)
    return bytes(data)


cr3 = F.cr3(T(BASE), T(F.exif_ifd()), T(F.GPS))
BROKEN = {
    "empty": b"",
    "garbage": b"not a photo at all " * 20,
    "TIFF magic only": b"II*\x00",
    "ORF magic only": b"IIRO",
    "TIFF IFD offset past the end": b"II*\x00" + struct.pack("<L", 10_000) + b"\x00" * 32,
    "ORF IFD offset past the end": b"IIRO" + struct.pack("<L", 10_000) + b"\x00" * 32,
    "TIFF cut inside IFD0": full()[:20],
    "RW2 cut inside IFD0": T(BASE, F.exif_ifd(), F.GPS, magic=b"IIU\x00")[:20],
    "CR3 ftyp only": cr3[:cr3.index(b"moov") - 4],
    "CR3 cut inside moov": cr3[:cr3.index(b"CMT1") + 40],
    "CR3 box smaller than its header": _cr3_bad_box(),
    "CR3 without the Canon uuid": cr3.replace(F.CANON_UUID, b"\x11" * 16),
    "RAF header only": F.raf(jpeg_with_exif())[:92],
    "RAF JPEG offset past the end": _raf_pointing(1 << 30, 5000),
    "RAF pointing at its own header": _raf_pointing(0, 200),
    "RAF JPEG cut before its EXIF": _raf_pointing(160, 4),
    "JPEG cut after SOI": jpeg_with_exif()[:4],
}


@pytest.mark.parametrize("name", BROKEN)
def test_broken_files_give_nothing_and_never_raise(name):
    assert decode.read_exif(BROKEN[name]) == (None, None, None)


def test_subsec_time_original():
    ex = {0x9003: "2026:05:01 08:00:00", 0x9291: "37", 0x9011: "-07:00"}
    assert decode.taken_at_from(ex, {}) == "2026-05-01T08:00:00.37-07:00"
    assert decode.taken_at_from({**ex, 0x9291: "370 "}, {}) == "2026-05-01T08:00:00.370-07:00"
    for blank in ("", "   ", "ab", None):
        assert decode.taken_at_from({**ex, 0x9291: blank}, {}) == "2026-05-01T08:00:00-07:00"
    # IFD0 DateTime pairs with SubSecTime, not SubSecTimeOriginal
    assert decode.taken_at_from({0x9290: "5", 0x9291: "9"}, {0x0132: "2026:05:01 07:59:59"}) == "2026-05-01T07:59:59.5"
    assert decode.read_exif(full(subsec="04"))[2] == "2026-05-01T08:00:00.04-07:00"
    assert decode.read_exif(F.cr3(T(BASE), T(F.exif_ifd(subsec="12")), None))[2] == "2026-05-01T08:00:00.12-07:00"


def test_burst_frames_keep_their_order():
    times = [decode.read_exif(full(subsec=s))[2] for s in ("03", "37", "70")]
    assert times == sorted(times) and len(set(times)) == 3


def test_subsec_does_not_move_the_location_prior_week():
    """taken_at reaches identify only through geo.week_of; the fraction must not change it."""
    from bioscan.service.adapters import geo

    for t in ("2026-05-01T08:00:00", "2026-05-01T08:00:00-07:00", "2026-12-31T23:59:59+10:00"):
        frac = t[:19] + ".37" + t[19:]
        assert geo.week_of(frac) == geo.week_of(t) is not None


def test_decode_carries_raw_metadata(monkeypatch, tmp_path):
    """decode() on a CR3 (pixels from a faked rawpy) now has the GPS and time the file carries."""
    import sys
    import types

    import numpy as np

    class Raw:
        sizes = types.SimpleNamespace(flip=0, width=60, height=40)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def postprocess(self, **kw):
            return np.zeros((20, 30, 3), np.uint8)

    monkeypatch.setitem(sys.modules, "rawpy", types.SimpleNamespace(imread=lambda f: Raw()))
    for name, data in (("a.CR3", cr3), ("b.raf", CONTAINERS["raf"][0]), ("c.ORF", CONTAINERS["orf IIRO"][0]),
                       ("d.rw2", CONTAINERS["rw2"][0])):
        (tmp_path / name).write_bytes(data)
        d = decode.decode(str(tmp_path / name))
        assert (d.lat, d.lon, d.taken_at) == (pytest.approx(F.LAT), pytest.approx(F.LON), WHEN), name


def test_every_raw_extension_decodes_as_raw_in_any_case():
    for ext in formats.RAW_EXT:
        assert formats.is_raw(f"x.{ext}") and formats.is_raw(f"/d/x.{ext.upper()}")
    for name in ("x.jpg", "x.JPEG", "x.png", "x.tif", "arw", "x.arw.jpg"):
        assert not formats.is_raw(name)


def test_check_command_prints_metadata_per_file(tmp_path, capsys):
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.CR3").write_bytes(cr3)
    (tmp_path / "b.jpg").write_bytes(F.jpeg())
    (tmp_path / "sub" / "c.orf").write_bytes(CONTAINERS["orf IIRO"][0])
    (tmp_path / "notes.txt").write_text("x")
    assert decode.main([str(tmp_path), "-r"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "ext\tcontainer\tlat\tlon\ttaken_at\tpath"
    rows = {r.split("\t")[0]: r.split("\t") for r in lines[1:4]}
    assert rows["CR3"][1:5] == ["cr3", "37.500100", "-122.250000", WHEN]
    assert rows["JPG"][1:5] == ["image", "-", "-", "-"]
    assert rows["ORF"][1] == "orf"
    assert lines[4:] == ["# CR3: 1 files, 1 with GPS, 1 with capture time",
                         "# JPG: 1 files, 0 with GPS, 0 with capture time",
                         "# ORF: 1 files, 1 with GPS, 1 with capture time"]
    assert decode.main([str(tmp_path / "sub" / "missing-dir")]) == 1


def test_check_command_reports_an_unreadable_file(tmp_path, capsys, monkeypatch):
    (tmp_path / "a.nef").write_bytes(b"x")
    monkeypatch.setattr(decode.Path, "read_bytes", lambda self: (_ for _ in ()).throw(PermissionError(13, "Permission denied")))
    assert decode.main([str(tmp_path)]) == 0
    row = capsys.readouterr().out.splitlines()[1].split("\t")
    assert row[:2] == ["NEF", "unreadable"] and row[-1] == "Permission denied"
