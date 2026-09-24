# TASKS — v1.5 W3 accuracy fixes (branch v15/w3-accuracy, base 0a66f72)

Checklist for this work package; the v1.5 section of TASKS.md links here.
Scratch: `$SCRATCH/v15-w3/` (golden recordings, label rebuild, map build inputs).

- [ ] golden recording from a clean `git archive 0a66f72` (300 random frames x 5 option sets, batched + one by one)
- [ ] mdd_map.csv: BirdNET geo v3.0.4 mammal labels -> MDD v2.5 rows (exact, then reviewed MDD synonyms; lumps = several labels)
- [ ] LocationPrior: several labels per row (max p); unlabelled policy zero | genus (+ neutral constant)
- [ ] names: mammal ListSource.label_map + unlabelled policy; mammal cache key unchanged
- [ ] rules: range veto (RANGE_EPS, RANGE_TAU), species level without "species", kind-check thresholds
- [ ] pipeline: range veto, two-way kind check (generic over taxa.KIND_CHECK lists), mammal_geo switch
- [ ] switches as identify options (range_veto, kind_check, mammal_geo); /products schema; eval `--identify-opt`
- [ ] fingerprint: switches, unlabelled policies, neutral constant; info() priors per list
- [ ] tests: veto, kind switch both ways, batched == one-by-one, lumps, genus back-off, bird posterior unchanged, fingerprint
- [ ] real-model smoke: mammal labels from mdd_map.csv; ON vs OFF ablation table in the report
- [ ] equivalence: switches OFF == base golden (byte-identical); ON diff summary
- [ ] docs: README section, CONTEXT.md terms, data/README
- [ ] self-review against base
