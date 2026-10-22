"""export on a REAL exFAT volume (macOS hdiutil disk images; opt-in: GARDENER_EXFAT_TEST=1, images under
GARDENER_EXFAT_DIR or a tmp dir, detached and deleted afterwards). exFAT has no hard links, and an
empty file's inode number changes after its first write, which mocks on APFS cannot show: these
check that nothing of ours is left behind on success or failure, and nothing of anyone else's is
removed."""
from __future__ import annotations

import errno
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from bioscan.gardener import export as ex

pytestmark = pytest.mark.skipif(os.environ.get("GARDENER_EXFAT_TEST") != "1" or sys.platform != "darwin"
                                or not shutil.which("hdiutil"), reason="opt-in: GARDENER_EXFAT_TEST=1 on macOS")
SCOPE = {"kind": "asset_ids", "value": ["a"], "scope": "selected"}


def _volume(work: Path, name: str, size: str):
    image, mnt = work / f"{name}.dmg", work / f"{name}-mnt"
    image.unlink(missing_ok=True)
    subprocess.run(["hdiutil", "create", "-size", size, "-fs", "ExFAT", "-volname", name.upper(), "-o", str(image)],
                   check=True, capture_output=True)
    mnt.mkdir(exist_ok=True)
    subprocess.run(["hdiutil", "attach", "-nobrowse", "-mountpoint", str(mnt), str(image)], check=True,
                   capture_output=True)
    return image, mnt


def _gone(image: Path, mnt: Path) -> None:
    subprocess.run(["hdiutil", "detach", "-force", str(mnt)], check=False, capture_output=True)
    image.unlink(missing_ok=True)
    if mnt.exists() and not any(mnt.iterdir()):
        mnt.rmdir()


@pytest.fixture(scope="module")
def work(tmp_path_factory):
    w = Path(os.environ.get("GARDENER_EXFAT_DIR") or tmp_path_factory.mktemp("exfat"))
    w.mkdir(parents=True, exist_ok=True)
    return w


@pytest.fixture(scope="module")
def volume(work):
    image, mnt = _volume(work, "gexfat", "40m")
    yield mnt
    _gone(image, mnt)


@pytest.fixture
def src(tmp_path):
    d = tmp_path / "source"
    d.mkdir()
    (d / "same.jpg").write_bytes(os.urandom(300_000))
    return d


@pytest.fixture
def dest(volume, request):
    d = volume / request.node.name.replace("[", "_").replace("]", "")
    shutil.rmtree(d, ignore_errors=True)
    return d


def run(dest, src, name="same.jpg"):
    return ex.export(dest, title="t", scope=SCOPE, collections=[], copy=True, protected=[src],
                     assets=[{"id": "a", "files": [{"path": str(src / name), "role": "jpeg"}]}])


def folder_entries(dest):
    """The folder's names, without macOS AppleDouble companions ("._X", which macOS writes on exFAT to
    hold X's extended attributes, e.g. com.apple.provenance) of files that are there; an orphaned
    companion still counts."""
    f = dest / "files" / "t"
    names = {p.name for p in f.iterdir()} if f.exists() else set()
    return sorted(n for n in names if not (n.startswith("._") and n[2:] in names))


def test_success_leaves_no_part_file(dest, src):
    rep = run(dest, src)
    assert rep["copied"] == 1 and rep["verified"] and not rep["failed"], rep
    assert os.stat(dest.parent).st_dev in ex._NO_LINKS              # really the no-hard-link path
    assert folder_entries(dest) == ["same.jpg"]
    assert (dest / "files" / "t" / "same.jpg").read_bytes() == (src / "same.jpg").read_bytes()
    assert run(dest, src)["identical"] == 1 and folder_entries(dest) == ["same.jpg"]


@pytest.mark.parametrize("which", [1, 2], ids=["temp-copy", "published-copy"])
def test_sha_mismatch_leaves_nothing(dest, src, monkeypatch, which):
    real, calls = ex._fd_sha, []

    def wrong_once(fd):
        calls.append(fd)
        return "0" * 64 if len(calls) == which else real(fd)

    monkeypatch.setattr(ex, "_fd_sha", wrong_once)
    rep = run(dest, src)
    assert rep["failed"] and "校验不一致" in rep["failed"][0]["error"] and not rep["verified"]
    assert folder_entries(dest) == []


def test_fsync_eio_leaves_nothing(dest, src, monkeypatch):
    def eio(fd):
        raise OSError(errno.EIO, "I/O error")

    monkeypatch.setattr(ex.os, "fsync", eio)
    rep = run(dest, src)
    assert rep["failed"] and "I/O error" in rep["failed"][0]["error"]
    assert folder_entries(dest) == []


def test_real_enospc_leaves_nothing_and_is_reported(work, tmp_path):
    image, mnt = _volume(work, "gexfat-tiny", "8m")
    try:
        big = tmp_path / "source"
        big.mkdir()
        (big / "big.bin").write_bytes(os.urandom(16 << 20))         # larger than the volume
        dest = mnt / "export"
        rep = run(dest, big, "big.bin")
        assert rep["failed"] and f"Errno {errno.ENOSPC}" in rep["failed"][0]["error"], rep["failed"]
        assert not rep["verified"] and rep["copied"] == 0
        assert folder_entries(dest) == []
        assert os.path.isfile(rep["xml_path"])                       # the space came back: the XML fits
    finally:
        _gone(image, mnt)


def test_target_replaced_by_another_process_is_not_deleted(dest, src, monkeypatch):
    target = dest / "files" / "t" / "same.jpg"
    elsewhere = dest / "moved-away.jpg"

    def replaced_then_eio(fd):
        subprocess.run([sys.executable, "-c", "import os, sys; os.rename(sys.argv[1], sys.argv[2]); "
                        "open(sys.argv[1], 'wb').write(b'another process')", str(target), str(elsewhere)],
                       check=True)
        raise OSError(errno.EIO, "I/O error")

    monkeypatch.setattr(ex.os, "fsync", replaced_then_eio)
    rep = run(dest, src)
    assert target.read_bytes() == b"another process"
    assert rep["failed"] and "未删除" in rep["failed"][0]["error"]
    assert folder_entries(dest) == ["same.jpg"]                      # our temp file is gone, theirs stays
