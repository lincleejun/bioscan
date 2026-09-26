"""`bioscan lr`: the latest.json rules (stars, keywords, group) and the open/install commands.
Synthetic NDJSON only; everything is written under tmp_path and Lightroom is never launched."""
import json
from datetime import datetime

import pytest

from bioscan import contract
from bioscan.cli import lr
from bioscan.cli.main import PROJECT_ROOT, main

OWL = ["Animalia", "Chordata", "Aves", "Strigiformes", "Strigidae", "Megascops", "Megascops kennicottii"]


def cand(sci, common=None, taxonomy=OWL):
    return contract.candidate(sci, common, taxonomy, 0.9, None, 0.9)


def box(score=0.9, sharp=0.5, kind="bird", level=None, top=(), species=True):
    b = contract.box(0, [0, 0, 1, 1], score, kind, contract.quality(sharp, 0.0))
    if species:
        b["species"] = contract.species("avilist-2025", level, list(top)) if level else None
    return b


def result(path, *boxes):
    return contract.result(path, "0" * 64, {"width": 1, "height": 1, "orientation": 1}, {},
                           {"identify": contract.identify(contract.gate("animal", {}), list(boxes))}, {})


def write_preds(path, events):
    path.write_text("".join(json.dumps(e) + "\n" for e in events))
    return path


def test_score_is_max_sharpness_and_zero_without_boxes():
    assert lr.photo(result("/a", box(sharp=0.2), box(sharp=0.7)))["score"] == 0.7
    assert lr.photo(result("/a"))["score"] == 0.0


def stars_of(scores):
    photos = [{"path": p, "score": s, "level": "unconfirmed"} for p, s in scores]
    lr.stars(photos)
    return {p["path"]: p["stars"] for p in photos}


def test_stars_one_photo_gets_five_stars():
    assert stars_of([("/a", 0.9)]) == {"/a": 5}      # #51: the page's rule, the top fifth is 5


def test_stars_five_photos_one_to_five():
    got = stars_of([("/e", 0.5), ("/a", 0.1), ("/c", 0.3), ("/b", 0.2), ("/d", 0.4)])
    assert got == {"/a": 1, "/b": 2, "/c": 3, "/d": 4, "/e": 5}


def test_stars_seven_with_ties_sorted_by_score_then_path():
    # #51: best first by (-score, path) like the page: /g .9, /d .3, /e .3, /f .3, /b .2, /c .2, /a .1 -> 5 5 4 3 3 2 1
    got = stars_of([("/f", 0.3), ("/c", 0.2), ("/g", 0.9), ("/e", 0.3), ("/a", 0.1), ("/d", 0.3), ("/b", 0.2)])
    assert got == {"/g": 5, "/d": 5, "/e": 4, "/f": 3, "/b": 3, "/c": 2, "/a": 1}


def test_stars_equal_the_page_stars():
    scores = [(f"/p/{i}", (4 + i) / 10) for i in range(7)]
    got = stars_of(scores)
    assert [got[p] for p, _ in sorted(scores, key=lambda x: -x[1])] == [5, 5, 4, 3, 3, 2, 1]


def test_photos_without_boxes_get_zero_stars_and_are_not_ranked():
    photos = [{"path": "/none", "score": 0.0, "level": "none"}, {"path": "/a", "score": 0.1, "level": "species"}]
    lr.stars(photos)
    assert [p["stars"] for p in photos] == [0, 5]


