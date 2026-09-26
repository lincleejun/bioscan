"""Model-free logic: triage rules, geo rerank, gate softmax, option/request validation, names."""
import base64

import numpy as np
import pytest
from PIL import Image, ImageFilter

from bioscan.plugins import BY_NAME
from bioscan.plugins.embed.stage import embed
from bioscan.service import pipeline, rules, stages
from bioscan.service.adapters import geo, siglip2
from bioscan.service.adapters.owlv2 import Detection, clip_to_frame
from bioscan.service.app import parse_run

TAX = lambda g, f, s: ["Animalia", "Chordata", "Aves", "O", f, g, f"{g} {s}"]  # noqa: E731


def cand(p, g="G", f="F", s="x"):
    return {"scientific": f"{g} {s}", "posterior": p, "taxonomy": TAX(g, f, s)}


@pytest.mark.parametrize("cands,level", [
    ([cand(0.8), cand(0.1, s="y")], "species"),
    ([cand(0.55), cand(0.3, s="y")], "genus"),                             # margin < 0.3, same genus
    ([cand(0.4, "A"), cand(0.35, "B")], "family"),                         # genera split, family agrees
    ([cand(0.4, "A", "F1"), cand(0.35, "B", "F2")], "unconfirmed"),
    ([], "unconfirmed"),
])
def test_species_level(cands, level):
    assert rules.species_level(cands)[0] == level


@pytest.mark.parametrize("family,level", [
    ("", "unconfirmed"),         # the list leaves family empty: no rollup under ""
    ("Colubridae", "family"),    # a shared named family still rolls up
])
def test_species_level_family_rollup_needs_a_name(family, level):
    cands = [cand(0.2, g, family) for g in "ABCDE"]  # five genera, 1.0 total
    assert rules.species_level(cands)[0] == level


def test_species_level_empty_family_keeps_genus_rollup():
    assert rules.species_level([cand(0.35, "A", "", "x"), cand(0.3, "A", "", "y")]) == ("genus", "A")


def test_species_level_names_the_rank_that_rolled_up():
    """#57: the first candidate is in family FA, but FB holds 0.60 of the top-5 mass: the name is FB."""
    cands = [cand(0.30, "A", "FA"), cand(0.25, "B", "FB"), cand(0.20, "C", "FB"), cand(0.15, "D", "FB")]
    assert rules.species_level(cands) == ("family", "FB")
    assert rules.species_level([cand(0.8), cand(0.1, s="y")]) == ("species", "G x")
    assert rules.species_level([]) == ("unconfirmed", None)


def test_judge():
    g = lambda **kw: {**dict.fromkeys(siglip2.GATE_CLASSES, 0.0), **kw}  # noqa: E731
    assert rules.judge("bird", g(none=0.9)) is None
    assert rules.judge("bird", g(person=0.85)) is None
    assert rules.judge("mammal", g(bird=0.6, mammal=0.4)) == "bird"
    assert rules.judge("mammal", g(mammal=0.1, none=0.7)) is None
    assert rules.judge("mammal", g(mammal=0.5)) == "mammal"
    assert rules.judge("bird", g(bird=0.2, none=0.7)) == "bird"


def test_dedupe_keeps_stronger():
    a = Detection("a bird", 0.9, (0, 0, 10, 10))
    b = Detection("a mammal", 0.5, (1, 1, 10, 10))
    c = Detection("a bird", 0.4, (50, 50, 60, 60))
    assert rules.dedupe([b, a, c]) == [a, c]
    assert clip_to_frame((-5, 2, 20, 30), 10, 10) == (0.0, 2, 10.0, 10.0)
    assert clip_to_frame((5, 5, 5, 9), 10, 10) is None


def test_crop_with_context_stays_in_frame():
    im = Image.new("RGB", (1000, 600))
    crop = rules.crop_with_context(im, (0, 0, 20, 20))
    assert crop.size == (320, 320)
    crop = rules.crop_with_context(im, (900, 500, 1000, 600))
    assert crop.size == (320, 320)


def test_quality_prefers_sharp():
    rng = np.random.default_rng(0)
    im = Image.fromarray((rng.random((200, 200)) > 0.5).astype(np.uint8) * 255).convert("RGB")
    im = im.resize((400, 400), Image.Resampling.NEAREST)
    sharp = rules.quality(im, (50, 50, 350, 350))
    blurred = rules.quality(im.filter(ImageFilter.GaussianBlur(4)), (50, 50, 350, 350))
    assert sharp["sharpness"] > blurred["sharpness"]
    assert -0.5 <= sharp["exposure"] <= 0.5


