"""`bioscan geotag DIR --gpx a.gpx`: positions for photos without GPS from GPX tracks, as a CSV and,
on request, XMP sidecars; and the per-file coordinates `bioscan run --gpx` sends.

The geotagging itself is bioscan.geotag (standard library). Reading each photo's capture time and
GPS needs the service's EXIF reader (Pillow), imported only when a command runs, like `names stats`.
"""
from __future__ import annotations

import csv
import os
import sys
from pathlib import Path

from bioscan import formats
from bioscan import geotag as gt

HEAD_BYTES = 8 << 20            # EXIF sits at the front of every supported container
CSV_FIELDS = ["path", "lat", "lon", "source", "dt_s", "err_m", "utc", "ele"]


def read_photos(paths: list[str]) -> list[gt.Photo]:
    """Capture time and EXIF GPS of each file, read the way the service's decode reads them."""
    from bioscan.service.decode import read_exif  # Pillow; keep the CLI's import light

    out = []
    for path in paths:
        try:
            with open(path, "rb") as f:
                data = f.read(HEAD_BYTES)
                lat, lon, taken = read_exif(data)
                if taken is None and len(data) == HEAD_BYTES:     # e.g. a preview past the head
                    lat, lon, taken = read_exif(data + f.read())
        except OSError:
            lat = lon = taken = None
        out.append(gt.Photo(path, taken, lat, lon))
    return out


def _clocks(specs: list[str] | None) -> dict[str, str]:
    out = {}
    for s in specs or []:
        path, sep, shown = s.rpartition("=")
        if not sep or not path or not shown:
            raise SystemExit(f"--clock wants PHOTO=TIME, e.g. DSC0001.ARW=2026-05-01T08:00:13 (got {s!r})")
        out[os.path.abspath(path)] = shown.strip()
    return out


def run_geotag(paths: list[str], gpx: list[str], offset: str | None = None, tz: str | None = None,
               clocks: list[str] | None = None, max_gap_s: float = gt.MAX_GAP_S, max_span_m: float = gt.MAX_SPAN_M,
               extrapolate_s: float = gt.EXTRAPOLATE_S, photos: list[gt.Photo] | None = None,
               max_still_s: float = gt.MAX_STILL_S) -> gt.Result:
    """Load the tracks, read the photos (unless given) and geotag them. SystemExit on bad input."""
    try:
        zone = gt.resolve_tz(tz)
        off = gt.parse_offset(offset) if offset else None
    except ValueError as e:
        raise SystemExit(str(e)) from None
    try:
        track = gt.Track.load(gpx)
    except (OSError, gt.ET.ParseError) as e:
        raise SystemExit(f"cannot read GPX: {e}") from None
    if not len(track):
        raise SystemExit(f"no timed track points in {', '.join(gpx)}")
    photos = photos if photos is not None else read_photos(paths)
    shown = _clocks(clocks)
    missing = sorted(set(shown) - {os.path.abspath(p.path) for p in photos})
    if missing:
        raise SystemExit(f"--clock photo not among the inputs: {', '.join(missing)}")
    for p in photos:
        p.clock = shown.get(os.path.abspath(p.path))
    try:
        return gt.geotag(photos, track, zone, off, max_gap_s=max_gap_s, max_span_m=max_span_m,
                         extrapolate_s=extrapolate_s, max_still_s=max_still_s)
    except ValueError as e:                        # an unreadable clock photo or clock time
        raise SystemExit(str(e)) from None


def _row(f: gt.Fix) -> dict:
    return {"path": f.path, "lat": "" if f.lat is None else f"{f.lat:.7f}", "lon": "" if f.lon is None else f"{f.lon:.7f}",
            "source": f.source, "dt_s": "" if f.dt_s is None else f.dt_s, "err_m": "" if f.err_m is None else f.err_m,
            "utc": f.utc, "ele": "" if f.ele is None else f.ele}


