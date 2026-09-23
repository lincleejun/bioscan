# bioscan 设计（v1）

日期：2026-09-22。状态：待审。

## 1. 目标

把 PhotoOS 里的动物识别 pipeline 抽成一个独立工具 `bioscan`：常驻 HTTP 服务 + 薄 CLI，输入照片路径，输出 JSON 或终端摘要，回答"这张图里有什么动物、在哪、是什么种"。

使用者是 outman 本人，场景是野外拍摄回来后的批量初筛。以后 Immich 通过同一 API 逐张调用（本版不做）。

成功判据：
- `bioscan run <目录>` 能一口气扫完几百到几千张 RAW，边跑边出结果。
- `bioscan eval` 在自建真值集上跑通，出一份带 Top-1、覆盖率 × 精度的报告。第一次跑出的数字就是 baseline。
- 识别范围从"鸟到种、哺乳只到纲"扩到"鸟 + 哺乳到种"，名单用 AviList 和 Mammal Diversity Database。

## 2. 非目标（v1 明确不做）

- 缓存、持久状态、人工纠正回写
- Immich 集成、webhook 入口
- 个体聚类、连拍分组、keep 探针、画质以外的策展逻辑
- Dockerfile（Mac 上 Docker 拿不到 MPS，只跑原生进程）
- 哺乳动物地理先验
- eval 的跨 run 对比（`--compare`）
- 任何照片管理、浏览、Web UI

## 3. 背景与已知事实

- PhotoOS 现有链路：SigLIP2 场景门 → OWLv2 开放词表检测（框再用 SigLIP2 复判）→ BioCLIP 2.5 Huge 对 11045 鸟种零样本打分 → BirdNET 地理先验重排 → 定级（种/属/科/unconfirmed）。哺乳和其他动物无物种头。
- BioCLIP 2.5 Huge 的输入是裁切框像素，不使用 SigLIP2 的向量。两者向量空间互不通用。
- 三个模型常驻 MPS，冷启动数十秒，所以做常驻服务。（2026-09-23 更正：实现是 fp32，约为 fp16 估算 3.5 GB 的两倍，24 GB 统一内存仍放得下；fp16 需在 Mac 上跑 `bioscan eval` 对比精度后再定。）
- 真值现状：`/Volumes/Media/bird/` 三个物种文件夹共 404 张；阿拉斯加 `ak_selected` 含驯鹿等哺乳；PhotoOS `review/` 有一组约 90 到 100 张的测试卡。哺乳动物真值几乎为零，需要用 iNaturalist 补广度集。
- AviList 2025 是统一的全球鸟类清单（11131 种，含目/科/属层级），只是名单不含照片。哺乳用 Mammal Diversity Database（MDD）。BioCLIP 官方预计算的名字向量基于 TreeOfLife 分类，与 AviList 有拆并差异，需要映射表。

## 4. API 契约

服务监听 `http://127.0.0.1:8765`。三个端点。

### `GET /health`
```json
{"status": "ok", "device": "mps", "models_loaded": ["siglip2", "owlv2", "bioclip"], "running": 1, "queued": 0}
```

### `GET /products`
返回三个产物的名字、说明、options schema、输出 schema。CLI 的校验和 `--help` 从这里取。

### `POST /run`
请求：
```json
{
  "inputs": [
    {"path": "/Volumes/Media/bird/a.ARW", "lat": 37.4, "lon": -122.1, "taken_at": "2026-05-01T08:00:00Z"}
  ],
  "want": ["identify", "embed"],
  "options": {
    "identify": {"top_k": 5, "geo": true, "species": true},
    "embed": {"format": "list"},
    "jpg": {"out_dir": "/tmp/jpg"}
  }
}
```
- `path` 是服务本机可读的绝对路径。不传字节。
- `lat/lon/taken_at` 可省，省略时读 EXIF；请求给的值优先于 EXIF。
- `want` 是三个产物的任意非空子集，互相独立。
- `options` 按产物分组，缺省用默认值。
- 校验失败（未知产物、空 inputs、非绝对路径）返回 400。模型加载失败返回 503。

响应 `Content-Type: application/x-ndjson`，一行一个事件，顺序不保证按输入顺序：

