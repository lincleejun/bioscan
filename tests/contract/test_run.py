"""spec §4: /health, /products, /run events and error conventions, want combos, lock queue, cancel."""
import asyncio
import base64
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pytest
from conftest import ALL_WANTS, FAIL_SIZE, Fakes, client_for, events, make_jpg, species_crop
from fastapi.testclient import TestClient
from PIL import Image

import bioscan
from bioscan import plugin
from bioscan.service import run as run_mod
from bioscan.service import stages
from bioscan.service.app import create_app


def test_health(client):
    h = client.get("/health").json()
    assert h == {"status": "ok", "device": "cpu", "models_loaded": [], "running": 0, "queued": 0}


def test_products(client):
    p = client.get("/products").json()
    assert set(p) == {"identify", "embed", "jpg", "geotag", "quality"}
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
    with client_for(Fakes(fail_load=True).engine()) as c:
        r = c.post("/run", json={"inputs": [{"path": "/x.jpg"}]})
        assert r.status_code == 503 and "weights missing" in r.json()["error"]


def test_candidates_are_checked_against_the_loaded_lists(client, tmp_path):
    """Unknown candidates are a 400 naming them, after the lists load; known ones narrow the ranking."""
    path = make_jpg(tmp_path / "a.jpg")
    r = client.post("/run", json={"inputs": [{"path": path}],
                                  "options": {"identify": {"candidates": ["Strigidae", "Nonexistus rex"]}}})
    assert r.status_code == 400 and "['Nonexistus rex']" in r.json()["error"]
    r = client.post("/run", json={"inputs": [{"path": path}],
                                  "options": {"identify": {"candidates": ["Megascops asio"]}}})
    box = next(e for e in events(r) if e["type"] == "result")["products"]["identify"]["boxes"][0]
    assert [c["scientific"] for c in box["species"]["top"]] == ["Megascops asio"]
    assert box["species"]["top"][0]["posterior"] == 1.0 and box["species"]["list"] == "fake-owls"
    assert client.get("/products").json()["identify"]["options"]["candidates"]["default"] == []


def test_full_run_all_products(client, fakes, tmp_path):
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
        assert e["engine"]["version"] == bioscan.__version__
        assert set(e["engine"]["models"]) >= {"gate", "detect", "species"}
        assert set(e["timing_ms"]) == {"decode", "identify", "embed", "jpg"}
        ident = e["products"]["identify"]
        assert ident["gate"]["class"] == "bird" and set(ident["gate"]["probs"]) == {"bird", "mammal", "other_animal", "person", "none"}
        box = ident["boxes"][0]
        assert box["xyxy"] == [0.31, 0.22, 0.58, 0.71] and box["species"]["level"] == "species"
        emb = e["products"]["embed"]
        assert emb["dim"] == 768 and len(emb["vector"]) == 768 and emb["model"] == "siglip2-base-patch16-224"
        jpg = e["products"]["jpg"]
        assert (jpg["width"], jpg["height"]) == (2048, 1365)
        with Image.open(jpg["path"]) as im:
            assert im.size == (2048, 1365)
    prog = [e for e in ev if e["type"] == "progress"]
    assert [(e["product"], e["done"], e["total"]) for e in prog] == [("identify", 3, 3), ("embed", 3, 3), ("jpg", 3, 3)]
    # species crops from the full 3000 px frame, not the 2048 one
    assert fakes.crops == [species_crop((2048, 1365), (3000, 2000))] * 3


def test_detail_image_only_for_identify_and_can_be_off(tmp_path):
    p = make_jpg(tmp_path / "a.jpg", (3000, 2000))
    assert species_crop((2048, 1365), (3000, 2000)) != species_crop((2048, 1365))   # the crops tell them apart
    fakes = Fakes()
    with client_for(fakes.engine()) as c:
        events(c.post("/run", json={"inputs": [{"path": p}], "want": ["embed"]}))
        events(c.post("/run", json={"inputs": [{"path": p}]}))
    assert fakes.crops == [species_crop((2048, 1365), (3000, 2000))]
    fakes = Fakes()
    with TestClient(create_app(fakes.engine(), decode_pool=ThreadPoolExecutor(2), detail_edge=None)) as c:
        events(c.post("/run", json={"inputs": [{"path": p}]}))
    assert fakes.crops == [species_crop((2048, 1365))] and fakes.frames == [(2048, 1365)]


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


