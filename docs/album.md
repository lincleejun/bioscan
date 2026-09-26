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

## Scoring a folder: `bioscan aesthetic score`

The ranking on its own, without the cull reducers: score a folder with the `album` profile and export it in any of
three forms at once, so the same run can be looked at and kept as data.

```sh
bioscan aesthetic score ~/Pictures/2026-05-trip -r --export json,csv,html --out ~/Pictures/2026-05-trip/aesthetic
bioscan aesthetic score --preds aesthetic.ndjson --export html --out aesthetic      # again, offline
bioscan aesthetic score ~/Pictures/2026-05-trip --species --export json,csv,html --out aes   # ... and name the animals
```

`--export` takes `json` (default), `csv`, `html`, comma-separated; `--out` is a prefix. `--species` turns the album
profile's species naming on (BioCLIP loads; the location prior applies as in `bioscan run`): each photo carries the
name of its surest box (species, or genus / family when only that held up, as `bioscan summarize` counts it).

| file | what |
|---|---|
| `<out>.ndjson` | the run's events with a meta line: what `--preds`, `bioscan cull --preds` and `bench aesthetic score` read back |
| `<out>.csv` | one row per photo, best first: rank, path, score, stars, scene, species, common, level, reject_reasons, sharpness, taken_at (the three name columns are empty without `--species`; `common` is the English name at every level: "Lesser Goldfinch", or "a vireo" / "a hawk or eagle" for a genus or family, from the common names of the taxon's candidates); failed decodes last |
| `<out>.html` | the review page: a photo grid (score, stars, name, reject reason on each card), an overview panel (marks progress, the score cut of each star) and a **taxon tree** (class → order → family → genus → species, with counts; click a branch to see only it, ⇢ merges one name into another). Search, sort (default: stars, then time taken, so a burst stays in shooting order within a star band; also score, name, date, species) and filters (stars, marks, reject reason, scene, species, folder) live in the toolbar's popovers; active filters show as chips. **Marks**: click selects a card, shift-click a range, ⌘-click adds, ⌘A all shown; a floating bar keeps / drops / unmarks the selection, the group header does the same for everything in view (the branch or filter), <kbd>K</kbd> / <kbd>X</kbd> / <kbd>U</kbd> work on the selection and in the lightbox. Marks stay in the browser. **Export keeps…** (overview panel) asks for a destination path (remembered) and copies the kept originals and their XMP sidecars there, files already there left as is; **Delete drops…** asks for confirmation and removes the dropped originals and sidecars from disk. Both go through the running bioscan service (`POST /apply` on the `--url` the page was made with, gated by the token in `~/.cache/bioscan/serve-token` that `aesthetic score` bakes into the page), so keep `bioscan serve` running while you review; the paths must be inside its `--allow-root`s. Without a service the page falls back to the browser's directory picker (Chrome, Edge: it asks for the photo folder once and remembers it); elsewhere save the decisions file from the ⋯ menu and run `bioscan aesthetic apply`. The **lightbox** (double-click, ↵ or the corner button) shows the original when the browser can read it (jpg, png), else the service's jpg copy at `--edge` (default 3072 px, the ceiling of the service's detail image); ←/→ move, Esc closes. The copies are in `<out>-files/` with a `--thumb-edge` (default 1024 px) thumbnail next to each for the grid; `--no-thumbs` shows browser-readable originals only |

`bioscan aesthetic apply bioscan-decisions.json --keep-to DIR --drop-to DIR` acts on the page's marks: the keeps are
**copied** to `--keep-to` (originals and their XMP sidecars), the drops **moved** to `--drop-to`; `--dry-run` only
reports. Nothing is ever deleted: empty the drop folder yourself when you are sure. A photo already at the target is
skipped and a missing one reported (exit 1).

Stars are quintiles of the run's own scores (5 = top fifth), a relative rank and not a rating. A photo without a
score (no head installed) sorts last with the head's note; nothing is rated, moved or deleted.

## Culling an album

`bioscan cull` sorts a folder for review: rule-based rejects with their reasons, bursts with their best frame, and
the best photos per scene category. It runs the `album` profile through the service, then the `burst` and `select`
reducers here. It never deletes or moves anything: rejects are listed, not removed.

```sh
bioscan cull ~/Pictures/2026-05-trip -r --html review.html --csv selection.csv --link-dir picks --per-category 20
bioscan cull --preds cull.ndjson --html review.html      # again, offline, from a saved --json run (e.g. new limits)
```

