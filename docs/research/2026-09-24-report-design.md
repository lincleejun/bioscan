# Run report: JSON first, then a page (design, 2026-09-24)

> Owner direction (2026-09-24): the JSON is the core; the HTML report is read only now and then, and once the
> pipeline runs unattended most runs will not be looked at. The report must answer: what the scan found by
> category, the results and species within each, and what is uncertain. Open question it must settle: does the
> user need to give feedback on the page. The page's look is to be designed, not defaulted.

This replaces the TASKS.md item "`bioscan report <preds.ndjson>`" (thumbnails grouped by top-1, species table,
review.csv). Built (2026-09-25): `bioscan summarize` and a read-only `bioscan report` ([usage.md](../usage.md#run-summary-and-report));
the feedback part (§3) is not. A clickable prototype of the page, filled with example data, was
published with this note; it has no real photos because this container cannot reach iNaturalist or the
owner's archive.

## 1. Three files, one direction

```
preds.ndjson ──bioscan summarize──▶ summary.json ──bioscan report──▶ report/index.html (+ thumbs/)
                                         ▲                                  │ reviewer
                                         └──── bioscan gt review ◀── review.json
```

- **`summary.json`** is the product. A **reducer** (CONTEXT.md: model-free, whole-run, CLI or offline, never
  in the service stream) reads a preds file and writes it. Standard library only, deterministic, so a golden
  test pins it. Everything a person or a later tool needs to know about a run is in it; the page adds nothing.
- **`report/index.html`** is a view of `summary.json` and nothing else. It never reads preds.ndjson, so the page
  cannot say something the JSON does not.
- **`review.json`** is what a person hands back. The service stays stateless: the verdicts live in a file the
  user saves, like preds and baselines.

For unattended runs, `bioscan summarize` also prints one line (`1,424 photos · 212 with animals · 31 species ·
9 to review`) and the page is only worth opening when "to review" is above zero.

## 2. `summary.json` (schema 1)

```jsonc
{
  "schema": 1, "kind": "bioscan.summary",
  "source": {"preds": "coyote-hills/preds.ndjson", "sha256": "…", "engine": {"version": "…", "settings": "…",
             "models": {…}}, "first_taken_at": "…", "last_taken_at": "…"},
  "counts": {"images": 1424, "ok": 1421, "failed": 3, "boxes": 305, "elapsed_ms": 812000},
  "categories": [                       // gate classes in this fixed order; every class present, zeros included
    {"class": "bird", "images": 180, "boxes": 251, "taxa": 24, "review": 6},
    {"class": "mammal", "images": 28, "boxes": 38, "taxa": 5, "review": 2},
    {"class": "other_animal", "images": 4, "boxes": 16, "taxa": 2, "review": 1},
    {"class": "person", "images": 37}, {"class": "none", "images": 1172}],
  "taxa": [                             // one row per named taxon, by boxes desc
    {"name": "Megascops kennicottii", "common": "Western Screech-Owl", "level": "species", "kind": "bird",
     "taxonomy": ["Animalia", …, "Megascops kennicottii"], "list": "avilist-2025",
     "images": 12, "boxes": 12, "posterior": {"max": 0.97, "median": 0.91},
     "best": {"sha256": "…", "path": "…", "box": 0},       // highest posterior × sharpness until C1 picks it
     "members": [{"sha256": "…", "path": "…", "box": 0, "posterior": 0.97,   // every box, so any one can be corrected
                  "top": [/* first 3 candidates, as in the payload */]}],
     "first_taken_at": "…", "last_taken_at": "…"}],
  "review": [                           // boxes (or frames) that need a person, grouped so one decision covers many
    {"sha256": "…", "path": "…", "box": 1, "kind": "bird", "level": "genus",
     "reasons": ["coarse_level"], "suggested": "Calidris", "top": [/* first 3 candidates, as in the payload */]}],
  "rules": {"single_sighting_max_posterior": 0.8},        // every threshold the reducer used, so the page can show it
  "errors": [{"path": "…", "product": null, "message": "…"}]
}
```

Choices, each derivable from today's payload (bioscan/contract.py) with no new model:

- **Categories are the gate classes** (bird, mammal, other_animal, person, none). An image counts once, under its
  gate class; a box counts under its own kind, which may differ (the kind check can move it). `taxa` and
  `review` count per category by box kind.
- **Taxa key on the name at the box's level**: a species-level box counts under its species, a genus-level box
  under the genus (taxonomy[5]), a family-level box under the family (taxonomy[4]). Unconfirmed boxes are not a
  taxon; they are review items.
- **Review reasons** are fixed codes, computed by rules, never by a model:

  | code | when | why a person should look |
  |---|---|---|
  | `unconfirmed` | level is unconfirmed | no name held up (rules.level: p ≥ 0.5 and margin ≥ 0.3 fail) |
  | `coarse_level` | level is genus or family | a person can often finish the name |
  | `out_of_range` | the first candidate's p_geo < `rules.RANGE_EPS` | the range veto fired; a vagrant or a wrong place |
  | `no_list` | species is null (a kind with no name list) | nothing can name it yet (TASKS: reptile/fish list) |
  | `gate_no_box` | gate class is an animal but no box survived | a missed detection, or a gate false positive |
  | `single_sighting` | the only box of its taxon in the run and posterior < 0.8 | a lifer or a misidentification |

  `soft` (subject not sharp) waits for C1's quality thresholds; it will be one more code, not a new mechanism.
- **Grouping**: review items sort by `suggested` taxon, then capture time, so "these 5 frames are all *Calidris*"
  is one decision on the page.
- **Recommendations are recorded, not only shown**: every member and review item keeps its first candidates
  (`top`), so what the page offered first is in the JSON and a later review can be scored against it.
- **No embeddings, no thumbnails** in the JSON. Paths and sha256 are enough; `bioscan report` makes the
  thumbnails (from the `jpg` product when present, else the embedded preview).

## 3. Feedback: asked on the review queue, possible everywhere

The page asks for a verdict only in the review queue, but any box on it can be corrected and any photo marked
for deletion. A confident result can still be wrong (owner, 2026-09-24: "what if the avocets are Snowy Egrets?"),
and those are the most valuable corrections: the pipeline did not know it was unsure.

- Most runs will not be looked at (owner direction), so feedback cannot be a chore over every photo. The review
  queue is the exception list: in the Coyote Hills run that would be a few dozen boxes, not 1,424 photos.
- A verdict is worth more than a glance: each one is a ground-truth row for the owner's own tier, which is the
  data the harness is short of (standards: own-tier numbers; culling research §5 wants owner labels too).
- Two separate questions, never mixed:
  - **Name** (per box): **Confirm**, **Correct to…** (type a name; checked against the loaded lists by
    `bioscan gt review`, not by the page), **Not an animal**, **Skip**. A review group has "Confirm all".
  - **Keep** (per photo): **Mark for deletion**. It is not a name verdict: a correct name on a blurry frame is
    still a correct name, and an uncertain name is no reason to delete a photo.
- **The name picker searches the full index, recommendations first.** A fixed short list would make the owner's
  answer depend on our guess. `bioscan report` writes `names.json` next to the summary: every row of the loaded
  lists plus their genera and families (AviList 2025 + MDD 2.5 today: 18,035 species, 3,739 genera, 421
  families, about 1.2 MB). The picker shows three sections: this box's candidates (with posterior), taxa already
  seen in this run (same kind), then the whole index searched by scientific or English name. A name in no list
  is still accepted, marked `typed`, and checked by `bioscan gt review`. The all-taxa TreeOfLife list is not in
  `names.json` (too large, and it lives in the model cache, not the repo); other animals get the typed path until
  a reptile/fish list lands (TASKS "not_in_list").
- Correcting outside the queue: open a taxon to see its members, then either correct one frame or pick frames
  and "Move to…" another name. Moving all of a taxon is the same action with every frame picked.
- Nothing is ever deleted by bioscan. `bioscan gt review` writes the deletion marks as a rejected label in XMP
  sidecars (the culling research's "mark, never delete"); the owner deletes in Lightroom or Finder.
- `review.json`: `{"schema": 1, "kind": "bioscan.review", "summary_sha256": "…", "verdicts": [{"sha256", "box",
  "verdict": "confirm|correct|not_animal|skip", "name", "level", "list", "picked_from": "candidates|seen|list|typed",
  "picked_rank", "was", "was_level", "note"}], "reject": [{"sha256", "path"}]}`. `picked_from` and `picked_rank`
  say where the right name was: when it was already the box's second candidate, that is a ranking miss; when it
  came from the full list, the model never offered it. Both are harness numbers. Keyed by
  sha256 and box id, so renamed or moved files still match.
- The page keeps a draft in the browser's local storage and saves `review.json` next to the report with a
  button (a Blob download, which works for a local file). `bioscan gt review report/review.json` turns it into
  ground-truth rows; writing them into `data/groundtruth-own.csv` is committed data and needs the owner's
  approval each time (CLAUDE.md).

## 4. The page

Order follows the questions a photographer asks after a day out, summary before detail:

1. **What did I get?** One sentence and a category strip: photos, photos with animals, taxa, items to review.
   The strip is proportional (1,172 empty frames make the 212 with animals visible as a share, not a list).
2. **What did I see?** The taxa, as a field-guide list: best frame, common name, Latin name in italics, level,
   count. Filter by category. Photos are the largest thing on the page.
3. **What needs me?** The review queue, grouped, with the reasons in plain words and the verdict buttons.
4. **The rest**: persons and empty frames as counts with a "show" toggle; errors in full.
5. **Run facts**: engine version, settings hash, models, the rules thresholds. Small, at the bottom.

Design rules, taken from the skills the owner pointed at and applied to this subject:

- Anthropic `frontend-design` [1]: ground the design in the subject; one bold element, everything around it
  quiet; structure must encode information (no numbered markers unless it is a sequence); avoid the usual
  generated looks (cream and terracotta, dark with a neon accent, card kits, tracked-out capitals).
- `taste-skill` [2] (MIT): its three dials, set for a review tool: design variance low, motion low (hover and
  focus only), visual density high. Its "no placeholder-looking UI" and "no em-dash" bans apply to our copy.
- `design-taste` [3] (merges taste-skill, emilkowalski/skill and pbakaus/impeccable): visible focus rings and
  keyboard review (J/K to move, C confirm, X not an animal), real interaction states on the verdict buttons.
- The subject's own conventions do the rest: the bold element is the photo; names follow field-guide usage
  (common name first, binomial in italics, genus capitalised); level is shown as a word (species / genus /
  family), never as a percentage badge, because level is what the pipeline actually asserts (CONTEXT.md:
  "Level", avoid "confidence"); the posterior appears only in the candidate detail. Both light and dark themes;
  dark is not the default, a photographer's culling app convention is not ours to assume.

## 5. How this is checked

- `summary.json`: unit tests on hand-built events for each review reason and the taxon keying, plus a golden
  summary of `tests/contract/golden/run-all-products.ndjson`. It moves no identification result, so no bench
  compare is needed.
- `review.json` → `bioscan gt review`: a round-trip test (verdicts in, ground-truth rows out, unknown names
  refused).
- The page: one test that it renders from a golden summary and that every taxon and review item appears; no
  pixel tests.

## 6. Open for the owner

- The `single_sighting` threshold (0.8) is a guess; the first real run should show whether it floods the queue.
- Whether `bioscan gt review` writes into `data/groundtruth-own.csv` or a separate `review` tier.

## Sources

[1] https://github.com/anthropics/skills/blob/main/skills/frontend-design/SKILL.md (fetched)
[2] https://github.com/leonxlnx/taste-skill (fetched; MIT)
[3] https://github.com/h3nryprod01/design-taste (fetched)
