"""`gardener` CLI round-trips against the §6 fake (tests/gardener/fake_api.py) over a real socket."""
import json
import sys

import pytest
from fake_api import Fake, start

from bioscan.gardener import cli


@pytest.fixture
def svc():
    url, fake, server = start(Fake())
    yield url, fake
    server.should_exit = True


def run(capsys, url, *argv):
    code = cli.main(["--url", url, *argv])
    out = capsys.readouterr()
    return code, out.out, out.err


def run_json(capsys, url, *argv):
    code, out, err = run(capsys, url, "--json", *argv)
    assert code == 0, err
    return json.loads(out)


def test_cli_is_import_light():
    heavy = {"fastapi", "uvicorn", "torch", "PIL", "numpy"}
    import subprocess
    code = ("import sys; import bioscan.gardener.cli, bioscan.gardener.mcp; "
            f"print(sorted(m for m in {sorted(heavy)!r} if m in sys.modules))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    assert out.strip() == "[]"


def test_health_and_actor_header(svc, capsys):
    url, fake = svc
    assert run_json(capsys, url, "health")["status"] == "ok"
    assert fake.actors[-1] == "cli"


def test_task_lifecycle(svc, capsys, tmp_path):
    url, fake = svc
    t = run_json(capsys, url, "task", "create", "--source", str(tmp_path), "--title", "秦岭", "--preset", "wildlife")
    assert t["title"] == "秦岭" and t["sources"][0]["path"] == str(tmp_path) and "species" in t["capabilities"]
    tid = t["id"]
    assert tid in [x["id"] for x in run_json(capsys, url, "task", "list")]
    assert run_json(capsys, url, "task", "pause", tid)["scan"]["status"] == "paused"
    assert run_json(capsys, url, "task", "resume", tid)["scan"]["status"] == "queued"
    assert run_json(capsys, url, "task", "stop", tid)["scan"]["status"] == "stopped"
    r = run_json(capsys, url, "task", "append", tid, str(tmp_path / "card2"))
    assert r["kind"] == "append"
    r = run_json(capsys, url, "task", "rescan", tid, "--cap", "species", "--asset", "a_0005")
    assert r["kind"] == "rescan" and r["scope"] == {"capabilities": ["species"], "asset_ids": ["a_0005"]}
    assert run_json(capsys, url, "task", "retry", tid)["kind"] == "retry"
    assert run_json(capsys, url, "task", "verify", tid)["id"] == tid
    show = run_json(capsys, url, "task", "show", tid)
    assert [x["kind"] for x in show["runs"]] == ["scan", "append", "rescan", "retry"]
    assert run_json(capsys, url, "task", "archive", tid)["archived"] is True
    assert tid not in [x["id"] for x in run_json(capsys, url, "task", "list")]
    code, out, _ = run(capsys, url, "task", "show", tid)
    assert code == 0 and "运行:" in out and "append" in out


def test_task_error_is_reported(svc, capsys):
    url, _ = svc
    code, _, err = run(capsys, url, "task", "pause", "t_nope")
    assert code == cli.EXIT_ERROR and "没有这个任务" in err


def test_unreachable_service(capsys):
    code, _, err = run(capsys, "http://127.0.0.1:9", "health")
    assert code == cli.EXIT_SERVICE and "无法连接" in err


def test_assets_filters_and_table(svc, capsys):
    url, _ = svc
    out = run_json(capsys, url, "assets", "--ai", "reject", "--media", "photo")
    assert out["total"] == 2 and {i["ai"]["verdict"] for i in out["items"]} == {"reject"}
    assert run_json(capsys, url, "assets", "--species", "画眉")["total"] == 2
    code, text, _ = run(capsys, url, "assets", "--q", "MVI")
    assert code == 0 and "MVI_0006.MOV" in text and "共 1 张" in text


def test_mark_and_undo(svc, capsys):
    url, fake = svc
    m = run_json(capsys, url, "mark", "a_0006", "a_0007", "--flag", "reject", "--stars", "2", "--reason", "糊")
    assert [a["flag"] for a in m["assets"]] == ["reject", "reject"]
    assert fake.assets["a_0006"]["mark_history"][0]["actor"] == "cli"
    assert run_json(capsys, url, "undo", m["batch_id"]) == {"undone": 2}
    assert fake.assets["a_0006"]["flag"] is None
    assert run_json(capsys, url, "mark", "a_0006", "--flag", "none", "--stars", "0")["assets"][0]["flag"] is None


def test_collections_review(svc, capsys):
    url, fake = svc
    cols = run_json(capsys, url, "collections", "--task", "t_0001")
    birds = next(c for c in cols if c["group_kind"] == "species")
    code, _, err = run(capsys, url, "collection", "zero", birds["id"])
    assert code == cli.EXIT_ERROR and "picks" in err          # the 409 body lists the picks and their collections
    z = run_json(capsys, url, "collection", "zero", birds["id"], "--resolution", "keep_picks")
    assert z["review"]["state"] == "zeroed" and fake.assets["a_0005"]["flag"] == "pick"
    assert run_json(capsys, url, "collection", "reopen", birds["id"])["review"]["state"] == "open"
    assert run_json(capsys, url, "collection", "complete", birds["id"], "--note", "ok")["review"]["state"] == "completed"
    run_json(capsys, url, "collection", "delete", birds["id"])
    assert birds["id"] in [c["id"] for c in run_json(capsys, url, "collections", "--deleted")]
    run_json(capsys, url, "collection", "restore", birds["id"])
    code, text, _ = run(capsys, url, "collections")
    assert code == 0 and "林鸟 · 画眉" in text


def test_export_scopes(svc, capsys, tmp_path):
    url, fake = svc
    col = run_json(capsys, url, "collections")[1]["id"]
    rep = run_json(capsys, url, "export", "--collection", col, "--scope", "all", "--dest", str(tmp_path))
    assert rep["assets"] == 3 and rep["copied"] == 0 and rep["xml_path"].startswith(str(tmp_path))
    assert fake.exports[-1]["request"] == {"scope": "all", "copy": False, "conflict": "rename",
                                           "dest_dir": str(tmp_path), "collection_id": col}
    run_json(capsys, url, "export", "--media", "video", "--scope", "all", "--copy")
    assert fake.exports[-1]["request"]["rule"] == {"media": "video"} and fake.exports[-1]["request"]["copy"] is True


def test_caps_and_storage(svc, capsys):
    url, fake = svc
    caps = run_json(capsys, url, "caps")
    assert [c["id"] for c in caps][:2] == ["meta", "content"]
    code, text, _ = run(capsys, url, "caps", "--check")
    assert code == 0 and "BioCLIP 模型未下载" in text
    assert run_json(capsys, url, "storage")["cache_files"] == 12
    assert run_json(capsys, url, "storage", "--clear-cache")["removed_files"] == 12


def test_serve_passes_its_arguments_through(monkeypatch):
    import types
    seen = {}
    mod = types.ModuleType("bioscan.gardener.server")
    mod.main = lambda argv: seen.setdefault("argv", argv) and 0
    monkeypatch.setitem(sys.modules, "bioscan.gardener.server", mod)
    assert cli.main(["serve", "--port", "8770", "--data-dir", "/tmp/g"]) == 0
    assert seen["argv"] == ["--port", "8770", "--data-dir", "/tmp/g"]


def test_unknown_arguments_rejected():
    with pytest.raises(SystemExit):
        cli.main(["health", "--bogus"])