- **Rejects** (`quality`, rules only, each with a reason): `soft_subject` (the subject box is soft while something
  else in the frame is sharp: focus landed elsewhere), `motion` and `defocus` (nothing in the frame is sharp,
  which includes a soft subject against smooth bokeh; `motion` when the frame kept its detail along one axis and lost
  it along the other, as a shake or a pan leaves it, `defocus` when it is soft both ways), `overexposed` (8% of the
  subject blown, or a bright subject with 4% blown), `underexposed` (nothing in the frame brighter than two stops under white,
  or the whole frame dark and the subject too; a dark bird alone is not a reject), `subject_cut` (the box touches the frame edge and is not frame-filling),
  `subject_too_small` (under 0.5% of the frame) and `no_subject` (the gate sees an animal, the detector boxes none).
  Sharpness here is a re-blur measure on the subject box's core; every threshold is a constant in
  `bioscan/plugins/quality/stage.py` and in the stage's fingerprint. The subject is identify's best box, so photos
  without an animal (landscapes, people) are judged on the whole frame. `select` waives `underexposed` for the
  `night` group (not for the attribute `light=night`: a -2 EV day photo reads as night, CI 2026-09-26); an unknown
  category or reason in `waive` is rejected.
- **Scene** (`scene`): SigLIP2 zero-shot over the frame vector the service already computes. The album profile
  names 40 fine labels (`label`) in 8 groups (`group`): wildlife, landscape, night, people, macro, architecture, food,
  other (docs/research/2026-09-24-scene-taxonomy.md §3.2). The wildlife group gets the gate's bird + mammal +
  other_animal share (only when identify found a box: `wildlife_box`, since the gate drifts on photos without
  animals), split by its own prompts; its label, in order (`wildlife_rules`): 4 identify boxes or more is
  `herd_flock`; `bird_flight` or `domestic` when its prompts give it more than 0.5; the best box's kind and area
  (8% of the frame or more a `_portrait`, less a `_habitat`; `other_animal`); with no box, its prompts' top. The
  other labels share one softmax; a group scores the sum
  of its labels (`group_scores`). Three attributes are scored apart: `light` (day, golden_hour, blue_hour, night),
  `setting` (outdoor, indoor, underwater), `framing` (close_up, medium, wide, aerial). Change them under
  `[profile.album.options.scene]` (`labels`, `groups`, `attributes`, `wildlife_rules`); without `groups` the stage
  keeps its 8 built-in labels, one per category. The landscape group also gets a horizon tilt (reported, not a reject).
  In `aesthetic score`'s CSV and HTML the scene column shows the label with its group, e.g. `coast (landscape)`.
- **Bursts** (`burst`): frames of one camera (EXIF Make and Model) at most 1.5 s apart, using the sub-second capture
  time, whose frame vectors have cosine at least 0.92.
- **Selection** (`select`): the best frame of each burst: not rejected, subject sharpness (within 0.03 of the sharpest
  counts as equal), not cut off, exposure within ±0.2, then the aesthetic score when a run has one (it only reorders,
  never rejects). Then the top `per_category` (default 10; `--per-category`, 0 = all) of each category (`by`: the
  scene group, the default, or `"label"` for the fine label), skipping a
  photo whose frame vector is 0.95 alike to one already picked. Each photo gets a status: `pick`, `spare` (a keeper
  past the top N), `duplicate` or `reject`. `waive` lifts reject reasons per scene group, label or attribute value:
  `waive = { night = ["underexposed"] }` by default; an attribute key reads `"light=night" = ["underexposed"]`.
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
  rejects. Lightroom reads sidecars for RAW files only, not for JPEGs. These stars are bioscan's, not yours: cull
  records them in `bioscan:stars` (0 for a reject), and the ratings reader (`bioscan aesthetic ratings|train|eval`,
  `bench aesthetic init`) skips a cull sidecar still carrying those stars. Once you change its stars (to -1 for a
  reject too), it counts as your rating.
- **Accuracy**: unverified on real albums. CI measures the rules and reducers on a synthetic reject set made from the
  smoke photos (docs/harness.md "Album tier", docs/standards.md §14).
- **motion vs defocus** (`MOTION_RATIO` = 1.4 in `bioscan/plugins/quality/stage.py`): set on the synthetic set only
  (#28 brings real rejects). `scripts/cull_synth.py` over the 1,026 photos of `runs/2026-09-23-inat-v2` (iNat, the
  subject box from the run standing in for the detector), 2026-09-25: of the whole-frame motion smears (`shake`) the
  rules gave a whole-frame reason, 851 of 869 (97.9%) read `motion`; of the whole-frame Gaussian blurs (`defocus`),
  875 of 888 (98.5%) read `defocus`. At 1.5 motion drops to 94.1% (defocus 99.2%), at 1.3 defocus to 96.4%. The split
  changes no reject: which photos get a whole-frame reason is the same as before it. Real shake is rarely one clean
  line and real defocus is not a Gaussian, so expect less on real albums.
