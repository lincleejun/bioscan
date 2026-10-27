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


def test_mark_sends_only_given_keys(svc, capsys):
    """§10 X5: an omitted key means unchanged, so `mark --stars 4` must not send a flag."""
    url, fake = svc
    run_json(capsys, url, "mark", "a_0005", "--stars", "4")
    assert fake.mark_bodies[-1] == {"asset_ids": ["a_0005"], "stars": 4}
    assert fake.assets["a_0005"]["flag"] == "pick"
    run_json(capsys, url, "mark", "a_0005", "--flag", "none")
    assert fake.mark_bodies[-1] == {"asset_ids": ["a_0005"], "flag": None}


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


def test_species_commands_build_the_revision_4_requests():
    from bioscan.gardener.cli import parser, request

    req = lambda *argv: request(parser().parse_args(list(argv)))  # noqa: E731
    assert req("identify", "s_1", "s_2", "--accept", "--burst") == \
        ("POST", "/identifications", {"subject_ids": ["s_1", "s_2"], "claim": {"accept": True}, "scope": "burst",
                                      "dry_run": False}, None)
    assert req("identify", "s_1", "--level", "species", "--taxon", "Aquila chrysaetos", "--concept", "avibase:a",
               "--dry-run")[2]["claim"] == {"level": "species", "taxon": "Aquila chrysaetos", "concept": "avibase:a",
                                            "common": None}
    assert req("identify", "s_1", "--level", "genus", "--taxon", "Aquila")[2]["claim"] == {"level": "genus", "taxon": "Aquila"}
    assert req("identify", "s_1", "--unsure")[2]["claim"] == {"level": "unconfirmed"}
    assert req("identify-undo", "b_1") == ("POST", "/identifications/undo", {"batch_id": "b_1"}, None)
    assert req("species", "--task", "t_1") == ("GET", "/species", None, {"task": "t_1", "hidden": "0"})
    assert req("names", "金雕") == ("GET", "/names", None, {"q": "金雕", "limit": 20})
    assert req("species-card", "name:Corvus corax", "--hide") == ("PATCH", "/species/name:Corvus%20corax", {"hidden": True}, None)
    assert req("assets", "--concept", "avibase:a", "--id-state", "confirmed")[3]["id_state"] == "confirmed"


def test_review_flow_commands_build_the_revision_5b_requests():
    from bioscan.gardener.cli import parser, request

    req = lambda *argv: request(parser().parse_args(list(argv)))  # noqa: E731
    assert req("mark", "a_1", "--use", "edit") == ("POST", "/marks", {"asset_ids": ["a_1"], "use": "edit"}, None)
    assert req("assets", "--use", "pending")[3]["use"] == "pending"
    assert req("keep-best", "a_1", "--edit", "--dry-run") == \
        ("POST", "/marks/keep-best", {"asset_id": "a_1", "keep_use": "edit", "dry_run": True}, None)
    assert req("encounters", "--task", "t_1") == ("GET", "/encounters", None, {"task": "t_1", "concept": None})
    assert req("encounter", "split", "e_1", "--at", "a_2") == ("POST", "/encounters/e_1/split", {"at": "a_2"}, None)
    assert req("encounter", "split", "e_1", "--assets", "a_2", "a_3")[2] == {"asset_ids": ["a_2", "a_3"]}
    assert req("encounter", "merge", "e_1", "e_2") == ("POST", "/encounters/merge", {"ids": ["e_1", "e_2"]}, None)
    assert req("collection", "preview-rule", "c_1", "--rule", '{"use": "edit"}') == \
        ("POST", "/collections/c_1/preview-rule", {"rule": {"use": "edit"}}, None)
    assert req("identify", "s_1", "--accept", "--encounter")[2]["scope"] == "encounter"


