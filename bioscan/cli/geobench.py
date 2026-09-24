"""`bioscan bench geotag`: score geotagging against known positions.

Input is a folder of scenarios (scripts/geotag_synth.py writes one from the golden set; any set
built the same way works): SCENARIO/photos.csv (the inputs geotag sees), SCENARIO/truth.csv (true
positions, whether the photo's true time is inside the track, the true clock offset) and
SCENARIO/gpx/<group>/*.gpx. Each group is one outing: one camera, one --tz, its own tracks.

The report (schema `bioscan-geotag-report`, version 1) has metrics per scope (`all` and each
scenario) and one row per photo and per group. The scorecard of data/standards.toml's `geotag` tier
reads it like a species report. Standard library only.
"""
from __future__ import annotations

import csv
import json
import math
import statistics
import time
from collections import defaultdict
from pathlib import Path

from bioscan import geotag as gt
from bioscan.cli import bench

VERSION = 1
NEAR_M, FAR_M = 100.0, 1000.0


def _f(x) -> float | None:
    return float(x) if x not in (None, "") else None


def _read(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def scenario_dirs(root: Path, only: list[str] | None = None) -> list[Path]:
    dirs = sorted(d for d in root.iterdir() if (d / "photos.csv").is_file() and (d / "truth.csv").is_file()) \
        if root.is_dir() else []
    if (root / "photos.csv").is_file():
        dirs = [root]
    if only:
        dirs = [d for d in dirs if d.name in only]
    if not dirs:
        raise bench.BenchError(f"{root}: no scenario folder (photos.csv + truth.csv)" +
                               (f" named {', '.join(only)}" if only else ""))
    return dirs


def run_scenario(d: Path, options: dict | None = None) -> tuple[list[dict], list[dict]]:
    """(image rows, group rows) for one scenario folder."""
    options = options or {}
    photos, truth = _read(d / "photos.csv"), {r["path"]: r for r in _read(d / "truth.csv")}
    by_group: dict[str, list[dict]] = defaultdict(list)
    for r in photos:
        by_group[r["group"]].append(r)
    images, groups = [], []
    for g in sorted(by_group):
        rows = by_group[g]
        track = gt.Track.load(sorted((d / "gpx" / g).glob("*.gpx")))
        ph = [gt.Photo(r["path"], r["taken_at"] or None, _f(r.get("lat")), _f(r.get("lon")), r.get("clock") or None)
              for r in rows]
        res = gt.geotag(ph, track, gt.resolve_tz(rows[0].get("tz") or None), options.get("offset_s"),
                        max_gap_s=options.get("max_gap_s", gt.MAX_GAP_S),
                        max_span_m=options.get("max_span_m", gt.MAX_SPAN_M),
                        extrapolate_s=options.get("extrapolate_s", gt.EXTRAPOLATE_S),
                        max_still_s=options.get("max_still_s", gt.MAX_STILL_S))
        true_offsets = set()
        for r, fx in zip(rows, res.fixes):
            if r.get("role", "photo") != "photo" or fx.path not in truth:
                continue
            t = truth[fx.path]
            true_offsets.add(float(t.get("offset_s") or 0))
            tlat, tlon = float(t["lat"]), float(t["lon"])
            fixed = fx.lat is not None
            err = round(gt.distance_m(tlat, tlon, fx.lat, fx.lon), 2) if fixed else None
            images.append({"scenario": d.name, "group": g, "path": fx.path, "source": fx.source,
                           "lat": fx.lat, "lon": fx.lon, "truth_lat": tlat, "truth_lon": tlon,
                           "expect_fix": t.get("expect_fix", "1") in ("1", "true", "True"),
                           "error_m": err, "err_est_m": fx.err_m, "dt_s": fx.dt_s, "precision": t.get("precision"),
                           "cell_changed": None if not fixed else
                           (round(tlat, 2), round(tlon, 2)) != (round(fx.lat, 2), round(fx.lon, 2))})
        true_off = true_offsets.pop() if len(true_offsets) == 1 else None
        o = res.offset
        # An estimate was due when the group has reference or clock photos, or its clock is off. Its
        # error is |applied - true|, so a failed estimate (method none, 0 applied) counts in full.
        due = true_off is not None and o.method != "given" and (
            true_off != 0 or any(r.get("role") in ("ref", "clock") for r in rows))
        groups.append({"scenario": d.name, "group": g, "photos": sum(r.get("role", "photo") == "photo" for r in rows),
                       "track_points": res.track_points, "offset_method": o.method, "offset_s": o.offset_s,
                       "offset_true_s": true_off, "residual_m": o.residual_m,
                       "offset_error_s": round(abs(o.offset_s - true_off), 2) if due else None,
                       "warnings": res.warnings})
    return images, groups


def _pct_rank(vals: list[float], q: float) -> float | None:
    """Nearest-rank percentile (deterministic, no interpolation)."""
    if not vals:
        return None
    v = sorted(vals)
    return v[max(0, math.ceil(q * len(v)) - 1)]


def metrics_for(images: list[dict], groups: list[dict]) -> dict:
    exp = [r for r in images if r["expect_fix"]]
    out_ = [r for r in images if not r["expect_fix"]]
    errs = [r["error_m"] for r in exp if r["error_m"] is not None]
    counts = {
        "within_100m_rate": (sum(e <= NEAR_M for e in errs), len(exp)),
        "within_1km_rate": (sum(e <= FAR_M for e in errs), len(exp)),
        "no_fix_rate": (sum(r["error_m"] is None for r in exp), len(exp)),
        "false_fix_rate": (sum(r["error_m"] is not None for r in out_), len(out_)),
        "cell_change_rate": (sum(bool(r["cell_changed"]) for r in exp if r["cell_changed"] is not None), len(errs)),
    }
    m: dict = {"n": len(images), "n_expected": len(exp),
               "median_error_m": round(statistics.median(errs), 2) if errs else None,
               "p90_error_m": _pct_rank(errs, 0.9)}
    for k in bench.GEOTAG_RATES:
        num, den = counts[k]
        m[k] = round(num / den, 6) if den else None
        m[k + "_ci"] = bench.wilson(num, den)
    judged = [r for r in exp if r["error_m"] is not None and r["err_est_m"] is not None]
    m["err_est_coverage"] = round(sum(r["error_m"] <= r["err_est_m"] for r in judged) / len(judged), 6) if judged else None
    off = [g["offset_error_s"] for g in groups if g["offset_error_s"] is not None]
    m["offset_error_s"] = round(statistics.median(off), 2) if off else None
    m["offset_error_p90_s"] = _pct_rank(off, 0.9)
    m["offset_groups"] = len(off)
    m["offset_failed"] = sum(g["offset_error_s"] is not None and g["offset_method"] == "none" for g in groups)
    return m


def build_report(root: Path, only: list[str] | None = None, options: dict | None = None) -> dict:
    images, groups = [], []
    for d in scenario_dirs(root, only):
        i, g = run_scenario(d, options)
        images += i
        groups += g
    metrics = {"all": metrics_for(images, groups)}
    for name in dict.fromkeys(r["scenario"] for r in groups):
        metrics[name] = metrics_for([r for r in images if r["scenario"] == name],
                                    [g for g in groups if g["scenario"] == name])
    synth = root / "scenarios.json"
    sha, dirty = bench._git()
    meta = {"tier": "geotag", "git_sha": sha, "git_dirty": dirty,
            "date": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "groundtruth": str(root),
            "synth": json.loads(synth.read_text(encoding="utf-8")) if synth.is_file() else None,
            "options": {"max_gap_s": gt.MAX_GAP_S, "max_span_m": gt.MAX_SPAN_M, "max_still_s": gt.MAX_STILL_S,
                        "extrapolate_s": gt.EXTRAPOLATE_S,
                        **(options or {})}}
    return {"schema": bench.GEOTAG_SCHEMA, "version": VERSION, "meta": meta, "metrics": metrics,
            "groups": groups, "images": images}


def _m(v, unit: str = "") -> str:
    if v is None:
        return "–"
    return f"{v * 100:.1f}%" if unit == "%" else f"{v:g}"


def report_md(rep: dict) -> str:
    lines = ["# bioscan bench geotag", "",
             f"- scenarios: {rep['meta']['groundtruth']} (git {str(rep['meta']['git_sha'])[:12]}, {rep['meta']['date']})",
             f"- fix rule: max gap {rep['meta']['options']['max_gap_s']:g} s, max span "
             f"{rep['meta']['options']['max_span_m']:g} m within {rep['meta']['options']['max_still_s']:g} s, "
             f"extrapolate {rep['meta']['options']['extrapolate_s']:g} s", "",
             "Errors are over photos whose true time is inside the track (`expected`); a photo there without a fix "
             "counts against the within-rates and as `no fix`. `false fix`: a fix for a photo outside the track. "
             "`cell changed`: the fix and the truth round to different 0.01° cells (the location prior's cache key). "
             "Offset error: median |applied − true| over the groups where an estimate was due (reference or clock "
             "photos, or a clock that is off); a failed estimate applies 0 and counts in full. "
             "`err ≤ est`: share of fixes whose true error is within geotag's own `err_m` estimate.", "",
             "| scope | n | expected | median m | p90 m | ≤100 m | ≤1 km | no fix | false fix | cell changed "
             "| err ≤ est | offset err s (median / p90, groups, failed) |", "|---" * 12 + "|"]
    for s, m in rep["metrics"].items():
        lines.append(f"| {s} | {m['n']} | {m['n_expected']} | {_m(m['median_error_m'])} | {_m(m['p90_error_m'])} | "
                     f"{_m(m['within_100m_rate'], '%')} | {_m(m['within_1km_rate'], '%')} | {_m(m['no_fix_rate'], '%')} | "
                     f"{_m(m['false_fix_rate'], '%')} | {_m(m['cell_change_rate'], '%')} | {_m(m['err_est_coverage'], '%')} | "
                     f"{_m(m['offset_error_s'])} / {_m(m['offset_error_p90_s'])} ({m['offset_groups']}, "
                     f"{m['offset_failed']}) |")
    methods = defaultdict(lambda: defaultdict(int))
    for g in rep["groups"]:
        methods[g["scenario"]][g["offset_method"]] += 1
    lines += ["", "Offset method per scenario: " + "; ".join(
        f"{s} " + ", ".join(f"{k} {v}" for k, v in sorted(c.items())) for s, c in methods.items()), ""]
    return "\n".join(lines)


def write_gt(rep: dict, source_csv: str, out_dir: Path) -> list[Path]:
    """Per scenario, the source ground truth with lat/lon replaced by the geotag fix (blank: no fix),
    to run `bioscan bench run` on GPX-derived coordinates."""
    with open(source_csv, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fields, rows = reader.fieldnames or [], list(reader)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name in dict.fromkeys(r["scenario"] for r in rep["images"]):
        fix = {r["path"]: r for r in rep["images"] if r["scenario"] == name}
        path = out_dir / f"{name}.csv"
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            for r in rows:
                x = fix.get(r["path"])
                if x is not None:
                    r = {**r, "lat": "" if x["lat"] is None else f"{x['lat']:.7f}",
                         "lon": "" if x["lon"] is None else f"{x['lon']:.7f}"}
                w.writerow(r)
        written.append(path)
    return written


def cmd_geotag(a) -> int:
    root = Path(a.scenarios)
    options = {k: v for k, v in (("extrapolate_s", a.extrapolate), ("max_gap_s", a.max_gap),
                                 ("max_span_m", a.max_span), ("max_still_s", a.max_still)) if v is not None}
    rep = build_report(root, a.scenario, options)
    md = report_md(rep)
    standards = bench.read_standards(a.standards or bench.STANDARDS_TOML)
    sc = bench.scorecard(rep, standards, "geotag")
    md += "\n" + bench.scorecard_md(sc, rep)
    print(md)
    bench._write_md(md, a.md)
    if a.json:
        bench.write_json(rep, a.json)
    if a.gt_out:
        src = (rep["meta"]["synth"] or {}).get("source")
        if not src:
            raise bench.BenchError("--gt-out needs scenarios.json (from scripts/geotag_synth.py) naming the source CSV")
        for p in write_gt(rep, src, Path(a.gt_out)):
            print(f"ground truth with GPX coordinates -> {p}")
    return bench.EXIT_OVER if any(r["status"] == "fail" for r in sc["rows"]) else bench.EXIT_OK


def add_parser(b) -> None:
    s = b.add_parser("geotag", help="score GPX geotagging on scenario folders (scripts/geotag_synth.py); "
                                    "exit 1 when a geotag standard is missed")
    s.add_argument("scenarios", help="folder of scenario folders, or one scenario folder")
    s.add_argument("--scenario", action="append", help="only this scenario (repeatable)")
    s.add_argument("--extrapolate", type=float, help=f"seconds to hold the track's ends (default {gt.EXTRAPOLATE_S:g})")
    s.add_argument("--max-gap", type=float, help=f"geotag --max-gap (default {gt.MAX_GAP_S:g})")
    s.add_argument("--max-span", type=float, help=f"geotag --max-span (default {gt.MAX_SPAN_M:g})")
    s.add_argument("--max-still", type=float, help=f"geotag --max-still (default {gt.MAX_STILL_S:g})")
    s.add_argument("--standards", help=f"standards TOML (default {bench.STANDARDS_TOML})")
    s.add_argument("--md", help="also write the markdown here")
    s.add_argument("--json", help="write the report (schema bioscan-geotag-report) here")
    s.add_argument("--gt-out", help="write <scenario>.csv here: the source ground truth with GPX-derived lat/lon")
    s.set_defaults(func=cmd_geotag)
