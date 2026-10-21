"""scan: discovery, RAW+JPEG pairing, skips, file identity."""
from __future__ import annotations

import os

from bioscan.gardener.scan import Skipped, discover, is_online, quick_hash


def touch(path, data=b"x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return str(path)


def test_pairs_only_raw_with_jpeg_and_never_repeats_keys(tmp_path):
    d = tmp_path / "card"
    for name in ("A.ARW", "a.jpg", "x.jpg", "x.mov", "y.ARW", "y.DNG", "z.tif", "z.png", "b.ARW", "b.DNG",
                 "b.JPG"):
        touch(d / name)
    found, skipped = discover(str(tmp_path))
    assert not skipped
    keys = [f.pair_key for f in found]
    assert len(keys) == len(set(keys))
    by = {f.pair_key: f for f in found}
    pair = by[f"{d}/a|rawjpeg"]
    assert pair.media == "photo" and pair.primary == str(d / "a.jpg")
    assert sorted(f.role for f in pair.files) == ["jpeg", "raw"]
    assert by[f"{d}/x.jpg"].media == "photo" and by[f"{d}/x.mov"].media == "video"
    assert f"{d}/y.arw" in by and f"{d}/y.dng" in by                      # RAW+RAW: two assets
    assert [f.role for f in by[f"{d}/z.tif"].files] == ["image"]
    # three of one stem: the JPEG pairs with the first RAW by extension, the other RAW stands alone
    assert [os.path.basename(f.path) for f in by[f"{d}/b|rawjpeg"].files] == ["b.ARW", "b.JPG"]
    assert f"{d}/b.dng" in by
    assert len(found) == 9
    assert [f.primary for f in found] == sorted(f.primary for f in found)


def test_skips_hidden_sidecars_and_unknown(tmp_path):
    touch(tmp_path / "IMG.JPG")
    touch(tmp_path / "IMG.xmp")
    touch(tmp_path / "meta.json")
    touch(tmp_path / ".DS_Store")
    touch(tmp_path / "._IMG.JPG")
    touch(tmp_path / ".hidden" / "inside.jpg")
    touch(tmp_path / "notes.txt")
    touch(tmp_path / "IMG.HEIC")
    touch(tmp_path / "sub" / "deep" / "C.mp4")
    found, skipped = discover(str(tmp_path))
    assert [os.path.basename(f.primary) for f in found] == ["IMG.JPG", "C.mp4"]
    reasons = {os.path.basename(s.path): s.reason for s in skipped}
    assert reasons == {"IMG.xmp": "sidecar", "meta.json": "sidecar", ".DS_Store": "sidecar", "._IMG.JPG": "hidden",
                       ".hidden": "hidden", "notes.txt": "unsupported", "IMG.HEIC": "unsupported"}
    f = found[0].files[0]
    assert f.size == 1 and f.ext == ".jpg" and f.mtime_ns > 0


def test_missing_root_is_unreadable(tmp_path):
    assert discover(str(tmp_path / "gone")) == ([], [Skipped(str(tmp_path / "gone"), "unreadable")])


def test_quick_hash_and_online(tmp_path):
    big = bytes(range(256)) * 12288          # 3 MiB: the middle MiB is not read
    a = touch(tmp_path / "a.bin", big)
    b = touch(tmp_path / "b.bin", big[:1 << 20] + b"\0" * (1 << 20) + big[2 << 20:])
    c = touch(tmp_path / "c.bin", big[:-1] + b"\1")
    assert quick_hash(a) == quick_hash(b) != quick_hash(c)
    assert quick_hash(touch(tmp_path / "small", b"abc")) != quick_hash(touch(tmp_path / "small2", b"abd"))
    assert is_online(a) and not is_online(str(tmp_path / "nope")) and not is_online(str(tmp_path))


def test_symlinked_folders_are_reported_not_followed(tmp_path):
    outside = tmp_path / "outside"
    touch(outside / "far.jpg")
    root = tmp_path / "root"
    touch(root / "here.jpg")
    os.symlink(outside, root / "linkdir")
    found, skipped = discover(str(root))
    assert [os.path.basename(f.primary) for f in found] == ["here.jpg"]
    assert skipped == [Skipped(str(root / "linkdir"), "symlink")]
