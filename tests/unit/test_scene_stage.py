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
        check({"labels": labels, "wildlife_gate": gate})


def test_defaults_pass_and_settings_fingerprint():
    check(MANIFEST.defaults)
    assert list(DEFAULT_LABELS) == ["landscape", "people", "wildlife", "macro", "architecture", "food", "night", "other"]
    s = sc.STAGE.settings()
    assert s["model"] == "siglip2-base-patch16-224" and s["HORIZON_MAX_DEG"] == sc.HORIZON_MAX_DEG
