"""grouping: subject groups (people, taxa, animal types, scenes) split by place or shooting day."""
from __future__ import annotations

import pytest

from bioscan.gardener.grouping import suggest

OWL = {"taxon": "Megascops asio", "common": "Eastern Screech-Owl", "level": "species"}
EAGLE = {"taxon": "Haliaeetus leucocephalus", "common": "Bald Eagle", "level": "species"}
BIRD = {"group": "wildlife", "label": "bird_portrait"}
MOUNTAIN = {"group": "landscape", "label": "mountain"}


def asset(i, taken_at=None, lat=None, lon=None, content=None, species=None, people=None, media="photo", settled=True):
    return {"id": i, "taken_at": taken_at, "lat": lat, "lon": lon, "media": media, "content": content,
            "species": species, "people": people, "settled": settled}


def by_key(groups):
    return {g.key: g for g in groups}


def test_subjects_taxa_animal_types_scenes_people_and_videos():
    assets = ([asset(f"o{i}", content=BIRD, species=OWL) for i in range(3)]
              + [asset(f"g{i}", content=BIRD, species={"taxon": "Megascops", "common": None, "level": "genus"})
                 for i in range(3)]
              + [asset(f"b{i}", content={"group": "wildlife", "label": "bird_flight"}) for i in range(3)]
              + [asset(f"m{i}", content=MOUNTAIN) for i in range(3)]
              + [asset(f"p{i}", content={"group": "people", "label": "portrait"}) for i in range(3)]
              + [asset("v", media="video"), asset("none")])
    g = by_key(suggest(assets))
    assert g["species:Megascops asio"].name == "Eastern Screech-Owl" and g["species:Megascops asio"].kind == "species"
    assert g["species:Megascops"].name == "Megascops（属）"
    assert g["animal:bird"].asset_ids == ["b0", "b1", "b2"] and g["animal:bird"].name == "鸟类 · 未识别到种"
    assert g["scene:mountain"].name == "山地" and g["scene:mountain"].kind == "scene"
    assert g["person:other"].asset_ids == ["p0", "p1", "p2"]                 # people without a known face
    assert g["other:video"].asset_ids == ["v"] and g["other:none"].name == "未分类"
    assert "scene:bird_portrait" not in g                                    # an animal photo is not a scene


def test_rare_subjects_fold_into_a_broader_group():
    assets = [asset("e1", content=BIRD, species=EAGLE), asset("e2", content=BIRD, species=EAGLE),
              asset("x", content={"group": "wildlife", "label": "mammal_habitat"},
                    species={"taxon": "Alces alces", "common": "Moose", "level": "species"}),
              asset("c1", content={"group": "landscape", "label": "coast"}),
              asset("f1", content={"group": "landscape", "label": "forest"})]
    g = by_key(suggest(assets))
    assert g["animal:bird"].asset_ids == ["e1", "e2"]                        # 2 eagles: into their animal type
    assert g["animal:mammal"].asset_ids == ["x"]
    assert g["scene:other"].asset_ids == ["c1", "f1"] and g["scene:other"].name == "其他场景"
    assert not any(k.startswith("species:") for k in g)


def test_people_are_named_by_size_and_share_photos_with_taxa():
    big, small = "a_big", "a_small"
    assets = ([asset(f"x{i}", content=BIRD, species=OWL, people=[big]) for i in range(3)]
              + [asset(f"y{i}", content={"group": "people", "label": "portrait"}, people=[big]) for i in range(2)]
              + [asset(f"z{i}", content={"group": "people", "label": "group"}, people=[small, big] if i == 0 else [small])
                 for i in range(3)]
              + [asset("lone", content={"group": "people", "label": "portrait"}, people=["a_lone"])])
    g = by_key(suggest(assets))
    assert g[f"person:{big}"].name == "人物 1" and len(g[f"person:{big}"].asset_ids) == 6
    assert g[f"person:{small}"].name == "人物 2" and g[f"person:{small}"].asset_ids == ["z0", "z1", "z2"]
    assert g["species:Megascops asio"].asset_ids == ["x0", "x1", "x2"]       # in the person's group too
    assert g["person:other"].asset_ids == ["lone"]                           # one photo of someone: 人物 · 其他


