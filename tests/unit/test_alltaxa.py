"""The all-taxa list (names.load_all_taxa) and the candidates option (candidates.py), on stand-ins:
the TreeOfLife files are small fakes in the documented row format; the models are test_batch's
(extended to a 12-d space holding an all-taxa list) and test_accuracy's."""
import dataclasses
import json
import logging

import numpy as np
import pytest
from test_accuracy import Engine3, species, unit
from test_batch import OPTS, SWITCHES_OFF, Models, frame, g
from test_names import FakeModel, FakeTokenizer, setup

from bioscan import plugin
from bioscan.service import candidates, pipeline, stages
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


# Rows shaped the way a backbone without Reptilia (or with gaps) may give them: the smoke set's six
# reptiles and its fish were missing from the first real build (CI, 365,973 species).
ODD = [
    [["Animalia", "Chordata", "", "Squamata", "Viperidae", "Crotalus", "oreganus"], "Western rattlesnake"],   # 0
    [["Animalia", "Chordata", None, "Testudines", "Emydidae", "Trachemys", "scripta"], "Pond slider"],       # 1
    [["Animalia", "Chordata", "Squamata", "", "Colubridae", "Thamnophis", "sirtalis"], ""],                  # 2 order as class
    [["", "Chordata", "", "Perciformes", "Pomacentridae", "Hypsypops", "rubicundus"], "Garibaldi"],          # 3 no kingdom
    [["Metazoa", "Chordata", "", "Squamata", "Anguidae", "Elgaria", "multicarinata"], ""],                  # 4 Metazoa
    [["animalia ", "Chordata", "", "Squamata", "Phrynosomatidae", "Sceloporus", "Sceloporus occidentalis"], ""],  # 5
    [["Animalia", "Chordata", "", "Passeriformes", "Corvidae", "Corvus", "corax"], "Common raven"],          # 6 bird, no class
    [["Animalia", "Chordata", "Aves", "Passeriformes", "Corvidae", "Pica", "pica"], ""],                    # 7 curated class
    [["Animalia", "Chordata", "MAMMALIA", "Carnivora", "Felidae", "Lynx", "rufus"], ""],                    # 8 curated, any case
    [["", "Tracheophyta", "", "Rosales", "Rosaceae", "Rosa", "canina"], ""],                                # 9 no kingdom, plant
    [["Fungi", "Basidiomycota", "", "Agaricales", "Amanitaceae", "Amanita", "muscaria"], ""],               # 10 fungus
    [["Animalia", "Chordata", "", "Squamata", "Viperidae", "Crotalus", ""], ""],                            # 11 genus row
    [["Animalia", "Chordata", "", "Squamata", "Colubridae", "", "catenifer"], ""],                          # 12 no genus
    [["Animalia", "Chordata", "", "Squamata", "Colubridae", "Pituophis", "catenifer sayi"], ""],            # 13 subspecies
    [["Animalia", "Chordata", "Reptilia", "Squamata"], ""],                                                 # 14 short
]


def test_filter_keeps_classless_reptiles_and_fish_and_drops_the_rest():
    reasons: dict[str, int] = {}
    got = names_mod.all_taxa_select(ODD, reasons=reasons)
    assert list(got) == [0, 1, 2, 3, 4, 5]
    assert got[0] == ["Animalia", "Chordata", "", "Squamata", "Viperidae", "Crotalus", "Crotalus oreganus"]
    assert got[3][0] == "Animalia" and got[3][6] == "Hypsypops rubicundus"        # empty kingdom, animal phylum
    assert got[4][0] == "Metazoa" and got[5][6] == "Sceloporus occidentalis"      # genus repeated in the epithet
    assert reasons == {"no class, curated order": 1, "curated class (Aves/Mammalia)": 2,
                       "no kingdom, phylum not animal": 1, "not an animal": 1, "no epithet (higher rank)": 1,
                       "no genus": 1, "infraspecific or unparsed epithet": 1, "not 7 ranks": 1}
    assert sum(reasons.values()) + len(got) == len(ODD)
    # the candidates index finds them by order and by the kingdom stored for an empty one
    nl = names_mod.NameList("x", "other_animal", [t[6] for t in got.values()], [""] * 6, list(got.values()),
                            np.zeros((6, 2), np.float32), ["exact"] * 6)
    assert candidates.allowed({"other_animal": nl}, ["Squamata"])["other_animal"].tolist() == [0, 2, 4, 5]
    assert candidates.allowed({"other_animal": nl}, ["Animalia"])["other_animal"].tolist() == [0, 1, 2, 3, 5]


