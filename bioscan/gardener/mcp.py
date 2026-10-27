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
    _tool("list_species", "物种卡片（契约修订 4）：species = 有人工确认的物种（数量即“已拍到 N 种”），to_confirm = 只有模型"
          "候选，unresolved = 只到属/科或不确定的照片数。task 只看一个任务。成员用 query_assets 的 concept 筛选。",
          {"task": _STR, "hidden": {"type": "boolean"}}),
    _tool("search_names", "按学名、英文名、同义名、旧学名或用户中文名查物种，得到 identify_species 需要的 concept 与学名。",
          {"q": _STR, "limit": {"type": "integer"}}, ("q",)),
    _tool("identify_species", "人工确认或纠正主体（照片里的一只动物，id 见 get_asset 的 subjects）的物种。claim：{accept: true}"
          "（采纳模型结论）、{level: species, concept, taxon, common?}、{level: genus|family, taxon}、{level: unconfirmed|"
          "not_animal}。scope=burst 时同一连拍里模型认成同一种的主体一起改（已有人工结论的跳过）。先用 dry_run 看影响"
          "范围；返回 batch_id 可用 undo_identification 撤销。没看过图像证据时不要确认物种。",
          {"subject_ids": _IDS, "new_subjects": {"type": "array", "items": {"type": "object"}},
           "claim": {"type": "object"}, "scope": {"type": "string", "enum": ["subject", "burst"]}, "reason": _STR,
           "dry_run": {"type": "boolean"}}, ("claim",)),
    _tool("undo_identification", "撤销一批物种结论（之后又被人工改过的主体会返回冲突）。", {"batch_id": _STR}, ("batch_id",)),
    _tool("set_species_card", "物种卡片的固定封面（cover_asset_id，null 取消）与隐藏（hidden）；只影响显示。",
          {"concept": _STR, "cover_asset_id": {"type": ["string", "null"]}, "hidden": {"type": "boolean"}}, ("concept",)),
    # ---- 契约修订 5E: Lightroom handoff and cleanup plans ----
    _tool("lightroom_handoff", "把集合（collection_id）、物种（concept）或选中照片（asset_ids）交给 Lightroom Classic：冻结成员快照、"
          "写出交接文件，插件导入缺少的照片、加入以交接命名的集合、写星级与物种关键词。默认只交 Pick；uses 可指定用途。"
          "返回的 status 为 written 时插件还没处理；用 get_handoff 看逐张真实结果，不要把 written 说成完成。",
          {"collection_id": _STR, "concept": _STR, "asset_ids": _IDS, "uses": _IDS, "name": _STR}),
    _tool("get_handoff", "交接的逐张结果：ok / skipped / failed 与原因，以及因 Lightroom 中已被改过而跳过的字段（conflicts）。",
          {"id": _STR}, ("id",)),
    _tool("cleanup_plan", "生成清理计划：范围内（collection_id / concept / asset_ids，缺省全库）淘汰照片的全部文件（含 sidecar）、"
          "容量、是否某物种的最后一张。只生成计划，不动文件。", {"collection_id": _STR, "concept": _STR, "asset_ids": _IDS}),
    _tool("get_cleanup_plan", "清理计划与执行结果。", {"id": _STR}, ("id",)),
    _tool("run_cleanup_plan", "执行清理计划：重新核对每张（仍为淘汰、文件未变），把文件移到废纸篓（可恢复，不释放空间，不永久删除）。"
          "只有用户看过计划（尤其是最后的物种照片）并明确同意后才调用；confirm 必须为 true。",
          {"id": _STR, "confirm": {"type": "boolean"}}, ("id", "confirm")),
]
_NAMES = {t["name"] for t in TOOLS}


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
        return "GET", f"/tasks/{args['id']}", None, None
    if name == "control_task":
        tid, act = args["id"], args["action"]
        if act == "append":
            return "POST", f"/tasks/{tid}/sources", {"paths": args.get("paths") or []}, None
        if act in ("rescan", "retry"):
            return "POST", f"/tasks/{tid}/{act}", _pick(args, ("capabilities", "asset_ids")), None
        return "POST", f"/tasks/{tid}/{act.replace('_', '-')}", {}, None
    if name == "query_assets":
        return "GET", "/assets", None, _pick(args, (*FILTERS, "order", "limit", "offset"))
    if name == "get_asset":
        return "GET", f"/assets/{args['id']}", None, None
    if name == "set_marks":
        return "POST", "/marks", _pick(args, ("asset_ids", "flag", "stars", "reason")), None
    if name == "list_collections":
        return "GET", "/collections", None, {**_pick(args, ("task", "kind")), "deleted": "1" if args.get("deleted") else "0"}
    if name == "review_collection":
        cid, act = args["id"], args["action"]
        body = _pick(args, ("note",)) if act == "complete" else _pick(args, ("resolution",)) if act == "zero" else {}
        return "POST", f"/collections/{cid}/{act}", body, None
    if name == "export":
        return "POST", "/export", _pick(args, ("collection_id", "asset_ids", "rule", "scope", "dest_dir", "copy",
                                               "conflict")), None
    if name == "list_species":
        return "GET", "/species", None, {**_pick(args, ("task",)), "hidden": "1" if args.get("hidden") else "0"}
    if name == "search_names":
        return "GET", "/names", None, _pick(args, ("q", "limit"))
    if name == "identify_species":
        return "POST", "/identifications", _pick(args, ("subject_ids", "new_subjects", "claim", "scope", "reason",
                                                        "dry_run")), None
    if name == "undo_identification":
        return "POST", "/identifications/undo", _pick(args, ("batch_id",)), None
    if name == "set_species_card":
        return ("PATCH", f"/species/{urllib.parse.quote(args['concept'], safe=':')}",
                _pick(args, ("cover_asset_id", "hidden")), None)
    if name == "lightroom_handoff":
        return "POST", "/handoffs", _pick(args, ("collection_id", "concept", "asset_ids", "uses", "name")), None
    if name == "get_handoff":
        return "GET", f"/handoffs/{args['id']}", None, None
    if name == "cleanup_plan":
        return "POST", "/cleanup-plans", _pick(args, ("collection_id", "concept", "asset_ids")), None
    if name == "get_cleanup_plan":
        return "GET", f"/cleanup-plans/{args['id']}", None, None
    if name == "run_cleanup_plan":
        return "POST", f"/cleanup-plans/{args['id']}/run", {"confirm": args["confirm"]}, None
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
            reply = handle(msg, url) if isinstance(msg, dict) else {
                "jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid request"}}
        if reply is not None:
            out.write(json.dumps(reply, ensure_ascii=False) + "\n")
            out.flush()
    return 0
