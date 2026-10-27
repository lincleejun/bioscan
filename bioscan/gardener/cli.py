"""`gardener` CLI (docs/gardener/contract.md §8). Every command is one call to the service's HTTP API
(§6); stdlib only (argparse + urllib) so it stays import-light. `serve` and `mcp` import lazily."""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_URL = "http://127.0.0.1:8770"
API = "/api/v1"
FILTERS = ("task", "collection", "source", "media", "flag", "stars_min", "stars", "ai", "judged", "online",
           "analysis", "date_from", "date_to", "has_gps", "tag", "content", "species", "concept", "id_state", "q",
           "changed_after", "use")
EXIT_OK, EXIT_ERROR, EXIT_SERVICE = 0, 1, 2


class ApiError(Exception):
    """The service answered with an error (`status` 400/404/409/503, `body` the §6 error object), or
    could not be reached (`status` 0)."""

    def __init__(self, status: int, body: dict):
        self.status, self.body = status, body
        super().__init__(body.get("error") or str(body))


def call(url: str, method: str, path: str, body: dict | None = None, query: dict | None = None,
         actor: str = "cli", timeout: float | None = 60) -> object:
    """One JSON request to `url` + /api/v1 + `path`; returns the decoded JSON body."""
    q = {k: v for k, v in (query or {}).items() if v not in (None, "")}
    full = url.rstrip("/") + API + path + ("?" + urllib.parse.urlencode(q) if q else "")
    req = urllib.request.Request(full, method=method, headers={"X-Gardener-Actor": actor, "Accept": "application/json"},
                                 data=None if body is None else json.dumps(body).encode())
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8", "replace")
        try:
            err = json.loads(text)
        except ValueError:
            err = {"error": text.strip() or e.reason, "code": "http"}
        raise ApiError(e.code, err if isinstance(err, dict) else {"error": str(err)}) from None
    except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
        raise ApiError(0, {"error": f"无法连接 Gardener 服务 {url}（{getattr(e, 'reason', e)}）；"
                                    "先运行 `gardener serve`", "code": "unreachable"}) from None
    return json.loads(raw) if raw else None


# ---- output ------------------------------------------------------------------

def _cell(v) -> str:
    if v is None:
        return "-"
    if isinstance(v, bool):
        return "是" if v else "否"
    if isinstance(v, float):
        return f"{v:.2f}"
    if isinstance(v, (list, dict)):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def table(rows: list[dict], cols: list[tuple[str, str]]) -> str:
    """`cols`: (header, key); a dotted key reads nested dicts."""
    def get(r, k):
        for p in k.split("."):
            r = r.get(p) if isinstance(r, dict) else None
        return _cell(r)
    data = [[h for h, _ in cols]] + [[get(r, k) for _, k in cols] for r in rows]
    width = [max(len(row[i]) for row in data) for i in range(len(cols))]
    return "\n".join("  ".join(c.ljust(w) for c, w in zip(row, width)).rstrip() for row in data)


def pairs(d: dict) -> str:
    return "\n".join(f"{k}: {_cell(v)}" for k, v in d.items())


TASK_COLS = [("id", "id"), ("标题", "title"), ("扫描", "scan.status"), ("阶段", "scan.phase"),
             ("发现", "scan.discovered"), ("预览", "scan.previewed"), ("失败", "scan.failed"),
             ("已判断", "review.judged"), ("总数", "review.total"), ("归档", "archived")]
RUN_COLS = [("run", "id"), ("#", "seq"), ("类型", "kind"), ("状态", "status"), ("阶段", "phase"),
            ("排队", "queued_at"), ("结束", "finished_at")]
ASSET_COLS = [("id", "id"), ("文件", "name"), ("媒体", "media"), ("拍摄", "taken_at"), ("用途", "use"),
              ("星", "stars"), ("AI", "ai.verdict"), ("分", "ai.score"), ("分析", "analysis"), ("在线", "online")]
COL_COLS = [("id", "id"), ("名称", "name"), ("类型", "kind"), ("分组", "group_kind"), ("张数", "size"),
            ("已判断", "judged"), ("Pick", "picks"), ("审阅", "review.state"), ("新增待审", "review.new_since")]
