"""Ask codex (gpt-5.6-sol, medium) to identify each sample JPG; never opens images here.
Usage: python codex_verify.py sample.csv out.jsonl
"""
import csv, json, subprocess, sys, re, time
from pathlib import Path

PROMPT = (
    "You are a bird and mammal identification expert. Look at the attached photo and identify the "
    "animal species. Answer ONLY with a JSON object on a single line, no prose, no markdown: "
    '{"common": "<English common name>", "scientific": "<Genus species>", "confidence": <0-1>, '
    '"alternatives": ["<scientific 2>", "<scientific 3>"]}. '
    "If no animal is visible, use common \"none\" and scientific \"none\"."
)

def ask(jpg: str) -> dict:
    cmd = ["codex", "exec", "-m", "gpt-5.6-sol", "-c", 'model_reasoning_effort="medium"',
           "-s", "read-only", "--skip-git-repo-check", "-i", jpg, "--", PROMPT]
    for attempt in range(3):
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        text = p.stdout
        m = list(re.finditer(r"\{.*?\}", text, re.S))
        for cand in reversed(m):
            try:
                d = json.loads(cand.group(0))
                if "scientific" in d:
                    return d
            except json.JSONDecodeError:
                continue
        time.sleep(2)
    return {"common": "?", "scientific": "?", "confidence": 0, "raw": text[-500:]}

def main(sample_csv, out_path):
    out = Path(out_path)
    done = {}
    if out.exists():
        for line in out.read_text().splitlines():
            if line.strip():
                r = json.loads(line); done[r["idx"]] = r
    rows = list(csv.DictReader(open(sample_csv)))
    with out.open("a") as f:
        for r in rows:
            if r["idx"] in done:
                continue
            ans = ask(r["jpg_path"])
            rec = {"idx": r["idx"], "jpg": r["jpg_path"], "codex": ans}
            f.write(json.dumps(rec, ensure_ascii=False) + "\n"); f.flush()
            print(r["idx"], ans.get("scientific"), ans.get("confidence"), flush=True)

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
