"""scene: a scene category per photo, zero-shot with SigLIP2 over the whole-frame vector the frame
pass already computed (no new model, no second forward). Manifest only (stdlib); the stage is
stage.py.

Each label is a list of text prompts, averaged into one text vector (encoded once per label set, on
the model thread). A label with no prompts takes the gate's share instead: `wildlife` is the gate's
bird + mammal probability (`wildlife_gate`), and the text labels share the rest by softmax. For the
top label `landscape` it also measures horizon tilt (Hough on a small copy of the frame). The labels
are an option, so an album can use its own categories.

With `groups` (docs/research/2026-09-24-scene-taxonomy.md §3.1-3.4) the labels are fine labels in
groups: the gate group's labels (`gate_group`) split the gate share by their own softmax and its main
label follows `wildlife_rules` over identify's boxes; the other labels share one softmax; the top
group (by the sum of its labels) gives the main label, and horizon is measured for the `landscape`
group. `attributes` are independent softmaxes (light, setting, framing). Without `groups`, as before.

The gate's probabilities drift on photos without animals (a rainbow or a parked car can read as a
third bird), so when identify is in the run (`boxes` is a fact) and found no box, the gate label
gets no share (`wildlife_box`; scene tier 2026-09-25: 259 of 375 photos wrongly called wildlife had
no box). A run of scene alone keeps the gate share, as before."""
from __future__ import annotations

import re
from typing import Any

from bioscan.plugin import Manifest, Metric

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
LANDSCAPE = "landscape"      # the label (with groups: the group) whose photos get a horizon measure


def check(o: dict[str, Any]) -> None:
    labels = o["labels"]
    if not isinstance(labels, dict) or len(labels) < 2:
        raise ValueError("options.scene.labels must be an object of at least two labels: {label: [prompts]}")
    for name, prompts in labels.items():
        if not re.fullmatch(r"[a-z0-9_]+", name):
            raise ValueError(f"options.scene.labels: label {name!r} must be lower-case letters, digits or _")
        if not isinstance(prompts, list) or not all(isinstance(p, str) and p.strip() for p in prompts):
            raise ValueError(f"options.scene.labels.{name} must be a list of text prompts")
    groups = o["groups"]
    if not isinstance(o["gate_group"], str):
        raise ValueError("options.scene.gate_group must be a group name")
    if not isinstance(groups, dict) or not all(isinstance(v, list) for v in groups.values()):
        raise ValueError("options.scene.groups must be an object {group: [labels]}")
    if groups:
        members = [k for v in groups.values() for k in v]
        if sorted(members) != sorted(labels):
            raise ValueError("options.scene.groups: every label must be in exactly one group, every member a label")
        gated = groups.get(o["gate_group"])
        if not gated or len(groups) < 2:
            raise ValueError("options.scene.gate_group must name a group, and there must be another group")
        if any(not labels[k] for k in members if k not in gated) or \
                (not all(labels[k] for k in gated) and len(gated) > 1):
            raise ValueError("options.scene.labels: only the gate group may have a label without prompts, "
                             "and then only that one label")
    elif sum(not p for p in labels.values()) > 1:
        raise ValueError("options.scene.labels: at most one label may be empty (the gate label)")
    attrs = o["attributes"]
    if not isinstance(attrs, dict) or not all(
            re.fullmatch(r"[a-z0-9_]+", n) and isinstance(v, dict) and v and all(
                isinstance(p, list) and p and all(isinstance(x, str) and x.strip() for x in p) for p in v.values())
            for n, v in attrs.items()):
        raise ValueError("options.scene.attributes must be {name: {value: [text prompts]}}, names lower-case "
                         "letters, digits or _")
    r = o["wildlife_rules"]
    if not isinstance(r, dict) or set(r) != {"portrait_area", "flock_boxes"} \
            or isinstance(r["portrait_area"], bool) or not isinstance(r["portrait_area"], (int, float)) \
            or not 0 <= r["portrait_area"] <= 1 \
            or isinstance(r["flock_boxes"], bool) or not isinstance(r["flock_boxes"], int) or r["flock_boxes"] < 1:
        raise ValueError("options.scene.wildlife_rules must be {portrait_area: 0-1, flock_boxes: integer >= 1}")
    g = o["wildlife_gate"]
    if not isinstance(g, list) or not g or not set(g) <= set(GATE_CLASSES):
        raise ValueError(f"options.scene.wildlife_gate must be a non-empty list of {', '.join(GATE_CLASSES)}")
    if not isinstance(o["wildlife_box"], bool):
        raise ValueError("options.scene.wildlife_box must be true or false")


MANIFEST = Manifest(
    name="scene",
    version=3,
    description="Scene category, zero-shot: SigLIP2 text prompts per label against the whole-frame vector "
                "(labels without prompts, wildlife by default, take the gate's share of wildlife_gate). Top "
                "label and every label's score; horizon tilt for landscape photos. With groups: fine labels in "
                "groups, the gate group split by its own prompts and identify's boxes, group scores and "
                "independent attributes.",
    reads=("vec", "gate", "image", "boxes?"),
    provides=("scene",),
    models=lambda opts: ("siglip2",),
    thread="model",
    options={"labels": {"type": "object", "additionalProperties": {"type": "array", "items": {"type": "string"}},
                        "default": DEFAULT_LABELS,
                        "description": "label -> text prompts (averaged); one label may be [] = the gate's share"},
             "wildlife_gate": {"type": "array", "items": {"type": "string", "enum": list(GATE_CLASSES)},
                               "default": ["bird", "mammal"],
                               "description": "gate classes whose probability the prompt-less label gets"},
             "wildlife_box": {"type": "boolean", "default": True,
                              "description": "the prompt-less label gets its share only when identify (in the run) "
                                             "found a box; false: the gate share regardless"},
             "groups": {"type": "object", "additionalProperties": {"type": "array", "items": {"type": "string"}},
                        "default": {}, "description": "group -> [labels], each label in one group; {} = each "
                                                      "label its own group, the prompt-less one the gate's"},
             "gate_group": {"type": "string", "default": "wildlife",
                            "description": "with groups: the group whose labels split the gate share"},
             "attributes": {"type": "object", "default": {},
                            "description": "name -> {value -> text prompts}: an independent softmax each"},
             "wildlife_rules": {"type": "object", "default": {"portrait_area": 0.08, "flock_boxes": 4},
                                "description": "with groups, the gate group's main label: flock_boxes boxes or "
                                               "more = herd_flock; the best box's area share >= portrait_area = "
                                               "<kind>_portrait, else <kind>_habitat"}},
    output={"label": "the top label (with groups: the top group's main label)",
            "group": "the top group (only with groups)", "scores": "{label: float}, summing to 1",
            "group_scores": "{group: float}, the sums of its labels' scores (only with groups)",
            "attributes": "{name: {label, scores}} (only with attributes)",
            "horizon": "null | {tilt_deg: degrees the dominant near-horizontal line rises to the right, "
                       "strength: its share of the edge weight} (landscape label, or group with groups)"},
    impl="bioscan.plugins.scene.stage:STAGE",
    check=check,
    metrics=(Metric("scene_acc", "rate", "bioscan.cull:row_scene",
                    description="top label or group is the truth's `scene`; scopes all and each truth label"),
             Metric("group_acc", "rate", "bioscan.cull:row_scene_group",
                    description="scene group (`group`, else the label) is the truth's `scene_group`; "
                                "scopes all and each truth group")),
    fingerprinted=True,
)
