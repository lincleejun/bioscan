"""The geotag stage (bioscan/plugins/geotag): its manifest and checks, what `run` does per item, and
end to end through /run on the fake Engine: the service-side path (a profile with the stage) places
photos exactly as the CLI-side path (`run --gpx` without it) does, allow-roots covers the track,
and a run without a track is unchanged."""
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_geotag import M_LAT, WALK0, folder, gpx, north_walk  # noqa: F401 - folder is a fixture

from bioscan import geotag as gt
from bioscan import plugin, profile
from bioscan.cli import main as cli
from bioscan.plugins import BY_NAME
from bioscan.plugins.geotag import MANIFEST
from bioscan.plugins.geotag.stage import STAGE

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "contract"))
from conftest import Fakes, events  # noqa: E402

BUILTIN = profile.builtin()
T8 = "2026-05-01T08:"                 # the folder fixture's local capture times are -07:00, 08:xx


def opts(**given):
    o = {**MANIFEST.defaults, **given}
    MANIFEST.check(o)
    return o


def test_manifest():
    assert (MANIFEST.reads, MANIFEST.provides, MANIFEST.thread, MANIFEST.models({})) == (("time",), ("place",), "cpu", ())
    assert MANIFEST.defaults == {"gpx": [], "camera_utc_offset": "", "offset": "", "max_gap_s": gt.MAX_GAP_S,
                                 "max_span_m": gt.MAX_SPAN_M, "max_still_s": gt.MAX_STILL_S,
                                 "extrapolate_s": gt.EXTRAPOLATE_S}
    assert BY_NAME["geotag"] is MANIFEST
    for bad, message in [({"gpx": ["rel.gpx"]}, "gpx must be a list of absolute paths"),
                         ({"gpx": "/a.gpx"}, "gpx must be a list of absolute paths"),
                         ({"max_gap_s": -1}, "max_gap_s must be a number >= 0"),
                         ({"max_still_s": True}, "max_still_s must be a number >= 0"),
                         ({"offset": "soon"}, "options.geotag.offset: clock offset"),
                         ({"camera_utc_offset": "Mars/Base"}, "options.geotag.camera_utc_offset: unknown timezone"),
                         ({"offset": 3}, "offset must be a string")]:
        with pytest.raises(ValueError, match=message):
            opts(**bad)


def test_plan_runs_geotag_before_identify_and_full_never_has_it():
    p = plugin.plan(["identify", "geotag"], plugin.merge_options(None))
    assert p.stages == ("geotag", "identify") and p.want == ("identify", "geotag") and p.models == (
        "bioclip", "owlv2", "siglip2")
    assert plugin.plan(["geotag"], plugin.merge_options(None)).models == ()
    assert profile.resolve(BUILTIN, "wildlife").plan.stages == ("geotag", "identify")
    assert profile.resolve(BUILTIN, "full").want == ["identify"]


def item(path, lat=None, lon=None, exif=(None, None), taken=None):
    dec = SimpleNamespace(lat=exif[0], lon=exif[1], taken_at=taken)
    return plugin.Item(dec, {"path": path, "lat": lat, "lon": lon, "taken_at": None})


def test_run_places_only_photos_without_a_place(tmp_path):
    track = tmp_path / "walk.gpx"
    track.write_bytes(gpx(north_walk(601, lat0=WALK0, lon0=-122.25)))
    items = [item("/r.jpg", 1.0, 2.0, taken=T8 + "01:40-07:00"), item("/e.jpg", exif=(3.0, 4.0), taken=T8 + "01:40-07:00"),
             item("/g.jpg", taken=T8 + "01:40-07:00"), item("/n.jpg", taken="2026-05-01T09:00:00-07:00"),
             item("/t.jpg")]
    out = STAGE.run(None, items, opts(gpx=[str(track)]))
    assert [o["place_source"] for o in out] == ["request", "exif", "gpx", "none", "none"]
    assert out[2]["lat"] == pytest.approx(WALK0 + 100 * M_LAT, abs=1e-6) and out[2]["err_m"] is not None
    assert [it.facts.get("place") for it in items] == [None, None, (out[2]["lat"], out[2]["lon"]), None, None]
    assert (items[2].lat, items[2].lon) == (out[2]["lat"], -122.25)          # identify sees the track's place
    assert STAGE.run(None, items, opts()) == [None] * 5                      # no track: nothing to do
    off = STAGE.run(None, [item("/g.jpg", taken=T8 + "01:40-07:00")], opts(gpx=[str(track)], offset="+60"))
    assert off[0]["lat"] == pytest.approx(WALK0 + 40 * M_LAT, abs=1e-6)     # camera 60 s fast
    local = STAGE.run(None, [item("/g.jpg", taken=T8 + "01:40")], opts(gpx=[str(track)], camera_utc_offset="-07:00"))
    assert local[0]["place_source"] == "gpx"                                 # a time without an offset, read in the zone


def test_unreadable_tracks_are_request_errors(tmp_path):
    (tmp_path / "bad.gpx").write_text("<gpx")
    (tmp_path / "empty.gpx").write_bytes(gpx([]))
    with pytest.raises(ValueError, match="options.geotag.gpx: cannot read GPX"):
        STAGE.check_loaded(None, opts(gpx=[str(tmp_path / "bad.gpx")]))
    with pytest.raises(ValueError, match="cannot read GPX"):
        STAGE.check_loaded(None, opts(gpx=[str(tmp_path / "none.gpx")]))
    with pytest.raises(ValueError, match="no timed track points"):
        STAGE.check_loaded(None, opts(gpx=[str(tmp_path / "empty.gpx")]))
    assert STAGE.reads_paths(opts(gpx=["/a.gpx", "/b.gpx"])) == ["/a.gpx", "/b.gpx"]


