# TASKS — arch 2: deepen the location prior

Branch `arch/2-location-prior`, base 7c011d5. Behaviour-preserving: identify output (every p_geo /
posterior), /run events, info() fields, geo-gaps CLI output, exit codes unchanged.

- [x] Baseline: ruff clean; pytest 149 collected / 141 passed / 8 skipped (models)
- [x] Golden outputs recorded before any edit (outside the repo): identify over 300 random frames via
      test_batch stand-in `Models` (+ GeoPrior-wrapped fake BirdNET, failing sources, geo on/off, top_k
      1/3/5/12, single-frame path), engine `_load_bioclip` priors + info() with a fake `birdnet` module
      (ok / load fails / no labels), geo-gaps CLI stdout over the real data/names/avilist_map.csv with a
      fake BirdNET source (6 places/dates x bare/GeoPrior-wrapped)
- [x] geo.py: one `LocationPrior` per name list (`p_geo`, `posterior`, `gaps`); BirdNET `GeoPrior`
      stays the adapter behind it; `priors_for(lists)` binds one per list; `PriorBinding`, `align`,
      `GeoPrior.index`, module `posterior`/`gaps` removed (replace, don't layer)
- [x] engine.py `_load_bioclip` / `geo` use `priors_for` / `LocationPrior.source`
- [x] pipeline.py `_species_many`: prior lookup and posterior lines only (+ the `priors` comment in `Models`)
- [x] cli/main.py `cmd_names_geo_gaps` binds a `LocationPrior` to the map rows
- [x] Tests moved to the new interface (test_rules, test_batch, test_geo_gaps); none deleted; added
      p_geo (row order, week, no place, failing source), priors_for, gaps-raises tests
- [x] Golden outputs regenerated through the new interface: byte-identical (sha256 d64f6578...), and
      identical to a BEFORE re-recorded from a clean 7c011d5 checkout
- [x] ruff clean; pytest green 3x (152 collected / 144 passed / 8 skipped)
- [x] Self-review diff vs 7c011d5: no blocking findings
- [x] Docs: README layout / data/README still accurate (no names changed that they cite); no TODOs in touched files
- [ ] Integration (orchestrator): sibling 1 stand-ins that build `geo.PriorBinding` must switch to
      `geo.LocationPrior(source, labels)`; real-model CI (`models.yml`) on the merged head
