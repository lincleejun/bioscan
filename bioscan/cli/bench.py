"""`bioscan bench`: the evaluation harness on top of `bioscan eval`.

report.json (schema `bioscan-report`, version 1) is one eval run as data: metrics per scope with
Wilson intervals, per-species and per-family tables, and one row per image. A baseline is a
committed report; `compare` holds a new report against it under a regression budget; `analyze`
sorts every wrong answer into a failure class with a fix pointer; `scorecard` checks a report
against the standards file. docs/harness.md has the workflow and the schema.

Standard library only, like the rest of the CLI. Per-image scoring is eval.outcome; this module
only adds the fields eval's markdown never needed (genus, level, p values, family).
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
import shutil
import statistics
import subprocess
import time
import tomllib
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from bioscan import contract, naming
from bioscan.cli import eval as ev
from bioscan.cli.config import PROFILE_HELP, eval_request

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BASELINES_DIR = PROJECT_ROOT / "baselines"
BUDGET_TOML = BASELINES_DIR / "budget.toml"
STANDARDS_TOML = PROJECT_ROOT / "data" / "standards.toml"

REPORT_SCHEMA, REPORT_VERSION = "bioscan-report", 1
COMPARE_SCHEMA, ANALYSIS_SCHEMA = "bioscan-compare", "bioscan-analysis"
SCOPES = ("all", "bird", "mammal", "other")
RATES = ("gate_acc", "detect_rate", "top1", "top5", "genus_acc", "coverage", "precision", "confident_error_rate",
         "no_box_rate", "failed_rate")
METRICS = ("n", *RATES, "ece", "decode_ms_median", "identify_ms_median", "images_per_s")
LOWER_IS_BETTER = {"confident_error_rate", "no_box_rate", "failed_rate", "ece", "decode_ms_median",
                   "identify_ms_median"}
FRACTIONS = set(RATES) | {"ece"}          # 0-1 in the report; percent in markdown and standards.toml
Z95 = 1.959963984540054
OUT_OF_RANGE_P_GEO = 0.01                  # a top-1 below this p_geo, where the place is known, is out of range
EXAMPLES = 5

EXIT_OK, EXIT_OVER, EXIT_INCOMPARABLE = 0, 1, 2

# `bench geotag` (bioscan.cli.geobench): its own report schema, scored by the same scorecard.
GEOTAG_SCHEMA = "bioscan-geotag-report"
GEOTAG_RATES = ("within_100m_rate", "within_1km_rate", "no_fix_rate", "false_fix_rate", "cell_change_rate")
GEOTAG_METRICS = ("n", "n_expected", "median_error_m", "p90_error_m", *GEOTAG_RATES, "offset_error_s")
# `aesthetic eval` (bioscan.cli.aesbench): agreement of an aesthetic head with the owner's ratings.
AESTHETIC_SCHEMA = "bioscan-aesthetic-report"
AESTHETIC_RATES = ("precision_at_k",)
AESTHETIC_METRICS = ("n", "spearman", "kendall", "plcc", "spearman_trip_mean", "ndcg_at_k", *AESTHETIC_RATES)


class BenchError(Exception):
    """A file the harness cannot use (unreadable, wrong schema, invalid budget or standards)."""


# ---- statistics ----------------------------------------------------------------------------

def wilson(k: int, n: int, z: float = Z95) -> list[float] | None:
    """Wilson score interval for k successes in n trials; None when n == 0."""
    if not n:
        return None
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [round(max(0.0, centre - half), 6), round(min(1.0, centre + half), 6)]


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact McNemar p for b and c discordant pairs (binomial, p = 0.5)."""
    n = b + c
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, i) for i in range(min(b, c) + 1))
    return min(1.0, 2 * tail / 2 ** n)


def ece(pairs: list[tuple[float, bool]], bins: int = 10) -> float | None:
    """Expected calibration error over (stated probability, correct) pairs, equal-width bins."""
    if not pairs:
        return None
    total = 0.0
    for i in range(bins):
        lo, hi = i / bins, (i + 1) / bins
        b = [(p, ok) for p, ok in pairs if lo <= p < hi or (i == bins - 1 and p == 1.0)]
        if b:
            total += len(b) * abs(sum(p for p, _ in b) / len(b) - sum(ok for _, ok in b) / len(b))
    return round(total / len(pairs), 6)


# ---- name lists and families ---------------------------------------------------------------

def scope_of(kind: str | None) -> str:
    return kind if kind in ("bird", "mammal") else "other"


def genus_of(name: str | None) -> str:
    return naming.norm_binomial(name).split(" ")[0]


def read_name_list(path: str | Path) -> dict[str, str]:
    """norm(scientific) -> family from one list CSV: avilist_map.csv (scientific, family), the
    AviList CSV (Scientific_name, Family, Taxon_rank) or MDD (genus, specificEpithet, family)."""
    out: dict[str, str] = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r.get("Taxon_rank") not in (None, "species"):
                continue
            sci = r.get("scientific") or r.get("Scientific_name") or " ".join(
                x for x in (r.get("genus"), r.get("specificEpithet")) if x)
            if sci:
                out[naming.norm_binomial(sci)] = (r.get("family") or r.get("Family") or "").strip()
    return out


def default_name_lists() -> dict[str, str]:
    """kind -> list CSV: the committed avilist_map.csv; an MDD CSV under data/mdd when present."""
    lists = {"bird": str(naming.AVILIST_MAP_CSV)} if naming.AVILIST_MAP_CSV.is_file() else {}
    mdd = sorted((naming.DATA_DIR / "mdd").glob("*.csv"))
    if mdd:
        lists["mammal"] = str(mdd[0])
    return lists


def load_name_lists(specs: dict[str, str]) -> dict[str, dict[str, str]]:
    return {kind: read_name_list(p) for kind, p in specs.items()}


# ---- report ----------------------------------------------------------------------------------

def _best(ident) -> dict | None:
    boxes = contract.boxes_of(ident)
    return max(boxes, key=lambda b: b.get("score", 0)) if boxes else None


def _family(name: str | None, taxonomy: list | None, families: dict[str, str]) -> str | None:
    if taxonomy and len(taxonomy) > 4 and taxonomy[4]:
        return taxonomy[4]
    return families.get(naming.norm_binomial(name)) or None


def _truth_candidate(sci: str, top: list[dict]) -> dict:
    """Where the truth sits among the best box's candidates: its rank by posterior (1 = top-1), its
    rank by p_visual among the listed candidates, and its p values. All None when it is not listed."""
    key = naming.norm_binomial(sci)
    j = next((j for j, t in enumerate(top) if naming.norm_binomial(t.get("scientific")) == key), None)
    if j is None:
        return {"truth_rank": None, "truth_visual_rank": None, "truth_p_visual": None, "truth_p_geo": None,
                "truth_posterior": None}
    t = top[j]
    pv = t.get("p_visual")
    vrank = None if pv is None else 1 + sum((c.get("p_visual") or 0) > pv for c in top)
    return {"truth_rank": j + 1, "truth_visual_rank": vrank, "truth_p_visual": pv, "truth_p_geo": t.get("p_geo"),
            "truth_posterior": t.get("posterior")}


def image_row(truth: dict, pev: dict | None, lists: dict[str, dict[str, str]], families: dict[str, str]) -> dict:
    """One image of report.json. Correctness (gate, detected, top1, top5, level) is eval.outcome."""
    o = ev.outcome(truth, pev)
    ok = bool(pev) and pev.get("type") == contract.RESULT
    ident = contract.identify_of(pev) if ok else None
    best = _best(ident)
    sp = contract.species_of(best) if best else None
    top = contract.top_of(sp)
    c = top[0] if top else {}
    sci, kind = truth.get("scientific") or "", truth.get("kind") or ""
    listed = lists.get(kind)
    return {
        "path": truth["path"], "sha256": (pev or {}).get("sha256"), "tier": truth.get("tier") or "?",
        "truth": sci, "truth_raw": truth.get("scientific_raw", sci), "kind": kind, "scope": scope_of(kind),
        "failed": not ok, "error": (pev or {}).get("message") if pev and not ok else (None if pev else "missing"),
        "gate": contract.gate_class_of(ident), "gate_ok": o["gate"], "detected": o["detected"],
        "has_box": best is not None, "box_kind": (best or {}).get("kind"),
        "top1": c.get("scientific"), "top5": [t.get("scientific") for t in top[:5]],
        "level": contract.level_of(sp), "correct_top1": o["top1"], "correct_top5": o["top5"],
        "correct_genus": bool(c.get("scientific")) and genus_of(c.get("scientific")) == genus_of(sci),
        "p_visual": c.get("p_visual"), "p_geo": c.get("p_geo"), "posterior": c.get("posterior"),
        "p_correct": (sp or {}).get("p_correct"),
        **_truth_candidate(sci, top),
        "family_truth": families.get(naming.norm_binomial(sci)) or None,
        "family_pred": _family(c.get("scientific"), c.get("taxonomy"), families) if c else None,
        "in_list": None if listed is None else naming.norm_binomial(sci) in listed,
        "place_known": bool(truth.get("lat") and truth.get("lon")) or c.get("p_geo") is not None,
        "decode_ms": o["decode_ms"], "identify_ms": o["identify_ms"],
    }


