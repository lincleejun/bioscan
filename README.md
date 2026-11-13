# Gardener

**English** | [简体中文](README.zh-CN.md)

**Sort a wildlife shoot on your own Mac.** Point Gardener at a card or a folder: it previews every RAW and JPG, finds the animals, names the species, groups bursts, and gives you a review page ordered by species. You keep, star and reject; it never deletes and no photo leaves the machine.

## Video

52 seconds, Chinese narration and captions: [assets/gardener-promo.mp4](assets/gardener-promo.mp4). Every screen in it is the real UI on a real shoot of 1,361 photos.

## What you get

- **Scan**: several cards or folders in one task; previews first, so review can start before analysis ends.
- **Species**: birds against AviList, mammals against MDD, every other animal against the TreeOfLife list, with a location prior from GPS. A wrong name is corrected once for the whole shoot.
- **Review by group**: one burst is one group. Star what you keep, reject the rest, or finish many groups at once. Every mark can be undone.
- **AI 选鸟**: a few best frames of each species, from different moments, in one collection.
- **Out**: copy the keeps with checksums, hand stars and species keywords to Lightroom Classic, move confirmed rejects to the Trash.
- **Three front ends, one local service**: web UI, `gardener` CLI, MCP.

## Quick start

macOS on Apple Silicon (MPS) or CPU; Python 3.12 with `uv`.

```sh
git clone https://github.com/lincleejun/gardener && cd gardener
uv sync
uv run python tests/models/download.py    # pinned weights, about 7 GB, once
# name lists: download the AviList and MDD CSVs, see docs/usage.md#name-lists
uv run gardener-engine names stats        # once, online: builds the name caches (3.26 GB download)
uv run gardener serve                     # open http://127.0.0.1:8770
```

## The engine on its own

`gardener-engine` is the identification service and its CLI, usable without the workbench:

| Command | Does |
|---|---|
| `gardener-engine run DIR -r` | boxes and species per photo, graded species / genus / family / unconfirmed |
| `gardener-engine geotag DIR --gpx track.gpx` | GPS from a watch or phone track, written to XMP sidecars |
| `gardener-engine cull DIR -r --html review.html` | rejects with reasons, bursts with their best frame, picks per scene |
| `gardener-engine aesthetic score DIR --species --export html` | aesthetic ranking with names, as a review page |
| `gardener-engine eval`, `gardener-engine bench` | accuracy reports, baselines and regression budgets |

Every flag, the output schema and the HTTP API: [docs/usage.md](docs/usage.md).

## Measured

iNaturalist golden set, California: 64 species, 1,571 research-grade photos with GPS, on an M-series Mac (2026-11-13).

| Group | Top-1 | Top-5 | Graded to species | Right when graded |
|---|---|---|---|---|
| Birds (1046) | 91.7% | 96.2% | 92.8% | 97.1% |
| Mammals (525) | 91.0% | 94.9% | 87.8% | 96.5% |

Aesthetic score against crowd stars (100 EVA photos): Spearman 0.877. GPS from a GPX track: median error 7 m on synthetic tracks. All tables and how they were measured: [docs/results.md](docs/results.md); the bars they are held to: [docs/standards.md](docs/standards.md).

Not measured yet: burst grouping, culling rules and AI 选鸟 on real albums; species outside birds and mammals beyond 18 test photos. The release gates in `docs/standards.md` are not all met. Known limits: [docs/how-it-works.md](docs/how-it-works.md#known-limitations-and-roadmap).

## Documentation

| | |
|---|---|
| [docs/usage.md](docs/usage.md) | install, both CLIs, profiles and `gardener.toml`, Lightroom, HTTP API |
| [docs/gardener/](docs/gardener/) | the workbench: product brief, API contract, decisions |
| [docs/how-it-works.md](docs/how-it-works.md) | pipeline, grading rules, models and data with licences |
| [docs/album.md](docs/album.md), [docs/geotag.md](docs/geotag.md) | culling and aesthetics; GPX geotagging |
| [docs/development.md](docs/development.md), [docs/harness.md](docs/harness.md) | tests, CI, the evaluation harness |
| [CONTEXT.md](CONTEXT.md) | the terms used in code and docs |

## Formerly bioscan

Renamed in 2026-11. The `bioscan` command is now `gardener-engine`. `BIOSCAN_*` variables and `bioscan.toml` are still read (new names: `GARDENER_ENGINE_*`, `gardener.toml`). Names already on disk are unchanged: `~/.cache/bioscan`, the XMP `bioscan:` fields and the Lightroom plugin.

## License

Code: MIT ([LICENSE](LICENSE)). Models and data keep their own licences ([list](docs/how-it-works.md#pipeline)); the BirdNET location prior is CC BY-NC-SA 4.0, so check before commercial use.
