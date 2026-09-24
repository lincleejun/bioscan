# TASKS — v1.1 improvements

Source of truth for this run. Each item: goal → how it is verified.
Decisions taken (2026-09-23): geo gaps = data-driven + review tool; detail edge = 3072;
jpg name = `<stem>-<sha8>.jpg`; real-model smoke runs on every PR.

## 0. Ground rules
- [x] CLAUDE.md from the Opus 5.5 template, filled for this repo
- [x] ruff config in pyproject; baseline lint clean (scripts/verify excluded as one-off scripts)
- [x] Existing 75 tests pass before any feature change

## 1. Species from a high-resolution crop
Goal: BioCLIP sees the box cropped from a ≤3072 px "detail" image instead of the 2048 px frame;
detection, gate, quality and box coordinates stay on the 2048 image (unchanged numbers).
- [x] decode: `detail_edge` argument; `Decoded.detail` (None when not asked or when not larger than `image`)
- [x] service: `BIOSCAN_DETAIL_EDGE` / `--detail-edge` (default 3072; ≤2048 disables); detail decoded only when `identify` is wanted
- [x] products.identify: species crops from detail with bbox scaled; everything else unchanged
- [x] engine/fake: `identify(..., detail=None)`
Verify: unit tests (detail size/None rules; crops scale exactly; identify with/without detail gives
identical boxes/quality; BioCLIP receives the larger crop); contract tests; real-model smoke in CI.
Accuracy: unverified until `bioscan eval` on the golden set (Mac).

## 2. Geo gaps review tool (no behaviour change)
Goal: list AviList species with no BirdNET label whose genus is present at a place, so the gaps
that matter there get a reviewed `birdnet` synonym instead of a blanket rule.
- [x] pure function `geo.gaps(scientific, common, index, probs, min_p)` in adapters/geo.py
- [x] `bioscan names geo-gaps --lat --lon [--date] [--min-p]` (reads avilist_map.csv; loads BirdNET only)
- [x] docs in README (名字映射 section)
Verify: unit tests with a fake prior; prior values for mapped rows unchanged (existing tests).

## 3. Mammal detection vocabulary
Goal: fewer "no box" mammals (black bear, mountain lion, grizzly, bobcat in the golden set).
- [x] add large carnivores and common CA mammals to VOCAB["mammal"]
- [x] gate rescue: gate says none/person but bird+mammal+other_animal >= 0.25 -> first-pass detection with the
      strongest animal's vocab (crop gate still vetoes; no low-floor second pass; reported gate class unchanged)
- [x] eval: "no box" split by whole-frame gate class (works on existing preds.ndjson via `--preds`)
Found: eval's "(no box)" includes images the gate called none/person, where the detector never ran,
so vocab alone could not fix gate misses.
Verify: unit tests (rescue on/off, first pass only, veto, old path unchanged, vocab); real-model smoke in CI.
Accuracy: unverified until `bioscan eval`.

## 4. jpg product naming
Goal: no silent overwrite across folders or RAW+JPG pairs.
- [x] `<out_dir>/<stem>-<sha256[:8]>.jpg`; /products description
- [x] README
Verify: contract test with two same-stem files in different dirs → two outputs.

## 5. Path allow-list for the service
Goal: exposing the service beyond localhost does not open the whole disk.
- [x] `BIOSCAN_ALLOW_ROOTS` / `--allow-root` (repeatable): inputs and jpg.out_dir must be inside; unset = current behaviour
- [x] warn at start when host is not loopback and no roots set
Verify: contract tests (inside ok, outside 400, symlink/`..` escape rejected).

## 6. CI
- [x] `.github/workflows/ci.yml`: ruff + pytest on push/PR
- [x] `tests/models/` real-model smoke (SigLIP2, OWLv2, BioCLIP 2.5 Huge on CPU, small in-memory name list, iNat open-data photos)
- [x] `.github/workflows/models.yml`: every PR/push, HF cache
Verify: workflows green on the pushed branch. ci green; models runs 3 and 5 green (7 passed; 23.5 min cold, 20 min cached).
- [x] FLOORS moved from placeholder values to ~4 images under the measured numbers (bird top-1 88.1 %, mammal top-1 85.7 %)

