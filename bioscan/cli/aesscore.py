"""`bioscan aesthetic score PATHS...`: score photos with the album profile and export the ranking.

    bioscan aesthetic score ~/Pictures/trip -r --export json,csv,html --out ~/Pictures/trip/aesthetic
    bioscan aesthetic score --preds aesthetic.ndjson --export html --out aesthetic     # again, offline

`--export` takes any of json (default), csv, html, comma-separated; `--out` is a prefix: `<out>.ndjson`
(the run's events with a meta line: what `--preds`, `bioscan cull --preds` and `bench aesthetic score`
read back), `<out>.csv` (one row per photo, best first) and `<out>.html` (a gallery of thumbnails
sorted by score with filters, like `bioscan cull`'s review page). The thumbnails for the page are the
service's jpg copies in `<out>-files/`, shrunk here to long edge `--thumb-edge` (default 1024) px once
written (the jpg product itself is a frozen contract); a `--preds` run without them shows
browser-readable originals and marks the rest.

Stars are quintiles of this run's scores (5 = the top fifth), a relative rank and not a rating; scene
and reject reasons come from the album profile's scene and quality stages. Nothing is rated, moved
or deleted. Standard library only, like the rest of the CLI."""
from __future__ import annotations

import csv
import html
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from bioscan import contract, cull, formats
from bioscan.cli import cull as cc
from bioscan.cli.config import expand, load_config, request_options

FORMATS = ("json", "csv", "html")
THUMB_EDGE = 1024
DEFAULT_OUT = "aesthetic-scores"
CSV_FIELDS = ("rank", "path", "score", "stars", "scene", "reject_reasons", "sharpness", "taken_at")


def parse_export(spec: str) -> list[str]:
    out = []
    for x in spec.split(","):
        x = x.strip().lower()
        if x not in FORMATS:
            raise SystemExit(f"--export takes {', '.join(FORMATS)} (comma-separated), not {x!r}")
        if x not in out:
            out.append(x)
    return out


def files_dir(out: str) -> str:
    return str(Path(out).resolve().with_name(Path(out).name + "-files"))


