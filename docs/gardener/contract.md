# Gardener v0.1 实现契约（接口与文件归属）

需求：[`prd-v0.1.md`](prd-v0.1.md)、[`decisions.md`](decisions.md)（D01–D37）。架构决定：[ADR 0001](../adr/0001-gardener-persistent-service.md)。
追踪矩阵：[`traceability.md`](traceability.md)。本文件是并行实现的契约；改动契约须由集成负责人同意并同步更新本文件。

## 1 包结构与归属

| 路径 | 归属 | 内容 |
|---|---|---|
| `bioscan/gardener/capabilities.py` | 集成（已写） | 能力、预设、运行模式表；`expand`、`stages_for` |
| `bioscan/gardener/schema.sql` | 单元 1（可扩展，须同步本文 §2） | SQLite DDL |
| `bioscan/gardener/store.py` | 单元 1 | 连接、迁移、单写锁、全部写入与查询函数 |
| `bioscan/gardener/query.py` | 单元 1 | 资产筛选（§6.4）→ SQL；Smart Collection 规则求值 |
| `bioscan/gardener/tasks.py` | 单元 1 | 后台队列、工作循环、运行状态机、重启恢复 |
| `bioscan/gardener/api.py` | 单元 1 | FastAPI 路由 `/api/v1/*`、`create_gardener_app`、静态 UI 挂载 |
| `bioscan/gardener/server.py` | 单元 1 | `gardener serve` 启动（uvicorn、参数、数据目录） |
| `bioscan/gardener/scan.py` | 单元 2 | 目录发现、RAW/JPEG 配对、文件身份、离线检测 |
| `bioscan/gardener/preview.py` | 单元 2 | 缩略图/大图预览、元数据读取、视频探测与封面 |
| `bioscan/gardener/analyze.py` | 单元 2 | `Analyzer`：复用 BioScan `RunQueue`/`Engine`，归一化结果、reducers |
| `bioscan/gardener/grouping.py` | 单元 2 | 自动分组建议：主体（人物/物种/动物类型/场景）→ 地点或日期（修订 2） |
| `bioscan/gardener/faces.py` | 修订 2 | 本地人脸检测与特征（YuNet + SFace）、按人聚类 |
| `bioscan/gardener/versions.py` | 修订 2 | 同一拍摄的多个文件（跨目录副本、导出图）归到一个代表素材 |
| `bioscan/gardener/export.py` | 单元 2 | Gardener XML 导出、可选复制 + 校验 + 冲突 |
| `bioscan/gardener/web/` | 单元 3 | 中文 Web UI（静态 HTML/CSS/JS，无构建） |
| `bioscan/gardener/cli.py` | 单元 3 | `gardener` CLI（只走 HTTP，import-light） |
| `bioscan/gardener/mcp.py` | 单元 3 | MCP stdio 服务（stdlib JSON-RPC，只走 HTTP） |
| `tests/gardener/` | 各单元各自的 `test_<unit>_*.py` | 测试；共享夹具 `tests/gardener/conftest.py` 由单元 1 写 |
| `pyproject.toml`、`uv.lock`、`bioscan/cli/main.py`、`.github/`、`README*`、`docs/`、`CLAUDE.md` | 集成 | 子代理不要改 |

单元 2 的模块是**纯函数/类**：不打开 SQLite、不知道 HTTP。单元 1 调用它们并负责落库。单元 3 只依赖 §6 的 HTTP 契约。

## 2 存储

- 数据目录 `--data-dir`（默认 `$GARDENER_HOME`，否则 `~/.gardener`）：`gardener.sqlite`（WAL）、`cache/previews/<id[2:4]>/<id>.thumb.jpg|.large.jpg`、`exports/`（默认导出位置）、`logs/`。
- 只有服务写数据库；所有写入经 `store` 的单一写锁（`threading.Lock`）串行。读可并发。一个数据目录只能有一个服务：`gardener serve` 启动时对 `<data>/gardener.lock` 取独占 `flock` 并在进程存活期间持有，已被占用则拒绝启动（退出码 2，“已有 Gardener 服务在使用数据目录 …”），不碰数据库；其他无法加锁的原因如实报告（“无法锁定数据目录 …：<原因>”）。没有 `fcntl` 的平台（Windows）不加锁。
- DDL 见 `schema.sql`；`meta.schema_version` = `1`。数据库只存预览索引（`assets.preview_status/method`），不存图片。
- 原片只读：Gardener 绝不在来源目录写入、改名、移动、删除；不得调用 BioScan 的 `jpg` stage、`/apply`、XMP 写入。导出目的地若位于任何任务来源目录之内则拒绝（400）。
- 清理缓存只删 `cache/`，把 `preview_status` 置回 `pending`；原片在线时按需重建（请求预览时同步生成）。

## 3 能力 → BioScan

见 `capabilities.py`。`identify` 的选项：`species = ("species" in caps)`；`content` 需要 `identify`（门控与框）+ `scene`，scene 选项取 `profile.album` 的 options。`burst` = `embed` stage + `cull.burst` reducer；`aesthetic` = `aesthetics` stage + `cull.select` reducer（选项取 `profile.album`），产出 AI Pick/Reject 建议。`place_event` 与 `meta` 不调用模型。模型加载失败（`engine.ensure` 抛错）→ 相关能力对该运行全部记为 `unavailable` 并附原因，**绝不给出伪造分数**。

版本：`Analyzer.versions()` 返回 `{cap: str}`，取自 `engine.info()["models"]`、`stages.fingerprints(plan)` 与 `bioscan.__version__`，形如 `scene@<fp>;siglip2@<rev12>`。每条 `results`、`ai_suggestions` 记录版本；运行 `config.versions` 快照。

## 4 单元 2 的 Python 接口

```python
# scan.py
PHOTO_EXT: frozenset[str]   # .jpg .jpeg .png .tif .tiff .heic? + bioscan.formats RAW 扩展
VIDEO_EXT: frozenset[str]   # .mov .mp4 .m4v .avi .mts .mkv
@dataclass class FileInfo: path: str; role: str; ext: str; size: int; mtime_ns: int   # role: raw|jpeg|image|video
@dataclass class Discovered: pair_key: str; media: str; primary: str; files: list[FileInfo]  # media: photo|video
@dataclass class Skipped: path: str; reason: str      # unsupported | sidecar | hidden | unreadable | symlink | unsafe_name（文件名含换行）
def discover(root: str) -> tuple[list[Discovered], list[Skipped]]   # 递归、排序稳定；同目录同 stem(小写) 的 RAW+JPEG 为一个素材，primary=JPEG
def quick_hash(path: str) -> str        # sha256(size + 首 1 MiB + 尾 1 MiB)，文件身份用
def is_online(path: str) -> bool

# preview.py
@dataclass class Preview: ok: bool; method: str | None; error: str | None; meta: dict
    # method: raw-embedded | raw-decoded | image | video-frame | None
def read_meta(paths: list[str]) -> dict[str, dict]   # 批量；键: taken_at(ISO), camera, lens, width, height, orientation,
                                                      # lat, lon, duration_s, video_codec, fps, playable(bool|None)
def make_preview(primary: str, media: str, thumb_path: Path, large_path: Path) -> Preview
    # thumb 长边 360、large 长边 1600，JPEG；RAW 先 rawpy.extract_thumb()，失败再 half_size 解码；视频 ffmpeg 取帧
    # 失败返回 ok=False + error，不抛异常

# analyze.py
@dataclass class ItemResult: asset_id: str; capability: str; status: str   # done | failed | unrecognized | unavailable
    data: dict | None; error: str | None; version: str | None
class Analyzer:
    def __init__(self, engine, *, decode_workers: int = 2, chunk: int = 16, detail_edge: int | None = None): ...
    def versions(self, caps: list[str]) -> dict[str, str]
    async def analyze(self, items: list[dict], caps: list[str], should_stop: Callable[[], bool]) -> AsyncIterator[ItemResult]
        # items: {asset_id, path, lat, lon, taken_at}；should_stop 在块之间检查（暂停/停止/关服）
        # 模型加载失败 → 对每个 item 每个相关 cap 产出 status=unavailable, error=原因
    def reduce(self, rows: list[dict], caps: list[str]) -> dict[str, dict]
        # rows: {asset_id, path, taken_at, camera, products: {stage: raw product}}（单元 1 从 results 取回）
        # 返回 {asset_id: {"burst": {"id", "rank", "size"} | None,
        #                  "suggestion": {"verdict": pick|reject|none, "score", "rank", "reasons": [中文]}}}
    def close(self) -> None

# grouping.py
@dataclass class Group: key: str; kind: str; name: str; asset_ids: list[str]; summary: dict; parent: str | None
    # 修订 2/3：kind: species | person | animal | scene | other（主体）| place（带 GPS 的主体内细分，parent = 主体 key）
def suggest(assets: list[dict], *, tz: str | None = None, final: bool = True, names: dict[str, str] | None = None) -> list[Group]
    # assets: {id, taken_at, lat, lon, media, content, species, people: [person key]|None, settled: bool}

# export.py
def export(dest_dir: Path, *, title: str, scope: dict, collections: list[dict], assets: list[dict],
           copy: bool, conflict: str = "rename", protected: list[Path]) -> dict
    # 详见 §7
```

归一化的 `results.data`（每能力，UI 直接显示）：

| 能力 | data |
|---|---|
| meta | `read_meta` 的字典 |
| content | `{group, group_zh, label, label_zh, score, top: [[label, score], ...]}` |
| subject | `{class, boxes: [{xyxy, score, kind}]}`；无主体 → status `unrecognized` |
| species | `{taxon, common, level, p, top: [{scientific, common, p}], uncertain: bool}`；无动物/无候选 → `unrecognized` |
| burst | `{vector_dim}`（向量留在 raw；分组在 reduce） |
| quality | `{sharpness, exposure, reasons: [code], reasons_zh: [..]}` |
| aesthetic | `{score}` |

`results` 另以 `capability = "raw:<stage>"` 保存原始 BioScan product（reduce 与重放用；embed 向量以 base64 f16 存）。

## 5 任务与运行生命周期（单元 1）

- 创建任务即创建 `runs(kind=scan, status=queued)` 并返回任务 id；一个工作协程按 `queued_at` 顺序**一次执行一个运行**。Review 不占执行名额。
- 运行阶段：`discover` → `preview`（逐项提交，UI 立即可见；结束时归并同一拍摄的版本，修订 2）→ `analyze`（分块，逐项提交；BioScan 能力之后跑人物）→ `group`（reduce + 最终分组）。照片的主体能力结束后才进入主体分组；分析中途的分组不做合并与细分（`final=False`）。
- 状态机：`queued → running → (completed | partial | failed | stopped)`；`running → paused`（当前块结束后释放名额，下一个排队运行开始）；`paused → queued`（继续）；暂停尚未生效时继续：运行在当前块结束后直接 `running → queued` 并从未完成项接着跑（不会停在 `paused`）；`queued|paused → cancelled`；服务重启时 `running → interrupted → queued`（自动恢复，从未完成项继续）。`partial` = 有单项失败、不可用能力或离线来源；不得统称完成。发现之后、登记之前消失的文件按缺失处理（`missing`），不使运行失败。
- 追加来源 → 新运行 `append`，复用已有素材（路径 + size + mtime 未变即不重做）；重扫 `rescan`（可指定能力、素材子集，用于小批试跑新版本）；重试 `retry`（失败项）。新运行不改写旧运行与旧结果。
- 来源离线：该来源素材 `online=0`，记录与预览保留，运行记为 `partial` 并在汇总列出；`POST /tasks/{id}/verify-sources` 重新核对，恢复在线后自动排队 `append` 继续。
- 运行模式（`capabilities.RUN_MODES`）只调资源：解码进程数、块大小、预览线程、块间暂停。
- 汇总 `summary`：`{discovered, files, bytes, by_media, previewed, preview_failed, analyzed: {cap: n}, failed: [{asset_id, capability, error}](≤200), unavailable: {cap: reason}, not_run: [cap], offline_sources: [...], unsupported: n, elapsed_s}`。

