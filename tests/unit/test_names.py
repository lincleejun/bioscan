import csv
import json
import time

import numpy as np
import torch

from bioscan.service import names

AVI_HEADER = ["Sequence", "Taxon_rank", "Order", "Family", "Scientific_name", "English_name_AviList"]
AVI_ROWS = [
    [1, "order", "Passeriformes", "", "Passeriformes", ""],
    [2, "species", "Passeriformes", "Corvidae", "Corvus corax", "Northern Raven"],
    [3, "subspecies", "Passeriformes", "Corvidae", "Corvus corax principalis", ""],
    [4, "species", "Strigiformes", "Strigidae", "Megascops kennicottii", "Western Screech-Owl"],
]
MDD_HEADER = ["sciName", "mainCommonName", "order", "family", "genus", "specificEpithet"]
MDD_ROWS = [
    ["Rangifer_tarandus", "Reindeer", "Artiodactyla", "Cervidae", "Rangifer", "tarandus"],
    ["Alces_alces", "Moose", "Artiodactyla", "Cervidae", "Alces", "alces"],
]
TOL_NAMES = [
    [["Animalia", "Chordata", "Aves", "Passeriformes", "Corvidae", "Corvus", "corax"], "Common raven"],
    [["Animalia", "Chordata", "Mammalia", "Carnivora", "Ursidae", "Megascops", "kennicottii"], ""],  # wrong class
    [["Animalia", "Chordata", "Mammalia", "Artiodactyla", "Bovidae", "Rangifer", "tarandus"], ""],  # dup, wrong family
    [["Animalia", "Chordata", "Mammalia", "Artiodactyla", "Cervidae", "Rangifer", "tarandus"], "Reindeer"],
]


