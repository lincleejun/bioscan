"""grouping: event, place, scene and species suggestions."""
from __future__ import annotations

import pytest

from bioscan.gardener.grouping import suggest


def asset(i, taken_at=None, lat=None, lon=None, content=None, species=None):
    return {"id": i, "taken_at": taken_at, "lat": lat, "lon": lon, "media": "photo", "content": content,
            "species": species}


def test_events_split_on_gaps_over_two_hours():
    assets = [asset("a", "2025-03-01T08:12:05"), asset("b", "2025-03-01T10:12:05"),     # 2 h exactly: same
              asset("c", "2025-03-01T12:12:06"), asset("d"), asset("e", "not a time")]
    events = [g for g in suggest(assets) if g.kind == "event"]
    assert [(g.key, g.name, g.asset_ids) for g in events] == [
        ("event:20250301-0812", "3月1日 08:12 起 · 2 张", ["a", "b"]),
        ("event:20250301-1212", "3月1日 12:12 起 · 1 张", ["c"])]
    assert events[0].summary["count"] == 2


def test_times_with_an_offset_compare_as_instants():
    # a camera at -08:00 and a video stamped in UTC, the same moment: one event, named in the photo's local time
    assets = [asset("photo", "2025-03-01T08:12:00-08:00"), asset("video", "2025-03-01T16:13:00Z"),
              asset("later", "2025-03-01T10:00:00-08:00")]
    events = [g for g in suggest(assets) if g.kind == "event"]
    assert [g.asset_ids for g in events] == [["photo", "video", "later"]]
    assert events[0].name == "3月1日 08:12 起 · 3 张"
    far = [g for g in suggest([asset("x", "2025-03-01T08:12:00-08:00"), asset("y", "2025-03-01T08:12:00Z")])
           if g.kind == "event"]
    assert [g.asset_ids for g in far] == [["y"], ["x"]] and len({g.key for g in far}) == 2


def events(assets, **kw):
    return [g for g in suggest(assets, **kw) if g.kind == "event"]


def test_naive_and_aware_at_the_same_local_time_stay_together():
    got = events([asset("naive", "2025-03-01T10:00:00"), asset("aware", "2025-03-01T10:00:00-08:00")])
    assert [g.asset_ids for g in got] == [["aware", "naive"]]          # same moment: ties by id
    assert got[0].summary["time_basis"] == "local" and got[0].summary["uncertain_ids"] == []
    assert got[0].name == "3月1日 10:00 起 · 2 张"


def test_given_zone_places_camera_time_against_a_utc_video():
    assets = [asset("photo", "2025-03-01T10:05:00"), asset("video", "2025-03-01T18:02:00Z")]
    got = events(assets, tz="-08:00")
    assert len(got) == 1 and set(got[0].asset_ids) == {"photo", "video"}
    s = got[0].summary
    assert s["time_basis"] == "instant" and (s["start"], s["end"]) == ("2025-03-01T10:02:00-08:00",
                                                                        "2025-03-01T10:05:00-08:00")
    assert got[0].name == "3月1日 10:02 起 · 2 张"
    assert events(assets, tz="America/Los_Angeles")[0].asset_ids == got[0].asset_ids
    assert assets[0]["taken_at"] == "2025-03-01T10:05:00"            # inputs untouched
    with pytest.raises(ValueError):
        suggest(assets, tz="Mars/Olympus")


def test_without_zone_a_utc_stamp_among_local_times_is_flagged_not_trusted():
    assets = [asset("photo", "2025-03-01T10:05:00"), asset("video", "2025-03-01T18:02:00Z"),
              asset("other", "2025-03-01T10:30:00-08:00")]
    got = events(assets)
    flagged = [g for g in got if "video" in g.asset_ids][0]
    assert flagged.summary["time_basis"] == "mixed-unknown" and flagged.summary["uncertain_ids"] == ["video"]
    local = [g for g in got if "photo" in g.asset_ids][0]
    assert set(local.asset_ids) == {"photo", "other"} and local.summary["time_basis"] == "local"
    for g in got:                                                    # nothing shown as UTC or with an offset
        assert not any(x.endswith(("Z", "+00:00")) for x in (g.summary["start"], g.summary["end"]))


def test_start_and_end_share_one_basis():
    # the reviewer's case: a UTC video three minutes before a -08:00 photo
    got = events([asset("photo", "2025-03-01T10:05:00-08:00"), asset("video", "2025-03-01T18:02:00Z")])
    s = got[0].summary
    assert (s["start"], s["end"]) == ("2025-03-01T10:02:00-08:00", "2025-03-01T10:05:00-08:00")
    assert got[0].name == "3月1日 10:02 起 · 2 张"


def test_places_on_a_km_grid_without_gps_left_out():
    assets = [asset("a", lat=37.5001, lon=-122.2501), asset("b", lat=37.5003, lon=-122.2502),
              asset("c", lat=38.0, lon=-122.0), asset("d"), asset("e", lat=0.0, lon=0.0)]
    places = [g for g in suggest(assets) if g.kind == "place"]
    assert [g.asset_ids for g in places] == [["a", "b"], ["c"]]
    assert places[0].name == "地点 · 约 2 张 (37.50, -122.25)"
    assert places[0].key == [g for g in suggest(assets[:2]) if g.kind == "place"][0].key      # stable


def test_scene_and_species_groups():
    owl = {"taxon": "Megascops asio", "common": "Eastern Screech-Owl", "level": "species"}
    assets = [asset("a", content={"group": "wildlife", "label": "bird_portrait"}, species=owl),
              asset("b", content={"group": "wildlife", "label": "bird_flight"},
                    species={"taxon": "Megascops", "common": None, "level": "genus"}),
              asset("c", content={"group": "landscape", "label": "mountain"},
                    species={"taxon": "Megascops kennicottii", "common": None, "level": "species"}),
              asset("d")]
    by = {g.key: g for g in suggest(assets)}
    assert by["scene:wildlife"].name == "野生动物" and by["scene:wildlife"].asset_ids == ["a", "b"]
    assert by["scene:landscape"].name == "风光"
    assert by["species:Megascops asio"].name == "Eastern Screech-Owl"
    assert by["species:Megascops kennicottii"].name == "Megascops kennicottii"
    assert "species:Megascops" not in by                     # genus level: no species group
    assert suggest([]) == []
