"""Candidates: an optional list of taxa ("Megascops kennicottii, Strigidae, Bubo") that species
ranking is restricted to. Empty means all taxa, the default.

A candidate is a scientific name or any higher taxon (genus, family, order, class, phylum, kingdom)
and matches every row of every loaded name list whose taxonomy holds it at some rank, compared by
`naming.norm_binomial`. This module only says which rows those are (`allowed`); how a box is ranked
among them (kind check, prior, range veto) is pipeline._species_many's. Unknown names are rejected
before the run (`unknown`, used by the identify stage).
"""
from __future__ import annotations

import threading
from typing import Any

import numpy as np

from bioscan.naming import norm_binomial

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


def allowed(lists: dict[str, Any], candidates: list[str]) -> dict[str, np.ndarray] | None:
    """{kind: indexes of the rows the candidates leave that list} for every list with one, in
    `lists` order; None when there are no candidates (all taxa)."""
    if not candidates:
        return None
    masks = resolve(lists, candidates)[0]
    return {k: np.flatnonzero(masks[k]) for k in lists if k in masks}
