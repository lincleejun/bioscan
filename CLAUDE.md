# bioscan — Agent Operating Rules

These rules apply to every agent and model working in this repo (Claude Code, other agents, humans
using agents). Model-specific tuning, if ever needed, goes in a separate file and never switches
these rules off. Where a rule can be checked by a tool, the tool is the source of truth: `uv run ruff
check .`, `uv run pytest`, the CI workflows and the harness (`bioscan eval` / `bioscan bench`).

## Think before coding
State assumptions explicitly before the first edit. If the request has more than one reasonable
interpretation, list them. Push back when a simpler approach exists. If confused, stop and name
exactly what is unclear.

## Definition of done
A task is done only when: `uv run ruff check .` is clean, `uv run pytest` passes, the GitHub
Actions workflows (`ci.yml`: lint + tests, `models.yml`: real-model smoke) are green on the pushed
head, docs (README / data/README / docs/standards.md) match the behaviour, and no TODOs are left in
touched files. Accuracy claims need an eval run on real photos; without one, say "unverified".
A change that can move identification results is compared against a baseline with the harness
(`bioscan bench compare`); a regression beyond the budget blocks the change unless the owner accepts it.
Work toward that finish line; do not pause just to report progress.

## Keep going vs. stop and ask
- Keep going when a step needs no judgment from the owner. Put status notes in the same message as your next action.
- Never end a turn with "want me to continue?", a next step you name but don't take, or a list of choices that don't block the work.
- Ask, don't guess, whenever a choice would materially change the deliverable: give 2-3 one-line options and stop until the owner picks.
- Always stop before anything destructive: deleting or rewriting committed data (`data/names/*.csv`,
  `data/inat/groundtruth-inat.csv`, `data/groundtruth-own.csv`, `baselines/`), deleting caches under
  `~/.cache/huggingface` or `~/.cache/bioscan`, force-pushing, changes outside this repo, and paid
  external API calls.
- If a test fails for a reason you can't explain, stop and ask.

## Long runs
- For anything longer than a few steps, keep a checklist in `TASKS.md`. Tick items when done, add anything new you find. That file, not the scrollback, is the source of truth.
- For audits, migrations, or reviews across many modules (`bioscan/service`, `bioscan/cli`, `scripts`, `tests`): one subagent per unit. Check each subagent's evidence before accepting it. Finish with one table: unit, result, evidence.
- Parallel agents each work in their own worktree and their own scratch folder; never share scratch files.

## Reporting
End every run with these headings, in order:
- **Blocked on me**: decisions left open, approvals needed. Empty if none.
- **Changed**: each item tagged `verified (how)` or `unverified`.
- **Found**: anything you couldn't confirm, and where you looked.
- **Left**: only if the task is not done; what remains and why.
Explain choices in plain language ("why this approach, in three sentences"). Never reproduce internal reasoning.

## Code review
Before handing a diff to a human, review it against `main`. List only problems
you'd block the merge for. For each: file and line, why it's wrong, how to show it fails.

## Product decisions (2026-09-24)
- Open source first; commercial use is not a goal for now.
- The service stays stateless (no persistent store); state lives in files the user asks for (preds, baselines).
- Identification is all-taxa by default. A caller may narrow it to candidate taxa (e.g. "A, B or C") as a
  speed/accuracy aid, never as a requirement.
- Where a list has no location label for a row, fall back to its genus; a box may move between kinds
  (bird <-> mammal) when the species evidence says so.
- Targets live in `docs/standards.md` (industry bar, our status, how measured); the harness reports against them.

## Default workflow (2026-09-24)
When the owner gives a folder of photos and says nothing else, this is the job, start to finish:
1. Service: `uv run bioscan --url http://127.0.0.1:8767 health`; if it fails, start one from this checkout:
   `uv run bioscan serve --port 8767 --allow-root DIR` (background, log in the scratchpad). Stop it when done if you started it.
2. Run, one request, names and aesthetics together, all three exports:
   `uv run bioscan --url http://127.0.0.1:8767 aesthetic score DIR --species --export json,csv,html --out DIR/bioscan`
   (top level only unless told `-r`; add `--lat/--lon` when the photos have no GPS and the place is known).
3. Report the run's own summary (scored / named / failed, score range, star cuts, top taxa, scene counts),
   open `DIR/bioscan.html`, and list names that look out of place for the location.
4. If the flow cannot do what was asked, change the code (with tests and docs), then run it.
5. When the work is done and the definition of done holds: commit on a `claude/<topic>` branch, push, open a PR
   to `main` with `gh pr create`, and watch `ci.yml` and `models.yml` until green. Report the PR link.

## Project facts
- Python 3.12, `uv`. Service deps (torch, transformers, open_clip) load lazily; the CLI stays import-light.
- Contract tests run the real Engine and identify pipeline on fake model adapters injected through
  `engine.Loaders` (`tests/contract/conftest.py`); the service (app.py, run.py) uses `frame`, `ensure`, `loaded`, `info`.
- Domain terms live in `CONTEXT.md`; use them in code, tests and docs.
- Real-model checks live in `tests/models/` and only run with `BIOSCAN_MODEL_TESTS=1` (CI `models.yml`).
- The container used for agent development has no Hugging Face / BirdNET network; real-model numbers come
  from CI (`models.yml`) or the owner's Mac.
