"""Gardener MCP server over stdio (docs/gardener/contract.md §8): newline-delimited JSON-RPC 2.0, MCP
protocol 2025-06-18 (initialize, notifications/initialized, ping, tools/list, tools/call). Every tool is
one call to the service's HTTP API with `X-Gardener-Actor: mcp`; nothing here touches files or SQLite.
stdlib only."""
from __future__ import annotations

import json
import sys
import urllib.parse

from bioscan.gardener.cli import DEFAULT_URL, FILTERS, ApiError, call

PROTOCOL = "2025-06-18"
_STR = {"type": "string"}
_IDS = {"type": "array", "items": _STR}
_FILTER_PROPS = {k: ({"type": "integer"} if k in ("stars_min", "stars") else _STR) for k in FILTERS}


def _tool(name: str, description: str, props: dict, required: tuple[str, ...] = ()) -> dict:
    return {"name": name, "description": description,
            "inputSchema": {"type": "object", "properties": props, "required": list(required)}}


TOOLS = [
    _tool("create_task", "创建扫描任务（来源为服务所在电脑的绝对目录路径），返回 Task。扫描在后台排队执行，不复制原片。",
          {"title": _STR, "sources": _IDS, "subtitle": _STR, "description": _STR, "preset": _STR,
           "capabilities": _IDS, "run_mode": {"type": "string", "enum": ["quiet", "balanced", "full"]}},
          ("title", "sources")),
    _tool("list_tasks", "任务列表：扫描状态（scan）与人工 Review 进度（review）分开给出。",
          {"archived": {"type": "string", "enum": ["0", "1", "all"]}}),
    _tool("get_task", "任务详情，含运行历史 runs 与汇总。", {"id": _STR}, ("id",)),
    _tool("control_task", "任务控制：pause/resume/stop/archive/unarchive/verify_sources，append（paths 追加来源），"
          "rescan（可选 capabilities、asset_ids 小批试跑），retry（失败项，可选 asset_ids）。",
          {"id": _STR, "action": {"type": "string", "enum": ["pause", "resume", "stop", "archive", "unarchive",
                                                             "verify_sources", "append", "rescan", "retry"]},
           "paths": _IDS, "capabilities": _IDS, "asset_ids": _IDS}, ("id", "action")),
    _tool("query_assets", "按 §6.4 筛选查询素材，分页返回 {total, items, offset}。",
          {**_FILTER_PROPS, "order": {"type": "string", "enum": ["time", "path", "score"]},
           "limit": {"type": "integer"}, "offset": {"type": "integer"}}),
    _tool("get_asset", "素材详情：文件、元数据、逐能力状态、AI 建议历史（与人工判断分开）、标记历史、所在集合。",
          {"id": _STR}, ("id",)),
    _tool("set_marks", "设置人工 Flag（pick/reject/null）和/或星级 0–5，全局共享；返回 batch_id 可撤销。",
          {"asset_ids": _IDS, "flag": {"type": ["string", "null"], "enum": ["pick", "reject", None]},
           "stars": {"type": "integer", "minimum": 0, "maximum": 5}, "reason": _STR}, ("asset_ids",)),
    _tool("list_collections", "集合列表，含审阅状态、Pick 数、AI 建议计数、低分提示、新增待审数。",
          {"task": _STR, "kind": {"type": "string", "enum": ["manual", "smart"]}, "deleted": {"type": "boolean"}}),
    _tool("review_collection", "集合审阅：complete（完成本组，未处理项保持原状）、zero（本组不留；含 Pick 时需 "
          "resolution keep_picks 或 clear_picks，否则返回冲突列表）、reopen。",
          {"id": _STR, "action": {"type": "string", "enum": ["complete", "zero", "reopen"]}, "note": _STR,
           "resolution": {"type": "string", "enum": ["keep_picks", "clear_picks"]}}, ("id", "action")),
    _tool("export", "导出一个 Gardener XML（不是 XMP，Lightroom 不能直接读取）；copy=true 时另复制原片并 sha256 校验。"
          "范围：collection_id、asset_ids 或 rule（§6.4 筛选）。",
          {"collection_id": _STR, "asset_ids": _IDS, "rule": {"type": "object"},
           "scope": {"type": "string", "enum": ["picked", "all", "selected"]}, "dest_dir": _STR,
           "copy": {"type": "boolean"}, "conflict": {"type": "string", "enum": ["rename", "skip", "fail"]}},
          ("scope",)),
    _tool("capabilities", "能力列表：版本、可用状态与原因；check=true 时试加载模型（可能耗时）。",
          {"check": {"type": "boolean"}}),
]
_NAMES = {t["name"] for t in TOOLS}


