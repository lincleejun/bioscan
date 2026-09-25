"""`bioscan bench`: report.json, baselines, compare with budgets, failure analysis, scorecard.
Everything runs on fake preds files and ground truth; no service, no models."""
import copy
import json
from pathlib import Path

import pytest

from bioscan.cli import bench
from bioscan.cli import main as cli

HERE = Path(__file__).resolve().parent
STANDARDS = HERE / "fixtures" / "standards-example.toml"
ENGINE = {"version": "0.1.0", "settings": "abc123abc123", "models": {"gate": "g@1"}, "detail_edge": 3072}

BIRDS = {"Buteo jamaicensis": "Accipitridae", "Buteo lineatus": "Accipitridae", "Corvus corax": "Corvidae",
         "Corvus sierramadrensis": "Corvidae", "Megascops kennicottii": "Strigidae", "Anas platyrhynchos": "Anatidae",
         "Larus occidentalis": "Laridae"}
MAMMALS = {"Canis latrans": "Canidae", "Urocyon cinereoargenteus": "Canidae", "Ursus americanus": "Ursidae",
           "Lynx rufus": "Felidae", "Phoca vitulina": "Phocidae"}

# path, truth, kind, tier, expected failure class
CASES = [
    ("/a.jpg", "Buteo jamaicensis", "bird", "own", None),
    ("/b.jpg", "Buteo jamaicensis", "bird", "own", "within_genus"),
    ("/c.jpg", "Corvus corax", "bird", "inat", "out_of_range"),
    ("/d.jpg", "Megascops kennicottii", "bird", "inat", "gate_miss"),
    ("/e.jpg", "Canis latrans", "mammal", "inat", "detector_miss"),
    ("/f.jpg", "Phoca vitulina", "mammal", "inat", "wrong_kind"),
    ("/g.jpg", "Canis latrans", "mammal", "own", "within_family"),
    ("/h.jpg", "Ursus americanus", "mammal", "own", "far_miss"),
    ("/i.jpg", "Fakeus birdus", "bird", "own", "not_in_list"),
    ("/j.jpg", "Anas platyrhynchos", "bird", "own", "failed"),
]


def cand(name, p_geo=0.5, post=0.8, fam=None):
    genus, ep = name.split(" ")
    return {"scientific": name, "common": None, "taxonomy": ["Animalia", "Chordata", "X", "O", fam or "", genus, name],
            "p_visual": 0.7, "p_geo": p_geo, "posterior": post}


def res(path, gate, boxes, sha=None, ident=50.0):
    return {"type": "result", "schema": 1, "path": path, "sha256": sha or path.strip("/").split(".")[0] * 64,
            "image": {}, "engine": ENGINE, "products": {"identify": {"gate": {"class": gate, "probs": {}}, "boxes": boxes}},
            "timing_ms": {"decode": 100.0, "identify": ident}}


def box(kind, level, *cands, score=0.9):
    return {"id": 0, "xyxy": [0, 0, 1, 1], "score": score, "kind": kind, "quality": {"sharpness": 1, "exposure": 0},
            "species": {"list": "x", "level": level, "top": list(cands)}}


def events():
    return [
        {"type": "meta", "schema": 1, "options": {"identify": {"top_k": 5, "geo": True}}, "groundtruth": "gt.csv"},
        res("/a.jpg", "bird", [box("bird", "species", cand("Buteo jamaicensis", fam="Accipitridae"))]),
        res("/b.jpg", "bird", [box("bird", "species", cand("Buteo lineatus"), cand("Buteo jamaicensis"))]),
        res("/c.jpg", "bird", [box("bird", "genus", cand("Corvus sierramadrensis", p_geo=0.0), cand("Corvus corax"))]),
        res("/d.jpg", "none", []),
        res("/e.jpg", "mammal", []),
        res("/f.jpg", "mammal", [box("bird", "species", cand("Larus occidentalis", post=0.95))]),
        res("/g.jpg", "mammal", [box("mammal", "genus", cand("Urocyon cinereoargenteus"))]),
        res("/h.jpg", "mammal", [box("mammal", "unconfirmed", cand("Lynx rufus"))]),
        res("/i.jpg", "bird", [box("bird", "species", cand("Buteo lineatus"))]),
        {"type": "error", "path": "/j.jpg", "product": None, "message": "decode: boom"},
        {"type": "done", "schema": 1, "ok": 9, "failed": 1, "elapsed_ms": 4500.0},
    ]


