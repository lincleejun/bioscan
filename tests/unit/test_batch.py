"""identify_many batches every model stage across a chunk and must give exactly what identifying
each frame alone gives; a frame that makes a batched stage throw costs only itself."""
import numpy as np
from PIL import Image

from bioscan.service import pipeline
from bioscan.service.adapters import geo
from bioscan.service.adapters.owlv2 import Detection
from bioscan.service.names import NameList

GATE = ("bird", "mammal", "other_animal", "person", "none")
OPTS = {"top_k": 3, "geo": True, "species": True}


def g(**kw):
    return {**dict.fromkeys(GATE, 0.0), **kw}


def names(kind, cls, genus, n=4):
    sci = [f"{genus} s{i}" for i in range(n)]
    tax = [["Animalia", "Chordata", cls, "O", "F", genus, s] for s in sci]
    return NameList(f"{kind}-list", kind, sci, [""] * n, tax, np.zeros((n, 4), np.float32), ["exact"] * n,
                    birdnet=[f"{s}_x" for s in sci] if kind == "bird" else [])


class Models:
    """Answers depend only on the pixels, so batching cannot change them; calls are recorded."""

    def __init__(self, boom_colour=None):
        self.calls = {"detect": 0, "crop": 0, "bioclip": 0}
        self.boom = boom_colour
        self.names = {"bird": names("bird", "Aves", "Buteo"), "mammal": names("mammal", "Mammalia", "Lynx")}

        class Prior:
            labels = [f"Buteo s{i}_x" for i in range(4)]

            def probs(self, lat, lon, week):
                return np.array([0.9, 0.05, 0.02, 0.03]) if lat > 0 else np.array([0.01, 0.01, 0.9, 0.08])

        self.priors = {"bird": geo.LocationPrior(Prior(), self.names["bird"].birdnet)}
        models = self

        class Owl:
            def detect_batch(self, images, prompts, *, threshold):
                models.calls["detect"] += 1
                out = []
                for im in images:
                    r, gg, b = im.getpixel((5, 5))
                    if (r, gg, b) == models.boom:
                        raise RuntimeError("detector exploded")
                    conf = r / 255
                    dets = [Detection(prompts[0], conf, (10.0, 10.0, 60.0, 50.0)),
                            Detection(prompts[-1], conf * 0.9, (100.0, 80.0, 180.0, 150.0))]
                    out.append([d for d in dets if d.confidence >= threshold])
                return out

        class Sig:
            def embed_images(self, crops):
                models.calls["crop"] += 1
                return [c.getpixel((c.width // 2, c.height // 2)) for c in crops]

            def gate(self, vecs):
                return [g(bird=0.9, none=0.1) if v[1] > 100 else g(mammal=0.7, none=0.3) for v in vecs]

        class Bio:
            def encode_images(self, crops):
                models.calls["bioclip"] += 1
                return [c.getpixel((c.width // 2, c.height // 2)) for c in crops]

            def probs(self, feats, matrix):
                return np.array([np.roll([0.6, 0.2, 0.15, 0.05], f[2] % 4) for f in feats])

        self.owlv2, self.siglip2, self.bioclip = Owl(), Sig(), Bio()


def frame(rgb, gate, lat=None):
    im = Image.new("RGB", (400, 300), rgb)
    return pipeline.Frame(im, gate, lat, -120.0 if lat is not None else None, "2026-05-01")


FRAMES = [
    frame((200, 150, 1), g(bird=0.9), lat=37.0),          # bird, geo prior north
    frame((180, 150, 2), g(bird=0.8), lat=-30.0),         # bird, other prior
    frame((150, 20, 3), g(mammal=0.9)),                   # mammal box, no prior
    frame((40, 150, 0), g(mammal=0.8)),                   # weak detections -> second pass
    frame((220, 20, 1), g(none=0.6, mammal=0.3)),         # rescued: first pass only
    frame((220, 150, 2), g(none=0.9, bird=0.05)),         # not rescued: no detector call
    frame((90, 150, 3), g(other_animal=0.9)),             # other_animal: no name list
]


def test_batched_equals_one_by_one(monkeypatch):
    monkeypatch.setattr(pipeline, "SPECIES_BATCH", 2)      # small, to cross BioCLIP batch boundaries
    batched = pipeline.identify_many(Models(), FRAMES, OPTS)
    single = [pipeline.identify(Models(), f.image, f.gate, f.lat, f.lon, f.taken_at, OPTS) for f in FRAMES]
    assert batched == single
    assert any(b["species"] and b["species"]["top"][0]["p_geo"] for b in batched[0]["boxes"])
    assert batched[5]["boxes"] == [] and batched[4]["gate"]["class"] == "none" and batched[4]["boxes"]


def test_stages_are_batched(monkeypatch):
    monkeypatch.setattr(pipeline, "SPECIES_BATCH", 16)     # the equivalence test uses 2 to cross batch boundaries
    m = Models()
    pipeline.identify_many(m, FRAMES, OPTS)
    # first pass: one call per gate class present (bird, mammal, other_animal); second pass: one
    assert m.calls["detect"] <= 4
    assert m.calls["crop"] <= 2                  # all crops of a pass in one SigLIP2 call
    one = Models()
    for f in FRAMES:
        pipeline.identify(one, f.image, f.gate, f.lat, f.lon, f.taken_at, OPTS)
    assert one.calls["detect"] > m.calls["detect"] and one.calls["bioclip"] > m.calls["bioclip"] == 2   # 1 per list


def test_bad_frame_costs_only_itself():
    m = Models(boom_colour=(150, 20, 3))
    out = pipeline.identify_many(m, FRAMES, OPTS)
    assert isinstance(out[2], RuntimeError) and "exploded" in str(out[2])
    good = pipeline.identify_many(Models(), FRAMES, OPTS)
    assert [o for i, o in enumerate(out) if i != 2] == [o for i, o in enumerate(good) if i != 2]


def test_species_off_and_empty_chunk():
    out = pipeline.identify_many(Models(), FRAMES[:2], {**OPTS, "species": False})
    assert all("species" not in b for o in out for b in o["boxes"])
    assert pipeline.identify_many(Models(), [], OPTS) == []
