"""Fitting aesthetic heads (numpy): ridge regression on centred SigLIP2 frame vectors, K-fold
cross-validation, an optional pull toward a prior head, and the personalisation learning curve.

Imported only by `bioscan aesthetic train|eval` (lazily: the CLI stays import-light) and by
scripts/train_aesthetic_head.py. Head files, ratings and the ranking metrics are bioscan/aesthetic.py
(standard library). Everything here is deterministic given `seed`.
"""
from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from bioscan import aesthetic as aes

ALPHAS = (1.0, 10.0, 100.0, 1000.0, 10000.0)      # ridge strengths tried by cross-validation
STD_FLOOR = 1e-6
CURVE_SIZES = (50, 100, 200, 500, 1000)
CURVE_REPEATS = 3


@dataclass
class Fit:
    """A fitted linear head in standardised coordinates: pred = bias + w . (x - mean) / std."""
    w: np.ndarray
    bias: float
    mean: np.ndarray
    std: np.ndarray
    alpha: float

    def predict(self, X: np.ndarray) -> np.ndarray:
        return (X - self.mean) / self.std @ self.w + self.bias


def prior_weights(prior: aes.Head, mean: np.ndarray, std: np.ndarray, lo: float, hi: float) -> tuple[np.ndarray, float]:
    """The prior head as (w0, b0) in this fit's standardisation and rating units: its 0-1 score
    mapped onto [lo, hi], so a personal head can be pulled toward the general one."""
    a, c = prior.folded()
    a = np.asarray(a, dtype=np.float64)
    k = (hi - lo) / (prior.hi - prior.lo)
    return a * std * k, float(lo + k * (a @ mean + c - prior.lo))