## 6 HTTP API（`/api/v1`，JSON，UTF-8）

错误：`{"error": "中文或英文说明", "code": "bad_request|not_found|conflict|unavailable"}`，状态码 400/404/409/503。时间 ISO 8601。客户端 `X-Gardener-Actor: ui|cli|mcp` 头写入历史（缺省 `api`）。进度用**轮询**：UI 每 2 s 拉 `GET /tasks` 与 `GET /assets?...&changed_after=`。

### 6.1 服务与管理
- `GET /health` → `{status:"ok", version, schema_version, data_dir, cache_dir, worker: {state: idle|running, run_id, task_id}, device, models_loaded: [..]}`
- `GET /capabilities` → `[{id, name, description, stages, requires, version|null, state: unknown|available|unavailable, reason}]`；`POST /capabilities/check {ids?}` 试加载模型并返回同形列表（可能耗时）。
- `GET /presets` → `[{id, name, capabilities, builtin}]`；`POST /presets {name, capabilities}`；`PUT /presets/{id}`；`DELETE /presets/{id}`（内建不可删）。
- `GET /settings` → `{run_mode, default_preset, capability_versions: {cap: version}}`；`PUT /settings {..}`。
- `GET /storage` → `{data_dir, db_path, db_bytes, cache_dir, cache_bytes, cache_files}`；`POST /storage/clear-cache` → `{removed_files, freed_bytes}`。
- `GET /tags` → `[{id, name, parent_id, aliases, active, merged_into, count}]`；`POST /tags {name, parent_id?}`；`PATCH /tags/{id} {name?, parent_id?, aliases?, active?}`（留历史）；`POST /tags/{id}/merge {into}`；`POST /assets/tags {asset_ids, tag_id, remove?: bool}`（人工标签）。

### 6.2 任务与运行
- `POST /tasks {title, subtitle?, description?, sources: [绝对路径], preset?: id, capabilities?: [id], run_mode?}` → 201 `Task`。路径须存在且为目录、在 `--allow-root` 内，否则 400。
- `GET /tasks?archived=0|1|all` → `[Task]`；`GET /tasks/{id}` → `Task` + `runs: [Run]`；`PATCH /tasks/{id} {title?, subtitle?, description?, capabilities?, run_mode?}`
- `POST /tasks/{id}/sources {paths}` → `Run`(append)；`POST /tasks/{id}/pause|resume|stop|archive|unarchive|verify-sources`；`POST /tasks/{id}/rescan {capabilities?, asset_ids?}` → `Run`；`POST /tasks/{id}/retry {asset_ids?}` → `Run`
- `GET /runs/{id}` → `Run`（含 `summary`）

`Task = {id, title, subtitle, description, preset, capabilities, run_mode, archived, created_at, sources: [{id, path, online}], scan: {status, run_id, phase, discovered, previewed, analyzed, failed, total_analysis, progress: 0..1}, review: {judged, total, collections_done, collections_total}, counts: {assets, photos, videos, files, bytes}}`。`scan.status` 取最新运行状态；`review.judged` = 本任务素材中有 Flag 或星级的数量（分母为已发现素材）。

`Run = {id, task_id, seq, kind, status, phase, config, scope, counts, summary, error, queued_at, started_at, finished_at}`

### 6.3 素材
- `GET /assets?<筛选>&order=time|path|score&limit=200&offset=0` → `{total, items: [AssetItem], offset}`
- `GET /assets/{id}` → `AssetDetail`；`GET /assets/{id}/thumb`、`/large`（image/jpeg；缺失且原片在线则同步生成；无法生成 404 + 原因）；`GET /assets/{id}/original`（原文件流，支持 Range，用于视频播放）；`POST /assets/{id}/reveal`（本机 Finder 中显示，返回 `{ok}`）
- `POST /marks {asset_ids, flag?: "pick"|"reject"|null, stars?: 0..5, reason?, expect?: {asset_id: version}}` → `{batch_id, assets: [{id, flag, stars, version}]}`；版本不符 409 `{conflicts: [{id, version}]}`
- `POST /marks/undo {batch_id}` → `{undone: n}`（该批之后被再次修改的素材 409）

`AssetItem = {id, media, name, taken_at, camera, width, height, duration_s, online, preview: pending|ready|failed, flag, stars, mark_version, judged, ai: {verdict, score, reasons}|null, content_label_zh, species: {taxon, common, p}|null, burst: {id, rank, size}|null, analysis: pending|running|done|partial|failed|unavailable, task_ids, updated_at}`

`AssetDetail = AssetItem + {files: [{path, role, size, online}], meta, lat, lon, caps: {cap: {state, version, error, data}}, ai_history: [{run_id, verdict, score, reasons, version, at}], mark_history: [{field, old, new, actor, reason, at}], collections: [{id, name}], tags: [{id, name, source}]}`

### 6.4 筛选参数（`GET /assets` 与 Smart Collection `rule` 相同）
`task, collection, source, media=photo|video, flag=pick|reject|none, stars_min, stars, ai=pick|reject|none, judged=yes|no, online=1|0, analysis=pending|done|failed|unavailable, date_from, date_to, has_gps=1|0, tag, content(场景 group/label), species(学名或俗名子串), q(文件名子串), changed_after`。`rule` 为同名键的 JSON 对象。

### 6.5 Collections
- `GET /collections?task=&kind=&deleted=0|1` → `[Collection]`；`GET /collections/{id}` → `Collection`
- `POST /collections {name, description?, kind: manual|smart, rule?, asset_ids?, task_id?}`；`PATCH /collections/{id} {name?, description?, rule?, version}`（版本不符 409）
- `POST /collections/{id}/members {add?: [], remove?: []}`（smart 不可，400）；`POST /collections/{id}/merge {ids, name?}` → 新 Manual，原集合软删除；`POST /collections/{id}/split {asset_ids, name}` → 新 Manual，成员移出原集合；`POST /collections/{id}/save-as-manual {name}`
- `POST /collections/{id}/complete {note?}`；`POST /collections/{id}/zero {resolution?: keep_picks|clear_picks}`（有 Pick 且无 resolution → 409 `{picks: [{id, collections: [{id, name}]}]}`）；`POST /collections/{id}/reopen`
- `DELETE /collections/{id}`（软删除，素材和标记不变）；`POST /collections/{id}/restore`
- `GET /coverage?task=` → `[{collection_id, name, group_kind, size, picks, state: covered|zeroed|uncovered}]`（除“全部素材”与自建集合外的自动集合；不宣称知道未拍到的地点）

`Collection = {id, kind, origin, group_kind, task_id, name, description, rule, version, deleted_at, size, cover_ids: [≤4], judged, picks, rejects, ai_picks, ai_rejects, low_score: bool, review: {state: open|completed|zeroed, at, unhandled, new_since: n}, user_edited}`。`low_score` = 有美学分且全部低于 AI 拒绝线或全部为 AI reject。

自动集合规则：`grouping.suggest` 的每组对应 `(task_id, auto_key)` 一个 `origin=auto, kind=manual` 集合；重组只**新增**成员（标记 `added_by=auto`），不移除人工加入的成员，不重新加入人工移除（墓碑）的成员，`user_edited=1` 时不改名。新成员在已完成集合中计入 `review.new_since`。每个任务另有一个 `group_kind=all` 的“全部素材”集合。

### 6.6 导出
- `POST /export {collection_id? | asset_ids? | rule?, scope: picked|all|selected, dest_dir?, copy: false, conflict: rename|skip|fail}` → `ExportReport`
- `GET /exports` → 历史

## 7 导出规则（单元 2 实现，单元 1 调用）
- 默认 `copy=false`：只写 **一个** `gardener-export-<时间>.xml` 到 `dest_dir`（默认 `<data>/exports/`），不复制任何原片。
- XML：根 `<gardener-export xmlns="urn:gardener:export:0.1" version="0.1" compatibility="gardener-only">`，注释写明“不是 XMP，Lightroom 不能直接读取”；含 `<scope>`、`<collections>`（成员、完成状态）、`<assets>`（每个素材的所有文件原路径、copied 路径与 sha256（如复制）、Flag、星级、AI 建议与理由及版本、审阅状态）。XML 转义并剔除非法控制字符。
- `copy=true`：复制所选素材全部文件（视频整段）到 `dest_dir/files/<task 标题安全名>/`，复制后 sha256 校验源与副本；同名冲突：内容相同 → `identical` 复用；不同 → `rename`（`name (1).ext`）/`skip`/`fail`；任何校验失败如实报告，不删除源。
- `ExportReport = {xml_path, assets, files, copied, identical, skipped, renamed, failed: [{path, error}], bytes, verified: bool}`。

## 8 UI / CLI / MCP（单元 3）
- 主导航顺序：**全部素材 / Collections / 任务 / 管理**；中文界面；沿用已认可原型（仓库外 gardener-prototype/，仅作视觉参考）的视觉方向，**删除全部示例数据、模拟扫描和业务 localStorage**（仅可存 UI 偏好，如上次页面）。
- 任务内两个视图：Preview 与 Collections，共享素材与标记，进度条持续可见；Review 页：网格 + 大图 + 主体放大（species/subject 框放大）+ 并排比较 + 连拍折叠 + 批量 Flag/Star + 撤销 + 键盘（P/X/U、0–5、←→、空格、C、⌘Z）；AI 信息与人工判断分区显示；能力状态逐项显示 未启用/待运行/运行中/完成/失败/未识别/不可用（含原因）。新结果追加到末尾，不改变当前选中项。
- 视频：`<video src=/original>` 可播放则播放/拖动，`onerror` 显示“无法在浏览器播放”并提供“在 Finder 中显示”。
- CLI `gardener`（`bioscan/gardener/cli.py:main`）：`serve`（转调 server）、`health`、`task create|list|show|pause|resume|stop|append|rescan|retry|archive`、`assets`（筛选参数）、`mark`、`undo`、`collections`、`collection complete|zero|reopen|delete|restore`、`export`、`caps`、`storage`、`mcp`。默认 `--url http://127.0.0.1:8770`；只用 `urllib`。
- MCP（`gardener mcp`）：stdio JSON-RPC 2.0（`initialize`、`tools/list`、`tools/call`），工具与 CLI 同名能力，全部转调 HTTP。

