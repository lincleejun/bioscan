"""data/standards.toml follows the schema `bioscan bench scorecard` reads (docs/standards.md). Stdlib only."""
import math
import re
import tomllib
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[2] / "data" / "standards.toml"

REQUIRED = {"id", "dimension", "title", "scope", "metric", "op", "community", "unit", "how", "source"}
OPTIONAL = {"industry", "stretch"}          # TOML has no null: a missing key means null
NAMES = {"tier", "profile", "plugin"}       # optional strings
METRICS = {"n", "gate_acc", "detect_rate", "top1", "top5", "genus_acc", "coverage", "precision",
           "confident_error_rate", "no_box_rate", "failed_rate", "ece", "decode_ms_median",
           "identify_ms_median", "images_per_s"}
RATES = METRICS - {"n", "decode_ms_median", "identify_ms_median", "images_per_s"}
GEOTAG_RATES = {"within_100m_rate", "within_1km_rate", "no_fix_rate", "false_fix_rate", "cell_change_rate"}
GEOTAG_METRICS = GEOTAG_RATES | {"n", "n_expected", "median_error_m", "p90_error_m", "offset_error_s"}
AESTHETIC_RATES = {"precision_at_k"}
AESTHETIC_METRICS = AESTHETIC_RATES | {"n", "spearman", "kendall", "plcc", "spearman_trip_mean", "ndcg_at_k"}
DIMENSIONS = {"accuracy", "trust", "detection", "location", "directory", "speed", "coverage", "robustness",
              "onboarding", "privacy", "reproducibility", "aesthetics", "culling"}
SCOPES = {"all", "bird", "mammal", "other"}
OPS = {">=", "<="}
TIERS = {"smoke", "golden", "own", "public", "mac", "geotag", "aesthetic_own", "album"}
PROFILES = {"wildlife", "album"}            # no profile = wildlife; full is held to the wildlife standards


def plugin_metrics() -> dict:
    """plugin -> {metric name: Metric} of the built-in plugins (stages and reducers)."""
    from bioscan.plugins import BUILTIN, REDUCERS

    return {m.name: {x.name: x for x in m.metrics} for m in (*BUILTIN, *REDUCERS) if m.metrics}
VARIANTS = {"nogeo"}
UNITS = {"fraction", "ms", "images/s", "images", "species", "formats", "minutes", "requests", "bool", "m", "s",
         "correlation", "score"}


def standards() -> list[dict]:
    with open(PATH, "rb") as f:
        doc = tomllib.load(f)
    assert set(doc) == {"standard"}, "only [[standard]] tables at the top level"
    return doc["standard"]


def is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def test_file_parses_and_is_not_empty():
    assert len(standards()) >= 20


def test_ids_are_unique():
    ids = [s["id"] for s in standards()]
    assert len(ids) == len(set(ids)), sorted(i for i in ids if ids.count(i) > 1)


@pytest.mark.parametrize("s", standards(), ids=lambda s: s.get("id", "?"))
def test_fields(s):
    keys = set(s)
    assert REQUIRED <= keys, f"missing {REQUIRED - keys}"
    assert keys <= REQUIRED | OPTIONAL | NAMES, f"unknown {keys - REQUIRED - OPTIONAL - NAMES}"
    for k in ("id", "dimension", "title", "unit", "how", "source"):
        assert isinstance(s[k], str) and s[k].strip(), k
    assert s["dimension"] in DIMENSIONS
    assert s["op"] in OPS
    assert s["unit"] in UNITS
    tier = s["id"].split(".")[1]
    assert s.get("profile", "wildlife") in PROFILES
    if "tier" in s:                       # an explicit tier (aesthetic-own) is the id's segment with _ for -
        assert isinstance(s["tier"], str) and s["tier"].replace("-", "_") == tier
    if "plugin" in s:                   # a plugin metric: report.json plugin_metrics[plugin][scope]
        metric = plugin_metrics()[s["plugin"]][s["metric"]]
        assert s["unit"] == ("fraction" if metric.fraction else s["unit"]) and s.get("profile") == "album"
        assert isinstance(s["scope"], str) and s["scope"]
    else:
        assert s["scope"] in SCOPES
        assert s["metric"] in METRICS or s["metric"] == "manual" or (tier == "geotag" and s["metric"] in GEOTAG_METRICS) \
            or (tier == "aesthetic_own" and s["metric"] in AESTHETIC_METRICS)
    assert is_number(s["community"])
    for k in {"industry", "stretch"} & keys:
        assert is_number(s[k]), k


@pytest.mark.parametrize("s", standards(), ids=lambda s: s.get("id", "?"))
def test_id_names_dimension_tier_scope_metric(s):
    parts = s["id"].split(".")
    assert all(re.fullmatch(r"[a-z0-9_]+", p) for p in parts), s["id"]
    assert parts[0] == s["dimension"]
    if s["metric"] == "manual":
        assert len(parts) >= 2
        return
    assert len(parts) in (4, 5), "report metrics: <dimension>.<tier>.<scope>.<metric>[.<variant>]"
    assert parts[1] in TIERS
    assert parts[2] == s["scope"]
    assert parts[3] == s["metric"]
    if len(parts) == 5:
        assert parts[4] in VARIANTS
        if parts[4] == "nogeo":
            assert "--no-geo" in s["how"]


@pytest.mark.parametrize("s", standards(), ids=lambda s: s.get("id", "?"))
def test_bars_are_ordered(s):
    if "stretch" not in s:
        return
    if s["op"] == ">=":
        assert s["community"] <= s["stretch"]
    else:
        assert s["stretch"] <= s["community"]


@pytest.mark.parametrize("s", standards(), ids=lambda s: s.get("id", "?"))
def test_rates_are_fractions(s):
    if s["metric"] in RATES | GEOTAG_RATES | AESTHETIC_RATES or ("plugin" in s
                                                                and plugin_metrics()[s["plugin"]][s["metric"]].fraction):
        assert s["unit"] == "fraction"
    if s["unit"] == "fraction":
        for k in ("industry", "community", "stretch"):
            if k in s:
                assert 0 <= s[k] <= 1, k


def test_the_harness_reads_the_file():
    from bioscan.cli import bench

    assert len(bench.read_standards(PATH)) == len(standards())


def test_industry_numbers_cite_a_url():
    missing = [s["id"] for s in standards() if "industry" in s and "https://" not in s["source"]]
    assert not missing
