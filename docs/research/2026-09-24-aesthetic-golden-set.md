# Aesthetic golden set: how to choose and upgrade aesthetic models (2026-09-24)

> Goal (owner): pick better photos and drop a share of the weak ones faster. This document designs a
> test set and a way of testing that any aesthetic scorer can be held to (bioscan's own head, an
> external model, a VLM judge), without touching the service. The scorer is built:
> `bioscan bench aesthetic` (bioscan/cli/aesgolden.py) and `scripts/aes_plant.py`. The golden set itself
> does not exist yet; only the owner can label it. Every accuracy number below is **unverified** for bioscan.

## 1. What already exists, and the gap

| Piece | What it measures | Gap for choosing a model |
|---|---|---|
| `bioscan aesthetic eval` (tier `aesthetic-own`, standards §13) | Spearman / Kendall / NDCG@k / precision@k of **bioscan's own head** against the owner's stars, per trip; learning curve | Scores only bioscan heads (it embeds through the service); no "which frame of this shot", no cull cost, no planted checks, no bias slices, no model-vs-model paired test |
| `album` tier (standards §14, `scripts/cull_synth.py`) | Rule rejects on synthetic degradations, burst grouping | Rules only; aesthetics is never scored there |

The decision the owner makes is not "give this photo a number". It is, in order of how often it happens:
1. **Which frame of this shot?** Choose the winner of a group of near-identical frames.
2. **Which frames can go?** Drop the weakest share of a trip without losing a keeper.
3. **What is the best of the trip?** Order the keepers.

Stars alone measure step 3. The golden set adds the labels and metrics for steps 1 and 2.

## 2. What the research says (and what it is worth)

Status marks follow `2026-09-24-culling-aesthetics.md`. **V** = I fetched a primary page that states it.
**S** = I only saw it in a search result. **✗** = I found no trace of it. The claims come from an earlier
chat answer and were checked here. This container blocks arxiv, huggingface.co, CVF, nature.com and aclanthology.

| Claim | Status | What it means for the design |
|---|---|---|
| Q-ReAlign (Q-Future): Qwen3.5 backbone, 0.8B / 4B / 9B; score = probability-weighted excellent…bad; AVA SRCC 0.797 / 0.814 / 0.832 (Q-Align 0.798) | **V** (README) | A real candidate. 0.8B is "CPU-runnable". **No licence file**, so evaluate it locally and do not ship it. Beware the lookalike repo `scenic-industrialarts74/Q-ReAlign`, which pushes a Windows .exe |
| ArtiMuse: CVPR 2026, InternVL3, score plus 8 attribute analyses, weights fine-tuned on AVA / FLICKR-AES / PARA / TAD66K | **V** (README) | A candidate with sub-scores (`dims`). No licence file. The dataset is gated and non-commercial. The docs assume CUDA + FlashAttention |
| ArtiMuse SRCC 0.827 / 0.936 / 0.814 / 0.614 | **S** (the numbers appear, but not which one goes with which dataset) | Do not quote them |
| UniPercept: aesthetics + technical quality + structure/texture, ICML 2026, InternVL base | **V** (README) | A candidate with sub-scores. No licence file |
| SILVA (SigLIP2 + personal head, Spearman 0.7342) | **✗** | Treat it as invented. The same idea is bioscan's own personal head (C2) |
| AestheticSigLIP: SigLIP2 multi-layer 3456-d features, Gemini-Flash-scored training | **S** | Its scores follow another model's taste, not people's. The "0.671 vs Qwen3-VL-32B" figure was not found |
| PAMELA: personal preference, 200 users, frozen SigLIP2 | **S** | Its images are **AI-generated**, not photos. Useful as method, not as data |
| Scientific Reports 2026-07-13: Qwen3-VL-8B and Llama-3.2-11B rate East Asian art lower (d = −0.46 / −0.36) | **S** | The "87 % of the gap remains after complexity controls" figure is for **Qwen only**. Evidence that a model's lean must be measured, not assumed |
| AesBiasBench (EMNLP 2025): adding identity to the prompt often increases bias | **S** | Never prompt "rate like an Asian viewer". Measure the lean against the owner instead (§5.6) |
| Photo Triage (Chang et al., SIGGRAPH 2016): 15,545 photos in 5,953 series, human pairwise preferences, metric = pairwise accuracy | **S** (licence unknown; a Kaggle mirror's "CC0" is not the authors') | The de-facto protocol for "which frame of this series", adopted here (§5.2). Its data is evaluation only, local only |
| BI-SQA (2025): burst quality, 5,109 sequences built from Photo Triage + SPAQ | **S** | A newer public companion. Licence not found |
| EVA: per-attribute votes (light & colour, composition & depth, quality, semantics), CC0 annotations | **V** | The one public set whose labels bioscan may use. Its attributes can check a model's sub-scores |
| VQQA (Google, arXiv 2603.12310): questions generated per sample, a VLM answers each on 0–100, low answers are the "semantic gradient"; a holistic global score selects better than the mean of the per-question scores | **S** (project page and abstract); the E2E-Recall 82.08 vs 70.18 figure was not found; no code found | Four ideas carried over (§3) |

