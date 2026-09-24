"""`bioscan aesthetic`: the owner's ratings, training aesthetic heads, and how well a head agrees
with the owner.

- `ratings SRC`: what a ratings folder (XMP sidecars or embedded) or CSV holds, per trip.
- `train --ratings SRC` fits a personal head (default ~/.config/bioscan/aesthetic-personal.json),
  pulled toward the general head; `train --eva DIR` fits the general head from an EVA checkout.
  Vectors come from the running service's `embed` product, so no model code runs here; an
  `--embeddings FILE` cache (NDJSON) keeps them between runs.
- `eval SRC` scores heads against the ratings: Spearman / Kendall, NDCG@k and precision@k against
  the picks per trip, and the learning curve (personal vs general vs blended at 50-1000 ratings,
  split by trip, never by frame). Writes report.json (schema `bioscan-aesthetic-report`, version 1,
  tier `aesthetic-own`, read by `bioscan bench scorecard`) and report.md.

Standard library at import (the CLI stays import-light); fitting imports numpy through
bioscan.aesthetic_fit only inside `train` and the learning curve.
"""
from __future__ import annotations

import json
import os
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from bioscan import aesthetic as aes
from bioscan.cli import bench, client

REPORT_SCHEMA, REPORT_VERSION = bench.AESTHETIC_SCHEMA, bench.REPORT_VERSION
TIER, PROFILE = "aesthetic-own", "album"
EMBED_BATCH = 256                   # inputs per /run request
TOP_K = 10


# ---- vectors through the service ---------------------------------------------------------------

def _stamp(path: str) -> list[float] | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return [st.st_size, round(st.st_mtime, 3)]


def read_cache(path: str | Path | None) -> dict[str, dict[str, Any]]:
    """An embeddings cache: one JSON line per image {path, stamp: [size, mtime], vec: f16 base64}."""
    out: dict[str, dict[str, Any]] = {}
    if not path or not Path(path).is_file():
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rec = json.loads(line)
                out[rec["path"]] = rec
    return out


def embed_paths(paths: list[str], url: str, cache: str | Path | None = None,
                log=sys.stderr) -> tuple[dict[str, list[float]], dict[str, str]]:
    """{path: vector} for every path the service could embed, and {path: error} for the rest.
    Vectors already in `cache` (same file size and mtime) are reused; new ones are appended to it."""
    known = read_cache(cache)
    vecs: dict[str, list[float]] = {}
    todo = []
    for p in paths:
        rec = known.get(p)
        if rec is not None and rec.get("stamp") == _stamp(p):
            vecs[p] = aes.f16_decode(rec["vec"])
        else:
            todo.append(p)
    failed: dict[str, str] = {}
    if todo:
        print(f"embedding {len(todo)} images through {url} ({len(vecs)} from the cache)", file=log)
    sink = open(cache, "a", encoding="utf-8") if cache and todo else None
    try:
        for i in range(0, len(todo), EMBED_BATCH):
            batch = todo[i:i + EMBED_BATCH]
            payload = {"inputs": [{"path": p} for p in batch], "want": ["embed"],
                       "options": {"embed": {"format": "f16_base64"}}}
            for line in client.run(payload, url):
                ev = json.loads(line)
                if ev.get("type") == "result" and "embed" in ev.get("products", {}):
                    p, v = ev["path"], ev["products"]["embed"]["vector"]
                    vecs[p] = aes.vector_of(ev["products"]["embed"])
                    if sink:
                        sink.write(json.dumps({"path": p, "stamp": _stamp(p),
                                               "vec": v if isinstance(v, str) else aes.f16_encode(vecs[p])}) + "\n")
                elif ev.get("type") == "error":
                    failed[ev["path"]] = ev.get("message", "")
            if sink:
                sink.flush()
            print(f"  {min(i + EMBED_BATCH, len(todo))}/{len(todo)}", file=log)
    finally:
        if sink:
            sink.close()
    for p in todo:
        if p not in vecs and p not in failed:
            failed[p] = "no result (the stream ended early)"
    return vecs, failed


# ---- ratings -------------------------------------------------------------------------------------

def _ratings(source: str) -> list[aes.Rating]:
    try:
        rows = aes.read_ratings(source)
    except (OSError, ValueError) as e:
        raise SystemExit(f"error: {e}") from None
    if not rows:
        raise SystemExit(f"error: no rated images in {source} (XMP xmp:Rating in sidecars or embedded, or a CSV "
                         "with path,rating)")
    return rows


