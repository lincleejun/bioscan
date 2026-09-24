# bioscan: opportunities and the plan to lead a niche (2026-09-24)

Shareable page: https://claude.ai/artifact/7Lv4jooPmTSyXUUGMSYKTV (private until shared).

## Owner decisions (2026-09-24, after reading this)
1. No commercial goal for now: open source, aiming to lead the field. Licence questions are no longer blocking.
2. The service stays stateless; no observation store for now.
3. Default is **all taxa**. Candidate taxa ("A, B or C") are an optional speed/accuracy aid, never required.
4. Genus back-off where a row has no location label; a box may switch kind (bird <-> mammal) on species evidence.
5. CLAUDE.md becomes common rules for every model.
6. The harness gets baselines (today, per tag/release), compare with regression budgets, and failure analysis
   that tells us what to fix next.
7. Industry standards across several dimensions, with our current status, become the target
   (`docs/standards.md`); release to the community once the bars are met.

The sections below are the research as delivered; where they assume a commercial product, read decision 1.

## How this was produced

- **Round 1: five research agents**, run in parallel, each with sources:
  - competitors;
  - market and who pays;
  - models and technology;
  - harness evolution;
  - a product gap audit of this repo.
- **Round 2: one deep dive per proposal (P1–P5)**, each covering design, effort, success and kill criteria, risks, and scores.
- **Evaluation:** the orchestrating session spot-checked the key claims. Checked claims:
  - the geo model URL in the installed `birdnet` package;
  - the EXIF path in `decode.py`;
  - the CI per-image table.
- **Blocked pages:** the egress proxy blocked several sites (inaturalist.org, ebird.org, arxiv.org, huggingface.co, nomenapp.com, exiftool.org). Claims that rest on search snippets are marked *(unverified)*.
- **Accuracy numbers:** every accuracy target is unverified until `bioscan eval` runs on a Mac.

## Thesis

Generic species ID belongs to Cornell (Merlin/eBird), iNaturalist and, soon, Adobe.

The niche bioscan can lead is **trusted, automatic species keywords for a Mac wildlife photographer's whole RAW archive, written into whatever catalog they use**. The pitch is "keywords you don't have to check": certified precision on what is written automatically, and everything else goes to review.

Accuracy is not what stops people paying today. Three things do:
- results never reach the catalog;
- only a developer can install it;
- it makes confident species-level mistakes.

## Where it stands (evidence)

| Dimension | Now | Leader needs | Gap |
|---|---|---|---|
| Birds | Golden Top-1 89.8% (README); the baseline doc says 85.7 → 91.1% after synonyms. **The two sources disagree.** Own RAW 96.3% (3 species) | ≥98% precision on auto-written species, calibrated, full-res, multi-region | M |
| Mammals | Top-1 74.1% (before the v1.1 fixes), 45 images with no box, no location prior | ≥85%, with a range prior | L |
| Confident errors | Species-level wrong answers as a share of all images: birds 6.3%, mammals 8.9%. CI examples: *Megascops* → *Spilogale*; *Corvus corax* → *C. sierramadrensis*; *Phoca* → *Larus* | ≤3%, the rest reviewed | L |
| Output | NDJSON and full-frame JPG | XMP/IPTC write-back, LrC plugin, review, search | L |
| Install | clone, uv, 7 GB+, hand-converted CSVs, launchd | Signed app, one download | L |
| Speed | CPU 12 s/image; MPS about 0.25 s/image (estimate, unverified) | 30k photos overnight, resumable, incremental | M |
| RAW robustness | CR3/RAF/ORF/RW2: no GPS or time (`decode.read_exif` parses only TIFF-based RAW); sub-second time ignored; folder scan skips CR2/ORF/RW2/PEF/SRW (`cli/gt.py:16`) | All mainstream bodies | M |
| Privacy | Local, offline hub, allow-roots | same | S (a strength) |
| Licence | The prior is geomodel v3.0.4, whose repo licenses its model artifacts Apache-2.0; the README still says CC BY-NC-SA | Written confirmation | S |

## Market and competition (short)

