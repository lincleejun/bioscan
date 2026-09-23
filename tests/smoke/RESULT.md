# 冒烟结果（2026-09-22）

服务：`uv run bioscan-serve --port 8775`（8765 被本机正在运行的 PhotoOS 服务占用），MPS，fp32。
请求：`want=[identify,embed,jpg]`，5 张 ARW 一批，未给经纬度（照片无 GPS，所以无地理先验，`p_geo=null`）。
脚本：`uv run python tests/smoke/run_smoke.py --url http://127.0.0.1:8775`。只看 JSON，未查看图片。

来源：`/Volumes/Media/bird/` 的 Red-Tailed-Hawk（RTHA_ ×2）、Steller's Jay（STJA_ ×2）、Western-Screech-Owl（WESO_ ×1）。

## 首次请求（含模型冷加载约 11 s；名单文本向量已缓存）

| file | gate.class | boxes | level | top-1 | posterior | decode ms | identify ms | embed ms | jpg ms |
|---|---|---|---|---|---|---|---|---|---|
| RTHA_DSC02897.ARW | bird | 1 | species | Buteo jamaicensis | 1.000 | 511 | 447 | 0 | 19 |
| RTHA_DSC02937.ARW | bird | 1 | species | Buteo jamaicensis | 1.000 | 534 | 284 | 0 | 6 |
| STJA_DSC02868.ARW | bird | 1 | species | Cyanocitta stelleri | 1.000 | 539 | 275 | 0 | 6 |
| STJA_DSC02883.ARW | bird | 1 | species | Cyanocitta stelleri | 1.000 | 537 | 273 | 0 | 5 |
| WESO_DSC02610.ARW | mammal | 1 | species | Megascops asio | 0.900 | 407 | 290 | 0 | 4 |

ok=5 failed=0，客户端墙钟 14.3 s（模型加载占大头）。

## 热请求（模型常驻）

| file | gate.class | boxes | level | top-1 | posterior | decode ms | identify ms | embed ms | jpg ms |
|---|---|---|---|---|---|---|---|---|---|
| RTHA_DSC02897.ARW | bird | 1 | species | Buteo jamaicensis | 1.000 | 475 | 232 | 0 | 6 |
| RTHA_DSC02937.ARW | bird | 1 | species | Buteo jamaicensis | 1.000 | 492 | 211 | 0 | 6 |
| STJA_DSC02868.ARW | bird | 1 | species | Cyanocitta stelleri | 1.000 | 493 | 209 | 0 | 6 |
| STJA_DSC02883.ARW | bird | 1 | species | Cyanocitta stelleri | 1.000 | 487 | 209 | 0 | 5 |
| WESO_DSC02610.ARW | mammal | 1 | species | Megascops asio | 0.900 | 405 | 214 | 0 | 4 |

ok=5 failed=0，elapsed 1994 ms（约 0.4 s/张；解码 4 进程并行，与 GPU 串行部分重叠）。

说明：
- identify 耗时含该图分摊的 SigLIP2 整图前向；embed 与门共用同一次前向，所以 embed 只剩格式化时间。
- 验收项全部满足：每张都有 `result`，`boxes` 非空，`species.level` 有值，`embed.dim == 768`，jpg 已落盘。
- 猫头鹰那张：整图门判成 `mammal`（mammal 0.54 / bird 0.38），但框的裁切复判把它提升为 `bird`，物种头照常运行。
  没有地理先验时 top-1 是 Eastern Screech-Owl（Megascops asio，p_visual 0.90），真值应为 Western Screech-Owl（Megascops kennicottii）。
  开发时用一个假设坐标（37.5, -122.0）跑过一次，BirdNET 先验把 M. kennicottii 排到第一（posterior 0.68）。坐标是假设值，不是这张照片的真实拍摄地。
- 红尾鵟和暗冠蓝鸦 4 张 top-1 全部正确，且都定级到种。
