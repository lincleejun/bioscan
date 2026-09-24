"""identify_many batches every model stage across a chunk and must give exactly what identifying
each frame alone gives, with the v1.5 accuracy switches on and off; a frame that makes a batched
stage throw costs only itself."""
import numpy as np
import pytest
from PIL import Image

from bioscan.service import pipeline
from bioscan.service.adapters import geo
from bioscan.service.adapters.owlv2 import Detection
from bioscan.service.names import NameList

GATE = ("bird", "mammal", "other_animal", "person", "none")
OPTS = {"top_k": 3, "geo": True, "species": True}
SWITCHES_OFF = {"range_veto": False, "kind_check": False, "mammal_geo": False}


def g(**kw):
    return {**dict.fromkeys(GATE, 0.0), **kw}


def names(kind, cls, genus, first, n=4):
    """Rows are unit vectors e_first .. e_first+n-1 of an 8-d space; mammals label rows 0-1 only
    (genus back-off for the rest)."""
    sci = [f"{genus} s{i}" for i in range(n)]
    tax = [["Animalia", "Chordata", cls, "O", "F", genus, s] for s in sci]
    labels = [f"{s}_x" for s in sci] if kind == "bird" else [f"{s}_x" for s in sci[:2]] + [""] * (n - 2)
    return NameList(f"{kind}-list", kind, sci, [""] * n, tax, np.eye(8, dtype=np.float32)[first:first + n],
                    ["exact"] * n, birdnet=labels, unlabelled="zero" if kind == "bird" else "genus")


class Models:
    """Answers depend only on the pixels, so batching cannot change them; calls are recorded."""

    def __init__(self, boom_colour=None):
        self.calls = {"detect": 0, "crop": 0, "bioclip": 0}
        self.boom = boom_colour
        self.names = {"bird": names("bird", "Aves", "Buteo", 0), "mammal": names("mammal", "Mammalia", "Lynx", 4)}

        class Prior:
            labels = [f"Buteo s{i}_x" for i in range(4)] + ["Lynx s0_x", "Lynx s1_x"]

            def probs(self, lat, lon, week):
                return (np.array([0.9, 0.05, 0.02, 0.03, 0.4, 0.0]) if lat > 0
                        else np.array([0.01, 0.01, 0.9, 0.08, 0.0, 0.3]))

        self.priors = geo.priors_for(self.names, Prior())
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

            @staticmethod
            def _vecs(feats):
                vecs = np.zeros((len(feats), 8))
                for i, (r, _g, b) in enumerate(feats):
                    vecs[i, b % 8] += 1.0
                    vecs[i, r % 8] += 0.6
                return vecs

            def probs(self, feats, matrix):
                """softmax over the given rows of a feature that leans to row b % 8 and a little to row r % 8"""
                z = np.exp(4.0 * self._vecs(feats) @ np.asarray(matrix, dtype=np.float64).T)
                return z / z.sum(axis=1, keepdims=True)

            def logits(self, feats, matrix):
                """what probs takes the softmax of"""
                return 4.0 * self._vecs(feats) @ np.asarray(matrix, dtype=np.float64).T

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


@pytest.mark.parametrize("switches", [{}, SWITCHES_OFF], ids=["switches-on", "switches-off"])
def test_batched_equals_one_by_one(monkeypatch, switches):
    monkeypatch.setattr(pipeline, "SPECIES_BATCH", 2)      # small, to cross BioCLIP batch boundaries
    opts = {**OPTS, **switches}
    batched = pipeline.identify_many(Models(), FRAMES, opts)
    single = [pipeline.identify(Models(), f.image, f.gate, f.lat, f.lon, f.taken_at, opts) for f in FRAMES]
    assert batched == single
    assert any(b["species"] and b["species"]["top"][0]["p_geo"] for b in batched[0]["boxes"])
    assert batched[5]["boxes"] == [] and batched[4]["gate"]["class"] == "none" and batched[4]["boxes"]
    moved = [b for o in batched for b in o["boxes"] if b.get("species") and b["species"]["list"] != f"{b['kind']}-list"]
    assert not moved                                       # a box is always named from its own kind's list
    kinds = [b["kind"] for o in batched for b in o["boxes"]]
    if switches:                                           # off: the crop check's kinds, as before v1.5
        assert kinds == [b["kind"] for o in pipeline.identify_many(Models(), FRAMES, {**OPTS, "kind_check": False})
                         for b in o["boxes"]]
    else:                                                  # on: frame 2's mammal boxes look like birds
        assert batched[2]["boxes"] and all(b["kind"] == "bird" for b in batched[2]["boxes"])


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