def describe_offset(o: gt.OffsetEstimate) -> str:
    if o.method == "given":
        how = "given (--offset)"
    elif o.method == "clock":
        how = f"from {o.refs} clock photo(s)"
    elif o.method == "gps":
        how = f"from {o.refs} photo(s) with GPS, on average {o.residual_m:g} m from the track"
    else:
        how = "none (camera clock taken as right)"
    return f"clock offset {gt.format_offset(o.offset_s)} (camera minus true time), {how}"


def cmd_geotag(a) -> int:
    exts = formats.parse_ext(a.ext)
    paths = [p for root in a.paths for p in formats.list_images(root, exts, a.recursive)]
    if not paths:
        raise SystemExit("no images found")
    res = run_geotag(paths, a.gpx, a.offset, a.tz, a.clock, a.max_gap, a.max_span, a.extrapolate,
                     max_still_s=a.max_still)
    w = csv.DictWriter(sys.stdout, fieldnames=CSV_FIELDS, lineterminator="\n")
    w.writeheader()
    rows = [_row(f) for f in res.fixes]
    w.writerows(rows)
    if a.csv:
        Path(a.csv).parent.mkdir(parents=True, exist_ok=True)
        with open(a.csv, "w", newline="", encoding="utf-8") as f:
            cw = csv.DictWriter(f, fieldnames=CSV_FIELDS)
            cw.writeheader()
            cw.writerows(rows)
    c = res.counts()
    print(f"# {len(res.fixes)} photos: {c['exif']} exif, {c['gpx']} gpx, {c['none']} none; "
          f"{res.track_points} track points; {describe_offset(res.offset)}", file=sys.stderr)
    for msg in res.warnings:
        print(f"warning: {msg}", file=sys.stderr)
    if a.xmp:
        done = {"written": 0, "exists": 0}
        for f in res.fixes:
            if f.source == "gpx":
                done[gt.write_sidecar(f.path, f.lat, f.lon, f.ele)] += 1
        print(f"# xmp: {done['written']} sidecars written, {done['exists']} left alone (a sidecar exists)",
              file=sys.stderr)
    if a.csv:
        print(f"# csv -> {a.csv}", file=sys.stderr)
    return 0


def run_coordinates(paths: list[str], a) -> tuple[dict[str, tuple[float, float]], set[str]]:
    """`bioscan run --gpx`: (path -> (lat, lon) for the photos without EXIF GPS that the track places,
    the paths whose EXIF has GPS). Photos with EXIF GPS get no request coordinate: the service reads
    their own position (EXIF first). The EXIF is read here (Pillow), so no exiftool is needed."""
    res = run_geotag(paths, a.gpx, a.offset, a.tz, a.clock, a.max_gap, a.max_span, a.extrapolate,
                     max_still_s=a.max_still)
    c = res.counts()
    print(f"gpx: {c['gpx']} of {len(paths)} photos placed from the track ({c['exif']} have EXIF GPS, "
          f"{c['none']} no fix); {describe_offset(res.offset)}", file=sys.stderr)
    for msg in res.warnings:
        print(f"warning: {msg}", file=sys.stderr)
    return ({f.path: (f.lat, f.lon) for f in res.fixes if f.source == "gpx"},
            {f.path for f in res.fixes if f.source == "exif"})


