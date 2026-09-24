"""Settings fingerprint: stable, and moves when any output-changing constant moves."""
from bioscan.service import names, pipeline, rules, settings, taxa
from bioscan.service.adapters import geo


def test_fingerprint_stable_and_sensitive(monkeypatch):
    base = settings.fingerprint()
    assert len(base) == 12 and settings.fingerprint.__wrapped__() == base
    monkeypatch.setattr(rules, "VETO", 0.81)
    assert settings.fingerprint.__wrapped__() != base
    monkeypatch.undo()
    monkeypatch.setitem(taxa.VOCAB["mammal"], "a wolverine", 0.2)
    assert settings.fingerprint.__wrapped__() != base


def test_snapshot_is_json_and_complete():
    snap = settings.snapshot()
    assert set(snap) == {"rules", "gate_prompts", "vocab", "taxa", "geo", "switches", "prior_switch", "max_edge"}
    assert snap["rules"]["RESCUE"] == 0.25
    assert snap["geo"]["unlabelled"] == {"bird": "zero", "mammal": "genus"}
    assert snap["switches"] == {"range_veto": True, "kind_check": True, "mammal_geo": True}


def test_every_rules_threshold_is_in_the_fingerprint(monkeypatch):
    assert set(settings.snapshot()["rules"]) == {"VETO", "MAMMAL_SUPPORT", "BIRD_PROMOTE", "MIN_CROP", "SPECIES_P",
                                                 "SPECIES_MARGIN", "ROLLUP", "SECOND_PASS_FLOOR", "SECOND_PASS_TOP",
                                                 "IOU_SAME", "RESCUE", "RANGE_EPS", "RANGE_TAU", "KIND_TOP", "KIND_SURE"}
    base = settings.fingerprint()
    monkeypatch.setattr(rules, "NEW_THRESHOLD", 0.42, raising=False)   # nobody listed it anywhere
    assert settings.snapshot()["rules"]["NEW_THRESHOLD"] == 0.42 and settings.fingerprint.__wrapped__() != base


def test_label_map_contents_move_the_fingerprint(monkeypatch, tmp_path):
    """Editing mdd_map.csv changes mammal answers without touching the list sha: the fingerprint
    carries each committed label map's sha."""
    import shutil

    from bioscan import naming

    snap = settings.snapshot()["geo"]["label_maps"]
    assert set(snap) == {"bird", "mammal"} and all(len(v) == 12 for v in snap.values())
    data = tmp_path / "data"
    shutil.copytree(naming.DATA_DIR / naming.NAMES_DIR, data / naming.NAMES_DIR)
    monkeypatch.setattr(naming, "DATA_DIR", data)
    base = settings.fingerprint.__wrapped__()
    assert base == settings.fingerprint()                                # same bytes, same fingerprint
    mdd = data / naming.NAMES_DIR / "mdd_map.csv"
    mdd.write_text(mdd.read_text().replace("Ursus arctos_Brown Bear", "", 1))
    assert settings.snapshot()["geo"]["label_maps"]["mammal"] != snap["mammal"]
    assert settings.fingerprint.__wrapped__() != base


def test_accuracy_fixes_move_the_fingerprint(monkeypatch):
    """Every v1.5 knob that changes identify output moves the fingerprint: ε, τ, the kind-check
    margin, a list's unlabelled policy, the neutral constant, the kind-check lists, a switch default."""
    base = settings.fingerprint()
    changes = [lambda: monkeypatch.setattr(rules, "RANGE_EPS", 0.02),
               lambda: monkeypatch.setattr(rules, "RANGE_TAU", 0.1),
               lambda: monkeypatch.setattr(rules, "KIND_SURE", 0.6),
               lambda: monkeypatch.setitem(names.LISTS, "mammal", names.LISTS["mammal"]._replace(unlabelled="zero")),
               lambda: monkeypatch.setattr(geo, "UNLABELLED_NEUTRAL", 0.1),
               lambda: monkeypatch.setattr(taxa, "KIND_CHECK", ("bird",)),
               lambda: monkeypatch.setitem(pipeline.SWITCHES, "range_veto", False)]
    for change in changes:
        change()
        assert settings.fingerprint.__wrapped__() != base
        monkeypatch.undo()
        assert settings.fingerprint.__wrapped__() == base