## 9 v0.1 对 PRD 未决项的默认决定（记录于 ADR 0001，可改）
1. RAW/JPEG：同目录、同 stem（不区分大小写）为一个素材；文件、逻辑素材、字节分别计数。
2. 任务级 Review 分母 = 已发现素材数；“已判断” = 有 Flag 或星级；浏览不算。
3. 导出 schema 如 §7；冲突默认 `rename`；复制后 sha256 校验。
4. 默认自动集合：每任务“全部素材”+ 修订 2 的主体分组与细分（取代最初的事件/地点/场景/物种平铺分组）。
5. Smart Collection 动态：成员按规则实时求值；完成时记录快照，之后新增显示“新增待审”。
6. 删除集合为软删除，恢复期限首版不限。
7. 预览：thumb 长边 360、large 长边 1600；低分辨率预览不代表 RAW 解码或对焦验证（UI 明示）。
8. 服务默认只监听 127.0.0.1，不开放局域网认证。

## 10 契约修订 1（独立验收评审 X1–X9 与澄清项，优先于上文冲突处）

- **X1 pair_key**：只有同目录同 stem（不区分大小写）的 **RAW + JPEG** 合为一个素材；其余任何文件（视频、TIFF、PNG、RAW+RAW 如 ARW+DNG、JPG+MOV）各自成素材，`pair_key = 目录 + "/" + 完整文件名（小写）`。RAW+JPEG 的 `pair_key = 目录 + "/" + stem（小写）+ "|rawjpeg"`。同目录多 RAW 同 stem 时只与 JPEG 配对第一个（按扩展名排序），其余独立。`discover()` 永不返回重复 pair_key。
- **X2 原始产品**：`Analyzer.analyze` 对每个结果事件额外 yield `ItemResult(capability="raw:<stage>", status="done", data=<该 stage 原始 product>)`；`embed` 向量由 **单元 2** 在 analyze 中编码为 `{"f16_b64": ...}`（或保持 BioScan 原有编码），`reduce` 自己解码。单元 1 原样存入 `results`，**不写** `asset_caps`，不在 UI/API `caps` 中暴露 `raw:*`。
- **X3 连拍**：`reduce(rows, caps)` 每次输入**该任务全部照片**（单元 1 负责从最新 `raw:*` 结果组装），单元 1 保存时 `burst_id = "<task_id>:<bid>"`；`AssetItem.burst.id` 为带命名空间的 id。select 的 rank 同样只在任务内有意义。
- **X4 文件变化与缺失**：**永远不删除** `assets`、`task_assets`、`marks`、`mark_history`、集合成员行。来源在线但文件不见 → `asset_files.online=0` 且 `assets.meta.missing=true`（summary 计 `missing`，与 `offline_sources` 区分）；之后重新出现即恢复。同一路径 size/mtime 变化且 `quick_hash` 不同 = **内容变化**：沿用同一 `asset_id`；在 `mark_history` 写入 `actor=system, reason=content_changed` 的记录并把当前 Flag/Star **清空**（旧判断保留在历史中，可 undo 该 batch 恢复）；`assets.meta.content_changed_at` 记录时间；能力状态回 `pending` 并在本运行重新预览与分析；旧 results/ai_suggestions 保留为历史。已完成集合把这类成员计入 `review.changed_since`。size/mtime 变但 hash 相同 = 未变化，只更新 mtime。
- **X5 marks 省略与 null**：`POST /marks` 中键**省略 = 不改**，`flag: null` = 清除 Flag，`stars: 0` = 清除星级。CLI/MCP 只发送用户明确给出的键。
- **X6 本组不留清 Pick**：`resolution=clear_picks` 经同一 marks 写入路径：把这些素材 Flag 设为 `null`（**不是** reject），写 `mark_history`（同一 batch_id、actor、reason=`zero:<collection_id>`），响应带 `batch_id`，`POST /marks/undo` 可恢复。
- **X7 changed_after**：任何影响 AssetItem 的写入（marks、asset_caps、results、ai_suggestions、预览状态、集合成员、标签、在线状态）都同时更新 `assets.updated_at`。所有记录时间戳统一为 UTC `YYYY-MM-DDTHH:MM:SS.ffffffZ`（定长，可字符串比较）；`changed_after` 为严格大于。
- **X8 自动集合身份**：重组时先按**成员重叠**把每个建议组匹配到该任务已有的同 `group_kind` 自动集合（重叠成员数最多者；无重叠才新建）；`auto_key` 只在首次创建时确定，以后不变。被软删除或被合并掉的自动集合**不恢复、不再加成员**；其已有成员也不会因此被塞进新建的同 key 集合（建议组与已删集合重叠时跳过这些成员）。scene/species 组的 key 本身稳定（标签 id），仍按同一规则处理已删集合。
- **X9 导出输入形状**：`export(dest_dir, *, title, scope, collections, assets, copy, conflict, protected)`：`assets` = **AssetDetail 列表**（§6.3，含 files、flag、stars、ai、ai_history[0].version、caps 不需要、collections）；`collections` = `Collection` 列表（§6.5，另含 `member_ids`）；`scope = {kind: collection|asset_ids|rule, value, scope: picked|all|selected}`；复制目录 `dest_dir/files/<安全化 title>/`，`title` 由单元 1 给出（集合名或“筛选结果”），跨任务导出同样只用 title。XML 每个素材含：所有文件原路径（与复制路径、sha256）、flag、stars、AI verdict/score/reasons/version、所属集合及其审阅状态。导出不复制 XMP sidecar，ExportReport 加 `notes: ["未包含来源目录中的 XMP sidecar"]`。
- **标签拆分（PRD §7 / D25 已确认，首版必须）**：`POST /tags/{id}/split {names: [新名], assign?: {新名: [asset_ids]}}` → 新建子标签（原标签保留或停用由 `deactivate_source` 决定，默认保留），记 `tag_history`，人工与 AI 的 `asset_tags` 来源分开保留。
- **澄清**：`taken_at` 保留相机本地时间（有 OffsetTime 时带偏移），不换算 UTC；记录时间戳用 UTC。`GET /assets` 增加 `order=discovered`（`task_assets.ord`，任务内默认）并统一以 `id` 为次级排序。缓存分片用 `id[2:4]`。`PHOTO_EXT` 不含 HEIC。runs.kind 枚举为 scan|append|rescan|retry（恢复不新建运行，而是原运行重新排队）。`expect` 可选，发送时不符返回 409。只有开启 `place_event` 才生成事件/地点分组，`content` 才有场景分组，`species` 才有物种分组。`--allow-root` 默认不限制（与 BioScan 一致），但服务只监听 127.0.0.1，并且：Host 头必须是 127.0.0.1/localhost/[::1]（否则 403）；带非本机 `Origin` 的非 GET 请求返回 403。`Collection.review` 增加 `removed_since`（完成快照中、当前已不在集合的数量）与 `changed_since`。`asset_tags` 主键不含 run_id：重跑覆盖当前 AI 标签，旧值在 `results` 历史中。`settings.capability_versions` v0.1 只记录与展示（版本由已安装模型决定），比较新旧靠 `ai_history`/results 历史。
- **真实数据边界**：DNG/ARW 内嵌预览（如 1024 px 级）只用于浏览，UI 与报告不得称其为精细对焦或完整 RAW 解码验证。

## 单元 1 变更

实现（`store.py`、`query.py`、`tasks.py`、`api.py`）相对上文的增补与取舍：

- **schema**：`ai_suggestions` 增加 `burst_rank`、`burst_size`（`AssetItem.burst` 的 rank/size）；新增表 `run_assets(run_id, asset_id)` 记录每个运行覆盖的素材（续跑、汇总与进度据此计算）。`schema_version` 仍为 1。
- **注入**：`create_gardener_app(data_dir, *, engine=None, toolkit=None, allow_roots=None, start_worker=True, allowed_hosts=("127.0.0.1", "localhost", "::1"))`；`tasks.Toolkit` 汇集单元 2 的函数（默认在创建 app 时导入真实模块），Analyzer 以 `toolkit.analyzer(engine, decode_workers=, chunk=)` 构造。Host/Origin 拒绝返回 403，`code: "forbidden"`。
- **来源在线**：来源是目录，在线 = 存在且可列出（`scan.is_online` 只用于文件）。
- **视频**：只做记录、元数据、预览与 place_event，不送模型分析（没有模型能力状态行）。
- **重新配对**（RAW 补了 JPEG 或反之）：先按已登记的文件路径找素材，再按 pair_key；沿用原 asset_id，原地更新 pair_key/primary，Flag/Star 保留且不写 content_changed 批次，只重新预览与分析。若两个旧素材因此合并，另一个保留并标 `meta.merged_into`、`online=0`（不删除）。
- **自动集合**：建议组与已删除/合并的自动集合匹配时整组跳过，`summary.groups_skipped_deleted` 计数；新组的 key 已被其他行占用时加后缀 `#2`、`#3`。
- **状态机细节**：对排队中的运行“暂停”直接变 `paused`；“继续”把 `queued_at` 设为当前时间（排到队尾）。`counts.interruptions` 记录重启恢复次数。
- **响应补充**：`pause|resume|stop|verify-sources` 返回 `Task`（含 `runs`），`verify-sources` 另带 `queued_run_id`；`members` 响应另带 `added`、`removed`；`zero` 响应另带 `batch_id`；`marks/undo` 另带撤销本身的 `batch_id`；`mark_history` 条目含 `batch_id`、`undone`；新增 `GET /marks/history?asset_id=&batch_id=&actor=&limit=`（新在前）。
- **汇总补充**：`summary` 另含 `missing`、`reused`、`groups_skipped_deleted`。
- **low_score**：只按“全部为 AI reject”判断（`cull.select` 没有数值美学拒绝线）。
- **标签**：人工标签 id `user:<hex>`；场景标签 `scene:<label>` 的父标签为 `scene:<group>`；`content` 筛选按场景标签 id/名称，`species` 筛选按物种标签 id/名称子串。
- **导出**：`scope=selected` 取 `selected`（或 `asset_ids`）与选择范围的交集；传给 `export.export` 的 `collections` = 被导出的集合加上含有导出素材的集合。目的地默认 `<data>/exports/`；落在任何来源目录内则先返回 400，不调用导出。
- **预设**：内建预设可改（PUT），不可删。
- **未实现**：`assets.sha256` 不在扫描时计算（导出复制时自行计算 sha256）。
- **时区（R2）**：任务可选字段 `timezone`（IANA 名称或 `±HH:MM`，否则 400），见于 `Task`、`POST/PATCH /tasks` 与运行 `config.timezone` 快照；分组时若 `grouping.suggest` 接受 `tz` 参数则传入。自动集合保存建议组的 `summary`，`Collection` 增加 `time_basis` 与 `uncertain`（`uncertain_ids` 数量）。拍摄时间不做任何时区换算，日期筛选按相机本地时间比较。schema 增加 `tasks.timezone`、`collections.summary`；旧数据库启动时按缺失列 `ALTER TABLE ADD COLUMN`。
- **队列韧性**：运行中的任何异常（含 Analyzer.close 等清理）都不会停止工作队列：结果完整而只有清理失败 → `partial`，`summary.cleanup_error` 记原因；其他异常 → `failed` 并记 `error`；工作循环本身捕获异常、退避后继续。未结束运行的 `progress` 最多 0.99，`total_analysis` 自预览阶段起即为估计值。删除当前默认预设时 `default_preset` 回到内建 `photo`（DELETE 响应带 `default_preset`）；设置指向不存在的预设时建任务也回退到 `photo`。

