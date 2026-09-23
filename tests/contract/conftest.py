"""A fake engine with fixed boxes and scores, and tiny JPEGs on disk: the real decode and the
real /run pipeline run, only the models are stubbed."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from itertools import combinations

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from bioscan.service.app import create_app

FAIL_SIZE = (17, 17)   # identify throws on images of this size


class FakeEngine:
    device = "cpu"

    def __init__(self, fail_load=False, block: threading.Event | None = None):
        self.fail_load, self.block = fail_load, block
        self._loaded: list[str] = []
        self.calls: list[dict] = []
        self.details: list[tuple[int, int] | None] = []

    def loaded(self):
        return list(self._loaded)

    def ensure(self, want):
        if self.fail_load:
            raise RuntimeError("weights missing")
        self._loaded = ["siglip2", "owlv2", "bioclip"]

    def info(self):
        return {"version": "test", "models": {"gate": "fake-siglip2", "detect": "fake-owlv2", "species": "fake-bioclip"}}

    def frame(self, images):
        vecs = np.full((len(images), 768), 1 / np.sqrt(768), dtype=np.float32)
        gate = {"bird": 0.93, "mammal": 0.02, "other_animal": 0.01, "person": 0.0, "none": 0.04}
        return vecs, [dict(gate) for _ in images]

    def identify_many(self, frames, opts):
        out = []
        for f in frames:
            try:
                out.append(self.identify(f.image, f.gate, f.lat, f.lon, f.taken_at, opts, detail=f.detail))
            except Exception as exc:  # noqa: BLE001 - per-frame, like the real pipeline
                out.append(exc)
        return out

    def identify(self, image, gate, lat, lon, taken_at, opts, detail=None):
        self.calls.append({"size": image.size, "lat": lat, "lon": lon, "taken_at": taken_at})
        self.details.append(None if detail is None else detail.size)
        if self.block is not None:
            self.block.wait(10)
        if image.size == FAIL_SIZE:
            raise RuntimeError("detector exploded")
        box = {"id": 0, "xyxy": [0.31, 0.22, 0.58, 0.71], "score": 0.84, "kind": "bird",
               "quality": {"sharpness": 0.71, "exposure": 0.05}}
        if opts["species"]:
            box["species"] = {"list": "fake", "level": "species", "top": [
                {"scientific": "Megascops kennicottii", "common": "Western Screech-Owl",
                 "taxonomy": ["Animalia", "Chordata", "Aves", "Strigiformes", "Strigidae", "Megascops",
                              "Megascops kennicottii"], "p_visual": 0.81, "p_geo": None, "posterior": 0.81}][:opts["top_k"]]}
        return {"gate": {"class": max(gate, key=gate.get), "probs": gate}, "boxes": [box]}


def make_jpg(path, size=(64, 48)):
    Image.new("RGB", size, (90, 120, 30)).save(path)
    return str(path)


def events(resp):
    """Parsed NDJSON events; every one must carry its contract fields (bioscan/contract.py)."""
    from bioscan import contract

    assert resp.headers["content-type"].startswith("application/x-ndjson")
    evs = [json.loads(line) for line in resp.text.splitlines() if line.strip()]
    for ev in evs:
        assert not contract.missing_fields(ev), (ev.get("type"), contract.missing_fields(ev))
    return evs


def client_for(engine, chunk=32):
    return TestClient(create_app(engine, decode_pool=ThreadPoolExecutor(2), chunk=chunk))


ALL_WANTS = [list(c) for n in (1, 2, 3) for c in combinations(["identify", "embed", "jpg"], n)]


@pytest.fixture
def engine():
    return FakeEngine()


@pytest.fixture
def client(engine):
    with client_for(engine) as c:
        yield c
