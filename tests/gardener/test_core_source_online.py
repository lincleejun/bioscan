"""Sources are folders: with the real toolkit (not a fake), a fresh source must read online."""
from bioscan.gardener.tasks import source_online


def test_source_online_accepts_a_readable_folder_and_rejects_a_missing_one(tmp_path):
    (tmp_path / "a.jpg").write_bytes(b"x")
    assert source_online(str(tmp_path))
    assert not source_online(str(tmp_path / "gone")) and not source_online(str(tmp_path / "a.jpg"))
