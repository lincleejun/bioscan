# Evaluation harness (`bioscan bench`)

`bioscan eval` scores one run. The harness turns that run into data (`report.json`), keeps
**baselines**, compares every new run with one under a **budget**, sorts what went wrong into
**failure classes** with a pointer to the code to fix, and checks a report against the standards
(`data/standards.toml`). It is standard library only, like the rest of the CLI, and reuses eval's
per-image scoring (`eval.outcome`), so eval's report.md and a report.json of the same preds agree.

| Command | Does |
|---|---|
| `bench run GT.csv [--out DIR] [--no-geo] [--preds F] [--no-synonyms] [--tier T] [--names KIND=CSV]` | `bioscan eval` (writes `preds.ndjson` and `report.md`), then `report.json` in the same folder. Exit 3 when the stream was cut short, like eval |
| `bench report PREDS GT.csv [--out FILE] [--tier T] [--no-synonyms] [--names KIND=CSV]` | Rebuilds `report.json` offline from an existing preds file (default: next to it) |
| `bench baseline REPORT --name NAME [--dir D] [--force]` | Copies a report to `baselines/NAME.json`; refuses to replace one without `--force` |
| `bench compare BASE NEW [--budget FILE] [--md OUT] [--json OUT]` | Deltas, paired images, McNemar, species changes, broken images, budget check. Exit 0 within budget, 1 over budget, 2 not comparable |
| `bench analyze REPORT [--md OUT] [--json OUT] [--examples N]` | Failure classes with counts, shares, examples and fix pointers; top confusion pairs |
| `bench scorecard REPORT [--standards FILE] [--tier T] [--md OUT]` | Each standard of the tier: bar, our value, pass/fail, gap. Exit 1 when a bar is missed, 2 when the file is invalid or the tier unknown |
| `bench geotag DIR [--scenario S] [--max-gap S] [--max-span M] [--max-still S] [--extrapolate S] [--md OUT] [--json OUT] [--gt-out DIR]` | GPX geotagging scored on scenario folders (`scripts/geotag_synth.py`), with the `geotag` tier's scorecard; `--gt-out` writes the ground truth with GPX-derived lat/lon. Exit 1 when a bar is missed ([below](#geotag-gpx-geotagging-bench-geotag)) |

`--names KIND=CSV` sets the name list used to tell whether a truth is in the list (`not_in_list`) and
for families. Default: `bird=data/names/avilist_map.csv`, and `mammal=` the MDD CSV under `data/mdd/`
when present. A kind with no list leaves `in_list` null. The CSV may be avilist_map.csv
(`scientific,family`), the AviList CSV, or MDD (`genus,specificEpithet,family`).

## Workflow

**Baseline today.** Run the set once and keep the report:

```sh
bioscan bench run data/inat/groundtruth-inat.csv --out runs/2026-09-24-golden --tier golden
bioscan bench baseline runs/2026-09-24-golden/report.json --name golden-inat-v1.5.0
git add baselines/golden-inat-v1.5.0.json && git commit -m "baseline: golden set at v1.5.0"
```

**Compare tomorrow.** After a model swap or a code change, run again and compare:

```sh
bioscan bench run data/inat/groundtruth-inat.csv --out runs/2026-09-25-golden --tier golden
bioscan bench compare baselines/golden-inat-v1.5.0.json runs/2026-09-25-golden/report.json \
  --md runs/2026-09-25-golden/compare.md
bioscan bench analyze runs/2026-09-25-golden/report.json --md runs/2026-09-25-golden/analyze.md
```

A regression past the budget exits 1. Per CLAUDE.md, a change over budget needs the owner's acceptance;
when it is accepted, the new report becomes the baseline in its own commit (`bench baseline ... --force`).

**Profiles.** `bench run` and `eval` take `--profile NAME` (see README "Profiles and bioscan.toml"). The
profile's stages must include identify, which is what the harness scores; eval still asks for `top_k` 5, and
`--no-geo` / `--identify-opt` override the profile. The preds meta line records `"profile"` and the expanded
`options`, so report.json's `meta.options` shows what ran. Without `--profile`, `BIOSCAN_PROFILE` or a
`default_profile`, the request is byte for byte the one eval sent before profiles, so existing baselines stay
comparable. Compare runs of different profiles only when you mean to: `album` switches species off. A
`meta.profile` field and per-plugin metrics come with harness step A6.

