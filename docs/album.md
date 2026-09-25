# Album: aesthetics and culling

The `album` profile scores each frame (aesthetics, quality, scene) and `bioscan cull` turns a folder into picks, bursts and rejects with reasons. Nothing here deletes or moves a photo or writes into one; only `cull --xmp` rates photos, in new XMP sidecars.

## Aesthetics

The `aesthetics` stage gives each frame an aesthetic score from a small linear **head** on the SigLIP2 frame vector that the frame pass already computes: no new model, a few kB of weights, one matmul per chunk on the CPU pool. It is in the `album` profile (never in `full` or `wildlife`), and **it only reorders frames: it never rejects or deletes one**; the `select` reducer ([Culling an album](#culling-an-album)) reads `products.aesthetics.score` to rank within a burst or a category.

- **Output** `products.aesthetics`: `score` (0-1, the blend below; not clipped), `general`, `personal` (null without a personal head), `head_id` (`name:sha12`, `+name:sha12~blend` when blended). Without the general head file the score is null and a `note` says why; the run never fails for it.
- **General head**: `data/aesthetic/eva-head-v1.json`, a ridge head fitted on **EVA** (4,070 photos, 30+ votes each, mean score 0-10). Committed (CV SRCC 0.79 on EVA; retrain with the `aesthetic` workflow or on a Mac, commands in `data/aesthetic/README.md`); without the file album runs report `score: null`.
- **Personal head**: fitted on your own ratings, pulled toward the general head, and blended with it by `blend` (the personal weight, default 0.5):

  ```sh
  bioscan aesthetic ratings ~/Pictures/Album                     # stars/labels from XMP (sidecars or embedded), per trip
  bioscan aesthetic train --ratings ~/Pictures/Album --embeddings ~/.cache/bioscan/album-vec.ndjson
  #   -> ~/.config/bioscan/aesthetic-personal.json (ridge, alpha by 5-fold CV over trips)
  bioscan run ~/Pictures/Album --profile album                   # general head only, unless the profile names yours:
  ```

  ```toml
  [profile.album.options.aesthetics]
  head = "/Users/me/.config/bioscan/aesthetic-personal.json"   # builtin | an absolute path | off
  blend = 0.5
  ```

  Ratings are Lightroom stars 1-5 (`xmp:Rating`; 0 or missing = unrated and skipped, as the XMP spec says; a reject, -1, is kept as a reject, graded below one star), with the colour label and `xmpDM:pick` where a tool writes them; a sidecar wins over embedded XMP. Lightroom Classic keeps pick flags in its catalogue, not in XMP, so give picks through a CSV (`path,rating,pick,trip`) if you want them. The CLI never loads a model: vectors come from the running service's `embed` product (`--embeddings FILE` keeps them). The head path is checked against the service's allow-roots.
- **How well it agrees with you**: `bioscan aesthetic eval ~/Pictures/Album --out runs/aes --personal ~/.config/bioscan/aesthetic-personal.json` writes report.json and report.md: Spearman and Kendall against your stars, NDCG@10 and precision@k against your picks per trip (k = your picks in that trip, next to what a random order gets), and a **learning curve** at 50/100/200/500/1,000 ratings for personal vs general vs blended heads, always split by trip (folder) so a burst never sits on both sides. `bioscan bench scorecard runs/aes/report.json` holds it to the `aesthetic-own` standards (docs/standards.md §13). **No aesthetic number has been measured yet: every claim here is unverified.**
- **Choosing between aesthetic models**: the aesthetic golden set (docs/research/2026-09-24-aesthetic-golden-set.md) scores *any* scorer's output, not only bioscan's head. Build it once: `bioscan bench aesthetic init ~/Pictures/Album --out ~/aes-golden` writes images.csv from your stars; add shot groups and their winners, keep/drop with a reason, categories and slices; then `uv run python scripts/aes_plant.py ~/aes-golden` adds planted copies whose answer is known (renamed, re-encoded or resized = same score; blurred or 2 EV off = lower). Score a model with `bioscan bench aesthetic score ~/aes-golden SCORES --out runs/aes/<model>` (SCORES = `bioscan run --json` output, or NDJSON `{path, score}` from any model), and compare two with `bench aesthetic compare` (McNemar on pairs and shot groups; budget baselines/budget-aesthetic.toml). The headline numbers are the winner of each shot group, pairwise accuracy, and keepers lost when the lowest 20 % of each trip is dropped.
- **Licences.** EVA's annotations are **CC0 1.0** (its repository's LICENSE). Its images are AVA photos from dpchallenge.com whose copyright stays with the photographers: bioscan uses them only to compute vectors and never redistributes them. The head weights are trained locally or in this repository's CI, and the head file records its data, licence, n, date, seed and CV numbers. **AVA scores and AVA-trained weights are never used or distributed.** A personal head is fitted on your own ratings of your own photos and stays on your machine.

## Culling an album

`bioscan cull` sorts a folder for review: rule-based rejects with their reasons, bursts with their best frame, and
the best photos per scene category. It runs the `album` profile through the service, then the `burst` and `select`
reducers here. It never deletes or moves anything: rejects are listed, not removed.

```sh
bioscan cull ~/Pictures/2026-05-trip -r --html review.html --csv selection.csv --link-dir picks --per-category 20
bioscan cull --preds cull.ndjson --html review.html      # again, offline, from a saved --json run (e.g. new limits)
```

- **Rejects** (`quality`, rules only, each with a reason): `soft_subject` (the subject box is soft while something
  else in the frame is sharp: focus landed elsewhere), `motion_or_defocus` (nothing in the frame is sharp: shake,
  motion, or focus missed everything, which includes a soft subject against smooth bokeh), `overexposed` (8% of the
  subject blown, or a bright subject with 4% blown), `underexposed` (the whole frame dark and the subject too; a dark
  bird alone is not a reject), `subject_cut` (the box touches the frame edge and is not frame-filling),
  `subject_too_small` (under 0.5% of the frame) and `no_subject` (the gate sees an animal, the detector boxes none).
  Sharpness here is a re-blur measure on the subject box's core; every threshold is a constant in
  `bioscan/plugins/quality/stage.py` and in the stage's fingerprint. The subject is identify's best box, so photos
  without an animal (landscapes, people) are judged on the whole frame. `select` waives `underexposed` for `night`.
- **Scene** (`scene`): SigLIP2 zero-shot over the frame vector the service already computes: landscape, people,
  wildlife (the gate's bird + mammal share), macro, architecture, food, night, other. Change the labels and prompts with
  `[profile.album.options.scene.labels]`. Landscapes also get a horizon tilt (reported, not a reject).
- **Bursts** (`burst`): frames of one camera (EXIF Make and Model) at most 1.5 s apart, using the sub-second capture
  time, whose frame vectors have cosine at least 0.92.
- **Selection** (`select`): the best frame of each burst: not rejected, subject sharpness (within 0.03 of the sharpest
  counts as equal), not cut off, exposure within ±0.2, then the aesthetic score when a run has one (it only reorders,
  never rejects). Then the top `per_category` (default 10; `--per-category`, 0 = all) of each category, skipping a
  photo whose frame vector is 0.95 alike to one already picked. Each photo gets a status: `pick`, `spare` (a keeper
  past the top N), `duplicate` or `reject`.
- **Outputs**: `--csv` (one row per photo: status, keep, category, rank, reasons, burst, burst rank, duplicate of,
  sharpness, aesthetic, capture time; failed photos too), `--link-dir` (a symlink per pick in `<dir>/<category>/`,
  never replacing a file), `--html` (a page with picks per category, spares, bursts, rejects by reason and failures;
  thumbnails are upright JPEGs the service writes to `<page>-files/`, so that folder must be under the service's
  allow-roots; `--no-thumbs` skips them), `--json` (the results with `products.burst` and `products.select`, which
  `cull --preds` and `bench report` read back).
- **XMP** (`--xmp`, off by default): a new `<stem>.xmp` per photo that has no sidecar yet, the same rule as
  `geotag --xmp` (docs/geotag.md): a photo with `<stem>.xmp` or darktable's `<name>.<ext>.xmp` is left alone and
  counted, and the photo file is never written. Picks get `xmp:Rating` 3 stars, spares 2; a reject gets the colour
  label `xmp:Label="Red"`, no stars (so it stays unrated), and its reasons in `bioscan:reasons` (`;`-joined; namespace
  `https://github.com/lincleejun/bioscan/ns/cull/1.0/`); duplicates get nothing. Every pick is already the best of
  its burst, so a burst win adds no star. Filter on 3 stars for the picks, 2 and up for every keeper, Red for the
  rejects. Lightroom reads sidecars for RAW files only, not for JPEGs. These stars are bioscan's, not yours: the ratings
  reader (`bioscan aesthetic ratings|train|eval`, `bench aesthetic init`) skips any sidecar that carries the bioscan namespace.
  An editor that keeps unknown properties when you re-rate a photo keeps that mark too, so delete the cull sidecar
  before rating a photo whose stars should count.
- **Accuracy**: unverified on real albums. CI measures the rules and reducers on a synthetic reject set made from the
  smoke photos (docs/harness.md "Album tier", docs/standards.md §14).
