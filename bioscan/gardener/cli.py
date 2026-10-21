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
           "analysis", "date_from", "date_to", "has_gps", "tag", "content", "species", "q", "changed_after")
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
ASSET_COLS = [("id", "id"), ("文件", "name"), ("媒体", "media"), ("拍摄", "taken_at"), ("Flag", "flag"),
              ("星", "stars"), ("AI", "ai.verdict"), ("分", "ai.score"), ("分析", "analysis"), ("在线", "online")]
COL_COLS = [("id", "id"), ("名称", "name"), ("类型", "kind"), ("分组", "group_kind"), ("张数", "size"),
            ("已判断", "judged"), ("Pick", "picks"), ("审阅", "review.state"), ("新增待审", "review.new_since")]
CAP_COLS = [("id", "id"), ("名称", "name"), ("状态", "state"), ("版本", "version"), ("原因", "reason")]


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
        return f"batch_id: {out['batch_id']}\n" + table(out["assets"], [("id", "id"), ("Flag", "flag"),
                                                                          ("星", "stars"), ("版本", "version")])
    if cmd in ("collections",):
        return table(out, COL_COLS)
    if cmd.startswith("collection "):
        return table([out], COL_COLS)
    if cmd == "caps":
        return table(out, CAP_COLS)
    if isinstance(out, dict):
        return pairs(out)
    return _cell(out)


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
        if a.flag is not None:
            body["flag"] = None if a.flag == "none" else a.flag
        if a.stars is not None:
            body["stars"] = a.stars
        if a.reason:
            body["reason"] = a.reason
        if len(body) == 1:
            raise SystemExit("mark: 需要 --flag 或 --stars")
        return "POST", "/marks", body, None
    if c == "undo":
        return "POST", "/marks/undo", {"batch_id": a.batch_id}, None
    if c == "collections":
        return "GET", "/collections", None, {"task": a.task, "kind": a.kind, "deleted": "1" if a.deleted else "0"}
    if c == "collection":
        if sub == "delete":
            return "DELETE", f"/collections/{a.id}", None, None
        if sub == "complete":
            return "POST", f"/collections/{a.id}/complete", {"note": a.note} if a.note else {}, None
        if sub == "zero":
            return "POST", f"/collections/{a.id}/zero", {"resolution": a.resolution} if a.resolution else {}, None
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

    m = s.add_parser("mark", help="人工 Flag / 星级")
    m.add_argument("ids", nargs="+")
    m.add_argument("--flag", choices=("pick", "reject", "none"))
    m.add_argument("--stars", type=int, choices=range(6))
    m.add_argument("--reason")
    u = s.add_parser("undo", help="撤销一批标记")
    u.add_argument("batch_id")

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