def cmd_ratings(a) -> int:
    rows = _ratings(a.source)
    picks = aes.picks_of(rows, a.pick_min)
    by_trip: dict[str, list[int]] = defaultdict(list)
    for i, r in enumerate(rows):
        by_trip[r.trip].append(i)
    print(f"{len(rows)} rated images in {len(by_trip)} trips; sources {dict(Counter(r.source for r in rows))}")
    print("stars: " + ", ".join(f"{k:g}: {v}" for k, v in sorted(Counter(r.rating for r in rows).items())))
    explicit = any(r.pick == 1 for r in rows)
    print(f"picks: {sum(picks)} ({'explicit' if explicit else f'rating >= {a.pick_min:g}'}); "
          f"rejects: {sum(r.pick == -1 for r in rows)}; labels: {dict(Counter(r.label for r in rows if r.label))}")
    for t, idx in sorted(by_trip.items()):
        print(f"  {t}: {len(idx)} rated, {sum(picks[i] for i in idx)} picks")
    if a.csv:
        import csv

        Path(a.csv).parent.mkdir(parents=True, exist_ok=True)
        with open(a.csv, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["path", "rating", "pick", "label", "trip"])
            for r in rows:
                w.writerow([r.path, f"{r.rating:g}", r.pick, r.label, r.trip])
        print(f"-> {a.csv}")
    return 0


# ---- train ---------------------------------------------------------------------------------------

def _prior(spec: str | None) -> aes.Head | None:
    """--prior builtin (when installed) | PATH | none."""
    if spec in (None, "none"):
        return None
    if spec == "builtin":
        if not aes.BUILTIN_HEAD.is_file():
            print("note: no general head installed (data/aesthetic/README.md); the personal head is fitted "
                  "without a prior", file=sys.stderr)
            return None
        spec = str(aes.BUILTIN_HEAD)
    try:
        return aes.load_head(spec)
    except aes.HeadError as e:
        raise SystemExit(f"error: --prior: {e}") from None


def cmd_train(a) -> int:
    if bool(a.eva) == bool(a.ratings):
        raise SystemExit("error: give exactly one of --eva DIR (general head) or --ratings SRC (personal head)")
    today = time.strftime("%Y-%m-%d", time.gmtime())
    if a.eva:
        eva = aes.read_eva(a.eva)
        if not eva:
            raise SystemExit(f"error: {a.eva}: no EVA image with votes ({aes.EVA_VOTES}, {aes.EVA_IMAGES}/*.jpg)")
        paths, ys, groups = [p for p, _, _ in eva], [s for _, s, _ in eva], None
        name, lo, hi, target = a.name or "eva-head-v1", 0.0, 10.0, "EVA mean general score, 0-10"
        prior = None
        out = a.out or str(aes.BUILTIN_HEAD)
        prov = {**aes.EVA_PROVENANCE, "date": today, "vectors": f"bioscan service /run embed ({a.url})"}
    else:
        rows = _ratings(a.ratings)
        paths, ys, groups = [r.path for r in rows], [r.rating for r in rows], [r.trip for r in rows]
        name, lo, hi, target = a.name or f"personal-{today}", 0.0, 5.0, "the owner's stars, 0-5 (reject = 0)"
        prior = _prior(a.prior)
        out = a.out or str(aes.PERSONAL_HEAD.expanduser())
        prov = {"data": f"the owner's ratings from {os.path.abspath(a.ratings)} ({len(set(groups))} trips)",
                "licence": "the owner's own ratings of the owner's own photos; the head stays with the owner",
                "ratings_sha256": aes.ratings_sha(rows), "date": today,
                "vectors": f"bioscan service /run embed ({a.url})"}
    vecs, failed = embed_paths(paths, a.url, a.embeddings)
    keep = [i for i, p in enumerate(paths) if p in vecs]
    if failed:
        print(f"{len(failed)} images could not be embedded and are left out, e.g. "
              f"{next(iter(failed.items()))}", file=sys.stderr)
    if len(keep) < 10:
        raise SystemExit(f"error: only {len(keep)} images with a vector; need at least 10 to fit a head")
    from bioscan import aesthetic_fit as fit  # numpy: only here, never at CLI import

    X = [vecs[paths[i]] for i in keep]
    doc, summary = fit.train_head(fit.np.asarray(X), fit.np.asarray([ys[i] for i in keep]), name=name, lo=lo,
                                  hi=hi, target=target, provenance=prov, k=a.folds, seed=a.seed,
                                  groups=None if groups is None else [groups[i] for i in keep], prior=prior)
    path = aes.write_head(doc, out)
    cv = summary["cv"]
    print(f"head {doc['name']}:{doc['sha'][:12]} -> {path}")
    print(f"n {summary['n']}, alpha {summary['alpha']:g}, prior {summary['prior'] or 'none'}; " +
          (f"{cv['k']}-fold CV by {cv['by']}: SRCC {cv['srcc']:.3f} (sd {cv['srcc_sd']:.3f}), PLCC {cv['plcc']:.3f}"
           if cv else "no cross-validation (too few rows or trips)"))
    return 0


