# bioscan

[English](README.md) | **简体中文**

**照片里有什么动物、在哪、是什么种。** 面向野生动物摄影的本地识别服务：输入一批 RAW 或 JPG，输出每张图的动物框、物种（鸟到种，哺乳到种）、置信度与定级。常驻 HTTP 服务 + 薄 CLI，Mac 上跑 MPS，不上传任何照片。

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

### 标准与目标

[`docs/standards.md`](docs/standards.md) 规定 bioscan 按哪些标准衡量，共 11 个维度（分类群准确率、可信度、检测、位置、目录级验收测试、速度、名单覆盖、输入稳健性、上手、隐私、可复现）。每一项给出业界水平（附来源）、社区门槛、冲刺目标、当前状态和测量方法；并定义发布阶段：v0.x "试用并帮忙鉴定"，然后 v1.0 "打包发布"。达到门槛才邀请社区；v0.x 的门槛目前尚未全部达成。同一套标准的机器可读版本在 `data/standards.toml`，供 `bioscan bench scorecard` 读取。

## 目标与边界

做的：
- 单次扫描一个目录，一口气出结果，边跑边打。
- 三个产物可以任意组合：`identify`（框 + 物种）、`embed`（整图 SigLIP2 向量）、`jpg`（RAW 转旋正 JPG）。
- 鸟和哺乳的名字以 **AviList 2025**（鸟，11131 种）和 **MDD v2.5**（哺乳，6904 种）为标准；BirdNET、TreeOfLife/BioCLIP、iNaturalist 的名字都通过 `data/names/` 的映射表归一到它们。
- 默认全类群：其他动物（爬行、两栖、鱼、昆虫、蜘蛛……）用 TreeOfLife-200M 全类群名单（`tol200m-animalia`）命名，不用先指定类群。可选的 `candidates`（候选类群）只在你已知答案范围时缩小排序：`bioscan run DIR --candidates "Megascops kennicottii,Strigidae,Bubo"`，或请求里 `options.identify.candidates`。学名或任意上级类群（属、科、目、纲）都行，跨所有已加载名单匹配；只有含匹配行的名单参与，且只用匹配的行；鸟、哺乳框只在有匹配行的鸟、哺乳名单间比较，两者都没有时才去其他名单（全类群名单）；类别核对开着时只在这些名单间比较，关着时框在自己名单有匹配行时保留原类别，否则去证据最强的那张；不认识的名字返回 400 并列出。细节见英文 README 的 "All taxa and candidates" 和 `data/README.md`。
- 自带评测：`bioscan gt` 建真值集（文件夹名或 iNaturalist），`bioscan eval` 出报告。

不做的（v1）：
- 缓存与持久状态、人工纠正回写、照片管理、Web UI。
- 个体识别（同一只动物跨照片）。
- 相册软件集成（Immich 等，通过同一 HTTP API 后续接入）。

## 流程

```
RAW/JPG ─ decode ─▶ 旋正 2048 图 + EXIF(GPS, 时间) + sha256 （identify 时另出长边 ≤3072 的细节图）
              │
              ├─ SigLIP2 整图 ─▶ 门：bird / mammal / other_animal / person / none    ─▶ embed 产物
              │
              ├─ OWLv2 开放词表检测（词表按门选；门判 none/person 但三类动物合计 ≥0.25 时
              │   仍按最强动物类的词表查一遍）─▶ 每框 SigLIP2 裁切复判 ─▶ 画质
              │
              └─ BioCLIP 2.5 Huge 对细节图上同一取景的裁切编码 ─▶ 鸟、哺乳两张名单合起来做类别核对
                        ─▶ 与该类名单的文本向量做余弦 × (0.02 + BirdNET 地理先验)
                        ─▶ 归一化 ─▶ top-k ─▶ 分布否决 ─▶ 定级
```

定级规则：top-1 ≥ 0.5 且领先第二名 ≥ 0.3 定为种；否则 top-5 按属累加 ≥ 0.6 定为属，按科累加 ≥ 0.6 定为科；否则 `unconfirmed`。分布否决和类别核对（下节）可以降低这个级别。

### 准确率规则（v1.5）

三项修正针对"定到种却错了"的情况，各是一个 `identify` 选项，默认开，可以单独关掉来测效果：