CAP_COLS = [("id", "id"), ("名称", "name"), ("状态", "state"), ("版本", "version"), ("原因", "reason")]
SPECIES_COLS = [("状态", "state"), ("中文", "zh"), ("名称", "common"), ("学名", "taxon"), ("已确认", "confirmed"),
                ("待确认", "candidates"), ("最近", "last_taken_at"), ("concept", "concept")]
ENC_COLS = [("id", "id"), ("开始", "start"), ("结束", "end"), ("张数", "photos"), ("连拍/单张", "bursts_n"),
            ("物种", "names"), ("代表候选", "candidates")]
SKIP_WHY = {"human": "已有人工决定", "only_record": "唯一物种记录"}
NAME_COLS = [("中文", "zh"), ("名称", "common"), ("学名", "scientific"), ("命中", "matched"), ("库中", "photos"),
             ("concept", "concept")]


def render(cmd: str, out) -> str:
    if cmd in ("task list",):
        return table(out, TASK_COLS)
    if cmd == "task show":
        head = {k: out.get(k) for k in ("id", "title", "subtitle", "preset", "capabilities", "run_mode", "archived")}
        srcs = "\n".join(f"  {'在线' if s['online'] else '离线'}  {s['path']}" for s in out.get("sources", []))
        return (pairs(head) + "\n扫描: " + _cell(out.get("scan")) + "\nReview: " + _cell(out.get("review"))
                + "\n来源:\n" + srcs + "\n运行:\n" + table(out.get("runs", []), RUN_COLS))
    if cmd.startswith("task ") and isinstance(out, dict) and "kind" in out and "seq" in out:
        return table([out], RUN_COLS)
    if cmd.startswith("task "):
        return table([out], TASK_COLS)
    if cmd == "assets":
        return table(out["items"], ASSET_COLS) + f"\n共 {out['total']} 张（从 {out['offset']} 起显示 {len(out['items'])} 张）"
    if cmd == "mark":
        return f"batch_id: {out['batch_id']}\n" + table(out["assets"], [("id", "id"), ("用途", "use"),
                                                                          ("星", "stars"), ("版本", "version")])
    if cmd in ("collections",):
        return table(out, COL_COLS)
    if cmd.startswith("collection "):
        return table([out], COL_COLS)
    if cmd == "caps":
        return table(out, CAP_COLS)
    if cmd == "species":
        rows = [k | {"state": "已确认"} for k in out["species"]] + [k | {"state": "待确认"} for k in out["to_confirm"]]
        un = out["unresolved"]
        return (f"已拍到 {out['confirmed_total']} 种（只计人工确认的）\n" + table(rows, SPECIES_COLS)
                + f"\n待鉴定：属/科 {un['genus_family']} 张，不确定 {un['unconfirmed']} 张"
                + (f"\n待重新确认（名录拆分）：{out['to_reconfirm']} 张" if out.get("to_reconfirm") else ""))
    if cmd == "names":
        return table(out, NAME_COLS)
    if cmd == "encounters":          # contract revision 5B
        rows = [e | {"bursts_n": len(e["bursts"]), "names": "、".join(x.get("zh") or x.get("common") or x["taxon"]
                                                                  for x in e["species"])} for e in out["encounters"]]
        return table(rows, ENC_COLS) + (f"\n未分段 {len(out['unassigned'])} 张（无拍摄时间或尚未分组）"
                                        if out["unassigned"] else "")
    if cmd == "keep-best":
        skipped = "；".join(f"{x['id']}（{SKIP_WHY.get(x['reason'], x['reason'])}）" for x in out["skipped"]) or "无"
        return (f"{'预览（未写入）' if out['dry_run'] else 'batch_id: ' + str(out['batch_id'])}\n保留 {out['keep']['id']}"
                f"（{out['keep']['use']}）\n淘汰 {len(out['reject'])} 张：{' '.join(out['reject']) or '无'}\n跳过：{skipped}")
    if cmd == "import list":
        return table(out, IMPORT_COLS)
    if cmd.startswith("import "):
        return import_text(out)
    if cmd in ("handoff create", "handoff show", "cleanup plan", "cleanup show", "cleanup run"):
        return _render_5e(cmd, out)
    if cmd in ("handoff list", "cleanup list"):
        return table(out, HANDOFF_COLS if cmd == "handoff list" else PLAN_COLS)
    if isinstance(out, dict):
        return pairs(out)
    return _cell(out)