## 7. Wrap-up
- [x] README updated (data/README unchanged: its facts still hold)
- [x] self-review of the diff against main (subagent; 1 blocker found and fixed: `serve --launchd` dropped `--allow-root`/`--detail-edge`)
- [x] push `claude/sleepy-hawking-0jht6y` (PR lincleejun/bioscan#1)

## Found along the way
- Geo prior: 749 AviList species have no BirdNET label; most are extinct or lumped sisters
  (e.g. Tyto javanica) that should stay suppressed, so "no label → no prior" would regress. Hence item 2.
- Mammal geo prior needs a range-data source (not in this round).
- Golden-set photos are iNat "medium" (≤500 px), so the detail crop (item 1) cannot change golden-set
  numbers; its benefit shows only on large originals (own RAW tier).
- CPU-only torch index for Linux CI would save several GB per run, but relocking needs
  download.pytorch.org, blocked in the dev container; CI caches uv instead.
- BirdNET geo model download is blocked in the dev container; geo-gaps verified with a fake prior
  locally and against the real model only in CI.
- geo-gaps at 37.4,-122.1 (CI, real BirdNET): 77 species listed, nearly all island endemics or extinct
  (confirms "no blanket rule"). One real Californian gap: Icterus bullockiorum (Bullock's Oriole),
  presumably BirdNET's "Icterus bullockii"; needs a `birdnet` row in synonyms.csv (committed data: user's call).
- Real-model smoke: 1 image rescued by the gate rescue (Marmota flaviventris, gate "none" -> boxed, correct species).
- Real-model smoke: Megascops kennicottii photo gated "mammal", best box ranked against mammals (Spilogale);
  the crop gate did not promote it to bird. Known owl/mammal gate confusion from the baseline doc; not addressed here.

---

# TASKS — v1.2 architecture review follow-up (stacked on PR #1)

Review: 4 subagents (service core, models/names, CLI, tests/CI); key evidence spot-checked.
Decisions (2026-09-23): packages A+B+C+D; stack on PR #1; spec records fp32 (no fp16 now);
models.yml every push with path filters. D4 (on-demand detail) dropped: the ~1.2 GB prefetch was
accepted with the 3072 choice, and re-decoding RAW would double decode time.
Invariant for C and D: real-model CI numbers unchanged (bird top-1 88.1 %, mammal 85.7 %; CPU is deterministic).

## E. CI cost
- [x] models.yml path filters (service, tests/models, data/names, uv.lock, pyproject, workflow)

## A. Versions and reproducibility
- [x] pin SigLIP2 / BioCLIP / TreeOfLife revisions (values read from a CI run), pass everywhere, in names cache key and info()
- [x] settings fingerprint (thresholds, prompts, vocab, geo floor, detail edge) in info()
- [x] eval: meta header line in preds.ndjson (schema, options, gt/synonyms sha); rescoring reads geo from it
- [x] one stdlib name normaliser (`bioscan/naming.py`) used by names, geo, eval
- [x] stale-map check: synonyms.csv rows not reflected in avilist_map.csv -> warning at load, test on committed data
- [x] BirdNET geo model fetched in download.py, cached in CI; CI fails if geo is missing
Pins read from a CI run: siglip2 75de2d55, bioclip 6e3d04e3 (loaded via open_clip local-dir: of the pinned
snapshot, since its hf-hub: path cannot take a revision), TreeOfLife 5f2dc493 (= data/README snapshot = main).
Name cache key unchanged (no forced 3.26 GB re-download); new caches record revisions and rebuild on mismatch.
Verify: unit tests; CI.

## B. Contract hardening
- [x] `bioscan/contract.py` (stdlib): products, event types, schema version; used by app, CLI, render, eval
- [x] `EngineProtocol`; test that Engine and FakeEngine match it (incl. signatures)
- [x] import-light test for the CLI modules
- [x] exit codes: 0 ok / 1 some images failed / 2 service unreachable / 3 incomplete stream or upstream error
- [x] process-pool decode contract test; RAW orientation logic test (rawpy faked)
Verify: tests.

## C. Pipeline restructure (no behaviour change)
- [x] `rules.py` (pure rules + thresholds), `pipeline.py` (identify orchestration behind a `Models` protocol)
- [x] product registry (needs, defaults, validation, runner); app dispatch and engine NEEDS derived from it
- [x] model loader registry in Engine
- [x] taxa registry (gate prompts, detector words, name list per kind)
- [x] priors per kind (`engine.priors`), floor per prior; names.py no longer imports geo private helpers
Verify: all tests; real-model CI numbers identical.

## D. Throughput and scheduling
- [x] model lock per chunk (FIFO) instead of per request: a 1-image request waits at most one chunk
- [x] batch identify across the chunk (crop gate, BioCLIP, OWLv2 by vocab group) with per-image error isolation
- [x] rebuild a broken decode process pool and retry the chunk once
- [x] jpg writes off the GPU thread
- [x] spec: record fp32 (fp16 left for a Mac eval)
Verify: tests; real-model CI numbers identical; speed unverified until Mac benchmark.
- [x] self-review (subagent): 1 blocker (decode-pool rebuild race cancelled another request's decodes), fixed in af233c9
      and confirmed with the reviewer's own reproduction (3/3 runs: only the crashing file fails)
- [x] batched pipeline == old per-image code: 300 random frames x 3 option sets identical (scratch comparison)
- [x] real-model CI on f587283 (pinned revisions, BirdNET required): 7 passed; bird 97.6/95.2/88.1/92.9 and mammal
      91.4/91.4/85.7/91.4 (gate/detect/top-1/top-5), coverage/precision and every per-image row identical to the
      pre-refactor run. CPU identify ms/image 11.7 s bird / 12.7 s mammal vs 10.9 / 14.1 before: batching shows no
      clear gain on a 4-core CPU runner; MPS speed unverified until a Mac benchmark.

---

# TASKS — v1.3 deepening (report candidates 1-6), run as 6 parallel worktrees

Source: /improve-codebase-architecture report 2026-09-23 (candidates 1-6; 7 not in scope).
Base: 7959dc2. Each task: own worktree + local branch `arch/N-*`, own checklist `TASKS-arch-N.md`,
behaviour-preserving (identify output, /run events, CLI behaviour and exit codes unchanged).
Acceptance per task (by the orchestrating session): ruff + pytest green in the worktree, golden-output
equivalence evidence re-run by the reviewer, diff review with no blocking findings.
Integration: merged in order 5, 6, 2, 1, 4, 3 onto claude/sleepy-hawking-0jht6y; real-model CI there must
reproduce bird 97.6/95.2/88.1/92.9 and mammal 91.4/91.4/85.7/91.4 exactly.

Run: 6 background agents, one worktree each (they started at 78af566 and branched from 7c011d5 as told).
Every task recorded golden outputs before its first edit and again from a clean `git archive 7c011d5`.
Acceptance by the orchestrator: golden re-run on fresh base/branch trees, ruff + pytest x2, and an
independent read-only reviewer per task ("NO BLOCKERS" for all six; its extra edge-case runs noted below).

- [x] 1 One identify module; seam at the three model adapters (`engine.Loaders`); Engine.identify /
      identify_many / name_matrix / EngineProtocol deleted; product runner calls `pipeline.identify_many`;
      BioCLIP `place()` puts name-list matrices on the device at load (a failure there is still a 503);
      contract tests run the real Engine + pipeline on fake adapters. Golden sha 6fa4f1eb (4800 direct +
      1200 via the product runner); reviewer: 503 path, one device copy, `array_equal` probs, and every
      assertion of the deleted test_engine_protocol.py covered by test_adapter_seam.py / contract tests.
- [x] 2 Location prior: `geo.LocationPrior(source, row_labels)` with `p_geo(lat, lon, taken_at)`,
      `posterior`, `gaps`; `priors_for(lists, source)`; PriorBinding / align / GeoPrior.index / module
      posterior+gaps removed. Golden sha d64f6578 (15727 identify results, 12495 geo-gaps lines);
      reviewer: 170 extra edge cases (no geo, lat/lon None, bad dates, failing source, mammal list) identical.
- [x] 3 Run module `bioscan/service/run.py`: `RunQueue.events(inputs, want, opts, is_disconnected)` owns
      chunks, the per-chunk FIFO model turn, executors and the self-healing decode pool; app.py is HTTP only.
      Golden sha 59559456 (110 /run scenarios); reviewer: lock and counters released on cancel and on error.
- [x] 4 Identify payload owned by `bioscan/contract.py` (fields, constructors, CLI readers,
      `identify_problems`, `IDENTIFY_OUTPUT`). Goldens identical (identify, render, eval, /products, /run);
      reviewer: 1022 partial/malformed payload events through render/eval identical; 10/10 one-sided field
      renames caught by tests.
- [x] 5 `bioscan/serve_config.py`: flag > BIOSCAN_* > default resolved once for `bioscan serve`,
      `bioscan-serve` and launchd; fingerprint reads every numeric UPPER_CASE constant of rules.py (value
      unchanged 1dd3f33ab9c7). Golden: 710 cases, 0 differences except the two intended ones below.
- [x] 6 Name-list load returns finished, frozen lists (`ListSource.label_map` instead of `kind == "bird"`);
      `naming.norm_label` replaces `gt.norm`. Golden identical; cache files identical, old caches load
      with 0 re-encodes both ways (the 3.26 GB cache is reused).
- [x] integration onto claude/sleepy-hawking-0jht6y in order 5, 6, 2, 1, 4, 3 (merge commits). Conflicts:
      READMEs (layout lines), engine/pipeline/test_rules (task 1 moved to task 2's LocationPrior;
      `priors_for` now takes the source from the injected geo loader), conftest (task 1's fake adapters
      replace the FakeEngine task 4 had edited), app.py imports (3 + 5).
- [x] all six goldens re-run on the merged head against the 7c011d5 recordings: identical (1: 6fa4f1eb;
      2: d64f6578 with a recorder adapted to the merged API, same hash on base; 3 and 4 with a shim routing
      the merged app to the old FakeEngine: identical; 5: 0 non-env differences; 6: identical + cache reuse)
- [x] CONTEXT.md (domain terms named by the six tasks); CLAUDE.md project facts updated (fake engine gone)
- [x] duplicate test_names.py::test_norm_binomial removed (test_naming.py has the same cases)
- [x] final review of the diff against main (subagent): 1 blocker (CONTEXT.md claimed the fingerprint covers
      the detail edge; it covers max edge), fixed in bbd6377 with engine-caller wording and models.yml paths
- [x] ci.yml + models.yml green on bbd6377: 8 real-model tests passed; bird 97.6/95.2/88.1/92.9 and mammal
      91.4/91.4/85.7/91.4 (gate/detect/top-1/top-5), coverage/precision 95.2/92.5 and 91.4/93.8, per-image rows
      as in v1.2 -- identical. CPU identify 11.9 s bird / 12.8 s mammal per image (v1.2: 11.7 / 12.7).

Intended behaviour changes (both in task 5):
- `bioscan serve` no longer writes BIOSCAN_* into its own environment (nothing read them back).
- `--allow-root /a:b` is one root; before, `bioscan serve` split it on ":" (a relative second root that
  widened access) while `bioscan-serve` did not. Both entry points now agree.

Kept as before (decided by the orchestrator; behaviour-preserving):
- `--launchd` ignores BIOSCAN_* and validates nothing (plist bytes identical).
- gt folder matching keeps its own normaliser (`norm_label`): merging with `norm_binomial` would change
  840 folder matches.
- `render.result_line` still raises on an identify payload without a gate.

Found:
- Fingerprint scan counts numbers only; a future tuple threshold in rules.py would need adding by hand.
- `pipeline.identify` (one-frame helper) is used only by unit tests.

---

# TASKS — v1.4 strategy research (2026-09-24)

Result: docs/strategy/2026-09-24-opportunities.md (shareable page linked there). No code changed.
- [x] round 1: 5 research agents (competitors, market, models, harness, product audit), sourced
- [x] round 2: 5 proposal deep dives (P1 catalog writer, P2 licensable/mammal prior, P3 accuracy,
      P4 store + agents, P5 packaging + GTM); key claims spot-checked (geo model URL in the installed
      birdnet package; decode.read_exif TIFF-only EXIF path; CI per-image errors)
- [x] verdict + phased roadmap with gates and kill criteria
- [ ] owner decisions: BirdNET licence email; lift "no persistent state"; positioning; mammal unmapped-row policy
- [ ] phase 0 (after decisions): Mac eval + speed benchmark; RAW EXIF / SubSec / scan-extension fixes

Found:
- README says the location prior is CC BY-NC-SA; the loaded artifact (geomodel v3.0.4) is Apache-2.0 per
  its LICENSE-MODELS.md. README left as is until the BirdNET team confirms in writing.
- README (89.8% bird Top-1) and docs/2026-09-23-baseline-results.md (85.7 -> 91.1%) disagree; a Mac eval decides.

---

# TASKS — v1.5 harness, standards, all-taxa, accuracy (2026-09-24)

Decisions: see docs/strategy/2026-09-24-opportunities.md "Owner decisions". One PR; owner reviews, then runs
local data on the Mac. Parallel agents in worktrees from the v1.5 base commit; orchestrator accepts each
(ruff, pytest, golden equivalence where behaviour must not change, independent reviewer), then integrates.

- [x] CLAUDE.md: common rules for every model (no model pin); product decisions recorded
- [x] W1 harness: `bioscan bench run | baseline | compare | analyze | scorecard`; report.json schema;
      regression budgets; CI smoke compares against a committed baseline; tag runs publish a report
      (branch v15/w1-harness; docs/harness.md)
  - [ ] after the first green models.yml run: commit its printed report as `baselines/ci-smoke.json`
  - [ ] models.yml on a real push (tags + branches + paths, the compare step) is unverified until CI runs it
- [x] W2 standards: docs/standards.md + data/standards.toml (industry bar, community bar, our status, how measured);
      tests/unit/test_standards.py (schema, mutation-checked: 7/7 broken copies fail); README links; CONTEXT terms
      Found: most industry pages blocked by egress (numbers rest on search snippets, marked); Merlin does publish a
      95% average (vendor claim); TOML has no null, so a missing industry/stretch key means null; the golden set
      has no other-animal slice yet (v0.x gates other-animal confident errors on it); own set (404, 3 species)
      is too small to prove 95% at the Wilson bound.
- [x] W3 accuracy: out-of-range veto; two-way kind check (bird <-> mammal); mammal location prior
      (mdd_map.csv from geomodel v3.0.4 labels) with genus back-off. Checklist: TASKS-w3.md
- [ ] W4 all-taxa: other animals get species from the TreeOfLife-wide list by default; `candidates` option
  (branch v15/w4-alltaxa)
  - [x] all-taxa list `tol200m-animalia` from TreeOfLife rows (Animalia, species level, minus Aves/Mammalia,
        deduped), float16, cached like the others; optional at load (warning, species null without it)
  - [x] `candidates` option (API, `run --candidates`, `eval --candidates`, preds meta), 400 on unknown names
  - [x] tests on fakes + golden (300 frames, before side from `git archive 0a66f72`)
  - [x] models.yml: 18 CC0/CC-BY other-animal photos ranked against the real all-taxa list, loose floors
  - [x] docs: README, README.zh-CN, data/README, CONTEXT
  - [x] review fixes: eval report unchanged without candidates; all-taxa OOM on the device drops only that
        list; candidates reweight through LocationPrior.posterior; fp16 error stated; --preds + --candidates refused
  - [x] merged the integration branch (W1 W2 W3 W5); candidates path honours range veto, mammal prior and
        kind check; kind check per list (no stacked matrix) with the all-taxa list as a kind
  - [ ] first models.yml run: record real row count, memory, top-1; tighten other_animal floors; check that
        the all-taxa kind check does not cost birds or mammals (on/off table)
  - [ ] plants/fungi: only with a gate class (owner decision), as a second AllTaxaSource
- [x] W5 RAW robustness: EXIF/GPS/time for CR3/RAF/ORF/RW2/PEF; SubSecTimeOriginal; one shared scan-extension list
      (+ broken GPS -> None instead of NaN/out of range; per-CMT CR3 reads; details in TASKS-w5 below)
- [ ] integration: behaviour-preserving parts first -> CI -> commit baseline from that run; then W3/W4 ->
      CI compare vs baseline (no regression beyond budget); final review; PR

## W5 checklist (folded from TASKS-w5.md)


Base: 0a66f72 (branch `v15/w5-raw`). Source of truth for this work package.

## Setup / evidence
- [x] baseline: ruff clean, pytest 160 passed / 8 skipped on base
- [x] problems 1-3 reproduced on base (golden "before"): CR3/RAF/ORF/RW2 -> (None, None, None); SubSecTimeOriginal
      ignored; CR2/NRW/ORF/PEF/RW2/SRW not scanned by `run` or `gt folders`
- [x] golden "before" recorded from a clean `git archive 0a66f72` (read_exif over 76 inputs, decode over 128 files
      with rawpy faked, 4 detail cases, 4 folder scans)

## 1. EXIF/GPS/time for every supported RAW (stdlib + Pillow)
- [x] TIFF-variant headers (ORF IIRO/IIRS/MMOR, RW2 IIU) parsed as TIFF; RW2 JpgFromRaw fallback
- [x] CR3: ISOBMFF moov -> Canon uuid -> CMT1/CMT2/CMT4 (32- and 64-bit box sizes)
- [x] RAF: embedded JPEG from the header (offset/length at 84)
- [x] HEIC/HEIF: unsupported, documented (decode itself needs pillow-heif, a new dependency)
- [x] rawpy check: `raw.other` has iso/shutter/aperture/focal/timestamp/shot_order/artist; no GPS, no offset, no
      sub-seconds -> not used
- [x] tests: synthetic fixture per container (tests/unit/raw_fixtures.py) + 17 broken inputs -> (None, None, None)
- [x] real-file check: `uv run python -m bioscan.service.decode DIR -r`

## 2. SubSecTimeOriginal
- [x] fraction appended (`2026-05-01T08:00:00.37-07:00`); DateTime fallback pairs with SubSecTime; gt (exiftool) agrees
- [x] consumers: geo.week_of reads [5:7]/[8:10] (test); eval passes GT taken_at through; render and the CLI do not
      read it; not in the result event; datetime.fromisoformat parses the fraction

## 3. One shared extension list
- [x] `bioscan/formats.py` (stdlib): RAW_EXT, SCAN_EXT, DEFAULT_EXT, parse_ext, list_images, is_raw, subsec
- [x] decode, `bioscan run`, `bioscan gt folders` use it; import-light test covers it
- [x] test: lower, UPPER and Capitalised file of every extension scanned by both commands

## 4. Behaviour preserved
- [x] golden after vs before: 144 identical, 73 differ, all intended (new containers' lat/lon/taken_at, sub-second
      fraction, added scan extensions); pixels, sizes, orientation, sha256 identical everywhere

## 5. Docs
- [x] README + README.zh-CN supported-files table, check command, sub-seconds; layout lines
- [x] CONTEXT.md: Scan extensions, Container, Capture time
- [x] design spec `--ext` default

## Wrap-up
- [x] ruff + pytest green (200 passed, 8 skipped)
- [x] self-review of diff against base
- [ ] owner: run the check command over real CR3 / RAF / ORF / RW2 (and ARW / NEF) files on the Mac
- [ ] CI (ci.yml, models.yml) on the pushed head: not pushed by this package

## Review notes (orchestrator, after acceptance)
- [x] ORF/RW2: IFDs parsed from the first 4 MB (no whole-file copy); JpgFromRaw sliced from the file by offset/count
- [x] CR3: each CMT block parsed on its own; a corrupt one no longer loses the others
- [x] gps_from_ifd: None for non-finite (0/0 rationals gave NaN on base) or out-of-range (|lat| > 90, |lon| > 180)
- [x] formats.subsec decodes bytes as ASCII (NULs dropped; non-ASCII -> no fraction)
- [x] golden re-run: 152 identical, 77 differ, all intended (adds cr3_bad_cmt1, rw2_far_preview, gpsbad_*)

## Found
- `gt folders --ext .arw` matched nothing (no dot stripping, unlike `run`); both now share `formats.parse_ext`.
- `gt.read_exif` (own-tier CSV, `run --lat` default) uses exiftool, which already reads every format; left as is.

## W3 checklist (folded from TASKS-w3.md)


Checklist for this work package; the v1.5 section of TASKS.md links here.
Scratch: `$SCRATCH/v15-w3/` (golden recordings, label rebuild, map build inputs, fake-model dry run).

- [x] golden recording from a clean `git archive 0a66f72`: 300 random frames x 5 option sets, batched in random
      chunks and one by one (asserted equal), test_batch-style stand-ins with matrix-aware BioCLIP -> sha 1d89c01b8af47833
- [x] mdd_map.csv: BirdNET geo v3.0.4 mammal labels -> MDD v2.5 rows (1028 exact, 20 reviewed MDD synonyms;
      4 lump rows); all 1,048 mammal labels used; label list rebuilt offline = birdnet 1.1.1's (all 10,383 bird map labels found)
- [x] LocationPrior: several labels per row (max p); unlabelled policy zero | genus (+ UNLABELLED_NEUTRAL); `direct` mask
- [x] names: mammal ListSource.label_map = mdd_map.csv, unlabelled policy per list; labels-only map leaves the cache key
- [x] rules: range_veto (RANGE_EPS 0.01, RANGE_TAU 0.05; genus back-off never vetoes), species_level(species_ok), kind_of (KIND_SURE 0.75)
- [x] pipeline: range veto, two-way kind check (iterates taxa.KIND_CHECK), mammal_geo switch; batched + one-frame paths
- [x] switches as identify options (range_veto, kind_check, mammal_geo; default on); /products schema; eval `--identify-opt`
- [x] fingerprint: new thresholds (auto), switches, prior switch, unlabelled policies, neutral constant, KIND_CHECK; info() priors per list
- [x] tests: veto cases, kind switch both ways, thin margin, third list, batched == one-by-one (on and off), lumps,
      genus back-off, bird posterior unchanged by the mammal prior, fingerprint moves, labels-only map, map build
- [x] real-model smoke: mammal labels from mdd_map.csv; switches-off pass replaying the main run's model outputs;
      on/off table + changed images in the report; fails on a lost Top-1 hit or an added confident error per kind.
      Dry-run on fake models in the container (flow only; numbers meaningless)
- [x] equivalence: switches OFF == base golden, byte-identical (sha 1d89c01b8af47833); ON diff summary (every change attributed)
- [x] docs: README + README.zh-CN section and flags, CONTEXT.md terms, data/README (mdd_map inputs, reviewed synonyms)
- [x] self-review against base
- [x] orchestrator follow-ups: kind evidence from each list's top KIND_TOP rows (size-independent; padding test);
      label map sha in info() `models.label_maps` and in the fingerprint; real-model on/off gate allows 1 image per
      kind and measure (tripwire; harness budget is the gate). Switches-off golden still 1d89c01b8af47833
- [ ] CI (ci.yml, models.yml) on the pushed head: orchestrator (this branch is not pushed)
- [ ] Mac eval: `bioscan eval` golden set on, then each switch off (`--identify-opt NAME=false`)

## Found
- The range veto can rename a real vagrant to a local congener (level genus, never species); kept per spec, measurable.
- Genus back-off inherits absence: *Lepus californicus* / *Sylvilagus audubonii* (golden, unlabelled) borrow the p_geo of
  L. americanus/europaeus and S. floridanus/palustris, which are low in lowland California; hence a borrowed p_geo never vetoes.
- Unlabelled AviList rows (zero policy) count as direct evidence of absence and can be vetoed; no golden or own-tier
  bird species is unlabelled.
- The kind check keeps a stacked bird+mammal matrix (18,035 x 1024 float32, ~74 MB) plus its device copy.
  (Superseded at the W4 merge: each list is scored with its own matmul and `rules.kind_evidence_logits`; no
  stacked matrix is kept.)