def test_unsettled_assets_wait_and_a_running_pass_makes_no_folds_or_children():
    days = [f"2025-03-0{d}T09:00:00" for d in (1, 2)]
    assets = ([asset(f"o{i}", days[i % 2], content=BIRD, species=OWL) for i in range(12)]
              + [asset("e", days[0], content=BIRD, species=EAGLE), asset("w", days[0], settled=False)])
    running = suggest(assets, final=False)
    assert [g.key for g in running] == ["species:Megascops asio"]            # no eagle fold, no days, no "w"
    done = by_key(suggest(assets))
    assert "animal:bird" in done and not any("w" in g.asset_ids for g in done.values())
    assert {k for k in done if "/" in k} == {"species:Megascops asio/day:2025-03-01",
                                            "species:Megascops asio/day:2025-03-02"}


def test_large_subjects_split_by_day_small_days_fold_one_day_no_split():
    times = ["2025-08-29T09:00:00"] * 6 + ["2025-08-30T10:00:00"] * 5 + ["2025-09-02T08:00:00"] * 2 + [None]
    assets = [asset(f"m{i:02d}", t, content=MOUNTAIN) for i, t in enumerate(times)]
    kids = [g for g in suggest(assets) if g.parent == "scene:mountain"]
    assert [(g.key.split("/")[1], g.name, len(g.asset_ids)) for g in kids] == [
        ("day:2025-08-29", "山地 · 8月29日", 6), ("day:2025-08-30", "山地 · 8月30日", 5),
        ("day:other", "山地 · 其他日期", 2), ("day:none", "山地 · 无日期", 1)]
    assert all(g.kind == "day" for g in kids)
    one_day = [asset(f"s{i:02d}", "2025-08-29T09:00:00", content=MOUNTAIN) for i in range(12)]
    assert [g.key for g in suggest(one_day)] == ["scene:mountain"]
    few = [asset(f"f{i}", f"2025-08-2{i}T09:00:00", content=MOUNTAIN) for i in range(9)]
    assert [g.key for g in suggest(few)] == ["scene:mountain"]               # under SPLIT_MIN: never split
    years = [asset(f"y{i:02d}", f"{2024 + i % 2}-10-13T09:00:00", content=MOUNTAIN) for i in range(10)]
    assert {g.name for g in suggest(years) if g.parent} == {"山地 · 2024年10月13日", "山地 · 2025年10月13日"}


def test_large_subjects_with_gps_split_by_place_and_touching_cells_join():
    here = [asset(f"a{i}", "2025-08-29T09:00:00", 61.10 + 0.01 * i, -149.80, content=MOUNTAIN) for i in range(5)]
    there = [asset(f"b{i}", "2025-08-31T09:00:00", 63.50, -150.10, content=MOUNTAIN) for i in range(4)]
    assets = here + there + [asset("n", "2025-08-30T09:00:00", content=MOUNTAIN)]
    kids = [g for g in suggest(assets) if g.parent]
    assert [sorted(g.asset_ids) for g in kids] == [[a["id"] for a in here], [a["id"] for a in there], ["n"]]
    assert kids[0].kind == "place" and kids[0].name == "山地 · 地点 (61.12, -149.80) · 8月29日"
    assert kids[2].name == "山地 · 地点未知"


def test_shooting_day_uses_the_task_zone_and_flags_utc_stamps_without_one():
    assets = ([asset(f"p{i}", "2025-03-01T10:05:00", content=MOUNTAIN) for i in range(5)]
              + [asset(f"v{i}", "2025-03-02T03:00:00Z", content=MOUNTAIN) for i in range(5)])
    kids = [g for g in suggest(assets) if g.parent]
    assert [g.name for g in kids] == ["山地 · 3月1日", "山地 · 3月2日"]
    assert kids[1].summary["uncertain_ids"] == [f"v{i}" for i in range(5)]
    assert [g.key for g in suggest(assets, tz="-08:00")] == ["scene:mountain"]   # 19:00 the same day: one day
    with pytest.raises(ValueError):
        suggest(assets, tz="Mars/Olympus")
    assert suggest([]) == []
