"""Manual smoke: POST every ARW in tests/smoke/ to a running service with want=[identify,embed,jpg],
check the acceptance fields and print a markdown table. Not collected by pytest.

    uv run bioscan-serve &   # then:
    uv run python tests/smoke/run_smoke.py [--url http://127.0.0.1:8765]
"""
import argparse
import json
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

here = Path(__file__).resolve().parent
ap = argparse.ArgumentParser()
ap.add_argument("--url", default="http://127.0.0.1:8765")
args = ap.parse_args()

paths = sorted(str(p) for p in here.iterdir() if p.suffix.lower() == ".arw")
out_dir = tempfile.mkdtemp(prefix="bioscan-smoke-")
body = {"inputs": [{"path": p} for p in paths], "want": ["identify", "embed", "jpg"], "options": {"jpg": {"out_dir": out_dir}}}
req = urllib.request.Request(args.url + "/run", json.dumps(body).encode(), {"Content-Type": "application/json"})
t0 = time.perf_counter()
rows, problems = [], []
with urllib.request.urlopen(req) as resp:
    for line in resp:
        ev = json.loads(line)
        if ev["type"] == "error":
            problems.append(f"{ev['path']}: {ev['product']}: {ev['message']}")
        elif ev["type"] == "done":
            done = ev
        elif ev["type"] == "result":
            p = ev["products"]
            boxes = p["identify"]["boxes"]
            best = max(boxes, key=lambda b: b["score"]) if boxes else None
            sp = best and best.get("species")
            if not boxes:
                problems.append(f"{ev['path']}: no boxes")
            if boxes and not (sp and sp["level"]):
                problems.append(f"{ev['path']}: no species level")
            if p["embed"]["dim"] != 768:
                problems.append(f"{ev['path']}: embed dim {p['embed']['dim']}")
            if not Path(p["jpg"]["path"]).is_file():
                problems.append(f"{ev['path']}: jpg missing")
            rows.append((Path(ev["path"]).name, p["identify"]["gate"]["class"], len(boxes),
                         sp["level"] if sp else "-", sp["top"][0]["scientific"] if sp else "-",
                         f"{sp['top'][0]['posterior']:.3f}" if sp else "-", ev["timing_ms"]))
print("| file | gate.class | boxes | level | top-1 | posterior | decode | identify | embed | jpg |")
print("|---|---|---|---|---|---|---|---|---|---|")
for name, cls, n, level, sci, post, t in sorted(rows):
    print(f"| {name} | {cls} | {n} | {level} | {sci} | {post} | {t['decode']:.0f} | {t['identify']:.0f} | {t['embed']:.0f} | {t['jpg']:.0f} |")
print(f"\nok={done['ok']} failed={done['failed']} elapsed_ms={done['elapsed_ms']:.0f} wall_s={time.perf_counter() - t0:.1f}")
for p in problems:
    print("PROBLEM:", p)
sys.exit(1 if problems or done["failed"] else 0)
