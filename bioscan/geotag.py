"""Geotag photos from GPX tracks: parse the tracks, turn each photo's capture time into UTC, correct
the camera's clock offset, and interpolate a position along the track.

Standard library only: `bioscan geotag` and `bioscan run --gpx` use it without the service.

Time model. A photo's capture time is the camera clock in local time, often without a UTC offset.
It becomes UTC with, in order: the file's own offset (OffsetTimeOriginal, already in `taken_at`),
else the `tz` given (a fixed offset such as "-07:00" or a zone name such as "America/Los_Angeles",
DST-aware), else the system's local zone. GPX times are UTC. The **clock offset** is camera time
minus true time (a camera 37 s fast has +37 s); the corrected time is camera UTC minus the offset.

Fix rule (`locate`). Between two neighbouring track points a and b the position is linear in time
when the points are at most `max_gap_s` apart, or, across a longer gap (a dropout or an auto-pause),
when they are at most `max_span_m` apart (the device stood still). Outside the track, or inside a
longer gap that moved, there is no fix, except within `extrapolate_s` of the nearest point, which
then gives that point's position (held, not projected along the velocity: noise would be amplified).
"""
from __future__ import annotations

import bisect
import math
import re
import statistics
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path

EARTH_RADIUS_M = 6_371_008.8
MAX_GAP_S = 1800.0         # interpolate across neighbours up to 30 min apart (err_m grows with the gap)
MAX_SPAN_M = 200.0         # ... and across a longer gap when its ends are this close (stood still)
EXTRAPOLATE_S = 0.0        # hold the first/last point this long outside the track (0: no fix outside)
GPS_ERR_M = 10.0           # a consumer fix, open sky 4.9 m (95%), worse under trees
DRIFT_MPS = 0.5            # how far a walker strays from the chord per second away from a track point
WALK_MPS = 1.4             # how far a walker gets per second beyond the end of the track
REF_RADIUS_M = 100.0       # a reference photo votes for times the track passed within this distance
MAX_OFFSET_S = 26 * 3600   # clock offsets searched: every timezone mistake (UTC-12 .. UTC+14)
MAX_REF_RESIDUAL_M = 100.0  # an estimated offset whose reference photos sit further off is not used
SOURCES = ("exif", "gpx", "none")


# ---- geometry ------------------------------------------------------------------------------

def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance (haversine), metres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(h)))


def _lerp_lon(a: float, b: float, f: float) -> float:
    if b - a > 180:
        b -= 360
    elif a - b > 180:
        b += 360
    x = a + (b - a) * f
    return (x + 540) % 360 - 180 if not -180 <= x <= 180 else x


# ---- GPX -----------------------------------------------------------------------------------

