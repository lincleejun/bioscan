"""Static checks on the web UI (gardener/workbench/web): local assets only, no demo data or business state in
localStorage, contract endpoints only, the mandated notices, and JS that parses."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fake_api import create_app

WEB = Path(__file__).resolve().parents[2] / "gardener" / "workbench" / "web"
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
    assert keys <= {"route", "grid", "burst", "advance", "last", "idscope", "enc", "cmpsync", "cploupe", "spview", "cands", "picks", "cpmode", "mode", "panel", "libmore", "addkind"}, keys


def test_nav_order():
    # ADR 0004: the library first, nouns only; 待整理 shows only while a batch is there; tasks live under 管理
    assert re.findall(r'data-nav="([\w-]+)"', text("index.html")) == ["library", "fs", "species", "collections", "inbox", "settings"]
    labels = re.findall(r'data-nav="[\w-]+"[^>]*>.*?</svg>([^<]+)<', text("index.html"))
    assert labels == ["图库", "文件夹", "物种", "集合", "待整理", "管理"]
    assert "prefs.get('route', '#/library')" in text("app.js")
    assert 'href="#/tasks">扫描任务' in text("app.js") and 'href="#/new" class="nav-add"' in text("index.html")   # both still reachable
    assert '.nav a[data-nav="inbox"]:not([aria-current="page"]):not(:has(.nav-has, .nav-n)) { display: none; }' in text("styles.css")
    # one language: no English product nouns in what a user reads (comments aside)
    code = "\n".join(line.partition("   // ")[0] for line in text("app.js").split("\n") if not line.lstrip().startswith("//"))
    assert not re.findall(r"(?<![A-Za-z])(?:Review|Collections?|Smart|Manual)(?![A-Za-z])", code)


@pytest.mark.parametrize("phrase", [
    "不代表完整 RAW 解码或对焦验证",            # low-res / embedded RAW preview is browse-only
    "它不是 XMP，Lightroom 不能直接读取",        # export XML is Gardener-only
    "只写 <b>一个</b> XML 文件",                 # copy switch off
    "sha256 校验",                              # copy switch on
    "无可靠理由",
    "AI 整组评分偏低 · 全部候选仍可审阅",
    "无法在浏览器播放", "在 Finder 中显示",
    "内容已变化，旧判断已清空", "恢复旧判断",
    "不会被自动淘汰",
    "整理记录全部保留",
    "未启用", "待运行", "运行中", "完成", "失败", "未识别", "不可用",
    "快速预览", "原像素细节加载中", "原像素细节可用", "不是原像素",   # PRD §08 第三级 / AC06 (revision 5C)
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


def test_detail_images_use_the_contract_route_and_never_say_100_percent():
    """Revision 5C: original-pixel crops come from GET /assets/{id}/detail through apiImage, and no
    preview zoom is called "100%"."""
    from fastapi.testclient import TestClient
    js = text("app.js")
    used = re.findall(r"apiImage\([`'](/[^`'?]*)", js)
    assert used == ["/assets/${encodeURIComponent(v.id)}/detail"]
    c = TestClient(create_app(static=False))
    aid = c.get("/api/v1/assets").json()["items"][0]["id"]
    r = c.get(f"/api/v1/assets/{aid}/detail", params={"xyxy": "0,0,1,1", "max": 512})
    assert r.status_code == 200 and r.headers["x-detail-source"] == "original"
    assert "100%" not in js and "100 %" not in js


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


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed: the app.js view-function tests need node 22+")
def test_js_view_functions():
    """Runs tests/workbench/web/*.test.js (node --test) against the pure view functions of app.js."""
    r = subprocess.run(["node", "--test", str(Path(__file__).parent / "web" / "*.test.js")], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_restore_cleared_judgment_flow():
    """Recheck B: a judgment the service cleared (content changed / zero clear_picks) is restorable from the
    detail panel by undoing its batch; the fake mirrors the shapes the panel reads."""
    from fastapi.testclient import TestClient
    c = TestClient(create_app(static=False))
    item = next(a for a in c.get("/api/v1/assets", params={"q": "IMG_0003"}).json()["items"])
    hist = c.get(f"/api/v1/assets/{item['id']}").json()["mark_history"]
    cleared = [h for h in hist if h["actor"] == "system" and h["reason"] == "content_changed"]
    assert cleared and all(h["batch_id"] for h in cleared)
    rows = c.get("/api/v1/marks/history", params={"asset_id": item["id"], "actor": "system"}).json()
    assert {r["batch_id"] for r in rows} == {cleared[0]["batch_id"]}
    assert c.post("/api/v1/marks/undo", json={"batch_id": cleared[0]["batch_id"]}).json() == {"undone": 1}
    after = c.get(f"/api/v1/assets/{item['id']}").json()
    assert (after["flag"], after["stars"]) == ("pick", 3)
    app = text("app.js")
    assert "h.actor === 'system' && h.reason === 'content_changed'" in app and "startsWith('zero:')" in app
    assert "'restore-batch': el => undoBatch(el.dataset.id" in app
    assert "api('POST', '/marks/undo', { batch_id: batch })" in app


def test_poll_cursor_and_order():
    """X7: the cursor is the max updated_at received; task and collection views page in discovery order."""
    app = text("app.js")
    assert "if (a.updated_at > ws.cursor) ws.cursor = a.updated_at;" in app
    assert "changed_after: ws.cursor" in app
    assert "makeWs('preview', { task: t.id }, 'discovered'" in app
    assert "makeWs('review', { collection: c.id }, 'discovered'" in app


def test_review_flow_ui_contract_5b():
    """Keys set the four uses (Lightroom keys since revision 9), keep-best previews before it writes, encounters
    group the review grid, and a changed Smart rule is previewed before it is saved."""
    app = text("app.js")
    assert "const KEY_USE = { p: 'edit', r: 'record', u: 'pending', x: 'reject' };" in app
    assert "markKey({ flag" not in app and "{ use: v }" in app   # the UI writes uses only
    assert "api('POST', '/marks/keep-best', { asset_id: id, dry_run: true })" in app
    # revision 14: the grid still reads /encounters, but as groups; a folded encounter no longer hides photos
    assert "api('GET', '/encounters', null, ws.encQuery)" in app and "e.candidates.includes(a.id)" not in app
    assert "dd.dataset.previewed !== ruleKey" in app and "/preview-rule`" in app
    from fastapi.testclient import TestClient
    c = TestClient(create_app(static=False))
    t = c.get("/api/v1/tasks").json()[-1]
    enc = c.get("/api/v1/encounters", params={"task": t["id"]}).json()["encounters"][0]
    assert {"id", "start", "end", "photos", "asset_ids", "bursts", "candidates", "species"} <= set(enc)


def test_review_polish_ui_contract_7a():
    """Identify scope is a three-way choice remembered as idscope, a burst can be split from the current frame,
    encounter split/merge toasts offer undo, keep-best names the other collections, species cards show encounters."""
    app = text("app.js")
    assert "data-pref=\"idscope\"" in app and "['encounter', `同一拍摄段" in app and "idburst" not in app
    assert "api('POST', '/bursts/split', { at: el.dataset.id })" in app and "从这张起拆为新连拍" in app
    # revision 14: 拍摄段 is no review unit in any page, so the grid has no encounter split / merge / undo and
    # species cards count photos only (the API routes stay; the identify scope 同一拍摄段 above stays)
    assert "/encounters/undo" not in app and "encUndo" not in app and "'enc-split'" not in app
    assert "要淘汰的帧也在其他集合" in app and "个拍摄段" not in app
    assert "Pick" not in app.replace("cmpPick", "").replace("cmp-pick", "")
    from fastapi.testclient import TestClient
    c = TestClient(create_app(static=False))
    assert c.post("/api/v1/marks/keep-best", json={"asset_id": "a_0006", "dry_run": True}).json()["collections"] == []
    mine = c.post("/api/v1/collections", json={"name": "行程精选", "asset_ids": ["a_0007"]}).json()   # auto groups never
    kb = c.post("/api/v1/marks/keep-best", json={"asset_id": "a_0006", "dry_run": True}).json()
    assert kb["collections"] == [{"id": mine["id"], "name": "行程精选", "asset_ids": ["a_0007"]}]
    assert c.get("/api/v1/assets/a_0006").json()["encounters"][0]["photos"] > 1


def test_task_preview_is_a_lightroom_style_library():
    """Revision 9: the task Preview is a Library (grid / loupe / compare / full screen on G E C F, P X U and 0-5 on
    the photo), laid out flat by capture time with no encounter grouping, and AI 初选 comes from the shortlist route."""
    app = text("app.js")
    assert "lib: true, collapse: false" in app
    assert "const LIB_KEY = { g: 'grid', e: 'loupe', c: 'compare', f: 'full' };" in app
    # loupe marks only the photo on screen, as in Lightroom (#131); a poll keeps the photo being looked at;
    # auto-advance only from the photo the key was pressed on; P/R/U/X in compare mark the highlighted pane
    assert "const one = lb || ws.mode === 'loupe';" in app
    assert "const targets = one ? (ws.current ? [ws.current] : []) : selTargets();" in app
    assert "(keep && id === ws.current) || visible(ws, A(id))" in app and "if (ws.lf.ai) recompute(true);" in app
    assert "ui.ws === ws && ws.current === from) move(1);" in app
    assert "doMark([panes[active].id], { use: u }, useZh(u));" in app
    assert "doMark([panes[active].id], { stars: Number(k) }" in app       # 0-5 in compare rate the clicked pane
    assert "let active = Math.max(0, ids.indexOf(ws && ws.current));" in app
    assert "api('GET', `/tasks/${encodeURIComponent(ws.taskId)}/shortlist`)" in app
    vtask = app[app.index("async function vTask"):app.index("const KIND = {")]
    assert "encQuery" not in vtask                              # no forced grouping on the task preview
    from fastapi.testclient import TestClient
    c = TestClient(create_app(static=False))
    t = c.get("/api/v1/tasks").json()[0]
    s = c.get(f"/api/v1/tasks/{t['id']}/shortlist").json()
    assert s["size"] == min(s["scored"], max(-(-s["scored"] // 10), 20)) and len(s["asset_ids"]) == s["size"]


def test_library_scales_to_thousands_of_photos():
    """#131 P1: the saved position survives past the first page, the grid only renders visible tiles,
    and timed polls pause while the tab is hidden and run once when it comes back."""
    app, css = text("app.js"), text("styles.css")
    # keep paging until the saved photo is loaded; select nothing (so nothing overwrites it) meanwhile
    assert "const waitStart = ws => !ws.current && !!ws.start && !ws.allSet.has(ws.start) && !ws.loadedAll;" in app
    assert "else if (!waitStart(ws)) setCurrent(" in app and "if (waitStart(ws) || (s && " in app
    # cheapest virtualization: the browser skips layout and paint of off-screen tiles
    tile_wrap = re.search(r"^\.tile-wrap \{[^}]*\}", css, re.M).group(0)
    assert "content-visibility: auto" in tile_wrap and "contain-intrinsic-size: auto" in tile_wrap
    # every timer goes through `every`, which skips hidden tabs; visibilitychange polls at once
    assert "setInterval(() => { if (!document.hidden) fn(); }, ms)" in app
    assert app.count("setInterval(") == 1
    assert "every(2000, pollNow);" in app and "every(30000, disk);" in app and "every(2000, async () => {" in app
    assert "addEventListener('visibilitychange', () => { if (!document.hidden) pollers.forEach(fn => fn()); })" in app


def test_every_photo_view_has_the_same_two_layouts():
    """Revision 11: every ws view (task Preview, collection Review, species, All photos) is built by one wsHTML with
    grid (thumbnails only, no side panel) and loupe (big image + filmstrip, info panel beside it that Tab / I hide);
    the mode is remembered per view; other screens open their photos in that loupe instead of their own viewers."""
    app, css = text("app.js"), text("styles.css")
    assert app.count("function wsHTML()") == 1 and "libHTML" not in app
    assert app.count("${wsHTML()}") == 4                                          # preview, review, species, library
    assert "{ encQuery: scope, view: 'species' }" in app and "{ view: 'library' }" in app
    assert "mode: prefs.get('mode.' + view, 'grid')" in app and "prefs.set('mode.' + ws.view, v);" in app
    assert ".ws.mode-grid .inspector, .ws.panel-off .inspector { display: none; }" in css
    assert "if (ws.mode !== 'loupe' || prefs.get('panel', '1') !== '1') { el.innerHTML = '';" in app   # no detail fetch, no hidden reason
    assert "togglePanel();" in app and "k === 'Tab' && !e.shiftKey && ws.mode === 'loupe'" in app
    # the old inspector photo, subject zoom and the collection's own lightbox are gone
    for gone in ("insp-canvas", "canvasBarHTML", "ui.zoom", "applyZoom", "cdOpen", "cdv"):
        assert gone not in app and gone not in css, gone
    # opening a photo from another screen lands in the shared loupe at that photo, even inside a collapsed group
    assert "open ? { start: open, open, mode: 'loupe' } : {}" in app and "if (a.id === ws.open) return true;" in app
    # one left: the import page's tiles. The Review overview opens a group in the compare page (revision 13)
    assert app.count('data-action="open-photo"') == 1 and "'cd-open': el => { ui.open = el.dataset.id;" in app
    # a missed animal is drawn on the loupe's big image; an encounter poll keeps the photo being looked at
    assert "$('#lib-loupe')" in app and "if (ui.ws === ws) recompute(had);" in app


def test_all_photos_filters_are_compact_and_side_lists_scroll_on_their_own():
    """Owner feedback 2026-11-12: 全部素材 shows one row (search, task, use, stars, sort) and folds the rest behind
    更多筛选 (n); every active filter is a removable chip; the search box writes the existing `species` or `q`
    filter, never a key of its own. The Review rail and the scan groups scroll apart from the page."""
    app, css = text("app.js"), text("styles.css")
    assert "const LIB_MAIN = ['task', 'use', 'stars_min', 'order'];" in app
    assert 'data-action="lib-more" aria-controls="lib-more"' in app and "prefs.set('libmore'" in app
    assert 'data-action="lib-unset"' in app and "'lib-unset': el => { delete ui.lib[el.dataset.k]; route(true); }" in app
    assert '<select class="input sm" data-lib-sfield' in app                     # no name: never reaches ui.lib
    assert "t.matches('[data-lib-sfield]')" in app.split("t.form.id === 'lib-filters'")[0]
    assert ".page.fr, .page.sc { overflow: hidden; }" in css
    assert ".fr-rail, .fr-center, .fr-rules, .sc .fl-main, .sc .fl-panel { min-height: 0; overflow: auto; }" in css

def test_local_folder_browser_is_read_only_and_reuses_the_library_layout():
    """契约修订 12: 本地目录 lists folders through /fs/*, says plainly that nothing is imported, has no marks,
    and loads thumbnails lazily in the library's grid / loupe layout."""
    js, css = text("app.js"), text("styles.css")
    fs = js[js.index("契约修订 12：本地目录"):js.index("契约修订 12 结束")]
    assert set(re.findall(r"api\('(\w+)', '(/fs/[\w/]+)'", fs)) == {("GET", "/fs/roots"), ("GET", "/fs/list")}
    assert "/fs/thumb?" in fs and 'loading="lazy"' in fs
    assert 'data-action="fs-add" data-kind="browse"' in fs and "只读取、不复制、不移动" in fs   # adding is one click, and says so
    assert "ws lib fs mode-" in fs and "lib-loupe" in fs and "grid-scroll" in fs      # the library's layout classes
    for banned in ("/marks", "doMark", "ui.ws =", "starsHTML", "flagSeg"):           # no marks here
        assert banned not in fs, banned
    assert "{ g: 'grid', e: 'loupe', f: 'full' }" in fs and "ArrowRight" in fs
    assert "fs-task" in fs and "#/task/" in fs                                       # a covered folder links to its task
    assert "case 'fs':" in js and "Object.assign(VIEWS, { fs: vFs })" in fs
    assert ".ws.lib.fs .lib-side { width: 280px; }" in css and ".fs-tree" in css


def test_review_is_by_group_with_merge_split_move_and_a_one_key_keep():
    """Contract revision 13: the Review overview and the compare page work on groups (bursts), a group merges into
    the one before it, a group or a whole species moves to another species, and Enter keeps one frame."""
    app = text("app.js")
    assert "function groupsOf(enc)" in app and "'gr-merge'" in app and "'cp-merge'" in app and "'cp-split'" in app
    assert "api('POST', '/bursts/merge'" in app and "api('POST', '/identifications/move'" in app
    assert "'cp-quick'" in app and "k === 'enter'" in app and "k === ']'" in app and "k === 'm'" in app
    assert "'task-regroup'" in app and "零星识别" in app and "比较当前拍摄段" not in app
    assert ".gr-grid" in text("styles.css")


def test_z_toggles_one_to_one_the_loupe_keeps_its_zoom_and_browsing_does_not_ask_for_original_pixels():
    """Contract revision 13 (viewer): Z is fit <-> 1:1 in the single view and full screen; a zoomed loupe stays
    zoomed on the next photo; at fit-to-window, or with the original offline, no /detail request is made and the
    label is neutral, not the red failure."""
    app = text("app.js")
    assert "dvDo(zv, zv.z > 1.02 ? 'fit' : 'one')" in app and "ws.loupe = dvMount(st, a, keep, null)" in app
    assert "v.z <= 1.02 || shown <= v.pw * 1.05 || off" in app and "offline: '原片离线 · 仅预览'" in app


def test_same_subject_runs_are_framed_together_and_a_tile_shows_no_pill_without_a_suggestion():
    app = text("app.js")
    assert "function groupBlocksHTML" in app and "'run-merge'" in app and "同一对象" in app
    assert "pill('无 AI 建议'" not in app and "if (!a.ai || (a.ai.verdict === 'none' && a.ai.score == null)) return '';" in app
    assert "原片离线 · 分析未完成" in app and ".gr-run" in text("styles.css")


def test_the_photo_grid_shows_groups_and_frames_one_subject_like_review():
    """Revision 14: 所有照片 (one task, capture order) and the species page show groups and frame consecutive groups of
    one subject with Review's own groupsOf / groupBlocksHTML; another order or any filter shows no frame."""
    app, css = text("app.js"), text("styles.css")
    assert app.count("function groupsOf(") == 1 and app.count("function groupBlocksHTML(") == 1
    assert "const groups = groupsOf(r), sig = JSON.stringify(groups.map(g => [g.id, g.asset_ids, g.run]));" in app
    assert "return groupBlocksHTML(gs, g => g.asset_ids.filter(id => shown.has(id)).map(id => tile(A(id), ws)).join(''));" in app
    assert "ws.order === 'time' && ws.filter === 'all' && (!ws.lf || (ws.lf.flag === 'all' && !ws.lf.stars && !ws.lf.ai))" in app
    assert "Object.keys(rule).join() === 'task' ? { view: 'library', encQuery: { task: rule.task } }" in app
    assert "groupMerge(el.dataset.task, ids[0], ids[1], ids)" in app and "groupMerge(fr.t.id, ids[0]" not in app
    for gone in ("不分段", "encHeadHTML", "'enc-mode'", "'enc-toggle'", "从这张起拆为新拍摄段", "收起的拍摄段"):
        assert gone not in app, gone
    assert "enc-head" not in css
    # the frame spans the grid's row and keeps its columns; in the loupe's filmstrip it takes no space
    assert ".grid > .gr-run { grid-column: 1 / -1; display: grid; grid-template-columns: subgrid;" in css
    assert ".ws.mode-loupe .grid > .gr-run { display: contents; }" in css and ".ws.mode-loupe .gr-run-h { display: none; }" in css


def test_species_progress_comes_from_the_server_and_shows_on_the_rail_and_the_species_cards():
    """Revision 16: a group's state is the server's kept / rejected over all its frames (not the frames this browser
    loaded); the Review rail and the task's species cards show decided / total groups and a done state; after a mark
    the Review overview fetches the groups and the progress again."""
    app, css = text("app.js"), text("styles.css")
    assert "const kept = g.kept || 0, out = g.rejected || 0;" in app
    assert "a.use === 'record' || a.use === 'edit').length" not in app             # no counting over the cache
    assert app.count("${spProgHTML(k)}") == 2                                       # the rail row and the species card
    assert "${k.decided} / ${k.groups} 组" in app and "已完成 · " in app
    assert "if (ui.view === 'flow-review') await frReload();" in app                # pollFlow
    assert "if (ui.view === 'flow-review') frReload().catch(e => toast(errMsg(e)));" in app   # afterMarks
    assert ".sp-prog.is-done .bar > i { background: var(--accent); }" in css


def test_review_compare_detail_inset_is_off_unless_switched_on():
    """The eye / head inset in the Review compare covers the photo: off by default, a switch brings it back."""
    app = text("app.js")
    assert "const lp = prefs.get('cploupe', '0') === '1';" in app and "$('.cp-loupe', host).hidden = !lp;" in app
    assert "if (!lp || a.media !== 'photo' || a.preview !== 'ready') return;" in app      # off: no extra detail request
    assert "data-cp-loupe" in app and "prefs.set('cploupe', t.checked ? '1' : '0');" in app
