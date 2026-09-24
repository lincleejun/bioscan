# bioscan

**English** | [简体中文](README.zh-CN.md)

**What animal is in the photo, where, and which species.** A local identification service for wildlife photography: give it a batch of RAW or JPG files and get back, per image, animal boxes, species (birds and mammals to species), confidence and a grade. A resident HTTP service plus a thin CLI; runs on MPS on a Mac; no photo ever leaves the machine.

## Results

### iNaturalist golden set (California, 65 species × 25 = 1625 research-grade observations, real GPS and dates)

| Group | n | Gate acc. | Detected | Top-1 | Top-5 | Coverage | Precision |
|---|---|---|---|---|---|---|---|
| Birds (1050) | 1050 | 97.0% | 97.0% | **89.8%** | 95.0% | 95.6% | 93.4% |
| Mammals (575) | 575 | 89.7% | 84.9% | **74.1%** | 81.4% | 82.3% | 89.2% |

- Coverage = share of images graded to species; precision = Top-1 accuracy among those. Read them together, so coverage is never quoted without precision.
- Effect of the location prior (birds): Top-1 83.3% → 89.8%; Northern Harrier went from 1/25 to 25/25.
- Mammal failures are mostly "no box": 45 images of black bear, mountain lion, grizzly and bobcat where the detector vocabulary did not box the animal. This is the largest known weakness.
- Ground truth is iNaturalist community-verified, CC0 / CC BY / CC BY-NC, used for evaluation only; images are not distributed with the repo. `data/inat/groundtruth-inat.csv` keeps each observation's link and attribution.

### Own photos (telephoto RAW, 404 images, 3 species)

| Location prior | Top-1 | Top-5 | Coverage | Precision |
|---|---|---|---|---|
| Off (photos have no GPS) | 80.2% | 98.0% | 85.9% | 85.3% |
| On (one coordinate for the batch) | **96.3%** | 98.0% | 97.8% | 97.7% |

Western, Eastern and Whiskered Screech-Owls look almost identical and are told apart by range: 72 errors without a coordinate, 7 with one.

### Speed (M-series Mac, MPS)

| Step | Per image |
|---|---|
| RAW decode + rotate + resize (USB hard disk) | ~650 ms |
| Identify (gate + detect + crop check + species) | ~210 ms |
| JPG read | ~1 ms |

Cold start loads the three models in about 11 s; they stay resident afterwards.

Full numbers and confusion tables: `docs/2026-09-23-baseline-results.md`.

### Standards and targets

[`docs/standards.md`](docs/standards.md) sets the bars bioscan is measured against, in 11 dimensions
(accuracy per kind, trust, detection, location, a directory-level acceptance test, speed, coverage,
robustness, onboarding, privacy, reproducibility). For each it gives the industry bar with its
source, our community bar, a stretch bar, the current status and how it is measured. It also sets
the release stages: v0.x "try it and help identify", then v1.0 "bundle and release". The bars say
when we invite the community; the v0.x gates are not met yet. `data/standards.toml` holds the same bars for
`bioscan bench scorecard`.

## Goals and scope

In scope:
- Scan a directory in one pass and stream results as they are produced.
- Three products, in any combination: `identify` (boxes + species), `embed` (whole-frame SigLIP2 vector), `jpg` (RAW to upright JPG).
- **AviList 2025** (birds, 11131 species) and **MDD v2.5** (mammals, 6904 species) are the only naming standards; BirdNET, TreeOfLife/BioCLIP and iNaturalist names are mapped onto them through the tables in `data/names/`.
- Built-in evaluation: `bioscan gt` builds a ground-truth set (from folder names or iNaturalist), `bioscan eval` writes a report.

Out of scope (v1):
- Caching and persistent state, writing back human corrections, photo management, a web UI.
- Individual re-identification (the same animal across photos).
- Photo-library integration (Immich and others, later through the same HTTP API).

## Pipeline

