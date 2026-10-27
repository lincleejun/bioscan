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
    _tool("set_marks", "设置人工用途 use（record 保留记录 / edit 选入修图 / pending 待定 / reject 淘汰；flag pick/reject/null "
          "是旧写法，pick = record）和/或星级 0–5，全局共享；返回 batch_id 可撤销。",
          {"asset_ids": _IDS, "use": {"type": "string", "enum": ["record", "edit", "pending", "reject"]},
           "flag": {"type": ["string", "null"], "enum": ["pick", "reject", None]},
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
          "候选，unresolved = 只到属/科或不确定的照片数，to_reconfirm = 名录拆分后待重新确认的照片数（id_state=reconfirm）。task 只看一个任务。成员用 query_assets 的 concept 筛选。",
          {"task": _STR, "hidden": {"type": "boolean"}}),
    _tool("search_names", "按学名、英文名、同义名、旧学名或用户中文名查物种，得到 identify_species 需要的 concept 与学名。",
          {"q": _STR, "limit": {"type": "integer"}}, ("q",)),
    _tool("identify_species", "人工确认或纠正主体（照片里的一只动物，id 见 get_asset 的 subjects）的物种。claim：{accept: true}"
          "（采纳模型结论）、{level: species, concept, taxon, common?}、{level: genus|family, taxon}、{level: unconfirmed|"
          "not_animal}。scope=burst 时同一连拍里模型认成同一种的主体一起改（已有人工结论的跳过）。先用 dry_run 看影响"
          "范围；返回 batch_id 可用 undo_identification 撤销。没看过图像证据时不要确认物种。",
          {"subject_ids": _IDS, "new_subjects": {"type": "array", "items": {"type": "object"}},
           "claim": {"type": "object"}, "scope": {"type": "string", "enum": ["subject", "burst", "encounter"]}, "reason": _STR,
           "dry_run": {"type": "boolean"}}, ("claim",)),
    _tool("undo_identification", "撤销一批物种结论（之后又被人工改过的主体会返回冲突）。", {"batch_id": _STR}, ("batch_id",)),
    _tool("set_species_card", "物种卡片的固定封面（cover_asset_id，null 取消）与隐藏（hidden）；只影响显示。",
          {"concept": _STR, "cover_asset_id": {"type": ["string", "null"]}, "hidden": {"type": "boolean"}}, ("concept",)),
    # contract revision 5B
    _tool("keep_best", "保留此张（keep_use record 或 edit）并淘汰同一连拍的其他帧；已有人工决定（用途或星级）与唯一物种记录的帧"
          "跳过并列在 skipped。先 dry_run 看影响范围，再把返回的 expect 原样带上执行；返回 batch_id 可用 undo_marks 撤销。",
          {"asset_id": _STR, "keep_use": {"type": "string", "enum": ["record", "edit"]}, "dry_run": {"type": "boolean"},
           "expect": {"type": "object"}}, ("asset_id",)),
    _tool("undo_marks", "撤销一批用途/星级（set_marks、keep_best 返回的 batch_id）。", {"batch_id": _STR}, ("batch_id",)),
    _tool("list_encounters", "拍摄段（一次连续观察）：每段的连拍（代表帧 rep）、代表候选 candidates 与物种。task 或 concept "
          "至少给一个；concept 只看该物种的照片。", {"task": _STR, "concept": _STR}),
    _tool("edit_encounters", "人工拆合拍摄段（重扫不会恢复旧分组）：action=split 时给 id 和 at（这张及之后）或 asset_ids；"
          "action=merge 时给 ids（并入第一个）。",
          {"action": {"type": "string", "enum": ["split", "merge"]}, "id": _STR, "at": _STR, "asset_ids": _IDS,
           "ids": _IDS}, ("action",)),
    _tool("preview_rule", "Smart Collection 规则改动的预览：命中数 total、当前成员数 current、增减 added/removed（各至多 200 个 id）；"
          "不保存。", {"id": _STR, "rule": {"type": "object"}}, ("id", "rule")),
    # ---- import (契约修订 5D) ----
    _tool("plan_import", "为任务生成导入计划：按用途复制（保留记录、选入修图，include_pending 默认含待定；淘汰不复制），"
          "每组含 RAW、同拍 JPEG 与 XMP/ACR sidecar，目标 dest_root/YYYY-MM-DD_地点；返回重复、改名、所需与可用空间、"
          "未完成项。只读来源，不复制任何文件。",
          {"task_id": _STR, "dest_root": _STR, "place": _STR, "include_pending": {"type": "boolean"}},
          ("task_id", "dest_root")),
    _tool("run_import", "执行或续跑导入计划（后台复制，逐文件 sha256 校验后入库；已核验的文件不再复制）。"
          "先让用户看过 plan_import 的结果。", {"plan_id": _STR}, ("plan_id",)),
    _tool("get_import", "导入计划的进度与结果：status 为 partial 时 failed 列出失败的文件，不是成功。"
          "species 为完成页物种统计：new（库中首次确认的物种）、added（已有物种补充）、to_confirm（只有模型识别的候选）、"
          "sole（某物种唯一的照片，imported=false 表示不导入、只留在卡片与来源中）。",
          {"plan_id": _STR}, ("plan_id",)),

    # ---- 契约修订 5E: Lightroom handoff and cleanup plans ----
    _tool("lightroom_handoff", "把集合（collection_id）、物种（concept）或选中照片（asset_ids）交给 Lightroom Classic：冻结成员快照、"
          "写出交接文件，插件导入缺少的照片、加入以交接命名的集合、写星级与物种关键词。默认只交选入修图（edit）；uses 可指定用途。"
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
    # ---- 契约修订 7B: import batch collection, handoff retry, presets and export ----
    _tool("import_collection", "把导入批次中已导入的照片存为以批次目录命名的手动 Collection（运行结束时已自动建立；"
          "被删除后可再建）。", {"plan_id": _STR}, ("plan_id",)),
    _tool("retry_handoff", "只重新交接一次交接中失败的、因原片离线或 Lightroom 找不到文件而跳过的照片：新建一次同名交接，"
          "已在 Lightroom 中的照片按身份找回，不重复导入。插件还没返回结果时 409。", {"id": _STR}, ("id",)),
    # ---- end 契约修订 7B ----
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
        return "POST", "/marks", _pick(args, ("asset_ids", "use", "flag", "stars", "reason")), None
    if name == "list_collections":
        return "GET", "/collections", None, {**_pick(args, ("task", "kind")), "deleted": "1" if args.get("deleted") else "0"}
    if name == "review_collection":
        cid, act = args["id"], args["action"]
        body = _pick(args, ("note",)) if act == "complete" else _pick(args, ("resolution",)) if act == "zero" else {}
        return "POST", f"/collections/{_seg(cid)}/{act}", body, None
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
    if name == "keep_best":
        return "POST", "/marks/keep-best", _pick(args, ("asset_id", "keep_use", "dry_run", "expect")), None
    if name == "undo_marks":
        return "POST", "/marks/undo", _pick(args, ("batch_id",)), None
    if name == "list_encounters":
        return "GET", "/encounters", None, _pick(args, ("task", "concept"))
    if name == "edit_encounters":
        if args["action"] == "merge":
            return "POST", "/encounters/merge", {"ids": args["ids"]}, None
        return "POST", f"/encounters/{args['id']}/split", _pick(args, ("at", "asset_ids")), None
    if name == "preview_rule":
        return "POST", f"/collections/{args['id']}/preview-rule", {"rule": args["rule"]}, None
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
    if name == "plan_import":
        return ("POST", f"/tasks/{args['task_id']}/import-plan",
                {"dest_root": args["dest_root"], **_pick(args, ("place", "include_pending"))}, None)
    if name == "run_import":
        return "POST", f"/imports/{args['plan_id']}/run", {}, None
    if name == "get_import":
        return "GET", f"/imports/{args['plan_id']}", None, None
    if name == "retry_handoff":
        return "POST", f"/handoffs/{args['id']}/retry", {}, None
    if name == "import_collection":
        return "POST", f"/imports/{args['plan_id']}/collection", {}, None
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