# ---- eval ----------------------------------------------------------------------------------------

def _general(spec: str) -> aes.Head | None:
    if spec == "none":
        return None
    path = aes.BUILTIN_HEAD if spec == "builtin" else Path(spec)
    if spec == "builtin" and not path.is_file():
        print("note: no general head installed (data/aesthetic/README.md): general and blended rows are empty",
              file=sys.stderr)
        return None
    try:
        return aes.load_head(path)
    except aes.HeadError as e:
        raise SystemExit(f"error: --head: {e}") from None


def score_rows(heads: dict[str, aes.Head | None], weight: float, vecs: list[list[float]]) -> dict[str, list[float]]:
    """{general, personal, blended}: each head's 0-1 scores over the vectors (only the heads there are)."""
    out = {name: [h.predict(v) for v in vecs] for name, h in heads.items() if h is not None}
    if "general" in out and "personal" in out:
        out["blended"] = [aes.blend(g, p, weight) for g, p in zip(out["general"], out["personal"])]
    return out


def set_metrics(scores: list[float], rows: list[aes.Rating], picked: list[bool], k: int) -> dict[str, Any]:
    """Pooled Spearman / Kendall / PLCC; per-trip NDCG@k (mean) and precision@k with k = the trip's
    number of picks (pooled hits over pooled picks, Wilson interval), and its random-order expectation."""
    m = aes.rows_metrics(scores, rows, picked, k)
    by_trip: dict[str, list[int]] = defaultdict(list)
    for i, r in enumerate(rows):
        by_trip[r.trip].append(i)
    trips, ndcgs, hits, total, rand = [], [], 0, 0, 0.0
    for t, idx in sorted(by_trip.items()):
        s, rs, pk = [scores[i] for i in idx], [rows[i] for i in idx], [picked[i] for i in idx]
        kp = sum(pk)
        h = aes.hits_at_k(s, pk, kp, [r.path for r in rs]) if kp else 0
        g = aes.ndcg_at_k(s, [r.rating for r in rs], k, [r.path for r in rs]) if len(idx) > 1 else None
        rho = aes.spearman(s, [r.rating for r in rs])
        trips.append({"trip": t, "n": len(idx), "picks": kp, "hits": h,
                      "precision_at_k": round(h / kp, 6) if kp else None,
                      "ndcg_at_k": None if g is None else round(g, 6), "spearman": None if rho is None else round(rho, 6)})
        if g is not None:
            ndcgs.append(g)
        hits, total, rand = hits + h, total + kp, rand + kp * kp / len(idx)
    rhos = [t["spearman"] for t in trips if t["spearman"] is not None]
    m.update({"ndcg_at_k": round(sum(ndcgs) / len(ndcgs), 6) if ndcgs else None,
              "precision_at_k": round(hits / total, 6) if total else None, "precision_at_k_ci": bench.wilson(hits, total),
              "precision_at_k_random": round(rand / total, 6) if total else None,
              "spearman_trip_mean": round(sum(rhos) / len(rhos), 6) if rhos else None, "trips": len(trips)})
    return {"metrics": m, "per_trip": trips}


