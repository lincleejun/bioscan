# bioscan — Agent Operating Rules (Opus 5.5)
<!--
Source: claude.dev "Getting the most out of Opus 5.5" (Addy Osmani, 2026-09-22),
merged with lijun.02 Rule 1 (Think Before Coding) and Rule 10 (Checkpoint).
Template lives at ~/.claude/templates/agents-opus55.md — edit there, then re-copy.
-->

## Scope: Opus 5.5 only
These rules are tuned for Claude Opus 5.5 (model id `claude-opus-5-5`). If the running model is
anything else, say so in one line at the start of the session, ignore this file, and follow the
project's other instructions. Do not adapt these rules to another model on your own.

## Think before coding
State assumptions explicitly before the first edit. If the request has more than one reasonable
interpretation, list them. Push back when a simpler approach exists. If confused, stop and name
exactly what is unclear.

## Definition of done
A task is done only when: `uv run ruff check .` is clean, `uv run pytest` passes, the GitHub
Actions workflows (`ci.yml`: lint + tests, `models.yml`: real-model smoke) are green on the pushed
head, docs (README / data/README) match the behaviour, and no TODOs are left in touched files.
Accuracy claims need an eval run (`bioscan eval`) on real photos; without one, say "unverified".
Work toward that finish line; do not pause just to report progress.

## Keep going vs. stop and ask
- Keep going when a step needs no judgment from me. Put status notes in the same message as your next action.
- Never end a turn with "want me to continue?", a next step you name but don't take, or a list of choices that don't block the work.
- Ask, don't guess, whenever a choice would materially change the deliverable: give 2-3 one-line options and stop until I pick.
- Always stop before anything destructive: deleting or rewriting committed data (`data/names/*.csv`,
  `data/inat/groundtruth-inat.csv`, `data/groundtruth-own.csv`), deleting caches under
  `~/.cache/huggingface` or `~/.cache/bioscan`, force-pushing, changes outside this repo, and paid
  external API calls.
- If a test fails for a reason you can't explain, stop and ask.

## Long runs
- For anything longer than a few steps, keep a checklist in `TASKS.md`. Tick items when done, add anything new you find. That file, not the scrollback, is the source of truth.
- For audits, migrations, or reviews across many modules (`bioscan/service`, `bioscan/cli`, `scripts`, `tests`): one subagent per unit. Check each subagent's evidence before accepting it. Finish with one table: unit, result, evidence.

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

## Not needed on this model
No "think carefully" / "think step by step" lines in this file or in prompts. The model decides how much to think.

## Project facts
- Python 3.12, `uv`. Service deps (torch, transformers, open_clip) load lazily; the CLI stays import-light.
- Contract tests run the real Engine and identify pipeline on fake model adapters injected through
  `engine.Loaders` (`tests/contract/conftest.py`); app.py uses `frame`, `ensure`, `loaded`, `info`.
- Domain terms live in `CONTEXT.md`; use them in code, tests and docs.
- Real-model checks live in `tests/models/` and only run with `BIOSCAN_MODEL_TESTS=1` (CI `models.yml`).
