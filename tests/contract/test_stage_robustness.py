"""What the run does with a stage that misbehaves (throws, or answers for the wrong number of images),
which thread each built-in stage runs on, that only planned stages' code is imported, and `want`."""
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from conftest import Fakes, events, make_jpg
from fastapi.testclient import TestClient

from bioscan.plugin import Manifest, StageBase
from bioscan.plugins import BUILTIN, BY_NAME
from bioscan.service.app import create_app


class Boom(StageBase):
    def run(self, engine, items, o):
        raise RuntimeError("stage exploded")


class Short(StageBase):
    def run(self, engine, items, o):
        return [{"n": 1}] * (len(items) - 1)


class NotAList(StageBase):
    def run(self, engine, items, o):
        return None


BOOM, SHORT, NOT_A_LIST = Boom(), Short(), NotAList()
BAD = [Manifest(n, 1, n, reads=("image",), thread=t, impl=f"{__name__}:{a}")
       for n, t, a in (("boom", "model", "BOOM"), ("short", "cpu", "SHORT"), ("notalist", "cpu", "NOT_A_LIST"))]


@pytest.mark.parametrize("stage, messages", [
    ("boom", ["stage exploded"] * 3),
    ("short", ["stage returned 1 results for 2 images"] * 2 + ["stage returned 0 results for 1 images"]),
    ("notalist", ["stage returned NoneType results for 2 images"] * 2 + ["stage returned NoneType results for 1 images"]),
])
def test_a_misbehaving_stage_is_an_error_per_image_and_the_stream_ends(tmp_path, stage, messages):
    """Chunks of 2 + 1 images: every image of each chunk gets the stage's error (identify's output
    is not reported for them), the next chunk still runs, and `done` closes the stream."""
    paths = [make_jpg(tmp_path / f"{i}.jpg") for i in range(3)]
    app = create_app(Fakes().engine(), decode_pool=ThreadPoolExecutor(2), chunk=2, plugins=(*BUILTIN, *BAD))
    with TestClient(app) as c:
        ev = events(c.post("/run", json={"inputs": [{"path": p} for p in paths], "want": ["identify", stage]}))
    errors = [(e["path"], e["product"], e["message"]) for e in ev if e["type"] == "error"]
    assert errors == [(p, stage, f"RuntimeError: {m}") for p, m in zip(paths, messages)]
    assert not any(e["type"] == "result" for e in ev)
    assert ev[-1]["type"] == "done" and (ev[-1]["ok"], ev[-1]["failed"]) == (0, 3)


def test_builtin_stage_threads(client, tmp_path, monkeypatch):
    """identify, embed and scene on the single model thread, jpg, aesthetics and quality on the CPU pool:
    pinned in the manifests and observed at run time."""
    from bioscan import plugin

    assert {m.name: m.thread for m in BUILTIN} == {"identify": "model", "embed": "model", "jpg": "cpu",
                                                    "geotag": "cpu", "aesthetics": "cpu", "quality": "cpu",
                                                    "scene": "model"}
    seen = {}
    for m in BUILTIN:
        stage = plugin.load(m)

        def spy(engine, items, o, real=stage.run, name=m.name):
            seen[name] = threading.current_thread().name
            return real(engine, items, o)
        monkeypatch.setattr(stage, "run", spy)
    events(client.post("/run", json={"inputs": [{"path": make_jpg(tmp_path / "a.jpg")}],
                                     "want": ["identify", "embed", "jpg", "aesthetics", "quality", "scene"],
                                     "options": {"jpg": {"out_dir": str(tmp_path / "o")}}}))
    assert {n: t.split("_")[0] for n, t in seen.items()} == {"identify": "gpu", "embed": "gpu", "jpg": "cpu",
                                                                    "aesthetics": "cpu", "quality": "cpu", "scene": "gpu"}
    assert BY_NAME["jpg"].models({}) == ()


def test_only_planned_stages_are_imported(tmp_path):
    """A jpg-only run imports jpg's stage and no other: option checks come from the manifests."""
    here = Path(__file__).parent
    code = f"""
import sys; sys.path.insert(0, {str(here)!r})
from concurrent.futures import ThreadPoolExecutor
from fastapi.testclient import TestClient
from conftest import Fakes, make_jpg
from bioscan.service.app import create_app
p = make_jpg({str(tmp_path / 'a.jpg')!r})
with TestClient(create_app(Fakes().engine(), decode_pool=ThreadPoolExecutor(1))) as c:
    r = c.post("/run", json={{"inputs": [{{"path": p}}], "want": ["jpg"],
                              "options": {{"jpg": {{"out_dir": {str(tmp_path / 'o')!r}}}, "identify": {{"top_k": 3}}}}}})
    assert r.status_code == 200 and '"done"' in r.text, r.text
    assert c.post("/run", json={{"inputs": [{{"path": p}}], "options": {{"embed": {{"format": "x"}}}}}}).status_code == 400
print(sorted(m for m in sys.modules if m.endswith(".stage")))
"""
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    assert out.strip().splitlines()[-1] == "['bioscan.plugins.jpg.stage']"


def test_want_null_is_a_400_unless_a_profile_is_named(client, tmp_path):
    p = make_jpg(tmp_path / "a.jpg")
    r = client.post("/run", json={"inputs": [{"path": p}], "want": None})
    assert r.status_code == 400 and r.json()["error"] == "want must be a non-empty list of product names"
    ev = events(client.post("/run", json={"inputs": [{"path": p}], "want": None, "profile": "album"}))
    assert list(next(e for e in ev if e["type"] == "result")["products"]) == ["identify", "embed", "aesthetics",
                                                                            "quality", "scene"]