@dataclass(frozen=True)
class TrackPoint:
    t: float                  # UTC, POSIX seconds
    lat: float
    lon: float
    ele: float | None = None


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_time(text: str | None) -> float | None:
    """ISO 8601 with an offset or Z ('2026-05-01T15:00:00Z', '...00.250+00:00') -> POSIX seconds.
    A time without an offset is UTC, as GPX requires. None when it cannot be read."""
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.strip().replace("z", "Z"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def parse_gpx(source: str | bytes | Path) -> list[list[TrackPoint]]:
    """The track segments of one GPX 1.0 or 1.1 file (a path, or the XML itself as bytes): every
    trk/trkseg with its trkpts in file order. Points without a time or a valid position are
    dropped (they cannot be placed in time). Namespaces are ignored, so both versions read alike."""
    root = ET.parse(str(source)).getroot() if isinstance(source, (str, Path)) else ET.fromstring(source)
    segments = []
    for trk in (e for e in root.iter() if _local(e.tag) == "trk"):
        for seg in (e for e in trk if _local(e.tag) == "trkseg"):
            pts = []
            for pt in (e for e in seg if _local(e.tag) == "trkpt"):
                try:
                    lat, lon = float(pt.get("lat", "")), float(pt.get("lon", ""))
                except ValueError:
                    continue
                kids = {_local(k.tag): (k.text or "").strip() for k in pt}
                t = parse_time(kids.get("time"))
                if t is None or not (abs(lat) <= 90 and abs(lon) <= 180):
                    continue
                try:
                    ele = float(kids["ele"]) if kids.get("ele") else None
                except ValueError:
                    ele = None
                pts.append(TrackPoint(t, lat, lon, ele))
            if pts:
                segments.append(pts)
    return segments


@dataclass
class Track:
    """Every point of one or more GPX files and segments, in time order. Two devices recording at
    once simply interleave; a segment break is a gap like any other (the fix rule decides)."""
    points: list[TrackPoint]
    segments: int = 1
    times: list[float] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.points = sorted(self.points, key=lambda p: p.t)
        self.times = [p.t for p in self.points]

    @classmethod
    def from_segments(cls, segments: list[list[TrackPoint]]) -> Track:
        return cls([p for s in segments for p in s], segments=len(segments))

    @classmethod
    def load(cls, paths: list[str | Path]) -> Track:
        return cls.from_segments([s for p in paths for s in parse_gpx(p)])

    def __len__(self) -> int:
        return len(self.points)

    @property
    def start(self) -> float | None:
        return self.times[0] if self.times else None

    @property
    def end(self) -> float | None:
        return self.times[-1] if self.times else None

    def nearest_dt(self, t: float) -> float | None:
        """Seconds from t to the nearest track point; None for an empty track."""
        if not self.times:
            return None
        i = bisect.bisect_left(self.times, t)
        return min(abs(self.times[j] - t) for j in (i - 1, i) if 0 <= j < len(self.times))


# ---- time ------------------------------------------------------------------------------------

_OFFSET = re.compile(r"^([+-])?(\d+):(\d\d)(?::(\d\d(?:\.\d+)?))?$")
_TZ_FIXED = re.compile(r"^(?:UTC|GMT)?([+-])(\d\d):?(\d\d)$")
_EXIF_TIME = re.compile(r"^(\d{4})[-:](\d\d)[-:](\d\d)[T ](\d\d):(\d\d):(\d\d)(\.\d+)?(Z|[+-]\d\d:?\d\d)?$")


def parse_offset(text: str) -> float:
    """A clock offset: '+00:01:23', '-1:00:00', '+01:00' (hours:minutes), or seconds ('37', '-3600',
    '+37.5'). Camera time minus true time. Raises ValueError on anything else."""
    s = text.strip()
    m = _OFFSET.match(s)
    if m:
        sign = -1 if m.group(1) == "-" else 1
        return sign * (int(m.group(2)) * 3600 + int(m.group(3)) * 60 + float(m.group(4) or 0))
    try:
        v = float(s)
    except ValueError:
        raise ValueError(f"clock offset {text!r}: use +HH:MM:SS or seconds") from None
    if not math.isfinite(v):
        raise ValueError(f"clock offset {text!r} is not finite")
    return v


def format_offset(seconds: float) -> str:
    """37.0 -> '+00:00:37', -3600.5 -> '-01:00:00.5'."""
    sign = "-" if seconds < 0 else "+"
    s = abs(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    frac = f"{sec:04.1f}" if round(sec % 1, 1) else f"{int(round(sec)):02d}"
    return f"{sign}{int(h):02d}:{int(m):02d}:{frac}"


def resolve_tz(name: str | None) -> tzinfo | None:
    """--tz: None/'' or 'local' -> None (the system zone); 'UTC'/'Z'; a fixed offset ('-07:00',
    '+0530', 'UTC+02:00'); or an IANA name ('America/Los_Angeles'), which is DST-aware."""
    if not name or name.strip().lower() == "local":
        return None
    s = name.strip()
    if s.upper() in ("UTC", "Z", "GMT"):
        return timezone.utc
    m = _TZ_FIXED.match(s)
    if m:
        sign = -1 if m.group(1) == "-" else 1
        return timezone(sign * timedelta(hours=int(m.group(2)), minutes=int(m.group(3))))
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError  # stdlib; loaded only for a zone name

    try:
        return ZoneInfo(s)
    except (ZoneInfoNotFoundError, ValueError) as e:
        raise ValueError(f"unknown timezone {name!r}: use an offset like -07:00 or a name like "
                         "America/Los_Angeles") from e


def _zone_of(taken_at: str | None) -> tzinfo | None:
    """The UTC offset written in a capture time string, as a fixed zone; None when it has none."""
    m = _EXIF_TIME.match((taken_at or "").strip())
    off = m.group(8) if m else None
    if not off:
        return None
    if off == "Z":
        return timezone.utc
    off = off.replace(":", "")
    sign = -1 if off[0] == "-" else 1
    return timezone(sign * timedelta(hours=int(off[1:3]), minutes=int(off[3:5])))


def capture_utc(taken_at: str | None, tz: tzinfo | None = None) -> float | None:
    """A capture time ('2026-05-01T08:00:00.37-07:00', '2026-05-01T08:00:00', or EXIF's
    '2026:05:01 08:00:00') -> POSIX seconds on the camera clock. The string's own offset wins;
    else `tz`; else the system's local zone. None when it cannot be read (e.g. a date only)."""
    if not taken_at:
        return None
    m = _EXIF_TIME.match(taken_at.strip())
    if not m:
        return None
    y, mo, d, h, mi, s = (int(x) for x in m.groups()[:6])
    try:
        dt = datetime(y, mo, d, h, mi, s)
    except ValueError:
        return None
    frac = float(m.group(7) or 0)
    own = _zone_of(taken_at)
    if own is not None:
        dt = dt.replace(tzinfo=own)
    elif tz is not None:
        dt = dt.replace(tzinfo=tz)
    else:
        dt = dt.astimezone()                         # naive -> the system's local zone
    return dt.timestamp() + frac


def iso_utc(t: float | None) -> str:
    if t is None:
        return ""
    return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# ---- position ---------------------------------------------------------------------------------

@dataclass
class Position:
    lat: float
    lon: float
    ele: float | None
    dt_s: float               # seconds to the nearest track point
    err_m: float              # estimated error


def locate(track: Track, t: float, max_gap_s: float = MAX_GAP_S, max_span_m: float = MAX_SPAN_M,
           extrapolate_s: float = EXTRAPOLATE_S) -> Position | None:
    """The position at UTC time t by the fix rule (module docstring); None = no fix."""
    pts, times = track.points, track.times
    n = len(pts)
    if not n:
        return None
    i = bisect.bisect_left(times, t)
    if i < n and times[i] == t:
        p = pts[i]
        return Position(p.lat, p.lon, p.ele, 0.0, GPS_ERR_M)
    if 0 < i < n:
        a, b = pts[i - 1], pts[i]
        gap = b.t - a.t
        span = distance_m(a.lat, a.lon, b.lat, b.lon)
        if gap <= max_gap_s or span <= max_span_m:
            f = (t - a.t) / gap if gap > 0 else 0.0
            dt = min(t - a.t, b.t - t)
            ele = a.ele + (b.ele - a.ele) * f if a.ele is not None and b.ele is not None else (a.ele if a.ele is not None else b.ele)
            err = GPS_ERR_M + (DRIFT_MPS * dt if gap <= max_gap_s else span / 2)
            return Position(a.lat + (b.lat - a.lat) * f, _lerp_lon(a.lon, b.lon, f), ele, dt, err)
    near = [j for j in (i - 1, i) if 0 <= j < n]
    j = min(near, key=lambda k: abs(times[k] - t))
    dt = abs(times[j] - t)
    if dt <= extrapolate_s:
        p = pts[j]
        return Position(p.lat, p.lon, p.ele, dt, GPS_ERR_M + WALK_MPS * dt)
    return None


# ---- clock offset ------------------------------------------------------------------------------

@dataclass
class OffsetEstimate:
    offset_s: float
    method: str                 # given | clock | gps | none
    residual_m: float | None = None   # gps: mean distance (capped at 1 km) of the reference photos at this offset
    refs: int = 0               # reference photos (gps) or clock photos used
    note: str = ""


def offset_from_clock(camera_taken_at: str, shown: str, tz: tzinfo | None = None) -> float:
    """A reference photo of a clock (a GPS watch, a phone): the camera's capture time minus the time
    the clock shows. `shown` without an offset is read in the photo's zone (its own offset, else tz)."""
    cam = capture_utc(camera_taken_at, tz)
    if cam is None:
        raise ValueError(f"clock photo has no readable capture time ({camera_taken_at!r})")
    true = capture_utc(shown, _zone_of(camera_taken_at) or tz)
    if true is None:
        raise ValueError(f"clock time {shown!r}: use YYYY-MM-DDTHH:MM:SS")
    return cam - true


def estimate_offset(track: Track, refs: list[tuple[float, float, float]], radius_m: float = REF_RADIUS_M,
                    max_offset_s: float = MAX_OFFSET_S, window_s: float = 120.0) -> OffsetEstimate | None:
    """Camera clock offset from reference photos that already have GPS: [(camera UTC, lat, lon)].
    Every time the track passed within `radius_m` of a reference is a candidate offset. Windows of
    `window_s` holding candidates from the most distinct references are refined (1 s, then 0.1 s
    steps) to the offset where the references sit closest to the track (mean distance, each capped
    at 1 km); among fits within 5 m (or 25%) of the best, the one nearest a whole quarter-hour wins,
    and a spread over 2 min is reported as ambiguous. None when no reference is ever near the track."""
    if not refs or not len(track):
        return None
    cands: list[tuple[float, int]] = []
    for k, (tc, lat, lon) in enumerate(refs):
        dlat = radius_m / 111_320
        dlon = radius_m / (111_320 * max(0.01, math.cos(math.radians(lat))))
        for p in track.points:
            if abs(p.lat - lat) <= dlat and abs(p.lon - lon) <= dlon and \
                    distance_m(lat, lon, p.lat, p.lon) <= radius_m and abs(tc - p.t) <= max_offset_s:
                cands.append((tc - p.t, k))
    if not cands:
        return None
    cands.sort()
    windows: dict[int, int] = {}                 # 60 s bin of a window centre -> distinct refs in it
    lo = 0
    for hi in range(len(cands)):
        while cands[hi][0] - cands[lo][0] > window_s:
            lo += 1
        b = round((cands[lo][0] + cands[hi][0]) / 2 / 60)
        windows[b] = max(windows.get(b, 0), len({k for _, k in cands[lo:hi + 1]}))
    most = max(windows.values())

    def cost(off: float) -> float:
        """Mean distance of the references from the track at this offset, each capped at 1 km so one
        stray reference (a phone photo from elsewhere) cannot dominate."""
        d = []
        for tc, lat, lon in refs:
            pos = locate(track, tc - off, extrapolate_s=0.0)
            d.append(min(1000.0, distance_m(lat, lon, pos.lat, pos.lon)) if pos else 1000.0)
        return sum(d) / len(d)

    def search(lo: float, hi: float, step: float) -> tuple[float, float]:
        n = int(round((hi - lo) / step))
        return min(((round(cost(o), 2), o) for o in (lo + i * step for i in range(n + 1))),
                   key=lambda co: (co[0], abs(co[1] - (lo + hi) / 2)))

    centres = sorted((cost(b * 60.0), b * 60.0) for b, k in windows.items() if k == most)[:8]
    found = []
    for _, c in centres:
        _, coarse = search(c - window_s, c + window_s, 1.0)
        res, fine = search(coarse - 1.0, coarse + 1.0, 0.1)
        found.append((res, fine))
    best_cost = min(r for r, _ in found)
    tied = [o for r, o in found if r <= best_cost + max(5.0, 0.25 * best_cost)]
    # Equally good fits: a camera clock is usually off by drift (seconds to minutes) plus whole
    # quarter-hours (timezone, DST), so prefer the fit nearest such an offset.
    pick = min(tied, key=lambda o: (round(abs(o - 900 * round(o / 900)), -1), abs(o)))
    spread = max(tied) - min(tied)
    note = (f"offset ambiguous: fits from {format_offset(min(tied))} to {format_offset(max(tied))} "
            "(the reference photos sit where the track passes more than once)") if spread > 120 else ""
    return OffsetEstimate(round(pick, 1), "gps", round(cost(pick), 1), len(refs), note)


# ---- photos -------------------------------------------------------------------------------------

@dataclass
class Photo:
    path: str
    taken_at: str | None                       # camera clock, as decode/exiftool give it
    lat: float | None = None                   # EXIF GPS
    lon: float | None = None
    clock: str | None = None                   # a clock photo: the true time the clock shows


@dataclass
class Fix:
    path: str
    lat: float | None
    lon: float | None
    source: str                                # exif | gpx | none
    dt_s: float | None = None                  # seconds to the nearest track point (gpx, none)
    err_m: float | None = None                 # estimated error (gpx)
    utc: str = ""                              # corrected capture time, UTC
    ele: float | None = None


@dataclass
class Result:
    fixes: list[Fix]
    offset: OffsetEstimate
    track_points: int
    warnings: list[str]

    def counts(self) -> dict[str, int]:
        return {s: sum(f.source == s for f in self.fixes) for s in SOURCES}


def has_gps(p: Photo) -> bool:
    return p.lat is not None and p.lon is not None


def resolve_offset(photos: list[Photo], track: Track, tz: tzinfo | None, offset_s: float | None = None,
                   estimate: bool = True) -> OffsetEstimate:
    """given (--offset) > clock photos (median) > photos with EXIF GPS (estimate_offset) > 0."""
    if offset_s is not None:
        return OffsetEstimate(offset_s, "given")
    clocks = [offset_from_clock(p.taken_at, p.clock, tz) for p in photos if p.clock and p.taken_at]
    if clocks:
        return OffsetEstimate(round(statistics.median(clocks), 1), "clock", refs=len(clocks))
    refs = [(t, p.lat, p.lon) for p in photos if has_gps(p) and (t := capture_utc(p.taken_at, tz)) is not None]
    if estimate and refs:
        est = estimate_offset(track, refs)
        if est is None:
            return OffsetEstimate(0.0, "none", refs=len(refs),
                                  note=f"none of the {len(refs)} photos with GPS is within {REF_RADIUS_M:g} m of the track")
        if est.residual_m is not None and est.residual_m > MAX_REF_RESIDUAL_M:
            return OffsetEstimate(0.0, "none", est.residual_m, len(refs),
                                  note=f"offset {format_offset(est.offset_s)} from GPS photos rejected: they sit "
                                       f"{est.residual_m:.0f} m from the track (> {MAX_REF_RESIDUAL_M:g} m)")
        return est
    return OffsetEstimate(0.0, "none")


def geotag(photos: list[Photo], track: Track, tz: tzinfo | None = None, offset_s: float | None = None,
           estimate: bool = True, max_gap_s: float = MAX_GAP_S, max_span_m: float = MAX_SPAN_M,
           extrapolate_s: float = EXTRAPOLATE_S) -> Result:
    """One Fix per photo, in input order. EXIF GPS always wins (source exif); else the track at the
    corrected capture time (gpx); else none. The offset is resolved once for all photos, so give
    one camera per call."""
    off = resolve_offset(photos, track, tz, offset_s, estimate)
    fixes, warnings, misses = [], [], []
    for p in photos:
        t = capture_utc(p.taken_at, tz)
        tt = None if t is None else t - off.offset_s
        if has_gps(p):
            fixes.append(Fix(p.path, p.lat, p.lon, "exif", utc=iso_utc(tt)))
            continue
        pos = locate(track, tt, max_gap_s, max_span_m, extrapolate_s) if tt is not None else None
        if pos is None:
            dt = track.nearest_dt(tt) if tt is not None else None
            fixes.append(Fix(p.path, None, None, "none", None if dt is None else round(dt, 1), utc=iso_utc(tt)))
            if dt is not None:
                misses.append(dt)
            continue
        fixes.append(Fix(p.path, round(pos.lat, 7), round(pos.lon, 7), "gpx", round(pos.dt_s, 1), round(pos.err_m, 1),
                         iso_utc(tt), None if pos.ele is None else round(pos.ele, 1)))
    if off.note:
        warnings.append(off.note)
    timed = sum(1 for p in photos if not has_gps(p) and capture_utc(p.taken_at, tz) is not None)
    if timed and len(misses) > timed / 2:
        hours = statistics.median(misses) / 3600
        hint = (f" (median {hours:.1f} h away: a timezone or DST mistake? try --tz or --offset)" if hours >= 0.5
                else "")
        warnings.append(f"{len(misses)} of {timed} photos fall outside the track{hint}")
    return Result(fixes, off, len(track), warnings)


# ---- XMP sidecars --------------------------------------------------------------------------------

def xmp_coordinate(value: float, pos: str, neg: str) -> str:
    """XMP GPSCoordinate 'DDD,MM.mmmmmmk': 37.5 -> '37,30.000000N'."""
    ref = pos if value >= 0 else neg
    v = abs(value)
    deg = int(v)
    return f"{deg},{(v - deg) * 60:.6f}{ref}"


def xmp_packet(lat: float, lon: float, ele: float | None = None) -> str:
    alt = ""
    if ele is not None:
        alt = (f'\n   exif:GPSAltitudeRef="{0 if ele >= 0 else 1}"'
               f'\n   exif:GPSAltitude="{round(abs(ele) * 10)}/10"')
    return ('<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
            '<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="bioscan geotag">\n'
            ' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
            '  <rdf:Description rdf:about=""\n'
            '   xmlns:exif="http://ns.adobe.com/exif/1.0/"\n'
            '   exif:GPSVersionID="2.2.0.0"\n'
            '   exif:GPSMapDatum="WGS-84"\n'
            f'   exif:GPSLatitude="{xmp_coordinate(lat, "N", "S")}"\n'
            f'   exif:GPSLongitude="{xmp_coordinate(lon, "E", "W")}"{alt}/>\n'
            ' </rdf:RDF>\n'
            '</x:xmpmeta>\n'
            '<?xpacket end="w"?>\n')


def sidecar_paths(photo: str) -> tuple[Path, Path]:
    """(stem.xmp: Lightroom, Capture One, Bridge; name.ext.xmp: darktable, digiKam)."""
    p = Path(photo)
    return p.with_suffix(".xmp"), p.with_name(p.name + ".xmp")


def write_sidecar(photo: str, lat: float, lon: float, ele: float | None = None) -> str:
    """Write stem.xmp with the position when neither sidecar exists: 'written', else 'exists'.
    An existing sidecar (the editor's develop settings, keywords) is never touched or merged."""
    ours, other = sidecar_paths(photo)
    if ours.exists() or other.exists():
        return "exists"
    try:
        with open(ours, "x", encoding="utf-8") as f:       # exclusive create: no race with a writer
            f.write(xmp_packet(lat, lon, ele))
    except FileExistsError:
        return "exists"
    return "written"
