# data/

名单原始文件。CSV/XLSX 体积大，已 gitignore，按下面步骤重新获取。`bioscan/service/names.py` 读取 `avilist/*.csv` 与 `mdd/*.csv`（每个目录恰好一个 CSV）。

## avilist/ — 鸟类（list_id `avilist-2025`）

- 来源页：https://www.avilist.org/checklist/v2025/
- 文件：https://www.avilist.org/wp-content/uploads/2025/06/AviList-v2025-11Jun-extended.xlsx
- 版本：AviList v2025（2025-06-11 发布），CC BY 4.0。引用：AviList Core Team. 2025. AviList: The Global Avian Checklist, v2025. https://doi.org/10.2173/avilist.v2025
- 下载日期：2026-09-22
- 官方只发 XLSX，已把第一个工作表 `AviList v2025 extended` 原样转成 UTF-8 CSV：`AviList-v2025-11Jun-extended.csv`（sha256 `a6024ec5680665489303f50bcefd6f222f5f9deca611868a849548a009aa83d3`；xlsx sha256 `5aa71c2eedd0a9e9a50b4908bfea7a412776d80460e666c0a2949ff567991f8e`）。转换命令：

  ```sh
  uv run --no-project --with openpyxl python -c "
  import csv, openpyxl
  ws = openpyxl.load_workbook('AviList-v2025-11Jun-extended.xlsx', read_only=True).worksheets[0]
  with open('AviList-v2025-11Jun-extended.csv', 'w', newline='', encoding='utf-8') as f:
      csv.writer(f).writerows([('' if v is None else v) for v in r] for r in ws.iter_rows(values_only=True))"
  ```
- 行：33684（order 46、family 252、genus 2376、species 11131、subspecies 19879）。只取 `Taxon_rank == species` 的 11131 行，含已灭绝种。
- 用到的列：`Taxon_rank`、`Order`、`Family`、`Scientific_name`（双名）、`English_name_AviList`（俗名）。其余列（Clements/BirdLife 俗名、IUCN、Range、Avibase ID 等）未用。
- 注：AviList 已发布 v2025b，本仓库按计划固定用 v2025。

## mdd/ — 哺乳类（list_id `mdd-2025`）

- 来源：https://github.com/mammaldiversity/mammaldiversity.github.io 仓库 `assets/data/MDD.zip`（提交 `22ab0ad7`，2026-07-28 "updating MDD to v2.5"），解压取 `MDD/MDD_v2.5_6904species.csv`
- 版本：MDD v2.5，2026-07-28 发布，6904 种（6791 现存 + 113 近代灭绝）。https://doi.org/10.5281/zenodo.21654811
- 下载日期：2026-09-22；sha256 `0d07a7e9409712fa86c1e3afadcf4c67bf4f9e16d5693a878e11ec1bf6860493`
- 用到的列：`order`、`family`、`genus`、`specificEpithet`、`mainCommonName`（`sciName` 是 `Genus_epithet`，未直接用）。
- 注：list_id 沿用计划接口里的 `mdd-2025`，实际数据版本是 v2.5（2026）。

## 名字向量

- taxonomy 7 级：`[Animalia, Chordata, Aves|Mammalia, 目, 科, 属, "属 种加词"]`。两张表都没有界、门，固定填 Animalia、Chordata。
- 官方向量：HF dataset `imageomics/TreeOfLife-200M` 的 `embeddings/txt_emb_bioclip-2.5-vith14.{json,npy}`（snapshot `5f2dc493`，npy 形状 `(1024, 794878)`，3.26 GB）。json 每行 `[[界, 门, 纲, 目, 科, 属, 种加词], 俗名]`。
- 匹配：鸟走 `names/avilist_map.csv` 的 `tol_name`，哺乳在建缓存时现算；两者规则相同：`naming.norm_binomial(属 + " " + 种加词)`（下划线转空格、`-` 转空格、去 `'`、合并空白、小写）精确相等 → `names/synonyms.csv` → 无。TreeOfLife 行的纲必须是 Aves / Mammalia（避免跨界同名，例如 *Oenanthe albifrons* 在 TreeOfLife 里只有植物那一行）。同一学名多行时取科相同的那行，否则取第一行。exact 和 synonym 都用官方向量。
- 对不上的名字用 BioCLIP 2.5 Huge 文本塔编码，文本与 TreeOfLife-toolbox `processing/scripts/make_txt_embedding.py` 完全一致：`"an image of {界 门 纲 目 科 属 种加词} with common name {俗名}."`（无俗名时省掉 ` with common name …`），L2 归一化。实测对 10 个 TreeOfLife 行用此模板重编码，与官方向量余弦均为 1.0000。
- 缓存：`~/.cache/bioscan/names/bioclip-2.5-vith14-<sha>.npz`，sha 取 CSV 内容 + list_id + 缓存版本（2）+ synonyms.csv +（鸟）avilist_map.csv；改任一文件都会重建。建完缓存后 3.26 GB 的官方文件可删。

