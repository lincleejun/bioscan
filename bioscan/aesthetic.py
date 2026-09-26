"""Aesthetic heads on the SigLIP2 frame vector, the owner's ratings, and the ranking metrics.

An aesthetic head is a linear map from the whole-frame SigLIP2 vector (what `embed` returns) to a
score: `bias + weights . (vec - mean) / std`, then scaled to 0-1 by the head's rating range
(`target.lo` -> 0, `target.hi` -> 1; not clipped, so a ranking keeps its order at the ends). The
general head is trained on EVA (CC0 annotations, data/aesthetic/README.md); a personal head on the
owner's own ratings (Lightroom stars from XMP, or a CSV). Aesthetics only reorders frames: nothing
here deletes or rejects one.

A head file is JSON (format `bioscan-aesthetic-head`, version 1): weights, bias, mean, std, target,
embedding (model@revision the vectors came from), provenance (data, licence, n, date, seed, CV
numbers) and `sha`, the sha256 of the canonical JSON of everything else, checked on load.

This module is standard library only (the CLI imports it). Fitting is numpy: bioscan/aesthetic_fit.py,
imported only by `bioscan aesthetic train|eval` and scripts/train_aesthetic_head.py.
"""
from __future__ import annotations

import base64
import csv
import hashlib
import json
import math
import os
import random
import re
import struct
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bioscan import formats, naming, xmp

HEAD_FORMAT, HEAD_VERSION = "bioscan-aesthetic-head", 1
# The vectors every head is fitted on: bioscan/service/adapters/siglip2.py MODEL_ID@REVISION[:12]
# (a unit test holds the two together; the adapter imports numpy, so it is not imported here).
EMBEDDING = "google/siglip2-base-patch16-224@75de2d55ec2d"
DIM = 768
AESTHETIC_DIR = naming.DATA_DIR / "aesthetic"
BUILTIN_HEAD = AESTHETIC_DIR / "eva-head-v1.json"           # the committed general head
EVA_HOLDOUT = AESTHETIC_DIR / "eva-golden-v1.csv"           # EVA images never fitted on: the public golden set
PERSONAL_HEAD = Path("~/.config/bioscan/aesthetic-personal.json")   # `bioscan aesthetic train` default output
DIGITS = 7                                                  # significant digits kept in a head file
EMBED_SCAN_BYTES = 16 * 2**20                               # embedded XMP is looked for in this much of a file
PICK_MIN = 4                                                # without explicit picks: rating >= this is a pick
Z95 = 1.959963984540054


class HeadError(ValueError):
    """A head file that cannot be used (unreadable, wrong format, wrong embedding, sha mismatch)."""


# ---- head files ------------------------------------------------------------------------------

def _round(x: float) -> float:
    return float(f"{x:.{DIGITS}g}")


