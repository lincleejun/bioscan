"""Committed data files stay consistent with each other and with the code that reads them."""
import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NAMES = ROOT / "data" / "names"


def read(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_synonyms_well_formed():
    rows = read(NAMES / "synonyms.csv")
    assert rows
    for r in rows:
        assert r["source"] in ("tol", "birdnet", "inat", "spelling"), r
        assert r["note"].strip(), f"every synonym needs a note: {r}"
        assert len(r["avilist_scientific"].split()) == 2 and len(r["alias"].split()) == 2, r


def test_avilist_map_shape():
    rows = read(NAMES / "avilist_map.csv")
    assert len(rows) == 11131
    assert set(rows[0]) == {"scientific", "common", "order", "family", "tol_name", "tol_how", "birdnet_label", "birdnet_how"}
    assert len({r["scientific"] for r in rows}) == len(rows)
    for r in rows:
        assert r["tol_how"] in ("exact", "synonym", "none") and r["birdnet_how"] in ("exact", "synonym", "none")
        assert bool(r["birdnet_label"]) == (r["birdnet_how"] != "none")


def test_model_test_sample_is_covered_by_its_name_lists():
    sample = read(ROOT / "tests" / "models" / "sample.csv")
    mammals = {r["scientific"] for r in read(ROOT / "tests" / "models" / "mammals.csv")}
    avilist_genera = {r["scientific"].split()[0] for r in read(NAMES / "avilist_map.csv")}
    assert len(sample) == 95 and len({r["photo_id"] for r in sample}) == 95
    for r in sample:
        if r["kind"] == "mammal":
            assert r["scientific"] in mammals, r["scientific"]
        elif r["kind"] == "other_animal":   # ranked against the all-taxa list; photos are CC0 / CC BY only
            assert len(r["scientific"].split()) == 2 and r["license"] in ("cc0", "cc-by"), r
        else:
            assert r["kind"] == "bird" and r["scientific"].split()[0] in avilist_genera, r["scientific"]
        assert r["lat"] and r["lon"] and r["license"].startswith("cc")


def test_golden_set_rows():
    rows = read(ROOT / "data" / "inat" / "groundtruth-inat.csv")
    assert len(rows) == 1625 and {r["kind"] for r in rows} == {"bird", "mammal"}


def test_committed_map_reflects_synonyms():
    from bioscan import naming
    from bioscan.service import names

    assert naming.map_problems(names.read_map(naming.AVILIST_MAP_CSV), naming.read_synonyms()) == []
