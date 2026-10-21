"""Automatic group suggestions (docs/gardener/contract.md §4, §9.4): events split where capture times
are more than two hours apart (on one time basis, see _events), places on a ~1 km GPS grid (no reverse geocoding), scene groups
and species-level taxa. An asset without the field a kind needs is in no group of that kind.
Standard library only."""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from bioscan.gardener.analyze import zh

EVENT_GAP_S = 2 * 3600
GRID_DEG = 0.009            # ~1 km of latitude


@dataclass
class Group:
    key: str                # stable: event:20250301-0812, place:<lat cell>:<lon cell>, scene:<group>, species:<taxon>
    kind: str               # event | place | scene | species
    name: str               # Chinese
    asset_ids: list[str]
    summary: dict = field(default_factory=dict)


def _parse(taken_at: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(taken_at) if taken_at else None
    except (TypeError, ValueError):
        return None


def zone(tz: str) -> tzinfo:
    """An IANA zone name or a fixed offset (+08:00, -0800); ValueError otherwise."""
    m = re.fullmatch(r"([+-])(\d{2}):?(\d{2})", tz.strip())
    if m:
        delta = timedelta(hours=int(m.group(2)), minutes=int(m.group(3)))
        return timezone(-delta if m.group(1) == "-" else delta)
    try:
        return ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError) as e:
        raise ValueError(f"unknown time zone {tz!r}") from e


def _scale(t: datetime) -> float:
    """Seconds on one scale: an aware time's instant; a naive wall clock counted as is (only gaps matter)."""
    return (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).timestamp()


def _events(assets: list[dict], tz: str | None) -> list[Group]:
    """Bases: with `tz`, naive (camera-local) times get that zone and everything compares as instants
    ("instant"); all times with offsets also compare as instants; otherwise every time is read as its
    own local wall clock ("local"), an offset just dropped. Then a time at UTC (Z / +00:00), likely a
    video's UTC stamp, cannot be placed in local time: it is listed in `uncertain_ids` and its group's
    basis is "mixed-unknown". A naive time is never taken for UTC. Names, start and end use one basis:
    the local wall clock, with the offset when it is known. Assets' taken_at are not changed."""
    parsed = [(a["id"], t) for a in assets if (t := _parse(a.get("taken_at"))) is not None]
    where = zone(tz) if tz else None
    aware = [t for _, t in parsed if t.tzinfo]
    instant = where is not None or (parsed and len(aware) == len(parsed))
    rows = []                                  # (scale, id, time on the comparison basis, uncertain)
    for aid, t in parsed:
        if instant:
            t = t if t.tzinfo else t.replace(tzinfo=where)
            rows.append((_scale(t), aid, t, False))
        else:
            utc = t.tzinfo is not None and t.utcoffset() == timedelta(0)
            local = t.replace(tzinfo=None)
            rows.append((_scale(local), aid, local, utc))
    rows.sort(key=lambda r: (r[0], r[1]))
    runs: list[list[tuple]] = []
    for row in rows:
        if runs and row[0] - runs[-1][-1][0] <= EVENT_GAP_S:
            runs[-1].append(row)
        else:
            runs.append([row])
    out, keys = [], set()
    for run in runs:
        times = [r[2] for r in run]
        if instant:
            # show the group in one zone: tz when given, else the commonest non-UTC offset of its frames
            offsets = Counter(t.utcoffset() for t in times if t.utcoffset())
            show = where or (timezone(offsets.most_common(1)[0][0]) if offsets else times[0].tzinfo)
            times = [t.astimezone(show) for t in times]
        start, end = times[0], times[-1]
        key = f"event:{start:%Y%m%d-%H%M}"
        while key in keys:                          # same wall clock in another time zone
            key += "+"
        keys.add(key)
        uncertain = [r[1] for r in run if r[3]]
        basis = "instant" if instant else "mixed-unknown" if uncertain else "local"
        out.append(Group(key, "event", f"{start.month}月{start.day}日 {start:%H:%M} 起 · {len(run)} 张",
                         [r[1] for r in run],
                         {"count": len(run), "start": start.isoformat(), "end": end.isoformat(),
                          "time_basis": basis, "uncertain_ids": uncertain}))
    return out


def _cell(lat: float, lon: float) -> tuple[int, int]:
    i = math.floor(lat / GRID_DEG)
    step = GRID_DEG / max(math.cos(math.radians((i + 0.5) * GRID_DEG)), 0.01)
    return i, math.floor(lon / step)


def _places(assets: list[dict]) -> list[Group]:
    cells: dict[tuple[int, int], list[dict]] = {}
    for a in assets:
        lat, lon = a.get("lat"), a.get("lon")
        if isinstance(lat, (int, float)) and isinstance(lon, (int, float)) and not (lat == 0 and lon == 0):
            cells.setdefault(_cell(lat, lon), []).append(a)
    out = []
    for (i, j), members in cells.items():
        lat = sum(a["lat"] for a in members) / len(members)
        lon = sum(a["lon"] for a in members) / len(members)
        out.append(Group(f"place:{i}:{j}", "place", f"地点 · 约 {len(members)} 张 ({lat:.2f}, {lon:.2f})",
                         [a["id"] for a in members], {"count": len(members), "lat": round(lat, 5),
                                                      "lon": round(lon, 5)}))
    return sorted(out, key=lambda g: (-len(g.asset_ids), g.key))


def _by(assets: list[dict], kind: str) -> list[Group]:
    groups: dict[str, Group] = {}
    for a in assets:
        if kind == "scene":
            c = a.get("content") or {}
            value = c.get("group") or c.get("label")
            name = zh(value)
        else:
            s = a.get("species") or {}
            value = s.get("taxon") if s.get("level") == "species" else None
            name = s.get("common") or value
        if value:
            g = groups.setdefault(value, Group(f"{kind}:{value}", kind, name, [], {}))
            g.asset_ids.append(a["id"])
    for g in groups.values():
        g.summary = {"count": len(g.asset_ids)}
    return sorted(groups.values(), key=lambda g: (-len(g.asset_ids), g.key))


def suggest(assets: list[dict], *, tz: str | None = None) -> list[Group]:
    """assets: {id, taken_at, lat, lon, media, content: {group, label} | None, species: {taxon, common,
    level} | None}; tz: the zone camera-local (naive) times were taken in, when known (IANA name or
    +HH:MM; see _events). Events in time order, then places, scenes and species, each largest first.
    ValueError on an unknown tz."""
    return _events(assets, tz) + _places(assets) + _by(assets, "scene") + _by(assets, "species")
