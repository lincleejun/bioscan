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
- 匹配：`norm(属 + " " + 种加词)`（下划线转空格、合并空白、小写）精确相等，且 TreeOfLife 行的纲必须是 Aves / Mammalia（避免跨界同名，例如 *Oenanthe albifrons* 在 TreeOfLife 里只有植物那一行）。同一学名多行时取科相同的那行，否则取第一行。不做同物异名映射。
- 对不上的名字用 BioCLIP 2.5 Huge 文本塔编码，文本与 TreeOfLife-toolbox `processing/scripts/make_txt_embedding.py` 完全一致：`"an image of {界 门 纲 目 科 属 种加词} with common name {俗名}."`（无俗名时省掉 ` with common name …`），L2 归一化。实测对 10 个 TreeOfLife 行用此模板重编码，与官方向量余弦均为 1.0000。
- 缓存：`~/.cache/bioscan/names/bioclip-2.5-vith14-<sha>.npz`，sha 取 CSV 内容 + list_id + 缓存版本。建完缓存后 3.26 GB 的官方文件可删。

## 实测（2026-09-22，M 系列 Mac，MPS，`uv run python scripts/build_names.py`）

| 名单 | 总数 | 官方向量 | 自编码 | 覆盖率 | 缓存文件 |
|---|---|---|---|---|---|
| avilist-2025 | 11131 | 9416 | 1715 | 84.6% | 56.2 MiB |
| mdd-2025 | 6904 | 3835 | 3069 | 55.5% | 35.0 MiB |

- 首次构建（官方文件已在 HF 缓存，不含下载与模型加载 4.5 s）：23.0 s
- 二次加载：同进程 0.03 s；新进程冷启动（不加载模型）0.06 s
