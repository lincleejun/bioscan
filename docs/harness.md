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
| `aesthetic eval RATINGS --out DIR [--head H] [--personal P] [--blend B] [--k K] [--curve SIZES]` | Agreement of the aesthetic score with the owner's stars and picks, per trip, plus the learning curve; report.json read by `scorecard` (tier `aesthetic-own`) ([below](#aesthetic-agreement-with-the-owner-bioscan-aesthetic-eval)) |
| `bench aesthetic init\|score\|compare\|table` | Any aesthetic scorer (a scores file) against the frozen aesthetic golden set: shot-group winners, pairwise accuracy, keepers lost when culling, planted checks, slice residuals; paired compare under baselines/budget-aesthetic.toml; report.json read by `scorecard` (tier `aesthetic-golden`); `table` is the arena: N scorers ranked with their deviation from the labels ([below](#aesthetic-golden-set-bench-aesthetic)) |
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

**Profiles.** `bench run` and `eval` take `--profile NAME` (see usage.md "Profiles and bioscan.toml"). The
profile's stages must include identify, which is what the harness scores; eval still asks for `top_k` 5, and
`--no-geo` / `--identify-opt` override the profile. The preds meta line records `"profile"` and the expanded
`options`, so report.json's `meta.options` shows what ran. Without `--profile`, `BIOSCAN_PROFILE` or a
`default_profile`, the request is byte for byte the one eval sent before profiles, so existing baselines stay
comparable. Compare runs of different profiles only when you mean to: `album` switches species off, and
`compare` warns when `meta.profile` differs. `meta.profile` records the profile, and each plugin's own
metrics (`quality`, `burst`, `scene`, ...) go to [`plugin_metrics`](#plugin_metrics-and-plugin_images). A
profile with reducers (album: burst, select) also records them in the preds meta line, and the report runs
them over the results before scoring, as `bioscan cull` does.

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
 "meta": {...}, "metrics": {...}, "per_species": {...}, "per_family": {...}, "images": [...],
 "plugin_metrics": {...}, "plugin_images": [...]}
```

A reader refuses any other `schema` or `version`. Adding a key is not a version bump. Removing a key,
or changing what one means, is. `plugin_metrics`, `plugin_images`, `meta.profile` and `meta.reducers` were
added in v1.7 (A6) without a bump: `bench report` on an existing preds file gives the same `metrics`,
`per_species`, `per_family` and `images` as before (the A0 golden `tests/contract/golden/bench-report.json`
holds them to that), and an older report without the new keys still loads, compares and scores.

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
| `profile` | The profile the run expanded (`--profile`, from the preds meta line); null for a run without one, which the scorecard treats as `wildlife` |
| `reducers` | `{reducer: options}` run over the results before scoring (from the preds meta line; album: burst, select); null when none ran |

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

### plugin_metrics and plugin_images

A plugin (stage or reducer) declares its harness metrics in its manifest, `Manifest.metrics`: a tuple of
`plugin.Metric(name, kind, row, lower_is_better)`. `row` is a standard-library function
(`"package.module:function"`) that maps one ground-truth row and that image's result event (with the reducers'
products added; an error event, or None when missing) to `{scope: value}`. An image adds nothing to a scope
where the value is None, so a plugin that did not run, or a ground truth without its columns, leaves no trace.

`plugin_images` keeps the values per image: `[{path, sha256, tier, values: {plugin: {metric: {scope: value}}}}]`,
only for images with a value. `plugin_metrics[plugin][scope]` aggregates them; scope `all` comes first, then the
others in name order. Each scope has `n` (images with any value of that plugin there) and, per metric:

| kind | `<metric>` | `<metric>_ci` | `<metric>_n` |
|---|---|---|---|
| `rate` | share of True among the values | Wilson 95% interval | values counted (the denominator) |
| `median` | median of the numbers | – | values counted |
| `pair_precision`, `pair_recall` | each value is `[truth group, predicted group]`; over every pair of images, pairs in both groups ÷ pairs in the predicted (precision) or the truth (recall) group | Wilson 95% over the pairs | predicted or truth pairs |
| `pair_f1` | harmonic mean of the two | – | images |

Rates are fractions 0–1 rounded to 6 places, like `metrics`. The built-in metrics (album tier) are defined in
[Album tier](#album-tier-culling-bench---profile-album).

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
- **Plugin metrics** (`plugin_metrics`). The same deltas for every plugin metric, `{plugin: {scope: {metric:
  {base, new, delta, base_ci, new_ci}}}}` plus `n`, recomputed over the `plugin_images` entries the two
  reports share (paired as below). Plugins and scopes on one side only are left out.
- **Warnings.** The comparison warns when any of these differ between the reports: settings
  fingerprint, engine (which includes `engine.plugins`, each new stage's version and settings
  fingerprint), ground-truth sha, synonyms sha, request options, profile or reducer options.
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

[[rule]]
plugin = "quality"                   # a plugin rule: metric is one of that plugin's Manifest.metrics
metric = "keepers_lost"
scopes = ["all"]                     # default for a plugin rule: all; any plugin_metrics scope works
max_rise_pts = 2.0                   # *_pts for its rates (rate, pair_*), *_pct for anything
```

Every rule reads the paired-image metrics (`images_per_s`: whole-run), and `max_lost` / `max_broken`
count paired images only. A plugin rule reads the paired plugin metrics. A metric that is null in either
report, or a scope one of them lacks, is skipped. Unknown sections, keys, plugins or plugin metrics are an
error (exit 2).
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
profile = "wildlife"                  # optional: default wildlife; the report's meta.profile must match
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
- **Profile.** A standard has a `profile` (default `wildlife`) and applies only to a report of that profile
  (`meta.profile`). A report without a profile, or with `full`, counts as `wildlife`: both score identify
  with the contract's defaults, which is what every standard before v1.7 was written for.
- **Plugin standards.** With `plugin = "<name>"`, `metric` is one of that plugin's metrics and `scope` one of
  its `plugin_metrics` scopes (any string, e.g. a reject reason); the value and interval come from
  `plugin_metrics[plugin][scope]`. Rates (`rate`, `pair_*` kinds) take unit `"fraction"`.
- **Geo mode.** A report run with `--no-geo` is held only to the `.nogeo` standards. A normal report
  skips them.
- **Statistical rule** (docs/standards.md). A rate passes when its Wilson 95% bound clears the bar:
  the lower bound for `>=`, the upper bound for `<=`. Two cases are judged on the observed value
  instead:
  - the smoke and album tiers, whose bars are regression guards;
  - metrics without an interval, i.e. speeds, medians and `pair_f1`.
- **Gap.** The gap is the judged value minus the bar for `>=`, and the bar minus it for `<=`. A
  negative gap is short of the bar.
- **Missing values.** A metric that is null (`ece` without `p_correct`, an empty scope) shows as `n/a`
  and does not fail.
- **Manual standards.** `metric = "manual"` entries are listed apart, as not measurable from a report.

A minimal example is `tests/unit/fixtures/standards-example.toml`.

## Album tier: culling (`bench ... --profile album`)

The album profile (identify with species off, embed, aesthetics, quality, scene; reducers burst and select) is scored
with the same report.json: its species `metrics` mean nothing there, its [`plugin_metrics`](#plugin_metrics-and-plugin_images)
are the measure. The ground truth is a CSV with `path`, `tier`, `keep` (1/0), `reject_reasons` (`;`-joined, blank
= none), `burst_id` (blank = in no burst) and `scene` (blank = unlabelled); a column it lacks measures nothing.

**Synthetic reject set.** No labelled album exists yet, so `scripts/cull_synth.py` makes one from photos whose
subject box is known (a preds file's best identify box, or a CSV): per source the original (keep 1), Gaussian blur
and motion smear on the subject box (`soft_subject`), the same smear over the whole frame (`motion_or_defocus`), a
crop cutting 40% of the box off (`subject_cut`), +2 and −2 EV in linear light (`overexposed`, `underexposed`), the
photo shrunk onto a canvas so the subject covers 0.3% of it (`subject_too_small`), and for every third source a
burst of 4 frames shifted by up to 2%, 0.2 s apart (keep 1, one `burst_id`). Every other photo is 10 minutes from the
next. The script is deterministic from its seed (docstring: every variant and parameter).

```sh
bioscan run photos/ --json > runs/src.ndjson                        # any run with identify boxes
uv run python scripts/cull_synth.py --preds runs/src.ndjson --out runs/album-synth --seed 7
bioscan bench run runs/album-synth/groundtruth-album.csv --profile album --tier album --out runs/album
bioscan bench scorecard runs/album/report.json                       # tier album, profile album
```

CI does the same in `tests/models` on 24 smoke photos (scene truth `wildlife`), writes `models-report-album.json`
and `models.yml` compares it with `baselines/ci-album.json` under `baselines/budget-album.toml` (no baseline yet:
it prints the candidate between `===== BEGIN bioscan-report ci-album candidate =====` markers).

| Plugin | Metric (kind) | Scopes | Definition |
|---|---|---|---|
| quality | `reject_precision` (rate) | `all`, `soft`, each reason | Of the images rejected for the scope (any reason; soft_subject or motion_or_defocus; that reason), the share whose truth has it |
| quality | `reject_recall` (rate) | `all`, `soft`, each reason | Of the images whose truth has the scope's reason(s), the share rejected for it; a failed image counts as not rejected |
| quality | `keepers_lost` (rate, lower is better) | `all` | Of the keep-labelled images, the share a rule rejected: the budget metric, since losing a keeper costs more than reviewing a reject |
| burst | `burst_pair_precision`, `burst_pair_recall`, `burst_pair_f1` (pairs) | `all` | Over every pair of images: grouped by the reducer and by the truth; a frame in no burst is its own group |
| scene | `scene_acc` (rate) | `all`, each truth label | The top label is the truth's |

"Rejected" means select's reasons (after its waivers, e.g. underexposed at night) when the run had select, else
quality's. `soft` pools `soft_subject` and `motion_or_defocus`, which differ only in whether anything else in the
frame is sharp: a soft subject against smooth bokeh reads as `motion_or_defocus`. Synthetic degradations are cleaner
than real ones, so these numbers are floors for the rules, not a claim about real albums; that needs the owner's
labelled trips (issue #29).

## geotag: GPX geotagging (`bench geotag`)

`bioscan geotag` places photos on a GPX track (geotag.md). The owner has no GPX
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
expected. Measured 2026-09-25 on the Mac (perfect scenario, v1.5 build): vs-truth 0 fixed, 0 broken, every metric identical; vs-nogeo top-1 +5.2 pts (85 fixed, 1 broken, McNemar p ≈ 0). See results.md.

## aesthetic: agreement with the owner (`bioscan aesthetic eval`)

The `aesthetics` stage (album.md "Aesthetics") ranks frames by a linear head on the SigLIP2 frame
vector. `bioscan aesthetic eval` measures how its ranking agrees with the owner's own ratings. Like
`bench geotag`, it has its own report (schema `bioscan-aesthetic-report`, version 1) and feeds the shared
scorecard, not `bench compare`: compare is built around species answers per image. It lives in
`bioscan/cli/aesbench.py`, so it does not touch the species report's code.

```sh
# service running (bioscan serve): vectors come from its embed product, cached in --embeddings
bioscan aesthetic ratings ~/Pictures/Album                               # what the XMP holds, per trip
bioscan aesthetic eval ~/Pictures/Album --out runs/$D-aesthetic --embeddings runs/aes-vec.ndjson \
    [--head builtin|PATH|none] [--personal PATH] [--blend 0.5] [--k 10] [--curve 50,100,200,500,1000] [--no-curve]
bioscan bench scorecard runs/$D-aesthetic/report.json                    # tier aesthetic-own (docs/standards.md §13)
```

**Ground truth.** A folder of rated images (`xmp:Rating` in `<stem>.xmp`, `<name>.<ext>.xmp` or embedded) or a
CSV `path,rating[,pick,label,trip]`, both read by one rule (`aesthetic.stars_of`, the XMP spec): 1-5 = stars;
**0 or missing = unrated, skipped**; -1, or a reject pick flag without stars, = a **reject**, kept with grade 0
(below one star) and pick -1; a pick flag without stars gives no grade and is skipped. The **trip** of
an image is its first folder under the root (the CSV's `trip`, else its parent folder). Picks are the explicit
pick flags when any row has one (`xmpDM:pick`, or the CSV), else stars >= `--pick-min` (4).

**Heads scored.** `general` (the builtin head, or `--head PATH`), `personal` (`--personal`) and `blended`
(`(1 - blend) * general + blend * personal`) when both exist. The **served** score, the one the stage would
return with these options, is what `metrics.all` holds and what the scorecard judges.

**Metrics** (per head in `by_head`, per trip in `per_trip`):

| Key | Definition |
|---|---|
| `spearman` (`spearman_ci`) | Spearman ρ between score and stars over every rated frame; 95% interval by Fisher z with SE 1.06/√(n−3) |
| `kendall` | Kendall τ-b, ties corrected |
| `plcc` | Pearson r (a scale check; rankings use the two above) |
| `spearman_trip_mean` | mean of the per-trip ρ (the pooled ρ also rewards telling trips apart) |
| `ndcg_at_k` | NDCG@k (k 10) per trip with gain 2^stars − 1, averaged over trips |
| `precision_at_k` (`_ci`, `_random`) | per trip, k = the owner's picks there: how many of the top-k frames by score are picks; pooled over trips (hits / picks, Wilson interval), next to what a random order gets (Σ k²/n / Σ k) |

**Learning curve** (`curve`, needs numpy, so it is skipped with `--no-curve`). Trips are split into `--folds`
folds (default 5); a trip is never split. For each fold and each size N (50, 100, 200, 500, 1,000), N ratings are
drawn from the other folds' trips (3 seeded draws), a personal head is fitted (ridge, `--alpha`, pulled toward the
general head when there is one) and scored on the held-out trips; the general head and the blend are scored on the
same frames. A size larger than a fold's training set is skipped (`runs` 0). Each point is the mean ± sd of the
held-out Spearman over folds and draws.

**In-sample warning.** A personal head records the sha of the ratings it was fitted on (`ratings_sha256`). When it
equals the evaluated set's, `meta.personal_in_sample` is true and report.md says the personal rows are optimistic.
Fit on some trips, evaluate on others, or read the learning curve, which always holds trips out.

**Training** (`bioscan aesthetic train`) shares the code: `--ratings SRC` fits a personal head (ridge on centred
vectors, alpha by 5-fold CV over trips, pulled toward `--prior builtin|PATH|none`; default output
`~/.config/bioscan/aesthetic-personal.json`), `--eva DIR` the general head from an EVA checkout. The EVA head is
normally made by `scripts/train_aesthetic_head.py` in CI or on the Mac (data/aesthetic/README.md). Every fit is
deterministic from its inputs and `--seed`. The CV score in a head's provenance is the best alpha's mean over the
same folds that chose it (not nested CV), so it reads slightly optimistic.

## aesthetic golden set (`bench aesthetic`)

`bioscan aesthetic eval` scores bioscan's own heads against stars. `bench aesthetic` is the model-agnostic
counterpart: it reads a **scores file** from any scorer and holds it to a frozen **aesthetic golden set**. The design,
the research behind it and the target sizes are in docs/research/2026-09-24-aesthetic-golden-set.md. Standard library
only (bioscan/cli/aesgolden.py); no service, no model.

```sh
bioscan bench aesthetic init ~/Pictures/Album/golden-trips --out ~/aes-golden [--cull runs/cull/cull.csv]
# fill images.csv: group, best, keep, reasons, category, slices, split, stars2; optional pairs.csv (a,b,winner)
uv run python scripts/aes_plant.py ~/aes-golden --n 60          # planted copies; refuses to run twice
bioscan run ~/aes-golden -r --profile album --json --out runs/aes/eva.ndjson
bioscan bench aesthetic score ~/aes-golden runs/aes/eva.ndjson --out runs/aes/eva [--repeat runs/aes/eva-2.ndjson]
bioscan bench aesthetic score ~/aes-golden runs/aes/qrealign-4b.ndjson --out runs/aes/qrealign-4b
bioscan bench aesthetic table runs/aes/*/report.json --ref data/aesthetic/eva-golden-v1.csv --csv runs/aes/arena-residuals.csv
bioscan bench aesthetic compare runs/aes/eva/report.json runs/aes/qrealign-4b/report.json --md runs/aes/compare.md
```

**Scores file.** NDJSON: bioscan events (a `result` gives `products.aesthetics.score`, an `error` a failed frame;
`meta`, `progress`, `done` are skipped), or one line per frame `{"path", "score", "model"?, "dims"?: {name: value},
"reasons"?: [drop reason], "ms"?}`. Or CSV `path,score`. Relative paths are taken from the golden folder. A frame
without a finite score counts as the lowest score everywhere.

**images.csv / pairs.csv.** Columns and the drop-reason vocabulary are in the design doc (§4.1); a file with an
unknown column, variant or reason is refused. Rows default to split `test`; `--split dev|all` scores the others.

**report.json** (schema `bioscan-aesthetic-golden`, version 1): `meta` (tier `aesthetic-golden` and profile `album`
for `bench scorecard`, golden path and sha256 of images.csv + pairs.csv, split, model, scores path and sha, git, date, tolerances), `metrics` per scope (`all`, `category:<c>`,
`slice:<s>`), `dims`, `repeat`, `owner_ceiling`, `reasons`, `missing`, `extra`, and per-item `images`, `pairs`,
`groups` for paired comparison. Keys of `metrics.all`:

| Key | Definition |
|---|---|
| `expected`, `scored`, `missing_rate`, `failed`, `nonfinite`, `extra`, `duplicates` | Frames of the split plus their planted copies; how many have a finite score; error events; non-numeric scores; frames not in the set; paths scored twice |
| `pair_acc`, `pairs`, `pairs_tied_by_owner` | Share of owner choices (pairs.csv, plus each group winner over every other member) where the chosen frame scores strictly higher. Owner ties are left out |
| `group_top1`, `groups`, `group_top1_random` | Share of shot groups whose highest-scoring frame is the owner's winner; random = mean 1/size |
| `keepers_lost_at_{10,20,30}`, `reject_precision_at_{10,20,30}` | Per trip, frames with a keep label sorted by score, the lowest q dropped: kept frames among the dropped / kept frames; dropped frames the owner dropped / dropped frames (pooled over trips) |
| `drop_auc` | P(a frame the owner dropped scores below one they kept), ties half |
| `spearman`(`_ci`), `kendall`, `plcc`, `spearman_trip_mean`, `ndcg_at_k`, `precision_at_k`(`_ci`, `_random`), `rated` | As `aesthetic eval` (same code, `aesbench.set_metrics`), with keep = 1 as the picks |
| `degrade_acc`(`_ci`, `_n`, `_by_kind`) | Planted blur / ev-2 / ev+2 / jpeg10 copies scoring strictly below their original |
| `invariance_rate`(`_ci`, `_n`, `_by_kind`), `invariance_max_shift` | Planted rename / jpeg95 / resize2048 copies within 5 percentile points of their original (percentiles of the split's scores) |
| `residual`(`_ci`, `_n`) | Mean of (score percentile − stars percentile): above 0, the scorer likes the scope more than the owner does (every scope) |
| `ms_median` | Median `ms` of the scores file, when given |

**compare** refuses reports of different golden sets or splits (exit 2). It prints deltas for every shared metric,
McNemar on the pairs and on the shot groups both reports scored, and a 95 % bootstrap interval of the Spearman change
that resamples shot groups (`--boot`, default 1000, seed 0). `--budget` (default `baselines/budget-aesthetic.toml`
when present) takes `[[rule]]` tables as in budget.toml, with any numeric key of `metrics` and `scopes` defaulting to
`["all"]`. Exit 1 when over budget.

**aes-golden-v1 = EVA-100, baseline = eva-head-v1.** The frozen golden set v1 is the 100 held-out EVA images
(`data/aesthetic/eva-golden-v1.csv`, built with `scripts/eva_golden.py build`); its first baseline is the general
head's report, `baselines/aes-golden-v1-eva-head-v1.json`. To re-score: `bioscan run ~/aes-golden-eva -r --profile
album --json --out runs/aes/<model>.ndjson` (or any scorer's scores file), `bench aesthetic score ~/aes-golden-eva
runs/aes/<model>.ndjson --out runs/aes/<model>`, then `bench scorecard runs/aes/<model>/report.json` (tier
`aesthetic-golden`, docs/standards.md §13) and `bench aesthetic compare baselines/aes-golden-v1-eva-head-v1.json
runs/aes/<model>/report.json` (budget sized at about half of that run's 95 % intervals). A report scored before
the tier existed has no `meta.tier` or `meta.profile`: score its scores file again (no model needed).

**table** is the arena: N reports of one golden set and split (else exit 2), ranked by Spearman against the grades,
each with the deviation from the labels in the labels' own units and a paired bootstrap against the leader:

| column | what |
|---|---|
| Spearman [95% CI] | against stars, bootstrap over shot groups (`--boot`, default 1000, seed 0) |
| cross-grade pairs [CI] | of all frame pairs with different stars, the share the model orders like the owner (a score tie counts as wrong): Spearman restricted to the pairs the labels actually separate, the most sensitive column at small n |
| grade MAE [CI], exact / ±1 | after quantile calibration (the model's i-th lowest frame gets the i-th lowest grade, no fitted parameter): how many stars off on average, how often exactly right, how often within one |
| ΔSpearman vs top [CI] | the same resamples for every model; an interval containing 0 marks the rank `=` (tied at this n) |
| ms, and the owner-set columns (owner pairs, group top-1, NDCG, drop AUC, keepers lost @20 %, planted, missing) | only those with a value in some report |

Below the table: the frames the models disagree on most (calibrated grade per model), with the label's sd when
`--ref` gives it (`data/aesthetic/eva-golden-v1.csv`, keyed by file stem). `--csv` writes every frame's score,
percentile, calibrated grade and residual (model percentile − grade percentile) per model, worst first. No Elo:
every frame has an answer. Models within about 0.05 Spearman at n = 100 are not separable
(docs/research/2026-09-24-aesthetic-arena.md). External scorers are run by `scripts/aes_arena_score.py` in their own
environment; `--purge` removes the weights it downloaded once the scores are written, so only the service's own
models stay in the cache. Reports of this tier list the owner's frame paths: they stay on the owner's machine unless
the owner decides otherwise.

