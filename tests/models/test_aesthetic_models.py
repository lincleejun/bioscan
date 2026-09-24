"""Real SigLIP2 for the aesthetic head: the vectors scripts/train_aesthetic_head.py trains on are the
vectors /run's `embed` serves and the aesthetics stage scores, and the stage runs on real vectors
(with the committed general head when it exists, else score null and a note).

Only with BIOSCAN_MODEL_TESTS=1 and SigLIP2 in the HF cache (CI models.yml). Uses 4 photos of
tests/models/sample.csv (fetched like test_real_models.py)."""
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pytest

pytestmark = pytest.mark.skipif(os.environ.get("BIOSCAN_MODEL_TESTS") != "1",
                                reason="real-model check: set BIOSCAN_MODEL_TESTS=1 (see tests/models/download.py)")

ROOT = Path(__file__).resolve().parents[2]


def test_training_vectors_are_the_served_vectors_and_the_stage_scores_them(tmp_path):
    from aesthetic_helpers import random_head
    from fastapi.testclient import TestClient
    from test_real_models import fetch, sample

    from bioscan import aesthetic as aes
    from bioscan.service.app import create_app
    from bioscan.service.engine import Engine

    paths = [str(p) for p in (fetch(r) for r in sample()[:4]) if p is not None]
    assert len(paths) >= 2, "could not fetch the sample photos"
    head = aes.write_head(random_head(1, name="probe"), tmp_path / "probe.json")
    with TestClient(create_app(Engine("cpu"), decode_pool=ThreadPoolExecutor(2))) as c:
        r = c.post("/run", json={"inputs": [{"path": p} for p in paths], "want": ["embed", "aesthetics"],
                                 "options": {"aesthetics": {"head": str(head), "blend": 1.0}}})
    assert r.status_code == 200, r.text
    res = {e["path"]: e for e in map(json.loads, r.text.splitlines()) if e["type"] == "result"}
    assert set(res) == set(paths)
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import train_aesthetic_head as script
    finally:
        sys.path.remove(str(ROOT / "scripts"))
    trained_on = script.embed(paths, None, "cpu")          # what the EVA training feeds the fit
    probe = aes.load_head(head)
    for p in paths:
        served = np.asarray(res[p]["products"]["embed"]["vector"])
        assert np.allclose(served, trained_on[p], atol=2e-6), p
        out = res[p]["products"]["aesthetics"]
        assert out["personal"] == pytest.approx(probe.predict(served.tolist()), abs=2e-3)
        assert out["score"] == out["personal"]
        if aes.BUILTIN_HEAD.is_file():
            assert out["general"] is not None and "note" not in out
        else:
            assert out["general"] is None and "general head not installed" in out["note"]
