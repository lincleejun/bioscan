"""The `bioscan aesthetic score` review page: one self-contained HTML file (standard library only,
no network) over the run's rows. A photo grid with a selection model (click, shift-click ranges,
cmd/ctrl-click, cmd+A), keep/drop marks per card, per selection and per group (the taxon branch or
filter in view), a sort/filter toolbar in popovers, an overview panel and taxon tree, a lightbox at
the copy's full size, and export/import of the marks as `bioscan-decisions.json`."""
from __future__ import annotations

import html
import json
import os
from pathlib import Path
from typing import Any

from bioscan.cli import cull as cc


def thumb_of(jpg: str) -> str:
    """The grid thumbnail next to a jpg copy: `<stem>-<sha8>-t.jpg`."""
    p = Path(jpg)
    return str(p.with_name(p.stem + "-t.jpg"))


def page_rows(rows: list[dict[str, Any]], base: Path, roots: str) -> list[dict[str, Any]]:
    """The page's records: f (file), d (folder under the root), p (absolute path), s, st, sc, rr, ts
    (taken_at), t (grid thumbnail), l (lightbox copy), o (the original when a browser can show it),
    sp/cn/lv, tx (lineage). None values are left out."""
    data = []
    for r in rows:
        orig = r["path"] if Path(r["path"]).suffix.lower() in cc.BROWSER_IMAGES else None
        large = r["jpg"] or orig
        thumb = thumb_of(r["jpg"]) if r["jpg"] else None
        shown = thumb if thumb and Path(thumb).is_file() else large
        rel = lambda p: os.path.relpath(p, base) if p else None                                     # noqa: E731
        d = {"f": Path(r["path"]).name, "d": os.path.relpath(Path(r["path"]).parent, roots) if roots else "",
             "p": r["path"], "s": None if r["score"] is None else round(r["score"], 4), "st": r["stars"],
             "sc": r["scene"], "rr": r["reject_reasons"], "ts": r.get("taken_at"), "t": rel(shown),
             "l": rel(large) if large != shown else None, "o": rel(orig) if orig and orig != shown else None,
             "sp": r["species"], "cn": r["common"], "lv": r["level"], "tx": r["lineage"]}
        d["d"] = "" if d["d"] == "." else d["d"]
        data.append({k: v for k, v in d.items() if v is not None})
    return data