def write_csv(path, header, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows([header, *rows])


class FakeTokenizer:
    def __call__(self, texts):
        self.seen = getattr(self, "seen", []) + list(texts)
        return torch.tensor([[len(t), sum(map(ord, t))] for t in texts], dtype=torch.float32)


class FakeModel:
    calls = 0

    def encode_text(self, tokens):
        FakeModel.calls += 1
        g = torch.Generator().manual_seed(int(tokens[0, 1]))
        return torch.randn(tokens.shape[0], names.DIM, generator=g)


def setup(tmp_path):
    data = tmp_path / "data"
    write_csv(data / "avilist" / "avi.csv", AVI_HEADER, AVI_ROWS)
    write_csv(data / "mdd" / "mdd.csv", MDD_HEADER, MDD_ROWS)
    rng = np.random.default_rng(0)
    vecs = rng.normal(size=(names.DIM, len(TOL_NAMES))).astype(np.float32)  # TreeOfLife layout (dim, N)
    vecs /= np.linalg.norm(vecs, axis=0)
    (tmp_path / "tol.json").write_text(json.dumps(TOL_NAMES))
    np.save(tmp_path / "tol.npy", vecs)
    return data, (tmp_path / "tol.json", tmp_path / "tol.npy"), vecs


def test_tol_text_matches_treeoflife_format():
    tax = ["Animalia", "Chordata", "Aves", "Passeriformes", "Corvidae", "Corvus", "Corvus corax"]
    assert names.tol_text(tax, "Northern Raven") == \
        "an image of Animalia Chordata Aves Passeriformes Corvidae Corvus corax with common name Northern Raven."
    assert names.tol_text(tax, "") == "an image of Animalia Chordata Aves Passeriformes Corvidae Corvus corax."


def test_match_tol_class_and_family():
    birds = [("Corvus corax", "", ["", "", "Aves", "", "Corvidae", "", ""]),
             ("Megascops kennicottii", "", ["", "", "Aves", "", "Strigidae", "", ""])]
    assert names.match_tol(birds, TOL_NAMES, "Aves") == [0, None]  # homonym in another class ignored
    mammals = [("Rangifer tarandus", "", ["", "", "Mammalia", "", "Cervidae", "", ""])]
    assert names.match_tol(mammals, TOL_NAMES, "Mammalia") == [3]  # same family preferred over first hit


def test_load_lists_end_to_end_and_cache(tmp_path):
    data, tol, vecs = setup(tmp_path)
    tok, cache = FakeTokenizer(), tmp_path / "cache"
    lists = names.load_lists(FakeModel(), tok, "cpu", cache, data_dir=data, tol_files=tol)

    bird, mammal = lists["bird"], lists["mammal"]
    assert bird.list_id == "avilist-2025" and mammal.list_id == "mdd-2025"
    assert bird.scientific == ["Corvus corax", "Megascops kennicottii"]  # subspecies/order rows dropped
    assert bird.taxonomy[0] == ["Animalia", "Chordata", "Aves", "Passeriformes", "Corvidae", "Corvus", "Corvus corax"]
    assert mammal.scientific == ["Rangifer tarandus", "Alces alces"] and mammal.common == ["Reindeer", "Moose"]
    assert bird.matrix.shape == (2, names.DIM) and bird.matrix.dtype == np.float32
    np.testing.assert_allclose(np.linalg.norm(mammal.matrix, axis=1), 1, rtol=1e-5)
    np.testing.assert_allclose(bird.matrix[0], vecs[:, 0], rtol=1e-5)    # official vector taken as-is
    np.testing.assert_allclose(mammal.matrix[0], vecs[:, 3], rtol=1e-5)
    assert sorted(tok.seen) == sorted([
        "an image of Animalia Chordata Aves Strigiformes Strigidae Megascops kennicottii with common name Western Screech-Owl.",
        "an image of Animalia Chordata Mammalia Artiodactyla Cervidae Alces alces with common name Moose.",
    ])
    how = {"exact": 1, "synonym": 0, "none": 1}
    assert names.stats(lists) == {
        "bird": {"list_id": "avilist-2025", "total": 2, "official": 1, "encoded": 1, "coverage": 0.5,
                 "tol": how, "birdnet": None},
        "mammal": {"list_id": "mdd-2025", "total": 2, "official": 1, "encoded": 1, "coverage": 0.5,
                   "tol": how, "birdnet": None},
    }
    assert len(list(cache.glob(f"{names.MODEL_NAME}-*.npz"))) == 2

    # Second load: from cache, no model, no TreeOfLife files.
    calls = FakeModel.calls
    t0 = time.perf_counter()
    again = names.load_lists(None, None, "cpu", cache, data_dir=data, tol_files=(tmp_path / "gone", tmp_path / "gone"))
    assert time.perf_counter() - t0 < 3 and FakeModel.calls == calls
    assert again["bird"].taxonomy == bird.taxonomy and again["mammal"].common == mammal.common
    np.testing.assert_array_equal(again["mammal"].matrix, mammal.matrix)
    assert names.stats(again) == names.stats(lists)

    # Editing a list invalidates only that list's cache entry.
    write_csv(data / "mdd" / "mdd.csv", MDD_HEADER, MDD_ROWS[:1])
    third = names.load_lists(FakeModel(), FakeTokenizer(), "cpu", cache, data_dir=data, tol_files=tol)
    assert third["mammal"].scientific == ["Rangifer tarandus"]
    assert len(list(cache.glob("*.npz"))) == 3


SYN = [{"avilist_scientific": "Megascops kennicottii", "alias": "Megascops kennicotti", "source": "spelling", "note": ""},
       {"avilist_scientific": "Alces alces", "alias": "Alces americanus", "source": "tol", "note": ""},
       {"avilist_scientific": "Corvus corax", "alias": "Corvus sinuatus", "source": "inat", "note": ""}]


def test_match_names_exact_then_synonym_by_source():
    targets = {"corvus corax": "Corvus corax", "megascops kennicotti": "Megascops kennicotti",
               "corvus sinuatus": "Corvus sinuatus"}
    got = names.match_names(["Corvus  corax", "Megascops kennicottii", "Alces alces"], targets,
                            names.aliases(SYN, ("tol", "spelling")))
    assert got == [("Corvus corax", "exact"), ("Megascops kennicotti", "synonym"), ("", "none")]
    assert names.aliases(SYN, ("inat", "spelling")) == {"megascops kennicottii": ["Megascops kennicotti"],
                                                         "corvus corax": ["Corvus sinuatus"]}


def test_load_lists_uses_map_for_birds_and_synonyms_for_mammals(tmp_path):
    data, tol, vecs = setup(tmp_path)
    (data / "names").mkdir()
    write_csv(data / "names" / "synonyms.csv", list(SYN[0]), [list(r.values()) for r in SYN])
    # The map says kennicottii takes TreeOfLife's (wrong-class, so unusable) row and a BirdNET label.
    write_csv(data / "names" / "avilist_map.csv",
              ["scientific", "common", "order", "family", "tol_name", "tol_how", "birdnet_label", "birdnet_how"],
              [["Corvus corax", "Northern Raven", "Passeriformes", "Corvidae", "Corvus corax", "exact", "", "none"],
               ["Megascops kennicottii", "", "Strigiformes", "Strigidae", "", "none",
                "Megascops kennicottii_Western Screech Owl", "exact"]])
    # mammals: Alces alces reaches TreeOfLife's Alces americanus through a tol synonym
    tol_names = TOL_NAMES + [[["Animalia", "Chordata", "Mammalia", "Artiodactyla", "Cervidae", "Alces", "americanus"], "Moose"]]
    rng = np.random.default_rng(1)
    v = rng.normal(size=(names.DIM, len(tol_names))).astype(np.float32)
    v /= np.linalg.norm(v, axis=0)
    (tmp_path / "tol.json").write_text(json.dumps(tol_names))
    np.save(tmp_path / "tol.npy", v)

    lists = names.load_lists(FakeModel(), FakeTokenizer(), "cpu", tmp_path / "cache", data_dir=data, tol_files=tol)
    bird, mammal = lists["bird"], lists["mammal"]
    assert bird.tol_how == ["exact", "none"]
    assert bird.birdnet == ["", "Megascops kennicottii_Western Screech Owl"] and bird.birdnet_how == ["none", "exact"]
    assert mammal.tol_how == ["exact", "synonym"] and mammal.birdnet == []
    np.testing.assert_allclose(mammal.matrix[1], v[:, 4], rtol=1e-5)
    s = names.stats(lists)
    assert s["mammal"]["tol"] == {"exact": 1, "synonym": 1, "none": 0} and s["mammal"]["coverage"] == 1.0
    assert s["bird"]["birdnet"] == {"exact": 1, "synonym": 0, "none": 1}
    again = names.load_lists(None, None, "cpu", tmp_path / "cache", data_dir=data, tol_files=(tmp_path / "x", tmp_path / "x"))
    assert again["bird"].birdnet == bird.birdnet and again["mammal"].tol_how == mammal.tol_how


def write_map(path, rows):
    write_csv(path, ["scientific", "common", "order", "family", "tol_name", "tol_how", "birdnet_label", "birdnet_how"],
              rows)


def test_loaded_lists_are_finished_and_frozen(tmp_path):
    import dataclasses

    import pytest

    data, tol, _ = setup(tmp_path)
    write_map(data / "names" / "avilist_map.csv",
              [["Megascops kennicottii", "", "Strigiformes", "Strigidae", "", "none", "Megascops kennicottii_WSO", "exact"]])
    cache = tmp_path / "cache"
    fresh = names.load_lists(FakeModel(), FakeTokenizer(), "cpu", cache, data_dir=data, tol_files=tol)
    cached = names.load_lists(None, None, "cpu", cache, data_dir=data, tol_files=(tmp_path / "x", tmp_path / "x"))
    for kind, nl in fresh.items():
        assert (cache / f"{names.MODEL_NAME}-{nl.sha}.npz").is_file()           # sha is the cache key
        other = cached[kind]
        assert [nl.sha, nl.birdnet, nl.birdnet_how, nl.tol_how, nl.scientific] == \
            [other.sha, other.birdnet, other.birdnet_how, other.tol_how, other.scientific]
    # Corvus corax is not in the map: it gets no label rather than a missing row.
    assert fresh["bird"].birdnet == ["", "Megascops kennicottii_WSO"] and fresh["bird"].birdnet_how == ["none", "exact"]
    with pytest.raises(dataclasses.FrozenInstanceError):
        fresh["bird"].birdnet = []


def test_label_map_is_list_data_not_a_kind_check(tmp_path, monkeypatch):
    """Which list carries BirdNET labels is LISTS data: give the mammal list a map and take the birds' away."""
    data, tol, _ = setup(tmp_path)
    write_map(data / "names" / "avilist_map.csv",
              [["Corvus corax", "", "Passeriformes", "Corvidae", "", "none", "Corvus corax_Common Raven", "exact"]])
    write_map(data / "names" / "mdd_map.csv",
              [["Rangifer tarandus", "Reindeer", "Artiodactyla", "Cervidae", "Rangifer tarandus", "exact",
                "Rangifer tarandus_Reindeer", "exact"]])
    monkeypatch.setitem(names.LISTS, "bird", names.LISTS["bird"]._replace(label_map=None))
    monkeypatch.setitem(names.LISTS, "mammal", names.LISTS["mammal"]._replace(label_map="mdd_map.csv"))
    lists = names.load_lists(FakeModel(), FakeTokenizer(), "cpu", tmp_path / "cache", data_dir=data, tol_files=tol)
    assert lists["bird"].birdnet == [] and lists["bird"].tol_how == ["exact", "none"]   # matched here, map ignored
    assert lists["mammal"].birdnet == ["Rangifer tarandus_Reindeer", ""]
    assert lists["mammal"].birdnet_how == ["exact", "none"] and lists["mammal"].tol_how == ["exact", "none"]
    assert names.stats(lists)["mammal"]["birdnet"] == {"exact": 1, "synonym": 0, "none": 1}
    # The map is part of the key of the list that uses it, and only that one.
    write_map(data / "names" / "mdd_map.csv", [])
    again = names.load_lists(FakeModel(), FakeTokenizer(), "cpu", tmp_path / "cache", data_dir=data, tol_files=tol)
    assert again["mammal"].sha != lists["mammal"].sha and again["bird"].sha == lists["bird"].sha
    assert again["mammal"].birdnet == []


def test_build_name_map_small_sample():
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "build_name_map", Path(__file__).resolve().parents[2] / "scripts" / "build_name_map.py")
    bnm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bnm)

    tax = lambda g, e, f="Fam": ["Animalia", "Chordata", "Aves", "Ord", f, g, f"{g} {e}"]  # noqa: E731
    rows = [("Pica nuttallii", "Yellow-billed Magpie", tax("Pica", "nuttallii", "Corvidae")),
            ("Tyto furcata", "American Barn Owl", tax("Tyto", "furcata")),
            ("Tyto alba", "Western Barn Owl", tax("Tyto", "alba")),
            ("Gyps rueppelli", "", tax("Gyps", "rueppelli"))]
    tol_names = [[["Animalia", "Chordata", "Aves", "O", "Corvidae", "Pica", "nuttalli"], ""],
                 [["Animalia", "Chordata", "Aves", "O", "Tytonidae", "Tyto", "furcata"], ""],
                 [["Animalia", "Chordata", "Aves", "O", "Tytonidae", "Tyto", "alba"], ""],
                 [["Animalia", "Chordata", "Aves", "O", "Accipitridae", "Gyps", "rueppellii"], ""]]
    labels = ["Pica nuttalli_Yellow-billed Magpie", "Tyto alba_Barn Owl"]
    syn = [{"avilist_scientific": "Pica nuttallii", "alias": "Pica nuttalli", "source": "spelling"},
           {"avilist_scientific": "Tyto furcata", "alias": "Tyto alba", "source": "birdnet"}]
    mapped, cands = bnm.build(rows, tol_names, labels, syn)
    got = {m["scientific"]: (m["tol_name"], m["tol_how"], m["birdnet_label"], m["birdnet_how"]) for m in mapped}
    assert got == {
        "Pica nuttallii": ("Pica nuttalli", "synonym", "Pica nuttalli_Yellow-billed Magpie", "synonym"),
        "Tyto furcata": ("Tyto furcata", "exact", "Tyto alba_Barn Owl", "synonym"),
        "Tyto alba": ("Tyto alba", "exact", "Tyto alba_Barn Owl", "exact"),
        "Gyps rueppelli": ("", "none", "", "none"),                    # near miss is NOT adopted
    }
    assert mapped[0]["order"] == "Ord" and mapped[0]["family"] == "Corvidae"
    assert cands == [{"side": "tol", "scientific": "Gyps rueppelli", "candidate": "Gyps rueppellii",
                      "distance": 1, "candidate_in_avilist": False}]
    assert bnm.edit_distance("kitten", "sitting") == 3


