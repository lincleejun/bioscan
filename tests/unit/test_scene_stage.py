"""The scene stage's pieces: gate-shared zero-shot scores, horizon tilt on drawn horizons, the label
options check, and that its gate classes are the service's."""
import numpy as np
import pytest
from PIL import Image, ImageDraw

from bioscan.plugins.scene import DEFAULT_LABELS, GATE_CLASSES, MANIFEST, check
from bioscan.plugins.scene import stage as sc


def test_gate_classes_are_the_services():
    from bioscan.service.taxa import GATE_CLASSES as SERVICE

    assert GATE_CLASSES == SERVICE


def test_scores_share_the_gate_and_softmax_the_rest():
    labels = {"landscape": ["a"], "wildlife": [], "food": ["b"]}
    matrix = np.eye(3, 4, dtype=np.float32)[:2]                  # landscape = e0, food = e1
    vec = np.array([1.0, 0.0, 0.0, 0.0], np.float32)
    s = sc.scores(vec, {"bird": 0.5, "mammal": 0.2, "none": 0.3}, labels, matrix, 10.0, ["bird", "mammal"])
    assert list(s) == ["landscape", "wildlife", "food"] and s["wildlife"] == pytest.approx(0.7)
    assert s["landscape"] > s["food"] and sum(s.values()) == pytest.approx(1.0)
    no_gate = sc.scores(vec, None, {"landscape": ["a"], "food": ["b"]}, matrix, 10.0, ["bird"])
    assert sum(no_gate.values()) == pytest.approx(1.0) and no_gate["landscape"] > 0.99


def test_gate_share_needs_a_box_when_identify_ran():
    labels = {"landscape": ["a"], "wildlife": [], "food": ["b"]}
    matrix = np.eye(3, 4, dtype=np.float32)[:2]
    vec = np.array([1.0, 0.0, 0.0, 0.0], np.float32)
    gate = {"bird": 0.5, "mammal": 0.2, "none": 0.3}
    with_box = sc.scores(vec, gate, labels, matrix, 10.0, ["bird", "mammal"], boxes=[{"id": 1}])
    assert with_box["wildlife"] == pytest.approx(0.7)
    no_box = sc.scores(vec, gate, labels, matrix, 10.0, ["bird", "mammal"], boxes=[])
    assert no_box["wildlife"] == 0.0 and no_box["landscape"] > 0.99 and sum(no_box.values()) == pytest.approx(1.0)
    alone = sc.scores(vec, gate, labels, matrix, 10.0, ["bird", "mammal"], boxes=None)   # identify not in the run
    assert alone["wildlife"] == pytest.approx(0.7)


def test_no_groups_is_the_old_formula_to_the_bit():
    rng = np.random.default_rng(3)
    matrix = rng.normal(size=(3, 8)).astype(np.float32)
    vec = rng.normal(size=8).astype(np.float32)
    labels = {"a": ["x"], "wildlife": [], "b": ["y"], "c": ["z"]}
    gate = {"bird": 0.3, "mammal": 0.1}
    z = 100.0 * (vec @ matrix.T)
    p = np.exp(z - z.max())
    p = p / p.sum()
    old = {"a": 0.6 * float(p[0]), "wildlife": 0.4, "b": 0.6 * float(p[1]), "c": 0.6 * float(p[2])}
    new = sc.scores(vec, gate, labels, matrix, 100.0, ["bird", "mammal"])
    assert new["a"] == old["a"] and new["b"] == old["b"] and new["c"] == old["c"]
    assert new["wildlife"] == pytest.approx(0.4)


# ---- groups: the taxonomy (docs/research/2026-09-24-scene-taxonomy.md §3.1-3.4) ------------------

RULES = {"portrait_area": 0.08, "flock_boxes": 4}
TAX = {"bird_portrait": ["p0"], "bird_flight": ["p1"], "mountain": ["p2"], "coast": ["p3"], "food_drink": ["p4"]}
GROUPS = {"wildlife": ["bird_portrait", "bird_flight"], "landscape": ["mountain", "coast"], "food": ["food_drink"]}


