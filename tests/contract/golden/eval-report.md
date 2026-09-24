# bioscan eval report

- groundtruth: <tmp>/gt.csv
- preds: <tmp>/preds.ndjson
- images: 4
- geo: True
- preds schema: 1
- complete: True
- synonyms: <repo>/data/names/synonyms.csv
- engine: {"detail_edge": 3072, "models": {"detect": "google/owlv2-base-patch16-ensemble@cfd3195ba4ea", "gate": "google/siglip2-base-patch16-224@75de2d55ec2d", "geo": "birdnet-geo-3.0", "label_maps": {}, "names": {"bird": "fake-owls@test"}, "priors": {"bird": "birdnet-geo-3.0"}, "species": "imageomics/bioclip-2.5-vith14@6e3d04e3d652", "taxonomy": {}}, "settings": "c4b749b93b71", "version": "0.1.0"}

## Metrics by tier × kind

| tier | kind | n | failed | gate acc | detect | Top-1 | Top-5 | coverage | precision | decode ms (mean/median) | identify ms (mean/median) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| golden | bird | 2 | 0 | 100.0% | 100.0% | 50.0% | 100.0% | 100.0% | 50.0% | 0 / 0 | 0 / 0 |
| golden | mammal | 1 | 0 | 0.0% | 0.0% | 0.0% | 0.0% | 100.0% | 0.0% | 0 / 0 | 0 / 0 |
| own | bird | 1 | 1 | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | – | – | – |

Definitions: Top-1/Top-5 use the highest-`score` box; coverage = best box at `level == species`; precision = Top-1 hit rate among those; failed images count as misses everywhere.

Top-1 hits gained by synonym normalisation of the truth (miss -> hit): golden/bird 0, golden/mammal 0, own/bird 0

No box, by whole-frame gate class (none/person: the gate missed the animal; else the detector did): golden/bird 0; golden/mammal 0; own/bird 0

## Confusion Top-10 — golden / bird

| truth | predicted | count |
|---|---|---|
| Megascops asio | Megascops kennicottii | 1 |

## Confusion Top-10 — golden / mammal

| truth | predicted | count |
|---|---|---|
| Canis latrans | Megascops kennicottii | 1 |

## Confusion Top-10 — own / bird

| truth | predicted | count |
|---|---|---|
| Megascops asio | (missing) | 1 |
