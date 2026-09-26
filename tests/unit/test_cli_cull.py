"""`bioscan cull`: the request it sends, the reducers it runs over the stream, and what it writes
(selection CSV, symlink folders, HTML review page, reduced NDJSON), online (client.run faked) and
offline (--preds)."""
import csv
import json
import os
import xml.etree.ElementTree as ET

import pytest
from aesthetic_helpers import xmp
from cull_fixtures import result

from bioscan import aesthetic, profile
from bioscan.cli import client
from bioscan.cli import cull as cc
from bioscan.cli.main import main, parser


def photos(tmp_path, names=("a.jpg", "b.jpg", "c.ARW", "d.jpg")):
    d = tmp_path / "album"
    d.mkdir()
    for n in names:
        (d / n).write_bytes(b"x")
    return d


def stream(d):
    """What the service would send for photos(): a-b a burst, c a reject, d another category."""
    evs = [result(str(d / "a.jpg"), 0.0, blur=0.25), result(str(d / "b.jpg"), 0.3, blur=0.2),
           result(str(d / "c.ARW"), 30.0, reasons=["soft_subject"], v=(0.0, 1.0)),
           result(str(d / "d.jpg"), 60.0, label="landscape", v=(0.0, 0.0, 1.0)),
           {"type": "error", "path": str(d / "e.jpg"), "product": None, "message": "decode: broken"},
           {"type": "done", "schema": 1, "ok": 4, "failed": 1, "elapsed_ms": 1.0}]
    evs[3]["products"]["jpg"] = {"path": str(d.parent / "page-files" / "d-0.jpg"), "width": 1, "height": 1}
    return evs


def test_request_runs_the_album_stages_and_asks_for_thumbnails(tmp_path, monkeypatch):
    monkeypatch.setattr(cc, "load_config", lambda: profile.builtin())
    d = photos(tmp_path)
    a = parser().parse_args(["cull", str(d), "--html", str(tmp_path / "page.html"), "--per-category", "3"])
    res = cc.resolve(a)
    body = cc.build_request(a, res)
    assert body["want"][-1] == "jpg" and set(res.want) <= set(body["want"])
    assert body["options"]["jpg"] == {"out_dir": str(tmp_path / "page-files")}
    assert body["options"]["identify"]["species"] is False and len(body["inputs"]) == 4
    assert cc.reducer_run(res)["select"]["per_category"] == 3
    a = parser().parse_args(["cull", str(d), "--html", str(tmp_path / "p.html"), "--no-thumbs"])
    assert "jpg" not in cc.build_request(a, cc.resolve(a))["want"]


def test_cull_writes_csv_links_html_and_json(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cc, "load_config", lambda: profile.builtin())
    d = photos(tmp_path)
    sent = {}

    def fake_run(payload, url):
        sent["payload"] = payload
        return [json.dumps(e).encode() for e in stream(d)]
    monkeypatch.setattr(client, "run", fake_run)
    out = tmp_path / "out"
    args = ["cull", str(d), "--csv", str(out / "sel.csv"), "--link-dir", str(out / "links"), "--html",
            str(tmp_path / "page.html"), "--json", str(out / "cull.ndjson")]
    assert main(args) == 1                                          # one photo failed
    text = capsys.readouterr().out
    assert "5 photos: 2 pick, 0 spare, 1 duplicate, 1 reject, 1 failed" in text and "bursts: 1 (2 frames)" in text
    rows = {r["path"]: r for r in csv.DictReader(open(out / "sel.csv", encoding="utf-8"))}
    assert rows[str(d / "b.jpg")]["status"] == "pick" and rows[str(d / "a.jpg")]["status"] == "duplicate"
    assert rows[str(d / "c.ARW")]["reasons"] == "soft_subject" and rows[str(d / "e.jpg")]["status"] == "failed"
    assert os.readlink(out / "links" / "wildlife" / "b.jpg") == str(d / "b.jpg")
    assert os.readlink(out / "links" / "landscape" / "d.jpg") == str(d / "d.jpg")
    page = (tmp_path / "page.html").read_text()
    assert 'id="cat-wildlife"' in page and 'id="cat-landscape"' in page and "soft_subject" in page
    assert 'src="page-files/d-0.jpg"' in page and 'src="album/b.jpg"' in page    # jpg copy, else the photo
    assert "decode: broken" in page and "b0001" in page
    lines = [json.loads(x) for x in open(out / "cull.ndjson", encoding="utf-8")]
    assert lines[0]["type"] == "meta" and lines[0]["profile"] == "album" and set(lines[0]["reducers"]) == {"burst", "select"}
    assert lines[1]["products"]["select"]["status"] == "duplicate" and lines[-1]["type"] == "done"
    # again, offline from the saved NDJSON: the same records; no link is replaced or duplicated
    (out / "links" / "wildlife" / "other.jpg").write_bytes(b"mine")
    assert main(["cull", "--preds", str(out / "cull.ndjson"), "--csv", str(out / "again.csv"),
                 "--link-dir", str(out / "links")]) == 1
    assert "0 new links" in capsys.readouterr().out
    assert list(csv.DictReader(open(out / "again.csv", encoding="utf-8"))) == list(rows.values())
    assert sent["payload"]["want"][-1] == "jpg"


