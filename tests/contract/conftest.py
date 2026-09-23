"""Fake model adapters with fixed answers, and tiny JPEGs on disk: the real Engine, the real decode,
the real /run pipeline and the real identify pipeline run; only the models are stubbed, at the seam
where Engine builds them (engine.Loaders)."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from itertools import combinations

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from bioscan.service.adapters.owlv2 import Detection
from bioscan.service.app import create_app
from bioscan.service.engine import Engine, Loaders
from bioscan.service.names import NameList
from bioscan.service.rules import species_crops

FAIL_SIZE = (17, 17)   # the detector throws on images of this size
GATE = {"bird": 0.93, "mammal": 0.02, "other_animal": 0.01, "person": 0.0, "none": 0.04}
BOX = (0.31, 0.22, 0.58, 0.71)          # where the detector finds the bird, as fractions of the frame
OWLS = [("Megascops kennicottii", "Western Screech-Owl"), ("Megascops asio", "Eastern Screech-Owl")]


class FakeSigLIP2:
    """Gate and crop check: every frame and every crop is a bird."""

    def __init__(self, fakes):
        self.fakes = fakes

    def embed_images(self, images):
        return np.full((len(images), 768), 1 / np.sqrt(768), dtype=np.float32)

    def gate(self, vecs):
        return [dict(GATE) for _ in vecs]


class FakeOWLv2:
    """Detector: one bird box at BOX, confidence 0.84; raises on FAIL_SIZE images."""

    def __init__(self, fakes):
        self.fakes = fakes

    @staticmethod
    def box(size):
        w, h = size
        return (BOX[0] * w, BOX[1] * h, BOX[2] * w, BOX[3] * h)

    def detect(self, image, prompts, *, threshold):
        return self.detect_batch([image], prompts, threshold=threshold)[0]

    def detect_batch(self, images, prompts, *, threshold, batch=8):
        self.fakes.detected(images)
        if any(im.size == FAIL_SIZE for im in images):
            raise RuntimeError("detector exploded")
        return [[d for d in [Detection(prompts[0], 0.84, self.box(im.size))] if d.confidence >= threshold]
                for im in images]


class FakeBioCLIP:
    """Species: Western Screech-Owl 0.81 over Eastern 0.19, whatever the crop."""

    def __init__(self, fakes):
        self.fakes = fakes

    def encode_images(self, images):
        self.fakes.crops += [im.size for im in images]
        return np.ones((len(images), 4), np.float32)

    def probs(self, features, matrix):
        return np.array([[0.81, 0.19]] * len(features))


class FakeGeo:
    """Location prior (BirdNET-shaped): the Western owl is the local one."""
    labels = [f"{s}_{c}" for s, c in OWLS]

    def __init__(self, fakes):
        self.fakes = fakes

    def index(self, labels):
        pos = {label: i for i, label in enumerate(self.labels)}
        return np.array([pos.get(label, -1) for label in labels], dtype=np.int64)

    def probs(self, lat, lon, week):
        self.fakes.where.append((lat, lon, week))
        return np.array([0.6, 0.1])


def owl_list():
    tax = [["Animalia", "Chordata", "Aves", "Strigiformes", "Strigidae", "Megascops", s] for s, _ in OWLS]
    return NameList(list_id="fake-owls", kind="bird", scientific=[s for s, _ in OWLS],
                    common=[c for _, c in OWLS], taxonomy=tax, matrix=np.zeros((2, 4), np.float32),
                    tol_how=["exact"] * 2, sha="test", birdnet=list(FakeGeo.labels), birdnet_how=["exact"] * 2)


class Fakes:
    """The model adapters of one Engine, and what reached them: `frames` (the image size of every
    frame the detector was shown), `crops` (every species crop's size), `where` ((lat, lon, week)
    of every location-prior lookup)."""

    def __init__(self, fail_load=False, block: threading.Event | None = None):
        self.fail_load, self.block = fail_load, block
        self.frames: list[tuple[int, int]] = []
        self.crops: list[tuple[int, int]] = []
        self.where: list[tuple] = []

    def detected(self, images):
        self.frames += [im.size for im in images]
        if self.block is not None:
            self.block.wait(10)

    def _siglip2(self, device):
        if self.fail_load:
            raise RuntimeError("weights missing")
        return FakeSigLIP2(self)

    def engine(self) -> Engine:
        return Engine("cpu", Loaders(siglip2=self._siglip2, owlv2=lambda device: FakeOWLv2(self),
                                     species=lambda device: (FakeBioCLIP(self), {"bird": owl_list()}),
                                     geo=lambda: FakeGeo(self)))


def species_crop(size, detail=None):
    """The crop size BioCLIP gets for the fake box on a frame of `size`, cut from a `detail` copy."""
    return species_crops(Image.new("RGB", size), [FakeOWLv2.box(size)],
                         None if detail is None else Image.new("RGB", detail))[0].size


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
def fakes():
    return Fakes()


@pytest.fixture
def engine(fakes):
    return fakes.engine()


@pytest.fixture
def client(engine):
    with client_for(engine) as c:
        yield c
