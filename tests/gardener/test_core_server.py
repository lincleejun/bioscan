"""`gardener serve` owns its data directory: a second service on the same one refuses to start
before it touches the database (it would re-queue the first one's running run)."""
from __future__ import annotations

import pytest

from bioscan.gardener import server


def test_a_second_service_on_the_same_data_dir_refuses_to_start(tmp_path, capsys, monkeypatch):
    started = []
    monkeypatch.setattr("bioscan.gardener.api.create_gardener_app", lambda *a, **k: started.append(a))
    held = server.hold_data_dir(tmp_path)
    try:
        with pytest.raises(SystemExit) as e:
            server.main(["--data-dir", str(tmp_path), "--port", "0"])
        assert e.value.code == 2 and started == []
        assert "已有 Gardener 服务在使用数据目录" in capsys.readouterr().err
    finally:
        held.close()
    again = server.hold_data_dir(tmp_path)          # released with its holder
    again.close()


def test_a_data_dir_that_cannot_be_locked_says_why(tmp_path, capsys):
    blocker = tmp_path / "file"
    blocker.write_text("not a directory")
    with pytest.raises(SystemExit) as e:
        server.main(["--data-dir", str(blocker)])
    err = capsys.readouterr().err
    assert e.value.code == 2 and "无法锁定数据目录" in err and "exists" in err.lower()
