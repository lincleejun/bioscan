"""Synthetic GPX scenarios from the golden set, for `bioscan bench geotag`.

The owner has no GPX to share, so the tracks are built from the iNaturalist ground truth: each
photo's true position and capture time. Photos are grouped into outings (same observer and local
day, split where the next photo is more than 4 h later or would need more than 40 m/s). A true
path runs through every photo of an outing in time order: a walk-in, a stop of 5-60 s at each
photo, legs that wander as far as a 1.2 m/s walker could in the time between photos (or drive,
when the photos are too far apart to walk), and a walk-out. A "device" samples that path every
1, 2, 5 or 10 s with correlated GPS noise (sigma 3-10 m per axis, 30 s correlation time).

Capture times. 44% of the golden times have seconds ":00" (iNaturalist keeps minutes only for
many observations) and 15 rows are a date only. Minute-precision rows get seconds drawn uniformly
in the minute; date-only rows get a time between 07:00 and 17:00 in a zone of round(lon / 15) h.
The drawn time is the truth the track is built around, so it is consistent; it is recorded in
truth.csv (`precision`: second, minute or date). One row without coordinates is skipped.

Scenarios (one folder each): see SCENARIOS. Every random draw comes from a generator seeded with
(seed, scenario, outing), so a scenario or outing is the same whatever else changes.

    uv run python scripts/geotag_synth.py data/inat/groundtruth-inat.csv --out runs/geotag-synth --seed 7
    bioscan bench geotag runs/geotag-synth

Layout: OUT/scenarios.json; OUT/<scenario>/photos.csv (the inputs: group, path, role, taken_at,
tz, lat, lon, clock), truth.csv (group, path, lat, lon, utc, expect_fix, offset_s, precision) and
gpx/<group>/*.gpx. Roles: photo (scored), ref (a photo that already has GPS), clock (a photo of a
clock showing the true time).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCENARIOS = {
    "perfect": "camera clock right; tracks sampled every 1-10 s with GPS noise; half the photos carry OffsetTimeOriginal",
    "offset37": "camera 37 s fast; offset estimated from 3 photos per outing that already have GPS",
    "dst": "camera 1 h fast (DST change missed); offset estimated from 3 photos per outing with GPS",
    "wrongtz": "camera on home time 3 h ahead, no OffsetTimeOriginal, --tz of the trip; offset from one clock photo",
    "gaps": "2-4 dropouts of 30 s-20 min per track, and auto-pause at half the photo stops",
    "outside": "track starts up to 20 min late and ends up to 20 min early; photos outside it expect no fix",
    "multi": "each track split into 2-3 GPX files (one GPX 1.0, one with 2 segments) with restart gaps, plus a "
             "decoy track from the day before",
}
TRUE_OFFSET = {"offset37": 37.0, "dst": 3600.0, "wrongtz": 3 * 3600.0}
WALK_MPS, MAX_SPEED_MPS, SPLIT_S = 1.2, 40.0, 4 * 3600
M_PER_DEG = 111_320.0
GPX11, GPX10 = "http://www.topografix.com/GPX/1/1", "http://www.topografix.com/GPX/1/0"


# ---- golden rows -> timed, placed photos ----------------------------------------------------

@dataclass
class Shot:
    path: str
    lat: float
    lon: float
    t: float              # true UTC, POSIX seconds
    tz_min: int           # the photo's local UTC offset, minutes
    precision: str        # second | minute | date
    observer: str
    local_day: str


def _rng(*key) -> random.Random:
    return random.Random(":".join(str(k) for k in key))


def observer_of(attribution: str) -> str:
    m = re.match(r"\(c\) (.+?), (?:some|all) rights reserved", attribution or "")
    return m.group(1) if m else "?"


def load_shots(csv_path: str, seed: int) -> list[Shot]:
    out = []
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if not r.get("lat") or not r.get("lon"):
                continue
            lat, lon, s = float(r["lat"]), float(r["lon"]), (r.get("taken_at") or "").strip()
            rng = _rng(seed, "time", r["path"])
            m = re.fullmatch(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)([+-])(\d\d):(\d\d)", s)
            if m:
                tz_min = (1 if m.group(2) == "+" else -1) * (int(m.group(3)) * 60 + int(m.group(4)))
                local = datetime.fromisoformat(m.group(1))
                if local.second == 0:
                    local += timedelta(seconds=rng.uniform(0, 60))
                    precision = "minute"
                else:
                    precision = "second"
            elif re.fullmatch(r"\d{4}-\d\d-\d\d", s):
                tz_min = round(lon / 15) * 60
                local = datetime.fromisoformat(s) + timedelta(hours=7, seconds=rng.uniform(0, 10 * 3600))
                precision = "date"
            else:
                continue
            t = local.replace(tzinfo=timezone(timedelta(minutes=tz_min))).timestamp()
            out.append(Shot(r["path"], lat, lon, t, tz_min, precision, observer_of(r.get("attribution", "")),
                            local.date().isoformat()))
    return out


def dist_m(a_lat, a_lon, b_lat, b_lon) -> float:
    x = (b_lon - a_lon) * M_PER_DEG * math.cos(math.radians((a_lat + b_lat) / 2))
    y = (b_lat - a_lat) * M_PER_DEG
    return math.hypot(x, y)


def outings(shots: list[Shot]) -> list[list[Shot]]:
    """Same observer and local day, in time order; split at a > 4 h pause or an impossible speed."""
    by: dict[tuple[str, str], list[Shot]] = {}
    for s in shots:
        by.setdefault((s.observer, s.local_day), []).append(s)
    out = []
    for key in sorted(by):
        cur: list[Shot] = []
        for s in sorted(by[key], key=lambda s: (s.t, s.path)):
            if cur:
                p = cur[-1]
                dt, d = s.t - p.t, dist_m(p.lat, p.lon, s.lat, s.lon)
                if dt > SPLIT_S or d > max(50.0, MAX_SPEED_MPS * dt):
                    out.append(cur)
                    cur = []
            cur.append(s)
        out.append(cur)
    out.sort(key=lambda g: (g[0].t, g[0].path))
    return out


# ---- the true path ---------------------------------------------------------------------------

@dataclass
class Leg:
    t0: float
    t1: float
    a: tuple[float, float]      # local metres (x east, y north)
    b: tuple[float, float]
    bulge: float = 0.0          # lateral detour amplitude, metres
    wobble: float = 0.0         # uneven speed, |c| < 1 keeps it monotonic

    def at(self, t: float) -> tuple[float, float]:
        if self.t1 <= self.t0:
            return self.b
        u = min(1.0, max(0.0, (t - self.t0) / (self.t1 - self.t0)))
        s = u - self.wobble / (2 * math.pi) * math.sin(2 * math.pi * u)
        (ax, ay), (bx, by) = self.a, self.b
        dx, dy = bx - ax, by - ay
        d = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / d, dx / d
        off = self.bulge * math.sin(math.pi * s)
        return ax + dx * s + nx * off, ay + dy * s + ny * off


@dataclass
class Path_:
    lat0: float
    lon0: float
    legs: list[Leg] = field(default_factory=list)

    def to_xy(self, lat, lon):
        return (lon - self.lon0) * M_PER_DEG * math.cos(math.radians(self.lat0)), (lat - self.lat0) * M_PER_DEG

    def to_ll(self, x, y):
        return self.lat0 + y / M_PER_DEG, self.lon0 + x / (M_PER_DEG * math.cos(math.radians(self.lat0)))

    @property
    def start(self):
        return self.legs[0].t0

    @property
    def end(self):
        return self.legs[-1].t1

    def at(self, t: float) -> tuple[float, float]:
        """True (lat, lon) at t; legs are contiguous and in order."""
        lo, hi = 0, len(self.legs) - 1
        while lo < hi:
            mid = (lo + hi) // 2
            if self.legs[mid].t1 < t:
                lo = mid + 1
            else:
                hi = mid
        return self.to_ll(*self.legs[lo].at(t))


def build_path(group: list[Shot], rng: random.Random) -> tuple[Path_, list[tuple[float, float]]]:
    """The true path through the outing's photos, and each photo's stop [start, end]."""
    path = Path_(group[0].lat, group[0].lon)
    pts = [path.to_xy(s.lat, s.lon) for s in group]
    stops = []
    for s in group:
        stops.append([s.t - rng.uniform(5, 60), s.t + rng.uniform(5, 60)])
    for i in range(len(group) - 1):                         # neighbouring stops must not overlap
        if stops[i][1] > stops[i + 1][0]:
            mid = (group[i].t + group[i + 1].t) / 2
            stops[i][1] = max(group[i].t, min(stops[i][1], mid))
            stops[i + 1][0] = min(group[i + 1].t, max(stops[i + 1][0], mid))

    def walk(t0, t1, a, b):
        dur, d = t1 - t0, math.hypot(b[0] - a[0], b[1] - a[1])
        reach = WALK_MPS * dur * rng.uniform(0.3, 1.0)
        bulge = 0.5 * math.sqrt(reach * reach - d * d) if reach > d else rng.uniform(-0.1, 0.1) * d
        bulge = min(bulge, 3000.0) * rng.choice((-1, 1))
        return Leg(t0, t1, a, b, bulge, rng.uniform(-0.5, 0.5))

    lead = rng.uniform(300, 1200)
    ang = rng.uniform(0, 2 * math.pi)
    r = WALK_MPS * lead * rng.uniform(0.3, 0.9)
    start = (pts[0][0] + r * math.cos(ang), pts[0][1] + r * math.sin(ang))
    path.legs.append(walk(stops[0][0] - lead, stops[0][0], start, pts[0]))
    for i, p in enumerate(pts):
        path.legs.append(Leg(stops[i][0], stops[i][1], p, p))
        if i + 1 < len(pts):
            path.legs.append(walk(stops[i][1], stops[i + 1][0], p, pts[i + 1]))
    lead = rng.uniform(300, 1200)
    ang = rng.uniform(0, 2 * math.pi)
    r = WALK_MPS * lead * rng.uniform(0.3, 0.9)
    path.legs.append(walk(stops[-1][1], stops[-1][1] + lead, pts[-1],
                          (pts[-1][0] + r * math.cos(ang), pts[-1][1] + r * math.sin(ang))))
    return path, [tuple(s) for s in stops]


def sample(path: Path_, rng: random.Random) -> list[list[float]]:
    """The device's fixes: [t, lat, lon, ele] every 1/2/5/10 s (whole seconds), AR(1) noise."""
    step = rng.choice((1, 2, 5, 10))
    sigma = rng.uniform(3, 10)
    rho = math.exp(-step / 30.0)
    k = math.sqrt(1 - rho * rho)
    nx = rng.gauss(0, sigma)
    ny = rng.gauss(0, sigma)
    ele0 = rng.uniform(0, 1500)
    out = []
    t = float(math.ceil(path.start))
    while t <= path.end:
        lat, lon = path.at(t)
        x, y = path.to_xy(lat, lon)
        out.append([t, *path.to_ll(x + nx, y + ny), round(ele0 + rng.gauss(0, 3), 1)])
        nx = rho * nx + k * rng.gauss(0, sigma)
        ny = rho * ny + k * rng.gauss(0, sigma)
        t += step
    return out