def build_request(paths: list[str], out: str, thumbs: bool) -> tuple[dict[str, Any], dict[str, Any]]:
    """(the /run body, the meta line): the album profile, plus jpg copies for the page."""
    res = expand(load_config(), cc.DEFAULT_PROFILE, None, {})
    want, options = list(res.want), request_options(res)
    if thumbs:
        want = [*want, "jpg"] if "jpg" not in want else want
        options["jpg"] = {"out_dir": files_dir(out)}
    body = {"inputs": [{"path": p} for p in paths], "want": want, "options": options}
    meta = {"type": "meta", "schema": cc.PREDS_SCHEMA, "profile": res.profile, "options": options,
            "created": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    return body, meta


def shrink(events: list[dict[str, Any]], edge: int) -> int:
    """Downscale the run's jpg copies to long edge `edge` in place (the page needs thumbnails, not 2048 px
    copies); returns how many were shrunk. Pillow only here; without it the copies stay full size."""
    try:
        from PIL import Image
    except ImportError:
        return 0
    n = 0
    for ev in events:
        j = cull.products(ev).get("jpg") or {}
        p = j.get("path")
        if p and max(j.get("width") or 0, j.get("height") or 0) > edge and Path(p).is_file():
            with Image.open(p) as im:
                im.thumbnail((edge, edge))
                im.save(p, "JPEG", quality=85)
                j["width"], j["height"] = im.size
            n += 1
    return n


def rows_of(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per result, best first (a photo without a finite score comes last); stars = quintile."""
    rows = []
    for ev in events:
        if ev.get("type") != contract.RESULT:
            continue
        p = cull.products(ev)
        a, q, sc = p.get("aesthetics") or {}, p.get("quality") or {}, p.get("scene") or {}
        s = a.get("score")
        score = float(s) if isinstance(s, (int, float)) and not isinstance(s, bool) and s == s else None
        rows.append({"path": ev["path"], "score": score, "scene": sc.get("label"),
                     "reject_reasons": list(q.get("reject_reasons") or []),
                     "sharpness": (q.get("frame") or {}).get("sharpness"), "taken_at": cull.capture(ev)[0],
                     "jpg": (p.get("jpg") or {}).get("path"), "note": a.get("note")})
    rows.sort(key=lambda r: (r["score"] is None, -(r["score"] or 0), r["path"]))
    n = sum(r["score"] is not None for r in rows)
    for i, r in enumerate(rows):
        r["rank"] = i + 1
        r["stars"] = 5 - int(5 * i / n) if r["score"] is not None and n else None
    return rows


def cuts(rows: list[dict[str, Any]]) -> list[float]:
    """The lowest score of stars 5, 4, 3, 2 (the quintile boundaries)."""
    scored = [r["score"] for r in rows if r["score"] is not None]
    n = len(scored)
    return [scored[int(n * k / 5) - 1] for k in range(1, 5)] if n >= 5 else []


def write_csv(rows: list[dict[str, Any]], fails: list[dict[str, Any]], path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({**r, "score": "" if r["score"] is None else f"{r['score']:.4f}", "stars": r["stars"] or "",
                        "reject_reasons": ";".join(r["reject_reasons"])})
        for r in fails:
            w.writerow({"path": r["path"], "reject_reasons": "failed: " + " | ".join(r["messages"])})


def summary(rows: list[dict[str, Any]], fails: list[dict[str, Any]]) -> str:
    scored = [r["score"] for r in rows if r["score"] is not None]
    lines = [f"{len(rows) + len(fails)} photos: {len(scored)} scored, {len(rows) - len(scored)} without a score, "
             f"{len(fails)} failed"]
    if scored:
        c = cuts(rows)
        lines.append(f"score {min(scored):.3f}-{max(scored):.3f}, median {scored[len(scored) // 2]:.3f}"
                     + (f"; stars 5/4/3/2 from {', '.join(f'{x:.3f}' for x in c)}" if c else ""))
    notes = {r["note"] for r in rows if r["score"] is None and r["note"]}
    lines += [f"note: {n}" for n in sorted(notes)]
    return "\n".join(lines)


# ---- html ---------------------------------------------------------------------------------------

CSS = """
:root{--bg:#f6f6f4;--card:#fff;--ink:#1b1b1b;--mute:#6b6b6b;--line:#e2e2df;--acc:#1f6feb;--bad:#c0392b;--ok:#2e8b57}
@media(prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#121212;--card:#1c1c1c;--ink:#ececec;--mute:#9a9a9a;--line:#2c2c2c;--acc:#5ea1ff;--bad:#ff6b5b;--ok:#5fc48c}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 -apple-system,"Helvetica Neue",Arial,sans-serif}
header{position:sticky;top:0;z-index:5;background:var(--bg);border-bottom:1px solid var(--line);padding:10px 16px;display:flex;flex-wrap:wrap;gap:8px 14px;align-items:center}
header h1{font-size:16px;margin:0 8px 0 0;font-weight:600}header label{display:flex;gap:4px;align-items:center;color:var(--mute);white-space:nowrap}
select,input[type=search]{font:inherit;padding:3px 6px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--ink)}
.chips{display:flex;gap:4px;flex-wrap:wrap}.chip{border:1px solid var(--line);border-radius:999px;padding:1px 9px;cursor:pointer;background:var(--card);color:var(--mute);user-select:none}
.chip.on{border-color:var(--acc);color:var(--acc)}#count{color:var(--mute);margin-left:auto}
.legend{padding:6px 16px;color:var(--mute);font-size:12px}
main{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px;padding:0 16px 40px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden;display:flex;flex-direction:column}
.im{aspect-ratio:3/2;background:#000;position:relative;cursor:zoom-in}.im img{position:absolute;inset:0;width:100%;height:100%;object-fit:contain}
.im .no{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:#888;font-size:12px}
.rank{position:absolute;z-index:1;left:6px;top:6px;background:rgba(0,0,0,.55);color:#fff;font-size:11px;padding:1px 6px;border-radius:4px}
.meta{padding:8px 10px;display:flex;flex-direction:column;gap:3px}.row1{display:flex;align-items:baseline;gap:8px}
.score{font-size:22px;font-weight:700;font-variant-numeric:tabular-nums}.stars{color:#d4a017;letter-spacing:1px}.scene{margin-left:auto;color:var(--mute)}
.file{font-family:ui-monospace,Menlo,monospace;font-size:12px;word-break:break-all}.dir{color:var(--mute);font-size:11px}
.rr{display:flex;gap:4px;flex-wrap:wrap}.rr span{font-size:11px;color:var(--bad);border:1px solid var(--bad);border-radius:4px;padding:0 5px}.rr .clean{color:var(--ok);border-color:var(--ok)}
#lb{position:fixed;inset:0;background:rgba(0,0,0,.92);display:none;align-items:center;justify-content:center;z-index:10;flex-direction:column;gap:8px;cursor:zoom-out}
#lb img{max-width:96vw;max-height:88vh;object-fit:contain}#lb div{color:#ddd;font-size:13px}
@media(max-width:600px){main{grid-template-columns:1fr 1fr;gap:8px;padding:0 8px 40px}.score{font-size:18px}}
"""

JS = """
const $=s=>document.querySelector(s),grid=$('#grid'),lb=$('#lb'),lbi=$('#lbi'),lbt=$('#lbt'),on=new Set();
for(const d of DIRS){const o=document.createElement('option');o.value=d;o.textContent=d;$('#dir').appendChild(o)}
for(const s of SCENES){const c=document.createElement('span');c.className='chip';c.textContent=s;c.onclick=()=>{c.classList.toggle('on');on.has(s)?on.delete(s):on.add(s);render()};$('#scenes').appendChild(c)}
const esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
function render(){
  const sort=$('#sort').value,dir=$('#dir').value,star=$('#star').value,rej=$('#rej').value,q=$('#q').value.trim().toLowerCase();
  const rows=DATA.filter(r=>(dir===''||r.d===dir)&&(star===''||String(r.st)===star)&&(!on.size||on.has(r.sc))&&(!q||r.f.toLowerCase().includes(q))
    &&(rej===''||(rej==='clean'&&!r.rr.length)||(rej==='any'&&r.rr.length)||r.rr.includes(rej)));
  rows.sort(sort==='sd'?(a,b)=>(b.s??-1)-(a.s??-1):sort==='sa'?(a,b)=>(a.s??9)-(b.s??9):(a,b)=>a.f.localeCompare(b.f));
  $('#count').textContent=rows.length+' photos';
  grid.innerHTML=rows.map((r,i)=>`<div class="card"><div class="im" data-t="${esc(r.t||'')}" data-f="${esc(r.f)} · ${r.s==null?'–':r.s.toFixed(3)}"><span class="rank">#${i+1}</span>${r.t?`<img loading="lazy" src="${esc(r.t)}" alt="">`:'<span class="no">no thumbnail</span>'}</div>
<div class="meta"><div class="row1"><span class="score">${r.s==null?'–':r.s.toFixed(3)}</span><span class="stars">${r.st?'★'.repeat(r.st)+'☆'.repeat(5-r.st):''}</span><span class="scene">${esc(r.sc||'')}</span></div>
<div class="file">${esc(r.f)}</div>${r.d?`<div class="dir">${esc(r.d)}</div>`:''}<div class="rr">${r.rr.length?r.rr.map(x=>`<span>${esc(x)}</span>`).join(''):'<span class="clean">no reject reason</span>'}</div></div></div>`).join('');
}
grid.onclick=e=>{const im=e.target.closest('.im');if(!im||!im.dataset.t)return;lbi.src=im.dataset.t;lbt.textContent=im.dataset.f;lb.style.display='flex'};
lb.onclick=()=>{lb.style.display='none';lbi.src=''};document.addEventListener('keydown',e=>{if(e.key==='Escape')lb.click()});
for(const id of['#sort','#dir','#star','#rej'])$(id).onchange=render;$('#q').oninput=render;render();
"""


def write_html(rows: list[dict[str, Any]], fails: list[dict[str, Any]], path: str, title: str) -> None:
    base = Path(path).resolve().parent
    roots = os.path.commonpath([r["path"] for r in rows]) if len(rows) > 1 else str(Path(rows[0]["path"]).parent) \
        if rows else ""
    data = []
    for r in rows:
        shown = r["jpg"] or (r["path"] if Path(r["path"]).suffix.lower() in cc.BROWSER_IMAGES else None)
        data.append({"f": Path(r["path"]).name, "d": os.path.relpath(Path(r["path"]).parent, roots) if roots else "",
                     "s": None if r["score"] is None else round(r["score"], 4), "st": r["stars"], "sc": r["scene"],
                     "rr": r["reject_reasons"], "t": os.path.relpath(shown, base) if shown else None})
    for d in data:
        d["d"] = "" if d["d"] == "." else d["d"]
    dirs = sorted({d["d"] for d in data if d["d"]})
    scenes = sorted({d["sc"] for d in data if d["sc"]})
    reasons = sorted({x for d in data for x in d["rr"]})
    c = cuts(rows)
    legend = (f"Stars are quintiles of this run's scores: 5★ ≥ {c[0]:.3f}, 4★ ≥ {c[1]:.3f}, 3★ ≥ {c[2]:.3f}, "
              f"2★ ≥ {c[3]:.3f}, else 1★. " if c else "") + \
        "The score (bioscan aesthetics head) only orders photos; reject reasons come from the quality stage. Click a photo to enlarge." + \
        (f" {len(fails)} photo(s) failed to decode and are listed in the CSV." if fails else "")
    page = (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,'
            f'initial-scale=1"><title>{html.escape(title)}</title><style>{CSS}</style></head><body><header>'
            f'<h1>{html.escape(title)}</h1>'
            '<label>sort<select id="sort"><option value="sd">score high→low</option><option value="sa">score low→high'
            '</option><option value="f">file name</option></select></label>'
            '<label>folder<select id="dir"><option value="">all</option></select></label>'
            '<label>stars<select id="star"><option value="">all</option>'
            + "".join(f"<option>{k}</option>" for k in (5, 4, 3, 2, 1)) + "</select></label>"
            '<label>rejects<select id="rej"><option value="">all</option><option value="clean">none</option>'
            '<option value="any">any</option>' + "".join(f"<option>{html.escape(x)}</option>" for x in reasons)
            + '</select></label><div class="chips" id="scenes"></div>'
            '<input type="search" id="q" placeholder="file name"><span id="count"></span></header>'
            f'<div class="legend">{html.escape(legend)}</div><main id="grid"></main>'
            '<div id="lb"><img id="lbi" alt=""><div id="lbt"></div></div>'
            f"<script>const DATA={json.dumps(data, ensure_ascii=False, separators=(',', ':'))};"
            f"const DIRS={json.dumps(dirs, ensure_ascii=False)};const SCENES={json.dumps(scenes)};{JS}</script>"
            "</body></html>\n")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(page, encoding="utf-8")


# ---- command ------------------------------------------------------------------------------------

def cmd_score(a) -> int:
    exports = parse_export(a.export)
    out = a.out or DEFAULT_OUT
    if a.preds:
        if a.paths:
            raise SystemExit("--preds re-exports a saved run; give no paths with it")
        meta, events = cc.read_ndjson(a.preds)
        head = meta or {"type": "meta", "schema": cc.PREDS_SCHEMA, "profile": None, "options": None}
    else:
        if not a.paths:
            raise SystemExit("give photo files or folders, or --preds FILE")
        exts = formats.parse_ext(a.ext)
        paths = [p for root in a.paths for p in formats.list_images(root, exts, a.recursive)]
        if not paths:
            raise SystemExit("no images found")
        body, head = build_request(paths, out, "html" in exports and not a.no_thumbs)
        events, _ = cc.fetch(body, a.url)
        shrink(events, a.thumb_edge)
    complete = any(e.get("type") == contract.DONE for e in events)
    rows, fails = rows_of(events), cc.failed(events)
    written = []
    if "json" in exports:
        cc.write_json(head, events, f"{out}.ndjson")
        written.append(f"{out}.ndjson")
    if "csv" in exports:
        write_csv(rows, fails, f"{out}.csv")
        written.append(f"{out}.csv")
    if "html" in exports:
        write_html(rows, fails, f"{out}.html", f"bioscan aesthetic score: {len(rows) + len(fails)} photos")
        written.append(f"{out}.html")
    print(summary(rows, fails))
    for w in written:
        print(f"-> {w}")
    if not complete:
        print("error: the stream ended before the service's `done`; photos after the cut are missing", file=sys.stderr)
        return cc.EXIT_INCOMPLETE
    return cc.EXIT_PARTIAL if fails else cc.EXIT_OK


def add_parser(g) -> None:
    s = g.add_parser("score", help="score photos with the album profile; export the ranking as json, csv and/or html")
    s.add_argument("paths", nargs="*", help="photo files or folders (not with --preds)")
    s.add_argument("--export", default="json", help=f"comma list of {', '.join(FORMATS)} (default %(default)s)")
    s.add_argument("--out", help=f"output prefix: <out>.ndjson / .csv / .html, thumbnails in <out>-files/ "
                                 f"(default {DEFAULT_OUT})")
    s.add_argument("--preds", metavar="FILE", help="re-export a saved <out>.ndjson (or bioscan run --json) offline")
    s.add_argument("--thumb-edge", type=int, default=THUMB_EDGE,
                   help="long edge the page's jpg copies are shrunk to (default %(default)s px; 2048 = keep)")
    s.add_argument("--no-thumbs", action="store_true", help="HTML without jpg copies (browser-readable originals show)")
    s.add_argument("-r", "--recursive", action="store_true")
    s.add_argument("--ext", default=formats.DEFAULT_EXT)
    s.set_defaults(func=cmd_score)
