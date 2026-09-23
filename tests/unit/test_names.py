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


def test_norm_binomial():
    assert names.norm_binomial("Rangifer_tarandus") == "rangifer tarandus"
    assert names.norm_binomial("  Corvus   Corax ") == "corvus corax"


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
    assert names.stats(lists) == {
        "bird": {"list_id": "avilist-2025", "total": 2, "official": 1, "encoded": 1, "coverage": 0.5},
        "mammal": {"list_id": "mdd-2025", "total": 2, "official": 1, "encoded": 1, "coverage": 0.5},
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
