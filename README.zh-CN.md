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
| `kind_check` | **类别核对**（kind check）：每个框已算好的 BioCLIP 特征再与鸟+哺乳合并名单打一次分，框的类别取最好 5 个名字视觉概率之和更高的那张名单（每张名单取同样个数，名单长不占便宜），可以推翻门和裁切复判（门判哺乳的猫头鹰不再被叫成臭鼬）。改了类别但优势不足 0.75 的定为 `unconfirmed`。按视觉质量而不是后验比较，因为两张名单的先验覆盖不同。参与的名单：鸟、哺乳，以及加载了的全类群名单（其他动物），每张名单各做一次矩阵乘法、不合并。每张名单取同样 5 个名字，能消掉 AviList（1.1 万）和 MDD（6900）之间的大部分名单大小效应，但消不掉它们与约 37 万行全类群名单之间的：名单越大，最好 5 个名字的分数单靠偶然就越高。所以全类群名单只参与 `other_animal` 框的核对：`other_animal` 框可以移到鸟或哺乳，鸟和哺乳框不会移到其他动物。没有全类群名单时 `other_animal` 框不参与。代价（仅 `other_animal` 框）：每个框对约 37 万行多一次乘法（4 核 CPU 约 30 ms/框；MPS 估计 1–2 ms）。 | `rules.KIND_TOP` = 5，`rules.KIND_SURE` = 0.75；参与的名单见 `taxa.KIND_CHECK` |
| `mammal_geo` | **哺乳地理先验**：服务已加载的 BirdNET geo 模型也给 1,048 种哺乳打分，`data/names/mdd_map.csv` 把它们对到 MDD 行。没有标签的 MDD 行取同属有标签种里最高的 p_geo（**属回退**，unlabelled policy `genus`），同属都没标签时取 0.05。鸟保持原规则：无标签为 0（policy `zero`）。 | `geo.UNLABELLED_NEUTRAL` = 0.05；每张名单的 `names.LISTS[...].unlabelled` |

