# bioscan v1 实施计划（目标制）

spec：`docs/superpowers/specs/2026-09-22-bioscan-design.md`，以 spec 为准。每个目标交给一个独立子代理，在各自 worktree 完成，主线验收后合并。

## 共同约束
- 物种模型只有一个：**BioCLIP 2.5 Huge**，`hf-hub:imageomics/bioclip-2.5-vith14`，ViT-H/14，1024 维。不得用 bioclip-2、bioclip。
- Python 3.12，uv 管理。依赖版本沿用 PhotoOS（`~/workspace/personal/ImageProcess/PhotoOS/pyproject.toml`）：torch>=2.6、open-clip-torch>=3.3,<4、transformers>=4.51、rawpy==0.27.1、pillow、numpy、huggingface-hub、birdnet>=1.1,<2（geo）。不引入 typer/httpx(运行时)/rich/pydantic 之外的东西。
- 设备：MPS 优先，无 MPS 回退 CPU。HF 缓存沿用 `~/.cache/huggingface`，模型已在本机缓存，不要重复下载。
- 标准库能做的不装包。每个非平凡逻辑留一个可运行检查（pytest）。
- 不读图、不看图内容。冒烟只检查 JSON 结构与数值范围。
- 提交信息末尾加 `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`。

## 模块接口（跨目标契约）
`bioscan/service/names.py` 暴露：
```python
@dataclass
class NameList:
    list_id: str                 # "avilist-2025" | "mdd-2025"
    kind: str                    # "bird" | "mammal"
    scientific: list[str]
    common: list[str]
    taxonomy: list[list[str]]    # 7 级
    matrix: np.ndarray           # (N, 1024) float32, L2 归一化，BioCLIP 2.5 Huge 文本空间

def load_lists(model, tokenizer, device, cache_dir=Path("~/.cache/bioscan/names")) -> dict[str, NameList]  # key = kind
def stats(lists) -> dict  # 覆盖率等
```
engine 通过 `names.load_lists` 取名单；目标 S 在 names.py 未交付前用 `names_legacy.py`（同接口，数据源为 PhotoOS 现有 11045 鸟种名单）跑通。

## 目标 S：服务
范围：`bioscan/service/{app,engine,products,decode,names_legacy}.py`、`bioscan/service/adapters/*`、`tests/unit`、`tests/contract`、`tests/smoke`、`pyproject.toml`、`README.md` 骨架。
验收：
1. `uv run pytest tests/unit tests/contract` 全绿；契约测试用假 engine，覆盖 spec 4 节全部事件与错误约定、`want` 组合、锁排队、断开取消。
2. `uv run bioscan-serve`（或 `uv run python -m bioscan.service`）起服务后，`curl /health`、`/products` 符合 spec。
3. 对 `tests/smoke/` 5 张真图（从 `/Volumes/Media/bird/` 三个文件夹各取 1 到 2 张 ARW 复制进来，gitignore）`POST /run want=[identify,embed,jpg]`，每张返回 `result`，`boxes` 非空、`species.level` 有值、`embed.dim==768`、jpg 落盘；日志打印每张耗时。
4. 迁移自 PhotoOS 的 adapters 去掉 workflow/catalog/contract 包装，只留函数。

## 目标 N：名单
范围：`bioscan/service/names.py`、`data/avilist/`、`data/mdd/`、`data/README.md`、`scripts/build_names.py`、`tests/unit/test_names.py`。
验收：
1. 下载 AviList 2025 全表 CSV 与 MDD 最新 CSV 到 `data/`（大文件 gitignore，README 写清来源 URL 与版本）。
2. `load_lists` 产出 bird（AviList 全部种）与 mammal（MDD 全部种）两个 NameList，矩阵为 BioCLIP 2.5 Huge 文本空间；能按学名精确对上 HF `imageomics/TreeOfLife-200M` `embeddings/txt_emb_bioclip-2.5-vith14.{npy,json}` 的行直接取官方向量，对不上的用文本塔编码，文本格式与 TreeOfLife 一致。
3. 缓存 `~/.cache/bioscan/names/<model>-<list_sha>.npz`，二次加载 < 3 秒。
4. `stats()` 返回每个名单的总数、官方向量覆盖数、自编码数；单测覆盖名字规范化与映射逻辑（用小样本，不依赖 3.26 GB 文件）。

## 目标 C：CLI 与 harness
范围：`bioscan/cli/{main,client,render,gt,eval}.py`、`data/taxa.csv`、`tests/unit/test_cli_*.py`。只依赖 spec 4 节的 API，用契约里的假事件流测试，不依赖目标 S 的实现。
验收：
1. `bioscan run/health/serve/serve --launchd/gt folders/gt inat/eval/names stats` 全部子命令按 spec 7 节实现，argparse + urllib，无第三方运行时依赖。
2. `render` 对 spec 中示例事件流输出 spec 里的 pretty 样式与汇总。
3. `gt folders /Volumes/Media/bird` 产出 `groundtruth.csv`，三个文件夹名对上 AviList 学名（对照表可先内置最小映射，N 交付后改为查名单）。
4. `gt inat` 实现 iNaturalist v1 下载（限速、license 过滤、CSV 字段齐全）；`data/taxa.csv` 含加州约 20 种哺乳 + 约 40 种鸟 + 驯鹿/驼鹿/灰熊，两列 `scientific, common`。用 `--dry-run` 只打印将要请求的 URL，单测覆盖。
5. `eval` 读 preds.ndjson + groundtruth.csv 计算 spec 8 节全部指标并写 report.md；指标计算有单测。

## 目标 I：集成与 baseline（S、N、C 合并后）
1. engine 切到 `names.py`，删 `names_legacy.py`。
2. 跑 `gt folders` 建 own tier 真值；跑 `bioscan eval` 出 baseline 报告。
3. 抽样 40 张预测结果交由外部视觉模型独立复核，正确率目标 ≥ 85%。
