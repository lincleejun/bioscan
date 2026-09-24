# bioscan

**English** | [简体中文](README.zh-CN.md)

**What animal is in the photo, where, and which species.** A local identification service for wildlife photography: give it a batch of RAW or JPG files and get back, per image, animal boxes, species (birds and mammals against curated lists, every other animal against the TreeOfLife all-taxa list), confidence and a grade. A resident HTTP service plus a thin CLI; runs on MPS on a Mac; no photo ever leaves the machine.

## Results

Measured on the owner's M-series Mac (MPS) on 2026-09-24: v1.4 (`91b6bd6`) against v1.5 (`875dc7a`) on the same photos, paired by sha256 with `bioscan bench compare`. ▲ better, ▼ worse. Reports: `baselines/golden-inat-v1.4.json`, `baselines/golden-inat-v1.5.json`, `baselines/own-raw-2026-09-24-v1.{4,5}.json`; comparison, failure analysis and scorecard in `docs/2026-09-24-*.md`.

### iNaturalist golden set (California, 65 species × 25 = 1,625 research-grade observations, real GPS and dates)

| Version | Group | Top-1 | Top-5 | Detected | Coverage | Precision | Confident errors | Identify (median) |
|---|---|---|---|---|---|---|---|---|
| v1.4 | Birds (1050) | 89.8% | 95.0% | 97.0% | 95.6% | 93.4% | 6.3% | 190 ms |
| v1.5 | Birds (1050) | 91.2% ▲ | 95.7% ▲ | 97.9% ▲ | 92.5% ▼ −3.1 | 97.1% ▲ | 2.7% ▲ | 202 ms ▼ |
| v1.4 | Mammals (575) | 75.0% | 82.4% | 86.4% | 83.8% | 88.6% | 9.6% | 207 ms |
| v1.5 | Mammals (575) | 82.8% ▲ | 86.3% ▲ | 89.6% ▲ | 81.4% ▼ −2.4 | 95.1% ▲ | 4.0% ▲ | 621 ms ▼ ×3 |
| v1.4 | All (1625) | 84.6% | 90.5% | 93.3% | 91.4% | 91.9% | 7.4% | 4.68 img/s |
| v1.5 | All (1625) | 88.2% ▲ | 92.4% ▲ | 95.0% ▲ | 88.6% ▼ −2.9 | 96.5% ▲ | 3.1% ▲ | 2.20 img/s ▼ −53% |

- Paired images: 62 fixed, 2 broken, McNemar exact p ≈ 0. Largest species gain: *Cervus canadensis* 0/25 → 19/25 (mammal location prior). Only species regression: *Ardea herodias* 25/25 → 24/25.
- The two regressions are deliberate trade-offs. Coverage drops because the range veto and stricter grading push some images that were "species-level but wrong" down to genus or unconfirmed; precision and the confident-error rate improve accordingly. Throughput halves because v1.5 also scores every box against the all-taxa list (366,460 species); `bench compare` flags this as over the 50 % speed budget.
- Coverage = share of images graded to species; precision = Top-1 accuracy among those; confident errors = graded species-level and wrong. Read coverage and precision together.
- Scorecard against the community bars (`data/standards.toml`, judged on the Wilson 95 % bound): 1 pass, 21 fail, 8 n/a. Birds Top-1 91.2 % clears the 90 % bar on the point value but not on the bound (89.4 %); mammals fall short on detection (89.6 % vs 95 %) and Top-5 (86.3 % vs 93 %).
- Failure classes (`bench analyze`, 191 wrong of 1,625): detector miss 50, top-1 out of range 50, overconfident 51, far miss 31, wrong kind 29, within genus 18, within family 7, gate miss 5.
- Ground truth is iNaturalist community-verified (CC0 / CC BY / CC BY-NC), used for evaluation only; images are not distributed with the repo. `data/inat/groundtruth-inat.csv` keeps each observation's link and attribution.

### Own photos (telephoto RAW, 404 images, 3 species, no GPS in EXIF)

| Version | Top-1 | Top-5 | Detected | Coverage | Precision | Decode (median) | Identify (median) |
|---|---|---|---|---|---|---|---|
| v1.4 | 79.0% | 98.0% | 100% | 84.7% | 85.4% | 716 ms | 194 ms |
| v1.5 | 79.0% | 98.0% | 100% | 85.1% ▲ | 84.9% ▼ −0.5 | 715 ms | 199 ms |

