import csv
import json
import plistlib

import pytest

from bioscan import formats
from bioscan.cli import gt
from bioscan.cli.main import build_payload, launchd_plist, parser


def test_norm_and_builtin_match():
    assert gt.match_folder("Red-Tailed-Hawk", {}) == ("Buteo jamaicensis", "bird")
    assert gt.match_folder("Steller’s Jay", {}) == ("Cyanocitta stelleri", "bird")
    assert gt.match_folder("Western-Screech-Owl", {}) == ("Megascops kennicottii", "bird")
    assert gt.match_folder("Mystery Bird", {}) == ("", "")
    assert gt.match_folder("Stellers Jay", {}) == ("", "")    # the apostrophe is part of the name


def test_names_csv_match_and_ambiguity(tmp_path):
    avi = tmp_path / "AviList-2025.csv"
    avi.write_text("Taxon_rank,Scientific_name,English_name_AviList\n"
                   "species,Buteo jamaicensis,Red-tailed Hawk\n"
                   "subspecies,Buteo jamaicensis borealis,Red-tailed Hawk\n"
                   "species,Aus bus,Dup Bird\nspecies,Aus cus,Dup Bird\n")
    mdd = tmp_path / "MDD_v2.csv"
    mdd.write_text("sciName,mainCommonName,otherCommonNames\nRangifer_tarandus,Reindeer,Caribou|Wild Reindeer\n")
    idx = gt.load_name_index([str(avi), str(mdd)])
    assert gt.match_folder("Red-Tailed-Hawk", idx) == ("Buteo jamaicensis", "bird")  # subspecies row ignored
    assert gt.match_folder("caribou", idx) == ("Rangifer tarandus", "mammal")
    assert gt.match_folder("Rangifer tarandus", idx) == ("Rangifer tarandus", "mammal")
    assert gt.match_folder("Dup Bird", idx) == ("", "")
    assert gt.match_folder("Steller's Jay", idx) == ("Cyanocitta stelleri", "bird")  # builtin fallback


def test_gt_folders_writes_csv(tmp_path):
    (tmp_path / "Red-Tailed-Hawk").mkdir()
    for n in ("b.ARW", "a.arw", "._a.arw", "c.MP4"):
        (tmp_path / "Red-Tailed-Hawk" / n).write_bytes(b"")
    (tmp_path / "Unknown").mkdir()
    (tmp_path / "Unknown" / "x.jpg").write_bytes(b"")
    out = tmp_path / "gt.csv"
    counts = gt.gt_folders(str(tmp_path), str(out), [], formats.SCAN_EXT)
    assert counts == {"Red-Tailed-Hawk": 2, "Unknown": 1}
    rows = list(csv.DictReader(out.open()))
    assert list(rows[0]) == gt.OWN_FIELDS
    assert [r["path"].rsplit("/", 1)[1] for r in rows] == ["a.arw", "b.ARW", "x.jpg"]
    assert rows[0]["scientific"] == "Buteo jamaicensis" and rows[0]["tier"] == "own"
    assert rows[2]["scientific"] == ""


def test_exif_time():
    assert gt.exif_time("2025:12:24 16:41:44", "-07:00") == "2025-12-24T16:41:44-07:00"
    assert gt.exif_time("2025:12:24 16:41:44", None) == "2025-12-24T16:41:44"
    assert gt.exif_time("0000:00:00", None) == ""
    # exiftool -j gives SubSecTimeOriginal as a number when it has no leading zero, else a string
    assert gt.exif_time("2025:12:24 16:41:44", "-07:00", 37) == "2025-12-24T16:41:44.37-07:00"
    assert gt.exif_time("2025:12:24 16:41:44", "-07:00", "037") == "2025-12-24T16:41:44.037-07:00"
    assert gt.exif_time("2025:12:24 16:41:44", None, "") == "2025-12-24T16:41:44"


