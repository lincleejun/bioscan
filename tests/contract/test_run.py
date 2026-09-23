"""spec §4: /health, /products, /run events and error conventions, want combos, lock queue, cancel."""
import asyncio
import base64
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pytest
from conftest import ALL_WANTS, FAIL_SIZE, FakeEngine, client_for, events, make_jpg
from fastapi.testclient import TestClient
from PIL import Image

from bioscan.service import products
from bioscan.service.app import create_app, run_events


def test_health(client):
    h = client.get("/health").json()
    assert h == {"status": "ok", "device": "cpu", "models_loaded": [], "running": 0, "queued": 0}


def test_products(client):
    p = client.get("/products").json()
    assert set(p) == {"identify", "embed", "jpg"}
    for spec in p.values():
        assert {"description", "options", "output"} <= set(spec)
    assert p["identify"]["options"]["top_k"]["default"] == 5
    assert p["embed"]["options"]["format"]["enum"] == ["list", "f16_base64"]


@pytest.mark.parametrize("body", [
    {"inputs": [{"path": "/x.jpg"}], "want": ["video"]},
    {"inputs": [], "want": ["identify"]},
    {"inputs": [{"path": "relative/x.jpg"}]},
    {"inputs": [{"path": "/x.jpg"}], "want": []},
    {"inputs": [{"path": "/x.jpg"}], "options": {"identify": {"top_k": "5"}}},
])
def test_validation_400(client, body):
    r = client.post("/run", json=body)
    assert r.status_code == 400 and "error" in r.json()


def test_bad_json_400(client):
    assert client.post("/run", content=b"{nope", headers={"content-type": "application/json"}).status_code == 400


def test_model_load_failure_503():
    with client_for(FakeEngine(fail_load=True)) as c:
        r = c.post("/run", json={"inputs": [{"path": "/x.jpg"}]})
        assert r.status_code == 503 and "weights missing" in r.json()["error"]


def test_full_run_all_products(client, engine, tmp_path):
    paths = [make_jpg(tmp_path / f"{i}.jpg", (3000, 2000)) for i in range(3)]
    out = tmp_path / "out"
    r = client.post("/run", json={"inputs": [{"path": p} for p in paths], "want": ["identify", "embed", "jpg"],
                                  "options": {"jpg": {"out_dir": str(out)}}})
    assert r.status_code == 200
    ev = events(r)
    assert ev[-1]["type"] == "done" and ev[-1]["ok"] == 3 and ev[-1]["failed"] == 0 and ev[-1]["elapsed_ms"] >= 0
    results = [e for e in ev if e["type"] == "result"]
    assert sorted(e["path"] for e in results) == sorted(paths)
    for e in results:
        assert len(e["sha256"]) == 64
        assert e["image"] == {"width": 3000, "height": 2000, "orientation": 1}
        assert e["engine"]["version"] == "test" and set(e["engine"]["models"]) >= {"gate", "detect", "species"}
        assert set(e["timing_ms"]) == {"decode", "identify", "embed", "jpg"}
        ident = e["products"]["identify"]
        assert ident["gate"]["class"] == "bird" and set(ident["gate"]["probs"]) == {"bird", "mammal", "other_animal", "person", "none"}
        box = ident["boxes"][0]
        assert all(0 <= v <= 1 for v in box["xyxy"]) and box["species"]["level"] == "species"
        emb = e["products"]["embed"]
        assert emb["dim"] == 768 and len(emb["vector"]) == 768 and emb["model"] == "siglip2-base-patch16-224"
        jpg = e["products"]["jpg"]
        assert (jpg["width"], jpg["height"]) == (2048, 1365)
        with Image.open(jpg["path"]) as im:
            assert im.size == (2048, 1365)
    prog = [e for e in ev if e["type"] == "progress"]
    assert [(e["product"], e["done"], e["total"]) for e in prog] == [("identify", 3, 3), ("embed", 3, 3), ("jpg", 3, 3)]
    assert engine.details == [(3000, 2000)] * 3      # species crops from the full 3000 px frame, not the 2048 one


