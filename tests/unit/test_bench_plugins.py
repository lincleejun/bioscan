"""Harness per profile (A6): meta.profile, plugin_metrics / plugin_images from Manifest.metrics row
functions, plugin rules in the budget, plugin standards (with a profile) in the scorecard. A toy
registry keeps these tests independent of the real plugins' metrics."""
import json

import pytest

from bioscan.cli import bench
from bioscan.plugin import Metric


def hit_row(truth, pred):
    """rate: the prediction's `hit` product, per the truth's `group` scope and `all`."""
    if not pred or pred.get("type") != "result" or "toy" not in pred["products"]:
        return {}
    hit = pred["products"]["toy"]["hit"]
    return {"all": hit, truth["group"]: hit}


def score_row(truth, pred):
    return {"all": (pred or {}).get("products", {}).get("toy", {}).get("score")}


def group_row(truth, pred):
    if not truth.get("burst"):
        return {}
    return {"all": (truth["burst"], (pred or {}).get("products", {}).get("toy", {}).get("burst"))}


REG = {"toy": (Metric("toy_hit", "rate", f"{__name__}:hit_row"),
               Metric("toy_score", "median", f"{__name__}:score_row"),
               Metric("toy_lost", "rate", f"{__name__}:hit_row", lower_is_better=True)),
       "grp": (Metric("pair_p", "pair_precision", f"{__name__}:group_row"),
               Metric("pair_r", "pair_recall", f"{__name__}:group_row"),
               Metric("pair_f1", "pair_f1", f"{__name__}:group_row"))}


def rows_and_preds(hits, bursts=None):
    rows, preds = [], {}
    for i, h in enumerate(hits):
        p = f"/x/{i}.jpg"
        rows.append({"path": p, "scientific": "", "kind": "", "tier": "album", "group": "a" if i % 2 else "b",
                     "burst": (bursts or {}).get(i, [None, None])[0]})
        toy = {"hit": h, "score": i, "burst": (bursts or {}).get(i, [None, None])[1]}
        preds[p] = {"type": "result", "path": p, "sha256": f"s{i}", "products": {"toy": toy}, "timing_ms": {}}
    return rows, preds


def test_plugin_metrics_rates_medians_and_pairs():
    # burst truth: {0,1,2} and {3,4}; predicted: {0,1} and {2,3,4}
    bursts = {0: ("t1", "p1"), 1: ("t1", "p1"), 2: ("t1", "p2"), 3: ("t2", "p2"), 4: ("t2", "p2")}
    rows, preds = rows_and_preds([True, True, False, True, False, None], bursts)
    rep = bench.build_report(rows, preds, registry=REG, preds_meta={"profile": "album"})
    assert rep["meta"]["profile"] == "album" and rep["meta"]["reducers"] is None
    toy = rep["plugin_metrics"]["toy"]
    assert list(toy) == ["all", "a", "b"]                       # all first
    assert toy["all"]["n"] == 6 and toy["all"]["toy_hit"] == 0.6 and toy["all"]["toy_hit_n"] == 5
    assert toy["all"]["toy_hit_ci"] == bench.wilson(3, 5)
    assert toy["all"]["toy_score"] == 2.5 and "toy_score_ci" not in toy["all"]
    assert toy["a"]["toy_hit"] == 1.0 and toy["b"]["toy_hit"] == round(1 / 3, 6) and "toy_score" not in toy["a"]
    assert toy["a"]["n"] == 2                                    # image 5 has no value in scope a
    grp = rep["plugin_metrics"]["grp"]["all"]
    # truth pairs 3 + 1 = 4; predicted pairs 1 + 3 = 4; both: (0,1) and (3,4) = 2
    assert (grp["pair_p"], grp["pair_p_n"], grp["pair_r"], grp["pair_r_n"]) == (0.5, 4, 0.5, 4)
    assert grp["pair_f1"] == 0.5 and grp["pair_p_ci"] == bench.wilson(2, 4) and grp["n"] == 5
    assert [e["path"] for e in rep["plugin_images"]] == [r["path"] for r in rows]
    assert rep["plugin_images"][5]["values"]["toy"] == {"toy_score": {"all": 5}}   # None hit left out
    json.dumps(rep)                                             # tuples stored as lists


def test_core_metrics_do_not_depend_on_plugin_metrics():
    rows, preds = rows_and_preds([True, False])
    with_plugins = bench.build_report(rows, preds, registry=REG)
    without = bench.build_report(rows, preds, registry={})
    assert without["plugin_metrics"] == {} and without["plugin_images"] == []
    for k in ("metrics", "per_species", "per_family", "images"):
        assert with_plugins[k] == without[k]
    assert bench.build_report(rows, preds)["plugin_metrics"] == {}      # the built-in plugins: nothing to read
    md = bench.plugin_md(with_plugins["plugin_metrics"], REG)
    assert "### plugin toy" in md and "| all | 2 | 50.0% [" in md and bench.plugin_md({}, REG) == ""


