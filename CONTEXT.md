# bioscan

Local service and CLI that find animals in photos and name the species (all taxa by default), for one
photographer's archive. Terms below are the ones code, tests and docs use.

## Photos and runs

**Run**:
One `POST /run` request: inputs, wanted products and options, answered as an NDJSON stream of events.
_Avoid_: job, batch

**Chunk**:
The slice of a run's images decoded together and given one model turn.
_Avoid_: batch (reserved for model calls)

**Model turn**:
A chunk's exclusive, first-come-first-served hold on the models; between turns other runs can interleave.
_Avoid_: model lock (the mechanism), slot

**Frame**:
One decoded image as identify sees it: the upright ≤2048 px image, its gate, place and date, and an optional detail copy.

**Detail copy**:
A larger (≤ detail edge) decode of the same photo used only for species crops; off when the detail edge is ≤ 2048.

**Scan extensions**:
The file extensions a folder scan picks up, in any case: every RAW extension the decoder routes to rawpy plus JPEG
(`formats.SCAN_EXT`); one list for the decoder, `bioscan run` and `bioscan gt folders`.

**Container**:
Where a file keeps its EXIF: `tiff` (ARW, NEF, DNG, CR2, …), `orf`, `rw2` (TIFF with their own magic), `cr3`
(CMT boxes), `raf` (embedded JPEG) or `image` (read by Pillow) (`decode.container`).
_Avoid_: format (that is the extension)

**Capture time**:
`taken_at`: DateTimeOriginal as ISO 8601 with the sub-seconds and UTC offset when the file has them
(`2026-05-01T08:00:00.37-07:00`); what orders burst frames and picks the location prior's week.
_Avoid_: timestamp, date

**Stage**:
One unit of per-image work in a run, batched per chunk: a plugin in `bioscan/plugins/<name>/` (today `identify`,
`embed`, `jpg`, `geotag`, `aesthetics`, `quality`, `scene`). It reads facts and may provide facts for later stages; its output is the product of the same name.
_Avoid_: hook, step, node

