"""bioscan.geotag (GPX parsing, time model, fix rule, clock offset, XMP) and the `bioscan geotag` /
`bioscan run --gpx` commands, on hand-built tracks and tiny synthetic JPEGs. No network."""
import csv
import io
import json
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import pytest
import raw_fixtures as F

from bioscan import geotag as gt
from bioscan import profile
from bioscan.cli import main as cli

T0 = datetime(2026, 5, 1, 15, 0, 0, tzinfo=timezone.utc).timestamp()   # 08:00:00 at -07:00
LAT0, LON0 = 37.5, -122.25
M_LAT = 1 / 111_195                     # degrees of latitude per metre (haversine radius)


def trkpts(points, ele=True):
    out = []
    for t, lat, lon in points:
        ts = datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        e = "<ele>12.5</ele>" if ele else ""
        out.append(f'<trkpt lat="{lat}" lon="{lon}">{e}<time>{ts}</time></trkpt>')
    return "".join(out)


def gpx(*segments, version="1.1") -> bytes:
    ns = "http://www.topografix.com/GPX/1/1" if version == "1.1" else "http://www.topografix.com/GPX/1/0"
    segs = "".join(f"<trkseg>{trkpts(s)}</trkseg>" for s in segments)
    return (f'<?xml version="1.0"?><gpx version="{version}" creator="t" xmlns="{ns}"><trk>{segs}</trk></gpx>').encode()


def north_walk(n=601, step_s=1.0, speed=1.0, t0=T0, lat0=LAT0, lon0=LON0):
    """A walk due north at `speed` m/s, one point per step: t, lat, lon."""
    return [(t0 + i * step_s, lat0 + i * step_s * speed * M_LAT, lon0) for i in range(n)]


def track(points) -> gt.Track:
    return gt.Track([gt.TrackPoint(t, la, lo) for t, la, lo in points])


# ---- GPX ------------------------------------------------------------------------------------

def test_parse_gpx_11_and_10_segments_and_untimed_points():
    body = gpx(north_walk(3), north_walk(2, t0=T0 + 100))
    segs = gt.parse_gpx(body)
    assert [len(s) for s in segs] == [3, 2]
    assert segs[0][0].t == T0 and segs[0][0].ele == 12.5
    v10 = gpx(north_walk(2), version="1.0")
    assert len(gt.parse_gpx(v10)[0]) == 2
    raw = (b'<gpx xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>'
           b'<trkpt lat="37" lon="-122"></trkpt>'                                    # no time: dropped
           b'<trkpt lat="x" lon="-122"><time>2026-05-01T15:00:00Z</time></trkpt>'    # bad lat: dropped
           b'<trkpt lat="37" lon="-122"><time>2026-05-01T15:00:01.500Z</time></trkpt>'
           b'</trkseg></trk></gpx>')
    (seg,) = gt.parse_gpx(raw)
    assert len(seg) == 1 and seg[0].t == T0 + 1.5 and seg[0].ele is None


def test_track_merges_files_in_time_order(tmp_path):
    (tmp_path / "b.gpx").write_bytes(gpx(north_walk(3, t0=T0 + 10)))
    (tmp_path / "a.gpx").write_bytes(gpx(north_walk(3)))
    tr = gt.Track.load([tmp_path / "b.gpx", tmp_path / "a.gpx"])
    assert len(tr) == 6 and tr.times == sorted(tr.times) and tr.segments == 2


# ---- time -------------------------------------------------------------------------------------

def test_capture_utc_precedence_and_formats():
    assert gt.capture_utc("2026-05-01T08:00:00-07:00") == T0                    # own offset
    assert gt.capture_utc("2026-05-01T08:00:00-07:00", gt.resolve_tz("+02:00")) == T0   # ... wins over tz
    assert gt.capture_utc("2026-05-01T08:00:00", gt.resolve_tz("-07:00")) == T0
    assert gt.capture_utc("2026:05:01 08:00:00", gt.resolve_tz("-0700")) == T0          # EXIF form
    assert gt.capture_utc("2026-05-01T08:00:00.37-07:00") == pytest.approx(T0 + 0.37)
    assert gt.capture_utc("2026-05-01T15:00:00Z") == T0
    assert gt.capture_utc("2026-05-01") is None and gt.capture_utc("") is None and gt.capture_utc(None) is None


