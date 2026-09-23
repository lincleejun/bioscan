"""BirdNET geo model (v3.0) as a location/week prior over bird candidates (migrated from
PhotoOS scan/geo.py). Reranks the visual top-k; never removes a candidate. Optional: if
birdnet cannot load there is no prior and posterior == p_visual."""
from __future__ import annotations

from functools import lru_cache
from typing import Any

GEO_MODEL = ("geo", "3.0", "onnx")
GEO_FLOOR = 0.02
GEO_MISSING = 0.05


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


def _norm(s: str | None) -> str:
    return " ".join((s or "").lower().replace("-", " ").replace("'", "").split())


class GeoPrior:
    def __init__(self, model: Any) -> None:
        self._model = model

    @classmethod
    def load(cls) -> GeoPrior | None:
        try:
            import birdnet

            load_model: Any = birdnet.load
            return cls(load_model(*GEO_MODEL))
        except Exception:  # noqa: BLE001 - a missing prior is not an error
            return None

    @lru_cache(maxsize=64)  # noqa: B019 - one instance per process
    def _probs(self, lat: float, lon: float, week: int | None) -> dict[str, float]:
        result = self._model.predict(lat, lon, week=week, min_confidence=0.0)
        out: dict[str, float] = {}
        for label, p in zip(result.species_list, result.species_probs):
            sci, _, common = str(label).partition("_")
            for k in (_norm(sci), _norm(common)):
                if k:
                    out[k] = float(p)
        return out

    def probs(self, lat: float, lon: float, week: int | None) -> dict[str, float]:
        return self._probs(round(lat, 2), round(lon, 2), week)


def rerank(cands: list[dict[str, Any]], probs: dict[str, float] | None) -> list[dict[str, Any]]:
    """posterior = p_visual * (0.02 + p_geo), renormalised over this list; None prior -> p_visual."""
    out = []
    for c in cands:
        c = dict(c)
        if probs is None:
            c["p_geo"], c["posterior"] = None, c["p_visual"]
        else:
            p = probs.get(_norm(c["scientific"]), probs.get(_norm(c.get("common"))))
            c["p_geo"] = GEO_MISSING if p is None else p
            c["posterior"] = c["p_visual"] * (GEO_FLOOR + c["p_geo"])
        out.append(c)
    if probs is not None:
        total = sum(c["posterior"] for c in out) or 1.0
        for c in out:
            c["posterior"] /= total
    return sorted(out, key=lambda c: -c["posterior"])
