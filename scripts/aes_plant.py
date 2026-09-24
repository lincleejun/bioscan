"""Planted copies for the aesthetic golden set: frames whose right answer is known without the owner.

    uv run python scripts/aes_plant.py GOLDEN [--n 60] [--seed 7]

Picks `--n` originals from GOLDEN/images.csv (rated, not planted, readable by Pillow; RAW files are
skipped), deterministically from the seed, and writes one copy of each kind to GOLDEN/planted/:

| variant | what | expected |
|---|---|---|
| rename | the file, byte for byte, under a new name | same score (path must not matter) |
| jpeg95 | re-encoded JPEG quality 95, metadata dropped | same score |
| resize2048 | long edge 2048 px (the service's own decode size), when the original is larger | same score |
| blur | Gaussian blur, radius = long edge / 300 (at least 2 px) | lower score |
| ev-2 / ev+2 | -2 / +2 EV in linear light, clipped to 8 bit (scripts/cull_synth.py `ev`) | lower score |
| jpeg10 | JPEG quality 10 | lower score |

"Same" means within 5 percentile points of the album's score distribution; "lower" means strictly
below the original (`bioscan bench aesthetic score`). Rows are appended to images.csv with
`variant_of` and `variant`, copying split, trip and category; planted rows carry no stars, keep or
group, so they never enter the ranking, pair, group or cull metrics. Refuses to run twice: the
golden set is frozen once built.
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import random
import shutil
from pathlib import Path

from PIL import Image, ImageFilter, ImageOps

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("cull_synth", HERE / "cull_synth.py")
cull_synth = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cull_synth)

KINDS = ("rename", "jpeg95", "resize2048", "blur", "ev-2", "ev+2", "jpeg10")
PIL_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"}
DECODE_EDGE = 2048


def make(src: Path, kind: str, out_dir: Path, tag: str) -> Path | None:
    """One planted copy of `src`; None when the kind does not apply (resize of a small image)."""
    if kind == "rename":
        dst = out_dir / f"{tag}{src.suffix.lower()}"
        shutil.copyfile(src, dst)
        return dst
    im = ImageOps.exif_transpose(Image.open(src)).convert("RGB")
    long_edge = max(im.size)
    if kind == "resize2048":
        if long_edge <= DECODE_EDGE:
            return None
        im = im.copy()
        im.thumbnail((DECODE_EDGE, DECODE_EDGE), Image.Resampling.LANCZOS)
    elif kind == "blur":
        im = im.filter(ImageFilter.GaussianBlur(max(2.0, long_edge / 300)))
    elif kind in ("ev-2", "ev+2"):
        im = cull_synth.ev(im, -2.0 if kind == "ev-2" else 2.0)
    dst = out_dir / f"{tag}__{kind}.jpg"
    im.save(dst, quality=10 if kind == "jpeg10" else 95)
    return dst


def plant(golden: str | Path, n: int = 60, seed: int = 7) -> list[dict]:
    root = Path(golden).resolve()
    csv_path = root / "images.csv"
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fields = list(reader.fieldnames or [])
        rows = list(reader)
    if any((r.get("variant") or "").strip() for r in rows):
        raise SystemExit(f"error: {csv_path} already has planted rows; the golden set is frozen once built")
    for col in ("variant_of", "variant"):
        if col not in fields:
            fields.append(col)

    def path_of(r):
        p = Path(r["path"]).expanduser()
        return p if p.is_absolute() else root / p

    pool = [r for r in rows if (r.get("stars") or "").strip() not in ("", "0") and path_of(r).suffix.lower() in PIL_EXT
            and path_of(r).is_file()]
    pool.sort(key=lambda r: r["path"])
    chosen = random.Random(seed).sample(pool, min(n, len(pool)))
    out_dir = root / "planted"
    out_dir.mkdir(exist_ok=True)
    added = []
    for i, r in enumerate(chosen):
        tag = f"p{seed}-{i:04d}"
        for kind in KINDS:
            dst = make(path_of(r), kind, out_dir, tag)
            if dst is None:
                continue
            added.append({"path": dst.relative_to(root).as_posix(), "variant_of": r["path"], "variant": kind,
                          **{k: r[k] for k in ("split", "trip", "category") if k in fields}})
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows + added)
    return added


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("golden", help="golden folder with images.csv")
    ap.add_argument("--n", type=int, default=60, help="originals to copy (default %(default)s)")
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args(argv)
    added = plant(a.golden, a.n, a.seed)
    print(f"{len(added)} planted copies of {len({x['variant_of'] for x in added})} originals -> {a.golden}/planted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
