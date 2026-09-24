"""/run's "profile": expanded by the service with its profiles (bioscan/profile.py), under the
request's own want and options; a request without one gets `full`, whatever the files say."""
from concurrent.futures import ThreadPoolExecutor

from conftest import Fakes, events, make_jpg
from fastapi.testclient import TestClient

from bioscan import profile
from bioscan.service.app import create_app


def test_album_profile_runs_without_bioclip(client, engine, tmp_path):
    p = make_jpg(tmp_path / "a.jpg")
    ev = events(client.post("/run", json={"inputs": [{"path": p}], "profile": "album"}))
    res = next(e for e in ev if e["type"] == "result")
    assert list(res["products"]) == ["identify", "embed", "quality", "scene"]
    assert "species" not in res["products"]["identify"]["boxes"][0]
    assert engine.loaded() == ["siglip2", "owlv2"]
    ev = events(client.post("/run", json={"inputs": [{"path": p}], "profile": "album", "want": ["identify"],
                                          "options": {"identify": {"species": True}}}))
    res = next(e for e in ev if e["type"] == "result")
    assert list(res["products"]) == ["identify"] and "species" in res["products"]["identify"]["boxes"][0]


def test_bad_profile_is_a_400(client):
    for body, message in [({"profile": "nope"}, "unknown profile 'nope' (known: album, full, wildlife)"),
                          ({"profile": 3}, "profile must be a string naming a profile"),
                          ({"profile": "album", "options": {"identify": {"nope": 1}}}, "unknown options.identify")]:
        r = client.post("/run", json={"inputs": [{"path": "/x.jpg"}], **body})
        assert r.status_code == 400 and message in r.json()["error"], r.text


def test_service_profiles_from_files_and_no_profile_is_full(tmp_path):
    toml = tmp_path / "bioscan.toml"
    toml.write_text('default_profile = "quick"\n[profile.quick]\nstages = ["identify"]\n'
                    '[profile.quick.options.identify]\nspecies = false\ntop_k = 2\n')
    profiles = profile.load([("project", toml)], env={"BIOSCAN_PROFILE": "album"})
    fakes = Fakes()
    p = make_jpg(tmp_path / "a.jpg")
    with TestClient(create_app(fakes.engine(), decode_pool=ThreadPoolExecutor(2), profiles=profiles)) as c:
        plain = next(e for e in events(c.post("/run", json={"inputs": [{"path": p}]})) if e["type"] == "result")
        quick = next(e for e in events(c.post("/run", json={"inputs": [{"path": p}], "profile": "quick"}))
                     if e["type"] == "result")
    assert list(plain["products"]) == ["identify"] and "species" in plain["products"]["identify"]["boxes"][0]
    assert "species" not in quick["products"]["identify"]["boxes"][0]
