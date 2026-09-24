"""The all-taxa list (names.load_all_taxa) and the candidates option (candidates.py), on stand-ins:
the TreeOfLife files are small fakes in the documented row format, the models test_batch's."""
import dataclasses
import json
import logging

import numpy as np
import pytest
from test_batch import FRAMES, OPTS, Models, frame, g, names
from test_names import FakeModel, FakeTokenizer, setup

from bioscan.service import candidates, pipeline, products
from bioscan.service import names as names_mod

ANIMAL = ["Animalia", "Chordata"]
TOL = [
    [[*ANIMAL, "Reptilia", "Squamata", "Dactyloidae", "Anolis", "carolinensis"], ""],             # 0 dup, no common
    [[*ANIMAL, "Reptilia", "Squamata", "Dactyloidae", "Anolis", "carolinensis"], "Green anole"],  # 1 kept
    [[*ANIMAL, "Aves", "Strigiformes", "Strigidae", "Bubo", "virginianus"], "Great horned owl"],  # 2 curated class
    [[*ANIMAL, "Mammalia", "Carnivora", "Felidae", "Lynx", "rufus"], "Bobcat"],                   # 3 curated class
    [["Plantae", "Tracheophyta", "Magnoliopsida", "Rosales", "Rosaceae", "Rosa", "canina"], "Dog rose"],  # 4 plant
    [[*ANIMAL, "Reptilia", "Squamata", "Dactyloidae", "Anolis", ""], ""],                         # 5 genus row
    [[*ANIMAL, "Reptilia", "Testudines", "Emydidae", "Trachemys", "scripta elegans"], ""],        # 6 infraspecific
    [["Animalia", "Arthropoda", "Insecta", "Lepidoptera", "Nymphalidae", "Danaus", "plexippus"], "Monarch"],  # 7
    [[*ANIMAL, "Amphibia", None, "Salamandridae", "Taricha", "torosa"], None],                  # 8 null order, common
]
KEPT = [1, 7, 8]


def tol_files(tmp_path, rows=TOL, dim_major=True):
    rng = np.random.default_rng(3)
    vecs = rng.normal(size=(len(rows), names_mod.DIM)).astype(np.float32)
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
    (tmp_path / "tol.json").write_text(json.dumps(rows))
    np.save(tmp_path / "tol.npy", vecs.T.copy() if dim_major else vecs)    # TreeOfLife ships (dim, N)
    return (tmp_path / "tol.json", tmp_path / "tol.npy"), vecs


def test_rows_are_species_level_animals_outside_the_curated_classes():
    assert names_mod.ALL_TAXA.exclude == ("Aves", "Mammalia") and names_mod.ALL_TAXA.kingdoms == ("Animalia",)
    assert names_mod.all_taxa_rows(TOL) == KEPT


@pytest.mark.parametrize("dim_major", [True, False])
def test_load_all_taxa_builds_float16_and_caches(tmp_path, dim_major, monkeypatch):
    monkeypatch.setattr(names_mod, "_GATHER_BLOCK", 3)        # cross block boundaries on 1024 dims
    files, vecs = tol_files(tmp_path, dim_major=dim_major)
    cache = tmp_path / "cache"
    nl = names_mod.load_all_taxa(cache, tol_files=files)
    assert (nl.list_id, nl.kind, nl.source) == ("tol200m-animalia", "other_animal", names_mod.ALL_TAXA.source)
    assert nl.scientific == ["Anolis carolinensis", "Danaus plexippus", "Taricha torosa"]
    assert nl.common == ["Green anole", "Monarch", ""]
    assert nl.taxonomy[2][3] == "" and nl.taxonomy[2][6] == "Taricha torosa"
    assert nl.taxonomy[1] == ["Animalia", "Arthropoda", "Insecta", "Lepidoptera", "Nymphalidae", "Danaus",
                              "Danaus plexippus"]
    assert nl.matrix.dtype == np.float16 and nl.matrix.shape == (3, names_mod.DIM)
    np.testing.assert_allclose(nl.matrix.astype(np.float32), vecs[KEPT], atol=2e-3)   # official vectors, as is
    assert nl.tol_how == ["exact"] * 3 and nl.birdnet == []
    assert names_mod.all_taxa_path(cache) == cache / f"{names_mod.MODEL_NAME}-{nl.sha}.npz"
    again = names_mod.load_all_taxa(cache, tol_files=(tmp_path / "gone",) * 2)       # from the cache alone
    assert (again.scientific, again.taxonomy, again.common, again.sha) == (nl.scientific, nl.taxonomy, nl.common, nl.sha)
    np.testing.assert_array_equal(again.matrix, nl.matrix)
    with pytest.raises(dataclasses.FrozenInstanceError):
        again.kind = "bird"


def test_list_sha_follows_its_inputs(monkeypatch):
    sha = names_mod.all_taxa_sha()
    monkeypatch.setattr(names_mod, "TOL_REVISION", "0" * 40)
    assert names_mod.all_taxa_sha() != sha
    assert names_mod.all_taxa_sha(names_mod.ALL_TAXA._replace(kingdoms=("Animalia", "Plantae"))) != sha