| 选项 | 作用 | 常量 |
|---|---|---|
| `range_veto` | **分布否决**（range veto）：地点已知且名单有地理先验时，top-1 自身的 p_geo < ε 就不能定到种；若返回的候选里有同属且 p_geo ≥ τ 的种，把它排到第一（`top` 唯一不按后验排序的情况），级别按属/科累加定。解决加州渡鸦被认成菲律宾乌鸦。借自同属的 p_geo（见下）不触发否决。 | `rules.RANGE_EPS` ε = 0.01，`rules.RANGE_TAU` τ = 0.05 |
| `kind_check` | **类别核对**（kind check）：每个框已算好的 BioCLIP 特征再与鸟+哺乳合并名单打一次分，框的类别取最好 5 个名字视觉概率之和更高的那张名单（每张名单取同样个数，名单长不占便宜），可以推翻门和裁切复判（门判哺乳的猫头鹰不再被叫成臭鼬）。改了类别但优势不足 0.75 的定为 `unconfirmed`。按视觉质量而不是后验比较，因为两张名单的先验覆盖不同。参与的名单：鸟、哺乳，以及加载了的全类群名单（其他动物），每张名单各做一次矩阵乘法、不合并。每张名单取同样 5 个名字，能消掉 AviList（1.1 万）和 MDD（6900）之间的大部分名单大小效应，但消不掉它们与约 47 万行全类群名单之间的：名单越大，最好 5 个名字的分数单靠偶然就越高。所以全类群名单只参与 `other_animal` 框的核对：`other_animal` 框可以移到鸟或哺乳，鸟和哺乳框不会移到其他动物。没有全类群名单时 `other_animal` 框不参与。代价（仅 `other_animal` 框）：每个框对约 47 万行多一次乘法（4 核 CPU 约 30 ms/框；MPS 估计 1–2 ms）。 | `rules.KIND_TOP` = 5，`rules.KIND_SURE` = 0.75；参与的名单见 `taxa.KIND_CHECK` |
| `mammal_geo` | **哺乳地理先验**：服务已加载的 BirdNET geo 模型也给 1,048 种哺乳打分，`data/names/mdd_map.csv` 把它们对到 MDD 行。没有标签的 MDD 行取同属有标签种里最高的 p_geo（**属回退**，unlabelled policy `genus`），同属都没标签时取 0.05。鸟保持原规则：无标签为 0（policy `zero`）。 | `geo.UNLABELLED_NEUTRAL` = 0.05；每张名单的 `names.LISTS[...].unlabelled` |

所有阈值、无标签策略、标签映射表内容和选项默认值都在 settings 指纹里；`result.engine.models.label_maps` 给出每张映射表及其 sha。三项全关时 identify 输出与 v1.4 相同（在 300 帧替身模型录制、5 组选项上逐字节比对过）。测某一项：同一份真值跑两遍对比报告，例如 `bioscan eval GT.csv --out runs/x-no-veto --identify-opt range_veto=false`；HTTP 里传 `"options":{"identify":{"kind_check":false}}`。CI 的真模型冒烟把样本照片开、关各跑一遍，报告（`models-report`）里附开/关对照表和每张变了答案的图；任一类别丢了一张以上 Top-1 命中、或多了一张以上定到种的错误才失败：每类约 38 张，这只是绊线，真正的关卡是 `bioscan bench compare` 对照已提交基线的回退预算。

模型与数据：

| 用途 | 来源 | 许可 |
|---|---|---|
| 门、复判、embed | `google/siglip2-base-patch16-224` | Apache-2.0 |
| 检测 | `google/owlv2-base-patch16-ensemble` | Apache-2.0 |
| 物种 | `imageomics/bioclip-2.5-vith14`（BioCLIP 2.5 Huge） | MIT |
| 物种名文本向量 | `imageomics/TreeOfLife-200M` 官方预计算向量，对不上的名字用文本塔自编 | CC0 |
| 地理先验（鸟、哺乳） | BirdNET geo 3.0（`birdnet` 包） | CC BY-NC-SA 4.0 |
| 鸟名单 | AviList v2025 | CC BY 4.0 |
| 哺乳名单 | Mammal Diversity Database v2.5 | CC BY 4.0 |

BirdNET 的先验模型是非商业许可，商业使用需去掉先验或换来源。

## 安装

```sh
git clone https://github.com/lincleejun/bioscan && cd bioscan
uv sync                                   # Python 3.12
```