def test_geo_week_and_posterior():
    assert geo.week_of("2026-01-01T00:00:00Z") == 1
    assert geo.week_of("2026-12-31") == 48
    assert geo.week_of(None) is None and geo.week_of("garbage!!!") is None
    pv = np.array([0.6, 0.4, 0.0])

    class Source:
        labels = ["Buteo b_x", "Buteo a_x"]

        def probs(self, lat, lon, week):
            return np.array([0.0, 0.9])

    prior = geo.LocationPrior(Source(), ["", "Buteo a_x", "Buteo b_x"])   # row 0 has no BirdNET label -> 0
    assert prior.posterior(pv, None) is pv
    p_geo = prior.p_geo(37.4, -122.1, None)
    np.testing.assert_allclose(p_geo, [0.0, 0.9, 0.0])
    post = prior.posterior(pv, p_geo)
    assert post.argmax() == 1 and abs(post.sum() - 1) < 1e-12
    np.testing.assert_allclose(post, pv * (geo.GEO_FLOOR + p_geo) / (pv * (geo.GEO_FLOOR + p_geo)).sum())


def test_location_prior_p_geo():
    """Rows follow the name list, not the source's order; the week comes from taken_at; no place
    or a failing source -> None (never an error)."""
    asked = []

    class Source:
        labels = ["Tyto alba_x", "Junco hyemalis_x", "Anas platyrhynchos_x"]

        def probs(self, lat, lon, week):
            asked.append((lat, lon, week))
            if lat < 0:
                raise RuntimeError("model down")
            return np.array([0.3, 0.6, 0.9])

    prior = geo.LocationPrior(Source(), ["Junco hyemalis_x", "", "Tyto alba_x", "Not in source_x"])
    np.testing.assert_allclose(prior.p_geo(32.9, -118.5, "2026-05-01T08:00:00"), [0.6, 0.0, 0.3, 0.0])
    assert asked == [(32.9, -118.5, 17)]
    prior.p_geo(32.9, -118.5, "garbage!!!")
    assert asked[-1] == (32.9, -118.5, None)
    assert prior.p_geo(None, -118.5, "2026-05-01") is None and prior.p_geo(32.9, None, None) is None
    assert len(asked) == 2                                              # no place: the source is not asked
    assert prior.p_geo(-33.9, 151.2, "2026-05-01") is None              # the source failed
    assert prior.name == "birdnet-geo-3.0" and prior.floor == geo.GEO_FLOOR


def test_priors_for_binds_lists_with_birdnet_labels():
    from types import SimpleNamespace

    class Source:
        labels = ["Buteo a_x"]

        def probs(self, lat, lon, week):
            return np.array([0.5])

    lists = {"bird": SimpleNamespace(birdnet=["Buteo a_x", ""]), "mammal": SimpleNamespace(birdnet=[])}
    assert geo.priors_for(lists, None) == {}                            # birdnet cannot load: no prior
    src = Source()
    priors = geo.priors_for(lists, src)
    assert list(priors) == ["bird"] and priors["bird"].source is src
    np.testing.assert_allclose(priors["bird"].p_geo(1.0, 2.0, None), [0.5, 0.0])


def test_geo_prior_applies_to_whole_list_before_top_k():
    """The species the eye ranks 6th (outside top_k=5) is where it lives and must come out first."""
    from types import SimpleNamespace

    from bioscan.service.names import NameList

    sci = [f"Circus s{i}" for i in range(8)]
    tax = [["Animalia", "Chordata", "Aves", "Accipitriformes", "Accipitridae", "Circus", s] for s in sci]
    birds = NameList("avilist-2025", "bird", sci, [""] * 8, tax, np.zeros((8, 4), np.float32), ["exact"] * 8,
                     birdnet=[f"{s}_x" for s in sci[:7]] + [""], birdnet_how=["exact"] * 7 + ["none"])
    visual = np.array([0.30, 0.20, 0.15, 0.12, 0.10, 0.08, 0.03, 0.02])   # row 5 is visual rank 6
    birdnet_labels = [f"{s}_x" for s in reversed(sci[:7])]                 # BirdNET's own order
    probs = {lab: 1e-6 for lab in birdnet_labels} | {"Circus s5_x": 0.9}

    class Geo:
        labels = birdnet_labels

        def probs(self, lat, lon, week):
            return np.array([probs[lab] for lab in self.labels])

    eng = SimpleNamespace(names={"bird": birds}, priors={"bird": geo.LocationPrior(Geo(), birds.birdnet)},
                          bioclip=SimpleNamespace(encode_images=lambda ims: ims, probs=lambda f, m: np.array([visual])))

    def run(**opts):
        boxes = [{"kind": "bird"}]
        pipeline._species(eng, Image.new("RGB", (100, 100)), boxes, [(10, 10, 60, 60)], 37.4, -122.1, None,
                          {"geo": True, "top_k": 5} | opts)
        return boxes[0]["species"]

    sp = run()
    top = sp["top"][0]
    assert top["scientific"] == "Circus s5" and top["p_visual"] == 0.08 and top["p_geo"] == 0.9
    assert sp["level"] == "species" and len(sp["top"]) == 5
    assert abs(top["posterior"] - 0.08 * 0.92 / (0.08 * 0.92 + 0.92 * 0.02)) < 1e-5   # ~0.8
    assert all(c["p_geo"] == 1e-6 for c in sp["top"][1:])
    off = run(geo=False)                                                 # geo off: visual order, no prior
    assert [c["scientific"] for c in off["top"]] == sci[:5]
    assert off["top"][0]["p_geo"] is None and off["top"][0]["posterior"] == 0.3