```
RAW/JPG ─ decode ─▶ upright 2048 px image + EXIF (GPS, time) + sha256 (plus a ≤3072 px detail copy for identify)
              │
              ├─ SigLIP2 whole frame ─▶ gate: bird / mammal / other_animal / person / none   ─▶ embed product
              │
              ├─ OWLv2 open-vocabulary detection (vocabulary chosen by the gate; if the gate says
              │   none/person but the three animal classes total ≥ 0.25, detect anyway with the strongest
              │   animal class's words) ─▶ SigLIP2 check of each box crop ─▶ image quality
              │
              └─ BioCLIP 2.5 Huge on the same framing cut from the detail copy ─▶ kind check against the
                 bird and mammal lists together ─▶ cosine against that kind's name-list text vectors
                 × (0.02 + BirdNET location prior) ─▶ normalise ─▶ top-k ─▶ range veto ─▶ grade
```

Grading: species when top-1 ≥ 0.5 and leads the runner-up by ≥ 0.3; otherwise genus when the top-5 summed by genus reaches ≥ 0.6, family when summed by family reaches ≥ 0.6; otherwise `unconfirmed`. The range veto and the kind check (next section) can lower that grade.

### Accuracy rules (v1.5)

Three fixes for confident species-level mistakes. Each is an `identify` option, on by default, so a run can switch one off and measure it:

| Option | What it does | Constants |
|---|---|---|
| `range_veto` | **Range veto.** Where the place is known and the list has a location prior, a top candidate whose own p_geo is below ε cannot be graded species. If a congener among the returned candidates has p_geo ≥ τ, it is listed first (the only case where `top` is not in posterior order); the grade then comes from the genus/family roll-up. Fixes a Raven named as a Philippine crow in California. A p_geo borrowed from the genus (below) never vetoes. | `rules.RANGE_EPS` ε = 0.01, `rules.RANGE_TAU` τ = 0.05 |
| `kind_check` | **Kind check.** Each box's BioCLIP features (already computed) are also scored against the bird and mammal lists stacked into one, and the box takes the kind whose 5 best names hold most of that visual probability (the same number of names per list, so a longer list does not win by size). So a box can move bird ↔ mammal against the gate and crop check (an owl gated mammal is no longer named as a skunk). A box that moved on less than 0.75 of the mass is graded `unconfirmed`: any name above that would assert a kind the evidence cannot. The visual mass decides, not the posterior, because the two lists differ in prior coverage. `other_animal` boxes are left alone. | `rules.KIND_TOP` = 5, `rules.KIND_SURE` = 0.75; lists in `taxa.KIND_CHECK` |
| `mammal_geo` | **Mammal location prior.** The BirdNET geo model the service already loads also scores 1,048 mammals; `data/names/mdd_map.csv` gives them to MDD rows. An MDD row with no label gets the highest p_geo among labelled species of its genus (**genus back-off**), or 0.05 when its genus has none. Birds keep their rule: unlabelled rows get 0. | `geo.UNLABELLED_NEUTRAL` = 0.05; per-list policy `names.LISTS[...].unlabelled` |

All thresholds, the unlabelled policies, the label maps' contents and the option defaults are in the settings fingerprint; `result.engine.models.label_maps` names each label map with its sha. With all three options off, identify output is the same as v1.4 (checked byte for byte on a 300-frame stand-in recording, 5 option sets). To measure one fix, run the same ground truth twice and compare the reports:
```sh
bioscan eval data/inat/groundtruth-inat.csv --out runs/<date>-on
bioscan eval data/inat/groundtruth-inat.csv --out runs/<date>-no-veto --identify-opt range_veto=false
```
Over HTTP, pass `"options":{"identify":{"kind_check":false}}`. CI's real-model smoke runs its photos with all three on and all three off and adds an on/off table plus every changed image to its report (`models-report`). It fails only if, for either kind, the options lose more than one Top-1 hit or add more than one species-level wrong answer: with about 38 photos a kind that is a tripwire, and the real gate is `bioscan bench compare` against the committed baseline with its regression budgets.

Models and data:

