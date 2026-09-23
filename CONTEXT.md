# bioscan

Local service and CLI that find birds and mammals in photos and name the species, for one photographer's
archive. Terms below are the ones code, tests and docs use.

## Photos and runs

**Run**:
One `POST /run` request: inputs, wanted products and options, answered as an NDJSON stream of events.
_Avoid_: job, batch

**Chunk**:
The slice of a run's images decoded together and given one model turn.
_Avoid_: batch (reserved for model calls)

**Model turn**:
A chunk's exclusive, first-come-first-served hold on the models; between turns other runs can interleave.
_Avoid_: model lock (the mechanism), slot

**Frame**:
One decoded image as identify sees it: the upright ≤2048 px image, its gate, place and date, and an optional detail copy.

**Detail copy**:
A larger (≤ detail edge) decode of the same photo used only for species crops; off when the detail edge is ≤ 2048.

**Product**:
A named result a run can ask for per image (`identify`, `embed`, `jpg`), declared in the product registry.

## Identify

**Identify payload**:
`result.products.identify`: the gate plus a list of boxes.

**Gate**:
The whole-frame class (bird, mammal, other_animal, person, none) with its probabilities.
_Avoid_: classifier, filter

**Gate rescue**:
Running the detector on a frame the gate called none/person when enough animal probability remains.

**Box**:
One detected animal: id, xyxy (fractions of the frame), score, kind, quality and optionally species.
_Avoid_: detection (the detector's raw output), bbox (pixels)

**Crop check**:
The gate model re-judging a box's crop, which can veto the box or promote its kind.
_Avoid_: judge, second gate

**Species**:
A box's naming result: the name list used, a level and the top candidates. Absent when species is off, null for a kind with no name list.

**Level**:
How far the evidence supports a name: species, genus, family or unconfirmed.
_Avoid_: grade, confidence

**Candidate**:
One ranked name for a box: scientific, common, taxonomy, p_visual, p_geo, posterior.

**Conformance**:
How a payload departs from the contract's fields (`contract.identify_problems`).

**Reader**:
A CLI-side accessor that tolerates partial payloads (`contract.gate_class_of`, `boxes_of`, …).

## Models

**Model adapter**:
The wrapper around one model: gate and crop check (SigLIP2), detector (OWLv2), species encoder (BioCLIP).
_Avoid_: backend, wrapper

**Adapter seam**:
`engine.Loaders`: where the service builds its model adapters and location prior source; tests pass fakes there.

**Identify module**:
`pipeline.identify_many(models, frames, opts)`; `models` is the three adapters plus name lists and location priors.

## Names

**Name list**:
One species list in the species encoder's text space, loaded finished and frozen: AviList for birds, MDD for mammals.
_Avoid_: vocabulary (that is the detector's words), taxonomy

**List source**:
Where a name list comes from: id, data folder, taxonomic class, reader and label map (`names.ListSource`).

**Label map**:
The CSV giving each list row its TreeOfLife name and BirdNET label (`data/names/avilist_map.csv`).

**BirdNET label**:
A name-list row's label in the location prior source; empty when the row has none.

**Match key**:
The normalised form two names are compared by: `norm_binomial` for scientific names, `norm_label` for folder names.

**List sha**:
The hash of a name list's inputs; it names the list's cache file.
_Avoid_: cache key

## Location prior

**Location prior**:
How likely each row of one name list is at a place and date (`geo.LocationPrior`).
_Avoid_: geo filter, range map

**Prior source**:
The model behind a location prior: `labels` plus `probs(lat, lon, week)`; today BirdNET geo 3.0, birds only.

**p_geo**:
One row's location prior value at a place and date; none when the place is unknown or the source fails.

**Posterior**:
p_visual × (floor + p_geo) renormalised over the whole name list, before top-k.

**Geo gap**:
A row with no BirdNET label whose genus is present at a place; fixed by a reviewed synonym, not a blanket rule.

## Serving

**Serve config**:
The resolved settings one service process runs with (host, port, decode workers, chunk, detail edge, allow roots).

**Resolve**:
Flag, else `BIOSCAN_*` variable, else default, done once per process (`serve_config.resolve`).

**Allow roots**:
Directories the service may read from and write to; empty means no limit.

**Settings fingerprint**:
Twelve hex characters over every output-changing constant (thresholds, prompts, vocabulary, prior floor, detail edge).
