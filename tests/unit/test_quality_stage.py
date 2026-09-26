"""The quality stage's rules on synthetic photos (tests/unit/cull_fixtures.py): the blur measure,
each reject reason, the measures it reports, and its settings fingerprint."""
import numpy as np
import pytest
from cull_fixtures import BOX, box, photo
from PIL import Image, ImageFilter

from bioscan import plugin
from bioscan.plugins.quality import MANIFEST, REASONS
from bioscan.plugins.quality import stage as q

BIRD = {"bird": 0.9, "mammal": 0.02, "other_animal": 0.01, "person": 0.0, "none": 0.07}
SCENERY = {"bird": 0.01, "mammal": 0.01, "other_animal": 0.01, "person": 0.02, "none": 0.95}


def px(im, b):
    return (int(b[0] * im.width), int(b[1] * im.height), round(b[2] * im.width), round(b[3] * im.height))


def reasons(im, boxes, gate=BIRD):
    return q.assess(im, boxes, gate)["reject_reasons"]


def test_blur_measure_orders_sharp_soft_and_flat():
    im, _ = photo(1)
    g = q.luma(im)
    soft = [q.blur(q.luma(im.filter(ImageFilter.GaussianBlur(r)))) for r in (1, 2, 4)]
    assert q.blur(g) < 0.3 and soft == sorted(soft) and soft[-1] > q.SOFT_BLUR
    assert q.blur(np.full((50, 50), 120, np.float32)) is None             # flat: no value
    assert q.blur(g[:5, :5]) is None                                       # too small


def test_a_sharp_photo_has_no_reason_and_reports_its_measures():
    im, b = photo(2)
    out = q.assess(im, [box(b, box_id=3), box((0.0, 0.0, 0.1, 0.1), score=0.2)], BIRD, "2026-05-01T08:00:00.37", "X Y")
    assert out["reject_reasons"] == []
    s = out["subject"]
    assert s["box"] == 3 and s["kind"] == "bird" and s["edges"] == [] and s["cut"] is False
    assert s["area"] == pytest.approx((b[2] - b[0]) * (b[3] - b[1]), abs=1e-6)
    assert s["placement"] == "centre" and s["blur"] < q.SOFT_BLUR and 0 <= s["clip_high"] <= 1
    assert set(out["frame"]) == {"sharpness", "exposure", "blur", "clip_high", "clip_low"}
    assert out["capture"] == {"taken_at": "2026-05-01T08:00:00.37", "camera": "X Y"}


def test_soft_subject_against_a_sharp_background_and_a_shaken_frame():
    im, b = photo(3)
    soft = im.copy()
    soft.paste(im.filter(ImageFilter.GaussianBlur(4)).crop(px(im, b)), px(im, b)[:2])
    assert reasons(soft, [box(b)]) == ["soft_subject"]
    shaken = im.filter(ImageFilter.BoxBlur(5))                              # a 2-D box: soft on both axes
    assert reasons(shaken, [box(b)]) == ["defocus"]
    assert reasons(shaken, [], SCENERY) == ["defocus"]                      # no subject: the frame alone
    bokeh, _ = photo(3, bokeh=True)                                         # nothing sharp: documented
    blurred = bokeh.copy()
    blurred.paste(bokeh.filter(ImageFilter.GaussianBlur(4)).crop(px(im, b)), px(im, b)[:2])
    assert reasons(blurred, [box(b)]) == ["defocus"]