| Use | Source | License |
|---|---|---|
| Gate, crop check, embed | `google/siglip2-base-patch16-224` | Apache-2.0 |
| Detection | `google/owlv2-base-patch16-ensemble` | Apache-2.0 |
| Species | `imageomics/bioclip-2.5-vith14` (BioCLIP 2.5 Huge) | MIT |
| Species-name text vectors | official precomputed `imageomics/TreeOfLife-200M` vectors; unmatched names encoded with the text tower | CC0 |
| Location prior (birds, mammals) | BirdNET geo 3.0 (`birdnet` package) | CC BY-NC-SA 4.0 |
| Bird list | AviList v2025 | CC BY 4.0 |
| Mammal list | Mammal Diversity Database v2.5 | CC BY 4.0 |

The BirdNET prior model is licensed non-commercially; for commercial use, drop the prior or replace its source.

## Install

```sh
git clone https://github.com/lincleejun/bioscan && cd bioscan
uv sync                                   # Python 3.12
```

Model weights are read from `~/.cache/huggingface`, and the service itself runs offline (`HF_HUB_OFFLINE=1`). All three models and the TreeOfLife vectors are pinned to fixed HF commits (`siglip2.REVISION`, `bioclip.REVISION`, `owlv2.REVISION`, `names.TOL_REVISION`), and `result.engine.models` carries those versions. On a new machine, or when the cache lacks the pinned snapshot, fetch once with network access:
```sh
uv run python tests/models/download.py      # pinned versions of the three models + BirdNET geo model; prints each version
```
The name-vector cache records the BioCLIP / TreeOfLife versions it was built with and is rebuilt when they change; older caches without that record are still used.

The name-list CSVs are large and not in git: download them as described in `data/README.md` into `data/avilist/` and `data/mdd/`. The first start encodes the lists as BioCLIP text vectors and caches them in `~/.cache/bioscan/names/` (this needs the 3.26 GB official TreeOfLife-200M vector file, which can be deleted afterwards; about half a minute). Later starts take a second. Editing `data/names/synonyms.csv` or `avilist_map.csv` rebuilds the bird cache once. `mdd_map.csv` carries labels only and does not touch the mammal cache.

```sh
uv run bioscan names stats                # name-list coverage
```

## Usage

```sh
uv run bioscan serve                                   # 127.0.0.1:8765, models stay resident
uv run bioscan serve --launchd > ~/Library/LaunchAgents/cc.outman.bioscan.plist   # start at login on macOS
uv run bioscan health
```

```sh
bioscan run /path/to/photos                            # filters and sorts by extension; -r recurses
bioscan run a.ARW b.ARW --want identify,embed --json --out preds.ndjson
bioscan run DIR --want jpg --jpg-out /tmp/jpg          # upright JPG, long edge 2048, named <stem>-<first 8 of sha256>.jpg
bioscan run DIR --lat 37.4 --lon -122.1                # batch default coordinate for images without EXIF GPS (the location prior matters)
bioscan run DIR --no-geo --top-k 10 --no-species
```

Terminal output is one line per image, then a summary (the grade is printed as 种 / 属 / 科 = species / genus / family):
```
DSC00364.ARW  bird    2 boxes  [1] Western Screech-Owl 0.91 种  [2] unconfirmed
DSC00458.ARW  none
DSC00566.ARW  mammal  1 box    [1] Rangifer tarandus 0.77 种
```

With `--json` the service's NDJSON is written as is, one line per image, for downstream programs:
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

### Supported files

`bioscan run` and `bioscan gt folders` scan for every extension below, in any case (`bioscan/formats.py` is the one list the scan and the decoder share); `--ext` narrows or widens it. GPS and capture time come from the file's EXIF and feed the location prior; a request's `lat`/`lon`/`taken_at` override them.

