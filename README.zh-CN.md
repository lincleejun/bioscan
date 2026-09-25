# bioscan

[English](README.md) | **简体中文**

**照片里是什么动物、在哪里、是哪个物种。** 面向野生动物摄影的本地识别服务：给它一个 RAW 或 JPG 文件夹，每张照片返回动物框、物种（鸟类和哺乳类对照权威名单，其他动物对照 TreeOfLife 全类群名单）、置信度和定级。它还能用 GPX 轨迹给照片补 GPS，把一个相册挑成精选、连拍和带理由的废片。常驻的 HTTP 服务加一个轻量 CLI；在 Mac 上跑 MPS；照片不出本机。

## 结果

2026-09-24 在 owner 的 M 系列 Mac（MPS）上实测，v1.5（`875dc7a`）。完整表格、与 v1.4 的对照、失败分类和记分卡见 [docs/results.md](docs/results.md)；这些数字对照的标准见 [docs/standards.md](docs/standards.md)。

**物种识别**，iNaturalist golden 集（加州，65 种 × 25 = 1625 张 research-grade 照片，带 GPS 和日期）：

| 组 | Top-1 | Top-5 | 检出 | 覆盖 | 精度 | 自信错误 |
|---|---|---|---|---|---|---|
| 鸟类（1050） | 91.2% | 95.7% | 97.9% | 92.5% | 97.1% | 2.7% |
| 哺乳类（575） | 82.8% | 86.3% | 89.6% | 81.4% | 95.1% | 4.0% |
| 全部（1625） | 88.2% | 92.4% | 95.0% | 88.6% | 96.5% | 3.1% |

覆盖 = 定到种级的照片比例；精度 = 其中 Top-1 正确的比例；自信错误 = 定到种级但错了。相对 v1.4：Top-1 +3.6 个百分点，自信错误 7.4% → 3.1%，覆盖 −2.9 个百分点，吞吐 4.7 → 2.2 张/秒（两者都是有意的取舍：定级更严，且每个框还要对照 36.6 万种的全类群名单）。自有长焦 RAW 集（404 张，3 种猫头鹰，无 GPS）：Top-1 79.0%，Top-5 98.0%；整批给一个坐标后 96.3%。

**美学评分**，通用头从未见过的 100 张 EVA 照片（每星 20 张，评分人意见一致）：与人群星级的 Spearman 0.877 [0.82, 0.92]，drop AUC 0.98，砍掉最低 20% 时 keeper 损失 0%。**相册挑片**规则只在合成废片集上测过（CI 照片做的 224 帧：拒绝召回 84%，精度 96%；欠曝召回 33%，低于 80% 的门槛）；真实相册上未验证。

**用 GPX 轨迹补 GPS**，由 golden 集生成的合成轨迹：中位误差 7 m，97% 在 100 m 内，无假定位。喂给物种识别后，GPX 位置和真 GPS 的答案完全一致（1625 张无一不同），比无坐标 Top-1 高 5.2 个百分点。

## 快速开始

```sh
git clone https://github.com/lincleejun/bioscan && cd bioscan
uv sync                                       # Python 3.12
uv run python tests/models/download.py        # 固定版本的权重（约 7 GB）+ BirdNET 地理模型，一次
# 名单：按 data/README.md 下载 AviList 和 MDD 的 CSV（首次启动用 3.26 GB 的 TreeOfLife 向量文件建缓存）
uv run bioscan serve                          # 127.0.0.1:8765；模型常驻
uv run bioscan run ~/Pictures/trip -r         # 每张一行，最后一个汇总
```

```
DSC00364.ARW  bird    2 boxes  [1] Western Screech-Owl 0.91 种  [2] unconfirmed
DSC00458.ARW  none
DSC00566.ARW  mammal  1 box    [1] Rangifer tarandus 0.77 种
```

`--json` 原样写出服务的 NDJSON（框、带分类学的物种、p_visual / p_geo / posterior、耗时）。完整安装说明、全部参数和输出结构：[docs/usage.md](docs/usage.md)（英文）。

## 能做什么