## 契约修订 2：主体分组、人物、同一拍摄（2026-10-21，owner 确认，优先于上文冲突处）

依据：真实语料全量扫描后 owner 判定“单纯按时间分组跟不分组没区别”；选定主轴为 **主体 → 地点/日期**，新增本地人脸聚类，导出图作为原片的版本合并（#88 评论）。

- **同一拍摄（`versions.py`）**：预览阶段结束时在任务的照片间归并。规则依次为：① 相机文件编号（文件名开头的字母 + ≥3 位数字，如 `DSC01234`）、拍摄时间（到秒）、相机三者一致 → 同一拍摄（`same_capture`）；② 没有拍摄时间、编号与某次拍摄相同、且预览的 64 位差值哈希在任一 90° 旋转下相差 ≤ 10 位、并且没有另一个不同时间的拍摄同样接近（相差 ≤ 2 位以内）→ 导出图（`export`）。代表版本：RAW（DNG 次于其他 RAW）→ 像素多者 → 路径。其余版本写 `assets.version_of`、`version_reason`，从代表版本所在任务的自动集合中移出（`collection_members.removed_by='version'`，人工加入的成员不动；其他任务的自动集合不动），已启用的模型能力记 `not_applicable`（原因“同一拍摄的其他版本”），不再分析；代表版本缺时间或 GPS 时分组借用版本的。哈希缓存在 `assets.meta.dhash`。缺失的文件（`meta.missing`）不参与归并、不做代表：代表版本消失后，其版本在下次归并时恢复为独立素材并补做分析。素材在任务间共享：一个任务不改动指向本任务外代表版本的归并；计划的代表版本若本身是任务外某素材的版本，则归到那个代表版本（不形成版本链，重跑不改变归并）。代表版本不在本任务内的版本（如只扫描了大目录中的一个子目录），在本任务的列表、计数、筛选、分析（含连拍与 AI 建议）与分组中视为独立素材。`POST /assets/{id}/split-version` = 人确认“不是同一张”：取消归并、恢复自动集合成员、能力回到 `pending`（retry 补跑），并设 `version_lock=1`，以后永不再自动归并（归并进行中同时发生的也一样）。
- **查询**：筛选参数增加 `versions=hide|all|only`，缺省 `hide`（列表、Smart 规则、导出按筛选都只看代表版本；带 `task=` 时，代表版本不在该任务内的版本算作代表）；`collection=` 按成员如实返回。`AssetItem` 增加 `version_of`（id）与 `versions`（数量）；`AssetDetail` 增加 `original {id, name, reason}`、`versions [{id, name, path, reason, preview, online}]`、`version_lock`。`Task.counts.versions`（代表版本在本任务内的版本）；`Task.review.total` 不含这些版本；运行 `summary.versions = {same_capture, export}`。
- **人物（`faces.py`，能力 `people`，`local=True`）**：OpenCV YuNet（MIT）检测人脸与五点，SFace（Apache-2.0）对齐后出 128 维特征；两个 ONNX 文件在 Hugging Face 固定 revision（`opencv/face_detection_yunet@3cc26e7f`、`opencv/face_recognition_sface@3d708243`），CPU 运行。输入是 Gardener 自己的直立预览（长边 1600），不是原片。结果 `results.data = {faces: [{box: 比例坐标, score, vec: f16 base64}], count, input: "preview"}`，`done`（有脸）/ `unrecognized`（无脸）；置信度 < 0.85 或脸框短边 < 40 px 的不算。特征只存数据库，`AssetDetail.caps.people.data.faces` 不含 `vec`。分组时对任务内全部人脸贪心聚类（按脸的面积 × 置信度从高到低开新人，余弦 ≥ 0.40 归入最接近的人）。预设：`photo`、`wildlife` 含 `people`（已有数据库的内建预设保留其现值，可在管理页打开）。模型加载失败 → 人物对该运行 `unavailable` 并附原因。
- **分组（`grouping.suggest`）**：主体 = 人物（每人一组，“人物 1, 2…”按照片数从多到少命名，可改名）、分类单元（种；只到属/科时“Fratercula（属）”）、未识别到种的动物类型（鸟类 / 哺乳动物 / 其他动物，取内容标签）、否则内容标签（山地、海岸、人像…；人像类但无已识别人脸 → “人物 · 其他”）；视频一组“视频”，无主体信息“未分类”。同一照片可同时在人物组与物种组；场景只收没有人物与动物主体的照片。少于 3 张的主体并入上一级（稀有物种 → 其动物类型，稀有场景 → “其他场景”，单人 → “人物 · 其他”）。≥ 10 张的主体再细分：多数成员有 GPS → 约 2 km 网格、相邻格合并为一个地点；否则按拍摄日（相机本地时间；任务有 `timezone` 时换算到该时区；无时区的 UTC 时间标为不确定）。细分少于 3 张的并入“其他日期 / 其他地点”，无时间/无 GPS 的单列，只分出一份时不细分。不再生成单纯按时间或单纯按地点的顶层分组。
- **自动集合**：`collections.parent_id` 指向细分所属的主体集合；`group_kind` 取上面的 kind；`Collection` 增加 `parent_id`、`label`（细分的短名）。匹配：同 kind、同父集合内先按 `auto_key`；人物与地点（key 随规模漂移）再按成员重叠（重叠 ≥ 较小一方的一半），每个已有集合一轮只匹配一个建议组。父集合被删除时不建、不补其细分。分析进行中的重组只建达到 3 张的主体组，不做合并与细分；`group` 阶段（以及停止的运行）做最终分组。旧数据库已有的 `event`/`place` 自动集合原样保留。
- **UI**：Collections 以主体为 block，细分作为 block 内的日期/地点入口；类型筛选为人物 / 物种 / 动物 / 场景 / 其他；详情显示“同一拍摄的其他版本”与“不是同一张”。

## 契约修订 3：时间不切分分组、给人物命名（2026-10-21，owner 确认，优先于修订 2 冲突处）

依据：owner 看过修订 2 的结果后要求“同一个类型都合并，不要让时间区分开”，以及“人物 1 如何命名、点人脸命名，下次直接加进去”。

- **分组**：一个主体就是一个分组，跨多少天都不按日期细分；不再生成 `day` 细分。只有多数成员带 GPS 的 ≥ 10 张主体，仍按地点（约 2 km 网格）细分，地点名附日期范围。
- **命名人物**（`people.py`；表 `people(id, name UNIQUE)`、`person_faces(asset_id, face, person_id, vec, source)`）：
  - 给人物分组改名（`PATCH /collections/{id} {name}`，人物自动分组）= 命名这个人：按“每张照片取最接近全组平均特征的那张脸”（`faces.shared_face`）把这些脸记为此人的确认脸（`source=collection`），分组改键为 `person:named:<id>`，名字随人。名字已属于另一个人 → 并入那个人：本任务内两组合并（成员移入，原组软删除并记 `merge`），确认脸合并。给已命名人物分组改名 = 给人改名；改成另一个已有的名字 = 两人合并。“人物 N”保留给未命名的人（409）。
  - 给一张脸命名：`POST /assets/{id}/faces/{index} {name}`（新名字新建人物，已有名字即同一人；`source=face`）；`DELETE /assets/{id}/faces/{index}` 取消。`GET /people` → `[{id, name, faces}]`。
  - 分组时：被命名的脸归其人；其余的脸与每个已命名人物的确认脸平均特征比较，余弦 ≥ 0.40 取最接近者；剩下的再聚类为“人物 N”。已命名人物即使只有 1 张也单独成组，不并入“人物 · 其他”。命名后服务立即对所有开启人物识别的任务重组，以后的扫描也直接归入。
  - `AssetDetail.caps.people.data.faces[i].person = {id, name, confirmed} | null`：`confirmed` = 用户命名过这张脸；否则为按已命名的脸自动认出。
- **UI**：人物分组的“编辑”是“给人物命名”（输入时提示已有名字）；素材详情的“人物”卡片列出每张脸，可命名、改名、确认（“是此人”）或取消。
- **人物分组的稳定性**：分析进行中只建已命名人物的分组；未命名的人脸聚类与“人物 N”编号只在最终分组（`group` 阶段）生成，避免同名“人物 1”出现多份。最终分组时，不再被建议、且没有人动过（未改名、未审阅、无人工加入成员）的未命名聚类分组软删除（`collection_history.detail.reason = superseded_cluster`），其 `auto_key` 加后缀以免挡住以后同键的聚类。

## 契约修订 4：物种身份、主体与识别（2026-10-26，AfterShooting PRD v1 阶段 A，ADR 0002，#106；优先于上文冲突处）

依据：owner 要求按 AfterShooting PRD v1 与调研意见（`docs/research/2026-10-26-wildlife-workflow.md`）落地；PRD §17 的顺序是先统一身份、Collections 与人工决定。

- **概念 ID**：BioScan 每个候选物种带 `concept`（`avibase:<Avibase ID>` | `mdd:<id>` | `name:<学名>`，见 `CONTEXT.md`）。Gardener 以概念为物种身份；学名、俗名只用于显示与检索。旧结果没有 `concept` 时按 `name:<学名>` 处理。
- **schema 2**：新表 `subjects`（一张照片里的一只动物，id `s_…`）与 `identifications`（对一个主体的一条结论，只追加）。见 `schema.sql`。`schema_version` 1 的库启动时建表，并从全部 `raw:identify` 结果按时间顺序回填 AI 识别，然后记为 2。
- **主体匹配**：`raw:identify` 的每个框与该素材现有主体按 IoU ≥ 0.5 匹配（一对一，取重叠最大者）；匹配不上就新建主体（`origin=ai`）。本轮没看到、且没有人工结论的 AI 主体记 `removed_at`；有人工结论的主体永远保留。人工画的框是 `origin=user` 的主体（S3）。
- **AI 识别**：开启 species 时，每个带 `species` 的框写一行 `source=ai`：只有 `level=species` 才有 `concept`；属/科级保留上卷的 `taxon`，`concept` 为空；`candidates` 为前几名 `[{concept, scientific, common, p}]`。species 关闭时只有主体、没有识别。
- **当前结论**：主体最新一条未撤销的 `source=user` 行，否则最新的 `source=ai` 行。模型重跑只追加 AI 行，不改写、不覆盖人工行。
- **AssetDetail.subjects**：`[{id, xyxy, kind, origin, current: Identification|null, ai: Identification|null, human: bool}]`；`Identification = {id, concept, taxon, common, level, qualifier, p, candidates, source, run_id, version, actor, reason, batch_id, at}`（`reason`：人工批次的理由，或 AI 行的 `flock`；`batch_id`：人工批次，可撤销）。
- **人工确认与纠错（S3）**：`POST /identifications {subject_ids?, new_subjects?: [{asset_id, xyxy}], claim, scope?: subject|burst, reason?, dry_run?}`。
  - `claim` 为 `{accept: true}`（采纳该主体当前的 AI 结论）、`{level: species, concept, taxon（学名）, common?}`、`{level: genus|family, taxon}`，或 `{level: unconfirmed|not_animal}`。
  - `new_subjects` 是人画的框（比例坐标），新建 `origin=user` 的主体。
  - `scope=burst` 把同一结论写到每个目标所在连拍（`ai_suggestions.burst_id`）的其他照片：那里与目标 AI 结论同一概念的主体，或照片唯一的主体。已有人工结论的主体不改，列入 `skipped`（`reason: human`）。
  - 响应 `{batch_id, dry_run, changed: [{subject_id, asset_id, via: target|burst, from, to}], skipped: [{subject_id, asset_id, reason}]}`；`dry_run` 只报告影响范围，不写入（PRD §10“先展示影响范围”）。
  - `POST /identifications/undo {batch_id}` 整批撤销；该批之后有人再改过其中的主体 → 409 `{subjects}`；该批画出的框若再无结论则随之退役。
  - 拍摄段（Encounter）范围留到阶段 B。