| Format | Extensions | Decode | GPS + capture time | `jpg` preview | Checked on real camera files |
|---|---|---|---|---|---|
| Sony | `.arw` | rawpy (LibRaw) | TIFF IFDs | yes | decode: yes (own tier); EXIF: not recorded |
| Nikon | `.nef` `.nrw` | rawpy | TIFF IFDs | yes | no |
| Canon (older) | `.cr2` | rawpy | TIFF IFDs | yes | no |
| Canon (R-series, M50, …) | `.cr3` | rawpy | CR3 `CMT1`/`CMT2`/`CMT4` boxes | yes | no |
| Fujifilm | `.raf` | rawpy | EXIF of the embedded JPEG | yes | no |
| OM System / Olympus | `.orf` | rawpy | TIFF IFDs (ORF header) | yes | no |
| Panasonic | `.rw2` | rawpy | TIFF IFDs (RW2 header); else the embedded JpgFromRaw | yes | no |
| Pentax, Samsung | `.pef` `.srw` | rawpy | TIFF IFDs | yes | no |
| DNG | `.dng` | rawpy | TIFF IFDs | yes | no |
| JPEG | `.jpg` `.jpeg` | Pillow | EXIF | yes | yes (iNat golden set) |
| PNG, TIFF, WebP | only with `--ext` or a file path | Pillow | EXIF when present | yes | no |
| HEIC / HEIF | not supported | no: Pillow needs the `pillow-heif` plugin; the image gets an `error` event | no | no | — |

Every format's metadata reader is tested on small synthetic files (`tests/unit/test_raw_exif.py`); none of the new ones (CR3, RAF, ORF, RW2) has been run on a real camera file yet. Check your own files without starting the service; this reads metadata only:
```sh
uv run python -m bioscan.service.decode /path/to/card -r    # per file: ext, container, lat, lon, taken_at; then a count per extension
```
`taken_at` keeps the sub-second part when the camera writes one (`SubSecTimeOriginal`), so burst frames get distinct, ordered times: `2026-05-01T08:00:00.37-07:00`. `bioscan gt folders` reads DateTimeOriginal, SubSecTimeOriginal and OffsetTimeOriginal with `exiftool` (the CLI does not load Pillow) and writes them in the same form.

### Ports and environment variables

| Name | Purpose | Default |
|---|---|---|
| `--port` | service port | 8765 |
| `BIOSCAN_URL` / `--url` | service the CLI talks to | `http://127.0.0.1:8765` |
| `--decode-workers` / `BIOSCAN_DECODE_WORKERS` | decode processes (about 4 is best for a USB hard disk) | 4 |
| `--chunk` / `BIOSCAN_CHUNK` | images per pipeline chunk | 32 |
| `--detail-edge` / `BIOSCAN_DETAIL_EDGE` | long edge of the species-crop detail copy; ≤ 2048 turns it off (crops come from the 2048 image) | 3072 |
| `--allow-root` / `BIOSCAN_ALLOW_ROOTS` | only read and write files under these directories (repeatable; `:`-separated in the variable); unset means no limit, with a warning when listening beyond localhost | no limit |

### HTTP API

```sh
curl -s 127.0.0.1:8765/health
curl -s 127.0.0.1:8765/products
curl -sN 127.0.0.1:8765/run -H 'content-type: application/json' \
  -d '{"inputs":[{"path":"/abs/a.ARW","lat":37.4,"lon":-122.1}],"want":["identify","embed","jpg"],"options":{"jpg":{"out_dir":"/tmp/jpg"}}}'
```
The response is an NDJSON stream of `progress` / `result` / `error` / `done` events, defined in `bioscan/contract.py` together with the `identify` payload (gate, boxes, quality, species, candidates); `result` and `done` carry `schema: 1`. Concurrent requests take turns on the models one chunk at a time (a one-image request waits for at most one chunk); within a chunk every model stage is batched across images, and CPU decoding overlaps inference. `result.engine` holds the model versions, the name-list versions, `settings` (a fingerprint of rule thresholds, prompts and vocabularies: if it changes, results are not directly comparable) and `detail_edge`. The full contract is in section 4 of `docs/superpowers/specs/2026-09-22-bioscan-design.md`.

CLI exit codes: 0 all images succeeded, 1 some images failed, 2 service unreachable or refused, 3 incomplete stream (no `done`) or an upstream error (iNaturalist and similar). `run --json` used to always return 0 and now follows these codes too; scripts that treat non-zero as failure should take note. eval now compares scientific names with the same normalisation as the synonym lookup (ignoring hyphens and case), so Top-1/Top-5 in older reports can differ slightly.

## Evaluation

