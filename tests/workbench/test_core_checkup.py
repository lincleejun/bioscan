"""The 2026-11 check-up fixes: backups and restore, the diagnostics page, safer cache clearing, a cleanup that
survives a crash between a move and its record, and one filter list for the API, the CLI and MCP."""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest
from core_fixtures import *  # noqa: F403 - fixtures (env, fakes)
from core_fixtures import create_task, make_jpg, wait_task

from gardener.workbench import backup, cleanup, cli, mcp, query
from gardener.workbench import store as st

API = "/api/v1"


def scanned(c, tmp_path, n=3):
    src = tmp_path / "card"
    for i in range(n):
        make_jpg(src / f"IMG_{i}.jpg", when=1_700_000_000 + 600 * i)
    t = wait_task(c, create_task(c, [str(src)])["id"])
    return src, t, sorted(i["id"] for i in c.get(f"{API}/assets", params={"task": t["id"]}).json()["items"])


def test_a_backup_is_made_before_a_migration_kept_to_five_and_restores_what_it_held(env, tmp_path, monkeypatch):
    with env.client() as c:
        _, _, aids = scanned(c, tmp_path)
        c.post(f"{API}/marks", json={"asset_ids": aids[:1], "stars": 5})
    assert backup.listing(env.data) == []                              # a store at the current schema: nothing to save
    with sqlite3.connect(env.data / "gardener.sqlite") as cx:
        cx.execute("UPDATE meta SET value = '4' WHERE key = 'schema_version'")
    st.Store(env.data).close()                                         # the migration backs up first
    [first] = backup.listing(env.data)
    assert first.name.endswith("-schema4.sqlite") and backup.check(first) == "ok"
    with env.client() as c:                                            # the person goes on working ...
        c.post(f"{API}/marks", json={"asset_ids": aids[:1], "stars": 1})
    assert backup.on_start(env.data) is None                           # ... and a start within a day makes no second copy
    monkeypatch.setattr(backup, "EVERY_S", 0)
    for _ in range(6):
        time.sleep(1.05)                                               # names carry the second
        assert backup.on_start(env.data) is not None
    assert len(backup.listing(env.data)) == backup.KEEP and first not in backup.listing(env.data)
    kept = backup.make(env.data, "five-stars-gone")
    with sqlite3.connect(env.data / "gardener.sqlite") as cx:
        cx.execute("UPDATE marks SET stars = 0")
    with pytest.raises(ValueError):
        backup.restore(env.data, "no-such-file.sqlite")
    assert backup.restore(env.data, kept.name) == kept
    with env.client() as c:
        assert c.get(f"{API}/assets", params={"stars_min": 1}).json()["total"] == 1
    assert any(p.name.endswith("-before-restore.sqlite") for p in backup.listing(env.data))


def test_a_damaged_backup_is_refused_and_the_database_stays(env, tmp_path):
    with env.client() as c:
        scanned(c, tmp_path)
    bad = backup.folder(env.data)
    bad.mkdir()
    (bad / "gardener-20260101T000000Z-start.sqlite").write_bytes(b"not a database at all" * 100)
    before = (env.data / "gardener.sqlite").read_bytes()
    with pytest.raises(ValueError, match="完整性"):
        backup.restore(env.data, "latest")
    assert (env.data / "gardener.sqlite").read_bytes() == before


def test_diagnostics_reports_the_check_the_backups_the_disk_and_log_warnings(env, tmp_path):
    with env.client() as c:
        scanned(c, tmp_path)
        backup.make(env.data, "manual")
        (env.data / "logs" / "gardener.log").write_text("2026-11-13 10:00:00 INFO gardener fine\n"
                                                        "2026-11-13 10:00:01 WARNING gardener.backup no backup (start)\n")
        d = c.get(f"{API}/diagnostics").json()
    assert d["database"]["check"] == "ok" and d["database"]["foreign_key_violations"] == 0
    assert d["counts"]["assets"] == 3 and [b["name"][-13:] for b in d["backups"]] == ["manual.sqlite"]
    assert d["disk"]["free"] > 0 and d["log"]["warnings"] == ["2026-11-13 10:00:01 WARNING gardener.backup no backup (start)"]


def test_clearing_the_cache_keeps_the_previews_of_offline_photos(env, tmp_path):
    with env.client() as c:
        _, _, aids = scanned(c, tmp_path)
        with sqlite3.connect(env.data / "gardener.sqlite") as cx:
            cx.execute("UPDATE assets SET online = 0 WHERE id = ?", (aids[0],))
        r = c.post(f"{API}/storage/clear-cache", json={}).json()
        assert r["kept_offline_files"] == 2 and r["removed_files"] == 4      # a thumbnail and a large preview each
        left = sorted(p.name.split(".")[0] for p in (env.data / "cache" / "previews").rglob("*.jpg"))
        assert left == [aids[0], aids[0]]
        with sqlite3.connect(env.data / "gardener.sqlite") as cx:
            assert dict(cx.execute("SELECT id, preview_status FROM assets"))[aids[0]] == "ready"


