"""Model-free logic: triage rules, geo rerank, gate softmax, option/request validation, names."""
import base64

import numpy as np
import pytest
from PIL import Image, ImageFilter

from bioscan.service import names_legacy, products
from bioscan.service.adapters import geo, siglip2
from bioscan.service.adapters.owlv2 import Detection, clip_to_frame
from bioscan.service.app import parse_run

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


def test_geo_week_and_rerank():
    assert geo.week_of("2026-01-01T00:00:00Z") == 1
    assert geo.week_of("2026-12-31") == 48
    assert geo.week_of(None) is None and geo.week_of("garbage!!!") is None
    cands = [{"scientific": "A a", "common": "Aa", "p_visual": 0.6}, {"scientific": "B b", "common": "Bee-eater", "p_visual": 0.4}]
    same = geo.rerank(cands, None)
    assert [c["posterior"] for c in same] == [0.6, 0.4] and same[0]["p_geo"] is None
    ranked = geo.rerank(cands, {"b b": 0.9})
    assert ranked[0]["scientific"] == "B b" and ranked[1]["p_geo"] == geo.GEO_MISSING
    assert abs(sum(c["posterior"] for c in ranked) - 1) < 1e-9
    assert geo.rerank(cands, {"bee eater": 0.9})[0]["scientific"] == "B b"   # common-name match


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


def test_names_legacy_filter_and_prompt():
    raw = [
        [["Animalia", "Chordata", "Aves", "Strigiformes", "Strigidae", "Megascops", "kennicottii"], "Western Screech-Owl"],
        [["Animalia", "Chordata", "Aves", "Strigiformes", "Strigidae", "Megascops", "kennicottii"], "dup"],
        [["Animalia", "Chordata", "Aves", "Strigiformes", "Strigidae", "Megascops", ""], ""],        # genus only
        [["Animalia", "Chordata", "Aves", "Passeriformes", "Corvidae", "Cyanocitta", "Stelleri"], ""],  # bad epithet
        [["Animalia", "Chordata", "Mammalia", "Carnivora", "Ursidae", "Ursus", "arctos"], "Brown Bear"],
        [["Animalia", "Chordata", "Aves", "Passeriformes", "Corvidae", "Cyanocitta", "stelleri"], ""],
    ]
    rows = names_legacy.birds_from_tol(raw)
    assert [t[6] for t, _ in rows] == ["Cyanocitta stelleri", "Megascops kennicottii"]
    assert rows[1][1] == "Western Screech-Owl" and len(rows[1][0]) == 7
    assert names_legacy.prompt(*rows[0]) == \
        "a photo of Cyanocitta stelleri, Cyanocitta stelleri, a bird in the taxonomic family Corvidae"
