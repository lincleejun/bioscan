"""`bioscan summarize PREDS --out DIR` and `bioscan report DIR`: the run summary
(docs/research/2026-09-24-report-design.md, docs/usage.md).

`summarize` is a whole-run, model-free pass over a saved preds file (`bioscan run --json`, `cull --json`)
that writes `summary.json`: what the run found per gate class, one row per named taxon, and the review
queue with fixed rule reasons. `report` renders that file, and only that file, to an HTML page with
cull's stylesheet and photo tiles. Standard library only, like the rest of the CLI."""
from __future__ import annotations

import hashlib
import html
import json
from pathlib import Path
from typing import Any

from bioscan import contract, cull
from bioscan.cli import cull as cull_cli

SCHEMA = 1
KIND = "bioscan.summary"
CLASSES = ("bird", "mammal", "other_animal", "person", "none")    # gate classes, in the summary's order
ANIMALS = CLASSES[:3]
RANGE_EPS = 0.01                   # = bioscan.service.rules.RANGE_EPS (numpy there; tests hold the two together)
SINGLE_SIGHTING_MAX_POSTERIOR = 0.8
TOP = 3                            # candidates kept per member / review item
LEVEL_RANK = {"species": 6, "genus": 5, "family": 4}               # taxonomy index of the name at each level
REASONS = {   # code -> what the page says
    "unconfirmed": "no name held up",
    "coarse_level": "named only to genus or family; a person can often finish it",
    "out_of_range": "first candidate is outside its known range (vagrant, or a wrong place)",
    "no_list": "no name list for this kind yet",
    "gate_no_box": "the frame looks like an animal but no box survived",
    "single_sighting": "the only box of its taxon in the run, and not sure",
}


# ---- summarize --------------------------------------------------------------------------------

def _name(sp: dict[str, Any] | None) -> str | None:
    """The taxon a box counts under: the first candidate's name at the box's level; None when unconfirmed."""
    top, level = contract.top_of(sp), contract.level_of(sp)
    if not top or level not in LEVEL_RANK:
        return None
    return top[0]["taxonomy"][LEVEL_RANK[level]] if level != "species" else top[0]["scientific"]


def _span(times: list[str | None]) -> tuple[str | None, str | None]:
    known = sorted(t for t in times if t)
    return (known[0], known[-1]) if known else (None, None)