def write_case(tmp_path, evs=None, name="preds.ndjson", gt_rows=CASES):
    gt = tmp_path / "gt.csv"
    gt.write_text("path,scientific,tier,lat,lon,taken_at,source,kind\n"
                  + "".join(f"{p},{s},{t},37.4,-122.1,,,{k}\n" for p, s, k, t, _ in gt_rows))
    preds = tmp_path / name
    preds.write_text("".join(json.dumps(e) + "\n" for e in (evs or events())))
    for kind, table in (("bird", BIRDS), ("mammal", MAMMALS)):
        (tmp_path / f"{kind}.csv").write_text("scientific,family\n" + "".join(f"{s},{f}\n" for s, f in table.items()))
    return str(preds), str(gt)


def names_args(tmp_path):
    return ["--names", f"bird={tmp_path / 'bird.csv'}", "--names", f"mammal={tmp_path / 'mammal.csv'}"]


def lists(tmp_path):
    return bench.load_name_lists({"bird": str(tmp_path / "bird.csv"), "mammal": str(tmp_path / "mammal.csv")})


@pytest.fixture
def report(tmp_path):
    preds, gt = write_case(tmp_path)
    return bench.report_from_preds(preds, gt, lists=lists(tmp_path))


# ---- statistics ------------------------------------------------------------------------------

def test_wilson_interval():
    assert bench.wilson(0, 0) is None
    assert bench.wilson(0, 10) == [0.0, pytest.approx(0.277533, abs=1e-6)]
    lo, hi = bench.wilson(5, 10)
    assert lo == pytest.approx(0.236593, abs=1e-6) and hi == pytest.approx(0.763407, abs=1e-6)
    lo, hi = bench.wilson(10, 10)
    assert hi == 1.0 and lo == pytest.approx(0.722467, abs=1e-6)


def test_mcnemar_exact():
    assert bench.mcnemar_exact(0, 0) == 1.0
    assert bench.mcnemar_exact(1, 0) == 1.0
    assert bench.mcnemar_exact(0, 6) == pytest.approx(2 / 64)
    assert bench.mcnemar_exact(2, 8) == pytest.approx(2 * (1 + 10 + 45) / 1024)
    assert bench.mcnemar_exact(8, 2) == bench.mcnemar_exact(2, 8)
    assert bench.mcnemar_exact(300, 330) < 1       # large n stays exact and finite


def test_ece():
    assert bench.ece([]) is None
    assert bench.ece([(0.9, True), (0.9, True)]) == pytest.approx(0.1)
    assert bench.ece([(1.0, False)]) == pytest.approx(1.0)


# ---- report ----------------------------------------------------------------------------------

def test_report_schema_and_metrics(report):
    assert (report["schema"], report["version"]) == ("bioscan-report", 1)
    meta = report["meta"]
    assert meta["n"] == 10 and meta["complete"] is True and meta["settings_fingerprint"] == "abc123abc123"
    assert meta["engine"] == ENGINE and len(meta["groundtruth_sha256"]) == 64 and len(meta["synonyms_sha256"]) == 64
    assert meta["options"] == {"identify": {"top_k": 5, "geo": True}, "synonyms": True}
    assert meta["done"] == {"ok": 9, "failed": 1, "elapsed_ms": 4500.0} and meta["date"].endswith("Z")
    keys = ["n"] + [x for k in bench.RATES for x in (k, k + "_ci")] + ["ece", "decode_ms_median",
                                                                       "identify_ms_median", "images_per_s"]
    for scope in ("all", "bird", "mammal", "other", "own/all", "inat/mammal"):
        assert list(report["metrics"][scope]) == keys, scope
    a = report["metrics"]["all"]
    assert a["n"] == 10 and a["top1"] == 0.1 and a["top5"] == 0.3          # /a; /b and /c in top-5
    assert a["genus_acc"] == 0.3                                             # /a /b /c
    assert a["coverage"] == 0.4 and a["precision"] == 0.25                   # /a /b /f /i at species
    assert a["confident_error_rate"] == 0.3 and a["no_box_rate"] == 0.2 and a["failed_rate"] == 0.1
    assert a["gate_acc"] == 0.8 and a["detect_rate"] == 0.6                  # gate: all but /d /j; box: /f is a bird
    assert a["images_per_s"] == 2.0 and report["metrics"]["bird"]["images_per_s"] is None
    assert a["ece"] is None and a["identify_ms_median"] == 50.0
    assert a["top1_ci"] == bench.wilson(1, 10) and a["precision_ci"] == bench.wilson(1, 4)
    assert report["metrics"]["other"]["n"] == 0 and report["metrics"]["other"]["top1"] is None
    assert report["metrics"]["other"]["top1_ci"] is None