```sh
bioscan gt folders /path/to/photos --out data/groundtruth-own.csv \
  --names data/avilist/AviList-v2025-11Jun-extended.csv --names data/mdd/MDD_v2.5_6904species.csv
bioscan gt inat --place california --taxa data/taxa.csv --per-species 25 --out data/inat   # --dry-run only prints URLs
bioscan eval data/inat/groundtruth-inat.csv --out runs/<date>          # calls the service, writes preds.ndjson + report.md
bioscan eval data/inat/groundtruth-inat.csv --out runs/<date>-nogeo --no-geo
bioscan eval GT.csv --out runs/x --preds runs/<date>/preds.ndjson        # rescore only
```
The first line of `preds.ndjson` is a meta line (schema, request options, sha256 of the ground truth and of synonyms.csv); rescoring reports whether the location prior was on from it. When the stream is cut short, eval exits with 3.
The report splits "no box" by whole-frame gate class: `none/person` means the gate missed the animal (the detector never ran); anything else means the detector did not box it.
Ground-truth columns: `path, scientific, tier, lat, lon, taken_at, source, kind`. Truth names are normalised to AviList/MDD through `data/names/synonyms.csv` before comparison.

### Harness: baselines, compare, failure analysis

`bioscan bench` builds on eval so that results never silently degrade. The full workflow and the schemas are in [docs/harness.md](docs/harness.md).
```sh
bioscan bench run data/inat/groundtruth-inat.csv --out runs/<date>-golden --tier golden   # eval + report.json
bioscan bench report runs/<date>/preds.ndjson GT.csv                                    # report.json offline from preds
bioscan bench baseline runs/<date>-golden/report.json --name golden-inat-<tag>          # -> baselines/
bioscan bench compare baselines/golden-inat-<tag>.json runs/<new>/report.json          # exit 1 over budget
bioscan bench analyze runs/<new>/report.json                                            # failure classes, what to fix next
bioscan bench scorecard runs/<new>/report.json                                          # against data/standards.toml
```
- **report.json** (`bioscan-report` v1) holds:
  - the run's git sha, engine, settings fingerprint and ground-truth sha;
  - every metric per scope (`all`, `bird`, `mammal`, `other`, and per tier) with Wilson 95% intervals;
  - per-species and per-family tables;
  - one row per image.
- **compare** pairs images by sha256 and counts fixed and broken images, with an exact McNemar p-value. It lists species regressions and broken images with their evidence, and checks `baselines/budget.toml`. It exits 0 within budget, 1 over budget and 2 when the reports can't be compared.
- **analyze** puts every wrong answer into a failure class: gate miss, detector miss, wrong kind, not in list, out of range, suppressed by the location prior, within genus, within family or far miss. It also flags overconfident answers. Each class comes with examples and a pointer to the code to fix.
- **CI.** `models.yml` compares every real-model smoke with `baselines/ci-smoke.json`. A `v*` tag publishes the smoke report.

## Name mapping

`data/names/avilist_map.csv`: for each AviList species, its TreeOfLife name and BirdNET label and how each was matched (exact / synonym / none). `synonyms.csv` is the hand-maintained alias table, each row with a source and a note; `candidates.csv` lists suspected spelling differences found by the script, for human review only, never adopted automatically. Rebuild with `uv run python scripts/build_name_map.py`.

`data/names/mdd_map.csv`: for each MDD species, its BirdNET label(s) and how they matched. Only BirdNET labels of class Mammalia count. Matching is exact name first, then the MDD synonym table (reviewed; see `data/README.md`). MDD lumps some species that BirdNET splits (e.g. four white-fronted capuchins into *Cebus albifrons*); such a row lists every label joined by `|`, and the prior takes the largest. Rebuild with `uv run python scripts/build_name_map.py --list mammal --mdd-synonyms MDD/Species_Syn_Current_v2.5.csv`.

The 748 AviList species without a BirdNET label get 0 in the location prior (mostly extinct species or species BirdNET lumps with a sister, such as *Tyto javanica*, which should stay suppressed). To find the gaps that actually matter at a place:
```sh
bioscan names geo-gaps --lat 37.4 --lon -122.1 --date 2026-05-01   # unlabelled species whose genus occurs there
```
After review, add the row to `synonyms.csv` (source `birdnet`) and rebuild the map.

