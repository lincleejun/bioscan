# bioscan

**照片里有什么动物、在哪、是什么种。** 面向野生动物摄影的本地识别服务：输入一批 RAW 或 JPG，输出每张图的动物框、物种（鸟到种，哺乳到种）、置信度与定级。常驻 HTTP 服务 + 薄 CLI，Mac 上跑 MPS，不上传任何照片。

*bioscan is a local, offline animal-identification service for wildlife photographers: give it a folder of RAW/JPG files and get back, per image, bounding boxes, species (birds via AviList 2025, mammals via the Mammal Diversity Database), confidence and a species/genus/family/unconfirmed grade. Zero-shot BioCLIP 2.5 Huge + OWLv2 + SigLIP2, with a BirdNET location prior. Runs on Apple Silicon (MPS) or CPU. Chinese documentation below.*

## 结果

### iNaturalist golden 集（加州，65 种 × 25 张 = 1625 张 research-grade 观察，真实 GPS 与日期）

| 类群 | n | 门准确率 | 检出率 | Top-1 | Top-5 | 覆盖率 | 精度 |
|---|---|---|---|---|---|---|---|
| 鸟（1050） | 1050 | 97.0% | 97.0% | **89.8%** | 95.0% | 95.6% | 93.4% |
| 哺乳（575） | 575 | 89.7% | 84.9% | **74.1%** | 81.4% | 82.3% | 89.2% |

- 覆盖率 = 定级到"种"的比例；精度 = 定级到"种"时 Top-1 正确率。两者一起看，避免只提覆盖不提精度。
- 地理先验的作用（鸟）：Top-1 83.3% → 89.8%，把北鹞从 1/25 提到 25/25。
- 哺乳的失败集中在"没框"：黑熊、美洲狮、灰熊、短尾猫共 45 张检测词表没框到主体，是当前最大的已知短板。
- 真值来自 iNaturalist 社区核验，CC0 / CC BY / CC BY-NC，仅用于评测，图片不随仓库分发；`data/inat/groundtruth-inat.csv` 保留了每张的观察链接与署名。

### 自有照片（长焦 RAW，404 张，3 种）

| 地理先验 | Top-1 | Top-5 | 覆盖率 | 精度 |
|---|---|---|---|---|
| 无（照片无 GPS） | 80.2% | 98.0% | 85.9% | 85.3% |
| 有（整批给一个坐标） | **96.3%** | 98.0% | 97.8% | 97.7% |

西美角鸮、东美角鸮、须角鸮外形几乎相同，靠分布区分；没有坐标时 72 次判错，有坐标后 7 次。

### 速度（M 系列 Mac，MPS）

| 步骤 | 每张 |
|---|---|
| RAW 解码 + 旋正 + 缩放（USB 机械盘） | 约 650 ms |
| 识别（门 + 检测 + 复判 + 物种） | 约 210 ms |
| JPG 直读 | 约 1 ms |

冷启动加载三个模型约 11 s，之后常驻。

完整数字与混淆榜：`docs/2026-09-23-baseline-results.md`。

## 目标与边界

做的：
- 单次扫描一个目录，一口气出结果，边跑边打。
- 三个产物可以任意组合：`identify`（框 + 物种）、`embed`（整图 SigLIP2 向量）、`jpg`（RAW 转旋正 JPG）。
- 名字以 **AviList 2025**（鸟，11131 种）和 **MDD v2.5**（哺乳，6904 种）为唯一标准；BirdNET、TreeOfLife/BioCLIP、iNaturalist 的名字都通过 `data/names/` 的映射表归一到它们。
- 自带评测：`bioscan gt` 建真值集（文件夹名或 iNaturalist），`bioscan eval` 出报告。

不做的（v1）：
- 缓存与持久状态、人工纠正回写、照片管理、Web UI。
- 个体识别（同一只动物跨照片）。
- 相册软件集成（Immich 等，通过同一 HTTP API 后续接入）。

## 流程

```
RAW/JPG ─ decode ─▶ 旋正 2048 图 + EXIF(GPS, 时间) + sha256
              │
              ├─ SigLIP2 整图 ─▶ 门：bird / mammal / other_animal / person / none    ─▶ embed 产物
              │
              ├─ OWLv2 开放词表检测（词表按门选）─▶ 每框 SigLIP2 裁切复判 ─▶ 画质
              │
              └─ BioCLIP 2.5 Huge 对裁切框编码 ─▶ 与该纲名单的文本向量做余弦
                        × (0.02 + BirdNET 地理先验)  ─▶ 归一化 ─▶ top-k ─▶ 定级
```

