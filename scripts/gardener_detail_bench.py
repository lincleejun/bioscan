"""Gardener browse timings (AfterShooting PRD §14, contract revision 5C): hot-cache preview switches
(GET /assets/{id}/large, target P95 <= 150 ms) and original-pixel detail crops (GET /assets/{id}/detail).

    uv run python scripts/gardener_detail_bench.py                 # synthetic 24 MP JPEGs
    uv run python scripts/gardener_detail_bench.py --photos DIR    # a real folder (RAW included)

Runs the real service in-process (TestClient: no network, no browser decode) on a throw-away data
dir; only reads the photos. Prints medians and P95 with the machine it ran on (PRD §14 asks for the
baseline to name CPU, system, image size and cache state)."""
from __future__ import annotations

import argparse
import os
import platform
import statistics
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

from bioscan.gardener.api import create_gardener_app

API = "/api/v1"


def synthetic(folder: Path, n: int, size: tuple[int, int]) -> None:
    """Photo-like JPEGs (gradient + grain, ~q90) of `size`, half of them stored with orientation 6."""
    rng = np.random.default_rng(0)
    w, h = size
    base = np.linspace(40, 210, w, dtype=np.float32)[None, :, None] + np.linspace(0, 30, h, dtype=np.float32)[:, None, None]
    for i in range(n):
        px = np.clip(base + rng.normal(0, 9, (h, w, 3)).astype(np.float32) + i, 0, 255).astype(np.uint8)
        exif = Image.Exif()
        if i % 2:
            exif[0x0112] = 6
        Image.fromarray(px).save(folder / f"SYN{i:04d}.jpg", "JPEG", quality=90, exif=exif)


def timed(c: TestClient, url: str, **params) -> tuple[float, int]:
    t0 = time.perf_counter()
    r = c.get(url, params=params)
    ms = (time.perf_counter() - t0) * 1000
    if r.status_code != 200:
        raise SystemExit(f"{url}: {r.status_code} {r.text[:200]}")
    return ms, len(r.content)


def report(name: str, ms: list[float], target: float | None = None) -> None:
    p95 = statistics.quantiles(ms, n=20)[-1] if len(ms) >= 2 else ms[0]
    verdict = "" if target is None else ("  (target ≤ %.0f ms: %s)" % (target, "met" if p95 <= target else "NOT met"))
    print(f"{name:<44} n={len(ms):<4} median {statistics.median(ms):8.1f} ms   P95 {p95:8.1f} ms{verdict}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--photos", type=Path, help="a folder of real photos (default: synthetic JPEGs)")
    ap.add_argument("-n", type=int, default=12, help="synthetic photos (default 12)")
    ap.add_argument("--size", default="6000x4000", help="synthetic size WxH (default 6000x4000, 24 MP)")
    ap.add_argument("--rounds", type=int, default=10, help="preview switch rounds over all photos (default 10)")
    args = ap.parse_args()
    with tempfile.TemporaryDirectory(prefix="gardener-bench-") as tmp:
        src = args.photos.resolve() if args.photos else Path(tmp) / "photos"
        if not args.photos:
            src.mkdir()
            w, h = (int(x) for x in args.size.split("x"))
            print(f"making {args.n} synthetic {w}x{h} JPEGs ...", file=sys.stderr)
            synthetic(src, args.n, (w, h))
        app = create_gardener_app(Path(tmp) / "data", name_lists=None)
        with TestClient(app, base_url="http://127.0.0.1:8770") as c:
            t = c.post(f"{API}/tasks", json={"title": "bench", "sources": [str(src)], "capabilities": []}).json()
            t0 = time.perf_counter()
            while c.get(f"{API}/tasks/{t['id']}").json()["scan"]["status"] not in ("completed", "partial", "failed"):
                time.sleep(0.2)
            items = c.get(f"{API}/assets", params={"task": t["id"], "limit": 1000}).json()["items"]
            photos = [a for a in items if a["media"] == "photo" and a["preview"] == "ready"]
            print(f"scan + previews: {len(photos)} photos in {time.perf_counter() - t0:.1f} s", file=sys.stderr)
            if not photos:
                raise SystemExit("no previewable photos")
            sizes = sorted({f"{a['width']}x{a['height']}" for a in photos if a["width"]})
            raws = [a for a in photos if any(f["role"] == "raw" for f in c.get(f"{API}/assets/{a['id']}").json()["files"])]
            print(f"machine: {platform.platform()} · {platform.processor() or platform.machine()} · {os.cpu_count()} CPUs · "
                  f"Python {platform.python_version()}")
            print(f"photos: {len(photos)} ({len(raws)} with RAW) · sizes {', '.join(sizes[:4])}"
                  f"{' …' if len(sizes) > 4 else ''} · source {'synthetic' if not args.photos else src}")
            print("in-process HTTP (TestClient): excludes network and browser decode\n")

            for a in photos:                                   # warm: OS page cache + preview files exist
                timed(c, f"{API}/assets/{a['id']}/large")
            switch = [timed(c, f"{API}/assets/{a['id']}/large")[0] for _ in range(args.rounds) for a in photos]
            report("preview switch, hot cache (large 1600 px)", switch, 150)
            report("thumbnail, hot cache (360 px)",
                   [timed(c, f"{API}/assets/{a['id']}/thumb")[0] for _ in range(args.rounds) for a in photos])

            jpegs = [a for a in photos if a not in raws]
            for label, group in (("JPEG", jpegs), ("RAW", raws)):
                if not group:
                    print(f"{label}: none in this set (unmeasured)")
                    continue
                first = [timed(c, f"{API}/assets/{a['id']}/detail", xyxy="0.4,0.4,0.6,0.6", max=2048)[0] for a in group]
                again = [timed(c, f"{API}/assets/{a['id']}/detail", xyxy="0.3,0.3,0.5,0.5", max=2048)[0]
                         for _ in range(3) for a in group]
                full = [timed(c, f"{API}/assets/{a['id']}/detail", xyxy="0,0,1,1", max=2048)[0] for a in group]
                tag = "first request (decode" + (" + cache write)" if label == "RAW" else ")")
                report(f"{label} detail 20 % crop, {tag}", first)
                report(f"{label} detail 20 % crop, again" + (" (cached decode)" if label == "RAW" else ""), again)
                report(f"{label} detail whole frame -> 2048 px", full)


if __name__ == "__main__":
    main()
