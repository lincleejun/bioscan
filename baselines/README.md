# baselines

Committed `bioscan-report` files (report.json, schema in [docs/harness.md](../docs/harness.md)) that later runs
are compared with, plus the regression budget.

| File | What it is |
|---|---|
| `budget.toml` | The default regression budget for `bioscan bench compare` |
| `ci-smoke.json` | The real-model CI smoke (`tests/models`, 95 iNaturalist photos: 42 birds, 35 mammals, 18 other animals; CPU). `models.yml` compares every run with it. It is created from the first CI run: while it is missing, the harness step prints that run's report between `===== BEGIN bioscan-report ci-smoke candidate =====` markers and passes; commit that JSON here, from a run without the inference cache (a `v*` tag or a `cold` dispatch of `models.yml`), whose speed is real |
| `golden-inat-<tag>.json` | The full iNaturalist golden set (`data/inat/groundtruth-inat.csv`) at release tag `<tag>`, run on the owner's Mac |
| `own-raw-<YYYY-MM-DD>.json` | The owner's own RAW set (`data/groundtruth-own.csv`) on that date |
| `budget-album.toml` | The budget for album reports (tier `album`): plugin rules on keepers lost, reject recall and precision, burst F1, scene accuracy |
| `ci-album.json` (not yet) | The album profile on the synthetic reject set made from 24 smoke photos (`tests/models`, `scripts/cull_synth.py`). Created like `ci-smoke.json`: `models.yml` prints the candidate between `===== BEGIN bioscan-report ci-album candidate =====` markers until it is committed |

Names: lower case, `[a-z0-9._-]`, the set first, then the tag or date. One file per set and point in time;
a baseline is replaced only on purpose (`bioscan bench baseline ... --force`) and in its own commit that says why.

```sh
bioscan bench baseline runs/2026-09-24/report.json --name golden-inat-v1.5.0
bioscan bench compare baselines/golden-inat-v1.5.0.json runs/2026-10-01/report.json
```

Baselines are committed data: do not delete or rewrite one without the owner's approval (CLAUDE.md).
Image paths inside a report are the machine's own; compare pairs images by sha256 first, so a baseline made on
one Mac still pairs with a run on the same photos elsewhere.