def test_keep_best_and_encounters_render(svc, capsys):
    url, _ = svc
    assert cli.main(["--url", url, "keep-best", "a_0006", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "预览（未写入）" in out and "淘汰 2 张" in out
    assert cli.main(["--url", url, "encounters", "--task", "t_0001"]) == 0
    assert "e_fake1" in capsys.readouterr().out


def test_import_commands(svc, capsys, tmp_path):
    from bioscan.gardener.cli import parser, request

    req = lambda *argv: request(parser().parse_args(list(argv)))  # noqa: E731
    assert req("import", "plan", "t_1", "--dest", "/lib", "--place", "东滩", "--no-pending") == \
        ("POST", "/tasks/t_1/import-plan", {"dest_root": "/lib", "include_pending": False, "place": "东滩"}, None)
    assert req("import", "run", "ip_1") == ("POST", "/imports/ip_1/run", {}, None)
    assert req("import", "show", "ip_1") == ("GET", "/imports/ip_1", None, None)
    assert req("import", "list", "--task", "t_1") == ("GET", "/imports", None, {"task": "t_1"})
    url, fake = svc
    tid = next(iter(fake.tasks))
    p = run_json(capsys, url, "import", "plan", tid, "--dest", str(tmp_path))
    assert p["status"] == "planned" and p["dest_root"] == str(tmp_path)
    code, out, _ = run(capsys, url, "import", "run", p["id"])
    assert code == 0 and "partial" in out and "源文件不存在或离线" in out and "选入修图" in out


def test_handoff_and_cleanup_commands(svc, capsys):
    """契约修订 5E: handoff create/show and cleanup plan/run (run refuses without --yes)."""
    url, fake = svc
    from bioscan.gardener.cli import parser, request

    req = lambda *argv: request(parser().parse_args(list(argv)))  # noqa: E731
    assert req("handoff", "create", "--concept", "avibase:x", "--use", "record") == \
        ("POST", "/handoffs", {"concept": "avibase:x", "uses": ["record"]}, None)
    assert req("cleanup", "plan") == ("POST", "/cleanup-plans", {}, None)
    with pytest.raises(SystemExit):
        req("cleanup", "run", "p_1")
    col = next(c for c in fake.cols.values() if c["group_kind"] == "all")
    code, out, err = run(capsys, url, "handoff", "create", "--collection", col["id"])
    assert code == 0, err
    assert "等待 Lightroom 插件返回结果" in out
    hid = next(iter(fake.handoffs))
    assert run(capsys, url, "handoff", "show", hid)[0] == 0
    fake.assets[next(iter(fake.assets))]["flag"] = "reject"
    plan = run_json(capsys, url, "cleanup", "plan")
    code, out, _ = run(capsys, url, "cleanup", "run", plan["id"], "--yes")
    assert code == 0 and "可恢复，未释放空间" in out and "清空废纸篓后才真正释放" in out


def test_import_species_stats_and_batch_collection_commands(svc, capsys, tmp_path):
    """契约修订 7B: `import show` prints the species stats; `import collection` saves the batch as a collection."""
    from bioscan.gardener.cli import parser, request

    assert request(parser().parse_args(["import", "collection", "ip_1"])) == ("POST", "/imports/ip_1/collection", {}, None)
    url, fake = svc
    p = run_json(capsys, url, "import", "plan", next(iter(fake.tasks)), "--dest", str(tmp_path))
    code, out, _ = run(capsys, url, "import", "show", p["id"])
    assert code == 0 and "物种：新增 1" in out and "唯一记录" in out and "不导入" in out
    run(capsys, url, "import", "run", p["id"])
    code, out, _ = run(capsys, url, "import", "collection", p["id"])
    assert code == 0 and "Collection：c" in out


def test_handoff_retry_command(svc, capsys):
    """契约修订 7B: `handoff retry` maps to POST /handoffs/{id}/retry; before a result it is refused (409)."""
    from bioscan.gardener.cli import parser, request

    assert request(parser().parse_args(["handoff", "retry", "h_1"])) == ("POST", "/handoffs/h_1/retry", {}, None)
    url, fake = svc
    pick = next(a for a in fake.assets.values() if a["flag"] == "pick")
    h = run_json(capsys, url, "handoff", "create", "--assets", pick["id"])
    code, _, err = run(capsys, url, "handoff", "retry", h["id"])
    assert code != 0 and "还没有返回" in err


def test_handoff_preset_export_preview_and_presets_commands(svc, capsys):
    """契约修订 7B: presets and export on `handoff create` (export needs all three settings), preview, presets."""
    from bioscan.gardener.cli import parser, request

    req = lambda *argv: request(parser().parse_args(list(argv)))  # noqa: E731
    assert req("handoff", "create", "--concept", "k", "--preset", "P1", "--export-preset", "/p/J.lrtemplate",
               "--export-dir", "/out", "--overwrite", "skip", "--no-remember") == \
        ("POST", "/handoffs", {"concept": "k", "develop": {"uuid": "P1", "name": "P1"},
                               "export": {"preset_path": "/p/J.lrtemplate", "dest_dir": "/out", "overwrite": "skip"},
                               "remember": False}, None)
    with pytest.raises(SystemExit):
        req("handoff", "create", "--concept", "k", "--export-dir", "/out")          # no preset, no policy
    assert req("handoff", "preview", "--concept", "k") == ("POST", "/handoffs/preview", {"concept": "k"}, None)
    assert req("handoff", "presets") == ("GET", "/lightroom/presets", None, None)
    url, fake = svc
    assert run_json(capsys, url, "handoff", "presets")["develop"][0]["uuid"] == "P-WARM"
    pick = next(a for a in fake.assets.values() if a["flag"] == "pick")
    assert run_json(capsys, url, "handoff", "preview", "--assets", pick["id"])["count"] == 1


def test_revision_7a_commands_split_bursts_undo_encounters_and_show_other_collections(svc, capsys):
    from bioscan.gardener.cli import parser, request

    req = lambda *argv: request(parser().parse_args(list(argv)))  # noqa: E731
    assert req("burst", "split", "--at", "a_2") == ("POST", "/bursts/split", {"at": "a_2"}, None)
    assert req("burst", "split", "--assets", "a_2", "a_3")[2] == {"asset_ids": ["a_2", "a_3"]}
    assert req("encounter", "undo", "b_1") == ("POST", "/encounters/undo", {"batch_id": "b_1"}, None)
    url, fake = svc
    fake.new_col({"name": "行程精选", "asset_ids": ["a_0007"]})
    assert cli.main(["--url", url, "keep-best", "a_0006", "--dry-run"]) == 0
    assert "也在集合「行程精选」：1 张" in capsys.readouterr().out
    assert cli.main(["--url", url, "burst", "split", "--at", "a_0006"]) == 0
    assert "t_0001:b1" in capsys.readouterr().out
    assert cli.main(["--url", url, "encounter", "undo", "b_enc1"]) == 0
    assert "split" in capsys.readouterr().out
