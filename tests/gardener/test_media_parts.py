"""Head / eye crop suggestions (contract revision 7C): OWLv2 asked inside a subject box on its original pixels,
boxes mapped back to upright-frame fractions after the crop is shrunk, only hits at or above the floor, and
the route's errors. OWLv2 is faked here; the hit rates on real photos are in revision 7C."""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest
from core_fixtures import *  # noqa: F403 - fixtures (env, fakes)
from core_fixtures import Env, create_task, owl_box, wait_task
from PIL import Image
from test_media_detail import PATCH, is_red, oriented_jpg

from bioscan.gardener import parts
from bioscan.service.adapters.owlv2 import Detection

API = "/api/v1"


class FakeOwl:
    """Finds the head in the top-left quarter of whatever crop it is given and an eye at `eye` score."""

    def __init__(self, eye: float = 0.5):
        self.eye, self.seen = eye, []

    def detect(self, image, prompts, *, threshold):
        w, h = image.size
        self.seen.append((image.copy(), list(prompts), threshold))
        return [d for d in (Detection(prompts[0], 0.6, (0, 0, w / 2, h / 2)),
                            Detection(prompts[1], self.eye, (w / 4, h / 4, w / 4 + 4, h / 4 + 4)))
                if d.confidence >= threshold]


def test_find_maps_a_shrunk_crop_back_to_the_frame():
    owl = FakeOwl()
    box = [0.2, 0.4, 0.6, 0.6]
    got = parts.find(owl, Image.new("RGB", (2000, 1000)), box, "bird")
    image, prompts, _ = owl.seen[0]
    assert image.size == (960, 480) and prompts == ["bird head", "bird eye"]          # shrunk to OWLv2's side
    head = next(p for p in got if p["name"] == "head")
    assert head["xyxy"] == pytest.approx([0.2, 0.4, 0.4, 0.5]) and head["score"] == 0.6
    eye = next(p for p in got if p["name"] == "eye")
    assert eye["xyxy"][:2] == pytest.approx([0.3, 0.45])
    parts.find(owl, Image.new("RGB", (40, 40)), box, None)
    assert owl.seen[-1][1] == ["animal head", "animal eye"]                            # user-drawn: no kind
    low = parts.find(FakeOwl(eye=parts.FLOOR["eye"] - 0.01), Image.new("RGB", (100, 100)), box, "mammal")
    assert [p["name"] for p in low] == ["head"]                                        # an eye under the floor: none
    assert parts.find(owl, Image.new("RGB", (4, 40)), box, "bird") == []               # too small to ask
    whole = SimpleNamespace(detect=lambda im, p, threshold: [Detection(p[0], 0.9, (0, 0, *im.size))])
    assert parts.find(whole, Image.new("RGB", (100, 100)), box, "bird") == []           # a "head" that is the animal


def test_parts_endpoint_reads_the_subjects_original_pixels(tmp_path, fakes):
    owl = FakeOwl()
    loads = []
    eng = SimpleNamespace(device="cpu", loaded=lambda: [], owlv2=owl, ensure=lambda m: loads.append(list(m)))
    env = Env(tmp_path, fakes, eng)
    src = tmp_path / "card"
    src.mkdir()
    oriented_jpg(src / "rot6.jpg", 6)
    fakes.boxes["rot6.jpg"] = [owl_box(xyxy=PATCH)]
    with env.client() as c:
        t = wait_task(c, create_task(c, [str(src)])["id"])
        aid = c.get(f"{API}/assets", params={"task": t["id"]}).json()["items"][0]["id"]
        sid = c.get(f"{API}/assets/{aid}").json()["subjects"][0]["id"]
        r = c.get(f"{API}/assets/{aid}/parts", params={"subject": sid})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["file"] == "image" and body["kind"] == "bird" and ["owlv2"] in loads
        crop = owl.seen[0][0]
        assert crop.size == (60, 80) and is_red(np.asarray(crop).reshape(-1, 3).mean(0))   # the red patch, upright
        head = next(p for p in body["parts"] if p["name"] == "head")
        x0, y0, x1, y1 = PATCH
        assert head["xyxy"] == pytest.approx([x0, y0, (x0 + x1) / 2, (y0 + y1) / 2], abs=1e-4)
        assert c.get(f"{API}/assets/{aid}/parts", params={"subject": "nope"}).status_code == 404
        assert c.get(f"{API}/assets/{aid}/parts").status_code == 400                   # subject is required
        eng.ensure = lambda m: (_ for _ in ()).throw(RuntimeError("no weights"))
        r = c.get(f"{API}/assets/{aid}/parts", params={"subject": sid})
        assert r.status_code == 503 and "no weights" in r.json()["error"]
        (src / "rot6.jpg").unlink()
        assert c.get(f"{API}/assets/{aid}/parts", params={"subject": sid}).status_code == 404



def test_run_queue_model_work_holds_the_device_lock():
    """BioScan's model thread takes the same lock the parts route does (two MPS callers abort the process)."""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from bioscan.service.run import RunQueue

    lock = threading.Lock()
    q = RunQueue(SimpleNamespace(device_lock=lock), decode_pool=ThreadPoolExecutor(1))
    assert q._on_device(lambda: lock.locked()) is True and not lock.locked()
    assert RunQueue(SimpleNamespace(), decode_pool=ThreadPoolExecutor(1))._on_device(lambda: 7) == 7
