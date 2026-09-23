"""Model-free logic: triage rules, geo rerank, gate softmax, option/request validation, names."""
import base64

import numpy as np
import pytest
from PIL import Image, ImageFilter

from bioscan.service import products
from bioscan.service.adapters import geo, siglip2
from bioscan.service.adapters.owlv2 import Detection, clip_to_frame
from bioscan.service.app import allow_roots_from, parse_run, tunables

TAX = lambda g, f, s: ["Animalia", "Chordata", "Aves", "O", f, g, f"{g} {s}"]  # noqa: E731


def cand(p, g="G", f="F", s="x"):
    return {"posterior": p, "taxonomy": TAX(g, f, s)}


@pytest.mark.parametrize("cands,level", [
    ([cand(0.8), cand(0.1, s="y")], "species"),
    ([cand(0.55), cand(0.3, s="y")], "genus"),                             # margin < 0.3, same genus
    ([cand(0.4, "A"), cand(0.35, "B")], "family"),                         # genera split, family agrees
    ([cand(0.4, "A", "F1"), cand(0.35, "B", "F2")], "unconfirmed"),
    ([], "unconfirmed"),
])
def test_species_level(cands, level):
    assert products.species_level(cands) == level


def test_judge():
    g = lambda **kw: {**dict.fromkeys(siglip2.GATE_CLASSES, 0.0), **kw}  # noqa: E731
    assert products.judge("bird", g(none=0.9)) is None
    assert products.judge("bird", g(person=0.85)) is None
    assert products.judge("mammal", g(bird=0.6, mammal=0.4)) == "bird"
    assert products.judge("mammal", g(mammal=0.1, none=0.7)) is None
    assert products.judge("mammal", g(mammal=0.5)) == "mammal"
    assert products.judge("bird", g(bird=0.2, none=0.7)) == "bird"


def test_dedupe_keeps_stronger():
    a = Detection("a bird", 0.9, (0, 0, 10, 10))
    b = Detection("a mammal", 0.5, (1, 1, 10, 10))
    c = Detection("a bird", 0.4, (50, 50, 60, 60))
    assert products.dedupe([b, a, c]) == [a, c]
    assert clip_to_frame((-5, 2, 20, 30), 10, 10) == (0.0, 2, 10.0, 10.0)
    assert clip_to_frame((5, 5, 5, 9), 10, 10) is None


def test_crop_with_context_stays_in_frame():
    im = Image.new("RGB", (1000, 600))
    crop = products.crop_with_context(im, (0, 0, 20, 20))
    assert crop.size == (320, 320)
    crop = products.crop_with_context(im, (900, 500, 1000, 600))
    assert crop.size == (320, 320)


def test_quality_prefers_sharp():
    rng = np.random.default_rng(0)
    im = Image.fromarray((rng.random((200, 200)) > 0.5).astype(np.uint8) * 255).convert("RGB")
    im = im.resize((400, 400), Image.Resampling.NEAREST)
    sharp = products.quality(im, (50, 50, 350, 350))
    blurred = products.quality(im.filter(ImageFilter.GaussianBlur(4)), (50, 50, 350, 350))
    assert sharp["sharpness"] > blurred["sharpness"]
    assert -0.5 <= sharp["exposure"] <= 0.5


