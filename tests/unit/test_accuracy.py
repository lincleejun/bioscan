"""v1.5 accuracy fixes, each behind an identify switch: the range veto (rules.range_veto), the
two-way kind check (pipeline) and the mammal location prior with genus back-off (geo.LocationPrior)."""
import dataclasses
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from bioscan.service import pipeline, rules
from bioscan.service.adapters import geo
from bioscan.service.engine import Engine, Loaders
from bioscan.service.names import NameList

OFF = {"range_veto": False, "kind_check": False, "mammal_geo": False}


def cand(sci, p_geo, post, fam="F"):
    genus = sci.split(" ")[0]
    return {"scientific": sci, "p_geo": p_geo, "posterior": post,
            "taxonomy": ["Animalia", "Chordata", "Aves", "O", fam, genus, sci]}


# ---- range veto (pure) ------------------------------------------------------------------------

def test_range_veto_promotes_an_in_range_congener():
    cands = [cand("Corvus sierramadrensis", 0.0, 0.7), cand("Larus occidentalis", 0.9, 0.2),
             cand("Corvus corax", 0.8, 0.08)]
    out, vetoed = rules.range_veto(cands)
    assert vetoed and [c["scientific"] for c in out] == ["Corvus corax", "Corvus sierramadrensis", "Larus occidentalis"]
    assert rules.species_level(out, species_ok=False) == ("genus", "Corvus")          # 0.78 of the mass is Corvus


@pytest.mark.parametrize("cands, vetoed, first", [
    ([cand("Corvus a", None, 0.9), cand("Corvus b", 0.9, 0.1)], False, "Corvus a"),          # no place / no prior
    ([cand("Corvus a", rules.RANGE_EPS, 0.9), cand("Corvus b", 0.9, 0.1)], False, "Corvus a"),  # at eps: in range
    ([cand("Corvus a", 0.001, 0.9), cand("Corvus b", 0.04, 0.1)], True, "Corvus a"),         # congener below tau
    ([cand("Corvus a", 0.001, 0.9), cand("Pica b", 0.9, 0.1)], True, "Corvus a"),            # not a congener
    ([], False, None),
])
def test_range_veto_cases(cands, vetoed, first):
    out, v = rules.range_veto(cands)
    assert v is vetoed and (out[0]["scientific"] if out else None) == first
    assert sorted(map(id, out)) == sorted(map(id, cands))              # reordered, never dropped


def test_range_veto_ignores_genus_back_off():
    """A p_geo borrowed from the genus is no evidence about the species: no veto on it, and a
    congener whose p_geo is borrowed is not promoted."""
    cands = [cand("Lepus californicus", 0.001, 0.9), cand("Lepus americanus", 0.5, 0.1)]
    assert rules.range_veto(cands, [False, True]) == (cands, False)
    out, vetoed = rules.range_veto(cands, [True, False])                  # top direct, congener borrowed
    assert vetoed and out[0]["scientific"] == "Lepus californicus"


def test_species_level_without_species():
    cands = [cand("Corvus a", 0.9, 0.9), cand("Corvus b", 0.9, 0.05)]
    assert rules.species_level(cands) == ("species", "Corvus a") and rules.species_level(cands, species_ok=False) == ("genus", "Corvus")


def test_kind_of():
    assert rules.kind_of({"bird": 0.2, "mammal": 0.8}) == ("mammal", True)
    assert rules.kind_of({"bird": 0.4, "mammal": 0.6}) == ("mammal", False)
    assert rules.kind_of({"bird": 0.1, "mammal": 0.1, "reptile": 0.8}) == ("reptile", True)


# ---- location prior: lumps, genus back-off -------------------------------------------------

class Source:
    labels = ["Cebus imitator_x", "Cebus albifrons_x", "Lepus americanus_x", "Lepus europaeus_x", "Ursus arctos_x"]
    p = np.array([0.7, 0.2, 0.3, 0.05, 0.0])

    def probs(self, lat, lon, week):
        return self.p


def test_lump_takes_the_largest_label():
    prior = geo.LocationPrior(Source(), ["Cebus albifrons_x|Cebus imitator_x", "Cebus albifrons_x", "Nope_x|"])
    np.testing.assert_array_equal(prior.p_geo(1.0, 2.0, None), [0.7, 0.2, 0.0])