def test_compare_pairs_plugin_images_and_budgets_plugin_metrics(tmp_path):
    rows, preds = rows_and_preds([True, True, True, True])
    base = bench.build_report(rows, preds, registry=REG)
    rows2, preds2 = rows_and_preds([True, False, False, True])
    new = bench.build_report(rows2, preds2, registry=REG, preds_meta={"profile": "album"})
    budget_file = tmp_path / "b.toml"
    budget_file.write_text('[[rule]]\nplugin = "toy"\nmetric = "toy_hit"\nmax_drop_pts = 10\n'
                           '[[rule]]\nplugin = "toy"\nmetric = "toy_lost"\nscopes = ["all", "a"]\nmax_drop_pts = 90\n')
    budget = bench.read_budget(budget_file, REG)
    c = bench.compare(base, new, budget, registry=REG)
    row = c["plugin_metrics"]["toy"]["all"]["toy_hit"]
    assert (row["base"], row["new"], row["delta"]) == (1.0, 0.5, -0.5)
    assert c["plugin_metrics"]["toy"]["all"]["n"]["new"] == 4
    assert [v["rule"] for v in c["budget"]["violations"]] == ["toy.toy_hit max_drop_pts 10"]
    assert c["verdict"] == "over_budget" and "profile differs: None -> album" in c["warnings"]
    md = bench.compare_md(c, registry=REG)
    assert "## Plugin metrics: toy (paired images)" in md and "| toy_hit | 100.0% [" in md and "-50.0 pts ▼" in md
    assert "| toy_lost | 100.0% [" in md and "-50.0 pts ▲" in md          # lower is better
    assert "Plugin metrics" not in bench.compare_md(bench.compare(base, base, registry=REG) | {"plugin_metrics": {}})


@pytest.mark.parametrize("rule, message", [
    ('plugin = "nope"\nmetric = "toy_hit"\nmax_drop_pts = 1\n', "plugin must be one of toy, grp"),
    ('plugin = "toy"\nmetric = "top1"\nmax_drop_pts = 1\n', "metric must be one of plugin toy's"),
    ('plugin = "toy"\nmetric = "toy_score"\nmax_drop_pts = 1\n', r"\*_pts limits are for rates"),
])
def test_bad_plugin_budget_rules(tmp_path, rule, message):
    f = tmp_path / "b.toml"
    f.write_text("[[rule]]\n" + rule)
    with pytest.raises(bench.BenchError, match=message):
        bench.read_budget(f, REG)


STANDARDS = """
[[standard]]
id = "culling.album.all.toy_hit"
tier = "album"
profile = "album"
plugin = "toy"
dimension = "culling"
title = "toy hits"
scope = "all"
metric = "toy_hit"
op = ">="
community = 0.5
unit = "fraction"
how = "x"
source = "y"

[[standard]]
id = "culling.album.a.toy_hit"
tier = "album"
profile = "album"
plugin = "toy"
dimension = "culling"
title = "toy hits in a"
scope = "a"
metric = "toy_hit"
op = ">="
community = 0.9
unit = "fraction"
how = "x"
source = "y"

[[standard]]
id = "accuracy.album.all.top1"
tier = "album"
dimension = "accuracy"
title = "a wildlife standard on the same tier"
scope = "all"
metric = "top1"
op = ">="
community = 0.5
unit = "fraction"
how = "x"
source = "y"
"""


def test_scorecard_reads_plugin_metrics_and_matches_profiles(tmp_path):
    f = tmp_path / "s.toml"
    f.write_text(STANDARDS)
    standards = bench.read_standards(f, REG)
    rows, preds = rows_and_preds([True, True, False, True])
    album = bench.build_report(rows, preds, registry=REG, preds_meta={"profile": "album"}, tier="album")
    sc = bench.scorecard(album, standards, "album")
    assert [(r["id"], r["status"], r["value"], r["judged_on"]) for r in sc["rows"]] == [
        ("culling.album.all.toy_hit", "pass", 0.75, "value"), ("culling.album.a.toy_hit", "pass", 1.0, "value")]
    assert sc["skipped"] == 1 and sc["profile"] == "album"
    plain = bench.build_report(rows, preds, registry=REG, tier="album")      # no profile: held to wildlife
    assert [r["id"] for r in bench.scorecard(plain, standards, "album")["rows"]] == ["accuracy.album.all.top1"]
    assert "profile album" in bench.scorecard_md(sc, album)


@pytest.mark.parametrize("change, message", [
    (('plugin = "toy"', 'plugin = "nope"'), "plugin must be one of"),
    (('metric = "toy_hit"', 'metric = "top1"'), "metric must be one of plugin toy's"),
    (('unit = "fraction"', 'unit = "ms"'), "rate metrics take unit"),
    (('profile = "album"', "profile = 3"), "profile must be a non-empty string"),
])
def test_bad_plugin_standards(tmp_path, change, message):
    f = tmp_path / "s.toml"
    f.write_text(STANDARDS.replace(*change, 1))
    with pytest.raises(bench.BenchError, match=message):
        bench.read_standards(f, REG)
