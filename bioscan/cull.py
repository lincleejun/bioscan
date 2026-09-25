"""Culling reducers: burst and select, over one run's result events (a /run stream or a preds file).

A reducer is model-free and sees the whole run, so bursts can cross chunks; the service never runs
one (it stays stateless). `bioscan cull` runs them after the album profile's stages, `bioscan bench`
before scoring, and anyone can run them offline over a saved NDJSON. `apply` adds each reducer's
per-image output under `products[<reducer>]` of a copy of every result event; `records` flattens
them into one cull record per image.

burst: frames of one camera (EXIF Make/Model; frames without one count as one camera) whose capture
times are at most `max_gap_s` apart and whose SigLIP2 frame vectors (products.embed) have cosine at
least `min_cosine` are chained into a burst, in time order.

select: per burst, the best frame by these criteria in order: not rejected, subject sharpness
(within `sharp_tie` of the burst's sharpest counts as equal), not cut off, exposure within
`exposure_ok`, then the aesthetic score (products.aesthetics.score) when the run has one. Then per
scene category (products.scene.label), the best frames are ranked (aesthetic when present, then
sharpness) and the top `per_category` are picked, skipping one whose frame vector has cosine at least
`dup_cosine` with a frame already picked. Aesthetics only reorders: it never rejects.

Statuses: pick (selected), spare (a keeper beyond the top per_category), duplicate (in a burst but
not its best, or a near-duplicate of a pick), reject (a quality reject reason; `waive` lifts reasons
per category: underexposed is normal at night).

Standard library only: the CLI imports it."""
from __future__ import annotations

import base64
import copy
import math
import struct
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

ONE_CAMERA = "(one camera)"
UNCATEGORISED = "uncategorised"
STATUSES = ("pick", "spare", "duplicate", "reject")


# ---- reading result events ------------------------------------------------------------------

def products(ev: dict[str, Any]) -> dict[str, Any]:
    return ev.get("products") or {} if ev.get("type") == "result" else {}


def vector(ev: dict[str, Any]) -> list[float] | None:
    """The frame vector (products.embed), unit length; None without one."""
    e = products(ev).get("embed") or {}
    v = e.get("vector")
    if isinstance(v, str):
        raw = base64.b64decode(v)
        v = list(struct.unpack(f"<{len(raw) // 2}e", raw))
    if not v:
        return None
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def cosine(a: list[float] | None, b: list[float] | None) -> float | None:
    if a is None or b is None or len(a) != len(b):
        return None
    return sum(x * y for x, y in zip(a, b))


def seconds(taken_at: str | None) -> float | None:
    """ISO 8601 capture time -> seconds; a time without an offset is read as UTC (frames of one
    camera share their zone, so only differences matter)."""
    if not taken_at:
        return None
    try:
        t = datetime.fromisoformat(taken_at)
    except ValueError:
        return None
    return (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).timestamp()


def capture(ev: dict[str, Any]) -> tuple[str | None, str]:
    """(taken_at, camera) from products.quality.capture, else geotag's corrected utc; camera
    ONE_CAMERA when unknown."""
    p = products(ev)
    cap = (p.get("quality") or {}).get("capture") or {}
    return cap.get("taken_at") or (p.get("geotag") or {}).get("utc"), cap.get("camera") or ONE_CAMERA


# ---- burst ------------------------------------------------------------------------------------

def check_burst(o: dict[str, Any]) -> None:
    for k in ("max_gap_s", "min_cosine"):
        v = o[k]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
            raise ValueError(f"burst.{k} must be a number >= 0")
    if o["min_cosine"] > 1:
        raise ValueError("burst.min_cosine must be at most 1")


