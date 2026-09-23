"""Gate rescue and the mammal vocabulary: an animal the whole-frame gate outvoted still gets looked for."""
from types import SimpleNamespace

from PIL import Image

from bioscan.service import products
from bioscan.service.adapters.owlv2 import VOCAB, Detection

OPTS = {"top_k": 5, "geo": False, "species": False}


class Engine:
    def __init__(self, dets, crop_gate):
        self.detect_calls: list[tuple[tuple[str, ...], float]] = []

        def detect(image, prompts, threshold):
            self.detect_calls.append((tuple(prompts), threshold))
            return [d for d in dets if d.confidence >= threshold]

        self.owlv2 = SimpleNamespace(detect=detect)
        self.siglip2 = SimpleNamespace(embed_images=lambda ims: ims, gate=lambda ims: [dict(crop_gate) for _ in ims])


def gate(**kw):
    return {**dict.fromkeys(("bird", "mammal", "other_animal", "person", "none"), 0.0), **kw}


BEAR = Detection("a black bear", 0.45, (100, 100, 400, 300))
MAMMAL_CROP = gate(mammal=0.8, none=0.2)


def test_rescue_looks_with_strongest_animal_vocab_first_pass_only():
    eng = Engine([BEAR], MAMMAL_CROP)
    out = products.identify(eng, Image.new("RGB", (800, 600)), gate(none=0.6, mammal=0.3, bird=0.1), None, None, None, OPTS)
    assert out["gate"]["class"] == "none"                      # reported as the gate said
    assert [b["kind"] for b in out["boxes"]] == ["mammal"]
    assert eng.detect_calls == [(tuple(VOCAB["mammal"]), min(VOCAB["mammal"].values()))]


def test_rescue_has_no_low_floor_second_pass():
    eng = Engine([Detection("a mammal", 0.05, (0, 0, 50, 50))], MAMMAL_CROP)
    out = products.identify(eng, Image.new("RGB", (800, 600)), gate(none=0.7, mammal=0.3), None, None, None, OPTS)
    assert out["boxes"] == [] and len(eng.detect_calls) == 1


def test_no_rescue_below_threshold_and_crop_gate_still_vetoes():
    eng = Engine([BEAR], MAMMAL_CROP)
    out = products.identify(eng, Image.new("RGB", (800, 600)), gate(none=0.8, mammal=0.1, bird=0.1), None, None, None, OPTS)
    assert out["boxes"] == [] and eng.detect_calls == []
    eng = Engine([BEAR], gate(none=0.9, mammal=0.1))
    out = products.identify(eng, Image.new("RGB", (800, 600)), gate(person=0.5, mammal=0.4), None, None, None, OPTS)
    assert out["boxes"] == [] and len(eng.detect_calls) == 1


def test_animal_gate_path_unchanged():
    """Gate says mammal, first pass empty: the floor-0.1 second pass still runs as before."""
    eng = Engine([Detection("a deer", 0.15, (100, 100, 400, 300))], MAMMAL_CROP)   # under its 0.2 floor
    out = products.identify(eng, Image.new("RGB", (800, 600)), gate(mammal=0.9), None, None, None, OPTS)
    assert len(out["boxes"]) == 1 and [t for _, t in eng.detect_calls] == [0.1, products.SECOND_PASS_FLOOR]


def test_mammal_vocab_covers_golden_set_misses():
    words = set(VOCAB["mammal"])
    for w in ("a black bear", "a grizzly bear", "a mountain lion", "a bobcat", "an opossum", "a raccoon"):
        assert w in words and VOCAB["mammal"][w] == 0.2
    assert all(w.startswith(("a ", "an ")) for w in words)
