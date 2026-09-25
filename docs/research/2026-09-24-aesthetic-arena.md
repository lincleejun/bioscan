# Aesthetic arena: nine scorers on the EVA golden set (2026-09-24)

> Owner's question: not just "what does our head score", but "on the current test set, how do models A, B, C
> rank, and how far is each from the answer key", so every later iteration of our own head has something to
> stand next to. This document is the research and the first run. Numbers marked **V\*** were measured on the
> owner's Mac (M4 Max, MPS) on 2026-09-24; everything else follows the V / S / ✗ convention of
> `2026-09-24-aesthetic-golden-set.md`.

## 1. Result

Test set: `data/aesthetic/eva-golden-v1.csv` built into `~/aes-golden-eva` (100 EVA photos, 20 per star, held out of
the general head). Every model scored the same 100 files; reports come from `bioscan bench aesthetic score`, the
table from `bioscan bench aesthetic table` (bootstrap over frames, seed 0; the numbers below are from the first run with
2,000 draws, the command's default is 1,000). Speeds are per frame on MPS after warm-up,
model only, no decode.

| rank | model | AVA-trained | Spearman vs stars [95% CI] | PLCC vs mean | cross-star pair acc [CI] | star MAE [CI] | exact / ±1 star | ΔSpearman vs top [CI] | s/frame |
|---|---|---|---|---|---|---|---|---|---|
| 1 | eva-head-v1 (ours: SigLIP2 base + ridge) | – | 0.877 [0.820, 0.912] | 0.846 | 90.8% [87.3, 93.7] | 0.46 [0.36, 0.63] | 57% / 97% | – | 0.15 (whole album profile) |
| 2 | Qwen3-VL-4B-Instruct, zero-shot | – | 0.759 [0.663, 0.830] | 0.743 | 83.4% [78.4, 87.8] | 0.76 [0.59, 0.91] | 37% / 87% | −0.118 [−0.207, −0.041] | 0.41 |
| 3 | Qwen3.5-2B, zero-shot | – | 0.732 [0.626, 0.815] | 0.722 | 82.2% [76.9, 87.1] | 0.74 [0.59, 0.93] | 46% / 83% | −0.144 [−0.243, −0.054] | 0.49 |
| 4 | Q-ReAlign Mini 0.8B (aesthetic) | yes | 0.728 [0.617, 0.813] | 0.728 | 82.2% [76.7, 87.2] | 0.72 [0.58, 0.90] | 47% / 85% | −0.149 [−0.249, −0.061] | 0.35 |
| 5 | TOPIQ-IAA (pyiqa) | yes | 0.721 [0.619, 0.804] | 0.728 | 81.5% [76.5, 86.4] | 0.74 [0.61, 0.93] | 41% / 87% | −0.155 [−0.247, −0.076] | 0.026 |
| 6 | LAION aesthetic v2 (pyiqa `laion_aes`) | yes | 0.717 [0.594, 0.801] | 0.700 | 81.2% [75.3, 86.2] | 0.80 [0.63, 0.96] | 39% / 83% | −0.161 [−0.267, −0.075] | 0.029 |
| 7 | NIMA (pyiqa) | yes | 0.713 [0.611, 0.794] | 0.706 | 80.7% [75.5, 85.4] | 0.76 [0.60, 0.94] | 44% / 82% | −0.162 [−0.253, −0.079] | 0.020 |
| 8 | Aesthetic Predictor V2.5 | ✗ unknown | 0.688 [0.578, 0.774] | 0.647 | 79.1% [73.8, 83.9] | 0.86 [0.67, 1.00] | 36% / 80% | −0.189 [−0.285, −0.102] | 0.08 |
| 9 | AestheticSigLIP | – | 0.611 [0.473, 0.724] | 0.654 | 75.3% [69.0, 81.3] | 0.95 [0.76, 1.14] | 31% / 78% | −0.266 [−0.401, −0.146] | 0.036 |

Also measured, not in the table: MUSIQ-AVA 0.666 / 0.688 (SRCC / PLCC), CLIP-IQA+ 0.512 / 0.567 (a technical-quality
metric, not aesthetics), Qwen3.5-0.8B zero-shot 0.302 / 0.271 (unusable). All **V\***.

Reference lines for the same 100 images (computed from `votes_filtered.csv`, **V\***):

| line | Spearman vs stars |
|---|---|
| crowd split-half (random halves of the raters) | 0.923; Spearman-Brown corrected to all votes 0.960 |
| one random rater vs the rest | 0.604 |

Reading it:

- **Our head leads by a wide margin, and the margin is real**: every ΔSpearman interval excludes 0. But the head was
  fitted on the *same raters' labels* (the other 3,970 EVA images), so this is in-distribution agreement. The
  external models never saw EVA's raters. The fair statement is "on EVA's taste, our head is 0.12 Spearman ahead of
  the best general model", not "our head is a better aesthetic model".
- **Ranks 2-7 are one cluster.** The six models sit within 0.05 Spearman of each other; at n = 100 that is noise
  (§3). Only the top, the bottom two, and the gap between them are decided.
- **Zero-shot Qwen3-VL-4B beats every AVA-trained specialist**, with no aesthetic fine-tuning, and it is the only
  external model not exposed to these photos (§2). At 0.41 s/frame it is 15× slower than the CNN/ViT heads.
- **Backbone is not what decides.** AestheticSigLIP shares our SigLIP2 so400m family and comes last: its labels
  came from Gemini Flash, not people. The training labels matter more than the encoder.
- **Deviation in the owner's units**: after quantile calibration our head is off by 0.46 stars on average and never
  by more than one star on 97 of 100 images; the cluster is off by ~0.75 stars and lands within one star on
  82-87 %.

### Where the models disagree most

Calibrated star per model (crowd star, crowd sd, then the models in the table's order):

| image_id | stars | sd | ours | Qwen3-VL-4B | Qwen3.5-2B | Q-ReAlign | TOPIQ | LAION | NIMA | V2.5 | AesSigLIP |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 604125 | 5 | 1.37 | 4 | 4 | 4 | 1 | 2 | 3 | 3 | 4 | 3 |
| 799197 | 2 | 1.91 | 2 | 4 | 4 | 4 | 4 | 5 | 4 | 5 | 5 |
| 461307 | 2 | 1.67 | 4 | 3 | 2 | 5 | 5 | 4 | 5 | 5 | 4 |
| 79720 | 5 | 1.16 | 5 | 5 | 5 | 3 | 4 | 3 | 3 | 3 | 2 |
| 435055 | 1 | 1.96 | 2 | 3 | 3 | 3 | 3 | 2 | 3 | 3 | 4 |
| 95079 | 5 | 1.38 | 4 | 3 | 4 | 2 | 3 | 4 | 2 | 3 | 3 |
| 22402 | 1 | 1.86 | 1 | 1 | 4 | 2 | 1 | 2 | 1 | 2 | 1 |
| 714552 | 5 | 1.40 | 5 | 3 | 4 | 4 | 4 | 2 | 4 | 4 | 3 |
| 678012 | 4 | 1.35 | 4 | 4 | 1 | 4 | 4 | 3 | 4 | 3 | 5 |
| 91666 | 2 | 1.90 | 3 | 3 | 2 | 3 | 4 | 4 | 3 | 4 | 5 |

799197 and 461307 (crowd 2 stars, most models 4-5) are the images to look at by eye: either the crowd's taste
differs from every model's, or the label is the noisy one (sd 1.9 is at the high end of the set). The full list is
`runs/aes/arena-residuals.csv` (local, not committed: `runs/` is ignored).

## 2. Caveats that change how the table reads

| Caveat | Evidence | Consequence |
|---|---|---|
| **AVA leakage.** EVA's photos are AVA photos. 93 of the 100 golden images are in AVA's official *training* split (pyiqa `meta_info_AVADataset.csv`, `official_split`), 7 in test | **V\*** | NIMA, MUSIQ, TOPIQ-IAA, LAION v2 and Q-ReAlign were trained on these very photos with AVA's scores (which correlate with EVA's). Their rows are upper bounds. That they still trail a zero-shot VLM makes the VLM result stronger, not weaker |
| **Gapped star bands inflate Spearman.** Selecting 20 images per band with gaps between bands removes the hard middle | crowd split-half 0.923 on the 100 vs 0.816 on all 4,070 (**V\***) | Do not compare any number here with AVA-split SRCC from papers, nor with the head's CV 0.792 in `data/aesthetic/README.md` |
| **Within a band there is no order.** Split-half Spearman inside a band is 0.26 / −0.46 / −0.69 / −0.61 / 0.02 for stars 1-5 | **V\*** | Stars are the target; Spearman vs the 0-10 mean adds nothing. Residuals inside one band are noise |
| **Cull metrics are saturated here.** Every model has keepers-lost@20 % ≈ 0 and drop AUC ≥ 0.88 | reports | The EVA set cannot rank models on culling; that needs the owner's set with shot groups |
| **n = 100.** | §3 | Differences under ~0.08 Spearman are not decidable |

## 3. Method (why these columns, and what n = 100 can tell)

Sources were checked first-hand (**V**): pyiqa's IAA benchmark table (PLCC/SRCC/KRCC per model, no CI); NIMA's AVA
protocol (LCC, SRCC, two-class accuracy at 5); ITU-T P.1401 (monotone mapping before error metrics, Fisher-z for
comparing correlations, Bonferroni for many comparisons); Q-Bench and AesBench (VLM judges are scored on the same
SRCC/PLCC table, AesBench on three-class accuracy); LMSYS Chatbot Arena (Bradley-Terry over crowd votes, used
because there is no answer key); KonIQ-10k (split-half rater consistency as the ceiling); `cocor` (Williams' t for
dependent overlapping correlations).

**No Elo / Bradley-Terry.** Every frame has a crowd answer, so models are scored against it directly. BT is for the
case with pairwise preferences and no key; it would only re-derive the ranking from known answers. It becomes
relevant only if the owner later runs blind "model A's pick vs model B's pick" votes, and with fewer than five
models a win rate with a Wilson interval does that job too.

**Deviation across score scales.** Models score on different scales (0-1, 1-10, logits). Following P.1401, map
each monotonically to the label scale first. The set has exactly 20 per star, so the map needs no fitted
parameter: the model's lowest 20 are 1 star, ... highest 20 are 5 stars. Then star MAE, exact and ±1 hit rate,
and the 5×5 confusion are in the owner's units. Isotonic regression fitted in-sample on 100 points would read
optimistic and was not used.

**What n = 100 resolves** (bootstrap widths, simulated at SRCC 0.60 / 0.75 / 0.88, **V\***):

| metric | 95 % CI full width | verdict |
|---|---|---|
| Spearman | 0.26 / 0.17 / 0.09 | usable |
| cross-star pair accuracy | 0.12 / 0.09 / 0.06 | most sensitive; it is Spearman restricted to pairs the crowd actually separates |
| star MAE | 0.39 / 0.33 / 0.26 | usable, blunter (discrete) |
| exact star | ~0.2 | context only |
| top-20 / bottom-20 hit rate | ~0.45 | cannot separate models; left out |

Paired comparison: all models share the same bootstrap resamples, and the interval of the difference decides a tie
(`=`). Two models of SRCC 0.88 vs 0.85 are not separable; 0.88 vs 0.80 is borderline; 0.88 vs 0.75 is clear.
**Models within 0.05 Spearman share a rank.** The existing `compare` does this for Spearman between two reports; the
arena does it for N models and for pair accuracy and MAE too.

**Not done, deliberately.** EVA's four attribute means (visual, composition, quality, semantic) correlate 0.82-0.91
with each other and 0.88-0.94 with the total on these 100 images, so they cannot validate a model's sub-scores
(`dims`): a dim that repeats the total would score 0.9 on all four. Skip until a set with independent attributes
exists.

## 4. Candidates: what was checked and what to keep

| model | repo | backbone | weights | code / weight licence | trained on | runs with torch 2.14 + transformers 5.17 | keep? |
|---|---|---|---|---|---|---|---|
| LAION aesthetic v2 | christophschuhmann/improved-aesthetic-predictor; pyiqa `laion_aes` | CLIP ViT-L/14 + MLP | ~0.9 GB | Apache-2.0 / same repo **V** | SAC + logos + **AVA** **V** | yes (needs `setuptools<81` for openai-clip) **V\*** | yes: community default |
| Aesthetic Predictor V2.5 | discus0434/aesthetic-predictor-v2-5 | SigLIP v1 so400m + MLP | 3.5 GB | **AGPL-3.0** **V** | not published ✗ | yes; README hardcodes `.cuda()`, install `--no-deps` **V\*** | no: AGPL, unknown data, duplicates AestheticSigLIP |
| NIMA | pyiqa `nima` | Inception-ResNet-v2 | 218 MB | PolyForm NC / CC-BY-NC-SA **V** | **AVA** | yes **V\*** | optional: cheapest baseline |
| MUSIQ-AVA | pyiqa `musiq-ava` | multi-scale ViT | 109 MB | PolyForm NC / CC-BY-NC-SA | **AVA** | yes **V\*** | no: below NIMA here |
| TOPIQ-IAA | pyiqa `topiq_iaa` | Swin-B + CFANet | 508 MB (+ timm Swin) | PolyForm NC / CC-BY-NC-SA **V** | **AVA** (0.791 on AVA **V**) | yes **V\*** | yes: AVA-specialist SOTA in pyiqa |
| CLIP-IQA+ | pyiqa `clipiqa+` | CLIP RN50 + prompts | – | S-Lab NC / CC-BY-NC-SA **V** | KonIQ (IQA) | yes **V\*** | no: technical quality, 0.51 |
| LIQE | pyiqa `liqe` | CLIP ViT-B/32 | 354 MB | MIT / CC-BY-NC-SA **V** | KonIQ (IQA) | asserts short side ≥ 224; EVA's minimum is 190 **V\*** | no |
| Q-Align / OneAlign | q-future/one-align; pyiqa `qalign` | mPLUG-Owl2 ~8B | **16.4 GB** | repo S-Lab NC vs HF card MIT (conflict) **V** | incl. AVA | **crashes** on transformers 5.17 (`invert_attention_mask`), card pins 4.36.1 **V\*** | no: superseded by Q-ReAlign |
| Q-ReAlign Mini 0.8B | q-future/Q-ReAlign-Mini-0.8B; pyiqa `qrealign` | Qwen3.5 0.8B | 2.2 GB | no LICENSE file, `pyproject` MIT / HF card apache-2.0 **V** | One-Align mix incl. **AVA** | yes, `task_='aesthetic'` **V\*** | yes: smallest tuned VLM; vs zero-shot Qwen3.5-0.8B (0.30) it shows what tuning buys |
| VILA (Google 2023) | google-research/vila | CoCa-style, TensorFlow | TF Hub | Apache-2.0 / ✗ | AVA + comments | TF only, no torch port **V** | no |
| AestheticSigLIP | somepago/AestheticSigLIP | SigLIP2 so400m naflex + tap MLP | 1.7 GB | Apache-2.0 / Apache-2.0 **V** | ~134k web images labelled by Gemini Flash **V** | yes, pure torch **V\*** | yes: same backbone family as ours, machine labels; the control for "backbone vs labels" |
| Qwen3-VL-4B-Instruct zero-shot | Qwen/Qwen3-VL-4B-Instruct | 4B VLM | 8.9 GB | Apache-2.0 **V** | not aesthetic-tuned; pretraining data ✗ | yes **V\*** | yes: best external, clean of AVA scores |
| Qwen3.5-2B / 0.8B zero-shot | Qwen/Qwen3.5-* | 2B / 0.8B | 4.6 / 1.8 GB | Apache-2.0 **V** | same | yes; must pass `enable_thinking=False` **V\*** | 2B optional; 0.8B no |

Corrections to `2026-09-24-culling-aesthetics.md` / the golden-set doc: pyiqa itself is **PolyForm Noncommercial**
and its weight mirror CC-BY-NC-SA (**V**), so it stays a local evaluation tool and is never vendored; Q-ReAlign is
not licence-less, its `pyproject.toml` says MIT and the model card apache-2.0 (**V**), though the repo has no LICENSE file.

**First-round arena set (5 + ours):** Qwen3-VL-4B zero-shot, TOPIQ-IAA, LAION v2, AestheticSigLIP, Q-ReAlign Mini.
Each is there for a different reason (best external; AVA specialist; community default; backbone control; tuned
small VLM), so a change in our head can be read against each axis.

## 5. How to run it again

```sh
# once: an environment for the external models, outside the service's
python3 -m venv ~/.cache/bioscan/arena && ~/.cache/bioscan/arena/bin/pip install \
    torch==2.14.0 transformers==5.17.0 open_clip_torch==3.3.0 pyiqa==0.1.16 "setuptools<81"
# scores: one NDJSON per model; --purge deletes the weights each model downloaded once its scores are written
A=~/.cache/bioscan/arena/bin/python
for m in pyiqa:topiq_iaa pyiqa:laion_aes pyiqa:qrealign aessiglip vlm:Qwen/Qwen3-VL-4B-Instruct; do
  $A scripts/aes_arena_score.py $m ~/aes-golden-eva --out runs/aes/${m//[:\/]/-}.ndjson --purge
done
# ours: the service, album profile
bioscan run ~/aes-golden-eva/images -r --profile album --json --out runs/aes/eva-head-v1.ndjson
# reports, then the arena
for f in runs/aes/*.ndjson; do uv run bioscan bench aesthetic score ~/aes-golden-eva $f --out ${f%.ndjson} --model $(basename ${f%.ndjson}); done
uv run bioscan bench aesthetic table runs/aes/*/report.json --ref data/aesthetic/eva-golden-v1.csv --csv runs/aes/arena-residuals.csv
```

Owner decisions (2026-09-24): the arena lives in `bench aesthetic table` (stdlib, one second for nine models); the
test set is these 100 EVA images; arena models are downloaded when an arena runs and removed afterwards (`--purge`),
only the service's own models (SigLIP2 base, OWLv2, BioCLIP) stay cached. A frame that fails scores `null` and counts
as the lowest score in the report, as everywhere in the harness.

## 6. What this changes for the head's iteration

1. **The number to beat is not 0.877 alone.** Report every new head next to Qwen3-VL-4B (0.759) and TOPIQ (0.721)
   on the same table; if a head drops below the cluster on EVA, something broke.
2. **The EVA set decides ranking, not culling.** Keepers-lost and drop AUC only become informative on the owner's
   set with shot groups (`2026-09-24-aesthetic-golden-set.md` §4).
3. **A cheap ensemble is worth one run**: the disagreement table shows the VLM and our head failing on different
   images. Rank-average of the two is a 10-line experiment, and the arena will say whether it is worth its 0.4 s.
4. **Owner-set arena next**: the same five external models on the owner's rated folder, where the head is *not*
   in-distribution and the AVA leakage does not exist. That is the comparison that says whether our head is good or
   merely EVA-shaped.