CSS = """
:root{--bg:#f4f4f2;--panel:#fbfbfa;--card:#fff;--ink:#17181a;--ink2:#5c5f66;--mute:#8a8d94;--line:#e3e3e0;--line2:#d0d0cc;
--acc:#2563eb;--acc-bg:#e8effd;--keep:#15803d;--keep-bg:#dcfce7;--drop:#b91c1c;--drop-bg:#fee2e2;--star:#d97706;--sel:#2563eb;
--col:230px;--r:10px;--shadow:0 1px 2px rgba(0,0,0,.06),0 8px 24px -12px rgba(0,0,0,.25)}
@media(prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#111214;--panel:#17181b;--card:#1d1e22;--ink:#ececee;--ink2:#a8abb3;--mute:#7d818a;--line:#2a2b30;--line2:#3a3b41;
--acc:#6d9cff;--acc-bg:#1d2a45;--keep:#4ade80;--keep-bg:#14301f;--drop:#f87171;--drop-bg:#3b1717;--star:#fbbf24;--sel:#6d9cff;--shadow:0 1px 2px rgba(0,0,0,.4),0 8px 24px -12px rgba(0,0,0,.7)}}
*{box-sizing:border-box}html,body{height:100%}body{margin:0;background:var(--bg);color:var(--ink);font:13px/1.45 -apple-system,"SF Pro Text","Helvetica Neue",Inter,Arial,sans-serif;display:flex;flex-direction:column;overflow:hidden}
button,input,select{font:inherit;color:inherit}button{background:none;border:0;cursor:pointer;padding:0}
a{color:var(--acc)}
/* top bar */
.top{flex:none;height:52px;display:flex;align-items:center;gap:10px;padding:0 14px;background:var(--panel);border-bottom:1px solid var(--line)}
.brand{font-weight:650;font-size:14px;letter-spacing:-.01em;white-space:nowrap}.brand small{color:var(--mute);font-weight:400;margin-left:8px}
.search{flex:1;max-width:420px;display:flex;align-items:center;gap:6px;background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:0 10px;height:32px}
.search input{flex:1;border:0;background:none;outline:none;min-width:0}.search svg{color:var(--mute);flex:none}
.active{display:flex;gap:6px;flex-wrap:nowrap;overflow:hidden;min-width:0}
.chip{display:inline-flex;align-items:center;gap:5px;height:24px;padding:0 8px 0 10px;border-radius:999px;background:var(--acc-bg);color:var(--acc);font-size:12px;white-space:nowrap;cursor:default}
.chip b{font-weight:600}.chip .x{cursor:pointer;opacity:.7;font-size:14px;line-height:1}.chip .x:hover{opacity:1}
.tools{margin-left:auto;display:flex;align-items:center;gap:4px;position:relative}
.tb{height:32px;min-width:32px;padding:0 9px;border-radius:8px;display:inline-flex;align-items:center;gap:6px;color:var(--ink2);border:1px solid transparent}
.tb:hover{background:var(--bg);color:var(--ink)}.tb.on{background:var(--acc-bg);color:var(--acc)}.tb svg{width:16px;height:16px}
.tb .dot{width:6px;height:6px;border-radius:50%;background:var(--acc);margin-left:-2px}
/* popovers */
.pop{position:absolute;top:40px;right:0;z-index:30;background:var(--card);border:1px solid var(--line2);border-radius:12px;box-shadow:var(--shadow);padding:8px;min-width:240px;display:none}
.pop.open{display:block}.pop h4{margin:8px 6px 4px;font-size:11px;font-weight:600;color:var(--mute);text-transform:uppercase;letter-spacing:.04em}
.pop .item{display:flex;align-items:center;gap:8px;width:100%;text-align:left;padding:7px 10px;border-radius:8px;color:var(--ink)}
.pop .item:hover{background:var(--bg)}.pop .item.on{background:var(--acc-bg);color:var(--acc)}.pop .item .k{margin-left:auto;color:var(--mute);font-size:11px}
.pop hr{border:0;border-top:1px solid var(--line);margin:6px 0}
.seg{display:flex;gap:4px;padding:2px 6px;flex-wrap:wrap}.seg button{height:26px;padding:0 10px;border-radius:7px;border:1px solid var(--line);color:var(--ink2);font-size:12px}
.seg button.on{background:var(--acc-bg);border-color:var(--acc);color:var(--acc)}.seg button.st{color:var(--star)}
.pop select{width:100%;margin:2px 6px;height:30px;border:1px solid var(--line);border-radius:7px;background:var(--bg);padding:0 6px}
.pop input[type=range]{width:100%;margin:6px 0}
/* body */
.body{flex:1;min-height:0;display:flex}
aside{width:280px;flex:none;overflow:auto;background:var(--panel);border-right:1px solid var(--line);padding:12px;display:flex;flex-direction:column;gap:12px}
.body.notree aside{display:none}
.ov{background:var(--card);border:1px solid var(--line);border-radius:var(--r);padding:12px}
.ov .big{font-size:26px;font-weight:650;letter-spacing:-.02em;line-height:1.1}.ov .sub{color:var(--mute);font-size:12px;margin-top:2px}
.bar{display:flex;height:6px;border-radius:3px;overflow:hidden;background:var(--line);margin:10px 0 6px}.bar i{display:block;height:100%}.bar .k{background:var(--keep)}.bar .x{background:var(--drop)}
.leg{display:flex;gap:10px;font-size:11px;color:var(--ink2)}
.ovacts{display:flex;gap:6px;margin-top:12px}.ovacts .btn{flex:1;justify-content:center;height:32px;padding:0 6px;font-size:12px;white-space:nowrap}.btn:disabled{opacity:.45;cursor:default;border-color:var(--line)}
#modal{position:fixed;inset:0;background:rgba(0,0,0,.45);display:none;align-items:center;justify-content:center;z-index:50}#modal.on{display:flex}
#modal .box{background:var(--card);border:1px solid var(--line2);border-radius:14px;box-shadow:var(--shadow);padding:20px 22px;max-width:440px;width:92vw}
#modal h3{margin:0 0 8px;font-size:15px}#modal p{margin:0 0 16px;color:var(--ink2);font-size:13px;line-height:1.5;word-break:break-all}
#modal .mb{display:flex;justify-content:flex-end;gap:8px}#modal .danger{background:var(--acc);color:#fff;border-color:var(--acc)}#modal.del .danger{background:var(--drop);border-color:var(--drop)}
#toast{position:fixed;top:64px;left:50%;transform:translateX(-50%);z-index:45;background:var(--ink);color:var(--bg);padding:9px 16px;border-radius:10px;font-size:13px;box-shadow:var(--shadow);display:none;max-width:80vw}#toast.on{display:block}.leg i{display:inline-block;width:8px;height:8px;border-radius:2px;margin-right:4px;vertical-align:0}
.hist{margin-top:10px;display:grid;grid-template-columns:auto 1fr auto;gap:2px 8px;align-items:center;font-size:11px}
.hist button{text-align:left;color:var(--star);letter-spacing:.5px;padding:2px 4px;border-radius:4px}.hist button.on{background:var(--acc-bg)}
.hist .h{color:var(--ink2);font-variant-numeric:tabular-nums}.hist .n{color:var(--mute);font-variant-numeric:tabular-nums}
.tree{font-size:12.5px}.tree h4{margin:0 0 6px;font-size:11px;font-weight:600;color:var(--mute);text-transform:uppercase;letter-spacing:.04em;display:flex;align-items:center}
.tree h4 .cl{margin-left:auto;font-weight:400;text-transform:none;letter-spacing:0;cursor:pointer;color:var(--acc);display:none}.tree.has h4 .cl{display:inline}
.tree details{margin:0}.tree summary{list-style:none;cursor:pointer}.tree summary::-webkit-details-marker{display:none}
.tree .row{display:flex;align-items:center;gap:6px;padding:3px 6px;border-radius:6px;min-height:24px}.tree .row:hover{background:var(--bg)}.tree .row.on{background:var(--acc-bg)}.tree .row.on .nm{color:var(--acc);font-weight:600}
.tree .tg{width:14px;color:var(--mute);font-size:10px;flex:none;text-align:center;transition:transform .12s}.tree details[open]>summary .tg{transform:rotate(90deg)}
.tree .nm{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;cursor:pointer}.tree .nm small{color:var(--mute);font-style:italic;margin-left:4px}
.tree .n{color:var(--mute);font-size:11px;font-variant-numeric:tabular-nums}.tree .mg{opacity:0;color:var(--mute);padding:0 3px;border-radius:4px}.tree .row:hover .mg,.tree .mg.on{opacity:1}.tree .mg:hover,.tree .mg.on{color:var(--acc);background:var(--acc-bg)}
.tree .kids{margin-left:10px;padding-left:8px;border-left:1px solid var(--line)}.tree .leaf{padding-left:14px}
.merges{font-size:11px;color:var(--ink2);padding:6px;border-top:1px solid var(--line);margin-top:6px}.merges a{cursor:pointer}
main{flex:1;min-width:0;display:flex;flex-direction:column}
.ghead{flex:none;display:flex;align-items:center;gap:8px;padding:10px 16px;border-bottom:1px solid var(--line);background:var(--bg)}
.ghead h2{font-size:15px;font-weight:650;margin:0;letter-spacing:-.01em}.ghead .m{color:var(--mute);font-size:12px}.ghead .m b{color:var(--ink2);font-weight:500}
.ghead .acts{margin-left:auto;display:flex;gap:6px}
.btn{height:30px;padding:0 12px;border-radius:8px;border:1px solid var(--line2);background:var(--card);color:var(--ink);display:inline-flex;align-items:center;gap:6px;font-weight:500}
.btn:hover{border-color:var(--ink2)}.btn.k{color:var(--keep)}.btn.x{color:var(--drop)}.btn.q{color:var(--ink2);border-color:transparent;background:none}.btn.q:hover{background:var(--panel)}
.grid{flex:1;overflow:auto;padding:14px 16px 90px;display:grid;grid-template-columns:repeat(auto-fill,minmax(var(--col),1fr));grid-auto-rows:max-content;gap:12px;align-content:start;outline:none}
.hint{color:var(--acc);font-size:12px}
/* cards */
.card{position:relative;background:var(--card);border-radius:var(--r);overflow:hidden;box-shadow:0 0 0 1px var(--line);user-select:none;cursor:default}
.card.sel{box-shadow:0 0 0 2px var(--sel)}.card.k .im::after{content:"";position:absolute;inset:0;box-shadow:inset 0 0 0 3px var(--keep);pointer-events:none;border-radius:var(--r) var(--r) 0 0}
.card.x{opacity:.45}.card.x .im::after{content:"";position:absolute;inset:0;box-shadow:inset 0 0 0 3px var(--drop);pointer-events:none;border-radius:var(--r) var(--r) 0 0}
.card.x.sel,.card.k.sel{opacity:1}
.im{aspect-ratio:3/2;background:#0b0b0c;position:relative}.im img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;display:block}
.im .no{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:#666;font-size:11px}
.ov1{position:absolute;left:8px;top:8px;display:flex;gap:4px;align-items:center}.badge{background:rgba(0,0,0,.62);color:#fff;font-size:11px;font-weight:600;padding:2px 7px;border-radius:6px;font-variant-numeric:tabular-nums;backdrop-filter:blur(4px)}
.badge.st{color:#fbbf24;letter-spacing:1px;font-weight:500}
.mk{position:absolute;right:8px;top:8px;width:22px;height:22px;border-radius:50%;display:none;align-items:center;justify-content:center;color:#fff;font-size:12px;font-weight:700}
.card.k .mk{display:flex;background:var(--keep)}.card.x .mk{display:flex;background:var(--drop)}
.zoom{position:absolute;right:8px;bottom:8px;width:26px;height:26px;border-radius:7px;background:rgba(0,0,0,.55);color:#fff;display:none;align-items:center;justify-content:center;cursor:zoom-in;backdrop-filter:blur(4px)}
.card:hover .zoom{display:flex}
.hov{position:absolute;left:8px;bottom:8px;display:none;gap:4px}.card:hover .hov{display:flex}
.hov button{height:26px;padding:0 9px;border-radius:7px;background:rgba(0,0,0,.55);color:#fff;font-size:12px;font-weight:500;backdrop-filter:blur(4px)}.hov button:hover{background:rgba(0,0,0,.8)}
.hov .k:hover{background:var(--keep)}.hov .x:hover{background:var(--drop)}
.cap{padding:7px 10px 8px;display:flex;flex-direction:column;gap:2px}.cap .t{font-weight:600;font-size:12.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.cap .t i{color:var(--mute);font-weight:400}
.cap .f{display:flex;gap:6px;align-items:center;color:var(--mute);font-size:11px;font-family:ui-monospace,Menlo,monospace;white-space:nowrap;min-width:0}
.cap .f span:first-child{overflow:hidden;text-overflow:ellipsis}
.cap .rr{flex:none;color:var(--drop);font-family:-apple-system,sans-serif;background:var(--drop-bg);padding:0 5px;border-radius:4px}
.empty{grid-column:1/-1;color:var(--mute);padding:60px;text-align:center}
/* selection bar */
.selbar{position:fixed;left:50%;bottom:22px;transform:translate(-50%,20px);opacity:0;pointer-events:none;transition:.15s;z-index:20;display:flex;align-items:center;gap:6px;padding:8px 10px 8px 14px;background:var(--ink);color:var(--bg);border-radius:14px;box-shadow:0 10px 30px -10px rgba(0,0,0,.5)}
.selbar.on{transform:translate(-50%,0);opacity:1;pointer-events:auto}.selbar b{margin-right:8px;font-weight:600}
.selbar button{height:30px;padding:0 12px;border-radius:8px;color:var(--bg);font-weight:500;background:rgba(255,255,255,.1)}.selbar button:hover{background:rgba(255,255,255,.2)}
.selbar .k{color:#86efac}.selbar .x{color:#fca5a5}.selbar .c{opacity:.7;padding:0 8px}
/* lightbox */
#lb{position:fixed;inset:0;background:rgba(8,8,10,.96);display:none;flex-direction:column;z-index:40}#lb.on{display:flex}
#lb .stage{flex:1;min-height:0;display:flex;align-items:center;justify-content:center;position:relative}
#lb img{max-width:100%;max-height:100%;object-fit:contain}
#lb .nav{position:absolute;top:0;bottom:0;width:18%;cursor:pointer;opacity:0;display:flex;align-items:center;color:#fff;font-size:34px;transition:.15s}#lb .nav:hover{opacity:.8}
#lb .prev{left:0;justify-content:flex-start;padding-left:18px}#lb .next{right:0;justify-content:flex-end;padding-right:18px}
#lb .bar{display:flex;align-items:center;gap:14px;padding:10px 16px;color:#ddd;font-size:12.5px;background:#000;height:auto;border-radius:0;margin:0}
#lb .bar .f{font-family:ui-monospace,Menlo,monospace;color:#aaa}#lb .bar .sp{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#lb .bar button{height:30px;padding:0 12px;border-radius:8px;background:rgba(255,255,255,.1);color:#fff;font-weight:500}#lb .bar button.on.k{background:var(--keep)}#lb .bar button.on.x{background:var(--drop)}
#lb .close{position:absolute;top:12px;right:14px;color:#fff;font-size:22px;width:36px;height:36px;border-radius:50%;background:rgba(255,255,255,.1)}
kbd{font:11px ui-monospace,Menlo,monospace;background:rgba(127,127,127,.18);padding:1px 5px;border-radius:4px}
@media(max-width:800px){aside{position:absolute;z-index:25;top:52px;bottom:0;left:0;width:82vw;box-shadow:var(--shadow)}.body.notree aside{display:none}.search{max-width:none}.active{display:none}.ghead{flex-wrap:wrap}.grid{padding:10px 10px 90px;gap:8px;--col:150px}}
"""