def test_every_supported_extension_is_scanned_in_any_case(tmp_path):
    """One list (bioscan.formats) for the decoder and both folder scans: nothing it decodes is skipped."""
    from bioscan.service import decode

    (tmp_path / "Red-Tailed-Hawk").mkdir()
    want = []
    for ext in sorted(formats.SCAN_EXT):
        for name in (f"a.{ext}", f"b.{ext.upper()}", f"c.{ext.capitalize()}"):
            (tmp_path / "Red-Tailed-Hawk" / name).write_bytes(b"")
            want.append(name)
    for name in ("d.png", "e.tif", "f.heic", "g.mp4", "h.xmp", "._a.arw"):
        (tmp_path / "Red-Tailed-Hawk" / name).write_bytes(b"")
    assert formats.RAW_EXT <= formats.SCAN_EXT and set(formats.DEFAULT_EXT.split(",")) == formats.SCAN_EXT
    assert {"cr2", "cr3", "nrw", "orf", "pef", "raf", "rw2", "srw"} <= formats.SCAN_EXT
    assert decode.is_raw is formats.is_raw          # the decoder routes RAW by the same list
    counts = gt.gt_folders(str(tmp_path), str(tmp_path / "gt.csv"), [], formats.parse_ext(formats.DEFAULT_EXT))
    assert counts == {"Red-Tailed-Hawk": len(want)}
    run = build_payload(parser().parse_args(["run", str(tmp_path), "-r"]))
    assert sorted(i["path"].rsplit("/", 1)[1] for i in run["inputs"]) == sorted(want)
    gt_default = parser().parse_args(["gt", "folders", str(tmp_path)]).ext
    assert formats.parse_ext(gt_default) == formats.SCAN_EXT


def test_parse_ext():
    assert formats.parse_ext("ARW, .jpg,,CR3 ") == {"arw", "jpg", "cr3"}


def test_inat_dry_run_urls():
    urls = []
    gt.gt_inat([("Canis latrans", "Coyote")], 14, 25, "/nonexistent", dry_run=True, log=urls.append)
    assert len(urls) == 1
    u = urls[0]
    assert u.startswith("https://api.inaturalist.org/v1/observations?")
    for part in ("taxon_name=Canis+latrans", "quality_grade=research", "photo_license=cc0%2Ccc-by%2Ccc-by-nc",
                 "place_id=14", "per_page=25", "photos=true"):
        assert part in u


