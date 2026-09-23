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
- [ ] Mutation check: renaming a field in the contract, pipeline, fake or readers fails a test
- [ ] ruff clean; pytest x3
- [ ] Self-review vs 7c011d5; fix blockers
- [ ] Commit; worktree clean
