"""The burst and select reducers (bioscan/cull.py) on crafted result events, and how profiles carry
reducer options."""
import base64
import struct

import pytest
from cull_fixtures import result as ev

from bioscan import cull, profile

BURST = {"max_gap_s": 1.5, "min_cosine": 0.92}
SELECT = {"per_category": 2, "dup_cosine": 0.95, "sharp_tie": 0.03, "exposure_ok": 0.2, "by": "group",
          "waive": {"night": ["underexposed"]}, "horizon_flag_deg": 3.0, "headroom_min": 0.02}


def test_burst_chains_one_camera_close_in_time_and_alike():
    events = [ev("a", 0.0), ev("b", 0.2), ev("c", 0.4), ev("far", 10.0),           # a-b-c burst; far alone
              ev("other_cam", 0.1, camera="Cam B"),                               # another camera: alone
              ev("unlike", 0.6, v=(0.0, 1.0)), ev("after", 0.8),                   # a different scene breaks the chain
              ev("no_time"), ev("no_vec", 11.0, v=None),
              {"type": "error", "path": "bad", "product": None, "message": "x"}]
    b = cull.burst(events, BURST)
    assert (b["a"]["id"], b["b"]["id"], b["c"]["id"]) == ("b0001",) * 3 and b["a"]["size"] == 3
    assert [b[p]["index"] for p in "abc"] == [0, 1, 2] and b["b"]["gap_s"] == 0.2 and b["b"]["cosine"] == 1.0
    assert all(b[p]["id"] is None and b[p]["size"] == 1 for p in ("far", "other_cam", "unlike", "no_time", "no_vec"))
    assert b["after"]["id"] is None and b["unlike"]["cosine"] == 0.0 and "bad" not in b
    assert cull.burst([ev("x", 0.0), ev("y", 1.4)], BURST)["y"]["id"] == "b0001"
    assert cull.burst([ev("x", 0.0), ev("y", 1.6)], BURST)["y"]["id"] is None          # max_gap_s


def test_burst_reads_float16_vectors_and_camera_fallback():
    f16 = base64.b64encode(struct.pack("<4e", 1.0, 0.0, 0.0, 0.0)).decode()
    a, b = ev("a", 0.0, camera=None), ev("b", 0.3, camera=None)
    a["products"]["embed"]["vector"] = f16
    got = cull.burst([a, b], BURST)
    assert got["a"]["id"] == got["b"]["id"] == "b0001"                  # no camera: one camera


def run(events, **select):
    out = cull.apply(events, {"burst": BURST, "select": {**SELECT, **select}})
    return {r["path"]: r for r in cull.records(out)}


def test_best_of_burst_in_criteria_order():
    # rejected frames lose even when sharper; within sharp_tie, not cut off wins; then exposure; then aesthetic
    r = run([ev("rejected", 0.0, blur=0.1, reasons=["overexposed"]), ev("soft", 0.2, blur=0.4),
             ev("cut", 0.4, blur=0.21, cut=True), ev("dark", 0.6, blur=0.22, exposure=-0.3),
             ev("best", 0.8, blur=0.215, aesthetic=0.1), ev("pretty", 1.0, blur=0.22, aesthetic=0.9)])
    ranks = {p: x["burst_rank"] for p, x in r.items()}
    assert ranks == {"pretty": 1, "best": 2, "dark": 3, "cut": 4, "soft": 5, "rejected": 6}
    assert r["pretty"]["status"] == "pick" and r["pretty"]["keep"] and r["pretty"]["rank"] == 1
    assert r["best"]["status"] == "duplicate" and r["best"]["duplicate_of"] == "pretty"
    assert r["rejected"]["status"] == "reject" and r["rejected"]["reasons"] == ["overexposed"]
    assert all(x["burst"] == "b0001" and x["burst_size"] == 6 for x in r.values())


def test_sharpness_outside_the_tie_band_beats_the_rest():
    r = run([ev("sharp_cut", 0.0, blur=0.15, cut=True), ev("soft_whole", 0.2, blur=0.3)])
    assert r["sharp_cut"]["burst_rank"] == 1