| type | 字段 | 说明 |
|---|---|---|
| `progress` | `product, done, total` | 每个 chunk 每个产物完成后发 |
| `result` | `path, sha256, image{width, height, orientation}, engine{version, models{}}, products{...}, timing_ms{decode, identify, embed, jpg}` | 一张图全部请求产物完成后发 |
| `error` | `path, product?, message` | 单图失败，批不中断；`product` 为空表示解码失败 |
| `done` | `ok, failed, elapsed_ms` | 收尾，恒为最后一行 |

`sha256` 是原文件内容哈希，供以后做键。

### 产物输出

**identify**
```json
{
  "gate": {"class": "bird", "probs": {"bird": 0.93, "mammal": 0.02, "other_animal": 0.01, "person": 0.0, "none": 0.04}},
  "boxes": [
    {
      "id": 0,
      "xyxy": [0.31, 0.22, 0.58, 0.71],
      "score": 0.84,
      "kind": "bird",
      "quality": {"sharpness": 0.71, "exposure": 0.05},
      "species": {
        "list": "avilist-2025",
        "level": "species",
        "top": [
          {"scientific": "Megascops kennicottii", "common": "Western Screech-Owl",
           "taxonomy": ["Animalia", "Chordata", "Aves", "Strigiformes", "Strigidae", "Megascops", "Megascops kennicottii"],
           "p_visual": 0.81, "p_geo": 0.62, "posterior": 0.91}
        ]
      }
    }
  ]
}
```
- `xyxy` 归一化到 0–1，基准是按 EXIF 旋正后的原图。
- `kind` 取值 `bird | mammal | other_animal`。
- `level` 取值 `species | genus | family | unconfirmed`。`unconfirmed` 时 `top` 仍返回，供人看。
- `list` 取值 `avilist-2025 | mdd-2025`；`other_animal` 无名单，`species` 为 `null`。
- gate 为 `none` 或 `person` 时 `boxes` 为空数组，不跑检测。
- `options.identify.species=false` 时 `boxes[].species` 省略，只检测。
- 哺乳无地理先验：`p_geo` 为 `null`，`posterior` 等于 `p_visual`。

**embed**
```json
{"model": "siglip2-base-patch16-224", "dim": 768, "vector": [0.01, ...]}
```
`format` 取值 `list`（默认）或 `f16_base64`。

**jpg**
```json
{"path": "/tmp/jpg/a.jpg", "width": 2048, "height": 1365}
```
写旋正后的长边 2048 图，质量 92。文件名取原名换扩展名；已存在则覆盖。

### 版本
每条 `result` 的 `engine` 带 `version`（bioscan 版本）和 `models{gate, detect, species}`（模型标识和名单哈希）。以后换模型能区分结果来源。

## 5. 服务内部

进程：uvicorn 单 worker。模型常驻 MPS，首次用到时加载；`/health` 报告已加载哪些。

`/run` 执行流程，按 chunk（默认 32 张）推进，chunk 内按产物优先：
1. **decode**：解 RAW（rawpy）或读 JPG，按 EXIF 旋正，长边缩到 2048，读 EXIF 的 GPS 与时间，算原文件 sha256。逐张失败发 `error`，其余继续。
2. **identify**：
   - 门：SigLIP2 整图前向，与固定 prompt 集比对得五类 softmax，取最大为 `gate.class`。
   - 检测：OWLv2，词表按 `gate.class` 选（bird 词表 / mammal 词表 / other_animal 词表）。每个候选框用 SigLIP2 对裁切图复判，通过才保留。若门判有动物但检测为空，用 0.1 阈值再跑一次（沿用 PhotoOS 规则）。
   - 画质：对每个框的裁切计算清晰度（Laplacian 方差归一化）和曝光偏差，numpy 实现。
   - 物种：BioCLIP 2.5 Huge 对裁切框（外扩 10%）编码，只与 `gate.class` 对应的名单向量矩阵打分做 softmax 得 `p_visual`。`geo=true`、`kind=bird` 且有经纬度时，`posterior = p_visual × (0.02 + p_geo)` 后归一化。定级规则沿用 PhotoOS：top-1 ≥ 0.5 且领先第二名 ≥ 0.3 定为种；否则 top-5 按属累加 ≥ 0.6 定为属，按科累加 ≥ 0.6 定为科；否则 unconfirmed。
3. **embed**：SigLIP2 整图向量。与门共用同一次前向，不重算。
4. **jpg**：把第 1 步的旋正图写到 `out_dir`。

三个模型全部常驻，chunk 之间切换模型无代价。

