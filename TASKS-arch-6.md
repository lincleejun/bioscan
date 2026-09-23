# TASKS — arch 6: complete name lists from one load; one normaliser family

Branch `arch/6-name-lists` from 7c011d5. Report candidate 6 (Worth exploring).

## Before any edit
- [x] baseline: ruff clean, pytest 141 passed / 8 skipped
- [x] golden capture (outside the repo): load_lists on fixtures (none / synonyms / map / both / full committed map),
      fresh + cached, every field + sha + npz contents + logs; stats; map_problems; gt match_folder (23.5k folder
      names) + gt_folders CSV; eval.normalise_truth; normaliser outputs. Re-recorded from a clean 7c011d5 export.
- [x] caches written by the base code kept for the load-without-rebuild check

## Change
- [x] names.py: loader builds each NameList once with labels, label-match how and sha (no post-load patching)
- [x] names.py: bird/mammal difference as data (`ListSource.label_map`), not `kind == "bird"`
- [x] names.py: NameList frozen; drop the `noqa: F401` import (every name is used)
- [x] tests/models/test_real_models.py: build NameList complete (no field patching); frozen would break it
- [x] naming.py: folder-label variant (`norm_label`) next to norm/norm_binomial; gt.py uses it (gt.norm removed).
      Switching gt to norm_binomial was measured: 12 built-in and 828 index matches change, so the variant stays.
- [x] scripts import normalisation from bioscan.naming, not via the service package (build_names.py had none)
- [x] tests at the interfaces: finished + frozen lists, label map as data, norm_label cases, "Stellers Jay" kept unmatched
- [x] docs: README / README.zh-CN module map, data/README normaliser name (was the stale `geo._norm`)

## After
- [x] goldens identical (byte-for-byte JSON); base-built caches load with zero encodes; small_lists helper identical
- [x] ruff clean; pytest x3: 144 passed / 8 skipped
- [x] self-review vs 7c011d5: map warnings restored to before the list-CSV check (error-path log order)