def test_top_per_category_with_near_duplicates_skipped():
    events = [ev("w1", 0, aesthetic=0.9, v=(1.0, 0.0)), ev("w2", 20, aesthetic=0.8, v=(1.0, 0.01)),   # w2 ~ w1
              ev("w3", 40, aesthetic=0.7, v=(0.0, 1.0)), ev("w4", 60, aesthetic=0.6, v=(0.5, 0.5, 0.7)),
              ev("l1", 80, label="landscape", v=(0.0, 0.0, 1.0)), ev("n1", 90, label="night", reasons=["underexposed"],
                                                                     v=(0.0, 0.0, 0.0, 1.0))]
    r = run(events)
    assert [r[p]["status"] for p in ("w1", "w2", "w3", "w4")] == ["pick", "duplicate", "pick", "spare"]
    assert r["w2"]["duplicate_of"] == "w1" and [r[p]["rank"] for p in ("w1", "w2", "w3", "w4")] == [1, 2, 3, 4]
    assert r["l1"]["status"] == "pick" and r["l1"]["category"] == "landscape"
    assert r["n1"]["status"] == "pick" and r["n1"]["reasons"] == [] and r["n1"]["waived"] == ["underexposed"]
    everything = run(events, per_category=0)
    assert everything["w4"]["status"] == "pick"                       # 0 = no limit


def test_without_aesthetics_sharpness_ranks_and_without_scene_one_category():
    events = [ev("x", 0, blur=0.3, v=(1.0,)), ev("y", 30, blur=0.1, v=(0.0, 1.0)), ev("z", 60, blur=0.2, v=(0.0, 0.0, 1.0))]
    for e in events:
        del e["products"]["scene"]
    r = run(events)
    assert [r[p]["rank"] for p in "xyz"] == [3, 1, 2] and {x["category"] for x in r.values()} == {cull.UNCATEGORISED}
    assert [r[p]["status"] for p in "yzx"] == ["pick", "pick", "spare"] and r["x"]["aesthetic"] is None


