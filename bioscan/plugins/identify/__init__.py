"""identify: gate, animal boxes, per-box quality and species. Manifest only (stdlib); the stage is
stage.py, whose body is pipeline.identify_many."""
from __future__ import annotations

from bioscan import contract
from bioscan.plugin import Manifest

MAX_CANDIDATES = 1000

# The v1.5 accuracy fixes, each an identify option so a run can switch one off to measure it
# (`bioscan eval --identify-opt NAME=false`). A missing option means the default here. In
# settings.fingerprint() (as pipeline.SWITCHES).
SWITCHES = {
    "range_veto": True,      # rules.range_veto at the species step
    "kind_check": True,      # species evidence may move a box between KIND_CHECK kinds
    "mammal_geo": True,      # the mammal list's location prior (MDD rows, genus back-off)
}

MANIFEST = Manifest(
    name="identify",
    version=1,
    description="Scene gate (SigLIP2), animal boxes (OWLv2 + crop gate), per-box quality and species "
                "(BioCLIP 2.5 Huge zero-shot against AviList for birds, MDD for mammals and the "
                "TreeOfLife-200M all-taxa list for other animals; optional BirdNET geo prior for birds "
                "and mammals, range veto, kind check).",
    reads=("image", "gate", "detail", "place", "time"),
    provides=("boxes",),
    models=lambda opts: ("siglip2", "owlv2", "bioclip"),
    thread="model",
    options={"top_k": {"type": "integer", "minimum": 1, "maximum": 50, "default": 5},
             "geo": {"type": "boolean", "default": True},
             "species": {"type": "boolean", "default": True},
             # accuracy fixes, on by default; false switches one off to measure it (README)
             **{k: {"type": "boolean", "default": v} for k, v in SWITCHES.items()},
             "candidates": {"type": "array", "items": {"type": "string"}, "maxItems": MAX_CANDIDATES,
                            "default": [],
                            "description": "Optional. Rank species only among these taxa: scientific names "
                                           "or any higher taxon (genus, family, order, class), matched "
                                           "across every loaded name list. Only lists with a matching row "
                                           "compete: the kind check picks among them (off: the box keeps "
                                           "its kind if its list has one, else the list with the most "
                                           "evidence). Empty = all taxa. Names no list knows are a 400."}},
    output=contract.IDENTIFY_OUTPUT,          # the payload's fields live in bioscan/contract.py
    impl="bioscan.plugins.identify.stage:STAGE",
)