def test_cache_rebuilt_when_model_revision_changes(tmp_path, monkeypatch):
    from bioscan.service.adapters import bioclip

    data, tol, _ = setup(tmp_path)
    cache = tmp_path / "cache"
    names.load_lists(FakeModel(), FakeTokenizer(), "cpu", cache, data_dir=data, tol_files=tol)
    with np.load(next(cache.glob("*.npz"))) as z:
        assert str(z["bioclip_revision"]) == bioclip.REVISION and str(z["tol_revision"]) == names.TOL_REVISION
    calls = FakeModel.calls
    names.load_lists(None, None, "cpu", cache, data_dir=data, tol_files=tol)          # same revisions: cached
    assert FakeModel.calls == calls
    monkeypatch.setattr(bioclip, "REVISION", "f" * 40)                                  # new weights pinned
    names.load_lists(FakeModel(), FakeTokenizer(), "cpu", cache, data_dir=data, tol_files=tol)
    assert FakeModel.calls > calls


def test_cache_from_before_revisions_were_recorded_is_used(tmp_path):
    data, tol, _ = setup(tmp_path)
    cache = tmp_path / "cache"
    names.load_lists(FakeModel(), FakeTokenizer(), "cpu", cache, data_dir=data, tol_files=tol)
    for p in cache.glob("*.npz"):                       # rewrite without the revision fields
        with np.load(p) as z:
            kept = {k: z[k] for k in z.files if not k.endswith("_revision")}
        with open(p, "wb") as f:
            np.savez(f, **kept)
    calls = FakeModel.calls
    again = names.load_lists(None, None, "cpu", cache, data_dir=data, tol_files=(tmp_path / "x", tmp_path / "x"))
    assert FakeModel.calls == calls and again["bird"].scientific == ["Corvus corax", "Megascops kennicottii"]