# ---- output ----------------------------------------------------------------------------------

def gpx_xml(segments: list[list[list[float]]], ns: str = GPX11, name: str = "track") -> str:
    version = "1.0" if ns == GPX10 else "1.1"
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             f'<gpx version="{version}" creator="bioscan geotag_synth" xmlns="{ns}">',
             f"<trk><name>{name}</name>"]
    for seg in segments:
        lines.append("<trkseg>")
        for t, lat, lon, ele in seg:
            ts = datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            lines.append(f'<trkpt lat="{lat:.7f}" lon="{lon:.7f}"><ele>{ele}</ele><time>{ts}</time></trkpt>')
        lines.append("</trkseg>")
    lines += ["</trk>", "</gpx>", ""]
    return "\n".join(lines)


def camera_string(t: float, tz_min: int, with_offset: bool) -> str:
    """How the camera writes a capture time: local wall time with sub-seconds, and the offset when
    it records OffsetTimeOriginal."""
    z = timezone(timedelta(minutes=tz_min))
    local = datetime.fromtimestamp(t, z)
    s = local.strftime("%Y-%m-%dT%H:%M:%S") + f".{int(local.microsecond / 10000):02d}"
    return s + local.strftime("%z")[:3] + ":" + local.strftime("%z")[3:] if with_offset else s


