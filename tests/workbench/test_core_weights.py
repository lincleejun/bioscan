"""The models download when 自动归类 is first asked for (契约修订 28): what is on disk, and one download at a time."""
import sys
import time

from core_fixtures import *  # noqa: F403 - fixtures (env)

from gardener.workbench import weights

API = "/api/v1"


def test_models_read_missing_on_an_empty_cache_and_a_download_runs_once_and_reports(env, tmp_path, monkeypatch):
    from huggingface_hub import constants

    monkeypatch.setattr(constants, "HF_HUB_CACHE", str(tmp_path / "hub"))
    script = tmp_path / "fake_download.py"
    script.write_text("import os, sys, time\nassert 'HF_HUB_OFFLINE' not in os.environ\ntime.sleep(0.6)\nsys.exit(int(open(sys.argv[0] + '.code').read()))\n")
    (tmp_path / "fake_download.py.code").write_text("3")
    monkeypatch.setattr(weights, "SCRIPT", script)
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")                      # as in the running service: the child must not inherit it
    with env.client() as c:
        m = c.get(f"{API}/models").json()
        assert not m["ready"] and m["download"]["state"] == "idle"
        assert {i["id"]: i["present"] for i in m["items"]}["bioclip"] is False and m["bytes"] > 6e9
        assert c.post(f"{API}/models/download", json={}).json()["download"]["state"] == "running"
        assert c.post(f"{API}/models/download", json={}).json()["download"]["state"] == "running"    # the same one
        for _ in range(100):
            m = c.get(f"{API}/models").json()
            if m["download"]["state"] != "running":
                break
            time.sleep(0.1)
        assert m["download"]["state"] == "failed" and "3" in m["download"]["error"]
        assert (env.data / "logs" / "models-download.log").is_file()
    assert sys.executable                                           # (the child ran with this interpreter)
