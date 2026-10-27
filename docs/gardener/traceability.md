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

| R44 | 物种身份用概念 ID（`avibase:`/`mdd:`/`name:`），改学名不改身份（ADR 0002，PRD §15） | BioScan `names.read_avilist/read_mdd`、`contract.Candidate.concept` | `tests/unit/test_names.py::test_concept_ids_*`；契约 golden（去掉 concept 后不变） | 已由测试证实；真实名单 18,076 个概念全部唯一 |
| R45 | 按主体（每只动物）记录识别，ID 跨重跑稳定；一张照片多个物种（PRD §03，AC03） | `identities.ingest`、表 `subjects`/`identifications` | `test_core_identities.py::test_a_rerun_box_*`、`test_a_two_species_photo_*` | 已由测试证实 |
| R46 | 人工结论优先，模型重跑不覆盖；纠错、连拍范围、先看影响范围、整批撤销（PRD §10，AC04、AC09） | `POST /identifications`、`/identifications/undo` | `test_core_identities.py::test_correcting_*`、`test_a_burst_claim_*`、`test_a_rescan_keeps_*`、`test_a_drawn_box_*` | 已由测试证实；真模型试用一次（Mac） |
| R47 | 物种卡片跨任务累积、无最小张数，“已拍到 N 种”只计人工确认，候选不计（PRD §03、§04，AC02、AC05） | `GET /species`、视图 `current_ids` | `test_core_identities.py::test_species_cards_*`、`test_hiding_a_card_*` | 已由测试证实；真模型试用一次（Mac） |
| R48 | 中英文名、学名、同义名、旧学名检索同一物种；`species=` 跟随当前结论（PRD §10，AC13 部分） | `taxa.py`、`GET /names`、`query.where` | `test_core_identities.py::test_names_resolve_*`、`test_the_species_filter_*`、`test_the_name_index_rebuilds_*` | 已由测试证实；Smart 规则增减预览见 R58 |
| R49 | 物种页、物种详情、任务内按物种查看、检查器逐主体确认纠错（PRD §05、§07） | `web/app.js`（vSpecies、vSpeciesOne、subjectsHTML） | `test_ui_web.py`（静态与契约路由）；真模型浏览器走查（Mac） | 部分验证：无自动化浏览器测试 |
| R50 | CLI 与 MCP 覆盖物种动作（PRD §02、§13） | `cli.py`、`mcp.py` | `test_ui_cli.py::test_species_commands_*`、`test_ui_mcp.py::test_species_tools_*` | 已由测试证实（请求映射） |
| R51 | 名录拆分：受影响的人工结论改为“待重新确认 A / B”，不计入任何子种，整批可撤销，每个名录版本只处理一次（PRD §15，S7 第二部分） | `data/names/avilist_splits.csv`（`scripts/build_splits.py`）、`taxa.apply_splits`、`identities.reconfirm`、`GET /species` 的 `to_reconfirm`、`id_state=reconfirm` | `test_core_splits_flock.py::test_a_split_reopens_*`、`test_a_split_waits_until_*` | 已由测试证实；不按坐标自动选定（见契约） |
| R52 | 人工画框补上检测器漏掉的动物，再选物种（PRD §10，S3 `new_subjects`） | `web/app.js`（`startDraw`、`id-change` 画框模式） | 浏览器走查一次（假引擎、无 EXIF 旋转样本）；`test_core_identities.py::test_a_drawn_box_*`（接口） | 部分验证：无自动化浏览器测试，旋转样本未走查 |
| R53 | 鸟群/兽群不逐只命名：同一张照片同类框 ≥ `FLOCK_MIN`（5）时 AI 结论上卷到共同属/科或“不确定”，人工结论不动 | `identities.flock`、`identities.ingest` | `test_core_splits_flock.py::test_a_flock_*` | 已由测试证实；阈值未用真实鸟群照片评估 |
| R54 | 四种用途（保留记录 / 选入修图 / 待定 / 淘汰）取代 Pick/Reject，全局、可撤销、模型不写；旧 Pick → 记录、Reject → 淘汰并留来源（PRD §05、§08、§15，AC08、AC09） | `marks.use`、`store.apply_marks`/`write_mark`/`migrate_uses`、`POST /marks`、`query` 的 `use=` | `test_core_review_flow.py::test_four_uses_*`、`test_schema_2_picks_become_records_*`；`test_core_review.py`（旧 flag 客户端） | 已由测试证实 |
| R55 | 保留此张并淘汰相似帧：先预览范围，跳过人工保护与唯一物种记录，整批撤销（PRD §08） | `review.keep_best`、`POST /marks/keep-best` | `test_core_review_flow.py::test_keep_best_*` | 已由测试证实 |
| R56 | 拍摄段：按时间与物种连续性建议，连拍嵌套其中，人工拆合持久、重扫不恢复；按任务或物种列出连拍与代表候选；识别可按拍摄段（PRD §03、§07，AC07 部分） | `review.assign_encounters`/`encounters`/`split_encounter`/`merge_encounters`、`/encounters*`、`identities.identify(scope=encounter)` | `test_core_review_flow.py::test_encounters_*`、`test_identify_scope_encounter_*` | 已由测试证实；真实照片未验证 |
| R57 | 界面：R/E/U/X 用途键，网格按拍摄段分组可展开收起，任务内物种 → 拍摄段 → 连拍（PRD §07、§08） | `web/app.js`（flagSeg、encHeadHTML、keepBest、vSpeciesOne） | `test_ui_web.py::test_review_flow_ui_contract_5b`、`test_ui_calls_only_contract_endpoints`；Chrome 对 §6 fake 走查一次（段标题与展开、R/E 键、保留此张预览、规则预览后二次保存，无控制台错误） | 部分验证：无自动化浏览器测试，未对真实服务走查 |
| R58 | Smart 规则改动先显示命中数与增减，再保存（PRD §04，AC13） | `POST /collections/{id}/preview-rule`；`openEditCol` | `test_core_review_flow.py::test_rule_preview_*` | 已由测试证实 |
| R59 | CLI 与 MCP 同样可做以上动作（PRD §02、§13） | `cli.py`、`mcp.py` | `test_ui_cli.py::test_review_flow_commands_*`、`test_ui_mcp.py::test_review_flow_tools_*` | 已由测试证实（请求映射） |
| R60 | 原像素细节读取：RAW 全尺寸解码、JPEG/TIFF 原图裁切，坐标为直立画面 0–1（EXIF 旋转、RAW flip），说明来源与像素（PRD §08 第三级，AC06；修订 5C） | `detail.py`、`GET /assets/{id}/detail` | `test_media_detail.py::test_jpeg_crop_is_in_upright_coordinates`、`test_raw_full_decode_upright_and_cached`、`test_detail_endpoint_reports_what_it_is` | JPEG 已由测试证实；RAW 只用假 rawpy 测，未在真实 RAW 上验证 |
| R61 | 细节异步解码、不阻塞网格与标记，可取消/被取代，有时限，缓存有上限且可清理（PRD §14 细节读取、资源） | `detail.Details`（2 线程、按素材取代、90 s、`cache/detail` 2 GiB） | `test_newer_request_supersedes_a_queued_one_and_timeout_cancels`、`test_marks_do_not_wait_for_a_decode`、`test_detail_timeout_is_503`、`test_cache_cap_evicts_oldest` | 已由测试证实（假解码器） |
| R62 | 大图与并排比较标明“快速预览 / 原像素细节加载中 / 原像素细节可用”，预览放大不称 100%；同尺度、同步或独立缩放平移，点动物切换目标，默认不选最高分框；底部连拍胶片条（PRD §08 第三级，AC06） | `web/app.js`（dvMount、dvFetch、dvLightbox、dvCompare） | `test_ui_web.py::test_mandated_copy_present`、`test_detail_images_use_the_contract_route_and_never_say_100_percent`；Chrome 手动走查（合成 24 MP JPEG） | 部分验证：无自动化浏览器测试 |
| R63 | 浏览基线：热缓存切换 P95 ≤ 150 ms，细节解码单独统计（PRD §14） | `scripts/gardener_detail_bench.py` | 本机合成 JPEG 运行结果见修订 5C | 只测了进程内 HTTP 与合成 JPEG；RAW 与浏览器解码未测 |
| R64 | 导入计划：按用途复制（默认含待定、不含淘汰），文件组含 RAW、同拍 JPEG、XMP/ACR sidecar，`YYYY-MM-DD_地点` 目录，批次后缀，精确重复，容量与未完成项（PRD §06，AC10） | `imports.py`（`build`、`uses_of`）、`scan.sidecars`、`POST /tasks/{id}/import-plan` | `test_core_imports.py::test_plan_by_use_*`、`test_name_conflicts_get_a_batch_suffix_*`、`test_exact_duplicates_*`、`test_sidecars_are_found_*` | 已由测试证实；真实存储卡未验证 |
| R65 | 复制后核对字节与 sha256 再入库；幂等、可续跑；部分失败如实报告；来源不变（PRD §06、§11，AC11） | `imports.Runner`、`POST /imports/{id}/run`、`managed_files` | `test_core_imports.py::test_run_verifies_*`、`test_partial_failure_*`、`test_crash_mid_import_resumes_*`、`test_not_enough_space_*` | 已由测试证实；exFAT 真盘未验证 |
| R66 | 完成与导入确认页、结果页（PRD §09） | `web/app.js`（vImport、importPlanHTML、importResultHTML） | `test_ui_web.py`（契约路由）；对假服务的无头 Chrome 截图 | 部分验证：无自动化浏览器测试 |
| R67 | CLI 与 MCP 的导入命令（PRD §13） | `cli.py`（`import`）、`mcp.py`（`plan_import`、`run_import`、`get_import`） | `test_ui_cli.py::test_import_commands`、`test_ui_mcp.py::test_import_tools_*` | 已由测试证实（请求映射与假服务往返） |
| R68 | 从集合、物种或选择交给 Lightroom：冻结成员、检查在线、稳定身份、重复不重复导入（PRD §12，AC14） | `handoff.py`、`POST /handoffs`、`editor_links`；插件 `Handoff.lua` | `test_core_handoff.py::test_a_handoff_freezes_*`、`test_offline_photos_*` | 服务端已由测试证实；Lightroom 内未验证 |
| R69 | 每张返回成功/跳过/失败，只写文件不算完成（PRD §12，AC14） | `handoff.ingest`、`GET /handoffs/{id}` | `test_core_handoff.py::test_a_handoff_freezes_*` | 服务端已由测试证实；Lightroom 内未验证 |
| R70 | 同步冲突：与基线不一致的字段跳过并报告；只动本产品写过的关键词（PRD §12，AC18） | `decide_stars`、`decide_keywords`；`Decide.lua` | `test_core_handoff.py::test_star_conflict_rules`、`test_keyword_conflict_*`、`test_decide_lua_*`、`test_a_conflicting_field_*` | 规则已由测试证实（Python 与 Lua 同用例）；Lightroom 内未验证 |
| R71 | 清理计划：真实文件组、sidecar、最后物种照片、预计容量（PRD §11，AC15） | `cleanup.create`、`POST /cleanup-plans` | `test_core_cleanup.py::test_a_plan_lists_*`、`test_a_stem_sidecar_*` | 已由测试证实 |
| R72 | 执行前重新核对；移到废纸篓与真正释放分开报告；离线不算删除；失败只重试失败项（PRD §11，AC15，ADR 0003） | `cleanup.run`、`POST /cleanup-plans/{id}/run` | `test_core_cleanup.py::test_running_needs_confirm_*`、`test_a_failed_move_*` | 已由测试证实（注入的废纸篓）；真实废纸篓只在本机临时文件试过 |
| R73 | UI、CLI、MCP 同一交接与清理接口，清理需确认（PRD §13，AC16 部分） | `web/app.js`（openHandoff、vCleanup、vCleanupPlan）、`cli.py`、`mcp.py` | `test_ui_web.py`、`test_ui_cli.py::test_handoff_and_cleanup_commands`、`test_ui_mcp.py::test_handoff_and_cleanup_tools_*` | 部分验证：页面在 headless Chrome 中对 fake API 渲染过（#/cleanup、计划页、Review 按钮）；对话框未点击，无自动化浏览器测试 |
| U2-R1 | 完成页物种统计：新增物种、已有物种补充、待确认候选、唯一记录影响（导入或只留在卡片），可点击核对（PRD §09；修订 7B） | `imports.species_stats`、`ImportPlan.species`；`web/app.js`（importSpeciesHTML） | `test_core_import_followups.py::test_completion_stats_show_new_added_candidates_and_sole_records`；`test_ui_cli.py::test_import_species_stats_*` | 服务端已由测试证实；页面无自动化浏览器测试 |
| U2-R2 | 导入批次成为手动 Collection，重试补入同一个，删除后可按需再建（PRD §09；修订 7B） | `imports.batch_collection`、`POST /imports/{id}/collection`、`import_plans.collection_id` | `test_core_import_followups.py::test_a_finished_batch_becomes_a_collection_*`；`test_ui_mcp.py::test_import_collection_tool` | 已由测试证实 |
| U2-R3 | 交接只重试失败与因离线跳过的照片：同名集合、同一用途与选项，按身份找回不重复导入（PRD §09、§12；修订 7B） | `handoff.retry`、`POST /handoffs/{id}/retry`；`web/app.js`（lr-retry） | `test_core_handoff_followups.py::test_retry_sends_only_failed_and_offline_photos_*`；`test_ui_cli.py::test_handoff_retry_command`、`test_ui_mcp.py::test_retry_handoff_tool` | 服务端已由测试证实；Lightroom 内未验证 |

## 明确后置（不在 v0.1，未实现）
自然语言/Embedding 检索、相似图搜索、跨年高级检索（D11，§3）；自动精彩片段、视频裁切拼接、声音识别；主题作品集；插件市场、复杂流程编排；永久删除原片；局域网认证；逐照片 XMP / Lightroom 兼容出口（需另定并按版本往返验证）；多任务并发重型推理；来源重定位、完整备份恢复。
