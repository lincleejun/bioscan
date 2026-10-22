# Gardener v0.1 需求 → 实现 → 测试 追踪矩阵

来源：[`prd-v0.1.md`](prd-v0.1.md)（§ 节号）与 [`decisions.md`](decisions.md)（D 编号）。实现路径相对 `bioscan/gardener/`，测试相对 `tests/gardener/`。
“状态”只写已由测试或真实数据运行证实的内容；未证实写“未验证”。明确后置的需求列在末尾，不算已实现。

| # | 需求（来源） | 实现 | 测试 / 证据 | 状态 |
|---|---|---|---|---|
| R01 | C/S：UI、CLI、MCP 共用一个后台服务；客户端不直接写 SQLite（D37，§12） | `api.py`、`server.py`；`cli.py`、`mcp.py` 只走 HTTP；`web/` 只调 `/api/v1` | `test_ui_cli*`、`test_ui_mcp*`、集成冒烟 | 见 PR 证据 |
| R02 | SQLite 保存整理记录，预览缓存独立目录（D30，§12） | `schema.sql`、`store.py`；`<data>/cache/previews` | `test_core_*`（重启后数据仍在） | 见 PR 证据 |
| R03 | 创建任务：多个目录 + 标题/副标题/描述，不要求先建 Collection，不复制原片（D15，§5） | `POST /tasks`；`tasks.py` | `test_core_tasks*`；UI 创建表单 | 见 PR 证据 |
| R04 | 渐进 Preview：RAW 内嵌 thumb 优先，分析前即可浏览与标记（D29，§5，§13 场景 1） | `preview.make_preview`；`tasks.py` 预览阶段逐项提交 | `test_media_preview*`、`test_core_*`（分析前可标记）；真实数据冒烟 | 见 PR 证据 |
| R05 | 失败不移除条目；未启用/待运行/失败/未识别分别表达（§5） | `asset_caps.state`；UI 能力状态 | `test_core_*`、`test_media_analyze*` | 见 PR 证据 |
| R06 | 浏览稳定：新结果不打乱当前图片与选中（§5） | UI 只在末尾追加、保持选中 | `test_ui_*`（静态检查）；手动走查 | 部分验证 |
| R07 | 暂停/继续、停止保留结果、中断恢复、单项重试（D28，§5） | `tasks.py` 状态机、`/pause /resume /stop /retry`、启动恢复 | `test_core_*` | 见 PR 证据 |
| R08 | 来源离线保留记录与预览；重连核对后继续（§5，§13 场景 8） | `sources.online`、`verify-sources` | `test_core_*` | 见 PR 证据 |
| R09 | 结束汇总：发现、完成、失败、未运行能力；分析完成 ≠ Review 完成（§5） | `runs.summary`；任务 `scan` 与 `review` 分开 | `test_core_*` | 见 PR 证据 |
| R10 | 多任务、后台队列逐个执行，Review A 时扫描 B；扫描状态与 Review 进度分开（D35） | `tasks.py` FIFO 单执行；`Task.scan` / `Task.review` | `test_core_*` | 见 PR 证据 |
| R11 | 追加来源/重分析记录新一轮执行，不改写历史；复用未变文件（D22，§5，§8） | `runs.kind=append/rescan`；`results` 只追加 | `test_core_*` | 见 PR 证据 |
| R12 | 能力：基础清点、内容分类、地点事件、物种、连拍相似、质量、美学与选择建议（D24，§6） | `capabilities.py` → BioScan stages/reducers（`analyze.py`、`grouping.py`） | `test_media_*`；真实模型冒烟 | 见 PR 证据 |
| R13 | 用户描述不变成逐图 GPS 或确认物种；无可靠解释如实表示（§6） | 描述只存任务；AI 理由为空时 UI 显示“无可靠理由” | `test_media_analyze*` | 见 PR 证据 |
| R14 | 模型不可用时不给伪造分数，如实报告（实施要求） | `Analyzer` → `unavailable` + 原因 | `test_media_analyze*`、`test_core_*` | 见 PR 证据 |
| R15 | 能力/标签/预设分开管理；运行记录能力与模型版本和参数（D25，§7） | `/capabilities`、`/tags`、`/presets`、`runs.config.versions`、`results.version` | `test_core_*` | 见 PR 证据 |
| R16 | 升级流程：小批试跑 → 比较 → 设默认 → 按需重分析；影响 Smart Collection 先显示变化（§7） | `rescan {capabilities, asset_ids}`；`AssetDetail.ai_history`；`settings.capability_versions`；完成快照 + `new_since` | `test_core_*` | 部分：本机每能力只安装一个版本 |
| R17 | 标签稳定身份、别名、层级、改名/合并/停用留历史（§7） | `tags`、`tag_history`、`/tags` | `test_core_*` | 见 PR 证据 |
| R18 | 预设：快速整理/摄影筛选/野生动物，可修改另存，任务保存配置快照（D31） | `capabilities.PRESETS`、`presets` 表、`runs.config` | `test_core_*` | 见 PR 证据 |
| R19 | 运行模式 安静/均衡/全速 只控资源；可见预览优先；可暂停分析继续 Review（D31，§7） | `RUN_MODES`；按需同步生成预览；暂停不影响 Review API | `test_core_*` | 见 PR 证据 |
| R20 | 结果页以内容 Collection blocks 为主；AI Pick/Reject、未审、人工 Pick 为快捷筛选，可存 Smart（D18，§8） | `grouping.suggest` 主体分组 + 日期/地点细分（契约修订 2）；UI Collections 两层 | `test_core_*`、`test_media_grouping*` | 见 PR 证据 |
| R21 | Review：网格、大图、主体放大、并排比较、连拍折叠、批量 Flag/Star、撤销；AI 与人工分区；理由可选（D19，§8） | `web/`；`POST /marks`、`/marks/undo` | `test_ui_*`、`test_core_*` | 见 PR 证据 |
| R22 | 整组低分可见，可选任意张或明确零选（D07，§13 场景 2） | `Collection.low_score`；`/zero` | `test_core_*` | 见 PR 证据 |
| R23 | Flag/Star 全局；集合完成独立；移出集合不改 Flag（D20，§13 场景 3） | `marks` 全局；`collection_reviews` | `test_core_*` | 见 PR 证据 |
| R24 | 完成本组不自动 Reject，显示未处理数；本组不留遇 Pick 显式冲突（D33） | `/complete`、`/zero` 409 | `test_core_*` | 见 PR 证据 |
| R25 | 已完成组追加素材 → 新增待审，历史完成保留（D33，§13 场景 5） | 完成快照、`review.new_since` | `test_core_*` | 见 PR 证据 |
| R26 | Collection 改名、描述、成员调整、合并、拆分；人工整理不被重跑覆盖（D21，§13 场景 4） | `/collections/*`；成员墓碑、`user_edited` | `test_core_*` | 见 PR 证据 |
| R27 | Smart Collection 保存规则，可另存 Manual（§4） | `collections.rule`、`query.py`、`/save-as-manual` | `test_core_*` | 见 PR 证据 |
| R28 | Coverage：提醒未覆盖地点/事件/物种，不强制每组一张，不宣称知道未拍地点（§8） | `GET /coverage` | `test_core_*` | 见 PR 证据 |
| R29 | 全部素材跨任务组合筛选（时间、地点、内容/物种、Flag/Star、AI、审阅、任务/集合/来源、媒体、在线、分析状态），可存 Smart（D26，§9） | `query.py`、`GET /assets` | `test_core_*` | 见 PR 证据 |
| R30 | 离线可查记录与已有预览，不伪装原片可用（§9） | `online` 字段；UI 离线提示 | `test_core_*` | 见 PR 证据 |
| R31 | 视频整段：时长、时间、分辨率、封面，可用格式播放/拖动，人工标记；不可播放保留条目并外部打开（D27，§13 场景 7） | `preview.read_meta`/视频帧；`/original`（Range）；`/reveal` | `test_media_*`；真实视频样本 | 见 PR 证据 |
| R32 | 导出“复制内容”默认关，只生成一个 XML；开启复制所选（视频整段）（D23，§13 场景 6） | `export.export`；`POST /export` | `test_media_export*`、`test_core_*` | 见 PR 证据 |
| R33 | XML 不冒称 Lightroom 可读；显式兼容边界（§10） | `compatibility="gardener-only"` + 注释；UI 文案 | `test_media_export*` | 见 PR 证据 |
| R34 | 复制校验与同名冲突（§10） | sha256 校验；rename/skip/fail | `test_media_export*` | 见 PR 证据 |
| R35 | 页面：全部素材 / Collections / 任务 / 管理；任务内 Preview 与 Collections 两视图（D36，§11） | `web/` | `test_ui_*`（导航顺序） | 见 PR 证据 |
| R36 | 归档任务；删除 Collection 可恢复；清理缓存不丢记录；Reject 不触发文件操作；无永久删除原片（D34，§12，§13 场景 9/10） | `/archive`、软删除 `/restore`、`/storage/clear-cache` | `test_core_*` | 见 PR 证据 |
| R37 | 原片只读，不自动移动、改名、删除（D09，§12） | 无任何写来源路径的代码；导出拒绝来源内目的地 | `test_core_*`；真实数据前后 sha256 比对 | 见 PR 证据 |
| R38 | 多客户端修改用版本检测冲突，不静默覆盖人工判断（§12） | `marks.version`、`collections.version`、409 | `test_core_*` | 见 PR 证据 |
| R39 | 文件数、逻辑素材数、字节量分别计数；RAW/JPEG 配对（D02，§2） | `scan.discover`；`Task.counts` | `test_media_scan*`；真实数据清点 | 见 PR 证据 |
| R40 | 本机访问默认（§12） | `server.py` 默认 127.0.0.1 | 代码审阅 | 见 PR 证据 |
| R41 | 多维分组：主体（人物、物种、动物类型、场景）为组，带 GPS 的大组按地点细分；时间不切分也不单独成组（owner 2026-10-21，契约修订 2/3） | `grouping.py`、`tasks.regroup`、`collections.parent_id` | `test_media_grouping*`、`test_core_scan*`、`test_core_review*` | 见 PR 证据 |
| R42 | 按人分组：本地人脸检测与聚类；给人物分组或单张脸命名，所有任务与以后的扫描直接归入，同名即合并（owner 2026-10-21，契约修订 3） | `faces.py`、`people.py`、能力 `people`、`PATCH /collections`、`POST /assets/{id}/faces/{i}` | `test_media_people_versions*`、`test_core_groups*` | 见 PR 证据；聚类质量未验证（无标注） |
| R43 | 导出图与跨目录副本作为原片的版本合并，只出现一次；人可撤销（owner 2026-10-21） | `versions.py`、`assets.version_of`、`POST /assets/{id}/split-version` | `test_media_people_versions*`、`test_core_groups*` | 见 PR 证据 |

## 明确后置（不在 v0.1，未实现）
自然语言/Embedding 检索、相似图搜索、跨年高级检索（D11，§3）；自动精彩片段、视频裁切拼接、声音识别；主题作品集；插件市场、复杂流程编排；永久删除原片；局域网认证；逐照片 XMP / Lightroom 兼容出口（需另定并按版本往返验证）；多任务并发重型推理；来源重定位、完整备份恢复。
