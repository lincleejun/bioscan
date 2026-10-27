"""Gardener MCP stdio server: JSON-RPC framing and every tool against the §6 fake."""
import io
import json
import subprocess
import sys

import pytest
from fake_api import Fake, start

from bioscan.gardener import mcp


@pytest.fixture
def svc():
    url, fake, server = start(Fake())
    yield url, fake
    server.should_exit = True


def converse(url, *msgs):
    out = io.StringIO()
    mcp.serve(io.StringIO("".join(json.dumps(m) + "\n" for m in msgs)), out, url)
    return [json.loads(line) for line in out.getvalue().splitlines()]


def tool(url, name, **args):
    [r] = converse(url, {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})
    res = r["result"]
    return json.loads(res["content"][0]["text"]), res["isError"]


def test_handshake_list_and_errors(svc):
    url, _ = svc
    replies = converse(url,
                       {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                        "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t"}}},
                       {"jsonrpc": "2.0", "method": "notifications/initialized"},
                       {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                       {"jsonrpc": "2.0", "id": 3, "method": "ping"},
                       {"jsonrpc": "2.0", "id": 4, "method": "nope"},
                       {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "nope"}})
    assert [r["id"] for r in replies] == [1, 2, 3, 4, 5]        # the notification gets no reply
    init = replies[0]["result"]
    assert init["protocolVersion"] == "2025-06-18" and "tools" in init["capabilities"]
    names = [t["name"] for t in replies[1]["result"]["tools"]]
    assert names == ["create_task", "list_tasks", "get_task", "control_task", "query_assets", "get_asset", "set_marks",
                     "list_collections", "review_collection", "export", "capabilities", "list_species", "search_names",
                     "identify_species", "undo_identification", "set_species_card", "plan_import", "run_import",
                     "get_import"]
    assert all(t["inputSchema"]["type"] == "object" for t in replies[1]["result"]["tools"])
    assert replies[2]["result"] == {}
    assert replies[3]["error"]["code"] == -32601 and replies[4]["error"]["code"] == -32602


def test_parse_error(svc):
    out = io.StringIO()
    mcp.serve(io.StringIO("{not json\n"), out, svc[0])
    assert json.loads(out.getvalue())["error"]["code"] == -32700


def test_tools_call_the_api(svc, tmp_path):
    url, fake = svc
    t, err = tool(url, "create_task", title="MCP 任务", sources=[str(tmp_path)], preset="quick")
    assert not err and t["title"] == "MCP 任务" and fake.actors[-1] == "mcp"
    assert t["id"] in [x["id"] for x in tool(url, "list_tasks")[0]]
    assert tool(url, "control_task", id=t["id"], action="pause")[0]["scan"]["status"] == "paused"
    assert tool(url, "control_task", id=t["id"], action="append", paths=[str(tmp_path / "b")])[0]["kind"] == "append"
    assert tool(url, "control_task", id=t["id"], action="rescan", capabilities=["species"])[0]["kind"] == "rescan"
    assert tool(url, "control_task", id=t["id"], action="verify_sources")[0]["id"] == t["id"]
    assert len(tool(url, "get_task", id=t["id"])[0]["runs"]) == 3
    q, _ = tool(url, "query_assets", ai="reject", limit=1)
    assert q["total"] == 2 and len(q["items"]) == 1
    d, _ = tool(url, "get_asset", id="a_0005")
    assert d["caps"]["subject"]["data"]["boxes"] and d["collections"]
    m, _ = tool(url, "set_marks", asset_ids=["a_0006"], flag="pick", stars=3)
    assert m["assets"][0] == {"id": "a_0006", "flag": "pick", "stars": 3, "version": 3}
    cols, _ = tool(url, "list_collections", task="t_0001")
    birds = next(c for c in cols if c["group_kind"] == "species")
    conflict, err = tool(url, "review_collection", id=birds["id"], action="zero")
    assert err and conflict["status"] == 409 and {p["id"] for p in conflict["picks"]} == {"a_0005", "a_0006"}
    z, err = tool(url, "review_collection", id=birds["id"], action="zero", resolution="clear_picks")
    assert not err and z["review"]["state"] == "zeroed" and z["picks"] == 0
    assert fake.assets["a_0005"]["flag"] is None                         # X6: cleared to null, not reject
    assert fake.assets["a_0005"]["mark_history"][-1]["reason"] == f"zero:{birds['id']}"
    m2, _ = tool(url, "set_marks", asset_ids=["a_0007"], stars=2)
    assert fake.mark_bodies[-1] == {"asset_ids": ["a_0007"], "stars": 2}
    [r] = converse(url, {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "list_tasks"}})
    assert not r["result"]["isError"]
    assert z["batch_id"] and fake.undo(z["batch_id"]) == {"undone": 2}
    assert tool(url, "review_collection", id=birds["id"], action="reopen")[0]["review"]["state"] == "open"
    rep, _ = tool(url, "export", collection_id=birds["id"], scope="all", copy=False)
    assert rep["assets"] == 3 and rep["copied"] == 0
    caps, _ = tool(url, "capabilities", check=True)
    assert any(c["state"] == "unavailable" and c["reason"] for c in caps)