**Manifest**:
What a stage declares without loading anything heavy (`plugin.Manifest`, in the plugin's `__init__.py`): name,
version, the facts it reads and provides, the models it needs under its options, its thread, its options with
defaults, the check of their values, and its output. The service code (`Stage`) is imported from `impl` only when
a plan contains the stage.
_Avoid_: registry entry, spec

**Geotag stage**:
The `geotag` stage (`bioscan/plugins/geotag`): reads `time`, provides `place` from GPX tracks for photos whose
request and EXIF have none, on the CPU pool, no models; in `wildlife`, never in `full`. `bioscan geotag` and
`run --gpx` without such a profile do the same work in the CLI.
_Avoid_: GPS stage

**Fact**:
A named value a stage can read: `image`, `detail`, `time`, `place`, `vec`, `gate` from the host, or one a stage
provides (`boxes` from identify) in `Item.facts`.

**Plan**:
What a run will do, decided before any model loads (`plugin.plan`): the wanted stages in report order, the run
order (providers before readers, ties by name), the models to load, and whether the frame pass and the detail copy
are needed. A read nobody provides, a cycle or an unknown option makes no plan (a 400).
_Avoid_: pipeline, DAG

**Profile**:
A named request template: stages, options and reducers. Built in: `full` (what a request without a profile
gets; fixed), `wildlife`, `album` (`bioscan/profiles.toml`); more, or changes, in a `bioscan.toml`. Expanded by the
CLI (`--profile`) and by the service (`"profile"` in /run) with the same resolver (`bioscan/profile.py`).
_Avoid_: preset, mode

**Config layer**:
One source of profile or serve values, lowest first: stage defaults, `profiles.toml`, the user `bioscan.toml`, the
project `bioscan.toml`, `$BIOSCAN_CONFIG`, then the request (flags or /run body). `bioscan config show` names each
value's layer.

**Reducer**:
A model-free unit over a whole run's results, run by the CLI (`bioscan cull`, `bench`) or offline over a saved
run, never inside the service stream: `burst` and `select` (`bioscan/cull.py`; manifests with kind `reducer` in
`plugins.REDUCERS`). Its output goes under `products[<reducer>]` of the CLI's copy of each result.
_Avoid_: stage (a stage sees one chunk)

**Product**:
A named result a run can ask for per image (`identify`, `embed`, `jpg`, `geotag`, `aesthetics`, `quality`, `scene`): the output of the stage of that name
(`bioscan.plugins.BUILTIN`), under `result.products[<name>]`.

## Identify

**Identify payload**:
`result.products.identify`: the gate plus a list of boxes.

**Gate**:
The whole-frame class (bird, mammal, other_animal, person, none) with its probabilities.
_Avoid_: classifier, filter

**Gate rescue**:
Running the detector on a frame the gate called none/person when enough animal probability remains.

**Box**:
One detected animal: id, xyxy (fractions of the frame), score, kind, quality and optionally species.
_Avoid_: detection (the detector's raw output), bbox (pixels)

**Crop check**:
The gate model re-judging a box's crop, which can veto the box or promote its kind.
_Avoid_: judge, second gate

**Species**:
A box's naming result: the name list used, a level and the top candidates. Absent when species is off, null for a kind with no name list.

**Level**:
How far the evidence supports a name: species, genus, family or unconfirmed.
_Avoid_: grade, confidence

**Range veto**:
Where the place is known and the list has a location prior, a top candidate whose own p_geo is below
ε (`rules.RANGE_EPS`) cannot get level species; the best in-range congener (p_geo ≥ τ, `rules.RANGE_TAU`)
anywhere in the list is listed first, taking the last top-k slot if it was not returned. Identify option `range_veto`.
_Avoid_: geo filter, out-of-range filter

**Kind check**:
Scoring a box's species features against every loaded kind-check list (`taxa.KIND_CHECK`: bird, mammal
and, for other_animal boxes only, the all-taxa list when loaded; each list its own matmul) and giving the box the kind whose best
`rules.KIND_TOP` names hold most of the visual evidence (list size does not count), whatever the gate
and crop check said;
a box that moved on a thin margin (`rules.KIND_SURE`) gets level unconfirmed. Identify option `kind_check`.
Size-corrected (trial `kind_size_correct`): each list's best logits minus their chance level for its size
(`rules.chance_top`) before the lists are compared.
_Avoid_: second crop check, reclassify

**Trial**:
An identify option for an accuracy fix that is off by default until an eval on real photos says it helps
(`identify.TRIALS`, today `kind_size_correct`); not in the settings fingerprint while off by default. One that
becomes a default moves to the switches (`identify.SWITCHES`).
_Avoid_: experiment, flag

**Candidate**:
One ranked name for a box: scientific, common, taxonomy, p_visual, p_geo, posterior.
_Avoid_: confusing it with candidate taxa (the option)

**Candidate taxa**:
The optional `candidates` identify option: scientific names or higher taxa species ranking is restricted
to, across every loaded name list. Only lists with a matching row compete; the kind check picks among
them (off: the box keeps its kind while its list has a match). Empty = all taxa.
_Avoid_: filter, whitelist; "candidates" alone when a Candidate could be meant

**Conformance**:
How a payload departs from the contract's fields (`contract.identify_problems`).

**Reader**:
A CLI-side accessor that tolerates partial payloads (`contract.gate_class_of`, `boxes_of`, …).

## Models

**Model adapter**:
The wrapper around one model: gate and crop check (SigLIP2), detector (OWLv2), species encoder (BioCLIP).
_Avoid_: backend, wrapper

**Adapter seam**:
`engine.Loaders`: where the service builds its model adapters and location prior source; tests pass fakes there.

**Identify module**:
`pipeline.identify_many(models, frames, opts)`; `models` is the three adapters plus name lists and location priors.

## Names

**Name list**:
One species list in the species encoder's text space, loaded finished and frozen: AviList for birds, MDD for
mammals, the all-taxa list for other animals.
_Avoid_: vocabulary (that is the detector's words), taxonomy

**All-taxa list**:
The name list for other_animal boxes (`tol200m-animalia`): every species-level TreeOfLife-200M animal row
outside the classes a curated list covers, with its official vector, float16 (`names.ALL_TAXA`).
_Avoid_: ToL list, fallback list

**List source**:
Where a name list comes from: id, data folder, taxonomic class, reader and label map (`names.ListSource`).

**Label map**:
The CSV giving each list row its BirdNET label and, for birds, its TreeOfLife name
(`data/names/avilist_map.csv`, `data/names/mdd_map.csv`).

**BirdNET label**:
A name-list row's label in the location prior source; empty when the row has none, several joined by
`|` when the list lumps species the source keeps apart (the row takes their largest p_geo).

**Match key**:
The normalised form two names are compared by: `norm_binomial` for scientific names, `norm_label` for folder names.

**List sha**:
The hash of a name list's inputs; it names the list's cache file.
_Avoid_: cache key

## Location prior

**Location prior**:
How likely each row of one name list is at a place and date (`geo.LocationPrior`).
_Avoid_: geo filter, range map

**Prior source**:
The model behind a location prior: `labels` plus `probs(lat, lon, week)`; today BirdNET geo 3.0, for birds
and mammals (identify option `mammal_geo` for the mammal list).

**p_geo**:
One row's location prior value at a place and date; none when the place is unknown or the source fails.

**Unlabelled policy**:
What a list row with no BirdNET label gets as p_geo (`names.ListSource.unlabelled`): `zero` (birds) or
`genus` (mammals: the largest p_geo among labelled rows of its genus, the genus back-off, else a neutral
constant). A backed-off p_geo is not evidence about the species, so it never triggers the range veto.
_Avoid_: default prior, fallback

**Posterior**:
p_visual × (floor + p_geo) renormalised over the whole name list, before top-k.

**Geo gap**:
A row with no BirdNET label whose genus is present at a place; fixed by a reviewed synonym, not a blanket rule.

## Serving

**Serve config**:
The resolved settings one service process runs with (host, port, decode workers, chunk, detail edge, allow roots).

**Resolve**:
Flag, else `BIOSCAN_*` variable, else the `[serve]` table of a `bioscan.toml`, else default, done once per process
(`serve_config.resolve`).

**Allow roots**:
Directories the service may read from and write to; empty means no limit.

**Settings fingerprint**:
Twelve hex characters over the output-changing constants (thresholds, prompts, vocabulary, prior floor,
unlabelled policies, accuracy option defaults, max edge);
the configured detail edge is reported beside it as `engine.detail_edge`.

## Geotagging

**Track**:
Every timed point of one or more GPX files and their segments, merged in time order (`geotag.Track`); GPX times are UTC.
_Avoid_: route (a GPX `rte` has no times), log

**Outing**:
One photographer's photos of one trip with one camera and the track(s) recorded alongside: the unit `bioscan geotag`
works on (one clock offset, one `--tz`), and a group in the synthetic scenarios (same observer and day).
_Avoid_: session, hike

**Clock offset**:
Camera time minus true time, in seconds (a camera 37 s fast has +37). Given (`--offset`), read from a clock photo, or
estimated from reference photos; the corrected capture time is camera UTC minus it.
_Avoid_: time shift, drift (drift is only the slow part)

**Reference photo**:
A photo of the outing that already has GPS (phone, camera GPS link); the clock offset is estimated from where it sits on the track.

**Clock photo**:
A photo of a clock (GPS watch, phone) with the true time it shows (`--clock PHOTO=TIME`); gives the clock offset directly.

**Fix**:
One photo's geotag result: lat, lon, source (`exif`, `gpx` or `none`), seconds to the nearest track point and an error
estimate (`geotag.Fix`). "No fix" means source none.
_Avoid_: match, hit

**Fix rule**:
When the track gives a position: linear between neighbouring points up to the max gap apart, or across a longer gap
whose ends are within the max span and at most the max still time apart (the device stood still, 3 h at most); none
outside the track unless extrapolation holds an end.

## Aesthetics

**Aesthetic head**:
A linear map from the whole-frame SigLIP2 vector to a score (`bioscan.aesthetic.Head`): weights, bias, the
vectors' mean and scale, the rating range that maps to 0-1, and its provenance, in one sha-checked JSON file.
The `aesthetics` stage scores every frame with it; the score only reorders frames, it never rejects one.
_Avoid_: aesthetic model (there is no second backbone), predictor, quality (that is identify's per-box sharpness and exposure, and the `quality` stage)

**General head**:
The aesthetic head shipped with bioscan (`data/aesthetic/eva-head-v1.json`, option `head: builtin`), fitted on EVA's
mean scores (CC0 annotations). Never fitted on AVA.
_Avoid_: default model, base model

**Personal head**:
An aesthetic head fitted on the owner's own ratings (`bioscan aesthetic train --ratings`), pulled toward the
general head; the stage blends the two by `blend`, the personal head's weight. Stays on the owner's machine.
_Avoid_: user model, fine-tune (the backbone is never trained)

**Rating**:
The owner's judgement of one frame: Lightroom stars 1-5 from `xmp:Rating` or a CSV, with a pick flag and colour
label where known. As the XMP spec says, Rating 0 or missing means **unrated**: the frame is skipped. Rating -1
(or a reject pick flag without stars) is a **reject**: kept, with grade 0 (below one star) and pick -1, apart
from unrated frames. A pick flag without stars gives no grade. Pick = explicit pick flag, else stars >= 4.
_Avoid_: label (that is Lightroom's colour label), score (that is the head's output)

**Trip**:
The split group of the owner's ratings: the first folder under the ratings root (or a CSV's `trip` column).
Cross-validation, the learning curve and precision@k split by trip, never by frame, so a burst never sits on
both sides of a split.
_Avoid_: outing (geotag's term: one camera, one track), session, burst

**Learning curve**:
Agreement (Spearman on held-out trips) of freshly fitted personal heads against the number of the owner's ratings
they were fitted on (50, 100, 200, 500, 1,000), next to the general head and the blend on the same frames
(`bioscan aesthetic eval`).

## Culling

**Reject reason**:
Why a rule rejects a photo, one of `soft_subject`, `motion_or_defocus`, `overexposed`, `underexposed`,
`subject_cut`, `subject_too_small`, `no_subject` (the `quality` stage; `select` may waive one per scene category).
Rules only: a photo is never rejected for taste.
_Avoid_: flaw, defect, score

**Subject**:
The photo's main animal: identify's best box by score. A photo without a box is judged on its whole frame.

**Keeper**:
A photo worth keeping: no reject reason (after waivers). In ground truth, a row with `keep = 1`; losing one to a
rule is the `keepers_lost` metric.
_Avoid_: good photo, select (a verb here)

**Burst**:
Frames of one camera at most `max_gap_s` apart whose frame vectors are alike (cosine at least `min_cosine`),
chained in time order by the `burst` reducer; a frame in no burst has id null.
_Avoid_: sequence, series, stack

**Scene category**:
A photo's top `scene` label (landscape, people, wildlife, macro, architecture, food, night, other by default; set
per profile). Selection ranks within it.
_Avoid_: class (the gate has classes), tag

**Selection**:
What the `select` reducer decides per photo: `pick` (the best of its burst and in the top `per_category` of its
scene category), `spare` (a keeper past that), `duplicate` (not the best of its burst, or near-identical to a
pick) or `reject`; with the category, rank and reasons it forms the photo's cull record (`cull.records`).
_Avoid_: rating, stars (those are the owner's, in XMP)

**Aesthetic score**:
`products.aesthetics.score`: the `aesthetics` stage's per-frame score (the general head, blended with a personal head
when one is given; null with a note when no head scored it). `select` reads it when a run has it: it only reorders
within a burst and a scene category; it never rejects.

**Aesthetic golden set**:
A frozen folder of the owner's labelled frames (`images.csv`, optional `pairs.csv`) that any aesthetic scorer is held to
with `bioscan bench aesthetic` (docs/research/2026-09-24-aesthetic-golden-set.md). Its sha256 ties reports together;
a changed set is a new version.
_Avoid_: test set (alone), benchmark data

**Shot group**:
Frames of one shot that the owner chooses between (a group id in the golden set), with exactly one **winner** (`best`).
Labelled by the owner; a burst from the `burst` reducer is only a draft of one.
_Avoid_: burst (that is the reducer's output), series

**Drop reason**:
Why the owner dropped a golden-set frame: a reject reason, or a taste reason (`composition`, `cluttered_background`,
`bad_light`, `eyes_closed`, `pose`, `duplicate`, `other`). A scorer's `reasons` are checked against it.
_Avoid_: reject reason (rules only), flaw

**Planted copy**:
A golden-set frame made from another by `scripts/aes_plant.py` whose expected score is known: the same (rename, jpeg95,
resize2048) or lower (blur, ev-2, ev+2, jpeg10).
_Avoid_: augmentation, synthetic image

**Residual**:
For a scope of the golden set, the mean of (score percentile − stars percentile): how much more (above 0) or less a
scorer likes those frames than the owner does. The bias check.
_Avoid_: bias score, cultural preference

## Standards and releases

**Standard**:
One measurable bar in `docs/standards.md` / `data/standards.toml`: a definition, the industry bar with its source,
our community and stretch bars, the current status and the tier and command that measure it.
_Avoid_: KPI, target (alone)

**Tier**:
One test folder a standard is judged on: `smoke` (CI, 95 photos: 42 birds, 35 mammals, 18 other animals; reduced lists), `golden` (1,625 iNat California),
`own` (the owner's RAW), `public` (future multi-region CC0/CC-BY set), `mac` (speed), `geotag` (synthetic GPX scenarios built from golden),
`album` (synthetic reject set and bursts built from the smoke photos, profile `album`), `aesthetic-own` (the owner's
rated album frames, profile `album`).
_Avoid_: dataset (alone), split

**Community bar**:
A standard's level that must be met, at the Wilson 95% bound, before the v0.x community call.
_Avoid_: threshold (that is a rule constant), floor (that is a smoke-test guard)

**Confident error**:
An image whose best box is graded species-level and named wrong; the confident-error rate divides by all images.

**Release stage**:
v0.x "try it and help identify" (community call) or v1.0 "bundle and release"; each is gated by a named set of
standards at their community bars.
_Avoid_: milestone, phase (the strategy doc's roadmap phases)

## Evaluation harness

**Preds file**:
`preds.ndjson` from `bioscan eval`: a meta line, then the run's result/error events and its `done`.

**Report**:
`report.json` (schema `bioscan-report`): one scored run as data — meta, metrics per scope with Wilson
intervals, per-species and per-family tables, one row per image (`bioscan bench run` / `bench report`).
_Avoid_: results, scores (eval's `report.md` is the markdown view of the same run)

**Scope**:
A report's metrics row: `all`, `bird`, `mammal`, `other`, or `<tier>/<scope>`. A plugin metric has its own scopes
(`all`, `soft`, a reject reason, a scene label).

**Plugin metric**:
A harness metric a plugin declares (`plugin.Metric`: a stdlib row function per image, aggregated as a rate,
median or pairwise precision / recall / F1), reported under `report.json` `plugin_metrics[plugin][scope]`, which
budgets and standards may name.

**Baseline**:
A committed report (`baselines/NAME.json`) that later runs are compared with, e.g. `ci-smoke`, `golden-inat-<tag>`.

**Budget**:
The regressions a comparison may show before it fails (`baselines/budget.toml`): per-metric drop/rise limits
per scope, species lost, images broken.
_Avoid_: threshold (rules.py has those)

**Broken / fixed image**:
An image paired between two reports (by sha256, else path) whose top-1 went from right to wrong / wrong to right.

**Failure class**:
Why an image has no correct top-1, one per image: failed, gate_miss, detector_miss, wrong_kind, not_in_list,
out_of_range, prior_suppressed, within_genus, within_family, far_miss; `overconfident` (wrong at level species) overlaps them.