| List | Total | Official TreeOfLife vectors | BirdNET label |
|---|---|---|---|
| AviList 2025 | 11131 | 84.6% | 93.3% |
| MDD v2.5 | 6904 | 55.5% | 15.1% (1042 rows carry all 1,048 BirdNET mammal labels; the rest back off to their genus) |

## Known limitations and roadmap

- 45 mammal images in the golden set got no box (mostly bears, mountain lions, bobcats). The detector vocabulary was extended and a gate-miss rescue added; the effect awaits a `bioscan eval` rerun.
- The mammal location prior, the range veto and the kind check are unverified on real photos until a `bioscan eval` on the Mac (CI's on/off table covers 77 photos only). ε, τ, the kind-check margin and the neutral constant are first guesses.
- The range veto can rename a genuine vagrant to a local congener (graded genus, never species).
- Recently split species (Northern / Hen Harrier, American / Western Barn Owl) carry old names in the training data and are separated by a shared vector plus the location prior; synonyms do not yet apply per region.
- The 0.02 floor in the prior formula limits how far location can override vision; it has not been tuned on the golden set.
- When the subject is tiny in the frame (distant raptors), the detector can box the wrong object.
- Only run on macOS + MPS and on CPU; no CUDA setup or Dockerfile.

## Tests

```sh
uv run ruff check .
uv run pytest                                     # no models, seconds; tests/models skipped by default
uv run python tests/models/download.py && BIOSCAN_MODEL_TESTS=1 uv run pytest tests/models   # real-model smoke, 77 iNat photos
uv run python tests/smoke/run_smoke.py --url ...  # needs a running service and your own tests/smoke/*.ARW
```

CI (`.github/workflows/`): `ci.yml` runs ruff + pytest on every push; `models.yml` runs the real-model smoke on CPU on every push / PR that touches the service, the model tests, the name data, eval/bench, baselines or dependencies (weights and photos cached), and on every `v*` tag. It writes the metrics to the job summary, and compares the run's report.json with `baselines/ci-smoke.json` under `baselines/budget.toml`: a regression over budget fails the job.

## Layout

```
bioscan/contract.py              single definition of /run events, product names and the identify payload (shared by CLI and service, stdlib only)
bioscan/naming.py                name normalisation (scientific names; gt folder labels), synonyms.csv, stale-map check (stdlib only)
bioscan/formats.py               supported photo extensions (decoder and folder scans share it), the folder scan (stdlib only)
bioscan/serve_config.py          serve settings: flag > BIOSCAN_* > default, once, for both entry points (stdlib only)
bioscan/service/app.py           routes, request validation, allow-roots, NDJSON stream
bioscan/service/run.py           a /run as events: chunks, per-chunk model turn, self-healing decode pool
bioscan/service/engine.py        device choice, lazy model loading via Loaders (tests inject fake adapters), per-kind priors
bioscan/service/products.py      product registry: dependencies, options, validation, schema, runner
bioscan/service/pipeline.py      identify orchestration (batched across images) behind the Models protocol
bioscan/service/rules.py         pure rules and thresholds: crop check, grading, quality, cropping
bioscan/service/taxa.py          gate prompts, detector vocabularies, promotable class
bioscan/service/settings.py      fingerprint of output-changing settings
bioscan/service/decode.py        RAW/JPG → upright 2048 image + detail copy + EXIF (every RAW container) + sha256
bioscan/service/names.py         AviList / MDD lists, TreeOfLife mapping, text-vector cache
bioscan/service/adapters/        siglip2 owlv2 bioclip geo
bioscan/cli/                     main client render gt eval bench (harness: report.json, compare, analyze, scorecard)
baselines/                       committed reports compared against, and the regression budget
data/names/                      name mapping tables keyed on AviList
docs/                            design spec, implementation plan, evaluation results
```

## License

Code is MIT (see `LICENSE`). Models and data carry their own licenses, listed above; the BirdNET prior is CC BY-NC-SA 4.0, so evaluate commercial use yourself.