**Rescore without the service.** A preds file carries everything:
`bioscan bench report runs/x/preds.ndjson data/inat/groundtruth-inat.csv --tier golden`. Rescoring after a
synonyms.csv edit changes truth labels; `compare` warns when the synonyms sha differs.

**Tag → report.** Pushing a `v*` tag runs `models.yml`. It runs the real-model smoke, writes
`models-report.json` (tier `smoke`) and compares it with `baselines/ci-smoke.json`. It publishes the
report as the `bench-report-<tag>` artifact (characters an artifact name cannot hold, such as `/`, become `-`) and prints it in the job log between
`===== BEGIN bioscan-report <tag> =====` and `===== END … =====`. For the golden set and the own RAW set,
run on the Mac at the tag (below) and commit the reports as `golden-inat-<tag>` and `own-raw-<date>`.

**CI on every push that touches the service.** `models.yml` runs:
1. the smoke (`tests/models`), which writes `models-report.json` next to `models-report.md`;
2. `bench compare baselines/ci-smoke.json models-report.json --budget baselines/budget.toml`;
3. `bench analyze`.

Both markdowns go to the job log and the job summary. When `baselines/ci-smoke.json` does not exist
yet, the step prints the new report between `===== BEGIN bioscan-report ci-smoke candidate =====` and
`===== END … =====` and passes. Commit that JSON as `baselines/ci-smoke.json`.

### Mac commands (owner)

```sh
# service running (bioscan serve, or the launchd job)
bioscan bench run data/inat/groundtruth-inat.csv --out runs/$(date +%F)-golden --tier golden
bioscan bench run data/inat/groundtruth-inat.csv --out runs/$(date +%F)-golden-nogeo --tier golden --no-geo
bioscan bench run data/groundtruth-own.csv --out runs/$(date +%F)-own --tier own
bioscan bench scorecard runs/$(date +%F)-golden/report.json
bioscan bench scorecard runs/$(date +%F)-golden-nogeo/report.json      # held to the .nogeo standards
bioscan bench analyze runs/$(date +%F)-golden/report.json --md runs/$(date +%F)-golden/analyze.md
bioscan bench baseline runs/$(date +%F)-own/report.json --name own-raw-$(date +%F)
# GPX geotagging and its effect on species ID: see "Downstream" under geotag below
```

## report.json (schema `bioscan-report`, version 1)

```json
{"schema": "bioscan-report", "version": 1,
 "meta": {...}, "metrics": {...}, "per_species": {...}, "per_family": {...}, "images": [...]}
```

A reader refuses any other `schema` or `version`. Adding a key is not a version bump. Removing a key,
or changing what one means, is.

### meta

| Key | Meaning |
|---|---|
| `tier` | Standards tier of the run (`smoke`, `golden`, `own`, `public`, `mac`), from `--tier`; null when not given |
| `git_sha`, `git_dirty` | `git rev-parse HEAD` of the checkout that built the report (falls back to `$GITHUB_SHA`), and whether tracked files had changes |
| `date` | UTC, ISO 8601 |
| `engine` | `result.engine` of the run (version, settings, models, name lists, priors, detail_edge); a list when the run mixed engines |
| `settings_fingerprint` | `engine.settings` |
| `groundtruth`, `groundtruth_sha256` | Ground-truth CSV path and the sha256 of the file now |
| `preds_groundtruth_sha256` | The sha256 the preds meta line recorded when the run was made |
| `synonyms_sha256` | sha256 of data/names/synonyms.csv as used for scoring; null with `--no-synonyms` |
| `options` | The request options from the preds meta line, plus `synonyms` |
| `n` | Images in the ground truth |
| `preds`, `preds_sha256`, `preds_schema` | The preds file |
| `complete` | The service's `done` event is in the preds file (null for a file without a meta line) |
| `done` | `{ok, failed, elapsed_ms}` of that event |
| `name_lists` | Kinds with a name list for `in_list` |

The engine is read from the `result` events, because the preds meta line is written before the service
answers.

### metrics

Keyed by scope: `all`, `bird`, `mammal`, `other` (every ground-truth kind that is not bird or mammal).
All four keys are always present. When the ground truth has a `tier` column, there are also
`<tier>/<scope>` keys for every non-empty combination, e.g. `inat/bird`. Every scope has exactly these
keys:

