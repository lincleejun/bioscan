# Usage

Install, the CLI, the files it reads, its settings (ports, variables, profiles, `bioscan.toml`), candidate taxa and the HTTP API. What the pipeline does with all this is in [how-it-works.md](how-it-works.md); GPX geotagging is in [geotag.md](geotag.md) and album culling in [album.md](album.md).

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

The name-list CSVs are large and not in git: download them as described in `data/README.md` into `data/avilist/` and `data/mdd/`. The first start encodes the lists as BioCLIP text vectors and caches them in `~/.cache/bioscan/names/` (this needs the 3.26 GB official TreeOfLife-200M vector file; about half a minute). The same first start also builds the all-taxa list from that file (no encoding; a float16 cache of about 1 GB). Delete the TreeOfLife file only after both are cached: if it is gone and the all-taxa cache is missing, the service still starts, logs a warning and gives other animals `species: null`, as before; `uv run python tests/models/download.py` or `uv run bioscan names stats` builds it again. Later starts take a few seconds (the all-taxa list is most of that). Editing `data/names/synonyms.csv` or `avilist_map.csv` rebuilds the bird cache once. `mdd_map.csv` carries labels only and does not touch the mammal cache.

```sh
uv run bioscan names stats                # name-list coverage
```

## Running

```sh
uv run bioscan serve                                   # 127.0.0.1:8765, models stay resident
uv run bioscan serve --launchd > ~/Library/LaunchAgents/cc.outman.bioscan.plist   # start at login on macOS
uv run bioscan health
uv run bioscan config show                             # the profile, plan and settings in effect, with their sources
```

```sh
bioscan run /path/to/photos                            # filters and sorts by extension; -r recurses
bioscan run a.ARW b.ARW --want identify,embed --json --out preds.ndjson
bioscan run DIR --want jpg --jpg-out /tmp/jpg          # upright JPG, long edge 2048, named <stem>-<first 8 of sha256>.jpg
bioscan run DIR --lat 37.4 --lon -122.1                # batch default coordinate for images without EXIF GPS, which the CLI reads with Pillow (no exiftool needed)
bioscan run DIR --no-geo --top-k 10 --no-species
```
The service loads only the models a run's stages need under its options: `--want embed` loads SigLIP2 alone, and `--no-species` (identify option `species: false`, with no `candidates`) loads SigLIP2 and OWLv2 but never BioCLIP or the name lists, so a service that has only served such runs reports no name lists in `result.engine.models.names`.

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
| `--profile` / `BIOSCAN_PROFILE` | the CLI's profile (run, eval, bench run, config show); see Profiles | `default_profile`, else `full` |
| `BIOSCAN_CONFIG` | one more `bioscan.toml`, above the project and user files | none |

Every serve setting above except the URL can also come from the `[serve]` table of a `bioscan.toml` (flag > variable > file > default; see Profiles and bioscan.toml).

### All taxa and candidates

