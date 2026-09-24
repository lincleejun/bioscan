"""Train the general aesthetic head (data/aesthetic/eva-head-v1.json) from EVA, in-process.

    uv run python scripts/train_aesthetic_head.py --download --eva-dir ~/.cache/bioscan/eva \\
        --embeddings ~/.cache/bioscan/eva/embeddings.ndjson

1. `--download`: the EVA files at the pinned commit (bioscan.aesthetic.EVA_COMMIT) from
   raw.githubusercontent.com: LICENSE, readme.md, data/votes_filtered.csv and the seven parts of
   images/EVA_together.zip (~695 MB), joined and unzipped into EVA_DIR/images/EVA_together/ (5,101
   images, 4,070 of them with filtered votes); the parts are deleted after unzipping.
2. Vectors: every EVA image goes through the service's own code: bioscan.service.decode.decode (the
   same 2048 px image the service embeds) and Engine.frame with the pinned SigLIP2 revision (weights
   from ~/.cache/huggingface, as the service loads them). `--embeddings` caches them (the format of
   `bioscan aesthetic --embeddings`), so a rerun only fits.
3. Fit: ridge on the standardised vectors to the per-image mean score (0-10), alpha by 5-fold CV
   (seeded), refit on all; CV SRCC/PLCC go into the head's provenance and are printed.
4. `--print-base64`: the head file between `===== BEGIN/END eva-head-v1.json base64 =====` markers
   (CI: the log carries the file; decode with `base64 -d`), then its sha256.

Runtime (estimate, unmeasured): download 1-3 min; 4,070 images through SigLIP2 base/224 on a 4-core
CI runner ~0.1-0.2 s each (7-14 min); on an M-series Mac with MPS 1-3 min; the fit takes seconds.

The same fit through a running service instead: `bioscan aesthetic train --eva EVA_DIR`.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import random
import sys
import time
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from bioscan import aesthetic as aes
from bioscan.cli.aesbench import _stamp, read_cache

BATCH = 32


def download(root: Path) -> None:
    """The pinned EVA files into `root` (skipping files already there), then the images unzipped
    and the zip parts deleted (once the images are there, the parts are not fetched again)."""
    images = root / aes.EVA_IMAGES
    have_images = images.is_dir() and any(images.glob("*.jpg"))
    for rel in ("LICENSE", "readme.md", aes.EVA_VOTES, *(() if have_images else aes.EVA_PARTS)):
        dest = root / rel
        if dest.is_file() and dest.stat().st_size:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        t = time.monotonic()
        part = dest.with_name(dest.name + ".part")
        with urllib.request.urlopen(f"{aes.EVA_RAW}/{rel}", timeout=600) as r, open(part, "wb") as f:
            while chunk := r.read(1 << 20):
                f.write(chunk)
        part.rename(dest)
        print(f"{rel}: {dest.stat().st_size / 2**20:.0f} MiB in {time.monotonic() - t:.0f} s", flush=True)
    if have_images:
        return
    joined = root / "images" / "EVA_together.zip"
    with open(joined, "wb") as out:
        for rel in aes.EVA_PARTS:
            out.write((root / rel).read_bytes())
    with zipfile.ZipFile(joined) as z:
        z.extractall(root / "images")
    joined.unlink()
    for rel in aes.EVA_PARTS:
        (root / rel).unlink()
    print(f"unzipped {sum(1 for _ in images.glob('*.jpg'))} images into {images}", flush=True)


def _decode(path: str):
    """The service's decode (the 2048 px image it embeds), or the exception."""
    from bioscan.service.decode import decode

    try:
        return decode(path)
    except Exception as e:  # noqa: BLE001 - one bad file costs that file
        return e


