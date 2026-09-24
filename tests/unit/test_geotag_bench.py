"""scripts/geotag_synth.py (synthetic GPX scenarios from the committed golden CSV) and
`bioscan bench geotag`. Small cases only: the first outings of the golden set, no network."""
import csv
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from bioscan.cli import bench, geobench
from bioscan.cli import main as cli

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = ROOT / "data" / "inat" / "groundtruth-inat.csv"
OUTINGS = 12


def _synth():
    spec = importlib.util.spec_from_file_location("geotag_synth", ROOT / "scripts" / "geotag_synth.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["geotag_synth"] = mod               # dataclasses look their module up there
    spec.loader.exec_module(mod)
    return mod


synth = _synth()


@pytest.fixture(scope="module")
def scenarios(tmp_path_factory):
    out = tmp_path_factory.mktemp("synth")
    synth.generate(str(GOLDEN), out, seed=7, limit=OUTINGS)
    return out


@pytest.fixture(scope="module")
def report(scenarios):
    return geobench.build_report(scenarios)


def test_golden_times_are_classified_and_synthesised_consistently():
    shots = synth.load_shots(str(GOLDEN), 7)
    with open(GOLDEN, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(shots) == sum(1 for r in rows if r["lat"] and r["lon"])          # the one row without GPS
    counts = {p: sum(s.precision == p for s in shots) for p in ("second", "minute", "date")}
    assert counts["date"] == sum(len(r["taken_at"]) == 10 for r in rows if r["lat"])
    assert counts["minute"] > 500                       # iNaturalist keeps minutes only for many observations
    again = {s.path: s.t for s in synth.load_shots(str(GOLDEN), 7)}
    assert all(again[s.path] == s.t for s in shots)     # seeded per row: the same truth every time
    by_path = {r["path"]: r for r in rows}
    for s in shots:                                      # a minute-precision time stays inside its minute
        if s.precision == "minute":
            local = synth.datetime.fromtimestamp(s.t, synth.timezone(synth.timedelta(minutes=s.tz_min)))
            assert local.strftime("%Y-%m-%dT%H:%M") == by_path[s.path]["taken_at"][:16]


def test_generator_is_deterministic(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    synth.generate(str(GOLDEN), a, seed=3, scenarios=["perfect", "multi"], limit=4)
    synth.generate(str(GOLDEN), b, seed=3, scenarios=["perfect", "multi"], limit=4)
    files = sorted(p.relative_to(a) for p in a.rglob("*") if p.is_file())
    assert files and files == sorted(p.relative_to(b) for p in b.rglob("*") if p.is_file())
    assert all((a / f).read_bytes() == (b / f).read_bytes() for f in files)
    c = tmp_path / "c"
    synth.generate(str(GOLDEN), c, seed=4, scenarios=["perfect"], limit=4)
    assert (c / "perfect" / "photos.csv").read_bytes() != (a / "perfect" / "photos.csv").read_bytes()


def test_scenario_layout(scenarios):
    meta = json.loads((scenarios / "scenarios.json").read_text())
    assert set(meta["scenarios"]) == set(synth.SCENARIOS) and meta["outings"] == OUTINGS
    multi = scenarios / "multi" / "gpx"
    names = {p.name for p in multi.rglob("*.gpx")}
    assert "decoy.gpx" in names and "track-2.gpx" in names
    assert any(b'topografix.com/GPX/1/0' in p.read_bytes() for p in multi.rglob("*.gpx"))
    roles = {r["role"] for r in csv.DictReader(open(scenarios / "offset37" / "photos.csv"))}
    assert roles == {"photo", "ref"}
    clocks = [r for r in csv.DictReader(open(scenarios / "wrongtz" / "photos.csv")) if r["role"] == "clock"]
    assert len(clocks) == OUTINGS and all(r["clock"] for r in clocks)
    # the photos' own coordinates are never an input
    photos = [r for r in csv.DictReader(open(scenarios / "perfect" / "photos.csv"))]
    assert all(r["lat"] == "" for r in photos)


def test_geotag_recovers_positions_and_offsets(report):
    m = report["metrics"]
    assert m["perfect"]["median_error_m"] < 15 and m["perfect"]["within_100m_rate"] == 1.0
    assert m["multi"]["within_100m_rate"] == 1.0                   # split files + a decoy change nothing
    assert m["wrongtz"]["offset_error_s"] < 2                     # clock photo, whole seconds
    assert m["offset37"]["offset_error_s"] < 20 and m["dst"]["offset_error_s"] < 20
    assert m["outside"]["false_fix_rate"] in (0.0, None)
    assert m["outside"]["n_expected"] < m["outside"]["n"] or m["outside"]["false_fix_rate"] is None
    methods = {(g["scenario"], g["offset_method"]) for g in report["groups"]}
    assert ("wrongtz", "clock") in methods and ("dst", "gps") in methods and ("perfect", "none") in methods
    assert report["schema"] == bench.GEOTAG_SCHEMA and report["meta"]["tier"] == "geotag"


def test_standards_score_a_geotag_report(report, tmp_path):
    standards = bench.read_standards(bench.STANDARDS_TOML)
    geotag = [s for s in standards if bench.standard_tier(s) == "geotag"]
    assert {s["metric"] for s in geotag} == set(bench.GEOTAG_METRICS) - {"n", "n_expected"}
    sc = bench.scorecard(report, standards, "geotag")
    assert len(sc["rows"]) == len(geotag) and all(r["status"] in ("pass", "fail", "n/a") for r in sc["rows"])
    path = bench.write_json(report, tmp_path / "geotag.json")
    assert cli.main(["bench", "scorecard", str(path)]) in (0, 1)   # the scorecard command reads it too
    bad = tmp_path / "bad.toml"
    bad.write_text('[[standard]]\nid = "location.geotag.all.within_100m_rate"\ndimension = "location"\n'
                   'title = "t"\nscope = "all"\nmetric = "within_100m_rate"\nop = ">="\ncommunity = 95\n'
                   'unit = "m"\nhow = "h"\nsource = "s"\n')
    with pytest.raises(bench.BenchError, match="fraction"):
        bench.read_standards(bad)


def test_bench_geotag_command_and_gt_out(scenarios, tmp_path, capsys):
    code = cli.main(["bench", "geotag", str(scenarios), "--scenario", "perfect", "--json", str(tmp_path / "r.json"),
                     "--md", str(tmp_path / "r.md"), "--gt-out", str(tmp_path / "gt")])
    assert code in (0, 1)
    out = capsys.readouterr().out
    assert "| perfect |" in out and "bioscan bench scorecard" in out
    rep = json.loads((tmp_path / "r.json").read_text())
    assert set(rep["metrics"]) == {"all", "perfect"}
    with open(GOLDEN, newline="", encoding="utf-8") as f:
        golden = list(csv.DictReader(f))
    with open(tmp_path / "gt" / "perfect.csv", newline="", encoding="utf-8") as f:
        derived = list(csv.DictReader(f))
    assert len(derived) == len(golden) and [r["path"] for r in derived] == [r["path"] for r in golden]
    fixed = {r["path"]: r for r in rep["images"]}
    changed = [d for d, g in zip(derived, golden) if d["path"] in fixed]
    assert changed and all(d["lat"] == f"{fixed[d['path']]['lat']:.7f}" for d in changed)
    untouched = [(d, g) for d, g in zip(derived, golden) if d["path"] not in fixed]
    assert all(d == g for d, g in untouched)
    with pytest.raises(bench.BenchError, match="no scenario folder"):
        geobench.build_report(tmp_path / "gt")
