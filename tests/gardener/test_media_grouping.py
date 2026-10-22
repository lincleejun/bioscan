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


def test_unsettled_assets_wait_and_a_running_pass_makes_no_folds():
    days = [f"2025-03-0{d}T09:00:00" for d in (1, 2)]
    assets = ([asset(f"o{i}", days[i % 2], content=BIRD, species=OWL) for i in range(12)]
              + [asset("e", days[0], content=BIRD, species=EAGLE), asset("w", days[0], settled=False)])
    running = suggest(assets, final=False)
    assert [g.key for g in running] == ["species:Megascops asio"]            # no eagle fold, no "w"
    done = by_key(suggest(assets))
    assert "animal:bird" in done and not any("w" in g.asset_ids for g in done.values())


def test_time_never_splits_a_subject():
    times = ["2025-08-29T09:00:00"] * 6 + ["2025-08-30T10:00:00"] * 5 + ["2024-09-02T08:00:00"] * 2 + [None]
    assets = [asset(f"m{i:02d}", t, content=MOUNTAIN) for i, t in enumerate(times)]
    assets += [asset(f"p{i:02d}", t, content={"group": "people", "label": "portrait"}, people=["a_x"])
               for i, t in enumerate(times)]
    got = suggest(assets)
    assert [(g.key, len(g.asset_ids)) for g in got] == [("person:a_x", 14), ("scene:mountain", 14)]


def test_named_people_keep_their_name_and_never_fold():
    assets = ([asset("n1", content={"group": "people", "label": "portrait"}, people=["named:p_1"])]
              + [asset(f"u{i}", content={"group": "people", "label": "portrait"}, people=["a_u"]) for i in range(3)])
    g = by_key(suggest(assets, names={"named:p_1": "小明"}))
    assert g["person:named:p_1"].name == "小明" and g["person:named:p_1"].asset_ids == ["n1"]   # one photo: still a group
    assert g["person:a_u"].name == "人物 1"                                  # clusters are numbered among themselves
    assert "person:other" not in g
    running = suggest(assets, final=False, names={"named:p_1": "小明"})
    assert [x.key for x in running] == ["person:named:p_1"]      # clusters (and "人物 N") only once faces are all in


def test_large_subjects_with_gps_split_by_place_and_touching_cells_join():
    here = [asset(f"a{i}", "2025-08-29T09:00:00", 61.10 + 0.01 * i, -149.80, content=MOUNTAIN) for i in range(5)]
    there = [asset(f"b{i}", "2025-08-31T09:00:00", 63.50, -150.10, content=MOUNTAIN) for i in range(4)]
    assets = here + there + [asset("n", "2025-08-30T09:00:00", content=MOUNTAIN)]
    kids = [g for g in suggest(assets) if g.parent]
    assert [sorted(g.asset_ids) for g in kids] == [[a["id"] for a in here], [a["id"] for a in there], ["n"]]
    assert kids[0].kind == "place" and kids[0].name == "山地 · 地点 (61.12, -149.80) · 8月29日"
    assert kids[2].name == "山地 · 地点未知"


def test_place_names_use_the_task_zone():
    assets = [asset(f"p{i}", "2025-03-02T03:00:00Z", 61.10, -149.80, content=MOUNTAIN) for i in range(5)]
    assets += [asset(f"q{i}", "2025-03-02T03:00:00Z", 63.50, -150.10, content=MOUNTAIN) for i in range(5)]
    assert [g.name for g in suggest(assets) if g.parent][0].endswith("3月2日")
    assert [g.name for g in suggest(assets, tz="-08:00") if g.parent][0].endswith("3月1日")   # 19:00 the day before
    with pytest.raises(ValueError):
        suggest(assets, tz="Mars/Olympus")
    assert suggest([]) == []