**Beachhead.** English-speaking serious amateurs on a Mac with Lightroom Classic and 50k+ bird images. Forum threads already ask for this. Price anchors:
- Excire $199–249 one-time ([PetaPixel](https://petapixel.com/2025/09/17/ai-powered-excire-search-2026-aims-to-redefine-lightroom-workflows/));
- Aftershoot $10–60 a month ([Aftershoot](https://aftershoot.com/blog/aftershoot-pricing-tiers/)).

**Second segment.** Capture One, Photo Mechanic, digiKam and Immich users. No species writer exists for them.

**Deferred:**
- camera traps: free tools dominate (MegaDetector, AddaxAI, Wildlife Insights);
- consultancies: BNG, CSRD and TNFD don't require photo species ID;
- China: 340k registered birders ([China Daily](https://www.chinadaily.com.cn/a/202405/21/WS664bf16ba31082fc043c82f9.html)), but [懂鸟 (Dongniao)](https://dongniao.com/) already identifies 11,131 species offline, and Windows is common there.

**Direct competitors:**
- [Nomen](https://nomenapp.com/): local LrC plugin with GPS and review, closed beta, no published price or accuracy. The site was blocked, so this is from snippets.
- [LrGeniusAI](https://github.com/LrGenius/LrGeniusAI): free, AGPL, BioCLIP 2, no location prior.
- [Project Kestrel](https://projectkestrel.org/): North America only.
- [WildlifeAI](https://github.com/NiyaNagi/WildlifeAI): 97.3% on its own validation set only.

**Threats:**
- Adobe adding species keywords (estimate: 6–18 months);
- a desktop or batch Merlin.

General VLMs score <13% on RealBirdID ([arXiv 2603.27033](https://arxiv.org/abs/2603.27033), snippet only).

**Defensible:**
- published, reproducible precision (no competitor publishes any);
- a coordinates-plus-week prior with graded levels (competitors stop at country or region);
- XMP / Darwin Core write-back that isn't tied to Lightroom;
- the user's own labels from the review queue.

## Proposals and verdicts

| Proposal | Pay impact | Effort | Verdict |
|---|---|---|---|
| **P2** Licensable location prior, plus a mammal prior | 4/5 | 2/5 | **Now.** Geomodel v3.0.4 has 1,048 Mammalia labels. They map to 1,047 MDD species, covering 21 of the 23 golden mammal species. One confirmation email plus 2–3 days of work. |
| **P3** Accuracy lead (fixes) | 3/5 | 3/5 | **Now.** Every confident-error class has a known root cause that needs no new model (see below). |
| **P1** Trusted catalog writer | 4/5 | 4/5 | **Core.** Calibration and the auto-write gate first, then XMP, then the LrC plugin. |
| **P4** Observation store and agent surfaces | 3–5/5 | 3/5 | **Store, incremental runs, eBird export and life list only.** MCP and search later. |
| **P5** Packaging, speed, go-to-market | 3/5 | 4/5 | **Split.** The concierge pilot starts in phase 2 without packaging; the signed app comes in phase 3; no Windows. |
| Public benchmark (second half of P3) | 3/5 | 4/5 | Phase 4: CC0/CC BY only, 5 or more regions, about 5k images |
| Cloud VLM by default, fine-tuning on iNat, Windows, DAM UI | — | — | **No.** They break the privacy promise, breach iNat's terms, cost more than any signal we have, and are out of scope. |

### Root causes behind the confident errors (P3)

- **Raven named as a Philippine crow.** The posterior is `p_visual × (0.02 + p_geo)`, so a zero p_geo can overturn visual odds of at most about 51:1. The species level never checks range. Fix: an **out-of-range veto** (where the place is known, a candidate with p_geo < ε cannot be graded species), plus tuning the floor offline. The normaliser `Z(floor) = floor + Σ p_visual·p_geo` makes a floor sweep possible without re-running the models.
- **Owl gated as mammal and named a skunk; seal gated as bird and named a gull.** `rules.judge` can only promote a box to bird, never demote it. Fix: a **two-way kind check**. Score the BioCLIP features BioCLIP already computed against the bird and mammal lists together, set the box's kind from where the mass falls, and cap the level when that disagrees with the gate.
- **45 mammals and the barn owl got no box.** Fix: a **MegaDetector v6 fallback** using the Apache (RT-DETR) or MIT (YOLOv9-c) variant, never the AGPL ones.
- **Bears confused with each other.** Mammals have no prior. Fix: the P2 mammal prior, backed by MDD `countryDistribution` (CC BY).

### Licence verdict (P2)

**What bioscan loads.** `birdnet==1.1.1` downloads `BirdNET+_Geomodel_V3.0.4_Global_14K_FP32.onnx` from `github.com/birdnet-team/geomodel/releases/download/v3.0.4/`. The orchestrator checked this in the installed package source.

**What the geomodel repo says at tag v3.0.4:**
- `LICENSE-MODELS.md`: Apache-2.0 covers "checkpoints, converted models (… `.onnx` …) and label files".
- Before commit `4d8abe9` it was CC BY-SA, and its terms already allowed commercial use.

**Where the conflict comes from.** The `birdnet` package README says in a blanket line that all models are CC BY-NC-SA. That line is where our README's warning came from.

**Remaining risk.** The model was trained on GBIF data without a licence filter, so some records are CC BY-NC. Confirm in writing before selling.

Email draft (to ccb-birdnet@cornell.edu, or as an issue on birdnet-team/geomodel):

> Subject: Commercial use of BirdNET+ Geomodel V3.0.4 (Apache-2.0) vs. birdnet package "CC BY-NC-SA" notice
>
> We use `BirdNET+_Geomodel_V3.0.4_Global_14K_FP32.onnx` (sha256 0de81d22…) and its Labels.txt, fetched by `birdnet==1.1.1` via `birdnet.load("geo","3.0","onnx")`, as a location prior in a paid, on-device photo-ID app. We will not use the BirdNET name or branding. geomodel's LICENSE-MODELS.md (tag v3.0.4) licenses these artifacts under Apache-2.0, while the birdnet package README says all models are CC BY-NC-SA 4.0. Please confirm: (1) Apache-2.0 governs these geomodel artifacts, including when downloaded through the birdnet package; (2) the CC BY-NC share of the GBIF training data does not restrict commercial use of the weights; (3) whether you would update the birdnet README to say which models it covers. We are happy to credit you as you prefer.

**Fallback, if the licence is refused or unanswered after 4 weeks.** Retrain the geomodel with its MIT code on a GBIF download filtered to CC0 and CC BY records (2–3 weeks on a GPU). Earth Engine's terms are a risk there.

## Roadmap (phases gate each other)

The order is the point. Auto-writing about 1,900 wrong keywords into a 30k catalog would end the product, so trust comes before surface.

### Phase 0: get the facts straight (1–2 weeks)

**Work:**
- Run `bioscan eval` on the golden set and own RAW on a Mac, and settle which of the 89.8 and 85.7 figures is right.
- Run a 2,000-file speed benchmark covering ARW, CR3 and NEF at 24 and 45 MP, from both USB and SSD. Measure wall throughput as `done.elapsed_ms / ok`.
- Send the BirdNET email, then fix the README licence line.
- Fix the RAW bugs: CR3/RAF/ORF/RW2 EXIF, `SubSecTimeOriginal`, and the missing scan extensions.

**Gate:** one set of accuracy numbers; measured MPS throughput; the licence answered, or the fallback started.

### Phase 1: trusted engine (4–5 weeks)

**Work:**
- Out-of-range veto.
- Two-way kind check.
- MegaDetector fallback.
- Offline floor sweep, with folds split by observer.
- Mammal prior: an `mdd_map.csv`, a per-list `unlabelled` policy (`genus` back-off for mammals), and the policy included in the settings fingerprint.
- Calibration:
  - `p_correct` and `decision` (auto / review / skip) in `contract.Species`;
  - fitted maps under `data/calibration/`, included in the fingerprint;
  - risk–coverage and confident-error rate in `bioscan eval`.

**Gate (golden set, targets):** bird Top-1 ≥92%, mammal ≥85%, confident errors ≤3% at ≥85% coverage.

### Phase 2: results in the catalog (5–6 weeks)

**Work:**
- **SQLite observation store:**
  - It lives in `~/Library/Application Support/bioscan/`, not in the deletable cache.
  - Key: sha256 + engine info + request options + effective location.
  - A stat fast path, and resume without job ids.
  - The CLI writes it; the service stays stateless.
- **XMP writer through exiftool `-stay_open`:**
  - `lr:hierarchicalSubject`, `dc:subject` and `digiKam:TagsList` kept consistent;
  - Darwin Core `dwc:Taxon` and `dwc:Identification`;
  - a `bioscan:` namespace recording exactly which keywords we added, so bioscan never deletes the user's own;
  - sidecars only for RAW, and no sidecar writes for LrC-managed photos.
- **`bioscan review` CLI loop.** Accepted answers become ground truth. Only non-top-1 picks are unbiased for measuring precision.
- **eBird CSV and life list.** Incidental protocol, `N`, count `X`; mapping through AviList's Cornell code column.
- **In parallel, a concierge pilot:** 10 photographers, $20 each, 2048 px exports.

**Gate:**
- pilot: ≥6 of 10 pay;
- spot-check precision ≥90%;
- 14-day keyword retention ≥95%;
- auto-write precision ≥98% at the 95% lower bound on real archives. That needs about 530 auto-written boxes per kind at 99% true precision, one per burst.

### Phase 3: something people can buy (6–8 weeks)

**Work:**
- PyInstaller app, signed and notarized, running as an `SMAppService` login item.
- Models on a CDN with sha256 pins, prebuilt name caches, the BioCLIP text tower dropped, fp16 weights (about 1.5–2 GB).
- Speed: fp16 on MPS; the embedded RAW preview for gate and detect; burst dedupe by camera, sub-second time and embedding cosine. Target: 30k photos overnight on an M1 Air.
- LrC plugin: `LrHttp` to localhost, `catalog:withWriteAccessDo`, keywords plus plugin metadata fields.
- Fake-door page with a $10 refundable deposit, and a demo video.
- Price: $79 including a year of updates, then $39 a year.

**Gate:** ≥1,500 targeted visitors and a deposit rate of ≥1.5% (≥25 deposits). Kill below 10 deposits.

### Phase 4: expand only on signal

- A public benchmark: CC0/CC BY, ≥5 regions, about 5k images, one observation per photo and at most 5 per observer, with a sequestered test split and SpeciesNet and BioCLIP 2 run on the same set.
- An MCP server (stdio, read-only SQLite) plus hybrid search: taxon and time filters first, SigLIP2 text search for the remaining scene words.
- China: a ModelScope mirror; Xiaohongshu and Bilibili, with no links in Xiaohongshu posts.
- Windows only after ≥300 requests for it.

## Kill criteria

- **Technical:**
  - bird auto-write coverage below 40% at 98% precision;
  - a lead over BioCLIP 2 zero-shot or SpeciesNet below 2 points after the fixes;
  - a mammal prior gain below 1.5 points.
- **Market:**
  - fewer than 3 of 10 pilots pay;
  - fewer than 10 deposits;
  - Nomen ships publicly while we have no measurable precision advantage.
- **Licence:** a BirdNET refusal, or no answer after 4 weeks (then the fallback); or a 5k benchmark that can't be legally cleared.

## Harness evolution

**Durable assets whichever model wins:**
- an observation store that records predictions per engine;
- eval used as a statistical gate (precision at fixed coverage, per-species regression budgets, tiered: PR smoke / nightly full golden / the user's own ground truth);
- a versioned taxonomy layer (AviList ↔ eBird ↔ MDD ↔ iNat);
- calibration plus the review loop.

**Don't build:**
- more detector vocabulary or gate thresholds (a grounding model will absorb them);
- NL query parsers (agents do that);
- trip-report generators;
- large-model fine-tuning infrastructure;
- a generic router before a second expert passes eval.

**Risk: CLAUDE.md applies only to Opus 5.5.** Every rule switches off when the agent model changes. Move the rules that don't depend on the model into executable checks and hooks, and keep only model-tuned prose in the model-specific file.

## Economics (estimate)

- $79 nets about $72 after Paddle's fee (5% + $0.50) and refunds; fixed costs are about $400 a year.
- Side income of about $20k a year needs about 280 sales a year.
- One salary needs about 1,110 sales a year.
- The target niche may be only in the low tens of thousands worldwide (unverified).

## Decisions for the owner

1. Send the BirdNET licence email in your name. The draft is above.
2. Lift v1's "no persistent state" non-goal so phase 2 can add the SQLite store and the review CLI.
3. Confirm the positioning: trusted species keywords for Mac photographers, not all-taxa ID.
4. Choose how unmapped mammal rows are handled: `genus` back-off (recommended) or a neutral constant.
