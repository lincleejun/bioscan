"""export safety regressions (acceptance MB1, MB2, E14): symlinks under the destination, foreign
temporary files, and destinations that appear after planning. Synthetic tmp fixtures only."""
from __future__ import annotations

import os

import pytest

from bioscan.gardener import export as ex

SCOPE = {"kind": "asset_ids", "value": ["a"], "scope": "selected"}


@pytest.fixture
def src(tmp_path):
    d = tmp_path / "source"
    (d / "sub").mkdir(parents=True)
    (d / "same.jpg").write_bytes(b"original")
    return d


def assets(src):
    return [{"id": "a", "media": "photo", "files": [{"path": str(src / "same.jpg"), "role": "jpeg"}]}]


def listing(d):
    return sorted(str(p.relative_to(d)) for p in d.rglob("*"))


def run(dest, src, **kw):
    return ex.export(dest, title="t", scope=SCOPE, collections=[], assets=assets(src), copy=True,
                     protected=[src], **kw)


def test_symlinked_files_folder_into_source_is_refused(tmp_path, src):
    dest = tmp_path / "out"
    dest.mkdir()
    os.symlink(src / "sub", dest / "files")
    before = listing(src)
    with pytest.raises(ValueError):
        run(dest, src)
    assert listing(src) == before


def test_symlinked_title_folder_is_refused(tmp_path, src):
    dest = tmp_path / "out"
    (dest / "files").mkdir(parents=True)
    os.symlink(tmp_path / "elsewhere", dest / "files" / "t")
    (tmp_path / "elsewhere").mkdir()
    with pytest.raises(ValueError):
        run(dest, src)
    assert listing(tmp_path / "elsewhere") == [] and listing(src) == ["same.jpg", "sub"]


def test_symlinked_file_target_is_never_written_through(tmp_path, src):
    dest = tmp_path / "out"
    (dest / "files" / "t").mkdir(parents=True)
    victim = tmp_path / "victim.jpg"
    os.symlink(victim, dest / "files" / "t" / "same.jpg")           # dangling: points outside
    rep = run(dest, src)
    assert not victim.exists()
    assert rep["copied"] == 1 and rep["renamed"] == 1 and rep["verified"]
    assert (dest / "files" / "t" / "same (1).jpg").read_bytes() == b"original"
    os.symlink(src / "same.jpg", dest / "files" / "t" / "link.jpg")   # a symlink to the original is not "identical"
    assert not ex._same(dest / "files" / "t" / "link.jpg", str(src / "same.jpg"))
    with pytest.raises(ValueError):                                   # conflict fail: a symlink counts as different
        _fail_on_symlink(tmp_path, src)


def _fail_on_symlink(tmp_path, src):
    dest = tmp_path / "out3"
    (dest / "files" / "t").mkdir(parents=True)
    os.symlink(src / "same.jpg", dest / "files" / "t" / "same.jpg")   # same bytes, but through a symlink
    run(dest, src, conflict="fail")


def test_foreign_part_file_is_left_alone(tmp_path, src):
    dest = tmp_path / "out"
    (dest / "files" / "t").mkdir(parents=True)
    foreign = dest / "files" / "t" / "same.jpg.gardener-part"
    foreign.write_bytes(b"someone else's")
    rep = run(dest, src)
    assert foreign.read_bytes() == b"someone else's"
    assert rep["copied"] == 1 and rep["verified"]
    assert not [p for p in (dest / "files" / "t").iterdir() if p.name.startswith(".")]   # our temp file is gone


def test_target_appearing_after_planning_is_not_overwritten(tmp_path, src, monkeypatch):
    real = ex._copy

    def racing(s, dst):
        if dst.name == "same.jpg" and not dst.exists():      # someone else takes the planned name, once
            dst.write_bytes(b"appeared after planning")
        return real(s, dst)

    monkeypatch.setattr(ex, "_copy", racing)
    dest = tmp_path / "out"
    rep = run(dest, src)
    folder = dest / "files" / "t"
    assert (folder / "same.jpg").read_bytes() == b"appeared after planning"
    assert (folder / "same (1).jpg").read_bytes() == b"original"
    assert rep["renamed"] == 1 and rep["verified"]

    rep = run(tmp_path / "skip", src, conflict="skip")
    assert rep["skipped"] == 1 and not rep["verified"]
    assert (tmp_path / "skip" / "files" / "t" / "same.jpg").read_bytes() == b"appeared after planning"
    rep = run(tmp_path / "fail", src, conflict="fail")
    assert rep["failed"] and not rep["verified"]


def test_copy_never_overwrites_an_existing_name(tmp_path):
    a = tmp_path / "a"
    a.write_bytes(b"new")
    b = tmp_path / "b"
    b.write_bytes(b"old")
    with pytest.raises(FileExistsError):
        ex._copy(str(a), b)
    assert b.read_bytes() == b"old" and sorted(p.name for p in tmp_path.iterdir()) == ["a", "b"]