def test_zone_names_follow_dst():
    la = gt.resolve_tz("America/Los_Angeles")
    assert gt.capture_utc("2026-05-01T08:00:00", la) == T0                                   # PDT, -07:00
    winter = datetime(2026, 1, 15, 16, 0, tzinfo=timezone.utc).timestamp()
    assert gt.capture_utc("2026-01-15T08:00:00", la) == winter                                # PST, -08:00
    assert gt.resolve_tz(None) is None and gt.resolve_tz("local") is None
    with pytest.raises(ValueError):
        gt.resolve_tz("Mars/Olympus")


@pytest.mark.parametrize("text,seconds", [("+00:01:23", 83), ("-01:00:00", -3600), ("+01:00", 3600),
                                          ("37", 37), ("-3600", -3600), ("+37.5", 37.5), ("0:00:01.5", 1.5)])
def test_parse_offset(text, seconds):
    assert gt.parse_offset(text) == seconds


def test_parse_offset_rejects_junk_and_formats_back():
    for bad in ("abc", "nan", "1h"):
        with pytest.raises(ValueError):
            gt.parse_offset(bad)
    assert gt.format_offset(37) == "+00:00:37" and gt.format_offset(-3600) == "-01:00:00"
    assert gt.format_offset(59.96) == "+00:01:00" and gt.format_offset(3599.97) == "+01:00:00"
    assert gt.format_offset(-0.04) == "+00:00:00" and gt.format_offset(90.25) == "+00:01:30.2"
    assert gt.parse_offset(gt.format_offset(-5025.5)) == -5025.5


# ---- fix rule -----------------------------------------------------------------------------------

def test_locate_interpolates_linearly_between_neighbours():
    tr = track([(T0, LAT0, LON0), (T0 + 10, LAT0 + 100 * M_LAT, LON0)])
    p = gt.locate(tr, T0 + 2.5)
    assert gt.distance_m(p.lat, p.lon, LAT0 + 25 * M_LAT, LON0) < 0.01
    assert p.dt_s == 2.5 and p.err_m == pytest.approx(gt.GPS_ERR_M + gt.DRIFT_MPS * 2.5)
    exact = gt.locate(tr, T0 + 10)
    assert exact.dt_s == 0 and exact.lat == LAT0 + 100 * M_LAT


def test_locate_gap_rules():
    moved = track([(T0, LAT0, LON0), (T0 + 3600, LAT0 + 3000 * M_LAT, LON0)])     # 1 h dropout, 3 km
    assert gt.locate(moved, T0 + 1800) is None
    assert gt.locate(moved, T0 + 1800, max_gap_s=7200) is not None
    still = track([(T0, LAT0, LON0), (T0 + 3600, LAT0 + 50 * M_LAT, LON0)])       # auto-pause, 50 m
    p = gt.locate(still, T0 + 1800)
    assert p is not None and p.err_m == pytest.approx(gt.GPS_ERR_M + 25, abs=0.1)
    assert gt.locate(still, T0 + 1800, max_span_m=10) is None


def test_stood_still_rule_has_a_time_limit():
    """Base camp: the watch saved a point on arrival and the next one 5 days later, 111 m away. A photo
    2 days in is not placed between them; a 2 h wait at a hide still is."""
    camp = track([(T0, LAT0, LON0), (T0 + 5 * 86400, LAT0 + 111 * M_LAT, LON0)])
    assert gt.locate(camp, T0 + 2 * 86400) is None
    assert gt.locate(camp, T0 + 2 * 86400, max_still_s=6 * 86400) is not None
    hide = track([(T0, LAT0, LON0), (T0 + 2 * 3600, LAT0 + 111 * M_LAT, LON0)])
    assert gt.locate(hide, T0 + 3600) is not None
    assert gt.MAX_STILL_S == 3 * 3600


def test_locate_outside_the_track_and_extrapolation():
    tr = track(north_walk(11))
    assert gt.locate(tr, T0 - 5) is None and gt.locate(tr, T0 + 20) is None
    p = gt.locate(tr, T0 - 5, extrapolate_s=10)
    assert (p.lat, p.lon) == (LAT0, LON0) and p.dt_s == 5 and p.err_m == gt.GPS_ERR_M + gt.WALK_MPS * 5
    assert gt.locate(gt.Track([]), T0) is None