| Key | Definition |
|---|---|
| `n` | Images in the scope |
| `gate_acc` | Whole-frame gate class == truth kind |
| `detect_rate` | At least one box of the truth kind |
| `top1`, `top5` | Truth is the best box's first / among its first five candidates. The best box is the highest `score` |
| `genus_acc` | The first candidate's genus == the truth's genus |
| `coverage` | Share of images whose best box is at level `species` |
| `precision` | `top1` among the images at level species |
| `confident_error_rate` | At level species and wrong, divided by `n` |
| `no_box_rate` | A result with no box, divided by `n` |
| `failed_rate` | An error or no prediction at all, divided by `n` |
| `ece` | Expected calibration error (10 equal-width bins) of `species.p_correct` (clamped to 0-1; non-finite values ignored) against top-1 correctness. Null until the service emits `p_correct`; posteriors are not treated as calibrated |
| `decode_ms_median`, `identify_ms_median` | Medians of `timing_ms.decode` / `timing_ms.identify` |
| `images_per_s` | Wall throughput, `done.ok / (done.elapsed_ms / 1000)`. Only in `all`; null elsewhere |
| `<rate>_ci` | Wilson 95% interval `[lo, hi]` for each of the ten rates above; null when its denominator is 0 |

Rates are fractions from 0 to 1, rounded to 6 places. Failed images count as misses everywhere, as in eval.

### per_species, per_family

`per_species[truth]`: `{kind, family, n, top1_hits, top1, confident_errors}`.
`per_family[family]`: `{n, top1_hits, top1, confident_errors}`. The family comes from the name list,
else from a candidate's taxonomy that names the truth. Otherwise it is `(unknown)`.

### images

One row per ground-truth row:

| Field | Meaning |
|---|---|
| `path`, `sha256`, `tier` | The image; sha256 from the result event (null for failed images) |
| `truth`, `truth_raw`, `kind`, `scope` | Truth after and before synonym normalisation, truth kind, scope |
| `failed`, `error` | No result: the error message, or `missing` |
| `gate`, `gate_ok` | Whole-frame gate class, and whether it matches the kind |
| `detected`, `has_box`, `box_kind` | A box of the truth kind; any box; the best box's kind |
| `top1`, `top5`, `level` | The best box's candidates (names) and level |
| `correct_top1`, `correct_top5`, `correct_genus` | Scoring as in the metrics |
| `p_visual`, `p_geo`, `posterior` | Of the first candidate |
| `p_correct` | `species.p_correct` when the service emits it |
| `truth_rank`, `truth_visual_rank` | Where the truth sits among the best box's candidates: rank by posterior (1 = top-1) and rank by p_visual among the listed candidates (ties share the better rank); null when it is not listed |
| `truth_p_visual`, `truth_p_geo`, `truth_posterior` | The truth candidate's values; null when it is not listed |
| `family_truth`, `family_pred` | Families of the truth and of the first candidate |
| `in_list` | Truth is in the kind's name list; null when no list is known for the kind |
| `place_known` | The ground truth has lat/lon, or the first candidate has a `p_geo` |
| `decode_ms`, `identify_ms` | Timings |

## compare

- **Paired images only.** Metrics, species changes and the budget are computed over the images the
  two reports share (paired as below), recomputed from the image rows of each side. A test set that
  gains or loses photos therefore never counts as a regression. The one exception is
  `images_per_s`, which stays the whole-run value of each report.
- **Metric deltas** (`metrics`). For every scope over the paired images, each metric gives `base`,
  `new`, `delta`, `base_ci` and `new_ci`. `metrics_whole` holds the same over each report's full image
  set, for reference; it is not budgeted.
- **Unpaired images** (`unpaired.only_new`, `unpaired.only_base`). Metrics per scope of the images in
  only one report. The markdown adds a note when either side has such images, and a "new images"
  section with their metrics.
- **Pairing.** Images pair by sha256, falling back to path, so moved folders still pair. When several images share a sha256 (duplicate photos), the copy at the same path pairs first and the remaining copies pair one-to-one in order. The comparison
  counts:
  - `fixed`: wrong before, right now;
  - `broken`: right before, wrong now;
  - `changed_same`: a different top-1 with the same correctness;
  - the images found in only one report.

  `mcnemar_p` is the exact two-sided McNemar p-value on fixed against broken. It tells you whether the
  change in top-1 is more than noise.
