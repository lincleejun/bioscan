"""The all-taxa list must not move a bird or mammal result. `golden/identify-0a66f72.json.gz` is
identify over 300 seeded random frames through the test_batch stand-ins, recorded on a clean
`git archive 0a66f72` (the v1.5 base, before the all-taxa list):

    git archive 0a66f72 | tar -x -C /tmp/base && cd /tmp/base
    PYTHONPATH=/tmp/base:/tmp/base/tests/unit <repo>/.venv/bin/python <repo>/tests/unit/test_golden_alltaxa.py OUT.json.gz

(run from /tmp/base so that neither `bioscan` nor `test_batch` resolves to the working tree).

Today's code on the same frames gives the same bytes without an other_animal list, and with one
only other_animal boxes change: their species goes from null to a ranking of that list.
"""
import gzip
import json
import random
import sys
from pathlib import Path

from test_batch import GATE, Models, frame, names

from bioscan.service import pipeline

GOLDEN = Path(__file__).parent / "golden" / "identify-0a66f72.json.gz"
OPTS = {"top_k": 3, "geo": True, "species": True}
CHUNK = 32


def frames(n: int = 300, seed: int = 20260924) -> list:
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        rgb = tuple(rng.randrange(256) for _ in range(3))
        w = [rng.random() ** 3 for _ in GATE]
        gate = {k: round(v / sum(w), 4) for k, v in zip(GATE, w)}
        lat = rng.choice([None, 37.0, -30.0])
        out.append(frame(rgb, gate, lat=lat))
    return out


def identify(models) -> list:
    fs = frames()
    return [o for i in range(0, len(fs), CHUNK) for o in pipeline.identify_many(models, fs[i:i + CHUNK], OPTS)]


def canonical(outs) -> list:
    return json.loads(json.dumps(outs))


def test_without_an_other_list_the_output_is_the_base_output():
    with gzip.open(GOLDEN, "rt") as f:
        base = json.load(f)
    assert canonical(identify(Models())) == base


def test_the_all_taxa_list_only_fills_other_animal_boxes():
    with gzip.open(GOLDEN, "rt") as f:
        base = json.load(f)
    models = Models()
    models.names["other_animal"] = names("other_animal", "Reptilia", "Anolis")
    now = canonical(identify(models))
    kinds = {"bird": 0, "mammal": 0, "other_animal": 0}
    for b_out, n_out in zip(base, now, strict=True):
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
        json.dump(canonical(identify(Models())), f, sort_keys=False, separators=(",", ":"))