def burst(events: list[dict[str, Any]], o: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """path -> {id, size, index, gap_s, cosine}: id is null for a frame in no burst; gap_s and
    cosine are to the previous frame of the same camera (null for the first)."""
    frames = []
    for ev in events:
        if ev.get("type") != "result":
            continue
        taken, camera = capture(ev)
        frames.append({"path": ev["path"], "camera": camera, "t": seconds(taken), "vec": vector(ev)})
    chains: list[list[dict]] = []
    links: dict[str, tuple[float | None, float | None]] = {}
    by_camera: dict[str, list[dict]] = {}
    for f in frames:
        by_camera.setdefault(f["camera"], []).append(f)
    for cam in sorted(by_camera):
        timed = sorted((f for f in by_camera[cam] if f["t"] is not None), key=lambda f: (f["t"], f["path"]))
        chains += [[f] for f in by_camera[cam] if f["t"] is None]
        prev = None
        for f in timed:
            gap = None if prev is None else f["t"] - prev["t"]
            cos = None if prev is None else cosine(prev["vec"], f["vec"])
            links[f["path"]] = (None if gap is None else round(gap, 3), None if cos is None else round(cos, 4))
            if prev is not None and gap <= o["max_gap_s"] and cos is not None and cos >= o["min_cosine"]:
                chains[-1].append(f)
            else:
                chains.append([f])
            prev = f
    out: dict[str, dict[str, Any]] = {}
    n = 0
    for chain in sorted(chains, key=lambda c: (c[0]["t"] is None, c[0]["t"] or 0.0, c[0]["camera"], c[0]["path"])):
        bid = None
        if len(chain) > 1:
            n += 1
            bid = f"b{n:04d}"
        for i, f in enumerate(chain):
            gap, cos = links.get(f["path"], (None, None))
            out[f["path"]] = {"id": bid, "size": len(chain), "index": i, "gap_s": gap, "cosine": cos}
    return out


# ---- select -----------------------------------------------------------------------------------

def check_select(o: dict[str, Any]) -> None:
    n = o["per_category"]
    if isinstance(n, bool) or not isinstance(n, int) or n < 0:
        raise ValueError("select.per_category must be an integer >= 0 (0 = no limit)")
    for k in ("dup_cosine", "sharp_tie", "exposure_ok"):
        v = o[k]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
            raise ValueError(f"select.{k} must be a number >= 0")
    w = o["waive"]
    if not isinstance(w, dict) or not all(isinstance(v, list) and all(isinstance(x, str) for x in v) for v in w.values()):
        raise ValueError("select.waive must be an object {category: [reject reasons]}")


def _sharpness(q: dict[str, Any]) -> float | None:
    """Subject sharpness as 1 - blur (the subject's, else the frame's sharpest part)."""
    for region in (q.get("subject") or {}, q.get("frame") or {}):
        if region.get("blur") is not None:
            return 1.0 - region["blur"]
    return None


def _exposure(q: dict[str, Any]) -> float | None:
    region = q.get("subject") or q.get("frame") or {}
    return region.get("exposure")


def facts(ev: dict[str, Any], o: dict[str, Any]) -> dict[str, Any]:
    """What select ranks one result by."""
    p = products(ev)
    q = p.get("quality") or {}
    category = (p.get("scene") or {}).get("label") or UNCATEGORISED
    raw = list(q.get("reject_reasons") or [])
    lifted = set(o["waive"].get(category, []))
    aesthetic = (p.get("aesthetics") or {}).get("score")   # null when no head scored it (a note says why)
    taken, _ = capture(ev)
    return {"path": ev["path"], "category": category, "reasons": [r for r in raw if r not in lifted],
            "waived": [r for r in raw if r in lifted], "sharpness": _sharpness(q),
            "cut": bool((q.get("subject") or {}).get("cut")), "exposure": _exposure(q),
            "aesthetic": aesthetic if isinstance(aesthetic, (int, float)) and not isinstance(aesthetic, bool) else None,
            "t": seconds(taken), "vec": vector(ev), "burst": (p.get("burst") or {}).get("id")}


def burst_order(frames: list[dict[str, Any]], o: dict[str, Any]) -> list[dict[str, Any]]:
    """One burst's frames, best first: not rejected; sharpness, where every frame within sharp_tie
    of the sharpest kept frame counts as equally sharp; not cut off; exposure within exposure_ok;
    aesthetic; then exact sharpness and path."""
    kept = [f for f in frames if not f["reasons"]] or frames
    top = max((f["sharpness"] for f in kept if f["sharpness"] is not None), default=None)

    def key(f: dict[str, Any]) -> tuple:
        s = -math.inf if f["sharpness"] is None else f["sharpness"]
        tied = top is not None and s >= top - o["sharp_tie"]
        exposure_bad = f["exposure"] is not None and abs(f["exposure"]) > o["exposure_ok"]
        aesthetic = -math.inf if f["aesthetic"] is None else f["aesthetic"]
        return (bool(f["reasons"]), not tied, 0.0 if tied else -s, f["cut"], exposure_bad, -aesthetic, -s, f["path"])
    return sorted(frames, key=key)


def category_order(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Best frames of one category, best first: aesthetic (when present), sharpness, capture time."""
    return sorted(frames, key=lambda f: (-(f["aesthetic"] if f["aesthetic"] is not None else -math.inf),
                                          -(f["sharpness"] if f["sharpness"] is not None else -math.inf),
                                          f["t"] if f["t"] is not None else math.inf, f["path"]))


def select(events: list[dict[str, Any]], o: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """path -> {status, keep, reasons, waived, category, rank, burst_rank, duplicate_of, sharpness,
    aesthetic}."""
    frames = [facts(ev, o) for ev in events if ev.get("type") == "result"]
    groups: dict[str, list[dict[str, Any]]] = {}
    for f in frames:
        groups.setdefault(f["burst"] or f"solo:{f['path']}", []).append(f)
    out: dict[str, dict[str, Any]] = {}
    best: list[dict[str, Any]] = []
    for members in groups.values():
        ordered = burst_order(members, o)
        for i, f in enumerate(ordered):
            f["burst_rank"] = i + 1
            f["status"] = "reject" if f["reasons"] else ("duplicate" if i else None)
            f["duplicate_of"] = ordered[0]["path"] if i and not f["reasons"] else None
        if not ordered[0]["reasons"]:
            best.append(ordered[0])
    for category in sorted({f["category"] for f in best}):
        picked: list[dict[str, Any]] = []
        for rank, f in enumerate(category_order([f for f in best if f["category"] == category]), start=1):
            f["rank"] = rank
            twin = next((p for p in picked if (cosine(p["vec"], f["vec"]) or -1.0) >= o["dup_cosine"]), None)
            if twin is not None:
                f["status"], f["duplicate_of"] = "duplicate", twin["path"]
            elif not o["per_category"] or len(picked) < o["per_category"]:
                f["status"] = "pick"
                picked.append(f)
            else:
                f["status"] = "spare"
    for f in frames:
        out[f["path"]] = {"status": f["status"], "keep": f["status"] == "pick", "reasons": f["reasons"],
                          "waived": f["waived"], "category": f["category"], "rank": f.get("rank"),
                          "burst_rank": f["burst_rank"], "duplicate_of": f["duplicate_of"],
                          "sharpness": None if f["sharpness"] is None else round(f["sharpness"], 4),
                          "aesthetic": f["aesthetic"]}
    return out


# ---- running reducers ------------------------------------------------------------------------

def apply(events: Iterable[dict[str, Any]], reducers: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """A copy of `events` with each named reducer's output under products[<reducer>] of every
    result, in the registry order (burst before select); options are each reducer's defaults
    under `reducers[name]`. ValueError on an unknown reducer or a bad option."""
    from bioscan import plugin
    from bioscan.plugins import REDUCERS

    known = {m.name: m for m in REDUCERS}
    unknown = sorted(set(reducers) - set(known))
    if unknown:
        raise ValueError(f"unknown reducers {unknown} (known: {', '.join(known)})")
    out = [copy.deepcopy(ev) if ev.get("type") == "result" else ev for ev in events]
    for m in REDUCERS:
        if m.name not in reducers:
            continue
        o = {**m.defaults, **(reducers[m.name] or {})}
        bad = sorted(set(reducers[m.name] or {}) - set(m.defaults))
        if bad:
            raise ValueError(f"unknown options {m.name}: {bad}")
        m.check(o)
        got = plugin.load(m).reduce(out, o)
        for ev in out:
            if ev.get("type") == "result" and ev["path"] in got:
                ev.setdefault("products", {})[m.name] = got[ev["path"]]
    return out


RECORD_FIELDS = ("path", "status", "keep", "category", "rank", "reasons", "waived", "burst", "burst_size",
                 "burst_rank", "duplicate_of", "sharpness", "aesthetic", "taken_at")


def records(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """One cull record per result (after apply): RECORD_FIELDS, in event order."""
    out = []
    for ev in events:
        p = products(ev)
        if ev.get("type") != "result":
            continue
        s, b = p.get("select") or {}, p.get("burst") or {}
        out.append({"path": ev["path"], "status": s.get("status"), "keep": bool(s.get("keep")),
                    "category": s.get("category"), "rank": s.get("rank"), "reasons": s.get("reasons") or [],
                    "waived": s.get("waived") or [], "burst": b.get("id"), "burst_size": b.get("size", 1),
                    "burst_rank": s.get("burst_rank"), "duplicate_of": s.get("duplicate_of"),
                    "sharpness": s.get("sharpness"), "aesthetic": s.get("aesthetic"), "taken_at": capture(ev)[0]})
    return out


# ---- harness rows (plugin.Metric.row; docs/harness.md "Album tier") -------------------------------
# Ground truth (scripts/cull_synth.py, or an owner's labels): keep (1/0), reject_reasons (";"-joined),
# burst_id (blank = in no burst), scene (blank = unlabelled). A column the CSV lacks measures nothing.

REJECT_REASONS = ("soft_subject", "motion_or_defocus", "overexposed", "underexposed", "subject_cut",
                  "subject_too_small", "no_subject")      # = bioscan.plugins.quality.REASONS
SOFT = ("soft_subject", "motion_or_defocus")              # scope "soft": either (they differ only in the background)


def split_reasons(text: str | None) -> list[str]:
    return [r.strip() for r in (text or "").replace("|", ";").split(";") if r.strip()]


def _truth_reasons(truth: dict[str, Any]) -> set[str] | None:
    if "reject_reasons" not in truth and "keep" not in truth:
        return None
    return set(split_reasons(truth.get("reject_reasons")))


def final_reasons(pred: dict[str, Any] | None) -> set[str] | None:
    """The reasons a result was rejected for: select's (after its waivers) when the run had it,
    else quality's; None when neither ran (a result) or the image failed (not a result)."""
    p = products(pred or {})
    if "select" in p:
        return set(p["select"].get("reasons") or [])
    if "quality" in p:
        return set(p["quality"].get("reject_reasons") or [])
    return None


def _measured(pred: dict[str, Any] | None) -> bool:
    """False when the image has a result without the cull products: the run did not measure it."""
    return not (pred and pred.get("type") == "result" and final_reasons(pred) is None)


def row_reject_precision(truth: dict[str, Any], pred: dict[str, Any] | None) -> dict[str, Any]:
    """Per scope (all, soft, each reason): among images rejected for it, whether the truth has it."""
    t, p = _truth_reasons(truth), final_reasons(pred)
    if t is None or not p:
        return {}
    out: dict[str, Any] = {"all": bool(t), "soft": bool(t & set(SOFT)) if p & set(SOFT) else None}
    out |= {r: (r in t) if r in p else None for r in REJECT_REASONS}
    return out


def row_reject_recall(truth: dict[str, Any], pred: dict[str, Any] | None) -> dict[str, Any]:
    """Per scope (all, soft, each reason): among images whose truth has it, whether they were
    rejected for it. A failed image counts as not rejected, as failures count as misses elsewhere."""
    t = _truth_reasons(truth)
    if not t or not _measured(pred):
        return {}
    p = final_reasons(pred) or set()
    out: dict[str, Any] = {"all": bool(p), "soft": bool(p & set(SOFT)) if t & set(SOFT) else None}
    out |= {r: (r in p) if r in t else None for r in REJECT_REASONS}
    return out


def row_keepers_lost(truth: dict[str, Any], pred: dict[str, Any] | None) -> dict[str, Any]:
    """Among images the truth keeps (keep = 1), whether a rule rejected them. Failed images do not count."""
    p = final_reasons(pred)
    if str(truth.get("keep", "")).strip() != "1" or p is None:
        return {}
    return {"all": bool(p)}


def row_burst(truth: dict[str, Any], pred: dict[str, Any] | None) -> dict[str, Any]:
    """(truth burst, predicted burst) for pairwise precision / recall / F1; a frame in no burst is its own group."""
    b = products(pred or {}).get("burst")
    if "burst_id" not in truth or b is None:
        return {}
    solo = f"solo:{truth['path']}"
    return {"all": ((truth.get("burst_id") or "").strip() or solo, b.get("id") or solo)}


def row_scene(truth: dict[str, Any], pred: dict[str, Any] | None) -> dict[str, Any]:
    """Whether the top scene label is the truth's, in `all` and in the truth label's own scope."""
    label = (truth.get("scene") or "").strip()
    s = products(pred or {}).get("scene")
    if not label or not s:
        return {}
    hit = s.get("label") == label
    return {"all": hit, label: hit}


def row_scene_group(truth: dict[str, Any], pred: dict[str, Any] | None) -> dict[str, Any]:
    """Whether the scene group is the truth's `scene_group`, in `all` and in the truth group's own scope.
    The prediction's group is `products.scene.group` when the stage reports one, else its label (a stage
    whose labels are the groups themselves, as today's 8 defaults)."""
    group = (truth.get("scene_group") or "").strip()
    s = products(pred or {}).get("scene")
    if not group or not s:
        return {}
    hit = (s.get("group") or s.get("label")) == group
    return {"all": hit, group: hit}


class _Reducer:
    def __init__(self, fn) -> None:
        self.reduce = fn


BURST = _Reducer(burst)
SELECT = _Reducer(select)