def _seg(v) -> str:
    """One URL path segment: percent-encoded, so a non-ASCII or '/' id stays one segment."""
    return urllib.parse.quote(str(v), safe="")


def _pick(args: dict, keys) -> dict:
    return {k: args[k] for k in keys if k in args}


def route(name: str, args: dict) -> tuple[str, str, dict | None, dict | None]:
    """(method, path, body, query) for one tool call."""
    if name == "create_task":
        return "POST", "/tasks", _pick(args, ("title", "sources", "subtitle", "description", "preset",
                                               "capabilities", "run_mode")), None
    if name == "list_tasks":
        return "GET", "/tasks", None, {"archived": args.get("archived", "0")}
    if name == "get_task":
        return "GET", f"/tasks/{_seg(args['id'])}", None, None
    if name == "control_task":
        tid, act = args["id"], args["action"]
        if act == "append":
            return "POST", f"/tasks/{_seg(tid)}/sources", {"paths": args.get("paths") or []}, None
        if act in ("rescan", "retry"):
            return "POST", f"/tasks/{_seg(tid)}/{act}", _pick(args, ("capabilities", "asset_ids")), None
        return "POST", f"/tasks/{_seg(tid)}/{act.replace('_', '-')}", {}, None
    if name == "query_assets":
        return "GET", "/assets", None, _pick(args, (*FILTERS, "order", "limit", "offset"))
    if name == "get_asset":
        return "GET", f"/assets/{_seg(args['id'])}", None, None
    if name == "set_marks":
        return "POST", "/marks", _pick(args, ("asset_ids", "flag", "stars", "reason")), None
    if name == "list_collections":
        return "GET", "/collections", None, {**_pick(args, ("task", "kind")), "deleted": "1" if args.get("deleted") else "0"}
    if name == "review_collection":
        cid, act = args["id"], args["action"]
        body = _pick(args, ("note",)) if act == "complete" else _pick(args, ("resolution",)) if act == "zero" else {}
        return "POST", f"/collections/{_seg(cid)}/{act}", body, None
    if name == "export":
        return "POST", "/export", _pick(args, ("collection_id", "asset_ids", "rule", "scope", "dest_dir", "copy",
                                               "conflict")), None
    if name == "capabilities":
        return ("POST", "/capabilities/check", {}, None) if args.get("check") else ("GET", "/capabilities", None, None)
    raise KeyError(name)


def handle(msg: dict, url: str) -> dict | None:
    """The JSON-RPC response to one message, or None for a notification."""
    mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
    if "id" not in msg:
        return None

    def ok(result):
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def err(code, message):
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}

    if method == "initialize":
        return ok({"protocolVersion": PROTOCOL, "capabilities": {"tools": {"listChanged": False}},
                   "serverInfo": {"name": "gardener", "version": "0.1.0"},
                   "instructions": "Gardener 素材整理服务的工具；全部经 HTTP API，人工 Flag/Star 与 AI 建议分开保存。"})
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": TOOLS})
    if method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        if name not in _NAMES:
            return err(-32602, f"unknown tool: {name}")
        try:
            m, path, body, query = route(name, args)
        except KeyError as e:
            return err(-32602, f"missing argument: {e}")
        try:
            out, is_error = call(url, m, path, body, query, actor="mcp",
                                 timeout=None if name == "capabilities" else 60), False
        except ApiError as e:
            out, is_error = {"status": e.status, **e.body}, True
        return ok({"content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False)}], "isError": is_error})
    return err(-32601, f"method not found: {method}")


def serve(inp=sys.stdin, out=sys.stdout, url: str = DEFAULT_URL) -> int:
    for line in inp:
        if not line.strip():
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        else:
            try:
                reply = handle(msg, url) if isinstance(msg, dict) else {
                    "jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid request"}}
            except Exception as e:  # noqa: BLE001 - one bad request must not end the server
                mid = msg.get("id") if isinstance(msg, dict) else None
                reply = None if isinstance(msg, dict) and "id" not in msg else {
                    "jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": f"internal error: {e}"}}
        if reply is not None:
            out.write(json.dumps(reply, ensure_ascii=False) + "\n")
            out.flush()
    return 0