def test_missing_argument_is_invalid_params(svc):
    [r] = converse(svc[0], {"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                            "params": {"name": "get_task", "arguments": {}}})
    assert r["error"]["code"] == -32602


def test_gardener_mcp_subprocess(svc):
    """`gardener mcp` over real stdio pipes: one JSON object per line in, one per line out."""
    url, _ = svc
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "list_tasks", "arguments": {}}}]
    p = subprocess.run([sys.executable, "-m", "bioscan.gardener.cli", "--url", url, "mcp"],
                       input="".join(json.dumps(m) + "\n" for m in msgs), capture_output=True, text=True, timeout=30)
    replies = [json.loads(line) for line in p.stdout.splitlines()]
    assert p.returncode == 0 and [r["id"] for r in replies] == [1, 2]
    assert len(json.loads(replies[1]["result"]["content"][0]["text"])) == 2


def test_species_tools_map_to_the_revision_4_endpoints():
    from bioscan.gardener.mcp import route

    claim = {"level": "species", "concept": "avibase:x", "taxon": "Nannus pacificus"}
    assert route("identify_species", {"subject_ids": ["s_1"], "claim": claim, "scope": "burst", "dry_run": True}) == \
        ("POST", "/identifications", {"subject_ids": ["s_1"], "claim": claim, "scope": "burst", "dry_run": True}, None)
    assert route("undo_identification", {"batch_id": "b_1"}) == ("POST", "/identifications/undo", {"batch_id": "b_1"}, None)
    assert route("list_species", {"task": "t_1"}) == ("GET", "/species", None, {"task": "t_1", "hidden": "0"})
    assert route("search_names", {"q": "金雕"}) == ("GET", "/names", None, {"q": "金雕"})
    assert route("set_species_card", {"concept": "name:Corvus corax", "hidden": True}) == \
        ("PATCH", "/species/name:Corvus%20corax", {"hidden": True}, None)


def test_import_tools_map_to_the_revision_5d_endpoints(svc):
    from bioscan.gardener.mcp import route

    assert route("plan_import", {"task_id": "t_1", "dest_root": "/lib", "include_pending": False}) == \
        ("POST", "/tasks/t_1/import-plan", {"dest_root": "/lib", "include_pending": False}, None)
    assert route("run_import", {"plan_id": "ip_1"}) == ("POST", "/imports/ip_1/run", {}, None)
    assert route("get_import", {"plan_id": "ip_1"}) == ("GET", "/imports/ip_1", None, None)
    url, fake = svc
    p, err = tool(url, "plan_import", task_id=next(iter(fake.tasks)), dest_root="/lib", place="东滩")
    assert not err and p["folder"].endswith("_东滩")
    done, err = tool(url, "run_import", plan_id=p["id"])
    assert not err and done["status"] in ("completed", "partial")
