"""`bioscan aesthetic score PATHS...`: score photos with the album profile and export the ranking.

    bioscan aesthetic score ~/Pictures/trip -r --export json,csv,html --out ~/Pictures/trip/aesthetic
    bioscan aesthetic score --preds aesthetic.ndjson --export html --out aesthetic     # again, offline
    bioscan aesthetic apply bioscan-decisions.json --keep-to ~/Pictures/trip-keep --drop-to ~/Pictures/trip-drop

`--export` takes any of json (default), csv, html, comma-separated; `--out` is a prefix: `<out>.ndjson`
(the run's events with a meta line: what `--preds`, `bioscan cull --preds` and `bench aesthetic score`
read back), `<out>.csv` (one row per photo, best first) and `<out>.html` (a gallery of thumbnails
sorted by score with filters, a taxon tree to browse and merge names, keep/drop marks and a lightbox
at the copy's full size). The service writes the page's jpg copies in `<out>-files/` at long edge
`--edge` (default 3072, the lightbox image); a `<stem>-<sha8>-t.jpg` thumbnail (long edge
`--thumb-edge`, default 1024) is made next to each for the grid; a `--preds` run without copies shows
browser-readable originals and marks the rest.

Stars are quintiles of this run's scores (5 = the top fifth), a relative rank and not a rating; scene
and reject reasons come from the album profile's scene and quality stages. `--species` turns the
profile's species naming on, so the CSV and the page also carry each photo's surest name (species,
genus or family, as `bioscan summarize` counts it) and the page groups and filters by it. The page
keeps its marks in the browser and exports them as `bioscan-decisions.json`; `bioscan aesthetic apply`
copies the keeps to a folder and/or moves the drops to another (with their XMP sidecars). Nothing is
ever deleted. Standard library only, like the rest of the CLI."""
from __future__ import annotations

import csv
import html
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from bioscan import contract, cull, formats, xmp
from bioscan.cli import cull as cc
from bioscan.cli import report as rp
from bioscan.cli.config import expand, load_config, request_options

FORMATS = ("json", "csv", "html")
THUMB_EDGE = 1024
EDGE = 3072
DEFAULT_OUT = "aesthetic-scores"
CSV_FIELDS = ("rank", "path", "score", "stars", "scene", "species", "common", "level", "reject_reasons", "sharpness",
              "taken_at")


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


