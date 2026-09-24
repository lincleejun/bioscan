"""Candidates: an optional list of taxa ("Megascops kennicottii, Strigidae, Bubo") that species
ranking is restricted to. Empty means all taxa, the default.

A candidate is a scientific name or any higher taxon (genus, family, order, class, phylum, kingdom)
and matches every row of every loaded name list whose taxonomy holds it at some rank, compared by
`naming.norm_binomial`. With candidates, a box is ranked among the matching rows of all lists
together, whatever its kind: the scaled similarities (`bioclip.logits`) of those rows share one
softmax, each list's location prior reweights its own rows within that list's share, and the box's
kind and `species.list` follow the list of the top-1 row. Unknown names are rejected before the run
(`unknown`, used by products.py).
"""
from __future__ import annotations

import threading
from typing import Any

import numpy as np

from bioscan import contract
from bioscan.naming import norm_binomial
from bioscan.service.rules import species_crops, species_level

RANKS = 7


class TaxonIndex:
    """Per name list and rank: each row's name as a code, and match key -> codes. Built once per
    list (a few seconds for the all-taxa list) and kept."""

    def __init__(self, taxonomy: list[list[str]]) -> None:
        self.codes: list[np.ndarray] = []
        self.keys: list[dict[str, list[int]]] = []
        for r in range(RANKS):
            vocab: dict[str, int] = {}
            self.codes.append(np.fromiter((vocab.setdefault(t[r] if r < len(t) else "", len(vocab)) for t in taxonomy),
                                          np.int32, len(taxonomy)))
            keys: dict[str, list[int]] = {}
            for name, code in vocab.items():
                if key := norm_binomial(name):
                    keys.setdefault(key, []).append(code)
            self.keys.append(keys)

    def mask(self, name: str) -> np.ndarray | None:
        """Rows holding `name` at any rank; None when no row does."""
        key, out = norm_binomial(name), None
        for codes, keys in zip(self.codes, self.keys):
            for code in keys.get(key, ()):
                hit = codes == code
                out = hit if out is None else out | hit
        return out


_INDEX: dict[int, tuple[Any, TaxonIndex]] = {}      # id(NameList) -> (the list, its index)
_LOCK = threading.Lock()


def index(nl: Any) -> TaxonIndex:
    with _LOCK:
        hit = _INDEX.get(id(nl))
        if hit is None or hit[0] is not nl:           # the entry holds the list, so its id is not reused meanwhile
            hit = _INDEX[id(nl)] = (nl, TaxonIndex(nl.taxonomy))
        return hit[1]


def resolve(lists: dict[str, Any], candidates: list[str]) -> tuple[dict[str, np.ndarray], list[str]]:
    """({kind: rows allowed} for every list with at least one match, [candidates matching nothing])."""
    allowed: dict[str, np.ndarray] = {}
    unknown = []
    for name in candidates:
        found = False
        for kind, nl in lists.items():
            m = index(nl).mask(name)
            if m is not None and m.any():
                allowed[kind] = m if kind not in allowed else allowed[kind] | m
                found = True
        if not found:
            unknown.append(name)
    return allowed, unknown


def unknown(lists: dict[str, Any], candidates: list[str]) -> list[str]:
    return resolve(lists, candidates)[1]


def species_among(engine: Any, work: list[tuple[Any, list[dict[str, Any]], list[tuple[float, ...]]]],
                  opts: dict[str, Any], batch: int) -> None:
    """Fills every box's "species" (and moves its "kind" to the top-1 row's list) ranking only the
    rows the candidates allow, across every loaded list. `work` is pipeline._species_many's."""
    allowed, _unknown = resolve(engine.names, opts["candidates"])
    lists = [(kind, engine.names[kind], np.flatnonzero(m)) for kind, m in allowed.items()]
    refs = [(fi, bi) for fi, (_f, boxes, _b) in enumerate(work) for bi in range(len(boxes))]
    for fi, bi in refs:
        work[fi][1][bi]["species"] = None
    if not lists:
        return
    p_geo: dict[tuple[str, int], np.ndarray | None] = {}
    for kind, _nl, rows in lists:
        prior = engine.priors.get(kind)
        for fi in {fi for fi, _bi in refs}:
            f = work[fi][0]
            g = prior.p_geo(f.lat, f.lon, f.taken_at) if opts["geo"] and prior is not None else None
            p_geo[kind, fi] = None if g is None else np.asarray(g, dtype=np.float64)[rows]
    owner = np.concatenate([np.full(len(rows), n) for n, (_k, _nl, rows) in enumerate(lists)])   # list per joint row
    where = np.concatenate([rows for _k, _nl, rows in lists])                                   # row in that list
    offset = np.cumsum([0] + [len(rows) for _k, _nl, rows in lists])
    for start in range(0, len(refs), batch):
        part = refs[start:start + batch]
        crops = [species_crops(work[fi][0].image, [work[fi][2][bi]], work[fi][0].detail)[0] for fi, bi in part]
        feats = engine.bioclip.encode_images(crops)
        logits = [np.asarray(engine.bioclip.logits(feats, nl.matrix), dtype=np.float64)[:, rows]
                  for _kind, nl, rows in lists]
        for j, (fi, bi) in enumerate(part):
            z = np.concatenate([lg[j] for lg in logits])
            p = np.exp(z - z.max())
            p /= p.sum()
            post = p.copy()
            for n, (kind, _nl, _rows) in enumerate(lists):
                g, prior, sl = p_geo[kind, fi], engine.priors.get(kind), slice(offset[n], offset[n + 1])
                w = p[sl] * (prior.floor + g) if g is not None and prior is not None else None
                if w is not None and w.sum() > 0:
                    # the list keeps its visual share of the mass; its prior reweights rows inside it
                    post[sl] = p[sl].sum() * w / w.sum()
            order = np.argsort(-post, kind="stable")[:opts["top_k"]]
            top = []
            for i in order:
                n = owner[i]
                kind, nl, _rows = lists[n]
                r, g = int(where[i]), p_geo[kind, fi]
                top.append(contract.candidate(nl.scientific[r], nl.common[r] or None, list(nl.taxonomy[r]),
                                              round(float(p[i]), 6),
                                              None if g is None else round(float(g[i - offset[n]]), 6),
                                              round(float(post[i]), 6)))
            kind, nl, _rows = lists[owner[order[0]]]
            box = work[fi][1][bi]
            box["kind"] = kind
            box["species"] = contract.species(nl.list_id, species_level(top), top)
