"""`bioscan cull`: the request it sends, the reducers it runs over the stream, and what it writes
(selection CSV, symlink folders, HTML review page, reduced NDJSON), online (client.run faked) and
offline (--preds)."""
import csv
import json
import os

import pytest
from cull_fixtures import result

from bioscan import profile
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
