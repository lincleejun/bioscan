"""bioscan.apply: copy the keeps, move or delete the drops, sidecars along; the service token file."""
import os

from bioscan import apply as ap


def album(tmp_path):
    src = tmp_path / "card"
    src.mkdir(parents=True)
    for n in ("a.ARW", "a.xmp", "b.ARW", "c.ARW", "c.ARW.xmp"):
        (src / n).write_bytes(b"x")
    return src


def test_copy_move_delete_with_sidecars_skips_and_reports(tmp_path):
    src = album(tmp_path)
    keep, drop = [str(src / "a.ARW"), str(src / "gone.ARW")], [str(src / "b.ARW"), str(src / "c.ARW")]
    r = ap.apply(keep, drop, keep_to=str(tmp_path / "k"), drop_to=str(tmp_path / "d"), dry_run=True)
    assert r == {"copied": 1, "moved": 2, "deleted": 0, "sidecars": 2, "skipped": [], "missing": [str(src / "gone.ARW")]}
    assert not (tmp_path / "k").exists() and (src / "b.ARW").exists()
    r = ap.apply(keep, drop, keep_to=str(tmp_path / "k"), drop_to=str(tmp_path / "d"))
    assert r["copied"] == 1 and r["moved"] == 2 and r["sidecars"] == 2 and r["missing"] == [str(src / "gone.ARW")]
    assert sorted(x.name for x in (tmp_path / "k").iterdir()) == ["a.ARW", "a.xmp"] and (src / "a.ARW").exists()
    assert sorted(x.name for x in (tmp_path / "d").iterdir()) == ["b.ARW", "c.ARW", "c.ARW.xmp"] and not (src / "b.ARW").exists()
    # again: the keep is already at the target, the drops are gone
    r = ap.apply(keep, drop, keep_to=str(tmp_path / "k"), drop_to=str(tmp_path / "d"))
    assert r["copied"] == 0 and r["skipped"] == [str(src / "a.ARW")] and len(r["missing"]) == 3
    # delete: the files and their sidecars are unlinked
    src2 = album(tmp_path / "two")
    r = ap.apply([], [str(src2 / "c.ARW"), str(src2 / "b.ARW")], delete=True)
    assert r["deleted"] == 2 and r["sidecars"] == 1 and sorted(x.name for x in src2.iterdir()) == ["a.ARW", "a.xmp"]
    # nothing asked: nothing done
    assert ap.apply(keep, drop)["copied"] == 0 and (src / "a.ARW").exists()


def test_token_is_made_once_and_kept_private(tmp_path):
    f = tmp_path / "tok"
    assert ap.token(path=f) is None
    t = ap.token(create=True, path=f)
    assert t and ap.token(path=f) == t and ap.token(create=True, path=f) == t
    assert oct(os.stat(f).st_mode & 0o777) == "0o600"
