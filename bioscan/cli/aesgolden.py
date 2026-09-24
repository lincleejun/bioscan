"""`bioscan bench aesthetic`: any aesthetic scorer against the aesthetic golden set.

The golden set is a folder the owner builds once and freezes: `images.csv` (one row per frame:
stars, keep, shot group and its winner, drop reasons, scene category, slices; planted variants
point at their original) and an optional `pairs.csv` (the owner's two-frame choices). A scores
file is whatever a scorer produced: bioscan's own `run --json` output (products.aesthetics.score),
or NDJSON / CSV lines `path, score[, dims, reasons, ms]` from any other model. Nothing here calls
the service or a model, so a new model is benchmarked by writing one scores file.

- `init RATINGS --out DIR`: images.csv from the owner's ratings (XMP folder or CSV), optionally
  with machine groups and categories from a `bioscan cull` CSV for the owner to correct.
- `score GOLDEN SCORES --out DIR`: report.json (schema `bioscan-aesthetic-golden`, version 1) and
  report.md: coverage, agreement with stars, pairwise and in-group choice, culling the bottom of each
  trip, planted checks (invariance, degradations), repeat stability, slices and bias residuals,
  explanation recall, the owner's own re-rating ceiling.
- `compare BASE NEW [--budget]`: paired deltas (McNemar on pairs and groups, a bootstrap interval
  for the Spearman change); exit 0 within budget, 1 over, 2 not comparable.
- `table REPORT...`: several models side by side.

Standard library only. docs/research/2026-09-24-aesthetic-golden-set.md has the design;
docs/harness.md the commands.
"""
from __future__ import annotations

import bisect
import csv
import hashlib
import json
import math
import os
import random
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from bioscan import aesthetic as aes
from bioscan.cli import aesbench, bench

SCHEMA, VERSION = "bioscan-aesthetic-golden", 1
IMAGES_CSV, PAIRS_CSV = "images.csv", "pairs.csv"
IMAGE_FIELDS = ("path", "split", "trip", "group", "best", "category", "stars", "stars2", "keep", "reasons",
                "slices", "variant_of", "variant")
INVARIANT = ("rename", "jpeg95", "resize2048")          # planted copies that must score like their original
DEGRADED = ("blur", "ev-2", "ev+2", "jpeg10")          # planted copies that must score below their original
DROP_REASONS = ("soft_subject", "motion_or_defocus", "overexposed", "underexposed", "subject_cut", "subject_too_small",
                "no_subject", "composition", "cluttered_background", "bad_light", "eyes_closed", "pose", "duplicate",
                "other")
CULL_SHARES = (0.1, 0.2, 0.3)       # reject this share of each trip, lowest scores first
INVARIANCE_TOL = 0.05               # a planted copy may move at most this far in the album's score percentiles
REPEAT_TOL = 0.01                   # a rerun may move a frame at most this far
TOP_K = aesbench.TOP_K
BOOT = 1000
HEADLINE = ("spearman", "kendall", "ndcg_at_k", "precision_at_k", "pair_acc", "group_top1", "drop_auc",
            "keepers_lost_at_20", "reject_precision_at_20", "degrade_acc", "invariance_rate", "missing_rate")


class GoldenError(ValueError):
    """A golden set or scores file this module cannot use."""


# ---- reading --------------------------------------------------------------------------------------

def _key(path: str, base: Path) -> str:
    p = Path(path).expanduser()
    return os.path.normpath(str(p if p.is_absolute() else base / p))


def _split(cell: str | None) -> list[str]:
    return [x.strip() for x in (cell or "").split(";") if x.strip()]


def _num(cell: str | None, where: str, what: str) -> float | None:
    cell = (cell or "").strip()
    if not cell:
        return None
    try:
        return float(cell)
    except ValueError:
        raise GoldenError(f"{where}: {what} must be a number (got {cell!r})") from None