def canonical_sha(doc: dict[str, Any]) -> str:
    """sha256 hex of the head document without its `sha` (sorted keys, compact separators)."""
    body = {k: v for k, v in doc.items() if k != "sha"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def make_head(name: str, weights: Sequence[float], bias: float, mean: Sequence[float], std: Sequence[float],
              lo: float, hi: float, target: str, provenance: dict[str, Any],
              embedding: str = EMBEDDING) -> dict[str, Any]:
    """A head document (floats rounded to DIGITS significant digits, then its sha)."""
    doc = {"format": HEAD_FORMAT, "version": HEAD_VERSION, "name": name, "embedding": embedding,
           "dim": len(weights), "weights": [_round(w) for w in weights], "bias": _round(bias),
           "mean": [_round(m) for m in mean], "std": [_round(s) for s in std],
           "target": {"lo": lo, "hi": hi, "what": target}, "provenance": provenance}
    doc["sha"] = canonical_sha(doc)
    return doc


def write_head(doc: dict[str, Any], path: str | Path) -> Path:
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return p


def _finite_list(v: Any, n: int, what: str) -> list[float]:
    if not isinstance(v, list) or len(v) != n or not all(
            isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in v):
        raise HeadError(f"{what} must be {n} finite numbers")
    return [float(x) for x in v]


@dataclass(frozen=True)
class Head:
    """A loaded, validated head. `predict` gives the 0-1 score of one frame vector."""
    name: str
    sha: str
    embedding: str
    weights: tuple[float, ...]
    bias: float
    mean: tuple[float, ...]
    std: tuple[float, ...]
    lo: float
    hi: float
    provenance: dict[str, Any] = field(default_factory=dict)
    path: str = ""

    @property
    def id(self) -> str:
        """`name:sha12`, what results and engine.plugins report."""
        return f"{self.name}:{self.sha[:12]}"

    def folded(self) -> tuple[list[float], float]:
        """(a, c) with raw(x) = a . x + c: the standardisation folded into the weights."""
        a = [w / s for w, s in zip(self.weights, self.std)]
        return a, self.bias - sum(ai * m for ai, m in zip(a, self.mean))

    def raw(self, vec: Sequence[float]) -> float:
        """The score in the head's rating units (EVA: 0-10, stars: 0-5)."""
        return self.bias + sum(w * (x - m) / s for w, x, m, s in zip(self.weights, vec, self.mean, self.std))

    def scaled(self, raw: float) -> float:
        return (raw - self.lo) / (self.hi - self.lo)

    def predict(self, vec: Sequence[float]) -> float:
        return self.scaled(self.raw(vec))


def parse_head(doc: Any, where: str = "head") -> Head:
    """Validate a head document; HeadError names what is wrong."""
    def fail(msg: str) -> HeadError:
        return HeadError(f"{where}: {msg}")

    if not isinstance(doc, dict) or doc.get("format") != HEAD_FORMAT:
        raise fail(f"not a {HEAD_FORMAT} file")
    if doc.get("version") != HEAD_VERSION:
        raise fail(f"head version {doc.get('version')!r}, this bioscan reads {HEAD_VERSION}")
    for k in ("name", "embedding", "dim", "weights", "bias", "mean", "std", "target", "provenance", "sha"):
        if k not in doc:
            raise fail(f"missing {k}")
    if doc["embedding"] != EMBEDDING:
        raise fail(f"fitted on {doc['embedding']!r}, but bioscan's frame vectors are {EMBEDDING!r}")
    dim = doc["dim"]
    if dim != DIM:
        raise fail(f"dim {dim!r}, the frame vector has {DIM}")
    try:
        w = _finite_list(doc["weights"], dim, "weights")
        m = _finite_list(doc["mean"], dim, "mean")
        s = _finite_list(doc["std"], dim, "std")
        (b,) = _finite_list([doc["bias"]], 1, "bias")
    except HeadError as e:
        raise fail(str(e)) from None
    if min(s) <= 0:
        raise fail("std must be > 0")
    t = doc["target"]
    if not isinstance(t, dict) or not all(isinstance(t.get(k), (int, float)) for k in ("lo", "hi")) \
            or not t["hi"] > t["lo"]:
        raise fail("target needs numbers lo < hi")
    if not isinstance(doc["name"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", doc["name"]):
        raise fail("name: letters, digits, '.', '_' and '-' only")
    if not isinstance(doc["provenance"], dict):
        raise fail("provenance must be an object")
    sha = canonical_sha(doc)
    if doc["sha"] != sha:
        raise fail(f"sha mismatch (file says {str(doc['sha'])[:12]}, content is {sha[:12]}): edited or damaged")
    return Head(doc["name"], sha, doc["embedding"], tuple(w), b, tuple(m), tuple(s), float(t["lo"]),
                float(t["hi"]), doc["provenance"], str(where))


def load_head(path: str | Path) -> Head:
    """Read and validate a head file; HeadError when missing, unreadable or invalid."""
    p = Path(path).expanduser()
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise HeadError(f"{p}: no such head file") from None
    except (OSError, ValueError) as e:
        raise HeadError(f"{p}: cannot read head ({e})") from None
    return parse_head(doc, str(p))


def blend(general: float | None, personal: float | None, weight: float) -> float | None:
    """The score: (1 - weight) * general + weight * personal when both exist, else the one there is."""
    if general is None:
        return personal
    if personal is None:
        return general
    return (1 - weight) * general + weight * personal


def head_id(general: Head | None, personal: Head | None, weight: float) -> str | None:
    """`eva-head-v1:sha12`, `name:sha12+name:sha12~0.5` (blended, personal weight), or None."""
    ids = [h.id for h in (general, personal) if h is not None]
    if not ids:
        return None
    return "+".join(ids) + (f"~{weight:g}" if len(ids) == 2 else "")


# ---- vectors ---------------------------------------------------------------------------------

def f16_decode(text: str) -> list[float]:
    """`embed`'s f16_base64 vector (little-endian float16) -> floats."""
    raw = base64.b64decode(text)
    return list(struct.unpack(f"<{len(raw) // 2}e", raw))


def f16_encode(vec: Sequence[float]) -> str:
    return base64.b64encode(struct.pack(f"<{len(vec)}e", *vec)).decode("ascii")


def vector_of(embed: dict[str, Any]) -> list[float]:
    """result.products.embed -> the vector, whichever format it was sent in."""
    v = embed["vector"]
    return f16_decode(v) if isinstance(v, str) else [float(x) for x in v]


# ---- the owner's ratings ---------------------------------------------------------------------

@dataclass
class Rating:
    path: str                   # absolute image path
    rating: float               # stars 1-5, or REJECT_GRADE (0) for a reject (then pick is -1): see stars_of
    pick: int = 0               # 1 picked, -1 rejected, 0 unknown
    label: str = ""             # Lightroom colour label ("Red", ...), "" when none
    trip: str = "."             # the split group: first folder under the ratings root, or the CSV's trip
    source: str = ""            # sidecar | embedded | csv


REJECT_GRADE = 0.0      # a rejected frame's grade: below 1 star; unrated frames never get it (they are skipped)


def stars_of(rating: float | None, pick: int) -> tuple[float, int] | None:
    """The rating rule for one frame, XMP and CSV alike: (grade, pick), or None = unrated.

    xmp:Rating 1-5 = stars. 0 or missing = unrated (the XMP spec): skipped, unless the frame is
    rejected. -1 (the Lightroom/Bridge reject), or a reject pick flag (-1) without stars, = a
    reject: grade REJECT_GRADE, pick -1, kept apart from unrated. A pick flag (1) without stars
    gives no grade, so that frame is skipped too."""
    if (rating is not None and rating < 0) or (not rating and pick == -1):
        return REJECT_GRADE, -1
    if rating is None or rating == 0:
        return None
    return float(rating), pick


NS = {"x": "adobe:ns:meta/", "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
      "xmp": "http://ns.adobe.com/xap/1.0/", "xmpDM": "http://ns.adobe.com/xmp/1.0/DynamicMedia/",
      "bioscan": xmp.CULL_NS}


def _xmp_value(root: ET.Element, ns: str, name: str) -> str | None:
    """A simple property, written as an attribute of rdf:Description or as its child element."""
    key = f"{{{NS[ns]}}}{name}"
    for desc in root.iter(f"{{{NS['rdf']}}}Description"):
        if key in desc.attrib:
            return desc.attrib[key].strip()
        el = desc.find(key)
        if el is not None and (el.text or "").strip():
            return el.text.strip()
    return None


def parse_xmp(text: str | bytes) -> dict[str, Any] | None:
    """{rating, pick, label} from one XMP packet, graded by `stars_of`: rating None when the frame
    is unrated (xmp:Rating 0 or missing, not rejected); None when the packet is not XML.
    xmp:Rating -1 (Lightroom/Bridge reject) -> rating REJECT_GRADE, pick -1.
    xmpDM:pick (1 / -1) is read where a tool writes it; Lightroom Classic keeps its pick flags
    in the catalogue, so they only arrive through a CSV.
    A packet `bioscan cull --xmp` wrote (its namespace) is not the owner's while its xmp:Rating
    (missing = 0) still equals the `bioscan:stars` cull recorded: None, like no packet. Re-rated,
    it is parsed like any other; one without `bioscan:stars` (an older cull) is always None."""
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    start = text.find("<x:xmpmeta")
    if start < 0:
        start = text.find("<rdf:RDF")
    end_tag = "</x:xmpmeta>" if text.startswith("<x:xmpmeta", start) else "</rdf:RDF>"
    end = text.find(end_tag, start)
    if start < 0 or end < 0:
        return None
    try:
        root = ET.fromstring(text[start:end + len(end_tag)])
    except ET.ParseError:
        return None
    rating = _xmp_value(root, "xmp", "Rating")
    label = _xmp_value(root, "xmp", "Label") or ""
    pick = _xmp_value(root, "xmpDM", "pick")
    try:
        r = float(rating) if rating is not None else None
    except ValueError:
        r = None
    if xmp.CULL_NS in text:
        culled = _xmp_value(root, "bioscan", "stars")
        try:
            if culled is None or float(culled) == (r or 0):
                return None
        except ValueError:
            return None
    p = 0
    if pick is not None:
        try:
            p = max(-1, min(1, int(float(pick))))
        except ValueError:
            p = 0
    graded = stars_of(r, p)
    if graded is None:
        return {"rating": None, "pick": p, "label": label}
    return {"rating": graded[0], "pick": graded[1], "label": label}


def sidecar_of(path: str) -> Path | None:
    """`IMG.xmp` (Lightroom, Capture One) or `IMG.ARW.xmp` (darktable), whichever exists."""
    p = Path(path)
    for c in (p.with_suffix(".xmp"), p.with_suffix(".XMP"), Path(path + ".xmp")):
        if c.is_file():
            return c
    return None


def embedded_xmp(path: str, limit: int = EMBED_SCAN_BYTES) -> bytes | None:
    """The first XMP packet in the first `limit` bytes of an image file (JPEG APP1, DNG/TIFF tag 700)."""
    with open(path, "rb") as f:
        data = f.read(limit)
    start = data.find(b"<x:xmpmeta")
    end = data.find(b"</x:xmpmeta>", start)
    return data[start:end + len(b"</x:xmpmeta>")] if start >= 0 and end >= 0 else None


def read_xmp_rating(path: str) -> tuple[dict[str, Any], str] | None:
    """(fields, source) for one image: the sidecar wins over embedded XMP (Lightroom writes
    sidecars for RAW files and embeds in JPEG/DNG). None when neither holds a rating."""
    side = sidecar_of(path)
    if side is not None:
        got = parse_xmp(side.read_bytes())
        if got is not None and got["rating"] is not None:
            return got, "sidecar"
    packet = embedded_xmp(path)
    if packet is not None:
        got = parse_xmp(packet)
        if got is not None and got["rating"] is not None:
            return got, "embedded"
    return None


def trip_of(path: str, root: str) -> str:
    """The split group of an image: its first folder under `root` ("." for files directly in it)."""
    rel = Path(os.path.relpath(path, root))
    return rel.parts[0] if len(rel.parts) > 1 else "."


def ratings_from_folder(root: str, exts: Iterable[str] = formats.SCAN_EXT) -> list[Rating]:
    """Every image under `root` (recursive) whose XMP rates it (1-5 stars, or a reject); unrated
    images (xmp:Rating 0 or missing) are skipped."""
    out = []
    for p in formats.list_images(root, set(exts), recursive=True):
        got = read_xmp_rating(p)
        if got is None:
            continue
        fields, source = got
        out.append(Rating(p, fields["rating"], fields["pick"], fields["label"], trip_of(p, os.path.abspath(root)),
                          source))
    return out


def ratings_from_csv(path: str) -> list[Rating]:
    """CSV with `path,rating` and optional `pick` (1/0/-1), `label`, `trip` columns, graded by the
    same rule as XMP (`stars_of`): 1-5 stars; 0 or blank = unrated, skipped unless pick is -1;
    -1 = reject. Relative paths are taken from the CSV's folder; without `trip`, the trip is the
    image's parent folder name."""
    base = Path(path).resolve().parent
    out = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or not {"path", "rating"} <= set(reader.fieldnames):
            raise ValueError(f"{path}: needs columns path,rating (got {reader.fieldnames})")
        for i, r in enumerate(reader, start=2):
            try:
                stars = float(r["rating"]) if (r.get("rating") or "").strip() else None
                flag = max(-1, min(1, int(float(r.get("pick") or 0))))
            except ValueError:
                raise ValueError(f"{path}:{i}: rating and pick must be numbers") from None
            graded = stars_of(stars, flag)
            if graded is None:
                continue
            rating, pick = graded
            p = Path(r["path"]).expanduser()
            p = p if p.is_absolute() else base / p
            out.append(Rating(str(p), rating, pick, (r.get("label") or "").strip(),
                              (r.get("trip") or "").strip() or p.parent.name, "csv"))
    return out


def read_ratings(source: str) -> list[Rating]:
    """A ratings CSV, or a folder of rated images (XMP sidecars or embedded)."""
    return ratings_from_csv(source) if Path(source).is_file() else ratings_from_folder(source)


def picks_of(rows: Sequence[Rating], pick_min: float = PICK_MIN) -> list[bool]:
    """Explicit picks when any row has one (pick == 1), else rating >= pick_min."""
    if any(r.pick == 1 for r in rows):
        return [r.pick == 1 for r in rows]
    return [r.rating >= pick_min for r in rows]


def ratings_sha(rows: Iterable[Rating]) -> str:
    """sha256 of the sorted (path, rating) pairs: tells whether a head was fitted on these ratings."""
    body = "\n".join(f"{r.path}\t{r.rating:g}" for r in sorted(rows, key=lambda r: r.path))
    return hashlib.sha256(body.encode()).hexdigest()


# ---- EVA (the general head's training data) ----------------------------------------------------

EVA_REPO = "kang-gnak/eva-dataset"
EVA_COMMIT = "fb40a9f1abe4be96b69229aaac3d0838a2e1d31c"     # master on 2026-09-24 (last change 2022-10-07)
EVA_RAW = f"https://raw.githubusercontent.com/{EVA_REPO}/{EVA_COMMIT}"
EVA_PARTS = tuple(f"images/EVA_together.zip.{i:03d}" for i in range(1, 8))   # one zip, cut in 7 (~695 MB)
EVA_VOTES = "data/votes_filtered.csv"
EVA_IMAGES = "images/EVA_together"                          # <id>.jpg after unzipping
EVA_PROVENANCE = {
    "data": f"EVA, github.com/{EVA_REPO}@{EVA_COMMIT[:12]}: {EVA_VOTES}, mean general score (0-10) per image "
            "over its filtered votes (30+ each); images are AVA photos resized by the EVA authors",
    "licence": "annotations CC0 1.0 (the repository's LICENSE); the images are AVA/dpchallenge.com photos whose "
               "copyright stays with their photographers: used here to compute vectors, never redistributed; "
               "no AVA score or AVA-trained weight is used",
    "citation": "Kang, Valenzise, Dufaux, 'EVA: An Explainable Visual Aesthetics Dataset', ATQAM/MAST'20",
}


def eva_holdout(path: str | Path = EVA_HOLDOUT) -> set[str]:
    """Image ids of the public golden set (scripts/eva_golden.py); empty when the file is absent."""
    try:
        with open(path, newline="", encoding="utf-8") as f:
            return {r["image_id"].strip() for r in csv.DictReader(f)}
    except FileNotFoundError:
        return set()


def eva_holdout_provenance(path: str | Path = EVA_HOLDOUT) -> dict[str, Any] | None:
    """What a general head was not fitted on: the held-out list, its size and sha256 (None when absent)."""
    p = Path(path)
    if not p.is_file():
        return None
    return {"file": f"data/aesthetic/{p.name}", "images": len(eva_holdout(p)),
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}


def read_eva(root: str | Path, exclude: set[str] | None = None) -> list[tuple[str, float, int]]:
    """(image path, mean score 0-10, votes) per EVA image with filtered votes, sorted by image id.
    `root` is an EVA checkout: data/votes_filtered.csv ('='-delimited) and the unzipped images.
    Images in `exclude` (default: the public golden set, `eva_holdout()`) are left out, so no
    general head is ever fitted on the images it is tested on."""
    root = Path(root)
    exclude = eva_holdout() if exclude is None else exclude
    votes: dict[str, list[float]] = {}
    with open(root / EVA_VOTES, newline="", encoding="utf-8", errors="replace") as f:
        for r in csv.DictReader(f, delimiter="="):
            try:
                votes.setdefault(r["image_id"].strip(), []).append(float(r["score"]))
            except (KeyError, TypeError, ValueError):
                continue
    out = []
    for iid in sorted(votes, key=lambda s: (len(s), s)):
        if iid in exclude:
            continue
        p = root / EVA_IMAGES / f"{iid}.jpg"
        if p.is_file():
            out.append((str(p.resolve()), sum(votes[iid]) / len(votes[iid]), len(votes[iid])))
    return out


# ---- splits ----------------------------------------------------------------------------------

def group_folds(groups: Sequence[str], k: int, seed: int) -> list[int]:
    """A fold index per row such that every group (trip) sits in exactly one fold, never split:
    groups are shuffled with `seed`, then placed largest first into the fold with fewest rows.
    k is capped at the number of groups."""
    sizes: dict[str, int] = {}
    for g in groups:
        sizes[g] = sizes.get(g, 0) + 1
    names = sorted(sizes)
    random.Random(seed).shuffle(names)
    names.sort(key=lambda g: -sizes[g])                 # stable: ties keep the shuffled order
    k = max(1, min(k, len(names)))
    load, fold_of = [0] * k, {}
    for g in names:
        f = min(range(k), key=lambda i: (load[i], i))
        fold_of[g] = f
        load[f] += sizes[g]
    return [fold_of[g] for g in groups]


# ---- ranking metrics (standard library) --------------------------------------------------------

def ranks(xs: Sequence[float]) -> list[float]:
    """1-based ranks, ties get their average rank."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    out = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for t in range(i, j + 1):
            out[order[t]] = (i + j) / 2 + 1
        i = j + 1
    return out


def pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    n = len(xs)
    if n < 2:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    return sxy / math.sqrt(sxx * syy)


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    return pearson(ranks(xs), ranks(ys))


def kendall(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Kendall's tau-b (ties in either variable corrected); O(n^2)."""
    n = len(xs)
    conc = disc = tx = ty = 0
    for i in range(n):
        for j in range(i + 1, n):
            dx, dy = xs[i] - xs[j], ys[i] - ys[j]
            if dx == 0 and dy == 0:
                continue
            if dx == 0:
                tx += 1
            elif dy == 0:
                ty += 1
            elif (dx > 0) == (dy > 0):
                conc += 1
            else:
                disc += 1
    den = math.sqrt((conc + disc + tx) * (conc + disc + ty))
    return (conc - disc) / den if den else None


def rank_ci(r: float | None, n: int, z: float = Z95) -> list[float] | None:
    """95% interval of a Spearman correlation: Fisher z with the Fieller-Hartley-Pearson SE 1.06/sqrt(n-3)."""
    if r is None or n < 4:
        return None
    r = max(-0.999999, min(0.999999, r))
    half = z * 1.06 / math.sqrt(n - 3)
    zr = math.atanh(r)
    return [round(math.tanh(zr - half), 6), round(math.tanh(zr + half), 6)]


def _order(scores: Sequence[float], keys: Sequence[str] | None) -> list[int]:
    """Indices by score, highest first; ties by key (path) so the order is deterministic."""
    keys = keys or [f"{i:09d}" for i in range(len(scores))]
    return sorted(range(len(scores)), key=lambda i: (-scores[i], keys[i]))


def ndcg_at_k(scores: Sequence[float], ratings: Sequence[float], k: int, keys: Sequence[str] | None = None) -> float | None:
    """NDCG@k with gain 2^rating - 1 (ratings below 0 count as 0)."""
    if not scores:
        return None
    gain = [2 ** max(0.0, r) - 1 for r in ratings]
    k = min(k, len(scores))
    dcg = sum(gain[i] / math.log2(pos + 2) for pos, i in enumerate(_order(scores, keys)[:k]))
    ideal = sum(g / math.log2(pos + 2) for pos, g in enumerate(sorted(gain, reverse=True)[:k]))
    return dcg / ideal if ideal > 0 else None


def hits_at_k(scores: Sequence[float], picked: Sequence[bool], k: int, keys: Sequence[str] | None = None) -> int:
    """How many of the top-k frames by score the owner picked."""
    return sum(bool(picked[i]) for i in _order(scores, keys)[:k])


# ---- a scored set ----------------------------------------------------------------------------

def rows_metrics(scores: Sequence[float], rows: Sequence[Rating], picked: Sequence[bool], k: int) -> dict[str, Any]:
    """Correlation and top-k metrics of `scores` against `rows` (one set: a trip, or all). Picks
    are judged per trip in `score_set` (precision@k: k = that trip's number of picks)."""
    ys = [r.rating for r in rows]
    keys = [r.path for r in rows]
    rho = spearman(scores, ys)
    return {"n": len(rows), "spearman": None if rho is None else round(rho, 6), "spearman_ci": rank_ci(rho, len(rows)),
            "kendall": None if (t := kendall(scores, ys)) is None else round(t, 6),
            "plcc": None if (c := pearson(scores, ys)) is None else round(c, 6),
            "ndcg_at_k": None if (g := ndcg_at_k(scores, ys, k, keys)) is None else round(g, 6),
            "picks": sum(map(bool, picked))}