# ---- import (契约修订 5D) ----
IMPORT_COLS = [("id", "id"), ("批次", "seq"), ("状态", "status"), ("目标", "dest_dir"), ("素材", "assets"),
               ("文件", "files"), ("已完成", "progress.files_done"), ("失败", "progress.files_failed")]
USE_ZH = (("edit", "选入修图"), ("record", "保留记录"), ("pending", "待定"), ("reject", "淘汰"))


def import_text(p: dict) -> str:
    u, nc, d, g = p.get("uses", {}), p.get("not_copied", {}), p.get("duplicates", {}), p.get("progress", {})
    lines = [f"导入计划 {p['id']}（批次 {p['seq']}）：{p['status']}" + ("（决定已变化，建议重新生成计划）" if p.get("stale") else ""),
             "用途：" + "，".join(f"{zh} {u.get(k, 0)}" for k, zh in USE_ZH) + f"，共 {u.get('total', 0)} 张",
             f"导入 {p['assets']} 张 · {p['files']} 个文件（含 sidecar {p['sidecars']}）· {_bytes(p['bytes'])}",
             f"目标：{p['dest_dir']}",
             f"需要 {_bytes(p['needed_bytes'])} · 可用 {_bytes(p['free_bytes'])}" + ("" if p["enough_space"] else " · 空间不足"),
             f"精确重复：目标已有 {d.get('identical', 0)} · 已导入过 {d.get('reuse', 0)} · 同内容已在库中 {d.get('duplicate', 0)}"
             f"；同名改为批次后缀 {p.get('renamed', {}).get('files', 0)} 个文件",
             f"不复制：淘汰 {nc.get('reject', 0)} · 待定 {nc.get('pending', 0)}（仍仅在来源中）· 同一拍摄的其他版本 {nc.get('versions', 0)}",
             f"进度：{g.get('files_done', 0)}/{g.get('files_total', 0)} 个文件，{g.get('assets_done', 0)}/"
             f"{g.get('assets_total', 0)} 张已入库，失败 {g.get('files_failed', 0)}"]
    lines += [f"  失败 {f['path']}：{f['error']}" for f in p.get("failed", [])]
    return "\n".join(lines)


def _bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"

# ---- 契约修订 5E: Lightroom handoff and cleanup plans ----
HANDOFF_COLS = [("id", "id"), ("名称", "name"), ("状态", "status"), ("成功", "counts.ok"), ("跳过", "counts.skipped"),
                ("失败", "counts.failed"), ("待返回", "counts.pending"), ("冲突", "counts.conflicts"),
                ("创建", "created_at")]
PLAN_COLS = [("id", "id"), ("状态", "status"), ("淘汰", "summary.rejected"), ("可清理", "summary.ready"),
             ("离线", "summary.offline"), ("文件", "summary.files"), ("字节", "summary.bytes"), ("创建", "created_at")]
HANDOFF_STATUS = {"written": "已写出交接文件，等待 Lightroom 插件返回结果（Lightroom 未运行时，打开它或用 Library > "
                             "Plug-in Extras > bioscan: Apply Gardener handoffs）",
                  "applied": "全部照片已在 Lightroom 中处理", "partial": "部分照片未成功，见下表", "failed": "没有照片成功"}