def test_report_tables_and_image_rows(report):
    sp = report["per_species"]
    assert sp["Buteo jamaicensis"] == {"kind": "bird", "family": "Accipitridae", "n": 2, "top1_hits": 1, "top1": 0.5,
                                       "confident_errors": 1}
    assert report["per_family"]["Canidae"]["n"] == 2 and report["per_family"]["(unknown)"]["n"] == 1   # Fakeus
    rows = {r["path"]: r for r in report["images"]}
    c = rows["/c.jpg"]
    assert (c["truth"], c["top1"], c["level"], c["correct_top1"], c["correct_genus"]) == \
        ("Corvus corax", "Corvus sierramadrensis", "genus", False, True)
    assert (c["p_visual"], c["p_geo"], c["posterior"], c["place_known"]) == (0.7, 0.0, 0.8, True)
    assert c["sha256"] == "c" * 64 and c["gate"] == "bird" and c["has_box"] and c["box_kind"] == "bird"
    assert rows["/j.jpg"]["failed"] and rows["/j.jpg"]["error"] == "decode: boom"
    assert rows["/i.jpg"]["in_list"] is False and rows["/a.jpg"]["in_list"] is True
    assert rows["/g.jpg"]["family_truth"] == rows["/g.jpg"]["family_pred"] == "Canidae"


def test_report_without_name_list_leaves_in_list_unknown(tmp_path):
    preds, gt = write_case(tmp_path)
    rep = bench.report_from_preds(preds, gt, lists={})
    assert all(r["in_list"] is None for r in rep["images"])
    assert rep["meta"]["name_lists"] == []


def test_ece_from_p_correct(tmp_path):
    evs = events()
    evs[1]["products"]["identify"]["boxes"][0]["species"]["p_correct"] = 0.8
    evs[2]["products"]["identify"]["boxes"][0]["species"]["p_correct"] = 0.4
    preds, gt = write_case(tmp_path, evs)
    rep = bench.report_from_preds(preds, gt, lists=lists(tmp_path))
    assert rep["metrics"]["all"]["ece"] == pytest.approx((abs(0.8 - 1) + abs(0.4 - 0)) / 2)


def test_ece_clamps_p_correct(tmp_path):
    evs = events()
    evs[1]["products"]["identify"]["boxes"][0]["species"]["p_correct"] = 1.5     # right, clamped to 1.0
    evs[2]["products"]["identify"]["boxes"][0]["species"]["p_correct"] = -0.2    # wrong, clamped to 0.0
    preds, gt = write_case(tmp_path, evs)
    assert bench.report_from_preds(preds, gt, lists=lists(tmp_path))["metrics"]["all"]["ece"] == 0.0


# ---- analyze ---------------------------------------------------------------------------------

def test_failure_classes_on_crafted_cases(report):
    got = {r["path"]: bench.failure_class(r) for r in report["images"]}
    assert got == {p: cls for p, _, _, _, cls in CASES}


def test_prior_suppressed_truth_first_by_p_visual(tmp_path):
    """The geo-gap case: the truth has the highest p_visual, but a lower p_geo put a congener on top."""
    write_case(tmp_path)                                    # writes the name-list CSVs lists() reads
    truth = {"path": "/k.jpg", "scientific": "Corvus corax", "tier": "own", "kind": "bird", "lat": "37", "lon": "-122"}

    def c(name, pv, pg, post):
        return {**cand(name, p_geo=pg, post=post), "p_visual": pv}

    pev = res("/k.jpg", "bird", [box("bird", "genus", c("Corvus brachyrhynchos", 0.3, 0.9, 0.7),
                                     c("Corvus corax", 0.6, 0.02, 0.2), c("Corvus ossifragus", 0.1, 0.5, 0.1))])
    row = bench.image_row(truth, pev, lists(tmp_path), {})
    assert (row["truth_rank"], row["truth_visual_rank"], row["truth_p_visual"], row["truth_p_geo"],
            row["truth_posterior"]) == (2, 1, 0.6, 0.02, 0.2)
    assert bench.failure_class(row) == "prior_suppressed"
    # not first by p_visual: an ordinary congener miss
    pev2 = res("/k.jpg", "bird", [box("bird", "genus", c("Corvus brachyrhynchos", 0.7, 0.9, 0.7),
                                      c("Corvus corax", 0.6, 0.02, 0.2))])
    assert bench.failure_class(bench.image_row(truth, pev2, lists(tmp_path), {})) == "within_genus"
    # truth not listed: all truth_* fields null
    pev3 = res("/k.jpg", "bird", [box("bird", "species", c("Buteo lineatus", 0.7, 0.9, 0.7))])
    row3 = bench.image_row(truth, pev3, lists(tmp_path), {})
    assert row3["truth_rank"] is None and row3["truth_p_geo"] is None