def test_genus_back_off_and_neutral():
    sci = ["Lepus americanus", "Lepus californicus", "Ursus arctos", "Ursus maritimus", "Myotis levis"]
    labels = ["Lepus americanus_x", "", "Ursus arctos_x", "", ""]
    genus = geo.LocationPrior(Source(), labels, unlabelled="genus", genera=[s.split()[0] for s in sci])
    np.testing.assert_array_equal(genus.p_geo(1.0, 2.0, None), [0.3, 0.3, 0.0, 0.0, geo.UNLABELLED_NEUTRAL])
    assert genus.direct.tolist() == [True, False, True, False, False]
    zero = geo.LocationPrior(Source(), labels)                            # birds: today's behaviour
    np.testing.assert_array_equal(zero.p_geo(1.0, 2.0, None), [0.3, 0.0, 0.0, 0.0, 0.0])
    assert zero.direct.all() and zero.unlabelled == "zero"
    with pytest.raises(ValueError):
        geo.LocationPrior(Source(), labels, unlabelled="genus")          # needs the genera
    with pytest.raises(ValueError):
        geo.LocationPrior(Source(), labels, unlabelled="family")


def test_priors_for_uses_each_lists_policy():
    lists = {"bird": SimpleNamespace(birdnet=["Lepus americanus_x", ""], scientific=["Lepus americanus", "Lepus b"]),
             "mammal": SimpleNamespace(birdnet=["Lepus americanus_x", ""], scientific=["Lepus americanus", "Lepus b"],
                                       unlabelled="genus")}
    priors = geo.priors_for(lists, Source())
    np.testing.assert_array_equal(priors["bird"].p_geo(1.0, 2.0, None), [0.3, 0.0])
    np.testing.assert_array_equal(priors["mammal"].p_geo(1.0, 2.0, None), [0.3, 0.3])


# ---- pipeline: stand-in models with a real softmax over the list matrix ----------------------

DIM = 6


def unit(v):
    return np.asarray(v, dtype=np.float32) / np.linalg.norm(v)


def name_list(kind, cls, rows, labels, vecs, unlabelled="zero"):
    tax = [["Animalia", "Chordata", cls, "O", f"F{s.split()[0]}", s.split()[0], s] for s in rows]
    return NameList(f"{kind}-list", kind, list(rows), [""] * len(rows), tax, np.array(vecs, np.float32),
                    ["exact"] * len(rows), birdnet=list(labels), birdnet_how=["exact"] * len(rows),
                    unlabelled=unlabelled)


E = np.eye(DIM)
BIRDS = name_list("bird", "Aves", ["Corvus corax", "Corvus sierramadrensis", "Megascops kennicottii"],
                  ["Corvus corax_x", "Corvus sierramadrensis_x", "Megascops kennicottii_x"], E[:3])
MAMMALS = name_list("mammal", "Mammalia", ["Spilogale gracilis", "Phoca vitulina", "Lepus californicus"],
                    ["", "Phoca vitulina_x", ""], E[3:6], unlabelled="genus")


class GeoSource:
    labels = ["Corvus corax_x", "Corvus sierramadrensis_x", "Megascops kennicottii_x", "Phoca vitulina_x"]

    def probs(self, lat, lon, week):
        return np.array([0.8, 0.0, 0.6, 0.4])