- **物种卡片（S4）**：
  - **派生方式**：卡片不落进 `collections` 表，查询时从视图 `current_ids`（每个在用主体的当前结论）派生。PRD §04 规定成员的真值来自物种标注，所以不经过 `grouping.suggest`。
  - **`GET /species?task=&hidden=0|1`** 返回 `{species: [Card], to_confirm: [Card], confirmed_total, to_reconfirm, unresolved: {genus_family, unconfirmed}}`（`to_reconfirm` 见 S7 第二部分）：
    - 只看代表版本，同一拍摄的其他版本不计。
    - 一个概念一张卡，没有最小张数。
    - 一张照片无论有几个主体是同一概念，都只算一张（AC03）。
    - `species` 是至少有一张人工确认的卡，张数即“已拍到 N 种”（PRD §03）；`to_confirm` 是只有 AI 结论的卡，不计入 N。
    - `unresolved` 是主体只到属/科或未确认的照片数。
    - Flag、星级、在线状态都不影响计数。
  - **`Card`** = `{concept, taxon, common, kind, confirmed, candidates, photos, only_one, first_taken_at, last_taken_at, cover_id, cover_pinned, hidden}`：
    - 名字以人工结论为准，否则取最新的 AI 结论。
    - 封面：固定的封面仍是成员时用它；否则在已确认的照片里按星级、再按拍摄时间挑，没有已确认照片时在全部成员里挑。
  - **`PATCH /species/{concept} {cover_asset_id?: id|null, hidden?: bool}`**：只影响显示（表 `species_prefs`）；封面必须是该物种的照片（否则 400），没有该物种的照片则 404。
  - **`GET /assets` 增加 `concept`、`id_state=confirmed|candidate|unresolved|reconfirm`**：两者同时给出时，须由同一个主体满足。Smart 规则同样可用。
  - **任务内分组**：本切片仍保留任务内物种分组（`grouping.suggest`），界面切换到物种库（S6）时再去掉。
- **名字检索（S5）**：
  - **索引表 `taxon_names`**：名字 → 概念（`taxa.py`）。来源有三类：
    - AviList/MDD 的学名与英文名、MDD 的其他俗名；
    - `synonyms.csv` 的别名，包括名录升级前的旧学名；
    - 用户的中文名 `<数据目录>/names-zh.csv`（`scientific,zh`；没有授权清楚的中文名单可随附，见调研 §3，所以不内置、也不机器翻译）。
  - **重建时机**：服务启动时对这些文件算哈希，变化才重建（真实名单 18,076 个概念、44,817 个名字，约 0.5 s）；名单缺失则跳过。`create_gardener_app(name_lists=)` 指定名单所在目录，`None` 不碰索引。
  - **`GET /names?q=&limit=`** 返回 `[{concept, scientific, common, zh, kind, matched, photos}]`：
    - 排序：名字完全相同在前，再是前缀匹配，再是包含；同一档里库中已有照片的在前。
    - 拍到过、但不在任何名单里的 `name:` 概念，从识别结果本身检索。
    - 纠错时用它找到 `claim` 需要的 `concept` 与 `taxon`。
  - **筛选 `species=`**（包括 Smart 规则）改为按名字索引与主体的当前结论匹配：人工纠正后筛选结果跟着变，旧学名同样命中。
- **界面（S6）**：
  - **物种页**：主导航新增“物种”（`#/species`），位于全部素材之后。页面分三区：
    - 已确认：“已拍到 N 种”；
    - 待确认：只有模型候选；
    - 待鉴定：链接到“全部素材”并带上 `id_state=unresolved`。
  - **物种卡片**：封面、名字、已确认与待确认张数、日期范围；只有一张照片时标“仅 1 张”。按鸟类、哺乳动物、其他动物筛选。
  - **物种详情**（`#/species/<concept>`）：复用网格与检查器；可把当前照片设为封面，可在物种页隐藏或显示。
  - **任务内**：“按物种看本次拍摄”（`#/species/task/<task>`）是同一页面按任务过滤的视图。
  - **检查器“物种”卡片**：
    - 每个主体一行：框内的缩略图、当前结论与来源（人工确认 / AI 候选与概率）；人工结论与模型不同时，同时显示模型的结论。
    - 可“确认”“改为…”或标“不确定”。“改为…”调用 `GET /names` 检索，学名、英文名、中文名、旧名都可以。
    - 有连拍时可勾选“同一连拍一起改”（`scope=burst`），这个界面偏好记在 `idburst`；提示条提供整批撤销。
  - **任务内自动分组不再生成物种组**：物种只在物种页出现，没有最小张数，因此去掉 `MIN_GROUP` 把稀有物种并入动物大类的冲突。动物照片在任务内按内容大类分组；旧库已有的任务级物种集合原样保留，不再增加成员。
  - **`AssetItem.species`** 改由主体的当前结论派生（人工确认优先，其次概率最高的 AI 物种结论），并增加 `confirmed`。
  - **缓存**：UI 静态文件返回 `Cache-Control: no-cache`（靠 ETag 重新校验），升级后不会继续使用旧脚本。
- **CLI 与 MCP**（PRD §02“MCP 同步可用”）：新接口都有对应的 CLI 命令与 MCP 工具，一律只走 HTTP。
  - CLI：`gardener species|names|identify|identify-undo|species-card`；`assets` 增加 `--concept`、`--id-state`。
  - MCP：`list_species`、`search_names`、`identify_species`（说明里要求先 `dry_run`，没看过图像证据不确认）、`undo_identification`、`set_species_card`。
- **名录升级（S7，第一部分：改名）**：
  - 改学名不改概念，所以人工结论自动跟随新名字。
  - 物种卡片、`AssetItem.species` 与主体的 `current`/`ai` 都按概念显示当前名单的学名、英文名和中文名（`zh`，来自 `taxon_names`）。
  - 识别行里存的名字保持原样不改写；与当前学名不同时，作为 `said: {taxon, common}` 一并返回，界面显示“当时记作 …（名录已更名）”。
- **名录升级（S7，第二部分：拆分）**：
  - 问题：拆分后旧的人工确认要么挂在一个新名单里已不存在的概念上（还算作“已拍到”的一种），要么在原 ID 留给某个子种时被静默算成那个子种。AviList v2026 的 35 个拆分实际都是前一种：亲本的种级 Avibase ID 全部退役，子种用的是原来的亚种 ID（如 Willet 在 v2025 是 `avibase-E7A14E91`；Saltmarsh Willet 的 `avibase-6D372C9F` 在 v2025 是指名亚种 *T. s. semipalmata*）。两种情况同样处理。
  - **拆分表** `data/names/avilist_splits.csv`（`scripts/build_splits.py` 由 AviList 变更页生成，见 `data/README.md`）：每个子种一行 `list_version, parent_scientific, parent_concept, concept, scientific, common`。
  - **何时生效**：名字索引同步（`taxa.sync`，服务启动时）时，对还没处理过的名录版本，若索引里已有它的全部子种（即已加载该版本名单）就处理一次；已处理的版本及其撤销批次记在 `meta.splits_applied`（`{版本: batch_id|null}`），之后不再重复。
  - **怎么改**：当前结论是人工（`source=user`）种级、概念为某个亲本的主体，追加一条 `source=user, actor=system, reason=split:<版本>` 的识别：`level=species`、`qualifier=slash`、`concept` 为空、`taxon/common` 保留原来记的名字、`candidates` 为各子种 `[{concept, scientific, common, p: null}]`。同一版本一个 `batch_id`，可用 `POST /identifications/undo` 整批撤销。AI 结论不动（不计入已拍到的种数）。
  - **不自动选定**：按坐标判定子种需要 BioScan 引擎里的 BirdNET 位置先验（`bioscan/service/geo.py`，要加载模型），名字索引同步时拿不到，所以全部留给人选，不做近似判断。
  - **效果**：
    - 这些主体不计入任何物种卡片，也不计入“已拍到 N 种”；`GET /species` 增加 `to_reconfirm`（这样的照片数）。
    - `GET /assets` 的 `id_state` 增加 `reconfirm`；`confirmed` 只算有概念的人工结论。视图 `current_ids` 增加 `qualifier` 列。
    - `AssetItem.species` 不取这类结论（退回模型的照片级结论）。`Identification` 增加 `reason`、`batch_id`。
    - 人用普通的 `POST /identifications` 选定一个子种（或任意物种）即解决。
  - **界面**：主体一行显示“待重新确认：A / B”和“名录拆分：原记作 …”，每个子种一个按钮；物种页在待确认与待鉴定之间增加“待重新确认”区，链接到 `id_state=reconfirm`；全部素材的“物种结论”筛选增加同一项。CLI `gardener species` 多打一行，MCP `list_species` 说明里写明 `to_reconfirm`。
- **标出漏掉的动物（S3 的界面）**：检查器“物种”卡片（照片有预览时总是显示，没有主体时提示“模型没有标出动物”或“还没有主体框”）增加“标出漏掉的动物”：
  - 在大图上按住拖出一个框（Esc 取消；“主体放大”会先关掉）。大图已按 EXIF 摆正，`xyxy` 是摆正后图像的 0–1 比例；图按 contain 显示，换算时只算图像实际显示的区域，不算留白。
  - 松开后打开与“改为…”相同的名字检索，选中即 `POST /identifications {new_subjects: [{asset_id, xyxy}], claim, scope: subject}`；也可“只标出，不确定是什么”（`claim: {level: unconfirmed}`）。画的框不按连拍范围扩散。提示条可整批撤销，撤销后这个框随之退役。
- **鸟群不逐只命名**：一张照片里同一 `kind` 的带物种结果的框达到 `identities.FLOCK_MIN`（5）个时，按 Merlin 的克制做法不逐只命名：
  - 这些框的 AI 识别改记为所有框首选答案共同的属（`level=genus`），没有共同属时取共同的科（需要候选带 `taxonomy`），都没有则 `unconfirmed`；`concept`、`common`、`p` 为空，`candidates` 保留每个框原来的候选，`reason=flock`。
  - 原因：一群鸟里分类器逐只猜测，往往给出几种不同的近缘种，每种都会变成物种库里的一张卡片（实测一张雨燕群照片出了三种雨燕）。
  - 人工结论不受影响（当前结论仍是人的）；少于 `FLOCK_MIN` 个的照片照常逐只命名。重跑与 schema 1 回填都经过同一规则。
  - 界面在该主体下显示“鸟群：不逐只命名”（哺乳为“兽群”，其他为“成群”）。
  - 照片级的 `species` 能力结果（`AssetItem.species` 在没有种级主体结论时的退路）不在本规则内。
