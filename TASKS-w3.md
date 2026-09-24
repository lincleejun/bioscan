# TASKS — v1.5 W3 accuracy fixes (branch v15/w3-accuracy, base 0a66f72)

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
