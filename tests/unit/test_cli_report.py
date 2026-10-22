"""`bioscan summarize` / `bioscan report` (bioscan/cli/report.py): each review reason and the taxon keying on
hand-built events, a golden summary of the contract golden stream, and the page rendered from a summary.
Re-record the golden only on purpose: BIOSCAN_REGOLDEN=1 uv run pytest tests/unit/test_cli_report.py"""
import json
import os
from pathlib import Path

from bioscan import contract
from bioscan.cli import cull as cull_cli
from bioscan.cli import report as rp
from bioscan.cli.main import main

ROOT = Path(__file__).resolve().parents[2]
STREAM = ROOT / "tests" / "contract" / "golden" / "run-all-products.ndjson"
GOLDEN = Path(__file__).parent / "golden" / "summary-run-all-products.json"
TAX = {"Calidris mauri": ["Animalia", "Chordata", "Aves", "Charadriiformes", "Scolopacidae", "Calidris", "Calidris mauri"],
       "Calidris alpina": ["Animalia", "Chordata", "Aves", "Charadriiformes", "Scolopacidae", "Calidris",
                           "Calidris alpina"],
       "Canis latrans": ["Animalia", "Chordata", "Mammalia", "Carnivora", "Canidae", "Canis", "Canis latrans"]}


def cand(name, p, p_geo=None):
    return {"scientific": name, "common": name.upper(), "taxonomy": TAX[name], "p_visual": p, "p_geo": p_geo,
            "posterior": p}


def bx(i, kind="bird", level="species", top=None, sharp=1.0, species=True):
    b = {"id": i, "xyxy": [0, 0, 1, 1], "score": 0.9, "kind": kind, "quality": {"sharpness": sharp, "exposure": 0.0}}
    if species:
        b["species"] = None if top is None else {"list": "avilist-2025", "level": level, "top": top}
    return b


def ev(name, gate, boxes, t="12"):
    return {"type": "result", "path": f"/p/{name}.jpg", "sha256": name * 4, "engine": {"version": "v"},
            "products": {"identify": {"gate": {"class": gate, "probs": {}}, "boxes": boxes},
                         "quality": {"capture": {"taken_at": f"2026-09-13T08:00:{t}"}}}}


def events():
    mauri = [cand("Calidris mauri", 0.9), cand("Calidris alpina", 0.1)]
    return [
        ev("a", "bird", [bx(0, top=mauri, sharp=0.2), bx(1, top=mauri, sharp=0.9)], t="10"),   # 2 boxes: a taxon
        ev("b", "bird", [bx(0, level="genus", top=[cand("Calidris alpina", 0.4), cand("Calidris mauri", 0.3)])]),
        ev("c", "bird", [bx(0, level="unconfirmed", top=[cand("Calidris alpina", 0.3)])]),
        ev("d", "mammal", [bx(0, "mammal", top=[cand("Canis latrans", 0.7, p_geo=0.001)])]),       # single + range
        ev("e", "other_animal", [bx(0, "other_animal")]),                                          # no list
        ev("f", "mammal", []),                                                                     # gate, no box
        ev("g", "none", []), ev("h", "person", []),
        ev("i", "bird", [bx(0, species=False)]),                                                    # species off
        {"type": "error", "path": "/p/j.jpg", "product": None, "message": "broken"},
        {"type": "done", "ok": 9, "failed": 1, "elapsed_ms": 5.0}]