- **后续切片**：S7 第二部分（拆分），见 #106。
  - 问题：拆分后原概念 ID 可能留给其中一个子种（如 Willet → Saltmarsh Willet 保留 avibase-6D372C9F，Prairie Willet 为新 ID），旧的人工确认会被静默算成那个子种。
  - 计划：
    - 提交由 AviList 变更页生成的拆分表；
    - 名单升级时，受影响的人工结论追加一条 `qualifier=slash` 的待重新确认记录，候选为各子种；
    - 能按坐标与日期判定的自动选定（eBird 的做法），其余进复核队列；
    - 整批可撤销。

## 契约修订 5B：四种用途、拍摄段、Smart 规则预览（2026-10-26，AfterShooting PRD v1 阶段 B；优先于上文冲突处）

依据：PRD §05 状态轴、§07 第二级、§08 决定与批量动作、§04 Smart Collection 第一版（AC13）、§15 迁移。

- **schema 3**：`marks.use`（`record | edit | reject`，NULL = 待定）与表 `encounters`、`encounter_members`。`schema_version` 2 的库启动时迁移：旧 Pick → `record`，Reject → `reject`，其余待定；每个改动写一行 `mark_history`（`field=use`、`actor=system`、`reason=migrate:flag:<旧值>`，同一 batch），并给每个已有任务分拍摄段。
- **四种用途取代 Pick/Reject**（PRD §05、§08）：
  - 保留记录 `record`、选入修图 `edit`、待定 `pending`（缺省）、淘汰 `reject`。按素材全局共享，与星级分开；模型不写用途；淘汰只是决定，不触发文件操作。
  - `POST /marks {asset_ids, use?: record|edit|pending|reject, flag?, stars?, reason?, expect?}`；`use` 与 `flag` 不能同时给（400）。`flag` 是旧写法：`pick` → `record`（已是 `edit` 的保持 `edit`），`reject` → `reject`，`null` → 待定。
  - `marks.flag` 保留为派生列，只由 `store.write_mark` 按用途写入（`record|edit` → `pick`，`reject` → `reject`）。所以旧客户端、`flag=` 筛选、`Collection.picks/rejects`、本组不留（`picks` = 记录或修图）、覆盖检查、导出 `scope=picked` 与导出 XML 的 `flag` 照旧可用。
  - 响应与 `AssetItem` 增加 `use`（永远是四个值之一）；`judged` = 用途不是待定或有星级。标记历史的字段从此是 `use`；撤销旧的 `flag` 历史行时按上面的映射还原为用途。
  - 筛选（含 Smart 规则）增加 `use=record|edit|pending|reject`。
  - 导出 XML 的 `<asset>` 增加 `use` 属性（`flag` 照旧）。
  - **保留此张并淘汰相似帧**：`POST /marks/keep-best {asset_id, keep_use?: record|edit（缺省 record）, dry_run?, expect?}` → `{dry_run, burst_id, keep: {id, use}, reject: [id], skipped: [{id, reason: human|only_record, concepts?}], expect: {id: version}, batch_id}`。
    - 范围是该照片所在连拍（`ai_suggestions.burst_id`）的其他代表版本；已是淘汰的不再列出。
    - 跳过 `human`：已有人工决定（记录、修图或星级）。
    - 跳过 `only_record`：淘汰后某个物种（当前结论，种级）在全库不再有未淘汰的照片；显示该物种的帧全部保留，`concepts` 列出物种。
    - 保留与淘汰同一 batch，`POST /marks/undo` 一次撤销。`dry_run` 不写入，返回的 `expect` 原样带回执行；期间被改过 → 409。不在连拍中 → 400。
  - **界面键盘**：R 保留记录、E 选入修图、U 待定、X 淘汰、1–5 星级（0 清除）、⌘/Ctrl+Z 撤销；P 不再使用。输入框内不触发。
- **拍摄段 Encounter**（PRD §03、§07）：
  - 任务内一次连续观察；同一照片在每个任务里属于一个拍摄段；只收有拍摄时间的代表版本。
  - 建议规则（`review.assign_encounters`）：按拍摄时间（相机本地，不换算）排序。停顿超过 600 s 断开；停顿超过 120 s 且前后照片的物种（当前种级结论）没有交集也断开；同一连拍内永不断开。
  - 持久化：只有还没有拍摄段的照片会被机器放置（`group` 阶段与迁移时）。一段内已有放置的照片时，新照片并入时间上最近的那张的拍摄段；否则新建一段。已放置的照片机器永不移动，所以人的拆合在重扫、追加后保持，重扫不会静默恢复旧分组。
  - `GET /encounters?task=&concept=`（至少一个）→ `{encounters: [Encounter], unassigned: [asset_id]}`。
    - `Encounter = {id, task_id, origin: auto|user, start, end, photos, asset_ids, bursts: [{id|null, size, rep, asset_ids}], candidates: [≤3 asset_id], species: [{concept, taxon, common, zh}]}`。
    - 给 `concept` 时只列该物种的照片。
    - 连拍代表 `rep`：人工选入修图 > 保留记录 > 星级 > 连拍名次 > 美学分；`candidates` 是各连拍（与单张）代表中排名前三的。
    - `unassigned` = 范围内没有拍摄段的照片（无拍摄时间，或扫描尚未到分组阶段）。
  - `POST /encounters/{id}/split {at: asset_id | asset_ids: [..]}` → 201 `{id（新段）, from}`：`at` 把这张及之后的照片拆为新的一段（`origin=user`），`asset_ids` 恰好拆出这些。两段都要有照片，否则 400；已被合并的段 400。
  - `POST /encounters/merge {ids}` → `{id}`：并入第一个，其余记 `merged_into`；须同一任务、两个以上。
  - 放置、拆、合都更新所涉照片的 `assets.updated_at`（§10 X7），轮询 `changed_after` 的客户端据此重新取拍摄段。
  - 本版没有拍摄段的撤销接口：拆与合互为逆操作。
  - **识别范围**：`POST /identifications` 的 `scope` 增加 `encounter`。同一结论写到目标所在拍摄段的其他照片，选主体的规则与 `burst` 相同；`changed[].via = encounter`。
  - **界面**：
    - 任务 Preview 与物种详情的网格按拍摄段分组，可在工具栏切换“按拍摄段 / 不分段”（界面偏好 `enc`）。
    - 每段有标题行：时间、张数、连拍数、物种。收起时只显示代表候选，可“展开全部”，也可“与上一段合并”。当前照片可“从这张起拆为新拍摄段”。
    - 段内仍按连拍折叠；在连拍中的照片可用“保留此张并淘汰相似帧…”，先看影响范围再确认。
    - 任务内“按物种看本次拍摄” → 物种卡片 → `#/species/task/<task>/<concept>`：本任务内的物种详情，即物种 → 拍摄段 → 连拍。
- **Smart 规则预览**（PRD §04，AC13）：`POST /collections/{id}/preview-rule {rule}` → `{total, current, added_count, removed_count, added: [≤200 id], removed: [≤200 id]}`，不保存；非 Smart 集合 400；规则校验与保存时相同。Smart 集合目前没有人工加入或排除，所以成员 = 规则命中。界面编辑 Smart 集合时，规则改动先显示命中数与增减，再点一次“保存”才写入。
- **CLI 与 MCP**：
  - CLI：`mark --use`、`assets --use`、`keep-best ID [--edit] [--dry-run]`、`encounters --task|--concept`、`encounter split ID --at|--assets`、`encounter merge IDS`、`collection preview-rule ID --rule JSON`、`identify --encounter`。
  - MCP：`set_marks` 增加 `use`；新工具 `keep_best`、`undo_marks`、`list_encounters`、`edit_encounters`、`preview_rule`；`identify_species` 的 `scope` 可为 `encounter`。
## 契约修订 5C：快速比较 RAW、原像素细节（2026-10-26，AfterShooting PRD §08 第三级、§14、AC06；优先于上文冲突处）

依据：PRD 要求“切换优先显示嵌入预览，再异步加载细节”，并明确标记“快速预览 / 原像素细节加载中 / 原像素细节可用”，不能把 1600 px 预览的放大称为真实 RAW 100%。**RAW 部分未在真实 RAW 文件上验证**（开发机上没有 RAW，测试用假 rawpy）。

- **`GET /assets/{id}/detail?xyxy=x0,y0,x1,y1&max=2048`**（`detail.py`）：
  - 返回原像素裁切的 JPEG（q92）。`xyxy` 是**直立画面**的 0–1 比例（与 `subjects.xyxy`、预览同一坐标），缺省整幅；`max` 是返回图长边上限（64–4096），只缩小、不放大。
  - 读哪个文件：素材有在线的 RAW（RAW+JPEG 对里的 RAW）就读 RAW，否则读 primary。
  - RAW：LibRaw 全尺寸解码（`postprocess(use_camera_wb=True, output_bps=8)`，不用 half_size），由 LibRaw 按 `sizes.flip` 转正，与 BioScan 识别用的解码同一方向，主体框对得上。JPEG/PNG/TIFF：Pillow 按 EXIF 方向转正后裁切。
  - 响应头说明它是什么：`X-Detail-Source: original`、`X-Detail-File: raw|image`、`X-Detail-Decoder: libraw-full|pillow`、`X-Detail-Orientation`（已转正的 EXIF 方向）、`X-Detail-Full: W×H`（原图直立尺寸）、`X-Detail-Crop: x0,y0,x1,y1`（原像素框）、`X-Detail-Size`、`X-Detail-Scale`（返回像素 / 原像素，1 = 未缩小）、`X-Detail-Cache: hit|miss|none`、`X-Detail-Ms`。
  - 错误：`xyxy`/`max` 不合法 400；视频 400；原片离线 404（“只有快速预览”）；解码失败 404 并附原因；被取代 409；超时 503。
