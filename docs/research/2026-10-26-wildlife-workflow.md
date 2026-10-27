# 野生动物摄影：整理工作流与识别管理的空间

调研日期 2026-10-26。输入：AfterShooting PRD v1（2026-10-26）、Gardener v0.1（`5f0d6d3`）、三路调研（竞品、用户痛点、识别管理）。
只记录读过的一手资料；只来自搜索摘要或未能打开的页面标为**未核实**。厂商自述标 **VC**，由工具作者发起的论坛帖标 **[V]**。

## 结论

1. **"选片 + 鸟种识别"正在变成免费品。** 2025–26 年至少 9 个鸟类选片/识别小工具发布或进入 beta，其中两个免费开源且已覆盖大半：Project Kestrel（AGPL）和 SuperPicky 慧眼选鸟（GPL/AGPL）。在这一层和它们比选片质量没有付费空间。
2. **物种档案本身已有人做。** Specio（€249，预售中）就是"每个物种一格最佳照片 + 写回 Lightroom 关键词"。此前评估说"现有工具都没做物种 Collection"，这一说法不成立，以本文为准。
3. **"已拍到 N 种 / 照片生命清单"缺需求证据。** 竞品没做好，但约 20 个论坛帖里没有一个专门抱怨这件事；不能当切入点。
4. **有两侧证据（竞品空缺 + 用户痛点）支持的空间是三件事**：
   - **A. 识别随时间可维护**：名录拆并、改名、拼写变体，旧关键词失效；AviList v2026 六天前刚发布，没有工具做迁移。
   - **B. 按拍摄段/连拍一致识别**：所有工具逐帧识别，用户实测相邻帧给出不同物种，因此不信任 AI；一次确认整段，可以把 N 次纠错变成 1 次。
   - **C. 按物种找回旧照片**：痛点第 4 位、中文社区情绪最强。它是物种档案的"索引"形态，不需要计数。
5. **不要做**：和免费工具比选片；承诺自动上传 eBird/Macaulay（没有第三方写入通道，数据条款非商用）；首页突出"已拍到 N 种"。

## 1 竞品：谁做了什么

列：(a) 识别野生动物的选片；(b) 自动物种识别/关键词；(c) 按物种的图库/生命清单；(d) Lightroom 交接。

| 工具 | a b c d | 物种识别 | 价格 · 授权 |
|---|---|---|---|
| Project Kestrel | a b c(搜索，无清单视图) d | BioCLIP 2，IOC 约 11,250 种，按所选地区收窄，离线；README 自称物种级结果"只是起点" | 免费 / Mac App Store $10；AGPL-3.0；XMP 只确认写星级，是否写物种关键词**未核实** |
| SuperPicky 慧眼选鸟 | a b d | BirdTrace FastViT，约 1 万种，模型 CC BY-NC 4.0；"97%+" 为 VC；AviList v2025 名称映射，新版需手动重建 | 免费；README 写 GPL-3.0、GitHub API 显示 AGPL-3.0（未解决） |
| Specio (CYME) | b c d | 全动物，"45 万种"为 VC，用 GPS 区分相似种；模型未公开 | €249 买断；预售；不做选片；连拍识别"即将推出" |
| BurstPick | a d | 手动标物种 | $89 买断 |
| Lightroom Classic Assisted Culling | a(弱) d | 无 | 订阅内含；2026-06 GA，官方 FAQ："currently tuned for people-focused photography" |
| Aftershoot / FilterPixel / Narrative / Optyx | a(通用) d | 无 | 订阅 |
| Excire Foto / Search | a b(通用) d | 只有"动物/野生动物"一类关键词 | 买断 |
| Merlin / iNaturalist / Apple 视觉查询 | b（逐张） | 强，但不批量写元数据 | 免费 |
| 慧眼观鸟、Birda | b c | 手机/云端目击记录，不基于自己的 RAW 库 | 免费 / 免费增值 |
| BirdSorter、WildlifeAI、lr-inaturalist-publish | 部分 | 小项目（0 star 或停更） | 免费 |

