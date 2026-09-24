"""data/standards.toml follows the schema `bioscan bench scorecard` reads (docs/standards.md). Stdlib only."""
import math
import re
import tomllib
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[2] / "data" / "standards.toml"

REQUIRED = {"id", "dimension", "title", "scope", "metric", "op", "community", "unit", "how", "source"}
OPTIONAL = {"industry", "stretch"}          # TOML has no null: a missing key means null
METRICS = {"n", "gate_acc", "detect_rate", "top1", "top5", "genus_acc", "coverage", "precision",
           "confident_error_rate", "no_box_rate", "failed_rate", "ece", "decode_ms_median",
           "identify_ms_median", "images_per_s"}
RATES = METRICS - {"n", "decode_ms_median", "identify_ms_median", "images_per_s"}
DIMENSIONS = {"accuracy", "trust", "detection", "location", "directory", "speed", "coverage", "robustness",
              "onboarding", "privacy", "reproducibility"}
SCOPES = {"all", "bird", "mammal", "other"}
OPS = {">=", "<="}
TIERS = {"smoke", "golden", "own", "public", "mac"}
VARIANTS = {"nogeo"}
UNITS = {"fraction", "ms", "images/s", "images", "species", "formats", "minutes", "requests", "bool"}


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
    assert keys <= REQUIRED | OPTIONAL, f"unknown {keys - REQUIRED - OPTIONAL}"
    for k in ("id", "dimension", "title", "unit", "how", "source"):
        assert isinstance(s[k], str) and s[k].strip(), k
    assert s["dimension"] in DIMENSIONS
    assert s["scope"] in SCOPES
    assert s["op"] in OPS
    assert s["unit"] in UNITS
    assert s["metric"] in METRICS or s["metric"] == "manual"
    assert is_number(s["community"])
    for k in OPTIONAL & keys:
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
    if s["metric"] in RATES:
        assert s["unit"] == "fraction"
    if s["unit"] == "fraction":
        for k in ("industry", "community", "stretch"):
            if k in s:
                assert 0 <= s[k] <= 1, k


def test_industry_numbers_cite_a_url():
    missing = [s["id"] for s in standards() if "industry" in s and "https://" not in s["source"]]
    assert not missing
