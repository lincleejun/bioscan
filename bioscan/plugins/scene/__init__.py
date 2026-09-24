"""scene: a scene category per photo, zero-shot with SigLIP2 over the whole-frame vector the frame
pass already computed (no new model, no second forward). Manifest only (stdlib); the stage is
stage.py.

Each label is a list of text prompts, averaged into one text vector (encoded once per label set, on
the model thread). A label with no prompts takes the gate's share instead: `wildlife` is the gate's
bird + mammal probability (`wildlife_gate`), and the text labels share the rest by softmax. For the
top label `landscape` it also measures horizon tilt (Hough on a small copy of the frame). The labels
are an option, so an album can use its own categories."""
from __future__ import annotations

import re
from typing import Any

from bioscan.plugin import Manifest

GATE_CLASSES = ("bird", "mammal", "other_animal", "person", "none")    # = bioscan.service.taxa.GATE_CLASSES
DEFAULT_LABELS: dict[str, list[str]] = {
    "landscape": ["a landscape photo", "a photo of mountains and valleys", "a photo of the sea, a beach or a lake",
                  "a photo of a forest or a meadow"],
    "people": ["a portrait photo of a person", "a street photo with people", "a photo of a group of people"],
    "wildlife": [],
    "macro": ["a macro photo of an insect", "a close-up photo of a flower", "a macro photo of a small creature"],
    "architecture": ["a photo of a building", "a photo of a city street", "a photo of architecture"],
    "food": ["a photo of food", "a photo of a meal on a plate", "a photo of a drink"],
    "night": ["a photo of the night sky with stars", "a photo of the milky way", "a night photo of city lights"],
    "other": ["a photo of an object", "a screenshot or a document", "an indoor photo of a room"],
}
LANDSCAPE = "landscape"      # the label whose photos get a horizon measure


def check(o: dict[str, Any]) -> None:
    labels = o["labels"]
    if not isinstance(labels, dict) or len(labels) < 2:
        raise ValueError("options.scene.labels must be an object of at least two labels: {label: [prompts]}")
    for name, prompts in labels.items():
        if not re.fullmatch(r"[a-z0-9_]+", name):
            raise ValueError(f"options.scene.labels: label {name!r} must be lower-case letters, digits or _")
        if not isinstance(prompts, list) or not all(isinstance(p, str) and p.strip() for p in prompts):
            raise ValueError(f"options.scene.labels.{name} must be a list of text prompts")
    if sum(not p for p in labels.values()) > 1:
        raise ValueError("options.scene.labels: at most one label may be empty (the gate label)")
    if not any(labels.values()):
        raise ValueError("options.scene.labels needs a label with prompts")
    g = o["wildlife_gate"]
    if not isinstance(g, list) or not g or not set(g) <= set(GATE_CLASSES):
        raise ValueError(f"options.scene.wildlife_gate must be a non-empty list of {', '.join(GATE_CLASSES)}")


MANIFEST = Manifest(
    name="scene",
    version=1,
    description="Scene category, zero-shot: SigLIP2 text prompts per label against the whole-frame vector "
                "(labels without prompts, wildlife by default, take the gate's share of wildlife_gate). Top "
                "label and every label's score; horizon tilt for landscape photos.",
    reads=("vec", "gate", "image"),
    provides=("scene",),
    models=lambda opts: ("siglip2",),
    thread="model",
    options={"labels": {"type": "object", "additionalProperties": {"type": "array", "items": {"type": "string"}},
                        "default": DEFAULT_LABELS,
                        "description": "label -> text prompts (averaged); one label may be [] = the gate's share"},
             "wildlife_gate": {"type": "array", "items": {"type": "string", "enum": list(GATE_CLASSES)},
                               "default": ["bird", "mammal"],
                               "description": "gate classes whose probability the prompt-less label gets"}},
    output={"label": "the top label", "scores": "{label: float}, summing to 1",
            "horizon": "null | {tilt_deg: degrees the dominant near-horizontal line rises to the right, "
                       "strength: its share of the edge weight} (landscape only)"},
    impl="bioscan.plugins.scene.stage:STAGE",
    check=check,
    fingerprinted=True,
)
