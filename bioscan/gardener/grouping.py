"""Automatic group suggestions (docs/gardener/contract.md §4, §9.4): events split where the capture
instants are more than two hours apart (times with a UTC offset compared as instants, times without
one as camera-local wall clock), places on a ~1 km GPS grid (no reverse geocoding), scene groups
and species-level taxa. An asset without the field a kind needs is in no group of that kind.
Standard library only."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

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


def _time(taken_at: str | None) -> tuple[float, datetime] | None:
    """(instant in seconds, wall clock as shot). With a UTC offset the instant is exact, so a photo at
    -08:00 and a UTC video of the same moment line up; a time without one stays camera-local and is
    read on the same scale as if it were UTC (there is nothing better to compare it with)."""
    try:
        t = datetime.fromisoformat(taken_at) if taken_at else None
    except (TypeError, ValueError):
        return None
    if t is None:
        return None
    instant = t.timestamp() if t.tzinfo else t.replace(tzinfo=timezone.utc).timestamp()
    return instant, t.replace(tzinfo=None)


def _events(assets: list[dict]) -> list[Group]:
    timed = sorted((t[0], t[1], a["id"]) for a in assets if (t := _time(a.get("taken_at"))) is not None)
    runs: list[list[tuple[float, datetime, str]]] = []
    for row in timed:
        if runs and row[0] - runs[-1][-1][0] <= EVENT_GAP_S:
            runs[-1].append(row)
        else:
            runs.append([row])
    out, keys = [], set()
    for run in runs:
        start, end = run[0][1], run[-1][1]          # names in the first frame's own local time
        key = f"event:{start:%Y%m%d-%H%M}"
        while key in keys:                          # same wall clock in another time zone
            key += "+"
        keys.add(key)
        out.append(Group(key, "event",
                         f"{start.month}月{start.day}日 {start:%H:%M} 起 · {len(run)} 张", [r[2] for r in run],
                         {"count": len(run), "start": start.isoformat(), "end": end.isoformat()}))
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


def suggest(assets: list[dict]) -> list[Group]:
    """assets: {id, taken_at, lat, lon, media, content: {group, label} | None, species: {taxon, common,
    level} | None}. Events in time order, then places, scenes and species, each largest first."""
    return _events(assets) + _places(assets) + _by(assets, "scene") + _by(assets, "species")
