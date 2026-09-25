# data/aesthetic/ — the general aesthetic head

`eva-head-v1.json` is the **general aesthetic head**: a ridge-regression head on the SigLIP2 frame
vector (`google/siglip2-base-patch16-224@75de2d55ec2d`), fitted on EVA's mean scores. The
`aesthetics` stage reads it as `head: builtin` (bioscan/plugins/aesthetics).

Committed 2026-09-25 (UTC) from the owner's Mac: `eva-head-v1:d5985bc9ea9e`, n 3,970 (the 100 held-out images
left out), alpha 1000, 5-fold CV SRCC 0.792 (sd 0.013), PLCC 0.806; on the held-out 100, Spearman 0.877
(`bioscan bench aesthetic score`). Without the file the stage answers `score: null` with a `note`, and nothing fails.

## File format

JSON, `format: "bioscan-aesthetic-head"`, `version: 1` (bioscan/aesthetic.py):

| Key | What |
|---|---|
| `name` | `eva-head-v1` |
| `embedding` | the model@revision the vectors came from; a head for another embedding is refused |
| `dim`, `weights`, `bias`, `mean`, `std` | score = bias + weights · (vec − mean) / std, 768 of each; 7 significant digits |
| `target` | `lo`, `hi`: the rating range that maps to 0 and 1 (EVA: 0 and 10), and what it is |
| `provenance` | data, licence, citation, n, date, seed, alpha, 5-fold CV SRCC/PLCC (with every alpha tried; the reported CV is the best alpha's on the same folds that chose it, not nested CV, so it reads slightly optimistic) |
| `sha` | sha256 of the canonical JSON of everything else; checked on load, so an edited file is refused |

The sha appears in `result.products.aesthetics.head_id`, `result.engine.plugins.aesthetics` and the
stage's `settings()`.

## Training data: EVA

| | |
|---|---|
| Source | https://github.com/kang-gnak/eva-dataset, commit `fb40a9f1abe4be96b69229aaac3d0838a2e1d31c` (master on 2026-09-24; last change 2022-10-07), pinned in `bioscan.aesthetic.EVA_COMMIT` |
| Licence | the repository's `LICENSE` is **CC0 1.0 Universal** (checked 2026-09-24) |
| Images | `images/EVA_together.zip.001`–`.007` (six of 103,809,024 bytes and one of 72,385,748; one zip, joined before unzipping), 5,101 JPEGs `EVA_together/<AVA id>.jpg`: "the resized images from AVA dataset that were shown in our experiment" (readme.md) |
| Image copyright | AVA photos come from dpchallenge.com; their copyright stays with the photographers. EVA's CC0 covers what the authors could waive (the annotations); bioscan uses the images only to compute vectors and **never redistributes them** |
| Scores | `data/votes_filtered.csv` (`=`-delimited): 136,943 filtered votes on 4,070 images, 30-46 each (median 33); `score` is the 0-10 general score. The head's target is each image's mean (1.76-9.03, mean 6.12, sd 1.02) |
| Not used | AVA's own scores, AVA-trained weights, the per-attribute votes, `votes.csv` (unfiltered) |
| Citation | Chen Kang, Giuseppe Valenzise, Frédéric Dufaux, "EVA: An Explainable Visual Aesthetics Dataset", ATQAM/MAST'20 |

**AVA is never used** and no AVA-trained weight is ever distributed (owner decision, 2026-09-24).
A personal head (`bioscan aesthetic train --ratings`) is fitted on the owner's own ratings and
stays on the owner's machine (`~/.config/bioscan/aesthetic-personal.json`); it is never committed.

## Held out: the public golden set (`eva-golden-v1.csv`)

100 EVA images the crowd agrees on, 20 per star, **never fitted on**: `bioscan.aesthetic.read_eva` leaves them out
of every general-head fit, and the head's provenance records `held_out` (file, 100 images, sha256). They are the
public half of the aesthetic golden set (docs/research/2026-09-24-aesthetic-golden-set.md, §4.5): how far a scorer
agrees with EVA's raters, next to how far it agrees with the owner.

Chosen by `scripts/eva_golden.py select` from `votes_filtered.csv` at the pinned commit, seed 7:
- **Star bands** on the mean general score, with gaps: 1 = below 4.5, 2 = 5.0-5.5, 3 = 5.9-6.3, 4 = 6.7-7.1,
  5 = 7.5 and up.
- **Consensus**: inside a band, only images whose score sd is at or below that band's median. Low-scored images
  draw more disagreement, so one global limit would empty the low bands.
- Every image has 30-46 votes. The columns are image_id, stars, mean, sd, votes, and the means of the four
  attribute ratings (visual, composition, quality, semantic).

The list is frozen: `select` refuses to overwrite it, and a new list is a new version (and a new head). Only ids
and CC0 scores are committed; `eva_golden.py build --eva DIR --out DIR` links the photos from a local EVA checkout.

## How to produce it

The vectors go through the service's own code: `bioscan.service.decode` (the 2048 px image) and
`Engine.frame` (SigLIP2 at the pinned revision). The fit is ridge on centred vectors, alpha by
5-fold CV (seed 0), refit on all 4,070.

**CI** (`.github/workflows/aesthetic.yml`, CPU; estimated 15-25 min, unmeasured): run the
`aesthetic` workflow from the Actions tab (the file must be on the default branch for the button),
or push a tag `aesthetic-head-<anything>` on the commit to train. The log prints the CV numbers,
then the file between markers, then its sha256:

```sh
# copy the lines between the markers from the log into head.b64, then:
base64 -d head.b64 > data/aesthetic/eva-head-v1.json
sha256sum data/aesthetic/eva-head-v1.json        # must equal the log's "sha256(file)"
uv run python -c "from bioscan import aesthetic as a; h = a.load_head(a.BUILTIN_HEAD); print(h.id, h.provenance['cv'])"
```

**Mac** (MPS; weights already in ~/.cache/huggingface from tests/models/download.py):

```sh
uv run python scripts/train_aesthetic_head.py --download --eva-dir ~/.cache/bioscan/eva
# -> data/aesthetic/eva-head-v1.json; ~700 MB download once, vectors cached in ~/.cache/bioscan/eva/embeddings.ndjson
```

or, with the service running (`bioscan serve`), through its `embed` product:

```sh
uv run python scripts/train_aesthetic_head.py --download-only --eva-dir ~/.cache/bioscan/eva
uv run bioscan aesthetic train --eva ~/.cache/bioscan/eva --embeddings ~/.cache/bioscan/eva/served.ndjson
```

Before committing the head: check the CV SRCC in its provenance (a generic head on EVA should rank
well above 0; published generic heads reach 0.67-0.82 SRCC on AVA's own split, a different task), and
run `bioscan aesthetic eval` on a rated folder to see how it agrees with the owner.
