"""The location prior: how likely each row of a name list is at a place and date (p_geo), and the
posterior that weighs the visual scores with it (migrated from PhotoOS scan/geo.py). The prior
multiplies the whole name list's visual scores before top-k, so a species the eye ranks low can
still win where it lives.

One `LocationPrior` per name list that has one (`priors_for`). Behind it sits a source model with
`labels` and `probs(lat, lon, week)` aligned to them; today that is BirdNET geo 3.0 (`GeoPrior`),
for birds only. Optional: if birdnet cannot load there is no prior and posterior == p_visual."""
from __future__ import annotations

from functools import lru_cache
from typing import Any

import numpy as np

GEO_MODEL = ("geo", "3.0", "onnx")
GEO_FLOOR = 0.02
PRIOR_NAME = "birdnet-geo-3.0"


def week_of(taken_at: str | None) -> int | None:
    """BirdNET's 48-week calendar: four weeks per month, days 1-8 / 9-16 / 17-24 / 25-31."""
    if not taken_at or len(taken_at) < 10:
        return None
    try:
        month, day = int(taken_at[5:7]), int(taken_at[8:10])
    except ValueError:
        return None
    if not 1 <= month <= 12 or not 1 <= day <= 31:
        return None
    return (month - 1) * 4 + min(3, (day - 1) // 8) + 1


class GeoPrior:
    """The BirdNET geo model as a prior source: `labels` ("Scientific name_Common name") and
    `probs(lat, lon, week)` aligned to them, cached per place (2 decimals) and week."""

    def __init__(self, model: Any) -> None:
        self._model = model
        self.labels = [str(s) for s in model.species_list]

    @classmethod
    def load(cls) -> GeoPrior | None:
        try:
            import birdnet

            load_model: Any = birdnet.load
            return cls(load_model(*GEO_MODEL))
        except Exception:  # noqa: BLE001 - a missing prior is not an error
            return None

    @lru_cache(maxsize=64)  # noqa: B019 - one instance per process
    def _probs(self, lat: float, lon: float, week: int | None) -> np.ndarray:
        result = self._model.predict(lat, lon, week=week, min_confidence=0.0)
        return np.asarray(result.species_probs, dtype=np.float64)  # aligned with self.labels

    def probs(self, lat: float, lon: float, week: int | None) -> np.ndarray:
        return self._probs(round(lat, 2), round(lon, 2), week)


class LocationPrior:
    """A location prior bound to one name list.

    `row_labels` gives each list row's label in `source` ("" or an unknown label = the row has
    none and always gets p_geo 0). `source` is anything with `labels` and `probs(lat, lon, week)`
    aligned to them (`GeoPrior`, or a test stand-in). `name` is what engine info() reports."""

    floor = GEO_FLOOR           # of the posterior formula; in settings.fingerprint()

    def __init__(self, source: Any, row_labels: list[str], name: str = PRIOR_NAME) -> None:
        self.source = source
        self.name = name
        pos = {label: i for i, label in enumerate(source.labels)}
        self._index = np.array([pos.get(label, -1) for label in row_labels], dtype=np.int64)

    def _aligned(self, lat: float, lon: float, taken_at: str | None) -> np.ndarray:
        probs = self.source.probs(lat, lon, week_of(taken_at))
        return np.where(self._index >= 0, probs[np.maximum(self._index, 0)], 0.0)

    def p_geo(self, lat: float | None, lon: float | None, taken_at: str | None) -> np.ndarray | None:
        """One p_geo per list row at this place and date (any ISO-like `taken_at`; unparseable or
        None = the whole year). None when the place is unknown or the source fails: an optional
        prior must never cost the visual result."""
        if lat is None or lon is None:
            return None
        try:
            return self._aligned(lat, lon, taken_at)
        except Exception:  # noqa: BLE001 - see docstring
            return None

    def posterior(self, p_visual: np.ndarray, p_geo: np.ndarray | None) -> np.ndarray:
        """p_visual * (floor + p_geo) renormalised over the whole list; no p_geo -> p_visual itself."""
        if p_geo is None:
            return p_visual
        post = p_visual * (self.floor + p_geo)
        total = post.sum()
        return post / total if total > 0 else p_visual

    def gaps(self, scientific: list[str], common: list[str], lat: float, lon: float, taken_at: str | None,
             min_p: float) -> list[dict[str, Any]]:
        """List rows with no label whose genus *is* present here (a labelled congener has
        p_geo >= min_p). Those rows get p_geo = 0 today; a reviewed `birdnet` synonym is the fix,
        not a blanket rule (most unlabelled rows are extinct or lumped sisters that should stay
        at 0). Sorted by the congener's p_geo, strongest first. A failing source raises here."""
        p_geo, index = self._aligned(lat, lon, taken_at), self._index
        best: dict[str, tuple[float, int]] = {}
        for i, sci in enumerate(scientific):
            if index[i] >= 0:
                genus = sci.split(" ")[0]
                if p_geo[i] > best.get(genus, (-1.0, -1))[0]:
                    best[genus] = (float(p_geo[i]), i)
        out = []
        for i, sci in enumerate(scientific):
            hit = best.get(sci.split(" ")[0]) if index[i] < 0 else None
            if hit and hit[0] >= min_p:
                out.append({"scientific": sci, "common": common[i], "congener": scientific[hit[1]],
                            "congener_p_geo": round(hit[0], 4)})
        return sorted(out, key=lambda g: -g["congener_p_geo"])


def priors_for(lists: dict[str, Any], source: Any) -> dict[str, LocationPrior]:
    """kind -> LocationPrior over `source` (`GeoPrior.load()` in production) for every name list
    that carries BirdNET labels (`NameList.birdnet`); {} when there is no source."""
    if source is None:
        return {}
    return {kind: LocationPrior(source, nl.birdnet) for kind, nl in lists.items() if nl.birdnet}