| | 命令 | 文档 |
|---|---|---|
| **识别**框和物种，定级为种 / 属 / 科 / unconfirmed；地理先验来自 EXIF GPS 或 `--lat/--lon`；`--candidates` 缩到候选类群 | `bioscan run DIR` | [usage.md](docs/usage.md)、[how-it-works.md](docs/how-it-works.md) |
| **补 GPS**：用手表或手机的 GPX 轨迹；时钟偏差按相机（EXIF 品牌 + 型号）分别定，来自表盘照片或带 GPS 的照片；写 XMP sidecar | `bioscan geotag DIR --gpx track.gpx` | [geotag.md](docs/geotag.md) |
| **挑片**：带理由的废片、连拍及其最佳一帧、每个场景类别的最佳照片、美学排序；HTML 审阅页、CSV、符号链接，可选把星级和颜色标签写进新的 XMP sidecar；从不删除 | `bioscan cull DIR -r --html review.html` | [album.md](docs/album.md) |
| **汇总一次运行**：类别、物种和带规则理由的待审队列写成 `summary.json`，再渲染为 HTML 报告 | `bioscan summarize preds.ndjson --out run`, `bioscan report run` | [usage.md](docs/usage.md#run-summary-and-report) |
| **给文件夹打分**：美学排序导出为 NDJSON、CSV 和/或带缩略图的 HTML 画廊；可从 NDJSON 离线重新导出 | `bioscan aesthetic score DIR -r --export json,csv,html --out aes` | [album.md](docs/album.md) |
| **个人美学**：用你的 Lightroom 星级拟合一个头，与通用头混合 | `bioscan aesthetic train --ratings DIR` | [album.md](docs/album.md) |
| **Profile** `full`、`wildlife`、`album`，以及 `bioscan.toml` 里你自己的 | `bioscan run DIR --profile album` | [usage.md](docs/usage.md#profiles-and-bioscantoml) |
| **评测**：从文件夹或 iNaturalist 建真值，带 Wilson 区间的报告，基线与回归预算，失败分类，对照标准的记分卡 | `bioscan eval`、`bioscan bench` | [development.md](docs/development.md)、[harness.md](docs/harness.md) |
| **HTTP API**：`/run` 流式返回 NDJSON；`/health`、`/products` | `curl 127.0.0.1:8765/run` | [usage.md](docs/usage.md#http-api) |

模型：SigLIP2（门控、embed）、OWLv2（检测）、BioCLIP 2.5 Huge（物种）、BirdNET geo（地理先验）、AviList 2025 与 MDD v2.5 名单、TreeOfLife-200M 全类群名单。来源和许可：[docs/how-it-works.md](docs/how-it-works.md#pipeline)。

## 现状

- 物种准确率在上面的 golden 集上实测；`docs/standards.md` 的 v0.x 发布门槛还没达到（哺乳类的检出和 Top-5 不够）。
- 其他动物（爬行类、鱼类、昆虫……）用 TreeOfLife 的名单命名，它缺很多爬行类和辐鳍鱼；它们没有地理先验。准确率只在 CI 的 18 张照片上测过。
- 挑片规则、连拍分组和美学头在真实相册上未验证；`bioscan aesthetic eval` 可以量它和你自己星级的一致程度。
- 只在 macOS + MPS 和 CPU 上跑过；没有 CUDA 配置和 Dockerfile。
- 完整清单：[docs/how-it-works.md#known-limitations-and-roadmap](docs/how-it-works.md#known-limitations-and-roadmap)。

## 文档

详细文档为英文。

| 文档 | 内容 |
|---|---|
| [docs/usage.md](docs/usage.md) | 安装、CLI、支持的 RAW 格式、端口与环境变量、候选类群、profile 与 `bioscan.toml`、HTTP API、退出码 |
| [docs/how-it-works.md](docs/how-it-works.md) | 范围、流程、定级与准确率规则、模型与数据及许可、名字映射、已知局限 |
| [docs/geotag.md](docs/geotag.md) | GPX 补 GPS：来源、时区、时钟偏差、定位规则、XMP、精度 |
| [docs/album.md](docs/album.md) | 美学（通用头与个人头、评测、golden 集）和 `bioscan cull` |
| [docs/results.md](docs/results.md) | 实测数字：v1.4 对 v1.5、自有照片、RAW 元数据、美学与挑片 |
| [docs/standards.md](docs/standards.md) | 11 个维度的标准及其出处，发布阶段 |
| [docs/development.md](docs/development.md) | 评测命令、评测框架简介、测试、CI、源码布局 |
| [docs/harness.md](docs/harness.md) | `bioscan bench` 全貌：report.json 结构、compare、预算、analyze、scorecard、album 与美学 tier |
| [data/README.md](data/README.md) | 名单、全类群名单、golden 真值、美学头 |
| [CONTEXT.md](CONTEXT.md) | 代码、测试和文档共用的领域术语 |

## 许可

代码为 MIT（见 `LICENSE`）。模型和数据各有许可，列在 [docs/how-it-works.md](docs/how-it-works.md#pipeline)；BirdNET 先验为 CC BY-NC-SA 4.0，商用请自行评估。