def summarize(events: list[dict[str, Any]], preds: str = "", preds_sha256: str | None = None) -> dict[str, Any]:
    """The summary (schema 1) of one run's result/error/done events. Deterministic: same events, same dict."""
    results = [e for e in events if e.get("type") == contract.RESULT]
    done = next((e for e in events if e.get("type") == contract.DONE), {})
    fails = cull_cli.failed(events)
    images = {c: 0 for c in CLASSES}
    boxes_by_kind = {c: 0 for c in ANIMALS}
    taxa: dict[str, dict[str, Any]] = {}
    review: list[dict[str, Any]] = []
    for ev in results:
        ident = contract.identify_of(ev)
        gate = contract.gate_class_of(ident)
        if gate in images:
            images[gate] += 1
        taken = cull.capture(ev)[0]
        jpg = ((ev.get("products") or {}).get("jpg") or {}).get("path")
        where = {"sha256": ev.get("sha256"), "path": ev["path"], "jpg": jpg, "taken_at": taken}
        bxs = contract.boxes_of(ident)
        if gate in ANIMALS and not bxs:
            review.append({**where, "box": None, "kind": gate, "level": None, "reasons": ["gate_no_box"],
                           "suggested": None, "top": []})
        for b in bxs:
            boxes_by_kind[b["kind"]] = boxes_by_kind.get(b["kind"], 0) + 1
            if "species" not in b:                  # species off: nothing to name, nothing to review
                continue
            sp = contract.species_of(b)
            top, level, name = contract.top_of(sp), contract.level_of(sp), _name(sp)
            reasons = []
            if sp is None:
                reasons.append("no_list")
            elif level == "unconfirmed":
                reasons.append("unconfirmed")
            elif level in ("genus", "family"):
                reasons.append("coarse_level")
            if top and top[0].get("p_geo") is not None and top[0]["p_geo"] < RANGE_EPS:
                reasons.append("out_of_range")
            item = {**where, "box": b["id"], "kind": b["kind"], "level": level, "reasons": reasons,
                    "suggested": name or (top[0]["scientific"] if top else None), "top": top[:TOP]}
            if name:
                p = top[0]["posterior"]
                t = taxa.setdefault(name, {
                    "name": name, "common": top[0].get("common") if level == "species" else None, "level": level,
                    "kind": b["kind"], "taxonomy": top[0]["taxonomy"][:LEVEL_RANK[level] + 1], "list": sp.get("list"),
                    "members": [], "_sharp": []})
                t["members"].append({**where, "box": b["id"], "posterior": p, "top": top[:TOP]})
                t["_sharp"].append((b.get("quality") or {}).get("sharpness") or 0.0)
                t["_item"] = item                   # the last one; only read when the taxon has one box
            if reasons:
                review.append(item)
    rows = []
    for t in taxa.values():
        members, sharp, item = t["members"], t["_sharp"], t["_item"]
        ps = sorted(m["posterior"] for m in members)
        # ponytail: best = posterior x sharpness (then posterior) until C1's quality picks the frame
        best = max(range(len(members)), key=lambda i: (members[i]["posterior"] * sharp[i], members[i]["posterior"], -i))
        m = members[best]
        first, last = _span([x["taken_at"] for x in members])
        rows.append({**{k: t[k] for k in ("name", "common", "level", "kind", "taxonomy", "list")},
                     "images": len({x["sha256"] or x["path"] for x in members}), "boxes": len(members),
                     "posterior": {"max": ps[-1], "median": ps[len(ps) // 2]},
                     "best": {"sha256": m["sha256"], "path": m["path"], "jpg": m["jpg"], "box": m["box"]},
                     "members": members, "first_taken_at": first, "last_taken_at": last})
        if len(members) == 1 and m["posterior"] < SINGLE_SIGHTING_MAX_POSTERIOR:
            item["reasons"].append("single_sighting")
            if len(item["reasons"]) == 1:
                review.append(item)
    rows.sort(key=lambda t: (-t["boxes"], t["name"]))
    review.sort(key=lambda r: (r["suggested"] is None, r["suggested"] or "", r["taken_at"] or "", r["path"],
                               -1 if r["box"] is None else r["box"]))
    cats = []
    for c in CLASSES:
        row: dict[str, Any] = {"class": c, "images": images[c]}
        if c in ANIMALS:
            row |= {"boxes": boxes_by_kind[c], "taxa": sum(t["kind"] == c for t in rows),
                    "review": sum(r["kind"] == c for r in review)}
        cats.append(row)
    engine = (results[0].get("engine") or {}) if results else {}
    first, last = _span([cull.capture(e)[0] for e in results])
    return {
        "schema": SCHEMA, "kind": KIND,
        "source": {"preds": preds, "sha256": preds_sha256,
                   "engine": {k: engine.get(k) for k in ("version", "settings", "models")},
                   "first_taken_at": first, "last_taken_at": last},
        "counts": {"images": len(results) + len(fails), "ok": len(results), "failed": len(fails),
                   "boxes": sum(boxes_by_kind.values()), "elapsed_ms": done.get("elapsed_ms")},
        "categories": cats, "taxa": rows, "review": review,
        "rules": {"single_sighting_max_posterior": SINGLE_SIGHTING_MAX_POSTERIOR, "range_eps": RANGE_EPS},
        "errors": [{"path": e["path"], "product": e.get("product"), "message": e.get("message")}
                   for e in events if e.get("type") == contract.ERROR],
    }


def line(s: dict[str, Any]) -> str:
    """The one line an unattended run prints: photos, photos the gate called an animal, taxa, items to review."""
    animals = sum(c["images"] for c in s["categories"] if c["class"] in ANIMALS)
    return (f"{s['counts']['images']:,} photos · {animals:,} with animals · {len(s['taxa']):,} taxa · "
            f"{len(s['review']):,} to review")


# ---- report -----------------------------------------------------------------------------------

CSS = cull_cli.CSS + """
.strip { display: flex; height: 26px; border-radius: 6px; overflow: hidden; margin: 10px 0 4px; }
.strip span { display: flex; align-items: center; padding: 0 6px; font-size: 12px; white-space: nowrap;
  overflow: hidden; color: #fff; }
.c-bird { background: #a16207; } .c-mammal { background: #1d4ed8; } .c-other_animal { background: #7e22ce; }
.c-person { background: #57534e; } .c-none { background: #a8a29e; }
.grid.taxa { grid-template-columns: repeat(auto-fill, minmax(230px, 1fr)); }
.common { font-weight: 600; } i { font-style: italic; } .reason { color: var(--reject); }
table { border-collapse: collapse; font-size: 13px; } td, th { border: 1px solid var(--line); padding: 3px 8px;
  text-align: left; vertical-align: top; }
"""
E = html.escape


def _label(name: str | None, common: str | None = None) -> str:
    if not name:
        return '<span class="muted">no name</span>'
    return (f'<span class="common">{E(common)}</span> <i>{E(name)}</i>' if common else f"<i>{E(name)}</i>")


def _candidates(top: list[dict[str, Any]]) -> str:
    return "<br>".join(f"{_label(c['scientific'], c.get('common'))} {c['posterior']:.2f}" for c in top)


def render(s: dict[str, Any], path: str) -> None:
    """The page for one summary (written to `path`): categories, taxa, review queue, the rest, run facts."""
    base = Path(path).resolve().parent
    cats = {c["class"]: c for c in s["categories"]}
    strip = "".join(f'<span class="c-{E(c["class"])}" style="flex:{c["images"]}" title="{E(c["class"])} '
                    f'{c["images"]}">{E(c["class"])} {c["images"]}</span>' for c in s["categories"] if c["images"])
    parts = [f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" '
             f'content="width=device-width, initial-scale=1"><title>bioscan report</title><style>{CSS}</style></head>'
             f'<body><h1>bioscan report</h1><p>{E(line(s))}</p><div class="strip">{strip}</div>'
             f'<p class="muted">{s["counts"]["boxes"]} boxes, {s["counts"]["failed"]} failed; '
             + ", ".join(f'{E(c["class"])}: {c["taxa"]} taxa, {c["review"]} to review'
                         for c in s["categories"] if c["class"] in ANIMALS)
             + '</p><nav><a href="#taxa">taxa</a><a href="#review">review</a><a href="#rest">the rest</a>'
             '<a href="#facts">run facts</a></nav>']
    parts.append(f'<h2 id="taxa">What was seen <span class="muted">{len(s["taxa"])} taxa</span></h2>')
    for c in ANIMALS:
        mine = [t for t in s["taxa"] if t["kind"] == c]
        if not mine:
            continue
        parts.append(f'<h3>{E(c)} <span class="muted">{cats[c]["boxes"]} boxes</span></h3><div class="grid taxa">')
        for t in mine:
            b = t["best"]
            cap = (f'{_label(t["name"], t["common"])}<br><span class="muted">{E(t["level"])} · {t["boxes"]} boxes in '
                   f'{t["images"]} photos</span>')
            parts.append(cull_cli.figure(b["path"], b["jpg"], base, cap))
        parts.append("</div>")
    parts.append(f'<h2 id="review">To review <span class="muted">{len(s["review"])}</span></h2>')
    groups: dict[str | None, list[dict[str, Any]]] = {}
    for r in s["review"]:
        groups.setdefault(r["suggested"], []).append(r)
    for name, items in groups.items():
        parts.append(f'<h3>{_label(name)} <span class="muted">{len(items)}</span></h3><div class="grid">')
        for r in items:
            box = "frame" if r["box"] is None else f'box {r["box"]}'
            cap = (f'{E(Path(r["path"]).name)} · {box} · {E(r["kind"])} · {E(r["level"] or "no level")}<br>'
                   + "<br>".join(f'<span class="reason">{E(REASONS[x])}</span>' for x in r["reasons"])
                   + (f'<br>{_candidates(r["top"])}' if r["top"] else ""))
            parts.append(cull_cli.figure(r["path"], r["jpg"], base, cap))
        parts.append("</div>")
    parts.append(f'<h2 id="rest">The rest</h2><p>{cats["person"]["images"]} photos of people, '
                 f'{cats["none"]["images"]} with no animal.</p>')
    if s["errors"]:
        parts.append("<h3>Errors</h3><ul>" + "".join(
            f'<li>{E(e["path"])}: {E(e["product"] or "decode")}: {E(str(e["message"]))}</li>' for e in s["errors"])
            + "</ul>")
    src, eng = s["source"], s["source"]["engine"]
    facts = [("preds", src["preds"]), ("preds sha256", src["sha256"]), ("taken", f'{src["first_taken_at"]} to '
             f'{src["last_taken_at"]}'), ("engine", eng.get("version")), ("settings", eng.get("settings")),
             ("models", json.dumps(eng.get("models"), sort_keys=True)),
             ("rules", json.dumps(s["rules"], sort_keys=True)), ("elapsed ms", s["counts"]["elapsed_ms"])]
    parts.append('<h2 id="facts">Run facts</h2><table>'
                 + "".join(f"<tr><th>{E(k)}</th><td>{E(str(v))}</td></tr>" for k, v in facts) + "</table></body></html>\n")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("".join(parts), encoding="utf-8")


# ---- commands ---------------------------------------------------------------------------------

def cmd_summarize(a) -> int:
    _, events = cull_cli.read_ndjson(a.preds)
    s = summarize(events, a.preds, hashlib.sha256(Path(a.preds).read_bytes()).hexdigest())
    out = Path(a.out) / "summary.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(s, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(line(s))
    print(f"summary -> {out}")
    return 0


def cmd_report(a) -> int:
    src = Path(a.summary)
    if src.is_dir():
        src = src / "summary.json"
    s = json.loads(src.read_text(encoding="utf-8"))
    if s.get("kind") != KIND or s.get("schema") != SCHEMA:
        raise SystemExit(f"{src}: not a bioscan summary (schema {SCHEMA})")
    out = a.out or str(src.with_name("report.html"))
    render(s, out)
    print(f"report -> {out}")
    return 0


def add_parser(sub) -> None:
    s = sub.add_parser("summarize", help="a saved run -> summary.json (categories, taxa, review queue with reasons)")
    s.add_argument("preds", help="a saved run: bioscan run --json, cull --json")
    s.add_argument("--out", required=True, metavar="DIR", help="folder for summary.json")
    s.set_defaults(func=cmd_summarize)
    s = sub.add_parser("report", help="summary.json -> an HTML page (report.html next to it)")
    s.add_argument("summary", help="summary.json, or the folder that holds it")
    s.add_argument("--out", metavar="HTML", help="where to write the page (default: report.html next to the summary)")
    s.set_defaults(func=cmd_report)