def test_keywords_per_level():
    sp = box(level="species", top=[cand("Megascops kennicottii", "Western Screech-Owl")])
    sci_only = box(level="species", top=[cand("Megascops kennicottii")])
    genus = box(level="genus", top=[cand("Megascops kennicottii")])
    family = box(level="family", top=[cand("Megascops kennicottii")])
    short = box(level="genus", top=[cand("Megascops kennicottii", taxonomy=OWL[:5])])
    unconf = box(kind="mammal", level="unconfirmed", top=[cand("Megascops kennicottii")])
    null_sp = box(kind="other_animal")
    no_top = box(level="species")
    assert lr.keywords(result("/a", sp, sci_only, genus, family)) == [
        ["bioscan", "bird", "Western Screech-Owl"], ["bioscan", "bird", "Megascops kennicottii"],
        ["bioscan", "bird", "Megascops"], ["bioscan", "bird", "Strigidae"]]
    assert lr.keywords(result("/a", short)) == [["bioscan", "bird"]]
    assert lr.keywords(result("/a", unconf, null_sp, no_top)) == [
        ["bioscan", "mammal"], ["bioscan", "other_animal"], ["bioscan", "bird"]]


def test_keywords_dedup_within_image_keeps_order():
    a = box(kind="mammal")
    b = box(level="species", top=[cand("Megascops kennicottii", "Western Screech-Owl")])
    assert lr.keywords(result("/a", a, b, box(kind="mammal"), dict(b))) == [
        ["bioscan", "mammal"], ["bioscan", "bird", "Western Screech-Owl"]]
    assert lr.keywords(result("/a")) == []


def test_group_species_box_with_highest_detection_score():
    low = box(score=0.3, sharp=0.9, level="species", top=[cand("Megascops kennicottii", "Western Screech-Owl")])
    high = box(score=0.8, sharp=0.1, level="species", top=[cand("Bubo virginianus")])
    genus = box(score=0.99, level="genus", top=[cand("Megascops kennicottii")])
    p = lr.photo(result("/a", genus, low, high))
    assert (p["group"], p["species"], p["level"]) == ("Bubo virginianus", "Bubo virginianus", "species")
    p = lr.photo(result("/a", genus, low))
    assert (p["group"], p["species"], p["level"]) == ("Western Screech-Owl", "Megascops kennicottii", "species")


def test_group_review_when_boxes_but_no_species():
    genus = box(score=0.9, sharp=0.1, level="genus", top=[cand("Megascops kennicottii")])
    fam = box(score=0.5, sharp=0.9, level="family", top=[cand("Megascops kennicottii")])
    p = lr.photo(result("/a", fam, genus))
    assert (p["group"], p["species"], p["level"]) == (lr.REVIEW, "", "genus")
    p = lr.photo(result("/a", box(score=0.9, species=False), fam))
    assert (p["group"], p["species"], p["level"]) == (lr.REVIEW, "", "unconfirmed")
    assert lr.REVIEW == "待确认" and lr.NONE == "无动物"


def test_group_none_without_boxes():
    p = lr.photo(result("/a"))
    assert (p["group"], p["species"], p["level"], p["keywords"]) == (lr.NONE, "", "none", [])


def test_results_skip_meta_progress_error_done(tmp_path):
    preds = write_preds(tmp_path / "p.ndjson", [
        {"type": "meta", "schema": 1}, contract.progress("identify", 0, 1), result("/a", box()),
        contract.error("/b", None, "decode failed"), contract.done(1, 1, 5.0)])
    assert [e["path"] for e in lr.results(preds)] == ["/a"]


def test_open_writes_latest_atomically_and_prints_counts(tmp_path, capsys):
    sp = box(sharp=0.6, level="species", top=[cand("Megascops kennicottii", "Western Screech-Owl")])
    preds = write_preds(tmp_path / "preds.ndjson", [
        {"type": "meta", "schema": 1}, result("/p/1.ARW", sp), result("/p/2.ARW", box(sharp=0.2)),
        result("/p/3.ARW"), contract.done(3, 0, 5.0)])
    target = tmp_path / "lr" / "latest.json"
    assert main(["lr", "open", str(preds), "--no-launch", "--to", str(target)]) == 0
    assert [p.name for p in target.parent.iterdir()] == ["latest.json"]      # no temp file left behind
    doc = json.loads(target.read_text())
    run = datetime.fromisoformat(doc["run"])
    assert doc["schema"] == 1 and doc["source"] == str(preds) and run.tzinfo and "." in doc["run"]
    assert doc["photos"][0] == {"path": "/p/1.ARW", "stars": 5, "score": 0.6,      # #51: best of 2 -> 5, next -> 3
                                "keywords": [["bioscan", "bird", "Western Screech-Owl"]],
                                "group": "Western Screech-Owl", "species": "Megascops kennicottii", "level": "species"}
    assert [(p["path"], p["stars"], p["group"]) for p in doc["photos"][1:]] == [
        ("/p/2.ARW", 3, lr.REVIEW), ("/p/3.ARW", 0, lr.NONE)]
    out = capsys.readouterr().out
    assert "3 photos" in out and "2 starred" in out and str(target) in out


