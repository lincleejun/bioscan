"""CLI exit codes: 0 ok, 1 partial, 2 service, 3 incomplete stream / upstream error."""
import json
import urllib.error

import pytest

from bioscan import contract
from bioscan.cli import client, gt
from bioscan.cli import main as cli

OK = [contract.result("/a.jpg", "0" * 64, {}, {}, {}, {}), contract.done(1, 0, 1.0)]
PARTIAL = [contract.result("/a.jpg", "0" * 64, {}, {}, {}, {}), contract.error("/b.jpg", None, "decode: boom"),
           contract.done(1, 1, 1.0)]
CUT = OK[:1]


@pytest.mark.parametrize("json_mode", [False, True])
@pytest.mark.parametrize("events,code", [(OK, 0), (PARTIAL, 1), (CUT, 3)])
def test_run_exit_codes(tmp_path, monkeypatch, capsys, events, code, json_mode):
    (tmp_path / "a.jpg").write_bytes(b"")
    monkeypatch.setattr(client, "run", lambda payload, url: (json.dumps(e).encode() for e in events))
    argv = ["run", str(tmp_path)] + (["--json"] if json_mode else [])
    assert cli.main(argv) == code


def test_service_down_is_2(tmp_path):
    (tmp_path / "a.jpg").write_bytes(b"")
    assert cli.main(["--url", "http://127.0.0.1:1", "run", str(tmp_path)]) == cli.EXIT_SERVICE


def test_upstream_error_is_3(monkeypatch, capsys):
    def boom(*a, **kw):
        raise urllib.error.URLError("iNaturalist unreachable")
    monkeypatch.setattr(gt, "gt_inat", boom)
    assert cli.main(["gt", "inat", "--dry-run"]) == cli.EXIT_INCOMPLETE
    assert "upstream request failed: iNaturalist unreachable" in capsys.readouterr().err
