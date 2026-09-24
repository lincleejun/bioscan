# Geotag from GPX: synthetic scenarios (2026-09-24, v1.6 W6)

**What was measured:** `bioscan.geotag` on GPX tracks generated from the golden set's true positions and capture
times. **How:** `scripts/geotag_synth.py` with seed 7, then `bioscan bench geotag` (docs/harness.md), in the
development container. No models are involved and no real GPX was used. Seed 11 gives the same picture: pooled
median 7.3 m, within 100 m 97.3%, 8/8 bars pass. Timing: about 65 s to generate (~750 MB) and 85 s to score.
Updated after the W6 review: offset tie-break, a 3 h limit on the stood-still rule, and failed estimates counted.

```sh
uv run python scripts/geotag_synth.py data/inat/groundtruth-inat.csv --out runs/geotag-synth --seed 7
bioscan bench geotag runs/geotag-synth
```

## Input

- **Photos.** 1,624 of 1,625 golden photos; one row has no coordinates.
- **Outings.** 1,061 of them: same observer and day, split at a pause of more than 4 h or a speed above 40 m/s.
- **Capture times.** 888 have seconds. 721 have minute precision (`:00`); their seconds were drawn in the minute. 15
  are date-only; their time was drawn between 07:00 and 17:00.
- **Tracks.** Sampled every 1–10 s, with correlated GPS noise σ 3–10 m per axis.
- **Fix rule.** Defaults: max gap 1800 s, max span 200 m within 3 h, no extrapolation.

## Results

| scenario | photos | inside track | median m | p90 m | ≤ 100 m | ≤ 1 km | no fix | false fix | cell changed | offset method | offset error s (median / p90; failed) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| perfect | 1,624 | 1,624 | 6.9 | 14.8 | 100.0% | 100.0% | 0.0% | – | 1.0% | none | – |
| offset37 (camera +37 s) | 1,624 | 1,624 | 7.0 | 15.6 | 99.8% | 100.0% | 0.0% | – | 1.4% | 3 GPS photos | 4.7 / 14.7; 0 |
| dst (camera +1 h) | 1,624 | 1,624 | 6.9 | 15.4 | 99.6% | 99.8% | 0.2% | – | 1.2% | 3 GPS photos | 1.0 / 10.2; 0 |
| wrongtz (home time +3 h, no offset tag) | 1,624 | 1,624 | 6.9 | 14.9 | 100.0% | 100.0% | 0.0% | – | 1.0% | clock photo | 0.5 / 0.9; 0 |
| gaps (dropouts, auto-pause) | 1,624 | 1,292 | 10.3 | 208 | 79.3% | 98.8% | 0.5% | 0.0% | 8.1% | none | – |
| outside (track starts late / ends early) | 1,624 | 657 | 7.0 | 14.9 | 100.0% | 100.0% | 0.0% | 0.0% | 0.8% | none | – |
| multi (2–3 files, GPX 1.0 + 1.1, decoy) | 1,624 | 1,624 | 7.1 | 15.1 | 99.9% | 100.0% | 0.0% | – | 1.0% | none | – |
| **all** | 11,368 | 10,069 | **7.2** | **17.0** | **97.2%** | **99.8%** | **0.1%** | **0.0%** | **2.0%** | | **1.0** / 10; 0 |

The scorecard of the `geotag` tier (data/standards.toml) gives 8 pass, 0 fail.

Rates are over the photos inside the track (a photo there without a fix is a miss); `false fix` is over the photos
outside it. The offset error is |applied − true| for every outing where an estimate was due (reference or clock
photos); a failed estimate applies 0 and counts in full (none failed here). geotag's own error estimate `err_m` holds the true error for 73% of fixes, so read it as a one-sigma
figure, not a bound.

## What it means

- **Camera clock mistakes are recovered.** Without correction, a +37 s clock puts a walker about 40 m off, and +1 h
  or a wrong timezone puts every photo outside the track or kilometres away. With three photos that already have GPS,
  or one photo of a clock, the result is as good as a perfect clock.
- **Ambiguous offsets exist** when the reference photos sit where the track passes more than once (a photographer
  circling one spot). Among equally good fits, `offset_prior` takes an offset under 5 min (plain drift, smallest
  first), then whole hours (DST, timezone), then half hours, then quarter hours, and a warning says so.
  - The first version preferred the smallest |offset|. `dst` then had a median offset error of 19 s, and 415 outings
    were off by more than 30 s.
  - The second preferred quarter-hours alone. That picked +1816 s for a 37 s clock (g0283), and 8 outings were off by
    more than 5 min.
  - Now 2 of 3,183 outings are off by more than 5 min. Both are in `dst` (g0682: −141 s; g0781: +7299 s against
    +3600 s), both are flagged ambiguous, and their fits span −15 to +165 min. `tests/unit/test_geotag_bench.py`
    rebuilds g0283 from the committed golden CSV.
- **Dropouts cost precision, not coverage.** With the 30-minute gap limit, 98.8% of the photos in `gaps` are still
  within 1 km (p90 208 m), and `err_m` grows with the gap. With a 5-minute limit, 29% had no fix; with 15 minutes,
  14%. The default favours a coarse fix, because the location prior works on ~1 km cells. Across a longer gap, the
  "stood still" rule (ends within 200 m) holds for at most 3 h (`--max-still`): long enough for a wait at a hide,
  never across a night at base camp.
- **No false positives.** Photos taken outside the recorded track get no fix (0 of 967). `--extrapolate N` would trade
  some of that for coverage near the ends.

## Downstream: the location prior

- **Week.** Geotagging never changes a photo's capture time, so BirdNET's week is unchanged by construction.
- **Cell.** The place moves the prior only if the fix and the truth fall in different 0.01° cells, the prior's cache
  key. That happens for 1.0% of photos with a perfect clock and 2.0% pooled. Adjacent 1 km cells give nearly the same
  range probabilities.
- **Expectation.** So species ID on GPX positions should match ID on true GPS, and gain what coordinates gain over no
  coordinates. The owner's earlier numbers put that at bird top-1 83.3% → 89.8%; docs/standards.md §4 disputes this
  gain (+2.4 vs +6.5).
- **Status: unverified.** The measurement needs real models and runs on the Mac. The commands are in docs/harness.md
  ("Downstream").

## Limits

- The tracks are generated. Real watches have multipath in canyons, cold starts, smoothing, and auto-pause rules
  that differ by brand, and the synthetic set models none of these.
- Minute-precision iNaturalist times were completed with drawn seconds, so the "true" capture times are synthetic
  for 44% of the photos. The tracks are built around those drawn times, so the evaluation is consistent, but it
  says nothing about the original seconds.
- Each outing has one camera. Mixed cameras with different clocks in one folder need one run per camera.
