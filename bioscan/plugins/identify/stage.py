"""identify as a stage: option checks, the loaded-list check for candidates, and one
pipeline.identify_many call per chunk."""
from __future__ import annotations

from typing import Any

from bioscan.plugin import Item, StageBase
from bioscan.plugins.identify import MAX_CANDIDATES, SWITCHES
from bioscan.service import candidates, pipeline


class Identify(StageBase):
    def check(self, o: dict[str, Any]) -> None:
        top_k = o["top_k"]
        if isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= 50:
            raise ValueError("options.identify.top_k must be an integer 1-50")
        for key in ("geo", "species", *SWITCHES):
            if not isinstance(o[key], bool):
                raise ValueError(f"options.identify.{key} must be a boolean")
        c = o["candidates"]
        if (not isinstance(c, list) or len(c) > MAX_CANDIDATES
                or not all(isinstance(x, str) and x.strip() for x in c)):
            raise ValueError(f"options.identify.candidates must be a list of at most {MAX_CANDIDATES} "
                             "non-empty names")
        o["candidates"] = [x.strip() for x in c]

    def check_loaded(self, engine: Any, o: dict[str, Any]) -> None:
        """Candidates every loaded name list is blind to are a request error, named in the message."""
        unknown = candidates.unknown(engine.names, o["candidates"]) if o["candidates"] else []
        if unknown:
            raise ValueError(f"options.identify.candidates: no loaded name list ({', '.join(sorted(engine.names))}) "
                             f"has {unknown}")

    def settings(self) -> dict[str, Any]:
        from bioscan.service import settings

        return settings.snapshot()

    def run(self, engine: Any, items: list[Item], o: dict[str, Any]) -> list[Any]:
        outs = pipeline.identify_many(
            engine, [pipeline.Frame(it.dec.image, it.gate, it.lat, it.lon, it.taken_at, it.dec.detail)
                     for it in items], o)
        for it, out in zip(items, outs):
            if not isinstance(out, BaseException):
                it.facts["boxes"] = out["boxes"]          # what identify provides to later stages
        return outs


STAGE = Identify()