def stage_options(paths: list[str], a) -> tuple[dict, set[str] | None, set[str] | None]:
    """`bioscan run --gpx` when the profile runs the geotag stage (wildlife): the service places the
    photos, so the request carries options.geotag instead of per-file coordinates. The clock offset
    is decided here, once for the whole folder (--offset, else --clock / GPS photos, as `bioscan
    geotag` does), because the stage sees one chunk at a time. Returns (options, the paths the track
    places, the paths with EXIF GPS); the two sets are None when no photo had to be read (--offset
    given, no --clock, no --lat)."""
    opts: dict = {"gpx": [os.path.abspath(g) for g in a.gpx]}
    for key, value, default in (("max_gap_s", a.max_gap, gt.MAX_GAP_S), ("max_span_m", a.max_span, gt.MAX_SPAN_M),
                                ("max_still_s", a.max_still, gt.MAX_STILL_S),
                                ("extrapolate_s", a.extrapolate, gt.EXTRAPOLATE_S)):
        if value != default:                       # a default flag must not hide a profile's value
            opts[key] = value
    if a.tz:
        opts["camera_utc_offset"] = a.tz
    placed = exif_gps = None
    if a.offset and not a.clock and a.lat is None:
        try:
            gt.parse_offset(a.offset)
        except ValueError as e:
            raise SystemExit(str(e)) from None
        opts["offset"] = a.offset
        how = "given (--offset)"
    else:
        res = run_geotag(paths, a.gpx, a.offset, a.tz, a.clock, a.max_gap, a.max_span, a.extrapolate,
                         max_still_s=a.max_still)
        opts["offset"] = repr(float(res.offset.offset_s))
        placed = {f.path for f in res.fixes if f.source == "gpx"}
        exif_gps = {f.path for f in res.fixes if f.source == "exif"}
        how = describe_offset(res.offset)
        for msg in res.warnings:
            print(f"warning: {msg}", file=sys.stderr)
    print(f"gpx: the service's geotag stage places the photos without GPS; {how}", file=sys.stderr)
    return opts, placed, exif_gps


def add_track_options(s, xmp: bool = False) -> None:
    """The options `geotag` and `run --gpx` share."""
    s.add_argument("--offset", help="camera clock minus true time, e.g. +00:01:23 (camera 83 s fast) or --offset=-3600; "
                                    "default: estimated from photos with GPS or --clock, else 0")
    s.add_argument("--tz", help="zone of capture times without an offset: --tz=-07:00, UTC-07:00 or America/Los_Angeles "
                                "(default: the system's zone); a file's OffsetTimeOriginal always wins")
    s.add_argument("--clock", action="append", metavar="PHOTO=TIME",
                   help="a photo of a clock (GPS watch, phone) and the time it shows, e.g. "
                        "DSC0001.ARW=2026-05-01T08:00:13; sets the clock offset (repeatable, median)")
    s.add_argument("--max-gap", type=float, default=gt.MAX_GAP_S,
                   help="interpolate between track points up to this many seconds apart (default %(default)g)")
    s.add_argument("--max-span", type=float, default=gt.MAX_SPAN_M,
                   help="... or across a longer gap whose ends are within this many metres (default %(default)g)")
    s.add_argument("--max-still", type=float, default=gt.MAX_STILL_S,
                   help="... but never across a gap longer than this many seconds (default %(default)g)")
    s.add_argument("--extrapolate", type=float, default=gt.EXTRAPOLATE_S,
                   help="hold the first/last track point this many seconds outside the track (default %(default)g)")


TRACK_ONLY = ("offset", "tz", "clock")      # options that mean nothing without a track


def warn_unused(a) -> None:
    """`run` with --offset/--tz/--clock but no --gpx: they would be silently ignored."""
    given = [f"--{k}" for k in TRACK_ONLY if getattr(a, k, None)]
    if given and not getattr(a, "gpx", None):
        print(f"warning: {', '.join(given)} only apply with --gpx; ignored", file=sys.stderr)


def add_parser(sub) -> None:
    s = sub.add_parser("geotag", help="positions for photos without GPS from GPX tracks (CSV, optional XMP)")
    s.add_argument("paths", nargs="+", help="files or folders")
    s.add_argument("--gpx", action="append", required=True, help="GPX 1.0/1.1 track file (repeatable)")
    add_track_options(s)
    s.add_argument("--csv", help="also write the CSV here")
    s.add_argument("--xmp", action="store_true",
                   help="write <stem>.xmp with the position for each gpx-placed photo that has no sidecar yet "
                        "(an existing sidecar is never changed)")
    s.add_argument("-r", "--recursive", action="store_true")
    s.add_argument("--ext", default=formats.DEFAULT_EXT)
    s.set_defaults(func=cmd_geotag)
