# bioscan

照片里有什么动物、在哪、是什么种。常驻 HTTP 服务 + 薄 CLI。设计见 `docs/superpowers/specs/2026-09-22-bioscan-design.md`。

## 安装

```sh
uv sync                                   # Python 3.12，依赖见 pyproject.toml
```

模型权重只从 `~/.cache/huggingface` 读（服务强制 `HF_HUB_OFFLINE=1`）。新机器上先联网拉一次：
```sh
uv run python -c "from huggingface_hub import snapshot_download as s; s('google/siglip2-base-patch16-224'); s('google/owlv2-base-patch16-ensemble', revision='cfd3195ba4ea9592eec887ded089f4c08eff231d', ignore_patterns=['*.bin']); s('imageomics/bioclip-2.5-vith14', ignore_patterns=['*.bin'])"
```

名单 CSV（AviList 鸟、MDD 哺乳）体积大、不进 git，按 `data/README.md` 下载放到 `data/avilist/`、`data/mdd/`。
首次启动把它们编成 BioCLIP 文本向量矩阵，缓存在 `~/.cache/bioscan/names/`（需要 HF 上 TreeOfLife-200M 的 3.26 GB 官方向量，约半分钟）；之后秒开。
查看覆盖率：`uv run bioscan names stats`。

模型（首次用到时加载，全部常驻 MPS，无 MPS 回退 CPU）：
- 门与 embed：`google/siglip2-base-patch16-224`
- 检测：`google/owlv2-base-patch16-ensemble@cfd3195`
- 物种：`hf-hub:imageomics/bioclip-2.5-vith14`（BioCLIP 2.5 Huge，唯一物种模型）；鸟 `avilist-2025`（11131 种），哺乳 `mdd-2025`（6904 种）
- 地理先验：BirdNET geo 3.0（`birdnet` 包，仅鸟；加载失败则无先验）。哺乳 `p_geo` 恒为 null

## 启动服务

```sh
uv run bioscan serve                                   # 127.0.0.1:8765
uv run bioscan serve --port 8767 --decode-workers 4 --chunk 32
uv run bioscan serve --launchd > ~/Library/LaunchAgents/cc.outman.bioscan.plist   # 开机常驻
uv run bioscan health
```
`bioscan-serve` / `python -m bioscan.service` 是同一个服务入口（多一个 `--host`）。

## 端口与环境变量

| 名称 | 作用 | 默认 |
|---|---|---|
| `--port` | 服务端口 | 8765 |
| `BIOSCAN_URL` / `--url` | CLI 连哪个服务，`--url` 优先 | `http://127.0.0.1:8765` |
| `--decode-workers` / `BIOSCAN_DECODE_WORKERS` | 解码进程数（USB 机械盘 4 左右最佳） | 4 |
| `--chunk` / `BIOSCAN_CHUNK` | 每个流水 chunk 的张数 | 32 |

服务端取值顺序：命令行参数 > 环境变量 > 默认值。`bioscan serve` 把自己的 `--decode-workers/--chunk` 写进这两个环境变量再启动服务。

本机 8765 常被 PhotoOS 服务占着。换端口时服务和 CLI 两边都要改：
```sh
uv run bioscan serve --port 8767 &
export BIOSCAN_URL=http://127.0.0.1:8767
```

## run

```sh
bioscan run /Volumes/Media/bird/Red-Tailed-Hawk            # 目录按 --ext 过滤、排序；-r 递归
bioscan run a.ARW b.ARW --want identify,embed --json --out preds.ndjson   # 原样 NDJSON
bioscan run DIR --want jpg --jpg-out /tmp/jpg              # 旋正、长边 2048 的 JPG
bioscan run DIR --lat 37.4 --lon -122.1                    # EXIF 无坐标时的整批默认坐标
bioscan run DIR --no-geo --top-k 10 --no-species
```
默认 `--want identify`，终端一张一行，末尾汇总（物种计数、unconfirmed、错误、耗时）。有错误时退出码 1。

## gt（真值）

```sh
bioscan gt folders /Volumes/Media/bird --out data/groundtruth-own.csv \
  --names data/avilist/AviList-v2025-11Jun-extended.csv --names data/mdd/MDD_v2.5_6904species.csv
bioscan gt inat --place california --taxa data/taxa.csv --per-species 25 --out data/inat   # --dry-run 只打印 URL
```
`gt folders` 用子文件夹名匹配俗名或学名，对不上的行 `scientific` 留空，需手改。

## eval

```sh
bioscan eval data/groundtruth-own.csv --out runs/<date>-baseline           # 调服务，写 preds.ndjson + report.md
bioscan eval data/groundtruth-own.csv --out runs/<date>-nogeo --no-geo
bioscan eval data/groundtruth-own.csv --out runs/x --preds runs/<date>-baseline/preds.ndjson   # 只重算指标
```
`preds.ndjson` 每张图一行（`result` 或 `error`）。`report.md` 按 tier × kind 报 gate 准确率、检出率、Top-1/Top-5、覆盖率、精度、混淆 Top-10、耗时。

## HTTP

```sh
curl -s 127.0.0.1:8765/health
curl -s 127.0.0.1:8765/products
curl -sN 127.0.0.1:8765/run -H 'content-type: application/json' \
  -d '{"inputs":[{"path":"/abs/a.ARW"}],"want":["identify","embed","jpg"],"options":{"jpg":{"out_dir":"/tmp/jpg"}}}'
```

## 测试

```sh
uv run pytest tests/unit tests/contract          # 无模型，秒级
uv run python tests/smoke/run_smoke.py --url ...  # 需要起服务，tests/smoke/*.ARW 自备（gitignore）
```

## 布局

```
bioscan/service/app.py           路由、NDJSON 流、请求锁与队列、服务入口
bioscan/service/engine.py        设备选择、懒加载（load_species = BioCLIP + 名单）、batch 常量
bioscan/service/products.py      identify / embed / jpg，选项与 /products schema，定级/复判/画质规则
bioscan/service/decode.py        RAW/JPG → 旋正 2048 图 + EXIF + sha256
bioscan/service/names.py         AviList / MDD 名单、TreeOfLife 映射、文本向量缓存
bioscan/service/adapters/        siglip2.py owlv2.py bioclip.py geo.py
bioscan/cli/                     main client render gt eval
```
