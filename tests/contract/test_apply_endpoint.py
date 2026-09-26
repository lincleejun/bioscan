"""POST /apply: the review page's copy and delete through the service; token-gated, inside the roots."""
from concurrent.futures import ThreadPoolExecutor

from conftest import Fakes
from fastapi.testclient import TestClient

from bioscan.service.app import create_app, parse_apply


def album(tmp_path):
    src = tmp_path / "card"
    src.mkdir()
    for n in ("a.ARW", "a.xmp", "b.ARW"):
        (src / n).write_bytes(b"x")
    return src


def test_apply_copies_keeps_and_deletes_drops_with_the_token(tmp_path):
    src = album(tmp_path)
    app = create_app(Fakes().engine(), decode_pool=ThreadPoolExecutor(2), allow_roots=[str(tmp_path)], token="t0k")
    with TestClient(app) as c:
        h = {"x-bioscan-token": "t0k"}
        assert c.post("/apply", json={"keep": [str(src / "a.ARW")], "keep_to": str(tmp_path / "k")}).status_code == 403
        r = c.post("/apply", json={"keep": [str(src / "a.ARW")], "keep_to": str(tmp_path / "k")}, headers=h)
        assert r.status_code == 200 and r.json()["copied"] == 1 and r.json()["sidecars"] == 1
        assert sorted(x.name for x in (tmp_path / "k").iterdir()) == ["a.ARW", "a.xmp"]
        r = c.post("/apply", json={"drop": [str(src / "b.ARW"), str(src / "zz.ARW")], "delete": True}, headers=h)
        assert r.status_code == 200 and r.json()["deleted"] == 1 and r.json()["missing"] == [str(src / "zz.ARW")]
        assert not (src / "b.ARW").exists() and (src / "a.ARW").exists()
        # outside the roots, or a bad body: refused, nothing touched
        r = c.post("/apply", json={"keep": [str(src / "a.ARW")], "keep_to": "/tmp/elsewhere"}, headers=h)
        assert r.status_code == 400 and "outside the allowed roots" in r.json()["error"]
        r = c.post("/apply", json={"drop": [str(src / "a.ARW")]}, headers=h)
        assert r.status_code == 400 and "delete" in r.json()["error"] and (src / "a.ARW").exists()
        assert c.options("/apply", headers={"origin": "null", "access-control-request-method": "POST",
                                              "access-control-request-headers": "x-bioscan-token"}).status_code == 200


def test_parse_apply_checks_the_shape():
    import pytest
    assert parse_apply({"keep": ["/a"], "keep_to": "/k", "drop": [], "delete": False}) == (["/a"], [], "/k", False)
    for bad in ([], {"keep": "/a", "keep_to": "/k"}, {"keep": ["a"], "keep_to": "/k"}, {"keep": ["/a"]},
                {"keep": ["/a"], "keep_to": "k"}, {"drop": ["/a"], "delete": "yes"}, {"drop": ["/a"]}):
        with pytest.raises(ValueError):
            parse_apply(bad)
