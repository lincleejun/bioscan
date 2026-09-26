"""`bioscan aesthetic score`: the request it sends, the ranking it derives, and the three exports (NDJSON that
`--preds` reads back, CSV best first, an HTML gallery), online (client.run faked) and offline."""
import csv
import json

import pytest
from cull_fixtures import result

from bioscan import profile
from bioscan.cli import aesscore as sc
from bioscan.cli import client
from bioscan.cli.main import main


def photos(tmp_path, names=("a.jpg", "b.jpg", "c.ARW", "d.jpg", "e.jpg", "f.jpg")):
    d = tmp_path / "album"
    d.mkdir()
    for n in names:
        (d / n).write_bytes(b"x")
    return d


def stream(d, out):
    """Six results (one without a score, one reject) and a failed decode."""
    evs = [result(str(d / "a.jpg"), 0.0), result(str(d / "b.jpg"), 0.3), result(str(d / "c.ARW"), 30.0, reasons=["soft_subject"]),
           result(str(d / "d.jpg"), 60.0, label="landscape"), result(str(d / "e.jpg"), 90.0), result(str(d / "f.jpg"), 120.0),
           {"type": "error", "path": str(d / "g.jpg"), "product": None, "message": "decode: broken"},
           {"type": "done", "schema": 1, "ok": 6, "failed": 1, "elapsed_ms": 1.0}]
    for ev, s in zip(evs, (0.7, 0.4, 0.5, 0.9, None, 0.6)):
        ev["products"]["aesthetics"] = {"score": s, "general": s, "personal": None, "head_id": "h"} if s is not None \
            else {"score": None, "general": None, "personal": None, "head_id": None, "note": "no head"}
    evs[2]["products"]["jpg"] = {"path": str(out.parent / "aes-files" / "c-0.jpg"), "width": 1, "height": 1}
    return evs


def test_score_exports_json_csv_html_and_reads_its_own_ndjson_back(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sc, "load_config", lambda: profile.builtin())
    d = photos(tmp_path)
    out = tmp_path / "x" / "aes"
    sent = {}

    def fake_run(payload, url):
        sent["payload"] = payload
        return [json.dumps(e).encode() for e in stream(d, out)]
    monkeypatch.setattr(client, "run", fake_run)

    rc = main(["aesthetic", "score", str(d), "--export", "json,csv,html", "--out", str(out), "--thumb-edge", "640"])
    assert rc == 1                                                       # one photo failed
    body = sent["payload"]
    assert body["want"][-1] == "jpg" and body["options"]["jpg"] == {"out_dir": str(out.parent / "aes-files"), "edge": 3072}
    assert "aesthetics" in body["want"] and len(body["inputs"]) == 6

    with open(f"{out}.csv") as f:
        rows = list(csv.DictReader(f))
    assert [r["path"].rsplit("/", 1)[1] for r in rows] == ["d.jpg", "a.jpg", "f.jpg", "c.ARW", "b.jpg", "e.jpg", "g.jpg"]
    assert [r["stars"] for r in rows[:5]] == ["5", "4", "3", "2", "1"] and rows[5]["stars"] == "" == rows[5]["score"]
    assert rows[3]["reject_reasons"] == "soft_subject" and rows[6]["reject_reasons"].startswith("failed:")
    assert rows[0]["scene"] == "landscape" and rows[0]["rank"] == "1"

    page = (out.parent / "aes.html").read_text()
    assert "aes-files/c-0.jpg" in page and '"f":"e.jpg"' in page and "const CUTS=[0.9, 0.7, 0.6, 0.5]" in page
    assert f'"p":"{d / "e.jpg"}"' in page and f'const ROOT="{d}"' in page                # marks and export need the path
    text = capsys.readouterr().out
    assert "7 photos: 5 scored, 1 without a score, 1 failed" in text and "note: no head" in text
    assert f"-> {out}.ndjson" in text and f"-> {out}.html" in text

    # offline: the NDJSON it wrote is a --preds input; html only, no service
    monkeypatch.setattr(client, "run", lambda *a: pytest.fail("no service call with --preds"))
    rc = main(["aesthetic", "score", "--preds", f"{out}.ndjson", "--export", "html", "--out", str(tmp_path / "again")])
    assert rc == 1 and (tmp_path / "again.html").exists() and not (tmp_path / "again.csv").exists()


