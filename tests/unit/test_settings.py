"""Settings fingerprint: stable, and moves when any output-changing constant moves."""
from bioscan.service import rules, settings, taxa


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
    assert set(snap) == {"rules", "gate_prompts", "vocab", "taxa", "geo", "max_edge"} and snap["rules"]["RESCUE"] == 0.25