def evaluate(rows: list[aes.Rating], vecs: list[list[float]], general: aes.Head | None,
             personal: aes.Head | None, *, weight: float = 0.5, k: int = TOP_K, pick_min: float = aes.PICK_MIN,
             curve: list[int] | None = None, folds: int = 5, seed: int = 0, alpha: float = 100.0,
             source: str = "") -> dict[str, Any]:
    """The report for one ratings set (rows and their vectors in the same order)."""
    picked = aes.picks_of(rows, pick_min)
    scores = score_rows({"general": general, "personal": personal}, weight, vecs)
    served = "blended" if "blended" in scores else ("personal" if "personal" in scores else "general")
    by_head = {name: set_metrics(s, rows, picked, k) for name, s in scores.items()}
    in_sample = personal is not None and personal.provenance.get("ratings_sha256") == aes.ratings_sha(rows)
    lc = None
    if curve:
        from bioscan import aesthetic_fit as fit  # numpy: only for the curve

        lc = fit.learning_curve(fit.np.asarray(vecs), fit.np.asarray([r.rating for r in rows]),
                                [r.trip for r in rows], general=general, sizes=curve, k=folds, seed=seed,
                                alpha=alpha, weight=weight)
    sha, dirty = bench._git()
    empty = {"n": len(rows), "trips": len({r.trip for r in rows})}
    meta = {"tier": TIER, "profile": PROFILE, "git_sha": sha, "git_dirty": dirty,
            "date": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "groundtruth": source,
            "ratings_sha256": aes.ratings_sha(rows), "n": len(rows), "trips": empty["trips"],
            "picks": sum(picked), "picks_from": "explicit" if any(r.pick == 1 for r in rows) else f"rating>={pick_min:g}",
            "k": k, "blend": weight, "served": served if scores else None,
            "heads": {"general": general.id if general else None, "personal": personal.id if personal else None},
            "personal_in_sample": in_sample}
    return {"schema": REPORT_SCHEMA, "version": REPORT_VERSION, "meta": meta,
            "metrics": {"all": by_head[served]["metrics"] if scores else empty},
            "by_head": {name: v["metrics"] for name, v in by_head.items()},
            "per_trip": {name: v["per_trip"] for name, v in by_head.items()}, "curve": lc}


def _v(x, pct: bool = False) -> str:
    if x is None:
        return "–"
    return f"{x * 100:.1f}%" if pct else f"{x:.3f}"