def _median(vals) -> float | None:
    vals = [v for v in vals if isinstance(v, (int, float))]
    return round(statistics.median(vals), 3) if vals else None


def metrics_for(rows: list[dict], images_per_s: float | None = None) -> dict:
    """Every metric of one scope; each rate with its Wilson interval as `<key>_ci`."""
    n = len(rows)
    sp = [r for r in rows if r["level"] == "species"]
    counts = {
        "gate_acc": (sum(r["gate_ok"] for r in rows), n),
        "detect_rate": (sum(r["detected"] for r in rows), n),
        "top1": (sum(r["correct_top1"] for r in rows), n),
        "top5": (sum(r["correct_top5"] for r in rows), n),
        "genus_acc": (sum(r["correct_genus"] for r in rows), n),
        "coverage": (len(sp), n),
        "precision": (sum(r["correct_top1"] for r in sp), len(sp)),
        "confident_error_rate": (sum(not r["correct_top1"] for r in sp), n),
        "no_box_rate": (sum(not r["failed"] and not r["has_box"] for r in rows), n),
        "failed_rate": (sum(r["failed"] for r in rows), n),
    }
    m: dict[str, Any] = {"n": n}
    for key in RATES:
        k, den = counts[key]
        m[key] = round(k / den, 6) if den else None
        m[key + "_ci"] = wilson(k, den)
    m["ece"] = ece([(min(1.0, max(0.0, float(r["p_correct"]))), r["correct_top1"]) for r in rows   # clamped
                    if isinstance(r.get("p_correct"), (int, float)) and not isinstance(r["p_correct"], bool)
                    and math.isfinite(r["p_correct"])])
    m["decode_ms_median"] = _median(r["decode_ms"] for r in rows)
    m["identify_ms_median"] = _median(r["identify_ms"] for r in rows)
    m["images_per_s"] = images_per_s
    return m


# ---- plugin metrics (report.json plugin_metrics / plugin_images) ------------------------------

def metric_registry() -> dict[str, tuple]:
    """plugin name -> its declared plugin.Metric tuple, for every built-in stage and reducer that has any."""
    from bioscan.plugins import BUILTIN, REDUCERS

    return {m.name: m.metrics for m in (*BUILTIN, *REDUCERS) if m.metrics}


def _metric(registry: dict[str, tuple], plugin: str, name: str):
    return next((m for m in registry.get(plugin, ()) if m.name == name), None)


def plugin_values(truth: dict, pev: dict | None, registry: dict[str, tuple]) -> dict:
    """{plugin: {metric: {scope: value}}} of one image from each Metric.row; scopes with value None
    and empty metrics are left out."""
    from bioscan import plugin

    out: dict[str, dict] = {}
    for name, metrics in registry.items():
        for m in metrics:
            got = plugin.load(m.row)(truth, pev) or {}
            got = {s: (list(v) if isinstance(v, tuple) else v) for s, v in got.items() if v is not None}
            if got:
                out.setdefault(name, {})[m.name] = got
    return out