def test_a_crash_between_a_move_and_its_record_is_settled_by_the_next_run(env, tmp_path):
    """The file was moved, the process died before saying so: the rerun records it as moved and moves the rest."""
    class Dies(BaseException):
        pass

    moved: list[str] = []

    def trash(path, die=True):
        (tmp_path / "bin").mkdir(exist_ok=True)
        Path(path).rename(tmp_path / "bin" / Path(path).name)
        moved.append(path)
        if die and len(moved) == 1:
            raise Dies

    with env.client(trash=trash) as c:
        src, _, aids = scanned(c, tmp_path)
        c.post(f"{API}/marks", json={"asset_ids": aids[:2], "use": "reject"})
        p = c.post(f"{API}/cleanup-plans", json={}).json()
        with pytest.raises(Dies):
            cleanup.run(st.Store(env.data), p["id"], trash)
        with sqlite3.connect(env.data / "gardener.sqlite") as cx:
            assert [r[0] for r in cx.execute("SELECT result FROM cleanup_files WHERE result IS NOT NULL")] == ["moving"]
        report = c.post(f"{API}/cleanup-plans/{p['id']}/run", json={"confirm": True}).json()["report"]
        assert report["trashed_files"] == 2 and report["failed"] == [] and report["skipped"] == []
        assert len(moved) == 2 and len(list(src.iterdir())) == 1          # each file moved once; the kept photo stays


def test_the_cli_and_mcp_take_every_filter_the_api_has_and_mcp_refuses_what_it_does_not_know():
    assert cli.FILTERS is query.KEYS and set(mcp._FILTER_PROPS) == set(query.KEYS)
    a = cli.parser().parse_args(["assets", "--pile", "good", "--order", "discovered"])
    assert cli.request(a)[3]["pile"] == "good"
    assert mcp.route("query_assets", {"pile": "bin"})[3] == {"pile": "bin"}
    r = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {"name": "query_assets", "arguments": {"pile": "good", "pilee": "x"}}}, "http://127.0.0.1:1")
    assert r["error"]["code"] == -32602 and "pilee" in r["error"]["message"]


def test_the_new_cli_commands_and_mcp_tools_send_what_the_api_expects():
    req = lambda *argv: cli.request(cli.parser().parse_args(list(argv)))  # noqa: E731
    assert req("add", "/p/湿地")[:3] == ("POST", "/tasks", {"sources": ["/p/湿地"], "capabilities": ["meta"]})
    assert req("add", "/p/湿地", "--sort", "--lat", "37.4", "--lon", "-122.1")[2] == {"sources": ["/p/湿地"], "lat": 37.4, "lon": -122.1}
    assert req("tag", "add", "精选", "a_1", "a_2")[:3] == ("POST", "/assets/tags", {"asset_ids": ["a_1", "a_2"], "tag": "精选"})
    assert req("tag", "remove", "精选", "a_1")[2] == {"asset_ids": ["a_1"], "tag": "精选", "remove": True}
    assert req("collection", "create", "十佳", "a_1")[:3] == ("POST", "/collections", {"name": "十佳", "asset_ids": ["a_1"]})
    assert req("collection", "add", "c_1", "a_2")[:3] == ("POST", "/collections/c_1/members", {"add": ["a_2"]})
    assert req("cleanup", "plan", "--bin")[:3] == ("POST", "/cleanup-plans", {"pile": "bin"})
    assert req("doctor")[:2] == ("GET", "/diagnostics") and req("tag", "list")[:2] == ("GET", "/tags")
    assert mcp.route("add_folder", {"path": "/p/x"})[:3] == ("POST", "/tasks", {"sources": ["/p/x"], "capabilities": ["meta"]})
    assert mcp.route("add_folder", {"path": "/p/x", "sort": True, "lat": 1.0, "lon": 2.0})[2] == {"sources": ["/p/x"], "lat": 1.0, "lon": 2.0}
    assert mcp.route("tag_assets", {"asset_ids": ["a_1"], "tag": "精选"})[:3] == ("POST", "/assets/tags", {"asset_ids": ["a_1"], "tag": "精选"})
    assert mcp.route("create_collection", {"name": "好片", "kind": "smart", "rule": {"pile": "good"}})[2]["rule"] == {"pile": "good"}
    assert mcp.route("edit_collection_members", {"id": "c/1", "add": ["a_1"]})[:3] == ("POST", "/collections/c%2F1/members", {"add": ["a_1"]})
    assert mcp.route("cleanup_plan", {"pile": "bin"})[2] == {"pile": "bin"}
    assert mcp.route("diagnostics", {})[:2] == ("GET", "/diagnostics") and mcp.route("list_tags", {})[:2] == ("GET", "/tags")