### 名单与文本向量
- 启动时读 `data/avilist/*.csv`（鸟）和 `data/mdd/*.csv`（哺乳），每张表编成一份文本向量矩阵，缓存在 `~/.cache/bioscan/names/<model>-<list_sha>.npz`。首次数分钟，之后秒开。
- 鸟：优先用 HF `imageomics/TreeOfLife-200M` 的 `embeddings/txt_emb_bioclip-2.5-vith14.{npy,json}` 里能按学名精确对上的行；对不上的名字用 BioCLIP 文本塔编码，文本格式与 TreeOfLife 一致（"a photo of <7 级分类链> with common name <俗名>"）。映射覆盖率在启动日志和 `bioscan names stats` 里打印。
- 哺乳：MDD 全表同样处理。
- 3.26 GB 的官方向量文件只在建缓存时需要，建完可删。

### 文件布局
```
bioscan/service/app.py        路由、NDJSON 流、按 chunk 的模型锁与队列
bioscan/service/engine.py     模型注册、懒加载、设备选择、batch size 常量
bioscan/service/products.py   identify / embed / jpg
bioscan/service/decode.py     RAW/JPG → 旋正 2048 图 + EXIF + sha256
bioscan/service/names.py      名单加载、TreeOfLife 映射、文本向量缓存
bioscan/service/adapters/     owlv2.py siglip2.py bioclip.py geo.py
```

## 6. 并发

三层：
1. **按 chunk 轮流用模型。** 模型锁（`asyncio.Lock`，FIFO）按 chunk 取放，而不是整个请求持有：单张请求最多等正在跑的那个 chunk，不等整批。解码在锁外。`/health` 的 `running` 是此刻占着模型的请求数（0 或 1），`queued` 是在等下一个 chunk 轮次的请求数。（2026-09-23 由"请求之间串行"改为此。）
2. **一批之内流水。** 解码用 `ProcessPoolExecutor`，默认 4 进程（/Volumes/Media 是 USB 机械盘，实测并发 5 是拐点），解码池预取下一个 chunk，GPU 处理当前 chunk。在飞行中最多 2 个 chunk，内存封顶约 800 MB。GPU 单流，靠 batch：SigLIP2 32 张、OWLv2 8 张、BioCLIP 16 个裁切。
3. **取消。** 客户端断开，服务在当前 chunk 结束后停止并释放锁。
4. **解码进程崩溃。** 解码 worker 死掉（如 LibRaw 遇损坏 RAW）时重建进程池，本 chunk 逐张重解码，只有致崩的那张报错。

可调项：`--decode-workers`（默认 4）、`--chunk`（默认 32）。每模型 batch size 写死在 engine.py。

## 7. CLI

薄客户端，标准库 argparse + urllib，不引入 typer/httpx/rich。

```
bioscan serve [--port 8765] [--decode-workers 4] [--chunk 32]
bioscan serve --launchd                     打印 launchd plist 到 stdout
bioscan health
bioscan run <path...> [--want identify,embed,jpg] [--json] [--out FILE]
                      [--lat --lon] [--no-geo] [--top-k 5] [--no-species]
                      [--jpg-out DIR] [-r] [--ext arw,dng,jpg,jpeg,raf,nef,cr3]
bioscan gt folders <dir> [--out groundtruth.csv]
bioscan gt inat --place california --taxa taxa.csv --per-species 25 --out DIR
bioscan eval <groundtruth.csv> --out DIR [--no-geo]
bioscan names stats
```

`run`：
- 路径可为文件或目录，目录按 `--ext` 过滤并排序，`-r` 递归。展开后一次请求发给服务。
- 默认 `--want identify`，终端 pretty；`--json` 把 NDJSON 原样写 stdout 或 `--out`，不二次加工。
- `--lat/--lon` 给整批设默认坐标，EXIF 有坐标时以 EXIF 为准（与 API 语义一致：请求值优先于 EXIF，所以 CLI 只在 EXIF 无坐标时才填入请求）。

pretty 渲染，一张一行，边收边打：
```
DSC00364.ARW  bird    2 boxes  [1] Western Screech-Owl 0.91 种  [2] unconfirmed
DSC00458.ARW  none
DSC00566.ARW  mammal  1 box    [1] Rangifer tarandus 0.77 种
```
结束打汇总：按物种计数、unconfirmed 数、错误列表、总耗时与每张耗时。