def _pairs(values: list) -> tuple[int, int, int]:
    """(true-positive pairs, truth pairs, predicted pairs) over (truth group, predicted group) per
    image: every pair of images is in the same truth group, the same predicted group, both or neither."""
    both, truth, pred = Counter(), Counter(), Counter()
    for t, p in values:
        both[(t, p)] += 1
        truth[t] += 1
        pred[p] += 1

    def pairs(c: Counter) -> int:
        return sum(n * (n - 1) // 2 for n in c.values())
    return pairs(both), pairs(truth), pairs(pred)


def _aggregate(m, values: list) -> dict:
    """One metric of one scope: `<name>`, `<name>_ci` (fractions with a denominator) and `<name>_n`."""
    if m.kind == "rate":
        k, n = sum(bool(v) for v in values), len(values)
        return {m.name: round(k / n, 6) if n else None, m.name + "_ci": wilson(k, n), m.name + "_n": n}
    if m.kind == "median":
        return {m.name: _median(values), m.name + "_n": len(values)}
    tp, t, p = _pairs(values)
    if m.kind == "pair_precision":
        return {m.name: round(tp / p, 6) if p else None, m.name + "_ci": wilson(tp, p), m.name + "_n": p}
    if m.kind == "pair_recall":
        return {m.name: round(tp / t, 6) if t else None, m.name + "_ci": wilson(tp, t), m.name + "_n": t}
    prec, rec = (tp / p if p else None), (tp / t if t else None)
    f1 = None if prec is None or rec is None else (2 * prec * rec / (prec + rec) if prec + rec else 0.0)
    return {m.name: None if f1 is None else round(f1, 6), m.name + "_n": len(values)}


def plugin_metrics_for(entries: list[dict], registry: dict[str, tuple]) -> dict:
    """plugin_metrics from plugin_images entries: {plugin: {scope: {n, <metric>, <metric>_ci,
    <metric>_n}}}, scope `all` first. `n` is the images with any value of the plugin in that scope."""
    values: dict[str, dict[str, dict[str, list]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    counts: dict[str, Counter] = defaultdict(Counter)
    for e in entries:
        for plugin, per_metric in (e.get("values") or {}).items():
            scopes = set()
            for metric, per_scope in per_metric.items():
                for scope, v in per_scope.items():
                    values[plugin][scope][metric].append(tuple(v) if isinstance(v, list) else v)
                    scopes.add(scope)
            for scope in scopes:
                counts[plugin][scope] += 1
    out: dict[str, dict] = {}
    for plugin in sorted(values):
        scopes = sorted(values[plugin], key=lambda s: (s != "all", s))
        out[plugin] = {}
        for scope in scopes:
            row: dict[str, Any] = {"n": counts[plugin][scope]}
            for m in registry.get(plugin, ()):
                if m.name in values[plugin][scope]:
                    row |= _aggregate(m, values[plugin][scope][m.name])
            out[plugin][scope] = row
    return out


def _git() -> tuple[str | None, bool | None]:
    try:
        sha = subprocess.run(["git", "-C", str(PROJECT_ROOT), "rev-parse", "HEAD"], capture_output=True, text=True,
                             timeout=10).stdout.strip() or None
        dirty = bool(subprocess.run(["git", "-C", str(PROJECT_ROOT), "status", "--porcelain", "--untracked-files=no"],
                                    capture_output=True, text=True, timeout=10).stdout.strip()) if sha else None
    except (OSError, subprocess.SubprocessError):
        sha, dirty = None, None
    return sha or os.environ.get("GITHUB_SHA") or None, dirty


def sha256_of(path) -> str | None:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest() if path and Path(path).is_file() else None


def build_report(rows: list[dict], preds: dict[str, dict], *, groundtruth: str | None = None,
                 synonyms_sha256: str | None = None, preds_meta: dict | None = None, done: dict | None = None,
                 lists: dict[str, dict[str, str]] | None = None, preds_path: str | None = None,
                 complete: bool | None = None, options: dict | None = None, tier: str | None = None,
                 profile: str | None = None, reducers: dict[str, dict] | None = None,
                 registry: dict[str, tuple] | None = None) -> dict:
    """report.json from ground-truth rows (already synonym-normalised) and preds (path -> event).
    `tier` names the standards tier the run belongs to (smoke, golden, own, public, mac, album), for
    the scorecard. `profile` (default: the preds meta line's) goes to meta.profile. `reducers`
    ({name: options}, default: the preds meta line's) run over the results first (bioscan.cull), so
    their products reach the plugin metrics; `registry` (plugin -> Metric tuple) defaults to the
    built-in plugins' metrics."""
    preds_meta = preds_meta or {}
    reducers = reducers if reducers is not None else preds_meta.get("reducers") or None
    if reducers:
        from bioscan import cull

        try:
            preds = {e["path"]: e for e in cull.apply(list(preds.values()), reducers)}
        except ValueError as e:
            raise BenchError(f"reducers {sorted(reducers)} (preds meta line): {e}") from e
    registry = metric_registry() if registry is None else registry
    lists = lists or {}
    families = {k: v for nl in lists.values() for k, v in nl.items() if v}
    for pev in preds.values():                       # the candidates' own taxonomy fills in the rest
        for b in contract.boxes_of(contract.identify_of(pev)) if pev.get("type") == contract.RESULT else []:
            for c in contract.top_of(contract.species_of(b)):
                tax = c.get("taxonomy") or []
                if len(tax) > 4 and tax[4]:
                    families.setdefault(naming.norm_binomial(c.get("scientific")), tax[4])
    images = [image_row(r, preds.get(r["path"]), lists, families) for r in rows]
    plugin_images = []
    for r, img in zip(rows, images):
        values = plugin_values(r, preds.get(r["path"]), registry) if registry else {}
        if values:
            plugin_images.append({"path": img["path"], "sha256": img["sha256"], "tier": img["tier"], "values": values})
    elapsed, ok = (done or {}).get("elapsed_ms"), (done or {}).get("ok")
    ips = round(ok / (elapsed / 1000), 4) if elapsed and ok else None
    metrics = {s: metrics_for([r for r in images if s == "all" or r["scope"] == s], ips if s == "all" else None)
               for s in SCOPES}
    if any(r.get("tier") for r in rows):
        for gt_tier in sorted({r["tier"] for r in images}):
            in_tier = [r for r in images if r["tier"] == gt_tier]
            for s in SCOPES:
                sel = [r for r in in_tier if s == "all" or r["scope"] == s]
                if sel:
                    metrics[f"{gt_tier}/{s}"] = metrics_for(sel)
    engines = sorted({json.dumps(p["engine"], sort_keys=True) for p in preds.values() if p.get("engine")})
    engine = json.loads(engines[0]) if len(engines) == 1 else [json.loads(e) for e in engines] or None
    fingerprints = sorted({(p.get("engine") or {}).get("settings") for p in preds.values()} - {None})
    sha, dirty = _git()
    meta = {
        "tier": tier, "git_sha": sha, "git_dirty": dirty, "date": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "engine": engine, "settings_fingerprint": fingerprints[0] if len(fingerprints) == 1 else fingerprints or None,
        "groundtruth": groundtruth, "groundtruth_sha256": sha256_of(groundtruth),
        "preds_groundtruth_sha256": preds_meta.get("groundtruth_sha256"),
        "synonyms_sha256": synonyms_sha256,
        "options": options if options is not None else preds_meta.get("options"),
        "n": len(images), "preds": preds_path, "preds_sha256": sha256_of(preds_path),
        "preds_schema": preds_meta.get("schema"), "complete": complete,
        "done": {k: done.get(k) for k in ("ok", "failed", "elapsed_ms")} if done else None,
        "name_lists": sorted(lists),
        "profile": profile if profile is not None else preds_meta.get("profile"),
        "reducers": reducers or None,
    }
    return {"schema": REPORT_SCHEMA, "version": REPORT_VERSION, "meta": meta, "metrics": metrics,
            "per_species": _table(images, lambda r: r["truth"], with_kind=True),
            "per_family": _table(images, lambda r: r["family_truth"] or "(unknown)"),
            "images": images,
            "plugin_metrics": plugin_metrics_for(plugin_images, registry), "plugin_images": plugin_images}


def _table(images: list[dict], key, with_kind: bool = False) -> dict:
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in images:
        groups[key(r)].append(r)
    out = {}
    for k in sorted(groups):
        g = groups[k]
        hits = sum(r["correct_top1"] for r in g)
        row = {"kind": g[0]["kind"], "family": g[0]["family_truth"]} if with_kind else {}
        out[k] = {**row, "n": len(g), "top1_hits": hits, "top1": round(hits / len(g), 6),
                  "confident_errors": sum(r["level"] == "species" and not r["correct_top1"] for r in g)}
    return out


def done_of(path) -> dict | None:
    """The service's last `done` event in a preds file, or None."""
    found = None
    with open(path, "rb") as f:
        for line in f:
            if line.strip():
                e = json.loads(line)
                if e.get("type") == contract.DONE:
                    found = e
    return found


def report_from_preds(preds_path: str, gt_csv: str, synonyms: bool = True,
                      lists: dict[str, dict[str, str]] | None = None, tier: str | None = None,
                      no_geo: bool | None = None) -> dict:
    """Rescore a preds file the way `bioscan eval --preds` does and return report.json. A preds file
    without a meta line has no options; `no_geo` (the command line's) stands in, as eval does."""
    rows = ev.read_gt(gt_csv)
    if synonyms:
        rows = ev.normalise_truth(rows, naming.read_synonyms(ev.SYNONYMS_CSV))
    meta = ev.read_preds_meta(preds_path)
    with open(preds_path, "rb") as f:
        preds = ev.load_preds(f)
    options = dict(meta.get("options") or {}) or None
    if options is None and no_geo is not None:
        options = {"identify": {"geo": not no_geo}, "from_command_line": True}
    if options is not None:
        options["synonyms"] = synonyms
    return build_report(rows, preds, groundtruth=gt_csv,
                        synonyms_sha256=sha256_of(ev.SYNONYMS_CSV) if synonyms else None, preds_meta=meta,
                        done=done_of(preds_path), lists=load_name_lists(default_name_lists()) if lists is None else lists,
                        preds_path=str(preds_path), complete=ev.preds_complete(preds_path) if meta else None,
                        options=options, tier=tier)


def write_json(obj: dict, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return p


def load_report(path: str | Path, schemas: tuple[str, ...] = (REPORT_SCHEMA,)) -> dict:
    try:
        rep = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise BenchError(f"{path}: cannot read report ({e})") from e
    if not isinstance(rep, dict) or rep.get("schema") not in schemas:
        raise BenchError(f"{path}: not a {' or '.join(schemas)} file")
    if rep.get("version") != REPORT_VERSION:
        raise BenchError(f"{path}: report version {rep.get('version')!r}, this bioscan reads {REPORT_VERSION}")
    return rep


# ---- formatting ------------------------------------------------------------------------------

def fmt(metric: str, v) -> str:
    if v is None:
        return "–"
    if metric in FRACTIONS:
        return f"{v * 100:.1f}%"
    if metric == "n":
        return str(v)
    return f"{v:.3g}" if isinstance(v, float) and abs(v) < 100 else f"{v:.0f}"


def fmt_ci(metric: str, ci) -> str:
    return "" if not ci else f" [{ci[0] * 100:.1f}, {ci[1] * 100:.1f}]"


def fmt_delta(metric: str, d) -> str:
    if d is None:
        return "–"
    if metric in FRACTIONS:
        return f"{d * 100:+.1f} pts"
    return f"{d:+.3g}" if abs(d) < 100 else f"{d:+.0f}"


def summary_md(rep: dict) -> str:
    m = rep["metrics"]
    scopes = [s for s in m if m[s]["n"]]
    keys = [k for k in METRICS if k != "n"]
    lines = ["| scope | n | " + " | ".join(keys) + " |", "|---" * (len(keys) + 2) + "|"]
    lines += [f"| {s} | {m[s]['n']} | " + " | ".join(fmt(k, m[s][k]) for k in keys) + " |" for s in scopes]
    extra = plugin_md(rep.get("plugin_metrics") or {})
    return "\n".join(lines) + ("\n\n" + extra if extra else "")


def _pfmt(m, v) -> str:
    if v is None:
        return "–"
    return f"{v * 100:.1f}%" if m is not None and m.fraction else f"{v:.3g}"


def plugin_md(pm: dict, registry: dict[str, tuple] | None = None) -> str:
    """plugin_metrics as one table per plugin: a row per scope, rates in % with their Wilson interval."""
    registry = metric_registry() if registry is None else registry
    out = []
    for plugin, scopes in pm.items():
        names = [m.name for m in registry.get(plugin, ()) if any(m.name in s for s in scopes.values())]
        out += [f"### plugin {plugin}", "", "| scope | n | " + " | ".join(names) + " |", "|---" * (len(names) + 2) + "|"]
        for scope, row in scopes.items():
            cells = []
            for name in names:
                m = _metric(registry, plugin, name)
                ci = row.get(name + "_ci")
                cells.append(_pfmt(m, row.get(name)) + (fmt_ci(name, ci) if ci else "")
                             + (f" (n {row[name + '_n']})" if name in row else ""))
            out.append(f"| {scope} | {row['n']} | " + " | ".join(cells) + " |")
        out.append("")
    return "\n".join(out).rstrip()


# ---- budget ----------------------------------------------------------------------------------

RULE_LIMITS = ("max_drop_pts", "max_rise_pts", "max_drop_pct", "max_rise_pct")


def read_budget(path: str | Path, registry: dict[str, tuple] | None = None) -> dict:
    """baselines/budget.toml: `[[rule]]` (metric, scopes, one limit, min_n; `plugin` names a
    plugin whose metric it is), `[species] max_lost`, `[images] max_broken`. Raises BenchError on
    anything it does not understand."""
    registry = metric_registry() if registry is None else registry
    try:
        with open(path, "rb") as f:
            raw = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise BenchError(f"{path}: cannot read budget ({e})") from e
    unknown = set(raw) - {"rule", "species", "images"}
    if unknown:
        raise BenchError(f"{path}: unknown budget sections {sorted(unknown)}")
    rules = raw.get("rule", [])
    if not isinstance(rules, list):
        raise BenchError(f"{path}: rules are [[rule]] tables (an array), not one [rule]")
    for i, r in enumerate(rules):
        where = f"{path}: rule {i + 1}"
        if "plugin" in r:
            if r["plugin"] not in registry:
                raise BenchError(f"{where}: plugin must be one of {', '.join(registry) or '(none with metrics)'}")
            m = _metric(registry, r["plugin"], r.get("metric"))
            if m is None:
                raise BenchError(f"{where}: metric must be one of plugin {r['plugin']}'s: "
                                 f"{', '.join(x.name for x in registry[r['plugin']])}")
            fraction = m.fraction
        elif r.get("metric") not in METRICS or r.get("metric") == "n":
            raise BenchError(f"{where}: metric must be one of {', '.join(METRICS[1:])}")
        else:
            fraction = r["metric"] in FRACTIONS
        limits = [k for k in RULE_LIMITS if k in r]
        if len(limits) != 1:
            raise BenchError(f"{where}: needs exactly one of {', '.join(RULE_LIMITS)}")
        if limits[0].endswith("_pts") and not fraction:
            raise BenchError(f"{where}: *_pts limits are for rates; use *_pct for {r['metric']}")
        extra = set(r) - {"metric", "plugin", "scopes", "min_n", "note", *RULE_LIMITS}
        if extra:
            raise BenchError(f"{where}: unknown keys {sorted(extra)}")
    for sect, key in (("species", "max_lost"), ("images", "max_broken")):
        if set(raw.get(sect, {})) - {key}:
            raise BenchError(f"{path}: [{sect}] takes only {key}")
    return raw


def check_budget(cmp: dict, budget: dict) -> list[dict]:
    """Budget violations of one comparison: [{rule, scope, base, new, limit, why}]."""
    out = []
    for r in budget.get("rule", []):
        metric, (limit_key,) = r["metric"], [k for k in RULE_LIMITS if k in r]
        limit = float(r[limit_key])
        source = (cmp.get("plugin_metrics") or {}).get(r["plugin"], {}) if "plugin" in r else cmp["metrics"]
        label = f"{r['plugin']}.{metric}" if "plugin" in r else metric
        for scope in r.get("scopes", ["all"] if "plugin" in r else SCOPES):
            row = source.get(scope, {}).get(metric)
            n = min(source.get(scope, {}).get("n", {}).get(x) or 0 for x in ("base", "new")) \
                if scope in source else 0
            if not row or row["base"] is None or row["new"] is None or n < r.get("min_n", 1):
                continue
            b, v = row["base"], row["new"]
            if limit_key.endswith("_pts"):
                change = (b - v if "drop" in limit_key else v - b) * 100
            elif b:
                change = ((b - v) if "drop" in limit_key else (v - b)) / abs(b) * 100
            else:
                continue
            if change > limit + 1e-9:
                unit = "pts" if limit_key.endswith("_pts") else "%"
                verb = "dropped" if "drop" in limit_key else "rose"
                out.append({"rule": f"{label} {limit_key} {limit:g}", "scope": scope, "base": b, "new": v,
                            "limit": limit, "why": f"{label} {verb} {change:.1f} {unit} in {scope} (limit {limit:g} {unit})"})
    max_lost = budget.get("species", {}).get("max_lost")
    if max_lost is not None:
        for s in cmp["species"]["regressions"]:
            if s["lost"] > max_lost:
                out.append({"rule": f"species max_lost {max_lost}", "scope": s["truth"], "base": s["base_hits"],
                            "new": s["new_hits"], "limit": max_lost,
                            "why": f"{s['truth']} lost {s['lost']} top-1 hits (limit {max_lost})"})
    max_broken = budget.get("images", {}).get("max_broken")
    if max_broken is not None and cmp["pairing"]["broken"] > max_broken:
        out.append({"rule": f"images max_broken {max_broken}", "scope": "all", "base": None,
                    "new": cmp["pairing"]["broken"], "limit": max_broken,
                    "why": f"{cmp['pairing']['broken']} images broken (limit {max_broken})"})
    return out


# ---- compare ---------------------------------------------------------------------------------

def _answer(r: dict) -> dict:
    return {k: r.get(k) for k in ("top1", "level", "box_kind", "gate", "p_visual", "p_geo", "posterior", "correct_top1")}


def pair_images(base: list[dict], new: list[dict]) -> tuple[list[tuple[dict, dict]], int, int, str]:
    """Pairs (base row, new row) by sha256, else by path; (pairs, only in base, only in new, key used)."""
    by_sha: dict[str, list[int]] = defaultdict(list)     # duplicate photos share a sha: keep them all
    by_path: dict[str, list[int]] = defaultdict(list)
    for i, r in enumerate(base):
        if r.get("sha256"):
            by_sha[r["sha256"]].append(i)
        by_path[r["path"]].append(i)
    used, pairs, keys = set(), [], Counter()
    for r in new:
        free = [i for i in by_sha.get(r.get("sha256") or "", []) if i not in used]
        # same sha: the copy at the same path first, then the remaining copies one-to-one in order
        i = next((i for i in free if base[i]["path"] == r["path"]), free[0] if free else None)
        k = "sha256"
        if i is None:
            i, k = next((i for i in by_path.get(r["path"], []) if i not in used), None), "path"
        if i is None:
            continue
        used.add(i)
        keys[k] += 1
        pairs.append((base[i], r))
    key = ", ".join(f"{k} {n}" for k, n in sorted(keys.items())) or "none"
    return pairs, len(base) - len(used), len(new) - len(pairs), key


def metrics_by_scope(images: list[dict], images_per_s: float | None = None) -> dict:
    """metrics_for per scope over any set of report image rows: SCOPES, then `<tier>/<scope>` when
    the rows carry tiers. `images_per_s` (a whole-run value) goes into `all` only."""
    out = {s: metrics_for([r for r in images if s == "all" or r["scope"] == s], images_per_s if s == "all" else None)
           for s in SCOPES}
    tiers = sorted({r.get("tier") or "?" for r in images})
    if tiers != ["?"]:
        for t in tiers:
            for s in SCOPES:
                sel = [r for r in images if (r.get("tier") or "?") == t and (s == "all" or r["scope"] == s)]
                if sel:
                    out[f"{t}/{s}"] = metrics_for(sel)
    return out


def _deltas(bm: dict, nm: dict) -> dict:
    """{scope: {metric: {base, new, delta, base_ci, new_ci}}} for scopes in both with any images."""
    out: dict[str, dict] = {}
    for scope in [s for s in bm if s in nm]:
        b, v = bm[scope], nm[scope]
        if not b["n"] and not v["n"]:
            continue
        out[scope] = {k: {"base": b.get(k), "new": v.get(k),
                          "delta": None if b.get(k) is None or v.get(k) is None else round(v[k] - b[k], 6),
                          "base_ci": b.get(k + "_ci"), "new_ci": v.get(k + "_ci")} for k in METRICS}
    return out


def _plugin_deltas(base: dict, new: dict, registry: dict[str, tuple]) -> dict:
    """{plugin: {scope: {metric: {base, new, delta, base_ci, new_ci}}}} over the plugin_images the two
    reports share (paired like the images), for plugins and scopes on both sides; `n` included."""
    pairs, _, _, _ = pair_images(base.get("plugin_images") or [], new.get("plugin_images") or [])
    if not pairs:
        return {}
    bm = plugin_metrics_for([b for b, _ in pairs], registry)
    nm = plugin_metrics_for([n for _, n in pairs], registry)
    out: dict[str, dict] = {}
    for plugin in [p for p in bm if p in nm]:
        for scope in [s for s in bm[plugin] if s in nm[plugin]]:
            b, v = bm[plugin][scope], nm[plugin][scope]
            keys = ["n"] + [m.name for m in registry.get(plugin, ()) if m.name in b or m.name in v]
            out.setdefault(plugin, {})[scope] = {
                k: {"base": b.get(k), "new": v.get(k),
                    "delta": None if b.get(k) is None or v.get(k) is None else round(v[k] - b[k], 6),
                    "base_ci": b.get(k + "_ci"), "new_ci": v.get(k + "_ci")} for k in keys}
    return out


def compare(base: dict, new: dict, budget: dict | None = None, labels: tuple[str, str] = ("base", "new"),
            registry: dict[str, tuple] | None = None) -> dict:
    """The comparison of two reports as data (the JSON `bench compare --json` writes)."""
    registry = metric_registry() if registry is None else registry
    warnings = []
    bm, nm = base["meta"], new["meta"]
    if bm.get("settings_fingerprint") != nm.get("settings_fingerprint"):
        warnings.append(f"settings fingerprint differs: {bm.get('settings_fingerprint')} -> {nm.get('settings_fingerprint')} "
                        "(thresholds, prompts or vocabulary changed)")
    if json.dumps(bm.get("engine"), sort_keys=True) != json.dumps(nm.get("engine"), sort_keys=True):
        warnings.append("engine info differs (version, models or name lists)")
    if bm.get("groundtruth_sha256") != nm.get("groundtruth_sha256"):
        warnings.append(f"ground truth differs: sha256 {str(bm.get('groundtruth_sha256'))[:12]} -> "
                        f"{str(nm.get('groundtruth_sha256'))[:12]} (metrics are over different images)")
    if bm.get("synonyms_sha256") != nm.get("synonyms_sha256"):
        warnings.append("synonyms.csv differs (truth labels may be normalised differently)")
    if json.dumps(bm.get("options"), sort_keys=True) != json.dumps(nm.get("options"), sort_keys=True):
        warnings.append(f"request options differ: {bm.get('options')} -> {nm.get('options')}")
    if bm.get("profile") != nm.get("profile"):
        warnings.append(f"profile differs: {bm.get('profile')} -> {nm.get('profile')}")
    if json.dumps(bm.get("reducers"), sort_keys=True) != json.dumps(nm.get("reducers"), sort_keys=True):
        warnings.append(f"reducer options differ: {bm.get('reducers')} -> {nm.get('reducers')}")
    pairs, only_base, only_new, key = pair_images(base["images"], new["images"])
    # Everything budgeted is computed over the paired images only, so a test set that gains (or
    # loses) photos never reads as a regression. images_per_s stays whole-run: it is a run property.
    metrics = _deltas(metrics_by_scope([b for b, _ in pairs], base["metrics"]["all"].get("images_per_s")),
                      metrics_by_scope([n for _, n in pairs], new["metrics"]["all"].get("images_per_s")))
    whole = _deltas(base["metrics"], new["metrics"])
    paired_ids = {id(b) for b, _ in pairs} | {id(n) for _, n in pairs}
    unpaired = {side: {s: m for s, m in metrics_by_scope([r for r in rep["images"] if id(r) not in paired_ids]).items()
                       if m["n"]}
                for side, rep in (("only_base", base), ("only_new", new))}
    fixed = [(b, n) for b, n in pairs if not b["correct_top1"] and n["correct_top1"]]
    broken = [(b, n) for b, n in pairs if b["correct_top1"] and not n["correct_top1"]]
    same = sum(b["correct_top1"] == n["correct_top1"] and naming.norm_binomial(b.get("top1"))
               != naming.norm_binomial(n.get("top1")) for b, n in pairs)
    hits: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])      # truth -> [base hits, new hits, n] (paired)
    for b, n in pairs:
        hits[b["truth"]][0] += b["correct_top1"]
        hits[n["truth"]][1] += n["correct_top1"]
        hits[n["truth"]][2] += 1
    changes = [{"truth": sci, "base_hits": bh, "new_hits": nh, "base_n": cnt, "new_n": cnt, "lost": bh - nh}
               for sci, (bh, nh, cnt) in sorted(hits.items()) if bh != nh]
    evidence = [{"path": n["path"], "truth": n["truth"], "old": _answer(b), "new": _answer(n)} for b, n in broken]
    out = {"schema": COMPARE_SCHEMA, "version": 1,
           "base": {"label": labels[0], "git_sha": bm.get("git_sha"), "date": bm.get("date"), "n": bm.get("n")},
           "new": {"label": labels[1], "git_sha": nm.get("git_sha"), "date": nm.get("date"), "n": nm.get("n")},
           "warnings": warnings, "metrics": metrics, "metrics_whole": whole, "unpaired": unpaired,
           "pairing": {"key": key, "paired": len(pairs), "only_base": only_base, "only_new": only_new,
                       "fixed": len(fixed), "broken": len(broken), "changed_same": same,
                       "mcnemar_p": round(mcnemar_exact(len(fixed), len(broken)), 6)},
           "species": {"regressions": sorted((c for c in changes if c["lost"] > 0), key=lambda c: (-c["lost"], c["truth"])),
                       "improvements": sorted((c for c in changes if c["lost"] < 0), key=lambda c: (c["lost"], c["truth"]))},
           "broken": evidence,
           "fixed": [{"path": n["path"], "truth": n["truth"], "old": _answer(b), "new": _answer(n)} for b, n in fixed],
           "plugin_metrics": _plugin_deltas(base, new, registry)}
    violations = check_budget(out, budget) if budget is not None else []
    out["budget"] = {"checked": budget is not None, "violations": violations}
    out["verdict"] = "over_budget" if violations else "ok"
    return out


def _p(x) -> str:
    return "–" if x is None else f"{x:.3g}"


def _ans_md(a: dict) -> str:
    return (f"{a.get('top1') or '(none)'} ({a.get('level') or '–'}; p_vis {_p(a.get('p_visual'))}, "
            f"p_geo {_p(a.get('p_geo'))}, post {_p(a.get('posterior'))})")


def compare_md(c: dict, limit: int = 30, registry: dict[str, tuple] | None = None) -> str:
    b, n, pr = c["base"], c["new"], c["pairing"]
    lines = ["# bioscan bench compare", "",
             f"- base: {b['label']} (git {str(b['git_sha'])[:12]}, {b['date']}, n {b['n']})",
             f"- new: {n['label']} (git {str(n['git_sha'])[:12]}, {n['date']}, n {n['n']})",
             f"- verdict: **{'OVER BUDGET' if c['verdict'] == 'over_budget' else 'within budget'}**"
             + ("" if c["budget"]["checked"] else " (no budget file)"), ""]
    if c["warnings"]:
        lines += ["## Warnings", ""] + [f"- {w}" for w in c["warnings"]] + [""]
    if c["budget"]["violations"]:
        lines += ["## Budget violations", ""] + [f"- {v['why']}" for v in c["budget"]["violations"]] + [""]
    lines += ["## Images", "",
              f"Paired by {pr['key']}: {pr['paired']} (only in base {pr['only_base']}, only in new {pr['only_new']}). "
              f"Fixed {pr['fixed']}, broken {pr['broken']}, changed answer with the same correctness "
              f"{pr['changed_same']}. McNemar exact p (top-1) = {pr['mcnemar_p']:.4g}.", ""]
    if pr["only_base"] or pr["only_new"]:
        lines += [f"Note: the reports hold different images ({pr['only_base']} only in base, {pr['only_new']} only "
                  "in new). Metrics, species changes and the budget below use the paired images only; the "
                  "unpaired ones are summarised under “Unpaired images”.", ""]
    lines += ["## Metrics (paired images)", "",
              "Rates in %, 95% Wilson intervals in brackets; images_per_s is whole-run.", ""]
    for scope, ms in c["metrics"].items():
        if scope != "all" and not any(row["delta"] for row in ms.values()):
            lines += [f"### {scope}: no change (n {ms['n']['new']})", ""]
            continue
        lines += [f"### {scope}", "", "| metric | base | new | Δ |", "|---|---|---|---|"]
        for k, row in ms.items():
            flag = ""
            if row["delta"] and k != "n":
                worse = row["delta"] > 0 if k in LOWER_IS_BETTER else row["delta"] < 0
                flag = " ▼" if worse else " ▲"
            lines.append(f"| {k} | {fmt(k, row['base'])}{fmt_ci(k, row['base_ci'])} | "
                         f"{fmt(k, row['new'])}{fmt_ci(k, row['new_ci'])} | {fmt_delta(k, row['delta'])}{flag} |")
        lines.append("")
    registry = metric_registry() if registry is None else registry
    for plugin, scopes in (c.get("plugin_metrics") or {}).items():
        lines += [f"## Plugin metrics: {plugin} (paired images)", ""]
        for scope, ms in scopes.items():
            lines += [f"### {plugin} / {scope}", "", "| metric | base | new | Δ |", "|---|---|---|---|"]
            for k, row in ms.items():
                m = _metric(registry, plugin, k)
                flag = ""
                if row["delta"] and m is not None:
                    flag = " ▼" if (row["delta"] > 0) == m.lower_is_better else " ▲"
                if m is None:                                   # n, or a metric this bioscan does not know
                    cells = [str(row["base"]), str(row["new"]), "–" if row["delta"] is None else f"{row['delta']:+g}"]
                else:
                    cells = [_pfmt(m, row["base"]) + fmt_ci(k, row["base_ci"]), _pfmt(m, row["new"]) + fmt_ci(k, row["new_ci"]),
                             "–" if row["delta"] is None else
                             (f"{row['delta'] * 100:+.1f} pts" if m.fraction else f"{row['delta']:+.3g}") + flag]
                lines.append(f"| {k} | " + " | ".join(cells) + " |")
            lines.append("")
    unpaired = c.get("unpaired") or {}
    for side, title in (("only_new", "Unpaired images: new images (only in new)"),
                        ("only_base", "Unpaired images: only in base")):
        scopes = unpaired.get(side) or {}
        if not scopes:
            continue
        keys = ("top1", "top5", "genus_acc", "coverage", "precision", "confident_error_rate", "no_box_rate",
                "failed_rate")
        lines += [f"## {title}", "", "Not budgeted.", "", "| scope | n | " + " | ".join(keys) + " |",
                  "|---" * (len(keys) + 2) + "|"]
        lines += [f"| {s} | {m['n']} | " + " | ".join(fmt(k, m[k]) for k in keys) + " |" for s, m in scopes.items()]
        lines.append("")
    for title, rows in (("Species regressions (paired images)", c["species"]["regressions"]),
                        ("Species improvements (paired images)", c["species"]["improvements"])):
        lines += [f"## {title}", ""]
        if not rows:
            lines += ["(none)", ""]
            continue
        lines += ["| truth | base hits / n | new hits / n | change |", "|---|---|---|---|"]
        lines += [f"| {s['truth']} | {s['base_hits']}/{s['base_n']} | {s['new_hits']}/{s['new_n']} | {-s['lost']:+d} |"
                  for s in rows[:limit]] + [""]
    for title, rows in (("Broken images", c["broken"]), ("Fixed images", c["fixed"])):
        lines += [f"## {title}", ""]
        if not rows:
            lines += ["(none)", ""]
            continue
        lines += ["| image | truth | old answer | new answer |", "|---|---|---|---|"]
        lines += [f"| {r['path']} | {r['truth']} | {_ans_md(r['old'])} | {_ans_md(r['new'])} |" for r in rows[:limit]]
        if len(rows) > limit:
            lines.append(f"\n… {len(rows) - limit} more in the JSON output")
        lines.append("")
    return "\n".join(lines)


# ---- analyze ---------------------------------------------------------------------------------

FAILURE_CLASSES = {   # class -> where to look first; the order is the precedence of the primary class
    "failed": "decode or service error: the preds error message; bioscan/service/decode.py, run.py",
    "gate_miss": "gate said none/person and no box: gate rescue threshold (rules.py), gate prompts (taxa.py)",
    "detector_miss": "gate saw an animal, detector boxed nothing: detector vocabulary (taxa.py), a detector fallback",
    "wrong_kind": "box kind differs from the truth: kind check, rules.kind_of / rules.kind_evidence_logits "
                  "in pipeline._species_many",
    "not_in_list": "truth is not in the kind's name list: name list / data/names/synonyms.csv",
    "out_of_range": "top-1 has p_geo ~ 0 where the place is known: range veto in rules.py; prior labels / geo gaps",
    "prior_suppressed": "truth ranked first by p_visual, pushed below top-1 by a lower p_geo: geo gaps "
                        "(bioscan names geo-gaps -> synonyms.csv birdnet rows), label map, prior floor",
    "within_genus": "right genus, wrong species: detail copy, crop quality, location prior between congeners",
    "within_family": "right family, wrong genus: prior floor tuning; grade to family when the genus is unsure",
    "far_miss": "wrong family: check the crop (wrong object boxed?) and image quality",
    "overconfident": "wrong at level species (overlaps the classes above): grading thresholds in rules.py; calibration",
}
PRIMARY = tuple(k for k in FAILURE_CLASSES if k != "overconfident")


def failure_class(r: dict) -> str | None:
    """The primary failure class of one report image row; None when top-1 is right."""
    if r["correct_top1"]:
        return None
    if r["failed"]:
        return "failed"
    if not r["has_box"]:
        return "gate_miss" if r["gate"] in ("none", "person") else "detector_miss"
    if scope_of(r["box_kind"]) != r["scope"]:
        return "wrong_kind"
    if r["in_list"] is False or (not r["top1"] and r["in_list"] is not True):
        return "not_in_list"
    if not r["top1"]:
        return "far_miss"
    if r["place_known"] and r["p_geo"] is not None and r["p_geo"] < OUT_OF_RANGE_P_GEO:
        return "out_of_range"
    if (r.get("truth_visual_rank") == 1 and (r.get("truth_rank") or 0) > 1 and r.get("truth_p_geo") is not None
            and r["p_geo"] is not None and r["truth_p_geo"] < r["p_geo"]):
        return "prior_suppressed"
    if r["correct_genus"]:
        return "within_genus"
    if r["family_truth"] and r["family_pred"] and r["family_truth"].lower() == r["family_pred"].lower():
        return "within_family"
    return "far_miss"


def _example(r: dict) -> dict:
    return {k: r.get(k) for k in ("path", "truth", "kind", "gate", "box_kind", "top1", "level", "p_visual", "p_geo",
                                  "posterior", "family_truth", "family_pred", "error")}


def analyze(rep: dict, examples: int = EXAMPLES) -> dict:
    images = rep["images"]
    wrong = [r for r in images if not r["correct_top1"]]
    groups: dict[str, list[dict]] = defaultdict(list)
    by_scope: dict[str, Counter] = defaultdict(Counter)
    for r in wrong:
        cls = failure_class(r)
        groups[cls].append(r)
        by_scope[r["scope"]][cls] += 1
        if not r["failed"] and r["level"] == "species":
            groups["overconfident"].append(r)
            by_scope[r["scope"]]["overconfident"] += 1
    classes = []
    for cls in sorted(FAILURE_CLASSES, key=lambda k: (-len(groups[k]), list(FAILURE_CLASSES).index(k))):
        g = sorted(groups[cls], key=lambda r: (-(r.get("posterior") or 0), r["path"]))
        classes.append({"class": cls, "primary": cls != "overconfident", "count": len(g),
                        "share_of_wrong": round(len(g) / len(wrong), 6) if wrong else None,
                        "share_of_n": round(len(g) / len(images), 6) if images else None,
                        "fix": FAILURE_CLASSES[cls], "examples": [_example(r) for r in g[:examples]]})
    conf = Counter((r["truth"], r["top1"]) for r in wrong if r["top1"])
    nxt = next((c for c in classes if c["primary"] and c["count"]), None)
    return {"schema": ANALYSIS_SCHEMA, "version": 1,
            "source": {k: rep["meta"].get(k) for k in ("git_sha", "date", "groundtruth", "settings_fingerprint")},
            "n": len(images), "wrong": len(wrong), "next": nxt and {"class": nxt["class"], "count": nxt["count"],
                                                                    "fix": nxt["fix"]},
            "classes": classes, "by_scope": {s: dict(c) for s, c in sorted(by_scope.items())},
            "confusion": [{"truth": t, "pred": p, "count": n} for (t, p), n in conf.most_common(15)]}


def analyze_md(a: dict) -> str:
    lines = ["# bioscan bench analyze", "",
             f"- report: git {str(a['source']['git_sha'])[:12]}, {a['source']['date']}, {a['source']['groundtruth']}",
             f"- {a['wrong']} of {a['n']} images without a correct top-1", ""]
    if a["next"]:
        lines += [f"**Fix next: `{a['next']['class']}`** ({a['next']['count']} images) → {a['next']['fix']}", ""]
    lines += ["## Failure classes", "", "| class | count | share of wrong | share of all | fix pointer |",
              "|---|---|---|---|---|"]
    for c in a["classes"]:
        name = c["class"] if c["primary"] else f"{c['class']} (overlaps)"
        lines.append(f"| {name} | {c['count']} | {fmt('top1', c['share_of_wrong'])} | {fmt('top1', c['share_of_n'])} | "
                     f"{c['fix']} |")
    lines += ["", "By scope: " + "; ".join(f"{s} " + ", ".join(f"{k} {v}" for k, v in sorted(c.items()))
                                          for s, c in a["by_scope"].items()), ""]
    for c in a["classes"]:
        if not c["examples"]:
            continue
        lines += [f"## {c['class']}: examples", "",
                  "| image | truth | gate | box | top-1 | level | p_visual | p_geo | posterior |",
                  "|---|---|---|---|---|---|---|---|---|"]
        lines += [f"| {e['path']} | {e['truth']} | {e['gate'] or '–'} | {e['box_kind'] or '–'} | "
                  f"{e['top1'] or e['error'] or '–'} | {e['level'] or '–'} | {_p(e['p_visual'])} | {_p(e['p_geo'])} | "
                  f"{_p(e['posterior'])} |" for e in c["examples"]] + [""]
    lines += ["## Top confusion pairs", ""]
    if a["confusion"]:
        lines += ["| truth | predicted | count |", "|---|---|---|"]
        lines += [f"| {x['truth']} | {x['pred']} | {x['count']} |" for x in a["confusion"]]
    else:
        lines.append("(none)")
    return "\n".join(lines) + "\n"


# ---- scorecard -------------------------------------------------------------------------------

STANDARD_FIELDS = ("id", "dimension", "title", "tier", "profile", "plugin", "scope", "metric", "op", "industry",
                   "community", "stretch", "unit", "how", "source")
OPTIONAL_FIELDS = ("tier", "profile", "plugin", "industry", "stretch")   # TOML has no null: an absent key means null
MANUAL = "manual"                                      # a standard checked by hand, not read from a report
POINT_TIERS = ("smoke", "album")                       # regression guards: judged on the observed value
DEFAULT_PROFILE = "wildlife"                           # a standard without `profile`
# a report's profile as the standards see it: a run without one (or `full`, the same identify
# contract) is held to the wildlife standards
PROFILE_ALIASES = {None: "wildlife", "full": "wildlife"}


def standard_profile(s: dict) -> str:
    return s.get("profile") or DEFAULT_PROFILE


def report_profile(rep: dict) -> str:
    p = rep["meta"].get("profile")
    return PROFILE_ALIASES.get(p, p)


def standard_tier(s: dict) -> str | None:
    """The explicit `tier`, else the id's second segment (`<dimension>.<tier>.<scope>.<metric>[.nogeo]`)."""
    if s.get("tier"):
        return s["tier"]
    parts = str(s.get("id", "")).split(".")
    return parts[1] if len(parts) >= 4 else None


def is_nogeo(s: dict) -> bool:
    return str(s.get("id", "")).endswith(".nogeo")


def read_standards(path: str | Path, registry: dict[str, tuple] | None = None) -> list[dict]:
    """data/standards.toml: `[[standard]]` tables with STANDARD_FIELDS (docs/harness.md). Rate
    metrics take unit "fraction" with targets 0-1, like the report; other metrics use the report's
    own units. `metric = "manual"` entries are listed, never scored. With `plugin`, the metric is
    one that plugin declares (report plugin_metrics) and the scope is one of its scopes."""
    registry = metric_registry() if registry is None else registry
    try:
        with open(path, "rb") as f:
            raw = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise BenchError(f"{path}: cannot read standards ({e})") from e
    out = raw.get("standard")
    if not isinstance(out, list) or not out:
        raise BenchError(f"{path}: no [[standard]] tables")
    seen = set()
    for i, s in enumerate(out):
        where = f"{path}: standard {s.get('id') or i + 1}"
        missing = [k for k in STANDARD_FIELDS if k not in s and k not in OPTIONAL_FIELDS]
        if missing:
            raise BenchError(f"{where}: missing {', '.join(missing)}")
        if set(s) - set(STANDARD_FIELDS):
            raise BenchError(f"{where}: unknown keys {sorted(set(s) - set(STANDARD_FIELDS))}")
        if s["id"] in seen:
            raise BenchError(f"{where}: duplicate id")
        seen.add(s["id"])
        for k in ("profile", "plugin"):
            if k in s and (not isinstance(s[k], str) or not s[k]):
                raise BenchError(f"{where}: {k} must be a non-empty string")
        if "plugin" in s:
            if s["plugin"] not in registry:
                raise BenchError(f"{where}: plugin must be one of {', '.join(registry) or '(none with metrics)'}")
            m = _metric(registry, s["plugin"], s["metric"])
            if m is None:
                raise BenchError(f"{where}: metric must be one of plugin {s['plugin']}'s: "
                                 f"{', '.join(x.name for x in registry[s['plugin']])}")
            if not isinstance(s["scope"], str) or not s["scope"]:
                raise BenchError(f"{where}: scope must be a non-empty string")
            fraction = m.fraction
        else:
            if s["scope"] not in SCOPES:
                raise BenchError(f"{where}: scope must be one of {', '.join(SCOPES)}")
            fraction = s["metric"] in FRACTIONS or s["metric"] in GEOTAG_RATES or s["metric"] in AESTHETIC_RATES
        if s["op"] not in (">=", "<="):
            raise BenchError(f"{where}: op must be >= or <=")
        if s["metric"] == MANUAL:
            continue
        if ("plugin" not in s and s["metric"] not in METRICS and s["metric"] not in GEOTAG_METRICS
                and s["metric"] not in AESTHETIC_METRICS):
            raise BenchError(f"{where}: metric must be one of {', '.join(METRICS)}, a geotag metric "
                             f"({', '.join(GEOTAG_METRICS[1:])}), an aesthetic metric "
                             f"({', '.join(AESTHETIC_METRICS[1:])}), a plugin metric (with `plugin`) or {MANUAL}")
        if fraction != (s["unit"] == "fraction"):
            raise BenchError(f"{where}: rate metrics take unit \"fraction\" (0-1), other metrics may not")
        if standard_tier(s) is None:
            raise BenchError(f"{where}: no tier (add `tier`, or use an id <dimension>.<tier>.<scope>.<metric>)")
        for k in ("industry", "community", "stretch"):
            v = s.get(k)
            if (v is not None or k == "community") and (not isinstance(v, (int, float)) or isinstance(v, bool)):
                raise BenchError(f"{where}: {k} must be a number" + (" or omitted" if k != "community" else ""))
    return out


def report_tier(rep: dict) -> str | None:
    """meta.tier (bench run/report --tier), else the ground truth's tier when it has exactly one."""
    if rep["meta"].get("tier"):
        return rep["meta"]["tier"]
    tiers = {r.get("tier") for r in rep["images"]} - {None, "", "?"}
    return tiers.pop() if len(tiers) == 1 else None


def report_nogeo(rep: dict) -> bool:
    return ((rep["meta"].get("options") or {}).get("identify") or {}).get("geo") is False


def scorecard(rep: dict, standards: list[dict], tier: str) -> dict:
    """{tier, nogeo, rows, manual, skipped}. A rate meets its bar when its Wilson 95% bound does (lower
    bound for >=, upper for <=), except on POINT_TIERS and for metrics without an interval, which are
    judged on the observed value. A --no-geo report is held to the `.nogeo` standards only; a normal
    report skips them."""
    nogeo = report_nogeo(rep)
    profile = report_profile(rep)
    rows, manual, skipped = [], [], 0
    for s in standards:
        if s["metric"] == MANUAL:
            manual.append({k: s.get(k) for k in STANDARD_FIELDS})
            continue
        if standard_tier(s) != tier or is_nogeo(s) != nogeo or standard_profile(s) != profile:
            skipped += 1
            continue
        if "plugin" in s:
            m = ((rep.get("plugin_metrics") or {}).get(s["plugin"]) or {}).get(s["scope"]) or {}
        else:
            m = rep["metrics"].get(s["scope"]) or {}
        v, ci = m.get(s["metric"]), m.get(s["metric"] + "_ci")
        use_ci = bool(ci) and tier not in POINT_TIERS
        judged = (ci[0] if s["op"] == ">=" else ci[1]) if use_ci else v
        if judged is None:
            status, gap = "n/a", None
        else:
            gap = round(judged - s["community"] if s["op"] == ">=" else s["community"] - judged, 6)
            status = "pass" if gap >= 0 else "fail"
        rows.append({**{k: s.get(k) for k in STANDARD_FIELDS}, "tier": tier, "n": m.get("n"), "value": v, "ci": ci,
                     "judged_on": "wilson" if use_ci else "value", "judged": judged, "status": status, "gap": gap})
    return {"tier": tier, "nogeo": nogeo, "profile": profile, "rows": rows, "manual": manual, "skipped": skipped}


def _num(x, unit) -> str:
    if x is None:
        return "–"
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return str(x)
    return f"{x * 100:.1f}%" if unit == "fraction" else f"{x:g}"


def scorecard_md(sc: dict, rep: dict) -> str:
    lines = ["# bioscan bench scorecard", "",
             f"- report: git {str(rep['meta'].get('git_sha'))[:12]}, {rep['meta'].get('date')}, "
             f"{rep['meta'].get('groundtruth')}",
             f"- tier: {sc['tier']}{' (no-geo run: .nogeo standards only)' if sc['nogeo'] else ''}, profile "
             f"{sc.get('profile', DEFAULT_PROFILE)}; {sc['skipped']} standards of other tiers, profiles or geo modes "
             "skipped", "",
             "Status and gap are against the community bar; gap > 0 means better than the bar. Rates are judged on "
             f"the Wilson 95% bound (lower for >=, upper for <=), except on the {' and '.join(POINT_TIERS)} tiers "
             "and for speeds.", ""]
    if not sc["rows"]:
        lines += [f"No standards for tier {sc['tier']!r} and profile {sc.get('profile', DEFAULT_PROFILE)!r}.", ""]
    else:
        lines += ["| id | standard | n | bar | ours [95% CI] | judged on | status | gap | industry | stretch |",
                  "|---|---|---|---|---|---|---|---|---|---|"]
        for r in sc["rows"]:
            u = r["unit"]
            ci = f" [{_num(r['ci'][0], u)}, {_num(r['ci'][1], u)}]" if r["ci"] else ""
            gap = "–" if r["gap"] is None else (f"{r['gap'] * 100:+.1f} pts" if u == "fraction" else f"{r['gap']:+g}")
            lines.append(f"| {r['id']} | {r['title']} | {r['n']} | {r['op']} {_num(r['community'], u)} | "
                         f"{_num(r['value'], u)}{ci} | {r['judged_on']} | {r['status'].upper()} | {gap} | "
                         f"{_num(r.get('industry'), u)} | {_num(r.get('stretch'), u)} |")
        c = Counter(r["status"] for r in sc["rows"])
        lines += ["", f"{c['pass']} pass, {c['fail']} fail, {c['n/a']} n/a", ""]
    if sc["manual"]:
        lines += ["## Not measurable from a report (checked by hand)", "",
                  "| id | standard | bar | how |", "|---|---|---|---|"]
        lines += [f"| {m['id']} | {m['title']} | {m['op']} {_num(m['community'], m['unit'])}"
                  f"{'' if m['unit'] == 'fraction' else ' ' + str(m['unit'])} | {m['how']} |" for m in sc["manual"]]
    return "\n".join(lines) + "\n"


# ---- commands (called from bioscan.cli.main) -------------------------------------------------

def _lists_from_args(specs: list[str] | None) -> dict[str, dict[str, str]]:
    lists = default_name_lists()
    for s in specs or []:
        kind, sep, path = s.partition("=")
        if not sep or not kind or not path:
            raise SystemExit(f"--names takes KIND=CSV (got {s!r})")
        lists[kind] = path
    return load_name_lists(lists)


def _write_md(text: str, path: str | None) -> None:
    if path:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")


def cmd_run(a) -> int:
    out = Path(a.out or f"runs/{time.strftime('%Y-%m-%d-%H%M%S', time.gmtime())}")
    if a.preds and a.profile:
        raise SystemExit("--profile only applies when bench run calls the service")
    request = None if a.preds else eval_request(a.profile, a.no_geo, {})
    report, complete = ev.run_eval(a.groundtruth, str(out), a.no_geo, a.url, a.preds, not a.no_synonyms, None, request)
    print(report)
    rep = report_from_preds(a.preds or str(out / "preds.ndjson"), a.groundtruth, not a.no_synonyms,
                            _lists_from_args(a.names), a.tier, a.no_geo)
    path = write_json(rep, out / "report.json")
    print(f"\n## bench summary\n\n{summary_md(rep)}\n\nreport.json -> {path}")
    return EXIT_OK if complete else 3


def cmd_report(a) -> int:
    rep = report_from_preds(a.preds, a.groundtruth, not a.no_synonyms, _lists_from_args(a.names), a.tier)
    path = write_json(rep, a.out or Path(a.preds).with_name("report.json"))
    print(f"{summary_md(rep)}\n\nreport.json -> {path}")
    return EXIT_OK


def cmd_baseline(a) -> int:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", a.name):
        raise BenchError(f"baseline name {a.name!r}: letters, digits, '.', '_' and '-' only")
    load_report(a.report)
    dest = Path(a.dir or BASELINES_DIR) / f"{a.name}.json"
    if dest.exists() and not a.force:
        raise BenchError(f"{dest} exists; baselines are committed data, pass --force to replace it")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(a.report, dest)
    print(f"baseline -> {dest}")
    return EXIT_OK


def cmd_compare(a) -> int:
    base, new = load_report(a.base), load_report(a.new)
    budget_path = a.budget or (BUDGET_TOML if BUDGET_TOML.is_file() else None)
    budget = read_budget(budget_path) if budget_path else None
    c = compare(base, new, budget, (str(a.base), str(a.new)))
    if not c["pairing"]["paired"]:
        raise BenchError("no image of the new report pairs with the base (by sha256 or path): nothing to compare")
    c["budget"]["file"] = str(budget_path) if budget_path else None
    md = compare_md(c)
    print(md)
    _write_md(md, a.md)
    if a.json:
        write_json(c, a.json)
    return EXIT_OVER if c["verdict"] == "over_budget" else EXIT_OK


def cmd_analyze(a) -> int:
    rep = load_report(a.report)
    res = analyze(rep, a.examples)
    md = analyze_md(res)
    print(md)
    _write_md(md, a.md)
    if a.json:
        write_json(res, a.json)
    return EXIT_OK


def cmd_scorecard(a) -> int:
    rep = load_report(a.report, (REPORT_SCHEMA, GEOTAG_SCHEMA, AESTHETIC_SCHEMA))
    standards = read_standards(a.standards or STANDARDS_TOML)
    tier = a.tier or report_tier(rep)
    if not tier:
        raise BenchError("the report has no tier (meta.tier or a single ground-truth tier): pass --tier")
    known = sorted({standard_tier(s) for s in standards if s["metric"] != MANUAL})
    if tier not in known:
        raise BenchError(f"no standards for tier {tier!r}; known tiers: {', '.join(known)}")
    sc = scorecard(rep, standards, tier)
    md = scorecard_md(sc, rep)
    print(md)
    _write_md(md, a.md)
    return EXIT_OVER if any(r["status"] == "fail" for r in sc["rows"]) else EXIT_OK


def add_parser(sub) -> None:
    """`bioscan bench ...` under the main parser's subcommands."""
    b = sub.add_parser("bench", help="evaluation harness: report.json, baselines, compare, failure analysis") \
        .add_subparsers(dest="bench_cmd", required=True)

    def report_opts(s):
        s.add_argument("--tier", help="standards tier of this run (smoke, golden, own, public, mac), kept in meta.tier")
        s.add_argument("--names", action="append", metavar="KIND=CSV",
                       help="name list for not_in_list/family (default bird=data/names/avilist_map.csv, "
                            "mammal=data/mdd/*.csv when present)")

    s = b.add_parser("run", help="bioscan eval, then report.json next to preds.ndjson and report.md")
    s.add_argument("groundtruth")
    s.add_argument("--out", help="run folder (default runs/<UTC time>)")
    s.add_argument("--no-geo", action="store_true")
    s.add_argument("--preds", help="score an existing preds.ndjson instead of calling the service")
    s.add_argument("--no-synonyms", action="store_true")
    s.add_argument("--profile", help=PROFILE_HELP)
    report_opts(s)
    s.set_defaults(func=cmd_run)

    s = b.add_parser("report", help="rebuild report.json offline from a preds.ndjson")
    s.add_argument("preds")
    s.add_argument("groundtruth")
    s.add_argument("--out", help="report.json path (default: next to the preds file)")
    s.add_argument("--no-synonyms", action="store_true")
    report_opts(s)
    s.set_defaults(func=cmd_report)

    s = b.add_parser("baseline", help="copy a report to baselines/NAME.json")
    s.add_argument("report")
    s.add_argument("--name", required=True, help="e.g. ci-smoke, golden-inat-<tag>, own-raw-<date>")
    s.add_argument("--dir", help=f"baselines folder (default {BASELINES_DIR})")
    s.add_argument("--force", action="store_true", help="replace an existing baseline")
    s.set_defaults(func=cmd_baseline)

    s = b.add_parser("compare", help="compare a report with a baseline; exit 0 within budget, 1 over, 2 incomparable")
    s.add_argument("base")
    s.add_argument("new")
    s.add_argument("--budget", help="budget TOML (default baselines/budget.toml when present)")
    s.add_argument("--md", help="also write the markdown here")
    s.add_argument("--json", help="write the comparison as JSON here")
    s.set_defaults(func=cmd_compare)

    s = b.add_parser("analyze", help="failure classes, examples and fix pointers for one report")
    s.add_argument("report")
    s.add_argument("--md", help="also write the markdown here")
    s.add_argument("--json", help="write the analysis as JSON here")
    s.add_argument("--examples", type=int, default=EXAMPLES, help="examples per class (default %(default)s)")
    s.set_defaults(func=cmd_analyze)

    s = b.add_parser("scorecard", help="a report against data/standards.toml; exit 1 when a bar is missed")
    s.add_argument("report")
    s.add_argument("--standards", help=f"standards TOML (default {STANDARDS_TOML})")
    s.add_argument("--tier", help="standards tier to apply (smoke, golden, own, public, mac); default: the report's")
    s.add_argument("--md", help="also write the markdown here")
    s.set_defaults(func=cmd_scorecard)

    from bioscan.cli import geobench  # imports this module; registered last to avoid a cycle

    geobench.add_parser(b)
