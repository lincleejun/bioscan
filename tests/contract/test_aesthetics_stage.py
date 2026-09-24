"""The aesthetics stage on the fake engine: it scores the frame vector with the general head (and a
personal one), degrades to score null with a note when the general head is missing, never breaks a
run, reports its heads in engine.plugins and settings(), and is in album only."""
import json

import numpy as np
import pytest
from aesthetic_helpers import random_head
from conftest import Fakes, client_for, events, make_jpg

from bioscan import aesthetic as aes
from bioscan import plugin, profile
from bioscan.plugins import BY_NAME

FAKE_VEC = [1 / np.sqrt(768)] * 768          # what FakeSigLIP2.embed_images returns for every frame


@pytest.fixture
def heads(tmp_path, monkeypatch):
    """A general head installed as the builtin, and a personal head file."""
    general = aes.write_head(random_head(1, name="eva-head-v1"), tmp_path / "heads" / "eva-head-v1.json")
    personal = aes.write_head(random_head(2, name="me", lo=0, hi=5), tmp_path / "heads" / "me.json")
    monkeypatch.setattr(aes, "BUILTIN_HEAD", general)
    return aes.load_head(general), aes.load_head(personal)


@pytest.fixture
def no_builtin(tmp_path, monkeypatch):
    monkeypatch.setattr(aes, "BUILTIN_HEAD", tmp_path / "absent" / "eva-head-v1.json")


def result(client, body):
    ev = events(client.post("/run", json=body))
    assert ev[-1]["type"] == "done" and ev[-1]["failed"] == 0, ev
    return [e for e in ev if e["type"] == "result"]


def test_general_head_scores_the_frame_vector(client, tmp_path, heads):
    g, _ = heads
    res = result(client, {"inputs": [{"path": make_jpg(tmp_path / "a.jpg")}, {"path": make_jpg(tmp_path / "b.jpg")}],
                          "want": ["aesthetics"]})
    out = res[0]["products"]["aesthetics"]
    assert out == {"score": round(g.predict(FAKE_VEC), 4), "general": round(g.predict(FAKE_VEC), 4),
                   "personal": None, "head_id": g.id}
    assert res[0]["engine"]["plugins"] == {"aesthetics": f"v1@{g.id}"}
    assert set(res[0]["timing_ms"]) == {"decode", "aesthetics"}


def test_personal_head_blends(client, tmp_path, heads):
    g, p = heads
    res = result(client, {"inputs": [{"path": make_jpg(tmp_path / "a.jpg")}], "want": ["aesthetics"],
                          "options": {"aesthetics": {"head": p.path, "blend": 0.25}}})
    out = res[0]["products"]["aesthetics"]
    gs, ps = round(g.predict(FAKE_VEC), 4), round(p.predict(FAKE_VEC), 4)
    assert (out["general"], out["personal"]) == (gs, ps)
    assert out["score"] == pytest.approx(0.75 * gs + 0.25 * ps, abs=1e-4)
    assert out["head_id"] == f"{g.id}+{p.id}~0.25" and res[0]["engine"]["plugins"]["aesthetics"] == f"v1@{out['head_id']}"


def test_missing_builtin_head_degrades_to_null_with_a_note(client, tmp_path, no_builtin):
    res = result(client, {"inputs": [{"path": make_jpg(tmp_path / "a.jpg")}], "want": ["identify", "aesthetics"]})
    out = res[0]["products"]["aesthetics"]
    assert out["score"] is None and out["general"] is None and out["personal"] is None and out["head_id"] is None
    assert "general head not installed" in out["note"] and "data/aesthetic/README.md" in out["note"]
    assert "identify" in res[0]["products"]                                  # the run goes on
    assert res[0]["engine"]["plugins"] == {"aesthetics": "v1@none"}


def test_missing_builtin_with_a_personal_head_serves_the_personal_score(client, tmp_path, no_builtin):
    p = aes.write_head(random_head(2, name="me", lo=0, hi=5), tmp_path / "me.json")
    res = result(client, {"inputs": [{"path": make_jpg(tmp_path / "a.jpg")}], "want": ["aesthetics"],
                          "options": {"aesthetics": {"head": str(p)}}})
    out = res[0]["products"]["aesthetics"]
    assert out["score"] == out["personal"] is not None and out["general"] is None and "note" in out


