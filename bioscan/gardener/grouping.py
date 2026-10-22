"""Automatic group suggestions (docs/gardener/contract.md §4, §9.4, 单元 3): two levels.

1. Subject: who or what the photo is of. A person found by the face clusters, a taxon from species
   identification (species level, or genus/family when that is all it says), an animal type when no
   taxon is named (bird / mammal / other, from the content label), else the content label (山地,
   海岸, 人像 ...). A photo with a person and a taxon is in both groups; scenes take the rest.
   Videos form one 视频 group; a photo with no subject information is 未分类.
2. Within a subject of at least SPLIT_MIN photos: by place when most members carry GPS (a ~2 km grid
   whose neighbouring cells join), otherwise by shooting day (the local wall clock; with `tz`, times
   with offsets are shown in that zone). Only a split into two or more parts is made.

Groups smaller than MIN_GROUP fold into a broader one (a rare species into its animal type, a rare
scene into 其他场景, a rare person into 人物 · 其他; small days or places into 其他日期 / 其他地点).
`final=False` (while analysis is still running) makes only subject groups that already meet the
size bar, never fold groups or children: members added then stay right when the run finishes.
An asset that is not `settled` (its subject capabilities still pending) is left out until it is.
Pure time is never a group on its own. Standard library only."""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from bioscan.gardener.analyze import zh

MIN_GROUP = 3
SPLIT_MIN = 10
GPS_SHARE = 0.5
GRID_DEG = 0.018            # ~2 km of latitude

BIRD = {"bird_portrait", "bird_flight", "bird_habitat"}
MAMMAL = {"mammal_portrait", "mammal_habitat"}
OTHER_ANIMAL = {"herd_flock", "other_animal", "domestic", "insect_macro"}
PEOPLE = {"portrait", "group", "street", "event", "sport"}
ANIMAL_BUCKET = {"bird": ("animal:bird", "鸟类 · 未识别到种"), "mammal": ("animal:mammal", "哺乳动物 · 未识别到种"),
                 "other": ("animal:other", "其他动物")}
LEVEL_ZH = {"genus": "属", "family": "科", "order": "目", "class": "纲"}
PERSON_OTHER = ("person:other", "人物 · 其他")
SCENE_OTHER = ("scene:other", "其他场景")


@dataclass
class Group:
    key: str                # stable: species:<taxon>, person:<seed>, animal:bird, scene:<label>, other:video, <parent>/day:2026-08-29
    kind: str               # species | person | animal | scene | other | day | place
    name: str               # Chinese
    asset_ids: list[str]
    summary: dict = field(default_factory=dict)
    parent: str | None = None


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


def _day(taken_at: str | None, where: tzinfo | None) -> tuple[date | None, bool]:
    """(shooting day, uncertain). The local wall clock; a UTC stamp (Z / +00:00, typically a video's)
    without a task zone cannot be placed in local time and is marked uncertain."""
    t = _parse(taken_at)
    if t is None:
        return None, False
    if where is not None:
        return (t if t.tzinfo else t.replace(tzinfo=where)).astimezone(where).date(), False
    return t.date(), t.tzinfo is not None and t.utcoffset() == timedelta(0)


def _animal_type(label: str | None) -> str | None:
    return "bird" if label in BIRD else "mammal" if label in MAMMAL else "other" if label in OTHER_ANIMAL else None


def _subjects(a: dict, people_names: dict[str, str]) -> list[tuple[str, str, str, tuple[str, str] | None]]:
    """[(key, kind, name, fold-into (key, name) or None)] for one asset."""
    if a.get("media") == "video":
        return [("other:video", "other", "视频", None)]
    label = (a.get("content") or {}).get("label")
    out = [(f"person:{p}", "person", people_names[p], PERSON_OTHER) for p in a.get("people") or [] if p in people_names]
    sp = a.get("species") or {}
    animal = _animal_type(label)
    if sp.get("taxon"):
        level = sp.get("level") or "species"
        name = (sp.get("common") or sp["taxon"]) if level == "species" else \
            f"{sp['taxon']}（{LEVEL_ZH.get(level, level)}）"
        out.append((f"species:{sp['taxon']}", "species", name, ANIMAL_BUCKET[animal or "other"]))
    elif animal:
        k, n = ANIMAL_BUCKET[animal]
        out.append((k, "animal", n, None))
    if out:
        return out
    if label in PEOPLE:
        return [(PERSON_OTHER[0], "person", PERSON_OTHER[1], None)]
    if label:
        return [(f"scene:{label}", "scene", zh(label) or label, SCENE_OTHER)]
    return [("other:none", "other", "未分类", None)]


def _places(members: list[dict]) -> dict[str, int]:
    """asset id -> place number. A grid of ~GRID_DEG cells (one longitude step for the whole subject,
    from its mean latitude); occupied cells that touch (8-neighbourhood) are one place."""
    step = GRID_DEG / max(math.cos(math.radians(sum(a["lat"] for a in members) / len(members))), 0.01)
    cell_of = {a["id"]: (math.floor(a["lat"] / GRID_DEG), math.floor(a["lon"] / step)) for a in members}
    occupied = set(cell_of.values())
    place: dict[tuple[int, int], int] = {}
    for start in sorted(occupied):
        if start in place:
            continue
        n = len(set(place.values()))
        place[start], todo = n, [start]
        while todo:
            i, j = todo.pop()
            for nb in ((i + di, j + dj) for di in (-1, 0, 1) for dj in (-1, 0, 1)):
                if nb in occupied and nb not in place:
                    place[nb] = n
                    todo.append(nb)
    return {aid: place[c] for aid, c in cell_of.items()}