def test_analyze_counts_examples_and_confusion(report):
    a = bench.analyze(report)
    by = {c["class"]: c for c in a["classes"]}
    assert a["wrong"] == 9 and all(by[c]["count"] == 1 for c in bench.PRIMARY if c != "prior_suppressed")
    assert by["prior_suppressed"]["count"] == 0
    over = by["overconfident"]                       # /b /f /i: wrong at species
    assert over["count"] == 3 and not over["primary"] and over["examples"][0]["path"] == "/f.jpg"   # highest posterior
    assert by["out_of_range"]["fix"].startswith("top-1 has p_geo") and "range veto in rules.py" in by["out_of_range"]["fix"]
    assert by["out_of_range"]["examples"][0]["p_geo"] == 0.0 and by["gate_miss"]["share_of_wrong"] == pytest.approx(1 / 9)
    assert {"truth": "Phoca vitulina", "pred": "Larus occidentalis", "count": 1} in a["confusion"]
    assert a["next"]["class"] == "failed"            # ties go to the earlier (upstream) class
    assert a["by_scope"]["mammal"]["wrong_kind"] == 1
    md = bench.analyze_md(a)
    assert "| out_of_range | 1 |" in md and "overconfident (overlaps)" in md and "## Top confusion pairs" in md


def test_analyze_cli(tmp_path, capsys):
    preds, gt = write_case(tmp_path)
    assert cli.main(["bench", "report", preds, gt, "--out", str(tmp_path / "r.json"), *names_args(tmp_path)]) == 0
    assert cli.main(["bench", "analyze", str(tmp_path / "r.json"), "--md", str(tmp_path / "a.md"),
                     "--json", str(tmp_path / "a.json")]) == 0
    assert "# bioscan bench analyze" in capsys.readouterr().out
    assert json.loads((tmp_path / "a.json").read_text())["schema"] == "bioscan-analysis"
    assert (tmp_path / "a.md").read_text().startswith("# bioscan bench analyze")


# ---- report / run / baseline CLI -------------------------------------------------------------

def test_bench_report_cli_default_out(tmp_path, capsys):
    preds, gt = write_case(tmp_path)
    assert cli.main(["bench", "report", preds, gt, *names_args(tmp_path)]) == 0
    rep = json.loads((tmp_path / "report.json").read_text())
    assert rep["metrics"]["all"]["n"] == 10 and "| all | 10 |" in capsys.readouterr().out


