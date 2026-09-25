# Results

Every number here was measured; the README shows the v1.5 row of each table. `docs/standards.md` says what the numbers are held to.

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

[`docs/standards.md`](standards.md) sets the bars bioscan is measured against, in 11 dimensions
(accuracy per kind, trust, detection, location, a directory-level acceptance test, speed, coverage,
robustness, onboarding, privacy, reproducibility). For each it gives the industry bar with its
source, our community bar, a stretch bar, the current status and how it is measured. It also sets
the release stages: v0.x "try it and help identify", then v1.0 "bundle and release". The bars say
when we invite the community; the v0.x gates are not met yet. `data/standards.toml` holds the same bars for
`bioscan bench scorecard`.

## Album: aesthetics and culling (2026-09-25 UTC, owner's Mac)

- **General aesthetic head** `eva-head-v1:d5985bc9ea9e` (data/aesthetic/README.md): 5-fold CV on the 3,970 EVA training images, SRCC 0.792 (sd 0.013), PLCC 0.806.
- **Held-out EVA golden set** (100 images the head never saw, 20 per star; `bioscan bench aesthetic score`): Spearman 0.877 [0.818, 0.917], Kendall τ-b 0.733, NDCG@10 0.877, drop AUC 0.982, keepers lost at 10/20/30% 0.0% [0.0, 8.8]. Mean score per star, 1 to 5: 0.49, 0.56, 0.62, 0.66, 0.74. The same 100 with the scores shuffled: Spearman −0.10, keepers lost at 20% 25%. Planted copies (30 originals × 6): degradations score lower 93.3% (blur 100%, −2 EV 100%, +2 EV 83%, JPEG q10 90%); renamed and re-encoded copies within 5 percentile points 100% (largest move 4.5).
- **Culling rules**, synthetic reject set (224 frames from the CI photos, `tests/models` album run): reject recall 0.839 [0.776, 0.887], precision 0.959 [0.914, 0.981], keepers lost 0.107 [0.050, 0.215]; per reason: soft 0.861, overexposed 0.958, underexposed 0.333 [0.180, 0.533] (8 of 24; floor 0.80, the one failing bar). On the 100 EVA photos the rules gave 44 a reject reason (overexposed 16, no_subject 15, underexposed 14), including 4 of the 20 five-star ones: unverified against any human judgement.