def test_open_fails_on_missing_or_resultless_preds(tmp_path, capsys):
    target = tmp_path / "latest.json"
    assert main(["lr", "open", str(tmp_path / "nope.ndjson"), "--no-launch", "--to", str(target)]) == 1
    assert "nope.ndjson" in capsys.readouterr().err
    empty = write_preds(tmp_path / "empty.ndjson", [{"type": "meta"}, contract.done(0, 0, 1.0)])
    assert main(["lr", "open", str(empty), "--no-launch", "--to", str(target)]) == 1
    assert "no result" in capsys.readouterr().err
    assert not target.exists()


def test_open_default_target_is_patchable_and_never_launches_with_no_launch(tmp_path, monkeypatch):
    monkeypatch.setattr(lr, "default_target", lambda: tmp_path / "d" / "latest.json")
    monkeypatch.setattr(lr.subprocess, "run", lambda *a, **k: pytest.fail("launched Lightroom"))
    preds = write_preds(tmp_path / "p.ndjson", [result("/a", box())])
    assert main(["lr", "open", str(preds), "--no-launch"]) == 0
    assert (tmp_path / "d" / "latest.json").exists()


def test_open_launches_lightroom_on_darwin(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(lr.sys, "platform", "darwin")
    monkeypatch.setattr(lr.subprocess, "run", lambda cmd, **k: calls.append(cmd))
    preds = write_preds(tmp_path / "p.ndjson", [result("/a", box())])
    assert main(["lr", "open", str(preds), "--to", str(tmp_path / "latest.json")]) == 0
    assert calls == [["open", "-a", "Adobe Lightroom Classic"]]


def test_install_symlinks_then_reports_existing(tmp_path, capsys):
    mods = tmp_path / "Modules"
    assert main(["lr", "install", "--modules", str(mods)]) == 0
    link = mods / "bioscan.lrplugin"
    assert lr.PLUGIN == PROJECT_ROOT / "extensions" / "lightroom" / "bioscan.lrplugin"
    assert link.is_symlink() and link.readlink() == lr.PLUGIN
    capsys.readouterr()
    assert main(["lr", "install", "--modules", str(mods)]) == 0
    assert "already" in capsys.readouterr().out


def test_install_copy(tmp_path, monkeypatch):
    src = tmp_path / "src.lrplugin"
    src.mkdir()
    (src / "Info.lua").write_text("return {}")
    monkeypatch.setattr(lr, "PLUGIN", src)
    assert main(["lr", "install", "--modules", str(tmp_path / "M"), "--copy"]) == 0
    dest = tmp_path / "M" / "bioscan.lrplugin"
    assert not dest.is_symlink() and (dest / "Info.lua").read_text() == "return {}"


def test_score_prefers_the_aesthetic_score_when_the_run_has_one():
    ev = {"type": "result", "path": "/p/a.jpg", "products": {
        "identify": {"gate": {"class": "bird"}, "boxes": [
            {"id": 0, "xyxy": [0, 0, 1, 1], "score": 0.9, "kind": "bird", "quality": {"sharpness": 0.4, "exposure": 0}}]},
        "aesthetics": {"score": 0.73, "general": 0.73, "personal": None, "head_id": "eva:abc"}}}
    assert lr.photo(ev)["score"] == 0.73
    ev["products"]["aesthetics"]["score"] = None          # head missing: back to sharpness
    assert lr.photo(ev)["score"] == 0.4