# ---- clock offset --------------------------------------------------------------------------------

def refs_on(points, offset, times):
    """Reference photos on the walk at these true times, with the camera `offset` s fast."""
    tr = track(points)
    return [(t + offset, (p := gt.locate(tr, t)).lat, p.lon) for t in times]


@pytest.mark.parametrize("offset", [37.0, 3600.0, -5400.0, 0.0])
def test_estimate_offset_from_gps_photos(offset):
    walk = north_walk(1201)
    est = gt.estimate_offset(track(walk), refs_on(walk, offset, [T0 + 100, T0 + 600, T0 + 1100]))
    assert est.method == "gps" and est.offset_s == pytest.approx(offset, abs=0.2) and est.residual_m < 1


@pytest.mark.parametrize("offsets,want", [
    ([37.0, 1816.3, -817.0], 37.0),             # plain drift beats a quarter-hour look-alike (g0283)
    ([-146.0, 158.0, 1816.3], -146.0),          # the smallest small one
    ([2713.1, 3551.8, 4453.9], 3551.8),         # no small fit: whole hours (DST) first
    ([2713.1, 1790.0], 1790.0),                 # then half hours
    ([2713.1, 1234.0], 2713.1),                 # then quarter hours
])
def test_offset_prior_among_equal_fits(offsets, want):
    assert min(offsets, key=gt.offset_prior) == want


def test_estimate_offset_uses_an_even_spread_of_many_references():
    walk = north_walk(3001)
    refs = refs_on(walk, 37.0, [T0 + 10 * i for i in range(300)])
    est = gt.estimate_offset(track(walk), refs)
    assert est.offset_s == pytest.approx(37, abs=0.2) and est.refs == gt.MAX_REFS