def test_bench_run_from_preds_writes_eval_outputs_and_report(tmp_path, capsys):
    preds, gt = write_case(tmp_path)
    assert cli.main(["bench", "run", gt, "--out", str(tmp_path / "run"), "--preds", preds, *names_args(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("# bioscan eval report") and "report.json ->" in out
    assert (tmp_path / "run" / "report.md").is_file()
    assert bench.load_report(tmp_path / "run" / "report.json")["meta"]["preds"] == preds


def test_bench_run_calls_the_service(tmp_path, monkeypatch):
    from bioscan.cli import eval as ev
    _, gt = write_case(tmp_path)
    monkeypatch.setattr(ev.client, "health", lambda url: {"status": "ok"})
    monkeypatch.setattr(ev.client, "run", lambda payload, url: (json.dumps(e).encode() for e in events()[1:]))
    assert cli.main(["bench", "run", gt, "--out", str(tmp_path / "svc"), *names_args(tmp_path)]) == 0
    rep = bench.load_report(tmp_path / "svc" / "report.json")
    assert rep["meta"]["preds"].endswith("preds.ndjson") and rep["meta"]["complete"] is True
    assert rep["meta"]["groundtruth_sha256"] == rep["meta"]["preds_groundtruth_sha256"]
    # stream cut before `done`: report still written, exit 3 like eval
    monkeypatch.setattr(ev.client, "run", lambda payload, url: (json.dumps(e).encode() for e in events()[1:4]))
    assert cli.main(["bench", "run", gt, "--out", str(tmp_path / "cut"), *names_args(tmp_path)]) == 3
    assert bench.load_report(tmp_path / "cut" / "report.json")["meta"]["complete"] is False


def test_baseline_copies_and_refuses_overwrite(tmp_path, report):
    src = bench.write_json(report, tmp_path / "r.json")
    base = tmp_path / "baselines"
    assert cli.main(["bench", "baseline", str(src), "--name", "ci-smoke", "--dir", str(base)]) == 0
    assert json.loads((base / "ci-smoke.json").read_text()) == report
    assert cli.main(["bench", "baseline", str(src), "--name", "ci-smoke", "--dir", str(base)]) == 2
    assert cli.main(["bench", "baseline", str(src), "--name", "ci-smoke", "--dir", str(base), "--force"]) == 0
    assert cli.main(["bench", "baseline", str(src), "--name", "../evil", "--dir", str(base)]) == 2
    (tmp_path / "x.json").write_text('{"schema": "other"}')
    assert cli.main(["bench", "baseline", str(tmp_path / "x.json"), "--name", "x", "--dir", str(base)]) == 2


# ---- compare and budgets -----------------------------------------------------------------------

def degraded(report):
    """/a broken (right -> congener), /h fixed, /c answer changed but still wrong; new engine settings."""
    new = copy.deepcopy(report)
    new["meta"]["settings_fingerprint"] = "fff000fff000"
    rows = {r["path"]: r for r in new["images"]}
    rows["/a.jpg"].update(top1="Buteo lineatus", correct_top1=False, level="species", p_geo=0.3)
    rows["/h.jpg"].update(top1="Ursus americanus", correct_top1=True)
    rows["/c.jpg"].update(top1="Corvus brachyrhynchos")
    new["per_species"]["Buteo jamaicensis"].update(top1_hits=0, top1=0.0)
    new["per_species"]["Ursus americanus"].update(top1_hits=1, top1=1.0)
    new["metrics"]["all"]["confident_error_rate"] = 0.4
    new["metrics"]["all"]["images_per_s"] = 1.0
    return new


def test_compare_pairs_and_counts(report):
    c = bench.compare(report, degraded(report))
    pr = c["pairing"]
    assert (pr["paired"], pr["fixed"], pr["broken"], pr["changed_same"]) == (10, 1, 1, 1)
    assert pr["key"] == "path 1, sha256 9" and pr["mcnemar_p"] == 1.0
    assert c["species"]["regressions"] == [{"truth": "Buteo jamaicensis", "base_hits": 1, "new_hits": 0,
                                            "base_n": 2, "new_n": 2, "lost": 1}]
    assert c["species"]["improvements"][0]["truth"] == "Ursus americanus"
    b = c["broken"][0]
    assert (b["path"], b["truth"], b["old"]["top1"], b["new"]["top1"], b["new"]["p_geo"]) == \
        ("/a.jpg", "Buteo jamaicensis", "Buteo jamaicensis", "Buteo lineatus", 0.3)
    assert any("settings fingerprint differs" in w for w in c["warnings"])
    assert c["metrics"]["all"]["confident_error_rate"]["delta"] == pytest.approx(0.1)
    assert c["metrics"]["all"]["top1"]["base_ci"] == report["metrics"]["all"]["top1_ci"]
    assert c["verdict"] == "ok" and not c["budget"]["checked"]


def test_compare_pairs_by_sha_when_paths_move(report):
    new = copy.deepcopy(report)
    for r in new["images"]:
        r["path"] = "/moved" + r["path"]
    pr = bench.compare(report, new)["pairing"]
    assert pr["paired"] == 9 and pr["only_base"] == 1 and pr["only_new"] == 1   # the failed image has no sha


def test_compare_pairs_duplicate_photos_one_to_one(report):
    """Two copies of one photo (same sha256) at different paths: each pairs with its own path, and
    moved copies still pair one-to-one instead of all landing on the last copy."""
    base = copy.deepcopy(report)
    x, y = copy.deepcopy(base["images"][0]), copy.deepcopy(base["images"][0])
    x["path"], y["path"] = "/dup/x.jpg", "/dup/y.jpg"
    y["correct_top1"] = False
    base["images"] = [x, y]
    new = copy.deepcopy(base)
    new["images"] = list(reversed(new["images"]))
    pairs, only_base, only_new, key = bench.pair_images(base["images"], new["images"])
    assert [(b["path"], n["path"]) for b, n in pairs] == [("/dup/y.jpg", "/dup/y.jpg"), ("/dup/x.jpg", "/dup/x.jpg")]
    assert (only_base, only_new, key) == (0, 0, "sha256 2")
    assert bench.compare(base, new)["pairing"]["broken"] == 0
    for r in new["images"]:
        r["path"] = "/moved" + r["path"]
    pairs, only_base, only_new, _ = bench.pair_images(base["images"], new["images"])
    assert len(pairs) == 2 and (only_base, only_new) == (0, 0)
    assert {b["path"] for b, _ in pairs} == {"/dup/x.jpg", "/dup/y.jpg"}


def test_compare_warns_on_groundtruth_change(report):
    new = copy.deepcopy(report)
    new["meta"]["groundtruth_sha256"] = "0" * 64
    assert any("ground truth differs" in w for w in bench.compare(report, new)["warnings"])


BUDGET = """
[[rule]]
metric = "top1"
scopes = ["all", "bird", "mammal"]
max_drop_pts = {top1}

[[rule]]
metric = "confident_error_rate"
scopes = ["all"]
max_rise_pts = {cer}

[[rule]]
metric = "images_per_s"
scopes = ["all"]
max_drop_pct = {ips}

[species]
max_lost = {lost}
"""


def _cmp(tmp_path, report, budget_text):
    base = bench.write_json(report, tmp_path / "base.json")
    new = bench.write_json(degraded(report), tmp_path / "new.json")
    (tmp_path / "b.toml").write_text(budget_text)
    return cli.main(["bench", "compare", str(base), str(new), "--budget", str(tmp_path / "b.toml"),
                     "--md", str(tmp_path / "c.md"), "--json", str(tmp_path / "c.json")])


def test_budget_pass(tmp_path, report, capsys):
    assert _cmp(tmp_path, report, BUDGET.format(top1=20, cer=15, ips=60, lost=1)) == 0
    c = json.loads((tmp_path / "c.json").read_text())
    assert c["verdict"] == "ok" and c["budget"]["checked"] and not c["budget"]["violations"]
    assert "within budget" in capsys.readouterr().out and "within budget" in (tmp_path / "c.md").read_text()


def test_budget_fail(tmp_path, report, capsys):
    # budget metrics are recomputed from the paired image rows: /a broke (bird 1/6 -> 0/6), /h was fixed
    # (mammal 0/4 -> 1/4), so `all` top1 is flat, bird drops, and /a is now a confident error
    assert _cmp(tmp_path, report, BUDGET.format(top1=0, cer=5, ips=10, lost=0)) == 1
    why = [v["why"] for v in json.loads((tmp_path / "c.json").read_text())["budget"]["violations"]]
    assert why == ["top1 dropped 16.7 pts in bird (limit 0 pts)",
                   "confident_error_rate rose 10.0 pts in all (limit 5 pts)",
                   "images_per_s dropped 50.0 % in all (limit 10 %)",
                   "Buteo jamaicensis lost 1 top-1 hits (limit 0)"]
    assert "OVER BUDGET" in capsys.readouterr().out


def test_budget_real_regression_among_paired_images_fails(tmp_path, report):
    new = copy.deepcopy(report)
    next(r for r in new["images"] if r["path"] == "/a.jpg").update(correct_top1=False, top1="Buteo lineatus")
    new["metrics"]["all"]["top1"] = 0.9            # aggregate metrics are not what the budget reads
    budget = bench.read_budget(_write(tmp_path / "b.toml", BUDGET.format(top1=2, cer=50, ips=90, lost=5)))
    v = bench.compare(report, new, budget)["budget"]["violations"]
    assert [x["scope"] for x in v] == ["all", "bird"]
    assert v[0]["why"] == "top1 dropped 10.0 pts in all (limit 2 pts)"


def _extra(row, path, sha, correct):
    r = copy.deepcopy(row)
    r.update(path=path, sha256=sha, correct_top1=correct, correct_top5=correct, correct_genus=correct,
             top1=r["truth"] if correct else "Nope nope", level="species")
    return r


def test_budget_ignores_images_only_in_the_new_report(tmp_path, report):
    """The smoke set grew: extra all-wrong images in the new report must not read as a regression."""
    new = copy.deepcopy(report)
    other = {**new["images"][0], "kind": "other_animal", "scope": "other", "truth": "Anolis carolinensis"}
    new["images"] += [_extra(other, f"/new/{i}.jpg", f"{i:064x}", False) for i in range(8)]
    new["metrics"] = bench.metrics_by_scope(new["images"], report["metrics"]["all"]["images_per_s"])
    budget = bench.read_budget(_write(tmp_path / "b.toml", BUDGET.format(top1=0, cer=0, ips=0, lost=0)))
    c = bench.compare(report, new, budget)
    assert c["verdict"] == "ok" and not c["budget"]["violations"]
    assert c["pairing"]["only_new"] == 8
    assert (c["metrics"]["all"]["n"]["base"], c["metrics"]["all"]["n"]["new"]) == (10, 10)
    assert c["metrics_whole"]["all"]["top1"]["delta"] < 0                    # the whole-set view did drop
    assert c["unpaired"]["only_new"]["other"]["n"] == 8 and c["unpaired"]["only_new"]["other"]["top1"] == 0
    assert c["unpaired"]["only_base"] == {}
    md = bench.compare_md(c)
    assert "8 only in new" in md and "## Unpaired images: new images (only in new)" in md
    assert "| other | 8 | 0.0% |" in md


def test_budget_with_disjoint_extras_on_both_sides(tmp_path, report):
    base, new = copy.deepcopy(report), copy.deepcopy(report)
    base["images"] += [_extra(base["images"][0], f"/old/{i}.jpg", f"b{i:063x}", True) for i in range(5)]
    new["images"] += [_extra(new["images"][0], f"/new/{i}.jpg", f"c{i:063x}", False) for i in range(5)]
    budget = bench.read_budget(_write(tmp_path / "b.toml", BUDGET.format(top1=0, cer=0, ips=0, lost=0)))
    c = bench.compare(base, new, budget)
    pr = c["pairing"]
    assert (pr["paired"], pr["only_base"], pr["only_new"], pr["broken"]) == (10, 5, 5, 0)
    assert c["verdict"] == "ok" and c["species"]["regressions"] == []      # per_species tables would say -5
    assert c["unpaired"]["only_base"]["bird"]["n"] == 5 and c["unpaired"]["only_new"]["bird"]["top1"] == 0
    md = bench.compare_md(c)
    assert "5 only in base, 5 only in new" in md and "## Unpaired images: only in base" in md


def _write(path, text):
    path.write_text(text)
    return path


@pytest.mark.parametrize("text,msg", [
    ('[[rule]]\nmetric = "nope"\nmax_drop_pts = 1\n', "metric must be"),
    ('[[rule]]\nmetric = "top1"\n', "exactly one of"),
    ('[[rule]]\nmetric = "images_per_s"\nmax_drop_pts = 1\n', "*_pts limits are for rates"),
    ('[[rule]]\nmetric = "top1"\nmax_drop_pts = 1\ntypo = 2\n', "unknown keys"),
    ("[other]\n", "unknown budget sections"),
    ('[rule]\nmetric = "top1"\nmax_drop_pts = 1\n', "not one"),
    ("not toml [", "cannot read budget"),
])
def test_budget_file_errors(tmp_path, text, msg):
    with pytest.raises(bench.BenchError, match=msg.replace("*", r"\*")):
        bench.read_budget(_write(tmp_path / "b.toml", text))


def test_compare_exit_2_when_incomparable(tmp_path, report):
    base = bench.write_json(report, tmp_path / "base.json")
    (tmp_path / "junk.json").write_text("{")
    assert cli.main(["bench", "compare", str(base), str(tmp_path / "junk.json")]) == 2
    other = copy.deepcopy(report)
    other["version"] = 99
    assert cli.main(["bench", "compare", str(base), str(bench.write_json(other, tmp_path / "v99.json"))]) == 2
    disjoint = copy.deepcopy(report)
    for r in disjoint["images"]:
        r["path"], r["sha256"] = "/other" + r["path"], None
    assert cli.main(["bench", "compare", str(base), str(bench.write_json(disjoint, tmp_path / "d.json"))]) == 2
    (tmp_path / "bad.toml").write_text("[x]\n")
    assert cli.main(["bench", "compare", str(base), str(base), "--budget", str(tmp_path / "bad.toml")]) == 2


def test_compare_against_itself_is_clean(tmp_path, report, capsys):
    base = bench.write_json(report, tmp_path / "base.json")
    assert cli.main(["bench", "compare", str(base), str(base), "--budget",
                     str(bench.BUDGET_TOML)]) == 0      # the committed budget parses and passes on no change
    out = capsys.readouterr().out
    assert "Fixed 0, broken 0" in out and "### bird: no change" in out


# ---- scorecard -------------------------------------------------------------------------------

def test_scorecard_golden_tier_uses_the_wilson_bound(report):
    sc = bench.scorecard(report, bench.read_standards(STANDARDS), "golden")
    rows = {r["id"]: r for r in sc["rows"]}
    assert set(rows) == {"accuracy.golden.bird.top1", "trust.golden.all.confident_error_rate",
                         "trust.golden.all.ece", "speed.golden.all.images_per_s"}
    assert sc["skipped"] == 2 and [m["id"] for m in sc["manual"]] == ["coverage.names.bird"]
    top1 = rows["accuracy.golden.bird.top1"]            # 1/6 = 16.7% clears 10% but its lower bound does not
    assert top1["value"] == pytest.approx(1 / 6, abs=1e-6) and top1["judged_on"] == "wilson"
    assert top1["judged"] == bench.wilson(1, 6)[0] and top1["status"] == "fail"
    assert top1["gap"] == pytest.approx(bench.wilson(1, 6)[0] - 0.10, abs=1e-6)
    cer = rows["trust.golden.all.confident_error_rate"]
    assert cer["judged"] == bench.wilson(3, 10)[1] and cer["status"] == "fail"
    assert rows["trust.golden.all.ece"]["status"] == "n/a"
    speed = rows["speed.golden.all.images_per_s"]
    assert (speed["judged_on"], speed["status"], speed["gap"]) == ("value", "pass", 0)


def test_scorecard_smoke_tier_is_judged_on_the_value(report):
    sc = bench.scorecard(report, bench.read_standards(STANDARDS), "smoke")
    (row,) = sc["rows"]
    assert (row["id"], row["tier"], row["judged_on"], row["status"]) == ("directory.all-top1", "smoke", "value", "pass")


def test_scorecard_nogeo_report_uses_nogeo_standards_only(report):
    report["meta"]["options"]["identify"]["geo"] = False
    sc = bench.scorecard(report, bench.read_standards(STANDARDS), "golden")
    assert [r["id"] for r in sc["rows"]] == ["location.golden.bird.top1.nogeo"] and sc["nogeo"]


def test_scorecard_cli_tier_and_exit_codes(tmp_path, report, capsys):
    path = bench.write_json(report, tmp_path / "r.json")
    assert cli.main(["bench", "scorecard", str(path), "--standards", str(STANDARDS)]) == 2     # two gt tiers
    assert "pass --tier" in capsys.readouterr().err
    assert cli.main(["bench", "scorecard", str(path), "--standards", str(STANDARDS), "--tier", "golden"]) == 1
    out = capsys.readouterr().out
    assert "| accuracy.golden.bird.top1 | Bird species Top-1 | 6 | >= 10.0% | 16.7% [3.0%, 56.4%] | wilson | FAIL |" in out
    assert "1 pass, 2 fail, 1 n/a" in out and "## Not measurable from a report" in out
    assert "| coverage.names.bird | Bird species in the name list | >= 11131 species |" in out
    assert cli.main(["bench", "scorecard", str(path), "--standards", str(STANDARDS), "--tier", "gold"]) == 2
    assert "no standards for tier 'gold'; known tiers: golden, smoke" in capsys.readouterr().err
    report["meta"]["tier"] = "smoke"                   # bench run/report --tier writes meta.tier
    path = bench.write_json(report, tmp_path / "r2.json")
    assert cli.main(["bench", "scorecard", str(path), "--standards", str(STANDARDS)]) == 0


def test_report_tier_from_flag_or_single_gt_tier(tmp_path):
    preds, gt = write_case(tmp_path)
    assert cli.main(["bench", "report", preds, gt, "--tier", "golden", *names_args(tmp_path)]) == 0
    assert bench.report_tier(bench.load_report(tmp_path / "report.json")) == "golden"
    preds, gt = write_case(tmp_path, gt_rows=[(p, s, k, "own", c) for p, s, k, _, c in CASES])
    assert bench.report_tier(bench.report_from_preds(preds, gt, lists={})) == "own"


@pytest.mark.parametrize("change,msg", [
    (('metric = "top1"', 'metric = "nope"'), "metric must be"),
    (('op = ">="', 'op = ">"'), "op must be"),
    (('scope = "bird"', 'scope = "fish"'), "scope must be"),
    (('unit = "images/s"', 'unit = "fraction"'), "rate metrics take unit"),
    (('unit = "fraction"', 'unit = "%"'), "rate metrics take unit"),
    (("community = 0.10", 'community = "high"'), "community must be a number"),
    (('source = "example"\n', 'source = "example"\nextra = 1\n'), "unknown keys"),
    (('id = "speed.golden.all.images_per_s"', 'id = "accuracy.golden.bird.top1"'), "duplicate id"),
    (('id = "speed.golden.all.images_per_s"', 'id = "speed.fast"'), "no tier"),
    (('how = "metrics.all.confident_error_rate"\n', ""), "missing how"),
])
def test_standards_schema_errors(tmp_path, change, msg):
    path = _write(tmp_path / "s.toml", STANDARDS.read_text().replace(*change, 1))
    with pytest.raises(bench.BenchError, match=msg):
        bench.read_standards(path)


def test_standards_tier_from_field_or_id():
    assert bench.standard_tier({"id": "accuracy.golden.bird.top1"}) == "golden"
    assert bench.standard_tier({"id": "location.own.bird.top1.nogeo"}) == "own"
    assert bench.standard_tier({"id": "accuracy.golden.bird.top1", "tier": "public"}) == "public"
    assert bench.standard_tier({"id": "coverage.names"}) is None




# ---- profiles and plugin metrics (A6) ----------------------------------------------------------

GEOTAG = {"/a.jpg": {"place_source": "gpx", "lat": 37.4, "lon": -122.1}, "/b.jpg": {"place_source": "exif"},
          "/c.jpg": {"place_source": "none"}, "/e.jpg": {"place_source": "gpx", "lat": 37.4, "lon": -122.1}}