def _render_5e(cmd: str, out: dict) -> str:
    if cmd.startswith("handoff"):
        rows = [i | {"why": i["reason"] or ",".join(i["conflicts"])} for i in out["items"]]
        return (table([out], HANDOFF_COLS) + "\n" + HANDOFF_STATUS.get(out["status"], out["status"])
                + f"\n交接文件: {out['doc_path']}\n" + table(rows, [("asset", "asset_id"), ("文件", "name"),
                                                               ("结果", "status"), ("原因/冲突", "why")]))
    sm = out["summary"]
    head = (f"计划 {out['id']}（{out['status']}）：淘汰 {sm['rejected']} 张，可清理 {sm['ready']} 张、{sm['files']} 个文件、"
            f"{sm['bytes']} 字节；离线跳过 {sm['offline']} 张")
    last = "".join(f"\n  ! {s.get('zh') or s.get('common') or s['taxon']}（{s['taxon']}）：清理后这个物种没有照片了"
                   for s in sm["last_species"])
    rep = out.get("report")
    done = "" if not rep else (f"\n已移到废纸篓 {rep['trashed_files']} 个文件、{rep['trashed_bytes']} 字节（可恢复，未释放空间）；"
                               f"失败 {len(rep['failed'])}，跳过 {len(rep['skipped'])} 张")
    files = [f | {"asset": i["asset_id"]} for i in out["items"] for f in i["files"]]
    return (head + ("\n最后的物种照片：" + last if last else "") + done + "\n"
            + table(files, [("asset", "asset"), ("文件", "path"), ("类型", "role"), ("字节", "size"), ("结果", "result"),
                            ("原因", "reason")]) + "\n" + out["note"])


def _scope_5e(a) -> dict:
    if a.collection_id:
        return {"collection_id": a.collection_id}
    if a.concept:
        return {"concept": a.concept}
    if a.assets:
        return {"asset_ids": a.assets}
    return {}


# ---- commands ----------------------------------------------------------------

def _abs(paths: list[str]) -> list[str]:
    return [os.path.abspath(os.path.expanduser(p)) for p in paths]


def _filters(a) -> dict:
    return {k: getattr(a, k) for k in FILTERS if getattr(a, k, None) not in (None, "")}


