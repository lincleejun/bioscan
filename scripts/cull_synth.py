"""Synthetic album set for the cull rules: degrade photos into labelled rejects and bursts.

    uv run python scripts/cull_synth.py --preds runs/x/preds.ndjson --out runs/album-synth [--seed 7]
    uv run python scripts/cull_synth.py --boxes sources.csv --out runs/album-synth

Sources are photos with their subject box: the best identify box of each result in a preds file
(`bioscan run --json`, `bioscan eval`), or a CSV `path,x0,y0,x1,y1[,scene]` (box normalised 0-1,
upright image). A source is used when its box is clear of the frame edges (so "cut" and "too small"
have one meaning) and covers 2-50 % of the frame. Per source, deterministically from (seed, index):

| variant | what | expected reasons | keep |
|---|---|---|---|
| original | the photo, re-saved | none | 1 |
| blur | Gaussian blur on the subject box, sigma = long edge / 150 (at least 3 px) | soft_subject | 0 |
| smear | motion blur (line kernel, long edge / 25 px, at least 12, horizontal or vertical) on the subject box | soft_subject | 0 |
| shake | the same motion blur over the whole frame | motion | 0 |
| defocus | the same Gaussian blur over the whole frame | defocus | 0 |
| cut | cropped so that 40 % of the box is outside the new frame | subject_cut | 0 |
| over / under | +2 / -2 EV in linear light, clipped back to 8 bit | overexposed / underexposed | 0 |
| small | the photo shrunk onto a canvas twice its size, subject at 0.3 % of the frame | subject_too_small | 0 |
| burst | (every `--burst-every`-th source) 4 frames shifted by up to 2 %, 0.2 s apart | none (burst_id) | 1 |

Every variant gets its own capture time, 10 minutes apart, so only burst frames can form a burst.
Writes `<out>/photos/*.jpg` and `<out>/groundtruth-album.csv` with columns path, tier (album),
keep, reject_reasons (";"-joined), burst_id, scene, taken_at, source, variant. docs/harness.md
("Album tier") scores it: `bioscan bench run <out>/groundtruth-album.csv --profile album --tier album`.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

TIER = "album"
START = datetime.fromisoformat("2026-05-01T08:00:00-07:00")
SHOT_GAP_S = 600
BURST_FRAMES, BURST_DT_S, BURST_SHIFT = 4, 0.2, 0.02
CUT_OUTSIDE = 0.4
SMALL_SHARE = 0.003
MIN_AREA, MAX_AREA, EDGE_CLEAR = 0.02, 0.5, 0.03
EV = 2.0
FIELDS = ("path", "tier", "keep", "reject_reasons", "burst_id", "scene", "taken_at", "source", "variant")


def usable(box: tuple[float, float, float, float]) -> bool:
    x0, y0, x1, y1 = box
    area = (x1 - x0) * (y1 - y0)
    return MIN_AREA <= area <= MAX_AREA and min(x0, y0, 1 - x1, 1 - y1) >= EDGE_CLEAR


def _px(im: Image.Image, box) -> tuple[int, int, int, int]:
    return (int(box[0] * im.width), int(box[1] * im.height), round(box[2] * im.width), round(box[3] * im.height))


def _line_blur(im: Image.Image, length: int, vertical: bool) -> Image.Image:
    a = np.asarray(im, dtype=np.float32)
    axis = 0 if vertical else 1
    pad = [(0, 0)] * a.ndim
    pad[axis] = (length // 2 + 1, length - length // 2 - 1)
    c = np.cumsum(np.pad(a, pad, mode="edge"), axis=axis)
    out = (np.take(c, range(length, c.shape[axis]), axis=axis) - np.take(c, range(0, c.shape[axis] - length), axis=axis))
    return Image.fromarray(np.clip(out / length, 0, 255).astype(np.uint8))


def blur_box(im: Image.Image, box, sigma: float) -> Image.Image:
    out = im.copy()
    p = _px(im, box)
    out.paste(im.filter(ImageFilter.GaussianBlur(sigma)).crop(p), p[:2])
    return out


def smear_box(im: Image.Image, box, length: int, vertical: bool) -> Image.Image:
    out = im.copy()
    p = _px(im, box)
    out.paste(_line_blur(im, length, vertical).crop(p), p[:2])
    return out


def ev(im: Image.Image, stops: float) -> Image.Image:
    """Exposure change in linear light (sRGB transfer), clipped to 8 bit."""
    s = np.asarray(im, dtype=np.float32) / 255.0
    lin = np.where(s <= 0.04045, s / 12.92, ((s + 0.055) / 1.055) ** 2.4) * (2.0 ** stops)
    lin = np.clip(lin, 0.0, 1.0)
    out = np.where(lin <= 0.0031308, lin * 12.92, 1.055 * lin ** (1 / 2.4) - 0.055)
    return Image.fromarray(np.clip(np.round(out * 255), 0, 255).astype(np.uint8))


def cut(im: Image.Image, box) -> tuple[Image.Image, tuple[float, ...]]:
    """Crop the side with the most room so CUT_OUTSIDE of the box width (or height) falls outside."""
    x0, y0, x1, y1 = _px(im, box)
    bw, bh = x1 - x0, y1 - y0
    room = {"left": x0, "right": im.width - x1, "top": y0, "bottom": im.height - y1}
    side = max(room, key=room.get)
    if side == "left":
        crop = (x0 + int(CUT_OUTSIDE * bw), 0, im.width, im.height)
    elif side == "right":
        crop = (0, 0, x1 - int(CUT_OUTSIDE * bw), im.height)
    elif side == "top":
        crop = (0, y0 + int(CUT_OUTSIDE * bh), im.width, im.height)
    else:
        crop = (0, 0, im.width, y1 - int(CUT_OUTSIDE * bh))
    out = im.crop(crop)
    nb = (max(0, x0 - crop[0]) / out.width, max(0, y0 - crop[1]) / out.height,
          min(out.width, x1 - crop[0]) / out.width, min(out.height, y1 - crop[1]) / out.height)
    return out, nb


def small(im: Image.Image, box, rng: random.Random) -> Image.Image:
    """The photo shrunk onto a canvas twice its size (mean colour, light noise) so the subject box
    covers SMALL_SHARE of the frame."""
    area = (box[2] - box[0]) * (box[3] - box[1])
    f = min(0.5, (4 * SMALL_SHARE / area) ** 0.5)
    w, h = im.width * 2, im.height * 2
    mean = np.asarray(im, dtype=np.float32).reshape(-1, 3).mean(axis=0)
    noise = np.random.default_rng(rng.randrange(2 ** 32)).normal(0, 4, (h, w, 3))
    canvas = Image.fromarray(np.clip(mean + noise, 0, 255).astype(np.uint8))
    sm = im.resize((max(1, round(im.width * f)), max(1, round(im.height * f))), Image.Resampling.LANCZOS)
    canvas.paste(sm, (rng.randrange(0, w - sm.width), rng.randrange(0, h - sm.height)))
    return canvas


def shifted(im: Image.Image, rng: random.Random) -> Image.Image:
    """One burst frame: the photo cropped by up to BURST_SHIFT per side, resized back, +-3 % brightness."""
    dx0, dx1, dy0, dy1 = (rng.uniform(0, BURST_SHIFT) for _ in range(4))
    crop = (round(dx0 * im.width), round(dy0 * im.height), im.width - round(dx1 * im.width),
            im.height - round(dy1 * im.height))
    a = np.asarray(im.crop(crop).resize(im.size, Image.Resampling.BILINEAR), dtype=np.float32) * rng.uniform(0.97, 1.03)
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))


def build(sources: list[dict], out: str | Path, seed: int = 7, burst_every: int = 3) -> list[dict]:
    """Writes the photos and returns the ground-truth rows. `sources`: {path, box, scene?}."""
    out = Path(out)
    (out / "photos").mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    shot = 0

    def emit(img: Image.Image, src: dict, i: int, variant: str, reasons: str = "", keep: int = 0,
             burst: str = "", when: datetime | None = None) -> None:
        nonlocal shot
        path = out / "photos" / f"s{i:03d}-{variant}.jpg"
        img.save(path, quality=92)
        t = when or START + timedelta(seconds=SHOT_GAP_S * shot)
        shot += when is None
        rows.append({"path": str(path.resolve()), "tier": TIER, "keep": keep, "reject_reasons": reasons,
                     "burst_id": burst, "scene": src.get("scene") or "", "source": src["path"], "variant": variant,
                     "taken_at": t.isoformat(timespec="milliseconds") if when else t.isoformat()})

    for i, src in enumerate(s for s in sources if usable(tuple(s["box"]))):
        rng = random.Random(f"{seed}:{i}")
        with Image.open(src["path"]) as f:
            im = f.convert("RGB")
        box = tuple(src["box"])
        long_edge = max(im.size)
        emit(im, src, i, "original", keep=1)
        sigma = max(3.0, long_edge / 150)
        emit(blur_box(im, box, sigma), src, i, "blur", "soft_subject")
        vertical = rng.random() < 0.5
        length = max(12, long_edge // 25)
        emit(smear_box(im, box, length, vertical), src, i, "smear", "soft_subject")
        emit(_line_blur(im, length, vertical), src, i, "shake", "motion")
        emit(im.filter(ImageFilter.GaussianBlur(sigma)), src, i, "defocus", "defocus")
        cropped, nb = cut(im, box)
        if (nb[2] - nb[0]) * (nb[3] - nb[1]) < 0.45:
            emit(cropped, src, i, "cut", "subject_cut")
        emit(ev(im, EV), src, i, "over", "overexposed")
        emit(ev(im, -EV), src, i, "under", "underexposed")
        emit(small(im, box, rng), src, i, "small", "subject_too_small")
        if burst_every and i % burst_every == 0:
            t0 = START + timedelta(seconds=SHOT_GAP_S * shot)
            shot += 1
            for k in range(BURST_FRAMES):
                emit(shifted(im, rng), src, i, f"burst{k}", keep=1, burst=f"s{i:03d}",
                     when=t0 + timedelta(seconds=BURST_DT_S * k))
    with open(out / "groundtruth-album.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    return rows


def sources_from_events(events, scenes: dict[str, str] | None = None) -> list[dict]:
    """The best identify box of every result event that has one; `scenes`: path -> scene label."""
    out = []
    for e in events:
        boxes = ((e.get("products") or {}).get("identify") or {}).get("boxes") or []
        if e.get("type") == "result" and boxes:
            best = max(boxes, key=lambda b: b.get("score", 0))
            out.append({"path": e["path"], "box": best["xyxy"], "scene": (scenes or {}).get(e["path"], "")})
    return out


def sources_from_preds(path: str | Path, scenes: dict[str, str] | None = None) -> list[dict]:
    """sources_from_events over a preds / run NDJSON file."""
    with open(path, encoding="utf-8") as f:
        return sources_from_events((json.loads(line) for line in f if line.strip()), scenes)


def sources_from_csv(path: str | Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return [{"path": r["path"], "box": [float(r[k]) for k in ("x0", "y0", "x1", "y1")], "scene": r.get("scene", "")}
                for r in csv.DictReader(f)]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--preds", help="preds / run NDJSON: each result's best identify box is the subject")
    src.add_argument("--boxes", help="CSV path,x0,y0,x1,y1[,scene] (normalised box)")
    p.add_argument("--out", required=True)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--burst-every", type=int, default=3, help="a burst from every Nth source (0 = none)")
    p.add_argument("--scene", default="", help="scene label for every source without one (e.g. wildlife)")
    a = p.parse_args(argv)
    sources = sources_from_preds(a.preds) if a.preds else sources_from_csv(a.boxes)
    for s in sources:
        s["scene"] = s.get("scene") or a.scene
    rows = build(sources, a.out, a.seed, a.burst_every)
    print(f"{len(rows)} photos from {len({r['source'] for r in rows})} sources -> {Path(a.out) / 'groundtruth-album.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