def test_labels_only_mdd_map_keeps_the_matrix_and_its_key(tmp_path, caplog):
    """mdd_map.csv carries BirdNET labels only: the mammal matrix, TreeOfLife matching and cache key
    stay what they were without it (no rebuild of an existing cache), and it is not checked
    against the bird synonyms."""
    data, tol, _ = setup(tmp_path)
    cache = tmp_path / "cache"
    before = names.load_lists(FakeModel(), FakeTokenizer(), "cpu", cache, data_dir=data, tol_files=tol)
    assert before["mammal"].birdnet == [] and before["mammal"].unlabelled == "genus"
    write_csv(data / "names" / "mdd_map.csv", ["scientific", "common", "order", "family", "birdnet_label", "birdnet_how"],
              [["Rangifer tarandus", "Reindeer", "Artiodactyla", "Cervidae", "Rangifer tarandus_Reindeer", "exact"],
               ["Alces alces", "Moose", "Artiodactyla", "Cervidae", "Alces alces_Moose|Alces americanus_Moose",
                "exact"]])
    calls = FakeModel.calls
    caplog.clear()
    after = names.load_lists(None, None, "cpu", cache, data_dir=data, tol_files=(tmp_path / "x", tmp_path / "x"))
    assert FakeModel.calls == calls and after["mammal"].sha == before["mammal"].sha     # cache hit
    np.testing.assert_array_equal(after["mammal"].matrix, before["mammal"].matrix)
    assert after["mammal"].tol_how == before["mammal"].tol_how
    assert after["mammal"].birdnet == ["Rangifer tarandus_Reindeer", "Alces alces_Moose|Alces americanus_Moose"]
    assert after["mammal"].unlabelled == "genus" and after["bird"].unlabelled == "zero"
    assert "stale name map" not in caplog.text
    assert after["bird"].sha == before["bird"].sha
    # provenance: the map's own sha travels with the list (engine info, settings fingerprint)
    assert before["mammal"].label_map_sha == "" and len(after["mammal"].label_map_sha) == 12
    assert after["mammal"].label_map_sha == names.label_map_sha(data / "names" / "mdd_map.csv")