模型权重从 `~/.cache/huggingface` 读，服务本身离线（`HF_HUB_OFFLINE=1`）。三个模型和 TreeOfLife 向量都钉在固定的 HF 提交上（`siglip2.REVISION`、`bioclip.REVISION`、`owlv2.REVISION`、`names.TOL_REVISION`），`result.engine.models` 里带着这些版本。新机器（或升级到钉版本之后缓存里没有对应快照时）先联网拉一次：
```sh
uv run python tests/models/download.py      # 三个模型的钉定版本 + BirdNET geo 模型，打印各自版本
```
名单向量缓存会记录建它时的 BioCLIP / TreeOfLife 版本，版本变了自动重建；早于记录的旧缓存照常使用。

名单 CSV 体积大、不进 git，按 `data/README.md` 下载放到 `data/avilist/`、`data/mdd/`。首次启动会把名单编成 BioCLIP 文本向量并缓存到 `~/.cache/bioscan/names/`（需要 TreeOfLife-200M 的 3.26 GB 官方向量文件，约半分钟）；同时用这个文件建全类群名单（不编码，float16 缓存约 1 GB）。两者都建好后才可删它；删了且全类群缓存缺失时服务照常启动，其他动物 `species: null`，跑 `uv run python tests/models/download.py` 可重建。之后启动要几秒（主要是全类群名单）。改动 `data/names/synonyms.csv` 或 `avilist_map.csv` 会让鸟类缓存重建一次；`mdd_map.csv` 只有标签，不影响哺乳缓存。

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
bioscan run DIR --want jpg --jpg-out /tmp/jpg          # 旋正、长边 2048 的 JPG，文件名 <stem>-<sha256前8位>.jpg
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

### 支持的文件

`bioscan run` 与 `bioscan gt folders` 扫描下表所有扩展名，大小写不限（扫描与解码共用 `bioscan/formats.py` 这一份清单）；`--ext` 可缩小或扩大范围。GPS 与拍摄时间取自文件 EXIF，用于地理先验；请求里的 `lat`/`lon`/`taken_at` 优先。

| 格式 | 扩展名 | 解码 | GPS + 拍摄时间 | `jpg` 预览 | 真实相机文件验证 |
|---|---|---|---|---|---|
| Sony | `.arw` | rawpy（LibRaw） | TIFF IFD | 是 | 解码：是（自有照片）；EXIF：无记录 |
| Nikon | `.nef` `.nrw` | rawpy | TIFF IFD | 是 | 否 |
| Canon（旧机型） | `.cr2` | rawpy | TIFF IFD | 是 | 否 |
| Canon（R 系列、M50 等） | `.cr3` | rawpy | CR3 的 `CMT1`/`CMT2`/`CMT4` box | 是 | 否 |
| Fujifilm | `.raf` | rawpy | 内嵌 JPEG 的 EXIF | 是 | 否 |
| OM System / Olympus | `.orf` | rawpy | TIFF IFD（ORF 文件头） | 是 | 否 |
| Panasonic | `.rw2` | rawpy | TIFF IFD（RW2 文件头）；没有时读内嵌 JpgFromRaw | 是 | 否 |
| Pentax、Samsung | `.pef` `.srw` | rawpy | TIFF IFD | 是 | 否 |
| DNG | `.dng` | rawpy | TIFF IFD | 是 | 否 |
| JPEG | `.jpg` `.jpeg` | Pillow | EXIF | 是 | 是（iNat golden 集） |
| PNG、TIFF、WebP | 仅 `--ext` 或直接给文件路径 | Pillow | 有 EXIF 时读取 | 是 | 否 |
| HEIC / HEIF | 不支持 | 否：Pillow 需要 `pillow-heif` 插件；该图返回 `error` 事件 | 否 | 否 | — |

每种格式的元数据读取都用合成的小文件测试过（`tests/unit/test_raw_exif.py`）；新增的 CR3、RAF、ORF、RW2 还没有在真实相机文件上跑过。不启动服务即可检查自己的文件（只读元数据）：
```sh
uv run python -m bioscan.service.decode /path/to/card -r    # 每个文件：扩展名、容器、lat、lon、taken_at；最后按扩展名计数
```
相机写了亚秒（`SubSecTimeOriginal`）时，`taken_at` 保留小数部分，连拍各帧时间不同且有序：`2026-05-01T08:00:00.37-07:00`。`bioscan gt folders` 用 `exiftool` 读 DateTimeOriginal、SubSecTimeOriginal 与 OffsetTimeOriginal（CLI 不加载 Pillow），写成同样的格式。

### 端口与环境变量