- **Species.** Regressions and improvements count top-1 hits per truth over the paired images.
- **Evidence.** `broken` and `fixed` list each image with its truth and its old and new answers: top-1,
  level, box kind, gate, p_visual, p_geo and posterior.
- **Warnings.** The comparison warns when any of these differ between the reports: settings
  fingerprint, engine, ground-truth sha, synonyms sha or request options.
- **Exit 2.** The reports cannot be compared when either is unreadable or has the wrong schema, when no
  image pairs, or when the budget file is invalid.

`--json` writes the same content as data: schema `bioscan-compare`, version 1.

### Budget (`baselines/budget.toml`, the default when present)

```toml
[[rule]]
metric = "top1"                      # any metric key except n
scopes = ["all", "bird", "mammal"]   # default: all, bird, mammal, other; <tier>/<scope> keys work too
max_drop_pts = 3.0                   # exactly one of: max_drop_pts, max_rise_pts (rates, percentage points),
                                     #                 max_drop_pct, max_rise_pct (relative %, any metric)
min_n = 1                            # skip scopes with fewer images in either report (optional)
note = "why"                         # optional

[species]
max_lost = 1                         # no species may lose more than this many top-1 hits

[images]
max_broken = 5                       # optional: at most this many broken images in total
```

Every rule reads the paired-image metrics (`images_per_s`: whole-run), and `max_lost` / `max_broken`
count paired images only. A metric that is null in either report is skipped. Unknown sections or keys
are an error (exit 2).
The committed budget suits the 95-image CI smoke (42 birds, 35 mammals, 18 other animals). For its reasoning, see the comments in the file.

## analyze: failure classes

Every image without a correct top-1 gets exactly one primary class. The classes are checked in this
order, and the first that matches wins:

| Class | When | Fix pointer |
|---|---|---|
| `failed` | Decode or service error | The preds error message; decode.py, run.py |
| `gate_miss` | No box, and the gate said none/person | Gate rescue threshold (rules.py), gate prompts (taxa.py) |
| `detector_miss` | No box, and the gate saw an animal | Detector vocabulary (taxa.py), a detector fallback |
| `wrong_kind` | Best box's kind (bird/mammal/other) differs from the truth's | Kind check: `rules.kind_of` / `rules.kind_evidence_logits` in `pipeline._species_many` |
| `not_in_list` | Truth not in the kind's name list (or a box with no species where no list is known) | Name list, synonyms.csv |
| `out_of_range` | Top-1 has p_geo < 0.01 where the place is known | Range veto in rules.py; prior labels, geo gaps |
| `prior_suppressed` | The truth is first by p_visual among the candidates, but ranked below top-1 because its p_geo is lower than the top-1's | Geo gaps (`bioscan names geo-gaps` → a `birdnet` row in synonyms.csv), label map, prior floor |
| `within_genus` | Right genus, wrong species | Detail copy, crop quality, prior between congeners |
| `within_family` | Right family, wrong genus | Prior floor; grade to family when the genus is unsure |
| `far_miss` | Anything else | The crop (wrong object boxed?), image quality |

`overconfident` is a flag that overlaps the primary classes: the answer is wrong and graded at level
species. These are the answers that would be written as keywords with no review.

For each class the analysis gives:
- the count;
- its share of the wrong answers and of all images;
- up to `--examples` images with their evidence, highest posterior first;
- a fix pointer.

It also counts the classes per scope and lists the top 15 truth → prediction confusion pairs.
The largest primary class (overconfident excluded; ties go to the class checked first) is printed as **Fix next**. The JSON uses schema
`bioscan-analysis`, version 1.

## scorecard: data/standards.toml

W2 owns the content; this is the schema the reader enforces.

```toml
[[standard]]
id = "accuracy.golden.bird.top1"      # <dimension>.<tier>.<scope>.<metric>[.nogeo]; unique
tier = "golden"                       # optional: default is the id's second segment
dimension = "accuracy"
title = "Birds: top-1 species correct"
scope = "bird"                        # all | bird | mammal | other
metric = "top1"                       # a metrics key above, a geotag metric (tier geotag), or "manual"
op = ">="                             # ">=" or "<="
industry = 0.95                       # optional (TOML has no null: omit the key)
community = 0.90                      # the release bar; required, a number
stretch = 0.95                        # optional
unit = "fraction"                     # rates and ece: "fraction" (0-1, as in the report); others: ms, images/s, ...
how = "bench run data/inat/groundtruth-inat.csv --tier golden; row bird"
source = "URL or why there is none"
```

