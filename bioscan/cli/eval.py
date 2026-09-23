"""`bioscan eval`: run identify over a ground-truth CSV and report spec section 8 metrics."""
import csv
import hashlib
import json
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

from bioscan import contract, naming
from bioscan.cli import client

SYNONYMS_CSV = naming.SYNONYMS_CSV
TRUTH_SOURCES = ("inat", "spelling")   # synonyms.csv sources that rename a ground-truth label
PREDS_SCHEMA = 1   # preds.ndjson: a "meta" line, then result/error lines, then the service's "done"


def read_gt(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        return [r for r in csv.DictReader(f) if r.get("path")]


def normalise_truth(rows: list[dict], synonyms: list[dict]) -> list[dict]:
    """Truth labels renamed to the AviList/MDD name (inat + spelling synonyms); the original stays
    in `scientific_raw`. ponytail: aliases apply regardless of where the photo was taken; add a
    region column to synonyms.csv if a non-Americas set ever needs Circus cyaneus kept."""
    to_list = {naming.norm_binomial(r["alias"]): r["avilist_scientific"].strip()
               for r in synonyms if r["source"].strip() in TRUTH_SOURCES}
    return [{**r, "scientific_raw": r.get("scientific", ""),
             "scientific": to_list.get(naming.norm_binomial(r.get("scientific") or ""), r.get("scientific", ""))}
            for r in rows]


def load_preds(lines) -> dict[str, dict]:
    """NDJSON lines -> path -> event. A `result` wins over `error`s for the same path."""
    preds: dict[str, dict] = {}
    for line in lines:
        line = line.strip()
        if not line:
            continue
        ev = json.loads(line)
        t = ev.get("type")
        if t == contract.RESULT or (t == contract.ERROR and preds.get(ev.get("path"), {}).get("type") != contract.RESULT):
            preds[ev["path"]] = ev
    return preds


def _sha256(path) -> str | None:
    p = Path(path)
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None


def read_preds_meta(path) -> dict:
    """The first line of a preds file when it is a meta line ({} for files written before schema 1)."""
    with open(path, "rb") as f:
        first = f.readline().strip()
    try:
        ev = json.loads(first) if first else {}
    except json.JSONDecodeError:
        return {}
    return ev if ev.get("type") == "meta" else {}


def preds_complete(path) -> bool:
    """True when the service's `done` event made it into the file (schema 1 files only)."""
    with open(path, "rb") as f:
        return any(json.loads(line).get("type") == contract.DONE for line in f if line.strip())


def _sci(s) -> str:
    """The same key synonyms are looked up with, so labels differing only in '-' or case match."""
    return naming.norm_binomial(s)


def outcome(truth: dict, ev: dict | None) -> dict:
    """Per-image scoring. Missing/errored predictions count as misses on every metric."""
    kind, sci = truth.get("kind", ""), _sci(truth.get("scientific"))
    o = {"gate": False, "detected": False, "top1": False, "top5": False, "species_level": False,
         "pred": "(error)" if ev else "(missing)", "decode_ms": None, "identify_ms": None}
    if not ev or ev.get("type") != contract.RESULT:
        return o
    t = ev.get("timing_ms") or {}
    o["decode_ms"], o["identify_ms"] = t.get("decode"), t.get("identify")
    ident = (ev.get("products") or {}).get("identify") or {}
    boxes = ident.get("boxes") or []
    o["gate"] = (ident.get("gate") or {}).get("class") == kind
    o["detected"] = any(b.get("kind") == kind for b in boxes)
    if not boxes:
        o["pred"] = "(no box)"
        o["no_box_gate"] = (ident.get("gate") or {}).get("class") or "?"
        return o
    best = max(boxes, key=lambda b: b.get("score", 0))
    sp = best.get("species")
    top = (sp or {}).get("top") or []
    if not top:
        o["pred"] = "(no species)"
        return o
    names = [_sci(t.get("scientific")) for t in top]
    o["pred"] = top[0].get("scientific", "")
    o["top1"] = names[0] == sci
    o["top5"] = sci in names[:5]
    o["species_level"] = sp.get("level") == "species"
    return o


def _rate(num, den):
    return num / den if den else None


def _ms(vals):
    vals = [v for v in vals if isinstance(v, (int, float))]
    return (statistics.mean(vals), statistics.median(vals)) if vals else (None, None)


def compute(gt_rows: list[dict], preds: dict[str, dict]) -> dict[tuple[str, str], dict]:
    """Group by (tier, kind) and compute every spec-8 metric."""
    groups: dict[tuple[str, str], list[tuple[dict, dict]]] = defaultdict(list)
    for r in gt_rows:
        groups[(r.get("tier") or "?", r.get("kind") or "?")].append((r, outcome(r, preds.get(r["path"]))))
    out = {}
    for key, items in sorted(groups.items()):
        n = len(items)
        os_ = [o for _, o in items]
        sp = [o for o in os_ if o["species_level"]]
        conf = Counter((r.get("scientific") or "(blank)", o["pred"]) for r, o in items if not o["top1"])
        out[key] = {
            "n": n,
            "failed": sum(o["pred"] in ("(error)", "(missing)") for o in os_),
            "gate_acc": _rate(sum(o["gate"] for o in os_), n),
            "detect_rate": _rate(sum(o["detected"] for o in os_), n),
            "top1": _rate(sum(o["top1"] for o in os_), n),
            "top5": _rate(sum(o["top5"] for o in os_), n),
            "coverage": _rate(len(sp), n),
            "precision": _rate(sum(o["top1"] for o in sp), len(sp)),
            "confusion": [(t, p, c) for (t, p), c in conf.most_common(10)],
            # why there was no box: the whole-frame gate class of those images (none/person = gate miss)
            "no_box_gate": dict(Counter(o["no_box_gate"] for o in os_ if "no_box_gate" in o).most_common()),
            # Top-1 hits that only exist because the truth label was renamed (a miss on the raw label)
            "synonym_hits": sum(o["top1"] and _sci(r.get("scientific_raw", r.get("scientific"))) != _sci(r.get("scientific"))
                                for r, o in items),
            "decode_ms": _ms(o["decode_ms"] for o in os_),
            "identify_ms": _ms(o["identify_ms"] for o in os_),
        }
    return out


def _pct(x):
    return "–" if x is None else f"{x * 100:.1f}%"


def _msf(pair):
    mean, med = pair
    return "–" if mean is None else f"{mean:.0f} / {med:.0f}"


def report_md(metrics: dict, meta: dict) -> str:
    lines = ["# bioscan eval report", ""]
    lines += [f"- {k}: {v}" for k, v in meta.items()]
    lines += ["", "## Metrics by tier × kind", "",
              "| tier | kind | n | failed | gate acc | detect | Top-1 | Top-5 | coverage | precision | decode ms (mean/median) | identify ms (mean/median) |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for (tier, kind), m in metrics.items():
        lines.append(f"| {tier} | {kind} | {m['n']} | {m['failed']} | {_pct(m['gate_acc'])} | {_pct(m['detect_rate'])} | "
                     f"{_pct(m['top1'])} | {_pct(m['top5'])} | {_pct(m['coverage'])} | {_pct(m['precision'])} | "
                     f"{_msf(m['decode_ms'])} | {_msf(m['identify_ms'])} |")
    lines += ["", "Definitions: Top-1/Top-5 use the highest-`score` box; coverage = best box at `level == species`; "
              "precision = Top-1 hit rate among those; failed images count as misses everywhere.", "",
              "Top-1 hits gained by synonym normalisation of the truth (miss -> hit): "
              + ", ".join(f"{t}/{k} {m['synonym_hits']}" for (t, k), m in metrics.items()), "",
              "No box, by whole-frame gate class (none/person: the gate missed the animal; else the detector did): "
              + "; ".join(f"{t}/{k} " + (", ".join(f"{g} {c}" for g, c in m["no_box_gate"].items()) or "0")
                          for (t, k), m in metrics.items()), ""]
    for (tier, kind), m in metrics.items():
        lines += [f"## Confusion Top-10 — {tier} / {kind}", ""]
        if not m["confusion"]:
            lines += ["(no misses)", ""]
            continue
        lines += ["| truth | predicted | count |", "|---|---|---|"]
        lines += [f"| {t} | {p} | {c} |" for t, p, c in m["confusion"]]
        lines.append("")
    return "\n".join(lines)


def run_eval(gt_csv: str, out_dir: str, no_geo: bool, url: str, preds_file: str | None = None,
             synonyms: bool = True) -> tuple[str, bool]:
    """(report markdown, complete). complete is False when the prediction stream ended without
    the service's `done` (service died mid-run): the missing images score as misses."""
    rows = read_gt(gt_csv)
    if synonyms:
        rows = normalise_truth(rows, naming.read_synonyms(SYNONYMS_CSV))
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    preds_path = Path(preds_file) if preds_file else out / "preds.ndjson"
    t0 = time.monotonic()
    if not preds_file:
        client.health(url)  # fail fast with a clear message if the service is down
        inputs = []
        for r in rows:
            inp = {"path": r["path"]}
            for k in ("lat", "lon"):
                if r.get(k):
                    inp[k] = float(r[k])
            if r.get("taken_at"):
                inp["taken_at"] = r["taken_at"]
            inputs.append(inp)
        payload = {"inputs": inputs, "want": ["identify"], "options": {"identify": {"top_k": 5, "geo": not no_geo}}}
        n = 0
        head = {"type": "meta", "schema": PREDS_SCHEMA, "options": payload["options"], "groundtruth": gt_csv,
                "groundtruth_sha256": _sha256(gt_csv), "synonyms_sha256": _sha256(SYNONYMS_CSV) if synonyms else None,
                "created": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
        with open(preds_path, "wb") as f:
            f.write(json.dumps(head).encode() + b"\n")
            for line in client.run(payload, url):
                if json.loads(line).get("type") in (contract.RESULT, contract.ERROR, contract.DONE):  # no progress
                    f.write(line + b"\n")
                    n += 1
                    print(f"\r{n}/{len(rows)}", end="", file=sys.stderr, flush=True)
        print(file=sys.stderr)
    preds_meta = read_preds_meta(preds_path)
    complete = preds_complete(preds_path) if preds_meta else True   # older files cannot tell
    with open(preds_path, "rb") as f:
        preds = load_preds(f)
    metrics = compute(rows, preds)
    geo = (preds_meta.get("options") or {}).get("identify", {}).get("geo")
    meta = {"groundtruth": gt_csv, "preds": str(preds_path), "images": len(rows),
            "geo": geo if geo is not None else f"{not no_geo} (from the command line; preds file has no meta line)",
            "preds schema": preds_meta.get("schema", "none (pre-schema file)"), "complete": complete,
            "synonyms": str(SYNONYMS_CSV) if synonyms else "off", "generated": time.strftime("%Y-%m-%d %H:%M:%S"), "wall_s": f"{time.monotonic() - t0:.1f}"}
    engines = {json.dumps(e.get("engine"), sort_keys=True) for e in preds.values() if e.get("engine")}
    if engines:
        meta["engine"] = " | ".join(sorted(engines))
    report = report_md(metrics, meta)
    (out / "report.md").write_text(report)
    return report, complete
