# Development: evaluation, tests, CI and layout

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

`bioscan bench` builds on eval so that results never silently degrade. The full workflow and the schemas are in [docs/harness.md](harness.md).
```sh
bioscan bench run data/inat/groundtruth-inat.csv --out runs/<date>-golden --tier golden   # eval + report.json
bioscan bench report runs/<date>/preds.ndjson GT.csv                                    # report.json offline from preds
bioscan bench baseline runs/<date>-golden/report.json --name golden-inat-<tag>          # -> baselines/
bioscan bench compare baselines/golden-inat-<tag>.json runs/<new>/report.json          # exit 1 over budget
bioscan bench analyze runs/<new>/report.json                                            # failure classes, what to fix next
bioscan bench scorecard runs/<new>/report.json                                          # against data/standards.toml
bioscan bench geotag runs/geotag-synth                                                  # GPX geotagging on synthetic tracks
bioscan bench aesthetic score ~/aes-golden runs/aes/eva.ndjson --out runs/aes/eva        # any aesthetic scorer vs the golden set
```
- **report.json** (`bioscan-report` v1) holds:
  - the run's git sha, engine, settings fingerprint and ground-truth sha;
  - every metric per scope (`all`, `bird`, `mammal`, `other`, and per tier) with Wilson 95% intervals;
  - per-species and per-family tables;
  - one row per image;
  - `meta.profile`, and `plugin_metrics`: each plugin's own metrics (the album tier's reject precision and recall per
    reason, keepers lost, burst pairwise F1, scene accuracy), with Wilson intervals, which budgets and standards may
    name.
- **compare** pairs images by sha256 and counts fixed and broken images, with an exact McNemar p-value. Metrics, species changes and the budget use the paired images only, so a test set that gains photos never counts as a regression; new images are listed separately. It lists species regressions and broken images with their evidence, and checks `baselines/budget.toml`. It exits 0 within budget, 1 over budget and 2 when the reports can't be compared.
- **analyze** puts every wrong answer into a failure class: gate miss, detector miss, wrong kind, not in list, out of range, suppressed by the location prior, within genus, within family or far miss. It also flags overconfident answers. Each class comes with examples and a pointer to the code to fix.
- **CI.** `models.yml` compares every real-model smoke with `baselines/ci-smoke.json`. A `v*` tag publishes the smoke report.

## Tests

```sh
uv run ruff check .
uv run pytest                                     # no models, seconds; tests/models skipped by default
uv run python tests/models/download.py && BIOSCAN_MODEL_TESTS=1 uv run pytest tests/models   # real-model smoke, 95 iNat photos
uv run python tests/smoke/run_smoke.py --url ...  # needs a running service and your own tests/smoke/*.ARW
```

CI (`.github/workflows/`): `ci.yml` runs ruff + pytest on every push; `models.yml` runs the real-model smoke on CPU on every push / PR that touches the service, the model tests, the name data, eval/bench, baselines or dependencies (weights, photos and the all-taxa list cached; the first run after a cache miss downloads the 3.26 GB TreeOfLife vectors to build it), and on every `v*` tag. It writes the metrics to the job summary per kind (birds, mammals and the 18 other animals, which are ranked against the real all-taxa list), and compares the run's report.json with `baselines/ci-smoke.json` under `baselines/budget.toml`: a regression over budget fails the job. Model outputs are cached between runs (the inference cache, keyed on the adapters and `uv.lock`), so a run computes only what reaches a model differently; a `v*` tag or a `cold` dispatch runs every model. It then runs the album profile on a synthetic reject set made from 24 of the photos and compares `models-report-album.json` with `baselines/ci-album.json` under `baselines/budget-album.toml` (until that baseline is committed, the job prints the candidate). `aesthetic.yml` runs only on demand (Actions tab, or a pushed `aesthetic-head-*` tag): it trains the EVA general head on CPU and prints the head file base64 in the log (data/aesthetic/README.md).

## Layout

