"""The identify payload has one owner, bioscan/contract.py: what identify emits conforms to it, the
CLI reads it through it, and /products describes the same fields."""
import json
import re

import pytest
from PIL import Image
from test_batch import FRAMES, Models

from bioscan import contract
from bioscan.cli import eval as ev
from bioscan.cli.render import Renderer
from bioscan.service import pipeline, rules

OPTION_SETS = [{"top_k": 3, "geo": True, "species": True}, {"top_k": 1, "geo": False, "species": True},
               {"top_k": 5, "geo": True, "species": False}]


@pytest.mark.parametrize("opts", OPTION_SETS)
def test_every_identify_output_conforms(opts):
    models = Models()
    del models.names["mammal"]          # mammal boxes now have no name list: species is null
    outs = pipeline.identify_many(models, FRAMES, opts)
    boxes = [b for o in outs for b in o["boxes"]]
    assert boxes and all(("species" in b) == opts["species"] for b in boxes)
    for o in outs:
        assert contract.identify_problems(o) == [], o
    if opts["species"]:   # both a species object and a null species were checked
        assert {b["species"] is None for b in boxes} == {True, False}


@pytest.mark.parametrize("size", [(4, 4), (64, 64)])     # under 8 px quality takes its early return
def test_quality_has_the_contract_fields(size):
    q = rules.quality(Image.new("RGB", size, (90, 120, 30)), (0.0, 0.0, float(size[0]), float(size[1])))
    assert list(q) == list(contract.Quality.__annotations__)


def _payload():
    owl = contract.candidate("Megascops kennicottii", "Western Screech-Owl",
                             ["Animalia", "Chordata", "Aves", "Strigiformes", "Strigidae", "Megascops",
                              "Megascops kennicottii"], 0.81, 0.62, 0.91)
    b = contract.box(0, [0.1, 0.1, 0.5, 0.5], 0.84, "bird", contract.quality(0.7, 0.05))
    b["species"] = contract.species("avilist-2025", "species", [owl])
    other = contract.box(1, [0.0, 0.0, 1.0, 1.0], 0.5, "other_animal", contract.quality(0.1, 0.0))
    other["species"] = None
    return contract.identify(contract.gate("bird", {"bird": 0.9, "none": 0.1}), [b, other])


def test_conformance_names_each_departure():
    good = _payload()
    assert contract.identify_problems(good) == []
    bad = json.loads(json.dumps(good))
    bad["boxes"][0]["species"]["top"][0]["p_prior"] = bad["boxes"][0]["species"]["top"][0].pop("p_geo")
    del bad["boxes"][1]["quality"]["exposure"]
    bad["gate"]["label"] = bad["gate"].pop("class")
    assert sorted(contract.identify_problems(bad)) == sorted([
        "missing boxes[0].species.top[0].p_geo", "unexpected boxes[0].species.top[0].p_prior",
        "missing boxes[1].quality.exposure", "missing gate.class", "unexpected gate.label"])
    assert contract.identify_problems({"gate": good["gate"], "boxes": [None]}) == ["boxes[0].: not an object"]
    assert contract.identify_problems([]) == ["identify: not an object"]


def test_cli_reads_what_the_contract_builds():
    """render and eval over a payload built only from the constructors: renaming a field in the
    contract without the readers following fails here."""
    res = contract.result("/v/a.jpg", "0" * 64, {}, {}, {"identify": _payload()}, {"decode": 1.0, "identify": 2.0})
    r = Renderer()
    line = r.feed(json.loads(json.dumps(res)))
    assert line == "a.jpg  bird    2 boxes  [1] Western Screech-Owl 0.91 种  [2] other_animal 0.50"
    assert r.species == {"Western Screech-Owl": 1}
    o = ev.outcome({"kind": "bird", "scientific": "Megascops kennicottii"}, json.loads(json.dumps(res)))
    assert o["gate"] and o["detected"] and o["top1"] and o["top5"] and o["species_level"]
    assert o["pred"] == "Megascops kennicottii" and (o["decode_ms"], o["identify_ms"]) == (1.0, 2.0)


def test_readers_accept_partial_payloads():
    assert contract.identify_of({"type": "result"}) is None
    assert contract.boxes_of(None) == [] and contract.gate_class_of(None) is None
    assert contract.gate_class_of({"gate": {}}) is None and contract.boxes_of({"boxes": None}) == []
    assert contract.species_of({"kind": "bird"}) is None
    assert contract.top_of(None) == [] and contract.level_of(None) is None


def test_products_describes_the_contract_fields():
    doc = contract.IDENTIFY_OUTPUT
    assert list(doc) == list(contract.Identify.__annotations__)
    assert list(doc["gate"]) == list(contract.Gate.__annotations__)
    (box,) = doc["boxes"]
    assert list(box) == list(contract.Box.__annotations__)
    assert list(box["quality"]) == list(contract.Quality.__annotations__)
    words = set(re.findall(r"\w+", box["species"]))
    assert set(contract.Species.__annotations__) <= words and set(contract.Candidate.__annotations__) <= words
    from bioscan.service import products
    assert products.PRODUCTS["identify"]["output"] is doc