def build_request(paths: list[str], out: str, thumbs: bool, species: bool = False,
                  edge: int = EDGE) -> tuple[dict[str, Any], dict[str, Any]]:
    """(the /run body, the meta line): the album profile, plus jpg copies for the page at long edge
    `edge`; `species` turns the profile's species naming on (BioCLIP loads), so each photo's best box
    gets a name."""
    res = expand(load_config(), cc.DEFAULT_PROFILE, None, {"identify": {"species": True}} if species else {})
    want, options = list(res.want), request_options(res)
    if thumbs:
        want = [*want, "jpg"] if "jpg" not in want else want
        options["jpg"] = {"out_dir": files_dir(out), "edge": edge}
    body = {"inputs": [{"path": p} for p in paths], "want": want, "options": options}
    meta = {"type": "meta", "schema": cc.PREDS_SCHEMA, "profile": res.profile, "options": options,
            "created": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    return body, meta


def thumb_of(jpg: str) -> str:
    """The grid thumbnail next to a jpg copy: `<stem>-<sha8>-t.jpg`."""
    p = Path(jpg)
    return str(p.with_name(p.stem + "-t.jpg"))


def shrink(events: list[dict[str, Any]], edge: int) -> int:
    """Write a thumbnail (long edge `edge`) next to each jpg copy larger than that, when there is none
    yet; the copy itself stays at its size for the lightbox. Returns how many were written. Pillow only
    here; without it the page shows the copies themselves."""
    try:
        from PIL import Image
    except ImportError:
        return 0
    n = 0
    for ev in events:
        p = (cull.products(ev).get("jpg") or {}).get("path")
        if not p or Path(thumb_of(p)).is_file() or not Path(p).is_file():
            continue
        with Image.open(p) as im:                       # the file's own size: a re-export may follow a resized copy
            if max(im.size) <= edge:
                continue
            im.thumbnail((edge, edge))
            im.save(thumb_of(p), "JPEG", quality=85)
        n += 1
    return n


def named(ev: dict[str, Any]) -> tuple[str | None, str | None, str | None, list[str]]:
    """(taxon, common, level, lineage) of the photo's surest named box: species, genus or family name
    as the summary counts it, and its lineage from class down to that name (the page's tree);
    (None, None, None, []) when species was off or nothing held up."""
    best = None
    for b in contract.boxes_of(contract.identify_of(ev)):
        sp = contract.species_of(b)
        name, level = rp.taxon(sp), contract.level_of(sp)
        if name:
            top = contract.top_of(sp)[0]
            if best is None or top["posterior"] > best[0]:
                lineage = [*(top.get("taxonomy") or [])[2:rp.LEVEL_RANK[level]], name]
                best = (top["posterior"], name, top.get("common") if level == "species" else None, level, lineage)
    return best[1:] if best else (None, None, None, [])


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
        species, common, level, lineage = named(ev)
        rows.append({"path": ev["path"], "score": score, "scene": sc.get("label"),
                     "species": species, "common": common, "level": level, "lineage": lineage,
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
    if any(r["species"] for r in rows):
        by = {}
        for r in rows:
            if r["species"]:
                by[r["level"]] = by.get(r["level"], 0) + 1
        lines.append(f"{sum(by.values())} named: " + ", ".join(f"{by[k]} to {k}" for k in ("species", "genus", "family")
                                                              if k in by))
    notes = {r["note"] for r in rows if r["score"] is None and r["note"]}
    lines += [f"note: {n}" for n in sorted(notes)]
    return "\n".join(lines)


# ---- html ---------------------------------------------------------------------------------------

CSS = """
:root{--bg:#f6f6f4;--card:#fff;--ink:#1b1b1b;--mute:#6b6b6b;--line:#e2e2df;--acc:#1f6feb;--bad:#c0392b;--ok:#2e8b57}
@media(prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#121212;--card:#1c1c1c;--ink:#ececec;--mute:#9a9a9a;--line:#2c2c2c;--acc:#5ea1ff;--bad:#ff6b5b;--ok:#5fc48c}}
*{box-sizing:border-box}body{margin:0;height:100vh;display:flex;flex-direction:column;background:var(--bg);color:var(--ink);font:14px/1.45 -apple-system,"Helvetica Neue",Arial,sans-serif}
header{flex:none;background:var(--bg);border-bottom:1px solid var(--line);padding:8px 16px;display:flex;flex-wrap:wrap;gap:6px 12px;align-items:center}
header h1{font-size:16px;margin:0 8px 0 0;font-weight:600}header label{display:flex;gap:4px;align-items:center;color:var(--mute);white-space:nowrap}
select,input[type=search],button{font:inherit;padding:3px 6px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--ink)}
button{cursor:pointer}button.on{border-color:var(--acc);color:var(--acc)}
.chips{display:flex;gap:4px;flex-wrap:wrap}.chip{border:1px solid var(--line);border-radius:999px;padding:1px 9px;cursor:pointer;background:var(--card);color:var(--mute);user-select:none}
.chip.on{border-color:var(--acc);color:var(--acc)}#count{color:var(--mute);margin-left:auto}
.legend{flex:none;padding:4px 16px;color:var(--mute);font-size:12px}#hint{color:var(--acc)}
#wrap{flex:1;min-height:0;display:flex}#wrap.notree aside{display:none}
aside{width:300px;flex:none;overflow:auto;border-right:1px solid var(--line);padding:8px 10px;font-size:13px}
.kids{margin-left:10px;border-left:1px solid var(--line);padding-left:6px}summary{cursor:pointer;list-style-position:outside}
.leaf{padding-left:14px}.tn{cursor:pointer}.tn.on{color:var(--acc);font-weight:600}.tn b{font-weight:400;color:var(--mute)}.tn small{color:var(--mute)}
.mg{cursor:pointer;color:var(--mute);margin-left:4px;padding:0 4px}.mg:hover,.mg.on{color:var(--acc)}
.merges{margin-top:10px;padding-top:6px;border-top:1px solid var(--line);color:var(--mute)}.merges a{cursor:pointer;color:var(--acc)}
main{flex:1;overflow:auto;display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));grid-auto-rows:max-content;gap:12px;padding:8px 16px 40px}
.card{background:var(--card);border:2px solid var(--line);border-radius:10px;overflow:hidden;display:flex;flex-direction:column}
.card.k{border-color:var(--ok)}.card.x{border-color:var(--bad);opacity:.55}
.im{aspect-ratio:3/2;background:#000;position:relative;cursor:zoom-in}.im img{position:absolute;inset:0;width:100%;height:100%;object-fit:contain}
.im .no{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:#888;font-size:12px}
.rank{position:absolute;z-index:1;left:6px;top:6px;background:rgba(0,0,0,.55);color:#fff;font-size:11px;padding:1px 6px;border-radius:4px}
.meta{padding:8px 10px;display:flex;flex-direction:column;gap:3px}.row1{display:flex;align-items:baseline;gap:8px}
.score{font-size:22px;font-weight:700;font-variant-numeric:tabular-nums}.stars{color:#d4a017;letter-spacing:1px}.scene{margin-left:auto;color:var(--mute)}
.file{font-family:ui-monospace,Menlo,monospace;font-size:12px;word-break:break-all}.dir{color:var(--mute);font-size:11px}
.sp{font-size:13px}.sp i{color:var(--mute)}.sp small{color:var(--mute)}
.rr{display:flex;gap:4px;flex-wrap:wrap}.rr span{font-size:11px;color:var(--bad);border:1px solid var(--bad);border-radius:4px;padding:0 5px}.rr .clean{color:var(--ok);border-color:var(--ok)}
.act{display:flex;gap:6px;margin-top:2px}.act a{cursor:pointer;border:1px solid var(--line);border-radius:6px;padding:1px 9px;color:var(--mute);user-select:none;font-size:12px}
.act a.on{color:#fff}.act .k.on{background:var(--ok);border-color:var(--ok)}.act .x.on{background:var(--bad);border-color:var(--bad)}
#lb{position:fixed;inset:0;background:rgba(0,0,0,.94);display:none;align-items:center;justify-content:center;z-index:10;flex-direction:column;gap:8px}
#lb img{max-width:98vw;max-height:90vh;object-fit:contain;cursor:zoom-out}#lbt{color:#ddd;font-size:13px;display:flex;gap:12px;align-items:center;flex-wrap:wrap;justify-content:center}
#lbt .act a{color:#ccc;border-color:#555}#lbt .act a.on{color:#fff}
@media(max-width:800px){#wrap{flex-direction:column}aside{width:auto;max-height:35vh;border-right:0;border-bottom:1px solid var(--line)}
main{grid-template-columns:1fr 1fr;gap:8px;padding:8px 8px 40px}.score{font-size:18px}}
"""

JS = r"""
const $=s=>document.querySelector(s),grid=$('#grid'),lb=$('#lb'),lbi=$('#lbi'),lbt=$('#lbt'),on=new Set();
const KEY='bioscan:'+ROOT,saved=(()=>{try{return JSON.parse(localStorage.getItem(KEY))||{}}catch(e){return{}}})();
const DEC=saved.dec||{},MERGE=saved.merge||{};let group='',merging=null,shown=[],cur=-1;
const save=()=>{try{localStorage.setItem(KEY,JSON.stringify({dec:DEC,merge:MERGE}))}catch(e){}};
const esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
for(const d of DIRS){const o=document.createElement('option');o.value=d;o.textContent=d;$('#dir').appendChild(o)}
for(const s of SCENES){const c=document.createElement('span');c.className='chip';c.textContent=s;c.onclick=()=>{c.classList.toggle('on');on.has(s)?on.delete(s):on.add(s);render()};$('#scenes').appendChild(c)}
const BY={};for(const r of DATA)if(r.sp&&!BY[r.sp])BY[r.sp]={cn:r.cn,lv:r.lv,tx:r.tx};
function eff(r){let sp=r.sp,n=0;while(sp&&MERGE[sp]&&n++<20)sp=MERGE[sp];if(sp===r.sp)return r;const b=BY[sp]||{};return{...r,sp,cn:b.cn||null,lv:b.lv||r.lv,tx:b.tx||[sp]}}
const keyOf=r=>(r.sp?r.tx:['(unnamed)']).join('/');
function tree(){
  const root={n:0,kids:{}};
  for(const r0 of DATA){const r=eff(r0);let node=root;node.n++;for(const k of (r.sp?r.tx:['(unnamed)'])){node=node.kids[k]||(node.kids[k]={n:0,kids:{}});node.n++}if(r.sp){node.leaf=r.sp;node.cn=r.cn}}
  const html=(node,pre)=>Object.keys(node.kids).sort().map(k=>{const c=node.kids[k],key=pre?pre+'/'+k:k,sub=Object.keys(c.kids).length;
    const lbl=`<a class="tn${group===key?' on':''}" data-k="${esc(key)}"${c.leaf?` data-sp="${esc(c.leaf)}"`:''}>${esc(k)}${c.cn?` <small>${esc(c.cn)}</small>`:''} <b>${c.n}</b></a>`+(c.leaf?`<a class="mg${merging===c.leaf?' on':''}" data-sp="${esc(c.leaf)}" title="merge this name into another: click it, then the target name">⇢</a>`:'');
    return sub?`<details open><summary>${lbl}</summary><div class="kids">${html(c,key)}</div></details>`:`<div class="leaf">${lbl}</div>`}).join('');
  const m=Object.entries(MERGE);
  $('#tree').innerHTML=html(root,'')+(m.length?`<div class="merges">merged: ${m.map(([a,b])=>esc(a)+' → '+esc(b)).join('; ')} · <a id="unmerge">undo all</a></div>`:'');
  $('#hint').textContent=merging?`merging "${merging}": click the name it belongs to (⇢ again to cancel)`:'';
}
$('#tree').onclick=e=>{const t=e.target.closest('a');if(!t)return;e.preventDefault();
  if(t.id==='unmerge'){for(const k in MERGE)delete MERGE[k];save();refresh();return}
  if(t.classList.contains('mg')){merging=merging===t.dataset.sp?null:t.dataset.sp;tree();return}
  if(merging){let to=t.dataset.sp;if(to&&to!==merging){while(MERGE[to]&&MERGE[to]!==merging)to=MERGE[to];MERGE[merging]=to;for(const k in MERGE)if(MERGE[k]===merging)MERGE[k]=to;if(MERGE[to]===merging)delete MERGE[to]}merging=null;save();refresh();return}
  group=group===t.dataset.k?'':t.dataset.k;render();tree()};
const act=(r,cls='')=>`<div class="act ${cls}"><a class="k${DEC[r.p]==='k'?' on':''}" data-v="k">✓ keep</a><a class="x${DEC[r.p]==='x'?' on':''}" data-v="x">✗ drop</a></div>`;
function render(){
  const sort=$('#sort').value,dir=$('#dir').value,star=$('#star').value,rej=$('#rej').value,sp=$('#sp').value,dec=$('#dec').value,q=$('#q').value.trim().toLowerCase();
  shown=DATA.map(eff).filter(r=>(dir===''||r.d===dir)&&(star===''||String(r.st)===star)&&(!on.size||on.has(r.sc))&&(sp===''||(sp==='named'?!!r.sp:sp==='unnamed'?!r.sp:r.sp===sp))
    &&(!q||r.f.toLowerCase().includes(q)||(r.sp||'').toLowerCase().includes(q)||(r.cn||'').toLowerCase().includes(q))
    &&(rej===''||(rej==='clean'&&!r.rr.length)||(rej==='any'&&r.rr.length)||r.rr.includes(rej))
    &&(dec===''||(dec==='u'?!DEC[r.p]:DEC[r.p]===dec))&&(!group||keyOf(r)===group||keyOf(r).startsWith(group+'/')));
  shown.sort(sort==='sd'?(a,b)=>(b.s??-1)-(a.s??-1):sort==='sa'?(a,b)=>(a.s??9)-(b.s??9):(a,b)=>a.f.localeCompare(b.f));
  const nk=DATA.filter(r=>DEC[r.p]==='k').length,nx=DATA.filter(r=>DEC[r.p]==='x').length;
  $('#count').textContent=`${shown.length} photos · ${nk} keep · ${nx} drop`;
  grid.innerHTML=shown.map((r,i)=>`<div class="card ${DEC[r.p]||''}" data-i="${i}" data-p="${esc(r.p)}"><div class="im"><span class="rank">#${i+1}</span>${r.t?`<img loading="lazy" src="${esc(r.t)}" alt="">`:'<span class="no">no thumbnail</span>'}</div>
<div class="meta"><div class="row1"><span class="score">${r.s==null?'–':r.s.toFixed(3)}</span><span class="stars">${r.st?'★'.repeat(r.st)+'☆'.repeat(5-r.st):''}</span><span class="scene">${esc(r.sc||'')}</span></div>
${r.sp?`<div class="sp">${r.cn?esc(r.cn)+' ':''}<i>${esc(r.sp)}</i>${r.lv!=='species'?` <small>(${esc(r.lv)})</small>`:''}</div>`:''}<div class="file">${esc(r.f)}</div>${r.d?`<div class="dir">${esc(r.d)}</div>`:''}<div class="rr">${r.rr.length?r.rr.map(x=>`<span>${esc(x)}</span>`).join(''):'<span class="clean">no reject reason</span>'}</div>${act(r)}</div></div>`).join('');
}
const label=b=>(b.cn?b.cn+' · '+b.sp:b.sp+(b.lv&&b.lv!=='species'?' ('+b.lv+')':''));
function fillSp(){const sel=$('#sp'),keep=sel.value,seen={};for(const r0 of DATA){const r=eff(r0);if(r.sp&&!seen[r.sp])seen[r.sp]={sp:r.sp,cn:r.cn,lv:r.lv}}
  const opts=Object.values(seen).map(b=>({v:b.sp,l:label(b)})).sort((a,b)=>a.l.localeCompare(b.l));
  sel.innerHTML='<option value="">all</option><option value="named">named</option><option value="unnamed">unnamed</option>'+opts.map(o=>`<option value="${esc(o.v)}">${esc(o.l)}</option>`).join('');
  sel.value=[...sel.options].some(o=>o.value===keep)?keep:''}
function refresh(){fillSp();render();tree()}
function mark(p,v){DEC[p]===v?delete DEC[p]:DEC[p]=v;save();
  for(const c of grid.querySelectorAll(`.card[data-p="${CSS.escape(p)}"]`)){c.className='card '+(DEC[p]||'');for(const a of c.querySelectorAll('.act a'))a.classList.toggle('on',DEC[p]===a.dataset.v)}
  const nk=DATA.filter(r=>DEC[r.p]==='k').length,nx=DATA.filter(r=>DEC[r.p]==='x').length;$('#count').textContent=`${shown.length} photos · ${nk} keep · ${nx} drop`;
  if(cur>=0)caption()}
grid.onclick=e=>{const a=e.target.closest('.act a');const card=e.target.closest('.card');if(!card)return;
  if(a){mark(card.dataset.p,a.dataset.v);return}if(e.target.closest('.im'))open(+card.dataset.i)};
function caption(){const r=shown[cur];lbt.innerHTML=`<span>#${cur+1}/${shown.length}</span><span class="file">${esc(r.p)}</span><span>${r.s==null?'–':r.s.toFixed(3)}${r.sp?' · '+esc(r.cn||r.sp):''}</span><span>${r.o?'original':r.l?'copy '+esc(r.l.split('/').pop()):''}</span>${act(r)}`}
function open(i){if(i<0||i>=shown.length)return;cur=i;const r=shown[i];const src=r.o||r.l||r.t;if(!src)return;lbi.src=src;caption();lb.style.display='flex'}
function close(){lb.style.display='none';lbi.src='';cur=-1}
lb.onclick=e=>{const a=e.target.closest('.act a');if(a){mark(shown[cur].p,a.dataset.v);return}if(e.target===lbi||e.target===lb)close()};
document.addEventListener('keydown',e=>{if(cur<0)return;if(e.key==='Escape')close();else if(e.key==='ArrowRight')open(cur+1);else if(e.key==='ArrowLeft')open(cur-1);
  else if(e.key==='k'||e.key==='K')mark(shown[cur].p,'k');else if(e.key==='x'||e.key==='X')mark(shown[cur].p,'x');else return;e.preventDefault()});
$('#keepall').onclick=()=>{for(const r of shown)DEC[r.p]='k';save();render()};$('#dropall').onclick=()=>{for(const r of shown)DEC[r.p]='x';save();render()};
$('#clearall').onclick=()=>{for(const r of shown)delete DEC[r.p];save();render()};
$('#export').onclick=()=>{const out={schema:1,root:ROOT,created:new Date().toISOString(),keep:DATA.filter(r=>DEC[r.p]==='k').map(r=>r.p),drop:DATA.filter(r=>DEC[r.p]==='x').map(r=>r.p),merge:MERGE};
  const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(out,null,1)],{type:'application/json'}));a.download='bioscan-decisions.json';a.click();URL.revokeObjectURL(a.href)};
$('#import').onchange=e=>{const f=e.target.files[0];if(!f)return;f.text().then(t=>{const d=JSON.parse(t);for(const p of d.keep||[])DEC[p]='k';for(const p of d.drop||[])DEC[p]='x';Object.assign(MERGE,d.merge||{});save();refresh()}).catch(()=>{});e.target.value=''};
$('#tg').onclick=()=>{$('#wrap').classList.toggle('notree');$('#tg').classList.toggle('on')};
if(!DATA.some(r=>r.sp)){$('#wrap').classList.add('notree');$('#tg').classList.remove('on')}
for(const id of['#sort','#dir','#star','#rej','#sp','#dec'])$(id).onchange=render;$('#q').oninput=render;refresh();
"""


def page_rows(rows: list[dict[str, Any]], base: Path, roots: str) -> list[dict[str, Any]]:
    """The page's records: f (file), d (folder under the root), p (absolute path), s, st, sc, rr, t
    (grid thumbnail), l (lightbox copy), o (the original when a browser can show it), sp/cn/lv, tx
    (lineage). None values are left out."""
    data = []
    for r in rows:
        orig = r["path"] if Path(r["path"]).suffix.lower() in cc.BROWSER_IMAGES else None
        large = r["jpg"] or orig
        thumb = thumb_of(r["jpg"]) if r["jpg"] else None
        shown = thumb if thumb and Path(thumb).is_file() else large
        rel = lambda p: os.path.relpath(p, base) if p else None                                     # noqa: E731
        d = {"f": Path(r["path"]).name, "d": os.path.relpath(Path(r["path"]).parent, roots) if roots else "",
             "p": r["path"], "s": None if r["score"] is None else round(r["score"], 4), "st": r["stars"],
             "sc": r["scene"], "rr": r["reject_reasons"], "t": rel(shown), "l": rel(large) if large != shown else None,
             "o": rel(orig) if orig and orig != shown else None, "sp": r["species"], "cn": r["common"],
             "lv": r["level"], "tx": r["lineage"]}
        d["d"] = "" if d["d"] == "." else d["d"]
        data.append({k: v for k, v in d.items() if v is not None})
    return data


def write_html(rows: list[dict[str, Any]], fails: list[dict[str, Any]], path: str, title: str) -> None:
    base = Path(path).resolve().parent
    roots = os.path.commonpath([r["path"] for r in rows]) if len(rows) > 1 else str(Path(rows[0]["path"]).parent) \
        if rows else ""
    data = page_rows(rows, base, roots)
    dirs = sorted({d["d"] for d in data if d["d"]})
    scenes = sorted({d["sc"] for d in data if d.get("sc")})
    reasons = sorted({x for d in data for x in d["rr"]})
    c = cuts(rows)
    legend = (f"Stars are quintiles of this run's scores: 5★ ≥ {c[0]:.3f}, 4★ ≥ {c[1]:.3f}, 3★ ≥ {c[2]:.3f}, "
              f"2★ ≥ {c[3]:.3f}, else 1★. " if c else "") + \
        "The score (bioscan aesthetics head) only orders photos; reject reasons come from the quality stage. " \
        "Click a photo for the full copy: ←/→ move, K keeps, X drops, Esc closes. The tree groups by taxon; ⇢ merges " \
        "a name into another. Keep/drop marks stay in this browser; export writes bioscan-decisions.json for " \
        "`bioscan aesthetic apply`." + \
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
            + '</select></label><label>species<select id="sp"><option value="">all</option><option value="named">named'
            '</option><option value="unnamed">unnamed</option>'
            '</select></label>'
            '<label>marks<select id="dec"><option value="">all</option><option value="k">keep</option>'
            '<option value="x">drop</option><option value="u">unmarked</option></select></label>'
            '<div class="chips" id="scenes"></div>'
            '<input type="search" id="q" placeholder="file or species name">'
            '<button id="tg" class="on">tree</button><button id="keepall">keep shown</button>'
            '<button id="dropall">drop shown</button><button id="clearall">unmark shown</button>'
            '<button id="export">export decisions</button>'
            '<label><button onclick="this.nextElementSibling.click()">import</button>'
            '<input type="file" id="import" accept=".json" hidden></label>'
            '<span id="count"></span></header>'
            f'<div class="legend">{html.escape(legend)} <span id="hint"></span></div>'
            '<div id="wrap"><aside id="tree"></aside><main id="grid"></main></div>'
            '<div id="lb"><img id="lbi" alt=""><div id="lbt"></div></div>'
            f"<script>const DATA={json.dumps(data, ensure_ascii=False, separators=(',', ':'))};"
            f"const ROOT={json.dumps(roots, ensure_ascii=False)};"
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
        body, head = build_request(paths, out, "html" in exports and not a.no_thumbs, a.species, a.edge)
        events, _ = cc.fetch(body, a.url)
    if "html" in exports:
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


def cmd_apply(a) -> int:
    """Copy the decisions file's keeps to --keep-to and/or move its drops to --drop-to, each photo with
    its XMP sidecars; a photo whose name is already at the target is skipped, a missing one reported.
    Exit 1 when any was missing or skipped."""
    if not (a.keep_to or a.drop_to):
        raise SystemExit("give --keep-to DIR (copies the keeps) and/or --drop-to DIR (moves the drops)")
    dec = json.loads(Path(a.decisions).read_text(encoding="utf-8"))
    trouble = 0
    for label, dest, op, verb in (("keep", a.keep_to, shutil.copy2, "copied to"), ("drop", a.drop_to, shutil.move, "moved to")):
        paths = dec.get(label) or []
        if not dest or not isinstance(paths, list):
            continue
        done = 0
        for p in paths:
            src = Path(p)
            if not src.is_file():
                print(f"missing: {p}", file=sys.stderr)
                trouble += 1
                continue
            if (Path(dest) / src.name).exists():
                print(f"already at {dest}, skipped: {p}", file=sys.stderr)
                trouble += 1
                continue
            for f in (src, *(s for s in xmp.sidecar_paths(p) if s.is_file())):
                if not a.dry_run:
                    Path(dest).mkdir(parents=True, exist_ok=True)
                    op(str(f), str(Path(dest) / f.name))
            done += 1
        print(f"{label}: {done} of {len(paths)} {verb} {dest}{' (dry run)' if a.dry_run else ''}")
    return 1 if trouble else 0


def add_parser(g) -> None:
    s = g.add_parser("score", help="score photos with the album profile; export the ranking as json, csv and/or html")
    s.add_argument("paths", nargs="*", help="photo files or folders (not with --preds)")
    s.add_argument("--export", default="json", help=f"comma list of {', '.join(FORMATS)} (default %(default)s)")
    s.add_argument("--out", help=f"output prefix: <out>.ndjson / .csv / .html, jpg copies in <out>-files/ "
                                 f"(default {DEFAULT_OUT})")
    s.add_argument("--preds", metavar="FILE", help="re-export a saved <out>.ndjson (or bioscan run --json) offline")
    s.add_argument("--edge", type=int, default=EDGE,
                   help="long edge of the page's jpg copies, the lightbox image (default %(default)s px; at most the "
                        "service's --detail-edge)")
    s.add_argument("--thumb-edge", type=int, default=THUMB_EDGE,
                   help="long edge of the grid thumbnails made next to the copies (default %(default)s px)")
    s.add_argument("--no-thumbs", action="store_true", help="HTML without jpg copies (browser-readable originals show)")
    s.add_argument("--species", action="store_true", help="also name the animals (identify with species on: BioCLIP "
                                                          "loads); the name goes in the CSV and the page")
    s.add_argument("-r", "--recursive", action="store_true")
    s.add_argument("--ext", default=formats.DEFAULT_EXT)
    s.set_defaults(func=cmd_score)
    ap = g.add_parser("apply", help="apply the page's exported bioscan-decisions.json: copy the keeps to a folder "
                                    "and/or move the drops to another (never deletes)")
    ap.add_argument("decisions", help="bioscan-decisions.json exported from the score page")
    ap.add_argument("--keep-to", metavar="DIR", help="copy the kept originals (and XMP sidecars) here")
    ap.add_argument("--drop-to", metavar="DIR", help="move the dropped originals (and XMP sidecars) here")
    ap.add_argument("--dry-run", action="store_true", help="only report what would be copied or moved")
    ap.set_defaults(func=cmd_apply)