class FakeClock:
    def __init__(self):
        self.t, self.slept = 0.0, []

    def clock(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


def test_inat_download_with_fallback_license_filter_and_throttle(tmp_path):
    obs = [
        {"id": 1, "uri": "https://www.inaturalist.org/observations/1", "geojson": {"coordinates": [-149.9, 61.2]},
         "time_observed_at": "2024-06-01T10:00:00-08:00", "taxon": {"iconic_taxon_name": "Mammalia"},
         "photos": [{"id": 10, "license_code": None, "url": "https://x/photos/10/square.jpg"},
                    {"id": 11, "license_code": "cc-by", "attribution": "(c) A, some rights reserved (CC BY)",
                     "url": "https://x/photos/11/square.jpg"}]},
        {"id": 2, "photos": [{"id": 20, "license_code": "cc-by-nd", "url": "https://x/photos/20/square.jpg"}]},
    ]
    calls = []

    def get(url):
        calls.append(url)
        if "api.inaturalist.org" in url:
            return json.dumps({"results": [] if "place_id=14" in url else obs}).encode()
        return b"JPEG"

    fc = FakeClock()
    rows = gt.gt_inat([("Rangifer tarandus", "Caribou")], 14, 25, str(tmp_path), get=get,
                      throttle=gt.Throttle(1.0, fc.clock, fc.sleep), log=lambda *_: None)
    assert "place_id=14" in calls[0] and "place_id" not in calls[1]  # fallback without place
    assert calls[2] == "https://x/photos/11/medium.jpg"  # unlicensed photo skipped, medium size
    assert len(calls) == 3  # obs 2 has only a cc-by-nd photo -> skipped
    assert fc.slept == [1.0, 1.0]  # 1 request/second
    assert len(rows) == 1
    r = rows[0]
    assert (r["scientific"], r["tier"], r["kind"], r["lat"], r["lon"]) == ("Rangifer tarandus", "inat", "mammal", 61.2, -149.9)
    assert r["license"] == "cc-by" and r["source"].endswith("/1") and "CC BY" in r["attribution"]
    written = list(csv.DictReader((tmp_path / "groundtruth-inat.csv").open()))
    assert list(written[0]) == gt.INAT_FIELDS
    assert (tmp_path / "Rangifer_tarandus" / "1_11.jpg").read_bytes() == b"JPEG"


def test_taxa_csv():
    from bioscan.cli.main import PROJECT_ROOT
    taxa = gt.read_taxa(str(PROJECT_ROOT / "data" / "taxa.csv"))
    sci = [s for s, _ in taxa]
    assert len(sci) == len(set(sci)) >= 60
    assert all(len(s.split()) == 2 and c for s, c in taxa)
    assert {"Rangifer tarandus", "Alces alces", "Ursus arctos"} <= set(sci)


def test_launchd_plist():
    p = plistlib.loads(launchd_plist(8765, 4, 32, uv="/opt/uv"))
    assert p["Label"] == "cc.outman.bioscan"
    assert p["ProgramArguments"][:3] == ["/opt/uv", "run", "--project"]
    assert p["ProgramArguments"][4:6] == ["bioscan", "serve"]
    assert "--allow-root" not in p["ProgramArguments"] and "--detail-edge" not in p["ProgramArguments"]


def test_launchd_plist_keeps_allow_roots_and_detail_edge():
    a = parser().parse_args(["serve", "--launchd", "--allow-root", "/Users/me/Photos", "--allow-root", "/Volumes/card",
                             "--detail-edge", "4096"])
    p = plistlib.loads(launchd_plist(8765, 4, 32, uv="/opt/uv", allow_roots=a.allow_root, detail_edge=a.detail_edge))
    assert p["ProgramArguments"][-6:] == ["--allow-root", "/Users/me/Photos", "--allow-root", "/Volumes/card",
                                          "--detail-edge", "4096"]
    # the served command line parses back to the same settings
    served = parser().parse_args(p["ProgramArguments"][5:])
    assert served.allow_root == ["/Users/me/Photos", "/Volumes/card"] and served.detail_edge == 4096


def test_run_payload(tmp_path):
    (tmp_path / "sub").mkdir()
    for n in ("b.ARW", "a.jpg", "notes.txt", "sub/c.dng"):
        (tmp_path / n).write_bytes(b"")
    a = parser().parse_args(["run", str(tmp_path), "--want", "identify,jpg", "--jpg-out", str(tmp_path / "o"), "--no-geo"])
    pl = build_payload(a)
    assert [i["path"].rsplit("/", 1)[1] for i in pl["inputs"]] == ["a.jpg", "b.ARW"]
    assert pl["want"] == ["identify", "jpg"]
    assert pl["options"]["identify"] == {"top_k": 5, "geo": False, "species": True}
    a = parser().parse_args(["run", str(tmp_path), "-r"])
    assert len(build_payload(a)["inputs"]) == 3


# ---- gt scene: Open Images V7 photos per scene label -------------------------------------------

def _oid_fixtures():
    """A tiny Open Images: 4 classes, 6 images. Class 'Star' has two MIDs, as in the real list."""
    classes = ("LabelName,DisplayName\n/m/bird,Bird\n/m/flight,Flight\n/m/person,Person\n/m/star1,Star\n"
               "/m/star2,Star\n/m/night,Night\n/m/close,Close-up\n")
    labels = {"validation": ("ImageID,Source,LabelName,Confidence\n"
                             "img1,verification,/m/bird,1\n"                     # bird alone -> bird
                             "img2,verification,/m/bird,1\nimg2,verification,/m/flight,1.0\n"   # bird in flight
                             "img3,verification,/m/bird,1\nimg3,verification,/m/person,1\n"     # person: disqualified
                             "img4,verification,/m/star2,1\nimg4,verification,/m/night,1\n"     # astro, at night
                             "img5,verification,/m/bird,0.0\n"                   # verified negative: nothing
                             "img6,verification,/m/bird,1\n"),                   # not in the images CSV: skipped
              "test": "ImageID,Source,LabelName,Confidence\nimg7,verification,/m/bird,1\nimg7,verification,/m/close,1\n"}
    images = {"validation": ("ImageID,Subset,OriginalURL,OriginalLandingURL,License,AuthorProfileURL,Author,Title,"
                             "OriginalSize,OriginalMD5,Thumbnail300KURL,Rotation\n"
                             + "".join(f"img{i},validation,u,https://flickr/{i},https://creativecommons.org/licenses/by/2.0/,"
                                       f"ap,Author {i},t,1,m,th,0\n" for i in (1, 2, 3, 4, 5))),
              "test": ("ImageID,Subset,OriginalURL,OriginalLandingURL,License,AuthorProfileURL,Author,Title,"
                       "OriginalSize,OriginalMD5,Thumbnail300KURL,Rotation\n"
                       "img7,test,u,https://flickr/7,https://creativecommons.org/licenses/by/2.0/,ap,Author 7,t,1,m,th,0\n")}
    spec = ("[label.bird]\ngroup = 'wildlife'\nscene = ''\nany = ['Bird']\nnot = ['Flight', 'Person']\n"
            "[label.bird_flight]\ngroup = 'wildlife'\nscene = 'bird_flight'\nall = ['Bird', 'Flight']\nnot = ['Person']\n"
            "[label.astro]\ngroup = 'night'\nscene = 'astro'\nany = ['Star']\nnot = ['Person']\n"
            "[attribute.light]\nnight = ['Night']\n[attribute.framing]\nclose_up = ['Close-up']\n")
    return classes, labels, images, spec


def test_gt_scene_samples_unambiguous_photos_with_attributes(tmp_path):
    classes, labels, images, spec = _oid_fixtures()
    (tmp_path / "map.toml").write_text(spec)
    calls = []

    def get(url):
        calls.append(url)
        if url == gt.OID_CLASSES:
            return classes.encode()
        for subset in ("validation", "test"):
            if url == gt.OID_LABELS[subset]:
                return labels[subset].encode()
            if url == gt.OID_IMAGES[subset]:
                return images[subset].encode()
        assert url.startswith("https://open-images-dataset.s3.amazonaws.com/")
        return b"JPEG"

    fc = FakeClock()
    rows = gt.gt_scene(str(tmp_path / "map.toml"), str(tmp_path / "out"), per_label=1, cache_dir=str(tmp_path / "cache"),
                       get=get, throttle=gt.Throttle(0.2, fc.clock, fc.sleep), log=lambda *_: None)
    by = {r["source"]: r for r in rows}
    assert set(by) == {"openimages:validation:img1", "openimages:validation:img2", "openimages:validation:img4"}
    # img3 has a person (not), img5 is a verified negative, img6 is not in the images CSV, img7 is past per_label
    b = by["openimages:validation:img1"]
    assert (b["tier"], b["scene"], b["scene_group"], b["light"], b["framing"]) == ("scene", "", "wildlife", "", "")
    assert b["license"].endswith("/by/2.0/") and b["attribution"] == "Author 1 https://flickr/1"
    assert by["openimages:validation:img2"]["scene"] == "bird_flight"
    a = by["openimages:validation:img4"]
    assert (a["scene"], a["scene_group"], a["light"]) == ("astro", "night", "night")   # the second Star MID counts
    assert (tmp_path / "out" / "bird_flight" / "validation_img2.jpg").read_bytes() == b"JPEG"
    assert (tmp_path / "cache" / "oidv7-class-descriptions.csv").exists()          # CSVs cached, fetched once
    assert sum(u == gt.OID_CLASSES for u in calls) == 1 and len(fc.slept) == 2   # 3 photo downloads, throttled between
    written = list(csv.DictReader((tmp_path / "out" / "groundtruth-scene.csv").open()))
    assert list(written[0]) == gt.SCENE_FIELDS and len(written) == 3

    # dry run: same sampling, URLs as paths, no photo downloads
    dry = gt.gt_scene(str(tmp_path / "map.toml"), str(tmp_path / "dry"), per_label=1, cache_dir=str(tmp_path / "cache"),
                      dry_run=True, get=get, log=lambda *_: None)
    assert {r["source"] for r in dry} == set(by)
    assert all(r["path"].startswith("https://open-images-dataset.s3.amazonaws.com/validation/") for r in dry)
    assert not (tmp_path / "dry" / "bird").exists()


def test_gt_scene_per_label_draws_from_both_subsets_and_is_seeded(tmp_path):
    classes, labels, images, spec = _oid_fixtures()
    (tmp_path / "map.toml").write_text(spec)

    def get(url):
        if url == gt.OID_CLASSES:
            return classes.encode()
        for subset in ("validation", "test"):
            if url == gt.OID_LABELS[subset]:
                return labels[subset].encode()
            if url == gt.OID_IMAGES[subset]:
                return images[subset].encode()
        return b"JPEG"

    kw = dict(per_label=5, cache_dir=str(tmp_path / "cache"), dry_run=True, get=get, log=lambda *_: None)
    rows = gt.gt_scene(str(tmp_path / "map.toml"), str(tmp_path / "a"), **kw)
    r = {r["source"]: r for r in rows}
    assert "openimages:test:img7" in r and r["openimages:test:img7"]["framing"] == "close_up"
    assert [x["source"] for x in gt.gt_scene(str(tmp_path / "map.toml"), str(tmp_path / "b"), **kw)] == [x["source"] for x in rows]


def test_read_scene_map_rejects_bad_labels(tmp_path):
    p = tmp_path / "m.toml"
    p.write_text("[label.Bad]\ngroup='x'\nany=['Bird']\n")
    with pytest.raises(ValueError, match="lower-case"):
        gt.read_scene_map(str(p))
    p.write_text("[label.ok]\ngroup='x'\n")
    with pytest.raises(ValueError, match="any"):
        gt.read_scene_map(str(p))
    p.write_text("[label.ok]\ngroup='x'\nany=['Bird']\n")
    assert gt.read_scene_map(str(p))["label"]["ok"]["group"] == "x"


def test_builtin_scene_map_is_valid():
    from bioscan.cli.main import PROJECT_ROOT
    d = gt.read_scene_map(str(PROJECT_ROOT / "data" / "scene" / "oid-labels.toml"))
    groups = {e["group"] for e in d["label"].values()}
    assert groups == {"wildlife", "landscape", "night", "people", "macro", "architecture", "food", "other"}
    assert set(d["attribute"]) == set(gt.SCENE_ATTRIBUTES)


def test_gt_scene_from_manifest_keeps_matching_files_and_checks_md5(tmp_path):
    import hashlib
    good, bad = b"JPEG-A", b"JPEG-B"
    manifest = tmp_path / "scene-v1.csv"
    rows = [{"path": "", "tier": "scene", "scene": "aurora", "scene_group": "night", "light": "night", "setting": "",
             "framing": "", "license": "https://creativecommons.org/licenses/by/2.0/", "attribution": "A https://flickr/1",
             "source": "openimages:validation:img1", "url": "https://open-images-dataset.s3.amazonaws.com/validation/img1.jpg",
             "md5": hashlib.md5(good).hexdigest()},
            {"path": "/elsewhere/x.jpg", "tier": "scene", "scene": "", "scene_group": "wildlife", "light": "", "setting": "",
             "framing": "", "license": "", "attribution": "", "source": "openimages:test:img2",
             "url": "https://open-images-dataset.s3.amazonaws.com/test/img2.jpg", "md5": hashlib.md5(bad).hexdigest()}]
    gt.write_csv(str(manifest), gt.SCENE_FIELDS, rows)
    out = tmp_path / "set"
    (out / "aurora").mkdir(parents=True)
    (out / "aurora" / "validation_img1.jpg").write_bytes(good)          # already here, md5 matches: kept
    (out / "wildlife").mkdir()
    (out / "wildlife" / "test_img2.jpg").write_bytes(b"stale")           # here but wrong md5: downloaded again
    calls = []

    def get(url):
        calls.append(url)
        return bad if url.endswith("img2.jpg") else good

    fc = FakeClock()
    got = gt.gt_scene_from(str(manifest), str(out), get=get, throttle=gt.Throttle(0.2, fc.clock, fc.sleep), log=lambda *_: None)
    assert calls == ["https://open-images-dataset.s3.amazonaws.com/test/img2.jpg"]
    assert (out / "wildlife" / "test_img2.jpg").read_bytes() == bad
    assert [r["path"] for r in got] == [str((out / "aurora" / "validation_img1.jpg").resolve()),
                                        str((out / "wildlife" / "test_img2.jpg").resolve())]
    assert got[0]["light"] == "night" and got[1]["scene_group"] == "wildlife" and got[0]["md5"] == rows[0]["md5"]
    written = list(csv.DictReader((out / "groundtruth-scene.csv").open()))
    assert list(written[0]) == gt.SCENE_FIELDS and len(written) == 2 and written[0]["path"] == got[0]["path"]

    # a download that does not match the manifest fails the run
    rows[0]["md5"] = "0" * 32
    gt.write_csv(str(manifest), gt.SCENE_FIELDS, rows)
    (out / "aurora" / "validation_img1.jpg").unlink()
    with pytest.raises(ValueError, match="md5"):
        gt.gt_scene_from(str(manifest), str(out), get=get, throttle=gt.Throttle(0.2, fc.clock, fc.sleep), log=lambda *_: None)

    # a CSV without url/md5 is not a manifest
    (tmp_path / "gt.csv").write_text("path,tier,scene\nx,scene,coast\n")
    with pytest.raises(ValueError, match="missing columns"):
        gt.gt_scene_from(str(tmp_path / "gt.csv"), str(out), get=get)
