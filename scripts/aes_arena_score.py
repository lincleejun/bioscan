"""Score a golden folder with an external aesthetic model: one NDJSON line per frame for `bench aesthetic score`.

    python scripts/aes_arena_score.py MODEL GOLDEN --out SCORES.ndjson [--device mps] [--purge]

MODEL is one of
  pyiqa:<metric>      any pyiqa metric (topiq_iaa, laion_aes, nima, musiq-ava, clipiqa+, ...)
  pyiqa:qrealign      Q-ReAlign Mini 0.8B, aesthetic task
  aessiglip           somepago/AestheticSigLIP (SigLIP2 so400m + tap MLP)
  v25                 aesthetic-predictor-v2-5 (SigLIP v1 so400m)
  vlm:<hf repo>       zero-shot VLM (Qwen3-VL / Qwen3.5): expected digit 1-9 from the next-token logits

Runs in its own environment, never in the service's: these models pull pyiqa (PolyForm NC), scipy, timm and
a few GB of weights. Tested 2026-09-24 with torch 2.14, transformers 5.17, open_clip 3.3, pyiqa 0.1.16,
setuptools<81 (openai-clip still imports pkg_resources). docs/research/2026-09-24-aesthetic-arena.md.
Output: {"path", "score", "model", "ms"} per frame, read directly by `bioscan bench aesthetic score`.
`--purge` deletes what this run downloaded (new Hugging Face hub repos, new files under ~/.cache/torch/hub and
~/.cache/clip) once the scores are written: arena weights live only for the run; the service's own models are
never touched because they were there before.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

VLM_PROMPT = ("Rate the aesthetic quality of this photo on a scale from 1 (very poor) to 9 (excellent). "
              "Answer with a single digit.")


def pyiqa_scorer(metric: str, device: str):
    import pyiqa
    import torch
    m = pyiqa.create_metric(metric, device=torch.device(device))
    kw = {"task_": "aesthetic"} if metric == "qrealign" else {}
    return lambda p: float(m(p, **kw))


def aessiglip_scorer(device: str):
    from huggingface_hub import snapshot_download
    sys.path.insert(0, snapshot_download("somepago/AestheticSigLIP"))
    from predict import AestheticScorer  # type: ignore[import-not-found]
    sc = AestheticScorer.from_pretrained("somepago/AestheticSigLIP", device=device)
    return lambda p: float(sc.rate(p))


def v25_scorer(device: str):
    import torch
    from aesthetic_predictor_v2_5 import convert_v2_5_from_siglip  # type: ignore[import-not-found]
    from PIL import Image
    model, pre = convert_v2_5_from_siglip(low_cpu_mem_usage=True)
    model = model.to(device).eval()

    def f(p):
        px = pre(images=Image.open(p).convert("RGB"), return_tensors="pt").pixel_values.to(device)
        with torch.inference_mode():
            return float(model(px).logits.squeeze())
    return f


def vlm_scorer(repo: str, device: str):
    import torch
    from PIL import Image
    from transformers import AutoModelForImageTextToText, AutoProcessor
    proc = AutoProcessor.from_pretrained(repo)
    model = AutoModelForImageTextToText.from_pretrained(repo, dtype=torch.bfloat16).to(device).eval()
    ids = [proc.tokenizer.encode(str(i), add_special_tokens=False)[0] for i in range(1, 10)]
    w = torch.arange(1, 10, dtype=torch.float32)

    def f(p):
        msgs = [{"role": "user", "content": [{"type": "image"}, {"type": "text", "text": VLM_PROMPT}]}]
        text = proc.apply_chat_template(msgs, add_generation_prompt=True, enable_thinking=False)
        inp = proc(text=[text], images=[Image.open(p).convert("RGB")], return_tensors="pt").to(device)
        with torch.inference_mode():
            lg = model(**inp).logits[0, -1, ids].float().cpu()
        return float((lg.softmax(-1) * w).sum())
    return f


def scorer(model: str, device: str):
    if model.startswith("pyiqa:"):
        return pyiqa_scorer(model[6:], device)
    if model.startswith("vlm:"):
        return vlm_scorer(model[4:], device)
    return {"aessiglip": aessiglip_scorer, "v25": v25_scorer}[model](device)


def _cache_state() -> tuple[set[str], set[Path]]:
    """Hugging Face repos and torch-hub / clip files present now."""
    from huggingface_hub import scan_cache_dir
    try:
        repos = {r.repo_id for r in scan_cache_dir().repos}
    except Exception:  # noqa: BLE001 - no cache dir yet
        repos = set()
    files = set()
    for d in (Path.home() / ".cache/torch/hub", Path.home() / ".cache/clip"):
        if d.is_dir():
            files |= {p for p in d.rglob("*") if p.is_file()}
    return repos, files


def purge(before: tuple[set[str], set[Path]]) -> None:
    import shutil

    from huggingface_hub import scan_cache_dir
    repos, files = _cache_state()
    cache = scan_cache_dir()
    new = {r.repo_id for r in cache.repos} - before[0]
    revs = [rv.commit_hash for r in cache.repos if r.repo_id in new for rv in r.revisions]
    if revs:
        st = cache.delete_revisions(*revs)
        st.execute()
        print(f"purged {sorted(new)} ({st.expected_freed_size / 1e9:.1f} GB)", file=sys.stderr)
    for f in sorted(files - before[1]):
        f.unlink(missing_ok=True)
        print(f"purged {f}", file=sys.stderr)
        d = f.parent
        while d != d.parent and d.is_dir() and not any(d.iterdir()):
            shutil.rmtree(d, ignore_errors=True)
            d = d.parent


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("model")
    ap.add_argument("golden", help="golden folder (images.csv with a path column)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--device", default="mps")
    ap.add_argument("--purge", action="store_true", help="delete the weights this run downloaded once scores are written")
    a = ap.parse_args()
    before = _cache_state() if a.purge else None
    root = Path(a.golden)
    with open(root / "images.csv", newline="", encoding="utf-8-sig") as f:
        paths = [str((root / r["path"]).resolve()) for r in csv.DictReader(f)]
    f = scorer(a.model, a.device)
    f(paths[0])  # warm-up
    n = 0
    with open(a.out, "w", encoding="utf-8") as out:
        for p in paths:
            t = time.perf_counter()
            try:
                s = f(p)
            except Exception as e:  # noqa: BLE001 - a failed frame is a missing score, never a crash
                print(f"{p}: {e}", file=sys.stderr)
                s = None
            out.write(json.dumps({"path": p, "score": s, "model": a.model,
                                  "ms": round((time.perf_counter() - t) * 1000, 1)}) + "\n")
            n += 1
    print(f"{n} frames -> {a.out}")
    if before is not None:
        purge(before)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