def report_md(rep: dict[str, Any]) -> str:
    m = rep["meta"]
    lines = ["# bioscan aesthetic eval", "",
             f"- ratings: {m['groundtruth']} ({m['n']} frames, {m['trips']} trips, {m['picks']} picks from "
             f"{m['picks_from']}); git {str(m['git_sha'])[:12]}, {m['date']}",
             f"- heads: general {m['heads']['general'] or 'none'}, personal {m['heads']['personal'] or 'none'}, "
             f"blend {m['blend']:g} (personal weight); served score: {m['served'] or 'none'}",
             f"- tier {m['tier']}, profile {m['profile']}: `bioscan bench scorecard report.json` holds the served "
             "score to data/standards.toml", ""]
    if m["personal_in_sample"]:
        lines += ["**The personal head was fitted on these same ratings: its rows below are in-sample (optimistic). "
                  "The learning curve fits fresh heads on held-out trips and is the honest number.**", ""]
    lines += ["Agreement with the owner, not \"aesthetic accuracy\". Spearman/Kendall/PLCC pool every frame; NDCG@k "
              f"(k = {m['k']}, gain 2^stars - 1) is the mean over trips; precision@k takes k = each trip's number of "
              "picks, pooled over trips, next to what a random order would get.", "",
              "| head | n | Spearman [95% CI] | Kendall | PLCC | Spearman per trip (mean) | NDCG@k | precision@k [95% CI] "
              "| random |", "|---" * 9 + "|"]
    for name, x in rep["by_head"].items():
        ci = x.get("spearman_ci")
        pci = x.get("precision_at_k_ci")
        lines.append(f"| {name} | {x['n']} | {_v(x['spearman'])}" + (f" [{ci[0]:.3f}, {ci[1]:.3f}]" if ci else "") +
                     f" | {_v(x['kendall'])} | {_v(x['plcc'])} | {_v(x['spearman_trip_mean'])} | {_v(x['ndcg_at_k'])} | "
                     f"{_v(x['precision_at_k'], True)}" + (f" [{pci[0] * 100:.1f}, {pci[1] * 100:.1f}]" if pci else "") +
                     f" | {_v(x['precision_at_k_random'], True)} |")
    if not rep["by_head"]:
        lines.append("| (no head) | | | | | | | | |")
    served = m["served"]
    if served:
        lines += ["", f"Per trip ({served}):", "", "| trip | n | picks | hits | precision@k | NDCG@k | Spearman |",
                  "|---" * 7 + "|"]
        for t in rep["per_trip"][served]:
            lines.append(f"| {t['trip']} | {t['n']} | {t['picks']} | {t['hits']} | {_v(t['precision_at_k'], True)} | "
                         f"{_v(t['ndcg_at_k'])} | {_v(t['spearman'])} |")
    lc = rep.get("curve")
    if lc:
        lines += ["", f"## Learning curve (Spearman on held-out trips; {lc['k']} trip folds, {lc['repeats']} draws "
                      f"each, alpha {lc['alpha']:g}, blend {lc['weight']:g}; seed {lc['seed']})", "",
                  f"{lc['n_ratings']} ratings in {lc['trips']} trips; at most {lc['n_train_max']} train ratings in a "
                  "fold, so larger sizes are skipped.", "",
                  "| ratings | runs | personal | general | blended |", "|---|---|---|---|---|"]
        for p in lc["points"]:
            cells = ["–" if p[key] is None else f"{p[key]:.3f} ± {p[key + '_sd']:.3f}"
                     for key in ("personal", "general", "blended")]
            lines.append(f"| {p['n']} | {p['runs']} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def cmd_eval(a) -> int:
    rows = _ratings(a.source)
    general = _general(a.head)
    personal = None
    if a.personal:
        try:
            personal = aes.load_head(a.personal)
        except aes.HeadError as e:
            raise SystemExit(f"error: --personal: {e}") from None
    vecs, failed = embed_paths([r.path for r in rows], a.url, a.embeddings)
    if failed:
        print(f"{len(failed)} rated images could not be embedded and are left out", file=sys.stderr)
    rows = [r for r in rows if r.path in vecs]
    if not rows:
        raise SystemExit("error: no rated image could be embedded")
    curve = None if a.no_curve else [int(x) for x in a.curve.split(",") if x.strip()]
    rep = evaluate(rows, [vecs[r.path] for r in rows], general, personal, weight=a.blend, k=a.k, pick_min=a.pick_min,
                   curve=curve, folds=a.folds, seed=a.seed, alpha=a.alpha, source=os.path.abspath(a.source))
    out = Path(a.out)
    path = bench.write_json(rep, out / "report.json")
    md = report_md(rep)
    (out / "report.md").write_text(md, encoding="utf-8")
    print(md)
    print(f"report.json -> {path}")
    return 0


# ---- parser --------------------------------------------------------------------------------------

def add_parser(sub) -> None:
    g = sub.add_parser("aesthetic", help="aesthetic heads: the owner's ratings, training, agreement with the owner") \
        .add_subparsers(dest="aesthetic_cmd", required=True)

    def common(s):
        s.add_argument("--embeddings", metavar="FILE", help="NDJSON cache of frame vectors, reused and extended")
        s.add_argument("--pick-min", type=float, default=aes.PICK_MIN,
                       help="without explicit picks, a rating >= this is a pick (default %(default)g)")
        s.add_argument("--seed", type=int, default=0)
        s.add_argument("--folds", type=int, default=5, help="cross-validation folds (by trip for ratings)")

    s = g.add_parser("ratings", help="what a ratings folder (XMP) or CSV holds, per trip")
    s.add_argument("source", help="folder of rated images (xmp:Rating in sidecars or embedded), or CSV path,rating")
    s.add_argument("--pick-min", type=float, default=aes.PICK_MIN)
    s.add_argument("--csv", help="also write the ratings as CSV (path,rating,pick,label,trip)")
    s.set_defaults(func=cmd_ratings)

    s = g.add_parser("train", help="fit a personal head (--ratings) or the general head (--eva)")
    s.add_argument("--ratings", help="folder of rated images or ratings CSV: fits a personal head")
    s.add_argument("--eva", help="EVA checkout (data/votes_filtered.csv + images/EVA_together/): the general head")
    s.add_argument("--out", help=f"head file (default: personal {aes.PERSONAL_HEAD}, EVA {aes.BUILTIN_HEAD})")
    s.add_argument("--name", help="head name (default personal-<date> or eva-head-v1)")
    s.add_argument("--prior", default="builtin", help="personal head: pull toward builtin | PATH | none "
                                                      "(default builtin, when installed)")
    common(s)
    s.set_defaults(func=cmd_train)

    s = g.add_parser("eval", help="agreement of heads with the owner's ratings; report.json + report.md")
    s.add_argument("source", help="folder of rated images or ratings CSV")
    s.add_argument("--out", required=True, help="folder for report.json and report.md")
    s.add_argument("--head", default="builtin", help="general head: builtin | PATH | none (default builtin)")
    s.add_argument("--personal", help="personal head file to score as well")
    s.add_argument("--blend", type=float, default=0.5, help="personal weight in the blend (default %(default)g)")
    s.add_argument("--k", type=int, default=TOP_K, help="NDCG@k (default %(default)s)")
    s.add_argument("--curve", default="50,100,200,500,1000", help="learning-curve sizes (default %(default)s)")
    s.add_argument("--no-curve", action="store_true", help="skip the learning curve (no fitting, no numpy)")
    s.add_argument("--alpha", type=float, default=100.0, help="ridge strength of the curve's personal heads")
    common(s)
    s.set_defaults(func=cmd_eval)