def test_census_explains_the_build():
    c = names_mod.all_taxa_census(ODD, species=["Crotalus oreganus", "Pituophis catenifer", "Corvus corax", "Nope nope"])
    assert c["rows"] == len(ODD) and c["kept"] == 6 and c["dropped"]["no genus"] == 1
    assert dict(c["by_kingdom_class"])[("Animalia", "(empty)")] == 6
    assert c["species"]["Crotalus oreganus"]["rows"][0]["kept"][6] == "Crotalus oreganus"
    assert c["species"]["Crotalus oreganus"]["genus_rows"] == 2
    assert [r["kept"] for r in c["species"]["Pituophis catenifer"]["rows"]] == [None]    # the subspecies row only
    assert c["species"]["Corvus corax"]["rows"][0]["kept"] is None
    assert c["species"]["Nope nope"] == {"rows": [], "genus_rows": 0, "genus_example": None}


def test_filter_change_moves_the_list_sha(monkeypatch):
    sha = names_mod.all_taxa_sha()
    monkeypatch.setattr(names_mod, "ALL_TAXA_VERSION", "1")                         # the filter before the fix
    assert names_mod.all_taxa_sha() != sha


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
    e.names = {"other_animal": dataclasses.replace(Models12().names["other_animal"], source="TreeOfLife-200M @ x")}
    info = e.info()["models"]
    assert info["names"] == {"other_animal": "other_animal-list@"} and info["taxonomy"] == {"other_animal": "TreeOfLife-200M @ x"}


# ---- pipeline ------------------------------------------------------------------------------

D12 = 12