def test_grouped_scores_split_the_share_in_group_and_the_rest_in_one_softmax():
    matrix = np.eye(5, 6, dtype=np.float32)
    vec = np.array([0.2, 0.1, 0.5, 0.3, 0.0, 0.0], np.float32)
    s = sc.scores(vec, {"bird": 0.6}, TAX, matrix, 10.0, ["bird"], gated=GROUPS["wildlife"])
    assert sum(s.values()) == pytest.approx(1.0) and s["bird_portrait"] + s["bird_flight"] == pytest.approx(0.6)
    assert s["bird_portrait"] / 0.6 == pytest.approx(1 / (1 + np.exp(-1.0)))          # softmax of 2 and 1 only
    rest = np.exp([5.0, 3.0, 0.0]) / np.exp([5.0, 3.0, 0.0]).sum()
    assert [s[k] for k in ("mountain", "coast", "food_drink")] == pytest.approx(list(0.4 * rest))
    label, group, gs = sc.grouped(s, GROUPS, "wildlife", None, RULES)
    assert (label, group) == ("bird_portrait", "wildlife") and sum(gs.values()) == pytest.approx(1.0)
    assert gs["landscape"] == pytest.approx(s["mountain"] + s["coast"])
    none = sc.scores(vec, {"bird": 0.6}, TAX, matrix, 10.0, ["bird"], boxes=[], gated=GROUPS["wildlife"])
    assert none["bird_portrait"] == none["bird_flight"] == 0.0
    assert sc.grouped(none, GROUPS, "wildlife", [], RULES)[:2] == ("mountain", "landscape")   # top of the top group


def box(kind="bird", area=0.25, score=0.9):
    return {"xyxy": [0.0, 0.0, area ** 0.5, area ** 0.5], "kind": kind, "score": score}


WILD = dict.fromkeys(["bird_portrait", "bird_flight", "bird_habitat", "mammal_portrait", "mammal_habitat",
                      "other_animal", "herd_flock", "domestic"], 0.05)


@pytest.mark.parametrize("inner, boxes, want", [
    ({**WILD, "bird_flight": 0.65}, [box()] * 4, "herd_flock"),                      # 1: flock_boxes boxes
    ({**WILD, "bird_flight": 0.65}, [box()], "bird_flight"),                         # 2: flight > 0.5
    ({**WILD, "domestic": 0.65}, [box("mammal")], "domestic"),
    ({**WILD, "bird_flight": 0.45}, [box()], "bird_portrait"),                       # 3: kind x area
    (WILD, [box(area=0.01)], "bird_habitat"),
    (WILD, [box("mammal", 0.01, 0.2), box("mammal", 0.5, 0.9)], "mammal_portrait"),   # the best box
    (WILD, [box("other_animal", 0.01)], "other_animal"),
    ({**WILD, "bird_habitat": 0.3}, None, "bird_habitat"),                          # 4: no box: the softmax top
])
def test_wildlife_rules_in_order(inner, boxes, want):
    assert sc.wildlife_label(inner, boxes, RULES) == want


def test_a_custom_gate_group_takes_its_softmax_top():
    assert sc.wildlife_label({"cats": 0.3, "dogs": 0.7}, [box("mammal")] * 9, RULES) == "dogs"


def test_attributes_are_plain_softmaxes():
    a = sc.scores(np.array([1.0, 0.0], np.float32), None, {"day": ["d"], "night": ["n"]}, np.eye(2, dtype=np.float32),
                  10.0, [])
    assert a["day"] > a["night"] and sum(a.values()) == pytest.approx(1.0)


def taxonomy(**o):
    return {**MANIFEST.defaults, "labels": TAX, "groups": GROUPS, **o}


