import csv
import json
import plistlib

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