def test_review_reasons_and_taxon_keying():
    s = rp.summarize(events(), "preds.ndjson", "abc")
    taxa = {t["name"]: t for t in s["taxa"]}
    assert list(taxa) == ["Calidris mauri", "Calidris", "Canis latrans"]    # boxes desc, then name
    m = taxa["Calidris mauri"]
    assert (m["level"], m["boxes"], m["images"], m["common"]) == ("species", 2, 1, "CALIDRIS MAURI")
    assert m["best"]["box"] == 1 and len(m["members"]) == 2 and m["posterior"] == {"max": 0.9, "median": 0.9}
    assert taxa["Calidris"]["taxonomy"][-1] == "Calidris" and taxa["Calidris"]["common"] == "an alpina or mauri"
    got = {(r["path"][3], r["box"]): r["reasons"] for r in s["review"]}
    assert got == {("b", 0): ["coarse_level", "single_sighting"], ("c", 0): ["unconfirmed"],
                   ("d", 0): ["out_of_range", "single_sighting"], ("e", 0): ["no_list"], ("f", None): ["gate_no_box"]}
    assert [r["suggested"] for r in s["review"]] == ["Calidris", "Calidris alpina", "Canis latrans", None, None]
    assert s["review"][0]["top"][0]["scientific"] == "Calidris alpina"
    cats = {c["class"]: c for c in s["categories"]}
    assert [c["class"] for c in s["categories"]] == list(rp.CLASSES)
    assert cats["bird"] == {"class": "bird", "images": 4, "boxes": 5, "taxa": 2, "review": 2}
    assert cats["mammal"] == {"class": "mammal", "images": 2, "boxes": 1, "taxa": 1, "review": 2}
    assert cats["none"] == {"class": "none", "images": 1} and cats["person"] == {"class": "person", "images": 1}
    assert s["counts"] == {"images": 10, "ok": 9, "failed": 1, "boxes": 7, "elapsed_ms": 5.0}
    assert s["errors"] == [{"path": "/p/j.jpg", "product": None, "message": "broken"}]
    assert s["source"]["first_taken_at"] == "2026-09-13T08:00:10"
    assert rp.line(s) == "10 photos · 7 with animals · 3 taxa · 5 to review"


def test_range_eps_matches_the_service_rule():
    from bioscan.service import rules

    assert rp.RANGE_EPS == rules.RANGE_EPS


