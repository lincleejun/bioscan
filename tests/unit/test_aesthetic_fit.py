"""bioscan/aesthetic_fit.py on synthetic embeddings: a planted linear signal is recovered, the fit is
seeded, the pull toward a prior head works, CV and the learning curve split by trip."""
import numpy as np
import pytest
from aesthetic_helpers import random_head

from bioscan import aesthetic as aes
from bioscan import aesthetic_fit as fit


def synthetic(n=600, seed=0, noise=0.3, trips=12):
    """Unit-length 768-d vectors that mostly vary in a 32-d subspace (as image embeddings do), a
    planted direction, stars = a noisy linear function of it."""
    rng = np.random.default_rng(seed)
    basis = np.random.default_rng(100).normal(size=(32, aes.DIM))       # the same subspace for every seed
    X = rng.normal(size=(n, 32)) @ basis + 0.3 * rng.normal(size=(n, aes.DIM))
    X /= np.linalg.norm(X, axis=1, keepdims=True)
    w = np.random.default_rng(101).normal(size=32) @ basis
    signal = X @ w
    y = (signal - signal.mean()) / signal.std() + noise * rng.normal(size=n)
    y = np.clip(2.5 + y, 0, 5)
    return X, y, [f"trip{i % trips}" for i in range(n)]


def test_planted_signal_is_recovered_with_high_srcc():
    X, y, _ = synthetic(1200, seed=1)
    doc, summary = fit.train_head(X[:900], y[:900], name="synth", lo=0, hi=5, target="stars",
                                  provenance={"data": "synthetic", "licence": "CC0"}, k=5, seed=0)
    head = aes.parse_head(doc)
    held = fit.head_scores(head, X[900:])
    assert aes.spearman(held.tolist(), y[900:].tolist()) > 0.85
    assert summary["cv"]["srcc"] > 0.8 and summary["cv"]["k"] == 5 and summary["cv"]["by"] == "row"
    assert doc["provenance"]["n"] == 900 and doc["provenance"]["alpha"] == summary["alpha"]
    # the stdlib Head.predict (what eval uses) and the numpy scores agree
    assert head.predict(X[900].tolist()) == pytest.approx(float(held[0]), abs=1e-4)


def test_noise_only_gives_no_signal():
    X, _, _ = synthetic(400, seed=2)
    y = np.random.default_rng(9).uniform(0, 5, size=400)
    _, summary = fit.train_head(X, y, name="noise", lo=0, hi=5, target="stars", provenance={}, k=5, seed=0)
    assert abs(summary["cv"]["srcc"]) < 0.2


def test_fit_is_deterministic_with_a_seed():
    X, y, trips = synthetic(300, seed=3)
    a = fit.train_head(X, y, name="s", lo=0, hi=5, target="t", provenance={}, seed=4, groups=trips)[0]
    b = fit.train_head(X, y, name="s", lo=0, hi=5, target="t", provenance={}, seed=4, groups=trips)[0]
    assert a["sha"] == b["sha"]


def test_cv_by_trip_keeps_each_trip_in_one_fold():
    X, y, trips = synthetic(240, seed=5, trips=8)
    cv = fit.cross_validate(X, y, k=4, seed=0, alphas=(10.0, 100.0), groups=trips)
    assert cv["by"] == "group" and cv["k"] == 4
    folds = fit.folds_of(len(y), 4, 0, trips)
    for t in set(trips):
        assert len({int(f) for f, g in zip(folds, trips) if g == t}) == 1


def test_prior_pulls_a_small_fit_toward_the_general_head():
    """With few ratings and a strong ridge, the personal head ranks like its prior (the general head)."""
    general = aes.parse_head(random_head(7, lo=0, hi=10))
    X, _, _ = synthetic(40, seed=6)
    y = np.random.default_rng(1).uniform(0, 5, size=40)          # ratings unrelated to the prior
    test = synthetic(200, seed=8)[0]
    pulled = fit.ridge(X, y, 1e6, general, 0, 5)
    free = fit.ridge(X, y, 1e6, None, 0, 5)
    g = fit.head_scores(general, test)
    assert aes.spearman(pulled.predict(test).tolist(), g.tolist()) > 0.99
    assert abs(aes.spearman(free.predict(test).tolist(), g.tolist()) or 0) < 0.5
    # prior_weights maps the prior's 0-1 score onto the fit's rating range exactly
    w0, b0 = fit.prior_weights(general, pulled.mean, pulled.std, 0, 5)
    xs = (test - pulled.mean) / pulled.std
    assert np.allclose(xs @ w0 + b0, 5 * g, atol=1e-6)


def test_learning_curve_shape_and_honesty():
    X, y, trips = synthetic(700, seed=10, trips=10)
    general = None
    lc = fit.learning_curve(X, y, trips, general=general, sizes=(50, 200, 5000), k=5, seed=0, repeats=2)
    assert [p["n"] for p in lc["points"]] == [50, 200, 5000]
    p50, p200, too_big = lc["points"]
    assert p50["runs"] == p200["runs"] == 10 and too_big["runs"] == 0 and too_big["personal"] is None
    assert p200["personal"] > p50["personal"] and p200["personal"] > 0.5         # more ratings, better ranking
    assert p50["general"] is None and p50["blended"] is None                    # no general head given
    assert lc["trips"] == 10 and lc["n_train_max"] < 700


def test_learning_curve_with_a_general_head_reports_all_three():
    X, y, trips = synthetic(400, seed=11, trips=8)
    general = aes.parse_head(random_head(3))
    lc = fit.learning_curve(X, y, trips, general=general, sizes=(50,), k=4, seed=1, repeats=1)
    p = lc["points"][0]
    assert p["runs"] == 4 and None not in (p["personal"], p["general"], p["blended"])
    assert p["general_sd"] < 0.5 and lc["weight"] == 0.5
