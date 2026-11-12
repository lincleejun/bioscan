"""本地目录 (contract revision 11): read-only browsing of the owner's folders. Path safety (symlinks out
of a root, `..`, outside the roots, missing), listing with RAW+JPEG pairing, task coverage, and the
fs/ preview cache (keyed by mtime, capped, emptied by clear-cache, never written next to an original)."""
import os
from pathlib import Path

import pytest
from core_fixtures import *  # noqa: F403
from core_fixtures import create_task, make_jpg

from bioscan.gardener import browse

P = "/api/v1/fs"


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    """A fake home (Pictures, Documents, Downloads; no Desktop) and /Volumes with one card and the
    startup disk's symlink to /."""
    h = tmp_path / "home"
    for d in ("Pictures", "Documents", "Downloads"):
        (h / d).mkdir(parents=True)
    vol = tmp_path / "Volumes"
    (vol / "CARD" / "DCIM" / "100MSDCF").mkdir(parents=True)
    os.symlink("/", vol / "Macintosh HD")
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setattr(browse, "VOLUMES", vol)
    return h


def snapshot(root: Path) -> dict[str, tuple[int, int]]:
    return {str(p): (p.stat().st_size, p.stat().st_mtime_ns) for p in root.rglob("*")}


def test_roots_are_home_folders_volumes_and_allow_roots_but_not_a_symlinked_volume(env, home, tmp_path):
    extra = tmp_path / "shoots"
    extra.mkdir()
    c = env.client(allow_roots=[str(extra)])
    roots = c.get(P + "/roots").json()
    by = {r["path"]: r for r in roots}
    real = os.path.realpath
    assert by[real(home / "Pictures")] == {"name": "图片", "path": real(home / "Pictures"), "kind": "home", "online": True}
    assert by[real(home / "Desktop")]["online"] is False                 # missing: shown, offline
    assert by[real(tmp_path / "Volumes" / "CARD")]["kind"] == "volume"
    assert by[real(extra)]["kind"] == "allow-root"
    assert "/" not in by and not any(r["name"] == "Macintosh HD" for r in roots)
    r = c.get(P + "/list", params={"path": str(tmp_path / "Volumes" / "Macintosh HD")})
    assert r.status_code == 403, r.text


def test_path_safety(env, home, tmp_path):
    outside = tmp_path / "secret"
    outside.mkdir()
    make_jpg(outside / "x.jpg")
    os.symlink(outside, home / "Pictures" / "escape")                       # a folder link out of the root
    os.symlink(outside / "x.jpg", home / "Pictures" / "x.jpg")               # a file link out of the root
    (home / "Pictures" / ".hidden").mkdir()
    make_jpg(home / "Pictures" / ".hidden" / "h.jpg")
    c = env.client()
    pic = str(home / "Pictures")

    def status(route, path):
        return c.get(P + route, params={"path": path}).status_code

    assert status("/list", pic + "/escape") == 403
    assert status("/thumb", pic + "/escape/x.jpg") == 403
    assert status("/thumb", pic + "/x.jpg") == 403
    assert status("/list", pic + "/../../secret") == 400                     # `..` refused before resolving
    assert status("/list", pic + "/sub/..") == 400
    assert status("/list", "Pictures") == 400                                # relative
    assert status("/list", str(outside)) == 403                              # outside every root
    assert status("/list", "/etc") == 403
    assert status("/thumb", "/etc/hosts") == 403
    assert status("/list", str(tmp_path / "nope")) == 403                    # missing outside: no probing
    assert status("/list", pic + "/nope") == 404                             # missing inside
    assert status("/thumb", pic + "/nope.jpg") == 404
    assert status("/list", pic + "/.hidden") == 403
    assert status("/thumb", pic + "/.hidden/h.jpg") == 403
    body = c.get(P + "/list", params={"path": pic}).json()
    assert [d["name"] for d in body["dirs"]] == [] and body["items"] == []   # links and hidden never listed


def test_listing_pairs_raw_with_jpeg_counts_subfolders_and_pages(env, home):
    trip = home / "Pictures" / "trip"
    trip.mkdir()
    for i in range(5):
        (trip / f"DSC{i:04d}.ARW").write_bytes(b"raw" * (i + 1))
    make_jpg(trip / "DSC0000.JPG")                                           # pairs with DSC0000.ARW
    make_jpg(trip / "pano.png")
    (trip / "clip.MP4").write_bytes(b"\0" * 10)
    (trip / "DSC0001.ARW.xmp").write_text("x")                               # sidecar
    (trip / "notes.txt").write_text("x")                                     # unsupported
    (trip / ".DS_Store").write_text("x")
    (trip / "day2").mkdir()
    make_jpg(trip / "day2" / "a.jpg")
    (trip / "day2" / "deeper").mkdir()
    c = env.client()
    top = c.get(P + "/list", params={"path": str(home / "Pictures")}).json()
    assert top["parent"] is None and top["root"]["kind"] == "home" and top["name"] == "图片"
    assert top["dirs"] == [{"name": "trip", "path": str(trip.resolve()), "photos": 6, "videos": 1, "dirs": 1,
                            "readable": True, "tasks": []}]
    body = c.get(P + "/list", params={"path": str(trip), "limit": 3}).json()
    assert (body["total"], body["photos"], body["videos"], body["skipped"]) == (7, 6, 1, 2)
    assert body["parent"] == str(home.resolve() / "Pictures")
    assert [d["name"] for d in body["dirs"]] == ["day2"] and body["dirs"][0]["photos"] == 1
    first = body["items"][1]
    assert first["name"] == "DSC0000.JPG" and first["kind"] == "raw+jpeg" and first["media"] == "photo"
    assert sorted(f["role"] for f in first["files"]) == ["jpeg", "raw"]
    assert first["size"] == sum(f["size"] for f in first["files"]) and first["mtime"].endswith("Z")
    page2 = c.get(P + "/list", params={"path": str(trip), "offset": 3, "limit": 3}).json()
    rest = c.get(P + "/list", params={"path": str(trip), "offset": 6}).json()["items"]
    every = body["items"] + page2["items"] + rest
    assert [(x["name"], x["kind"]) for x in every] == [
        ("clip.MP4", "video"), ("DSC0000.JPG", "raw+jpeg"), ("DSC0001.ARW", "raw"), ("DSC0002.ARW", "raw"),
        ("DSC0003.ARW", "raw"), ("DSC0004.ARW", "raw"), ("pano.png", "image")]


