"""Three-way compare: folder GT vs codex vs bioscan (baseline no-geo, and geo run). Usage: compare2.py sample.csv codex.jsonl geo_preds.ndjson"""
import csv, json, sys
def norm(s): return (s or "").strip().lower()
rows = list(csv.DictReader(open(sys.argv[1])))
codex = {json.loads(l)["idx"]: json.loads(l)["codex"] for l in open(sys.argv[2]) if l.strip()}
geo = {}
for l in open(sys.argv[3]):
    if not l.strip(): continue
    r = json.loads(l)
    if r.get("type") != "result": continue
    boxes = r["products"]["identify"]["boxes"]
    best = max(boxes, key=lambda b: b["score"]) if boxes else None
    sp = best["species"] if best and best.get("species") else None
    geo[r["path"]] = (sp["top"][0]["scientific"], sp["level"]) if sp and sp["top"] else ("", "none")
stats = {"base_vs_codex":0, "geo_vs_codex":0, "base_vs_gt":0, "geo_vs_gt":0, "codex_vs_gt":0}
n = 0; out = []
for r in rows:
    c = codex.get(r["idx"], {}); cs = norm(c.get("scientific")); alts = [norm(a) for a in c.get("alternatives", [])]
    gs = norm(r["gt_scientific"]); bs = norm(r["pred_scientific"]); gsci, glvl = geo.get(r["arw_path"], ("", "?")); gsn = norm(gsci)
    hit = lambda p: bool(p) and (p == cs or p in alts)
    n += 1
    stats["base_vs_codex"] += hit(bs); stats["geo_vs_codex"] += hit(gsn)
    stats["base_vs_gt"] += bs == gs; stats["geo_vs_gt"] += gsn == gs; stats["codex_vs_gt"] += cs == gs
    out.append(f"{r['idx']:>3} gt={r['gt_scientific']:<22} base={r['pred_scientific']:<24}({r['pred_level'][:4]}) geo={gsci:<24}({glvl[:4]}) codex={c.get('scientific','?'):<22} c={c.get('confidence','?')} {'OK' if hit(gsn) else 'X'}")
print("\n".join(out)); print()
for k, v in stats.items(): print(f"{k:<14} {v}/{n} = {v/n:.1%}")
