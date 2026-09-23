"""bioscan.naming: one normaliser for every side, and the stale-map check."""
from bioscan import naming

MAP = {"Tyto furcata": {"tol_name": "Tyto furcata", "tol_how": "exact",
                        "birdnet_label": "Tyto alba_Western Barn Owl", "birdnet_how": "synonym"},
       "Pica nuttallii": {"tol_name": "Pica nuttalli", "tol_how": "synonym",
                          "birdnet_label": "Pica nuttalli_Yellow-billed Magpie", "birdnet_how": "synonym"},
       "Icterus bullockiorum": {"tol_name": "", "tol_how": "none", "birdnet_label": "", "birdnet_how": "none"}}


def syn(sci, alias, source):
    return {"avilist_scientific": sci, "alias": alias, "source": source, "note": "x"}


def test_norm_binomial():
    assert naming.norm_binomial("Rangifer_tarandus") == "rangifer tarandus"
    assert naming.norm_binomial("  Corvus   Corax ") == naming.norm_binomial("corvus-corax") == "corvus corax"
    assert naming.norm_binomial(None) == ""


def test_map_in_sync_with_applied_synonyms():
    assert naming.map_problems(MAP, [syn("Tyto furcata", "Tyto alba", "birdnet"),
                                     syn("Pica nuttallii", "Pica nuttalli", "spelling"),
                                     syn("Cervus canadensis", "Cervus elaphus", "inat"),       # truth-only: ignored
                                     syn("Cervus canadensis", "Cervus elaphus", "spelling")]) == []  # mammal


def test_synonym_added_without_rebuilding_the_map_is_reported():
    problems = naming.map_problems(MAP, [syn("Icterus bullockiorum", "Icterus bullockii", "birdnet"),
                                         syn("Tyto furcata", "Tyto alba", "tol"),
                                         syn("Nonexistent bird", "Other bird", "birdnet")])
    assert len(problems) == 3
    assert "Icterus bullockiorum <- Icterus bullockii (birdnet)" in problems[0] and "build_name_map" in problems[0]
    assert "not in avilist_map.csv" in problems[2]


def test_names_module_reexports_the_same_functions():
    from bioscan.service import names
    assert names.norm_binomial is naming.norm_binomial and names.read_synonyms is naming.read_synonyms