def line_blur(im, length, axis):
    """A 1-D box kernel of `length` px along `axis` (0 vertical, 1 horizontal): motion or shake."""
    a = np.asarray(im, np.float32)
    return Image.fromarray(np.mean([np.roll(a, k - length // 2, axis) for k in range(length)], 0).astype(np.uint8))


@pytest.mark.parametrize("seed", [8, 9, 10])
def test_a_soft_frame_is_motion_when_one_axis_kept_its_detail_else_defocus(seed):
    im, b = photo(seed)
    assert reasons(im, [box(b)]) == [] and reasons(im, [], SCENERY) == []                   # sharp: no reason
    for boxes, gate in (([box(b)], BIRD), ([], SCENERY)):
        assert reasons(im.filter(ImageFilter.GaussianBlur(3)), boxes, gate) == ["defocus"]   # isotropic
        assert reasons(line_blur(im, 15, 1), boxes, gate) == ["motion"]                      # horizontal kernel
        assert reasons(line_blur(im, 15, 0), boxes, gate) == ["motion"]                      # vertical kernel
    lo, hi = sorted(1 - x for x in q.blur_axes(q.luma(line_blur(im, 15, 1))))
    assert hi >= q.MOTION_RATIO * lo and q.blur(q.luma(im)) == max(q.blur_axes(q.luma(im)))
    assert q.smear(np.full((50, 50), 120, np.float32)) == "defocus"                         # too flat to tell


def _ev(im, stops):
    s = np.asarray(im, np.float32) / 255
    lin = np.clip(np.where(s <= 0.04045, s / 12.92, ((s + 0.055) / 1.055) ** 2.4) * 2 ** stops, 0, 1)
    out = np.where(lin <= 0.0031308, lin * 12.92, 1.055 * lin ** (1 / 2.4) - 0.055)
    return Image.fromarray(np.round(out * 255).astype(np.uint8))


def test_exposure_reasons():
    im, b = photo(4)
    assert reasons(_ev(im, 2), [box(b)]) == ["overexposed"]
    assert reasons(_ev(im, -2), [box(b)]) == ["underexposed"]
    assert reasons(_ev(im, -2), [], SCENERY) == ["underexposed"]
    # a bright scene two stops under is not dark on average, but nothing in it reaches two stops under
    # white: the highlight rule catches it (the mean rule alone would not)
    dim = _ev(_ev(im, 1.5), -2)
    assert q.assess(dim, [box(b)], BIRD)["frame"]["exposure"] > q.UNDER_EXPOSURE
    assert reasons(dim, [box(b)]) == ["underexposed"]
    # a dark frame with one bright lamp keeps its highlights: only the mean rule applies, and it does
    lamp = _ev(im, -3)
    lamp.paste((255, 255, 255), (0, 0, 6, 6))
    assert reasons(lamp, [box(b)]) == ["underexposed"]
    # a flat mid-grey frame has no highlights and no tones either (fog, a test card): not underexposed
    assert reasons(Image.new("RGB", im.size, (110, 110, 110)), [], SCENERY) == []
    # a dark subject in a normal frame (a black bird) is not underexposed
    dark = im.copy()
    dark.paste(_ev(im, -3).crop(px(im, b)), px(im, b)[:2])
    assert reasons(dark, [box(b)]) == []


def test_cut_small_and_no_subject():
    im, _ = photo(5)
    assert reasons(im, [box((0.0, 0.3, 0.3, 0.8))]) == ["subject_cut"]
    out = q.assess(im, [box((0.0, 0.3, 0.3, 0.8))], BIRD)["subject"]
    assert out["edges"] == ["left"] and out["cut"]
    assert reasons(im, [box((0.0, 0.0, 0.9, 1.0))]) == []                   # frame-filling: a tight crop
    assert reasons(im, [box((0.5, 0.5, 0.55, 0.55))]) == ["subject_too_small"]
    assert reasons(im, [], BIRD) == ["no_subject"]
    assert reasons(im, [], SCENERY) == [] and q.assess(im, [], SCENERY)["subject"] is None


def test_placement():
    assert q.placement(0.5, 0.5)[0] == "centre"
    assert q.placement(1 / 3, 2 / 3)[0] == "thirds"
    assert q.placement(0.1, 0.1)[0] == "off"


def test_reasons_come_in_the_declared_order():
    im, _ = photo(6)
    got = reasons(_ev(im, 2).filter(ImageFilter.BoxBlur(5)), [box((0.0, 0.4, 0.06, 0.46))])
    assert got == [r for r in REASONS if r in got] and len(got) >= 3


def test_settings_are_the_thresholds_and_feed_the_fingerprint(monkeypatch):
    s = q.STAGE.settings()
    assert s["SOFT_BLUR"] == q.SOFT_BLUR and s["TOO_SMALL"] == q.TOO_SMALL and "THIRDS" in s
    assert s["MOTION_RATIO"] == q.MOTION_RATIO
    assert all(k.isupper() for k in s)
    fp = plugin.fingerprint(MANIFEST.version, s)
    monkeypatch.setattr(q, "SOFT_BLUR", 0.5)
    assert plugin.fingerprint(MANIFEST.version, q.STAGE.settings()) != fp and fp.startswith("v1@")


def test_a_large_frame_is_measured_on_the_working_copy():
    big, b = photo(7, w=2048, h=1536)
    same = big.resize((q.WORK_EDGE, q.WORK_EDGE * 3 // 4), Image.Resampling.BILINEAR)
    a, c = q.assess(big, [box(b)], BIRD), q.assess(same, [box(b)], BIRD)
    assert a["frame"] | {"sharpness": 0} == c["frame"] | {"sharpness": 0}
    assert a["subject"]["blur"] == c["subject"]["blur"] and max(q.working_copy(big).size) == q.WORK_EDGE
    assert q.working_copy(same) is same and b == BOX
