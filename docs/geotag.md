# Geotag from a GPX track

Most camera bodies write no GPS. If you record the outing with a watch or phone and export a GPX track, bioscan places each photo on the track at its capture time. This does the same job as Lightroom's "auto-tag photos" map module. The location matters: on the golden set (v1.5 build, 2026-09-25, [results.md](results.md)), top-1 without coordinates is 86.0% for birds and 77.7% for mammals; with the positions a GPX track gives, 91.2% and 82.8%, the same as with the true GPS to the image (0 answers differ).
```sh
bioscan geotag DIR --gpx hike.gpx --tz America/Los_Angeles --csv geo.csv   # path,lat,lon,source,dt_s,err_m,utc,ele
bioscan geotag DIR --gpx a.gpx --gpx b.gpx --offset +00:01:23 --xmp       # camera 83 s fast; write <stem>.xmp sidecars
bioscan geotag DIR --gpx hike.gpx --clock DIR/DSC0001.ARW=2026-05-01T08:00:13   # a photo of the watch showing 08:00:13
bioscan run DIR --gpx hike.gpx --tz=-07:00             # per-image coordinates for identify (EXIF GPS still wins; no exiftool needed)
```
- **Sources.** Each photo gets one of `exif`, `gpx` or `none`:
  - `exif`: the file already has GPS. EXIF always wins.
  - `gpx`: the track placed the photo.
  - `none`: the photo is outside the track or has no capture time.

  The CSV also gives `dt_s` (seconds to the nearest track point), `err_m` (an estimate of the error; about 3 fixes in 4 fall within it on the synthetic set) and the corrected UTC time.
- **Time.** GPX times are UTC; camera times are local wall time. A file's OffsetTimeOriginal is used when present. Otherwise the time is read in `--tz`: a fixed offset, or a zone name, which applies the right DST for each date. The default is this computer's zone. Write negative values with `=`, e.g. `--tz=-07:00` and `--offset=-3600`, or argparse takes them for options.
- **Clock offset** (camera time minus true time). It comes from the first of these that applies:
  1. `--offset`;
  2. a photo of a clock: `--clock PHOTO=TIME`, the time the clock shows, read in the photo's zone;
  3. an estimate from photos in the folder that already have GPS (a phone photo, or a camera with a GPS link). The estimate finds the offset at which those photos sit on the track. It is rejected when they sit more than 100 m off. When several offsets fit equally well, one under 5 min wins (plain drift), then whole hours, half hours and quarter hours (timezone and DST mistakes), and a warning says the fit was ambiguous. The estimate is skipped when every photo already has GPS;
  4. otherwise 0.

  The offset is applied once per run, so run one camera at a time. When most photos fall outside the track, a warning says how far off they are; a whole number of hours means a timezone mistake.
- **Fix rule.** The position is linear in time between neighbouring track points up to `--max-gap` seconds apart (default 1800). Across a longer gap, it is linear only when the gap's ends are within `--max-span` metres of each other (default 200: the watch auto-paused while you stood still) and at most `--max-still` seconds apart (default 3 h: a wait at a hide, not a night at base camp). Outside the track there is no fix, unless you allow `--extrapolate N`: then the first or last point is held for N seconds.
- **XMP.** `--xmp` writes `<stem>.xmp` holding `exif:GPSLatitude`/`GPSLongitude` in XMP. This is the sidecar Lightroom, Capture One and Bridge read for RAW files; Lightroom ignores sidecars of JPEGs. A photo that already has a sidecar (`<stem>.xmp`, or darktable's `<name>.<ext>.xmp`) is left alone: bioscan never edits or merges an existing sidecar, and never writes into the photo file. Use the CSV with exiftool if you need to change existing files.
- **Several tracks.** Several `--gpx` files and segments merge into one time-ordered track. A second device recording at the same time just adds points.
- **Where `run --gpx` places the photos.** With a profile that runs the `geotag` stage (`--profile wildlife`), the CLI sends `options.geotag` (the track paths, `--tz` as `camera_utc_offset`, the limits you changed, and the clock offset, which it decides once for the whole folder from `--offset`, `--clock` or the photos with GPS); the service reads the track, places each photo without a request or EXIF position, and reports `products.geotag` (`place_source` request / exif / gpx / none, and the fix). The track must be under the service's allow-roots. Without such a profile (no profile, `full`, `album`), `run --gpx` reads the track here and sends per-image coordinates, exactly as before, and `bioscan geotag` always works locally. Both paths give identify the same coordinates. The stage takes the same options from a /run body or a `bioscan.toml` (`[profile.wildlife.options.geotag] gpx = [...]`); it never estimates a clock offset itself, because it sees one chunk at a time (empty `offset` = 0).
- **Accuracy.** Measured on synthetic tracks built from the golden set (`bioscan bench geotag`, docs/harness.md):

  | Measure | Pooled result |
  |---|---|
  | Median error | 7.2 m |
  | p90 error | 17 m |
  | Within 100 m | 97.2% |
  | No fix | 0.1% |
  | False fix | 0% |
  | Clock-offset error | 1 s (median) |

  The per-scenario table is in docs/2026-09-24-geotag-synthetic.md.