By default every box is named against all taxa bioscan has: birds against AviList, mammals against MDD, and every other animal (`other_animal` boxes) against the **all-taxa list** `tol200m-animalia`, which holds every species-level TreeOfLife-200M row of the animal kingdom outside Aves and Mammalia (366,460 species, measured in CI 2026-09; each row has its official BioCLIP vector). **Known gap:** the TreeOfLife-200M BioCLIP 2.5 embedding file has no rows at all for many reptiles and ray-finned fish (none of the smoke set's 6 reptiles and 1 fish, not even their genera), so those animals get a wrong name, sometimes at species level; a self-encoded reptile/fish list is the next step (issue #17). There is nothing to choose first. The output shape does not change: `species.list` names the list used, and `engine.models.taxonomy` says which taxonomy each list follows. Other animals have no location prior yet (`p_geo` is null).

Plants and fungi are not in the default: the gate has no plant class, so no box could reach them, and they would cost memory for nothing. They become a second list together with a gate class, as a separate change.

When you already know the answer is one of a few taxa, `candidates` narrows the ranking to them, which is faster to read and harder to get wrong:
```sh
bioscan run DIR --candidates "Megascops kennicottii,Strigidae,Bubo"
curl -sN 127.0.0.1:8765/run -H 'content-type: application/json' \
  -d '{"inputs":[{"path":"/abs/a.ARW"}],"options":{"identify":{"candidates":["Megascops kennicottii","Strigidae","Bubo"]}}}'
```
- A candidate is a scientific name or any higher taxon (genus, family, order, class, even phylum), compared case- and spacing-insensitively. It matches every row of every loaded list whose taxonomy holds it.
- Only lists with a matching row compete, and only their matching rows. Every box is named, whatever its kind. A bird or mammal box competes among the bird and mammal lists with a match; only if neither has one does it go to the other lists (the all-taxa list), as its only option. Candidates that are all mammals turn a bird-gated box into a mammal box ranked among those mammals.
- With `kind_check` on (the default) the kind check runs over the competing lists only, on their matching rows: a kind with no candidate cannot win. With it off, a box keeps its kind when its own list has a matching row, and otherwise goes to the competing list with the most evidence (you said the answer is there, so that move is not graded `unconfirmed`).
- Within the chosen list, `p_visual` is the softmax over its matching rows; the location prior (birds, and mammals with `mammal_geo`), the range veto and the grade then work on those rows as they do on a whole list. `species.list` names the list, and every candidate in `top` comes from it.
- Empty or absent means all taxa. A name no loaded list knows is a 400 that lists the unknown names. `bioscan eval --candidates …` passes the option through and records it in the preds meta line and the report (with `--preds`, which rescores a finished run, it is refused).

### Profiles and bioscan.toml

A **profile** is a named request template: the stages a run wants and their options. Three are built in (`bioscan/profiles.toml`):

| Profile | Stages now | Options | Models loaded | Will gain |
|---|---|---|---|---|
| `full` | identify (embed, jpg when `want` asks) | defaults | SigLIP2, OWLv2, BioCLIP | nothing: it is what a request without a profile gets, and no file can change it |
| `wildlife` | geotag, identify | species, location prior and every accuracy fix on (`top_k` 5, `geo` true); geotag does nothing until `geotag.gpx` (or `run --gpx`) names a track | SigLIP2, OWLv2, BioCLIP | nothing planned |
| `album` | identify, embed, aesthetics, quality, scene; reducers burst, select (run by `bioscan cull`, never the service) | identify `species: false`; aesthetics `head: builtin`; scene labels; burst and select limits | SigLIP2, OWLv2 (BioCLIP never loads) | nothing planned |

```sh
bioscan run DIR --profile album                  # the profile's stages and options; flags still win
bioscan run DIR --profile wildlife --top-k 10
bioscan eval GT.csv --out runs/x --profile wildlife   # also bench run; recorded as "profile" in the preds meta line
bioscan config show --profile album              # the resolved plan and where every value came from (--json)
curl -sN 127.0.0.1:8765/run -H 'content-type: application/json' -d '{"inputs":[{"path":"/abs/a.ARW"}],"profile":"album"}'
```

Your own settings go in a `bioscan.toml`: the project one (`./bioscan.toml`, where you run the command) and the user one (`~/.config/bioscan/bioscan.toml`, or under `$XDG_CONFIG_HOME`); `$BIOSCAN_CONFIG` names one more. A file may change `wildlife` and `album` key by key, add profiles, pick the CLI's default profile and hold the service settings:

```toml
default_profile = "wildlife"          # the CLI's profile when --profile is not given

[serve]                               # bioscan serve: flag > BIOSCAN_* variable > this table > default
port = 8765
chunk = 32
detail_edge = 3072
allow_roots = ["~/Pictures/Wildlife"]

[profile.wildlife.options.identify]   # change a built-in profile
top_k = 10

[profile.trip]                        # or add one
stages = ["identify", "jpg"]
[profile.trip.options.jpg]
out_dir = "/Users/me/Pictures/trip-jpg"
[profile.trip.options.identify]
candidates = ["Strigidae", "Accipitridae"]
```

- **Merge order**, lowest first: each stage's defaults < `profiles.toml` < the user file < the project file < `$BIOSCAN_CONFIG` < command-line flags or the request's `want` / `options`. `--want` replaces the profile's stages.
- **Which profile**: `--profile`, else `$BIOSCAN_PROFILE`, else `default_profile`, else `full`. With none of them the CLI sends exactly the request it sent before profiles. The CLI expands the profile itself and sends plain `want` and `options`, so the service needs no copy of your file.
- **The service** expands a request's `"profile"` with its own files (read once at start). A request without `"profile"` always gets `full`: `default_profile` and `$BIOSCAN_PROFILE` are the CLI's, so HTTP clients see no change.
- **Errors**: an unknown key, profile, stage or option is an error naming the file (a 400 for a request). `[profile.full]` in a user file is refused. Option values are checked by the service. `bioscan serve --launchd` bakes the flags, else the file's `[serve]` values, into the plist and points the job at this folder's `bioscan.toml` through `BIOSCAN_CONFIG`.
- **Trust**: `./bioscan.toml` is read automatically from the folder you run in, so only run bioscan in folders whose file you trust: it can set `serve.host = "0.0.0.0"`, `allow_roots`, or a jpg `out_dir` to write to; `bioscan config show` lists every file read and the value each one set.
- **Stages and plugins**: each stage is a plugin in `bioscan/plugins/<name>/` (a stdlib manifest: what it reads and provides, its models under the options, its options and the check of their values; the service code in `stage.py`, imported only for the stages in a run's plan). A run's plan orders the stages so that a stage runs after the ones providing what it reads (ties by name) and loads only the models they need; results are listed in the order `identify, embed, jpg, geotag, aesthetics, quality, scene`. Stages built since v1.7 report what they ran with in `result.engine.plugins` when a run includes them: the head a stage's output depends on (aesthetics: `v1@<head id>`, nothing with `head: off`), else `v<version>@<settings fingerprint>` (quality, scene).
- **Reducers**: `reducers = ["burst", "select"]` names model-free units that run over a whole run's results in the CLI (`bioscan cull`, `bench`) or offline, never in the service, which stays stateless. Their options live beside the stages' (`[profile.album.options.select] per_category = 20`); a /run body may not set them.

### Run summary and report

```sh
bioscan run DIR -r --json run/preds.ndjson        # or: bioscan cull ... --json
bioscan summarize run/preds.ndjson --out run      # -> run/summary.json, prints one line
bioscan report run                                # -> run/report.html (reads summary.json only; --out HTML)
```

`summarize` prints `1,424 photos · 212 with animals · 31 taxa · 9 to review` ("with animals": the gate called an animal class); the page is worth opening when "to review" is above zero. Both commands are offline and model-free (standard library, no service). Design and reasons: [research/2026-09-24-report-design.md](research/2026-09-24-report-design.md).

`summary.json` (`schema: 1`, `kind: "bioscan.summary"`):

| Key | Holds |
|---|---|
| `source` | the preds path and its sha256, the engine (version, settings, models) of the first result, first and last capture time |
| `counts` | images (results + failed), ok, failed, boxes, elapsed_ms (from `done`) |
| `categories` | the gate classes in fixed order (bird, mammal, other_animal, person, none), zeros included. An image counts under its gate class; for the animal classes, boxes, taxa and review items count by box kind (the kind check may have moved a box) |
| `taxa` | one row per named taxon, most boxes first: the name at the box's level (species, else the genus or family of the first candidate), common name (at species level the first candidate's; at genus or family "a"/"an" and the last word most of the taxon's candidates' common names share, e.g. "a vireo", "a hawk or eagle"; null when none has one), level, kind, taxonomy down to that level, list, images, boxes, the first candidate's posterior (max, median), `best` (highest posterior × sharpness), every member box with its first 3 candidates, capture span |
| `review` | the review queue, sorted by suggested name then capture time: sha256, path, jpg copy, box id (null for a whole frame), kind, level, reasons, suggested name, its common name (as in `taxa`; null when unconfirmed, no list or a whole frame), first 3 candidates |
| `rules` | the thresholds used: `single_sighting_max_posterior` 0.8, `range_eps` 0.01 |
| `errors` | every error event: path, product (null = decode), message |

Review reasons are rules, never a model: `unconfirmed` (level unconfirmed), `coarse_level` (genus or family), `out_of_range` (the first candidate's p_geo below `range_eps`, the service's `RANGE_EPS`), `no_list` (species null: a kind with no name list), `gate_no_box` (the gate says an animal but no box survived), `single_sighting` (the only box of its taxon in the run, posterior below `single_sighting_max_posterior`). Boxes from a run with species off are counted, not named or reviewed. `jpg` (the service's upright copy, when the run asked for one) and `taken_at` on members and review items are additions to the design's schema, so the page shows photos and sorts by time without reading the preds file.

`report.html` is a view of `summary.json` alone, with `bioscan cull`'s stylesheet and photo tiles (the `jpg` copy, else the photo when a browser can show it, else its format): the one line and a proportional category strip, the taxa per kind with their best frame, the review queue grouped by suggested name (English beside Latin, with the level at genus or family) with reasons in words and the candidates, counts of people and empty frames, errors, run facts. Not built yet (design §3): verdict buttons, `names.json`, `review.json` and `bioscan gt review`; thumbnails of RAW files without a `jpg` copy.

### Lightroom Classic (experimental)

One-time install, then restart Lightroom Classic once:

```bash
bioscan lr install                 # symlinks extensions/lightroom/bioscan.lrplugin into Lightroom's Modules folder (--copy to copy)
```

Every scan after that is one line. `wildlife` keeps species on; add the `aesthetics` stage to get aesthetic stars (`album` alone turns species off, so every photo would land in `待确认`):

```bash
bioscan run DIR --profile wildlife --want identify,embed,aesthetics --json --out preds.ndjson && bioscan lr open preds.ndjson
```

`lr open` writes `~/Library/Application Support/bioscan/lightroom/latest.json` (atomically; `--to FILE` elsewhere) and brings Lightroom to the front (`--no-launch` skips that); the plugin picks the file up and applies it:

- **Stars** 1-5 by quantile among the photos with an animal (about 20% per star) of `products.aesthetics.score` when the run has it, else of the best box's sharpness; photos with no animal get none. A photo that already has stars you gave it is never overwritten.
- **Keywords**, hierarchical: `bioscan|kind|name`, where name is the common name at species level, `English (Latin)` at genus or family (`a vireo (Vireo)`, `a hawk or eagle (Accipitridae)`; the bare Latin when no candidate has a common name), and left out when unconfirmed.
- **Collections** in the collection set `bioscan`: one per species, `待确认` (animal, no species) and `无动物` (no animal).

Rerunning the same file adds no duplicates. The plugin and its manual acceptance steps are in `extensions/lightroom/README.md`.

### HTTP API

```sh
curl -s 127.0.0.1:8765/health
curl -s 127.0.0.1:8765/products
curl -sN 127.0.0.1:8765/run -H 'content-type: application/json' \
  -d '{"inputs":[{"path":"/abs/a.ARW","lat":37.4,"lon":-122.1}],"want":["identify","embed","jpg"],"options":{"jpg":{"out_dir":"/tmp/jpg"}}}'
```
`want` may be left out: a request then runs its profile's stages (`full`, the default: identify). `"want": null` is a 400 unless the body names a `"profile"`, where it means the same as leaving it out.
The response is an NDJSON stream of `progress` / `result` / `error` / `done` events, defined in `bioscan/contract.py` together with the `identify` payload (gate, boxes, quality, species, candidates); `result` and `done` carry `schema: 1`. Concurrent requests take turns on the models one chunk at a time (a one-image request waits for at most one chunk); within a chunk every model stage is batched across images, and CPU decoding overlaps inference. `result.engine` holds the model versions, the name-list versions, `settings` (a fingerprint of rule thresholds, prompts and vocabularies: if it changes, results are not directly comparable) and `detail_edge`; when a run includes a stage that depends on a trained file, `engine.plugins` names it (`{"aesthetics": "v1@eva-head-v1:<sha12>+…"}`). The full contract is in section 4 of `docs/superpowers/specs/2026-09-22-bioscan-design.md`.

CLI exit codes: 0 all images succeeded, 1 some images failed, 2 service unreachable or refused, 3 incomplete stream (no `done`) or an upstream error (iNaturalist and similar). `run --json` used to always return 0 and now follows these codes too; scripts that treat non-zero as failure should take note. eval now compares scientific names with the same normalisation as the synonym lookup (ignoring hyphens and case), so Top-1/Top-5 in older reports can differ slightly.