def read_golden(folder: str | Path) -> dict[str, Any]:
    """images.csv (+ pairs.csv) of a golden folder: {root, sha256, images: [row], pairs: [(a, b, winner)]}.
    Paths are keys: absolute and normalised (relative ones are taken from the folder)."""
    root = Path(folder).expanduser().resolve()
    ipath, ppath = root / IMAGES_CSV, root / PAIRS_CSV
    if not ipath.is_file():
        raise GoldenError(f"{root}: no {IMAGES_CSV}")
    digest = hashlib.sha256(ipath.read_bytes())
    rows, seen = [], set()
    with open(ipath, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or "path" not in reader.fieldnames:
            raise GoldenError(f"{ipath}: needs a path column (got {reader.fieldnames})")
        unknown = set(reader.fieldnames) - set(IMAGE_FIELDS)
        if unknown:
            raise GoldenError(f"{ipath}: unknown columns {sorted(unknown)}; allowed: {', '.join(IMAGE_FIELDS)}")
        for i, r in enumerate(reader, start=2):
            where = f"{ipath}:{i}"
            key = _key(r["path"], root)
            if key in seen:
                raise GoldenError(f"{where}: {r['path']} listed twice")
            seen.add(key)
            stars = _num(r.get("stars"), where, "stars")
            keep = _num(r.get("keep"), where, "keep")
            best = _num(r.get("best"), where, "best")
            if keep not in (None, 0, 1) or best not in (None, 0, 1):
                raise GoldenError(f"{where}: keep and best are 1, 0 or blank")
            graded = aes.stars_of(stars, 0)
            reasons = _split(r.get("reasons"))
            bad = [x for x in reasons if x not in DROP_REASONS]
            if bad:
                raise GoldenError(f"{where}: unknown reasons {bad}; allowed: {', '.join(DROP_REASONS)}")
            variant = (r.get("variant") or "").strip()
            original = (r.get("variant_of") or "").strip()
            if bool(variant) != bool(original):
                raise GoldenError(f"{where}: variant and variant_of go together")
            if variant and variant not in INVARIANT + DEGRADED:
                raise GoldenError(f"{where}: variant must be one of {', '.join(INVARIANT + DEGRADED)}")
            rel = os.path.relpath(key, root)
            rows.append({"key": key, "path": rel if not rel.startswith("..") else key,
                         "split": (r.get("split") or "").strip() or "test",
                         "trip": (r.get("trip") or "").strip() or aes.trip_of(key, str(root)),
                         "group": (r.get("group") or "").strip() or None, "best": best == 1,
                         "category": (r.get("category") or "").strip() or None,
                         "grade": None if graded is None else graded[0],
                         "stars2": _num(r.get("stars2"), where, "stars2"), "keep": None if keep is None else int(keep),
                         "reasons": reasons, "slices": _split(r.get("slices")),
                         "variant": variant or None, "variant_of": _key(original, root) if original else None})
    missing = [r["path"] for r in rows if r["variant_of"] and r["variant_of"] not in seen]
    if missing:
        raise GoldenError(f"{ipath}: variant_of names a frame not in the file for {missing[:5]}")
    pairs = []
    if ppath.is_file():
        digest.update(b"\0" + ppath.read_bytes())
        with open(ppath, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames or not {"a", "b", "winner"} <= set(reader.fieldnames):
                raise GoldenError(f"{ppath}: needs columns a,b,winner")
            for i, r in enumerate(reader, start=2):
                a, b, w = _key(r["a"], root), _key(r["b"], root), (r["winner"] or "").strip()
                if a not in seen or b not in seen or a == b:
                    raise GoldenError(f"{ppath}:{i}: a and b must be two different frames of {IMAGES_CSV}")
                if w not in ("a", "b", "tie"):
                    raise GoldenError(f"{ppath}:{i}: winner is a, b or tie")
                pairs.append((a, b, w))
    return {"root": str(root), "sha256": digest.hexdigest(), "images": rows, "pairs": pairs}


def _finite(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def read_scores(path: str | Path, base: str | Path) -> dict[str, Any]:
    """A scores file: {model, scores: {key: {score, dims?, reasons?, ms?}}, failed: [key], nonfinite: [key],
    duplicates: int}. NDJSON lines are bioscan events (a `result` carries products.aesthetics.score; an
    `error` is a failed frame; meta/progress/done are skipped) or `{path, score[, dims, reasons, ms, model]}`;
    a CSV has `path,score`. Relative paths are taken from `base` (the golden folder)."""
    base = Path(base)
    out: dict[str, dict[str, Any]] = {}
    failed, nonfinite, dup, model = [], [], 0, None

    def put(p: str, entry: dict[str, Any]) -> None:
        nonlocal dup
        key = _key(p, base)
        dup += key in out
        if not _finite(entry.get("score")):
            nonfinite.append(key)
            entry = {**entry, "score": None}
        out[key] = entry

    text = Path(path).read_text(encoding="utf-8-sig")
    if str(path).lower().endswith(".csv"):
        reader = csv.DictReader(text.splitlines())
        if not reader.fieldnames or not {"path", "score"} <= set(reader.fieldnames):
            raise GoldenError(f"{path}: a scores CSV needs columns path,score")
        for r in reader:
            try:
                s = float(r["score"]) if (r["score"] or "").strip() else None
            except ValueError:
                s = None
            put(r["path"], {"score": s})
            model = model or (r.get("model") or None)
    else:
        for i, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                raise GoldenError(f"{path}:{i}: not JSON") from None
            kind = ev.get("type")
            if kind == "result":
                a = (ev.get("products") or {}).get("aesthetics") or {}
                put(ev["path"], {"score": a.get("score")})
                model = model or a.get("head_id")
            elif kind == "error":
                failed.append(_key(ev.get("path", ""), base))
            elif kind in ("meta", "progress", "done"):
                model = model or ev.get("model")
            elif "path" in ev and "score" in ev:
                entry = {"score": ev["score"]}
                if isinstance(ev.get("dims"), dict):
                    entry["dims"] = {k: float(v) for k, v in ev["dims"].items() if _finite(v)}
                if isinstance(ev.get("reasons"), list):
                    entry["reasons"] = [str(x) for x in ev["reasons"]]
                if _finite(ev.get("ms")):
                    entry["ms"] = float(ev["ms"])
                put(ev["path"], entry)
                model = model or ev.get("model")
            else:
                raise GoldenError(f"{path}:{i}: neither a bioscan event nor a {{path, score}} line")
    return {"model": model, "scores": out, "failed": failed, "nonfinite": nonfinite, "duplicates": dup}


# ---- metrics ---------------------------------------------------------------------------------------

def _r(x: float | None) -> float | None:
    return None if x is None else round(x, 6)


def _rate(k: int, n: int) -> tuple[float | None, list[float] | None]:
    return (round(k / n, 6) if n else None), bench.wilson(k, n)


def ecdf(values: list[float]):
    """x -> share of `values` below x, ties counted half (a mid-rank percentile, 0-1)."""
    xs = sorted(values)
    n = len(xs)

    def pct(x: float) -> float:
        lo, hi = bisect.bisect_left(xs, x), bisect.bisect_right(xs, x)
        return (lo + (hi - lo) / 2) / n if n else 0.5
    return pct


def _auc_low(scores: list[float], dropped: list[bool]) -> float | None:
    """P(a dropped frame scores below a kept one), ties half: Mann-Whitney on mid-ranks."""
    nd = sum(dropped)
    nk = len(dropped) - nd
    if not nd or not nk:
        return None
    rk = aes.ranks(scores)
    kept_ranks = sum(r for r, d in zip(rk, dropped) if not d)
    return (kept_ranks - nk * (nk + 1) / 2) / (nk * nd)


def pair_list(g: dict[str, Any], keys: set[str]) -> list[tuple[str, str, str]]:
    """Owner pairs (pairs.csv), then each group winner against every other member of its group
    (explicit pairs win over implied ones); ties kept for counting. Only frames in `keys`."""
    seen, out = set(), []
    for a, b, w in g["pairs"]:
        if a in keys and b in keys and frozenset((a, b)) not in seen:
            seen.add(frozenset((a, b)))
            out.append((a, b, w))
    for members in groups_of(g, keys).values():
        winners = [m for m in members if m["best"]]
        if len(winners) != 1:
            continue
        w = winners[0]["key"]
        for m in members:
            if m["key"] != w and frozenset((w, m["key"])) not in seen:
                seen.add(frozenset((w, m["key"])))
                out.append((w, m["key"], "a"))
    return out


def groups_of(g: dict[str, Any], keys: set[str]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    for r in g["images"]:
        if r["group"] and r["key"] in keys:
            out[r["group"]].append(r)
    return {k: v for k, v in out.items() if len(v) > 1}


def _score(scores: dict[str, float | None], key: str) -> float:
    s = scores.get(key)
    return -math.inf if s is None else s


def choice_metrics(g: dict[str, Any], rows: list[dict], scores: dict[str, float | None]) -> dict[str, Any]:
    """Pairwise accuracy and in-group top-1 of one scorer over `rows`, with the per-item outcomes."""
    keys = {r["key"] for r in rows}
    pairs, ties_owner = [], 0
    for a, b, w in pair_list(g, keys):
        if w == "tie":
            ties_owner += 1
            continue
        win, lose = (a, b) if w == "a" else (b, a)
        pairs.append({"a": win, "b": lose, "correct": _score(scores, win) > _score(scores, lose)})
    groups = []
    for gid, members in sorted(groups_of(g, keys).items()):
        winners = [m for m in members if m["best"]]
        if len(winners) != 1:
            continue
        top = max(members, key=lambda m: (_score(scores, m["key"]), m["key"]))
        groups.append({"group": gid, "size": len(members), "winner": winners[0]["key"], "top": top["key"],
                       "correct": top["key"] == winners[0]["key"]})
    pk, pci = _rate(sum(p["correct"] for p in pairs), len(pairs))
    gk, gci = _rate(sum(x["correct"] for x in groups), len(groups))
    return {"pairs": len(pairs), "pairs_tied_by_owner": ties_owner, "pair_acc": pk, "pair_acc_ci": pci,
            "groups": len(groups), "group_top1": gk, "group_top1_ci": gci,
            "group_top1_random": _r(sum(1 / x["size"] for x in groups) / len(groups)) if groups else None,
            "_pairs": pairs, "_groups": groups}


def cull_metrics(rows: list[dict], scores: dict[str, float | None]) -> dict[str, Any]:
    """Drop the lowest-scoring share of each trip (frames with a keep label): keepers lost and reject
    precision at 10/20/30%, pooled over trips, and the threshold-free drop AUC."""
    labelled = [r for r in rows if r["keep"] is not None]
    out: dict[str, Any] = {"keep_labelled": len(labelled), "keepers": sum(r["keep"] == 1 for r in labelled)}
    by_trip: dict[str, list[dict]] = defaultdict(list)
    for r in labelled:
        by_trip[r["trip"]].append(r)
    for q in CULL_SHARES:
        lost = kept = rejected = dropped_rejected = 0
        for members in by_trip.values():
            order = sorted(members, key=lambda r: (_score(scores, r["key"]), r["key"]))
            cut = order[:round(q * len(order))]
            kept += sum(r["keep"] == 1 for r in members)
            lost += sum(r["keep"] == 1 for r in cut)
            rejected += len(cut)
            dropped_rejected += sum(r["keep"] == 0 for r in cut)
        tag = f"{round(q * 100)}"
        out[f"keepers_lost_at_{tag}"], out[f"keepers_lost_at_{tag}_ci"] = _rate(lost, kept)
        out[f"reject_precision_at_{tag}"], out[f"reject_precision_at_{tag}_ci"] = _rate(dropped_rejected, rejected)
    s = [_score(scores, r["key"]) for r in labelled]
    s = [x if x != -math.inf else -1e300 for x in s]
    out["drop_auc"] = _r(_auc_low(s, [r["keep"] == 0 for r in labelled]))
    return out


def ranking_metrics(rows: list[dict], scores: dict[str, float | None], k: int) -> dict[str, Any]:
    """Agreement with stars (aesbench.set_metrics: Spearman with CI, Kendall, PLCC, NDCG@k and
    precision@k per trip against keep = 1). Frames without a score count as the lowest score."""
    rated = [r for r in rows if r["grade"] is not None]
    if len(rated) < 2:
        return {"rated": len(rated)}
    s = [_score(scores, r["key"]) for r in rated]
    floor = min((x for x in s if x != -math.inf), default=0.0) - 1.0
    s = [floor if x == -math.inf else x for x in s]
    ratings = [aes.Rating(r["key"], r["grade"], 0, trip=r["trip"]) for r in rated]
    m = aesbench.set_metrics(s, ratings, [r["keep"] == 1 for r in rated], k)["metrics"]
    m["rated"] = m.pop("n")
    return m


def planted_metrics(g: dict[str, Any], scores: dict[str, float | None], pct) -> dict[str, Any]:
    """Planted copies against their originals: degradations must score lower; invariant copies must
    stay within INVARIANCE_TOL of the original in the album's score percentiles."""
    per: dict[str, list[bool]] = defaultdict(list)
    shifts = []
    for r in g["images"]:
        if not r["variant"]:
            continue
        o, v = scores.get(r["variant_of"]), scores.get(r["key"])
        if r["variant"] in DEGRADED:
            per[r["variant"]].append(o is not None and v is not None and v < o)
        else:
            ok = o is not None and v is not None and abs(pct(v) - pct(o)) <= INVARIANCE_TOL
            per[r["variant"]].append(ok)
            if o is not None and v is not None:
                shifts.append(abs(pct(v) - pct(o)))
    out: dict[str, Any] = {}
    for label, kinds in (("degrade_acc", DEGRADED), ("invariance_rate", INVARIANT)):
        vals = [x for kd in kinds for x in per.get(kd, [])]
        out[label], out[f"{label}_ci"] = _rate(sum(vals), len(vals))
        out[f"{label}_n"] = len(vals)
        out[f"{label}_by_kind"] = {kd: _rate(sum(per[kd]), len(per[kd]))[0] for kd in kinds if per.get(kd)}
    out["invariance_max_shift"] = _r(max(shifts)) if shifts else None
    return out


def residual(rows: list[dict], pct_score, pct_stars, scores: dict[str, float | None]) -> dict[str, Any]:
    """Mean of (score percentile - stars percentile) over rated, scored frames: > 0 means the scorer
    likes these frames more than the owner does. A 95% interval from the standard error."""
    d = [pct_score(scores[r["key"]]) - pct_stars(r["grade"]) for r in rows
         if r["grade"] is not None and scores.get(r["key"]) is not None]
    if not d:
        return {"residual": None, "residual_ci": None, "residual_n": 0}
    m = sum(d) / len(d)
    if len(d) < 2:
        return {"residual": _r(m), "residual_ci": None, "residual_n": 1}
    se = math.sqrt(sum((x - m) ** 2 for x in d) / (len(d) - 1) / len(d))
    return {"residual": _r(m), "residual_ci": [_r(m - bench.Z95 * se), _r(m + bench.Z95 * se)], "residual_n": len(d)}


def reason_metrics(rows: list[dict], entries: dict[str, dict]) -> dict[str, Any] | None:
    """Explanations: the owner's drop reasons found among the scorer's reasons (recall), and the scorer's
    reasons the owner agrees with (precision, on keepers and frames with owner reasons). None when the
    scorer gives no reasons."""
    judged = [r for r in rows if "reasons" in entries.get(r["key"], {}) and (r["reasons"] or r["keep"] == 1)]
    if not judged:
        return None
    found = owner = said = agreed = 0
    per: dict[str, list[bool]] = defaultdict(list)
    for r in judged:
        mine, theirs = set(r["reasons"]), set(entries[r["key"]]["reasons"])
        found += len(mine & theirs)
        owner += len(mine)
        said += len(theirs)
        agreed += len(mine & theirs)
        for x in mine:
            per[x].append(x in theirs)
    rec, rec_ci = _rate(found, owner)
    prec, prec_ci = _rate(agreed, said)
    return {"frames": len(judged), "reason_recall": rec, "reason_recall_ci": rec_ci, "reason_precision": prec,
            "reason_precision_ci": prec_ci, "by_reason": {x: _rate(sum(v), len(v))[0] for x, v in sorted(per.items())}}


def scope_rows(rows: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {"all": rows}
    for r in rows:
        if r["category"]:
            out.setdefault(f"category:{r['category']}", []).append(r)
        for s in r["slices"]:
            out.setdefault(f"slice:{s}", []).append(r)
    return out


def evaluate(g: dict[str, Any], sc: dict[str, Any], *, split: str | None = "test", k: int = TOP_K,
             repeat: dict[str, Any] | None = None, scores_path: str = "", model: str | None = None) -> dict[str, Any]:
    """The report of one scores file on one golden set (frames of `split`; None = every split)."""
    entries = sc["scores"]
    scores = {key: e["score"] for key, e in entries.items()}
    base = [r for r in g["images"] if not r["variant"] and (split is None or r["split"] == split)]
    base_keys = {r["key"] for r in base}
    planted = [r for r in g["images"] if r["variant"] and r["variant_of"] in base_keys]
    expected = base_keys | {r["key"] for r in planted}
    missing = sorted(k_ for k_ in expected if scores.get(k_) is None)
    known = {r["key"] for r in g["images"]}
    extra = sorted(k_ for k_ in entries if k_ not in known)
    scored_base = [scores[r["key"]] for r in base if scores.get(r["key"]) is not None]
    pct = ecdf(scored_base)
    pct_stars = ecdf([r["grade"] for r in base if r["grade"] is not None])
    miss_rate, miss_ci = _rate(len(missing), len(expected))
    coverage = {"expected": len(expected), "scored": len(expected) - len(missing), "missing_rate": miss_rate,
                "missing_rate_ci": miss_ci, "failed": len(set(sc["failed"]) & expected),
                "nonfinite": len(set(sc["nonfinite"]) & expected), "extra": len(extra),
                "duplicates": sc["duplicates"]}
    metrics: dict[str, Any] = {}
    items: dict[str, Any] = {}
    for scope, rows in scope_rows(base).items():
        m: dict[str, Any] = {"n": len(rows)}
        m.update(ranking_metrics(rows, scores, k))
        ch = choice_metrics(g, rows, scores)
        if scope == "all":
            items = {"pairs": ch["_pairs"], "groups": ch["_groups"]}
        m.update({k_: v for k_, v in ch.items() if not k_.startswith("_")})
        m.update(cull_metrics(rows, scores))
        m.update(residual(rows, pct, pct_stars, scores))
        if scope == "all":
            m.update(coverage)
            m.update(planted_metrics({"images": planted}, scores, pct))
            ms = [e["ms"] for key, e in entries.items() if key in expected and "ms" in e]
            m["ms_median"] = _r(sorted(ms)[len(ms) // 2]) if ms else None
        metrics[scope] = m
    alt = None
    dims_keys = sorted({d for key in base_keys for d in entries.get(key, {}).get("dims", {})})
    if dims_keys:
        dm = {key: (sum(e["dims"].values()) / len(e["dims"]) if e.get("dims") else None) for key, e in entries.items()}
        ch = choice_metrics(g, base, dm)
        rated = [r for r in base if r["grade"] is not None]
        per_dim = {}
        for d in dims_keys:
            got = [(entries[r["key"]]["dims"][d], r["grade"]) for r in rated if d in entries.get(r["key"], {}).get("dims", {})]
            per_dim[d] = _r(aes.spearman([x for x, _ in got], [y for _, y in got])) if len(got) > 2 else None
        alt = {"dims_mean": {k_: v for k_, v in ch.items() if not k_.startswith("_")}, "dims_spearman": per_dim}
    rep_block = None
    if repeat is not None:
        shifts = [abs(pct(repeat["scores"][r["key"]]["score"]) - pct(scores[r["key"]])) for r in base
                  if scores.get(r["key"]) is not None and (repeat["scores"].get(r["key"]) or {}).get("score") is not None]
        stable, stable_ci = _rate(sum(x <= REPEAT_TOL for x in shifts), len(shifts))
        rep_block = {"n": len(shifts), "repeat_stable_rate": stable, "repeat_stable_rate_ci": stable_ci,
                     "repeat_max_shift": _r(max(shifts)) if shifts else None}
    retest = [(r["grade"], r["stars2"]) for r in base if r["grade"] is not None and r["stars2"] not in (None, 0)]
    ceiling = {"n": len(retest), "spearman": _r(aes.spearman([a for a, _ in retest], [b for _, b in retest]))
               if len(retest) > 2 else None}
    reasons = reason_metrics(base, entries)
    sha, dirty = bench._git()
    meta = {"golden": g["root"], "golden_sha256": g["sha256"], "split": split, "k": k,
            "model": model or sc["model"], "scores": os.path.abspath(scores_path) if scores_path else None,
            "scores_sha256": bench.sha256_of(scores_path) if scores_path else None, "git_sha": sha,
            "git_dirty": dirty, "date": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "invariance_tol": INVARIANCE_TOL, "cull_shares": list(CULL_SHARES)}

    def rel(key: str) -> str:
        r = os.path.relpath(key, g["root"])
        return key if r.startswith("..") else r
    images = [{"path": rel(r["key"]), "trip": r["trip"], "group": r["group"], "grade": r["grade"], "keep": r["keep"],
               "score": scores.get(r["key"])} for r in base]
    return {"schema": SCHEMA, "version": VERSION, "meta": meta, "metrics": metrics, "dims": alt, "repeat": rep_block,
            "owner_ceiling": ceiling, "reasons": reasons, "missing": [rel(x) for x in missing[:50]],
            "extra": [rel(x) for x in extra[:50]], "images": images,
            "pairs": [{"a": rel(p["a"]), "b": rel(p["b"]), "correct": p["correct"]} for p in items.get("pairs", [])],
            "groups": [{**x, "winner": rel(x["winner"]), "top": rel(x["top"])} for x in items.get("groups", [])]}


# ---- compare ---------------------------------------------------------------------------------------

def load(path: str | Path) -> dict[str, Any]:
    try:
        rep = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise bench.BenchError(f"{path}: cannot read report ({e})") from e
    if rep.get("schema") != SCHEMA or rep.get("version") != VERSION:
        raise bench.BenchError(f"{path}: not a {SCHEMA} v{VERSION} report")
    return rep


def _spearman_rows(images: list[dict]) -> float | None:
    rated = [x for x in images if x["grade"] is not None]
    if len(rated) < 3:
        return None
    s = [x["score"] if x["score"] is not None else -1e300 for x in rated]
    return aes.spearman(s, [x["grade"] for x in rated])


def boot_delta(base: list[dict], new: list[dict], reps: int = BOOT, seed: int = 0) -> list[float] | None:
    """95% bootstrap interval of Spearman(new) - Spearman(base), resampling shot groups (a frame
    without a group is its own unit) so near-duplicates move together."""
    by_path = {x["path"]: x for x in new}
    units: dict[str, list[tuple[dict, dict]]] = defaultdict(list)
    for x in base:
        if x["path"] in by_path:
            units[x["group"] or f"\0{x['path']}"].append((x, by_path[x["path"]]))
    keys = sorted(units)
    if len(keys) < 3 or reps <= 0:
        return None
    rng, deltas = random.Random(seed), []
    for _ in range(reps):
        pick = [p for _k in range(len(keys)) for p in units[keys[rng.randrange(len(keys))]]]
        b, n = _spearman_rows([p[0] for p in pick]), _spearman_rows([p[1] for p in pick])
        if b is not None and n is not None:
            deltas.append(n - b)
    if len(deltas) < 10:
        return None
    deltas.sort()
    return [_r(deltas[int(0.025 * (len(deltas) - 1))]), _r(deltas[int(0.975 * (len(deltas) - 1))])]


def _mcnemar(base: list[dict], new: list[dict], key) -> dict[str, Any]:
    nb = {key(x): x["correct"] for x in new}
    b = c = n = 0
    for x in base:
        if key(x) in nb:
            n += 1
            b += x["correct"] and not nb[key(x)]
            c += (not x["correct"]) and nb[key(x)]
    return {"n": n, "base_only": b, "new_only": c, "p": _r(bench.mcnemar_exact(b, c)) if b + c else None}


def read_budget(path: str | Path) -> dict:
    """`[[rule]]` tables: metric (a numeric key of a report's metrics), scopes (default ["all"]), one
    of max_drop_pts / max_rise_pts / max_drop_pct / max_rise_pct, optional min_n and note."""
    import tomllib
    try:
        with open(path, "rb") as f:
            raw = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise bench.BenchError(f"{path}: cannot read budget ({e})") from e
    if set(raw) - {"rule"} or not isinstance(raw.get("rule", []), list):
        raise bench.BenchError(f"{path}: an aesthetic budget holds only [[rule]] tables")
    for i, r in enumerate(raw.get("rule", [])):
        limits = [x for x in bench.RULE_LIMITS if x in r]
        if not isinstance(r.get("metric"), str) or len(limits) != 1:
            raise bench.BenchError(f"{path}: rule {i + 1} needs metric and exactly one of {', '.join(bench.RULE_LIMITS)}")
        extra = set(r) - {"metric", "scopes", "min_n", "note", *bench.RULE_LIMITS}
        if extra:
            raise bench.BenchError(f"{path}: rule {i + 1}: unknown keys {sorted(extra)}")
        r.setdefault("scopes", ["all"])
    return raw


def compare(base: dict, new: dict, budget: dict | None = None, reps: int = BOOT) -> dict[str, Any]:
    for key in ("golden_sha256", "split"):
        if base["meta"].get(key) != new["meta"].get(key):
            raise bench.BenchError(f"not comparable: {key} differs ({base['meta'].get(key)} vs {new['meta'].get(key)}); "
                                   "score both models on the same frozen golden set")
    metrics: dict[str, dict] = {}
    for scope in sorted(set(base["metrics"]) & set(new["metrics"])):
        b, n = base["metrics"][scope], new["metrics"][scope]
        row: dict[str, Any] = {"n": {"base": b.get("n"), "new": n.get("n")}}
        for m in sorted(set(b) & set(n)):
            pair = (b[m], n[m])
            if m == "n" or pair == (None, None) or not all(v is None or _finite(v) for v in pair):
                continue
            row[m] = {"base": b[m], "new": n[m], "delta": _r(n[m] - b[m]) if None not in pair else None}
        metrics[scope] = row
    out = {"base": base["meta"], "new": new["meta"], "metrics": metrics,
           "pairs": _mcnemar(base["pairs"], new["pairs"], lambda x: (x["a"], x["b"])),
           "groups": _mcnemar(base["groups"], new["groups"], lambda x: x["group"]),
           "spearman_delta_ci": boot_delta(base["images"], new["images"], reps)}
    out["violations"] = bench.check_budget({"metrics": metrics}, budget) if budget else []
    return out


# ---- markdown -------------------------------------------------------------------------------------

def _v(x, pct: bool = False) -> str:
    if x is None:
        return "–"
    return f"{x * 100:.1f}%" if pct else f"{x:.3f}"


def _ci(ci, pct: bool = False) -> str:
    return "" if not ci else (f" [{ci[0] * 100:.1f}, {ci[1] * 100:.1f}]" if pct else f" [{ci[0]:.3f}, {ci[1]:.3f}]")


def report_md(rep: dict[str, Any]) -> str:
    m, a = rep["meta"], rep["metrics"]["all"]
    lines = ["# bioscan aesthetic golden set", "",
             f"- model: {m['model'] or '(unnamed)'}; scores {m['scores']}",
             f"- golden: {m['golden']} (sha256 {m['golden_sha256'][:12]}), split {m['split'] or 'all'}; "
             f"git {str(m['git_sha'])[:12]}, {m['date']}",
             "", "Agreement with one owner's choices, not \"aesthetic accuracy\". A missing score counts as the "
             "lowest score everywhere.", "",
             "## Coverage", "",
             f"{a['scored']} of {a['expected']} frames scored (missing {_v(a['missing_rate'], True)}); failed "
             f"{a['failed']}, non-finite {a['nonfinite']}, not in the set {a['extra']}, listed twice {a['duplicates']}.",
             "", "## Headline", "",
             "| metric | value [95% CI] | random order |", "|---|---|---|",
             f"| pairwise accuracy ({a['pairs']} owner choices) | {_v(a['pair_acc'], True)}{_ci(a['pair_acc_ci'], True)} "
             "| 50% |",
             f"| winner of the shot group ({a['groups']} groups) | {_v(a['group_top1'], True)}"
             f"{_ci(a['group_top1_ci'], True)} | {_v(a['group_top1_random'], True)} |",
             f"| Spearman vs stars ({a.get('rated', 0)} rated) | {_v(a.get('spearman'))}{_ci(a.get('spearman_ci'))} | 0 |",
             f"| Kendall τ-b | {_v(a.get('kendall'))} | 0 |",
             f"| NDCG@{m['k']} per trip | {_v(a.get('ndcg_at_k'))} | |",
             f"| precision@k vs keepers | {_v(a.get('precision_at_k'), True)}{_ci(a.get('precision_at_k_ci'), True)} "
             f"| {_v(a.get('precision_at_k_random'), True)} |",
             f"| drop AUC (dropped frames score lower) | {_v(a['drop_auc'])} | 0.500 |"]
    for q in CULL_SHARES:
        t = round(q * 100)
        lines.append(f"| cull lowest {t}% per trip: keepers lost | {_v(a[f'keepers_lost_at_{t}'], True)}"
                     f"{_ci(a[f'keepers_lost_at_{t}_ci'], True)} | {t}.0% |")
        lines.append(f"| cull lowest {t}% per trip: rejects the owner dropped | "
                     f"{_v(a[f'reject_precision_at_{t}'], True)}{_ci(a[f'reject_precision_at_{t}_ci'], True)} | "
                     f"{_v((a['keep_labelled'] - a['keepers']) / a['keep_labelled'], True) if a['keep_labelled'] else '–'} |")
    lines += ["", "## Planted checks", "",
              f"- degradations score lower: {_v(a['degrade_acc'], True)}{_ci(a['degrade_acc_ci'], True)} of "
              f"{a['degrade_acc_n']}; by kind " + (", ".join(f"{k} {_v(v, True)}" for k, v in a["degrade_acc_by_kind"].items()) or "–"),
              f"- invariant copies stay within {m['invariance_tol'] * 100:.0f} percentile points: "
              f"{_v(a['invariance_rate'], True)}{_ci(a['invariance_rate_ci'], True)} of {a['invariance_rate_n']}; by kind "
              + (", ".join(f"{k} {_v(v, True)}" for k, v in a["invariance_rate_by_kind"].items()) or "–")
              + f"; largest move {_v(a['invariance_max_shift'], True)}"]
    if rep.get("repeat"):
        r = rep["repeat"]
        lines.append(f"- rerun: {_v(r['repeat_stable_rate'], True)} of {r['n']} frames within "
                     f"{REPEAT_TOL * 100:.0f} percentile point; largest move {_v(r['repeat_max_shift'], True)}")
    c = rep["owner_ceiling"]
    lines += ["", "## Ceiling and cost", "",
              f"- owner's own re-rating (stars vs stars2, {c['n']} frames): Spearman {_v(c['spearman'])}; a model "
              "cannot be expected to agree with the owner better than the owner agrees with themself",
              f"- median time per frame: {_v(a['ms_median'])} ms" if a.get("ms_median") is not None else
              "- time per frame: not in the scores file"]
    lines += ["", "## Slices (bias check)", "",
              "Residual = mean(score percentile − stars percentile): above 0, the model likes these frames more "
              "than the owner does; an interval that excludes 0 is a systematic lean.", "",
              "| scope | n | Spearman | pairwise acc | group top-1 | keepers lost @20% | residual [95% CI] |",
              "|---|---|---|---|---|---|---|"]
    for scope, x in rep["metrics"].items():
        lines.append(f"| {scope} | {x['n']} | {_v(x.get('spearman'))} | {_v(x['pair_acc'], True)} | "
                     f"{_v(x['group_top1'], True)} | {_v(x['keepers_lost_at_20'], True)} | "
                     f"{_v(x['residual'])}{_ci(x['residual_ci'])} |")
    if rep.get("reasons"):
        r = rep["reasons"]
        lines += ["", "## Explanations", "",
                  f"On {r['frames']} frames: owner's drop reasons found {_v(r['reason_recall'], True)}"
                  f"{_ci(r['reason_recall_ci'], True)}; model reasons the owner shares {_v(r['reason_precision'], True)}"
                  f"{_ci(r['reason_precision_ci'], True)}.", "",
                  "| reason | recall |", "|---|---|"] + [f"| {k} | {_v(v, True)} |" for k, v in r["by_reason"].items()]
    if rep.get("dims"):
        d = rep["dims"]["dims_mean"]
        lines += ["", "## Sub-scores", "",
                  f"Selecting by the mean of the model's sub-scores instead of its overall score: pairwise "
                  f"{_v(d['pair_acc'], True)}, group top-1 {_v(d['group_top1'], True)}.", "",
                  "| sub-score | Spearman vs stars |", "|---|---|"] + \
                 [f"| {k} | {_v(v)} |" for k, v in rep["dims"]["dims_spearman"].items()]
    if rep["missing"]:
        lines += ["", "Missing (first 50): " + ", ".join(rep["missing"])]
    return "\n".join(lines) + "\n"


def compare_md(c: dict[str, Any]) -> str:
    lines = ["# bioscan aesthetic compare", "", f"- base: {c['base']['model']} ({c['base']['scores']})",
             f"- new: {c['new']['model']} ({c['new']['scores']})",
             f"- golden sha256 {c['base']['golden_sha256'][:12]}, split {c['base']['split']}", "",
             "| metric | base | new | Δ |", "|---|---|---|---|"]
    for m in HEADLINE:
        row = c["metrics"].get("all", {}).get(m)
        if row:
            lines.append(f"| {m} | {_v(row['base'])} | {_v(row['new'])} | {_v(row['delta'])} |")
    p, g = c["pairs"], c["groups"]
    lines += ["", f"- pairs: {p['n']} paired; only base right {p['base_only']}, only new right {p['new_only']}, "
                  f"McNemar p {_v(p['p'])}",
              f"- shot groups: {g['n']} paired; only base right {g['base_only']}, only new right {g['new_only']}, "
              f"McNemar p {_v(g['p'])}",
              f"- Spearman change, 95% bootstrap over shot groups: {_ci(c['spearman_delta_ci']) or '–'}", ""]
    if c["violations"]:
        lines += ["**Over budget:**", ""] + [f"- {v['why']}" for v in c["violations"]]
    else:
        lines.append("Within budget." if c.get("budget") else "No budget given.")
    return "\n".join(lines) + "\n"


def table_md(reports: list[dict]) -> str:
    cols = ("pair_acc", "group_top1", "spearman", "ndcg_at_k", "drop_auc", "keepers_lost_at_20", "degrade_acc",
            "invariance_rate", "missing_rate", "ms_median")
    lines = ["| model | " + " | ".join(cols) + " |", "|---" * (len(cols) + 1) + "|"]
    for r in reports:
        a = r["metrics"]["all"]
        lines.append(f"| {r['meta']['model'] or r['meta']['scores']} | " +
                     " | ".join(_v(a.get(k)) for k in cols) + " |")
    shas = {r["meta"]["golden_sha256"] for r in reports}
    if len(shas) > 1:
        lines += ["", "**Warning: these reports come from different golden sets; the rows are not comparable.**"]
    return "\n".join(lines) + "\n"


# ---- commands ------------------------------------------------------------------------------------

def cmd_init(a) -> int:
    rows = aesbench._ratings(a.ratings)
    cull_rows: dict[str, dict] = {}
    if a.cull:
        with open(a.cull, newline="", encoding="utf-8-sig") as f:
            cull_rows = {os.path.normpath(os.path.abspath(r["path"])): r for r in csv.DictReader(f)}
    out = Path(a.out)
    target = out / IMAGES_CSV
    if target.exists() and not a.force:
        raise SystemExit(f"error: {target} exists (the golden set is frozen once built); --force replaces it")
    out.mkdir(parents=True, exist_ok=True)
    with open(target, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=IMAGE_FIELDS)
        w.writeheader()
        for r in rows:
            c = cull_rows.get(os.path.normpath(r.path), {})
            w.writerow({"path": r.path, "split": "test", "trip": r.trip, "group": c.get("burst", ""),
                        "category": c.get("category", ""), "stars": -1 if r.pick == -1 else f"{r.rating:g}",
                        "keep": {1: 1, -1: 0}.get(r.pick, "")})
    print(f"{len(rows)} frames -> {target}; next: fill group/best/keep/reasons/slices (docs/research/"
          "2026-09-24-aesthetic-golden-set.md), then scripts/aes_plant.py for the planted copies")
    return 0


def _golden(path: str) -> dict[str, Any]:
    try:
        return read_golden(path)
    except GoldenError as e:
        raise SystemExit(f"error: {e}") from None


def _scores(path: str, base: str) -> dict[str, Any]:
    try:
        return read_scores(path, base)
    except (GoldenError, OSError) as e:
        raise SystemExit(f"error: {e}") from None


def cmd_score(a) -> int:
    g = _golden(a.golden)
    sc = _scores(a.scores, g["root"])
    repeat = _scores(a.repeat, g["root"]) if a.repeat else None
    rep = evaluate(g, sc, split=None if a.split == "all" else a.split, k=a.k, repeat=repeat, scores_path=a.scores,
                   model=a.model)
    out = Path(a.out)
    path = bench.write_json(rep, out / "report.json")
    md = report_md(rep)
    (out / "report.md").write_text(md, encoding="utf-8")
    print(md)
    print(f"report.json -> {path}")
    return 0


def cmd_compare(a) -> int:
    base, new = load(a.base), load(a.new)
    budget_path = a.budget or (bench.BASELINES_DIR / "budget-aesthetic.toml")
    budget = read_budget(budget_path) if a.budget or Path(budget_path).is_file() else None
    c = compare(base, new, budget, reps=a.boot)
    c["budget"] = str(budget_path) if budget else None
    md = compare_md(c)
    if a.md:
        Path(a.md).write_text(md, encoding="utf-8")
    if a.json:
        bench.write_json(c, a.json)
    print(md)
    return 1 if c["violations"] else 0


def cmd_table(a) -> int:
    print(table_md([load(p) for p in a.reports]))
    return 0


def add_parser(b) -> None:
    """`bioscan bench aesthetic ...` under `bioscan bench`."""
    g = b.add_parser("aesthetic", help="any aesthetic scorer against the aesthetic golden set") \
        .add_subparsers(dest="aes_golden_cmd", required=True)

    s = g.add_parser("init", help="images.csv of a new golden set from the owner's ratings")
    s.add_argument("ratings", help="folder of rated images (XMP) or ratings CSV")
    s.add_argument("--out", required=True, help="golden folder (images.csv is written there)")
    s.add_argument("--cull", help="a `bioscan cull` CSV: prefill group (burst) and category for the owner to correct")
    s.add_argument("--force", action="store_true", help="replace an existing images.csv")
    s.set_defaults(func=cmd_init)

    s = g.add_parser("score", help="one scores file against the golden set: report.json + report.md")
    s.add_argument("golden", help="golden folder (images.csv, optional pairs.csv)")
    s.add_argument("scores", help="`bioscan run --json` output, NDJSON {path, score, ...} or CSV path,score")
    s.add_argument("--out", required=True)
    s.add_argument("--split", default="test", help="test | dev | all (default %(default)s)")
    s.add_argument("--repeat", help="a second scores file of the same model: rerun stability")
    s.add_argument("--model", help="model name for the report (default: from the scores file)")
    s.add_argument("--k", type=int, default=TOP_K, help="NDCG@k (default %(default)s)")
    s.set_defaults(func=cmd_score)

    s = g.add_parser("compare", help="two reports on the same golden set; exit 0 within budget, 1 over, 2 incomparable")
    s.add_argument("base")
    s.add_argument("new")
    s.add_argument("--budget", help="budget TOML (default baselines/budget-aesthetic.toml when present)")
    s.add_argument("--boot", type=int, default=BOOT, help="bootstrap draws for the Spearman change (default %(default)s)")
    s.add_argument("--md")
    s.add_argument("--json")
    s.set_defaults(func=cmd_compare)

    s = g.add_parser("table", help="several reports side by side")
    s.add_argument("reports", nargs="+")
    s.set_defaults(func=cmd_table)
