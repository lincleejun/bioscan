"""geotag: a place for photos without one, from GPX tracks (bioscan.geotag, stdlib). Manifest only;
the stage is stage.py.

It reads each image's capture time and provides "place" only when neither the request nor EXIF
has one, so identify (which reads "place") runs after it and uses the track position for its
location prior. Off unless `gpx` names a track: a run without one is unchanged. In the wildlife
profile, never in full. The options are `bioscan geotag`'s: gpx (--gpx), camera_utc_offset (--tz:
the zone of capture times without an offset; empty = the service's zone), offset (--offset: the
camera clock minus true time; empty = 0, the stage sees one chunk so it does not estimate one;
`bioscan run --gpx` estimates it over the whole folder and sends it), max_gap_s, max_span_m,
max_still_s, extrapolate_s (--max-gap, --max-span, --max-still, --extrapolate)."""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from bioscan import geotag as gt
from bioscan.plugin import Manifest

LIMITS = ("max_gap_s", "max_span_m", "max_still_s", "extrapolate_s")


def check(o: dict[str, Any]) -> None:
    g = o["gpx"]
    if not isinstance(g, list) or not all(isinstance(p, str) and Path(p).is_absolute() for p in g):
        raise ValueError("options.geotag.gpx must be a list of absolute paths")
    for key in LIMITS:
        v = o[key]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
            raise ValueError(f"options.geotag.{key} must be a number >= 0")
    for key, parse in (("offset", gt.parse_offset), ("camera_utc_offset", gt.resolve_tz)):
        if not isinstance(o[key], str):
            raise ValueError(f"options.geotag.{key} must be a string")
        if o[key]:
            try:
                parse(o[key])
            except ValueError as e:
                raise ValueError(f"options.geotag.{key}: {e}") from None


MANIFEST = Manifest(
    name="geotag",
    version=1,
    description="Place from GPX tracks for photos whose request and EXIF have none: capture time -> UTC "
                "(camera_utc_offset), minus the camera clock offset, interpolated along the track (the rules "
                "of `bioscan geotag`). The place feeds identify's location prior.",
    reads=("time",),
    provides=("place",),
    thread="cpu",
    options={"gpx": {"type": "array", "items": {"type": "string", "format": "absolute path"}, "default": [],
                     "description": "GPX 1.0/1.1 track files; empty = off"},
             "camera_utc_offset": {"type": "string", "default": "",
                                   "description": "zone of capture times without an offset: -07:00, UTC, "
                                                  "America/Los_Angeles; empty = the service's zone"},
             "offset": {"type": "string", "default": "",
                        "description": "camera clock minus true time: +00:01:23 or seconds; empty = 0"},
             "max_gap_s": {"type": "number", "minimum": 0, "default": gt.MAX_GAP_S},
             "max_span_m": {"type": "number", "minimum": 0, "default": gt.MAX_SPAN_M},
             "max_still_s": {"type": "number", "minimum": 0, "default": gt.MAX_STILL_S},
             "extrapolate_s": {"type": "number", "minimum": 0, "default": gt.EXTRAPOLATE_S}},
    output={"place_source": "request | exif | gpx | none", "lat": "float (gpx)", "lon": "float (gpx)",
            "dt_s": "seconds to the nearest track point (gpx, none)", "err_m": "estimated error, m (gpx)",
            "utc": "corrected capture time, UTC"},
    impl="bioscan.plugins.geotag.stage:STAGE",
    check=check,
)