## 全类群名单（list_id `tol200m-animalia`，其他动物用）

- 来源：同上的 TreeOfLife-200M 官方向量（同一 snapshot `5f2dc493`），不另下载、不编码。代码：`names.ALL_TAXA` / `names.load_all_taxa`。
- json 行格式（`names._read_tol` 读取，见上）：`[[界, 门, 纲, 目, 科, 属, 种加词], 俗名]`，共 794878 行，与 npy 的 `(1024, 794878)` float32 一一对应（3.26 GB = 794878 × 1024 × 4 B）。
- 取行规则（`names.all_taxa_rows`）：7 级齐全；界 = Animalia；纲不是 Aves / Mammalia（它们有 AviList / MDD）；属和种加词都非空且种加词是一个词（只要种级，属级行和亚种行不要）。同一学名（`norm_binomial(属 + " " + 种加词)`）多行时只留一行：第一条有俗名的，否则第一条（重复行会把一个种的概率分给几行）。
- 植物、真菌不收：门判没有植物类，没有框会落到它们；收了只多占内存、稀释每个 other_animal 框的 softmax。以后要加，是另一个 `AllTaxaSource(kingdoms=("Plantae",))` 加一个门判类别，不改这张表。
- 向量：官方向量原样取，转 float16 存（再 L2 归一化）。npy 是 (dim, N) 布局，一行跨整个文件，所以按 64 维一块顺序读。
- 缓存：`~/.cache/bioscan/names/bioclip-2.5-vith14-<sha>.npz`，与鸟/哺乳同一套（同目录、同命名、同一个 `_stale` 版本检查）；sha 取缓存版本 + 全类群版本（`ALL_TAXA_VERSION`）+ list_id + `TOL_REVISION` + 界/排除纲。字符串（学名、俗名、taxonomy）存成一段 JSON（几十万行的 numpy 定长 unicode 数组比矩阵还大），加载时 taxonomy 各级名字共享同一个字符串对象。
- 缓存缺失且 TreeOfLife 文件已删时，服务照常启动，只记一条 warning，其他动物 `species: null`（与以前一样）；`uv run python tests/models/download.py` 会重新下载并建好（下载到临时目录，建完即删）。
- 内存（行数 N 要等真数据；CI 报告会写出真实的 N 和 MiB）：矩阵 N × 1024 × 2 B。按 N ≈ 47 万估：float16 918 MiB（float32 要 1.84 GiB），缓存文件约 980 MiB；字符串约 0.1–0.3 GiB。最坏把 794878 行全收：float16 1.52 GiB。实测（本仓库开发容器，CPU，按真实行数和布局生成的 47 万行替身文件）：建表 25 s，新进程从缓存加载 6 s、峰值 RSS 1.3 GiB；16 个框对 47 万行打分 1.3 s（CPU，float16 按 65536 行一段升 float32 算，logit 误差 < 1e-5）。
- 真实行数、去重条数、每张照片的 top-1：见 CI `models` 任务的报告（`tests/models`，18 张其他动物照片）。

## 实测（2026-09-22，M 系列 Mac，MPS，`uv run python scripts/build_names.py`）

