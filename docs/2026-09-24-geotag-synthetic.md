# Geotag from GPX: synthetic scenarios (2026-09-24, v1.6 W6)

**What was measured:** `bioscan.geotag` on GPX tracks generated from the golden set's true positions and capture
times. **How:** `scripts/geotag_synth.py` with seed 7, then `bioscan bench geotag` (docs/harness.md), in the
development container. No models are involved and no real GPX was used. Seed 11 gives the same picture: pooled
median 7.3 m, within 100 m 97.1%, 8/8 bars pass.

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
- **Fix rule.** Defaults: max gap 1800 s, max span 200 m, no extrapolation.

## Results

| scenario | photos | inside track | median m | p90 m | ≤ 100 m | ≤ 1 km | no fix | false fix | cell changed | offset method | offset error s (median / p90) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| perfect | 1,624 | 1,624 | 6.9 | 14.8 | 100.0% | 100.0% | 0.0% | – | 1.0% | none | – |
| offset37 (camera +37 s) | 1,624 | 1,624 | 7.0 | 15.7 | 99.4% | 99.6% | 0.4% | – | 1.5% | 3 GPS photos | 4.7 / 14.7 |
| dst (camera +1 h) | 1,624 | 1,624 | 6.9 | 15.5 | 99.6% | 99.9% | 0.1% | – | 1.3% | 3 GPS photos | 1.3 / 11 |
| wrongtz (home time +3 h, no offset tag) | 1,624 | 1,624 | 6.9 | 14.9 | 100.0% | 100.0% | 0.0% | – | 1.0% | clock photo | 0.5 / 0.9 |
| gaps (dropouts, auto-pause) | 1,624 | 1,292 | 10.3 | 208 | 79.3% | 98.8% | 0.5% | 0.0% | 8.1% | none | – |
| outside (track starts late / ends early) | 1,624 | 657 | 7.0 | 14.9 | 100.0% | 100.0% | 0.0% | 0.0% | 0.8% | none | – |
| multi (2–3 files, GPX 1.0 + 1.1, decoy) | 1,624 | 1,624 | 7.1 | 15.1 | 99.9% | 100.0% | 0.0% | – | 1.0% | none | – |
| **all** | 11,368 | 10,069 | **7.2** | **17.0** | **97.2%** | **99.8%** | **0.1%** | **0.0%** | **2.0%** | | **1.0** / 10.2 |

The scorecard of the `geotag` tier (data/standards.toml) gives 8 pass, 0 fail.

Rates are over the photos inside the track (a photo there without a fix is a miss); `false fix` is over the photos
outside it. geotag's own error estimate `err_m` holds the true error for 73% of fixes, so read it as a one-sigma
figure, not a bound.

## What it means

- **Camera clock mistakes are recovered.** Without correction, a +37 s clock puts a walker about 40 m off, and +1 h
  or a wrong timezone puts every photo outside the track or kilometres away. With three photos that already have GPS,
  or one photo of a clock, the result is as good as a perfect clock.
- **Ambiguous offsets exist** when the reference photos sit where the track passes more than once (a photographer
  circling one spot). The estimate then prefers offsets near whole quarter-hours and says so in a warning. Before that
  rule, `dst` had a median offset error of 19 s and 415 outings off by more than 30 s.
- **Dropouts cost precision, not coverage.** With the 30-minute gap limit, 98.8% of the photos in `gaps` are still
  within 1 km (p90 208 m), and `err_m` grows with the gap. With a 5-minute limit, 29% had no fix; with 15 minutes,
  14%. The default favours a coarse fix, because the location prior works on ~1 km cells.
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