def ridge(X: np.ndarray, y: np.ndarray, alpha: float, prior: aes.Head | None = None,
          lo: float = 0.0, hi: float = 1.0) -> Fit:
    """argmin_w |Xs w + b - y|^2 + alpha |w - w0|^2 on standardised X (w0 = 0, or the prior head).

    Standardised = centred per dimension and divided by ONE scale for all dimensions (the overall
    std): the embedding's geometry is kept, so the ridge penalty treats every direction alike and
    low-variance, mostly-noise dimensions are not blown up to unit variance."""
    X = np.asarray(X, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    mean = X.mean(axis=0)
    std = np.full(X.shape[1], max(float((X - mean).std()), STD_FLOOR))
    Xs = (X - mean) / std
    ym = float(y.mean())
    w0 = np.zeros(X.shape[1])
    if prior is not None:
        w0, _ = prior_weights(prior, mean, std, lo, hi)
    A = Xs.T @ Xs + alpha * np.eye(X.shape[1])
    w = np.linalg.solve(A, Xs.T @ (y - ym) + alpha * w0)
    return Fit(w, ym, mean, std, alpha)


def _metrics(pred: np.ndarray, y: np.ndarray) -> dict[str, float | None]:
    return {"srcc": aes.spearman(pred.tolist(), y.tolist()), "plcc": aes.pearson(pred.tolist(), y.tolist())}


def folds_of(n: int, k: int, seed: int, groups: Sequence[str] | None = None) -> np.ndarray:
    """A fold per row: by group (trip) when given, so a group never sits on both sides; else a
    seeded random split of the rows."""
    if groups is not None:
        return np.asarray(aes.group_folds(list(groups), k, seed))
    return np.random.default_rng(seed).permutation(n) % k


def cross_validate(X: np.ndarray, y: np.ndarray, *, k: int = 5, seed: int = 0, alphas: Sequence[float] = ALPHAS,
                   groups: Sequence[str] | None = None, prior: aes.Head | None = None, lo: float = 0.0,
                   hi: float = 1.0) -> dict[str, Any]:
    """K-fold CV for each alpha: mean SRCC/PLCC over held-out folds. Returns the best alpha (by mean
    SRCC, ties to the stronger ridge) and every alpha's per-fold numbers."""
    fold = folds_of(len(y), k, seed, groups)
    kk = int(fold.max()) + 1
    table = []
    for alpha in alphas:
        per = []
        for f in range(kk):
            tr, te = fold != f, fold == f
            if tr.sum() < 2 or te.sum() < 2:
                continue
            fit = ridge(X[tr], y[tr], alpha, prior, lo, hi)
            m = _metrics(fit.predict(X[te]), y[te])
            per.append({"fold": f, "n": int(te.sum()), **{k2: None if v is None else round(v, 6) for k2, v in m.items()}})
        srcc = [p["srcc"] for p in per if p["srcc"] is not None]
        plcc = [p["plcc"] for p in per if p["plcc"] is not None]
        table.append({"alpha": alpha, "srcc_mean": round(float(np.mean(srcc)), 6) if srcc else None,
                      "srcc_sd": round(float(np.std(srcc)), 6) if srcc else None,
                      "plcc_mean": round(float(np.mean(plcc)), 6) if plcc else None, "folds": per})
    scored = [t for t in table if t["srcc_mean"] is not None]
    best = max(scored, key=lambda t: (t["srcc_mean"], t["alpha"])) if scored else None
    return {"k": kk, "seed": seed, "by": "group" if groups is not None else "row",
            "best_alpha": best["alpha"] if best else max(alphas), "best": best, "alphas": table}


def train_head(X: np.ndarray, y: np.ndarray, *, name: str, lo: float, hi: float, target: str,
               provenance: dict[str, Any], k: int = 5, seed: int = 0, alphas: Sequence[float] = ALPHAS,
               groups: Sequence[str] | None = None, prior: aes.Head | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Cross-validate alpha, refit on everything, return (head document, CV summary)."""
    t0 = time.monotonic()
    cv = cross_validate(X, y, k=k, seed=seed, alphas=alphas, groups=groups, prior=prior, lo=lo, hi=hi) \
        if len(y) >= 2 * max(2, k) or groups is not None else None
    alpha = cv["best_alpha"] if cv else max(alphas)
    fit = ridge(X, y, alpha, prior, lo, hi)
    summary = {"n": int(len(y)), "alpha": alpha, "seed": seed,
               "cv": None if cv is None else {"k": cv["k"], "by": cv["by"], "srcc": cv["best"]["srcc_mean"],
                                              "srcc_sd": cv["best"]["srcc_sd"], "plcc": cv["best"]["plcc_mean"],
                                              "alphas": [{kk: t[kk] for kk in ("alpha", "srcc_mean", "plcc_mean")}
                                                         for t in cv["alphas"]]} if cv["best"] else None,
               "prior": prior.id if prior is not None else None, "fit_s": round(time.monotonic() - t0, 2)}
    doc = aes.make_head(name, fit.w.tolist(), fit.bias, fit.mean.tolist(), fit.std.tolist(), lo, hi, target,
                        {**provenance, "n": int(len(y)), "seed": seed, "alpha": alpha, "cv": summary["cv"],
                         "prior": summary["prior"]})
    return doc, summary


def head_scores(head: aes.Head, X: np.ndarray) -> np.ndarray:
    """0-1 scores of a loaded head over rows of X (vectorised Head.predict)."""
    a, c = head.folded()
    return (np.asarray(X, dtype=np.float64) @ np.asarray(a) + c - head.lo) / (head.hi - head.lo)


def learning_curve(X: np.ndarray, y: np.ndarray, trips: Sequence[str], *, general: aes.Head | None,
                   sizes: Sequence[int] = CURVE_SIZES, k: int = 5, seed: int = 0, repeats: int = CURVE_REPEATS,
                   alpha: float = 100.0, weight: float = 0.5, lo: float = 0.0, hi: float = 5.0) -> dict[str, Any]:
    """SRCC on held-out trips against the number of the owner's ratings a personal head is fitted on.

    Trips are split into k folds (never a trip on both sides). For each fold and size N, N ratings
    are drawn from the other folds' trips (`repeats` seeded draws), a personal head is fitted
    (pulled toward `general` when there is one) and scored on the held-out trips: `personal`,
    `general` (the same frames, no fitting) and `blended` (weight on personal). A size larger than
    a fold's training set is skipped for that fold; `n_train_max` gives the largest there was."""
    X = np.asarray(X, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    fold = folds_of(len(y), k, seed, trips)
    kk = int(fold.max()) + 1
    g_all = head_scores(general, X) if general is not None else None
    rng = np.random.default_rng(seed)
    points = []
    n_train_max = 0
    for n in sizes:
        cells: dict[str, list[float]] = {"personal": [], "general": [], "blended": []}
        used = 0
        for f in range(kk):
            tr_idx, te = np.flatnonzero(fold != f), fold == f
            n_train_max = max(n_train_max, len(tr_idx))
            if len(tr_idx) < n or te.sum() < 3:
                continue
            for _ in range(repeats):
                pick = rng.choice(tr_idx, size=n, replace=False)
                fit = ridge(X[pick], y[pick], alpha, general, lo, hi)
                p = (fit.predict(X[te]) - lo) / (hi - lo)
                cells["personal"].append(aes.spearman(p.tolist(), y[te].tolist()))
                if g_all is not None:
                    cells["general"].append(aes.spearman(g_all[te].tolist(), y[te].tolist()))
                    cells["blended"].append(aes.spearman(((1 - weight) * g_all[te] + weight * p).tolist(),
                                                         y[te].tolist()))
                used += 1
        row: dict[str, Any] = {"n": n, "runs": used}
        for key, vals in cells.items():
            vals = [v for v in vals if v is not None]
            row[key] = round(float(np.mean(vals)), 6) if vals else None
            row[key + "_sd"] = round(float(np.std(vals)), 6) if vals else None
        points.append(row)
    return {"k": kk, "seed": seed, "repeats": repeats, "alpha": alpha, "weight": weight, "trips": len(set(trips)),
            "n_ratings": int(len(y)), "n_train_max": n_train_max, "points": points}