| 名称 | 作用 | 默认 |
|---|---|---|
| `--port` | 服务端口 | 8765 |
| `BIOSCAN_URL` / `--url` | CLI 连哪个服务 | `http://127.0.0.1:8765` |
| `--decode-workers` / `BIOSCAN_DECODE_WORKERS` | 解码进程数（USB 机械盘 4 左右最佳） | 4 |
| `--chunk` / `BIOSCAN_CHUNK` | 流水 chunk 张数 | 32 |
| `--detail-edge` / `BIOSCAN_DETAIL_EDGE` | 物种裁切用细节图的长边；≤2048 关闭（回到 2048 图上裁） | 3072 |
| `--allow-root` / `BIOSCAN_ALLOW_ROOTS` | 只允许读写这些目录下的文件（可重复；环境变量用 `:` 分隔）；不设则不限制，监听非本机地址时会告警 | 不限 |

### HTTP API

```sh
curl -s 127.0.0.1:8765/health
curl -s 127.0.0.1:8765/products
curl -sN 127.0.0.1:8765/run -H 'content-type: application/json' \
  -d '{"inputs":[{"path":"/abs/a.ARW","lat":37.4,"lon":-122.1}],"want":["identify","embed","jpg"],"options":{"jpg":{"out_dir":"/tmp/jpg"}}}'
```
响应是 NDJSON 流：`progress` / `result` / `error` / `done`，字段定义在 `bioscan/contract.py`，`identify` 产物（gate、boxes、quality、species、候选）也定义在那里；`result`、`done` 带 `schema: 1`。多个请求按 chunk 轮流使用模型（单张请求最多等一个 chunk），一个 chunk 内各模型阶段跨图批处理，CPU 解码与推理流水。`result.engine` 含模型版本、名单版本、`settings`（规则阈值/提示词/词表的指纹，变了说明结果不可直接比）和 `detail_edge`。完整契约见 `docs/superpowers/specs/2026-09-22-bioscan-design.md` 第 4 节。

CLI 退出码：0 全部成功，1 部分图片失败，2 连不上服务或服务拒绝，3 流中断（没收到 `done`）或上游（iNaturalist 等）出错。`run --json` 过去总是返回 0，现在也按这套退出码返回，脚本里若把非 0 当失败需留意。eval 的学名比较改用与 synonyms 查找相同的归一化（忽略连字符与大小写），旧报告的 Top-1/Top-5 可能因此有细微差别。

## 评测

```sh
bioscan gt folders /path/to/photos --out data/groundtruth-own.csv \
  --names data/avilist/AviList-v2025-11Jun-extended.csv --names data/mdd/MDD_v2.5_6904species.csv
bioscan gt inat --place california --taxa data/taxa.csv --per-species 25 --out data/inat   # --dry-run 只打印 URL
bioscan eval data/inat/groundtruth-inat.csv --out runs/<date>          # 调服务，写 preds.ndjson + report.md
bioscan eval data/inat/groundtruth-inat.csv --out runs/<date>-nogeo --no-geo
bioscan eval GT.csv --out runs/x --preds runs/<date>/preds.ndjson        # 只重算指标
```
`preds.ndjson` 第一行是 meta（schema、请求参数、真值与 synonyms.csv 的 sha256），重算时按它报告当时是否开了地理先验；流中断时 eval 退出码为 3。
报告里"没框"按整图门类拆开：`none/person` 是门漏判（检测器没跑），其余是检测器没框到。
真值格式：`path, scientific, tier, lat, lon, taken_at, source, kind`。真值学名会先经 `data/names/synonyms.csv` 归一到 AviList/MDD 再比较。

## 名字映射

`data/names/avilist_map.csv`：每个 AviList 种对应的 TreeOfLife 名和 BirdNET 标签及匹配方式（exact / synonym / none）。`synonyms.csv` 是手工维护的别名表，每条带来源和说明；`candidates.csv` 是脚本列出的疑似拼写差异，只供人审，不自动采纳。重建：`uv run python scripts/build_name_map.py`。

没有 BirdNET 标签的 748 个 AviList 种在地理先验里按 0 处理（多为灭绝种或被 BirdNET 并入姊妹种，如 *Tyto javanica*，应当被压低）。要找某地真正该补的缺口：
```sh
bioscan names geo-gaps --lat 37.4 --lon -122.1 --date 2026-05-01   # 同属在当地有分布、自己却没标签的种
```
确认后把对应行写进 `synonyms.csv`（source `birdnet`）再重建映射表。