来源：projectkestrel.org、github.com/SanjaySoniLV/ProjectKestrel、github.com/jamesphotography/SuperPicky、specio.life/en、burstpick.com、community.adobe.com（Assisted Culling FAQ）、excire.com/lightroom-classic-ai-culling-vs-excire-search-plugin。Kestrel、SuperPicky、Specio、AviList 的关键说法已由本文作者复核原页。

**共同缺口**（来自上表）：
- 全部逐帧识别，没有人按连拍或拍摄段给出一致结论。
- 没有人处理名录版本升级。
- 鸟以外的动物几乎没人覆盖（Kestrel 只有 1,150 个通用标签）。
- 没有人公开可复现的 RAW 实拍准确率。
- 已发布的开源应用全是 copyleft。

## 2 用户痛点（按出现频次排序）

来源约 20 个读过的帖子：BCG Forums、BirdForum、Fred Miranda、Adobe Community、Lightroom Queen、Talk Photography、iNaturalist 论坛、知乎、小红书。Reddit、DPReview、NPN、鸟网都没能读到，所以样本偏向英文小论坛和小红书。

| # | 痛点 | 帖子数 | 代表原话 |
|---|---|---|---|
| 1 | 连拍选片耗时 | ~11 | "I spend many hours culling and rating before I can even think about editing."（BCG，2026-08）；"连拍一按，一天就是几千张。"（小红书，2026-09） |
| 2 | 积压清不完 | ~8 | "I have 2 trips backlogged already"（BCG，2026-08）；"随缘的意思就是forever" |
| 3 | 物种打标繁琐、名称漂移 | ~7 | "83 new species that I had to look up in books, it took days"（BCG [V]，2026-06）；同一物种的拼写变体在关键词库里变成两个种；名录拆分让旧关键词失效 |
| 4 | 找不到某个物种的旧照片 | ~6 | "如果我想找某种小鸟照片，要一个个打开来翻哪次拍到的"（小红书，2025-03）；一条 2026-10 的 [V] 帖有 46 条评论，多数在问软件 |
| 5 | 存储空间与成本 | ~5 | "tens of terabytes and that's before backups"（Fred Miranda，2026-07） |
| 6 | Lightroom 目录问题 | ~4 | "目录丢失了根本不知道哪些是标星了的" |
| 7 | 修图交接 | 2 | 证据薄 |
| 8 | 照片生命清单 | 0 | 只有顺带提及 |

**反面证据**（要当真）：
- 打标不难："Manual keyword in LrC takes just a few seconds"（BCG，2026-06）。
- 不信任 AI 识别："I do not trust automated ID plug-ins… I trust the Merlin app."
- **逐帧识别不一致**：有人用 Specio 跑了 7,000 张潘塔纳尔照片，"two images taking a fraction of a second apart often show different identification"（BCG，2026-08）。
- 手动选片可以很快：FastRawViewer 5–8 分钟过 5,000 张，而 BurstPick 光加载就要 22–23 分钟。
- 按物种建文件夹对一部分人够用；也有人不愿意在导入 Lightroom 之前多一步。

## 3 识别管理：事实

**名录变动。**
- AviList v2026 于 2026-10-20 发布，是首次大修订，对齐 eBird/Clements v2026 与 BirdLife v10，此后每年 9–10 月更新一次（avilist.org/checklist，已复核）。本仓库还钉在 v2025。
- eBird 2026：拆分新增 39 种、合并减少 7 种、新描述 2 种，10 月 27 日全量更新（ebird.org/news/2026-taxonomy-update，已复核）。2025：拆分 40、合并 18。2023：拆分 124、合并 16。
- MDD 自 2018 年起共 23 个版本，近两年每年 2–4 版。v2.4→v2.5 的 diff 有 79 行：27 个新种、11 个拆分、6 个合并、7 个属变更、5 个改名（Zenodo，调研代理计算，未复核）。
- eBird 怎么处理拆分：
  - 能靠地点定的，自动改成新种（Willet、Marsh Wren 东西部）。
  - 定不了的，改为 slash（"A/B"）或 spuh（"sp."），放到"Additional Taxa"，让用户自己细化。