`gt folders`：子文件夹名当物种，用 AviList/MDD 俗名与学名两列做匹配；对不上或多义的行 `scientific` 留空，由人手改。
`gt inat`：走 iNaturalist API v1 `observations`，`quality_grade=research`，license 限 CC0/CC-BY/CC-BY-NC，`place_id` 加州，每种取 `--per-species` 张 `medium` 尺寸图，限速 1 请求/秒。CSV 记 `path, scientific, tier=inat, lat, lon, taken_at, source(观察 URL), license, attribution`。`taxa.csv` 首版由实现提供：加州常见哺乳约 20 种 + 常见鸟约 40 种 + 阿拉斯加驯鹿、驼鹿、灰熊。

文件：`bioscan/cli/{main,client,render,gt,eval}.py`。

## 8. harness

`groundtruth.csv` 列：`path, scientific, tier(own|inat), lat, lon, taken_at, source`。一张图一个主体物种；多物种同框不入集。

own tier 来源：`/Volumes/Media/bird/` 三个物种文件夹、`ak_selected` 中可按文件夹标注的哺乳、PhotoOS `review/` 的测试卡。inat tier 来源：`gt inat` 下载。

`bioscan eval`：调服务跑 `identify`，原始预测存 `preds.ndjson`，报告存 `report.md`。指标按 tier × 类群（bird / mammal）分别报：

| 指标 | 定义 |
|---|---|
| gate 准确率 | `gate.class` 与真值类群一致的比例 |
| 检出率 | 至少有一个 `kind` 与真值类群一致的框 |
| Top-1 / Top-5 | 取 `score` 最高的框，`top[0]`（或 `top[:5]`）学名命中真值 |
| 覆盖率 | `level == species` 的比例 |
| 精度 | `level == species` 时 Top-1 命中的比例 |
| 混淆 Top-10 | 出现最多的 (真值 → 预测) 对 |
| 耗时 | 每张 ms，分 decode 与 identify |

`--no-geo` 关掉地理先验再跑一次，人工对比两份报告。v1 不做自动对比。第一次跑出的报告即 baseline。

## 9. 仓库、测试、迁移

仓库 `~/workspace/personal/bioscan`，uv 管理，Python 3.12。
```
bioscan/
  service/  cli/
  data/     taxa.csv  avilist/  mdd/（CSV 下载后放这里，大文件 gitignore）
tests/
  unit/     contract/   smoke/（5 张真图，手动跑，不进 CI）
docs/superpowers/specs/
```

依赖：fastapi、uvicorn、torch、open_clip_torch、transformers、rawpy、pillow、numpy、huggingface_hub。测试：pytest、httpx（仅测试用，FastAPI TestClient 需要）。

测试三层：
- **单测**（无模型）：定级规则、坐标归一化与旋正、AviList ↔ TreeOfLife 映射、NDJSON 解析、`gt folders` 名字匹配、eval 指标计算。
- **契约测试**（假 engine，返回固定框和分数）：`/run` 事件序列、单图错误不中断、`want` 组合、锁排队、客户端断开取消。
- **冒烟**：`tests/smoke/` 5 张真图，手动运行，检查输出合理。

从 PhotoOS 迁移（复制后裁剪，只留函数，去掉 workflow envelope / catalog / contract 包装）：
- `src/photoos/analysis/adapters/{owlv2,bioclip_huge,content_local}.py`
- `src/photoos/scan/{vocab,geo,gate}.py`
- `src/photoos/scan/triage.py` 中的框复判与定级规则
- `image_prepare` 的 RAW 解码与旋正

PhotoOS 仓库保留不动，新工具跑通后再归档。

## 10. 风险与应对

- **AviList 与 TreeOfLife 名字对不上的比例未知。** 启动时打印覆盖率；对不上的用文本塔自编，并在 eval 报告里单列这部分物种的命中率。
- **哺乳动物名单大（约 6500 种）且无地理先验，unconfirmed 可能偏多。** v1 接受，先拿到数字。
- **iNaturalist 图与长焦 RAW 有域差。** 两个 tier 分开报，不合并。
- **USB 机械盘解码是瓶颈。** `--decode-workers` 可调；默认 4。
- **MPS 上 OWLv2 / open_clip 的算子兼容性。** PhotoOS 已在同机跑通，沿用其环境和版本锁。

## 11. 未决

无。以上均已在对话中确认。