def embed(paths: list[str], cache: Path | None, device: str | None) -> dict[str, list[float]]:
    """Frame vectors for `paths`, from `cache` where the file is unchanged, else through the service's
    decode and Engine.frame (appended to `cache`)."""
    known = read_cache(cache)
    vecs = {p: aes.f16_decode(known[p]["vec"]) for p in paths if p in known and known[p].get("stamp") == _stamp(p)}
    todo = [p for p in paths if p not in vecs]
    if not todo:
        return vecs
    from bioscan.service.engine import Engine

    engine = Engine(device)
    engine.ensure(["siglip2"])
    print(f"embedding {len(todo)} images on {engine.device} ({len(vecs)} cached)", flush=True)
    t0 = time.monotonic()
    sink = open(cache, "a", encoding="utf-8") if cache else None
    try:
        with ThreadPoolExecutor(4) as pool:
            for i in range(0, len(todo), BATCH):
                got = list(zip(todo[i:i + BATCH], pool.map(_decode, todo[i:i + BATCH])))
                for p, d in got:
                    if isinstance(d, Exception):
                        print(f"  skipped {p}: {type(d).__name__}: {d}", flush=True)
                batch = [p for p, d in got if not isinstance(d, Exception)]
                if not batch:
                    continue
                frame, _ = engine.frame([d.image for _, d in got if not isinstance(d, Exception)])
                for p, v in zip(batch, frame):
                    vecs[p] = [float(x) for x in v]
                    if sink:
                        sink.write(json.dumps({"path": p, "stamp": _stamp(p), "vec": aes.f16_encode(vecs[p])}) + "\n")
                if (i // BATCH) % 10 == 0 or i + BATCH >= len(todo):
                    done = min(i + BATCH, len(todo))
                    rate = done / max(time.monotonic() - t0, 1e-9)
                    print(f"  {done}/{len(todo)} ({rate:.1f} img/s)", flush=True)
    finally:
        if sink:
            sink.close()
    return vecs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--eva-dir", default=str(Path("~/.cache/bioscan/eva").expanduser()),
                    help="EVA checkout or download folder (default %(default)s)")
    ap.add_argument("--download", action="store_true", help="fetch the pinned EVA files into --eva-dir first")
    ap.add_argument("--download-only", action="store_true", help="fetch the EVA files, then stop (no model, no fit)")
    ap.add_argument("--embeddings", help="NDJSON vector cache (default EVA_DIR/embeddings.ndjson)")
    ap.add_argument("--out", default=str(aes.BUILTIN_HEAD), help="head file (default %(default)s)")
    ap.add_argument("--name", default="eva-head-v1")
    ap.add_argument("--limit", type=int, help="a seeded random subset of this many images (quick runs)")
    ap.add_argument("--device", help="cpu or mps (default: mps when available)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--print-base64", action="store_true", help="print the head file base64 between markers")
    a = ap.parse_args(argv)

    root = Path(a.eva_dir).expanduser()
    if a.download or a.download_only:
        download(root)
    if a.download_only:
        print(f"EVA in {root}: {len(aes.read_eva(root))} images with votes")
        return 0
    eva = aes.read_eva(root)
    if not eva:
        raise SystemExit(f"no EVA images with votes under {root} (use --download)")
    if a.limit:
        eva = sorted(random.Random(a.seed).sample(eva, min(a.limit, len(eva))))
    cache = Path(a.embeddings).expanduser() if a.embeddings else root / "embeddings.ndjson"
    t0 = time.monotonic()
    vecs = embed([p for p, _, _ in eva], cache, a.device)
    t_embed = time.monotonic() - t0
    eva = [e for e in eva if e[0] in vecs]

    from bioscan import aesthetic_fit as fit
    from bioscan.service.adapters import siglip2

    assert aes.EMBEDDING == f"{siglip2.MODEL_ID}@{siglip2.REVISION[:12]}", "aesthetic.EMBEDDING is stale"
    X = fit.np.asarray([vecs[p] for p, _, _ in eva])
    y = fit.np.asarray([s for _, s, _ in eva])
    prov = {**aes.EVA_PROVENANCE, "date": time.strftime("%Y-%m-%d", time.gmtime()),
            "vectors": "in-process bioscan.service decode + Engine.frame (scripts/train_aesthetic_head.py)",
            "votes_per_image_min": min(n for _, _, n in eva), "limit": a.limit}
    doc, summary = fit.train_head(X, y, name=a.name, lo=0.0, hi=10.0, target="EVA mean general score, 0-10",
                                  provenance=prov, k=a.folds, seed=a.seed)
    path = aes.write_head(doc, a.out)
    aes.load_head(path)                                  # the file we wrote validates
    cv = summary["cv"]
    print(f"head {doc['name']}:{doc['sha'][:12]} -> {path} ({path.stat().st_size / 1024:.0f} KiB)")
    print(f"EVA n {summary['n']}, alpha {summary['alpha']:g}: {cv['k']}-fold CV SRCC {cv['srcc']:.4f} "
          f"(sd {cv['srcc_sd']:.4f}), PLCC {cv['plcc']:.4f}; embed {t_embed:.0f} s, fit {summary['fit_s']:.1f} s")
    print("alphas: " + ", ".join(f"{t['alpha']:g} SRCC {t['srcc_mean']:.4f}" for t in cv["alphas"]))
    if a.print_base64:
        data = path.read_bytes()
        print(f"===== BEGIN {path.name} base64 =====")
        print(base64.encodebytes(data).decode("ascii"), end="")
        print(f"===== END {path.name} base64 =====")
        print(f"sha256(file) {hashlib.sha256(data).hexdigest()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