**中文名。**
- AviList 不含中文名。eBird 的 `zh_SIM` 只覆盖中国有分布的种，其余回退到英文；随 taxonomy xlsx 免费下载，但旁边没有授权说明。
- 郑光美《中国鸟类分类与分布名录》第四版是商业出版物；CBR 名录有 Excel 版，授权**未核实**。
- 《中国兽类名录（2024版）》（兽类学报 2025）没有标注授权。文中有一例：物种拆分后中文名沿用（倭蜂猴），说明中文名和物种身份会各自变化。

**元数据标准。**
- IPTC 没有物种字段。可用的有两处：`Iptc4xmpExt:ImageRegion`（`rCtype=animal`，可挂其他属性）和 `XMP-dwc`（Taxon、Identification；ExifTool 支持）。
- Audubon Core 允许一张图挂多个分类单元，鉴定限定词放在 `dwc:identificationQualifier`。
- Lightroom 能读的是 `lr:hierarchicalSubject`（以 `|` 分层）和 `dc:subject`。它会不会保留未知命名空间：**未核实**（Adobe 帮助页返回 403）。

**生命清单接口。**
- eBird 只支持在网页上手动导入 CSV，媒体也只能事后逐条挂到清单上；没有找到第三方写入 API；数据条款为非商用（**未核实**，页面被 Cloudflare 拦截）。
- iNaturalist API 可以写观察和照片，需要 OAuth（JWT 24 小时有效），建议每分钟 ≤60 次请求；照片默认授权 CC BY-NC。

**不确定性怎么表达。**
- iNaturalist 的社区分类取得分超过 2/3 的最细分类单元，有分歧就上卷到共同祖先。
- Merlin 只在自己有把握、且备选种在当地当季合理时才质疑用户，对鸟群不发言。
- eBird 有 8 类记录，家养种不计入个人清单。

**一张照片多个物种。** Camtrap DP（TDWG 相机陷阱标准）的 `observations` 表按框一行，字段有 bbox、`scientificName`、`classificationMethod`（人工/机器）、`classifiedBy`、`classificationProbability`。没有找到按框导出物种的摄影工具。

## 4 现有代码和这些事实的差距

| 现状（`origin/main`，Gardener v0.1） | 问题 | 方向 |
|---|---|---|
| `tags.id = species:<学名>`（`bioscan/gardener/schema.sql`） | 属变更（MDD 一个版本就有 7 个）和改名会生成新 ID，旧标注断开 | 用"概念 ID + 名录版本"做身份：AviList 有 `AvibaseID`、`Species_code_Cornell_Lab`，MDD 有 `id`；学名、中英文名只是可变的显示属性 |
| 物种按整张照片存（`grouping.py` 读 `a["species"]`，取最佳框） | 多物种照片丢信息，与 PRD §03 Subject 冲突 | 按框存身份：bbox、概念、层级、限定词、来源、模型版本、概率、确认人、时间；照片级物种由框推导（照 `person_faces` 的做法） |
| `tags` 有 merge/split，`asset_tags.source` 区分 ai/user | 机制可复用，但缺"未决拆分"状态 | 合并、改名用 `merged_into`；拆分先按 GPS + 日期自动分配，其余进入 slash 状态和复核队列 |
| 导出是 `gardener-only` XML | Lightroom 里看不到物种 | 写 `lr:hierarchicalSubject = Aves|目|科|种`，`dc:subject` 写叶子名，中英文名作同义词；另写 `XMP-dwc` 和 `ImageRegion` 作存档；事实来源仍是数据库 |
| 每帧独立识别 | 相邻帧结果打架；自有 RAW 不带 GPS 时置信错误率 12.9% | 识别结论挂在连拍/拍摄段上，人工确认一次，传播到整段；只在模型高置信且备选种当地合理时提示冲突 |
| 名录 AviList v2025 | v2026 已发布 | 把名录升级做成一次迁移：读 AviList 的"Changes from previous version"页签和 MDD `Diff_*.csv`，先预览受影响的照片和确认，再提交 |