def _has_gps(a: dict) -> bool:
    lat, lon = a.get("lat"), a.get("lon")
    return isinstance(lat, (int, float)) and isinstance(lon, (int, float)) and not (lat == 0 and lon == 0)


def _md(d: date, years: set[int]) -> str:
    return f"{d.year}年{d.month}月{d.day}日" if len(years) > 1 else f"{d.month}月{d.day}日"


def _children(parent: Group, members: list[dict], where: tzinfo | None) -> list[Group]:
    """Split one subject by place (most members have GPS) or by day; [] when it is one part."""
    located = [a for a in members if _has_gps(a)]
    parts: dict[str, list[dict]] = {}
    names: dict[str, str] = {}
    days = {a["id"]: _day(a.get("taken_at"), where) for a in members}
    years = {d.year for d, _ in days.values() if d}
    if len(located) >= GPS_SHARE * len(members):
        grid = _places(located)
        for a in members:
            key = f"place:{grid[a['id']]}" if a["id"] in grid else "place:none"
            parts.setdefault(key, []).append(a)
        for key, ms in parts.items():
            if key == "place:none":
                names[key] = "地点未知"
                continue
            lat = sum(a["lat"] for a in ms) / len(ms)
            lon = sum(a["lon"] for a in ms) / len(ms)
            ds = sorted(d for a in ms if (d := days[a["id"]][0]))
            span = "" if not ds else f" · {_md(ds[0], years)}" + ("" if ds[0] == ds[-1] else f"–{_md(ds[-1], years)}")
            names[key] = f"地点 ({lat:.2f}, {lon:.2f}){span}"
        other = ("place:other", "其他地点")
    else:
        for a in members:
            d = days[a["id"]][0]
            key = f"day:{d.isoformat()}" if d else "day:none"
            parts.setdefault(key, []).append(a)
            names[key] = _md(d, years) if d else "无日期"
        other = ("day:other", "其他日期")
    kind = "place" if other[0].startswith("place") else "day"
    small = [k for k, ms in parts.items() if len(ms) < MIN_GROUP and not k.endswith(":none")]
    if small:
        folded = [a for k in small for a in parts.pop(k)]
        parts[other[0]] = folded
        names[other[0]] = other[1]
    if len(parts) < 2:
        return []
    out = []
    for key in sorted(parts, key=lambda k: (k.endswith(":none"), k.endswith(":other"), k)):
        ms = parts[key]
        uncertain = [a["id"] for a in ms if days[a["id"]][1]]
        ds = sorted(d.isoformat() for a in ms if (d := days[a["id"]][0]))
        out.append(Group(f"{parent.key}/{key}", kind, f"{parent.name} · {names[key]}", [a["id"] for a in ms],
                         {"count": len(ms), "start": ds[0] if ds else None, "end": ds[-1] if ds else None,
                          "uncertain_ids": uncertain, "time_basis": "instant" if where else "local"},
                         parent=parent.key))
        out[-1].summary["label"] = names[key]
    return out


def suggest(assets: list[dict], *, tz: str | None = None, final: bool = True) -> list[Group]:
    """assets: {id, taken_at, lat, lon, media, content: {group, label} | None, species: {taxon, common,
    level} | None, people: [person key] | None, settled: bool (default True)}; person keys come
    largest person first (they are named 人物 1, 人物 2, ...). tz: the zone camera-local times were
    taken in, when known (IANA name or +HH:MM). Returns parents (largest first) followed by their
    children. ValueError on an unknown tz."""
    where = zone(tz) if tz else None
    ready = [a for a in assets if a.get("settled", True)]
    order: list[str] = []
    for a in ready:
        for p in a.get("people") or []:
            if p not in order:
                order.append(p)
    sizes = Counter(p for a in ready for p in set(a.get("people") or []))
    order.sort(key=lambda p: -sizes[p])
    people_names = {p: f"人物 {i + 1}" for i, p in enumerate(order)}
    groups: dict[str, Group] = {}
    pending: list[tuple[dict, tuple[str, str, str, tuple[str, str] | None]]] = []
    for a in ready:
        for s in _subjects(a, people_names):
            pending.append((a, s))
    count = Counter(s[0] for _, s in pending)
    by_id = {a["id"]: a for a in ready}
    for a, (key, kind, name, fold) in pending:
        if count[key] < MIN_GROUP and fold is not None:
            if not final:
                continue
            key, name = fold
            kind = "person" if key.startswith("person") else "animal" if key.startswith("animal") else "scene"
        elif count[key] < MIN_GROUP and not final:
            continue
        g = groups.setdefault(key, Group(key, kind, name, [], {}))
        if a["id"] not in g.asset_ids:
            g.asset_ids.append(a["id"])
    parents = sorted(groups.values(), key=lambda g: (-len(g.asset_ids), g.key))
    for g in parents:
        g.summary = {"count": len(g.asset_ids)}
    if not final:
        return parents
    children = [c for g in parents if len(g.asset_ids) >= SPLIT_MIN
                for c in _children(g, [by_id[i] for i in g.asset_ids], where)]
    return parents + children
