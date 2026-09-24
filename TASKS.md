# TASKS — v1.1 improvements

Source of truth for this run. Each item: goal → how it is verified.
Decisions taken (2026-09-23): geo gaps = data-driven + review tool; detail edge = 3072;
jpg name = `<stem>-<sha8>.jpg`; real-model smoke runs on every PR.

## 0. Ground rules
- [x] CLAUDE.md from the Opus 5.5 template, filled for this repo
- [x] ruff config in pyproject; baseline lint clean (scripts/verify excluded as one-off scripts)
- [x] Existing 75 tests pass before any feature change

## 1. Species from a high-resolution crop
Goal: BioCLIP sees the box cropped from a ≤3072 px "detail" image instead of the 2048 px frame;
detection, gate, quality and box coordinates stay on the 2048 image (unchanged numbers).
- [x] decode: `detail_edge` argument; `Decoded.detail` (None when not asked or when not larger than `image`)
- [x] service: `BIOSCAN_DETAIL_EDGE` / `--detail-edge` (default 3072; ≤2048 disables); detail decoded only when `identify` is wanted
- [x] products.identify: species crops from detail with bbox scaled; everything else unchanged
- [x] engine/fake: `identify(..., detail=None)`
Verify: unit tests (detail size/None rules; crops scale exactly; identify with/without detail gives
identical boxes/quality; BioCLIP receives the larger crop); contract tests; real-model smoke in CI.
Accuracy: unverified until `bioscan eval` on the golden set (Mac).

## 2. Geo gaps review tool (no behaviour change)
Goal: list AviList species with no BirdNET label whose genus is present at a place, so the gaps
that matter there get a reviewed `birdnet` synonym instead of a blanket rule.
- [x] pure function `geo.gaps(scientific, common, index, probs, min_p)` in adapters/geo.py
- [x] `bioscan names geo-gaps --lat --lon [--date] [--min-p]` (reads avilist_map.csv; loads BirdNET only)
- [x] docs in README (名字映射 section)
Verify: unit tests with a fake prior; prior values for mapped rows unchanged (existing tests).

## 3. Mammal detection vocabulary
Goal: fewer "no box" mammals (black bear, mountain lion, grizzly, bobcat in the golden set).
- [x] add large carnivores and common CA mammals to VOCAB["mammal"]
- [x] gate rescue: gate says none/person but bird+mammal+other_animal >= 0.25 -> first-pass detection with the
      strongest animal's vocab (crop gate still vetoes; no low-floor second pass; reported gate class unchanged)
- [x] eval: "no box" split by whole-frame gate class (works on existing preds.ndjson via `--preds`)
Found: eval's "(no box)" includes images the gate called none/person, where the detector never ran,
so vocab alone could not fix gate misses.
Verify: unit tests (rescue on/off, first pass only, veto, old path unchanged, vocab); real-model smoke in CI.
Accuracy: unverified until `bioscan eval`.

## 4. jpg product naming
Goal: no silent overwrite across folders or RAW+JPG pairs.
- [x] `<out_dir>/<stem>-<sha256[:8]>.jpg`; /products description
- [x] README
Verify: contract test with two same-stem files in different dirs → two outputs.

## 5. Path allow-list for the service
Goal: exposing the service beyond localhost does not open the whole disk.
- [x] `BIOSCAN_ALLOW_ROOTS` / `--allow-root` (repeatable): inputs and jpg.out_dir must be inside; unset = current behaviour
- [x] warn at start when host is not loopback and no roots set
Verify: contract tests (inside ok, outside 400, symlink/`..` escape rejected).

## 6. CI
- [x] `.github/workflows/ci.yml`: ruff + pytest on push/PR
- [x] `tests/models/` real-model smoke (SigLIP2, OWLv2, BioCLIP 2.5 Huge on CPU, small in-memory name list, iNat open-data photos)
- [x] `.github/workflows/models.yml`: every PR/push, HF cache
Verify: workflows green on the pushed branch. ci green; models runs 3 and 5 green (7 passed; 23.5 min cold, 20 min cached).
- [x] FLOORS moved from placeholder values to ~4 images under the measured numbers (bird top-1 88.1 %, mammal top-1 85.7 %)