def test_shrink_writes_a_thumbnail_next_to_the_copy_and_keeps_the_copy(tmp_path):
    from PIL import Image
    p = tmp_path / "a-0.jpg"
    Image.new("RGB", (400, 300)).save(p)
    ev = result("/x/a.ARW", 0.0)
    ev["products"]["jpg"] = {"path": str(p), "width": 400, "height": 300}
    assert sc.shrink([ev], 100) == 1 and Image.open(p).size == (400, 300)
    assert sc.thumb_of(str(p)) == str(tmp_path / "a-0-t.jpg") and Image.open(tmp_path / "a-0-t.jpg").size == (100, 75)
    assert sc.shrink([ev], 100) == 0                                     # thumbnail there: untouched
    assert sc.shrink([ev], 400) == 0                                     # copy not larger than the edge: none needed
    # the page shows the thumbnail in the grid and the copy in the lightbox; a jpg original shows itself
    rows = [{"path": "/x/a.ARW", "jpg": str(p), "score": 0.5, "stars": 3, "scene": None, "reject_reasons": [],
             "species": None, "common": None, "level": None, "lineage": []},
            {"path": str(tmp_path / "b.jpg"), "jpg": None, "score": None, "stars": None, "scene": "landscape",
             "reject_reasons": ["dark"], "species": "Buteo", "common": None, "level": "genus", "lineage": ["Aves", "Buteo"]}]
    a, b = sc.page_rows(rows, tmp_path, "/x")
    assert a["t"] == "a-0-t.jpg" and a["l"] == "a-0.jpg" and "o" not in a and a["tx"] == [] and a["p"] == "/x/a.ARW"
    assert b["t"] == "b.jpg" and "l" not in b and "o" not in b and b["tx"] == ["Aves", "Buteo"] and "s" not in b


def test_apply_copies_keeps_and_moves_drops_with_sidecars(tmp_path, capsys):
    src = tmp_path / "card"
    src.mkdir()
    for n in ("a.ARW", "a.xmp", "b.ARW", "c.ARW"):
        (src / n).write_bytes(b"x")
    dec = tmp_path / "bioscan-decisions.json"
    dec.write_text(json.dumps({"schema": 1, "keep": [str(src / "a.ARW"), str(src / "gone.ARW")],
                               "drop": [str(src / "b.ARW")], "merge": {}}))
    with pytest.raises(SystemExit, match="--keep-to"):
        main(["aesthetic", "apply", str(dec)])
    assert main(["aesthetic", "apply", str(dec), "--keep-to", str(tmp_path / "keep"), "--drop-to", str(tmp_path / "drop"),
                 "--dry-run"]) == 1                                                       # gone.ARW is missing
    assert not (tmp_path / "keep").exists() and (src / "b.ARW").exists()
    out = capsys.readouterr()
    assert "keep: 1 of 2 copied to" in out.out and "(dry run)" in out.out and "missing:" in out.err
    assert main(["aesthetic", "apply", str(dec), "--keep-to", str(tmp_path / "keep"), "--drop-to", str(tmp_path / "drop")]) == 1
    assert sorted(x.name for x in (tmp_path / "keep").iterdir()) == ["a.ARW", "a.xmp"] and (src / "a.ARW").exists()
    assert [x.name for x in (tmp_path / "drop").iterdir()] == ["b.ARW"] and not (src / "b.ARW").exists()
    assert (src / "c.ARW").exists()                                                       # unmarked: untouched
    # a second run finds the keep already there and skips it, the drop already moved (missing)
    assert main(["aesthetic", "apply", str(dec), "--keep-to", str(tmp_path / "keep"), "--drop-to", str(tmp_path / "drop")]) == 1
    assert "already at" in capsys.readouterr().err and (tmp_path / "keep" / "a.ARW").read_bytes() == b"x"


