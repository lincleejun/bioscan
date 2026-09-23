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
from bioscan.service import names  # noqa: E402

MODEL_ID = "hf-hub:imageomics/bioclip-2.5-vith14"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache-dir", type=Path, default=names.CACHE_DIR)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    import open_clip
    import torch
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    t0 = time.perf_counter()
    model, _, _ = open_clip.create_model_and_transforms(MODEL_ID, device=device)
    model.eval()
    tokenizer = open_clip.get_tokenizer(MODEL_ID)
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
