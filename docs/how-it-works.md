# How it works

What bioscan is for, the pipeline, the accuracy rules, the models and data, the name mapping, and the known limitations. Commands are in [usage.md](usage.md).

## Goals and scope

In scope:
- Scan a directory in one pass and stream results as they are produced.
- Three products, in any combination: `identify` (boxes + species), `embed` (whole-frame SigLIP2 vector), `jpg` (RAW to upright JPG).
- **AviList 2025** (birds, 11131 species) and **MDD v2.5** (mammals, 6904 species) are the naming standards for birds and mammals; BirdNET, TreeOfLife/BioCLIP and iNaturalist names are mapped onto them through the tables in `data/names/`.
- All taxa by default: every other animal (reptiles, amphibians, fish, insects, spiders, …) is named from the TreeOfLife-200M all-taxa list. Naming candidate taxa first is optional (see [All taxa and candidates](usage.md#all-taxa-and-candidates)).
- Built-in evaluation: `bioscan gt` builds a ground-truth set (from folder names or iNaturalist), `bioscan eval` writes a report.

Out of scope (v1):
- Caching and persistent state, writing back human corrections, photo management, a web UI.
- Individual re-identification (the same animal across photos).
- Photo-library integration (Immich and others, later through the same HTTP API).

## Pipeline

```
RAW/JPG ─ decode ─▶ upright 2048 px image + EXIF (GPS, time) + sha256 (plus a ≤3072 px detail copy for identify)
              │
              ├─ SigLIP2 whole frame ─▶ gate: bird / mammal / other_animal / person / none   ─▶ embed product
              │
              ├─ OWLv2 open-vocabulary detection (vocabulary chosen by the gate; if the gate says
              │   none/person but the three animal classes total ≥ 0.25, detect anyway with the strongest
              │   animal class's words) ─▶ SigLIP2 check of each box crop ─▶ image quality
              │
              └─ BioCLIP 2.5 Huge on the same framing cut from the detail copy ─▶ kind check against the
                 bird, mammal and all-taxa lists (each list's best five names) ─▶ cosine against that kind's
                 name-list text vectors (other animals: the all-taxa list) × (0.02 + BirdNET location prior)
                 ─▶ normalise ─▶ top-k ─▶ range veto ─▶ grade
```

Grading: species when top-1 ≥ 0.5 and leads the runner-up by ≥ 0.3; otherwise genus when the top-5 summed by genus reaches ≥ 0.6, family when summed by family reaches ≥ 0.6; otherwise `unconfirmed`. The range veto and the kind check (next section) can lower that grade.

### Accuracy rules (v1.5)

Three fixes for confident species-level mistakes. Each is an `identify` option, on by default, so a run can switch one off and measure it:

| Option | What it does | Constants |
|---|---|---|
| `range_veto` | **Range veto.** Where the place is known and the list has a location prior, a top candidate whose own p_geo is below ε cannot be graded species. The congener with the highest posterior and its own p_geo ≥ τ, searched over the whole list (or the candidate rows), is listed first; when it was outside the top-k it takes the last slot, so `top` keeps its length (the only case where `top` is not in posterior order); the grade then comes from the genus/family roll-up. Fixes a Raven named as a Philippine crow in California. A p_geo borrowed from the genus (below) never vetoes. | `rules.RANGE_EPS` ε = 0.01, `rules.RANGE_TAU` τ = 0.05 |
| `kind_check` | **Kind check.** Each box's BioCLIP features (already computed) are also scored against every loaded kind-check list, each with its own matmul (no stacked matrix), and the box takes the kind whose 5 best names hold most of that visual evidence (the same number of names per list, so a longer list does not win by size). The lists are birds, mammals and, when it is loaded, the all-taxa list for other animals. Taking the same five names from each list removes most of the size effect between AviList (11k) and MDD (7k), but not between them and the ~366k-row all-taxa list, whose best five names score higher by chance alone; so the all-taxa list competes only for `other_animal` boxes. A box can move bird ↔ mammal, and an `other_animal` box can move to bird or mammal, against the gate and crop check (an owl gated mammal is no longer named as a skunk); a bird or mammal box never moves to other animal. A box that moved on less than 0.75 of the evidence is graded `unconfirmed`: any name above that would assert a kind the evidence cannot. The visual evidence decides, not the posterior, because the lists differ in prior coverage. Without the all-taxa list, `other_animal` boxes are left alone. Cost of the all-taxa list here: for `other_animal` boxes only, one extra matmul per box against its ~366k rows (about 30 ms a box on a 4-core CPU; on MPS an estimated 1–2 ms). | `rules.KIND_TOP` = 5, `rules.KIND_SURE` = 0.75; lists in `taxa.KIND_CHECK` |
| `mammal_geo` | **Mammal location prior.** The BirdNET geo model the service already loads also scores 1,048 mammals; `data/names/mdd_map.csv` gives them to MDD rows. An MDD row with no label gets the highest p_geo among labelled species of its genus (**genus back-off**), or 0.05 when its genus has none. Birds keep their rule: unlabelled rows get 0. | `geo.UNLABELLED_NEUTRAL` = 0.05; per-list policy `names.LISTS[...].unlabelled` |

**Trial: `kind_size_correct`** (identify option, off by default). A size-corrected kind check: before the lists are compared, each list's i-th best logit loses σ · E[i-th best of N standard normals], where N is the list's row count (or the rows candidates leave it) and σ the spread of that box's logits over the list (`rules.chance_top`, Blom's approximation via `statistics.NormalDist`: about 3.9 σ for the best of AviList's 11k rows, 4.6 σ for the ~366k-row all-taxa list). What is left is the evidence each list holds above what its size and spread give by chance, so a longer list no longer wins on size alone (on synthetic logits, a 20,000-row noise list beats a 1,000-row list in ~98 % of draws uncorrected and ~half corrected). It changes only the kind check's comparison, not the ranking within a list. `taxa.ONE_WAY` still applies with it on; it can replace that rule only after an on/off eval on real photos (`--identify-opt kind_size_correct=true`, then `bioscan bench compare`). Unverified on real photos.

All thresholds, the unlabelled policies, the label maps' contents and the option defaults (except a trial's, while it is off by default) are in the settings fingerprint; `result.engine.models.label_maps` names each label map with its sha. With all three options off, identify output is the same as v1.4 for birds and mammals (checked byte for byte on a 300-frame stand-in recording, 5 option sets). The whole output is the same only without the all-taxa list: with it, `other_animal` boxes get a species where v1.4 gave `null`. To measure one fix, run the same ground truth twice and compare the reports:
```sh
bioscan eval data/inat/groundtruth-inat.csv --out runs/<date>-on
bioscan eval data/inat/groundtruth-inat.csv --out runs/<date>-no-veto --identify-opt range_veto=false
```
Over HTTP, pass `"options":{"identify":{"kind_check":false}}`. CI's real-model smoke runs its photos with all three on and all three off and adds an on/off table plus every changed image to its report (`models-report`). It fails only if, for either kind, the options lose more than one Top-1 hit or add more than one species-level wrong answer: with about 38 photos a kind that is a tripwire, and the real gate is `bioscan bench compare` against the committed baseline with its regression budgets.

Models and data:

| Use | Source | License |
|---|---|---|
| Gate, crop check, embed | `google/siglip2-base-patch16-224` | Apache-2.0 |
| Detection | `google/owlv2-base-patch16-ensemble` | Apache-2.0 |
| Species | `imageomics/bioclip-2.5-vith14` (BioCLIP 2.5 Huge) | MIT |
| Species-name text vectors | official precomputed `imageomics/TreeOfLife-200M` vectors; unmatched names encoded with the text tower | CC0 |
| Location prior (birds, mammals) | BirdNET geo 3.0 (`birdnet` package) | CC BY-NC-SA 4.0 |
| Bird list | AviList v2025 | CC BY 4.0 |
| Mammal list | Mammal Diversity Database v2.5 | CC BY 4.0 |
| All-taxa list (other animals) | species-level Animalia rows of `imageomics/TreeOfLife-200M`, birds and mammals excluded, official vectors | CC0 |

The BirdNET prior model is licensed non-commercially; for commercial use, drop the prior or replace its source.

## Name mapping

`data/names/avilist_map.csv`: for each AviList species, its TreeOfLife name and BirdNET label and how each was matched (exact / synonym / none). `synonyms.csv` is the hand-maintained alias table, each row with a source and a note; `candidates.csv` lists suspected spelling differences found by the script, for human review only, never adopted automatically. Rebuild with `uv run python scripts/build_name_map.py`.

`data/names/mdd_map.csv`: for each MDD species, its BirdNET label(s) and how they matched. Only BirdNET labels of class Mammalia count. Matching is exact name first, then the MDD synonym table (reviewed; see `data/README.md`). MDD lumps some species that BirdNET splits (e.g. four white-fronted capuchins into *Cebus albifrons*); such a row lists every label joined by `|`, and the prior takes the largest. Rebuild with `uv run python scripts/build_name_map.py --list mammal --mdd-synonyms MDD/Species_Syn_Current_v2.5.csv`.

The 748 AviList species without a BirdNET label get 0 in the location prior (mostly extinct species or species BirdNET lumps with a sister, such as *Tyto javanica*, which should stay suppressed). To find the gaps that actually matter at a place:
```sh
bioscan names geo-gaps --lat 37.4 --lon -122.1 --date 2026-05-01   # unlabelled species whose genus occurs there
```
After review, add the row to `synonyms.csv` (source `birdnet`) and rebuild the map.

| List | Total | Official TreeOfLife vectors | BirdNET label |
|---|---|---|---|
| AviList 2025 | 11131 | 84.6% | 93.3% |
| MDD v2.5 | 6904 | 55.5% | 15.1% (1042 rows carry all 1,048 BirdNET mammal labels; the rest back off to their genus) |

## Known limitations and roadmap

- 45 mammal images in the golden set got no box (mostly bears, mountain lions, bobcats). The detector vocabulary was extended and a gate-miss rescue added; the effect awaits a `bioscan eval` rerun.
- Other animals have no location prior.
- Other animals use TreeOfLife's own names and taxonomy; `data/names/synonyms.csv` only maps AviList/MDD names, so an iNaturalist truth label spelled differently from TreeOfLife (a genus move, say) scores as a miss. Their accuracy is measured on 18 photos in CI only (see Tests).
- The mammal location prior, the range veto and the kind check are unverified on real photos until a `bioscan eval` on the Mac (CI's on/off table covers 95 photos only). ε, τ, the kind-check margin and the neutral constant are first guesses. With the all-taxa list loaded, the kind check also weighs it against birds and mammals; its effect on bird and mammal accuracy is measured only by CI's on/off table.
- The range veto can rename a genuine vagrant to a local congener (graded genus, never species).
- Recently split species (Northern / Hen Harrier, American / Western Barn Owl) carry old names in the training data and are separated by a shared vector plus the location prior; synonyms do not yet apply per region.
- The 0.02 floor in the prior formula limits how far location can override vision; it has not been tuned on the golden set.
- When the subject is tiny in the frame (distant raptors), the detector can box the wrong object.
- Only run on macOS + MPS and on CPU; no CUDA setup or Dockerfile.