def test_load_lists_adds_it_and_survives_without_it(tmp_path, caplog):
    data, _tol, _ = setup(tmp_path)
    files, _ = tol_files(tmp_path)                                  # birds and mammals match nothing here: encoded
    lists = names_mod.load_lists(FakeModel(), FakeTokenizer(), "cpu", tmp_path / "cache", data_dir=data, tol_files=files)
    assert set(lists) == {"bird", "mammal", "other_animal"}
    assert lists["bird"].source == "AviList v2025" and lists["mammal"].source == "MDD v2.5"
    assert names_mod.stats(lists)["other_animal"]["total"] == 3
    # bird and mammal caches built, TreeOfLife files deleted since, no all-taxa cache: birds and
    # mammals load as before, the others go without species
    names_mod.all_taxa_path(tmp_path / "cache").unlink()
    with caplog.at_level(logging.WARNING):
        fresh = names_mod.load_lists(None, None, "cpu", tmp_path / "cache", data_dir=data,
                                     tol_files=(tmp_path / "gone",) * 2)
    assert set(fresh) == {"bird", "mammal"} and "all-taxa list unavailable" in caplog.text


def test_engine_info_names_the_taxonomy():
    from bioscan.service.engine import Engine

    e = Engine("cpu")
    e.names = {"other_animal": dataclasses.replace(names("other_animal", "Reptilia", "Anolis"), source="TreeOfLife-200M @ x")}
    info = e.info()["models"]
    assert info["names"] == {"other_animal": "other_animal-list@"} and info["taxonomy"] == {"other_animal": "TreeOfLife-200M @ x"}


# ---- pipeline ------------------------------------------------------------------------------

OTHER = frame((90, 20, 3), g(other_animal=0.9))           # crop gate sees a non-bird animal: stays other_animal
C_OPTS = {**OPTS, "candidates": []}


def with_other(models=None, offset=0.0):
    m = models or Models()
    other = names("other_animal", "Reptilia", "Anolis")
    m.names["other_animal"] = dataclasses.replace(other, matrix=np.full((4, 4), offset, np.float32))
    return m


def test_other_animal_boxes_get_species_from_the_all_taxa_list():
    out = pipeline.identify_many(with_other(), [OTHER], OPTS)[0]
    assert out["boxes"] and all(b["kind"] == "other_animal" for b in out["boxes"])
    sp = out["boxes"][0]["species"]
    assert sp["list"] == "other_animal-list" and sp["top"][0]["scientific"].startswith("Anolis")
    assert sp["top"][0]["p_geo"] is None                           # no location prior for the list
    assert pipeline.identify_many(Models(), [OTHER], OPTS)[0]["boxes"][0]["species"] is None   # no list: null, as before


def top_names(out):
    return [[c["scientific"] for c in b["species"]["top"]] for b in out["boxes"]]


def test_candidates_restrict_ranking():
    base = pipeline.identify_many(Models(), FRAMES[:1], OPTS)[0]
    got = pipeline.identify_many(Models(), FRAMES[:1], {**OPTS, "candidates": ["Buteo s1", "buteo  S3"]})[0]
    assert all(set(t) == {"Buteo s1", "Buteo s3"} for t in top_names(got))
    for b0, b1 in zip(base["boxes"], got["boxes"]):
        assert sum(c["posterior"] for c in b1["species"]["top"]) == pytest.approx(1, abs=1e-5)
        assert b1["kind"] == b0["kind"] == "bird" and b1["species"]["list"] == "bird-list"
        # the order among the allowed rows is the unrestricted order
        allowed = [n for n in (c["scientific"] for c in b0["species"]["top"]) if n in ("Buteo s1", "Buteo s3")]
        assert [c["scientific"] for c in b1["species"]["top"]][:len(allowed)] == allowed


def test_a_higher_taxon_matches_every_row_under_it():
    base = pipeline.identify_many(Models(), FRAMES[:2], OPTS)
    for taxon in ("Buteo", "Aves", "aves"):
        got = pipeline.identify_many(Models(), FRAMES[:2], {**OPTS, "candidates": [taxon]})
        assert [top_names(o) for o in got] == [top_names(o) for o in base]        # the whole bird list: same ranking
        for o0, o1 in zip(base, got):
            for b0, b1 in zip(o0["boxes"], o1["boxes"]):
                for c0, c1 in zip(b0["species"]["top"], b1["species"]["top"]):
                    assert c1["posterior"] == pytest.approx(c0["posterior"], abs=2e-6)
                    assert c1["p_geo"] == c0["p_geo"]


