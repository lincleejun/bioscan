"""Analyzer on the real Engine, RunQueue and stages with fake model adapters (media_fixtures)."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest
from media_fixtures import fake_engine, make_jpg

from bioscan import cull
from bioscan.gardener import capabilities
from bioscan.gardener.analyze import Analyzer

WILDLIFE = capabilities.expand(capabilities.PRESETS["wildlife"]["capabilities"])


def run(analyzer, items, caps, stop=lambda: False):
    async def go():
        return [r async for r in analyzer.analyze(items, caps, stop)]
    return asyncio.run(go())


@pytest.fixture
def photos(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    # two frames 0.3 s apart (a burst: the fake vectors are all alike), one an hour later
    times = [("2025:03:01 08:12:00", "10"), ("2025:03:01 08:12:00", "40"), ("2025:03:01 09:12:00", None)]
    return [{"asset_id": f"a{i}", "path": make_jpg(src / f"IMG_{i}.JPG", taken=t, subsec=s),
             "lat": None, "lon": None, "taken_at": None} for i, (t, s) in enumerate(times)]


def analyzer(engine, chunk=16):
    return Analyzer(engine, chunk=chunk, decode_pool=ThreadPoolExecutor(2))


def test_every_capability_normalised_and_raw_products_kept(photos):
    a = analyzer(fake_engine())
    out = run(a, photos, WILDLIFE)
    by = {(r.asset_id, r.capability): r for r in out}
    for aid in ("a0", "a1", "a2"):
        for cap in ("content", "subject", "species", "burst", "quality", "aesthetic"):
            r = by[(aid, cap)]
            assert r.version, cap
            assert r.status == "done", (cap, r.error)
        for stage in ("identify", "embed", "aesthetics", "quality", "scene"):
            assert by[(aid, f"raw:{stage}")].status == "done"
    assert ("a0", "meta") not in by and ("a0", "place_event") not in by     # model-free: not analysed here
    c = by[("a0", "content")].data
    assert c["group"] == "wildlife" and c["group_zh"] == "野生动物" and c["label_zh"]
    sp = by[("a0", "species")].data
    assert sp["top"][0]["scientific"] == "Megascops kennicottii" and sp["uncertain"] == (sp["level"] != "species")
    assert by[("a0", "subject")].data["boxes"][0]["kind"] == "bird"
    q = by[("a0", "quality")].data
    assert len(q["reasons"]) == len(q["reasons_zh"])
    assert isinstance(by[("a0", "aesthetic")].data["score"], float)
    assert by[("a0", "burst")].data == {"vector_dim": 768}
    assert isinstance(by[("a0", "raw:embed")].data["vector"], str)        # base64 float16, as BioScan sends it
    assert a.versions(WILDLIFE)["species"] == by[("a0", "species")].version
    a.close()


def test_reduce_reads_the_vectors_back_and_suggests(photos):
    a = analyzer(fake_engine())
    out = run(a, photos, WILDLIFE)
    rows = []
    for it in photos:
        products = {r.capability[4:]: r.data for r in out if r.asset_id == it["asset_id"]
                    and r.capability.startswith("raw:")}
        assert cull.vector({"type": "result", "products": products}) is not None
        rows.append({"asset_id": it["asset_id"], "path": it["path"], "taken_at": None, "camera": None,
                     "products": products})
    got = a.reduce(rows, WILDLIFE)
    assert got["a0"]["burst"]["id"] == got["a1"]["burst"]["id"] and got["a0"]["burst"]["size"] == 2
    assert got["a2"]["burst"] is None
    assert sorted(got[x]["burst"]["rank"] for x in ("a0", "a1")) == [1, 2]
    for x in got.values():
        s = x["suggestion"]
        assert s["verdict"] in ("pick", "reject", "none")
        assert all(isinstance(r, str) for r in s["reasons"])
    second = next(x for x in ("a0", "a1") if got[x]["burst"]["rank"] == 2)
    if got[second]["suggestion"]["verdict"] == "none":
        assert got[second]["suggestion"]["reasons"] == ["连拍中非最佳帧"]
    a.close()


def test_burst_alone_takes_time_and_camera_from_rows():
    a = analyzer(fake_engine())
    rows = [{"asset_id": i, "path": f"/x/{i}", "taken_at": t, "camera": "SONY ILCE-7M4",
             "products": {"embed": {"dim": 2, "vector": [1.0, 0.0]}}} for i, t in
            (("a", "2025-03-01T08:00:00.1"), ("b", "2025-03-01T08:00:00.9"), ("c", "2025-03-01T09:00:00"))]
    got = a.reduce(rows, ["meta", "burst"])
    assert got["a"]["burst"] == {"id": got["b"]["burst"]["id"], "rank": 1, "size": 2}
    assert got["c"]["burst"] is None and got["a"]["suggestion"] is None
    a.close()


def test_a_model_that_does_not_load_makes_its_capabilities_unavailable(photos):
    a = analyzer(fake_engine(fail="bioclip"))
    out = run(a, photos, WILDLIFE)
    by = {(r.asset_id, r.capability): r for r in out}
    sp = by[("a0", "species")]
    assert sp.status == "unavailable" and "bioclip" in sp.error and sp.data is None
    assert by[("a0", "subject")].status == "done"       # the detector loaded: boxes still come
    assert by[("a0", "content")].status == "done"
    a.close()

    a = analyzer(fake_engine(fail="siglip2"))
    out = run(a, photos, WILDLIFE)
    assert out and all(r.status == "unavailable" and r.data is None and "siglip2" in r.error for r in out)
    assert {r.capability for r in out} == {"content", "subject", "species", "burst", "quality", "aesthetic"}
    a.close()


def test_decode_failure_fails_every_capability(tmp_path, photos):
    bad = tmp_path / "src" / "broken.jpg"
    bad.write_bytes(b"not a jpeg")
    items = photos[:1] + [{"asset_id": "bad", "path": str(bad), "lat": None, "lon": None, "taken_at": None}]
    a = analyzer(fake_engine())
    out = [r for r in run(a, items, ["meta", "content"]) if r.asset_id == "bad"]
    assert [(r.capability, r.status) for r in out] == [("content", "failed")]
    assert "decode" in out[0].error
    a.close()


def test_should_stop_is_checked_between_chunks(photos):
    a = analyzer(fake_engine(), chunk=1)
    out = run(a, photos, ["meta", "content"], stop=lambda: True)
    assert {r.asset_id for r in out} == {"a0"}         # the first chunk finishes, then it stops
    a.close()


def test_model_free_capabilities_run_nothing(photos):
    a = analyzer(fake_engine(fail="siglip2"))
    assert run(a, photos, ["meta", "place_event"]) == []
    assert a.versions(["meta", "place_event"]) == {}
    a.close()
