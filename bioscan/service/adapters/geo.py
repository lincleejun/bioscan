"""BirdNET geo model (v3.0) as a location/week prior over bird names (migrated from PhotoOS
scan/geo.py). The prior multiplies the whole name list's visual scores before top-k, so a species
the eye ranks low can still win where it lives. Optional: if birdnet cannot load there is no
prior and posterior == p_visual."""
from __future__ import annotations

from functools import lru_cache
from typing import Any

import numpy as np

GEO_MODEL = ("geo", "3.0", "onnx")
GEO_FLOOR = 0.02


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
    def __init__(self, model: Any) -> None:
        self._model = model
        self.labels = [str(s) for s in model.species_list]  # "Scientific name_Common name"

    @classmethod
    def load(cls) -> GeoPrior | None:
        try:
            import birdnet

            load_model: Any = birdnet.load
            return cls(load_model(*GEO_MODEL))
        except Exception:  # noqa: BLE001 - a missing prior is not an error
            return None

    def index(self, labels: list[str]) -> np.ndarray:
        """Position of each name-list row's BirdNET label in `probs()`, -1 where it has none."""
        pos = {label: i for i, label in enumerate(self.labels)}
        return np.array([pos.get(label, -1) for label in labels], dtype=np.int64)

    @lru_cache(maxsize=64)  # noqa: B019 - one instance per process
    def _probs(self, lat: float, lon: float, week: int | None) -> np.ndarray:
        result = self._model.predict(lat, lon, week=week, min_confidence=0.0)
        return np.asarray(result.species_probs, dtype=np.float64)  # aligned with self.labels

    def probs(self, lat: float, lon: float, week: int | None) -> np.ndarray:
        return self._probs(round(lat, 2), round(lon, 2), week)


def align(probs: np.ndarray, index: np.ndarray) -> np.ndarray:
    """BirdNET probabilities -> one p_geo per name-list row; rows without a label get 0."""
    return np.where(index >= 0, probs[np.maximum(index, 0)], 0.0)


def posterior(p_visual: np.ndarray, p_geo: np.ndarray | None) -> np.ndarray:
    """p_visual * (0.02 + p_geo) renormalised over the whole list; no prior -> p_visual."""
    if p_geo is None:
        return p_visual
    post = p_visual * (GEO_FLOOR + p_geo)
    total = post.sum()
    return post / total if total > 0 else p_visual


def gaps(scientific: list[str], common: list[str], index: np.ndarray, probs: np.ndarray,
         min_p: float) -> list[dict[str, Any]]:
    """Name-list rows with no BirdNET label whose genus *is* present here (a mapped congener has
    p_geo >= min_p). Those rows get p_geo = 0 today; a reviewed `birdnet` synonym is the fix, not a
    blanket rule (most unmapped rows are extinct or lumped sisters that should stay at 0).
    Sorted by the congener's p_geo, strongest first."""
    p_geo = align(probs, index)
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
