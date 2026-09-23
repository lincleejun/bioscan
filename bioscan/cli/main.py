"""bioscan CLI entry point (spec section 7). argparse + urllib, no third-party runtime deps."""
import argparse
import json
import os
import plistlib
import shutil
import sys
from pathlib import Path

from bioscan.cli import client, gt
from bioscan.cli.render import Renderer

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PRODUCTS = ("identify", "embed", "jpg")


# ---- serve -------------------------------------------------------------------

def launchd_plist(port: int, decode_workers: int, chunk: int, uv: str | None = None, root: Path = PROJECT_ROOT,
                  allow_roots: list[str] | None = None, detail_edge: int | None = None) -> bytes:
    uv = uv or shutil.which("uv") or os.path.expanduser("~/.local/bin/uv")
    log = os.path.expanduser("~/Library/Logs/bioscan.log")
    extra = [a for r in allow_roots or [] for a in ("--allow-root", os.path.abspath(r))]
    extra += ["--detail-edge", str(detail_edge)] if detail_edge is not None else []
    return plistlib.dumps({
        "Label": "cc.outman.bioscan",
        "ProgramArguments": [os.path.abspath(uv), "run", "--project", str(root), "bioscan", "serve",
                             "--port", str(port), "--decode-workers", str(decode_workers), "--chunk", str(chunk),
                             *extra],
        "WorkingDirectory": str(root),
        "EnvironmentVariables": {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"},
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": log,
        "StandardErrorPath": log,
    })


def cmd_serve(a):
    if a.launchd:
        sys.stdout.buffer.write(launchd_plist(a.port, a.decode_workers or 4, a.chunk or 32,
                                              allow_roots=a.allow_root, detail_edge=a.detail_edge))
        return 0
    # The service reads its tunables from the environment (flag > env > default 4/32).
    if a.decode_workers is not None:
        os.environ["BIOSCAN_DECODE_WORKERS"] = str(a.decode_workers)
    if a.chunk is not None:
        os.environ["BIOSCAN_CHUNK"] = str(a.chunk)
    if a.detail_edge is not None:
        os.environ["BIOSCAN_DETAIL_EDGE"] = str(a.detail_edge)
    if a.allow_root:
        os.environ["BIOSCAN_ALLOW_ROOTS"] = os.pathsep.join(os.path.abspath(r) for r in a.allow_root)
    from bioscan.service import app  # service deps live with bioscan.service; keep the CLI import-light

    app.main(["--port", str(a.port)])
    return 0


# ---- health / run --------------------------------------------------------------

def cmd_health(a):
    print(json.dumps(client.health(a.url), indent=2))
    return 0


def build_payload(a) -> dict:
    want = [w.strip() for w in a.want.split(",") if w.strip()]
    bad = [w for w in want if w not in PRODUCTS]
    if not want or bad:
        raise SystemExit(f"--want must be a non-empty subset of {','.join(PRODUCTS)} (got {a.want!r})")
    if "jpg" in want and not a.jpg_out:
        raise SystemExit("--want jpg needs --jpg-out DIR")
    if (a.lat is None) != (a.lon is None):
        raise SystemExit("--lat and --lon go together")
    exts = {e.strip().lower().lstrip(".") for e in a.ext.split(",")}
    paths = [p for root in a.paths for p in gt.list_images(root, exts, a.recursive)]
    if not paths:
        raise SystemExit("no images found")
    inputs = [{"path": p} for p in paths]
    if a.lat is not None:
        # Request coordinates override EXIF on the service side, so only fill images whose
        # EXIF has none -- that keeps "EXIF wins" semantics for the batch default.
        exif = gt.read_exif(paths)  # all blank without exiftool -> every image gets the default
        for inp in inputs:
            if exif.get(inp["path"], {}).get("lat", "") == "":
                inp["lat"], inp["lon"] = a.lat, a.lon
    options: dict = {}
    if "identify" in want:
        options["identify"] = {"top_k": a.top_k, "geo": not a.no_geo, "species": not a.no_species}
    if "jpg" in want:
        options["jpg"] = {"out_dir": os.path.abspath(a.jpg_out)}
    return {"inputs": inputs, "want": want, "options": options}


def cmd_run(a):
    payload = build_payload(a)
    out = open(a.out, "w" if not a.json else "wb") if a.out else None
    try:
        if a.json:  # raw NDJSON passthrough, no reprocessing
            sink = out or sys.stdout.buffer
            for line in client.run(payload, a.url):
                sink.write(line + b"\n")
                sink.flush()
            return 0
        sink = out or sys.stdout
        r = Renderer()
        for line in client.run(payload, a.url):
            text = r.feed(json.loads(line))
            if text is not None:
                print(text, file=sink, flush=True)
        print(r.summary(), file=sink)
        return 1 if r.errors else 0
    finally:
        if out:
            out.close()


# ---- gt / eval / names -------------------------------------------------------------

def cmd_gt_folders(a):
    exts = {e.strip().lower() for e in a.ext.split(",")}
    counts = gt.gt_folders(a.dir, a.out, a.names or [], exts)
    for k, v in counts.items():
        print(f"{v:>5}  {k}")
    print(f"{sum(counts.values()):>5}  total -> {a.out}")
    return 0


def cmd_gt_inat(a):
    place = a.place.lower()
    place_id = gt.PLACES.get(place) if not place.isdigit() else int(place)
    if place_id is None:
        raise SystemExit(f"unknown --place {a.place!r}; use a numeric iNaturalist place_id or one of {sorted(gt.PLACES)}")
    if not a.dry_run and not a.out:
        raise SystemExit("--out DIR is required unless --dry-run")
    rows = gt.gt_inat(gt.read_taxa(a.taxa), place_id, a.per_species, a.out or ".", dry_run=a.dry_run)
    if not a.dry_run:
        print(f"{len(rows)} photos -> {Path(a.out) / 'groundtruth-inat.csv'}")
    return 0


def cmd_eval(a):
    from bioscan.cli import eval as ev
    print(ev.run_eval(a.groundtruth, a.out, a.no_geo, a.url, a.preds, not a.no_synonyms))
    return 0


def cmd_names_stats(a):
    # Heavy imports stay here so every other subcommand runs without torch installed.
    import logging

    from bioscan.service import engine, names

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    _, lists = engine.load_species(engine.pick_device())
    print(json.dumps(names.stats(lists), indent=2, ensure_ascii=False, default=str))
    return 0


def cmd_names_geo_gaps(a, prior=None):
    """Unlabelled AviList species whose genus lives at --lat/--lon: candidates for a `birdnet`
    row in data/names/synonyms.csv. Needs only the birdnet package, not the models."""
    import csv

    import numpy as np

    from bioscan.service.adapters import geo

    with open(a.map, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    cands: dict[str, list[str]] = {}
    cand_path = Path(a.map).with_name("candidates.csv")
    if cand_path.is_file():
        with open(cand_path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r["side"] == "birdnet":
                    cands.setdefault(r["scientific"], []).append(r["candidate"])
    prior = prior or geo.GeoPrior.load()
    if prior is None:
        raise SystemExit("BirdNET geo model unavailable (pip package `birdnet`, model geo 3.0)")
    index = prior.index([r["birdnet_label"] for r in rows])
    probs = prior.probs(a.lat, a.lon, geo.week_of(a.date))
    found = geo.gaps([r["scientific"] for r in rows], [r["common"] for r in rows], np.asarray(index), probs, a.min_p)
    print(f"{len(found)} unlabelled species whose genus has p_geo >= {a.min_p} at {a.lat},{a.lon}"
          f" (week {geo.week_of(a.date) or 'all'}):")
    for g in found:
        extra = f"  candidates: {', '.join(cands[g['scientific']])}" if g["scientific"] in cands else ""
        print(f"  {g['scientific']} ({g['common']})  <- congener {g['congener']} p_geo {g['congener_p_geo']}{extra}")
    return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bioscan", description="Animal detection + species ID (thin client for the bioscan service).")
    p.add_argument("--url", default=client.DEFAULT_URL, help="service URL (env BIOSCAN_URL; default %(default)s)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="run the service (uvicorn, one worker)")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--decode-workers", type=int, help="env BIOSCAN_DECODE_WORKERS, default 4")
    s.add_argument("--chunk", type=int, help="env BIOSCAN_CHUNK, default 32")
    s.add_argument("--allow-root", action="append", help="only serve files under DIR (repeatable); env BIOSCAN_ALLOW_ROOTS")
    s.add_argument("--detail-edge", type=int, help="species-crop image long edge, <=2048 = off; env BIOSCAN_DETAIL_EDGE, default 3072")
    s.add_argument("--launchd", action="store_true", help="print a launchd plist to stdout instead of serving")
    s.set_defaults(func=cmd_serve)

    sub.add_parser("health", help="GET /health").set_defaults(func=cmd_health)

    s = sub.add_parser("run", help="identify/embed/jpg over files or directories")
    s.add_argument("paths", nargs="+")
    s.add_argument("--want", default="identify", help="comma list of identify,embed,jpg")
    s.add_argument("--json", action="store_true", help="write raw NDJSON")
    s.add_argument("--out", help="write to FILE instead of stdout")
    s.add_argument("--lat", type=float)
    s.add_argument("--lon", type=float)
    s.add_argument("--no-geo", action="store_true")
    s.add_argument("--top-k", type=int, default=5)
    s.add_argument("--no-species", action="store_true")
    s.add_argument("--jpg-out")
    s.add_argument("-r", "--recursive", action="store_true")
    s.add_argument("--ext", default=gt.DEFAULT_EXT)
    s.set_defaults(func=cmd_run)

    g = sub.add_parser("gt", help="build ground-truth CSVs").add_subparsers(dest="gt_cmd", required=True)
    s = g.add_parser("folders", help="species-named subfolders -> own-tier CSV")
    s.add_argument("dir")
    s.add_argument("--out", default="groundtruth.csv")
    s.add_argument("--names", action="append", help="AviList/MDD CSV to match folder names against (repeatable)")
    s.add_argument("--ext", default=gt.DEFAULT_EXT)
    s.set_defaults(func=cmd_gt_folders)
    s = g.add_parser("inat", help="download iNaturalist research-grade photos -> inat-tier CSV")
    s.add_argument("--place", default="california", help="name or numeric place_id")
    s.add_argument("--taxa", default=str(PROJECT_ROOT / "data" / "taxa.csv"))
    s.add_argument("--per-species", type=int, default=25)
    s.add_argument("--out")
    s.add_argument("--dry-run", action="store_true", help="print API URLs only, download nothing")
    s.set_defaults(func=cmd_gt_inat)

    s = sub.add_parser("eval", help="run identify over a ground-truth CSV and write report.md")
    s.add_argument("groundtruth")
    s.add_argument("--out", required=True)
    s.add_argument("--no-geo", action="store_true")
    s.add_argument("--preds", help="score an existing preds.ndjson instead of calling the service")
    s.add_argument("--no-synonyms", action="store_true", help="compare raw truth labels (skip data/names/synonyms.csv)")
    s.set_defaults(func=cmd_eval)

    n = sub.add_parser("names", help="species name lists").add_subparsers(dest="names_cmd", required=True)
    n.add_parser("stats", help="coverage of official TreeOfLife vectors").set_defaults(func=cmd_names_stats)
    s = n.add_parser("geo-gaps", help="unlabelled AviList species whose genus lives at a place (review for synonyms.csv)")
    s.add_argument("--lat", type=float, required=True)
    s.add_argument("--lon", type=float, required=True)
    s.add_argument("--date", help="YYYY-MM-DD for BirdNET's week; default: whole year")
    s.add_argument("--min-p", type=float, default=0.05, help="congener p_geo threshold (default %(default)s)")
    s.add_argument("--map", default=str(PROJECT_ROOT / "data" / "names" / "avilist_map.csv"))
    s.set_defaults(func=cmd_names_geo_gaps)
    return p


def main(argv=None) -> int:
    a = parser().parse_args(argv)
    try:
        return a.func(a)
    except client.ServiceError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:  # e.g. `bioscan run ... --json | head`
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 141


if __name__ == "__main__":
    sys.exit(main())