def test_no_estimate_when_every_photo_has_gps(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("estimate_offset called")

    monkeypatch.setattr(gt, "estimate_offset", boom)
    tr = track(north_walk(601))
    res = gt.geotag([photo_at(f"{i}.jpg", T0 + i, lat=LAT0, lon=LON0) for i in range(300)], tr,
                    gt.resolve_tz("-07:00"))
    assert res.counts()["exif"] == 300 and res.offset.method == "none"


def test_estimate_offset_none_when_no_reference_is_near_the_track():
    assert gt.estimate_offset(track(north_walk(10)), [(T0, LAT0 + 1, LON0)]) is None
    assert gt.estimate_offset(track(north_walk(10)), []) is None


def test_offset_from_clock_photo():
    # camera says 08:01:00 (local, -07:00 from --tz); the watch in the picture shows 08:00:23
    assert gt.offset_from_clock("2026-05-01T08:01:00", "2026-05-01T08:00:23", gt.resolve_tz("-07:00")) == 37
    # the photo's own offset tells how to read the clock time
    assert gt.offset_from_clock("2026-05-01T08:01:00-07:00", "2026-05-01T08:00:23") == 37
    assert gt.offset_from_clock("2026-05-01T09:00:00-07:00", "2026-05-01T15:00:00Z") == 3600
    with pytest.raises(ValueError):
        gt.offset_from_clock("2026-05-01T08:01:00-07:00", "8am")


# ---- geotag ----------------------------------------------------------------------------------------

def photo_at(path, t_true, offset=0.0, **kw):
    local = datetime.fromtimestamp(t_true + offset, timezone.utc).astimezone(gt.resolve_tz("-07:00"))
    return gt.Photo(path, local.strftime("%Y-%m-%dT%H:%M:%S"), **kw)


def test_geotag_sources_and_exif_first():
    walk = north_walk(601)
    tr = track(walk)
    photos = [photo_at("a.jpg", T0 + 100), photo_at("b.jpg", T0 + 100, lat=1.0, lon=2.0),
              photo_at("c.jpg", T0 + 5000), gt.Photo("d.jpg", None)]
    res = gt.geotag(photos, tr, gt.resolve_tz("-07:00"))
    a, b, c, d = res.fixes
    assert a.source == "gpx" and gt.distance_m(a.lat, a.lon, LAT0 + 100 * M_LAT, LON0) < 0.01
    assert a.utc == "2026-05-01T15:01:40.000Z" and a.dt_s == 0
    assert (b.source, b.lat, b.lon) == ("exif", 1.0, 2.0)
    assert c.source == "none" and c.dt_s == 4400 and d.source == "none" and d.dt_s is None
    assert res.counts() == {"exif": 1, "gpx": 1, "none": 2}
    # b has GPS but is nowhere near the track: no offset estimated, and the reason is reported
    assert res.offset.method == "none" and res.offset.offset_s == 0
    assert any("within 100 m of the track" in w for w in res.warnings)


def test_geotag_recovers_a_fast_clock_from_gps_photos_and_honours_a_given_offset():
    walk = north_walk(1201)
    tr = track(walk)
    refs = [photo_at(f"ref{i}.jpg", t, 37, lat=p[1], lon=p[2])
            for i, (t, p) in enumerate((t, next(w for w in walk if w[0] == t)) for t in (T0 + 100, T0 + 700, T0 + 1100))]
    target = photo_at("x.jpg", T0 + 400, 37)
    res = gt.geotag([*refs, target], tr, gt.resolve_tz("-07:00"))
    assert res.offset.method == "gps" and res.offset.offset_s == pytest.approx(37, abs=0.2)
    x = res.fixes[-1]
    assert gt.distance_m(x.lat, x.lon, LAT0 + 400 * M_LAT, LON0) < 1
    given = gt.geotag([target], tr, gt.resolve_tz("-07:00"), offset_s=37)
    assert given.offset.method == "given" and given.fixes[0].lat == pytest.approx(x.lat, abs=1e-6)
    wrong = gt.geotag([target], tr, gt.resolve_tz("-07:00"))       # no correction: 37 m off
    assert gt.distance_m(wrong.fixes[0].lat, LON0, LAT0 + 400 * M_LAT, LON0) == pytest.approx(37, abs=0.5)


def test_geotag_warns_about_a_timezone_mistake():
    tr = track(north_walk(601))
    photos = [photo_at(f"{i}.jpg", T0 + 60 * i, 3 * 3600) for i in range(5)]      # camera on home time
    res = gt.geotag(photos, tr, gt.resolve_tz("-07:00"))
    assert res.counts()["none"] == 5
    assert any("timezone" in w for w in res.warnings)


def mixed_cameras():
    """Two cameras on one walk, each with its own GPS reference photos: A 37 s fast, B an hour
    slow (DST missed); C has none. Returns (track, photos, the true time of each target)."""
    walk = north_walk(1801)
    tr = track(walk)
    at = {w[0]: w for w in walk}
    photos = []
    for cam, off, t0 in (("A", 37.0, T0), ("B", -3600.0, T0 + 50)):
        photos += [photo_at(f"{cam}ref{i}.jpg", t, off, lat=at[t][1], lon=at[t][2], camera=cam)
                   for i, t in enumerate((t0 + 100, t0 + 700, t0 + 1300))]
        photos.append(photo_at(f"{cam}.jpg", t0 + 400, off, camera=cam))
    photos.append(photo_at("C.jpg", T0 + 900, camera="C"))
    return tr, photos, {"A.jpg": T0 + 400, "B.jpg": T0 + 450, "C.jpg": T0 + 900}


def test_mixed_cameras_get_their_own_clock_offsets():
    tr, photos, true = mixed_cameras()
    res = gt.geotag(photos, tr, gt.resolve_tz("-07:00"))
    assert res.cameras["A"].method == "gps" and res.cameras["A"].offset_s == pytest.approx(37, abs=0.2)
    assert res.cameras["B"].method == "gps" and res.cameras["B"].offset_s == pytest.approx(-3600, abs=0.2)
    assert res.cameras["C"] is res.offset                     # no references of its own: the folder's
    fixes = {f.path: f for f in res.fixes}
    for name in ("A.jpg", "B.jpg"):
        assert gt.distance_m(fixes[name].lat, fixes[name].lon, LAT0 + (true[name] - T0) * M_LAT, LON0) < 1
    # one offset for the folder (today's rule, as with no Make/Model) misplaces at least one camera
    blind = gt.geotag([gt.Photo(p.path, p.taken_at, p.lat, p.lon) for p in photos], tr, gt.resolve_tz("-07:00"))
    assert blind.cameras == {}
    assert any(f.source == "none" or gt.distance_m(f.lat, f.lon, LAT0 + (true[f.path] - T0) * M_LAT, LON0) > 30
               for f in blind.fixes if f.path in ("A.jpg", "B.jpg"))
    # a given offset holds for every camera; `given` sets cameras outright (the stage)
    assert gt.geotag(photos, tr, gt.resolve_tz("-07:00"), offset_s=37).cameras == {}
    staged = gt.geotag(photos, tr, gt.resolve_tz("-07:00"), 0.0, estimate=False,
                       given={"A": res.cameras["A"].offset_s, "B": res.cameras["B"].offset_s})
    assert [f.lat for f in staged.fixes][:-1] == [f.lat for f in res.fixes][:-1]   # C: 0 here, the folder's there


def test_one_camera_is_unchanged_by_the_camera_name():
    """A single-camera folder gives exactly what it gave before cameras were read: the camera name
    changes nothing, and no per-camera offsets are reported."""
    tr, photos, _ = mixed_cameras()
    one = [p for p in photos if p.camera == "A"]
    named = gt.geotag(one, tr, gt.resolve_tz("-07:00"))
    blind = gt.geotag([gt.Photo(p.path, p.taken_at, p.lat, p.lon) for p in one], tr, gt.resolve_tz("-07:00"))
    assert named == blind and named.cameras == {}
    assert named.offset.offset_s == pytest.approx(37, abs=0.2)


# ---- XMP --------------------------------------------------------------------------------------------

def test_xmp_coordinates_and_packet():
    assert gt.xmp_coordinate(37.5, "N", "S") == "37,30.000000N"
    assert gt.xmp_coordinate(-122.25, "E", "W") == "122,15.000000W"
    root = ET.fromstring(gt.xmp_packet(37.5, -122.25, -3.2).split("?>", 1)[1].rsplit("<?xpacket", 1)[0])
    desc = root.find(".//{http://www.w3.org/1999/02/22-rdf-syntax-ns#}Description")
    ns = "{http://ns.adobe.com/exif/1.0/}"
    assert desc.get(ns + "GPSLatitude") == "37,30.000000N" and desc.get(ns + "GPSLongitude") == "122,15.000000W"
    assert desc.get(ns + "GPSAltitudeRef") == "1" and desc.get(ns + "GPSAltitude") == "32/10"


def test_write_sidecar_never_clobbers(tmp_path):
    raw = tmp_path / "DSC0001.ARW"
    raw.write_bytes(b"")
    assert gt.write_sidecar(str(raw), 37.5, -122.25) == "written"
    assert "37,30.000000N" in (tmp_path / "DSC0001.xmp").read_text()
    (tmp_path / "DSC0001.xmp").write_text("editor settings")
    assert gt.write_sidecar(str(raw), 1, 2) == "exists"
    assert (tmp_path / "DSC0001.xmp").read_text() == "editor settings"
    dt = tmp_path / "DSC0002.ARW"
    (tmp_path / "DSC0002.ARW.xmp").write_text("darktable")               # darktable-style sidecar
    assert gt.write_sidecar(str(dt), 1, 2) == "exists" and not (tmp_path / "DSC0002.xmp").exists()


# ---- commands ----------------------------------------------------------------------------------------

def jpeg(path, when, gps=False, offset=None, camera=None):
    exif = F.exif_ifd(when=when, offset=offset)
    ifd0 = {0x0132: (F.ASCII, when)}
    if camera:
        make, model = camera.split(" ", 1)
        ifd0 |= {0x010F: (F.ASCII, make), 0x0110: (F.ASCII, model)}
    path.write_bytes(F.jpeg(F.tiff(ifd0, exif, F.GPS if gps else None)))


WALK0 = F.LAT - 300 * M_LAT


@pytest.fixture
def folder(tmp_path):
    """Three JPEGs (local -07:00, no offset tag): a at +100 s, b at +300 s with EXIF GPS, c after the
    track; and a 10-minute GPX walk from T0 that passes b's GPS position at b's time (WALK0 + 300 m)."""
    (tmp_path / "photos").mkdir()
    jpeg(tmp_path / "photos" / "a.jpg", "2026:05:01 08:01:40")
    jpeg(tmp_path / "photos" / "b.jpg", "2026:05:01 08:05:00", gps=True)
    jpeg(tmp_path / "photos" / "c.jpg", "2026:05:01 09:00:00")
    (tmp_path / "walk.gpx").write_bytes(gpx(north_walk(601, lat0=WALK0, lon0=F.LON)))
    return tmp_path


def test_geotag_command_csv_and_xmp(folder, capsys):
    out = folder / "geo.csv"
    code = cli.main(["geotag", str(folder / "photos"), "--gpx", str(folder / "walk.gpx"), "--tz=-07:00",
                     "--csv", str(out), "--xmp"])
    assert code == 0
    printed = capsys.readouterr()
    rows = {r["path"].rsplit("/", 1)[1]: r for r in csv.DictReader(io.StringIO(printed.out))}
    assert list(csv.DictReader(open(out))) == list(csv.DictReader(io.StringIO(printed.out)))
    assert rows["a.jpg"]["source"] == "gpx" and float(rows["a.jpg"]["lat"]) == pytest.approx(WALK0 + 100 * M_LAT, abs=1e-6)
    assert rows["b.jpg"]["source"] == "exif" and float(rows["b.jpg"]["lat"]) == pytest.approx(F.LAT)
    assert rows["c.jpg"]["source"] == "none" and rows["c.jpg"]["lat"] == ""
    assert (folder / "photos" / "a.xmp").is_file()
    assert not (folder / "photos" / "b.xmp").exists() and not (folder / "photos" / "c.xmp").exists()
    assert "1 exif, 1 gpx, 1 none" in printed.err and "1 sidecars written" in printed.err
    assert "from 1 photo(s) with GPS" in printed.err             # b set the offset: +00:00:00


def test_geotag_command_clock_photo_and_bad_input(folder, capsys):
    photos = folder / "photos"
    code = cli.main(["geotag", str(photos), "--gpx", str(folder / "walk.gpx"), "--tz=-07:00",
                     "--clock", f"{photos / 'a.jpg'}=2026-05-01T08:01:40"])
    assert code == 0 and "from 1 clock photo" in capsys.readouterr().err
    with pytest.raises(SystemExit, match="not among the inputs"):
        cli.main(["geotag", str(photos), "--gpx", str(folder / "walk.gpx"), "--clock", "/x.jpg=2026-05-01T08:00:00"])
    with pytest.raises(SystemExit, match="clock offset"):
        cli.main(["geotag", str(photos), "--gpx", str(folder / "walk.gpx"), "--offset", "soon"])
    (folder / "empty.gpx").write_bytes(gpx([]))
    with pytest.raises(SystemExit, match="no timed track points"):
        cli.main(["geotag", str(photos), "--gpx", str(folder / "empty.gpx")])


@pytest.fixture
def two_cameras(folder):
    """The folder's a, b, c as SONY ILCE-7RM5 (clock right; b has GPS), plus d and e from a FUJIFILM
    X-T5 37 s fast: e has GPS (taken where b was, at b's true time), d at a's true time."""
    for name, when, gps in (("a", "08:01:40", False), ("b", "08:05:00", True), ("c", "09:00:00", False)):
        jpeg(folder / "photos" / f"{name}.jpg", f"2026:05:01 {when}", gps=gps, camera="SONY ILCE-7RM5")
    jpeg(folder / "photos" / "d.jpg", "2026:05:01 08:02:17", camera="FUJIFILM X-T5")
    jpeg(folder / "photos" / "e.jpg", "2026:05:01 08:05:37", gps=True, camera="FUJIFILM X-T5")
    return folder


def test_geotag_command_mixed_cameras(two_cameras, capsys):
    code = cli.main(["geotag", str(two_cameras / "photos"), "--gpx", str(two_cameras / "walk.gpx"), "--tz=-07:00"])
    assert code == 0
    printed = capsys.readouterr()
    rows = {r["path"].rsplit("/", 1)[1]: r for r in csv.DictReader(io.StringIO(printed.out))}
    for name in ("a.jpg", "d.jpg"):                       # the same true time, each camera corrected
        assert rows[name]["source"] == "gpx"
        assert float(rows[name]["lat"]) == pytest.approx(WALK0 + 100 * M_LAT, abs=1e-5)
    assert "#   FUJIFILM X-T5: clock offset +00:00:37" in printed.err
    assert "#   SONY ILCE-7RM5: clock offset +00:00:00" in printed.err
    photos, walk = str(two_cameras / "photos"), str(two_cameras / "walk.gpx")
    staged = cli.build_payload(cli.parser().parse_args(["run", photos, "--gpx", walk, "--tz=-07:00",
                                                        "--profile", "wildlife"]), profile.builtin())
    assert staged["options"]["geotag"]["camera_offsets"] == {"FUJIFILM X-T5": "37.0", "SONY ILCE-7RM5": "0.0"}
    a = cli.parser().parse_args(["run", photos, "--gpx", walk, "--tz=-07:00"])
    inputs = {i["path"].rsplit("/", 1)[1]: i for i in cli.build_payload(a)["inputs"]}
    assert inputs["d.jpg"]["lat"] == pytest.approx(inputs["a.jpg"]["lat"], abs=1e-5)


def test_run_gpx_gives_per_file_coordinates_and_exif_stays_first(folder, monkeypatch):
    def no_exiftool(paths):
        raise AssertionError("run --gpx must not need exiftool")

    monkeypatch.setattr(cli.gt, "read_exif", no_exiftool)
    a = cli.parser().parse_args(["run", str(folder / "photos"), "--gpx", str(folder / "walk.gpx"), "--tz=-07:00"])
    inputs = {i["path"].rsplit("/", 1)[1]: i for i in cli.build_payload(a)["inputs"]}
    assert inputs["a.jpg"]["lat"] == pytest.approx(WALK0 + 100 * M_LAT, abs=1e-6) and inputs["a.jpg"]["lon"] == F.LON
    assert "lat" not in inputs["b.jpg"]                  # EXIF GPS: the service reads the file's own
    assert "lat" not in inputs["c.jpg"]                  # no fix
    # --lat/--lon fill only what neither EXIF (read with Pillow, no exiftool) nor the track placed
    a = cli.parser().parse_args(["run", str(folder / "photos"), "--gpx", str(folder / "walk.gpx"), "--tz=-07:00",
                                 "--lat", "1", "--lon", "2"])
    inputs = {i["path"].rsplit("/", 1)[1]: i for i in cli.build_payload(a)["inputs"]}
    assert inputs["a.jpg"]["lat"] != 1 and (inputs["c.jpg"]["lat"], inputs["c.jpg"]["lon"]) == (1, 2)
    assert "lat" not in inputs["b.jpg"]                  # its own GPS, not the batch default


def test_run_lat_lon_without_exiftool_keeps_exif_gps(folder, monkeypatch):
    # --lat/--lon without --gpx: EXIF GPS is read with Pillow, so a missing exiftool must not hand the
    # batch coordinate to a photo that has its own (the service ranks request coordinates first).
    monkeypatch.setattr(cli.gt.shutil, "which", lambda name: None)
    a = cli.parser().parse_args(["run", str(folder / "photos"), "--lat", "1", "--lon", "2"])
    inputs = {i["path"].rsplit("/", 1)[1]: i for i in cli.build_payload(a)["inputs"]}
    assert "lat" not in inputs["b.jpg"]
    assert (inputs["a.jpg"]["lat"], inputs["a.jpg"]["lon"]) == (1, 2) == (inputs["c.jpg"]["lat"], inputs["c.jpg"]["lon"])


def test_run_lat_lon_without_pillow_stops(folder, monkeypatch):
    monkeypatch.setitem(sys.modules, "bioscan.service.decode", None)    # import -> ImportError
    a = cli.parser().parse_args(["run", str(folder / "photos"), "--lat", "1", "--lon", "2"])
    with pytest.raises(SystemExit, match="--lat/--lon needs Pillow"):
        cli.build_payload(a)


def test_run_without_gpx_is_unchanged(folder):
    a = cli.parser().parse_args(["run", str(folder / "photos")])
    pl = cli.build_payload(a)
    assert json.dumps(pl, sort_keys=True) == json.dumps(
        {"inputs": [{"path": str(folder / "photos" / n)} for n in ("a.jpg", "b.jpg", "c.jpg")],
         "want": ["identify"], "options": {"identify": {"top_k": 5, "geo": True, "species": True}}}, sort_keys=True)


def test_run_warns_when_track_options_come_without_gpx(folder, capsys):
    a = cli.parser().parse_args(["run", str(folder / "photos"), "--tz=-07:00", "--offset", "+37"])
    cli.build_payload(a)
    assert "--offset, --tz only apply with --gpx" in capsys.readouterr().err
    cli.build_payload(cli.parser().parse_args(["run", str(folder / "photos")]))
    assert "warning" not in capsys.readouterr().err
