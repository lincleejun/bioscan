# bioscan standards

**Our targets, what the leaders reach, and where we stand today.** Written 2026-09-24. The same bars
are in machine-readable form in [`data/standards.toml`](../data/standards.toml), which
`bioscan bench scorecard` reads.

## The short version

The owner's acceptance test is: **"Point bioscan at a folder of N photos. At least X% are named
correctly at species level, at least Y% at genus or better, and at most Z% are confident errors."**

| Test folder | N | X: species correct | Y: genus or better | Z: confident errors | Today |
|---|---|---|---|---|---|
| Golden iNat, California | 1,625 | **≥ 88%** | **≥ 95%** | **≤ 3%** | v1.5 on the owner's Mac (2026-09-24): X 88.2%, Y 91.0%, Z 3.1%: **not met** (Y short; X and Z miss on the Wilson bound) |
| Owner's own RAW | 404 | **≥ 95%** | **≥ 98%** | **≤ 3%** | v1.5 (2026-09-24), no GPS in the files: X 79.0%, Y 98.0%, Z 12.9%: **not met**; with one batch coordinate an earlier run gave X 96.3% |
| Public, multi-region (to build) | ≥ 5,000 | ≥ 85% | ≥ 93% | ≤ 3% | unmeasured (the set does not exist yet) |
| CI smoke (regression guard only) | 95 (42 birds, 35 mammals, 18 other animals) | ≥ 85% | ≥ 90% | ≤ 7% | X 87.0%, Z 6.5% (on the earlier 77-photo set): guard holds |

These are the **community bars**: meet them and we invite people to try bioscan and help identify
(release v0.x). The v1.0 "bundle and release" stage adds the bars for other animals, speed,
onboarding and the public set. Today neither stage's gates are met; a few single bars are (bird
coverage, list sizes, pinning, isolation). The largest gaps are confident errors
(7.2% against 3%) and mammals (74.1% top-1 against 85%).

Read "Today" with care. The golden numbers are from before the v1.1–v1.4 changes, and two of our own
documents disagree about the bird figure. Every golden number is **to be re-measured on the Mac**.

## How to read this document

Each standard has six parts:

- **Definition:** what is counted.
- **Industry bar:** what the leaders achieve or claim, with a source. *Vendor claim* means the
  company measured itself on a test set it did not publish.
- **Community bar:** the number we must reach before the v0.x community call.
- **Stretch bar:** the number we would call leading.
- **Now:** the latest measured number and where it came from, or *unmeasured*.
- **How measured:** the test folder (tier) and the command.

