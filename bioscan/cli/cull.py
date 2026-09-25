"""`bioscan cull DIR`: run a folder through the album profile, then the burst and select reducers
(bioscan/cull.py) here in the CLI, and write what to keep: a selection CSV, a folder of symlinks per
category, an HTML review page and the reduced results as NDJSON. `--preds FILE` reduces a saved
NDJSON (`bioscan run --json`, a previous `cull --json`) offline, without the service.

Nothing is ever deleted or moved: rejects are only listed with their reasons. `--xmp` writes new XMP
sidecars (stars, a colour label) only where a photo has none. Standard library only, like the rest of
the CLI."""
from __future__ import annotations

import csv
import html
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

from bioscan import contract, cull, formats, profile, xmp
from bioscan.cli import client
from bioscan.cli.config import expand, load_config, request_options

DEFAULT_PROFILE = "album"
DEFAULT_REDUCERS = ("burst", "select")
BROWSER_IMAGES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"}
EXIT_OK, EXIT_PARTIAL, EXIT_INCOMPLETE = 0, 1, 3
PREDS_SCHEMA = 1


def resolve(a, config: profile.Config | None = None) -> profile.Resolved:
    """The profile (--profile, default album) under the flags."""
    reducer_flags = {"select": {"per_category": a.per_category}} if a.per_category is not None else None
    return expand(config or load_config(), a.profile or DEFAULT_PROFILE, None, {}, reducer_flags)


def thumbs_dir(html_path: str) -> str:
    """Where the service writes the page's upright JPEG copies: `<page stem>-files/` next to it."""
    p = Path(html_path).resolve()
    return str(p.with_name(p.stem + "-files"))


def build_request(a, res: profile.Resolved) -> dict[str, Any]:
    """The /run body: the profile's stages and the options its files set, plus jpg copies for the
    HTML page when one is asked for (and --no-thumbs is not)."""
    exts = formats.parse_ext(a.ext)
    paths = [p for root in a.paths for p in formats.list_images(root, exts, a.recursive)]
    if not paths:
        raise SystemExit("no images found")
    want, options = list(res.want), request_options(res)
    if a.html and not a.no_thumbs:
        want = [*want, "jpg"] if "jpg" not in want else want
        options["jpg"] = {"out_dir": thumbs_dir(a.html)}
    return {"inputs": [{"path": p} for p in paths], "want": want, "options": options}


def reducer_run(res: profile.Resolved) -> dict[str, dict[str, Any]]:
    """The profile's reducers with their options; burst and select when it names none."""
    names = res.reducers or list(DEFAULT_REDUCERS)
    return {r: res.reducer_options[r] for r in names}


def warnings_for(want: list[str]) -> list[str]:
    out = []
    if "quality" not in want:
        out.append("the profile has no quality stage: nothing is rejected, bursts pick the first frame")
    if "embed" not in want:
        out.append("the profile has no embed stage: no bursts and no near-duplicates")
    if "scene" not in want:
        out.append("the profile has no scene stage: one category for everything")
    return out


def fetch(payload: dict[str, Any], url: str) -> tuple[list[dict[str, Any]], bool]:
    """(result/error/done events, whether `done` arrived), with progress on stderr."""
    events, n, total = [], 0, len(payload["inputs"])
    for line in client.run(payload, url):
        ev = json.loads(line)
        if ev.get("type") in (contract.RESULT, contract.ERROR, contract.DONE):
            events.append(ev)
            n += ev.get("type") != contract.DONE
            print(f"\r{n}/{total}", end="", file=sys.stderr, flush=True)
    print(file=sys.stderr)
    return events, any(e.get("type") == contract.DONE for e in events)