def request(a) -> tuple[str, str, dict | None, dict | None]:
    """(method, path, body, query) for the parsed command `a`."""
    c, sub = a.cmd, getattr(a, "sub", None)
    if c == "health":
        return "GET", "/health", None, None
    if c == "caps":
        return ("POST", "/capabilities/check", {}, None) if a.check else ("GET", "/capabilities", None, None)
    if c == "storage":
        return ("POST", "/storage/clear-cache", {}, None) if a.clear_cache else ("GET", "/storage", None, None)
    if c == "task":
        if sub == "create":
            body = {"title": a.title, "subtitle": a.subtitle, "description": a.description,
                    "sources": _abs(a.source), "preset": a.preset, "capabilities": a.cap or None,
                    "run_mode": a.run_mode}
            return "POST", "/tasks", {k: v for k, v in body.items() if v not in (None, "")}, None
        if sub == "list":
            return "GET", "/tasks", None, {"archived": a.archived}
        if sub == "show":
            return "GET", f"/tasks/{a.id}", None, None
        if sub == "append":
            return "POST", f"/tasks/{a.id}/sources", {"paths": _abs(a.paths)}, None
        if sub == "rescan":
            body = {"capabilities": a.cap or None, "asset_ids": a.asset or None}
            return "POST", f"/tasks/{a.id}/rescan", {k: v for k, v in body.items() if v}, None
        if sub == "retry":
            return "POST", f"/tasks/{a.id}/retry", {"asset_ids": a.asset} if a.asset else {}, None
        action = {"verify": "verify-sources"}.get(sub, sub)
        return "POST", f"/tasks/{a.id}/{action}", {}, None
    if c == "assets":
        return "GET", "/assets", None, {**_filters(a), "order": a.order, "limit": a.limit, "offset": a.offset}
    if c == "mark":
        body: dict = {"asset_ids": a.ids}
        if a.use is not None:
            body["use"] = a.use
        if a.flag is not None:
            body["flag"] = None if a.flag == "none" else a.flag
        if a.stars is not None:
            body["stars"] = a.stars
        if a.reason:
            body["reason"] = a.reason
        if len(body) == 1:
            raise SystemExit("mark: 需要 --use、--flag 或 --stars")
        return "POST", "/marks", body, None
    if c == "undo":
        return "POST", "/marks/undo", {"batch_id": a.batch_id}, None
    if c == "species":
        return "GET", "/species", None, {"task": a.task, "hidden": "1" if a.hidden else "0"}
    if c == "names":
        return "GET", "/names", None, {"q": a.q, "limit": a.limit}
    if c == "identify":
        claim = ({"accept": True} if a.accept else {"level": "unconfirmed"} if a.unsure else
                 {"level": "not_animal"} if a.not_animal else
                 {"level": a.level, "taxon": a.taxon, **({"concept": a.concept, "common": a.common}
                                                         if a.level == "species" else {})})
        body = {"subject_ids": a.subjects, "claim": claim,
                "scope": "encounter" if a.encounter else "burst" if a.burst else "subject",
                "dry_run": a.dry_run, **({"reason": a.reason} if a.reason else {})}
        return "POST", "/identifications", body, None
    if c == "identify-undo":
        return "POST", "/identifications/undo", {"batch_id": a.batch_id}, None
    if c == "species-card":
        body = ({"cover_asset_id": None} if a.no_cover else {"cover_asset_id": a.cover} if a.cover else {}) | \
            ({"hidden": a.hide} if a.hide is not None else {})
        if not body:
            raise SystemExit("species-card: 需要 --cover、--no-cover、--hide 或 --show")
        return "PATCH", f"/species/{urllib.parse.quote(a.concept, safe=':')}", body, None
    if c == "keep-best":
        return "POST", "/marks/keep-best", {"asset_id": a.id, "keep_use": "edit" if a.edit else "record",
                                            "dry_run": a.dry_run}, None
    if c == "encounters":
        if not a.task and not a.concept:
            raise SystemExit("encounters: 需要 --task 或 --concept")
        return "GET", "/encounters", None, {"task": a.task, "concept": a.concept}
    if c == "encounter":
        if sub == "merge":
            return "POST", "/encounters/merge", {"ids": a.ids}, None
        body = {"at": a.at} if a.at else {"asset_ids": a.assets}
        return "POST", f"/encounters/{a.id}/split", body, None
    if c == "handoff":
        if sub == "create":
            body = _scope_5e(a) | ({"uses": a.use} if a.use else {}) | ({"name": a.name} if a.name else {})
            if not body:
                raise SystemExit("handoff create: 需要 --collection、--concept 或 --assets")
            return "POST", "/handoffs", body, None
        return ("GET", "/handoffs", None, None) if sub == "list" else ("GET", f"/handoffs/{a.id}", None, None)
    if c == "cleanup":
        if sub == "plan":
            return "POST", "/cleanup-plans", _scope_5e(a), None
        if sub == "run":
            if not a.yes:
                raise SystemExit("cleanup run: 先用 cleanup show 看清计划，再加 --yes 确认移到废纸篓")
            return "POST", f"/cleanup-plans/{a.id}/run", {"confirm": True}, None
        return ("GET", "/cleanup-plans", None, None) if sub == "list" else ("GET", f"/cleanup-plans/{a.id}", None, None)
    if c == "collections":
        return "GET", "/collections", None, {"task": a.task, "kind": a.kind, "deleted": "1" if a.deleted else "0"}
    if c == "collection":
        if sub == "delete":
            return "DELETE", f"/collections/{a.id}", None, None
        if sub == "complete":
            return "POST", f"/collections/{a.id}/complete", {"note": a.note} if a.note else {}, None
        if sub == "zero":
            return "POST", f"/collections/{a.id}/zero", {"resolution": a.resolution} if a.resolution else {}, None
        if sub == "preview-rule":
            try:
                rule = json.loads(a.rule)
            except ValueError:
                raise SystemExit("preview-rule: --rule 不是有效的 JSON") from None
            return "POST", f"/collections/{a.id}/preview-rule", {"rule": rule}, None
        return "POST", f"/collections/{a.id}/{sub}", {}, None
    if c == "export":
        body = {"scope": a.scope, "copy": a.copy, "conflict": a.conflict}
        if a.dest:
            body["dest_dir"] = os.path.abspath(os.path.expanduser(a.dest))
        if a.collection_id:
            body["collection_id"] = a.collection_id
        elif a.assets:
            body["asset_ids"] = a.assets
        else:
            body["rule"] = _filters(a)
        return "POST", "/export", body, None
    if c == "import":
        if sub == "plan":
            body = {"dest_root": os.path.abspath(os.path.expanduser(a.dest)), "include_pending": not a.no_pending}
            return "POST", f"/tasks/{a.task}/import-plan", body | ({"place": a.place} if a.place else {}), None
        if sub == "run":
            return "POST", f"/imports/{a.id}/run", {}, None
        if sub == "show":
            return "GET", f"/imports/{a.id}", None, None
        return "GET", "/imports", None, {"task": a.task}
    raise SystemExit(f"unknown command {c}")


