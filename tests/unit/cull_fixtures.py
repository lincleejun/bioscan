"""Tiny synthetic photos for the cull tests: 1/f ("pink") noise, which has the power spectrum of
natural images, with an elliptical subject of finer texture. `photo(seed)` returns the image and
the subject box (normalised xyxy); `bokeh=True` blurs the background as a long lens would."""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageFilter

BOX = (0.3, 0.25, 0.62, 0.8)


def pink(h: int, w: int, rng: np.random.Generator, beta: float = 2.0) -> np.ndarray:
    f = np.fft.fft2(rng.normal(size=(h, w)))
    fy, fx = np.meshgrid(np.fft.fftfreq(h), np.fft.fftfreq(w), indexing="ij")
    r = np.sqrt(fx ** 2 + fy ** 2)
    r[0, 0] = 1
    img = np.real(np.fft.ifft2(f / r ** (beta / 2)))
    return (img - img.mean()) / img.std()


def photo(seed: int = 0, w: int = 320, h: int = 240, bokeh: bool = False, box=BOX) -> tuple[Image.Image, tuple]:
    rng = np.random.default_rng(seed)
    bg = 0.45 + 0.12 * pink(h, w, rng)
    if bokeh:
        bg = np.asarray(Image.fromarray((np.clip(bg, 0, 1) * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(6)),
                        dtype=np.float32) / 255
    x0, y0, x1, y1 = box
    yy, xx = np.mgrid[0:h, 0:w]
    cx, cy, rx, ry = (x0 + x1) / 2 * w, (y0 + y1) / 2 * h, (x1 - x0) / 2 * w, (y1 - y0) / 2 * h
    mask = ((xx - cx) / rx) ** 2 + ((yy - cy) / ry) ** 2 <= 1
    img = np.where(mask, 0.4 + 0.18 * pink(h, w, rng, beta=1.6), bg)
    col = np.stack([img, img * 0.95 + 0.03, img * 0.85], -1)
    return Image.fromarray((np.clip(col, 0, 1) * 255).astype(np.uint8)), box


def box(xyxy, score: float = 0.9, kind: str = "bird", box_id: int = 0) -> dict:
    return {"id": box_id, "xyxy": list(xyxy), "score": score, "kind": kind}
