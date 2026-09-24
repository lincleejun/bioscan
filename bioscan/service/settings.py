"""Fingerprint of every setting that changes identify output without changing a model: rule
thresholds, gate prompts, detector words, the geo formula and unlabelled policies, the accuracy
switches' defaults, image sizes. Reported in `info()` so a
result cache (or a person comparing two runs) can tell when results are no longer comparable."""
from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from typing import Any


def snapshot() -> dict[str, Any]:
    from bioscan.service import decode, names, pipeline, rules, taxa
    from bioscan.service.adapters import geo

    # every UPPER_CASE number in rules.py, so a threshold added there cannot be left out of the fingerprint
    thresholds = {k: v for k, v in vars(rules).items()
                  if k.isupper() and isinstance(v, (int, float)) and not isinstance(v, bool)}
    return {"rules": thresholds, "gate_prompts": taxa.GATE_PROMPTS, "vocab": taxa.VOCAB,
            "taxa": {"not_animal": list(taxa.NOT_ANIMAL), "promote_to": taxa.PROMOTE_TO,
                     "kind_check": list(taxa.KIND_CHECK)},
            "geo": {"model": list(geo.GEO_MODEL), "floor": geo.GEO_FLOOR, "neutral": geo.UNLABELLED_NEUTRAL,
                    "unlabelled": {kind: src.unlabelled for kind, src in names.LISTS.items()}},
            "switches": pipeline.SWITCHES, "prior_switch": pipeline.PRIOR_SWITCH, "max_edge": decode.MAX_EDGE}


@lru_cache(maxsize=1)
def fingerprint() -> str:
    """12 hex characters of sha256 over snapshot(); stable across processes and dict orders."""
    return hashlib.sha256(json.dumps(snapshot(), sort_keys=True).encode()).hexdigest()[:12]