def test_damaged_builtin_head_degrades_too(client, tmp_path, monkeypatch):
    bad = tmp_path / "eva-head-v1.json"
    doc = random_head(1)
    doc["weights"][0] += 1                                                   # sha no longer matches
    bad.write_text(json.dumps(doc))
    monkeypatch.setattr(aes, "BUILTIN_HEAD", bad)
    out = result(client, {"inputs": [{"path": make_jpg(tmp_path / "a.jpg")}], "want": ["aesthetics"]})[0]
    assert out["products"]["aesthetics"]["score"] is None
    assert "general head unusable" in out["products"]["aesthetics"]["note"] and "sha mismatch" in \
        out["products"]["aesthetics"]["note"]


def test_head_off_gives_no_product(client, tmp_path, heads):
    res = result(client, {"inputs": [{"path": make_jpg(tmp_path / "a.jpg")}], "want": ["identify", "aesthetics"],
                          "options": {"aesthetics": {"head": "off"}}})
    assert list(res[0]["products"]) == ["identify"] and "plugins" not in res[0]["engine"]


def test_bad_options_and_bad_personal_head_are_400(client, tmp_path, heads):
    p = make_jpg(tmp_path / "a.jpg")
    (tmp_path / "junk.json").write_text("{}")
    for opts, message in [({"head": "relative.json"}, "options.aesthetics.head must be"),
                          ({"head": 3}, "options.aesthetics.head must be"),
                          ({"blend": 1.5}, "options.aesthetics.blend must be a number 0-1"),
                          ({"blend": True}, "options.aesthetics.blend must be a number 0-1"),
                          ({"nope": 1}, "unknown options.aesthetics"),
                          ({"head": str(tmp_path / "none.json")}, "no such head file"),
                          ({"head": str(tmp_path / "junk.json")}, "not a bioscan-aesthetic-head")]:
        r = client.post("/run", json={"inputs": [{"path": p}], "want": ["aesthetics"], "options": {"aesthetics": opts}})
        assert r.status_code == 400 and message in r.json()["error"], (opts, r.text)


def test_personal_head_path_is_checked_against_allow_roots(tmp_path, heads):
    from concurrent.futures import ThreadPoolExecutor

    from fastapi.testclient import TestClient

    from bioscan.service.app import create_app

    (tmp_path / "photos").mkdir()
    p = make_jpg(tmp_path / "photos" / "a.jpg")
    with TestClient(create_app(Fakes().engine(), decode_pool=ThreadPoolExecutor(1),
                               allow_roots=[str(tmp_path / "photos")])) as c:
        r = c.post("/run", json={"inputs": [{"path": p}], "want": ["aesthetics"],
                                 "options": {"aesthetics": {"head": heads[1].path}}})
    assert r.status_code == 400 and "outside the allowed roots" in r.json()["error"]


def test_manifest_plan_and_profiles():
    m = BY_NAME["aesthetics"]
    assert (m.reads, m.provides, m.thread, m.models(m.defaults)) == (("vec",), ("aesthetic",), "cpu", ("siglip2",))
    assert m.defaults == {"head": "builtin", "blend": 0.5}
    p = plugin.plan(["aesthetics"], plugin.merge_options(None))
    assert p.frame and not p.detail and p.models == ("siglip2",)
    cfg = profile.builtin()
    assert "aesthetics" in profile.resolve(cfg, "album").want
    assert "aesthetics" not in profile.resolve(cfg, "full").want
    assert "aesthetics" not in profile.resolve(cfg, "wildlife").want


def test_stage_settings_and_facts(heads, tmp_path):
    g, p = heads
    stage = plugin.load(BY_NAME["aesthetics"])
    assert stage.settings() == {"head_format": 1, "embedding": aes.EMBEDDING, "builtin": g.id}
    items = [plugin.Item(dec=None, inp={"path": "/x"}, vec=np.asarray(v, dtype=np.float32))
             for v in (FAKE_VEC, [0.0] * 767 + [1.0])]
    out = stage.run(None, items, {"head": p.path, "blend": 1.0})
    assert [o["score"] for o in out] == [o["personal"] for o in out] == [it.facts["aesthetic"] for it in items]
    assert out[0]["score"] != out[1]["score"]
    assert out[1]["general"] == pytest.approx(g.predict([0.0] * 767 + [1.0]), abs=1e-4)


def test_album_profile_run_has_the_product_last(tmp_path, no_builtin):
    with client_for(Fakes().engine()) as c:
        res = result(c, {"inputs": [{"path": make_jpg(tmp_path / "a.jpg")}], "profile": "album"})
    assert list(res[0]["products"]) == ["identify", "embed", "aesthetics"]
    assert res[0]["products"]["aesthetics"]["score"] is None