**No single published protocol exists for photo culling.** The closest ones are Photo Triage pairwise
accuracy (choosing within a series) and SRCC on AVA-style scores (ranking). Neither measures the cost of
dropping a keeper, which is the mistake the owner cares about most. So the golden set combines the
two protocols and adds cull metrics.

## 3. What VQQA contributes (a still-photo reading)

VQQA optimises video generation, which bioscan does not do. What carries over is how it **evaluates an evaluator**:

| VQQA idea | Here |
|---|---|
| Per-sample questions produce a list of concrete flaws; E2E-Recall = flaws whose question got a low score | **Explanation recall/precision**: when a scorer returns `reasons`, they are checked against the owner's drop reasons on the same frames (§5.7). A reason is useful only if it names the owner's reason |
| Global score vs "average of the QA scores" (their ablation: averaging selects worse) | When a scorer returns sub-scores (`dims`: light, composition, …), the harness also selects by their mean and reports both side by side (§5.8). This tests whether sub-scores should drive selection or only explain it |
| Selection is the task (Best-of-N) | The shot-group winner metric (§5.2) *is* Best-of-N with the owner as the judge |
| Temperature 0, cost counted in VLM calls | Rerun stability (`--repeat`) and ms per frame (`ms` in the scores file) are part of the report |
| Their ground-truth flaws were extracted by one LLM from another LLM's critiques | **Not carried over**: every label here is the owner's. No model output is ever used as a golden label |

## 4. The golden set

A folder the owner builds once and freezes. The photos stay where they are, or are copied in.

### 4.1 Files

`images.csv`, one row per frame (only `path` is required):

| Column | Values | Used for |
|---|---|---|
| `path` | relative to the folder, or absolute | key |
| `split` | `test` (default) or `dev` | tune on dev (blend weight, thresholds), choose on test |
| `trip` | text; default is the first folder under the golden root | cull metrics and NDCG are computed per trip |
| `group` | shot-group id; blank = frame stands alone | choice within a shot |
| `best` | 1 = the owner's winner of its group | group top-1, implied pairs |
| `category` | bird, mammal, other_animal, landscape, people, street, macro, architecture, night, … | per-category scope |
| `stars` | 1–5; −1 = reject; 0 or blank = unrated | ranking (the `bioscan.aesthetic` rating rule) |
| `stars2` | a blind re-rating, at least two weeks later | the owner's own ceiling |
| `keep` | 1 keep, 0 drop, blank unknown | cull metrics, precision@k |
| `reasons` | `;`-list of drop reasons (below) | explanation recall |
| `slices` | `;`-list of free tags, e.g. `style:minimal`, `light:low_key`, `content:east_asian_architecture` | bias residuals |
| `variant_of`, `variant` | planted copies only (§4.3) | invariance and degradation checks |

**Drop reasons** use a fixed vocabulary: the rule reasons (`soft_subject`, `motion_or_defocus`,
`overexposed`, `underexposed`, `subject_cut`, `subject_too_small`, `no_subject`) plus taste reasons
(`composition`, `cluttered_background`, `bad_light`, `eyes_closed`, `pose`, `duplicate`, `other`).
A scorer that explains its choices must map its words onto this list in its adapter.

`pairs.csv` (optional): `a,b,winner` with winner `a`, `b` or `tie`. These are the owner's direct
two-frame choices, typically two compositions of the same subject that are not one burst. Every group
winner also beats each other member of its group (implied pairs). An explicit pair overrides an implied one.

### 4.2 Size and composition (v1 target)

The target sizes come from the width of the intervals the harness prints:

| Part | Target | Resolution it buys |
|---|---|---|
| Rated, keep-labelled frames | 800–1,200 from ≥ 6 trips (≥ 4 test, ≥ 2 dev) | Spearman ±0.07 (n = 800); keepers-lost @20 % ±3 pts |
| Shot groups with a winner | ≥ 150, 2–8 frames each | group top-1 ±8 pts; with 150 groups, a 10-pt gain between two models is usually significant (McNemar) |
| Pairs (implied + explicit) | ≥ 600 | pairwise accuracy ±4 pts; detects a 5-pt change between models |
| Frames per reported category | ≥ 50 | a category result smaller than this is shown but not decided on |
| Frames per slice | ≥ 30 per side of a comparison | residual interval about ±0.1 |
| Re-rated frames (`stars2`) | ≥ 100 (about 10 %), blind, ≥ 2 weeks later | the ceiling: no model is expected to beat the owner's own consistency |
| Planted originals | 60 × 7 kinds | invariance ±5 pts, degradation ±3 pts |

