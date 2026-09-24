"""Every identify payload /run emits conforms to bioscan/contract.py (as every event does to
contract.missing_fields), with species on and off."""
import pytest
from conftest import events, make_jpg

from bioscan import contract


@pytest.mark.parametrize("identify_opts", [{}, {"species": False}, {"top_k": 1, "geo": False}])
def test_identify_payloads_conform(client, tmp_path, identify_opts):
    paths = [make_jpg(tmp_path / f"{i}.jpg") for i in range(3)]
    r = client.post("/run", json={"inputs": [{"path": p} for p in paths], "want": ["identify"],
                                  "options": {"identify": identify_opts}})
    results = [e for e in events(r) if e["type"] == contract.RESULT]
    assert len(results) == 3
    for e in results:
        ident = contract.identify_of(e)
        assert contract.identify_problems(ident) == [], ident
        assert contract.boxes_of(ident)