- **Tier.** The scorecard applies the standards of one tier. The tier comes from `--tier`, else from
  the report's `meta.tier`, else from the ground truth's tier when it has exactly one. With none of
  these, it exits 2. A tier that no standard in the file uses also exits 2, with the known tiers listed.
- **Geo mode.** A report run with `--no-geo` is held only to the `.nogeo` standards. A normal report
  skips them.
- **Statistical rule** (docs/standards.md). A rate passes when its Wilson 95% bound clears the bar:
  the lower bound for `>=`, the upper bound for `<=`. Two cases are judged on the observed value
  instead:
  - the smoke tier, whose bars are regression guards;
  - metrics without an interval, i.e. speeds.
- **Gap.** The gap is the judged value minus the bar for `>=`, and the bar minus it for `<=`. A
  negative gap is short of the bar.
- **Missing values.** A metric that is null (`ece` without `p_correct`, an empty scope) shows as `n/a`
  and does not fail.
- **Manual standards.** `metric = "manual"` entries are listed apart, as not measurable from a report.

A minimal example is `tests/unit/fixtures/standards-example.toml`.

## geotag: GPX geotagging (`bench geotag`)

`bioscan geotag` places photos on a GPX track (README, "Geotag from a GPX track"). The owner has no GPX
to share, so `scripts/geotag_synth.py` builds tracks from the golden set's true positions and times.
`bench geotag` then scores geotagging against the truth. This is a separate subcommand with its own
report, and it does not feed `bench compare`, because compare is built around species answers: it
pairs images by sha256, counts fixed and broken top-1 answers, runs McNemar and applies a budget on
species rates. None of that means anything for a position error. The geotag report reuses the
scorecard instead, with standards on a `geotag` tier. The run is deterministic from committed data
and a seed, so a regression shows as a changed number, and the scorecard catches it.

```sh
uv run python scripts/geotag_synth.py data/inat/groundtruth-inat.csv --out runs/geotag-synth --seed 7   # ~1 min, ~750 MB
bioscan bench geotag runs/geotag-synth --md runs/geotag-synth/report.md --json runs/geotag-synth/report.json
bioscan bench scorecard runs/geotag-synth/report.json          # the same scorecard, tier geotag
```

`bench geotag DIR [--scenario NAME]... [--max-gap S] [--max-span M] [--max-still S] [--extrapolate S] [--standards F]
[--md F] [--json F] [--gt-out DIR]` exits 1 when a `geotag` standard is missed, like `scorecard`. On the seed-7 set, generating
takes about 65 s and scoring 80–85 s.

