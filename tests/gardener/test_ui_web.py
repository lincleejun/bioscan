"""Static checks on the web UI (bioscan/gardener/web): local assets only, no demo data or business state in
localStorage, contract endpoints only, the mandated notices, and JS that parses."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fake_api import create_app

WEB = Path(__file__).resolve().parents[2] / "bioscan" / "gardener" / "web"
FILES = ("index.html", "styles.css", "api.js", "app.js")


def text(name: str) -> str:
    return (WEB / name).read_text(encoding="utf-8")


def all_text() -> str:
    return "\n".join(text(f) for f in FILES)


def test_files_exist():
    assert all((WEB / f).is_file() for f in FILES)


def test_index_references_only_local_assets():
    html = text("index.html")
    refs = re.findall(r'(?:src|href)="([^"]*)"', html)
    assert refs and all(not re.match(r"[a-z]+:|//", r) for r in refs), refs
    assert set(re.findall(r'<script src="([^"]+)"', html)) == {"api.js", "app.js"}
    assert re.findall(r'<link rel="stylesheet" href="([^"]+)"', html) == ["styles.css"]


def test_no_remote_urls_demo_data_or_cdn():
    body = all_text()
    assert not re.search(r"https?://", body)
    for banned in ("unsplash", "data.js", "演示", "示例素材", "示例数据", "模拟扫描", "@import"):
        assert banned not in body, banned


def test_localstorage_only_holds_ui_preferences():
    calls = re.findall(r"localStorage\.(\w+)\(([^,)]*)", all_text())
    assert calls, "prefs should use localStorage"
    for method, arg in calls:
        assert method in ("getItem", "setItem") and arg.strip().startswith("'gardener.ui.' +"), (method, arg)
    keys = set(re.findall(r"prefs\.(?:get|set)\('([\w.]+)", text("app.js")))
    keys = {k.rstrip(".") for k in keys}
    assert keys <= {"route", "grid", "burst", "advance", "last"}, keys


def test_nav_order():
    assert re.findall(r'data-nav="(\w+)"', text("index.html")) == ["library", "collections", "tasks", "settings"]
    labels = re.findall(r'data-nav="\w+">.*?</svg>([^<]+)<', text("index.html"))
    assert labels == ["全部素材", "Collections", "任务", "管理"]


@pytest.mark.parametrize("phrase", [
    "不代表完整 RAW 解码或对焦验证",            # low-res / embedded RAW preview is browse-only
    "它不是 XMP，Lightroom 不能直接读取",        # export XML is Gardener-only
    "只写 <b>一个</b> XML 文件",                 # copy switch off
    "sha256 校验",                              # copy switch on
    "无可靠理由",
    "AI 整组评分偏低 · 全部候选仍可审阅",
    "无法在浏览器播放", "在 Finder 中显示",
    "内容已变化，旧判断已清空", "恢复旧判断",
    "不会被自动 Reject",
    "整理记录全部保留",
    "未启用", "待运行", "运行中", "完成", "失败", "未识别", "不可用",
])
def test_mandated_copy_present(phrase):
    assert phrase in all_text()


def _route_regex(path: str) -> re.Pattern:
    return re.compile("^" + re.sub(r"\\\{[^}]*\\\}", "[^/]+", re.escape(path)) + "$")


def test_ui_calls_only_contract_endpoints():
    """Every api(METHOD, path) in app.js matches a route of the §6 fake (which mirrors the contract)."""
    app = create_app(static=False)
    routes = [(m, _route_regex(r.path)) for r in app.routes if getattr(r, "methods", None) for m in r.methods]
    used = re.findall(r"api\('(GET|POST|PUT|PATCH|DELETE)', [`'](/[^`'?]*)", text("app.js"))
    assert len(used) > 40
    for method, path in used:
        concrete = re.sub(r"\$\{[^}]*\}", "x", path)
        assert any(m == method and rx.match("/api/v1" + concrete) for m, rx in routes), (method, path)


def test_every_write_sends_the_ui_actor():
    assert "'X-Gardener-Actor': 'ui'" in text("api.js")
    assert "fetch(" not in text("app.js")                         # all requests go through api.js


def test_server_text_is_escaped():
    """User/server strings never reach a template unescaped: no `${x.name}`-style interpolation."""
    app = text("app.js")
    raw = re.compile(r"\$\{\w+\.(?:name|title|subtitle|description|path|error|reason|note|label_zh|xml_path)\}")
    # safe sinks: toast() sets textContent, dialog({title}) escapes, tileLabel feeds esc(tileLabel(a))
    bad = [line for line in app.splitlines() if raw.search(line)
           and not re.search(r"toast\(|^\s*title: |const tileLabel", line)]
    assert bad == []
    assert "esc(tileLabel(a))" in app and "esc(o.title)" in app
    assert "textContent = msg" in app                             # toasts never parse HTML


def test_served_by_the_app():
    from fastapi.testclient import TestClient
    c = TestClient(create_app())
    assert "Gardener" in c.get("/").text and c.get("/app.js").status_code == 200


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
@pytest.mark.parametrize("name", ["api.js", "app.js"])
def test_js_parses(name):
    subprocess.run(["node", "--check", str(WEB / name)], check=True, capture_output=True)