def test_detail_image_only_for_identify_and_can_be_off(tmp_path):
    p = make_jpg(tmp_path / "a.jpg", (3000, 2000))
    engine = FakeEngine()
    with client_for(engine) as c:
        events(c.post("/run", json={"inputs": [{"path": p}], "want": ["embed"]}))
        events(c.post("/run", json={"inputs": [{"path": p}]}))
    assert engine.details == [(3000, 2000)]
    engine = FakeEngine()
    with TestClient(create_app(engine, decode_pool=ThreadPoolExecutor(2), detail_edge=None)) as c:
        events(c.post("/run", json={"inputs": [{"path": p}]}))
    assert engine.details == [None] and engine.calls[0]["size"] == (2048, 1365)


def test_jpg_names_do_not_collide(client, tmp_path):
    (tmp_path / "card1").mkdir()
    (tmp_path / "card2").mkdir()
    a = make_jpg(tmp_path / "card1" / "DSC0001.jpg")
    b = str(tmp_path / "card2" / "DSC0001.jpg")
    Image.new("RGB", (64, 48), (1, 2, 3)).save(b)
    out = tmp_path / "out"
    body = {"inputs": [{"path": a}, {"path": b}], "want": ["jpg"], "options": {"jpg": {"out_dir": str(out)}}}
    res = {e["path"]: e for e in events(client.post("/run", json=body)) if e["type"] == "result"}
    written = {p: Path(e["products"]["jpg"]["path"]) for p, e in res.items()}
    assert len(set(written.values())) == 2 and sorted(out.iterdir()) == sorted(written.values())
    for p, e in res.items():
        assert written[p].name == f"DSC0001-{e['sha256'][:8]}.jpg"
    again = {e["path"]: e for e in events(client.post("/run", json=body)) if e["type"] == "result"}
    assert {p: Path(e["products"]["jpg"]["path"]) for p, e in again.items()} == written   # idempotent
    assert len(list(out.iterdir())) == 2


def test_embed_f16_and_species_off(client, tmp_path):
    p = make_jpg(tmp_path / "a.jpg")
    ev = events(client.post("/run", json={"inputs": [{"path": p}], "want": ["identify", "embed"], "options": {
        "embed": {"format": "f16_base64"}, "identify": {"species": False}}}))
    res = next(e for e in ev if e["type"] == "result")["products"]
    assert np.frombuffer(base64.b64decode(res["embed"]["vector"]), "<f2").shape == (768,)
    assert "species" not in res["identify"]["boxes"][0]


@pytest.mark.parametrize("want", ALL_WANTS)
def test_want_combinations(client, tmp_path, want):
    p = make_jpg(tmp_path / "a.jpg")
    ev = events(client.post("/run", json={"inputs": [{"path": p}], "want": want,
                                          "options": {"jpg": {"out_dir": str(tmp_path / "o")}}}))
    res = [e for e in ev if e["type"] == "result"]
    assert len(res) == 1 and set(res[0]["products"]) == set(want)
    assert set(res[0]["timing_ms"]) == {"decode", *want}
    assert sorted(e["product"] for e in ev if e["type"] == "progress") == sorted(want)
    assert ev[-1] == {**ev[-1], "type": "done", "ok": 1, "failed": 0}


def test_decode_error_does_not_stop_batch(client, tmp_path):
    good = make_jpg(tmp_path / "good.jpg")
    junk = tmp_path / "junk.jpg"
    junk.write_bytes(b"not an image")
    missing = str(tmp_path / "missing.jpg")
    ev = events(client.post("/run", json={"inputs": [{"path": good}, {"path": str(junk)}, {"path": missing}]}))
    errors = {e["path"]: e for e in ev if e["type"] == "error"}
    assert set(errors) == {str(junk), missing}
    assert all(e["product"] is None and e["message"] for e in errors.values())
    assert [e["path"] for e in ev if e["type"] == "result"] == [good]
    assert ev[-1]["type"] == "done" and (ev[-1]["ok"], ev[-1]["failed"]) == (1, 2)
    assert [(e["done"], e["total"]) for e in ev if e["type"] == "progress"] == [(3, 3)]