def test_request_coordinates_override_exif(client, fakes, tmp_path):
    p = make_jpg(tmp_path / "a.jpg")
    client.post("/run", json={"inputs": [{"path": p, "lat": 37.4, "lon": -122.1, "taken_at": "2026-05-01T08:00:00Z"}]})
    client.post("/run", json={"inputs": [{"path": p}]})
    assert fakes.frames == [(64, 48), (64, 48)]
    # the request's place and date reach the location prior (2026-05-01 is BirdNET week 17);
    # the second request has neither, so the prior is never asked
    assert fakes.where == [(37.4, -122.1, 17)]


def test_requests_are_serialised_and_queued(tmp_path):
    gate = threading.Event()
    fakes = Fakes(block=gate)
    p = make_jpg(tmp_path / "a.jpg")
    finished: list[tuple[str, float]] = []
    with client_for(fakes.engine()) as c:
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
        assert len(fakes.frames) == 1            # the second batch has not touched the detector
        gate.set()
        first.join(10)
        second.join(10)
        assert [n for n, _ in sorted(finished, key=lambda x: x[1])] == ["first", "second"]
        h = c.get("/health").json()
        assert (h["running"], h["queued"]) == (0, 0) and len(fakes.frames) == 2


def test_disconnect_stops_after_current_chunk(tmp_path):
    fakes = Fakes()
    engine = fakes.engine()
    plan = plugin.plan(["identify"], stages.resolve_options(None))
    engine.ensure(plan.models)          # what POST /run does before RunQueue.events
    paths = [make_jpg(tmp_path / f"{i}.jpg") for i in range(6)]
    checks = []

    async def gone():
        checks.append(1)
        return True

    async def collect():
        with ThreadPoolExecutor(2) as pool:
            runs = run_mod.RunQueue(engine, decode_pool=pool, chunk=2)
            return [e async for e in runs.events([{"path": q, "lat": None, "lon": None, "taken_at": None} for q in paths],
                                                 plan, gone)]

    ev = asyncio.run(collect())
    assert len([e for e in ev if e["type"] == "result"]) == 2
    assert not any(e["type"] == "done" for e in ev) and len(fakes.frames) == 2 and checks == [1]