| 名单 | 总数 | 官方向量 | 自编码 | 覆盖率 | 缓存文件 |
|---|---|---|---|---|---|
| avilist-2025 | 11131 | 9416 | 1715 | 84.6% | 56.2 MiB |
| mdd-2025 | 6904 | 3835 | 3069 | 55.5% | 35.0 MiB |

- 首次构建（官方文件已在 HF 缓存，不含下载与模型加载 4.5 s）：23.0 s
- 二次加载：同进程 0.03 s；新进程冷启动（不加载模型）0.06 s

## names/ — 名字映射（提交进 git）

以 AviList（鸟）/ MDD（哺乳）学名为准，把另外三套名字对齐过来：TreeOfLife-200M（BioCLIP 官方向量）、BirdNET geo 3.0（地理先验）、iNaturalist（真值标签）。

- `synonyms.csv`（手工维护）：`avilist_scientific, alias, source, note`。`avilist_scientific` 是 AviList 或 MDD 的学名；`source` 决定别名用在哪一侧：`tol` 只用于 TreeOfLife 匹配，`birdnet` 只用于 BirdNET，`inat` 只用于 eval 真值，`spelling`（拼写差异）三侧都用。每条必须写 note。目前 6 条：
  - `Pica nuttallii` ← `Pica nuttalli`（spelling；TreeOfLife、BirdNET、iNat 都拼单 l）
  - `Tyto furcata` ← BirdNET `Tyto alba`（birdnet；BirdNET 只有合并的 Tyto alba）
  - `Circus hudsonius` ← `Circus cyaneus`（inat；美洲观测）
  - `Circus hudsonius` ← TreeOfLife `Circus cyaneus`（tol；TreeOfLife 没有 hudsonius 行，与 AviList C. cyaneus 共用官方向量，由地理先验按地点区分）
  - `Cervus canadensis` ← `Cervus elaphus`（inat；北美观测）
  - `Icterus bullockiorum` ← BirdNET `Icterus bullockii`（birdnet；BirdNET 用旧拼写。2026-09-23 由 `bioscan names geo-gaps` 在加州查出；映射表该行按脚本规则手工同步：birdnet_label `Icterus bullockii_Bullock's Oriole`、birdnet_how `synonym`）
  - 未收：`Alces alces → Alces americanus`。MDD v2.5 只有 `Alces alces`，没有 americanus，真值 `Alces alces` 已直接对上。
- `avilist_map.csv`（`uv run python scripts/build_name_map.py` 生成，需要 HF 缓存里的 TreeOfLife json 和 birdnet 包）：每个 AviList 种一行，`scientific, common, order, family, tol_name, tol_how, birdnet_label, birdnet_how`，`*_how` 取 `exact | synonym | none`。`birdnet_label` 是 BirdNET 原样的 `学名_俗名`，服务加载时建好"名单行号 → BirdNET 输出位置"索引，请求时一次数组查表得每行 `p_geo`（无标签的行为 0）。
- `candidates.csv`（同一脚本生成，只供人审，不会自动采纳）：映射为 none 的种里，同属且种加词编辑距离 ≤ 2 的 TreeOfLife / BirdNET 名字。`candidate_in_avilist=True` 表示候选本身也是 AviList 的另一个种，多半是不同种。确认后把对应行抄进 `synonyms.csv` 再重跑脚本。
- 哺乳只做 TreeOfLife 匹配（exact + tol/spelling 别名）和 eval 真值的 inat 别名，不做 BirdNET（哺乳无地理先验）。

覆盖率（2026-09-23）：

| 名单 | 总数 | TreeOfLife exact / synonym / none | 官方向量覆盖 | BirdNET exact / synonym / none |
|---|---|---|---|---|
| avilist-2025 | 11131 | 9416 / 2 / 1713 | 84.6% | 10380 / 3 / 748（93.3%） |
| mdd-2025 | 6904 | 3835 / 0 / 3069 | 55.5% | — |

旧版 BirdNET 匹配另外用俗名兜底（多 15 种），新版只按学名 + 别名，暂未把俗名匹配搬进映射表。`candidates.csv` 当前 49 条（distance 1：11 条）。