def test_xmp_sidecars_for_picks_and_rejects_never_over_an_existing_one(tmp_path, capsys):
    d = photos(tmp_path)
    nd = tmp_path / "run.ndjson"
    nd.write_text("".join(json.dumps(e) + "\n" for e in stream(d)))
    (d / "d.xmp").write_text("editor settings")                     # the pick d.jpg already has a sidecar
    assert main(["cull", "--preds", str(nd), "--xmp"]) == 1
    assert "xmp: 2 sidecars written, 1 left alone (a sidecar exists)" in capsys.readouterr().out
    assert (d / "d.xmp").read_text() == "editor settings"
    assert not (d / "a.xmp").exists() and (d / "a.jpg").read_bytes() == b"x"   # duplicate: nothing; photo untouched
    def props(text):                                                # the properties, as the ratings reader finds them
        root = ET.fromstring(text.split("?>", 1)[1].rsplit("<?xpacket", 1)[0])
        return (aesthetic._xmp_value(root, "xmp", "Rating"), aesthetic._xmp_value(root, "xmp", "Label"),
                next(root.iter(f"{{{aesthetic.NS['rdf']}}}Description")).attrib.get(f"{{{cc.XMP_NS}}}reasons"))
    assert props((d / "b.xmp").read_text()) == ("3", None, None)
    assert props((d / "c.xmp").read_text()) == (None, "Red", "soft_subject")      # a reject: no stars
    assert props(cc.xmp_packet({"status": "spare", "reasons": []}))[0] == "2"
    # the owner's-ratings reader skips a cull-written sidecar; a hand-written one next to it still counts
    assert aesthetic.read_xmp_rating(str(d / "b.jpg")) is None
    assert aesthetic.parse_xmp((d / "c.xmp").read_text()) is None
    (d / "own.jpg").write_bytes(b"x")
    (d / "own.xmp").write_text(xmp(rating=3))
    assert aesthetic.read_xmp_rating(str(d / "own.jpg")) == ({"rating": 3.0, "pick": 0, "label": ""}, "sidecar")
    # again: every sidecar exists now; a photo not on this disk is counted, not written
    (d / "b.jpg").unlink()
    assert main(["cull", "--preds", str(nd), "--xmp"]) == 1
    assert "xmp: 0 sidecars written, 2 left alone (a sidecar exists), 1 photos not found" in capsys.readouterr().out


def test_xmp_packet_records_the_stars_cull_wrote():
    stars = {s: aesthetic._xmp_value(ET.fromstring(cc.xmp_packet({"status": s, "reasons": ["blur"]})
                                                   .split("?>", 1)[1].rsplit("<?xpacket", 1)[0]), "bioscan", "stars")
             for s in ("pick", "spare", "reject")}
    assert stars == {"pick": "3", "spare": "2", "reject": "0"}
    assert cc.xmp_packet({"status": "duplicate", "reasons": []}) is None


def rerated(status, rating):
    """A cull sidecar the owner re-rated in an editor that keeps the bioscan properties."""
    text = cc.xmp_packet({"status": status, "reasons": ["blur"]})
    if 'xmp:Rating="' in text:
        return text.replace(f'xmp:Rating="{cc.XMP_STARS[status]}"', f'xmp:Rating="{rating}"')
    return text.replace('xmp:Label=', f'xmp:Rating="{rating}" xmp:Label=')


def test_parse_xmp_skips_a_cull_sidecar_only_while_it_keeps_culls_stars():
    for status in ("pick", "spare", "reject"):                     # unchanged: cull's stars, not the owner's
        assert aesthetic.parse_xmp(cc.xmp_packet({"status": status, "reasons": ["blur"]})) is None
    assert aesthetic.parse_xmp(rerated("pick", 3)) is None
    assert aesthetic.parse_xmp(rerated("reject", 0)) is None         # an explicit 0 is still "no stars"
    assert aesthetic.parse_xmp(rerated("pick", 4)) == {"rating": 4.0, "pick": 0, "label": ""}
    assert aesthetic.parse_xmp(rerated("spare", 5))["rating"] == 5.0
    assert aesthetic.parse_xmp(rerated("reject", 4)) == {"rating": 4.0, "pick": 0, "label": "Red"}
    assert aesthetic.parse_xmp(rerated("reject", -1)) == {"rating": aesthetic.REJECT_GRADE, "pick": -1, "label": "Red"}
    old = cc.xmp_packet({"status": "pick", "reasons": []}).replace('bioscan:stars="3"', "")
    assert "bioscan:stars" not in old
    assert aesthetic.parse_xmp(old.replace('xmp:Rating="3"', 'xmp:Rating="5"')) is None   # before bioscan:stars: skipped whole


def test_link_name_clash_gets_a_suffix(tmp_path):
    (tmp_path / "x").mkdir()
    (tmp_path / "y").mkdir()
    for p in ("x/a.jpg", "y/a.jpg"):
        (tmp_path / p).write_bytes(b"1")
    recs = [{"path": str(tmp_path / p), "keep": True, "category": "wildlife"} for p in ("x/a.jpg", "y/a.jpg")]
    assert cc.write_links(recs, str(tmp_path / "l")) == 2
    assert os.readlink(tmp_path / "l" / "wildlife" / "a-2.jpg") == str(tmp_path / "y" / "a.jpg")


def test_usage_errors(tmp_path):
    with pytest.raises(SystemExit, match="one of them"):
        main(["cull"])
    with pytest.raises(SystemExit, match="one of them"):
        main(["cull", str(tmp_path), "--preds", "x.ndjson"])


def test_warnings_for_profiles_without_the_cull_stages():
    got = cc.warnings_for(["identify"])
    assert len(got) == 3 and "no quality stage" in got[0]
    assert cc.warnings_for(["identify", "embed", "quality", "scene"]) == []