**Synthetic tracks** (the generator's docstring has the details):
- **Outings.** Photos with the same observer and local day, split at a pause of more than 4 h or a speed above 40 m/s.
  The golden set gives 1,624 photos in 1,061 outings.
- **True path.** A walk-in, a 5–60 s stop at each photo, wandering legs between photos (a 1.2 m/s walker; faster legs
  drive), and a walk-out.
- **Device.** Samples every 1, 2, 5 or 10 s, with AR(1) GPS noise: σ 3–10 m per axis, 30 s correlation time.
- **Times.** 721 golden times have minute precision (seconds `:00`) and 15 are date-only. Minute-precision times get
  seconds drawn within their minute; date-only times get a time between 07:00 and 17:00. The drawn time is the truth
  the track is built around, and `truth.csv` records the precision.
- **Seeding.** Every draw is seeded by (seed, scenario, outing).

| Scenario | What changes |
|---|---|
| `perfect` | camera clock right; half the outings write OffsetTimeOriginal, the rest rely on `--tz` |
| `offset37` | camera 37 s fast; 3 reference photos with GPS per outing (5 m noise) |
| `dst` | camera 1 h fast; 3 reference photos per outing |
| `wrongtz` | camera on home time 3 h ahead, no OffsetTimeOriginal, `--tz` of the trip; one clock photo per outing |
| `gaps` | 2–4 dropouts of 30 s–20 min per track, auto-pause at half the stops |
| `outside` | track starts up to 20 min late and ends up to 20 min early |
| `multi` | track split into 2–3 files (one GPX 1.0, one with two segments) with restart gaps, plus a decoy track a day earlier |

**Scenario folder** (any set built this way can be scored, e.g. your own photos with GPS, stripped). Each
`DIR/<scenario>/` holds three things:
- `photos.csv`: the inputs: `group, path, role, taken_at, tz, lat, lon, clock`.
  - `role` is `photo`, `ref` (lat/lon filled, used for the offset) or `clock` (`clock` holds the time shown).
  - `taken_at` is the camera clock, as decode writes it.
  - `tz` is the group's `--tz`.
- `truth.csv`: `group, path, lat, lon, utc, expect_fix, offset_s, precision`. `expect_fix` is 1 when the true time is
  inside the track; `offset_s` is the true clock offset.
- `gpx/<group>/*.gpx`.

A group is one outing: one `geotag` call.

**Report** (schema `bioscan-geotag-report`, version 1): `meta` (tier `geotag`, git sha, date, the synth's
`scenarios.json`, fix-rule options), `metrics` per scope (`all` pools every scenario; then one scope per scenario),
`groups` (offset method, estimated and true offset, residual, warnings) and `images` (fix, truth, error, estimated
error, cell change).

| Metric | Definition |
|---|---|
| `n`, `n_expected` | photos; photos whose true time is inside the track |
| `median_error_m`, `p90_error_m` | position error over the expected photos with a fix (p90: nearest rank) |
| `within_100m_rate`, `within_1km_rate` | expected photos with a fix within 100 m / 1 km (no fix counts as a miss) |
| `no_fix_rate` | expected photos without a fix |
| `false_fix_rate` | photos outside the track that got a fix anyway |
| `cell_change_rate` | fixes whose 2-decimal lat/lon (the location prior's cache key, ~1 km) differs from the truth's |
| `err_est_coverage` | fixes whose true error is within geotag's own `err_m` |
| `offset_error_s`, `offset_error_p90_s`, `offset_groups`, `offset_failed` | median and p90 of \|applied − true offset\| over the groups where an estimate was due (reference or clock photos, or a clock that is off; not `--offset`); how many; how many of them got no estimate (method none: 0 applied, so the whole true offset counts) |

Rates carry Wilson intervals (`<rate>_ci`), and the scorecard judges them on the bound as usual.

### Downstream: does a GPX position help species ID? (Mac)

Geotagging does not change a photo's capture time, so BirdNET's week (read from `taken_at`) is unchanged by
construction. Only the place can move, and `cell_change_rate` says how often it leaves the truth's 0.01° cell:
1.0% in `perfect` (docs/2026-09-24-geotag-synthetic.md). `--gt-out` writes the golden CSV once per scenario with
lat/lon replaced by the geotag fix (blank where there is none), so `bench run` can measure identification on
GPX-derived positions:

```sh
uv run python scripts/geotag_synth.py data/inat/groundtruth-inat.csv --out runs/geotag-synth --seed 7
bioscan bench geotag runs/geotag-synth --gt-out runs/geotag-synth/gt --md runs/geotag-synth/report.md
# service running (bioscan serve, or the launchd job)
D=$(date +%F)
bioscan bench run data/inat/groundtruth-inat.csv --out runs/$D-golden --tier golden                  # true GPS
bioscan bench run data/inat/groundtruth-inat.csv --out runs/$D-golden-nogeo --tier golden --no-geo   # no location
bioscan bench run runs/geotag-synth/gt/perfect.csv --out runs/$D-golden-gpx --tier golden            # GPX positions
bioscan bench run runs/geotag-synth/gt/gaps.csv --out runs/$D-golden-gpx-gaps --tier golden          # dropouts: some none
bioscan bench compare runs/$D-golden-nogeo/report.json runs/$D-golden-gpx/report.json --md runs/$D-golden-gpx/vs-nogeo.md
bioscan bench compare runs/$D-golden/report.json runs/$D-golden-gpx/report.json --md runs/$D-golden-gpx/vs-truth.md
```

`vs-nogeo` is what a GPX track buys a folder without GPS. `vs-truth` should show almost no change, because 99% of
the fixes share the truth's prior cell. Compare warns that the ground-truth sha and the options differ, which is
expected. Until these runs exist, the effect of GPX positions on species ID is **unverified**.
