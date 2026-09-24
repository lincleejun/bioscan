"""embed: the whole-frame SigLIP2 vector. Manifest only (stdlib)."""
from __future__ import annotations

from typing import Any

from bioscan.plugin import Manifest


def check(o: dict[str, Any]) -> None:
    if o["format"] not in ("list", "f16_base64"):
        raise ValueError("options.embed.format must be list or f16_base64")


MANIFEST = Manifest(
    name="embed",
    version=1,
    description="SigLIP2 whole-frame image vector (same forward pass as the gate).",
    reads=("vec",),
    models=lambda opts: ("siglip2",),
    thread="model",
    options={"format": {"type": "string", "enum": ["list", "f16_base64"], "default": "list"}},
    output={"model": "siglip2-base-patch16-224", "dim": 768, "vector": "list[float] | base64 float16 LE"},
    impl="bioscan.plugins.embed.stage:STAGE",
    check=check,
)
