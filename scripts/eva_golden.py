"""The public aesthetic golden set: 100 EVA images the crowd agrees on, 20 per star, held out of training.

    uv run python scripts/eva_golden.py select --votes EVA/data/votes_filtered.csv   # once; writes the frozen list
    uv run python scripts/eva_golden.py build --eva ~/.cache/bioscan/eva --out ~/aes-golden-eva
    bioscan run ~/aes-golden-eva/images -r --profile album --json --out runs/aes/eva-public.ndjson
    bioscan bench aesthetic score ~/aes-golden-eva runs/aes/eva-public.ndjson --out runs/aes/eva-public

`select` reads EVA's filtered votes (bioscan.aesthetic.EVA_COMMIT) and writes data/aesthetic/eva-golden-v1.csv:

1. Per image: mean and standard deviation of the 0-10 general score, number of votes, and the means of the
   four attribute ratings (visual = light and colour, composition, quality, semantic).
2. Star bands on the mean, with gaps so neighbouring stars are clearly apart (BANDS below). EVA's means are
   narrow (half of the 4,070 images lie in 5.3-7.0), so equal-width or quantile bins would put nearly equal
   images on both sides of a boundary.
3. Consensus: inside each band, only images whose sd is at or below that band's median sd. Low-scored images
   draw more disagreement, so one global sd limit would empty the low bands.
4. 20 per band, drawn with seed 7. The list is frozen: `select` refuses to overwrite it.

`bioscan.aesthetic.read_eva` leaves these images out of every general-head fit (train_aesthetic_head.py and
`bioscan aesthetic train --eva`), and the head's provenance records the held-out list's sha256.

`build` writes a golden folder for `bioscan bench aesthetic`: symlinks under `images/` to the EVA checkout's
JPEGs (never copied into the repository: the photos' copyright stays with their photographers) and
images.csv with stars, keep (4-5 stars = 1, 1-2 = 0, 3 = blank) and trip `eva`.
"""
from __future__ import annotations

import argparse
import csv
import random
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bioscan import aesthetic as aes  # noqa: E402

BANDS = {1: (0.0, 4.5), 2: (5.0, 5.5), 3: (5.9, 6.3), 4: (6.7, 7.1), 5: (7.5, 10.01)}   # [lo, hi) of the mean
PER_STAR = 20
SEED = 7
ATTRS = ("visual", "composition", "quality", "semantic")
FIELDS = ("image_id", "stars", "mean", "sd", "votes", *ATTRS)


def per_image(votes_csv: str | Path) -> list[dict]:
    """One row per EVA image: mean/sd of the general score, votes, attribute means."""
    acc: dict[str, dict[str, list[float]]] = {}
    with open(votes_csv, newline="", encoding="utf-8", errors="replace") as f:
        for r in csv.DictReader(f, delimiter="="):
            try:
                row = {"score": float(r["score"]), **{a: float(r[a]) for a in ATTRS}}
            except (KeyError, TypeError, ValueError):
                continue
            d = acc.setdefault(r["image_id"].strip(), {k: [] for k in row})
            for k, v in row.items():
                d[k].append(v)
    out = []
    for iid, d in acc.items():
        s = d["score"]
        if len(s) < 2:
            continue
        out.append({"image_id": iid, "mean": statistics.mean(s), "sd": statistics.stdev(s), "votes": len(s),
                    **{a: statistics.mean(d[a]) for a in ATTRS}})
    return sorted(out, key=lambda r: (len(r["image_id"]), r["image_id"]))


def select(rows: list[dict], per_star: int = PER_STAR, seed: int = SEED) -> list[dict]:
    rng = random.Random(seed)
    chosen = []
    for stars, (lo, hi) in BANDS.items():
        band = [r for r in rows if lo <= r["mean"] < hi]
        if not band:
            raise SystemExit(f"error: no EVA image in the {stars}-star band {lo}-{hi}")
        limit = statistics.median(r["sd"] for r in band)
        pool = [r for r in band if r["sd"] <= limit]
        if len(pool) < per_star:
            raise SystemExit(f"error: {stars}-star band has {len(pool)} agreeing images, fewer than {per_star}")
        chosen += [{**r, "stars": stars} for r in sorted(rng.sample(pool, per_star), key=lambda r: r["mean"])]
    return chosen


def write(rows: list[dict], path: Path) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: (f"{r[k]:.4f}" if isinstance(r[k], float) else r[k]) for k in FIELDS})


def build(eva: str | Path, out: str | Path, listing: str | Path = aes.EVA_HOLDOUT) -> int:
    """A golden folder for `bench aesthetic`: images/<id>.jpg symlinks and images.csv."""
    eva, out = Path(eva).expanduser().resolve(), Path(out).expanduser()
    (out / "images").mkdir(parents=True, exist_ok=True)
    if (out / "images.csv").exists():
        raise SystemExit(f"error: {out / 'images.csv'} exists; the golden folder is frozen once built")
    rows, missing = [], []
    with open(listing, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            src = eva / aes.EVA_IMAGES / f"{r['image_id']}.jpg"
            if not src.is_file():
                missing.append(r["image_id"])
                continue
            link = out / "images" / f"{r['image_id']}.jpg"
            if not link.exists():
                link.symlink_to(src)
            stars = int(r["stars"])
            rows.append({"path": f"images/{r['image_id']}.jpg", "trip": "eva", "stars": stars,
                         "keep": 1 if stars >= 4 else (0 if stars <= 2 else "")})
    if missing:
        raise SystemExit(f"error: {len(missing)} held-out images are not in {eva / aes.EVA_IMAGES}, "
                         f"e.g. {missing[:3]} (download EVA first: scripts/train_aesthetic_head.py --download-only)")
    with open(out / "images.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=("path", "trip", "stars", "keep"))
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("select", help="choose the held-out images (once)")
    s.add_argument("--votes", required=True, help="EVA data/votes_filtered.csv at the pinned commit")
    s.add_argument("--out", default=str(aes.EVA_HOLDOUT))
    s = sub.add_parser("build", help="golden folder (symlinks + images.csv) from an EVA checkout")
    s.add_argument("--eva", required=True, help="EVA checkout with images/EVA_together/")
    s.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "select":
        out = Path(a.out)
        if out.exists():
            raise SystemExit(f"error: {out} exists; the held-out list is frozen (a new list is a new version)")
        rows = select(per_image(a.votes))
        write(rows, out)
        print(f"{len(rows)} images ({PER_STAR} per star) -> {out}")
    else:
        print(f"{build(a.eva, a.out)} images -> {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
