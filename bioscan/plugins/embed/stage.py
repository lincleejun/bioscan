"""embed as a stage: the chunk's whole-frame vectors, as a float list or base64 float16."""
from __future__ import annotations

import base64
from typing import Any

import numpy as np

from bioscan.plugin import Item, StageBase, each
from bioscan.service.adapters.siglip2 import MODEL_NAME


def embed(vec: np.ndarray, fmt: str) -> dict[str, Any]:
    v = np.asarray(vec, dtype=np.float32)
    vector: Any = ([round(float(x), 6) for x in v] if fmt == "list"
                   else base64.b64encode(v.astype("<f2").tobytes()).decode("ascii"))
    return {"model": MODEL_NAME, "dim": int(v.shape[0]), "vector": vector}


class Embed(StageBase):
    def settings(self) -> dict[str, Any]:
        return {"model": MODEL_NAME}

    def run(self, engine: Any, items: list[Item], o: dict[str, Any]) -> list[Any]:
        return each(items, lambda it: embed(it.vec, o["format"]))


STAGE = Embed()