ICONS = {
    "search": '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>',
    "sort": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M7 4v16m0 0-3-3m3 3 3-3M17 20V4m0 0-3 3m3-3 3 3"/></svg>',
    "filter": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 6h16M7 12h10M10 18h4"/></svg>',
    "tree": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 5h16M4 12h16M4 19h16" opacity=".35"/><path d="M4 5h6M4 12h10M4 19h7"/></svg>',
    "more": '<svg viewBox="0 0 24 24" fill="currentColor"><circle cx="5" cy="12" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="19" cy="12" r="2"/></svg>',
    "zoom": '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/></svg>',
}

JS = r"""
const $=s=>document.querySelector(s),$$=s=>[...document.querySelectorAll(s)],grid=$('#grid');
const KEY='bioscan:'+ROOT,saved=(()=>{try{return JSON.parse(localStorage.getItem(KEY))||{}}catch(e){return{}}})();
const DEC=saved.dec||{},MERGE=saved.merge||{},GONE=new Set(saved.gone||[]);
const LIVE=()=>DATA.filter(r=>!GONE.has(r.p));
const F={sort:'st',dir:'',stars:new Set(),rej:'',scenes:new Set(),sp:'',dec:'',q:'',group:''};
let merging=null,shown=[],cur=-1,sel=new Set(),anchor=-1;
const save=()=>{try{localStorage.setItem(KEY,JSON.stringify({dec:DEC,merge:MERGE,gone:[...GONE]}))}catch(e){}};
const esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const BY={};for(const r of DATA)if(r.sp&&!BY[r.sp])BY[r.sp]={cn:r.cn,lv:r.lv,tx:r.tx};
function eff(r){let sp=r.sp,n=0;while(sp&&MERGE[sp]&&n++<20)sp=MERGE[sp];if(sp===r.sp)return r;const b=BY[sp]||{};return{...r,sp,cn:b.cn||null,lv:b.lv||r.lv,tx:b.tx||[sp]}}
const keyOf=r=>(r.sp?r.tx:['(unnamed)']).join('/');
const label=b=>b.cn||(b.sp+(b.lv&&b.lv!=='species'?' ('+b.lv+')':''));
const nameOf=r=>r.sp?label(r):'';
const stars=n=>n?'★'.repeat(n)+'☆'.repeat(5-n):'';
const byTime=(a,b)=>(a.ts||'').localeCompare(b.ts||'')||a.f.localeCompare(b.f);
const SORTS={st:['stars, then time taken',(a,b)=>(b.st||0)-(a.st||0)||byTime(a,b)],sd:['score, best first',(a,b)=>(b.s??-1)-(a.s??-1)],sa:['score, worst first',(a,b)=>(a.s??9)-(b.s??9)],f:['file name',(a,b)=>a.f.localeCompare(b.f)],
  td:['newest first',(a,b)=>(b.ts||'').localeCompare(a.ts||'')],ta:['oldest first',(a,b)=>(a.ts||'').localeCompare(b.ts||'')],sp:['species',(a,b)=>nameOf(a).localeCompare(nameOf(b))||(b.st||0)-(a.st||0)||byTime(a,b)]};
/* ---- filtering ---- */
function pass(r){const q=F.q;
  return (F.dir===''||r.d===F.dir)&&(!F.stars.size||F.stars.has(r.st))&&(!F.scenes.size||F.scenes.has(r.sc))
    &&(F.sp===''||(F.sp==='named'?!!r.sp:F.sp==='unnamed'?!r.sp:r.sp===F.sp))
    &&(!q||r.f.toLowerCase().includes(q)||(r.sp||'').toLowerCase().includes(q)||(r.cn||'').toLowerCase().includes(q))
    &&(F.rej===''||(F.rej==='clean'&&!r.rr.length)||(F.rej==='any'&&r.rr.length)||r.rr.includes(F.rej))
    &&(F.dec===''||(F.dec==='u'?!DEC[r.p]:DEC[r.p]===F.dec))&&(!F.group||keyOf(r)===F.group||keyOf(r).startsWith(F.group+'/'))}
function render(){
  shown=LIVE().map(eff).filter(pass).sort(SORTS[F.sort][1]);sel.clear();anchor=-1;
  grid.innerHTML=shown.length?shown.map((r,i)=>`<figure class="card ${DEC[r.p]||''}" data-i="${i}" data-p="${esc(r.p)}"><div class="im">${r.t?`<img loading="lazy" src="${esc(r.t)}" alt="">`:'<span class="no">no thumbnail</span>'}
<div class="ov1"><span class="badge">${r.s==null?'–':r.s.toFixed(3)}</span>${r.st?`<span class="badge st">${'★'.repeat(r.st)}</span>`:''}</div><span class="mk">${DEC[r.p]==='k'?'✓':'✕'}</span>
<span class="zoom" title="open">${ICON_ZOOM}</span><div class="hov"><button class="k">keep</button><button class="x">drop</button></div></div>
<figcaption class="cap"><div class="t" title="${esc(r.sp||'')}">${r.sp?esc(label(r)):'<i>unnamed</i>'}</div><div class="f"><span>${esc(r.f)}</span>${r.rr.length?`<span class="rr" title="${esc(r.rr.join(', '))}">${esc(r.rr[0].replace(/_/g,' '))}${r.rr.length>1?' +'+(r.rr.length-1):''}</span>`:''}</div></figcaption></figure>`).join('')
    :'<div class="empty">Nothing matches. Clear a filter above.</div>';
  ghead();selbar();chips();overview()}
function ghead(){const k=shown.filter(r=>DEC[r.p]==='k').length,x=shown.filter(r=>DEC[r.p]==='x').length;
  let title='All photos';if(F.group){const last=F.group.split('/').pop();title=(BY[last]&&BY[last].cn)||last}
  const parts=[];if(F.sp&&!F.group)parts.push(F.sp==='named'?'named':F.sp==='unnamed'?'unnamed':label(BY[F.sp]||{sp:F.sp}));if(F.stars.size)parts.push([...F.stars].sort().reverse().map(s=>s+'★').join(' '));if(F.dec)parts.push({k:'keep',x:'drop',u:'unmarked'}[F.dec]);
  $('#gtitle').textContent=title+(parts.length&&title==='All photos'?'':'' )+(parts.length?' · '+parts.join(' · '):'');
  $('#gmeta').innerHTML=`<b>${shown.length}</b> photos · <b class="kc">${k}</b> keep · <b class="xc">${x}</b> drop · ${shown.length-k-x} unmarked`}
function overview(){const live=LIVE(),k=live.filter(r=>DEC[r.p]==='k').length,x=live.filter(r=>DEC[r.p]==='x').length,n=live.length;
  $('#ovn').textContent=n+' photos';$('#exp').textContent=`Export ${k} keep${k===1?'':'s'}…`;$('#del').textContent=`Delete ${x} drop${x===1?'':'s'}…`;
  $('#exp').disabled=!FSA||!k;$('#del').disabled=!FSA||!x;
  $('#ovbar').innerHTML=`<i class="k" style="width:${100*k/n}%"></i><i class="x" style="width:${100*x/n}%"></i>`;
  $('#ovleg').innerHTML=`<span><i style="background:var(--keep)"></i>${k} keep</span><span><i style="background:var(--drop)"></i>${x} drop</span><span><i style="background:var(--line2)"></i>${n-k-x} unmarked</span>`;
  const h={};for(const r of live)if(r.st)h[r.st]=(h[r.st]||0)+1;
  $('#hist').innerHTML=[5,4,3,2,1].map(s=>`<button class="${F.stars.has(s)?'on':''}" data-s="${s}" title="filter to ${s} stars">${'★'.repeat(s)}</button><span class="h">${CUTS[5-s]!=null?'≥ '+CUTS[5-s].toFixed(3):'the rest'}</span><span class="n">${h[s]||0}</span>`).join('')}
/* ---- tree ---- */
function tree(){const root={n:0,kids:{}};
  for(const r0 of LIVE()){const r=eff(r0);let node=root;node.n++;for(const k of (r.sp?r.tx:['(unnamed)'])){node=node.kids[k]||(node.kids[k]={n:0,kids:{}});node.n++}if(r.sp){node.leaf=r.sp;node.cn=r.cn}}
  const row=(k,c,key)=>`<div class="row${F.group===key?' on':''}" data-k="${esc(key)}"${c.leaf?` data-sp="${esc(c.leaf)}"`:''}><span class="tg">${Object.keys(c.kids).length?'▶':''}</span><span class="nm">${esc(c.cn||k)}${c.cn?`<small>${esc(k)}</small>`:''}</span>${c.leaf?`<span class="mg${merging===c.leaf?' on':''}" data-sp="${esc(c.leaf)}" title="merge this name into another">⇢</span>`:''}<span class="n">${c.n}</span></div>`;
  const html=(node,pre,depth)=>Object.keys(node.kids).sort((a,b)=>node.kids[b].n-node.kids[a].n).map(k=>{const c=node.kids[k],key=pre?pre+'/'+k:k;
    return Object.keys(c.kids).length?`<details${depth<2||(F.group&&F.group.startsWith(key))?' open':''}><summary>${row(k,c,key)}</summary><div class="kids">${html(c,key,depth+1)}</div></details>`:`<div class="leaf">${row(k,c,key)}</div>`}).join('');
  const m=Object.entries(MERGE);
  $('#tree').innerHTML=html(root,'',0)+(m.length?`<div class="merges">merged: ${m.map(([a,b])=>esc(a)+' → '+esc(b)).join('; ')} · <a id="unmerge">undo all</a></div>`:'');
  $('#treebox').classList.toggle('has',!!F.group);
  $('#hint').textContent=merging?`merging "${merging}": click the name it belongs to (⇢ again to cancel)`:''}
$('#tree').addEventListener('click',e=>{if(e.target.id==='unmerge'){for(const k in MERGE)delete MERGE[k];save();refresh();return}
  const mg=e.target.closest('.mg');if(mg){e.preventDefault();merging=merging===mg.dataset.sp?null:mg.dataset.sp;tree();return}
  const row=e.target.closest('.row');if(!row)return;
  if(e.target.closest('.tg')&&!merging)return;                       // the arrow only folds
  e.preventDefault();
  if(merging){let to=row.dataset.sp;if(to&&to!==merging){while(MERGE[to]&&MERGE[to]!==merging)to=MERGE[to];MERGE[merging]=to;for(const k in MERGE)if(MERGE[k]===merging)MERGE[k]=to;if(MERGE[to]===merging)delete MERGE[to]}merging=null;save();refresh();return}
  F.group=F.group===row.dataset.k?'':row.dataset.k;render();tree()});
$('#treeclear').onclick=()=>{F.group='';refresh()};
/* ---- marks + selection ---- */
function paint(p){for(const c of grid.querySelectorAll(`.card[data-p="${CSS.escape(p)}"]`)){c.classList.toggle('k',DEC[p]==='k');c.classList.toggle('x',DEC[p]==='x');c.querySelector('.mk').textContent=DEC[p]==='k'?'✓':'✕'}}
function mark(paths,v,toggle){for(const p of paths){if(toggle&&DEC[p]===v)delete DEC[p];else if(v)DEC[p]=v;else delete DEC[p];paint(p)}save();
  if(F.dec){render()}else{ghead();overview()}if(cur>=0)caption()}
function select(paths,on=true){for(const p of paths)on?sel.add(p):sel.delete(p);for(const c of grid.querySelectorAll('.card'))c.classList.toggle('sel',sel.has(c.dataset.p));selbar()}
function clearSel(){sel.clear();anchor=-1;for(const c of grid.querySelectorAll('.card.sel'))c.classList.remove('sel');selbar()}
function selbar(){$('#selbar').classList.toggle('on',sel.size>0);$('#seln').textContent=sel.size+' selected'}
grid.addEventListener('click',e=>{const card=e.target.closest('.card');if(!card){clearSel();return}const p=card.dataset.p,i=+card.dataset.i;
  const b=e.target.closest('.hov button');if(b){mark(sel.has(p)&&sel.size>1?[...sel]:[p],b.classList.contains('k')?'k':'x',true);return}
  if(e.target.closest('.zoom')){open(i);return}
  if(e.shiftKey&&anchor>=0){const [a,z]=[Math.min(anchor,i),Math.max(anchor,i)];if(!e.metaKey&&!e.ctrlKey)sel.clear();select(shown.slice(a,z+1).map(r=>r.p))}
  else if(e.metaKey||e.ctrlKey){select([p],!sel.has(p));anchor=i}
  else{const only=sel.size===1&&sel.has(p);sel.clear();if(!only)sel.add(p);anchor=only?-1:i;select([])}});
grid.addEventListener('dblclick',e=>{const card=e.target.closest('.card');if(card)open(+card.dataset.i)});
$('#sel-k').onclick=()=>mark([...sel],'k');$('#sel-x').onclick=()=>mark([...sel],'x');$('#sel-u').onclick=()=>mark([...sel],null);$('#sel-c').onclick=clearSel;
$('#g-k').onclick=()=>mark(shown.map(r=>r.p),'k');$('#g-x').onclick=()=>mark(shown.map(r=>r.p),'x');$('#g-u').onclick=()=>mark(shown.map(r=>r.p),null);
$('#g-all').onclick=()=>{sel.clear();select(shown.map(r=>r.p))};
/* ---- lightbox ---- */
const lb=$('#lb'),lbi=$('#lbi');
function caption(){const r=shown[cur];if(!r)return;$('#lbn').textContent=`${cur+1} / ${shown.length}`;$('#lbf').textContent=r.p;
  $('#lbsp').innerHTML=`${r.s==null?'–':r.s.toFixed(3)} <span style="color:#fbbf24">${stars(r.st)}</span>${r.sp?' · '+esc(label(r))+(r.cn?' <i style="color:#aaa">'+esc(r.sp)+'</i>':''):''}${r.rr.length?' · <span style="color:#fca5a5">'+esc(r.rr.join(', ').replace(/_/g,' '))+'</span>':''}${r.o?' · original':r.l?' · '+esc(r.l.split('/').pop()):''}`;
  $('#lbk').classList.toggle('on',DEC[r.p]==='k');$('#lbx').classList.toggle('on',DEC[r.p]==='x')}
function open(i){if(i<0||i>=shown.length)return;cur=i;const r=shown[i],src=r.o||r.l||r.t;if(!src)return;lbi.src=src;caption();lb.classList.add('on');
  const nx=shown[i+1];if(nx){const im=new Image();im.src=nx.o||nx.l||nx.t||''}}
function close(){lb.classList.remove('on');lbi.src='';cur=-1}
$('#lbk').onclick=()=>mark([shown[cur].p],'k',true);$('#lbx').onclick=()=>mark([shown[cur].p],'x',true);$('#lbc').onclick=close;
$('#lbprev').onclick=()=>open(cur-1);$('#lbnext').onclick=()=>open(cur+1);$('#lbstage').onclick=e=>{if(e.target===e.currentTarget||e.target===lbi)close()};
document.addEventListener('keydown',e=>{if(e.target.tagName==='INPUT'||e.target.tagName==='SELECT')return;const k=e.key.toLowerCase();
  if(cur>=0){if(k==='escape')close();else if(k==='arrowright'||k===' ')open(cur+1);else if(k==='arrowleft')open(cur-1);else if(k==='k')mark([shown[cur].p],'k',true);else if(k==='x')mark([shown[cur].p],'x',true);else if(k==='u')mark([shown[cur].p],null);else return;e.preventDefault();return}
  if((e.metaKey||e.ctrlKey)&&k==='a'){sel.clear();select(shown.map(r=>r.p));e.preventDefault();return}
  if(k==='escape'){closePops();clearSel();return}
  if(!sel.size)return;if(k==='k')mark([...sel],'k',true);else if(k==='x')mark([...sel],'x',true);else if(k==='u')mark([...sel],null);else if(k==='enter'&&sel.size===1)open(shown.findIndex(r=>sel.has(r.p)));else return;e.preventDefault()});
/* ---- toolbar: search, sort, filter, more ---- */
$('#q').oninput=e=>{F.q=e.target.value.trim().toLowerCase();render()};
function closePops(){for(const p of $$('.pop'))p.classList.remove('open');for(const b of $$('.tb[data-pop]'))b.classList.toggle('on',false)}
for(const b of $$('.tb[data-pop]'))b.onclick=e=>{const p=$('#'+b.dataset.pop),was=p.classList.contains('open');closePops();if(!was){p.classList.add('open');b.classList.add('on')}e.stopPropagation()};
document.addEventListener('click',e=>{if(!e.target.closest('.pop'))closePops()});
function sortmenu(){$('#pop-sort').innerHTML=Object.entries(SORTS).map(([k,[l]])=>`<button class="item${F.sort===k?' on':''}" data-k="${k}">${l}</button>`).join('')}
$('#pop-sort').onclick=e=>{const b=e.target.closest('.item');if(!b)return;F.sort=b.dataset.k;sortmenu();render();closePops()};
function filtermenu(){const seg=(name,opts,cur,multi)=>`<h4>${name}</h4><div class="seg" data-f="${name}">${opts.map(([v,l,cls])=>`<button class="${cls||''}${(multi?cur.has(v):cur===v)?' on':''}" data-v="${esc(v)}">${l}</button>`).join('')}</div>`;
  const spOpts=Object.values(Object.fromEntries(LIVE().map(eff).filter(r=>r.sp).map(r=>[r.sp,{sp:r.sp,cn:r.cn,lv:r.lv}]))).sort((a,b)=>label(a).localeCompare(label(b)));
  $('#pop-filter').innerHTML=seg('stars',[[5,'★★★★★','st'],[4,'★★★★','st'],[3,'★★★','st'],[2,'★★','st'],[1,'★','st']],F.stars,true)
    +seg('marks',[['','all'],['k','keep'],['x','drop'],['u','unmarked']],F.dec)
    +seg('rejects',[['','all'],['clean','none'],['any','any'],...REASONS.map(r=>[r,r.replace(/_/g,' ')])],F.rej)
    +(SCENES.length>1?seg('scene',SCENES.map(s=>[s,s]),F.scenes,true):'')
    +`<h4>species</h4><select id="f-sp"><option value="">all</option><option value="named">named</option><option value="unnamed">unnamed</option>${spOpts.map(b=>`<option value="${esc(b.sp)}">${esc(label(b))}</option>`).join('')}</select>`
    +(DIRS.length?`<h4>folder</h4><select id="f-dir"><option value="">all</option>${DIRS.map(d=>`<option>${esc(d)}</option>`).join('')}</select>`:'')
    +`<hr><button class="item" id="f-clear">clear all filters</button>`;
  $('#f-sp').value=F.sp;if(DIRS.length)$('#f-dir').value=F.dir;
  $('#f-sp').onchange=e=>{F.sp=e.target.value;render();filtermenu()};if(DIRS.length)$('#f-dir').onchange=e=>{F.dir=e.target.value;render();filtermenu()};
  $('#f-clear').onclick=()=>{Object.assign(F,{dir:'',stars:new Set(),rej:'',scenes:new Set(),sp:'',dec:'',q:''});$('#q').value='';render();filtermenu();closePops()};
  $('#btn-filter').querySelector('.dot').style.display=(F.dir||F.stars.size||F.rej||F.scenes.size||F.sp||F.dec)?'':'none'}
$('#pop-filter').onclick=e=>{const b=e.target.closest('.seg button');if(!b)return;const f=b.closest('.seg').dataset.f,v=b.dataset.v;
  if(f==='stars'){const n=+v;F.stars.has(n)?F.stars.delete(n):F.stars.add(n)}else if(f==='scene'){F.scenes.has(v)?F.scenes.delete(v):F.scenes.add(v)}else if(f==='marks')F.dec=v;else if(f==='rejects')F.rej=v;
  render();filtermenu()};
$('#hist').onclick=e=>{const b=e.target.closest('button');if(!b)return;const n=+b.dataset.s;F.stars.has(n)?F.stars.delete(n):F.stars.add(n);render();filtermenu()};
function chips(){const c=[];const add=(l,fn)=>c.push({l,fn});
  if(F.group)add(F.group.split('/').pop(),()=>{F.group='';tree()});if(F.sp)add(F.sp==='named'?'named':F.sp==='unnamed'?'unnamed':label(BY[F.sp]||{sp:F.sp}),()=>F.sp='');
  if(F.stars.size)add([...F.stars].sort().reverse().map(s=>s+'★').join(' '),()=>F.stars.clear());if(F.dec)add({k:'keep',x:'drop',u:'unmarked'}[F.dec],()=>F.dec='');
  if(F.rej)add('rejects: '+F.rej.replace(/_/g,' '),()=>F.rej='');if(F.scenes.size)add([...F.scenes].join(', '),()=>F.scenes.clear());if(F.dir)add(F.dir,()=>F.dir='');
  $('#active').innerHTML=c.map((x,i)=>`<span class="chip"><b>${esc(x.l)}</b><span class="x" data-i="${i}">×</span></span>`).join('');
  $('#active').onclick=e=>{const x=e.target.closest('.x');if(!x)return;c[+x.dataset.i].fn();render();filtermenu();tree()}}
$('#btn-tree').onclick=()=>{$('#body').classList.toggle('notree');$('#btn-tree').classList.toggle('on')};
$('#size').oninput=e=>document.documentElement.style.setProperty('--col',e.target.value+'px');
$('#export').onclick=()=>{const out={schema:1,root:ROOT,created:new Date().toISOString(),keep:DATA.filter(r=>DEC[r.p]==='k').map(r=>r.p),drop:DATA.filter(r=>DEC[r.p]==='x').map(r=>r.p),merge:MERGE};
  const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(out,null,1)],{type:'application/json'}));a.download='bioscan-decisions.json';a.click();URL.revokeObjectURL(a.href);closePops()};
$('#import').onchange=e=>{const f=e.target.files[0];if(!f)return;f.text().then(t=>{const d=JSON.parse(t);for(const p of d.keep||[])DEC[p]='k';for(const p of d.drop||[])DEC[p]='x';Object.assign(MERGE,d.merge||{});save();refresh()}).catch(()=>{});e.target.value='';closePops()};
$('#clearmarks').onclick=()=>{if(!Object.keys(DEC).length)return;for(const k in DEC)delete DEC[k];save();refresh();closePops()};
function refresh(){render();tree();filtermenu()}
/* ---- export keeps / delete drops on disk (File System Access API: Chrome, Edge) ---- */
const FSA=typeof window.showDirectoryPicker==='function';let srcDir=null,toastT=0;
function toast(msg,ms){const t=$('#toast');clearTimeout(toastT);t.textContent=msg;t.classList.toggle('on',!!msg);if(msg&&ms)toastT=setTimeout(()=>t.classList.remove('on'),ms)}
function confirmBox(title,text,yes){return new Promise(res=>{$('#mt').textContent=title;$('#mp').textContent=text;$('#myes').textContent=yes;$('#modal').classList.add('on');$('#modal').classList.toggle('del',yes==='Delete');
  const done=v=>{$('#modal').classList.remove('on');$('#myes').onclick=$('#mno').onclick=null;res(v)};$('#myes').onclick=()=>done(true);$('#mno').onclick=()=>done(false)})}
const relOf=r=>r.p.startsWith(ROOT+'/')?r.p.slice(ROOT.length+1):r.p.split('/').pop();
const sidecars=name=>[name.replace(/\.[^.]+$/,'')+'.xmp',name+'.xmp'];
async function walk(dir,rel){const parts=rel.split('/');let d=dir;for(const p of parts.slice(0,-1))d=await d.getDirectoryHandle(p);return[d,parts[parts.length-1]]}
async function granted(mode){return !!srcDir&&(await srcDir.queryPermission({mode}))==='granted'}
async function source(mode){                                   // called right after a click: each picker needs its own user gesture
  if(srcDir&&(await granted(mode)||(await srcDir.requestPermission({mode}))==='granted'))return srcDir;
  const dir=await showDirectoryPicker({mode,id:'bioscan-photos'});
  const probe=LIVE()[0];if(probe){try{const[d,n]=await walk(dir,relOf(probe));await d.getFileHandle(n)}catch(e){throw new Error(`That folder has no ${relOf(probe)}; choose ${ROOT}`)}}
  return srcDir=dir}
async function exportKeeps(){const keeps=LIVE().filter(r=>DEC[r.p]==='k');if(!keeps.length)return;
  try{if(!await granted('read')){if(!await confirmBox('Export keeps · step 1 of 2',`Choose the folder that holds the photos: ${ROOT}`,'Choose photo folder…'))return;await source('read')}
    if(!await confirmBox('Export keeps · step 2 of 2',`Choose the folder to copy ${keeps.length} kept photo${keeps.length===1?'':'s'} (and their XMP sidecars) into.`,'Choose destination…'))return;
    const src=srcDir,dst=await showDirectoryPicker({mode:'readwrite',id:'bioscan-export'});
    let n=0,skip=0,side=0;
    for(const r of keeps){const[d,name]=await walk(src,relOf(r));
      for(const nm of[name,...sidecars(name)]){let fh;try{fh=await d.getFileHandle(nm)}catch(e){continue}
        let there=true;try{await dst.getFileHandle(nm)}catch(e){there=false}if(there){if(nm===name)skip++;continue}
        const out=await dst.getFileHandle(nm,{create:true});await(await fh.getFile()).stream().pipeTo(await out.createWritable());nm===name?n++:side++}
      toast(`copying… ${n+skip} / ${keeps.length}`)}
    toast(`copied ${n} photo${n===1?'':'s'}${side?` and ${side} XMP sidecar${side===1?'':'s'}`:''} to “${dst.name}”${skip?`; ${skip} already there, left as is`:''}`,8000)}
  catch(e){toast(e.name==='AbortError'?'':e.message,8000)}}
async function deleteDrops(){const drops=LIVE().filter(r=>DEC[r.p]==='x');if(!drops.length)return;
  if(!await confirmBox(`Delete ${drops.length} dropped photo${drops.length===1?'':'s'} from disk?`,`The original files and their XMP sidecars are removed under ${ROOT}. This cannot be undone from here.${srcDir?'':' The folder picker opens next: choose '+ROOT+'.'}`,'Delete'))return;
  try{const src=await source('readwrite');let n=0,miss=0;         // the confirm click is the gesture for the picker
    for(const r of drops){const[d,name]=await walk(src,relOf(r));try{await d.removeEntry(name);n++}catch(e){miss++}
      for(const nm of sidecars(name)){try{await d.removeEntry(nm)}catch(e){}}GONE.add(r.p);delete DEC[r.p];toast(`deleting… ${n+miss} / ${drops.length}`)}
    save();refresh();toast(`deleted ${n} photo${n===1?'':'s'}${miss?`; ${miss} already gone`:''}`,8000)}
  catch(e){save();refresh();toast(e.name==='AbortError'?'':e.message,8000)}}
$('#exp').onclick=exportKeeps;$('#del').onclick=deleteDrops;
if(!FSA)$('#exp').title=$('#del').title='needs Chrome or Edge; save the decisions file (⋯) and run bioscan aesthetic apply instead';
if(!DATA.some(r=>r.sp)){$('#body').classList.add('notree');$('#btn-tree').classList.remove('on')}
sortmenu();refresh();
"""