def test_softmax_gate():
    m = np.eye(5, 8)
    probs = siglip2.softmax_gate(np.eye(5, 8)[[0, 3]], m, 10.0)
    assert [max(p, key=p.get) for p in probs] == ["bird", "person"]
    assert abs(sum(probs[0].values()) - 1) < 1e-9


def test_resolve_options():
    o = stages.resolve_options(None)
    # the three v1.5 accuracy switches are identify options, on by default; trials off; candidates empty = all taxa
    assert o["identify"] == {"top_k": 5, "geo": True, "species": True, "range_veto": True, "kind_check": True,
                             "mammal_geo": True, "kind_size_correct": False, "candidates": []} and o["embed"]["format"] == "list"
    assert stages.resolve_options({"identify": {"top_k": 3}})["identify"]["top_k"] == 3
    assert stages.resolve_options({"identify": {"kind_check": False}})["identify"]["kind_check"] is False
    assert set(stages.PRODUCTS["identify"]["options"]) == set(BY_NAME["identify"].defaults)
    got = stages.resolve_options({"identify": {"candidates": [" Bubo ", "Strigidae"]}})["identify"]["candidates"]
    assert got == ["Bubo", "Strigidae"] and BY_NAME["identify"].defaults["candidates"] == []
    for bad in ({"identify": {"top_k": 0}}, {"identify": {"nope": 1}}, {"embed": {"format": "npy"}},
                {"jpg": {"out_dir": "rel/dir"}}, {"video": {}}, {"identify": {"geo": "yes"}},
                {"identify": {"range_veto": 0}}, {"identify": {"mammal_geo": "false"}},
                {"identify": {"kind_size_correct": 1}},
                {"identify": {"candidates": "Bubo"}}, {"identify": {"candidates": ["Bubo", " "]}},
                {"identify": {"candidates": [3]}}, {"identify": {"candidates": ["x"] * 1001}}):
        with pytest.raises(ValueError):
            stages.resolve_options(bad)


def test_embed_formats():
    v = np.linspace(-1, 1, 768, dtype=np.float32)
    assert embed(v, "list")["dim"] == 768
    raw = base64.b64decode(embed(v, "f16_base64")["vector"])
    assert np.allclose(np.frombuffer(raw, "<f2"), v, atol=1e-3)


def test_parse_run():
    inputs, plan = parse_run({"inputs": [{"path": "/a.ARW", "lat": 1}], "want": ["jpg", "identify"]})
    assert plan.want == ("identify", "jpg") and inputs[0]["lon"] is None
    for bad in ({}, {"inputs": []}, {"inputs": [{"path": "a.ARW"}]}, {"inputs": [{"path": "/a"}], "want": []},
                {"inputs": [{"path": "/a"}], "want": ["video"]}, {"inputs": [{"path": "/a", "lat": "x"}]}, []):
        with pytest.raises(ValueError):
            parse_run(bad)


def test_mammal_species_uses_mdd_without_geo():
    from types import SimpleNamespace

    from bioscan.service.names import NameList

    tax = ["Animalia", "Chordata", "Mammalia", "Artiodactyla", "Cervidae", "Rangifer", "Rangifer tarandus"]
    tax2 = ["Animalia", "Chordata", "Mammalia", "Artiodactyla", "Cervidae", "Alces", "Alces alces"]
    mdd = NameList("mdd-2025", "mammal", ["Rangifer tarandus", "Alces alces"], ["Reindeer", "Moose"],
                   [tax, tax2], np.zeros((2, 4), np.float32), ["exact", "exact"])

    class Geo:
        labels: list[str] = []

        def probs(self, *a):
            raise AssertionError("mammals get no geo prior")

    # a prior exists but is bound to birds only: mammals never ask it
    eng = SimpleNamespace(names={"mammal": mdd}, priors={"bird": geo.LocationPrior(Geo(), [])},
                          bioclip=SimpleNamespace(encode_images=lambda ims: ims,
                                                  probs=lambda f, m: np.array([[0.9, 0.1]] * len(f))))
    boxes = [{"kind": "mammal"}]
    pipeline._species(eng, Image.new("RGB", (100, 100)), boxes, [(10, 10, 60, 60)], 60.0, -150.0, None,
                      {"geo": True, "top_k": 5})
    sp = boxes[0]["species"]
    assert sp["list"] == "mdd-2025" and sp["level"] == "species"
    assert sp["top"][0]["scientific"] == "Rangifer tarandus" and sp["top"][0]["common"] == "Reindeer"
    assert sp["top"][0]["p_geo"] is None and sp["top"][0]["posterior"] == sp["top"][0]["p_visual"]
    # MDD 7-level taxonomy: [5] is the genus, [4] the family -> two cervid genera roll up to family
    assert rules.species_level([{"posterior": 0.45, "taxonomy": tax}, {"posterior": 0.35, "taxonomy": tax2}])[0] \
        == "family"