所有阈值、无标签策略、标签映射表内容和选项默认值都在 settings 指纹里；`result.engine.models.label_maps` 给出每张映射表及其 sha。三项全关时鸟和哺乳的 identify 输出与 v1.4 相同（在 300 帧替身模型录制、5 组选项上逐字节比对过）；整份输出相同仅限没有全类群名单时：有它时 `other_animal` 框会有物种，v1.4 是 `null`。测某一项：同一份真值跑两遍对比报告，例如 `bioscan eval GT.csv --out runs/x-no-veto --identify-opt range_veto=false`；HTTP 里传 `"options":{"identify":{"kind_check":false}}`。CI 的真模型冒烟把样本照片开、关各跑一遍，报告（`models-report`）里附开/关对照表和每张变了答案的图；任一类别丢了一张以上 Top-1 命中、或多了一张以上定到种的错误才失败：每类约 38 张，这只是绊线，真正的关卡是 `bioscan bench compare` 对照已提交基线的回退预算。

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
uv run bioscan config show                             # 当前生效的 profile、计划与设置，以及各自来源
```

```sh
bioscan run /path/to/photos                            # 目录按扩展名过滤、排序；-r 递归
bioscan run a.ARW b.ARW --want identify,embed --json --out preds.ndjson
bioscan run DIR --want jpg --jpg-out /tmp/jpg          # 旋正、长边 2048 的 JPG，文件名 <stem>-<sha256前8位>.jpg
bioscan run DIR --lat 37.4 --lon -122.1                # EXIF 无坐标时整批默认坐标（地理先验很重要）
bioscan run DIR --no-geo --top-k 10 --no-species
```
服务只加载本次运行的 stage 在其选项下需要的模型：`--want embed` 只加载 SigLIP2；`--no-species`（identify 选项 `species: false`，且没有 `candidates`）加载 SigLIP2 和 OWLv2，从不加载 BioCLIP 与名表，所以只处理过这类运行的服务在 `result.engine.models.names` 里没有名表。

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
| `--profile` / `BIOSCAN_PROFILE` | CLI 用的 profile（run、eval、bench run、config show），见 Profile 一节 | `default_profile`，否则 `full` |
| `BIOSCAN_CONFIG` | 再加一个 `bioscan.toml`，优先于项目与用户文件 | 无 |

上面除 URL 外的服务设置也可以写在 `bioscan.toml` 的 `[serve]` 表里（命令行 > 环境变量 > 文件 > 默认；见 Profile 与 bioscan.toml）。

### Profile 与 bioscan.toml

**profile** 是一份命名的请求模板：一次运行要哪些 stage、各带什么选项。内置三个（`bioscan/profiles.toml`）：

| Profile | 现在的 stage | 选项 | 加载的模型 | 以后会加 |
|---|---|---|---|---|
| `full` | identify（`want` 要时加 embed、jpg） | 默认 | SigLIP2、OWLv2、BioCLIP | 不加：不带 profile 的请求就是它，任何文件都改不了 |
| `wildlife` | geotag、identify | 物种、位置先验与各项准确率修正全开（`top_k` 5，`geo` true）；`geotag.gpx`（或 `run --gpx`）给出轨迹前 geotag 什么都不做 | SigLIP2、OWLv2、BioCLIP | 暂无 |
| `album` | identify、embed、aesthetics、quality、scene；reducer burst、select（由 `bioscan cull` 在 CLI 端跑，服务从不跑） | identify `species: false`；aesthetics `head: builtin`；scene 标签；burst、select 的阈值 | SigLIP2、OWLv2（从不加载 BioCLIP） | 暂无计划 |

```sh
bioscan run DIR --profile album                  # 用 profile 的 stage 与选项；命令行参数仍优先
bioscan run DIR --profile wildlife --top-k 10
bioscan eval GT.csv --out runs/x --profile wildlife   # bench run 同样可用；preds 的 meta 行记下 "profile"
bioscan config show --profile album              # 解析后的计划，以及每个值来自哪里（--json）
curl -sN 127.0.0.1:8765/run -H 'content-type: application/json' -d '{"inputs":[{"path":"/abs/a.ARW"}],"profile":"album"}'
```

自己的设置写在 `bioscan.toml`：项目文件（运行命令所在目录的 `./bioscan.toml`）和用户文件（`~/.config/bioscan/bioscan.toml`，或 `$XDG_CONFIG_HOME` 下）；`$BIOSCAN_CONFIG` 可再指定一个。文件可以逐键修改 `wildlife`、`album`，新增 profile，指定 CLI 的默认 profile，并放服务设置：

```toml
default_profile = "wildlife"          # 不给 --profile 时 CLI 用的 profile

[serve]                               # bioscan serve：命令行 > BIOSCAN_* 变量 > 本表 > 默认
port = 8765
chunk = 32
detail_edge = 3072
allow_roots = ["~/Pictures/Wildlife"]

[profile.wildlife.options.identify]   # 改内置 profile
top_k = 10

