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
- [ ] pure function `geo_gaps(names, probs, index, min_p)` in adapters/geo.py
- [ ] `bioscan names geo-gaps --lat --lon [--date] [--min-p]` (reads avilist_map.csv; loads BirdNET only)
- [ ] docs in data/README
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
- [ ] README
Verify: contract test with two same-stem files in different dirs → two outputs.

## 5. Path allow-list for the service
Goal: exposing the service beyond localhost does not open the whole disk.
- [ ] `BIOSCAN_ALLOW_ROOTS` / `--allow-root` (repeatable): inputs and jpg.out_dir must be inside; unset = current behaviour
- [ ] warn at start when host is not loopback and no roots set
Verify: contract tests (inside ok, outside 400, symlink/`..` escape rejected).

## 6. CI
- [ ] `.github/workflows/ci.yml`: ruff + pytest on push/PR
- [ ] `tests/models/` real-model smoke (SigLIP2, OWLv2, BioCLIP 2.5 Huge on CPU, small in-memory name list, iNat open-data photos)
- [ ] `.github/workflows/models.yml`: every PR/push, HF cache
Verify: workflows green on the pushed branch.

## 7. Wrap-up
- [ ] README / data/README updated
- [ ] self-review of the diff against main (blocking problems only)
- [ ] push `claude/sleepy-hawking-0jht6y`

## Found along the way
- Geo prior: 749 AviList species have no BirdNET label; most are extinct or lumped sisters
  (e.g. Tyto javanica) that should stay suppressed, so "no label → no prior" would regress. Hence item 2.
- Mammal geo prior needs a range-data source (not in this round).
