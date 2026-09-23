"""Fingerprint of every setting that changes identify output without changing a model: rule
thresholds, gate prompts, detector words, the geo formula, image sizes. Reported in `info()` so a
result cache (or a person comparing two runs) can tell when results are no longer comparable."""
from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from typing import Any


def snapshot() -> dict[str, Any]:
    from bioscan.service import decode, products
    from bioscan.service.adapters import geo, owlv2, siglip2

    rules = {k: getattr(products, k) for k in ("VETO", "MAMMAL_SUPPORT", "BIRD_PROMOTE", "MIN_CROP", "SPECIES_P",
                                               "SPECIES_MARGIN", "ROLLUP", "SECOND_PASS_FLOOR", "SECOND_PASS_TOP",
                                               "IOU_SAME", "RESCUE")}
    return {"rules": rules, "gate_prompts": siglip2.GATE_PROMPTS, "vocab": owlv2.VOCAB,
            "geo": {"model": list(geo.GEO_MODEL), "floor": geo.GEO_FLOOR}, "max_edge": decode.MAX_EDGE}


@lru_cache(maxsize=1)
def fingerprint() -> str:
    """12 hex characters of sha256 over snapshot(); stable across processes and dict orders."""
    return hashlib.sha256(json.dumps(snapshot(), sort_keys=True).encode()).hexdigest()[:12]
