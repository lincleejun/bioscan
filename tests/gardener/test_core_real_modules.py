"""The core on unit 2's real modules (scan, preview, Analyzer, grouping, export) and the real BioScan
Engine and identify pipeline, with only the models faked at engine.Loaders (tests/contract/conftest.py)."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from contract.conftest import Fakes as ModelFakes
from core_fixtures import BASE, create_task, photos, wait_task
from fastapi.testclient import TestClient

from bioscan.gardener.analyze import Analyzer
from bioscan.gardener.api import create_gardener_app
from bioscan.gardener.tasks import Toolkit

API = "/api/v1"


def real_toolkit(revealed: list[str]) -> Toolkit:
    tk = Toolkit.default()
    tk.analyzer = lambda engine, **kw: Analyzer(engine, decode_pool=ThreadPoolExecutor(2), **kw)
    tk.reveal = revealed.append
    return tk


def test_real_modules_scan_analyse_group_and_export(tmp_path):
    src = tmp_path / "card"
    photos(src, 3)
    app = create_gardener_app(tmp_path / "data", engine=ModelFakes().engine(), toolkit=real_toolkit([]))
    with TestClient(app, base_url=BASE) as c:
        t = wait_task(c, create_task(c, [str(src)], preset="wildlife")["id"], timeout=60)
        run = c.get(f"{API}/runs/{t['scan']['run_id']}").json()
        assert t["scan"]["status"] in ("completed", "partial"), run
        assert run["summary"]["discovered"] == 3 and run["summary"]["previewed"] == 3
        assert run["config"]["versions"]["species"]
        items = c.get(f"{API}/assets", params={"task": t["id"]}).json()["items"]
        d = c.get(f"{API}/assets/{items[0]['id']}").json()
        assert d["caps"]["species"]["state"] in ("done", "unrecognized") and d["caps"]["meta"]["state"] == "done"
        assert not any(k.startswith("raw:") for k in d["caps"])
        if d["caps"]["species"]["state"] == "done" and d["species"]["taxon"]:
            assert d["species"]["taxon"].startswith("Megascops")
        assert any(x["group_kind"] == "all" and x["size"] == 3 for x in c.get(f"{API}/collections", params={"task": t["id"]}).json())
        c.post(f"{API}/marks", json={"asset_ids": [items[0]["id"]], "flag": "pick"})
        rep = c.post(f"{API}/export", json={"asset_ids": [i["id"] for i in items], "scope": "picked"}).json()
        assert rep["assets"] == 1 and rep["xml_path"].endswith(".xml") and rep["copied"] == 0
        assert sorted(p.name for p in src.iterdir()) == ["IMG_0000.jpg", "IMG_0001.jpg", "IMG_0002.jpg"]