```
bioscan/contract.py              single definition of /run events, product names and the identify payload (shared by CLI and service, stdlib only)
bioscan/naming.py                name normalisation (scientific names; gt folder labels), synonyms.csv, stale-map check (stdlib only)
bioscan/formats.py               supported photo extensions (decoder and folder scans share it), the folder scan (stdlib only)
bioscan/serve_config.py          serve settings: flag > BIOSCAN_* > bioscan.toml [serve] > default, once, for both entry points (stdlib only)
bioscan/profile.py               profiles and bioscan.toml: layers, merge order, the resolved plan (stdlib only; CLI and service)
bioscan/profiles.toml            the built-in profiles full, wildlife, album
bioscan/service/app.py           routes, request validation, allow-roots, NDJSON stream
bioscan/service/run.py           a /run as events: chunks, per-chunk model turn, self-healing decode pool
bioscan/service/engine.py        device choice, lazy model loading via Loaders (tests inject fake adapters), per-kind priors
bioscan/plugin.py                what a stage plugin declares (Manifest) and implements (Stage); the run plan (stdlib only)
bioscan/plugins/<name>/          built-in stages identify, embed, jpg, geotag, aesthetics, quality, scene: stdlib manifest in
                                 __init__.py, service code in stage.py; reducer manifests burst, select
bioscan/service/stages.py        the stages in the service: options merged and checked, /products, paths for allow-roots
bioscan/service/pipeline.py      identify orchestration (batched across images) behind the Models protocol
bioscan/service/rules.py         pure rules and thresholds: crop check, grading, quality, cropping
bioscan/service/taxa.py          gate prompts, detector vocabularies, promotable class
bioscan/service/settings.py      fingerprint of output-changing settings
bioscan/service/decode.py        RAW/JPG → upright 2048 image + detail copy + EXIF (every RAW container) + sha256
bioscan/service/names.py         AviList / MDD lists, the TreeOfLife all-taxa list, TreeOfLife mapping, text-vector cache
bioscan/service/candidates.py    the candidates option: taxon index, the rows each list keeps
bioscan/service/adapters/        siglip2 owlv2 bioclip geo
bioscan/geotag.py                GPX parsing, capture time -> UTC, clock offset, track interpolation, the GPS XMP packet (stdlib only)
bioscan/xmp.py                   new XMP sidecars, never over an existing one: geotag --xmp, cull --xmp (stdlib only)
bioscan/aesthetic.py             aesthetic head files, XMP/CSV ratings, trip folds, ranking metrics (stdlib only)
bioscan/aesthetic_fit.py         ridge heads, CV, prior pull, learning curve (numpy; imported only to train or for the curve)
bioscan/cull.py                  the burst and select reducers, cull records, the album tier's metric rows (stdlib only)
bioscan/cli/                     main client render gt eval bench (harness: report.json, compare, analyze, scorecard)
                                 config (profiles) geotag_cli (bioscan geotag, run --gpx) geobench (bench geotag)
                                 aesbench (bioscan aesthetic ratings|train|eval) aesgolden (bench aesthetic)
                                 cull (bioscan cull: reducers, CSV, symlinks, HTML review, XMP)
                                 lr (bioscan lr: Lightroom Classic latest.json, plugin install)
extensions/lightroom/            the Lightroom Classic plugin (Lua) that applies latest.json
scripts/geotag_synth.py          synthetic GPX scenarios from the golden set, for bench geotag
scripts/train_aesthetic_head.py  the EVA general head, in-process with the service's decode and SigLIP2
scripts/aes_plant.py             planted copies (known answers) for the aesthetic golden set
scripts/cull_synth.py            synthetic album set (labelled rejects, bursts) from photos with a subject box
data/aesthetic/                  the general aesthetic head, the held-out EVA list and their README
baselines/                       committed reports compared against, and the regression budget
data/names/                      name mapping tables keyed on AviList
docs/                            design spec, implementation plan, evaluation results
```
