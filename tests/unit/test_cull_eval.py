"""The album tier: scripts/cull_synth.py on tiny synthetic photos, the quality rules on what it makes,
and the harness's album metrics (reject precision / recall per reason, keepers lost, burst pairwise
F1, scene accuracy) on crafted results, through report.json, compare and the album budget."""
import csv
import hashlib
import importlib.util
from pathlib import Path

import pytest
from cull_fixtures import box, photo, result
from PIL import Image

from bioscan import profile
from bioscan.cli import bench
from bioscan.plugins.quality import stage as q

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("cull_synth", ROOT / "scripts" / "cull_synth.py")
synth = importlib.util.module_from_spec(spec)
spec.loader.exec_module(synth)
ALBUM = profile.resolve(profile.builtin(), "album").reducer_run()


@pytest.fixture(scope="module")
def made(tmp_path_factory):
    src = tmp_path_factory.mktemp("src")
    sources = []
    for i in range(4):
        im, b = photo(i, w=240, h=180, bokeh=i == 3)
        im.save(src / f"p{i}.jpg", quality=95)
        sources.append({"path": str(src / f"p{i}.jpg"), "box": b, "scene": "wildlife"})
    sources.append({"path": str(src / "p0.jpg"), "box": (0.0, 0.2, 0.3, 0.6)})       # touches the edge: skipped
    out = tmp_path_factory.mktemp("set")
    return sources, out, synth.build(sources, out, seed=7, burst_every=2)


def test_the_set_is_labelled_and_deterministic(made, tmp_path):
    sources, out, rows = made
    assert {r["source"] for r in rows} == {s["path"] for s in sources[:4]}
    by = {(Path(r["path"]).name): r for r in rows}
    assert by["s000-original.jpg"]["keep"] == 1 and by["s000-original.jpg"]["reject_reasons"] == ""
    assert {by[f"s001-{v}.jpg"]["reject_reasons"] for v in ("blur", "smear")} == {"soft_subject"}
    assert by["s001-shake.jpg"]["reject_reasons"] == "motion_or_defocus"
    assert (by["s001-over.jpg"]["reject_reasons"], by["s001-under.jpg"]["reject_reasons"]) == ("overexposed", "underexposed")
    assert by["s001-small.jpg"]["reject_reasons"] == "subject_too_small" and by["s001-cut.jpg"]["keep"] == 0
    bursts = [r for r in rows if r["burst_id"]]
    assert {r["burst_id"] for r in bursts} == {"s000", "s002"} and len(bursts) == 8
    assert [r["taken_at"][-12:-6] for r in bursts[:4]] == ["00.000", "00.200", "00.400", "00.600"]
    assert len({r["taken_at"][:19] for r in rows if not r["burst_id"]}) == len([r for r in rows if not r["burst_id"]])
    with open(out / "groundtruth-album.csv", encoding="utf-8") as f:
        assert [r["path"] for r in csv.DictReader(f)] == [r["path"] for r in rows]
    again = synth.build(sources, tmp_path, seed=7, burst_every=2)

    def digest(p):
        return hashlib.sha256(Path(p).read_bytes()).hexdigest()
    assert [digest(r["path"]) for r in again] == [digest(r["path"]) for r in rows]