def _wait(cond, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return
        time.sleep(0.02)
    raise AssertionError("condition not reached")


def test_allow_roots(tmp_path):
    inside, outside = tmp_path / "photos", tmp_path / "private"
    inside.mkdir()
    outside.mkdir()
    ok = make_jpg(inside / "a.jpg")
    secret = make_jpg(outside / "b.jpg")
    (inside / "link.jpg").symlink_to(secret)
    fakes = Fakes()
    app = create_app(fakes.engine(), decode_pool=ThreadPoolExecutor(2), allow_roots=[str(inside)])
    with TestClient(app) as c:
        assert events(c.post("/run", json={"inputs": [{"path": ok}]}))[-1]["ok"] == 1
        for path in (secret, str(inside / "link.jpg"), str(inside / ".." / "private" / "b.jpg")):
            r = c.post("/run", json={"inputs": [{"path": ok}, {"path": path}]})
            assert r.status_code == 400 and "outside the allowed roots" in r.json()["error"], path
        r = c.post("/run", json={"inputs": [{"path": ok}], "want": ["jpg"], "options": {"jpg": {"out_dir": str(outside)}}})
        assert r.status_code == 400 and str(outside) in r.json()["error"]
        r = c.post("/run", json={"inputs": [{"path": ok}], "want": ["jpg"],
                                 "options": {"jpg": {"out_dir": str(inside / "jpg")}}})
        assert events(r)[-1]["ok"] == 1
    assert len(fakes.frames) == 1        # rejected requests never reach the models


def test_process_pool_decode_path(tmp_path):
    """Production decodes in a ProcessPoolExecutor: Decoded (with its detail image) must survive
    pickling back from the worker."""
    from concurrent.futures import ProcessPoolExecutor

    p = make_jpg(tmp_path / "a.jpg", (3000, 2000))
    fakes = Fakes()
    with ProcessPoolExecutor(1) as pool, TestClient(create_app(fakes.engine(), decode_pool=pool)) as c:
        ev = events(c.post("/run", json={"inputs": [{"path": p}, {"path": str(tmp_path / "missing.jpg")}]}))
    assert sorted(e["type"] for e in ev if e["type"] in ("result", "error")) == ["error", "result"]
    assert fakes.crops == [species_crop((2048, 1365), (3000, 2000))] and fakes.frames == [(2048, 1365)]


def test_small_request_waits_one_chunk_not_the_whole_batch(tmp_path):
    """A 3-chunk batch is running; a 1-image request arriving meanwhile goes after the chunk in
    progress, before the batch's remaining chunks."""
    first_chunk = threading.Event()
    order: list[str] = []

    class Recording(Fakes):
        def detected(self, images):             # one detector call per chunk (chunk=1, every frame a bird)
            order.append("small" if images[0].size == (30, 20) else "big")
            if len(order) == 1:
                first_chunk.wait(10)
            super().detected(images)

    engine = Recording().engine()
    big = [make_jpg(tmp_path / f"b{i}.jpg") for i in range(3)]
    small = make_jpg(tmp_path / "s.jpg", (30, 20))
    with client_for(engine, chunk=1) as c:
        t = threading.Thread(target=lambda: events(c.post("/run", json={"inputs": [{"path": p} for p in big]})))
        t.start()
        _wait(lambda: len(order) == 1)
        s = threading.Thread(target=lambda: events(c.post("/run", json={"inputs": [{"path": small}]})))
        s.start()
        _wait(lambda: c.get("/health").json()["queued"] == 1)
        first_chunk.set()
        t.join(10)
        s.join(10)
    assert order == ["big", "small", "big", "big"]


def _crash_on(path, edge=None):
    import os

    from bioscan.service import decode

    if path.endswith("crash.jpg"):
        os._exit(1)              # a decoder that dies the way LibRaw can on a corrupt RAW
    return decode.timed_decode(path, edge)


def test_decode_worker_crash_costs_one_file(tmp_path, monkeypatch):
    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor

    monkeypatch.setattr(run_mod, "timed_decode", _crash_on)
    ctx = multiprocessing.get_context("fork")        # the worker must see the patched function
    pool = run_mod.DecodePool(lambda: ProcessPoolExecutor(1, mp_context=ctx))
    good = [make_jpg(tmp_path / f"{i}.jpg") for i in range(3)]
    bad = make_jpg(tmp_path / "crash.jpg")
    engine = Fakes().engine()
    with TestClient(create_app(engine, decode_pool=pool, chunk=2)) as c:
        ev = events(c.post("/run", json={"inputs": [{"path": good[0]}, {"path": bad}, {"path": good[1]},
                                                    {"path": good[2]}]}))
        again = events(c.post("/run", json={"inputs": [{"path": good[0]}]}))     # the pool works afterwards
    errors = [e for e in ev if e["type"] == "error"]
    assert [e["path"] for e in errors] == [bad] and "crashed" in errors[0]["message"]
    assert sorted(e["path"] for e in ev if e["type"] == "result") == sorted(good)
    assert again[-1]["ok"] == 1
    pool.executor.shutdown()


def test_decode_pool_rebuild_only_replaces_the_broken_executor():
    """Two requests that saw the same crash both call rebuild; the second must not shut down the
    fresh executor the first created (that cancelled another request's queued decodes)."""
    made: list[ThreadPoolExecutor] = []

    def factory():
        made.append(ThreadPoolExecutor(1))
        return made[-1]

    pool = run_mod.DecodePool(factory)
    broken = pool.executor
    assert pool.rebuild(broken) and pool.executor is made[1]
    fut = pool.executor.submit(time.sleep, 0.05)             # queued work on the healthy executor
    assert pool.rebuild(broken) and pool.executor is made[1] and len(made) == 2   # late caller: no-op
    fut.result(timeout=5)                                    # not cancelled
    assert not run_mod.DecodePool(executor=broken).rebuild(broken)   # a caller's executor is never rebuilt
    made[1].shutdown()


@pytest.mark.parametrize("options, loaded", [
    ({"species": False}, ["siglip2", "owlv2"]),
    ({"species": False, "candidates": ["Strigidae"]}, ["siglip2", "owlv2", "bioclip"]),   # checked against the lists
    ({}, ["siglip2", "owlv2", "bioclip"]),
])
def test_species_off_does_not_load_bioclip(tmp_path, options, loaded):
    """identify's models depend on its options: with species off (and no candidates to check)
    BioCLIP and the name lists never load; a cold engine then reports no name lists."""
    fakes = Fakes()
    engine = fakes.engine()
    p = make_jpg(tmp_path / "a.jpg")
    with client_for(engine) as c:
        ev = events(c.post("/run", json={"inputs": [{"path": p}], "options": {"identify": options}}))
        assert c.get("/health").json()["models_loaded"] == loaded
    assert engine.loaded() == loaded and ev[-1]["ok"] == 1
    if "bioclip" not in loaded:
        assert fakes.crops == [] and "species" not in ev[1]["products"]["identify"]["boxes"][0]
        assert ev[1]["engine"]["models"]["names"] == {}


def test_embed_alone_loads_only_siglip2(client, engine, tmp_path):
    events(client.post("/run", json={"inputs": [{"path": make_jpg(tmp_path / "a.jpg")}], "want": ["embed"]}))
    assert engine.loaded() == ["siglip2"]
