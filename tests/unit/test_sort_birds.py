"""scripts/sort_birds.py: which photos move, and into which folder."""
from __future__ import annotations

import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("sort_birds", Path(__file__).resolve().parents[2] / "scripts" / "sort_birds.py")
sb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sb)


def box(taxon="Buteo jamaicensis", level="species", kind="bird", post=0.9, geo=0.5):
    return {"kind": kind, "species": {"level": level, "taxon": taxon if level == "species" else None,
            "top": [{"scientific": taxon, "common": "Red-tailed Hawk", "posterior": post, "p_geo": geo}]}}


def ev(boxes, gate="bird", person=0.0, reasons=(), blur=0.1):
    return {"type": "result", "products": {
        "identify": {"gate": {"class": gate, "probs": {"person": person}}, "boxes": boxes},
        "quality": {"reject_reasons": list(reasons), "subject": {"blur": blur}}}}


def test_decide():
    assert sb.decide(ev([box()])) == ("ok", "Buteo jamaicensis", "Red-tailed Hawk")
    assert sb.decide(ev([box()], person=0.3))[0] == "person"
    assert sb.decide(ev([box()], gate="mammal"))[0] == "not_bird"
    assert sb.decide(ev([]))[0] == "no_box"
    assert sb.decide(ev([box(), box(kind="mammal")]))[0] == "other_kind"
    assert sb.decide(ev([box(level="genus")]))[0] == "not_species"
    assert sb.decide(ev([box(), box(taxon="Cyanocitta stelleri")]))[0] == "mixed_species"
    assert sb.decide(ev([box(post=0.5)]))[0] == "low_posterior"
    assert sb.decide(ev([box(geo=0.01)]))[0] == "out_of_range"
    assert sb.decide(ev([box(geo=None)]))[0] == "out_of_range"
    assert sb.decide(ev([box()], reasons=["motion"]))[0] == "soft"
    assert sb.decide(ev([box()], blur=None))[0] == "soft"
    assert sb.decide({"type": "error"})[0] == "error"


def test_folder_name_reuses_existing():
    assert sb.folder_name("Red-tailed Hawk", ["Red-Tailed-Hawk", "x"]) == "Red-Tailed-Hawk"
    assert sb.folder_name("Steller's Jay", []) == "Steller's Jay"