def read_ndjson(path: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """(meta line or {}, result/error/done events) of a saved run."""
    meta, events = {}, []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            ev = json.loads(line)
            if ev.get("type") == "meta":
                meta = ev
            elif ev.get("type") in (contract.RESULT, contract.ERROR, contract.DONE):
                events.append(ev)
    return meta, events


# ---- outputs -----------------------------------------------------------------------------------

def failed(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per image without a result (its error messages joined), in event order."""
    ok = {e["path"] for e in events if e.get("type") == contract.RESULT}
    rows: dict[str, list[str]] = {}
    for e in events:
        if e.get("type") == contract.ERROR and e["path"] not in ok:
            rows.setdefault(e["path"], []).append(f"{e.get('product') or 'decode'}: {e.get('message')}")
    return [{"path": p, "messages": m} for p, m in rows.items()]


def write_csv(records: list[dict[str, Any]], fails: list[dict[str, Any]], path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cull.RECORD_FIELDS)
        w.writeheader()
        for r in records:
            w.writerow({**r, "keep": int(r["keep"]), "reasons": ";".join(r["reasons"]), "waived": ";".join(r["waived"])})
        for r in fails:
            w.writerow({"path": r["path"], "status": "failed", "keep": 0, "reasons": " | ".join(r["messages"])})


def write_links(records: list[dict[str, Any]], out: str) -> int:
    """A symlink per pick in `<out>/<category>/`; an existing file is never replaced (a clash gets
    `-2`, `-3`, ... before its suffix; a link to the same photo is left as it is). Returns links made."""
    made = 0
    for r in records:
        if not r["keep"]:
            continue
        src = Path(r["path"])
        folder = Path(out) / (r["category"] or cull.UNCATEGORISED)
        folder.mkdir(parents=True, exist_ok=True)
        target, n = folder / src.name, 1
        while target.is_symlink() or target.exists():
            if target.is_symlink() and os.readlink(target) == str(src):
                break
            n += 1
            target = folder / f"{src.stem}-{n}{src.suffix}"
        else:
            target.symlink_to(src)
            made += 1
    return made


# `--xmp`: the selection as stars and colour labels a photo editor filters on. Every pick is already its
# burst's best, so a burst win earns no extra star. Rejects get no stars (unrated). The bioscan
# namespace marks the sidecar, so `bioscan aesthetic` never reads these stars as the owner's.
XMP_STARS = {"pick": 3, "spare": 2}
XMP_REJECT_LABEL = "Red"
XMP_NS = xmp.CULL_NS


def xmp_packet(r: dict[str, Any]) -> str | None:
    """The sidecar for one cull record: picks 3 stars, spares 2, rejects the Red label and their
    reasons; None for a duplicate (nothing to write)."""
    status = r["status"]
    if status in XMP_STARS:
        props = [f'xmp:Rating="{XMP_STARS[status]}"']
    elif status == "reject":
        props = [f'xmp:Label="{XMP_REJECT_LABEL}"',
                 f'bioscan:reasons="{html.escape(";".join(r["reasons"]))}"']
    else:
        return None
    return xmp.packet("bioscan cull", ['xmlns:xmp="http://ns.adobe.com/xap/1.0/"', f'xmlns:bioscan="{XMP_NS}"',
                                       *props, f'bioscan:status="{html.escape(status)}"'])


def write_xmp(records: list[dict[str, Any]]) -> Counter:
    """A new `<stem>.xmp` per pick, spare and reject; a photo that has a sidecar is left alone.
    Counts by outcome: written, exists, missing (the photo is not on this disk, e.g. an old --preds)."""
    done: Counter = Counter()
    for r in records:
        text = xmp_packet(r)
        if text is None:
            continue
        done[xmp.write_new(r["path"], text) if Path(r["path"]).is_file() else "missing"] += 1
    return done


def write_json(meta: dict[str, Any], events: list[dict[str, Any]], path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for e in [meta, *events]:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


def summary(records: list[dict[str, Any]], fails: list[dict[str, Any]]) -> str:
    status = Counter(r["status"] for r in records)
    reasons = Counter(x for r in records if r["status"] == "reject" for x in r["reasons"])
    bursts = {r["burst"] for r in records if r["burst"]}
    cats = Counter(r["category"] for r in records)
    picks = Counter(r["category"] for r in records if r["keep"])
    lines = [f"{len(records) + len(fails)} photos: " + ", ".join(f"{status[s]} {s}" for s in cull.STATUSES)
             + f", {len(fails)} failed",
             f"bursts: {len(bursts)} ({sum(1 for r in records if r['burst'])} frames)",
             "categories: " + ", ".join(f"{c} {picks[c]}/{n} picked" for c, n in sorted(cats.items())) if cats else "",
             "reject reasons: " + (", ".join(f"{k} {v}" for k, v in reasons.most_common()) or "none")]
    return "\n".join(x for x in lines if x)


# ---- HTML review page --------------------------------------------------------------------------

CSS = """
:root { --bg: #fafaf9; --fg: #1c1917; --muted: #78716c; --card: #fff; --line: #e7e5e4; --pick: #15803d;
  --reject: #b91c1c; --dup: #a16207; }
@media (prefers-color-scheme: dark) { :root { --bg: #1c1917; --fg: #f5f5f4; --muted: #a8a29e; --card: #292524;
  --line: #44403c; --pick: #4ade80; --reject: #f87171; --dup: #facc15; } }
* { box-sizing: border-box; }
body { margin: 0; padding: 16px; background: var(--bg); color: var(--fg); font: 14px/1.4 system-ui, sans-serif; }
h1 { font-size: 20px; margin: 0 0 4px; } h2 { font-size: 17px; margin: 28px 0 8px; } h3 { font-size: 14px; margin: 16px 0 6px; }
nav a { margin-right: 12px; color: inherit; } .muted { color: var(--muted); }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(170px, 1fr)); gap: 10px; }
.row { display: flex; gap: 8px; overflow-x: auto; padding-bottom: 4px; } .row figure { flex: 0 0 150px; }
figure { margin: 0; background: var(--card); border: 1px solid var(--line); border-radius: 6px; overflow: hidden; }
figure img, figure .noimg { display: block; width: 100%; aspect-ratio: 3 / 2; object-fit: cover; background: var(--line); }
figure .noimg { display: flex; align-items: center; justify-content: center; font-size: 12px; color: var(--muted); }
figcaption { padding: 5px 7px; font-size: 12px; word-break: break-all; }
.pick { border-color: var(--pick); } .reject { opacity: .75; } .duplicate, .spare { opacity: .8; }
.tag { font-weight: 600; } .tag.pick { color: var(--pick); } .tag.reject { color: var(--reject); }
.tag.duplicate { color: var(--dup); } details { margin-top: 8px; }
"""


def _src(ev: dict[str, Any] | None, path: str, base: Path) -> str | None:
    """The image to show: the service's jpg copy, else the photo itself when a browser can show it."""
    jpg = (cull.products(ev or {}).get("jpg") or {}).get("path")
    shown = jpg or (path if Path(path).suffix.lower() in BROWSER_IMAGES else None)
    return None if shown is None else os.path.relpath(shown, base)


def _figure(r: dict[str, Any], ev: dict[str, Any] | None, base: Path, note: str = "") -> str:
    src = _src(ev, r["path"], base)
    img = (f'<img loading="lazy" src="{html.escape(src)}" alt="">' if src
           else f'<div class="noimg">{html.escape(Path(r["path"]).suffix.upper().lstrip("."))}</div>')
    tag = f'<span class="tag {r["status"]}">{html.escape(r["status"] or "")}</span>'
    return (f'<figure class="{html.escape(r["status"] or "")}" title="{html.escape(r["path"])}">{img}<figcaption>'
            f'{tag} {html.escape(Path(r["path"]).name)}{" · " + html.escape(note) if note else ""}</figcaption></figure>')


def write_html(records: list[dict[str, Any]], events: list[dict[str, Any]], fails: list[dict[str, Any]], path: str,
               title: str = "bioscan cull") -> None:
    base = Path(path).resolve().parent
    by_path = {e["path"]: e for e in events if e.get("type") == contract.RESULT}
    cats = sorted({r["category"] for r in records})
    parts = [f"<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" "
             f"content=\"width=device-width, initial-scale=1\"><title>{html.escape(title)}</title><style>{CSS}</style>"
             f"</head><body><h1>{html.escape(title)}</h1><p class=\"muted\">"
             f"{html.escape(summary(records, fails)).replace(chr(10), '<br>')}</p><nav>"
             + "".join(f'<a href="#cat-{html.escape(c)}">{html.escape(c)}</a>' for c in cats)
             + '<a href="#bursts">bursts</a><a href="#rejects">rejects</a>'
             + ('<a href="#failed">failed</a>' if fails else "") + "</nav>"]
    for c in cats:
        mine = [r for r in records if r["category"] == c]
        picks = sorted((r for r in mine if r["keep"]), key=lambda r: r["rank"] or 0)
        spares = sorted((r for r in mine if r["status"] == "spare"), key=lambda r: r["rank"] or 0)
        parts.append(f'<h2 id="cat-{html.escape(c)}">{html.escape(c)} <span class="muted">{len(picks)} picked of '
                     f'{len(mine)}</span></h2><div class="grid">'
                     + "".join(_figure(r, by_path.get(r["path"]), base, f"#{r['rank']}") for r in picks) + "</div>")
        if spares:
            parts.append(f"<details><summary>{len(spares)} more keepers (spares)</summary><div class=\"grid\">"
                         + "".join(_figure(r, by_path.get(r["path"]), base, f"#{r['rank']}") for r in spares)
                         + "</div></details>")
    bursts: dict[str, list[dict[str, Any]]] = {}
    for r in records:
        if r["burst"]:
            bursts.setdefault(r["burst"], []).append(r)
    parts.append(f'<h2 id="bursts">Bursts <span class="muted">{len(bursts)}</span></h2>')
    for bid, frames in sorted(bursts.items()):
        frames.sort(key=lambda r: (r["taken_at"] or "", r["path"]))
        parts.append(f"<h3>{html.escape(bid)} <span class=\"muted\">{len(frames)} frames</span></h3><div class=\"row\">"
                     + "".join(_figure(r, by_path.get(r["path"]), base, f"burst #{r['burst_rank']}") for r in frames)
                     + "</div>")
    rejects = [r for r in records if r["status"] == "reject"]
    parts.append(f'<h2 id="rejects">Rejects <span class="muted">{len(rejects)}; nothing is deleted</span></h2>')
    for reason in sorted({x for r in rejects for x in r["reasons"]}):
        mine = [r for r in rejects if reason in r["reasons"]]
        parts.append(f"<h3>{html.escape(reason)} <span class=\"muted\">{len(mine)}</span></h3><div class=\"grid\">"
                     + "".join(_figure(r, by_path.get(r["path"]), base, ", ".join(r["reasons"])) for r in mine)
                     + "</div>")
    if fails:
        parts.append('<h2 id="failed">Failed</h2><ul>' + "".join(
            f"<li>{html.escape(f['path'])}: {html.escape(' | '.join(f['messages']))}</li>" for f in fails) + "</ul>")
    parts.append("</body></html>\n")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("".join(parts), encoding="utf-8")


# ---- command ----------------------------------------------------------------------------------

def cmd_cull(a) -> int:
    res = resolve(a)
    if a.preds:
        meta, events = read_ndjson(a.preds)
        complete = any(e.get("type") == contract.DONE for e in events)
        head = {**meta, "type": "meta", "schema": PREDS_SCHEMA} if meta else {
            "type": "meta", "schema": PREDS_SCHEMA, "profile": None, "options": None}
    else:
        payload = build_request(a, res)
        for w in warnings_for(payload["want"]):
            print(f"warning: {w}", file=sys.stderr)
        events, complete = fetch(payload, a.url)
        head = {"type": "meta", "schema": PREDS_SCHEMA, "profile": res.profile, "options": payload["options"],
                "created": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    reducers = reducer_run(res)
    head["reducers"] = reducers
    try:
        reduced = cull.apply(events, reducers)
    except ValueError as e:
        raise SystemExit(f"error: {e}") from None
    records = cull.records(reduced)
    fails = failed(reduced)
    if a.json:
        write_json(head, reduced, a.json)
    if a.csv:
        write_csv(records, fails, a.csv)
    if a.link_dir:
        print(f"{write_links(records, a.link_dir)} new links -> {a.link_dir}")
    if a.xmp:
        done = write_xmp(records)
        missing = f", {done['missing']} photos not found" if done["missing"] else ""
        print(f"xmp: {done[xmp.WRITTEN]} sidecars written, {done[xmp.EXISTS]} left alone (a sidecar exists){missing}")
    if a.html:
        write_html(records, reduced, fails, a.html, f"bioscan cull: {len(records) + len(fails)} photos")
    print(summary(records, fails))
    for flag, what in ((a.csv, "selection CSV"), (a.html, "review page"), (a.json, "results NDJSON")):
        if flag:
            print(f"{what} -> {flag}")
    if not complete:
        print("error: the stream ended before the service's `done`; images after the cut are missing",
              file=sys.stderr)
        return EXIT_INCOMPLETE
    return EXIT_PARTIAL if fails else EXIT_OK


def add_parser(sub) -> None:
    s = sub.add_parser("cull", help="album culling: rejects with reasons, bursts, best frames per category "
                                    "(HTML review, selection CSV, symlink folders); never deletes")
    s.add_argument("paths", nargs="*", help="files or directories (not with --preds)")
    s.add_argument("--profile", help=f"profile to run (default {DEFAULT_PROFILE}); its reducers run here")
    s.add_argument("--per-category", type=int, metavar="N", help="picks per scene category (select.per_category; "
                                                                  "0 = every keeper)")
    s.add_argument("--json", metavar="OUT", help="write the results with the reducers' products as NDJSON")
    s.add_argument("--csv", metavar="OUT", help="write the selection CSV (one row per photo)")
    s.add_argument("--html", metavar="OUT", help="write an HTML review page (thumbnails: upright JPEGs the service "
                                                 "writes to OUT's <stem>-files/ folder)")
    s.add_argument("--no-thumbs", action="store_true", help="HTML without JPEG copies (JPEG/PNG inputs show as they are)")
    s.add_argument("--link-dir", metavar="OUT", help="symlink each pick into OUT/<category>/ (never replaces a file)")
    s.add_argument("--xmp", action="store_true",
                   help="write <stem>.xmp for each photo without a sidecar: picks 3 stars, spares 2, rejects the "
                        "Red label with their reasons (an existing sidecar and the photo are never changed)")
    s.add_argument("--preds", metavar="FILE", help="reduce a saved NDJSON (bioscan run --json, cull --json) offline")
    s.add_argument("-r", "--recursive", action="store_true")
    s.add_argument("--ext", default=formats.DEFAULT_EXT)
    s.set_defaults(func=cmd_cull_checked)


def cmd_cull_checked(a) -> int:
    if bool(a.preds) == bool(a.paths):
        raise SystemExit("give photo folders or --preds FILE (one of them)")
    if a.per_category is not None and a.per_category < 0:
        raise SystemExit("--per-category must be >= 0")
    return cmd_cull(a)