def scened(path, t, label, group, light="day", **kw):
    e = ev(path, t, v=(0.0,) * (t // 10) + (1.0,), **kw)
    e["products"]["scene"] = {"label": label, "group": group, "attributes": {"light": {"label": light, "scores": {}}}}
    return e


def test_select_buckets_by_group_or_label_and_waives_by_group_label_or_attribute():
    events = [scened("m", 0, "mountain", "landscape"), scened("c", 10, "coast", "landscape"),
              scened("astro", 20, "astro", "night", reasons=["underexposed"]),               # waived by group
              scened("bird", 30, "bird_portrait", "wildlife", light="night", reasons=["underexposed"]),   # attribute
              scened("mug", 40, "still_life", "other", reasons=["underexposed", "subject_cut"])]  # label
    waive = {"night": ["underexposed"], "light=night": ["underexposed"], "still_life": ["subject_cut"]}
    r = run(events, waive=waive)
    assert {p: x["category"] for p, x in r.items()} == {"m": "landscape", "c": "landscape", "astro": "night",
                                                         "bird": "wildlife", "mug": "other"}
    assert r["astro"]["waived"] == ["underexposed"] and r["bird"]["waived"] == ["underexposed"]
    assert r["mug"]["reasons"] == ["underexposed"] and r["mug"]["waived"] == ["subject_cut"]
    assert r["mug"]["status"] == "reject" and r["bird"]["status"] == "pick"
    by_label = run(events, waive=waive, by="label")
    assert by_label["m"]["category"] == "mountain" and by_label["c"]["category"] == "coast"
    assert run([ev("old", 0, label="night")], by="group")["old"]["category"] == "night"   # no group: the label
    with pytest.raises(ValueError, match="select.by"):
        cull.apply(events, {"select": {"by": "kind"}})


def test_select_reads_the_aesthetics_stage_output_and_it_only_reorders():
    # the aesthetics stage's product is {score, general, personal, head_id, note?}; select reads score
    events = [ev("low", 0, v=(1.0,)), ev("high", 30, v=(0.0, 1.0)), ev("nohead", 60, v=(0.0, 0.0, 1.0)),
              ev("ugly_keeper", 90, label="macro", v=(0.0, 0.0, 0.0, 1.0)),
              ev("pretty_reject", 120, label="macro", reasons=["overexposed"], v=(0.5, 0.5))]
    events[0]["products"]["aesthetics"] = {"score": 0.2, "general": 0.2, "personal": None, "head_id": "g:aaa"}
    events[1]["products"]["aesthetics"] = {"score": 0.8, "general": 0.7, "personal": 0.9, "head_id": "g:aaa+p:bbb~0.5"}
    events[2]["products"]["aesthetics"] = {"score": None, "general": None, "personal": None, "head_id": None,
                                           "note": "no builtin head"}
    events[3]["products"]["aesthetics"] = {"score": 0.0, "general": 0.0, "personal": None, "head_id": "g:aaa"}
    events[4]["products"]["aesthetics"] = {"score": 1.0, "general": 1.0, "personal": None, "head_id": "g:aaa"}
    r = run(events)
    assert [r[p]["rank"] for p in ("high", "low", "nohead")] == [1, 2, 3]            # reorders within the category
    assert r["high"]["aesthetic"] == 0.8 and r["nohead"]["aesthetic"] is None
    assert r["ugly_keeper"]["status"] == "pick" and r["ugly_keeper"]["reasons"] == []  # a low score never rejects
    assert r["pretty_reject"]["status"] == "reject"                                   # nor does a high one save a reject


def test_apply_copies_checks_and_passes_errors_through():
    events = [ev("a", 0.0), {"type": "error", "path": "e", "product": None, "message": "m"}]
    out = cull.apply(events, {"select": {}, "burst": {}})                # defaults; registry order
    assert "burst" not in events[0]["products"] and list(out[0]["products"])[-2:] == ["burst", "select"]
    assert out[1] is events[1]
    with pytest.raises(ValueError, match="unknown reducers"):
        cull.apply(events, {"nope": {}})
    with pytest.raises(ValueError, match="unknown options select"):
        cull.apply(events, {"select": {"top": 1}})
    with pytest.raises(ValueError, match="per_category"):
        cull.apply(events, {"select": {"per_category": -1}})
    assert cull.records(out)[0] | {"taken_at": None} == {
        "path": "a", "status": "pick", "keep": True, "category": "wildlife", "rank": 1, "reasons": [], "waived": [],
        "flags": [], "burst": None, "burst_size": 1, "burst_rank": 1, "duplicate_of": None, "sharpness": 0.8, "aesthetic": None,
        "taken_at": None}


def test_select_rejects_unknown_waive_reasons():
    cull.check_select(SELECT)
    with pytest.raises(ValueError, match=r"'underexpose'.*underexposed"):
        cull.check_select(SELECT | {"waive": {"night": ["underexpose"]}})


def test_waive_keys_are_scene_labels_groups_or_attributes():
    scene = {"labels": {"night": ["x"], "wildlife": []}, "groups": {"nature": ["wildlife"]},
             "attributes": {"light": {"night": ["x"], "day": ["y"]}}}
    cull.check_waive_keys({"night": [], "nature": [], "light=night": [], "uncategorised": []}, scene)
    for bad in ("nigth", "light=dusk", "mood=night"):
        with pytest.raises(ValueError, match=rf"select.waive: unknown category '{bad}'.*light=day"):
            cull.check_waive_keys({bad: []}, scene)
    cull.check_waive_keys({"nigth": []}, None)                        # no scene options: keys unchecked


def test_profiles_carry_reducer_options(tmp_path):
    f = tmp_path / "bioscan.toml"
    f.write_text('[profile.x]\nstages = ["identify"]\nreducers = ["burst", "select"]\n'
                 '[profile.x.options.select]\nper_category = 3\n[profile.x.options.burst]\nmax_gap_s = 0.5\n')
    cfg = profile.load([("project", f)], env={})
    res = profile.resolve(cfg, "x", reducer_options={"select": {"per_category": 7}}, source="flag")
    run_ = res.reducer_run()
    assert list(run_) == ["burst", "select"] and run_["burst"]["max_gap_s"] == 0.5 and run_["select"]["per_category"] == 7
    assert res.reducer_sources["select"]["per_category"] == "flag"
    assert res.reducer_sources["burst"]["max_gap_s"] == f"project {f}" and res.reducer_sources["burst"]["min_cosine"] == "default"
    assert "burst" not in res.options                                # never sent to the service
    with pytest.raises(ValueError, match="unknown options select"):
        profile.resolve(cfg, "x", reducer_options={"select": {"n": 1}})
    with pytest.raises(ValueError, match="min_cosine must be at most 1"):
        profile.resolve(cfg, "x", reducer_options={"burst": {"min_cosine": 2}})
    f.write_text('[profile.x.options.burst]\ngap = 1\n')
    with pytest.raises(ValueError, match=r"unknown options profile.x.options.burst: \['gap'\]"):
        profile.load([("project", f)], env={})


# ---- scene metrics: scene_acc (fine label) and group_acc (group, else label) -------------------------

def _scene_pred(label, group=None):
    s = {"label": label}
    if group:
        s["group"] = group
    return {"type": "result", "path": "p", "products": {"scene": s}}


def test_row_scene_and_row_scene_group():
    truth = {"path": "p", "scene": "coast", "scene_group": "landscape"}
    # today's stage: labels are the groups; group_acc falls back to the label
    assert cull.row_scene(truth, _scene_pred("landscape")) == {"all": False, "coast": False}
    assert cull.row_scene_group(truth, _scene_pred("landscape")) == {"all": True, "landscape": True}
    # a stage that reports fine labels and their group
    assert cull.row_scene(truth, _scene_pred("coast", "landscape")) == {"all": True, "coast": True}
    assert cull.row_scene_group(truth, _scene_pred("mountain", "landscape")) == {"all": True, "landscape": True}
    assert cull.row_scene_group(truth, _scene_pred("building", "architecture")) == {"all": False, "landscape": False}
    # the truth `scene` may be a group name: the prediction's group hits it
    assert cull.row_scene({"path": "p", "scene": "wildlife"}, _scene_pred("bird_portrait", "wildlife")) == {
        "all": True, "wildlife": True}
    assert cull.row_scene({"path": "p", "scene": "night"}, _scene_pred("bird_portrait", "wildlife")) == {
        "all": False, "night": False}
    # a column the CSV lacks measures nothing; so does a photo without a scene product
    assert cull.row_scene({"path": "p", "scene_group": "landscape"}, _scene_pred("coast")) == {}
    assert cull.row_scene_group({"path": "p", "scene": "coast"}, _scene_pred("coast")) == {}
    assert cull.row_scene_group(truth, {"type": "result", "path": "p", "products": {}}) == {}
    assert cull.row_scene_group(truth, {"type": "error", "path": "p", "error": "decode failed"}) == {}
    assert cull.row_scene_group(truth, None) == {}


def test_scene_manifest_declares_both_metrics():
    from bioscan.plugins.scene import MANIFEST
    assert [m.name for m in MANIFEST.metrics] == ["scene_acc", "group_acc"]
    assert all(m.kind == "rate" for m in MANIFEST.metrics)


def tilted(path, t, tilt, group="landscape", **kw):
    """A scene with the scene stage's horizon measure (null = none found, and always null outside landscape)."""
    e = scened(path, t, "coast" if group == "landscape" else "bird_portrait", group, **kw)
    e["products"]["scene"]["horizon"] = None if tilt is None else {"tilt_deg": tilt, "strength": 0.5}
    return e


def test_select_flags_a_tilted_horizon_and_the_flag_never_changes_the_selection():
    events = [tilted("level", 0, 2.9), tilted("tilted", 10, 3.1), tilted("down", 20, -3.1),
              tilted("none", 30, None), tilted("bird", 40, None, group="wildlife"),
              tilted("soft", 50, 8.0, reasons=["soft_subject"])]
    r = run(events, per_category=0)
    assert {p: x["flags"] for p, x in r.items()} == {"level": [], "tilted": ["horizon_tilt"], "down": ["horizon_tilt"],
                                                      "none": [], "bird": [], "soft": ["horizon_tilt"]}
    level = [ev | {"products": ev["products"] | {"scene": ev["products"]["scene"] | {"horizon": None}}} for ev in events]
    same = run(level, per_category=0)
    assert {p: {k: v for k, v in x.items() if k != "flags"} for p, x in r.items()} == \
        {p: {k: v for k, v in x.items() if k != "flags"} for p, x in same.items()}        # a flag is not a reason
    assert r["tilted"]["status"] == "pick" and r["tilted"]["reasons"] == [] and r["soft"]["reasons"] == ["soft_subject"]
    assert run(events, horizon_flag_deg=5.0)["tilted"]["flags"] == []
    assert run([ev("old", 0.0)])["old"]["flags"] == []                                  # preds before flags: none


def boxed(path, t, *boxes):
    """A wildlife photo with identify boxes (xyxy, normalised 0-1) and their detector scores."""
    e = ev(path, t)
    e["products"]["identify"] = {"boxes": [{"id": i, "xyxy": list(xyxy), "score": s, "kind": "bird"}
                                           for i, (xyxy, s) in enumerate(boxes)]}
    return e


def test_select_flags_tight_headroom_and_the_flag_never_changes_the_selection():
    events = [boxed("tight", 0, ((0.3, 0.01, 0.6, 0.5), 0.9)), boxed("room", 10, ((0.3, 0.03, 0.6, 0.5), 0.9)),
              boxed("portrait", 20, ((0.0, 0.0, 1.0, 0.9), 0.9)),               # fills the frame: no headroom to keep
              ev("nobox", 30),
              boxed("subject", 40, ((0.3, 0.01, 0.4, 0.2), 0.5), ((0.3, 0.3, 0.6, 0.6), 0.8))]  # best box by score
    r = run(events, per_category=0)
    assert {p: x["flags"] for p, x in r.items()} == {"tight": ["tight_headroom"], "room": [], "portrait": [],
                                                      "nobox": [], "subject": []}
    plain = [ev(e["path"], i * 10) for i, e in enumerate(events)]
    drop = lambda out: {p: {k: v for k, v in x.items() if k != "flags"} for p, x in out.items()}  # noqa: E731
    assert drop(r) == drop(run(plain, per_category=0))                                 # a flag is not a reason
    assert run(events, headroom_min=0.05)["room"]["flags"] == ["tight_headroom"]
    assert run(events, headroom_min=0.0)["tight"]["flags"] == []                       # 0 turns it off
    both = tilted("both", 0, 5.0)
    both["products"]["identify"] = boxed("both", 0, ((0.3, 0.0, 0.5, 0.4), 0.9))["products"]["identify"]
    assert cull.flags(both) == ["horizon_tilt", "tight_headroom"] and set(cull.FLAGS) >= set(cull.flags(both))


@pytest.mark.parametrize("bad", [-1, "0.02", True, float("nan"), 1.5])
def test_select_checks_headroom_min(bad):
    with pytest.raises(ValueError, match="select.headroom_min"):
        cull.check_select(SELECT | {"headroom_min": bad})


@pytest.mark.parametrize("bad", [-1, "3", True, float("nan")])
def test_select_checks_horizon_flag_deg(bad):
    with pytest.raises(ValueError, match="select.horizon_flag_deg"):
        cull.check_select(SELECT | {"horizon_flag_deg": bad})
