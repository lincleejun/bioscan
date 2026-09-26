# bioscan

**English** | [简体中文](README.zh-CN.md)

**What animal is in the photo, where, and which species.** A local identification service for wildlife photography: give it a folder of RAW or JPG files and get back, per image, animal boxes, the species (birds and mammals against curated lists, every other animal against the TreeOfLife all-taxa list), a confidence and a grade. It also places photos on a GPX track and culls an album into picks, bursts and rejects. A resident HTTP service plus a thin CLI; runs on MPS on a Mac; no photo ever leaves the machine.

## Results

Measured on the owner's M-series Mac (MPS), 2026-09-24, v1.5 (`875dc7a`). Full tables, the v1.4 comparison, failure classes and the scorecard: [docs/results.md](docs/results.md). The bars these are held to: [docs/standards.md](docs/standards.md).

**Species identification**, iNaturalist golden set (California, 65 species × 25 = 1,625 research-grade photos with GPS and dates):

| Group | Top-1 | Top-5 | Detected | Coverage | Precision | Confident errors |
|---|---|---|---|---|---|---|
| Birds (1050) | 91.2% | 95.7% | 97.9% | 92.5% | 97.1% | 2.7% |
| Mammals (575) | 82.8% | 86.3% | 89.6% | 81.4% | 95.1% | 4.0% |
| All (1625) | 88.2% | 92.4% | 95.0% | 88.6% | 96.5% | 3.1% |

Coverage = share of images graded to species; precision = Top-1 among those; confident errors = graded species-level and wrong. Against v1.4: Top-1 +3.6 points, confident errors 7.4% → 3.1%, coverage −2.9 points and throughput 4.7 → 2.2 img/s (both deliberate: stricter grading, and every box also scored against the 366k-species all-taxa list). Own telephoto RAW set (404 images, 3 owl species, no GPS): Top-1 79.0%, Top-5 98.0%; with one coordinate for the batch, 96.3%.

**Aesthetic score**, 100 EVA photos the general head never saw (20 per star, crowd consensus): Spearman 0.877 [0.82, 0.92] against the crowd's stars, drop AUC 0.98, 0% keepers lost when the lowest 20% is dropped. **Album culling** rules are measured on a synthetic reject set only (224 frames made from the CI photos: reject recall 84%, precision 96%; underexposure recall 33%, below its 80% floor); unverified on real albums.