## Next (found by the harness; each needs its own CI compare)
- [ ] not_in_list (7): a self-encoded reptile + fish list (Reptile Database / Eschmeyer's Catalog or GBIF
      checklists, encoded with BioCLIP's text tower like the AviList/MDD rows without ToL vectors); until then
      consider capping other_animal boxes whose kind lacks coverage at genus/unconfirmed
- [ ] out_of_range (3): the range veto only reorders within the returned top-k; search the whole list for an
      in-range congener (Corvus corax case)
- [ ] prior_suppressed (1): Cervus canadensis lost to the mammal prior (p_geo of Cervus?) -> inspect mdd_map / geomodel
      coverage for Cervus, genus back-off constant
- [ ] kind check against the all-taxa list: size-corrected top-5 statistic (reviewer fix.py), evaluate on real photos
- [x] owner's Mac: golden + own RAW baselines (2026-09-24: baselines/golden-inat-v1.{4,5}.json, own-raw-2026-09-24-v1.{4,5}.json; docs/2026-09-24-*.md) (`bioscan bench run ... --tier golden|own`), speed tier, RAW EXIF check
- [ ] golden other-animal slice: ~16 CA reptile/amphibian/insect/spider species × 25 via `bioscan gt inat` (docs/standards.md); measures accuracy.golden.other.*
- [ ] docs/standards.md status column: refresh after every Mac baseline (done 2026-09-24 for golden/own)
- [ ] service shutdown leaves decode-pool workers alive after SIGTERM (found 2026-09-24 on the Mac); terminate the pool in the lifespan handler
- [ ] Phase 0 speed benchmark (docs/strategy): 2,000 files, ARW/CR3/NEF at 24 and 45 MP, USB vs SSD; CR3/NEF need sample files the owner does not have yet

- [ ] `bioscan report <preds.ndjson>`: reviewable HTML (thumbnails with boxes, grouped by top-1, species table, review.csv with a verdict column); prototype in runs/coyote-hills/build_pages.py (2026-09-24, 1424 ARW at Coyote Hills, owner reviewed: OK)

## 7. Mac 本地跑 v1.4 / v1.5 数据（2026-09-24，owner 的操作清单）
- [x] git pull（875dc7a）+ uv sync
- [x] tests/models/download.py：all-taxa 366,460 种，float16 716 MiB，缓存 763 MiB
- [x] decode 元数据检查：ARW 29 / DNG 2 / RAF 263 / JPG 274 全部读到拍摄时间，均无 GPS；本机无 CR3/ORF/RW2/NEF
- [x] v1.4 基线：worktree .worktrees/v14 @ 91b6bd6 → bench run golden/own → baselines/golden-inat-v1.4.json, own-raw-2026-09-24-v1.4.json
- [x] v1.5：main → bench run golden/own → baselines/golden-inat-v1.5.json, own-raw-2026-09-24-v1.5.json
- [x] bench compare / analyze / scorecard → docs/2026-09-24-*.md；README Results 更新为 v1.4 vs v1.5 双行表
- [x] 报告：Blocked on me / Changed / Found / Left（见会话）
Found（已记录）：
- [x] 新 worktree 缺 gitignore 的名单 CSV，服务 503 "expected exactly one CSV"；复制 data/avilist、data/mdd 后正常
- [x] 服务 SIGTERM 后解码进程池子进程不退出，累积孤儿进程；已手动清理，修复留待 v1.6
- [x] golden compare 唯一超预算项是吞吐 −53%（全品类名表）；准确率无回归

---

# TASKS — v1.6 profiles and plugins, geotag, culling (2026-09-24)

Research: docs/research/2026-09-24-plugin-architecture.md, docs/research/2026-09-24-culling-aesthetics.md.
Decisions (owner, 2026-09-24): profiles expand in CLI and service; reducers in CLI/offline; aesthetic head
on SigLIP2 trained on EVA (CC0) + owner ratings; architecture steps 0-4 before culling.

- [x] A0 golden-stream recording test (fake engine, 10 option sets + refusals + /health; /products JSON; fingerprint; eval + bench report on a preds file): tests/contract/test_golden_stream.py, goldens in tests/contract/golden/ recorded at b02f189
- [ ] A1 Product -> Manifest + Stage; contract.PRODUCTS derived from plugins.BUILTIN
- [ ] A2 models(opts): identify with species=false skips BioCLIP; detail decode keyed on `reads`
- [ ] A3 Item.facts + topological plan from reads/provides; Loaders.extra
- [ ] A4 profiles: bioscan/profile.py (stdlib), bioscan.toml, serve_config file layer, --profile / "profile"; full = today
- [ ] W6 geotag from GPX (in progress, v16/w6-geotag) -> becomes the step-5 `geotag` stage after A4
- [ ] A6 harness: meta.profile, plugin_metrics, standards `profile` field
- [ ] C1 cull plugins: quality (+clipping), scene (SigLIP2 zero-shot), reducers burst + select; album tier + baseline
- [ ] C2 aesthetic head on SigLIP2 (EVA CC0 general head; owner-rating personalisation; learning curve in bench)
- [ ] cull ground truth: owner's Lightroom stars/labels on 2-3 trips (reject reason, burst winner, category);
      synthetic reject set (blur / cut-off / exposure degradations of iNat photos) for the rule stages