A bar counts as met only under the [statistical rule](#statistical-rule): the 95% confidence bound
must clear it, not just the observed rate.

## Test folders (tiers)

| Tier | What | N | Lists | Licence | Where it runs | Use |
|---|---|---|---|---|---|---|
| `smoke` | 95 iNat photos (42 birds, 35 mammals, 18 other animals; 83 species), `tests/models/sample.csv` | 95 | reduced: AviList rows of the sampled genera + 41 mammals | CC BY-NC / CC BY / CC0, fetched at test time | CI `models.yml`, CPU, every push | regression guard |
| `golden` | iNat research-grade, California, 65 species × 25 (42 bird, 23 mammal), real GPS and dates, `data/inat/groundtruth-inat.csv` | 1,625 | full AviList 2025 + MDD v2.5 | 89% CC BY-NC, 8% CC BY, 3% CC0; for evaluation only, not redistributed | owner's Mac | main accuracy claim |
| `golden` other-animal slice | **to build**: iNat California, about 16 non-bird, non-mammal species × 25 (reptiles, amphibians, insects, spiders), with `bioscan gt inat` | ~400 | TreeOfLife-wide list (work package W4) | as golden | owner's Mac | all-taxa default |
| `own` | owner's telephoto RAW, folder-name truth, `data/groundtruth-own.csv` (288 Western Screech-Owl, 85 Red-tailed Hawk, 31 Steller's Jay) | 404 | full lists | private | owner's Mac | the real use case |
| `public` | **to build**: ≥ 5 regions, CC0/CC BY only, one observation per photo, ≤ 5 per observer, sequestered test split | ≥ 5,000 | full lists | CC0 / CC BY, redistributable | owner's Mac; published | claim we can show others |
| `mac` | 2,000 RAW files, ARW + CR3 + NEF, 24 MP (plus 45 MP), from SSD and USB disk | 2,000 | full lists | private | M1-class Mac | speed |
| `aesthetic-own` | the owner's rated frames (Lightroom stars / picks from XMP or a CSV), split by trip folder; profile `album` | the owner's (the learning curve needs 1,000+) | none | private | owner's Mac, service running | aesthetic ranking ([section 13](#13-aesthetics-agreement-with-the-owner-album)) |
| `aesthetic-golden` | aes-golden-v1 = the 100 held-out EVA images, 20 per crowd star band (`data/aesthetic/eva-golden-v1.csv`); profile `album` | 100 (40 keepers, no shot groups) | none | CC0 (EVA) | anywhere with an EVA checkout; `bench aesthetic score` needs no model | any aesthetic scorer against crowd stars ([section 13](#13-aesthetics-agreement-with-the-owner-album)) |
| `geotag` | synthetic GPX tracks through the golden photos' true positions, 7 scenarios (`scripts/geotag_synth.py`, seed 7) | 1,624 photos × 7 | none (no models) | derived from golden; tracks are generated, not shipped | anywhere: about 65 s to generate (~750 MB) and 80–85 s to score (measured 64 s + 82–85 s) | GPX geotagging ([section 12](#12-geotag-from-a-gpx-track-synthetic-tier)) |
| `album` | synthetic reject set from 24 smoke photos (`scripts/cull_synth.py`, seed 7): originals, 7 degradations each, bursts; profile `album` | about 220 | none needed (species off) | derived from smoke; generated at test time, not shipped | CI `models.yml`, CPU | culling rules and reducers ([section 14](#14-culling-album-profile-synthetic-tier)) |

The golden set is 89% CC BY-NC. That is fine for measuring but not for publishing the photos. This is
why the public tier is CC0/CC BY only.

## Statistical rule

**A bar counts as met when the Wilson 95% bound clears it.** For a "≥" bar the lower bound must be
at or above the bar. For a "≤" bar the upper bound must be at or below it. Precision uses the
species-level images as its n, and every other rate uses all images of the row. Speeds are medians
over at least 500 images, with no interval. The smoke tier is exempt: its bars are regression guards
judged on the observed value, because 95 images cannot prove any accuracy bar. (With n = 42, even
42/42 correct has a lower bound of only 0.92.)

**Images needed** to prove a bar when the true rate beats it by a margin:

| Bar (≥) | true = bar + 1 pt | + 2 pts | + 3 pts |
|---|---|---|---|
| 85% | 4,825 | 1,188 | 520 |
| 88% | 3,959 | 974 | 427 |
| 90% | 3,361 | 830 | 362 |
| 95% | 1,736 | 414 | 173 |
| 97% | 1,024 | 239 | 125 |

| Bar (≤) | true = bar − 0.5 pt | − 1 pt | − 2 pts |
|---|---|---|---|
| 3% | 4,298 | 1,024 | 239 |
| 5% | 7,121 | 1,736 | 414 |

**Observed rate needed** on the folders we have:

| n | to clear ≥ 85% | ≥ 88% | ≥ 90% | ≥ 95% | to clear ≤ 3% |
|---|---|---|---|---|---|
| 404 (own) | 88.6% | 91.3% | 93.1% | 97.3% | 1.2% |
| 575 (golden mammals) | 88.0% | 90.8% | 92.5% | 96.9% | 1.6% |
| 1,050 (golden birds) | 87.2% | 90.0% | 91.9% | 96.4% | 1.9% |
| 1,625 (golden all) | 86.8% | 89.6% | 91.5% | 96.1% | 2.2% |
| 5,000 (public) | 86.0% | 88.9% | 90.8% | 95.6% | 2.5% |

So the golden set is big enough for every bar, as long as the true rate beats the bar by 2–3 points.
The own set, at 404 images across 3 species, is too small to prove 95%. It needs about 1,000 images
and more species before v1.0.

**Caveat.** Wilson assumes independent images. The golden set has 25 photos per species, and the own
set has bursts of the same bird, so the true uncertainty is larger. Per-species caps (25 per species,
at most 5 per observer in the public set, one frame per burst in `own`) keep this in check. The
harness should also report per-species results so that one easy species cannot carry the mean.

## Metric definitions

These are the report keys in `data/standards.toml`, one row per (tier, kind). "Kind" is the truth
kind: `bird`, `mammal`, `other` (other animals) or `all`. The best box is the box with the highest
`score`, as in `bioscan eval`.

| Key | Definition |
|---|---|
| `n` | images in the row |
| `gate_acc` | the whole-frame gate class equals the truth kind |
| `detect_rate` | at least one box of the truth kind |
| `no_box_rate` | no box at all (split by gate class in the report: `none/person` means a gate miss) |
| `top1` | the best box's first candidate is the truth species (after synonyms). This is X of the directory test. |
| `top5` | the truth is among the best box's first five candidates |
| `genus_acc` | the genus of the best box's first candidate equals the truth genus, whatever level was output. This is Y. |
| `coverage` | the best box is graded at level species |
| `precision` | `top1` among the species-level images |
| `confident_error_rate` | species-level and wrong ÷ n (= coverage × (1 − precision)). This is Z. |
| `ece` | expected calibration error: 10 equal-width bins of the best box's species confidence `p_correct` against `top1` correctness; n/a until the service emits `p_correct` (the posterior is not used) |
| `failed_rate` | images with an error or no result ÷ n |
| `decode_ms_median`, `identify_ms_median` | median `timing_ms` per image |
| `images_per_s` | ok images ÷ wall seconds of the run (`done.elapsed_ms`) |

Failed images count as misses on every accuracy metric.

## Commands

The commands assume the W1 harness: `bioscan bench run GT.csv --out DIR [--no-geo] [--lat --lon]`
writes `report.json`, and `bioscan bench scorecard` compares it with `data/standards.toml`. Until
W1 lands, `bioscan eval GT.csv --out DIR` gives the same counts for `n`, `gate_acc`, `detect_rate`,
`top1`, `top5`, `coverage`, `precision` and the timings. `genus_acc`, `confident_error_rate`,
`no_box_rate` (as a rate), `ece` and `images_per_s` need W1.

```sh
bioscan bench run data/inat/groundtruth-inat.csv --out runs/<tag>-golden                  # golden, real GPS
bioscan bench run data/inat/groundtruth-inat.csv --out runs/<tag>-golden-nogeo --no-geo   # location-aware
bioscan bench run data/groundtruth-own.csv --out runs/<tag>-own --lat 37.4 --lon -122.1    # own RAW
bioscan bench run data/groundtruth-own.csv --out runs/<tag>-own-nogeo --no-geo
BIOSCAN_MODEL_TESTS=1 uv run pytest tests/models                                          # smoke (CI models.yml)
bioscan bench scorecard runs/<tag>-golden                                                 # bars vs report
```

## 1. Accuracy per kind (golden tier, with GPS)

| Standard | Industry bar | Community | Stretch | Now | Why this bar |
|---|---|---|---|---|---|
| Birds top-1 | 95%, Merlin average (vendor claim) [^merlin]; ~92% academic closed-set SOTA (TransFG: CUB 91.7%, NABirds 90.8%) [^transfg] | **90%** | 95% | 89.8% (README golden) or 85.7% → 91.1% after synonyms (baseline doc): **disputed, to be re-measured on the Mac**; smoke 88.1% | the academic level, reached on an open 11,131-species list instead of 200–555 classes |
| Birds top-5 | 96%, Dongniao (vendor claim) [^dongniao] | **95%** | 98% | 95.0% (README golden); smoke 92.9% | at the vendor claim's level; top-5 is what a person picks from during review |
| Birds genus | none published | **95%** | 98% | unmeasured | a genus answer must be nearly always right to be worth keeping |
| Mammals top-1 | 88.7%, iNat CV 2.20 average over all taxa (vendor claim) [^inat220] | **85%** | 92% | 74.1% (README golden, before the v1.1 mammal fixes); smoke 85.7% | phase-1 gate of the strategy doc; mammals still have no location prior, so we sit just under iNat's all-taxa average |
| Mammals top-5 | none | **93%** | 97% | 81.4% (README golden); smoke 91.4% | top-5 misses are mostly no-box, which detection fixes |
| Mammals genus | none | **92%** | 97% | unmeasured | bear and cat confusions stay within a genus, so genus should run 5–7 points above top-1 |
| Other animals top-1 | 88.7%, iNat (all taxa, vendor claim) [^inat220] | **60%** | 80% | unmeasured (W4 built the list; CI smoke ranks 18 photos against it, golden slice still to build) | zero-shot over about 10⁵ names with no location prior; below 60%, the default output would mislead more often than help |
| Other animals top-5 | none | **80%** | 92% | unmeasured | enough for "help identify": the right answer is usually on the short list |
| Other animals genus | none | **75%** | 90% | unmeasured | the level most other-animal answers should stop at |

How measured: golden tier (other animals: the golden other-animal slice), with GPS, `bioscan bench run
data/inat/groundtruth-inat.csv --out runs/<tag>-golden`, rows `bird`, `mammal`, `other`.

The industry numbers are not like-for-like. Merlin, iNat and Dongniao measure on their own
unpublished test sets. CUB/NABirds are curated closed sets, and SpeciesNet works on camera traps.
They show the level people expect, not a result on our folders.

## 2. Trust

| Standard | Industry bar | Community | Stretch | Now | Why this bar |
|---|---|---|---|---|---|
| Birds confident-error rate (species-level and wrong ÷ n) | none published | **≤ 3%** | ≤ 1% | 6.3% (README golden, = 95.6% × (1 − 93.4%)); smoke 7.1% (3/42) | 30k photos at 6% means about 1,900 wrong species keywords to find by hand; at 3% the review is manageable |
| Mammals confident-error rate | none | **≤ 3%** | ≤ 1% | 8.9% (README golden); smoke 5.7% (2/35) | same promise for every kind |
| Other animals confident-error rate | none | **≤ 5%** | ≤ 2% | unmeasured | all taxa is the default, so other animals must not undo the trust built on birds; allows a little more without a prior |
| Birds precision at species level | 94.5%, SpeciesNet when it makes a species-level call, camera traps (vendor claim) [^speciesnet] | **97%** | 99% | 93.4% (README golden); smoke 92.5% | a species-level grade must mean "safe to keep"; with coverage ≥ 85% this keeps Z under 3% |
| Birds coverage (graded species-level) | not published (SpeciesNet rolls up to genus/family when unsure [^speciesnet-pypi]) | **≥ 85%** | ≥ 92% | 95.6% (README golden); smoke 95.2% | stops us buying precision by refusing to answer; the strategy doc's "≥ 85% coverage" |
| Mammals precision at species level | 94.5%, SpeciesNet (vendor claim) [^speciesnet] | **97%** | 99% | 89.2% (README golden); smoke 93.8% | as for birds |
| Mammals coverage | not published | **≥ 80%** | ≥ 90% | 82.3% (README golden); smoke 91.4% | 5 points below birds until mammals have a location prior |
| Other animals precision | none | **95%** | 98% | unmeasured | species-level other-animal answers must be rare and right |
| Other animals coverage | none | **≥ 40%** | ≥ 70% | unmeasured | most other-animal answers should stop at genus or family |
| Calibration (ECE, all kinds) | none published | **≤ 0.05** | ≤ 0.02 | unmeasured (no calibrated `p_correct` yet) | a "90%" answer should be right 85–95% of the time; this gates v1.0 only |

How measured: golden tier, `bioscan bench run data/inat/groundtruth-inat.csv --out runs/<tag>-golden`.
Confident-error rate and precision must always be read together with coverage.

For context, general vision-language models identify fewer than 13% of the answerable birds in the
RealBirdID benchmark (GPT-5 and Gemini 2.5 Pro included), and they abstain badly [^realbirdid]. The
trust bars are where a specialised local tool can lead.

## 3. Detection

| Standard | Industry bar | Community | Stretch | Now | Why this bar |
|---|---|---|---|---|---|
| Birds gate accuracy | none published for a bird/mammal gate | **97%** | 99% | 97.0% (README golden); smoke 97.6% | the gate picks the vocabulary and list, so its errors spread downstream |
| Birds detect rate | 99.4%, SpeciesNet animal vs blank (vendor claim) [^speciesnet] | **97%** | 99% | 97.0% (README golden); smoke 95.2% | our photos are framed by a photographer, so few should be missed |
| Birds no-box rate | none | **≤ 2%** | ≤ 1% | 1.8% (19/1,050, baseline doc) | every no-box image is a certain miss |
| Mammals gate accuracy | none | **93%** | 97% | 89.7% (README golden); smoke 91.4% | 4 points under birds, because owl/mammal and seal/bird confusions are known |
| Mammals detect rate | 99.4%, SpeciesNet (vendor claim) [^speciesnet] | **95%** | 98% | 84.9% (README golden, before the v1.1 vocabulary and gate rescue); smoke 91.4% | closes the known no-box gap (bears, mountain lions, bobcats) |
| Mammals no-box rate | none | **≤ 3%** | ≤ 1% | 7.8% (45/575, baseline doc, before v1.1) | the largest known weakness |
| Other animals detect rate | 99.4%, SpeciesNet (vendor claim) [^speciesnet] | **85%** | 95% | unmeasured | small insects and spiders are hard for an open-vocabulary detector |

How measured: golden tier, as above.

## 4. Location-aware accuracy

| Standard | Industry bar | Community | Stretch | Now | Why this bar |
|---|---|---|---|---|---|
| Birds top-1 without coordinates | ~75%, iNat vision alone (derived: geomodel +12 points → 87%, all taxa, vendor claim) [^inatgeo] | **85%** | 90% | 83.3% (baseline doc `inat-nogeo`, before synonyms) | a folder without GPS must still be usable |
| Mammals top-1 without coordinates | none | **80%** | 88% | 74.1% (no mammal prior yet, so with = without) | mammals lean less on range than look-alike birds |
| Own RAW top-1 without a coordinate | none | **85%** | 92% | 80.2% (baseline doc; screech-owls tell apart only by range) | the owner's cameras have no GPS |
| Birds gain from coordinates (golden, same build) | +12 points, iNat geomodel (vendor claim) [^inatgeo] | **≥ +3 pts** | ≥ +6 pts | **+5.2** (91.2 vs 86.0, same build 2a9b771, 2026-09-25, results.md; the earlier +2.4 / +6.5 figures came from different builds) | proves the location prior works; a manual check from two reports |

How measured: the same tier run twice, with and without `--no-geo`. The gain is the top-1 difference
(`metric = "manual"`).

## 5. Directory-level acceptance test

The owner's test, per tier (see [the short version](#the-short-version)):

| Tier | Id prefix | X top-1 (community / stretch) | Y genus (c / s) | Z confident errors (c / s) | Now |
|---|---|---|---|---|---|
| smoke, 95 | `directory.smoke.all.*` | 85% / 90% | 90% / 95% | ≤ 7% / ≤ 3% | X 87.0% (67/77), Z 6.5% (5/77) on bbd6377, the earlier 77-photo set (CI, CPU) |
| golden, 1,625 | `directory.golden.all.*` | 88% / 93% (industry 88.7% iNat [^inat220]) | 95% / 98% | ≤ 3% / ≤ 1% | X 84.2% (Wilson 82.4–85.9), Z 7.2% (6.0–8.6), from README golden numbers |
| own RAW, 404 | `directory.own.all.*` | 95% / 98% (WildlifeAI claims 97.3% on its own set [^wildlifeai]) | 98% / 99% | ≤ 3% / ≤ 1% | X 96.3% (Wilson 94.0–97.7), Z 2.2% (1.2–4.2), with an assumed batch coordinate, 3 species only |
| public, ≥ 5,000 | `directory.public.all.*` | 85% / 92% (industry 88.7% iNat [^inat220]) | 93% / 97% | ≤ 3% / ≤ 1% | unmeasured: set to build |

Why these numbers:

- **Golden X = 88%** is the size-weighted mix of the bird (90%) and mammal (85%) bars.
- **Own X = 95%** is higher because the photos are the owner's own, taken with long lenses in one
  region.
- **Public X = 85%** is lower because multi-region photos and other animals are harder.
- **Z = 3% everywhere** because trust is the promise, whatever the folder.
- **The smoke bars sit at today's level:** they catch breakage. `bioscan bench compare` against the
  committed baseline is the finer regression check.

The public set also has a size bar: **n ≥ 5,000** (`directory.public.all.n`), so that each region
group can prove an 85% bar with a 3-point margin.

## 6. Speed (Apple Silicon)

| Standard | Industry bar | Community | Stretch | Now | Why this bar |
|---|---|---|---|---|---|
| Throughput, 24 MP RAW, identify on | ~3.7 images/s, Excire Foto (user report of 10,000 photos in ~45 min; generic keywords, not species; **unverified**, search snippet) [^excire] | **≥ 1.05 images/s** | ≥ 3.0 images/s | unmeasured. Parts: ~650 ms decode + ~210 ms identify per image on an M-series Mac before v1.1 (README, unverified since); decode runs in 4 parallel workers, so 1.2–4 images/s is plausible | 30,000 photos in one 8-hour night = 1.04 images/s |
| Identify, median | none | **≤ 250 ms** | ≤ 120 ms | ~210 ms (README, before v1.1; the detail copy and batching since are unmeasured on MPS); CPU in CI 11.9 s bird / 12.8 s mammal (bbd6377) | keeps the model turn from becoming the bottleneck behind 4 decode workers |
| Decode, median | none | **≤ 700 ms** | ≤ 300 ms | ~650 ms from a USB disk (README, before v1.1; the 3072 px detail copy since is unmeasured) | with 4 workers, 700 ms still feeds more than 5 images/s; the stretch needs the embedded preview |

How measured: `mac` tier, 2,000 RAW files, 24 MP (plus 45 MP), from SSD and USB disk, on an M1-class
Mac: `bioscan bench run <gt> --out runs/<tag>-speed`. Throughput = ok ÷ (`done.elapsed_ms` / 1000).
Report the machine, the disk and the settings fingerprint with it.

## 7. Taxonomic coverage (manual)

| Standard | Industry bar | Community | Stretch | Now | Why this bar |
|---|---|---|---|---|---|
| Bird species in the name list | 11,000+, Dongniao (vendor claim) [^dongniao-api] | **11,131** (all of AviList 2025) | — | 11,131 (`bioscan names stats`; data/README) | the whole world list, extinct species included |
| Mammal species in the name list | ~2,000 categories, SpeciesNet, all taxa (vendor claim) [^speciesnet] | **6,904** (all of MDD v2.5) | — | 6,904 | the whole world list |
| Other animal species with a name | 100,000+ taxa, iNat CV 2.20, all kingdoms (vendor claim) [^inat220] | **100,000** | 300,000 | 366,460 (CI 2026-09); TreeOfLife-200M has no vectors for many reptiles and ray-finned fish (smoke: 0 of 6 reptiles, 0 of 1 fish) | all taxa by default has to mean most animals people photograph |
| Share of truth species in the lists | none | **100%** | 100% | golden birds 42/42 (checked against `avilist_map.csv` + synonyms, 2026-09-24); golden mammals 23/23 as scored by `bioscan eval` (MDD CSV not in the repo, not re-checked); own 3/3 | a species missing from the list can never be named, so it would make the accuracy numbers meaningless |

Name-list coverage by TreeOfLife vectors and BirdNET labels (84.6% / 93.3% of AviList; 55.5% of MDD
by vectors) is a quality signal, not a bar. The missing rows are encoded with the text tower.

## 8. Input robustness

| Standard | Industry bar | Community | Stretch | Now | Why this bar |
|---|---|---|---|---|---|
| Failed images (report `failed_rate`) | none | **≤ 0.1%** | 0 | unmeasured as a rate (the golden report has a `failed` column; its value is not recorded in the README) | one failure per 1,000 photos is the most a 30k archive should see |
| RAW formats with GPS and capture time read (manual) | Lightroom and exiftool read every mainstream body | **11 of 11** (ARW CR2 CR3 NEF NRW DNG RAF ORF RW2 PEF SRW) | 12 (+ HEIF) | 7 of 11 by reading the code (`decode.read_exif` reads TIFF-based RAW only; CR3, RAF, ORF and RW2 get no GPS or time); not tested on real files; W5 fixes it | missing GPS silently turns off the location prior, which costs birds 6+ points |
| Per-image error isolation (manual) | a baseline expectation | **yes** | yes | yes: contract tests cover per-image errors and the decode-pool rebuild (v1.2 D) | one bad file must not cost a night's run |

## 9. Onboarding (manual)

| Standard | Industry bar | Community | Stretch | Now | Why this bar |
|---|---|---|---|---|---|
| Fresh Mac to first identified photo, README only, downloads included | Merlin and iNat install from an app store in minutes (no published number) | **≤ 30 min** | ≤ 10 min | unmeasured. Today it needs `git`, `uv`, hand-converting an XLSX to CSV, a 3.26 GB TreeOfLife file and about 7 GB of weights: realistically hours for a non-developer | a volunteer gives up after half an hour; gates v1.0, and v0.x only needs it measured |

How measured: someone who has not installed bioscan before, timed, on a 100 Mbit/s line. The time goes
in the release report.

## 10. Privacy (manual)

| Standard | Industry bar | Community | Stretch | Now | Why this bar |
|---|---|---|---|---|---|
| Network requests carrying photos or their metadata during a default run | Nomen: "everything runs on your machine" (vendor claim) [^nomen] | **0** | 0 | 0 by design: the service runs with `HF_HUB_OFFLINE=1`, and no code path uploads; **not enforced by a test** | photos never leave the machine, and this is a promise we can check |

How measured: a golden-tier run with outbound network blocked, or a proxy log, counting requests.
Opt-in features that fetch data (for example `bioscan gt inat`) are outside a default run.

## 11. Reproducibility (manual)

| Standard | Industry bar | Community | Stretch | Now | Why this bar |
|---|---|---|---|---|---|
| Models, lists and prior pinned; revisions and settings fingerprint in each result | none of the competitors publishes this | **yes** | yes | yes: SigLIP2, OWLv2, BioCLIP and TreeOfLife revisions are pinned, and `result.engine` carries the models, name-list versions, settings fingerprint and detail edge (v1.2 A) | a number is only worth something if someone else can get it again |
| A published bench report for every release tag | none of the competitors publishes one [^strategy] | **yes** | yes | no (the W1 harness publishes on tag runs) | the community call rests on numbers anyone can check |

## 12. Geotag from a GPX track (synthetic tier)

The `geotag` tier is not a photo folder. `scripts/geotag_synth.py` (seed 7) builds GPX tracks through the golden set's
true positions and capture times in seven scenarios: perfect, camera 37 s fast, camera 1 h fast (DST), wrong
timezone, dropouts, photos outside the track, and several files. `bioscan bench geotag` then scores
`bioscan.geotag` against the truth (docs/harness.md). Scope `all` pools the scenarios. Errors count only the photos
whose true time is inside the track; a photo there without a fix is a miss.

| Standard | Industry bar | Community | Stretch | Now (2026-09-24, seed 7) | Why this bar |
|---|---|---|---|---|---|
| Median position error | none found for track geotagging; the synthetic GPS noise (σ 3–10 m per axis) is the floor | **≤ 15 m** | ≤ 10 m | 7.2 m | a site-level caption; far inside the prior's cell |
| 90th-percentile position error | none | **≤ 100 m** | ≤ 30 m | 17 m | a tenth of the prior's 0.01° cell |
| Placed within 100 m | none | **≥ 95%** | ≥ 98% | 97.2% [96.9, 97.5] | trail- or site-level captions |
| Placed within 1 km | none | **≥ 98%** | ≥ 99.5% | 99.8% | the location prior sees the right place |
| No fix inside the track | none | **≤ 5%** | ≤ 1% | 0.1% | a track should place what it covers |
| Fix outside the track | none | **≤ 2%** | ≤ 0.5% | 0.0% | a wrong place is worse than none for the prior |
| Fix in a different prior cell than the truth | none | **≤ 5%** | ≤ 2% | 2.0% | the BirdNET prior is looked up at 2-decimal lat/lon |
| Recovered clock-offset error (median) | none | **≤ 10 s** | ≤ 2 s | 1.0 s | 10 s is about 14 m at walking pace |

How measured:
```sh
uv run python scripts/geotag_synth.py data/inat/groundtruth-inat.csv --out runs/geotag-synth --seed 7
bioscan bench geotag runs/geotag-synth        # prints the tier's scorecard; exit 1 when a bar is missed
```
The numbers are synthetic: they show that the method works under the modelled noise, gaps and clock errors, not
how a real watch behaves under trees or in canyons. The effect on species ID needs the Mac runs in
docs/harness.md ("Downstream"); until then it is **unverified**.

## 13. Aesthetics: agreement with the owner (album)

The `aesthetic-own` tier (profile `album`) is the owner's own rated frames: Lightroom stars from XMP
(or a CSV), split into trips by folder. `bioscan aesthetic eval` scores the **served** aesthetic score
(the blend of the general and a personal head, else the general head) against them and writes a
`bioscan-aesthetic-report` that `bioscan bench scorecard` reads (docs/harness.md). This measures
**agreement with one photographer**, not "aesthetic accuracy". A personal head must be judged on trips
it was not fitted on: the report flags an in-sample head, and the learning curve always holds trips out.
Aesthetics only reorders frames, so none of these bars can cost a keeper; the keeper-loss bars belong
to the rule stages ([section 14](#14-culling-album-profile-synthetic-tier)).

| Standard | Industry bar | Community | Stretch | Now | Why this bar |
|---|---|---|---|---|---|
| Spearman ρ, score vs stars (all frames) | none for one owner's stars; generic heads reach 0.67–0.82 on AVA's own test split [^pyiqa-iaa], a different task | **≥ 0.30** (lower 95% bound) | ≥ 0.50 | unmeasured | a clear, if modest, rank agreement: enough to put better frames first |
| Kendall τ-b | none | **≥ 0.20** | ≥ 0.35 | unmeasured | τ-b runs at about two thirds of ρ on the same ranking |
| NDCG@10 per trip (gain 2^stars − 1) | none | **≥ 0.70** | ≥ 0.85 | unmeasured | provisional: random order already scores well above 0; to be set against the random-order value of the first real eval |
| Precision@k per trip vs picks (k = the owner's picks there) | none published (Lightroom Assisted Culling, Aftershoot) | **≥ 30%** | ≥ 50% | unmeasured | the report prints the random-order share beside it (about the pick share, often 10–20%) |

How measured (service running):
```sh
bioscan aesthetic train --ratings ~/Pictures/Album/train-trips --embeddings runs/aes-vec.ndjson   # optional personal head
bioscan aesthetic eval ~/Pictures/Album/test-trips --out runs/<tag>-aesthetic --embeddings runs/aes-vec.ndjson \
    --personal ~/.config/bioscan/aesthetic-personal.json
bioscan bench scorecard runs/<tag>-aesthetic/report.json
```
Every bar here is a first guess: no aesthetic number has been measured on the owner's photos yet. The general
head is committed (data/aesthetic/README.md: CV SRCC 0.792 on EVA, Spearman 0.877 on the 100 held-out EVA images).

### Public golden set: EVA-100 (`aesthetic-golden`)

The `aesthetic-golden` tier (profile `album`) is aes-golden-v1: the 100 EVA images held out of the general head,
frozen (owner decision 2026-09-25). `bioscan bench aesthetic score` scores **any** scorer's scores file on it and
`bioscan bench scorecard` reads the report. The bars are the first real run, eva-head-v1
(`baselines/aes-golden-v1-eva-head-v1.json`, 2026-09-25, owner's Mac), minus a margin, judged on the 95 % bound.

**What EVA's stars can and cannot stand in for.** They are the mean of 30-46 crowd votes on photo-contest
entries with little wildlife, banded into five star levels with gaps between the bands. They can show that a
scorer works and is not generic: it orders clearly better and clearly worse photos the way a crowd does, and it
would not throw a well-liked photo into the bottom fifth. They cannot show agreement with the owner's taste, choice
within a burst (EVA-100 has no shot groups), or culling of real wildlife frames; the band gaps also make the set
easier than a real album. eva-head-v1 was fitted on the rest of EVA, so it is in-domain here and off-the-shelf
scorers start behind it. The owner's set (above) remains the one that decides.

| Standard | Industry bar | Community | Stretch | Now (eva-head-v1) | Why this bar |
|---|---|---|---|---|---|
| Spearman ρ, score vs crowd stars (100 frames) | none on these images; best off-the-shelf scorer in the arena 0.759 [0.670, 0.827] (Qwen3-VL-4B, docs/research/2026-09-24-aesthetic-arena.md) | **≥ 0.70** (lower 95% bound) | ≥ 0.80 | 0.877 [0.818, 0.917]: **met**, stretch met | needs a point value of about 0.79: above every off-the-shelf scorer we ran, 0.12 below today's lower bound |
| Precision@k vs 4-5 star frames (k = 40) | none | **≥ 55%** (lower bound) | ≥ 65% | 82.5% [68.0, 91.3]: **met**, stretch met | random order scores 40% on this frozen set; 55% needs 28 of 40, five picks fewer than today |
| Keepers lost when the lowest 20% are dropped (40 keepers) | none | **≤ 15%** (upper bound) | ≤ 10% | 0.0% [0.0, 8.8]: **met**, stretch met | random order loses 20%; the bar allows one keeper of 40 lost (upper bound 12.9%), not two (16.5%) |
| Shot-group winner is the top-scored frame | none | **≥ 50%** (lower bound) | ≥ 70% | n/a: EVA-100 has no shot groups | only a set with groups is judged; the lower bound keeps a handful of groups from passing by luck; random is 1/group size |

How measured: data/aesthetic/README.md ("aes-golden-v1"). `bioscan bench aesthetic compare
baselines/aes-golden-v1-eva-head-v1.json NEW` holds a new scorer or head to `baselines/budget-aesthetic.toml`.

[^pyiqa-iaa]: pyiqa's aesthetics benchmark (LAION v2 0.665, TOPIQ-IAA 0.791, Q-Align 0.822 SRCC on AVA): https://raw.githubusercontent.com/chaofengc/IQA-PyTorch/main/tests/IAA_benchmark_results.csv

## 14. Culling: album profile (synthetic tier)

The `album` tier scores the album profile's rules and reducers, not species: `quality` reject reasons, `burst`
grouping and `scene` labels, from report.json's `plugin_metrics` (docs/harness.md, "Album tier"). Its standards carry
`profile = "album"` and `plugin`, and are judged on the observed value, as a regression tier like smoke. The set is
synthetic (`scripts/cull_synth.py` over the CI smoke photos): the degradations are cleaner than real blur or bad
exposure, so a pass shows the rules see what they are built for, not how they do on a real album. No culling tool
(Lightroom Assisted Culling, Aftershoot, Narrative Select, FilterPixel, Excire) publishes reject precision, recall or
keepers lost, so there is no industry bar. The community bars are ours; keepers lost is the one that matters most:
losing a keeper costs more than reviewing a reject.

| Standard | Industry bar | Community | Stretch | Now | Why this bar |
|---|---|---|---|---|---|
| Keep-labelled photos a rule rejects | none published | **≤ 5%** | ≤ 1% | 10.7% [5.0, 21.5] (6 of 56; CI run 36099637018, 2026-09-25) **FAIL** | rejects must be conservative |
| Degraded photos rejected, any reason | none published | **≥ 70%** | ≥ 90% | 92.9% [87.9, 95.9] (CI 2026-09-25) | most technical failures caught before review |
| Rejected photos that are degraded | none published | **≥ 80%** | ≥ 95% | 96.3% [92.2, 98.3] (CI 2026-09-25) | a reject list worth trusting |
| Soft subjects and shaken frames rejected as soft | none published | **≥ 70%** | ≥ 90% | 86.1% [76.3, 92.3] (CI 2026-09-25) | subject focus is what wildlife cullers sell |
| +2 EV rejected as overexposed; −2 EV as underexposed | none published | **≥ 80%** each | ≥ 95% | 95.8% / 95.8% [79.8, 99.3] (CI 2026-09-25; −2 EV was 33.3% before the no-highlights rule) | two stops is a clear miss |
| Crops cutting 40% of the subject rejected as cut | none published | **≥ 60%** | ≥ 90% | 95.8% [79.8, 99.3] (CI 2026-09-25) | needs the detector to box a partial animal |
| Subjects at 0.3% of the frame rejected as too small | none published | **≥ 50%** | ≥ 80% | 62.5% [42.7, 78.8] (CI 2026-09-25) | a subject the detector misses is not rejected |
| Burst grouping, pairwise F1 | none published; cosine near-duplicate detection is the usual method (thresholds 0.93–0.95, unverified) | **≥ 80%** | ≥ 95% | 95.7% (CI 2026-09-25) | one pick per burst |
| Scene label correct (the smoke photos are all wildlife) | none; SigLIP2 zero-shot is reported below its paper (unverified) | **≥ 80%** | ≥ 95% | 91.5% [87.1, 94.5] (CI 2026-09-25) | one label only until an album with scene labels exists |

How measured: CI `models.yml` (tests/models, `models-report-album.json`), or on any photos with boxes:
```sh
uv run python scripts/cull_synth.py --preds runs/src.ndjson --out runs/album-synth --seed 7
bioscan bench run runs/album-synth/groundtruth-album.csv --profile album --tier album --out runs/album
bioscan bench scorecard runs/album/report.json
```

## Release stages

### Always: every push

- CI smoke inside its guard (`directory.smoke.*`, and the `tests/models` floors).
- `bioscan bench compare` against the committed baseline, within budget.

### v0.x: "try it and help identify" (community call)

Every bar below must be met at its community level, under the statistical rule, on one tagged build
with a published report.

| Dimension | Standards that gate v0.x |
|---|---|
| Directory test | golden X, Y, Z; own X, Y, Z (the own set may need to grow to prove 95%) |
| Accuracy | birds and mammals: top-1, top-5, genus |
| Trust | birds and mammals: confident-error rate, precision, coverage; **other animals: confident-error rate** (needs the golden other-animal slice) |
| Detection | birds and mammals: gate, detect rate, no-box rate |
| Location | birds top-1 without coordinates (golden); own RAW without a coordinate |
| Coverage | bird and mammal lists; truth share 100% |
| Robustness | failed rate; per-image isolation |
| Privacy | 0 outbound requests |
| Reproducibility | pinned; release report published |

The v0.x report must also **measure and publish**, without gating: other-animal accuracy, ECE,
speed, onboarding time, RAW formats, mammals without coordinates, and the geo gain.

Other animals are gated only on confident errors in v0.x. That is the smallest bar that keeps the
all-taxa default honest: other animals may answer at genus, but they must not be confidently wrong.

### v1.0: "bundle and release"

- Everything in v0.x.
- Other animals: top-1, top-5, genus, precision, coverage, detect rate.
- Trust: ECE ≤ 0.05.
- Speed: throughput ≥ 1.05 images/s, identify ≤ 250 ms, decode ≤ 700 ms.
- Onboarding ≤ 30 min.
- RAW formats 11 of 11.
- Coverage: other-animal names ≥ 100,000.
- Location: mammals without coordinates, and a geo gain of at least 3 points.
- Public set: n ≥ 5,000, with X, Y and Z met.
- The own set grown to at least 1,000 images and 10 species, one frame per burst, with X, Y and Z
  still met.
- Confident-error classes reported by the community during v0.x are triaged (fixed, or documented
  as a known limitation).

**Stretch bars never gate a release.** They are what we would need to call bioscan the leader, and
the scorecard shows how far away they are.

## Machine-readable form

`data/standards.toml` has one `[[standard]]` per bar, with these fields: `id`, `dimension`, `title`,
`scope` (all|bird|mammal|other; a plugin's scope with `plugin`), `metric`, `op` (">=" or "<="), `industry`,
`community`, `stretch`, `unit`, `how` and `source`, and optionally `tier` (when it is not the id's second segment,
e.g. `aesthetic-own`), `profile` (the profile the tier runs under, e.g. `album`; default `wildlife`, and a report
of `full` or without a profile is held to the wildlife standards) and `plugin` (the metric is one that plugin
declares, read from report.json `plugin_metrics`).
`tests/unit/test_standards.py` checks the schema.

- **Nulls.** TOML has no null, so a missing `industry` or `stretch` key means null (no comparable
  number, or no stretch).
- **Ids.** A report metric's id is `<dimension>.<tier>.<scope>.<metric>[.<variant>]`, for example
  `trust.golden.mammal.confident_error_rate` or `location.golden.bird.top1.nogeo`. The scorecard
  applies a standard only to a report from that tier, and to a `--no-geo` report only for the
  variant `nogeo`.
- **Manual entries.** `metric = "manual"` (id `<dimension>.<name>`) covers standards that are not a
  report key: list sizes, truth share, RAW formats, isolation, onboarding, privacy, pinning, the
  release report and the geo gain. The scorecard lists them as "check by hand".
- **Units.** Rates are fractions (0–1). Speeds are in `ms` or `images/s`. Geotag errors are in `m`, clock offsets in `s`.
- **Profiles and plugin metrics.** An optional `profile` (default `wildlife`) says which profile's reports a
  standard applies to (a report without a profile, or `full`, counts as `wildlife`). With `plugin = "<name>"` the
  metric is one that plugin declares and is read from report.json's `plugin_metrics[plugin][scope]`; the scope may
  then be any of its scopes, such as a reject reason. The album tier's standards use both (section 14).
- **Geotag metrics.** The `geotag` tier reads a `bench geotag` report, whose metrics differ from a species report:
  `median_error_m`, `p90_error_m`, `within_100m_rate`, `within_1km_rate`, `no_fix_rate`, `false_fix_rate`,
  `cell_change_rate`, `offset_error_s` (docs/harness.md defines them).
- **Aesthetic metrics.** The `aesthetic-own` tier (ids `aesthetics.aesthetic_own.*`, explicit `tier = "aesthetic-own"`)
  reads a `bioscan aesthetic eval` report: `spearman`, `kendall`, `plcc`, `spearman_trip_mean` (unit `correlation`),
  `ndcg_at_k` (unit `score`) and `precision_at_k` (a rate, `fraction`). The `aesthetic-golden` tier (ids
  `aesthetics.aesthetic_golden.*`) reads a `bench aesthetic score` report (schema `bioscan-aesthetic-golden`) with
  the same metrics plus the rates `keepers_lost_at_20` and `group_top1`.

## Industry references: what we could verify

The egress proxy blocked inaturalist.org, ebird.org, research.google, arxiv.org and most news sites,
so most numbers rest on search-result snippets. None of them is independently measured on our
folders.

| Claim | Number | Status | Source |
|---|---|---|---|
| iNat CV 2.20 average accuracy (all taxa, >100k taxa) | 88.7% | snippet; vendor claim; test set not public | [^inat220] |
| iNat geomodel lifts top-1 | +12 pts → 87% | snippet; vendor claim | [^inatgeo] |
| SpeciesNet species-level accuracy (camera traps) | 94.5% when it makes a species-level call; 99.4% animal recall; 98.7% animal precision | snippet; vendor claim; the rollup behaviour is confirmed on PyPI | [^speciesnet] [^speciesnet-pypi] |
| Dongniao | top-1 85%, top-5 96%; 10,928–11,000+ species | snippet; vendor claim | [^dongniao] [^dongniao-api] |
| WildlifeAI | 97.3% top-1, 99.1% top-3 on its own 50,000-photo validation set; 1,000+ species | **read on GitHub**; vendor claim; not comparable | [^wildlifeai] |
| CUB / NABirds SOTA | TransFG 91.7% / 90.8%; "about 92%" | snippet; peer-reviewed (AAAI 2022) | [^transfg] |
| VLMs on RealBirdID | < 13% on the answerable set | snippet of the abstract | [^realbirdid] |
| Merlin photo ID | **95% average (Merlin 3.5 model)**; earlier 90–92% | snippet; vendor claim, no test set. **Correction:** the brief said Merlin publishes no number, but it does publish this average | [^merlin] |
| Nomen | no accuracy published; local, GPS-aware, review before writing | snippet of nomenapp.com | [^nomen] |
| Excire Foto speed | ~10,000 photos in ~45 min | **unverified**: snippet, and the page it came from was not confirmed | [^excire] |

[^inat220]: iNaturalist, "New computer vision model with over 100k taxa" (v2.20): https://www.inaturalist.org/blog/107012-new-computer-vision-model-with-over-100k-taxa
[^inatgeo]: iNaturalist, "Introducing the iNaturalist Geomodel": https://www.inaturalist.org/blog/84677-introducing-the-inaturalist-geomodel
[^speciesnet]: Google Research, "Where wild things roam: identifying wildlife with SpeciesNet": https://research.google/blog/where-wild-things-roam-identifying-wildlife-with-speciesnet/
[^speciesnet-pypi]: SpeciesNet on PyPI (ensemble rollup to a higher taxon when not confident): https://pypi.org/project/speciesnet/
[^dongniao]: 懂鸟全球 (Dongniao), appinn.com: https://www.appinn.com/dongniao-wechat-miniapp/
[^dongniao-api]: 懂鸟 bird recognition API, Baidu API store: https://apis.baidu.com/store/detail/ee97e453-2ce8-44b6-b04c-bd06fa484b5e
[^wildlifeai]: WildlifeAI README: https://github.com/NiyaNagi/WildlifeAI
[^transfg]: He et al., "TransFG: A Transformer Architecture for Fine-grained Recognition", AAAI 2022: https://arxiv.org/abs/2103.07976
[^realbirdid]: "RealBirdID: Benchmarking Bird Species Identification in the Era of MLLMs": https://arxiv.org/abs/2603.27033
[^merlin]: eBird news, "Merlin's Photo ID has a new update": https://ebird.org/news/new-photo-id-model-in-merlin
[^nomen]: Nomen: https://nomenapp.com/
[^excire]: search snippet for Excire Foto analysis speed; most likely https://www.digitalcameraworld.com/tech/software/excire-foto-2025-review (not confirmed)
[^strategy]: docs/strategy/2026-09-24-opportunities.md, "Defensible"