**Scene category** (album profile), 1,861 CC BY photos from Open Images V7 in 8 groups (`data/scene/scene-v1.csv`): group correct 70.6% overall; people 87%, food 88%, macro 86%, wildlife 77%; night, landscape, other and architecture 59–65% (the 8 default labels are too coarse there; a finer taxonomy is in progress, [#34](https://github.com/lincleejun/bioscan/issues/34)).

**GPS from a GPX track**, synthetic tracks from the golden set: median error 7 m, 97% within 100 m, no false fix. Fed to identification, GPX positions give the same answers as true GPS (0 of 1,625 differ) and +5.2 points Top-1 over no coordinates.

## Quick start

```sh
git clone https://github.com/lincleejun/bioscan && cd bioscan
uv sync                                       # Python 3.12
uv run python tests/models/download.py        # pinned weights (~7 GB) + BirdNET geo model, once
# name lists: download AviList and MDD CSVs as described in data/README.md (the 3.26 GB TreeOfLife vector file builds the caches on first start)
uv run bioscan serve                          # 127.0.0.1:8765; models stay resident
uv run bioscan run ~/Pictures/trip -r         # one line per image, then a summary
```

```
DSC00364.ARW  bird    2 boxes  [1] Western Screech-Owl 0.91 种  [2] unconfirmed
DSC00458.ARW  none
DSC00566.ARW  mammal  1 box    [1] Rangifer tarandus 0.77 种
```

`--json` writes the service's NDJSON as is (boxes, species with taxonomy, p_visual / p_geo / posterior, timings). Full install notes, every flag and the output schema: [docs/usage.md](docs/usage.md).

## What it does

| | Command | Doc |
|---|---|---|
| **Identify** boxes and species, graded species / genus / family / unconfirmed; location prior from EXIF GPS or `--lat/--lon`; narrow to candidate taxa with `--candidates` | `bioscan run DIR` | [usage.md](docs/usage.md), [how-it-works.md](docs/how-it-works.md) |
| **Geotag** photos from a watch or phone GPX track; clock offset per camera from a photo of the clock or photos with GPS; XMP sidecars | `bioscan geotag DIR --gpx track.gpx` | [geotag.md](docs/geotag.md) |
| **Cull an album**: rejects with reasons, bursts with their best frame, best photos per scene category, aesthetic ranking; HTML review page, CSV, symlinks, optional XMP stars and labels in new sidecars; never deletes | `bioscan cull DIR -r --html review.html` | [album.md](docs/album.md) |
| **Summarize a run**: categories, taxa and a review queue with rule reasons as `summary.json`, rendered to an HTML report | `bioscan summarize preds.ndjson --out run`, `bioscan report run` | [usage.md](docs/usage.md#run-summary-and-report) |
| **Score a folder**: aesthetic ranking exported as NDJSON, CSV and/or an HTML review page (taxon tree with merges, keep/drop marks, full-size lightbox), with the animals named on `--species`; re-export offline from the NDJSON; `aesthetic apply` copies the keeps out and moves the drops | `bioscan aesthetic score DIR -r --species --export json,csv,html --out aes` | [album.md](docs/album.md) |
| **Personal aesthetics**: a head fitted on your Lightroom stars, blended with the general one | `bioscan aesthetic train --ratings DIR` | [album.md](docs/album.md) |
| **Profiles** `full`, `wildlife`, `album`, and your own in `bioscan.toml` | `bioscan run DIR --profile album` | [usage.md](docs/usage.md#profiles-and-bioscantoml) |
| **Evaluate**: ground truth from folders or iNaturalist, reports with Wilson intervals, baselines and regression budgets, failure classes, scorecard against the standards | `bioscan eval`, `bioscan bench` | [development.md](docs/development.md), [harness.md](docs/harness.md) |
| **HTTP API**: `/run` streams NDJSON; `/health`, `/products` | `curl 127.0.0.1:8765/run` | [usage.md](docs/usage.md#http-api) |

Models: SigLIP2 (gate, embed), OWLv2 (detection), BioCLIP 2.5 Huge (species), BirdNET geo (location prior), AviList 2025 and MDD v2.5 name lists, TreeOfLife-200M all-taxa list. Sources and licences: [docs/how-it-works.md](docs/how-it-works.md#pipeline).

## Status

- Species accuracy is measured on the golden set above; the v0.x release gates in `docs/standards.md` are not met yet (mammal detection and Top-5 fall short).
- Other animals (reptiles, fish, insects, …) are named from TreeOfLife's list, which lacks many reptiles and ray-finned fish; they have no location prior. Accuracy is measured on 18 CI photos only.
- Culling rules, burst grouping and the aesthetic head are unverified on real albums; `bioscan aesthetic eval` measures agreement with your own stars.
- Runs on macOS + MPS and on CPU; no CUDA setup or Dockerfile.
- The full list: [docs/how-it-works.md#known-limitations-and-roadmap](docs/how-it-works.md#known-limitations-and-roadmap).

## Documentation

| Document | What it holds |
|---|---|
| [docs/usage.md](docs/usage.md) | install, CLI, supported RAW formats, ports and variables, candidates, profiles and `bioscan.toml`, run summary and report, Lightroom Classic, HTTP API, exit codes |
| [docs/how-it-works.md](docs/how-it-works.md) | scope, the pipeline, grading and the accuracy rules, models and data with licences, name mapping, known limitations |
| [docs/geotag.md](docs/geotag.md) | GPX geotagging: sources, time zones, clock offset, fix rule, XMP, accuracy |
| [docs/album.md](docs/album.md) | aesthetics (general and personal head, evaluation, the golden set) and `bioscan cull` |
| [docs/results.md](docs/results.md) | the measured numbers, v1.4 vs v1.5, own photos, RAW metadata |
| [docs/standards.md](docs/standards.md) | the bars in 11 dimensions, with sources, and the release stages |
| [docs/development.md](docs/development.md) | evaluation commands, the harness in short, tests, CI, source layout |
| [docs/harness.md](docs/harness.md) | `bioscan bench` in full: report.json schema, compare, budgets, analyze, scorecard, album and aesthetic tiers |
| [data/README.md](data/README.md) | name lists, the all-taxa list, the golden ground truth, the aesthetic head |
| [CONTEXT.md](CONTEXT.md) | domain terms used in code, tests and docs |

## License

Code is MIT (see `LICENSE`). Models and data carry their own licenses, listed in [docs/how-it-works.md](docs/how-it-works.md#pipeline); the BirdNET prior is CC BY-NC-SA 4.0, so evaluate commercial use yourself.