def test_product_error_is_per_image(client, tmp_path):
    good = make_jpg(tmp_path / "good.jpg")
    bad = make_jpg(tmp_path / "bad.jpg", FAIL_SIZE)
    ev = events(client.post("/run", json={"inputs": [{"path": bad}, {"path": good}], "want": ["identify", "embed"]}))
    err = [e for e in ev if e["type"] == "error"]
    assert len(err) == 1 and err[0]["path"] == bad and err[0]["product"] == "identify" and "exploded" in err[0]["message"]
    assert [e["path"] for e in ev if e["type"] == "result"] == [good]
    assert (ev[-1]["ok"], ev[-1]["failed"]) == (1, 1)


def test_chunked_progress(engine, tmp_path):
    paths = [make_jpg(tmp_path / f"{i}.jpg") for i in range(5)]
    with client_for(engine, chunk=2) as c:
        ev = events(c.post("/run", json={"inputs": [{"path": p} for p in paths], "want": ["identify", "embed"]}))
    assert [(e["product"], e["done"]) for e in ev if e["type"] == "progress"] == [
        ("identify", 2), ("embed", 2), ("identify", 4), ("embed", 4), ("identify", 5), ("embed", 5)]
    assert ev[-1]["ok"] == 5 and sum(e["type"] == "done" for e in ev) == 1


def test_request_coordinates_override_exif(client, engine, tmp_path):
    p = make_jpg(tmp_path / "a.jpg")
    client.post("/run", json={"inputs": [{"path": p, "lat": 37.4, "lon": -122.1, "taken_at": "2026-05-01T08:00:00Z"}]})
    client.post("/run", json={"inputs": [{"path": p}]})
    assert engine.calls[0] == {"size": (64, 48), "lat": 37.4, "lon": -122.1, "taken_at": "2026-05-01T08:00:00Z"}
    assert engine.calls[1]["lat"] is None and engine.calls[1]["taken_at"] is None


def test_requests_are_serialised_and_queued(tmp_path):
    gate = threading.Event()
    engine = FakeEngine(block=gate)
    p = make_jpg(tmp_path / "a.jpg")
    finished: list[tuple[str, float]] = []
    with client_for(engine) as c:
        def post(name):
            r = c.post("/run", json={"inputs": [{"path": p}]})
            finished.append((name, time.monotonic()))
            assert events(r)[-1]["ok"] == 1

        first = threading.Thread(target=post, args=("first",))
        first.start()
        _wait(lambda: c.get("/health").json()["running"] == 1)
        second = threading.Thread(target=post, args=("second",))
        second.start()
        _wait(lambda: c.get("/health").json()["queued"] == 1)
        assert len(engine.calls) == 1            # the second batch has not touched the models
        gate.set()
        first.join(10)
        second.join(10)
        assert [n for n, _ in sorted(finished, key=lambda x: x[1])] == ["first", "second"]
        h = c.get("/health").json()
        assert (h["running"], h["queued"]) == (0, 0) and len(engine.calls) == 2


def test_disconnect_stops_after_current_chunk(tmp_path):
    engine = FakeEngine()
    paths = [make_jpg(tmp_path / f"{i}.jpg") for i in range(6)]
    checks = []

    async def gone():
        checks.append(1)
        return True

    async def collect():
        with ThreadPoolExecutor(2) as pool, ThreadPoolExecutor(1) as gpu:
            return [e async for e in run_events(engine, [{"path": q, "lat": None, "lon": None, "taken_at": None} for q in paths],
                                                ["identify"], products.resolve_options(None),
                                                decode_pool=pool, gpu=gpu, chunk=2, is_disconnected=gone)]

    ev = asyncio.run(collect())
    assert len([e for e in ev if e["type"] == "result"]) == 2
    assert not any(e["type"] == "done" for e in ev) and len(engine.calls) == 2 and checks == [1]


def _wait(cond, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return
        time.sleep(0.02)
    raise AssertionError("condition not reached")