`data/names/mdd_map.csv`：每个 MDD 种对应的 BirdNET 标签及匹配方式，只用 BirdNET 里纲为 Mammalia 的标签；先按学名精确匹配，再查 MDD 同义名表（已人工审过，见 `data/README.md`）。MDD 把 BirdNET 分开的几个种并成一个时（如四种白额卷尾猴并入 *Cebus albifrons*），该行列出全部标签，用 `|` 连接，先验取最大值。重建：`uv run python scripts/build_name_map.py --list mammal --mdd-synonyms MDD/Species_Syn_Current_v2.5.csv`。

| 名单 | 总数 | TreeOfLife 官方向量 | BirdNET 标签 |
|---|---|---|---|
| AviList 2025 | 11131 | 84.6% | 93.3% |
| MDD v2.5 | 6904 | 55.5% | 15.1%（1042 行覆盖全部 1,048 个 BirdNET 哺乳标签，其余按属回退） |

## 已知局限与路线

- 哺乳 golden 集 45 张没框（熊、美洲狮、短尾猫为主）。已补检测词表并加了门漏判时的补查，效果待 `bioscan eval` 复测。
- 哺乳地理先验、分布否决、类别核对在真实照片上的效果未验证，要等 Mac 上的 `bioscan eval`（CI 开/关对照只覆盖 77 张）。ε、τ、类别核对阈值和中性常数都是初值。
- 分布否决可能把真正的迷鸟改名成本地同属种（只定到属，不会定到种）。
- 近期拆分的种（北鹞 / 白尾鹞、美洲仓鸮 / 西方仓鸮）在训练数据里用旧名，靠共用向量 + 地点先验区分；同义词目前不按地区生效。
- 先验公式的底数 0.02 限制了地点对视觉的纠正幅度，尚未在 golden 集上调参。
- 主体在画面里很小的图（远处猛禽）检测框会选错目标。
- 只在 macOS + MPS 和 CPU 上跑过；没有 CUDA 配置和 Dockerfile。

## 测试

```sh
uv run ruff check .
uv run pytest                                     # 无模型，秒级；tests/models 默认跳过
uv run python tests/models/download.py && BIOSCAN_MODEL_TESTS=1 uv run pytest tests/models   # 真模型冒烟，95 张 iNat 图（含 18 张其他动物）
uv run python tests/smoke/run_smoke.py --url ...  # 需起服务，tests/smoke/*.ARW 自备
```

CI（`.github/workflows/`）：`ci.yml` 每次 push 跑 ruff + pytest；`models.yml` 在改动服务代码、真模型测试、名字数据或依赖的 push / PR 上，用 CPU 跑真模型冒烟（权重与图片有缓存），指标写进 job summary。

## 布局

```
bioscan/contract.py              /run 事件、产物名与 identify 输出的唯一定义（CLI 与服务共用，纯标准库）
bioscan/naming.py                名称归一化（学名；gt 文件夹名）、synonyms.csv、映射表过期检查（纯标准库）
bioscan/formats.py               支持的照片扩展名（解码与目录扫描共用）、目录扫描（纯标准库）
bioscan/serve_config.py          服务设置：参数 > BIOSCAN_* > 默认值，两个入口共用一次解析（纯标准库）
bioscan/service/app.py           路由、请求校验、允许目录、NDJSON 流
bioscan/service/run.py           一次 /run 的事件流：分 chunk、按 chunk 的模型轮次、解码进程池自愈
bioscan/service/engine.py        设备选择、经 Loaders 惰性加载模型（测试注入假适配器）、每类先验
bioscan/service/products.py      产物注册表：依赖、选项、校验、schema、执行器
bioscan/service/pipeline.py      identify 编排（跨图批处理），经 Models 协议访问模型
bioscan/service/rules.py         复判 / 定级 / 画质 / 裁切等纯规则与阈值
bioscan/service/taxa.py          门类提示词、检测词表、可提升的类别
bioscan/service/settings.py      影响输出的设置指纹
bioscan/service/decode.py        RAW/JPG → 旋正 2048 图 + 细节图 + EXIF（各 RAW 容器）+ sha256
bioscan/service/names.py         AviList / MDD 名单、TreeOfLife 映射、文本向量缓存
bioscan/service/adapters/        siglip2 owlv2 bioclip geo
bioscan/cli/                     main client render gt eval
data/names/                      AviList 为准的名字映射表
docs/                            设计 spec、实施计划、评测结果
```

## 许可

代码 MIT（见 `LICENSE`）。模型与数据各有许可，见上表；BirdNET 先验为 CC BY-NC-SA 4.0，商业使用请自行评估。
