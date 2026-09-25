"""geotag as a stage: one bioscan.geotag.geotag call per chunk, on the CPU pool. A photo whose request
or EXIF has a place keeps it (place_source request / exif); otherwise the track's position becomes
the "place" fact identify reads (gpx), or there is none (none)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from bioscan import geotag as gt
from bioscan.plugin import Item, StageBase


@lru_cache(maxsize=8)
def _track(paths: tuple[str, ...], stamps: tuple[tuple[float, int], ...]) -> gt.Track:
    """The merged track, parsed once per run (keyed on the files' mtime and size)."""
    return gt.Track.load(list(paths))


def track(paths: list[str]) -> gt.Track:
    """ValueError (-> 400 before the run) when a file cannot be read or holds no timed point."""
    try:
        stamps = tuple((Path(p).stat().st_mtime, Path(p).stat().st_size) for p in paths)
        t = _track(tuple(paths), stamps)
    except (OSError, gt.ET.ParseError) as e:
        raise ValueError(f"options.geotag.gpx: cannot read GPX: {e}") from None
    if not len(t):
        raise ValueError(f"options.geotag.gpx: no timed track points in {', '.join(paths)}")
    return t


class Geotag(StageBase):
    def check_loaded(self, engine: Any, o: dict[str, Any]) -> None:
        if o["gpx"]:
            track(o["gpx"])

    def reads_paths(self, o: dict[str, Any]) -> list[str]:
        return list(o["gpx"])

    def settings(self) -> dict[str, Any]:
        return {k: getattr(gt, k) for k in ("GPS_ERR_M", "DRIFT_MPS", "WALK_MPS", "EARTH_RADIUS_M")}

    def run(self, engine: Any, items: list[Item], o: dict[str, Any]) -> list[Any]:
        if not o["gpx"]:
            return [None] * len(items)
        photos = [gt.Photo(it.inp["path"], it.taken_at, it.lat, it.lon, camera=getattr(it.dec, "camera", None))
                  for it in items]
        res = gt.geotag(photos, track(o["gpx"]), gt.resolve_tz(o["camera_utc_offset"] or None),
                        gt.parse_offset(o["offset"]) if o["offset"] else 0.0, estimate=False,
                        max_gap_s=o["max_gap_s"], max_span_m=o["max_span_m"], extrapolate_s=o["extrapolate_s"],
                        max_still_s=o["max_still_s"],
                        given={c: gt.parse_offset(v) for c, v in o["camera_offsets"].items()})
        out: list[Any] = []
        for it, f in zip(items, res.fixes):
            if f.source == "exif":
                out.append({"place_source": "request" if it.inp["lat"] is not None else "exif", "utc": f.utc})
            elif f.source == "gpx":
                it.facts["place"] = (f.lat, f.lon)
                out.append({"place_source": "gpx", "lat": f.lat, "lon": f.lon, "dt_s": f.dt_s, "err_m": f.err_m,
                            "utc": f.utc})
            else:
                out.append({"place_source": "none", "dt_s": f.dt_s, "utc": f.utc})
        return out


STAGE = Geotag()
