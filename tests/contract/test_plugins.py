"""A stage plugin end to end through /run on the fake Engine: its model loads lazily through
Loaders.extra, it reads what identify provides (Item.facts), runs after it whatever its name, and a
plan that cannot run (missing provider, cycle, unknown option) is a 400 before anything loads."""
import dataclasses
from concurrent.futures import ThreadPoolExecutor

from conftest import Fakes, events, make_jpg
from fastapi.testclient import TestClient

from bioscan.plugin import Manifest, StageBase, each
from bioscan.plugins import BUILTIN
from bioscan.service.app import create_app


class Counter:
    """A plugin model adapter: counts boxes."""

    def count(self, boxes):
        return len(boxes)


class Count(StageBase):
    def check(self, o):
        if not isinstance(o["min_score"], (int, float)):
            raise ValueError("options.count.min_score must be a number")

    def run(self, engine, items, o):
        model = engine.model("counter")
        return each(items, lambda it: {"boxes": model.count([b for b in it.facts["boxes"]
                                                             if b["score"] >= o["min_score"]])})


STAGE = Count()
NOTHING = StageBase()
COUNT = Manifest("count", 1, "boxes per image", reads=("boxes",), provides=("tally",),
                 models=lambda o: ("counter",), thread="model",
                 options={"min_score": {"type": "number", "default": 0.5}}, output={"boxes": "int"},
                 impl=f"{__name__}:STAGE")
LOOP_A = Manifest("loop_a", 1, "", reads=("y",), provides=("x",), impl=f"{__name__}:NOTHING")
LOOP_B = Manifest("loop_b", 1, "", reads=("x",), provides=("y",), impl=f"{__name__}:NOTHING")
PLUGINS = (*BUILTIN, COUNT, LOOP_A, LOOP_B)


def client(fakes):
    engine = fakes.engine()
    engine = type(engine)("cpu", dataclasses.replace(engine.loaders, extra={"counter": lambda device: Counter()}))
    return engine, TestClient(create_app(engine, decode_pool=ThreadPoolExecutor(2), plugins=PLUGINS))


def test_plugin_stage_reads_identify_facts_and_loads_its_model(tmp_path):
    engine, c = client(Fakes())
    p = make_jpg(tmp_path / "a.jpg")
    with c:
        assert "count" in c.get("/products").json()
        ev = events(c.post("/run", json={"inputs": [{"path": p}], "want": ["count", "identify"]}))
        res = next(e for e in ev if e["type"] == "result")
        assert list(res["products"]) == ["identify", "count"] and res["products"]["count"] == {"boxes": 1}
        assert [e["product"] for e in ev if e["type"] == "progress"] == ["identify", "count"]
        high = events(c.post("/run", json={"inputs": [{"path": p}], "want": ["count", "identify"],
                                           "options": {"count": {"min_score": 0.9}}}))
        assert next(e for e in high if e["type"] == "result")["products"]["count"] == {"boxes": 0}
    assert engine.loaded() == ["siglip2", "owlv2", "bioclip", "counter"]


def test_a_plan_that_cannot_run_is_a_400_and_loads_nothing(tmp_path):
    engine, c = client(Fakes())
    p = make_jpg(tmp_path / "a.jpg")
    with c:
        for body, message in [
            ({"want": ["count"]}, "stage count reads 'boxes', which no stage of this run provides (add identify)"),
            ({"want": ["loop_a", "loop_b"]}, "stages ['loop_a', 'loop_b'] read each other's facts"),
            ({"want": ["count", "identify"], "options": {"count": {"max": 1}}}, "unknown options.count: ['max']"),
            ({"want": ["count", "identify"], "options": {"count": {"min_score": "x"}}},
             "options.count.min_score must be a number"),
        ]:
            r = c.post("/run", json={"inputs": [{"path": p}], **body})
            assert r.status_code == 400 and message in r.json()["error"], (body, r.text)
    assert engine.loaded() == []
