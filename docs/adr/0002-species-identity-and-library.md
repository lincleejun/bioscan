# ADR 0002：物种身份用概念 ID，按主体保存识别，物种 Collection 跨任务由识别结果派生

日期：2026-10-26 · 状态：已采纳（owner 2026-10-26 要求按 AfterShooting PRD v1 与调研意见落地） ·
依据：AfterShooting PRD v1（2026-10-26）§03、§04、§10、§15、§17；调研 `docs/research/2026-10-26-wildlife-workflow.md` §3–4

## 背景
- Gardener v0.1 的物种身份是标签 `species:<学名>`。学名会变：AviList v2026 一次就有 43 个同一 Avibase ID 的改学名，
  MDD v2.4→v2.5 有 7 个换属。学名一变，旧的人工确认就挂在一个已经不存在的名字上。
- 物种按整张照片存（`analyze._species` 只取最佳框），一张照片里的两种鸟只留一种（PRD §03 Subject、AC03）。
- 物种 Collection 由 `grouping.suggest` 按任务生成，少于 3 张的物种并入动物大类（`MIN_GROUP = 3`），与 PRD
  “一个物种也可见”“跨拍摄持续聚合”冲突（AC02）。
- 竞品逐帧识别，用户反馈相邻帧结果打架、不信任 AI（调研 §2）。

## 决定
1. **概念 ID**：BioScan 每个候选物种增加 `concept` 字段（可选，增量字段，不改已有字段）：
   鸟 `avibase:<AVIBASE ID>`（AviList），哺乳 `mdd:<id>`（MDD）；没有稳定 ID 的行（TreeOfLife 的其他动物）为
   `name:<学名>`，前缀即说明它随改名而变。学名、英文名、中文名都只是概念的显示属性。
2. **按主体识别**：Gardener 新表 `identifications`，一行 = 一张照片的一个框（主体）在一次运行或一次人工动作中的结论：
   概念、层级（species/genus/family/unconfirmed）、限定词（`slash` 等）、来源 `ai | user`、运行与模型版本、概率、候选。
   AI 行从已有 `results` 的 `raw:identify` 回填，不需要重跑模型。当前结论 = 该主体最新的人工行，否则最新的 AI 行。
   模型重跑只追加 AI 行，从不改写人工行（PRD §10、AC04、AC09）。
3. **物种 Collection 由识别派生**：库级（不属于任何任务）的物种 Collection，一个概念一个，**没有最小张数**。
   成员 = 当前结论落在该概念上的照片（任一主体）。已确认（人工）与候选（只有 AI）分别计数；只有候选的物种在“待确认”
   区，不计入“已拍到 N 种”（PRD §03、§04）。`grouping.suggest` 不再产生物种组，继续负责人物、场景与“未识别到种的动物”。
   任务内“按物种看本次拍摄”是筛选（任务 × 概念），不另建一套集合。
4. **确认与纠错**：`POST /identifications` 一次写入一个或多个主体的人工结论，作用范围 `subject | asset | burst`；
   连拍范围按已有的 `burst_id` 传播（拍摄段 Encounter 留到阶段 B）。每次写入有 `batch_id`，可整批撤销；
   有人工结论的主体不被传播覆盖，冲突在响应里列出。
5. **名字检索**：名字索引（学名、AviList/MDD 英文名、同义名、旧学名、用户导入的中文名）→ 概念。中文名没有授权清楚的
   默认来源（调研 §3），首版读取用户放在数据目录的 `names-zh.csv`（`scientific,zh`），不机器翻译。
6. **名录升级**：概念不变的改学名自动跟随；拆分后旧概念消失的人工结论标为“待重新确认”（slash），候选为各子种，
   不强行替用户选（eBird 做法，调研 §3）。实现放在阶段 A 的最后一片。

## 后果
- BioScan `/run` 输出的每个候选多一个 `concept` 键；名单缓存版本加一（重建约 30 s）。旧的 preds 文件没有这个键，
  读取方按缺失处理。
- Gardener `schema_version` 1 → 2：新表 `identifications`；旧库启动时建表并回填。`asset_tags` 的 `species:` 标签
  保留为派生物（筛选兼容），不再是事实来源。
- 物种 Collection 不再是任务的自动集合；旧库里已有的任务级物种自动集合原样保留，不再增加成员。
- 不在本 ADR 内：拍摄段、四种选片用途、正式导入、Lightroom 交接、清理计划（PRD 阶段 B/C）。
