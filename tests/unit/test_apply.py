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


def test_blank_token_file_counts_as_missing(tmp_path):
    f = tmp_path / "tok"
    f.write_text("  \n")
    assert ap.token(path=f) is None
    t = ap.token(create=True, path=f)
    assert t and f.read_text().strip() == t and oct(os.stat(f).st_mode & 0o777) == "0o600"


def test_a_drop_never_clobbers_a_sidecar_at_the_target(tmp_path):
    out, day2 = tmp_path / "out", tmp_path / "day2"
    out.mkdir(), day2.mkdir()
    (out / "IMG_0001.ARW").write_bytes(b"raw")
    (out / "IMG_0001.xmp").write_bytes(b"old")
    (day2 / "IMG_0001.JPG").write_bytes(b"jpg")
    (day2 / "IMG_0001.xmp").write_bytes(b"new")
    for dry in (True, False):
        r = ap.apply([], [str(day2 / "IMG_0001.JPG")], drop_to=str(out), dry_run=dry)
        assert r["moved"] == 0 and r["skipped"] == [str(day2 / "IMG_0001.JPG")]
    assert (out / "IMG_0001.xmp").read_bytes() == b"old" and (day2 / "IMG_0001.JPG").exists()


def test_dropping_the_jpeg_of_a_pair_leaves_the_raws_sidecar(tmp_path):
    for delete in (True, False):
        d = tmp_path / str(delete)
        d.mkdir()
        for n in ("P.ARW", "P.JPG", "P.xmp"):
            (d / n).write_bytes(b"x")
        ap.apply([str(d / "P.ARW")], [str(d / "P.JPG")], drop_to=None if delete else str(tmp_path / f"d{delete}"),
                 delete=delete)
        assert sorted(x.name for x in d.iterdir()) == ["P.ARW", "P.xmp"]
    # the last of the stem takes the sidecar along
    r = ap.apply([], [str(d / "P.ARW")], delete=True)
    assert r["sidecars"] == 1 and list(d.iterdir()) == []


def test_dry_run_predicts_same_name_skips(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    (a / "X.JPG").write_bytes(b"1")
    (b / "X.JPG").write_bytes(b"2")
    drop = [str(a / "X.JPG"), str(b / "X.JPG")]
    dry = ap.apply([], drop, drop_to=str(tmp_path / "d"), dry_run=True)
    assert dry == ap.apply([], drop, drop_to=str(tmp_path / "d"))
    assert dry["moved"] == 1 and dry["skipped"] == [str(b / "X.JPG")]