def tz_text(tz_min: int) -> str:
    sign = "-" if tz_min < 0 else "+"
    return f"{sign}{abs(tz_min) // 60:02d}:{abs(tz_min) % 60:02d}"


def build_scenario(name: str, groups: list[list[Shot]], seed: int, out: Path) -> dict:
    photos, truth, points = [], [], 0
    for gi, group in enumerate(groups):
        gid = f"g{gi:04d}"
        base = _rng(seed, "path", gid)                      # the same path and device in every scenario
        path, stops = build_path(group, base)
        fixes = sample(path, base)
        rng = _rng(seed, name, gid)
        tz_min = group[0].tz_min
        with_offset = _rng(seed, "offset-tag", gid).random() < 0.5 and name != "wrongtz"
        offset = TRUE_OFFSET.get(name, 0.0)        # camera minus true time, as geotag should find it
        # wrongtz: the right instant written as wall time of a zone 3 h ahead; others: a fast clock
        cam_tz, shift = (tz_min + 180, 0.0) if name == "wrongtz" else (tz_min, offset)
        files: list[tuple[str, list[list[list[float]]]]] = []
        if name == "gaps":
            cut = []
            for _ in range(rng.randint(2, 4)):
                a = rng.uniform(path.start, path.end)
                cut.append((a, a + rng.uniform(30, 1200)))
            for a, b in stops:
                if b - a > 20 and rng.random() < 0.5:
                    cut.append((a + 3, b - 3))
            fixes = [f for f in fixes if not any(a < f[0] < b for a, b in cut)]
        elif name == "outside":
            lo = group[0].t + rng.uniform(-600, 1200)
            hi = group[-1].t + rng.uniform(-1200, 600)
            if hi - lo < 60:
                lo = path.start
            fixes = [f for f in fixes if lo <= f[0] <= hi] or fixes[:1]
        if name == "multi" and len(fixes) > 10:
            cuts = sorted(rng.sample(range(2, len(fixes) - 2), k=min(rng.randint(1, 2), len(fixes) - 5)))
            parts, prev = [], 0
            for c in cuts:
                restart = fixes[c][0] + rng.uniform(10, 120)
                parts.append(fixes[prev:c])
                prev = next((j for j in range(c, len(fixes)) if fixes[j][0] >= restart), len(fixes))
            parts.append(fixes[prev:])
            parts = [p for p in parts if p]
            fixes = [f for p in parts for f in p]
            first = parts[0]
            mid = len(first) // 2
            files.append(("track-1.gpx", [first[:mid], first[mid:]] if mid else [first]))
            for k, p in enumerate(parts[1:], start=2):
                files.append((f"track-{k}.gpx", [p]))
            decoy_t = path.start - 86400
            lat, lon = path.to_ll(50_000.0, 0.0)
            files.append(("decoy.gpx", [[[decoy_t + 5 * j, lat + j * 1e-5, lon, 10.0] for j in range(200)]]))
        else:
            files.append(("track.gpx", [fixes]))
        gdir = out / name / "gpx" / gid
        gdir.mkdir(parents=True, exist_ok=True)
        for k, (fname, segs) in enumerate(files):
            ns = GPX10 if name == "multi" and k == len(files) - 2 and len(files) > 2 else GPX11
            (gdir / fname).write_text(gpx_xml(segs, ns, f"{gid} {fname}"), encoding="utf-8")
            points += sum(len(s) for s in segs)
        t_first, t_last = fixes[0][0], fixes[-1][0]
        for s in group:
            photos.append({"group": gid, "path": s.path, "role": "photo",
                           "taken_at": camera_string(s.t + shift, cam_tz, with_offset), "tz": tz_text(tz_min),
                           "lat": "", "lon": "", "clock": ""})
            truth.append({"group": gid, "path": s.path, "lat": s.lat, "lon": s.lon,
                          "utc": datetime.fromtimestamp(s.t, timezone.utc).isoformat(timespec="milliseconds"),
                          "expect_fix": int(t_first <= s.t <= t_last), "offset_s": offset, "precision": s.precision})
        if name in ("offset37", "dst"):
            lo, hi = max(path.start, group[0].t - 300), min(path.end, group[-1].t + 300)
            for k in range(3):
                t = rng.uniform(lo, hi)
                lat, lon = path.at(t)
                x, y = path.to_xy(lat, lon)
                lat, lon = path.to_ll(x + rng.gauss(0, 5), y + rng.gauss(0, 5))
                photos.append({"group": gid, "path": f"{gid}/ref-{k}.jpg", "role": "ref",
                               "taken_at": camera_string(t + shift, cam_tz, with_offset), "tz": tz_text(tz_min),
                               "lat": f"{lat:.7f}", "lon": f"{lon:.7f}", "clock": ""})
        if name == "wrongtz":
            t = path.start + rng.uniform(60, 300)
            shown = datetime.fromtimestamp(math.floor(t), timezone(timedelta(minutes=tz_min)))
            photos.append({"group": gid, "path": f"{gid}/clock.jpg", "role": "clock",
                           "taken_at": camera_string(t + shift, cam_tz, False), "tz": tz_text(tz_min),
                           "lat": "", "lon": "", "clock": shown.strftime("%Y-%m-%dT%H:%M:%S")})
    for fname, rows in (("photos.csv", photos), ("truth.csv", truth)):
        with open(out / name / fname, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    return {"description": SCENARIOS[name], "groups": len(groups), "photos": len(truth),
            "expect_fix": sum(r["expect_fix"] for r in truth), "track_points": points,
            "true_offset_s": TRUE_OFFSET.get(name, 0.0)}


def generate(gt_csv: str, out: str | Path, seed: int = 7, scenarios: list[str] | None = None,
             limit: int | None = None) -> dict:
    """Write every scenario under `out`; return what scenarios.json holds. `limit` keeps the first
    N outings (tests)."""
    out = Path(out)
    shots = load_shots(gt_csv, seed)
    groups = outings(shots)[:limit] if limit else outings(shots)
    meta = {"schema": "bioscan-geotag-synth", "version": 1, "seed": seed, "source": str(gt_csv),
            "source_sha256": hashlib.sha256(Path(gt_csv).read_bytes()).hexdigest(),
            "photos": sum(len(g) for g in groups), "outings": len(groups),
            "precision": {p: sum(s.precision == p for g in groups for s in g) for p in ("second", "minute", "date")},
            "scenarios": {}}
    for name in scenarios or list(SCENARIOS):
        meta["scenarios"][name] = build_scenario(name, groups, seed, out)
    (out / "scenarios.json").write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8")
    return meta


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("groundtruth", help="ground-truth CSV with lat, lon, taken_at (e.g. data/inat/groundtruth-inat.csv)")
    p.add_argument("--out", required=True, help="output folder")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--scenario", action="append", choices=list(SCENARIOS), help="only these (repeatable)")
    p.add_argument("--limit", type=int, help="first N outings only")
    a = p.parse_args(argv)
    meta = generate(a.groundtruth, a.out, a.seed, a.scenario, a.limit)
    print(f"{meta['photos']} photos in {meta['outings']} outings (times: {meta['precision']}) -> {a.out}")
    for name, s in meta["scenarios"].items():
        print(f"  {name}: {s['track_points']} track points, {s['expect_fix']}/{s['photos']} photos inside the track")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