def test_candidates_decide_the_list_and_the_kind_follows():
    out = pipeline.identify_many(Models(), FRAMES[:1], {**OPTS, "candidates": ["Lynx"]})[0]   # a bird-gated frame
    assert out["gate"]["class"] == "bird" and out["boxes"]
    for b in out["boxes"]:
        assert b["kind"] == "mammal" and b["species"]["list"] == "mammal-list"
        assert all(c["scientific"].startswith("Lynx") and c["p_geo"] is None for c in b["species"]["top"])
    # two lists at once share one softmax: the closer list (higher logits) takes the box
    m = with_other(offset=3.0)
    mixed = pipeline.identify_many(m, FRAMES[:1], {**OPTS, "candidates": ["Buteo s0", "Anolis", "Lynx s2"]})[0]
    for b in mixed["boxes"]:
        assert b["kind"] == "other_animal" and b["species"]["list"] == "other_animal-list"
        lists = {c["taxonomy"][2] for c in b["species"]["top"]}
        assert lists <= {"Reptilia", "Aves", "Mammalia"} and "Reptilia" in lists
        assert sum(c["p_visual"] for c in b["species"]["top"]) <= 1 + 1e-6


def test_candidates_with_a_prior_keep_each_lists_visual_share():
    """Bird rows get the location prior inside the birds' share; the mammal row's share is visual."""
    out = pipeline.identify_many(Models(), FRAMES[:1], {**OPTS, "candidates": ["Buteo s0", "Buteo s2", "Lynx s1"],
                                                        "top_k": 3})[0]
    for b in out["boxes"]:
        top = {c["scientific"]: c for c in b["species"]["top"]}
        birds = [top[n] for n in ("Buteo s0", "Buteo s2")]
        assert all(c["p_geo"] is not None for c in birds) and top["Lynx s1"]["p_geo"] is None
        assert sum(c["posterior"] for c in birds) == pytest.approx(sum(c["p_visual"] for c in birds), abs=1e-5)
        assert top["Lynx s1"]["posterior"] == top["Lynx s1"]["p_visual"]


def test_batched_equals_one_by_one_with_candidates(monkeypatch):
    monkeypatch.setattr(pipeline, "SPECIES_BATCH", 2)
    frames = [*FRAMES, OTHER]
    opts = {**OPTS, "candidates": ["Buteo s1", "Lynx", "Anolis"]}
    batched = pipeline.identify_many(with_other(), frames, opts)
    single = [pipeline.identify(with_other(), f.image, f.gate, f.lat, f.lon, f.taken_at, opts) for f in frames]
    assert batched == single
    assert {b["kind"] for o in batched for b in o["boxes"]} >= {"bird", "mammal"}
    off = pipeline.identify_many(with_other(), frames, {**opts, "species": False})
    assert all("species" not in b for o in off for b in o["boxes"])


# ---- validation -----------------------------------------------------------------------------

class Loaded:
    def __init__(self):
        self.names = with_other().names


def test_unknown_candidates_are_named():
    opts = products.resolve_options({"identify": {"candidates": ["Buteo s1", "Nonexistus", "Anolis s9", "Reptilia"]}})
    with pytest.raises(ValueError) as e:
        products.check_loaded(Loaded(), ["identify"], opts)
    assert "['Nonexistus', 'Anolis s9']" in str(e.value)
    products.check_loaded(Loaded(), ["identify"], products.resolve_options({"identify": {"candidates": ["O", "Lynx s0"]}}))
    products.check_loaded(Loaded(), ["embed"], opts)                     # identify not wanted: not checked
    assert candidates.unknown(Loaded().names, ["reptilia", "LYNX"]) == []


def test_products_describes_the_option():
    o = products.PRODUCTS["identify"]["options"]["candidates"]
    assert o["type"] == "array" and o["default"] == [] and "all taxa" in o["description"]


# ---- CLI --------------------------------------------------------------------------------------

def test_run_and_eval_pass_candidates(tmp_path, monkeypatch):
    from test_cli_eval import PREDS, _fake_service, _gt

    from bioscan.cli import eval as ev
    from bioscan.cli import main as cli

    (tmp_path / "a.jpg").write_bytes(b"")
    a = cli.parser().parse_args(["run", str(tmp_path), "--candidates", " Megascops kennicottii, Strigidae,,Bubo "])
    assert cli.build_payload(a)["options"]["identify"]["candidates"] == ["Megascops kennicottii", "Strigidae", "Bubo"]
    assert "candidates" not in cli.build_payload(cli.parser().parse_args(["run", str(tmp_path)]))["options"]["identify"]
    _fake_service(monkeypatch, PREDS)
    report, _ = ev.run_eval(_gt(tmp_path), str(tmp_path / "e"), False, "http://x", candidates=["Buteo", "Canis"])
    meta = json.loads((tmp_path / "e" / "preds.ndjson").read_text().splitlines()[0])
    assert meta["options"]["identify"]["candidates"] == ["Buteo", "Canis"] and "- candidates: Buteo, Canis" in report
    report, _ = ev.run_eval(_gt(tmp_path), str(tmp_path / "f"), False, "http://x")
    assert "- candidates: all taxa" in report