def test_export_spec_and_arguments_are_checked(tmp_path):
    assert sc.parse_export("html, json,json") == ["html", "json"]
    with pytest.raises(SystemExit, match="--export takes"):
        sc.parse_export("xlsx")
    with pytest.raises(SystemExit, match="give photo files"):
        main(["aesthetic", "score"])
    with pytest.raises(SystemExit, match="no images found"):
        main(["aesthetic", "score", str(tmp_path)])


def test_rows_rank_missing_scores_last_and_stars_are_quintiles():
    evs = [result(f"/p/{i}.jpg", float(i)) for i in range(10)]
    for i, ev in enumerate(evs):
        ev["products"]["aesthetics"] = {"score": i / 10}
    evs.append(result("/p/none.jpg", 0.0))
    evs[-1]["products"]["aesthetics"] = {"score": float("nan")}
    rows = sc.rows_of(evs)
    assert rows[0]["path"] == "/p/9.jpg" and rows[-1]["path"] == "/p/none.jpg" and rows[-1]["stars"] is None
    assert [r["stars"] for r in rows[:-1]] == [5, 5, 4, 4, 3, 3, 2, 2, 1, 1]
    assert sc.cuts(rows) == [0.8, 0.6, 0.4, 0.2]


def test_species_names_the_surest_box_and_reaches_csv_and_page(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "load_config", lambda: profile.builtin())
    d = photos(tmp_path, ("a.jpg", "b.jpg"))
    sent = {}

    def cand(sci, common, posterior, tax=("Animalia", "Chordata", "Mammalia", "Artiodactyla", "Cervidae", "Rangifer")):
        return {"scientific": sci, "common": common, "taxonomy": [*tax, sci], "p_visual": posterior, "p_geo": None,
                "posterior": posterior}
    evs = [result(str(d / "a.jpg"), 0.0), result(str(d / "b.jpg"), 1.0),
           {"type": "done", "schema": 1, "ok": 2, "failed": 0, "elapsed_ms": 1.0}]
    evs[0]["products"]["aesthetics"] = {"score": 0.6}
    evs[1]["products"]["aesthetics"] = {"score": 0.8}
    # two boxes: the surer one names the photo; a genus-level box shows its genus, no common name
    evs[0]["products"]["identify"] = {"gate": {"class": "mammal"}, "boxes": [
        {"id": 0, "kind": "mammal", "species": {"list": "mdd", "level": "species",
                                                "top": [cand("Rangifer tarandus", "Caribou", 0.9)]}},
        {"id": 1, "kind": "mammal", "species": {"list": "mdd", "level": "species",
                                                "top": [cand("Alces alces", "Moose", 0.4)]}}]}
    evs[1]["products"]["identify"] = {"gate": {"class": "mammal"}, "boxes": [
        {"id": 0, "kind": "mammal", "species": {"list": "mdd", "level": "genus",
                                                "top": [cand("Rangifer tarandus", "Caribou", 0.5)]}}]}

    def fake_run(payload, url):
        sent["payload"] = payload
        return [json.dumps(e).encode() for e in evs]
    monkeypatch.setattr(client, "run", fake_run)
    out = tmp_path / "aes"
    assert main(["aesthetic", "score", str(d), "--species", "--export", "csv,html", "--out", str(out), "--no-thumbs"]) == 0
    assert sent["payload"]["options"]["identify"]["species"] is True
    with open(f"{out}.csv") as f:
        rows = list(csv.DictReader(f))
    assert [(r["species"], r["common"], r["level"]) for r in rows] == [("Rangifer", "", "genus"),
                                                                        ("Rangifer tarandus", "Caribou", "species")]
    page = (tmp_path / "aes.html").read_text()
    assert '"sp":"Rangifer tarandus","cn":"Caribou","lv":"species"' in page and '"sp":"Rangifer"' in page
    # without --species the request keeps the album profile's species=false and the columns stay empty
    assert main(["aesthetic", "score", str(d), "--export", "csv", "--out", str(out)]) == 0
    assert sent["payload"]["options"]["identify"]["species"] is False
