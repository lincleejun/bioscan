"""aesthetics: an aesthetic score from the whole-frame SigLIP2 vector the frame pass already computes
(a linear head, bioscan/aesthetic.py). Manifest only (stdlib); the stage is stage.py.

The general head is the committed EVA head (data/aesthetic/eva-head-v1.json, CC0 annotations); a
personal head is fitted on the owner's own ratings (`bioscan aesthetic train`). Options: `head` is
"builtin" (the general head alone), an absolute path to a personal head file (blended with the
general head by `blend`, the personal head's weight), or "off" (no product). Without the builtin
head (not trained yet) the score is null with a `note`, never an error. The score only reorders
frames (the select reducer reads products.aesthetics.score); it never rejects one. In `album`, never
in `full` or `wildlife`."""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from bioscan.plugin import Manifest

BUILTIN, OFF = "builtin", "off"
BLEND = 0.5


def check(o: dict[str, Any]) -> None:
    h = o["head"]
    if not isinstance(h, str) or not (h in (BUILTIN, OFF) or Path(h).is_absolute()):
        raise ValueError("options.aesthetics.head must be \"builtin\", \"off\" or an absolute path to a head file")
    b = o["blend"]
    if isinstance(b, bool) or not isinstance(b, (int, float)) or not math.isfinite(b) or not 0 <= b <= 1:
        raise ValueError("options.aesthetics.blend must be a number 0-1")


MANIFEST = Manifest(
    name="aesthetics",
    version=1,
    description="Aesthetic score 0-1 from a linear head on the whole-frame SigLIP2 vector: the general head "
                "(EVA, CC0 annotations), optionally blended with a personal head fitted on the owner's ratings. "
                "Reorders only; never rejects a frame.",
    reads=("vec",),
    provides=("aesthetic",),
    models=lambda opts: ("siglip2",),
    thread="cpu",
    options={"head": {"type": "string", "default": BUILTIN,
                      "description": "builtin = the general head; an absolute path = a personal head file "
                                     "(bioscan aesthetic train), blended with the general head; off = no product"},
             "blend": {"type": "number", "minimum": 0, "maximum": 1, "default": BLEND,
                       "description": "the personal head's weight when both heads score a frame"}},
    output={"score": "float 0-1 (unclipped) | null: the blend, else the head there is",
            "general": "float | null: the general head's score", "personal": "float | null",
            "head_id": "name:sha12[+name:sha12~blend] | null",
            "note": "string, only when the general head is missing or unusable (score is then the personal "
                    "head's, or null)"},
    impl="bioscan.plugins.aesthetics.stage:STAGE",
    check=check,
)
