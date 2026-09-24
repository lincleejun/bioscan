"""The all-taxa list must not move a bird or mammal result. `golden/identify-0a66f72.json.gz` is
identify over 300 seeded random frames through the v1.5-base test_batch stand-ins (copied below as
`BaseModels`, since test_batch has moved on), recorded on a clean `git archive 0a66f72`:

    git archive 0a66f72 | tar -x -C /tmp/base && cd /tmp/base
    PYTHONPATH=/tmp/base:/tmp/base/tests/unit <repo>/.venv/bin/python <repo>/tests/unit/test_golden_alltaxa.py OUT.json.gz

(run from /tmp/base so that `bioscan` resolves to the base tree, not the working tree).

Conditions: the v1.5 accuracy switches off (`SWITCHES_OFF`; the base tree ignores the keys) and no
candidates. Then today's code gives the same bytes without an other_animal list, and with one only
other_animal boxes change: their species goes from null to a ranking of that list. With the
switches on, the kind check may move boxes (the all-taxa list is a kind-check list); that is
tests/unit/test_alltaxa.py's and W3's golden's business, not this file's.
"""
import gzip
import json
import random
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from bioscan.service import pipeline
from bioscan.service.adapters import geo
from bioscan.service.adapters.owlv2 import Detection
from bioscan.service.names import NameList

GOLDEN = Path(__file__).parent / "golden" / "identify-0a66f72.json.gz"
GATE = ("bird", "mammal", "other_animal", "person", "none")
SWITCHES_OFF = {"range_veto": False, "kind_check": False, "mammal_geo": False}
OPTS = {"top_k": 3, "geo": True, "species": True, **SWITCHES_OFF}
CHUNK = 32


def names(kind, cls, genus, n=4):
    sci = [f"{genus} s{i}" for i in range(n)]
    tax = [["Animalia", "Chordata", cls, "O", "F", genus, s] for s in sci]
    return NameList(f"{kind}-list", kind, sci, [""] * n, tax, np.zeros((n, 4), np.float32), ["exact"] * n,
                    birdnet=[f"{s}_x" for s in sci] if kind == "bird" else [])


class BaseModels:
    """tests/unit/test_batch.py's Models as of 0a66f72: answers depend only on the pixels."""

    def __init__(self):
        self.names = {"bird": names("bird", "Aves", "Buteo"), "mammal": names("mammal", "Mammalia", "Lynx")}

        class Prior:
            labels = [f"Buteo s{i}_x" for i in range(4)]

            def probs(self, lat, lon, week):
                return np.array([0.9, 0.05, 0.02, 0.03]) if lat > 0 else np.array([0.01, 0.01, 0.9, 0.08])

        self.priors = {"bird": geo.LocationPrior(Prior(), self.names["bird"].birdnet)}

        class Owl:
            def detect_batch(self, images, prompts, *, threshold):
                out = []
                for im in images:
                    r, _gg, _b = im.getpixel((5, 5))
                    conf = r / 255
                    dets = [Detection(prompts[0], conf, (10.0, 10.0, 60.0, 50.0)),
                            Detection(prompts[-1], conf * 0.9, (100.0, 80.0, 180.0, 150.0))]
                    out.append([d for d in dets if d.confidence >= threshold])
                return out

        class Sig:
            def embed_images(self, crops):
                return [c.getpixel((c.width // 2, c.height // 2)) for c in crops]

            def gate(self, vecs):
                return [{**dict.fromkeys(GATE, 0.0), **({"bird": 0.9, "none": 0.1} if v[1] > 100
                                                        else {"mammal": 0.7, "none": 0.3})} for v in vecs]

        class Bio:
            def encode_images(self, crops):
                return [c.getpixel((c.width // 2, c.height // 2)) for c in crops]

            def probs(self, feats, matrix):
                return np.array([np.roll([0.6, 0.2, 0.15, 0.05], f[2] % 4) for f in feats])

        self.owlv2, self.siglip2, self.bioclip = Owl(), Sig(), Bio()


def frames(n: int = 300, seed: int = 20260924) -> list:
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        rgb = tuple(rng.randrange(256) for _ in range(3))
        w = [rng.random() ** 3 for _ in GATE]
        gate = {k: round(v / sum(w), 4) for k, v in zip(GATE, w)}
        lat = rng.choice([None, 37.0, -30.0])
        im = Image.new("RGB", (400, 300), rgb)
        out.append(pipeline.Frame(im, gate, lat, -120.0 if lat is not None else None, "2026-05-01"))
    return out


def identify(models) -> list:
    fs = frames()
    return [o for i in range(0, len(fs), CHUNK) for o in pipeline.identify_many(models, fs[i:i + CHUNK], OPTS)]


def canonical(outs) -> list:
    return json.loads(json.dumps(outs))


def base() -> list:
    with gzip.open(GOLDEN, "rt") as f:
        return json.load(f)


def test_without_an_other_list_the_output_is_the_base_output():
    assert canonical(identify(BaseModels())) == base()


def test_the_all_taxa_list_only_fills_other_animal_boxes():
    models = BaseModels()
    models.names["other_animal"] = names("other_animal", "Reptilia", "Anolis")
    now = canonical(identify(models))
    kinds = {"bird": 0, "mammal": 0, "other_animal": 0}
    for b_out, n_out in zip(base(), now, strict=True):
        assert n_out["gate"] == b_out["gate"] and len(n_out["boxes"]) == len(b_out["boxes"])
        for b, n in zip(b_out["boxes"], n_out["boxes"]):
            kinds[b["kind"]] += 1
            if b["kind"] == "other_animal":
                assert b["species"] is None and n["species"]["list"] == "other_animal-list"
                assert {k: v for k, v in n.items() if k != "species"} == {k: v for k, v in b.items() if k != "species"}
            else:
                assert n == b
    assert min(kinds.values()) >= 20, kinds          # every kind is well represented in the 300 frames


if __name__ == "__main__":
    with gzip.open(sys.argv[1], "wt") as f:
        json.dump(canonical(identify(BaseModels())), f, sort_keys=False, separators=(",", ":"))