def write_html(rows: list[dict[str, Any]], fails: list[dict[str, Any]], path: str, title: str) -> None:
    base = Path(path).resolve().parent
    roots = os.path.commonpath([r["path"] for r in rows]) if len(rows) > 1 else str(Path(rows[0]["path"]).parent) \
        if rows else ""
    data = page_rows(rows, base, roots)
    dirs = sorted({d["d"] for d in data if d["d"]})
    scenes = sorted({d["sc"] for d in data if d.get("sc")})
    reasons = sorted({x for d in data for x in d["rr"]})
    scored = [r["score"] for r in rows if r["score"] is not None]
    n = len(scored)
    cuts = [scored[int(n * k / 5) - 1] for k in range(1, 5)] if n >= 5 else []     # lowest score of 5, 4, 3, 2 stars
    named = sum(1 for r in rows if r["species"])
    sub = (f"{len(scored)} scored" + (f", {named} named" if named else "")
           + (f", {len(fails)} failed" if fails else ""))
    i = ICONS
    page = (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,'
            f'initial-scale=1"><title>{html.escape(title)}</title><style>{CSS}</style></head><body>'
            f'<header class="top"><div class="brand">bioscan<small>{len(rows)} photos</small></div>'
            f'<div class="search">{i["search"]}<input id="q" type="search" placeholder="file, species or common name"></div>'
            '<div class="active" id="active"></div>'
            f'<div class="tools"><button class="tb" id="btn-sort" data-pop="pop-sort" title="sort">{i["sort"]}</button>'
            f'<button class="tb" id="btn-filter" data-pop="pop-filter" title="filter">{i["filter"]}<span class="dot"></span></button>'
            f'<button class="tb on" id="btn-tree" title="overview and taxon tree">{i["tree"]}</button>'
            f'<button class="tb" id="btn-more" data-pop="pop-more" title="more">{i["more"]}</button>'
            '<div class="pop" id="pop-sort"></div><div class="pop" id="pop-filter" style="min-width:300px"></div>'
            '<div class="pop" id="pop-more"><h4>decisions file (for bioscan aesthetic apply)</h4><button class="item" id="export">save bioscan-decisions.json</button>'
            '<label class="item" style="cursor:pointer">load a decisions file<input type="file" id="import" accept=".json" hidden></label>'
            '<button class="item" id="clearmarks">clear all marks</button><hr><h4>thumbnail size</h4>'
            '<input type="range" id="size" min="140" max="360" step="10" value="200"><hr>'
            '<h4>keys</h4><div style="padding:4px 10px;color:var(--ink2);font-size:12px;line-height:1.9">click selects · <kbd>⇧</kbd> click ranges · <kbd>⌘</kbd> click adds · <kbd>⌘A</kbd> all shown<br>'
            '<kbd>K</kbd> keep · <kbd>X</kbd> drop · <kbd>U</kbd> unmark · <kbd>↵</kbd> / double-click opens<br>'
            'in the lightbox: <kbd>←</kbd> <kbd>→</kbd> move · <kbd>Esc</kbd> closes</div></div></div></header>'
            '<div class="body" id="body"><aside><div class="ov"><div class="big" id="ovn">' + str(len(rows)) + ' photos</div>'
            f'<div class="sub">{html.escape(sub)}</div><div class="bar" id="ovbar"></div><div class="leg" id="ovleg"></div>'
            '<div class="hist" id="hist"></div><div class="ovacts"><button class="btn k" id="exp">Export keeps…</button>'
            '<button class="btn x" id="del">Delete drops…</button></div></div>'
            '<div class="tree" id="treebox"><h4>taxa <a class="cl" id="treeclear">show all</a></h4><div id="tree"></div>'
            '<div class="hint" id="hint"></div></div></aside>'
            '<main><div class="ghead"><h2 id="gtitle"></h2><span class="m" id="gmeta"></span>'
            '<div class="acts"><button class="btn q" id="g-all">select all</button><button class="btn k" id="g-k">keep all</button>'
            '<button class="btn x" id="g-x">drop all</button><button class="btn q" id="g-u">unmark</button></div></div>'
            '<div class="grid" id="grid" tabindex="0"></div></main></div>'
            '<div class="selbar" id="selbar"><b id="seln"></b><button class="k" id="sel-k">keep</button><button class="x" id="sel-x">drop</button>'
            '<button id="sel-u">unmark</button><button class="c" id="sel-c">✕</button></div>'
            '<div id="toast"></div><div id="modal"><div class="box"><h3 id="mt"></h3><p id="mp"></p><div class="mb"><button class="btn" id="mno">Cancel</button>'
            '<button class="btn danger" id="myes"></button></div></div></div>'
            '<div id="lb"><div class="stage" id="lbstage"><img id="lbi" alt=""><div class="nav prev" id="lbprev">‹</div><div class="nav next" id="lbnext">›</div>'
            '<button class="close" id="lbc">×</button></div><div class="bar"><span id="lbn"></span><span class="f" id="lbf"></span>'
            '<span class="sp" id="lbsp"></span><button class="k" id="lbk">keep</button><button class="x" id="lbx">drop</button></div></div>'
            f"<script>const DATA={json.dumps(data, ensure_ascii=False, separators=(',', ':'))};"
            f"const ROOT={json.dumps(roots, ensure_ascii=False)};const DIRS={json.dumps(dirs, ensure_ascii=False)};"
            f"const SCENES={json.dumps(scenes)};const CUTS={json.dumps([round(c, 4) for c in cuts])};const REASONS={json.dumps(reasons)};const ICON_ZOOM={json.dumps(i['zoom'])};"
            f"{JS}</script></body></html>\n")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(page, encoding="utf-8")
