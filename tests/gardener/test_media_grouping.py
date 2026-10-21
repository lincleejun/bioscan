"""grouping: event, place, scene and species suggestions."""
from __future__ import annotations

from bioscan.gardener.grouping import suggest


def asset(i, taken_at=None, lat=None, lon=None, content=None, species=None):
    return {"id": i, "taken_at": taken_at, "lat": lat, "lon": lon, "media": "photo", "content": content,
            "species": species}


def test_events_split_on_gaps_over_two_hours():
    assets = [asset("a", "2025-03-01T08:12:05-08:00"), asset("b", "2025-03-01T10:12:05"),   # 2 h exactly: same
              asset("c", "2025-03-01T12:12:06-08:00"), asset("d"), asset("e", "not a time")]
    events = [g for g in suggest(assets) if g.kind == "event"]
    assert [(g.key, g.name, g.asset_ids) for g in events] == [
        ("event:20250301-0812", "3月1日 08:12 起 · 2 张", ["a", "b"]),
        ("event:20250301-1212", "3月1日 12:12 起 · 1 张", ["c"])]
    assert events[0].summary["count"] == 2


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
