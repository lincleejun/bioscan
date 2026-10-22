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
- 只有服务写数据库；所有写入经 `store` 的单一写锁（`threading.Lock`）串行。读可并发。
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
- 状态机：`queued → running → (completed | partial | failed | stopped)`；`running → paused`（当前块结束后释放名额，下一个排队运行开始）；`paused → queued`（继续）；`queued|paused → cancelled`；服务重启时 `running → interrupted → queued`（自动恢复，从未完成项继续）。`partial` = 有单项失败、不可用能力或离线来源；不得统称完成。
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

- **同一拍摄（`versions.py`）**：预览阶段结束时在任务的照片间归并。规则依次为：① 相机文件编号（文件名开头的字母 + ≥3 位数字，如 `DSC01234`）、拍摄时间（到秒）、相机三者一致 → 同一拍摄（`same_capture`）；② 没有拍摄时间、编号与某次拍摄相同、且预览的 64 位差值哈希在任一 90° 旋转下相差 ≤ 10 位、并且没有另一个不同时间的拍摄同样接近（相差 ≤ 2 位以内）→ 导出图（`export`）。代表版本：RAW（DNG 次于其他 RAW）→ 像素多者 → 路径。其余版本写 `assets.version_of`、`version_reason`，从自动集合中移出（`collection_members.removed_by='version'`，人工加入的成员不动），已启用的模型能力记 `not_applicable`（原因“同一拍摄的其他版本”），不再分析；代表版本缺时间或 GPS 时分组借用版本的。哈希缓存在 `assets.meta.dhash`。`POST /assets/{id}/split-version` = 人确认“不是同一张”：取消归并、恢复自动集合成员、能力回到 `pending`（retry 补跑），并设 `version_lock=1`，以后永不再自动归并。
- **查询**：筛选参数增加 `versions=hide|all|only`，缺省 `hide`（列表、Smart 规则、导出按筛选都只看代表版本）；`collection=` 按成员如实返回。`AssetItem` 增加 `version_of`（id）与 `versions`（数量）；`AssetDetail` 增加 `original {id, name, reason}`、`versions [{id, name, path, reason, preview, online}]`、`version_lock`。`Task.counts.versions`；`Task.review.total` 不含版本；运行 `summary.versions = {same_capture, export}`。
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
