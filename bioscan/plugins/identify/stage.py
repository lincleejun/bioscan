"""identify as a stage: the loaded-list check for candidates, and one
pipeline.identify_many call per chunk."""
from __future__ import annotations

from typing import Any

from bioscan.plugin import Item, StageBase
from bioscan.service import candidates, pipeline


class Identify(StageBase):
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