[profile.trip]                        # 或新增一个
stages = ["identify", "jpg"]
[profile.trip.options.jpg]
out_dir = "/Users/me/Pictures/trip-jpg"
[profile.trip.options.identify]
candidates = ["Strigidae", "Accipitridae"]
```

- **合并顺序**（由低到高）：各 stage 的默认 < `profiles.toml` < 用户文件 < 项目文件 < `$BIOSCAN_CONFIG` < 命令行参数或请求里的 `want` / `options`。`--want` 替换 profile 的 stage 列表。
- **用哪个 profile**：`--profile`，否则 `$BIOSCAN_PROFILE`，否则 `default_profile`，否则 `full`。都没有时 CLI 发出的请求与引入 profile 之前完全相同。CLI 自己展开 profile，只发普通的 `want` 和 `options`，服务端不需要你的配置文件。
- **服务端**用它自己的文件（启动时读一次）展开请求里的 `"profile"`。不带 `"profile"` 的请求永远是 `full`：`default_profile` 和 `$BIOSCAN_PROFILE` 只作用于 CLI，HTTP 客户端看不到变化。
- **错误**：未知的键、profile、stage 或选项都会报错并指出是哪个文件（请求里则是 400）。用户文件里写 `[profile.full]` 会被拒绝。选项的取值由服务检查。`bioscan serve --launchd` 把命令行参数、否则文件里 `[serve]` 的值写进 plist，并通过 `BIOSCAN_CONFIG` 让服务读当前目录的 `bioscan.toml`。
- **信任**：运行目录下的 `./bioscan.toml` 会被自动读取，只在你信任其文件的目录里运行 bioscan：它可以设 `serve.host = "0.0.0.0"`、`allow_roots`，或让 jpg 写到某个 `out_dir`；`bioscan config show` 会列出读到的每个文件及其设置的每个值。
- **Stage 与插件**：每个 stage 是 `bioscan/plugins/<name>/` 下的一个插件（标准库 manifest：读什么、提供什么、在给定选项下要哪些模型、有哪些选项及其取值检查；服务端代码在 `stage.py`，只为运行计划里的 stage 导入）。运行计划让 stage 排在它所读事实的提供者之后（同级按名字），只加载需要的模型；结果按 `identify, embed, jpg, geotag, aesthetics, quality, scene` 的顺序列出。v1.7 起新建的 stage 在运行包含它们时，把所用的东西写进 `result.engine.plugins`：输出依赖某个头文件的 stage 写该文件（aesthetics：`v1@<head id>`，`head: off` 时不写），否则写 `v<版本>@<设置指纹>`（quality、scene）。
- **Reducer**：`reducers = ["burst", "select"]` 指的是在一次运行的全部结果上跑的无模型单元，只在 CLI（`bioscan cull`、`bench`）或离线跑，从不在服务里跑，服务保持无状态。它们的选项和 stage 的写在一起（`[profile.album.options.select] per_category = 20`）；/run 请求不能设置它们。

### 用 GPX 轨迹补 GPS

多数相机不写 GPS。如果出行时用手表或手机记录了轨迹并导出 GPX，bioscan 可以按拍摄时间把每张照片放到轨迹上，作用相当于 Lightroom 地图模块的"自动标记照片"。位置很重要：golden 集上鸟类 Top-1 无坐标 83.3%、有坐标 89.8%（见 README 结果；两数在 docs/standards.md 第 4 节有争议）。GPX 坐标本身带来的提升尚未验证，要等 docs/harness.md 里的 Mac 运行。
```sh
bioscan geotag DIR --gpx hike.gpx --tz America/Los_Angeles --csv geo.csv   # path,lat,lon,source,dt_s,err_m,utc,ele
bioscan geotag DIR --gpx a.gpx --gpx b.gpx --offset +00:01:23 --xmp       # 相机快 83 秒；写 <stem>.xmp 旁车文件
bioscan geotag DIR --gpx hike.gpx --clock DIR/DSC0001.ARW=2026-05-01T08:00:13   # 一张拍手表的照片，表上是 08:00:13
bioscan run DIR --gpx hike.gpx --tz=-07:00             # identify 时每张图用自己的坐标（EXIF GPS 仍然优先；不需要 exiftool）
```
- **来源**：每张图的来源是以下三种之一：
  - `exif`：文件本身有 GPS，EXIF 永远优先；
  - `gpx`：由轨迹定位；
  - `none`：在轨迹之外，或没有拍摄时间。

  CSV 还给出 `dt_s`（离最近轨迹点的秒数）、`err_m`（误差估计；合成集上约 3/4 的定位落在其内）和校正后的 UTC 时间。
- **时间**：GPX 是 UTC，相机是本地钟点。文件有 OffsetTimeOriginal 就用它；否则按 `--tz` 解读。`--tz` 可以是固定偏移，也可以是时区名，时区名会按每个日期套用正确的夏令时。默认用本机时区。负值要写成 `--tz=-07:00`、`--offset=-3600`，否则 argparse 会把它当成选项。
- **相机时钟偏差**（相机时间减真实时间）按以下顺序取第一个可用的：
  1. `--offset`；
  2. 拍钟照片：`--clock 照片=时间`，写钟面显示的时间，按该照片的时区解读；
  3. 目录里已有 GPS 的照片（手机照片、带 GPS 连接的相机）：找出让这些照片落在轨迹上的偏差。若它们离轨迹超过 100 m 就放弃。若多个偏差同样吻合，先取 5 分钟以内的（普通漂移），其次整小时、半小时、一刻钟（即时区、夏令时错误），并告警说明吻合有歧义。所有照片都已有 GPS 时不做估计；
  4. 以上都没有则为 0。

  一次运行只用一个偏差，所以请一台相机一次。多数照片落在轨迹外时会告警并给出差多少；整小时通常是时区设错。
- **定位规则**：相邻轨迹点相隔不超过 `--max-gap` 秒（默认 1800）时，按时间线性插值。间隔更长时，只有两端相距不超过 `--max-span` 米（默认 200，即站着不动、手表自动暂停）且间隔不超过 `--max-still` 秒（默认 3 小时：在观鸟棚里等候可以，营地过夜不行）才插值。轨迹外不定位；加 `--extrapolate N` 时，在 N 秒内沿用首/末点。
- **XMP**：`--xmp` 写 `<stem>.xmp`，内含 XMP 的 `exif:GPSLatitude`/`GPSLongitude`。Lightroom、Capture One、Bridge 对 RAW 读这个旁车文件；Lightroom 不读 JPEG 的旁车文件。已有旁车文件（`<stem>.xmp`，或 darktable 的 `<name>.<ext>.xmp`）的照片一律跳过：bioscan 从不修改或合并已有旁车文件，也从不写照片文件本身。需要改已有文件时，请用 CSV 配合 exiftool。
- **多条轨迹**：多个 `--gpx` 文件、多个分段会合并成一条按时间排序的轨迹。第二台设备同时记录，只是多了点。
- **`run --gpx` 在哪里定位**：profile 含 `geotag` stage 时（`--profile wildlife`），CLI 发送 `options.geotag`（轨迹路径、`--tz` 作为 `camera_utc_offset`、你改过的限值，以及时钟偏差：由 CLI 用 `--offset`、`--clock` 或带 GPS 的照片为整个目录定一次）；服务读取轨迹，给请求和 EXIF 都没有位置的照片定位，并在 `products.geotag` 报告（`place_source` 为 request / exif / gpx / none，以及定位结果）。轨迹必须在服务的 allow-roots 之内。没有这样的 profile 时（不指定、`full`、`album`），`run --gpx` 在本机读轨迹、发送每张图的坐标，与以前完全相同；`bioscan geotag` 始终在本机运行。两条路径给 identify 的坐标相同。该 stage 也接受来自 /run 请求体或 `bioscan.toml`（`[profile.wildlife.options.geotag] gpx = [...]`）的同名选项；它自己从不估计时钟偏差，因为一次只看到一个 chunk（`offset` 为空即 0）。
- **精度**：在用 golden 集合成的轨迹上测得（`bioscan bench geotag`，见 docs/harness.md）：

  | 指标 | 汇总结果 |
  |---|---|
  | 误差中位数 | 7.2 m |
  | 误差 p90 | 17 m |
  | 100 m 内 | 97.2% |
  | 未定位 | 0.1% |
  | 误定位 | 0% |
  | 时钟偏差误差 | 中位数 1 秒 |

  各场景明细见 docs/2026-09-24-geotag-synthetic.md。

### 美学评分（album）

`aesthetics` stage 用一个小的线性**头**（head）给每帧打美学分，输入是整帧 pass 已经算好的 SigLIP2 向量：不加新模型，权重只有几 kB，每个 chunk 在 CPU 线程池上做一次矩阵乘。它在 `album` profile 里（`full`、`wildlife` 都不含），而且**只用来排序，从不剔除或删除任何一帧**；`select` reducer（见下文“相册挑片”）会读 `products.aesthetics.score`，在连拍组或类别内排序。

- **输出** `products.aesthetics`：`score`（0-1，即下面的混合分，不截断）、`general`、`personal`（没有个人头时为 null）、`head_id`（`name:sha12`，混合时为 `+name:sha12~blend`）。缺少通用头文件时 score 为 null，并有一条 `note` 说明原因；运行不会因此失败。
- **通用头**：`data/aesthetic/eva-head-v1.json`，在 **EVA** 上拟合的岭回归头（4070 张照片，每张 30 票以上，平均分 0-10）。**尚未提交**：由 `aesthetic` workflow 或在 Mac 上训练（命令见 `data/aesthetic/README.md`）；在那之前 album 运行报告 `score: null`。
- **个人头**：用你自己的评分拟合，向通用头收缩，再按 `blend`（个人头权重，默认 0.5）与通用头混合：

  ```sh
  bioscan aesthetic ratings ~/Pictures/Album                     # 按行程列出 XMP（旁车或内嵌）里的星级/色标
  bioscan aesthetic train --ratings ~/Pictures/Album --embeddings ~/.cache/bioscan/album-vec.ndjson
  #   -> ~/.config/bioscan/aesthetic-personal.json（岭回归，alpha 由按行程分折的 5 折交叉验证选出）
  bioscan run ~/Pictures/Album --profile album                   # 只用通用头，除非 profile 指定你的头：
  ```

  ```toml
  [profile.album.options.aesthetics]
  head = "/Users/me/.config/bioscan/aesthetic-personal.json"   # builtin | 绝对路径 | off
  blend = 0.5
  ```

  评分取 Lightroom 1-5 星（`xmp:Rating`；按 XMP 规范，0 或缺失 = 未评分，跳过；拒绝标记 -1 保留为拒绝，等级低于 1 星），有色标和 `xmpDM:pick` 时一并读取；旁车文件优先于内嵌 XMP。Lightroom Classic 的旗标（pick）存在目录库里、不写进 XMP，需要的话用 CSV（`path,rating,pick,trip`）提供。CLI 从不加载模型：向量来自正在运行的服务的 `embed` 产物（`--embeddings FILE` 可缓存）。头文件路径要在服务的 allow-roots 之内。
- **与你的一致程度**：`bioscan aesthetic eval ~/Pictures/Album --out runs/aes --personal ~/.config/bioscan/aesthetic-personal.json` 写出 report.json 和 report.md：与星级的 Spearman、Kendall；按行程对照你的 pick 的 NDCG@10 和 precision@k（k = 该行程里你的 pick 数，并列出随机顺序的期望值）；以及 50/100/200/500/1000 条评分下个人头、通用头、混合的**学习曲线**，始终按行程（文件夹）划分，连拍不会同时出现在训练和测试两侧。`bioscan bench scorecard runs/aes/report.json` 按 `aesthetic-own` 标准判定（docs/standards.md 第 13 节）。**美学相关数字都还没有实测，以上一律未验证。**
- **许可**：EVA 的标注是 **CC0 1.0**（见其仓库的 LICENSE）。图片是来自 dpchallenge.com 的 AVA 照片，版权属于原摄影师：bioscan 只用它们计算向量，从不再分发。头权重在本地或本仓库 CI 中训练，头文件记录数据、许可、样本数、日期、种子和交叉验证结果。**从不使用 AVA 评分，也不分发任何 AVA 训练的权重。**个人头用你自己对自己照片的评分拟合，只留在你的机器上。

### 相册挑片（cull）

`bioscan cull` 把一个文件夹整理成可审阅的结果：带原因的规则淘汰、连拍组及其最佳一张、每个场景类别里最好的照片。它先让服务跑 `album` profile，再在本地跑 `burst` 和 `select` 两个 reducer。它从不删除、移动或评分任何照片：淘汰只是列出来。

```sh
bioscan cull ~/Pictures/2026-05-trip -r --html review.html --csv selection.csv --link-dir picks --per-category 20
bioscan cull --preds cull.ndjson --html review.html      # 用保存的 --json 结果离线重跑（例如换阈值）
```

- **淘汰**（`quality`，只按规则，每张给出原因）：`soft_subject`（主体框发虚而画面别处清晰：对焦跑到了背景上）、`motion_or_defocus`（画面里没有清晰的地方：手抖、运动模糊或整体失焦，也包括柔和虚化背景前发虚的主体）、`overexposed`（主体 8% 像素过曝，或主体偏亮且 4% 过曝）、`underexposed`（整幅偏暗且主体也暗；只是黑色的鸟不算）、`subject_cut`（框碰到画面边缘且不是满画幅特写）、`subject_too_small`（不到画面的 0.5%）、`no_subject`（门类判断有动物，检测却没框出）。这里的清晰度是主体框中心区域的“再模糊”测度；所有阈值都是 `bioscan/plugins/quality/stage.py` 里的常量，并进入该 stage 的指纹。主体是 identify 的最佳框，没有动物的照片（风景、人像）按整幅画面判断。`select` 在 `night` 类别里豁免 `underexposed`。
- **场景**（`scene`）：在服务已算好的整幅 SigLIP2 向量上做零样本分类：landscape、people、wildlife（门类判断里 bird + mammal 的份额）、macro、architecture、food、night、other。标签和提示词可用 `[profile.album.options.scene.labels]` 修改。风景照还会给出地平线倾斜角（只报告，不淘汰）。
- **连拍**（`burst`）：同一台相机（EXIF 的 Make 与 Model）、按亚秒拍摄时间相隔不超过 1.5 s、整幅向量余弦不低于 0.92 的帧连成一组。
- **挑选**（`select`）：每组连拍的最佳一张依次看：未被淘汰、主体清晰度（与最清晰一张相差 0.03 以内算一样）、没被切、曝光在 ±0.2 以内，有美学分时再看美学分（只调整顺序，从不淘汰）。然后每个类别取前 `per_category` 张（默认 10；`--per-category`，0 表示全部），跳过与已选照片向量相似度达 0.95 的近重复。每张照片得到一个状态：`pick`、`spare`（超出前 N 的可留照片）、`duplicate` 或 `reject`。
- **输出**：`--csv`（每张一行：状态、keep、类别、名次、原因、连拍组、组内名次、重复自、清晰度、美学分、拍摄时间；失败的照片也在内）、`--link-dir`（每张入选照片在 `<dir>/<类别>/` 下建符号链接，从不覆盖已有文件）、`--html`（审阅页：各类别的入选、备选、连拍组、按原因分组的淘汰和失败；缩略图是服务写到 `<页面>-files/` 的旋正 JPEG，所以该目录要在服务的 allow-roots 里；`--no-thumbs` 不生成）、`--json`（带 `products.burst`、`products.select` 的结果，`cull --preds` 与 `bench report` 可读回）。
- **准确率**：在真实相册上未验证。CI 在用冒烟图片合成的淘汰集上测量规则与 reducer（docs/harness.md“Album tier”，docs/standards.md §14）；暂不写 XMP 评分或标签。

### HTTP API

```sh
curl -s 127.0.0.1:8765/health
curl -s 127.0.0.1:8765/products
curl -sN 127.0.0.1:8765/run -H 'content-type: application/json' \
  -d '{"inputs":[{"path":"/abs/a.ARW","lat":37.4,"lon":-122.1}],"want":["identify","embed","jpg"],"options":{"jpg":{"out_dir":"/tmp/jpg"}}}'