定级规则：top-1 ≥ 0.5 且领先第二名 ≥ 0.3 定为种；否则 top-5 按属累加 ≥ 0.6 定为属，按科累加 ≥ 0.6 定为科；否则 `unconfirmed`。

模型与数据：

| 用途 | 来源 | 许可 |
|---|---|---|
| 门、复判、embed | `google/siglip2-base-patch16-224` | Apache-2.0 |
| 检测 | `google/owlv2-base-patch16-ensemble` | Apache-2.0 |
| 物种 | `imageomics/bioclip-2.5-vith14`（BioCLIP 2.5 Huge） | MIT |
| 物种名文本向量 | `imageomics/TreeOfLife-200M` 官方预计算向量，对不上的名字用文本塔自编 | CC0 |
| 地理先验（仅鸟） | BirdNET geo 3.0（`birdnet` 包） | CC BY-NC-SA 4.0 |
| 鸟名单 | AviList v2025 | CC BY 4.0 |
| 哺乳名单 | Mammal Diversity Database v2.5 | CC BY 4.0 |

BirdNET 的先验模型是非商业许可，商业使用需去掉先验或换来源。

## 安装

```sh
git clone https://github.com/lincleejun/bioscan && cd bioscan
uv sync                                   # Python 3.12
```

模型权重从 `~/.cache/huggingface` 读，服务本身离线（`HF_HUB_OFFLINE=1`）。新机器先联网拉一次：
```sh
uv run python -c "from huggingface_hub import snapshot_download as s; s('google/siglip2-base-patch16-224'); s('google/owlv2-base-patch16-ensemble', revision='cfd3195ba4ea9592eec887ded089f4c08eff231d', ignore_patterns=['*.bin']); s('imageomics/bioclip-2.5-vith14', ignore_patterns=['*.bin'])"
```

名单 CSV 体积大、不进 git，按 `data/README.md` 下载放到 `data/avilist/`、`data/mdd/`。首次启动会把名单编成 BioCLIP 文本向量并缓存到 `~/.cache/bioscan/names/`（需要 TreeOfLife-200M 的 3.26 GB 官方向量文件，建完可删，约半分钟），之后秒开。

```sh
uv run bioscan names stats                # 名单覆盖率
```

## 使用

```sh
uv run bioscan serve                                   # 127.0.0.1:8765，模型常驻
uv run bioscan serve --launchd > ~/Library/LaunchAgents/cc.outman.bioscan.plist   # macOS 开机常驻
uv run bioscan health
```

```sh
bioscan run /path/to/photos                            # 目录按扩展名过滤、排序；-r 递归
bioscan run a.ARW b.ARW --want identify,embed --json --out preds.ndjson
bioscan run DIR --want jpg --jpg-out /tmp/jpg          # 旋正、长边 2048 的 JPG
bioscan run DIR --lat 37.4 --lon -122.1                # EXIF 无坐标时整批默认坐标（地理先验很重要）
bioscan run DIR --no-geo --top-k 10 --no-species
```

终端输出一张一行，末尾汇总：
```
DSC00364.ARW  bird    2 boxes  [1] Western Screech-Owl 0.91 种  [2] unconfirmed
DSC00458.ARW  none
DSC00566.ARW  mammal  1 box    [1] Rangifer tarandus 0.77 种
```

`--json` 时把服务返回的 NDJSON 原样写出，一张图一行，供下游程序读取：
```json
{"type":"result","path":"/abs/a.ARW","sha256":"…","image":{"width":6000,"height":4000,"orientation":1},
 "engine":{"version":"0.1.0","models":{"species":"imageomics/bioclip-2.5-vith14","names":{"bird":"avilist-2025@…"}}},
 "products":{"identify":{"gate":{"class":"bird","probs":{…}},
   "boxes":[{"id":0,"xyxy":[0.31,0.22,0.58,0.71],"score":0.84,"kind":"bird",
             "quality":{"sharpness":0.71,"exposure":0.05},
             "species":{"list":"avilist-2025","level":"species",
               "top":[{"scientific":"Megascops kennicottii","common":"Western Screech-Owl",
                       "taxonomy":["Animalia","Chordata","Aves","Strigiformes","Strigidae","Megascops","Megascops kennicottii"],
                       "p_visual":0.81,"p_geo":0.62,"posterior":0.91}]}}]}},
 "timing_ms":{"decode":650,"identify":210}}
```

