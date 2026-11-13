# Development: evaluation, tests, CI and layout

## Evaluation

```sh
gardener-engine gt folders /path/to/photos --out data/groundtruth-own.csv \
  --names data/avilist/AviList-v2026-20Oct2026-extended-version.csv --names data/mdd/MDD_v2.5_6904species.csv
gardener-engine gt inat --place california --taxa data/taxa.csv --per-species 25 --out data/inat   # --dry-run only prints URLs
gardener-engine eval data/inat/groundtruth-inat.csv --out runs/<date>          # calls the service, writes preds.ndjson + report.md
gardener-engine eval data/inat/groundtruth-inat.csv --out runs/<date>-nogeo --no-geo
gardener-engine eval GT.csv --out runs/x --preds runs/<date>/preds.ndjson        # rescore only
```
The first line of `preds.ndjson` is a meta line (schema, request options, sha256 of the ground truth and of synonyms.csv); rescoring reports whether the location prior was on from it. When the stream is cut short, eval exits with 3.
The report splits "no box" by whole-frame gate class: `none/person` means the gate missed the animal (the detector never ran); anything else means the detector did not box it.
Ground-truth columns: `path, scientific, tier, lat, lon, taken_at, source, kind`. Truth names are normalised to AviList/MDD through `data/names/synonyms.csv` before comparison.

### Harness: baselines, compare, failure analysis

`gardener-engine bench` builds on eval so that results never silently degrade. The full workflow and the schemas are in [docs/harness.md](harness.md).
```sh
gardener-engine bench run data/inat/groundtruth-inat.csv --out runs/<date>-golden --tier golden   # eval + report.json
gardener-engine bench report runs/<date>/preds.ndjson GT.csv                                    # report.json offline from preds
gardener-engine bench baseline runs/<date>-golden/report.json --name golden-inat-<tag>          # -> baselines/
gardener-engine bench compare baselines/golden-inat-<tag>.json runs/<new>/report.json          # exit 1 over budget
gardener-engine bench analyze runs/<new>/report.json                                            # failure classes, what to fix next
gardener-engine bench scorecard runs/<new>/report.json                                          # against data/standards.toml
gardener-engine bench geotag runs/geotag-synth                                                  # GPX geotagging on synthetic tracks
gardener-engine bench aesthetic score ~/aes-golden runs/aes/eva.ndjson --out runs/aes/eva        # any aesthetic scorer vs the golden set
```
What report.json holds, and what compare and analyze report, is in [docs/harness.md](harness.md).

## Tests

```sh
uv run ruff check .
uv run pytest                                     # no models, seconds; tests/models skipped by default
uv run python tests/models/download.py && GARDENER_ENGINE_MODEL_TESTS=1 uv run pytest tests/models   # real-model smoke, 95 iNat photos
uv run python tests/smoke/run_smoke.py --url ...  # needs a running service and your own tests/smoke/*.ARW
```

Test and CI switches (environment variables read only by tests and `models.yml`):

| Variable | Effect |
|---|---|
| `GARDENER_ENGINE_REGOLDEN=1` | re-record the golden files of `tests/contract/test_golden_stream.py` and `tests/unit/test_cli_report.py` (only for a reviewed output change) |
| `GARDENER_ENGINE_PHOTO_CACHE` | folder of the real-model test photos (default `~/.cache/bioscan/model-test-photos`) |
| `GARDENER_ENGINE_REPORT` | path of the real-model report (markdown); `models-report.json` and `models-report-album.json` are written next to it |
| `GARDENER_ENGINE_INFER_CACHE` | file that stores model outputs between real-model runs and replays them; empty turns it off (`models.yml` leaves it empty on a `v*` tag or a `cold` dispatch) |
| `GARDENER_ENGINE_REQUIRE_GEO=1` | the real-model tests fail when the BirdNET geo prior does not load |
| `GARDENER_ENGINE_REQUIRE_ALLTAXA=1` | the real-model tests fail when the all-taxa list is missing, instead of skipping the other-animal floors |
| `GARDENER_ENGINE_ALBUM_SOURCES` | how many bird and mammal photos the album-profile test degrades into rejects and bursts (default 24) |

CI (`.github/workflows/`): `ci.yml` runs ruff + pytest on every push; `models.yml` runs the real-model smoke on CPU on every push / PR that touches the service, the model tests, the name data, eval/bench, baselines or dependencies (weights, photos and the all-taxa list cached; the first run after a cache miss downloads the 3.26 GB TreeOfLife vectors to build it), and on every `v*` tag. It writes the metrics to the job summary per kind (birds, mammals and the 18 other animals, which are ranked against the real all-taxa list), and compares the run's report.json with `baselines/ci-smoke.json` under `baselines/budget.toml`: a regression over budget fails the job. Model outputs are cached between runs (the inference cache, keyed on the adapters and `uv.lock`; a branch run restores its own newest cache, else main's newest), so a run computes only what reaches a model differently; a `v*` tag or a `cold` dispatch runs every model. It then runs the album profile on a synthetic reject set made from 24 of the photos and compares `models-report-album.json` with `baselines/ci-album.json` under `baselines/budget-album.toml`: a regression over budget fails the job too. `aesthetic.yml` runs only on demand (Actions tab, or a pushed `aesthetic-head-*` tag): it trains the EVA general head on CPU and prints the head file base64 in the log (data/aesthetic/README.md).

