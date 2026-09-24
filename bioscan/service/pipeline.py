"""identify, orchestrated: gate -> detector -> crop gate -> species (+ location prior, range veto,
kind check).

Talks to the models only through `Models` (what Engine provides), and to the rules in rules.py.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np
from PIL import Image

from bioscan import contract
from bioscan.service.adapters.owlv2 import Detection
from bioscan.service.rules import (
    RESCUE,
    SECOND_PASS_FLOOR,
    SECOND_PASS_TOP,
    crop_with_context,
    dedupe,
    judge,
    kind_evidence,
    kind_of,
    quality,
    range_veto,
    species_crops,
    species_level,
)
from bioscan.service.taxa import KIND_CHECK, VOCAB

CROP_BATCH = 32          # crop-gate crops per SigLIP2 call
SPECIES_BATCH = 16       # species crops per BioCLIP call

# The v1.5 accuracy fixes, each an identify option so a run can switch one off to measure it
# (`bioscan eval --identify-opt NAME=false`). A missing option means the default here, which
# products.DEFAULTS repeats. In settings.fingerprint().
SWITCHES = {
    "range_veto": True,      # rules.range_veto at the species step
    "kind_check": True,      # species evidence may move a box between KIND_CHECK kinds
    "mammal_geo": True,      # the mammal list's location prior (MDD rows, genus back-off)
}
PRIOR_SWITCH = {"mammal": "mammal_geo"}      # kind -> the switch its location prior needs


def switch(opts: dict[str, Any], name: str) -> bool:
    """Whether the accuracy fix `name` (a SWITCHES key) is on for this run."""
    return bool(opts.get(name, SWITCHES[name]))


class Models(Protocol):
    """The seam between identify and the models: the three model adapters (detector, crop gate,
    species encoder) and the data they need (name lists and location priors per kind). Engine
    implements it; unit tests pass small stand-ins, the contract tests an Engine of fake adapters."""

    owlv2: Any                      # .detect(image, prompts, threshold=) -> list[Detection]
    siglip2: Any                    # .embed_images(images) -> vecs; .gate(vecs) -> [{class: p}]
    bioclip: Any                    # .encode_images(crops) -> feats; .probs(feats, NameList.matrix) -> (n, N)
    names: dict[str, Any]           # kind -> names.NameList
    priors: dict[str, Any]          # kind -> geo.LocationPrior (absent = no prior for that kind)


@dataclass(frozen=True)
class Frame:
    """One image to identify: the 2048 px frame, its whole-frame gate, where and when it was taken,
    and the optional larger copy species crops are cut from."""
    image: Image.Image
    gate: dict[str, float]
    lat: float | None = None
    lon: float | None = None
    taken_at: str | None = None
    detail: Image.Image | None = None


def _batches(seq: list[Any], size: int) -> list[list[Any]]:
    return [seq[i:i + size] for i in range(0, len(seq), size)] if seq else []


def _detect_many(engine: Models, images: list[Image.Image], vocabs: list[dict[str, float]],
                 second_pass: bool) -> list[list[Detection]]:
    """OWLv2 over many frames, one batched call per vocabulary (frames of one gate class share one)."""
    out: list[list[Detection]] = [[] for _ in images]
    groups: dict[int, list[int]] = defaultdict(list)
    for i, vocab in enumerate(vocabs):
        groups[id(vocab)].append(i)
    for idx in groups.values():
        vocab = vocabs[idx[0]]
        floor = SECOND_PASS_FLOOR if second_pass else min(vocab.values())
        batch = getattr(engine.owlv2, "detect_batch", None)
        found = (batch([images[i] for i in idx], list(vocab), threshold=floor) if batch is not None
                 else [engine.owlv2.detect(images[i], list(vocab), threshold=floor) for i in idx])
        for i, dets in zip(idx, found):
            out[i] = dets if second_pass else [d for d in dets if d.confidence >= vocab[d.prompt]]
    return out


def _judged_many(engine: Models, work: list[tuple[Image.Image, list[Detection], str]]
                 ) -> list[list[tuple[Detection, str]]]:
    """The crop gate over every box of every frame, in CROP_BATCH-sized SigLIP2 calls."""
    crops = [(fi, d, crop_with_context(image, d.bbox)) for fi, (image, dets, _k) in enumerate(work) for d in dets]
    gates: list[dict[str, float]] = []
    for part in _batches([c for _fi, _d, c in crops], CROP_BATCH):
        gates += engine.siglip2.gate(engine.siglip2.embed_images(part))
    out: list[list[tuple[Detection, str]]] = [[] for _ in work]
    for (fi, d, _c), g in zip(crops, gates):
        if (k := judge(work[fi][2], g)) is not None:
            out[fi].append((d, k))
    return out


def _prior(engine: Models, kind: str, opts: dict[str, Any]) -> Any:
    """The location prior identify uses for `kind` under these options, or None."""
    flag = PRIOR_SWITCH.get(kind)
    if not opts["geo"] or (flag is not None and not switch(opts, flag)):
        return None
    return engine.priors.get(kind)


def _union(lists: dict[str, Any], kinds: list[str]) -> tuple[np.ndarray, dict[str, slice]]:
    """The kind-check lists' matrices stacked, and each list's rows in it. Built once per set of
    lists and kept (with the lists' own matrices, so their ids stay unique) for BioCLIP's device copy."""
    key = tuple(id(lists[k].matrix) for k in kinds)
    if key not in _UNIONS:
        while len(_UNIONS) >= 4:                      # one per engine in the service; tests make many
            _UNIONS.pop(next(iter(_UNIONS)))
        mats = [lists[k].matrix for k in kinds]
        ends = [int(e) for e in np.cumsum([len(m) for m in mats])]
        _UNIONS[key] = (mats, np.concatenate(mats), {k: slice(e - len(m), e) for k, m, e in zip(kinds, mats, ends)})
    return _UNIONS[key][1], _UNIONS[key][2]


_UNIONS: dict[tuple[int, ...], tuple[list[np.ndarray], np.ndarray, dict[str, slice]]] = {}


def _named(names: Any, prior: Any, row: np.ndarray, p_geo: np.ndarray | None, opts: dict[str, Any],
           kind_unsure: bool) -> dict[str, Any]:
    """One box's species from its visual scores over one list: posterior, top-k, range veto, level."""
    post = prior.posterior(row, p_geo) if prior is not None else row
    order = np.argsort(-post, kind="stable")[:opts["top_k"]]
    top = [
        contract.candidate(names.scientific[j], names.common[j] or None, list(names.taxonomy[j]),
                           round(float(row[j]), 6), None if p_geo is None else round(float(p_geo[j]), 6),
                           round(float(post[j]), 6))
        for j in order]
    vetoed = False
    if switch(opts, "range_veto") and prior is not None:
        direct = getattr(prior, "direct", None)          # a stand-in prior without it: all direct
        top, vetoed = range_veto(top, None if direct is None else [bool(direct[j]) for j in order])
    level = "unconfirmed" if kind_unsure else species_level(top, species_ok=not vetoed)
    return contract.species(names.list_id, level, top)


def _species_many(engine: Models, work: list[tuple[Frame, list[dict[str, Any]], list[tuple[float, ...]]]],
                  opts: dict[str, Any]) -> None:
    """Fills every box's "species" in place: one BioCLIP pass per name list over all frames' boxes
    (SPECIES_BATCH at a time), each frame's own location prior.

    Kind check (switch "kind_check"): a box of a KIND_CHECK kind is also scored against all those
    lists stacked into one (one extra matmul on the features already computed) and takes the kind
    whose best KIND_TOP rows hold most of that visual probability (rules.kind_evidence: list size
    does not count); a box that moves on less than KIND_SURE of it is graded unconfirmed. Visual
    evidence decides, not the posterior: the lists differ in prior coverage and unlabelled policy,
    so their posteriors do not compare across lists."""
    by_kind: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for fi, (_f, boxes, _b) in enumerate(work):
        for bi, b in enumerate(boxes):
            b["species"] = None
            if b["kind"] in engine.names:
                by_kind[b["kind"]].append((fi, bi))
    rivals = [k for k in KIND_CHECK if k in engine.names] if switch(opts, "kind_check") else []
    p_geo: dict[tuple[str, int], np.ndarray | None] = {}

    def geo_at(kind: str, fi: int) -> np.ndarray | None:
        if (kind, fi) not in p_geo:
            f, prior = work[fi][0], _prior(engine, kind, opts)
            p_geo[kind, fi] = prior.p_geo(f.lat, f.lon, f.taken_at) if prior is not None else None
        return p_geo[kind, fi]

    for kind, refs in by_kind.items():
        for part in _batches(refs, SPECIES_BATCH):
            crops = [species_crops(work[fi][0].image, [work[fi][2][bi]], work[fi][0].detail)[0] for fi, bi in part]
            feats = engine.bioclip.encode_images(crops)
            probs = {kind: engine.bioclip.probs(feats, engine.names[kind].matrix)}
            final = [(kind, True)] * len(part)
            if kind in rivals and len(rivals) > 1:
                union, rows_of = _union(engine.names, rivals)
                joint = np.asarray(engine.bioclip.probs(feats, union), dtype=np.float64)
                final = [kind_of(kind_evidence(j, rows_of)) for j in joint]
                for other in dict.fromkeys(k for k, _sure in final if k != kind):
                    probs[other] = engine.bioclip.probs(feats, engine.names[other].matrix)
            for n, ((fi, bi), (k, sure)) in enumerate(zip(part, final)):
                box = work[fi][1][bi]
                box["kind"] = k
                box["species"] = _named(engine.names[k], _prior(engine, k, opts),
                                        np.asarray(probs[k][n], dtype=np.float64), geo_at(k, fi), opts,
                                        kind_unsure=k != kind and not sure)


def _species(engine: Models, image: Image.Image, boxes: list[dict[str, Any]], bboxes: list[tuple[float, ...]],
             lat: float | None, lon: float | None, taken_at: str | None, opts: dict[str, Any],
             detail: Image.Image | None = None) -> None:
    """One frame's species (the batched path with a batch of one)."""
    _species_many(engine, [(Frame(image, {}, lat, lon, taken_at, detail), boxes, bboxes)], opts)


def _identify_batch(engine: Models, frames: list[Frame], opts: dict[str, Any]) -> list[dict[str, Any]]:
    outs: list[dict[str, Any]] = []
    plan: dict[int, tuple[dict[str, float], str, bool]] = {}      # frame -> (vocab, kind, rescued)
    for i, f in enumerate(frames):
        cls = max(f.gate, key=lambda k: f.gate[k])
        outs.append(contract.identify(contract.gate(cls, {k: round(v, 4) for k, v in f.gate.items()}), []))
        rescue = cls not in VOCAB
        if rescue:
            # A bear at night or a bobcat in brush can lose the whole-frame vote to "none" while the
            # animal classes together still hold real mass: look with the strongest animal's words.
            # The reported gate class stays what the gate said.
            if sum(f.gate.get(k, 0.0) for k in VOCAB) < RESCUE:
                continue
            cls = max(VOCAB, key=lambda k: f.gate.get(k, 0.0))
        plan[i] = (VOCAB[cls], cls, rescue)
    active = list(plan)
    first = _detect_many(engine, [frames[i].image for i in active], [plan[i][0] for i in active], False)
    kept = dict(zip(active, _judged_many(engine, [(frames[i].image, dedupe(d), plan[i][1])
                                                  for i, d in zip(active, first)])))
    # The gate says an animal is there and the detector boxed none: floor 0.1, best three, the crop
    # gate still decides. Not for rescued frames, where the gate itself was unsure.
    again = [i for i in active if not kept[i] and not plan[i][2]]
    second = _detect_many(engine, [frames[i].image for i in again], [plan[i][0] for i in again], True)
    kept.update(zip(again, _judged_many(engine, [(frames[i].image, dedupe(d)[:SECOND_PASS_TOP], plan[i][1])
                                                 for i, d in zip(again, second)])))
    species_work = []
    for i in active:
        k = sorted(kept[i], key=lambda dk: -dk[0].confidence)
        image = frames[i].image
        w, h = image.size
        boxes = []
        for n, (d, kind) in enumerate(k):
            x0, y0, x1, y1 = d.bbox
            boxes.append(contract.box(n, [round(x0 / w, 5), round(y0 / h, 5), round(x1 / w, 5), round(y1 / h, 5)],
                                      round(d.confidence, 4), kind, quality(image, d.bbox)))
        outs[i]["boxes"] = boxes
        species_work.append((frames[i], boxes, [d.bbox for d, _ in k]))
    if opts["species"]:
        _species_many(engine, species_work, opts)
    return outs


def identify_many(engine: Models, frames: list[Frame], opts: dict[str, Any]) -> list[dict[str, Any] | Exception]:
    """identify over a chunk, every model stage batched across frames. If a batched stage throws,
    the frames are retried one by one, so a bad image costs only itself."""
    try:
        return list(_identify_batch(engine, frames, opts))
    except Exception as exc:  # noqa: BLE001
        if len(frames) == 1:
            return [exc]
    out: list[dict[str, Any] | Exception] = []
    for f in frames:
        try:
            out.append(_identify_batch(engine, [f], opts)[0])
        except Exception as exc:  # noqa: BLE001
            out.append(exc)
    return out


def identify(engine: Models, image: Image.Image, gate: dict[str, float], lat: float | None, lon: float | None,
             taken_at: str | None, opts: dict[str, Any], detail: Image.Image | None = None) -> dict[str, Any]:
    """One frame: gate, boxes and quality on the 2048 px `image`; species crops from `detail`."""
    result = identify_many(engine, [Frame(image, gate, lat, lon, taken_at, detail)], opts)[0]
    if isinstance(result, Exception):
        raise result
    return result
