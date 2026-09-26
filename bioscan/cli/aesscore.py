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
and reject reasons come from the album profile's scene and quality stages; flags (horizon_tilt: a
landscape's horizon tilts more than the profile's `select.horizon_flag_deg`; tight_headroom: the
subject's box starts within `select.headroom_min` of the frame top) only mark a photo, they never
change its stars. `--species` turns the
profile's species naming on, so the CSV and the page also carry each photo's surest name (species,
genus or family, as `bioscan summarize` counts it) with an English name beside it (`rp.common_of`: the common name, or
"a vireo" / "a hawk or eagle" above species), and the page groups and filters by it. The page
keeps its marks in the browser and exports them as `bioscan-decisions.json`; `bioscan aesthetic apply`
copies the keeps to a folder and/or moves the drops to another (with their XMP sidecars). Nothing is
ever deleted. Standard library only, like the rest of the CLI."""
from __future__ import annotations

import csv
import json
import sys
import time
from pathlib import Path
from typing import Any

from bioscan import aesthetic, contract, cull, formats
from bioscan import apply as applying
from bioscan.cli import cull as cc
from bioscan.cli import report as rp
from bioscan.cli.aespage import page_rows, thumb_of, write_html  # noqa: F401  (page_rows: tests)
from bioscan.cli.config import expand, load_config, request_options

FORMATS = ("json", "csv", "html")
THUMB_EDGE = 1024
EDGE = 3072
DEFAULT_OUT = "aesthetic-scores"
CSV_FIELDS = ("rank", "path", "score", "stars", "scene", "species", "common", "level", "reject_reasons", "flags",
              "sharpness", "taken_at")


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
                best = (top["posterior"], name, rp.common_of(sp), level, lineage)
    return best[1:] if best else (None, None, None, [])


def rows_of(events: list[dict[str, Any]], select: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """One row per result, best first (a photo without a finite score comes last); stars = quintile;
    flags as select sets them with its options `select` (cull.flags; None = the defaults)."""
    rows = []
    for ev in events:
        if ev.get("type") != contract.RESULT:
            continue
        p = cull.products(ev)
        a, q, sc = p.get("aesthetics") or {}, p.get("quality") or {}, p.get("scene") or {}
        s = a.get("score")
        score = float(s) if isinstance(s, (int, float)) and not isinstance(s, bool) and s == s else None
        species, common, level, lineage = named(ev)
        scene = f"{sc['label']} ({sc['group']})" if sc.get("group") not in (None, sc.get("label")) else sc.get("label")
        rows.append({"path": ev["path"], "score": score, "scene": scene,
                     "species": species, "common": common, "level": level, "lineage": lineage,
                     "reject_reasons": list(q.get("reject_reasons") or []), "flags": cull.flags(ev, select),
                     "sharpness": (q.get("frame") or {}).get("sharpness"), "taken_at": cull.capture(ev)[0],
                     "jpg": (p.get("jpg") or {}).get("path"), "note": a.get("note")})
    rows.sort(key=lambda r: (r["score"] is None, -(r["score"] or 0), r["path"]))
    stars = iter(aesthetic.quintile_stars([r["score"] for r in rows if r["score"] is not None])[0])
    for i, r in enumerate(rows):
        r["rank"] = i + 1
        r["stars"] = next(stars) if r["score"] is not None else None
    return rows


def cuts(rows: list[dict[str, Any]]) -> list[float]:
    """The lowest score of stars 5, 4, 3, 2 (the quintile boundaries)."""
    return aesthetic.quintile_stars([r["score"] for r in rows if r["score"] is not None])[1]


def write_csv(rows: list[dict[str, Any]], fails: list[dict[str, Any]], path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({**r, "score": "" if r["score"] is None else f"{r['score']:.4f}", "stars": r["stars"] or "",
                        "reject_reasons": ";".join(r["reject_reasons"]), "flags": ";".join(r["flags"])})
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
    select = expand(load_config(), cc.DEFAULT_PROFILE, None, {}).reducer_options["select"]
    rows, fails = rows_of(events, select), cc.failed(events)
    written = []
    if "json" in exports:
        cc.write_json(head, events, f"{out}.ndjson")
        written.append(f"{out}.ndjson")
    if "csv" in exports:
        write_csv(rows, fails, f"{out}.csv")
        written.append(f"{out}.csv")
    if "html" in exports:
        write_html(rows, fails, f"{out}.html", f"bioscan aesthetic score: {len(rows) + len(fails)} photos",
                   service=a.url, token=applying.token())
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
    keep, drop = dec.get("keep") or [], dec.get("drop") or []
    r = applying.apply(keep if a.keep_to else [], drop if a.drop_to else [], keep_to=a.keep_to, drop_to=a.drop_to,
                       dry_run=a.dry_run)
    for q in r["missing"]:
        print(f"missing: {q}", file=sys.stderr)
    for q in r["skipped"]:
        print(f"already at the target, skipped: {q}", file=sys.stderr)
    tail = " (dry run)" if a.dry_run else ""
    if a.keep_to:
        print(f"keep: {r['copied']} of {len(keep)} copied to {a.keep_to}{tail}")
    if a.drop_to:
        print(f"drop: {r['moved']} of {len(drop)} moved to {a.drop_to}{tail}")
    return 1 if r["missing"] or r["skipped"] else 0


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
