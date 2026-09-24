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
- `mdd_map.csv`（`uv run python scripts/build_name_map.py --list mammal --mdd-synonyms …/Species_Syn_Current_v2.5.csv` 生成，2026-09-24）：每个 MDD 种一行，`scientific, common, order, family, birdnet_label, birdnet_how`。只有 BirdNET 标签，没有 TreeOfLife 列：哺乳的 TreeOfLife 匹配仍在建缓存时现算，所以这个文件不进哺乳缓存的 key，加它不会重建缓存。
  - 输入：BirdNET geo v3.0.4 的标签文件 `labels_raw-8250b457e45d.txt`（14082 行）与分类表 `taxonomy_v0.2-Jun2026.csv`（birdnet 1.1.1 下载到 `~/.local/share/birdnet/` 的同一对文件，Apache-2.0，见 geomodel 仓库 LICENSE-MODELS.md），按 birdnet 包的规则拼成 `学名_英文名` 标签；其中分类表 `class_name == mammalia` 的 1048 个（两文件 sha256 前缀即文件名里的 `8250b457e45d`、`98b27fc4a77c`）；MDD 仓库 `assets/data/MDD.zip` 里的 `MDD/Species_Syn_Current_v2.5.csv`（取自提交 `749c2de`，同一 zip 的 `MDD_v2.5_6904species.csv` 与上文 sha256 相同；sha256 `6467d05eef4a45fddf2cab97fcd6dd19aeb1b4da3d70ff6d23e92c99351cdfd4`）。离线构建时用 `--labels`（一行一个标签，模型原序）和 `--birdnet-taxonomy` 指定文件。
  - 匹配：学名 `norm_binomial` 精确相等（1028 个标签）→ MDD 同义名表（原始组合名、规范化原始组合名、种加词放进现行属）→ 无。同义名得到两个 MDD 种时必须在脚本的 `REVIEWED` 里人工定，否则脚本报错。
  - 同义名 20 个，已逐条人审（2026-09-24）：拼写 4 个（*Saguinus weddelli*→*weddellii*、*Hypsugo alaschanicus*→*alashanicus*、*Lophiomys imhausi*→*imhausii*、*Rattus lutreolus*→*R. lutreola*）；换属 5 个（*Tadarida aegyptiaca*→*Nyctinomus aegyptiacus*、*Pecari tajacu*→*Dicotyles tajacu*、*Bison bonasus*/*bison*→*Bos*、*Parotomys brantsii*→*Otomys brantsii*、*Pipistrellus abramus*→*Alionoctula abramus*）；MDD 并种 11 个（*Cebus yuracus*/*versicolor*/*cuscinus*/*aequatorialis*→*C. albifrons*、*C. imitator*→*C. capucinus*、*Cephalophorus harveyi*→*C. natalensis*、*Microtus miurus*→*M. abbreviatus*、*Plecturocebus discolor*→*P. cupreus*、*P. aureipalatii*→*P. toppini*、*Myotis dinellii*→*M. levis*）。有歧义的两个：*Rattus lutreolus*（同义名表还指向 *R. fuscipes*）取 *R. lutreola*；*Pipistrellus abramus*（还指向 *Alionoctula paterculus*）取 *A. abramus*。
  - 一行多个标签（MDD 并了 BirdNET 分开的种）用 `|` 连接，先验取其中最大值：*Cebus albifrons*（4 个）、*Cebus capucinus*、*Plecturocebus cupreus*、*Plecturocebus toppini*（各 2 个）。
  - 结果：1042 行有标签（exact 1028、synonym 14），1048 个哺乳标签全部用上；golden 集 23 种哺乳里 21 种有标签，*Lepus californicus*、*Sylvilagus audubonii* 按属回退。
  - 没有标签的行按名单的 unlabelled policy 处理：哺乳为 `genus`（取同属有标签种的最大 p_geo，同属都没有时取 `geo.UNLABELLED_NEUTRAL` 0.05），鸟为 `zero`（按 0，行为不变）。属回退得到的 p_geo 不触发分布否决。

覆盖率（2026-09-23）：

| 名单 | 总数 | TreeOfLife exact / synonym / none | 官方向量覆盖 | BirdNET exact / synonym / none |
|---|---|---|---|---|
| avilist-2025 | 11131 | 9416 / 2 / 1713 | 84.6% | 10380 / 3 / 748（93.3%） |
| mdd-2025 | 6904 | 3835 / 0 / 3069 | 55.5% | 1028 / 14 / 5862（15.1%，其余按属回退） |

旧版 BirdNET 匹配另外用俗名兜底（多 15 种），新版只按学名 + 别名，暂未把俗名匹配搬进映射表。`candidates.csv` 当前 49 条（distance 1：11 条）。