class Engine3:
    """What identify needs, with a crop -> feature rule given per test: `feature(crop colour)`."""

    def __init__(self, feature, lists=None):
        self.names = lists or {"bird": BIRDS, "mammal": MAMMALS}
        self.priors = geo.priors_for(self.names, GeoSource())
        self.bioclip = SimpleNamespace(
            encode_images=lambda crops: np.array([feature(c.getpixel((c.width // 2, c.height // 2))) for c in crops]),
            probs=lambda feats, m: self._softmax(np.asarray(feats) @ np.asarray(m).T),
            logits=lambda feats, m: 8.0 * (np.asarray(feats, dtype=np.float64) @ np.asarray(m, dtype=np.float64).T))

    @staticmethod
    def _softmax(z):
        z = np.exp(8.0 * (z - z.max(axis=1, keepdims=True)))
        return z / z.sum(axis=1, keepdims=True)


def species(engine, kind, colour=(0, 0, 0), lat=37.4, **opts):
    boxes = [{"kind": kind}]
    pipeline._species(engine, Image.new("RGB", (100, 100), colour), boxes, [(10, 10, 60, 60)], lat,
                      -122.1 if lat is not None else None, None, {"geo": True, "top_k": 3, **opts})
    return boxes[0]


def test_raven_named_a_philippine_crow_is_vetoed():
    eng = Engine3(lambda c: unit([0.1, 1.0, 0, 0, 0, 0]))                # looks most like sierramadrensis
    off = species(eng, "bird", range_veto=False)["species"]
    assert off["top"][0]["scientific"] == "Corvus sierramadrensis" and off["level"] == "species"
    on = species(eng, "bird")["species"]
    assert [c["scientific"] for c in on["top"][:2]] == ["Corvus corax", "Corvus sierramadrensis"]
    assert on["level"] == "genus" and on["top"][1]["p_geo"] == 0.0
    nowhere = species(eng, "bird", lat=None)["species"]                    # place unknown: nothing to veto
    assert nowhere["top"][0]["scientific"] == "Corvus sierramadrensis" and nowhere["level"] == "species"


CROWISH = unit([0.0, 1.0, 0.4, 0, 0, 0])      # posterior: Philippine crow > owl > Raven


def test_in_range_congener_outside_the_top_k_is_found():
    """T1: the Raven ranks third, below an owl, with top_k=2. The veto on the Philippine crow now
    searches the whole list for an in-range Corvus; before, it only looked among the two returned."""
    eng = Engine3(lambda c: CROWISH)
    off = species(eng, "bird", top_k=3, range_veto=False)["species"]
    assert [c["scientific"] for c in off["top"]] == ["Corvus sierramadrensis", "Megascops kennicottii", "Corvus corax"]
    on = species(eng, "bird", top_k=2)["species"]
    assert [c["scientific"] for c in on["top"]] == ["Corvus corax", "Corvus sierramadrensis"]   # still k rows
    assert on["level"] != "species" and on["top"][0]["p_geo"] == 0.8


@pytest.mark.parametrize("feature, k", [
    ([0.6, 1.0, 0.2, 0, 0, 0], 3),    # Philippine crow first, vetoed, Raven already in the top-k
    ([0.6, 1.0, 0.2, 0, 0, 0], 2),
    ([1.0, 0.1, 0.3, 0, 0, 0], 1),    # Raven first: in range, no veto
    ([0.1, 0.2, 1.0, 0, 0, 0], 1),    # owl first: in range, no veto
    ([0.0, 1.0, 0.4, 0, 0, 0], 3),    # CROWISH with the Raven inside the top-k
])
def test_whole_list_search_changes_nothing_it_need_not(monkeypatch, feature, k):
    """Veto with the congener already returned, or no veto: byte-identical to the top-k-only veto."""
    eng = Engine3(lambda c: unit(feature))
    new = species(eng, "bird", top_k=k)
    monkeypatch.setattr(pipeline, "_congener", lambda *a: None)
    assert new == species(eng, "bird", top_k=k)


def test_no_in_range_congener_or_no_genus_leaves_the_veto_as_it_was():
    lonely = name_list("bird", "Aves", ["Corvus sierramadrensis", "Megascops kennicottii", "Pica pica"],
                       ["Corvus sierramadrensis_x", "Megascops kennicottii_x", "Corvus corax_x"], E[:3])
    no_genus = dataclasses.replace(BIRDS, taxonomy=[[*t[:5], "", t[6]] for t in BIRDS.taxonomy])
    feature = {"lonely": unit([1.0, 0.4, 0, 0, 0, 0]), "no_genus": CROWISH}
    for name, birds in (("lonely", lonely), ("no_genus", no_genus)):     # Pica in range, not a Corvus
        got = species(Engine3(lambda c, f=feature[name]: f, lists={"bird": birds}), "bird", top_k=2)["species"]
        assert got["top"][0]["scientific"] == "Corvus sierramadrensis" and len(got["top"]) == 2, name
        assert got["level"] != "species", name                           # still vetoed


def test_owl_gated_mammal_moves_to_bird():
    eng = Engine3(lambda c: unit([0, 0, 1.0, 0.3, 0, 0]))                 # an owl: Megascops, a little skunk
    box = species(eng, "mammal")
    assert box["kind"] == "bird" and box["species"]["list"] == "bird-list"
    assert box["species"]["top"][0]["scientific"] == "Megascops kennicottii" and box["species"]["level"] == "species"
    stay = species(eng, "mammal", kind_check=False)                        # switch off: named a skunk
    assert stay["kind"] == "mammal" and stay["species"]["top"][0]["scientific"] == "Spilogale gracilis"


def test_seal_gated_bird_moves_to_mammal():
    eng = Engine3(lambda c: unit([0, 0, 0, 0, 1.0, 0]))
    box = species(eng, "bird")
    assert box["kind"] == "mammal" and box["species"]["list"] == "mammal-list"
    assert box["species"]["top"][0]["scientific"] == "Phoca vitulina" and box["species"]["top"][0]["p_geo"] == 0.4


def test_thin_margin_move_is_unconfirmed():
    eng = Engine3(lambda c: unit([0, 0, 1.0, 1.05, 0, 0]))                # owl vs skunk, a coin flip
    box = species(eng, "bird")
    assert box["kind"] == "mammal" and box["species"]["level"] == "unconfirmed"
    agree = species(eng, "mammal")                                         # no move: graded as usual
    assert agree["kind"] == "mammal" and agree["species"]["level"] != "unconfirmed"


def test_kind_check_needs_two_lists_and_leaves_other_animals_alone():
    eng = Engine3(lambda c: unit([0, 0, 0, 0, 1.0, 0]), lists={"bird": BIRDS})
    assert species(eng, "bird")["kind"] == "bird"                          # nothing to compare against
    eng = Engine3(lambda c: unit([0, 0, 0, 0, 1.0, 0]))
    box = species(eng, "other_animal")
    assert box == {"kind": "other_animal", "species": None}


def test_list_size_does_not_buy_the_kind():
    """Pad the bird list with 3,000 near-duplicate names the crop looks nothing like: a skunk-ish
    crop stays mammal. Summed mass over whole lists would have handed it to the birds."""
    rng = np.random.default_rng(0)
    pad = [unit(E[0] * 0.2 + E[5] * -1.0 + 0.01 * rng.normal(size=DIM)) for _ in range(3000)]
    padded_birds = name_list("bird", "Aves", BIRDS.scientific + [f"Padus s{i}" for i in range(3000)],
                             BIRDS.birdnet + [""] * 3000, np.vstack([BIRDS.matrix, pad]))
    feature = unit([0, 0, 0.9, 1.0, 0, 0])                                 # skunk a bit more than owl
    plain = Engine3(lambda c: feature)
    padded = Engine3(lambda c: feature, lists={"bird": padded_birds, "mammal": MAMMALS})
    for eng in (plain, padded):
        box = species(eng, "bird")
        assert box["kind"] == "mammal" and box["species"]["top"][0]["scientific"] == "Spilogale gracilis"
    union = np.vstack([padded_birds.matrix, MAMMALS.matrix])
    joint = Engine3._softmax(feature[None, :] @ union.T)[0]
    rows_of = {"bird": slice(0, len(padded_birds.matrix)), "mammal": slice(len(padded_birds.matrix), len(union))}
    assert joint[rows_of["bird"]].sum() > joint[rows_of["mammal"]].sum()   # the old statistic: bird
    plain_joint = Engine3._softmax(feature[None, :] @ np.vstack([BIRDS.matrix, MAMMALS.matrix]).T)[0]
    # the same evidence padded or not (up to the two pad rows that fill this 3-name list's top 5)
    assert rules.kind_evidence(joint, rows_of) == pytest.approx(
        rules.kind_evidence(plain_joint, {"bird": slice(0, 3), "mammal": slice(3, 6)}), abs=0.01)


def test_a_third_list_joins_the_kind_check(monkeypatch):
    reptiles = name_list("reptile", "Reptilia", ["Crotalus oreganus"], [""], [unit([0, 0, 0, 0, 0.2, 1.0])])
    lists = {"bird": BIRDS, "mammal": MAMMALS, "reptile": reptiles}
    eng = Engine3(lambda c: unit([0, 0, 0, 0, 0.2, 1.0]), lists=lists)
    assert species(eng, "bird")["kind"] == "mammal"                        # not a kind-check list yet
    monkeypatch.setattr(pipeline, "KIND_CHECK", ("bird", "mammal", "reptile"))
    box = species(eng, "bird")
    assert box["kind"] == "reptile" and box["species"]["top"][0]["scientific"] == "Crotalus oreganus"


def test_mammal_geo_switch_and_genus_back_off_in_identify():
    eng = Engine3(lambda c: unit([0, 0, 0, 0.2, 0.1, 1.0]))               # a jackrabbit: unlabelled Lepus
    on = species(eng, "mammal")["species"]
    assert on["top"][0]["scientific"] == "Lepus californicus"
    assert on["top"][0]["p_geo"] == geo.UNLABELLED_NEUTRAL                  # no labelled Lepus in this list
    assert on["level"] == "species"                                        # borrowed p_geo vetoes nothing
    off = species(eng, "mammal", mammal_geo=False)["species"]
    assert all(c["p_geo"] is None for c in off["top"]) and off["top"][0]["posterior"] == off["top"][0]["p_visual"]


def test_bird_posterior_unchanged_by_the_mammal_prior():
    feature = lambda c: unit([0.6, 1.0, 0.2, 0, 0, 0])  # noqa: E731
    with_mammal = Engine3(feature)
    birds_only = Engine3(feature, lists={"bird": BIRDS, "mammal": name_list(
        "mammal", "Mammalia", MAMMALS.scientific, [], MAMMALS.matrix)})
    assert set(with_mammal.priors) == {"bird", "mammal"} and set(birds_only.priors) == {"bird"}
    for opts in ({}, {"mammal_geo": False}, OFF):
        assert species(with_mammal, "bird", **opts) == species(birds_only, "bird", **opts)


def test_all_switches_off_is_the_old_pipeline():
    """Switches off, a mammal prior loaded, an owl gated mammal: named against MDD with no p_geo,
    exactly as before v1.5."""
    eng = Engine3(lambda c: unit([0, 0, 1.0, 0.3, 0, 0]))
    box = species(eng, "mammal", **OFF)
    assert box["kind"] == "mammal" and all(c["p_geo"] is None for c in box["species"]["top"])


# ---- engine info ---------------------------------------------------------------------------

def test_info_reports_a_prior_per_list():
    lists = {"bird": BIRDS, "mammal": MAMMALS}
    eng = Engine("cpu", Loaders(siglip2=lambda d: None, owlv2=lambda d: None,
                                species=lambda d: (SimpleNamespace(), lists), geo=lambda: GeoSource()))
    eng._load_bioclip()
    assert eng.info()["models"]["priors"] == {"bird": geo.PRIOR_NAME, "mammal": geo.PRIOR_NAME}
    assert eng.info()["models"]["geo"] == geo.PRIOR_NAME                  # the v1.4 field, unchanged
    assert eng.info()["models"]["label_maps"] == {}                       # these lists came from no map file
    tagged = {"bird": BIRDS, "mammal": dataclasses.replace(MAMMALS, label_map_sha="0123456789ab")}
    eng = Engine("cpu", Loaders(siglip2=lambda d: None, owlv2=lambda d: None,
                                species=lambda d: (SimpleNamespace(), tagged), geo=lambda: GeoSource()))
    eng._load_bioclip()
    assert eng.info()["models"]["label_maps"] == {"mammal": "mdd_map.csv@0123456789ab"}
    assert eng.priors["mammal"].unlabelled == "genus" and eng.priors["bird"].unlabelled == "zero"
    eng = Engine("cpu", Loaders(siglip2=lambda d: None, owlv2=lambda d: None,
                                species=lambda d: (SimpleNamespace(), lists), geo=lambda: None))
    eng._load_bioclip()
    assert eng.info()["models"]["priors"] == {"bird": None, "mammal": None}