- **不阻塞**：解码在独立的 2 线程池里跑；等待中的细节请求只占一个请求线程（UI 每个视图同时最多一个），网格与标记不排在解码后面（`test_marks_do_not_wait_for_a_decode`）。若以后需要，可改为 async 路由 + `asyncio.wait_for(wrap_future)`，等待时不占线程；rawpy 0.27 在 `unpack`/`dcraw_process` 期间释放 GIL（已查其 `_rawpy.pyx`）。同一素材有更新的请求时，仍在排队的旧请求直接返回 409；等待上限 90 s，超时时还在排队的请求被取消，已经开始的解码继续完成并写入缓存，重试即命中。UI 换图、缩放、平移或点“取消”都会中止上一个请求。
- **缓存**：RAW 的全尺寸解码存为 `cache/detail/<id[2:4]>/<id>.<size>-<mtime>.o<方向>.npy`（未压缩，内存映射，之后任何裁切都是毫秒级），总量上限 2 GiB，按最近使用淘汰；文件大小或修改时间变化即重新解码。JPEG 等不复制（文件本身就是原像素）。`POST /storage/clear-cache` 与 `GET /storage` 一并覆盖它；原片目录从不写入。
- **界面**（`web/app.js` 的 dv* 函数）：
  - 大图（放大查看）与并排比较用同一个查看器：先显示 1600 px 快速预览；放大到预览会被拉伸时，按可见区域外加 25 % 余量请求原像素，叠在画面的同一位置。
  - 状态只有“快速预览”“原像素细节加载中”（可取消）“原像素细节可用（RAW 全尺寸解码 / 原图）· 原像素 N%”“原像素细节不可用：原因”。只有原像素可用时才给比例；预览放大写“预览放大 ×N（不是原像素）”。
  - 放大查看里左右换图保留缩放与位置，便于连拍逐张看同一处。
  - 并排比较：默认“同步缩放与平移”，两边保持**同一原像素比例**（偏好 `cmpsync`，可关闭后各自操作）。点画面中的动物框即放大到那只；同步时另一侧对准离它最近的动物。默认显示整幅，**不自动选检测分最高的框**。底部是两张所在连拍的胶片条，点缩略图替换高亮的一侧。
- **测量**（`scripts/gardener_detail_bench.py`，进程内 HTTP，不含网络与浏览器解码；2026-10-26，macOS 26.6.2 arm64，16 核，合成 6000×4000 JPEG 12 张，热缓存）：
  - 预览切换（large）中位 1.0 ms，P95 1.1 ms，达到 PRD §14 的 P95 ≤ 150 ms（只是服务端部分）。
  - JPEG 细节，20 % 区域：中位 69 ms，P95 74 ms（每次完整解码原图）。整幅缩到 2048 px：中位 168 ms，P95 176 ms。
  - RAW 解码耗时**未测**：用 `--photos <真实目录>` 在 owner 的 Mac 上补测。
- **未做**：四图以上比较（PRD 允许后置）；眼部裁切建议（没有眼部检测）；检查器侧栏的小图仍是快速预览（其“主体放大”沿用原逻辑）；RAW 与同拍 JPEG 若尺寸略有不同，比例坐标会有几个像素的偏差（未在真实文件上核对）。
## 契约修订 5D：正式导入（2026-10-26，AfterShooting PRD v1 §06“正式导入”、§09、AC10、AC11；优先于上文冲突处）

依据：PRD §06 要求 Review 后先生成导入计划、再复制并核对，§09 要求完成页能核对目标、待定、重复、容量与失败项后再“导入 N 张”。

- **用途**：导入按每张照片的用途选择：保留记录 `record`、选入修图 `edit`、待定 `pending`、淘汰 `reject`（`marks.use`，契约修订 5B）；只在 `imports.uses_of(c, asset_ids)` 一处读取，没有标记即待定。`imports.decided_since` 判断计划生成后决定是否又变过。
- **计划** `POST /tasks/{id}/import-plan {dest_root, place?, include_pending?: true}` → 201 `ImportPlan`：
  - 范围：本任务的代表版本；同一拍摄的其他版本不另复制（`not_copied.versions`），因为它们是同一次拍摄的副本或导出图。默认复制 record + edit + pending，`include_pending=false` 时待定不复制（`not_copied.pending`，界面提醒“仍仅在来源中”）；淘汰永不复制。
  - 文件组：素材的全部文件（RAW、同拍 JPEG）加上旁边的 sidecar（`scan.sidecars`：同目录、`<stem>.<ext>` 或 `<文件名>.<ext>`，不区分大小写，扩展名为 `SIDECAR_EXT` 加 `.acr`）。`discover()` 仍跳过 sidecar，发现语义不变。
  - 目录：`dest_root/YYYY-MM-DD_地点`；跨日为 `YYYY-MM-DD~YYYY-MM-DD_地点`；未填地点为“未标注地点”。日期取相机本地拍摄日，没有拍摄时间的素材取文件修改日。全部文件平铺在这一个目录。
  - 同名：目标名被不同内容占用（不区分大小写），或被本计划另一组占用时，整组文件加批次后缀 `_B<seq>`（再冲突为 `_B<seq>-2`…），RAW、JPEG 与 sidecar 保持同一 stem；从不覆盖。`seq` 是全部导入计划的批次号。
  - 精确重复（每个文件的 `action`）：`copy`；`identical` = 目标同名文件内容相同（sha256）；`reuse` = 同一来源文件此前已导入且大小、修改时间未变、副本仍在；`duplicate` = 其他路径的同内容文件已在库中（大小 + quick hash 命中后再比 sha256；sidecar 没有指纹，不参与）。后三种都不复制。连拍相似永远不算重复。
  - 目标路径在计划中冻结，执行时不再重新推导。目标位于任何任务来源目录之内 → 400。
- **`ImportPlan`** = `{id, task_id, seq, status: planned|queued|running|completed|partial|failed, dest_root, folder, dest_dir, place, include_pending, created_at, started_at, finished_at, error, interruptions, stale, uses: {record, edit, pending, reject, total}, not_copied: {reject, pending, versions}, unfinished: {offline, analysis_pending, scan_active}, assets, files, bytes, sidecars, copy_files, copy_bytes, needed_bytes, free_bytes, enough_space, duplicates: {identical, reuse, duplicate, items}, renamed: {files, items}, progress: {files_total, files_done, files_failed, bytes_total, bytes_done, assets_total, assets_done, assets_failed}, failed: [{asset_id, path, error}]}`（列表最多 200 项）。`GET /imports?task=`、`GET /imports/{id}`。
- **执行** `POST /imports/{id}/run`：
  - 后台线程 `imports.Runner` 按批次号依次执行，不占扫描队列名额，也不阻塞 Review。queued/running/completed 的计划原样返回；partial/failed 的失败文件回到待处理再跑一次。剩余所需容量大于可用空间 → 409 `{needed_bytes, free_bytes}`。
  - 每个文件：核对来源大小与计划一致，经 `export._copy` 复制（临时文件 + sha256 回读 + 硬链接或 O_EXCL 发布，从不覆盖），再核对字节数，然后立即提交为 `done` 并登记到 `managed_files`。目标已存在时：内容相同即算完成，否则列为失败，不覆盖。
  - 幂等与恢复：已完成的文件不再复制；重启时 running → queued（`interruptions` + 1）、copying → pending，并清理本目录中崩溃留下的 `.gardener-part` 临时文件。同一来源再生成计划得到 `reuse`，不复制、不新建素材。
  - 结果：有任何失败文件 → `partial`（全部失败为 `failed`），`failed` 逐项列出；不得称为成功。来源只读，不删除、不写入。
- **受管理副本**：表 `managed_files(asset_id, path, source_path, role, size, source_mtime_ns, quick_hash, sha256, plan_id, imported_at)`。素材 id 不变，`asset_files` 仍只记来源文件；`AssetDetail.managed` 列出已校验的副本。
- **界面**：任务页“完成与导入 →”（`#/import/<task>`）填写目标根目录、地点与“待定照片一起导入”，生成计划后进入确认页（`#/import/<task>/<plan>`）：各用途照片数与默认处理、文件与容量、目标目录、重复与改名、未完成项，按钮“导入 N 张”；之后显示进度与结果（入库数、失败清单、未复制范围、下一步）。确认页切换待定开关即按同一目标重新生成计划。
- **CLI 与 MCP**：`gardener import plan|run|show|list`；MCP `plan_import`、`run_import`、`get_import`。
- **未做**：完成页的物种统计（新增物种、已有物种补充、唯一记录影响）；导入批次成为 Collection；Lightroom 交接与清理计划；exFAT 上崩溃留下的半个目标文件只报告为失败（无法证明是自己写的，不删除）。
## 契约修订 5E：Lightroom 交接与清理计划（2026-10-26，AfterShooting PRD v1 §11、§12、§13，AC14、AC15、AC18；ADR 0003）

依据：owner 2026-10-26 决定——交接通过扩展现有 Lightroom Classic 插件（`extensions/lightroom/bioscan.lrplugin/`）完成，
Gardener 不为此写入照片目录；清理为经人确认后把计划里的文件移到废纸篓（ADR 0003）。

- **用途**：`handoff.uses_of(c, asset_ids)` 读 `marks.use`（契约修订 5B）。交接默认范围 `HANDOFF_USES = ("edit",)`：只交选入修图（PRD §12）。清理只看用途为淘汰的照片。
- **与导入（5D）的衔接**：每张的文件列表包括 `managed_files` 里已核验的受管理副本（角色 `managed`），交接时排在最前，清理时与原片、sidecar 一起移到废纸篓。
- **与物种计数（修订 4）的衔接**：一张照片的全部文件都被确认过的清理计划移到废纸篓（`assets.meta.trashed`，且没有任何文件在线）后，不再计入物种卡片；离线只改在线状态，永远不改计数（AC12）。
- **schema**（不改 `schema_version`，新表 `CREATE IF NOT EXISTS`）：`handoffs`、`handoff_items`、`editor_links`、
  `cleanup_plans`、`cleanup_files`，见 `schema.sql` 末尾的 5E 段。
- **交接 `POST /handoffs {collection_id | concept | asset_ids | rule, uses?, name?}`** → 201 `Handoff`：
  - 冻结此刻的成员：集合与物种按 `uses`（缺省 `HANDOFF_USES`）过滤；`asset_ids` 缺省为除淘汰外全部；没有可交接的照片 400。
  - 每张的文件按 `managed`（受管理副本，导入单元登记后生效）→ RAW → JPEG → 其他排序，只列磁盘上存在的；一个都不在 →
    该张 `skipped`（`offline`），不写入交接文件。
  - 写 `<lightroom_dir>/handoffs/<id>.json`（`create_gardener_app(lightroom_dir=)`，默认
    `~/Library/Application Support/bioscan/lightroom/`），原子替换；文件写不成则整个交接不落库。
    文件 `{schema: 2, kind: "gardener-handoff", id, name, collection_set: "Gardener", created_at, photos: [...]}`；每张
    `{asset_id, paths, uuid, stars, use, keywords: [{chain, synonyms, concept}], baseline: {stars, keywords}}`。
  - `stars` 只取人工星级，AI 分数不会变成星级。`keywords` 只来自人工的种级结论（`source=user`），层级
    `Class|Order|Family|学名`（目、科取自 AviList / MDD；名单缺失或 `name:` 概念时为 `Class|学名`），英文名与中文名为同义词。
  - `uuid` 与 `baseline` 来自 `editor_links`（上次交接的 Lightroom 身份与 Gardener 写过的值）；从未交接过为 `null`。
