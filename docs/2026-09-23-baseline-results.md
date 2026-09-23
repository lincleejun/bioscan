# v1 baseline 与外部复核（2026-09-23）

## 全量 eval（own tier，404 张，3 种鸟，文件夹名为真值）

| run | 地理先验 | gate 准确率 | 检出率 | Top-1 | Top-5 | 覆盖率 | 精度 | identify ms 中位 |
|---|---|---|---|---|---|---|---|---|
| 2026-09-22-baseline | 无（照片无 GPS） | 85.4% | 100% | 80.2% | 98.0% | 85.9% | 85.3% | 213 |
| 2026-09-23-geo-assumed | **假设**全批坐标 37.4,-122.1 | 85.4% | 100% | 96.3% | 98.0% | 97.8% | 97.7% | 211 |

无先验时 72 次错误是西美角鸮 → 东美角鸮/须角鸮（三者外形几乎相同，靠分布区分）；加先验后降到 7 次。
gate 错的 59 张里 55 张是角鸮整图被判 mammal，但框级复判全部纠回 bird，检出率不受影响。

## 外部复核（codex gpt-5.6-sol，reasoning medium，只看 JPG）

40 张分层样本，**故意多抽了未命中的图**，原始一致率不能直接当总体数字。

| 对比 | 40 张样本 | 按分层加权到 404 张 |
|---|---|---|
| bioscan(geo) 与 codex 一致（top-1 或 codex 备选） | 72.5% | **80.1%** |
| bioscan(无先验) 与 codex 一致 | 70.0% | — |
| bioscan(geo) 与文件夹真值一致 | 82.5% | 96.3%（全量） |
| codex 与文件夹真值一致 | 37.5% | — |

分歧集中在两处：
1. **红尾鵟文件夹**：9 张里 codex 有 6 张判 Buteo lineatus（红肩鵟），bioscan 与文件夹一致判 jamaicensis。三方里必有一方错，需要人眼确认。这一项决定加权一致率是 80% 还是 95% 以上。
2. **角鸮**：codex 无位置信息时常给 asio（东美角鸮，加州不分布），kennicottii 在其备选里，按备选计为一致。

bioscan 的真实失败（与谁比都错）：红尾鵟 6 张被判成鹪鹩/啄木鸟/花栗鼠/鼯鼠（`level` 为 unconfirmed/family/species 均有），主体很可能在画面里很小，检测框选错目标。这是下一轮要修的。

复核脚本：`scripts/verify/codex_verify.py`、`scripts/verify/compare2.py`；答案：`runs/2026-09-22-baseline/codex_answers.jsonl`。
