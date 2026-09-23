# arch/4: give the identify output one owner

Branch `arch/4-identify-output` from 7c011d5. Goal: `bioscan/contract.py` owns the identify payload
(gate, box, quality, species, candidate): field names, stdlib constructors, tolerant readers and a
conformance check. Output JSON, /products JSON, /run events, render and eval output stay identical.

## Assumptions
- Constructors only assemble fields (like the event constructors). Rounding and the choice of values
  stay where they are today (pipeline, rules.quality), so the siblings' lines keep their meaning.
- The box constructor does not set `species`: it is absent when `species` is off, null for a kind with
  no name list, and an object otherwise. The pipeline keeps filling it in place (key order unchanged).
- The CLI keeps reading defensively (partial and older streams); each reader reproduces one `.get` chain.
- /products must stay byte-identical: the identify "output" doc moves into contract.py verbatim, and a
  test ties it to the field names instead of generating new prose.

## Checklist
- [x] Base commit 7c011d5 checked out on `arch/4-identify-output` (worktree was created at 78af566)
- [x] Goldens recorded before any edit (scratchpad/arch4/before): identify x1367, render, eval, /products, /run
- [x] Baseline: ruff clean; pytest 141 passed, 8 skipped
- [x] contract.py: Gate, Box, Quality, Species, Candidate, Identify TypedDicts + constructors
- [x] contract.py: readers (identify_of, gate_class_of, boxes_of, species_of, level_of, top_of)
- [x] contract.py: identify_problems (conformance) + IDENTIFY_OUTPUT doc for /products
- [x] pipeline.py: gate/box/candidate/species built through contract (output-dict lines only)
- [x] rules.quality returns contract.quality
- [x] products.py: identify "output" = contract.IDENTIFY_OUTPUT
- [x] render.py / eval.py read through contract readers
- [x] conftest FakeEngine builds its payload with contract constructors
- [x] tests: conformance over pipeline output, /run output, doc <-> fields, reader round-trip (+1 assert in tests/models)
- [x] README (+ zh-CN): contract.py description
- [x] Goldens regenerated and byte-identical
- [x] Mutation check: 10 one-sided field renames (contract, pipeline, rules, render, eval, reader) all fail a test
      (first run missed rules.quality's under-8 px return; added test_quality_has_the_contract_fields)
- [x] BEFORE re-recorded from a pristine `git archive 7c011d5` export (the scratchpad is shared); equal to before and after
- [x] ruff clean; pytest x3: 153 passed, 8 skipped
- [x] Self-review vs 7c011d5: no blockers; fixed a stray "." in identify_problems messages
- [x] Commit; worktree clean

## Found along the way
- Worktree was created at 78af566, not 7c011d5; branch started from 7c011d5.
- Merge overlap to expect: pipeline candidate/species lines (sibling 2 edits p_geo/posterior there),
  conftest FakeEngine.identify (sibling 1 replaces it); test_identify_contract imports test_batch.Models.