- **插件**（`Handoff.lua`、`Decide.lua`，由 `Watch.lua` 每 2 s 轮询，或菜单 Library > Plug-in Extras >
  “bioscan: Apply Gardener handoffs”）：处理每个还没有 `<id>.result.json` 的交接文件。
  - 先按路径（`paths` 依次）在目录中找照片，再按 `uuid`；都找不到时就地添加第一个存在的文件（Add，不复制）。
    重复交接因此不会重复导入。
  - 加入集合组 `Gardener` 下以交接 `name` 命名的集合。
  - 写字段前读取 Lightroom 当前值，按冲突规则决定（`handoff.decide_stars` / `decide_keywords`，`Decide.lua` 逐行相同，
    测试用本机 `lua` 对同一组用例跑两边）：
    - 星级：Gardener 无星且上次没写过 → 不动；当前值 = 目标 → 不动；没有基线时 Lightroom 无星才写，有星为冲突；
      有基线时当前值 = 基线才写，否则冲突。
    - 关键词：基线里有、照片上已没有的链 = 在 Lightroom 里改过 → 冲突；否则添加缺的、移除基线里有而目标里没有的链
      （物种更正）。只动 Gardener 写过的关键词，不碰其他关键词与显影设置。
    - 冲突字段跳过并报告，不静默覆盖（P0）。
  - 结果文件 `{schema: 2, id, photos: [{asset_id, status: ok|skipped|failed, reason, path, imported, photo_id, uuid,
    stars: {action, value}, keywords: {action, value, added, removed}}]}`，先写 `.tmp` 再改名。
- **读回 `GET /handoffs/{id}`**（及 `GET /handoffs` 列表）：结果文件出现后第一次读取时入库——逐张状态与原因、
  `editor_links` 的 Lightroom 身份；字段动作为 `write|same` 时基线更新为写入的值，冲突字段保留旧基线（下次仍报告）。
  `Handoff.status`：`written`（只写了文件，**不算完成**）→ `applied`（全部 ok）| `partial` | `failed`。
  `Handoff = {id, name, scope, status, doc_path, result_path, actor, created_at, result_at,
  counts: {ok, skipped, failed, pending, conflicts}, items: [{asset_id, name, status, reason, conflicts, sent, result}]}`。
- **清理计划 `POST /cleanup-plans {collection_id | concept | asset_ids | rule}`**（缺省全库淘汰）→ 201 `Plan`：
  - 范围内 `uses_of == reject` 的素材，每组全部文件：`asset_files` 登记的文件，加上同目录、以其中某个文件命名的 sidecar
    （`IMG_1.xmp`、`IMG_1.ARW.xmp`；扩展名 `.xmp .dop .pp3`，不区分大小写）。与另一素材同 stem 的 `IMG_1.xmp`
    （ARW + DNG 各自成素材，契约 §10 X1）不收。
  - 快照每个文件的大小与 mtime；任何文件缺失 → 该组 `offline`，不进执行。
  - 每组列出所含物种（当前种级结论，只看代表版本）及计划外还剩几张，`last_photo` = 一张不剩；同一拍摄的其他版本只列出，不收入。
  - `Plan = {id, scope, status: planned|ran|partial, items: [{asset_id, name, state: ready|offline, bytes, files:
    [{path, role, size, exists, result, reason}], species: [{concept, taxon, common, zh, photos, photos_left, last_photo}],
    versions}], summary: {in_scope, rejected, ready, offline, files, bytes, last_species}, report, note}`；物种最后照片排在前面。
- **执行 `POST /cleanup-plans/{id}/run {confirm: true}`**（缺 `confirm: true` → 400）：见 ADR 0003。
  - 逐组重新核对：仍为淘汰（`not_rejected`）、文件存在（`offline`）、文件集合与大小/mtime 不变（`changed`），不符则整组跳过。
  - 通过的文件逐个移到废纸篓（`create_gardener_app(trash=)` 可注入，默认 `cleanup.trash`），每个结果立即落库；
    文件记为离线，素材 `meta.trashed = {at, plan}`，任何行都不删除。
  - 每组移动之前在一个短事务里再核对一次（仍为淘汰、文件大小与 mtime 不变）：执行耗时较长时，人在这期间改回保留的照片不会被移走，记 `not_rejected` 跳过。
  - 同一计划同一时间只执行一次：正在执行时再次请求 → 409；已移到废纸篓的文件结果不会被之后的失败覆盖。
  - 物种“最后一张”的判断不计已在废纸篓里的照片，与物种卡片口径相同。
  - 再次执行只处理尚未移走的文件。
  - `report = {trashed_files, trashed_bytes, freed_bytes: 0, recoverable: true, failed: [{asset_id, path, error}],
    skipped: [{asset_id, reason}], note}`：移到废纸篓的字节可恢复、**未释放**；Gardener 不做永久删除。
  - `GET /cleanup-plans`、`GET /cleanup-plans/{id}` 返回计划与逐文件结果。
- **UI**：Review 页、物种详情页“在 Lightroom 打开…”，全部素材页“选中的在 Lightroom 打开…”（对话框内显示逐张结果与
  “刷新结果”）；Review 页“清理淘汰…”与管理页“清理计划…”进入 `#/cleanup`、`#/cleanup/<id>`：物种最后照片、逐文件
  列表，执行只经确认对话框，并明示“将修改来源目录”。
- **CLI / MCP**：`gardener handoff create|show|list`、`gardener cleanup plan|show|list|run --yes`；MCP
  `lightroom_handoff`、`get_handoff`、`cleanup_plan`、`get_cleanup_plan`、`run_cleanup_plan`（`confirm` 必填）。
- **在 Lightroom Classic 14.2 里实测（2026-10-27，新建的测试目录，4 张照片含一张 Sony A1 ARW）**：
  - 轮询器自动处理交接文件、按路径就地导入、在 `Gardener` 集合组下建集合、写星级、写 `Aves|目|科|种` 层级关键词及英文名同义词、写结果文件：4/4 成功。
  - 同一批再交接：不重复导入，只有星级变了的那张写入，其余 `same`。
  - 在 Lightroom 里手动改过星级的照片再交接：星级字段跳过并报告 `conflicts: ["stars"]`，Lightroom 的值保留。
  - 实测发现并修复：dkjson 默认给解析出的数组挂元表，SDK 对 `createKeyword` 的同义词报 `Assertion failed: packed`；插件的 `json.decode` 现在都显式不挂元表（测试 `test_the_plugin_decodes_json_into_plain_tables`）。
- **仍未验证**：`findPhotoByUuid`（路径不变时用不到）、`removeKeyword`（物种更正）、`setRawMetadata("rating", nil)`（清除星级）、已存在关键词的同义词。真实磁盘上的废纸篓只在本机
  临时文件上试过 `/usr/bin/trash`；外置盘与网络卷未验证。
- **未做**：被移走照片的物种卡片计数排除（PRD §11）；交接失败项的单独重试（重复交接即可，身份复用）；预设与导出（P1）。

## 契约修订 6：任务视图按任务判断代表版本（2026-10-27，#102 与修订 4/5B/5D 的衔接）

- #102 引入按任务的代表版本（`query.IN_TASK_REP`）：一张照片只有当它的代表版本也在同一任务里时，才在该任务里算作版本；否则它就是这个任务自己的照片。
- 下列按任务的视图采用同一规则：`GET /species?task=`、拍摄段的分配、`GET /encounters?task=`（含“未分段”）、保留此张并淘汰相似帧的同连拍照片（连拍 id 带任务前缀）、导入计划（`not_copied.versions` 只计代表版本也在本任务里的版本）。
- 全库视图（`GET /species` 总数、清理计划的“最后一张”、`GET /names` 的张数）仍按全局规则，只计代表版本，同一拍摄不重复计数。

## 契约修订 7A：Review 收尾——一起改的范围、拆分错误连拍、拍摄段撤销（2026-10-27，#110；AfterShooting PRD §05、§07、AC07；优先于上文冲突处）

- **schema**（不改 `schema_version`，新表 `CREATE IF NOT EXISTS`，见 `schema.sql` 末尾 7A 段）：`burst_overrides`、`encounter_history`。
- **一起改的范围**（检查器“物种”卡片）：下拉框“仅此主体 / 同一连拍（N 张）/ 同一拍摄段（M 张）”，只列出当前照片实际有的范围；选择记在界面偏好 `idscope`（取代 `idburst`，缺省 `burst`；记的范围当前照片没有时退回同一连拍，再退回仅此主体）。画出的框仍只改它自己。`AssetDetail` 增加 `encounters: [{id, task_id, photos}]`，界面据此判断有没有拍摄段。
- **拆分错误连拍**（AC07）：`POST /bursts/split {at: asset_id | asset_ids: [..]}` → 201 `{burst_id（新连拍，只剩一帧时为 null）, from, moved}`。
  - `at`：这帧及之后（按拍摄时间）的帧；`asset_ids`：恰好这些帧。都须在同一个连拍里，两边都要有帧，否则 400。
  - 持久：被拆出的帧写入 `burst_overrides(task_id, asset_id, burst_id)`（`burst_id` = `<原连拍>~p_<hex>`，仍以任务 id 开头）。连拍 reducer 每次运行都重新推导 `ai_suggestions.burst_id`，所以每次保存建议之后由 `review.apply_burst_overrides` 把这些帧改到人的连拍，并重新给受影响的连拍排名次、计张数；一帧的连拍记为不在连拍中（与 reducer 相同）。只有 reducer 放进某个连拍的帧才会被移动，关掉连拍能力的运行不凭空造出连拍。
  - 当下的显示：拆分时为原连拍的每一帧追加一行 `ai_suggestions`（`run_id = user`，复制最新一行的建议，只改连拍字段），所以所有读最新建议的地方（网格、拍摄段、保留此张并淘汰相似帧、`scope=burst`）不用改动；`AssetDetail.ai_history` 不列这类行。
  - 物种身份按主体记录，拆连拍不碰识别与物种卡片（AC07 “不创建重复集合”）。
  - 已知上限：之后追加的帧若被 reducer 归入这个连拍，会落在机器那一半；需要时再拆一次。本版没有连拍拆分的撤销。
- **拍摄段撤销**：`POST /encounters/{id}/split` 的响应增加 `batch_id`，`POST /encounters/merge` 返回 `{id, batch_id}`。`POST /encounters/undo {batch_id}` → `{undone（移回的照片数）, action}`：照片回到原拍摄段（连同原来的 `source`）；拆分新建的段记为 `merged_into` 原段，合并掉的段恢复。所涉拍摄段在这一批之后又有变化（成员或合并状态，包括重扫并入的新照片）→ 409 `{encounters}`；未知或已撤销的批次 404。更新所涉照片的 `assets.updated_at`。修订 5B 的“本版没有拍摄段的撤销接口”作废。
- **保留此张并淘汰相似帧**：响应增加 `collections: [{id, name, asset_ids}]`——要淘汰的帧所在的、人建的集合（手动按成员，Smart 按规则；不含自动分组），PRD §05“批量淘汰前提示是否涉及其他集合”。界面在预览对话框里列出（不含当前正在看的集合）；CLI `keep-best` 多打“也在集合…”。
- **物种卡片**：`Card` 增加 `encounters`：含该物种照片的拍摄段数，与卡片同一口径（只计代表版本，不计已移到废纸篓的）；`task=` 时只数该任务的拍摄段。全库视图里同一照片在两个任务中时，每个任务的拍摄段各算一次。界面在日期后显示“N 个拍摄段”。
- **用词**：界面不再出现 Pick / Reject，按四种用途说（保留记录、选入修图、待定、淘汰）；接口字段 `picks`、`flag` 不变。
- **CLI 与 MCP**：CLI `burst split --at ID | --assets IDS`、`encounter undo BATCH`；MCP 新工具 `split_burst`，`edit_encounters` 的 `action` 增加 `undo`（`batch_id`）。