def test_geo_week_and_posterior():
    assert geo.week_of("2026-01-01T00:00:00Z") == 1
    assert geo.week_of("2026-12-31") == 48
    assert geo.week_of(None) is None and geo.week_of("garbage!!!") is None
    pv = np.array([0.6, 0.4, 0.0])
    assert geo.posterior(pv, None) is pv
    p_geo = geo.align(np.array([0.0, 0.9]), np.array([-1, 1, 0]))   # row 0 has no BirdNET label -> 0
    np.testing.assert_allclose(p_geo, [0.0, 0.9, 0.0])
    post = geo.posterior(pv, p_geo)
    assert post.argmax() == 1 and abs(post.sum() - 1) < 1e-12


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

    g = Geo()
    eng = SimpleNamespace(names={"bird": birds}, priors={"bird": geo.PriorBinding(g, geo.GeoPrior.index(g, birds.birdnet))},
                          bioclip=SimpleNamespace(encode_images=lambda ims: ims, probs=lambda f, m: np.array([visual])))

    def run(**opts):
        boxes = [{"kind": "bird"}]
        products._species(eng, Image.new("RGB", (100, 100)), boxes, [(10, 10, 60, 60)], 37.4, -122.1, None,
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
    o = products.resolve_options(None)
    assert o["identify"] == {"top_k": 5, "geo": True, "species": True} and o["embed"]["format"] == "list"
    assert products.resolve_options({"identify": {"top_k": 3}})["identify"]["top_k"] == 3
    for bad in ({"identify": {"top_k": 0}}, {"identify": {"nope": 1}}, {"embed": {"format": "npy"}},
                {"jpg": {"out_dir": "rel/dir"}}, {"video": {}}, {"identify": {"geo": "yes"}}):
        with pytest.raises(ValueError):
            products.resolve_options(bad)


def test_embed_formats():
    v = np.linspace(-1, 1, 768, dtype=np.float32)
    assert products.embed(v, "list")["dim"] == 768
    raw = base64.b64decode(products.embed(v, "f16_base64")["vector"])
    assert np.allclose(np.frombuffer(raw, "<f2"), v, atol=1e-3)


def test_parse_run():
    inputs, want, _ = parse_run({"inputs": [{"path": "/a.ARW", "lat": 1}], "want": ["jpg", "identify"]})
    assert want == ["identify", "jpg"] and inputs[0]["lon"] is None
    for bad in ({}, {"inputs": []}, {"inputs": [{"path": "a.ARW"}]}, {"inputs": [{"path": "/a"}], "want": []},
                {"inputs": [{"path": "/a"}], "want": ["video"]}, {"inputs": [{"path": "/a", "lat": "x"}]}, []):
        with pytest.raises(ValueError):
            parse_run(bad)


def test_tunables_flag_env_default():
    assert tunables(None, None, env={}) == (4, 32)
    env = {"BIOSCAN_DECODE_WORKERS": "2", "BIOSCAN_CHUNK": "8"}
    assert tunables(None, None, env=env) == (2, 8)
    assert tunables(6, None, env=env) == (6, 8)
    with pytest.raises(SystemExit):
        tunables(0, None, env={})


def test_mammal_species_uses_mdd_without_geo():
    from types import SimpleNamespace

    from bioscan.service.names import NameList

    tax = ["Animalia", "Chordata", "Mammalia", "Artiodactyla", "Cervidae", "Rangifer", "Rangifer tarandus"]
    tax2 = ["Animalia", "Chordata", "Mammalia", "Artiodactyla", "Cervidae", "Alces", "Alces alces"]
    mdd = NameList("mdd-2025", "mammal", ["Rangifer tarandus", "Alces alces"], ["Reindeer", "Moose"],
                   [tax, tax2], np.zeros((2, 4), np.float32), ["exact", "exact"])

    class Geo:
        def probs(self, *a):
            raise AssertionError("mammals get no geo prior")

    # a prior exists but is bound to birds only: mammals never ask it
    eng = SimpleNamespace(names={"mammal": mdd}, priors={"bird": geo.PriorBinding(Geo(), np.zeros(0, np.int64))},
                          bioclip=SimpleNamespace(encode_images=lambda ims: ims,
                                                  probs=lambda f, m: np.array([[0.9, 0.1]] * len(f))))
    boxes = [{"kind": "mammal"}]
    products._species(eng, Image.new("RGB", (100, 100)), boxes, [(10, 10, 60, 60)], 60.0, -150.0, None,
                      {"geo": True, "top_k": 5})
    sp = boxes[0]["species"]
    assert sp["list"] == "mdd-2025" and sp["level"] == "species"
    assert sp["top"][0]["scientific"] == "Rangifer tarandus" and sp["top"][0]["common"] == "Reindeer"
    assert sp["top"][0]["p_geo"] is None and sp["top"][0]["posterior"] == sp["top"][0]["p_visual"]
    # MDD 7-level taxonomy: [5] is the genus, [4] the family -> two cervid genera roll up to family
    assert products.species_level([{"posterior": 0.45, "taxonomy": tax}, {"posterior": 0.35, "taxonomy": tax2}]) \
        == "family"


def test_allow_roots_flag_env_default():
    import os

    assert allow_roots_from(None, env={}) == []
    assert allow_roots_from(None, env={"BIOSCAN_ALLOW_ROOTS": f"/a{os.pathsep}/b{os.pathsep}"}) == ["/a", "/b"]
    assert allow_roots_from(["/c"], env={"BIOSCAN_ALLOW_ROOTS": "/a"}) == ["/c"]