```
`want` 可以省略：这时运行其 profile 的 stage（默认 `full`：identify）。`"want": null` 返回 400，除非请求体同时给了 `"profile"`，那时等同于省略。
响应是 NDJSON 流：`progress` / `result` / `error` / `done`，字段定义在 `bioscan/contract.py`，`identify` 产物（gate、boxes、quality、species、候选）也定义在那里；`result`、`done` 带 `schema: 1`。多个请求按 chunk 轮流使用模型（单张请求最多等一个 chunk），一个 chunk 内各模型阶段跨图批处理，CPU 解码与推理流水。`result.engine` 含模型版本、名单版本、`settings`（规则阈值/提示词/词表的指纹，变了说明结果不可直接比）和 `detail_edge`；运行里有依赖训练文件的 stage 时，`engine.plugins` 写明用的是哪个文件（`{"aesthetics": "v1@eva-head-v1:<sha12>+…"}`）。完整契约见 `docs/superpowers/specs/2026-09-22-bioscan-design.md` 第 4 节。

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

### 评测框架：基线、对照、失败分析

`bioscan bench` 建在 eval 之上，保证结果不会悄悄变差。完整流程和各文件格式见 [docs/harness.md](docs/harness.md)。
```sh
bioscan bench run data/inat/groundtruth-inat.csv --out runs/<date>-golden --tier golden   # eval + report.json
bioscan bench report runs/<date>/preds.ndjson GT.csv                                    # 离线从 preds 重建 report.json
bioscan bench baseline runs/<date>-golden/report.json --name golden-inat-<tag>          # 存为 baselines/ 下的基线
bioscan bench compare baselines/golden-inat-<tag>.json runs/<new>/report.json          # 超出预算退出码 1
bioscan bench analyze runs/<new>/report.json                                            # 失败分类，下一步修什么
bioscan bench scorecard runs/<new>/report.json                                          # 对照 data/standards.toml
bioscan bench geotag runs/geotag-synth                                                  # 在合成轨迹上评 GPX 定位
```
- **基线流程**：今天跑一遍，用 `bench baseline` 存成基线并提交；之后换模型或改代码，再跑一遍，用 `bench compare` 对照基线。
- **report.json**（`bioscan-report` v1）：git sha、引擎、settings 指纹、真值 sha；按范围（`all`、`bird`、`mammal`、`other`，及按 tier）的全部指标和 Wilson 95% 区间；按种、按科的表；每张图一行；`meta.profile`，以及 `plugin_metrics`：各插件自己的指标（album 层级按原因的淘汰精确率与召回率、keepers lost、连拍成对 F1、场景准确率），带 Wilson 区间，预算与标准都可以引用。
- **compare** 按 sha256（其次路径）配对图片。指标、按种变化和回退预算（`baselines/budget.toml`）都只按配对上的图片算，测试集加了新图不算回退，新图单独列出。它统计修好/改坏的图并给出精确 McNemar p 值，列出改坏图片的证据。退出码：0 预算内，1 超预算，2 无法对照。
- **analyze** 把每个错答归入一个失败类：门漏判、检测漏框、类别错、不在名录、分布外、被地点先验压下、同属错、同科错、远错；另标出定到种却错的答案。每类附例图和该改哪段代码的提示。

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
- 哺乳地理先验、分布否决、类别核对在真实照片上的效果未验证，要等 Mac 上的 `bioscan eval`（CI 开/关对照只覆盖 95 张：42 鸟、35 哺乳、18 其他动物）。ε、τ、类别核对阈值和中性常数都是初值。
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

CI（`.github/workflows/`）：`ci.yml` 每次 push 跑 ruff + pytest；`models.yml` 在改动服务代码、真模型测试、名字数据或依赖的 push / PR 上，用 CPU 跑真模型冒烟（权重与图片有缓存），指标写进 job summary；每次还用 `bioscan bench compare` 把本次 report.json 对照 `baselines/ci-smoke.json`（预算见 `baselines/budget.toml`），超出预算 job 失败。随后用其中 24 张合成相册淘汰集跑 album profile，把 `models-report-album.json` 对照 `baselines/ci-album.json`（预算 `baselines/budget-album.toml`；该基线提交前 job 只打印候选报告）。推 `v*` tag 时同样运行，并把报告作为 artifact 发布、打印到日志。`aesthetic.yml` 只按需运行（Actions 页面，或推送 `aesthetic-head-*` tag）：用 CPU 训练 EVA 通用头，并把头文件以 base64 打印到日志（见 data/aesthetic/README.md）。

## 布局

```
bioscan/contract.py              /run 事件、产物名与 identify 输出的唯一定义（CLI 与服务共用，纯标准库）
bioscan/naming.py                名称归一化（学名；gt 文件夹名）、synonyms.csv、映射表过期检查（纯标准库）
bioscan/formats.py               支持的照片扩展名（解码与目录扫描共用）、目录扫描（纯标准库）
bioscan/profile.py               profile 与 bioscan.toml：分层、合并顺序、解析出的计划（仅标准库；CLI 与服务共用）
bioscan/profiles.toml            内置 profile：full、wildlife、album
bioscan/serve_config.py          服务设置：参数 > BIOSCAN_* > 默认值，两个入口共用一次解析（纯标准库）
bioscan/service/app.py           路由、请求校验、允许目录、NDJSON 流
bioscan/service/run.py           一次 /run 的事件流：分 chunk、按 chunk 的模型轮次、解码进程池自愈
bioscan/service/engine.py        设备选择、经 Loaders 惰性加载模型（测试注入假适配器）、每类先验
bioscan/plugin.py                stage 插件的声明（Manifest）与实现接口（Stage）；运行计划（仅标准库）
bioscan/plugins/<name>/          内置 stage：identify、embed、jpg、geotag、aesthetics、quality、scene；__init__.py 是标准库 manifest，
                                 stage.py 是服务端代码；reducer manifest：burst、select
