"""facing as a stage: one batched OWLv2 call over the chunk's subject crops per kind, asking for the
head and the eye; the side comes from where the head (else the eye) sits in the box."""
from __future__ import annotations

from typing import Any

from bioscan.plugin import Item, StageBase, each
from bioscan.service.adapters.owlv2 import Detection

# (head prompt, eye prompt) per box kind; on the 95 smoke photos the kind's word found a head or an
# eye in 83 of 91 subject boxes, against 73 for bare "head" / "eye" (2026-10-22, unverified until #28)
PROMPTS = {"bird": ("bird head", "bird eye"), "mammal": ("mammal head", "mammal eye"),
           "other_animal": ("animal head", "animal eye")}
PART_FLOOR = 0.05       # OWLv2 confidence below this: no head / eye (part scores run 0.05-0.4, far below a whole animal's)
FACING_OFFSET = 0.15    # the part's centre at least this share of the box width off the box centre: facing that side
MIN_CROP = 8            # a subject crop under this many pixels on a side is not asked
NONE = {"box": None, "side": None, "offset": None, "head": None, "eye": None}


def subject(boxes: list[dict[str, Any]]) -> dict[str, Any] | None:
    """identify's best box by score (quality's subject)."""
    return max(boxes, key=lambda b: b.get("score", 0)) if boxes else None


def crop_box(xyxy: list[float], size: tuple[int, int]) -> tuple[int, int, int, int]:
    """The normalised box in whole pixels of a frame of `size`."""
    w, h = size
    x0, y0, x1, y1 = xyxy
    return round(x0 * w), round(y0 * h), round(x1 * w), round(y1 * h)


def best(dets: list[Detection], prompt: str) -> Detection | None:
    found = [d for d in dets if d.prompt == prompt]
    return max(found, key=lambda d: d.confidence) if found else None


def side_of(offset: float | None) -> str | None:
    if offset is None or abs(offset) < FACING_OFFSET:
        return None
    return "right" if offset > 0 else "left"


def facing(box: dict[str, Any], crop: tuple[int, int, int, int], size: tuple[int, int],
           dets: list[Detection]) -> dict[str, Any]:
    """The output for one subject `box`, cut from a frame of `size` at pixel `crop`, from OWLv2's
    `dets` on that crop (pixels of the crop)."""
    head_p, eye_p = PROMPTS.get(box.get("kind"), PROMPTS["other_animal"])
    cx0, cy0, cx1, cy1 = crop
    w, h = size

    def part(d: Detection | None) -> dict[str, Any] | None:
        if d is None:
            return None
        x0, y0, x1, y1 = d.bbox
        return {"xyxy": [round((cx0 + x0) / w, 5), round((cy0 + y0) / h, 5), round((cx0 + x1) / w, 5),
                         round((cy0 + y1) / h, 5)], "score": round(d.confidence, 4)}

    head, eye = best(dets, head_p), best(dets, eye_p)
    used = head or eye
    offset = None if used is None else round(((used.bbox[0] + used.bbox[2]) / 2) / (cx1 - cx0) - 0.5, 4)
    return {"box": box.get("id"), "side": side_of(offset), "offset": offset, "head": part(head), "eye": part(eye)}


class Facing(StageBase):
    def settings(self) -> dict[str, Any]:
        """The prompts and every UPPER_CASE number here (the fingerprint in result.engine.plugins["facing"])."""
        return {"prompts": PROMPTS, **{k: v for k, v in globals().items() if k.isupper()
                                       and isinstance(v, (int, float)) and not isinstance(v, bool)}}

    def run(self, engine: Any, items: list[Item], o: dict[str, Any]) -> list[Any]:
        try:
            return self._batch(engine, items)
        except Exception:  # noqa: BLE001
            if len(items) == 1:
                raise
        return each(items, lambda it: self._batch(engine, [it])[0])     # a bad image costs only itself

    def _batch(self, engine: Any, items: list[Item]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = [dict(NONE) for _ in items]
        asks: dict[tuple[str, str], list[tuple[int, dict[str, Any], tuple[int, int, int, int]]]] = {}
        for i, it in enumerate(items):
            box = subject(it.facts.get("boxes") or [])
            if box is None:
                continue
            crop = crop_box(box["xyxy"], it.dec.image.size)
            out[i]["box"] = box.get("id")
            if min(crop[2] - crop[0], crop[3] - crop[1]) >= MIN_CROP:
                asks.setdefault(PROMPTS.get(box.get("kind"), PROMPTS["other_animal"]), []).append((i, box, crop))
        for prompts, work in asks.items():
            found = engine.owlv2.detect_batch([items[i].dec.image.crop(crop) for i, _b, crop in work],
                                              list(prompts), threshold=PART_FLOOR)
            for (i, box, crop), dets in zip(work, found):
                out[i] = facing(box, crop, items[i].dec.image.size, dets)
        return out


STAGE = Facing()
