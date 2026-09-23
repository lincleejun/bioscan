# bioscan

照片里有什么动物、在哪、是什么种。常驻 HTTP 服务 + 薄 CLI。设计见 `docs/superpowers/specs/2026-09-22-bioscan-design.md`。

## 服务

```sh
uv sync
uv run bioscan-serve                 # 或 uv run python -m bioscan.service；默认 127.0.0.1:8765
uv run bioscan-serve --port 8775 --decode-workers 4 --chunk 32
curl -s 127.0.0.1:8765/health
curl -s 127.0.0.1:8765/products
curl -sN 127.0.0.1:8765/run -H 'content-type: application/json' \
  -d '{"inputs":[{"path":"/abs/a.ARW"}],"want":["identify","embed","jpg"],"options":{"jpg":{"out_dir":"/tmp/jpg"}}}'
```

模型（首次用到时加载，全部常驻 MPS，无 MPS 回退 CPU；权重只从 `~/.cache/huggingface` 读，`HF_HUB_OFFLINE=1`）：
- 门与 embed：`google/siglip2-base-patch16-224`
- 检测：`google/owlv2-base-patch16-ensemble@cfd3195`
- 物种：`hf-hub:imageomics/bioclip-2.5-vith14`（BioCLIP 2.5 Huge，唯一物种模型）
- 地理先验：BirdNET geo 3.0（`birdnet` 包，加载失败则无先验）

首次在新机器上需要联网拉一次权重：
```sh
uv run python -c "from huggingface_hub import snapshot_download as s; s('google/siglip2-base-patch16-224'); s('google/owlv2-base-patch16-ensemble', revision='cfd3195ba4ea9592eec887ded089f4c08eff231d', ignore_patterns=['*.bin']); s('imageomics/bioclip-2.5-vith14', ignore_patterns=['*.bin'])"
```

名单：目前用 `names_legacy.py`（PhotoOS 的 11045 鸟种，源自 HF `imageomics/TreeOfLife-200M` 的 `embeddings/txt_emb_species.json`），
文本向量缓存在 `~/.cache/bioscan/names/`。哺乳暂无名单，mammal 框 `species: null`。目标 N 交付 `names.py` 后替换。

## 测试

```sh
uv run pytest tests/unit tests/contract          # 无模型，秒级
uv run python tests/smoke/run_smoke.py --url ...  # 需要起服务，tests/smoke/*.ARW 自备（gitignore）
```

## 布局

```
bioscan/service/app.py           路由、NDJSON 流、请求锁与队列、bioscan-serve 入口
bioscan/service/engine.py        设备选择、懒加载、batch 常量
bioscan/service/products.py      identify / embed / jpg，选项与 /products schema，定级/复判/画质规则
bioscan/service/decode.py        RAW/JPG → 旋正 2048 图 + EXIF + sha256
bioscan/service/names_legacy.py  临时名单（PhotoOS 11045 鸟种）
bioscan/service/adapters/        siglip2.py owlv2.py bioclip.py geo.py
```
