"""export where the destination has no hard links (exFAT, FAT): os.link fails, the copy is published
into a target created with O_EXCL, still never overwriting and never deleting a foreign file.
Synthetic tmp fixtures; the real exFAT test is opt-in (GARDENER_EXFAT_TEST=1, macOS hdiutil)."""
from __future__ import annotations

import errno
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from bioscan.gardener import export as ex

SCOPE = {"kind": "asset_ids", "value": ["a"], "scope": "selected"}


@pytest.fixture(params=[errno.EPERM, errno.ENOTSUP], ids=["EPERM", "ENOTSUP"])
def no_links(request, monkeypatch):
    def link(*a, **k):
        raise OSError(request.param, os.strerror(request.param))

    monkeypatch.setattr(ex.os, "link", link)
    monkeypatch.setattr(ex, "_NO_LINKS", set())


@pytest.fixture
def src(tmp_path):
    d = tmp_path / "source"
    (d / "sub").mkdir(parents=True)
    (d / "same.jpg").write_bytes(b"original")
    return d


def run(dest, src, **kw):
    return ex.export(dest, title="t", scope=SCOPE, collections=[], copy=True, protected=[src],
                     assets=[{"id": "a", "files": [{"path": str(src / "same.jpg"), "role": "jpeg"}]}], **kw)


def entries(d):
    return sorted(p.name for p in d.iterdir())


def test_copy_succeeds_and_is_verified(tmp_path, src, no_links):
    rep = run(tmp_path / "out", src)
    folder = tmp_path / "out" / "files" / "t"
    assert rep["copied"] == 1 and rep["verified"] and not rep["failed"]
    assert entries(folder) == ["same.jpg"] and (folder / "same.jpg").read_bytes() == b"original"
    assert run(tmp_path / "out", src)["identical"] == 1


@pytest.mark.parametrize("conflict", ["rename", "skip", "fail"])
def test_existing_target_is_never_overwritten(tmp_path, src, no_links, conflict):
    folder = tmp_path / "out" / "files" / "t"
    folder.mkdir(parents=True)
    (folder / "same.jpg").write_bytes(b"theirs")
    if conflict == "fail":
        with pytest.raises(ValueError):
            run(tmp_path / "out", src, conflict=conflict)
    else:
        rep = run(tmp_path / "out", src, conflict=conflict)
        assert rep["renamed" if conflict == "rename" else "skipped"] == 1
    assert (folder / "same.jpg").read_bytes() == b"theirs"


@pytest.mark.parametrize("conflict", ["rename", "skip", "fail"])
def test_target_appearing_before_publish_is_not_overwritten(tmp_path, src, no_links, monkeypatch, conflict):
    real = ex._publish_by_copy

    def racing(part, dst, digest, where, times):
        if dst.name == "same.jpg" and not dst.exists():
            dst.write_bytes(b"appeared")
        return real(part, dst, digest, where, times)

    monkeypatch.setattr(ex, "_publish_by_copy", racing)
    rep = run(tmp_path / "out", src, conflict=conflict)
    folder = tmp_path / "out" / "files" / "t"
    assert (folder / "same.jpg").read_bytes() == b"appeared"
    if conflict == "rename":
        assert (folder / "same (1).jpg").read_bytes() == b"original" and rep["verified"]
    else:
        assert not rep["verified"] and entries(folder) == ["same.jpg"]


def test_failure_mid_copy_leaves_nothing_and_deletes_nothing_foreign(tmp_path, src, no_links, monkeypatch):
    folder = tmp_path / "out" / "files" / "t"
    folder.mkdir(parents=True)
    (folder / "same.jpg.gardener-part").write_bytes(b"foreign")

    def fsync(fd):
        raise OSError(errno.EIO, "disk went away")

    monkeypatch.setattr(ex.os, "fsync", fsync)
    rep = run(tmp_path / "out", src)
    assert rep["failed"] and "disk went away" in rep["failed"][0]["error"] and not rep["verified"]
    assert entries(folder) == ["same.jpg.gardener-part"]
    assert (folder / "same.jpg.gardener-part").read_bytes() == b"foreign"


def test_a_target_replaced_mid_copy_is_not_deleted(tmp_path, monkeypatch, no_links):
    a = tmp_path / "a"
    a.write_bytes(b"new")
    dst = tmp_path / "out" / "b"
    dst.parent.mkdir()

    def swap_then_fail(fd):
        os.rename(dst, tmp_path / "ours-moved")      # someone moves ours away and puts theirs in its place
        dst.write_bytes(b"someone else's")
        raise OSError(errno.EIO, "boom")

    monkeypatch.setattr(ex.os, "fsync", swap_then_fail)
    with pytest.raises(OSError):
        ex._copy(str(a), dst)
    assert dst.read_bytes() == b"someone else's"


def test_symlinked_parent_is_still_refused(tmp_path, src, no_links):
    dest = tmp_path / "out"
    (dest / "files").mkdir(parents=True)
    os.symlink(src / "sub", dest / "files" / "t")
    with pytest.raises(ValueError):
        run(dest, src)
    assert entries(src / "sub") == []


def test_folder_swapped_for_a_symlink_before_publish_is_refused(tmp_path, src, no_links, monkeypatch):
    real = ex._publish_by_copy
    outside = tmp_path / "outside"
    outside.mkdir()

    def swap(part, dst, digest, where, times):
        os.rename(dst.parent, dst.parent.with_name("t-moved"))
        os.symlink(outside, dst.parent)
        return real(part, dst, digest, where, times)

    monkeypatch.setattr(ex, "_publish_by_copy", swap)
    rep = run(tmp_path / "out", src)
    assert rep["failed"] and entries(outside) == []


@pytest.mark.skipif(os.environ.get("GARDENER_EXFAT_TEST") != "1" or sys.platform != "darwin"
                    or not shutil.which("hdiutil"), reason="opt-in: GARDENER_EXFAT_TEST=1 on macOS")
def test_real_exfat_image(tmp_path, src):
    work = Path(os.environ.get("GARDENER_EXFAT_DIR") or tmp_path)     # where the image and mount point go
    work.mkdir(parents=True, exist_ok=True)
    image, mnt = work / "gardener-exfat-test.dmg", work / "mnt"
    if image.exists():
        image.unlink()
    subprocess.run(["hdiutil", "create", "-size", "20m", "-fs", "ExFAT", "-volname", "GTEST", "-o", str(image)],
                   check=True, capture_output=True)
    mnt.mkdir(exist_ok=True)
    subprocess.run(["hdiutil", "attach", "-nobrowse", "-mountpoint", str(mnt), str(image)], check=True,
                   capture_output=True)
    try:
        ex._NO_LINKS.clear()
        rep = run(mnt / "export", src)
        assert rep["copied"] == 1 and rep["verified"], rep
        assert os.stat(mnt).st_dev in ex._NO_LINKS            # links failed there: the fallback published it
        assert (mnt / "export" / "files" / "t" / "same.jpg").read_bytes() == b"original"
        assert os.path.isfile(rep["xml_path"])
        assert run(mnt / "export", src)["identical"] == 1
    finally:
        subprocess.run(["hdiutil", "detach", str(mnt)], check=False, capture_output=True)
        image.unlink(missing_ok=True)
        if mnt.exists() and not any(mnt.iterdir()):
            mnt.rmdir()