## Layout

```
gardener/contract.py              single definition of /run events, product names and the identify payload (shared by CLI and service, stdlib only)
gardener/naming.py                name normalisation (scientific names; gt folder labels), synonyms.csv, stale-map check (stdlib only)
gardener/formats.py               supported photo extensions (decoder and folder scans share it), the folder scan (stdlib only)
gardener/apply.py                 acting on a review's marks: copy the keeps, move or delete the drops, with their XMP sidecars
gardener/serve_config.py          serve settings: flag > GARDENER_ENGINE_* > gardener.toml [serve] > default, once, for both entry points (stdlib only)
gardener/profile.py               profiles and gardener.toml: layers, merge order, the resolved plan (stdlib only; CLI and service)
gardener/profiles.toml            the built-in profiles full, wildlife, album
gardener/service/app.py           routes, request validation, allow-roots, NDJSON stream
gardener/service/run.py           a /run as events: chunks, per-chunk model turn, self-healing decode pool
gardener/service/engine.py        device choice, lazy model loading via Loaders (tests inject fake adapters), per-kind priors
gardener/plugin.py                what a stage plugin declares (Manifest) and implements (Stage); the run plan (stdlib only)
gardener/plugins/<name>/          built-in stages identify, embed, jpg, geotag, aesthetics, quality, scene, facing: stdlib manifest in
                                 __init__.py, service code in stage.py; reducer manifests burst, select
gardener/service/stages.py        the stages in the service: options merged and checked, /products, paths for allow-roots
gardener/service/pipeline.py      identify orchestration (batched across images) behind the Models protocol
gardener/service/rules.py         pure rules and thresholds: crop check, grading, quality, cropping
gardener/service/taxa.py          gate prompts, detector vocabularies, promotable class
gardener/service/settings.py      fingerprint of output-changing settings
gardener/service/decode.py        RAW/JPG → upright 2048 image + detail copy + EXIF (every RAW container) + sha256
gardener/service/names.py         AviList / MDD lists, the TreeOfLife all-taxa list, TreeOfLife mapping, text-vector cache
gardener/service/candidates.py    the candidates option: taxon index, the rows each list keeps
gardener/service/adapters/        siglip2 owlv2 bioclip geo
gardener/geotag.py                GPX parsing, capture time -> UTC, clock offset, track interpolation, the GPS XMP packet (stdlib only)
gardener/xmp.py                   new XMP sidecars, never over an existing one: geotag --xmp, cull --xmp (stdlib only)
gardener/aesthetic.py             aesthetic head files, XMP/CSV ratings, trip folds, ranking metrics (stdlib only)
gardener/aesthetic_fit.py         ridge heads, CV, prior pull, learning curve (numpy; imported only to train or for the curve)
gardener/cull.py                  the burst and select reducers, cull records, the album tier's metric rows (stdlib only)
gardener/cli/                     main client (thin HTTP client) render gt eval bench (harness: report.json, compare, analyze, scorecard)
                                 config (profiles) geotag_cli (gardener-engine geotag, run --gpx) geobench (bench geotag)
                                 aesbench (gardener-engine aesthetic ratings|train|eval) aesgolden (bench aesthetic)
                                 cull (gardener-engine cull: reducers, CSV, symlinks, HTML review, XMP)
                                 lr (gardener-engine lr: Lightroom Classic latest.json, plugin install)
                                 report (gardener-engine summarize / report: the run summary)
                                 aesscore (gardener-engine aesthetic score / apply) aespage (its HTML review page)
gardener/workbench/                Gardener: local-first photo review workstation on top of gardener (docs/gardener/)
extensions/lightroom/            the Lightroom Classic plugin (Lua) that applies latest.json
scripts/build_names.py           build (or load from cache) the bird and mammal name matrices, print stats
scripts/build_name_map.py        build the committed label maps under data/names/
scripts/eva_golden.py            the public aesthetic golden set: 100 held-out EVA images, 20 per star
scripts/aes_arena_score.py       score a golden folder with an external aesthetic model, for bench aesthetic score
scripts/geotag_synth.py          synthetic GPX scenarios from the golden set, for bench geotag
scripts/train_aesthetic_head.py  the EVA general head, in-process with the service's decode and SigLIP2
scripts/aes_plant.py             planted copies (known answers) for the aesthetic golden set
scripts/cull_synth.py            synthetic album set (labelled rejects, bursts) from photos with a subject box
data/aesthetic/                  the general aesthetic head, the held-out EVA list and their README
baselines/                       committed reports compared against, and the regression budget
data/names/                      name mapping tables keyed on AviList
docs/                            design spec, implementation plan, evaluation results
```