def test_the_script_command_line(made, tmp_path, capsys):
    sources = made[0]
    (tmp_path / "src.csv").write_text("path,x0,y0,x1,y1\n" + f"{sources[0]['path']},{','.join(map(str, sources[0]['box']))}\n")
    assert synth.main(["--boxes", str(tmp_path / "src.csv"), "--out", str(tmp_path / "o"), "--scene", "wildlife",
                       "--burst-every", "0"]) == 0
    with open(tmp_path / "o" / "groundtruth-album.csv", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 8 and {r["scene"] for r in rows} == {"wildlife"} and "8 photos from 1 sources" in capsys.readouterr().out
    preds = tmp_path / "p.ndjson"
    preds.write_text('{"type": "result", "path": "/a.jpg", "products": {"identify": {"boxes": [{"xyxy": [0.1, 0.1, 0.2, 0.2], '
                     '"score": 0.3}, {"xyxy": [0.3, 0.3, 0.6, 0.6], "score": 0.9}]}}}\n{"type": "done"}\n')
    assert synth.sources_from_preds(preds) == [{"path": "/a.jpg", "box": [0.3, 0.3, 0.6, 0.6], "scene": ""}]


def test_the_rules_find_what_the_script_did(made):
    """On the tiny set, with the subject box where the script put it (a stand-in for the detector),
    every degradation gets its own reason and the originals none. The bokeh source (p3) turns a
    soft subject into motion_or_defocus: nothing else in its frame is sharp (documented)."""
    sources, _, rows = made
    for r in rows:
        if r["variant"] == "small":
            continue
        src = next(s for s in sources if s["path"] == r["source"])
        b = src["box"]
        with Image.open(r["path"]) as f:
            im = f.convert("RGB")
        if r["variant"] == "cut":
            with Image.open(src["path"]) as f:
                b = synth.cut(f.convert("RGB"), src["box"])[1]
        got = q.assess(im, [box(b)], {"bird": 0.9})["reject_reasons"]
        want = r["reject_reasons"]
        if Path(src["path"]).name == "p3.jpg" and want == "soft_subject":
            want = "motion_or_defocus"
        assert ";".join(got) == want, (r["path"], got)


def truth(path, keep=1, reasons="", burst="", scene="wildlife"):
    return {"path": path, "tier": "album", "keep": str(keep), "reject_reasons": reasons, "burst_id": burst,
            "scene": scene, "scientific": "", "kind": ""}


def album_run():
    """Crafted results for 8 photos and their truth: 2 bursts of 2 (one burst split by a 3 s gap),
    a soft reject found, an overexposed one missed, a keeper wrongly cut, a landscape labelled
    wildlife."""
    rows = [truth("a1", burst="A"), truth("a2", burst="A"), truth("b1", burst="B"), truth("b2", burst="B"),
            truth("soft", 0, "soft_subject"), truth("over", 0, "overexposed"), truth("keeper"),
            truth("land", scene="landscape")]
    preds = [result("a1", 0.0), result("a2", 0.2), result("b1", 10.0, v=(0.0, 1.0)), result("b2", 13.0, v=(0.0, 1.0)),
             result("soft", 20.0, reasons=["soft_subject"], v=(0.0, 0.0, 1.0)), result("over", 30.0, v=(0.0, 0.0, 0.0, 1.0)),
             result("keeper", 40.0, reasons=["subject_cut"], v=(0.5, 0.5)), result("land", 50.0, v=(0.3, 0.1, 0.9))]
    return rows, {p["path"]: p for p in preds}


def test_album_metrics_on_crafted_results():
    rows, preds = album_run()
    rep = bench.build_report(rows, preds, tier="album", profile="album", reducers=ALBUM)
    pm = rep["plugin_metrics"]
    assert rep["meta"]["reducers"] == ALBUM and set(pm) == {"quality", "burst", "scene"}
    qa = pm["quality"]
    # rejected: soft (right), keeper (wrong, subject_cut) -> precision 1/2; truth rejects: soft (found), over (missed)
    assert (qa["all"]["reject_precision"], qa["all"]["reject_recall"]) == (0.5, 0.5)
    assert qa["soft"]["reject_recall"] == 1.0 and qa["soft_subject"]["reject_precision"] == 1.0
    assert qa["overexposed"]["reject_recall"] == 0.0 and qa["subject_cut"]["reject_precision"] == 0.0
    assert qa["all"]["keepers_lost"] == round(1 / 6, 6) and qa["all"]["keepers_lost_n"] == 6
    b = pm["burst"]["all"]
    # truth pairs: (a1,a2), (b1,b2); predicted: (a1,a2) only (3 s > max_gap_s)
    assert (b["burst_pair_precision"], b["burst_pair_recall"], b["burst_pair_f1"]) == (1.0, 0.5, round(2 / 3, 6))
    s = pm["scene"]
    assert s["all"]["scene_acc"] == 0.875 and s["landscape"]["scene_acc"] == 0.0 and s["wildlife"]["n"] == 7
    assert "### plugin quality" in bench.summary_md(rep)


def test_bad_reducers_in_a_meta_line_are_a_bench_error():
    rows, preds = album_run()
    with pytest.raises(bench.BenchError, match="preds meta line"):
        bench.build_report(rows, preds, preds_meta={"reducers": {"select": {"per_category": -1}}})


def test_wildlife_ground_truth_measures_no_album_metric():
    rows = [{"path": "x", "scientific": "Megascops asio", "kind": "bird", "tier": "golden"}]
    assert bench.build_report(rows, {"x": result("x", 0.0)})["plugin_metrics"] == {}


def test_compare_album_reports_under_the_album_budget():
    rows, preds = album_run()
    base = bench.build_report(rows, preds, tier="album", profile="album", reducers=ALBUM)
    worse = dict(preds)
    worse["a1"] = result("a1", 0.0, reasons=["underexposed"])          # a keeper lost
    new = bench.build_report(rows, worse, tier="album", profile="album", reducers=ALBUM)
    budget = bench.read_budget(ROOT / "baselines" / "budget-album.toml")
    c = bench.compare(base, new, budget)
    lost = c["plugin_metrics"]["quality"]["all"]["keepers_lost"]
    assert lost["delta"] == pytest.approx(1 / 6, abs=1e-5)
    assert c["verdict"] == "over_budget" and any(v["rule"].startswith("quality.keepers_lost") for v in c["budget"]["violations"])
    assert bench.compare(base, base, budget)["verdict"] == "ok"
    assert "## Plugin metrics: quality (paired images)" in bench.compare_md(c)


def test_album_standards_score_an_album_report():
    rows, preds = album_run()
    rep = bench.build_report(rows, preds, tier="album", profile="album", reducers=ALBUM)
    standards = bench.read_standards(bench.STANDARDS_TOML)
    sc = bench.scorecard(rep, standards, "album")
    ids = {r["id"] for r in sc["rows"]}
    assert "culling.album.all.keepers_lost" in ids and all(r["profile"] == "album" for r in sc["rows"])
    assert {r["id"]: r["status"] for r in sc["rows"]}["culling.album.all.keepers_lost"] == "fail"   # 1/6 > the bar
    wildlife = bench.build_report(rows, preds, tier="album", reducers=ALBUM)
    assert bench.scorecard(wildlife, standards, "album")["rows"] == []
