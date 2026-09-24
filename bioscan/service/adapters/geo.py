"""The location prior: how likely each row of a name list is at a place and date (p_geo), and the
posterior that weighs the visual scores with it (migrated from PhotoOS scan/geo.py). The prior
multiplies the whole name list's visual scores before top-k, so a species the eye ranks low can
still win where it lives.

One `LocationPrior` per name list that has one (`priors_for`). Behind it sits a source model with
`labels` and `probs(lat, lon, week)` aligned to them; today that is BirdNET geo 3.0 (`GeoPrior`),
for birds (AviList, unlabelled rows 0) and mammals (MDD, unlabelled rows backed off to their genus).
Optional: if birdnet cannot load there is no prior and posterior == p_visual."""
from __future__ import annotations

from functools import lru_cache
from typing import Any

import numpy as np

GEO_MODEL = ("geo", "3.0", "onnx")
GEO_FLOOR = 0.02
PRIOR_NAME = "birdnet-geo-3.0"
LABEL_SEP = "|"                  # several source labels on one list row: a lump; the row takes their max
UNLABELLED_POLICIES = ("zero", "genus")
# p_geo of an unlabelled row under the genus policy when no row of its genus has a label: no
# evidence either way, so neither the floor (absent) nor a presence value. Above rules.RANGE_EPS,
# so such a row is never range-vetoed. In settings.fingerprint().
UNLABELLED_NEUTRAL = 0.05


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

    `row_labels` gives each list row's label in `source`; several labels joined by LABEL_SEP mean
    the list lumps species the source keeps apart, and the row takes the largest of their values.
    A row with no label known to `source` is unlabelled; `unlabelled` says what it gets
    (UNLABELLED_POLICIES): "zero" (p_geo 0), or "genus" (the largest p_geo among the labelled rows
    of its genus, `genera` giving each row's genus; UNLABELLED_NEUTRAL when its genus has none).
    `source` is anything with `labels` and `probs(lat, lon, week)` aligned to them (`GeoPrior`, or
    a test stand-in). `name` is what engine info() reports."""

    floor = GEO_FLOOR           # of the posterior formula; in settings.fingerprint()

    def __init__(self, source: Any, row_labels: list[str], name: str = PRIOR_NAME, *,
                 unlabelled: str = "zero", genera: list[str] | None = None) -> None:
        if unlabelled not in UNLABELLED_POLICIES:
            raise ValueError(f"unlabelled policy {unlabelled!r} not in {UNLABELLED_POLICIES}")
        if unlabelled == "genus" and (genera is None or len(genera) != len(row_labels)):
            raise ValueError("the genus policy needs one genus per row")
        self.source = source
        self.name = name
        self.unlabelled = unlabelled
        pos = {label: i for i, label in enumerate(source.labels)}
        hits = [[pos[x] for x in label.split(LABEL_SEP) if x in pos] for label in row_labels]
        self._index = np.full((len(hits), max([1, *map(len, hits)])), -1, dtype=np.int64)  # (rows, labels)
        for i, h in enumerate(hits):
            self._index[i, :len(h)] = h
        self._labelled = (self._index >= 0).any(axis=1)
        # rows whose p_geo is evidence about the species itself: its own label, or the list's
        # "zero" policy declaring unlabelled rows absent; not a genus back-off (rules.range_veto)
        self.direct = self._labelled if unlabelled == "genus" else np.ones(len(hits), dtype=bool)
        if unlabelled == "genus":
            assert genera is not None
            ids = {g: i for i, g in enumerate(dict.fromkeys(genera))}
            self._genus = np.array([ids[g] for g in genera], dtype=np.int64)

    def _aligned(self, lat: float, lon: float, taken_at: str | None) -> np.ndarray:
        """The source's value per row (the largest of a lump's labels); 0 for unlabelled rows."""
        probs = self.source.probs(lat, lon, week_of(taken_at))
        return np.where(self._index >= 0, probs[np.maximum(self._index, 0)], 0.0).max(axis=1)

    def _backed_off(self, p: np.ndarray) -> np.ndarray:
        """Unlabelled rows under the genus policy: the best labelled congener, else neutral."""
        best = np.full(int(self._genus.max()) + 1 if len(self._genus) else 0, -1.0)
        np.maximum.at(best, self._genus[self._labelled], p[self._labelled])
        congener = best[self._genus]
        return np.where(self._labelled, p, np.where(congener >= 0, congener, UNLABELLED_NEUTRAL))

    def p_geo(self, lat: float | None, lon: float | None, taken_at: str | None) -> np.ndarray | None:
        """One p_geo per list row at this place and date (any ISO-like `taken_at`; unparseable or
        None = the whole year). None when the place is unknown or the source fails: an optional
        prior must never cost the visual result."""
        if lat is None or lon is None:
            return None
        try:
            p = self._aligned(lat, lon, taken_at)
            return self._backed_off(p) if self.unlabelled == "genus" else p
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
        p_geo, labelled = self._aligned(lat, lon, taken_at), self._labelled
        best: dict[str, tuple[float, int]] = {}
        for i, sci in enumerate(scientific):
            if labelled[i]:
                genus = sci.split(" ")[0]
                if p_geo[i] > best.get(genus, (-1.0, -1))[0]:
                    best[genus] = (float(p_geo[i]), i)
        out = []
        for i, sci in enumerate(scientific):
            hit = best.get(sci.split(" ")[0]) if not labelled[i] else None
            if hit and hit[0] >= min_p:
                out.append({"scientific": sci, "common": common[i], "congener": scientific[hit[1]],
                            "congener_p_geo": round(hit[0], 4)})
        return sorted(out, key=lambda g: -g["congener_p_geo"])


def priors_for(lists: dict[str, Any], source: Any) -> dict[str, LocationPrior]:
    """kind -> LocationPrior over `source` (`GeoPrior.load()` in production) for every name list
    that carries BirdNET labels (`NameList.birdnet`), with the list's unlabelled policy
    (`NameList.unlabelled`, "zero" when absent); {} when there is no source."""
    if source is None:
        return {}
    out = {}
    for kind, nl in lists.items():
        if nl.birdnet:
            policy = getattr(nl, "unlabelled", "zero")
            genera = [s.split(" ")[0] for s in nl.scientific] if policy == "genus" else None
            out[kind] = LocationPrior(source, nl.birdnet, unlabelled=policy, genera=genera)
    return out
