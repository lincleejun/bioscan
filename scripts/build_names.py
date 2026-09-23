"""Build (or load from cache) the bird and mammal name matrices and print stats().

    uv run python scripts/build_names.py [--cache-dir DIR]
"""
import argparse
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bioscan.service import engine, names  # noqa: E402
from bioscan.service.adapters.bioclip import BioCLIP  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache-dir", type=Path, default=names.CACHE_DIR)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    device = engine.pick_device()
    t0 = time.perf_counter()
    bioclip = BioCLIP(device)                 # the pinned revision the service uses
    model, tokenizer = bioclip.model, bioclip.tokenizer
    print(f"model load {time.perf_counter() - t0:.1f}s on {device}")

    for label in ("first", "second"):
        t0 = time.perf_counter()
        lists = names.load_lists(model, tokenizer, device, args.cache_dir)
        print(f"load_lists ({label}) {time.perf_counter() - t0:.2f}s")
    print(json.dumps(names.stats(lists), indent=2))
    cache = Path(args.cache_dir).expanduser()
    for p in sorted(cache.glob(f"{names.MODEL_NAME}-*.npz")):
        print(f"{p}  {p.stat().st_size / 2**20:.1f} MiB")


if __name__ == "__main__":
    main()