## 5 和 PRD 假设对照

| PRD 假设 | 证据 | 判断 |
|---|---|---|
| §01 物种 Collection 持续积累是差异化 | Specio 已做，Kestrel 能按物种跨目录搜索 | **削弱**：差异化要转到"可维护的身份 + 一致的段级识别" |
| §03 "已拍到 N 种"作主数字 | 无痛点帖 | **削弱**：放进次级页面，首页做找回 |
| §03 Subject 级、多物种 | 有标准（Camtrap DP），没有工具做 | **支持**，作为身份模型基础 |
| §07 按物种 → 拍摄段 → 连拍 Review | 痛点 1、2；逐帧不一致 | **支持**，并扩大：识别也按段 |
| §10 候选、人工确认、模型重跑不覆盖 | 用户不信任 AI；iNat、Merlin 的做法 | **支持**；补 slash/属级上卷和"克制提示" |
| §10 中英文、学名、同义名检索 | 名称漂移是痛点 3；中文名来源授权不清 | **支持**；中文名单独成文件、带版本，用户可导入覆盖 |
| §12 Lightroom 交接 | 交接本身痛点薄；但"Lightroom 里看到物种关键词"是打标痛点的出口 | **部分支持**：首版先写层级关键词，预设和导出靠后 |
| §22 付费 | 免费开源竞品已覆盖选片 + 识别 | **削弱**：付费只可能来自 A/B/C，且要先验证 |

## 6 用 owner 自己的图库做的三个实验

1. **名录迁移演练**：用 v2025→v2026 的变更表扫一遍自己的识别结果，数受影响的照片、物种和人工确认数，看自动分配能解决多少、剩多少进 slash。量化痛点 A，也顺手完成本仓库的 v2026 升级。
2. **段级确认 vs 逐帧确认**：取一次真实外拍，同一批照片分别按逐帧和按拍摄段做物种确认，记操作次数和时间（PRD §20 的"纠错成本"）。验证 B 能否把 12.9% 的置信错误变成少数几次操作。
3. **找回任务**：给出 10 个"找某种鸟某次拍的好照片"任务，分别计时：文件夹/Lightroom 和 Gardener 物种检索。验证 C。

任一实验都不能说明别人愿意付费。付费判断仍按 PRD §20 的 5–10 人验证。

## 7 不做

- 和 Kestrel、SuperPicky 比选片、清晰度、眼部检测：它们免费，用户手动 5–8 分钟也能过完 5,000 张。
- 自动提交到 eBird/Macaulay：没有写入通道，条款非商用。可以导出 eBird Record Format CSV，让用户自己上传。
- 机器翻译中文名：没有授权来源时，留空或由用户填写。

## 未核实

- Reddit、DPReview、NPN、鸟网都没能读取，痛点样本有偏；多条 2026 帖子是工具作者发起的 [V]。
- SuperPicky 的授权：GPL-3.0 还是 AGPL-3.0。
- Kestrel 是否把物种写进 XMP 关键词。
- Lightroom 是否保留 `XMP-dwc` 和 `ImageRegion`。
- eBird API 条款原文；CBR、郑光美名录、CoL China 的数据授权。
- MDD v2.4→v2.5 diff 的 79 行统计（调研代理计算）；eBird 2024 年的拆并数字（来自搜索摘要）。