No image flipped between the versions: these files carry no coordinates, so the location prior is off, and every v1.5 accuracy change runs through it. With one coordinate given for the whole batch (`--lat/--lon`) an earlier run of this set reached Top-1 96.3 %: Western, Eastern and Whiskered Screech-Owls are told apart by range, not by looks.

### RAW metadata (checked on this machine)

ARW 29, DNG 2, RAF 263 and JPG 274 files: capture time read from every one; none carries GPS (the cameras have no receiver). CR3, ORF, RW2 and NEF: no sample files on this machine, so they are covered by the unit tests on real headers only.

Cold start loads the three models in about 11 s; they stay resident afterwards.

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
- **AviList 2025** (birds, 11131 species) and **MDD v2.5** (mammals, 6904 species) are the naming standards for birds and mammals; BirdNET, TreeOfLife/BioCLIP and iNaturalist names are mapped onto them through the tables in `data/names/`.
- All taxa by default: every other animal (reptiles, amphibians, fish, insects, spiders, …) is named from the TreeOfLife-200M all-taxa list. Naming candidate taxa first is optional (see [All taxa and candidates](#all-taxa-and-candidates)).
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
                 bird, mammal and all-taxa lists (each list's best five names) ─▶ cosine against that kind's
                 name-list text vectors (other animals: the all-taxa list) × (0.02 + BirdNET location prior)
                 ─▶ normalise ─▶ top-k ─▶ range veto ─▶ grade
```

Grading: species when top-1 ≥ 0.5 and leads the runner-up by ≥ 0.3; otherwise genus when the top-5 summed by genus reaches ≥ 0.6, family when summed by family reaches ≥ 0.6; otherwise `unconfirmed`. The range veto and the kind check (next section) can lower that grade.

### Accuracy rules (v1.5)

Three fixes for confident species-level mistakes. Each is an `identify` option, on by default, so a run can switch one off and measure it:

| Option | What it does | Constants |
|---|---|---|
| `range_veto` | **Range veto.** Where the place is known and the list has a location prior, a top candidate whose own p_geo is below ε cannot be graded species. If a congener among the returned candidates has p_geo ≥ τ, it is listed first (the only case where `top` is not in posterior order); the grade then comes from the genus/family roll-up. Fixes a Raven named as a Philippine crow in California. A p_geo borrowed from the genus (below) never vetoes. | `rules.RANGE_EPS` ε = 0.01, `rules.RANGE_TAU` τ = 0.05 |
| `kind_check` | **Kind check.** Each box's BioCLIP features (already computed) are also scored against every loaded kind-check list, each with its own matmul (no stacked matrix), and the box takes the kind whose 5 best names hold most of that visual evidence (the same number of names per list, so a longer list does not win by size). The lists are birds, mammals and, when it is loaded, the all-taxa list for other animals. Taking the same five names from each list removes most of the size effect between AviList (11k) and MDD (7k), but not between them and the ~366k-row all-taxa list, whose best five names score higher by chance alone; so the all-taxa list competes only for `other_animal` boxes. A box can move bird ↔ mammal, and an `other_animal` box can move to bird or mammal, against the gate and crop check (an owl gated mammal is no longer named as a skunk); a bird or mammal box never moves to other animal. A box that moved on less than 0.75 of the evidence is graded `unconfirmed`: any name above that would assert a kind the evidence cannot. The visual evidence decides, not the posterior, because the lists differ in prior coverage. Without the all-taxa list, `other_animal` boxes are left alone. Cost of the all-taxa list here: for `other_animal` boxes only, one extra matmul per box against its ~366k rows (about 30 ms a box on a 4-core CPU; on MPS an estimated 1–2 ms). | `rules.KIND_TOP` = 5, `rules.KIND_SURE` = 0.75; lists in `taxa.KIND_CHECK` |
| `mammal_geo` | **Mammal location prior.** The BirdNET geo model the service already loads also scores 1,048 mammals; `data/names/mdd_map.csv` gives them to MDD rows. An MDD row with no label gets the highest p_geo among labelled species of its genus (**genus back-off**), or 0.05 when its genus has none. Birds keep their rule: unlabelled rows get 0. | `geo.UNLABELLED_NEUTRAL` = 0.05; per-list policy `names.LISTS[...].unlabelled` |

All thresholds, the unlabelled policies, the label maps' contents and the option defaults are in the settings fingerprint; `result.engine.models.label_maps` names each label map with its sha. With all three options off, identify output is the same as v1.4 for birds and mammals (checked byte for byte on a 300-frame stand-in recording, 5 option sets). The whole output is the same only without the all-taxa list: with it, `other_animal` boxes get a species where v1.4 gave `null`. To measure one fix, run the same ground truth twice and compare the reports:
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
| All-taxa list (other animals) | species-level Animalia rows of `imageomics/TreeOfLife-200M`, birds and mammals excluded, official vectors | CC0 |

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

The name-list CSVs are large and not in git: download them as described in `data/README.md` into `data/avilist/` and `data/mdd/`. The first start encodes the lists as BioCLIP text vectors and caches them in `~/.cache/bioscan/names/` (this needs the 3.26 GB official TreeOfLife-200M vector file; about half a minute). The same first start also builds the all-taxa list from that file (no encoding; a float16 cache of about 1 GB). Delete the TreeOfLife file only after both are cached: if it is gone and the all-taxa cache is missing, the service still starts, logs a warning and gives other animals `species: null`, as before; `uv run python tests/models/download.py` or `uv run bioscan names stats` builds it again. Later starts take a few seconds (the all-taxa list is most of that). Editing `data/names/synonyms.csv` or `avilist_map.csv` rebuilds the bird cache once. `mdd_map.csv` carries labels only and does not touch the mammal cache.

```sh
uv run bioscan names stats                # name-list coverage
```

## Usage

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
bioscan run DIR --lat 37.4 --lon -122.1                # batch default coordinate for images without EXIF GPS (the location prior matters)
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

By default every box is named against all taxa bioscan has: birds against AviList, mammals against MDD, and every other animal (`other_animal` boxes) against the **all-taxa list** `tol200m-animalia`, which holds every species-level TreeOfLife-200M row of the animal kingdom outside Aves and Mammalia (366,460 species, measured in CI 2026-09; each row has its official BioCLIP vector). **Known gap:** the TreeOfLife-200M BioCLIP 2.5 embedding file has no rows at all for many reptiles and ray-finned fish (none of the smoke set's 6 reptiles and 1 fish, not even their genera), so those animals get a wrong name, sometimes at species level; a self-encoded reptile/fish list is the next step (TASKS.md). There is nothing to choose first. The output shape does not change: `species.list` names the list used, and `engine.models.taxonomy` says which taxonomy each list follows. Other animals have no location prior yet (`p_geo` is null).

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
| `album` | identify, embed | identify `species: false` | SigLIP2, OWLv2 (BioCLIP never loads) | `quality`, `scene`, `aesthetics` stages; `burst` and `select` reducers, run by the CLI |

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
- **Stages and plugins**: each stage is a plugin in `bioscan/plugins/<name>/` (a stdlib manifest: what it reads and provides, its models under the options, its options and the check of their values; the service code in `stage.py`, imported only for the stages in a run's plan). A run's plan orders the stages so that a stage runs after the ones providing what it reads (ties by name) and loads only the models they need; results are listed in the order `identify, embed, jpg, geotag`.

### Geotag from a GPX track

Most camera bodies write no GPS. If you record the outing with a watch or phone and export a GPX track, bioscan places each photo on the track at its capture time. This does the same job as Lightroom's "auto-tag photos" map module. The location matters: on the golden set, bird top-1 is 83.3% without coordinates and 89.8% with them (README results; the two numbers are disputed in docs/standards.md §4, and the GPX effect itself is unverified until the Mac run in docs/harness.md).
```sh
bioscan geotag DIR --gpx hike.gpx --tz America/Los_Angeles --csv geo.csv   # path,lat,lon,source,dt_s,err_m,utc,ele
bioscan geotag DIR --gpx a.gpx --gpx b.gpx --offset +00:01:23 --xmp       # camera 83 s fast; write <stem>.xmp sidecars
bioscan geotag DIR --gpx hike.gpx --clock DIR/DSC0001.ARW=2026-05-01T08:00:13   # a photo of the watch showing 08:00:13
bioscan run DIR --gpx hike.gpx --tz=-07:00             # per-image coordinates for identify (EXIF GPS still wins; no exiftool needed)
```
- **Sources.** Each photo gets one of `exif`, `gpx` or `none`:
  - `exif`: the file already has GPS. EXIF always wins.
  - `gpx`: the track placed the photo.
  - `none`: the photo is outside the track or has no capture time.

  The CSV also gives `dt_s` (seconds to the nearest track point), `err_m` (an estimate of the error; about 3 fixes in 4 fall within it on the synthetic set) and the corrected UTC time.
- **Time.** GPX times are UTC; camera times are local wall time. A file's OffsetTimeOriginal is used when present. Otherwise the time is read in `--tz`: a fixed offset, or a zone name, which applies the right DST for each date. The default is this computer's zone. Write negative values with `=`, e.g. `--tz=-07:00` and `--offset=-3600`, or argparse takes them for options.
- **Clock offset** (camera time minus true time). It comes from the first of these that applies:
  1. `--offset`;
  2. a photo of a clock: `--clock PHOTO=TIME`, the time the clock shows, read in the photo's zone;
  3. an estimate from photos in the folder that already have GPS (a phone photo, or a camera with a GPS link). The estimate finds the offset at which those photos sit on the track. It is rejected when they sit more than 100 m off. When several offsets fit equally well, one under 5 min wins (plain drift), then whole hours, half hours and quarter hours (timezone and DST mistakes), and a warning says the fit was ambiguous. The estimate is skipped when every photo already has GPS;
  4. otherwise 0.

  The offset is applied once per run, so run one camera at a time. When most photos fall outside the track, a warning says how far off they are; a whole number of hours means a timezone mistake.
- **Fix rule.** The position is linear in time between neighbouring track points up to `--max-gap` seconds apart (default 1800). Across a longer gap, it is linear only when the gap's ends are within `--max-span` metres of each other (default 200: the watch auto-paused while you stood still) and at most `--max-still` seconds apart (default 3 h: a wait at a hide, not a night at base camp). Outside the track there is no fix, unless you allow `--extrapolate N`: then the first or last point is held for N seconds.
- **XMP.** `--xmp` writes `<stem>.xmp` holding `exif:GPSLatitude`/`GPSLongitude` in XMP. This is the sidecar Lightroom, Capture One and Bridge read for RAW files; Lightroom ignores sidecars of JPEGs. A photo that already has a sidecar (`<stem>.xmp`, or darktable's `<name>.<ext>.xmp`) is left alone: bioscan never edits or merges an existing sidecar, and never writes into the photo file. Use the CSV with exiftool if you need to change existing files.
- **Several tracks.** Several `--gpx` files and segments merge into one time-ordered track. A second device recording at the same time just adds points.
- **Where `run --gpx` places the photos.** With a profile that runs the `geotag` stage (`--profile wildlife`), the CLI sends `options.geotag` (the track paths, `--tz` as `camera_utc_offset`, the limits you changed, and the clock offset, which it decides once for the whole folder from `--offset`, `--clock` or the photos with GPS); the service reads the track, places each photo without a request or EXIF position, and reports `products.geotag` (`place_source` request / exif / gpx / none, and the fix). The track must be under the service's allow-roots. Without such a profile (no profile, `full`, `album`), `run --gpx` reads the track here and sends per-image coordinates, exactly as before, and `bioscan geotag` always works locally. Both paths give identify the same coordinates. The stage takes the same options from a /run body or a `bioscan.toml` (`[profile.wildlife.options.geotag] gpx = [...]`); it never estimates a clock offset itself, because it sees one chunk at a time (empty `offset` = 0).
- **Accuracy.** Measured on synthetic tracks built from the golden set (`bioscan bench geotag`, docs/harness.md):

  | Measure | Pooled result |
  |---|---|
  | Median error | 7.2 m |
  | p90 error | 17 m |
  | Within 100 m | 97.2% |
  | No fix | 0.1% |
  | False fix | 0% |
  | Clock-offset error | 1 s (median) |

  The per-scenario table is in docs/2026-09-24-geotag-synthetic.md.

### HTTP API

```sh
curl -s 127.0.0.1:8765/health
curl -s 127.0.0.1:8765/products
curl -sN 127.0.0.1:8765/run -H 'content-type: application/json' \
  -d '{"inputs":[{"path":"/abs/a.ARW","lat":37.4,"lon":-122.1}],"want":["identify","embed","jpg"],"options":{"jpg":{"out_dir":"/tmp/jpg"}}}'
```
`want` may be left out: a request then runs its profile's stages (`full`, the default: identify). `"want": null` is a 400 unless the body names a `"profile"`, where it means the same as leaving it out.
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
bioscan bench geotag runs/geotag-synth                                                  # GPX geotagging on synthetic tracks
```
- **report.json** (`bioscan-report` v1) holds:
  - the run's git sha, engine, settings fingerprint and ground-truth sha;
  - every metric per scope (`all`, `bird`, `mammal`, `other`, and per tier) with Wilson 95% intervals;
  - per-species and per-family tables;
  - one row per image.
- **compare** pairs images by sha256 and counts fixed and broken images, with an exact McNemar p-value. Metrics, species changes and the budget use the paired images only, so a test set that gains photos never counts as a regression; new images are listed separately. It lists species regressions and broken images with their evidence, and checks `baselines/budget.toml`. It exits 0 within budget, 1 over budget and 2 when the reports can't be compared.
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
- Other animals have no location prior.
- Other animals use TreeOfLife's own names and taxonomy; `data/names/synonyms.csv` only maps AviList/MDD names, so an iNaturalist truth label spelled differently from TreeOfLife (a genus move, say) scores as a miss. Their accuracy is measured on 18 photos in CI only (see Tests).
- The mammal location prior, the range veto and the kind check are unverified on real photos until a `bioscan eval` on the Mac (CI's on/off table covers 95 photos only). ε, τ, the kind-check margin and the neutral constant are first guesses. With the all-taxa list loaded, the kind check also weighs it against birds and mammals; its effect on bird and mammal accuracy is measured only by CI's on/off table.
- The range veto can rename a genuine vagrant to a local congener (graded genus, never species).
- Recently split species (Northern / Hen Harrier, American / Western Barn Owl) carry old names in the training data and are separated by a shared vector plus the location prior; synonyms do not yet apply per region.
- The 0.02 floor in the prior formula limits how far location can override vision; it has not been tuned on the golden set.
- When the subject is tiny in the frame (distant raptors), the detector can box the wrong object.
- Only run on macOS + MPS and on CPU; no CUDA setup or Dockerfile.

## Tests

```sh
uv run ruff check .
uv run pytest                                     # no models, seconds; tests/models skipped by default
uv run python tests/models/download.py && BIOSCAN_MODEL_TESTS=1 uv run pytest tests/models   # real-model smoke, 95 iNat photos
uv run python tests/smoke/run_smoke.py --url ...  # needs a running service and your own tests/smoke/*.ARW
```

CI (`.github/workflows/`): `ci.yml` runs ruff + pytest on every push; `models.yml` runs the real-model smoke on CPU on every push / PR that touches the service, the model tests, the name data, eval/bench, baselines or dependencies (weights, photos and the all-taxa list cached; the first run after a cache miss downloads the 3.26 GB TreeOfLife vectors to build it), and on every `v*` tag. It writes the metrics to the job summary per kind (birds, mammals and the 18 other animals, which are ranked against the real all-taxa list), and compares the run's report.json with `baselines/ci-smoke.json` under `baselines/budget.toml`: a regression over budget fails the job.

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
bioscan/plugins/<name>/          built-in stages identify, embed, jpg, geotag: stdlib manifest in __init__.py, service code in stage.py
bioscan/service/stages.py        the stages in the service: options merged and checked, /products, paths for allow-roots
bioscan/service/pipeline.py      identify orchestration (batched across images) behind the Models protocol
bioscan/service/rules.py         pure rules and thresholds: crop check, grading, quality, cropping
bioscan/service/taxa.py          gate prompts, detector vocabularies, promotable class
bioscan/service/settings.py      fingerprint of output-changing settings
bioscan/service/decode.py        RAW/JPG → upright 2048 image + detail copy + EXIF (every RAW container) + sha256
bioscan/service/names.py         AviList / MDD lists, the TreeOfLife all-taxa list, TreeOfLife mapping, text-vector cache
bioscan/service/candidates.py    the candidates option: taxon index, the rows each list keeps
bioscan/service/adapters/        siglip2 owlv2 bioclip geo
bioscan/geotag.py                GPX parsing, capture time -> UTC, clock offset, track interpolation, XMP sidecars (stdlib only)
bioscan/cli/                     main client render gt eval bench (harness: report.json, compare, analyze, scorecard)
                                 config (profiles) geotag_cli (bioscan geotag, run --gpx) geobench (bench geotag)
scripts/geotag_synth.py          synthetic GPX scenarios from the golden set, for bench geotag
baselines/                       committed reports compared against, and the regression budget
data/names/                      name mapping tables keyed on AviList
docs/                            design spec, implementation plan, evaluation results
```

## License

Code is MIT (see `LICENSE`). Models and data carry their own licenses, listed above; the BirdNET prior is CC BY-NC-SA 4.0, so evaluate commercial use yourself.