@pytest.mark.parametrize("o, message", [
    (taxonomy(groups={**GROUPS, "food": ["food_drink", "coast"]}), "exactly one group"),        # label in two groups
    (taxonomy(groups={**GROUPS, "food": ["food_drink", "cake"]}), "exactly one group"),         # member not a label
    (taxonomy(groups={k: v for k, v in GROUPS.items() if k != "food"}), "exactly one group"),   # label in none
    (taxonomy(gate_group="birds"), "gate_group must name a group"),
    (taxonomy(labels={**TAX, "bird_flight": []}), "only the gate group"),                       # mixed gate group
    (taxonomy(labels={**TAX, "coast": []}), "only the gate group"),
    (taxonomy(attributes={"light": {"day": []}}), "attributes"),
    (taxonomy(attributes={"Light": {"day": ["d"]}}), "attributes"),
    (taxonomy(attributes={"light": {}}), "attributes"),
    (taxonomy(wildlife_rules={"portrait_area": 2, "flock_boxes": 4}), "wildlife_rules"),
    (taxonomy(wildlife_rules={"portrait_area": 0.1}), "wildlife_rules"),
])
def test_bad_taxonomy_options(o, message):
    with pytest.raises(ValueError, match=message):
        check(o)


def test_the_album_taxonomy_passes_and_a_prompt_less_gate_label_may_stand_alone():
    from bioscan import profile

    check(profile.resolve(profile.builtin(), "album").options["scene"])
    check(taxonomy(labels={**{k: v for k, v in TAX.items() if not k.startswith("bird")}, "wildlife": []},
                   groups={**GROUPS, "wildlife": ["wildlife"]}))


def horizon_photo(tilt_deg, w=400, h=300):
    """Bright sky over dark ground, the horizon rising `tilt_deg` to the right, some texture."""
    rng = np.random.default_rng(1)
    im = Image.new("L", (w, h), 200)
    d = ImageDraw.Draw(im)
    t = np.tan(np.radians(tilt_deg))
    y0 = h * 0.55
    d.polygon([(0, y0 + w / 2 * t), (w, y0 - w / 2 * t), (w, h), (0, h)], fill=60)
    a = np.asarray(im, np.float32) + rng.normal(0, 4, (h, w))
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).convert("RGB")


@pytest.mark.parametrize("tilt", [0.0, 3.0, -4.5])
def test_horizon_tilt(tilt):
    got = sc.horizon(horizon_photo(tilt))
    assert got is not None and got["tilt_deg"] == pytest.approx(tilt, abs=0.6) and got["strength"] > 0.3


def test_no_horizon_in_noise():
    rng = np.random.default_rng(2)
    assert sc.horizon(Image.fromarray(rng.integers(0, 255, (200, 300, 3), dtype=np.uint8))) is None


@pytest.mark.parametrize("labels, gate, message", [
    ({"a": ["x"]}, ["bird"], "at least two labels"),
    ({"A": ["x"], "b": ["y"]}, ["bird"], "lower-case"),
    ({"a": ["x"], "b": "y"}, ["bird"], "must be a list of text prompts"),
    ({"a": [], "b": []}, ["bird"], "at most one label may be empty"),
    ({"a": ["x"], "b": ["y"]}, ["cat"], "wildlife_gate"),
])
def test_bad_options(labels, gate, message):
    with pytest.raises(ValueError, match=message):
        check({**MANIFEST.defaults, "labels": labels, "wildlife_gate": gate})


def test_wildlife_box_must_be_a_bool_and_boxes_is_an_optional_read():
    with pytest.raises(ValueError, match="wildlife_box"):
        check({**MANIFEST.defaults, "wildlife_box": "yes"})
    assert "boxes?" in MANIFEST.reads and MANIFEST.defaults["wildlife_box"] is True


def test_defaults_pass_and_settings_fingerprint():
    check(MANIFEST.defaults)
    assert list(DEFAULT_LABELS) == ["landscape", "people", "wildlife", "macro", "architecture", "food", "night", "other"]
    s = sc.STAGE.settings()
    assert s["model"] == "siglip2-base-patch16-224" and s["HORIZON_MAX_DEG"] == sc.HORIZON_MAX_DEG