# ---- end to end: the two run --gpx paths -------------------------------------------------------

def post(body, allow_roots=None, fakes=None):
    from fastapi.testclient import TestClient

    from bioscan.service.app import create_app

    fakes = fakes or Fakes()
    app = create_app(fakes.engine(), decode_pool=ThreadPoolExecutor(2), allow_roots=allow_roots)
    with TestClient(app) as c:
        r = c.post("/run", json=body)
    return r, fakes


def results(r):
    return {Path(e["path"]).name: e for e in events(r) if e["type"] == "result"}


def test_service_side_geotag_matches_cli_side_geotag(folder, capsys):  # noqa: F811
    photos, walk = str(folder / "photos"), str(folder / "walk.gpx")
    local = cli.build_payload(cli.parser().parse_args(["run", photos, "--gpx", walk, "--tz=-07:00"]), BUILTIN)
    staged = cli.build_payload(cli.parser().parse_args(["run", photos, "--gpx", walk, "--tz=-07:00",
                                                        "--profile", "wildlife"]), BUILTIN)
    # full: the track is read here and sent as coordinates, as before the stage existed
    assert local["want"] == ["identify"] and "geotag" not in local["options"] and "lat" in local["inputs"][0]
    # wildlife: the stage places the photos; the offset (+0 from b's GPS) is decided here for the folder
    assert staged["want"] == ["geotag", "identify"] and all("lat" not in i for i in staged["inputs"])
    assert staged["options"]["geotag"] == {"gpx": [walk], "camera_utc_offset": "-07:00", "offset": "0.0"}
    assert "the service's geotag stage places" in capsys.readouterr().err
    r1, f1 = post(local)
    r2, f2 = post(staged)
    a, b = results(r1), results(r2)
    assert {n: e["products"]["identify"] for n, e in a.items()} == {n: e["products"]["identify"] for n, e in b.items()}
    assert f1.where == f2.where and len(f1.where) == 2                     # a (track) and b (EXIF) reach the prior
    assert {n: e["products"]["geotag"]["place_source"] for n, e in b.items()} == {
        "a.jpg": "gpx", "b.jpg": "exif", "c.jpg": "none"}
    assert b["a.jpg"]["products"]["geotag"]["lat"] == local["inputs"][0]["lat"]


def test_staged_run_gpx_with_offset_and_lat_default(folder):  # noqa: F811
    photos, walk = str(folder / "photos"), str(folder / "walk.gpx")
    pl = cli.build_payload(cli.parser().parse_args(["run", photos, "--gpx", walk, "--offset", "+37",
                                                    "--profile", "wildlife"]), BUILTIN)
    assert pl["options"]["geotag"] == {"gpx": [walk], "offset": "+37"} and all("lat" not in i for i in pl["inputs"])
    pl = cli.build_payload(cli.parser().parse_args(["run", photos, "--gpx", walk, "--tz=-07:00", "--lat", "1",
                                                    "--lon", "2", "--max-gap", "60", "--profile", "wildlife"]), BUILTIN)
    by = {Path(i["path"]).name: i for i in pl["inputs"]}
    assert "lat" not in by["a.jpg"] and "lat" not in by["b.jpg"] and (by["c.jpg"]["lat"], by["c.jpg"]["lon"]) == (1, 2)
    assert pl["options"]["geotag"]["max_gap_s"] == 60


def test_a_track_outside_the_allowed_roots_is_refused(folder):  # noqa: F811
    photo = str(folder / "photos" / "a.jpg")
    body = {"inputs": [{"path": photo}], "profile": "wildlife", "options": {"geotag": {"gpx": [str(folder / "walk.gpx")]}}}
    r, fakes = post(body, allow_roots=[str(folder / "photos")])
    assert r.status_code == 400 and "walk.gpx" in r.json()["error"] and fakes.frames == []
    r, _ = post(body, allow_roots=[str(folder)])
    assert r.status_code == 200
    r, _ = post({**body, "options": {"geotag": {"gpx": [str(folder / "nope.gpx")]}}})
    assert r.status_code == 400 and "cannot read GPX" in r.json()["error"]


def test_no_track_leaves_the_run_unchanged(folder):  # noqa: F811
    """W6's no-GPX equivalence, now through the stage: wildlife without a track reports exactly what
    full does (geotag adds no product, identify's output is the same)."""
    photos = str(folder / "photos")
    plain = cli.build_payload(cli.parser().parse_args(["run", photos]), BUILTIN)
    wild = cli.build_payload(cli.parser().parse_args(["run", photos, "--profile", "wildlife"]), BUILTIN)
    assert wild["want"] == ["geotag", "identify"] and "geotag" not in wild["options"]
    assert json.dumps(plain["inputs"]) == json.dumps(wild["inputs"])
    a, b = results(post(plain)[0]), results(post(wild)[0])
    assert {n: e["products"] for n, e in a.items()} == {n: e["products"] for n, e in b.items()}
