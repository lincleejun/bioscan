"""Settings fingerprint: stable, and moves when any output-changing constant moves."""
from bioscan.service import products, settings
from bioscan.service.adapters import owlv2


def test_fingerprint_stable_and_sensitive(monkeypatch):
    base = settings.fingerprint()
    assert len(base) == 12 and settings.fingerprint.__wrapped__() == base
    monkeypatch.setattr(products, "VETO", 0.81)
    assert settings.fingerprint.__wrapped__() != base
    monkeypatch.undo()
    monkeypatch.setitem(owlv2.VOCAB["mammal"], "a wolverine", 0.2)
    assert settings.fingerprint.__wrapped__() != base


def test_snapshot_is_json_and_complete():
    snap = settings.snapshot()
    assert set(snap) == {"rules", "gate_prompts", "vocab", "geo", "max_edge"} and snap["rules"]["RESCUE"] == 0.25