def test_golden_summary_of_the_contract_stream():
    _, evs = cull_cli.read_ndjson(str(STREAM))
    got = rp.summarize(evs, STREAM.name, "sha")
    if os.environ.get("BIOSCAN_REGOLDEN"):
        GOLDEN.write_text(json.dumps(got, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    assert got == json.loads(GOLDEN.read_text(encoding="utf-8")), "summary differs from its golden"


def test_summarize_then_report_on_the_command_line(tmp_path, capsys):
    preds = tmp_path / "preds.ndjson"
    preds.write_text("".join(json.dumps(e) + "\n" for e in events()))
    assert main(["summarize", str(preds), "--out", str(tmp_path / "run")]) == 0
    assert "5 to review" in capsys.readouterr().out
    assert main(["report", str(tmp_path / "run")]) == 0
    page = (tmp_path / "run" / "report.html").read_text()
    s = json.loads((tmp_path / "run" / "summary.json").read_text())
    for t in s["taxa"]:
        assert f"<i>{t['name']}</i>" in page
    for r in s["review"]:
        assert f'title="{r["path"]}"' in page
    assert page.count("<figure") == len(s["taxa"]) + len(s["review"])
    assert all(v in page for v in rp.REASONS.values()) and "broken" in page


def _c(sci, common, family="Accipitridae"):
    genus = sci.split()[0]
    return {"scientific": sci, "common": common, "p_visual": 0.2, "p_geo": None, "posterior": 0.2,
            "taxonomy": ["Animalia", "Chordata", "Aves", "Accipitriformes", family, genus, sci]}


def _sp(level, top):
    return {"list": "avilist-2025", "level": level, "top": top}


def test_common_of_species_is_the_first_candidates_common_name():
    assert rp.common_of(_sp("species", [_c("Spinus psaltria", "Lesser Goldfinch", "Fringillidae")])) == "Lesser Goldfinch"


def test_common_of_genus_is_the_shared_last_word_with_an_article():
    vireos = [_c("Vireo gilvus", "Warbling Vireo", "Vireonidae"), _c("Vireo olivaceus", "Red-eyed Vireo", "Vireonidae")]
    assert rp.common_of(_sp("genus", vireos)) == "a vireo"
    eagles = [_c("Aquila chrysaetos", "Golden Eagle"), _c("Aquila heliaca", "Eastern Imperial Eagle")]
    assert rp.common_of(_sp("genus", eagles)) == "an eagle"
    flys = [_c("Empidonax difficilis", "Pacific-slope Flycatcher", "Tyrannidae"),
            _c("Empidonax traillii", "Willow Flycatcher", "Tyrannidae")]
    assert rp.common_of(_sp("genus", flys)) == "a flycatcher"


def test_common_of_family_names_the_two_commonest_words_and_ignores_other_taxa():
    top = [_c("Buteo jamaicensis", "Red-tailed Hawk"), _c("Aquila chrysaetos", "Golden Eagle"),
           _c("Haliaeetus leucocephalus", "Bald Eagle"), _c("Accipiter cooperii", "Cooper's Hawk"),
           _c("Pandion haliaetus", "Osprey", "Pandionidae"), _c("Cathartes aura", "Turkey Vulture", "Cathartidae")]
    assert rp.common_of(_sp("family", top)) == "a hawk or eagle"
    one_off = [_c("Buteo jamaicensis", "Red-tailed Hawk"), _c("Buteo lineatus", "Red-shouldered Hawk"),
               _c("Aquila chrysaetos", "Golden Eagle")]
    assert rp.common_of(_sp("family", one_off)) == "a hawk"        # a lone second word is left out
    # genus level: only the candidates of the box's genus count (Buteo), not the eagle below it
    assert rp.common_of(_sp("genus", one_off)) == "a hawk"


def test_common_of_without_names_or_level_is_none():
    assert rp.common_of(_sp("genus", [_c("Vireo gilvus", "", "Vireonidae")])) is None
    assert rp.common_of(_sp("unconfirmed", [_c("Vireo gilvus", "Warbling Vireo", "Vireonidae")])) is None
    assert rp.common_of(None) is None


def test_review_row_and_heading_carry_the_english_name(tmp_path):
    vireos = [_c("Vireo gilvus", "Warbling Vireo", "Vireonidae"), _c("Vireo olivaceus", "Red-eyed Vireo", "Vireonidae")]
    e = ev("v", "bird", [bx(0, level="genus", top=vireos)])
    s = rp.summarize([e])
    assert [(r["suggested"], r["common"]) for r in s["review"]] == [("Vireo", "a vireo")]
    rp.render(s, str(tmp_path / "report.html"))
    page = (tmp_path / "report.html").read_text()
    assert ('<h3><span class="common">a vireo</span> <i>Vireo</i> <span class="muted">(genus)</span>'
            ' <span class="muted">1</span></h3>') in page


def _t(name, fam, p):
    return {"scientific": name, "common": None, "taxonomy": ["Animalia", "Chordata", "Aves", "O", fam,
                                                          name.split()[0], name], "posterior": p}


def _rolled(i, level, name, top):
    b = bx(i, level=level, top=top)
    b["species"]["taxon"] = name
    return b


def test_a_family_box_reads_its_taxon_mass_everywhere():
    """#73: first candidate 0.30 (Fringillidae), Accipitridae holds 0.66: summary, `named` and the terminal
    line all read 0.66, so it beats a family box whose first candidate is higher (0.45) but mass lower (0.62)."""
    from bioscan.cli import aesscore, render

    box = _rolled(0, "family", "Accipitridae", [
        _t("Fringilla a", "Fringillidae", 0.30), _t("Buteo b", "Accipitridae", 0.25),
        _t("Aquila c", "Accipitridae", 0.20), _t("Accipiter d", "Accipitridae", 0.15),
        _t("Haliaeetus e", "Accipitridae", 0.06)])
    gulls = _rolled(1, "family", "Laridae", [
        _t("Larus a", "Laridae", 0.45), _t("Corvus b", "Corvidae", 0.30), _t("Sterna c", "Laridae", 0.17)])
    e = ev("ab", "bird", [gulls, box])
    assert abs(contract.mass(box["species"]) - 0.66) < 1e-9
    taxa = {t["name"]: t for t in rp.summarize([e])["taxa"]}
    assert abs(taxa["Accipitridae"]["posterior"]["max"] - 0.66) < 1e-9
    assert render.box_label(1, box).startswith("[1] Accipitridae 0.66")
    assert aesscore.named(e)[0] == "Accipitridae"


def test_named_prefers_a_species_box_to_a_rolled_up_one():
    """A genus mass sums candidates, so it is not comparable with a species posterior: the service grading
    a box species is the verdict. American Crow 0.977 beats a Corvus genus box at 0.98."""
    from bioscan.cli import aesscore

    crow = bx(0, top=[_t("Corvus brachyrhynchos", "Corvidae", 0.977)])
    genus = _rolled(1, "genus", "Corvus", [_t("Corvus corax", "Corvidae", 0.6), _t("Corvus brachyrhynchos", "Corvidae", 0.38)])
    assert aesscore.named(ev("crow", "bird", [genus, crow]))[:3] == ("Corvus brachyrhynchos", None, "species")