def test_a_folder_needs_no_title_and_a_tag_goes_on_by_name(env, tmp_path):
    with env.client() as c:
        src = tmp_path / "湿地 2026-11"
        make_jpg(src / "a.jpg")
        t = c.post(f"{API}/tasks", json={"sources": [str(src)], "capabilities": ["meta"]}).json()
        assert t["title"] == "湿地 2026-11" and t["capabilities"] == ["meta"]
        [a] = [i["id"] for i in c.get(f"{API}/assets", params={"task": wait_task(c, t["id"])["id"]}).json()["items"]]
        assert c.post(f"{API}/assets/tags", json={"asset_ids": [a], "tag": "十佳"}).json() == {"changed": 1}
        assert c.post(f"{API}/assets/tags", json={"asset_ids": [a], "tag": "十佳"}).json() == {"changed": 0}    # the same tag, not a second one
        assert [x["name"] for x in c.get(f"{API}/tags").json() if x["name"] == "十佳"] == ["十佳"]
        assert c.get(f"{API}/assets", params={"tag": "十佳"}).json()["total"] == 1
        assert c.post(f"{API}/assets/tags", json={"asset_ids": [a], "tag": "十佳", "remove": True}).json() == {"changed": 1}
        assert c.post(f"{API}/assets/tags", json={"asset_ids": [a], "tag": "没有这个", "remove": True}).json() == {"changed": 0}
        assert c.post(f"{API}/assets/tags", json={"asset_ids": [a]}).status_code == 400


def test_the_partial_filter_finds_what_the_page_calls_partial(env, fakes, tmp_path):
    with env.client() as c:
        _, t, aids = scanned(c, tmp_path)
        with sqlite3.connect(env.data / "gardener.sqlite") as cx:
            cap = cx.execute("SELECT capability FROM asset_caps WHERE asset_id = ? AND state = 'done' LIMIT 1", (aids[0],)).fetchone()[0]
            cx.execute("UPDATE asset_caps SET state = 'failed' WHERE asset_id = ? AND capability = ?", (aids[0], cap))
        shown = {i["id"]: i["analysis"] for i in c.get(f"{API}/assets", params={"task": t["id"]}).json()["items"]}
        assert shown[aids[0]] == "partial"
        for value in ("partial", "failed"):                 # failed asks "has a failed capability": it finds it too
            assert [i["id"] for i in c.get(f"{API}/assets", params={"analysis": value}).json()["items"]] == [aids[0]]
        assert c.get(f"{API}/assets", params={"analysis": "done"}).json()["total"] == 2


def test_the_album_core_runs_with_no_model_library_loaded(tmp_path):
    """ADR 0005 §1: browse, mark, the bin, collections and export never import a model library."""
    import subprocess
    import sys

    make_jpg(tmp_path / "card" / "a.jpg")
    code = f"""
import sys
from fastapi.testclient import TestClient
from gardener.workbench.api import create_gardener_app
with TestClient(create_gardener_app({str(tmp_path / "home")!r}), base_url="http://127.0.0.1") as c:
    A = "/api/v1"
    t = c.post(A + "/tasks", json={{"sources": [{str(tmp_path / 'card')!r}], "capabilities": ["meta"]}}).json()
    import time
    for _ in range(200):
        if c.get(A + "/tasks/" + t["id"]).json()["scan"]["status"] in ("completed", "partial", "failed"):
            break
        time.sleep(0.05)
    [a] = [i["id"] for i in c.get(A + "/assets").json()["items"]]
    assert c.post(A + "/marks", json={{"asset_ids": [a], "use": "reject"}}).status_code == 200
    assert c.get(A + "/assets", params={{"pile": "bin"}}).json()["total"] == 1
    assert c.post(A + "/collections", json={{"name": "x", "asset_ids": [a]}}).status_code == 201
    assert c.post(A + "/cleanup-plans", json={{"pile": "bin"}}).status_code == 201
    assert c.get(A + "/diagnostics").json()["database"]["check"] == "ok"
print(sorted(m for m in ("torch", "transformers", "open_clip", "cv2", "rawpy") if m in sys.modules))
"""
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip().splitlines()[-1] == "[]"