class Models12(Models):
    """test_batch's Models in a 12-d space: birds e0-e3 (Buteo), mammals e4-e7 (Lynx) and, with
    `other`, an all-taxa list e8-e11 (Anolis). A crop of colour (r, g, b) leans to row b % 12 and a
    little to row r % 12; the crop gate says bird when g > 100 (test_batch)."""

    def __init__(self, other=True):
        super().__init__()
        eye = np.eye(D12, dtype=np.float32)
        self.names = {k: dataclasses.replace(nl, matrix=eye[first:first + 4])
                      for (k, nl), first in zip(self.names.items(), (0, 4))}
        if other:
            sci = [f"Anolis s{i}" for i in range(4)]
            self.names["other_animal"] = names_mod.NameList(
                "other_animal-list", "other_animal", sci, [""] * 4,
                [["Animalia", "Chordata", "Reptilia", "Squamata", "Dactyloidae", "Anolis", x] for x in sci],
                eye[8:12], ["exact"] * 4)

        class Bio:
            @staticmethod
            def _vecs(feats):
                vecs = np.zeros((len(feats), D12))
                for i, (r, _g, b) in enumerate(feats):
                    vecs[i, b % D12] += 1.0
                    vecs[i, r % D12] += 0.6
                return vecs

            def encode_images(self, crops):
                return [c.getpixel((c.width // 2, c.height // 2)) for c in crops]

            def logits(self, feats, matrix):
                return 4.0 * self._vecs(feats) @ np.asarray(matrix, dtype=np.float64).T

            def probs(self, feats, matrix):
                z = np.exp(self.logits(feats, matrix))
                return z / z.sum(axis=1, keepdims=True)

        self.bioclip = Bio()


BIRDLIKE = frame((204, 150, 1), g(bird=0.9), lat=37.0)       # Buteo s1, a little Buteo s0
REPTILE_GATED_BIRD = frame((200, 150, 9), g(bird=0.9))       # a bird box whose evidence is Anolis
BIRD_GATED_OTHER = frame((204, 20, 2), g(other_animal=0.9))  # an other_animal box whose evidence is Buteo
MAMMALIAN = frame((208, 20, 5), g(mammal=0.9))               # Lynx s1, a little Lynx s0
OTHER = frame((200, 20, 9), g(other_animal=0.9))             # an other_animal box whose evidence is Anolis
FRAMES12 = [BIRDLIKE, REPTILE_GATED_BIRD, BIRD_GATED_OTHER, MAMMALIAN, OTHER]


def run(models, f, **opts):
    return pipeline.identify_many(models, [f], {**OPTS, **opts})[0]["boxes"]


def kinds(models, f, **opts):
    return {b["kind"] for b in run(models, f, **opts)}


def test_other_animal_boxes_get_species_from_the_all_taxa_list():
    boxes = run(Models12(), BIRD_GATED_OTHER, **SWITCHES_OFF)
    assert boxes and all(b["kind"] == "other_animal" for b in boxes)
    sp = boxes[0]["species"]
    assert sp["list"] == "other_animal-list" and sp["top"][0]["scientific"].startswith("Anolis")
    assert sp["top"][0]["p_geo"] is None                            # no location prior for the list
    assert all(b["species"] is None for b in run(Models12(other=False), BIRD_GATED_OTHER, **SWITCHES_OFF))


def test_the_all_taxa_list_joins_the_kind_check_one_way():
    """other_animal boxes may move to bird or mammal; bird and mammal boxes never move to other_animal
    (the all-taxa list's size would win it every such box: taxa.ONE_WAY)."""
    m = Models12()
    assert kinds(m, BIRDLIKE) == {"bird"}
    assert kinds(m, BIRD_GATED_OTHER) == {"bird"}                    # other_animal-gated, strong bird evidence
    assert kinds(m, OTHER) == {"other_animal"}
    for b in run(m, BIRD_GATED_OTHER):
        assert b["species"]["list"] == "bird-list" and b["species"]["top"][0]["scientific"] == "Buteo s2"
    assert kinds(m, REPTILE_GATED_BIRD) <= {"bird", "mammal"}        # reptile evidence, but never other_animal
    assert kinds(m, BIRD_GATED_OTHER, kind_check=False) == {"other_animal"}      # behind the switch
    # not loaded: W3's bird <-> mammal check as it was, other animals left alone
    plain = Models12(other=False)
    assert run(plain, REPTILE_GATED_BIRD) == run(m, REPTILE_GATED_BIRD)
    assert all(b["kind"] == "other_animal" and b["species"] is None for b in run(plain, BIRD_GATED_OTHER))


def test_a_huge_all_taxa_list_never_takes_a_bird_box():
    """Pad the all-taxa list with 20,000 rows scattered around a bird crop's feature: each looks a bit
    like it, so that list's best five beat the four-row bird list's. The bird box stays a bird."""
    m = Models12()
    rng = np.random.default_rng(1)
    feature = m.bioclip._vecs([(204, 150, 1)])[0]
    pad = feature / np.linalg.norm(feature) * 0.9 + rng.normal(scale=0.05, size=(20000, D12))
    other = m.names["other_animal"]
    m.names["other_animal"] = dataclasses.replace(
        other, matrix=np.vstack([other.matrix, pad]).astype(np.float32),
        scientific=other.scientific + [f"Padus s{i}" for i in range(20000)],
        common=other.common + [""] * 20000, tol_how=other.tol_how + ["exact"] * 20000,
        taxonomy=other.taxonomy + [["Animalia", "Arthropoda", "Insecta", "O", "F", "Padus", f"Padus s{i}"]
                                   for i in range(20000)])
    from bioscan.service import rules

    z = {k: m.bioclip.logits([(204, 150, 1)], nl.matrix)[0] for k, nl in m.names.items()}
    assert rules.kind_of(rules.kind_evidence_logits(z)) == ("other_animal", True)   # two-way, it would win
    assert kinds(m, BIRDLIKE) == {"bird"}
    for b in run(m, BIRDLIKE):
        assert b["species"]["list"] == "bird-list" and b["species"]["level"] != "unconfirmed"


def test_chance_top_is_the_expected_best_of_n_normals():
    from bioscan.service import rules

    assert rules.chance_top(5) == pytest.approx((-1.16, -0.50, 0.0, 0.50, 1.16), abs=0.03)   # exact: +-1.163, +-0.495
    assert rules.chance_top(11131)[-1] == pytest.approx(3.86, abs=0.02)        # AviList
    assert rules.chance_top(366460)[-1] == pytest.approx(4.64, abs=0.02)       # all-taxa
    assert rules.chance_top(1) == (0.0,)


def test_size_corrected_kind_evidence_takes_the_list_size_out():
    """Synthetic logits (spread 3, BioCLIP-like): a 1,000-row bird list against a 20,000-row all-taxa
    list of pure noise. Uncorrected, the longer list wins nearly every draw by size alone; corrected,
    about half, rarely sure. With one real bird match 4.2 sd up, correction turns most such boxes back
    to bird."""
    from bioscan.service import rules

    rng = np.random.default_rng(0)
    draws = [{"bird": rng.normal(size=1000) * 3, "other_animal": rng.normal(size=20000) * 3} for _ in range(200)]
    raw = [rules.kind_of(rules.kind_evidence_logits(z)) for z in draws]
    fair = [rules.kind_of(rules.kind_evidence_logits(z, size_correct=True)) for z in draws]
    assert sum(k == "other_animal" for k, _ in raw) > 190
    assert 70 < sum(k == "other_animal" for k, _ in fair) < 130
    assert sum(sure for _, sure in fair) < 50 < sum(sure for _, sure in raw)
    matched = []
    for _ in range(200):
        bird = rng.normal(size=1000) * 3
        bird[0] = 3 * 4.2
        matched.append({"bird": bird, "other_animal": rng.normal(size=20000) * 3})
    assert sum(rules.kind_of(rules.kind_evidence_logits(z))[0] == "bird" for z in matched) < 100
    assert sum(rules.kind_of(rules.kind_evidence_logits(z, size_correct=True))[0] == "bird" for z in matched) > 150


def test_size_corrected_kind_check_against_a_padded_20k_all_taxa_list():
    """The padded case on synthetic logits: a bird box's logits over an 11-row bird list with a clear
    match and a 20,000-row all-taxa list of noise at the same spread. Uncorrected it goes to the
    all-taxa list, sure of it; corrected it stays a bird, sure of it. Off by default."""
    from bioscan.service import rules

    rng = np.random.default_rng(7)
    bird = rng.normal(size=11) * 3
    bird[3] = 3 * 3.5
    z = {"bird": bird, "other_animal": rng.normal(size=20000) * 3}
    assert rules.kind_of(rules.kind_evidence_logits(z)) == ("other_animal", True)
    assert rules.kind_of(rules.kind_evidence_logits(z, size_correct=True)) == ("bird", True)
    assert rules.kind_evidence_logits(z) == rules.kind_evidence_logits(z, size_correct=False)


def test_the_size_correct_trial_is_an_identify_option_off_by_default():
    from bioscan.plugins.identify import MANIFEST, TRIALS

    assert TRIALS == {"kind_size_correct": False}
    assert MANIFEST.options["kind_size_correct"] == {"type": "boolean", "default": False}
    m = Models12()
    for spec, want in ((BIRDLIKE, {"bird"}), (BIRD_GATED_OTHER, {"bird"}), (OTHER, {"other_animal"})):
        assert kinds(m, spec, kind_size_correct=True) == want


def test_kind_check_evidence_per_list_equals_the_stacked_lists():
    from bioscan.service import rules

    rng = np.random.default_rng(5)
    lists = {"bird": rng.normal(size=40) * 3, "mammal": rng.normal(size=7) * 3, "other_animal": rng.normal(size=900) * 3}
    joint = np.concatenate(list(lists.values()))
    probs = np.exp(joint - joint.max())
    probs /= probs.sum()
    ends = np.cumsum([len(v) for v in lists.values()])
    rows_of = {k: slice(e - len(v), e) for (k, v), e in zip(lists.items(), ends)}
    assert rules.kind_evidence_logits(lists) == pytest.approx(rules.kind_evidence(probs, rows_of), rel=1e-9)


def top_names(boxes):
    return [[c["scientific"] for c in b["species"]["top"]] for b in boxes]


def test_candidates_restrict_ranking():
    boxes = run(Models12(), BIRDLIKE, candidates=["Buteo s1", "buteo  S3"])
    assert boxes and all(set(t) == {"Buteo s1", "Buteo s3"} for t in top_names(boxes))
    for b in boxes:
        assert b["kind"] == "bird" and b["species"]["list"] == "bird-list"
        assert sum(c["posterior"] for c in b["species"]["top"]) == pytest.approx(1, abs=1e-5)
        assert b["species"]["top"][0]["scientific"] == "Buteo s1"


def test_a_higher_taxon_matches_every_row_under_it():
    base = run(Models12(), BIRDLIKE)
    for taxon in ("Buteo", "Aves", "aves"):
        got = run(Models12(), BIRDLIKE, candidates=[taxon])
        assert top_names(got) == top_names(base)                      # the whole bird list: same ranking
        for b0, b1 in zip(base, got):
            assert b1["species"]["level"] == b0["species"]["level"]
            for c0, c1 in zip(b0["species"]["top"], b1["species"]["top"]):
                assert c1["posterior"] == pytest.approx(c0["posterior"], abs=2e-6)
                assert c1["p_geo"] == c0["p_geo"]


def test_candidates_decide_the_list_and_the_kind_follows():
    for check in (True, False):
        boxes = run(Models12(), BIRDLIKE, candidates=["Lynx"], kind_check=check)   # a bird box, mammal candidates
        assert boxes and all(b["kind"] == "mammal" and b["species"]["list"] == "mammal-list" for b in boxes)
        assert all(c["scientific"].startswith("Lynx") for b in boxes for c in b["species"]["top"])


def test_with_candidates_the_kind_check_compares_only_kinds_that_keep_rows():
    m = Models12()
    assert kinds(m, REPTILE_GATED_BIRD, candidates=["Buteo", "Lynx"]) <= {"bird", "mammal"}
    assert "mammal" not in kinds(m, MAMMALIAN, candidates=["Buteo", "Anolis"])
    # a bird box with bird and all-taxa candidates competes among birds only, on or off
    for check in (True, False):
        assert kinds(m, REPTILE_GATED_BIRD, candidates=["Buteo s0", "Anolis"], kind_check=check) == {"bird"}
    # ... and with mammal and all-taxa candidates, among mammals only
    assert kinds(m, REPTILE_GATED_BIRD, candidates=["Lynx s0", "Anolis"]) == {"mammal"}
    # no bird or mammal candidate: the all-taxa list is its only option
    for check in (True, False):
        boxes = run(m, REPTILE_GATED_BIRD, candidates=["Anolis"], kind_check=check)
        assert boxes and all(b["kind"] == "other_animal" and b["species"]["list"] == "other_animal-list"
                             for b in boxes)
    # an other_animal box competes with every list that keeps a row, and may still move to bird
    assert kinds(m, BIRD_GATED_OTHER, candidates=["Buteo", "Anolis"]) == {"bird"}
    assert kinds(m, BIRD_GATED_OTHER, candidates=["Buteo", "Anolis"], kind_check=False) == {"other_animal"}
    assert kinds(m, OTHER, candidates=["Buteo", "Anolis"]) == {"other_animal"}


def test_candidates_use_the_lists_location_prior():
    m = Models12()
    boxes = run(m, BIRDLIKE, candidates=["Buteo s0", "Buteo s2"], range_veto=False)
    prior = m.priors["bird"]
    g_all = prior.p_geo(BIRDLIKE.lat, BIRDLIKE.lon, BIRDLIKE.taken_at)
    z = m.bioclip.logits([(204, 150, 1)], m.names["bird"].matrix)[0][[0, 2]]
    p = np.exp(z - z.max()) / np.exp(z - z.max()).sum()
    want = prior.posterior(p, g_all[[0, 2]])
    for b in boxes:
        got = {c["scientific"]: c for c in b["species"]["top"]}
        assert got["Buteo s0"]["posterior"] == pytest.approx(want[0], abs=1e-6)
        assert got["Buteo s2"]["p_geo"] == pytest.approx(g_all[2], abs=1e-6)
    off = run(m, BIRDLIKE, candidates=["Buteo s0", "Buteo s2"], geo=False)
    assert all(c["p_geo"] is None for b in off for c in b["species"]["top"])


def test_candidates_keep_the_range_veto_and_the_mammal_prior():
    raven = Engine3(lambda c: unit([0.1, 1.0, 0, 0, 0, 0]))             # looks most like sierramadrensis
    on = species(raven, "bird", candidates=["Corvus"])["species"]
    assert [c["scientific"] for c in on["top"][:2]] == ["Corvus corax", "Corvus sierramadrensis"]
    assert on["level"] == "genus"
    off = species(raven, "bird", candidates=["Corvus"], range_veto=False)["species"]
    assert off["top"][0]["scientific"] == "Corvus sierramadrensis" and off["level"] == "species"
    hare = Engine3(lambda c: unit([0, 0, 0, 0.2, 0.1, 1.0]))            # an unlabelled Lepus: genus back-off
    top = species(hare, "mammal", candidates=["Lepus", "Phoca"])["species"]["top"]
    from bioscan.service.adapters import geo

    assert top[0]["scientific"] == "Lepus californicus" and top[0]["p_geo"] == geo.UNLABELLED_NEUTRAL
    top = species(hare, "mammal", candidates=["Lepus", "Phoca"], mammal_geo=False)["species"]["top"]
    assert all(c["p_geo"] is None for c in top)


@pytest.mark.parametrize("switches", [{}, SWITCHES_OFF], ids=["switches-on", "switches-off"])
def test_batched_equals_one_by_one_with_candidates(monkeypatch, switches):
    monkeypatch.setattr(pipeline, "SPECIES_BATCH", 2)
    opts = {**OPTS, **switches, "candidates": ["Buteo s1", "Lynx", "Anolis"]}
    batched = pipeline.identify_many(Models12(), FRAMES12, opts)
    single = [pipeline.identify(Models12(), f.image, f.gate, f.lat, f.lon, f.taken_at, opts) for f in FRAMES12]
    assert batched == single
    assert {b["kind"] for o in batched for b in o["boxes"]} >= {"bird", "mammal", "other_animal"}
    off = pipeline.identify_many(Models12(), FRAMES12, {**opts, "species": False})
    assert all("species" not in b for o in off for b in o["boxes"])


# ---- validation -----------------------------------------------------------------------------

class Loaded:
    def __init__(self):
        self.names = Models12().names


def test_unknown_candidates_are_named():
    opts = stages.resolve_options({"identify": {"candidates": ["Buteo s1", "Nonexistus", "Anolis s9", "Reptilia"]}})
    with pytest.raises(ValueError) as e:
        stages.check_loaded(Loaded(), plugin.plan(["identify"], opts))
    assert "['Nonexistus', 'Anolis s9']" in str(e.value)
    stages.check_loaded(Loaded(), plugin.plan(["identify"],
                                              stages.resolve_options({"identify": {"candidates": ["O", "Lynx s0"]}})))
    stages.check_loaded(Loaded(), plugin.plan(["embed"], opts))                     # identify not wanted: not checked
    assert candidates.unknown(Loaded().names, ["reptilia", "LYNX"]) == []
    assert candidates.allowed(Loaded().names, []) is None
    assert {k: v.tolist() for k, v in candidates.allowed(Loaded().names, ["Lynx s2", "Squamata"]).items()} == \
        {"mammal": [2], "other_animal": [0, 1, 2, 3]}


def test_products_describes_the_option():
    o = stages.PRODUCTS["identify"]["options"]["candidates"]
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
    assert cli.main(["eval", _gt(tmp_path), "--out", str(tmp_path / "e"), "--candidates", "Buteo, Canis",
                     "--identify-opt", "kind_check=false"]) == 0
    meta = json.loads((tmp_path / "e" / "preds.ndjson").read_text().splitlines()[0])
    assert meta["options"]["identify"] == {"top_k": 5, "geo": True, "kind_check": False, "candidates": ["Buteo", "Canis"]}
    assert '- identify options: {"candidates": ["Buteo", "Canis"], "kind_check": false}' in \
        (tmp_path / "e" / "report.md").read_text()
    # without candidates the report is the base one: the same meta keys, in the same order
    report, _ = ev.run_eval(_gt(tmp_path), str(tmp_path / "f"), False, "http://x")
    keys = [line[2:].split(":")[0] for line in report.split("## ")[0].splitlines() if line.startswith("- ")]
    assert keys == ["groundtruth", "preds", "images", "geo", "preds schema", "complete", "synonyms", "generated",
                    "wall_s"] and "candidates" not in report
    for extra in (["--candidates", "Buteo"], ["--identify-opt", "kind_check=false"]):
        with pytest.raises(SystemExit, match="--preds"):
            cli.main(["eval", _gt(tmp_path), "--out", str(tmp_path / "g"), "--preds",
                      str(tmp_path / "f" / "preds.ndjson"), *extra])


def test_all_taxa_list_that_does_not_fit_on_the_device_is_dropped(caplog):
    from bioscan.service import engine

    lists = Models12().names

    class Device:
        placed = []

        def place(self, matrix):
            if matrix is lists["other_animal"].matrix:
                raise RuntimeError("MPS backend out of memory")
            self.placed.append(matrix)

    with caplog.at_level(logging.WARNING):
        dev, kept = engine.place_lists(Device(), lists)
    assert set(kept) == {"bird", "mammal"} and len(dev.placed) == 2 and "out of memory" in caplog.text

    class Full(Device):
        def place(self, matrix):
            raise RuntimeError("MPS backend out of memory")

    with pytest.raises(RuntimeError):                    # a curated list that does not fit still fails the load
        engine.place_lists(Full(), lists)