**Coverage.** Wildlife (bird, mammal) is at least 40 % of the set, since that is the album's core.
The rest spreads over landscape, people/street, macro, architecture and night. Every `keep = 0` frame
gets a drop reason. Groups should include the hard cases: the same pose with focus on the eye versus
the body, a tiny head turn, wings up versus down.

**Slices (the bias question).** Tag visual properties, not viewers. Examples: `style:minimal` / `style:busy`
(negative space), `light:high_key` / `light:low_key`, `colour:muted` / `colour:saturated`,
`subject:centred` / `subject:thirds`, `depth:bokeh` / `depth:deep`. Content tags whose models are
reported to lean are also useful: `content:east_asian_architecture`, `content:western_architecture`,
`content:ink_style`. The test is not "is the model Asian-minded". It is **"does the model like these
frames more or less than this owner does"** (§5.6).

### 4.3 Planted copies: answers known without the owner

`scripts/aes_plant.py GOLDEN --n 60` copies 60 rated originals, chosen by seed, into `GOLDEN/planted/`:

| Kind | Expected | Catches |
|---|---|---|
| `rename` (byte copy) | same score | a scorer or cache keyed by path or name |
| `jpeg95` (re-encode, metadata dropped) | same score | reliance on EXIF or encoder details |
| `resize2048` (the service's decode size) | same score | resolution sensitivity |
| `blur`, `ev-2`, `ev+2`, `jpeg10` | lower score | a scorer blind to technical failure |

"Same" means within 5 percentile points of the album's score distribution, a scale-free tolerance that
any model's score can be held to. Planted rows carry no stars, keep or group, so they never enter the
ranking, pair, group or cull metrics.

### 4.4 Rules that keep it honest

- **Freeze.** The report records the sha256 of images.csv + pairs.csv. `compare` refuses two reports
  from different sets (exit 2). A changed set is a new version (`aes-golden-v2`) with new baselines.
- **No training on test trips.** A personal head is fitted on other trips. `bioscan aesthetic eval`
  already flags an in-sample head. Here the owner keeps the golden trips out of `train --ratings`.
- **Split by trip, resample by group.** Near-duplicates move together: cull and NDCG run per trip, and
  the bootstrap resamples shot groups.
- **Labels are the owner's only.** Machine groups from `bioscan cull` (`init --cull`) are a draft to
  correct. No model output is ever written into a golden column.
- **Privacy.** The report lists frame paths. When the photos live outside the golden folder, those paths
  are absolute. Reports and baselines of this tier stay on the owner's machine, like the personal head,
  unless the owner decides otherwise.

### 4.5 The public half: EVA held out (built)

Owner decision (2026-09-24): a second, public golden set from EVA, the one aesthetic dataset whose scores bioscan may
use (CC0). It measures agreement with EVA's raters, not with the owner, so it sits beside the owner's set and never
replaces it. EVA's images are photo-contest entries with little wildlife, so it checks general aesthetics only.

- `data/aesthetic/eva-golden-v1.csv`: 100 images, 20 per star, each with at least 30 votes. Star bands on the mean
  score have gaps between them. Only images whose raters agree better than their band's median are used (data/aesthetic/README.md).
- **Held out before the general head was trained**: `read_eva` skips them in every fit, and the head's provenance
  records the list's sha256. Testing the EVA head on EVA images it was fitted on would read high.
- Build and score it: `scripts/eva_golden.py build --eva ~/.cache/bioscan/eva --out ~/aes-golden-eva`, then
  `bench aesthetic score` as for any golden folder. There are no shot groups, so the pair and group metrics come
  out empty. The four attribute means can check a scorer's sub-scores by hand.
- With 100 images a Spearman near 0.88 is good to about ±0.05 and one near 0.6 to about ±0.13 (bootstrap): enough to
  catch a broken scorer and to separate models 0.1 apart, not two within 0.05 (2026-09-24-aesthetic-arena.md §3).

## 5. The checks (`bioscan bench aesthetic score`)

A missing, failed or non-finite score counts as the **lowest** score everywhere, never as a skip.
This matches eval, where failed images count as misses.

| # | Layer | Metric | Random-order value printed beside it |
|---|---|---|---|
| 5.1 | Service correctness | expected / scored / missing rate / failed / non-finite / not in set / listed twice | — |
| 5.2 | Choosing within a shot | **pairwise accuracy** (Photo Triage protocol; a model tie counts as wrong), **group top-1** (the model's best frame is the owner's winner) | 50 %; mean 1/group size |
| 5.3 | Culling | per trip, drop the lowest 10 / 20 / 30 %: **keepers lost** (kept frames among the dropped ÷ kept frames) and **reject precision** (share of the dropped that the owner dropped); **drop AUC** (P(a dropped frame scores below a kept one)) | q; the trip's drop share; 0.5 |
| 5.4 | Ranking | Spearman (95 % CI), Kendall τ-b, PLCC, NDCG@10 per trip, precision@k against keepers (k = keepers in the trip); reuses `aesbench.set_metrics` | 0 |
| 5.5 | Planted | degradation accuracy (and per kind), invariance rate (and per kind), largest move; rerun stability with `--repeat` (within 1 percentile point) | — |
| 5.6 | Slices and bias | every metric above per `category:*` and `slice:*`, plus the **residual** = mean(score percentile − stars percentile) with a 95 % CI. Above 0 = the model likes this slice more than the owner does; an interval excluding 0 = a systematic lean | 0 |
| 5.7 | Explanations | when the scorer gives `reasons`: recall of the owner's drop reasons and precision of the model's, per reason, on keepers and frames the owner gave reasons for | — |
| 5.8 | Sub-scores | when the scorer gives `dims`: Spearman of each against stars, and pair / group choice by their mean next to the overall score (the VQQA ablation) | — |
| 5.9 | Ceiling and cost | the owner's Spearman between `stars` and `stars2`; median ms per frame from `ms` | — |

**Which metric decides.** For "pick better photos": **group top-1 and pairwise accuracy**.
For "drop weak photos": **keepers lost @20 %**, with reject precision as the benefit it buys.
Spearman and NDCG are context. A model that wins on Spearman but loses keepers does not ship.

## 6. Choosing and upgrading a model

1. **Scores file.** bioscan's head: `bioscan run GOLDEN -r --profile album --json --out runs/aes/eva.ndjson`
   (products.aesthetics.score is read directly). Any other model: an adapter script that writes one
   NDJSON line per frame: `{"path", "score", "model"?, "dims"?, "reasons"?, "ms"?}`.
2. **Score** on `--split test` (and `dev` while tuning), with a second run as `--repeat` for stability.
3. **Table**: `bench aesthetic table runs/aes/*/report.json` puts all candidates side by side.
4. **Compare** the candidate with the current baseline: paired McNemar on pairs and on groups, and a
   bootstrap interval for the Spearman change resampled by shot group. `baselines/budget-aesthetic.toml`
   (provisional) holds the regression limits. Exit 1 = over budget, which needs the owner's acceptance
   (CLAUDE.md).
5. **Ship rule.** A candidate replaces the baseline when it is significantly better on group top-1 or
   pairwise accuracy (McNemar p < 0.05), is not worse on keepers lost @20 % by more than the budget, keeps
   invariance ≥ 95 %, and fits the speed budget. Licence also decides: no licence file = local evaluation only.

Candidates worth a run, from the research: bioscan's EVA head alone and blended with a personal head;
Q-ReAlign 0.8B / 4B; a VLM judge (Qwen3-VL via mlx-vlm, Apache-2.0) scoring shortlisted groups, with
VQQA-style per-frame questions mapped onto the drop-reason list; ArtiMuse / UniPercept only where a
CUDA machine is at hand.

## 7. Not done, on purpose

- **No standards bars yet.** Like §13, bars are set from the first real run: group top-1 against its
  random value, keepers lost @20 % against 20 %. Until then, `bench scorecard` does not read these reports.
- **No public set is wired in.** Photo Triage / BI-SQA pairwise accuracy would be a useful sanity
  check, but their licences are unknown: evaluation only, local only, never committed.
- **No labelling UI.** The owner fills images.csv in a spreadsheet, starting from `init`. A review page
  in the style of `bioscan cull`'s HTML is the obvious next step if labelling is slow.

## Sources

- Q-ReAlign README: https://raw.githubusercontent.com/Q-Future/Q-ReAlign/HEAD/README.md
- ArtiMuse README: https://raw.githubusercontent.com/thunderbolt215/ArtiMuse/HEAD/README.md
- UniPercept README: https://raw.githubusercontent.com/thunderbolt215/UniPercept/HEAD/README.md
- EVA readme and LICENSE: https://raw.githubusercontent.com/kang-gnak/eva-dataset/HEAD/readme.md
- S: AestheticSigLIP https://huggingface.co/somepago/AestheticSigLIP
- S: PAMELA https://arxiv.org/abs/2604.07427
- S: Scientific Reports https://www.nature.com/articles/s41598-026-62173-3
- S: AesBiasBench https://aclanthology.org/2025.emnlp-main.386
- S: VQQA https://arxiv.org/html/2603.12310 ; https://yiwen-song.github.io/vqqa/
- S: Photo Triage https://gfx.cs.princeton.edu/pubs/Chang_2016_ATF/ ; BI-SQA https://arxiv.org/abs/2511.07958