def test_folders_inside_a_task_source_link_to_the_task(env, home):
    trip = home / "Pictures" / "trip"
    make_jpg(trip / "day1" / "a.jpg")
    c = env.client()
    t = create_task(c, [str(trip)], caps=[])
    top = c.get(P + "/list", params={"path": str(home / "Pictures")}).json()
    assert top["tasks"] == [] and top["dirs"][0]["tasks"] == [{"id": t["id"], "title": "测试任务"}]
    inner = c.get(P + "/list", params={"path": str(trip / "day1")}).json()
    assert inner["tasks"] == [{"id": t["id"], "title": "测试任务"}]


def test_thumbs_are_cached_by_mtime_outside_the_source_and_cleared_with_the_cache(env, home):
    trip = home / "Pictures" / "trip"
    src = make_jpg(trip / "a.jpg", size=(2000, 1500))
    before = snapshot(trip)
    c = env.client()
    fs = env.data / "cache" / "fs"
    r = c.get(P + "/thumb", params={"path": src})
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert [p.name.endswith(".t.jpg") for p in fs.rglob("*.jpg")] == [True]   # thumb only: no 1600 px file
    first = next(fs.rglob("*.t.jpg"))
    stamp = first.stat().st_mtime_ns
    assert c.get(P + "/thumb", params={"path": src}).content == r.content
    assert first.stat().st_mtime_ns == stamp                                   # served from the cache
    large = c.get(P + "/thumb", params={"path": src, "size": "large"})
    import io

    from PIL import Image
    assert max(Image.open(io.BytesIO(large.content)).size) == 1600
    assert max(Image.open(io.BytesIO(r.content)).size) == 360
    os.utime(src, ns=(stamp, stamp + 10**9))                                   # a changed file: a new key
    c.get(P + "/thumb", params={"path": src})
    assert len(list(fs.rglob("*.t.jpg"))) == 2
    assert c.get(P + "/thumb", params={"path": src, "size": "huge"}).status_code == 400
    (trip / "notes.txt").write_text("x")
    assert c.get(P + "/thumb", params={"path": str(trip / "notes.txt")}).status_code == 400
    after = snapshot(trip)
    assert {k: v for k, v in after.items() if not k.endswith("notes.txt") and not k.endswith("a.jpg")} == \
        {k: v for k, v in before.items() if not k.endswith("a.jpg")}             # nothing new beside the original
    assert set(after) == set(before) | {str(trip / "notes.txt")}
    cleared = c.post("/api/v1/storage/clear-cache").json()
    assert cleared["removed_files"] >= 3 and list(fs.rglob("*.jpg")) == []
    assert c.get(P + "/thumb", params={"path": src}).status_code == 200        # rebuilt on request


def test_thumb_cache_is_capped_oldest_first(tmp_path):
    root = tmp_path / "src"
    paths = [make_jpg(root / f"{i}.jpg", size=(800, 600)) for i in range(4)]
    thumbs = browse.Thumbs(tmp_path / "cache")
    got = [thumbs.get(p, "thumb") for p in paths]
    for i, g in enumerate(got[:-1]):
        os.utime(g, (1_000 + i, 1_000 + i))
    one = got[0].stat().st_size
    thumbs.cap = one * 3                                     # evicts down to 90 %: room for two
    thumbs.get(make_jpg(root / "new.jpg", size=(800, 600)), "thumb")
    left = {p.name for p in (tmp_path / "cache" / "fs").rglob("*.jpg")}
    assert len(left) == 2 and got[0].name not in left and got[-1].name in left


def test_browsing_writes_nothing_to_the_database(env, home):
    make_jpg(home / "Pictures" / "a.jpg")
    c = env.client()
    c.get(P + "/list", params={"path": str(home / "Pictures")})
    c.get(P + "/thumb", params={"path": str(home / "Pictures" / "a.jpg")})
    assert c.get("/api/v1/assets").json()["total"] == 0 and c.get("/api/v1/tasks").json() == []