def _add_filters(p: argparse.ArgumentParser, skip: tuple[str, ...] = ()):
    g = p.add_argument_group("筛选（§6.4）")
    for k in FILTERS:
        if k not in skip:
            g.add_argument("--" + k.replace("_", "-"), dest=k)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="gardener", description="Gardener：本地摄影整理与 Review 服务的命令行客户端")
    p.add_argument("--url", default=os.environ.get("GARDENER_URL", DEFAULT_URL),
                   help=f"服务地址（默认 $GARDENER_URL 或 {DEFAULT_URL}）")
    p.add_argument("--json", action="store_true", help="输出服务返回的原始 JSON")
    s = p.add_subparsers(dest="cmd", required=True)

    s.add_parser("serve", help="启动 Gardener 服务（其余参数原样交给服务）", add_help=False)
    s.add_parser("health", help="服务状态")
    s.add_parser("mcp", help="以 stdio MCP 服务运行（转调 HTTP API）")
    c = s.add_parser("caps", help="能力列表与可用性")
    c.add_argument("--check", action="store_true", help="试加载模型并刷新状态（可能耗时）")
    st = s.add_parser("storage", help="数据与缓存位置、大小")
    st.add_argument("--clear-cache", action="store_true", help="清理预览缓存（整理记录保留）")

    t = s.add_parser("task", help="扫描任务").add_subparsers(dest="sub", required=True)
    tc = t.add_parser("create", help="创建任务并加入扫描队列")
    tc.add_argument("--source", action="append", required=True, help="来源目录（可重复）")
    tc.add_argument("--title", required=True)
    tc.add_argument("--subtitle")
    tc.add_argument("--description")
    tc.add_argument("--preset", help="预设 id（quick / photo / wildlife / 自定义）")
    tc.add_argument("--cap", action="append", help="能力 id（可重复，覆盖预设）")
    tc.add_argument("--run-mode", choices=("quiet", "balanced", "full"))
    tl = t.add_parser("list", help="任务列表")
    tl.add_argument("--archived", default="0", choices=("0", "1", "all"))
    for name, hlp in (("show", "任务详情与运行历史"), ("pause", "暂停"), ("resume", "继续"), ("stop", "停止并保留结果"),
                      ("archive", "归档"), ("unarchive", "取消归档"), ("verify", "重新核对来源是否在线")):
        t.add_parser(name, help=hlp).add_argument("id")
    ta = t.add_parser("append", help="追加来源目录")
    ta.add_argument("id")
    ta.add_argument("paths", nargs="+")
    tr = t.add_parser("rescan", help="重新分析（可限定能力与素材，用于小批试跑）")
    tr.add_argument("id")
    tr.add_argument("--cap", action="append")
    tr.add_argument("--asset", action="append")
    ty = t.add_parser("retry", help="重试失败项")
    ty.add_argument("id")
    ty.add_argument("--asset", action="append")

    a = s.add_parser("assets", help="查询素材")
    _add_filters(a)
    a.add_argument("--order", default="time", choices=("time", "path", "score"))
    a.add_argument("--limit", type=int, default=200)
    a.add_argument("--offset", type=int, default=0)

    m = s.add_parser("mark", help="人工用途 / 星级（--flag 是旧写法：pick = 保留记录）")
    m.add_argument("ids", nargs="+")
    m.add_argument("--use", choices=("record", "edit", "pending", "reject"),
                   help="保留记录 / 选入修图 / 待定 / 淘汰")
    m.add_argument("--flag", choices=("pick", "reject", "none"))
    m.add_argument("--stars", type=int, choices=range(6))
    m.add_argument("--reason")
    u = s.add_parser("undo", help="撤销一批标记")
    u.add_argument("batch_id")

    sp = s.add_parser("species", help="物种卡片：已拍到 N 种（人工确认）、待确认、待鉴定")
    sp.add_argument("--task", help="只看一个任务")
    sp.add_argument("--hidden", action="store_true", help="包括已隐藏的卡片")
    nm = s.add_parser("names", help="按学名、英文名、中文名或旧名查物种（得到 identify 需要的 concept）")
    nm.add_argument("q")
    nm.add_argument("--limit", type=int, default=20)
    idf = s.add_parser("identify", help="人工确认或纠正主体的物种（主体 id 见 assets 详情的 subjects）")
    idf.add_argument("subjects", nargs="+")
    how = idf.add_mutually_exclusive_group(required=True)
    how.add_argument("--accept", action="store_true", help="采纳模型当前的结论")
    how.add_argument("--level", choices=("species", "genus", "family"), help="与 --taxon 一起给出")
    how.add_argument("--unsure", action="store_true", help="标为不确定")
    how.add_argument("--not-animal", action="store_true", help="不是动物")
    idf.add_argument("--taxon", help="学名（种）或属/科名")
    idf.add_argument("--concept", help="种的 concept（gardener names 查得）")
    idf.add_argument("--common")
    idf.add_argument("--burst", action="store_true", help="同一连拍里模型认成同一种的一起改")
    idf.add_argument("--encounter", action="store_true", help="同一拍摄段里模型认成同一种的一起改")
    idf.add_argument("--dry-run", action="store_true", help="只报告影响范围，不写入")
    idf.add_argument("--reason")
    iu = s.add_parser("identify-undo", help="撤销一批物种结论")
    iu.add_argument("batch_id")
    sc = s.add_parser("species-card", help="物种卡片的封面与隐藏（只影响显示）")
    sc.add_argument("concept")
    sc.add_argument("--cover", help="封面照片的 asset id")
    sc.add_argument("--no-cover", action="store_true", help="取消固定封面")
    sc.add_argument("--hide", action="store_const", const=True, default=None)
    sc.add_argument("--show", dest="hide", action="store_const", const=False)

    kb = s.add_parser("keep-best", help="保留此张并淘汰同一连拍的其他帧（跳过人工保护与唯一物种记录）")
    kb.add_argument("id")
    kb.add_argument("--edit", action="store_true", help="保留的这张选入修图（默认保留记录）")
    kb.add_argument("--dry-run", action="store_true", help="只报告影响范围，不写入")
    en = s.add_parser("encounters", help="拍摄段：连拍与代表候选")
    en.add_argument("--task")
    en.add_argument("--concept", help="只看一个物种的照片")
    eo = s.add_parser("encounter", help="拆分或合并拍摄段（重扫不会恢复旧分组）").add_subparsers(dest="sub", required=True)
    es = eo.add_parser("split", help="拆出一个新拍摄段")
    es.add_argument("id")
    how_split = es.add_mutually_exclusive_group(required=True)
    how_split.add_argument("--at", help="这张及之后的照片拆为新的一段")
    how_split.add_argument("--assets", nargs="+", help="恰好这些照片拆为新的一段")
    em = eo.add_parser("merge", help="合并为第一个拍摄段")
    em.add_argument("ids", nargs="+")

    cs = s.add_parser("collections", help="集合列表")
    cs.add_argument("--task")
    cs.add_argument("--kind", choices=("manual", "smart"))
    cs.add_argument("--deleted", action="store_true", help="只看已删除")
    co = s.add_parser("collection", help="集合审阅操作").add_subparsers(dest="sub", required=True)
    cc = co.add_parser("complete", help="完成本组（未处理项保持原状）")
    cc.add_argument("id")
    cc.add_argument("--note")
    cz = co.add_parser("zero", help="本组不留")
    cz.add_argument("id")
    cz.add_argument("--resolution", choices=("keep_picks", "clear_picks"), help="本组含 Pick 时必须选择")
    cp = co.add_parser("preview-rule", help="Smart 规则改动的命中数与增减（不保存）")
    cp.add_argument("id")
    cp.add_argument("--rule", required=True, help="JSON，键同 assets 筛选")
    for name, hlp in (("reopen", "重新打开"), ("delete", "删除集合（可恢复）"), ("restore", "恢复已删除集合")):
        co.add_parser(name, help=hlp).add_argument("id")

    e = s.add_parser("export", help="导出 Gardener XML（不是 XMP）；--copy 复制原片并 sha256 校验")
    e.add_argument("--collection", dest="collection_id")
    e.add_argument("--assets", nargs="+")
    e.add_argument("--scope", default="picked", choices=("picked", "all", "selected"))
    e.add_argument("--dest", help="目标目录（默认服务数据目录 exports/）")
    e.add_argument("--copy", action="store_true", help="复制原片（默认只写一个 XML）")
    e.add_argument("--conflict", default="rename", choices=("rename", "skip", "fail"))
    _add_filters(e, skip=("collection",))

    im = s.add_parser("import", help="正式导入：生成计划、执行（复制并校验，可续跑）、查看结果").add_subparsers(dest="sub", required=True)
    ip = im.add_parser("plan", help="为任务生成导入计划（默认含待定，不含淘汰）")
    ip.add_argument("task")
    ip.add_argument("--dest", required=True, help="目标根目录；其下建 YYYY-MM-DD_地点")
    ip.add_argument("--place", help="地点（缺省为“未标注地点”）")
    ip.add_argument("--no-pending", action="store_true", help="待定照片不导入（仍仅在来源中）")
    im.add_parser("run", help="执行或续跑导入计划").add_argument("id")
    im.add_parser("show", help="导入计划与进度、结果").add_argument("id")
    im.add_parser("list", help="导入计划列表").add_argument("--task")

    # ---- 契约修订 5E ----
    def scope_args(x):
        x.add_argument("--collection", dest="collection_id")
        x.add_argument("--concept", help="物种 concept（gardener names 查得）")
        x.add_argument("--assets", nargs="+")
    ho = s.add_parser("handoff", help="交给 Lightroom Classic（插件读取交接文件并返回逐张结果）").add_subparsers(
        dest="sub", required=True)
    hc = ho.add_parser("create", help="冻结集合、物种或选中照片的成员并写出交接文件（默认只交 Pick）")
    scope_args(hc)
    hc.add_argument("--use", action="append", help="只交这些用途（record / pending / ...；可重复）")
    hc.add_argument("--name", help="Lightroom 里的集合名（默认取集合或物种名）")
    ho.add_parser("show", help="逐张结果（成功 / 跳过 / 失败、字段冲突）").add_argument("id")
    ho.add_parser("list", help="交接历史")
    cl = s.add_parser("cleanup", help="清理计划：淘汰的照片移到废纸篓（可恢复）").add_subparsers(dest="sub", required=True)
    scope_args(cl.add_parser("plan", help="列出范围内淘汰照片的全部文件、容量与物种影响（默认全库）"))
    cl.add_parser("show", help="计划与执行结果").add_argument("id")
    cl.add_parser("list", help="清理计划历史")
    cr = cl.add_parser("run", help="重新核对后把文件移到废纸篓")
    cr.add_argument("id")
    cr.add_argument("--yes", action="store_true", help="确认执行")
    return p


def main(argv: list[str] | None = None) -> int:
    p = parser()
    a, rest = p.parse_known_args(argv)
    if a.cmd == "serve":
        from bioscan.gardener.server import main as serve_main
        return serve_main(rest) or 0
    if rest:
        p.error("unrecognized arguments: " + " ".join(rest))
    if a.cmd == "mcp":
        from bioscan.gardener import mcp
        return mcp.serve(sys.stdin, sys.stdout, a.url)
    method, path, body, query = request(a)
    try:
        out = call(a.url, method, path, body, query, timeout=None if a.cmd == "caps" else 60)
    except ApiError as e:
        print(f"错误：{e}", file=sys.stderr)
        extra = {k: v for k, v in e.body.items() if k not in ("error", "code")}
        if extra:
            print(json.dumps(extra, ensure_ascii=False, indent=2), file=sys.stderr)
        return EXIT_SERVICE if e.status in (0, 503) else EXIT_ERROR
    cmd = a.cmd + (f" {a.sub}" if getattr(a, "sub", None) else "")
    print(json.dumps(out, ensure_ascii=False, indent=2) if a.json else render(cmd, out))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