### 端口与环境变量

| 名称 | 作用 | 默认 |
|---|---|---|
| `--port` | 服务端口 | 8765 |
| `BIOSCAN_URL` / `--url` | CLI 连哪个服务 | `http://127.0.0.1:8765` |
| `--decode-workers` / `BIOSCAN_DECODE_WORKERS` | 解码进程数（USB 机械盘 4 左右最佳） | 4 |
| `--chunk` / `BIOSCAN_CHUNK` | 流水 chunk 张数 | 32 |

### HTTP API

```sh
curl -s 127.0.0.1:8765/health
curl -s 127.0.0.1:8765/products
curl -sN 127.0.0.1:8765/run -H 'content-type: application/json' \
  -d '{"inputs":[{"path":"/abs/a.ARW","lat":37.4,"lon":-122.1}],"want":["identify","embed","jpg"],"options":{"jpg":{"out_dir":"/tmp/jpg"}}}'
```
响应是 NDJSON 流：`progress` / `result` / `error` / `done`。请求之间串行排队，一批之内 CPU 解码与 GPU 推理流水。完整契约见 `docs/superpowers/specs/2026-09-22-bioscan-design.md` 第 4 节。

## 评测

```sh
bioscan gt folders /path/to/photos --out data/groundtruth-own.csv \
  --names data/avilist/AviList-v2025-11Jun-extended.csv --names data/mdd/MDD_v2.5_6904species.csv
bioscan gt inat --place california --taxa data/taxa.csv --per-species 25 --out data/inat   # --dry-run 只打印 URL
bioscan eval data/inat/groundtruth-inat.csv --out runs/<date>          # 调服务，写 preds.ndjson + report.md
bioscan eval data/inat/groundtruth-inat.csv --out runs/<date>-nogeo --no-geo
bioscan eval GT.csv --out runs/x --preds runs/<date>/preds.ndjson        # 只重算指标
```
真值格式：`path, scientific, tier, lat, lon, taken_at, source, kind`。真值学名会先经 `data/names/synonyms.csv` 归一到 AviList/MDD 再比较。

## 名字映射

`data/names/avilist_map.csv`：每个 AviList 种对应的 TreeOfLife 名和 BirdNET 标签及匹配方式（exact / synonym / none）。`synonyms.csv` 是手工维护的别名表，每条带来源和说明；`candidates.csv` 是脚本列出的疑似拼写差异，只供人审，不自动采纳。重建：`uv run python scripts/build_name_map.py`。

| 名单 | 总数 | TreeOfLife 官方向量 | BirdNET 标签 |
|---|---|---|---|
| AviList 2025 | 11131 | 84.6% | 93.3% |
| MDD v2.5 | 6904 | 55.5% | 不适用 |

## 已知局限与路线

- 哺乳检测词表缺大型食肉兽（熊、美洲狮、短尾猫），golden 集 45 张没框。
- 哺乳没有地理先验。
- 近期拆分的种（北鹞 / 白尾鹞、美洲仓鸮 / 西方仓鸮）在训练数据里用旧名，靠共用向量 + 地点先验区分；同义词目前不按地区生效。
- 先验公式的底数 0.02 限制了地点对视觉的纠正幅度，尚未在 golden 集上调参。
- 主体在画面里很小的图（远处猛禽）检测框会选错目标。
- 只在 macOS + MPS 和 CPU 上跑过；没有 CUDA 配置和 Dockerfile。

## 测试

```sh
uv run pytest tests/unit tests/contract          # 75 个，无模型，秒级
uv run python tests/smoke/run_smoke.py --url ...  # 需起服务，tests/smoke/*.ARW 自备
```

## 布局

```
bioscan/service/app.py           路由、NDJSON 流、请求锁与队列
bioscan/service/engine.py        设备选择、懒加载、batch 常量
bioscan/service/products.py      identify / embed / jpg，定级 / 复判 / 画质规则
bioscan/service/decode.py        RAW/JPG → 旋正 2048 图 + EXIF + sha256
bioscan/service/names.py         AviList / MDD 名单、TreeOfLife 映射、文本向量缓存
bioscan/service/adapters/        siglip2 owlv2 bioclip geo
bioscan/cli/                     main client render gt eval
data/names/                      AviList 为准的名字映射表
docs/                            设计 spec、实施计划、评测结果
```

## 许可

代码 MIT（见 `LICENSE`）。模型与数据各有许可，见上表；BirdNET 先验为 CC BY-NC-SA 4.0，商业使用请自行评估。