def _build_name_map():
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "build_name_map", Path(__file__).resolve().parents[2] / "scripts" / "build_name_map.py")
    bnm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bnm)
    return bnm


def test_build_mdd_map_exact_synonym_lump_and_review():
    import pytest

    bnm = _build_name_map()
    tax = lambda g, e: ["Animalia", "Chordata", "Mammalia", "Primates", "Cebidae", g, f"{g} {e}"]  # noqa: E731
    rows = [("Cebus albifrons", "", tax("Cebus", "albifrons")), ("Cebus capucinus", "", tax("Cebus", "capucinus")),
            ("Bos bison", "", tax("Bos", "bison")), ("Rattus lutreola", "", tax("Rattus", "lutreola")),
            ("Rattus fuscipes", "", tax("Rattus", "fuscipes")), ("Lepus californicus", "", tax("Lepus", "californicus"))]
    syn = [{"MDD_species": "Cebus_albifrons", "MDD_original_combination": "Cebus yuracus", "MDD_root_name": "yuracus"},
           {"MDD_species": "Cebus_albifrons", "MDD_original_combination": "Cebus versicolor", "MDD_root_name": "versicolor"},
           {"MDD_species": "Cebus_capucinus", "MDD_original_combination": "Cebus imitator", "MDD_root_name": "imitator"},
           {"MDD_species": "Bos_bison", "MDD_original_combination": "Bison bison", "MDD_root_name": "bison"},
           {"MDD_species": "Rattus_lutreola", "MDD_original_combination": "Mus lutreolus", "MDD_root_name": "lutreolus"},
           {"MDD_species": "Rattus_fuscipes", "MDD_original_combination": "Rattus lutreolus", "MDD_root_name": "NA"}]
    index = bnm.mdd_synonym_index(syn)
    labels = ["Cebus yuracus_Y", "Cebus versicolor_V", "Cebus capucinus_C", "Cebus imitator_I", "Bison bison_B",
              "Rattus lutreolus_R"]
    mapped, report = bnm.build_mdd(rows, labels, index)
    got = {m["scientific"]: (m["birdnet_label"], m["birdnet_how"]) for m in mapped}
    assert got == {"Cebus albifrons": ("Cebus versicolor_V|Cebus yuracus_Y", "synonym"),      # lump, no own label
                   "Cebus capucinus": ("Cebus capucinus_C|Cebus imitator_I", "exact"),         # own label first
                   "Bos bison": ("Bison bison_B", "synonym"),                                  # genus moved
                   "Rattus lutreola": ("Rattus lutreolus_R", "synonym"),                       # reviewed
                   "Rattus fuscipes": ("", "none"), "Lepus californicus": ("", "none")}
    assert "lump: Cebus albifrons <- Cebus versicolor_V, Cebus yuracus_Y" in report
    with pytest.raises(SystemExit, match="REVIEWED"):
        bnm.build_mdd(rows, labels, index, reviewed={})      # lutreolus -> lutreola or fuscipes: a human decides


def test_build_name_map_refuses_to_overwrite_without_force(tmp_path, monkeypatch, capsys):
    """An existing map stops the run before any model loads (exit 2, file untouched); --force passes."""
    import pytest

    bnm = _build_name_map()
    target = tmp_path / "avilist_map.csv"
    target.write_text("committed\n")
    before = target.stat().st_mtime_ns
    monkeypatch.setattr(bnm, "OUT", tmp_path)
    monkeypatch.setattr(bnm, "main_birds", lambda a: pytest.fail("ran past the guard"))
    with pytest.raises(SystemExit) as e:
        bnm.main([])
    assert e.value.code == 2 and str(target) in capsys.readouterr().err
    assert target.read_text() == "committed\n" and target.stat().st_mtime_ns == before
    bnm.refuse_overwrite([target], force=True)                   # --force: the guard lets it through
    bnm.refuse_overwrite([tmp_path / "absent.csv"], force=False)  # nothing to overwrite
