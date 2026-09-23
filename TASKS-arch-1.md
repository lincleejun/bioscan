# TASKS-arch-1 — one identify module, one seam: the three model adapters

Branch `arch/1-identify-module` from 7c011d5 (the worktree was checked out at 78af566; branched from the stated base).

## Assumptions (stated before the first edit)
- "Seam at the three model adapters" = the place where Engine builds its adapters (SigLIP2 gate/crop check,
  OWLv2 detector, BioCLIP species) plus the BirdNET prior model the per-kind priors are bound from. Tests
  inject fakes there (`engine.Loaders`); everything above (Engine.frame/ensure/loaded/info, products runner,
  pipeline) is the real code in the contract tests.
- The identify module's interface stays `pipeline.identify_many(models, frames, opts)`; `models` is the
  three adapters plus the data (names, priors). `BIOCLIP_BATCH` and `name_matrix` leave the seam:
  the batch size is the pipeline's own constant, the device copy of a name list's matrix is the BioCLIP
  adapter's business.
- Behaviour-preserving: identify output, /run events (envelope), CLI and exit codes unchanged; production
  loads the same models the same way. Pipeline edits limited to the seam (siblings 2 and 4 own the prior
  math and the output dicts); the `engine` parameter name in pipeline.py is kept to avoid conflicts.
- Interpretations considered: (a) keep Engine.identify_many and give the fake adapters to it (keeps a
  pass-through); (b) delete it and have the product runner call the identify module (chosen: deletion test
  says it is a pass-through); (c) collapse Engine into pipeline (rejected: loading/device/info is real work
  app.py needs and siblings 2/3/5 edit around it).

## Checklist
- [x] golden identify outputs before any edit (scratchpad arch-1/golden-before.json, sha 6fa4f1eb…)
- [x] contract-scenario NDJSON before (scratchpad arch-1/events-before.json)
- [x] baseline: ruff clean; pytest 141 passed, 8 skipped
- [x] engine.py: `Loaders` (adapter factories) injected into Engine; delete identify_many / identify /
      name_matrix / _matrices / BIOCLIP_BATCH / EngineProtocol
- [x] adapters/bioclip.py: probs() takes the NameList matrix; `place()` keeps its device copy
- [x] pipeline.py: Models protocol = siglip2, owlv2, bioclip, names, priors; SPECIES_BATCH constant
- [x] products.py: identify runner calls pipeline.identify_many
- [x] contract conftest: FakeEngine -> fake adapters under the real Engine; test_run.py assertions at the seam
- [x] test_engine_protocol.py -> test_adapter_seam.py (fakes match the real adapters; Engine meets pipeline.Models)
- [x] unit stand-ins: drop BIOCLIP_BATCH / name_matrix (test_batch, test_detail, test_rules)
- [x] tests/models/test_real_models.py on the new interface (Loaders, pipeline.identify_many spy);
      mechanics checked on fakes (scratch spy_check.py), real run unverified here (no weights)
- [x] verify BioCLIP.probs numerically identical (torch, CPU) old vs new: array_equal for N = 2, 9000, 11234
- [x] README / README.zh-CN layout line
- [x] golden after == before, byte-identical (sha 6fa4f1eb…, re-recorded from a clean export of 7c011d5);
      contract events: envelope identical in 7 scenarios, differences only in result.engine (real info)
      and products.identify (real pipeline output with fake adapters)
- [x] ruff clean; pytest x3: 137 passed, 8 skipped (baseline 141/8: the replaced guard had 12 parametrized
      cases, its replacement 8)
- [x] self-review vs 7c011d5: one gap fixed -- name-list matrices were copied to the device lazily, which
      would turn a device-copy failure from a 503 into per-image identify errors; the production species
      loader places them at load again (test_production_species_loader_places_name_lists_at_load)

## Found (for the integrator)
- CLAUDE.md "Project facts" still says the contract tests use a fake engine whose contract is
  frame/identify/ensure/loaded/info; not edited (agent may not change CLAUDE.md).
- pipeline.identify (one-frame convenience, re-exported as products.identify) is used only by unit
  tests; kept, since sibling tests (test_rescue, test_detail, test_batch) call it.