bioscan/service/stages.py        服务端的 stage：选项合并与校验、/products、allow-roots 路径
bioscan/service/pipeline.py      identify 编排（跨图批处理），经 Models 协议访问模型
bioscan/service/rules.py         复判 / 定级 / 画质 / 裁切等纯规则与阈值
bioscan/service/taxa.py          门类提示词、检测词表、可提升的类别
bioscan/service/settings.py      影响输出的设置指纹
bioscan/service/decode.py        RAW/JPG → 旋正 2048 图 + 细节图 + EXIF（各 RAW 容器）+ sha256
bioscan/service/names.py         AviList / MDD 名单、TreeOfLife 映射、文本向量缓存
bioscan/service/adapters/        siglip2 owlv2 bioclip geo
bioscan/geotag.py                GPX 解析、拍摄时间转 UTC、时钟偏差、轨迹插值、XMP 旁车文件（纯标准库）
bioscan/aesthetic.py             美学头文件、XMP/CSV 评分、按行程分折、排序指标（纯标准库）
bioscan/aesthetic_fit.py         岭回归头、交叉验证、向先验收缩、学习曲线（numpy；只在训练或算曲线时导入）
bioscan/cull.py                  burst、select reducer，挑片记录，album 层级的评测行函数（纯标准库）
bioscan/cli/                     main client render gt eval bench config（profile）geotag_cli（geotag、run --gpx）geobench（bench geotag）
                                 aesbench（bioscan aesthetic ratings|train|eval）
                                 cull（bioscan cull：reducer、CSV、符号链接、HTML 审阅页）
scripts/geotag_synth.py          用 golden 集合成 GPX 场景，供 bench geotag 使用
scripts/train_aesthetic_head.py  EVA 通用美学头，进程内用服务的 decode 与 SigLIP2
scripts/cull_synth.py            用带主体框的照片合成相册集（带标签的淘汰图、连拍）
data/aesthetic/                  通用美学头（训练出来之前只有 README）及其来源说明
data/names/                      AviList 为准的名字映射表
docs/                            设计 spec、实施计划、评测结果
```

## 许可

代码 MIT（见 `LICENSE`）。模型与数据各有许可，见上表；BirdNET 先验为 CC BY-NC-SA 4.0，商业使用请自行评估。
