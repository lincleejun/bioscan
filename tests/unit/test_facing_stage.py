"""The facing stage on synthetic subject boxes: the side from the head's (else the eye's) offset in the
box, part boxes back in frame coordinates, one OWLv2 call per kind, and the photos it never asks."""
from types import SimpleNamespace

import pytest
from PIL import Image

from bioscan.plugin import Item
from bioscan.plugins.facing import stage as fs
from bioscan.service.adapters.owlv2 import Detection

FRAME = (200, 100)


def det(prompt, cx, score=0.3, w=10):
    """A part detection centred at x = cx (pixels of a 100 px wide crop)."""
    return Detection(prompt, score, (cx - w / 2, 10, cx + w / 2, 20))


def one(dets, kind="bird", crop=(100, 0, 200, 100)):
    return fs.facing({"id": 3, "kind": kind}, crop, FRAME, dets)


@pytest.mark.parametrize("cx, side", [(90, "right"), (65, "right"), (64, None), (50, None), (36, None),
                                      (35, "left"), (5, "left")])
def test_side_from_the_head_offset(cx, side):
    got = one([det("bird head", cx)])
    assert got["side"] == side and got["offset"] == pytest.approx(cx / 100 - 0.5)


def test_head_wins_over_eye_and_eye_stands_in_for_a_missing_head():
    both = one([det("bird head", 20, 0.1), det("bird eye", 90, 0.4)])
    assert both["side"] == "left" and both["eye"]["score"] == 0.4
    eye_only = one([det("bird eye", 90)])
    assert eye_only["side"] == "right" and eye_only["head"] is None
    best = one([det("bird head", 90, 0.1), det("bird head", 10, 0.2)])           # the surest head counts
    assert best["side"] == "left" and best["head"]["score"] == 0.2
    assert one([]) == {"box": 3, "side": None, "offset": None, "head": None, "eye": None}
    assert one([det("mammal head", 90)])["side"] is None                        # another kind's words: not asked


def test_part_boxes_are_in_frame_coordinates():
    got = one([det("bird head", 80)], crop=(100, 0, 200, 100))
    assert got["head"] == {"xyxy": [0.875, 0.1, 0.925, 0.2], "score": 0.3}
    assert one([det("animal head", 80)], kind="other_animal")["side"] == "right"
    assert one([det("animal head", 80)], kind="unknown_kind")["side"] == "right"   # any other kind: the animal words


class Owl:
    """detect_batch stand-in: the head at HEAD_X (share of the crop width) for every crop."""

    def __init__(self, head_x=0.9, fail=None):
        self.calls, self.head_x, self.fail = [], head_x, fail

    def detect_batch(self, images, prompts, *, threshold):
        self.calls.append(([im.size for im in images], prompts, threshold))
        if self.fail is not None and any(im.size == self.fail for im in images):
            raise RuntimeError("detector exploded")
        return [[det(prompts[0], self.head_x * im.width, w=2)] for im in images]


def item(*boxes):
    """An Item whose identify boxes are (xyxy, score, kind)."""
    dec = SimpleNamespace(image=Image.new("RGB", FRAME))
    return Item(dec=dec, inp={}, facts={"boxes": [{"id": i, "xyxy": list(b), "score": s, "kind": k}
                                                  for i, (b, s, k) in enumerate(boxes)]})


def test_run_asks_once_per_kind_for_the_subject_box_only():
    owl = Owl()
    items = [item(((0.1, 0.1, 0.3, 0.5), 0.5, "bird"), ((0.5, 0.2, 0.9, 0.8), 0.9, "bird")),
             item(((0.0, 0.0, 0.5, 0.5), 0.8, "mammal")),
             item(((0.2, 0.2, 0.6, 0.6), 0.8, "bird")),
             item(),                                                              # no box: nothing to ask
             item(((0.5, 0.5, 0.52, 0.52), 0.8, "bird"))]                         # 4 x 2 px: too small to ask
    out = fs.STAGE.run(SimpleNamespace(owlv2=owl), items, {})
    assert [(c[1], len(c[0])) for c in owl.calls] == [(["bird head", "bird eye"], 2), (["mammal head", "mammal eye"], 1)]
    assert owl.calls[0][0][0] == (80, 60) and all(c[2] == fs.PART_FLOOR for c in owl.calls)   # the best box's crop
    assert [o["box"] for o in out] == [1, 0, 0, None, 0] and [o["side"] for o in out] == ["right"] * 3 + [None, None]
    assert out[3] == fs.NONE and out[4] == {**fs.NONE, "box": 0}


def test_a_failing_batch_costs_only_its_image():
    items = [item(((0.0, 0.0, 0.5, 0.5), 0.8, "bird")), item(((0.0, 0.0, 0.25, 0.25), 0.8, "bird"))]
    out = fs.STAGE.run(SimpleNamespace(owlv2=Owl(fail=(50, 25))), items, {})
    assert out[0]["side"] == "right" and isinstance(out[1], RuntimeError)
    with pytest.raises(RuntimeError):
        fs.STAGE.run(SimpleNamespace(owlv2=Owl(fail=(50, 25))), items[1:], {})


def test_settings_carry_the_prompts_and_thresholds():
    s = fs.STAGE.settings()
    assert s["prompts"] == fs.PROMPTS and s["PART_FLOOR"] == fs.PART_FLOOR and s["FACING_OFFSET"] == fs.FACING_OFFSET
