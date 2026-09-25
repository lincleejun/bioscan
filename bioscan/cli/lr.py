"""`bioscan lr open PREDS`: a scan's results as Lightroom Classic's `latest.json` (stars, hierarchical
keywords, a collection per species), which the bioscan.lrplugin plugin applies; `bioscan lr install`
puts that plugin in Lightroom's Modules folder.

All the rules live here (the plugin makes no decisions); the latest.json layout is schema 1 of
docs/superpowers/specs/2026-09-24-lightroom-plugin-design.md section 4. Standard library and
bioscan.contract only: the CLI stays import-light.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from bioscan import contract

REVIEW = "待确认"      # group of photos with an animal but no species-level box
NONE = "无动物"        # group of photos with no box
SCHEMA = 1
ROOT = "bioscan"      # keyword root
PLUGIN = Path(__file__).resolve().parents[2] / "extensions" / "lightroom" / "bioscan.lrplugin"


def default_target() -> Path:
    return Path.home() / "Library" / "Application Support" / "bioscan" / "lightroom" / "latest.json"


def default_modules() -> Path:
    return Path.home() / "Library" / "Application Support" / "Adobe" / "Lightroom" / "Modules"


def results(preds: str | Path) -> list[dict]:
    """The result events of a preds.ndjson; meta, progress, error and done lines are skipped."""
    with open(preds, encoding="utf-8") as f:
        return [ev for ev in map(json.loads, filter(str.strip, f)) if ev.get("type") == contract.RESULT]


def _keyword(b: dict) -> list[str]:
    sp = contract.species_of(b)
    level, top, kind = contract.level_of(sp), contract.top_of(sp), b["kind"]
    tax = (top[0].get("taxonomy") or []) if top else []
    if level == "species" and top:
        return [ROOT, kind, top[0].get("common") or top[0]["scientific"]]
    if level == "genus" and len(tax) > 5:
        return [ROOT, kind, tax[5]]
    if level == "family" and len(tax) > 4:
        return [ROOT, kind, tax[4]]
    return [ROOT, kind]


def keywords(ev: dict) -> list[list[str]]:
    """One hierarchical keyword per box, root first; duplicates within the image dropped, order kept."""
    out = []
    for kw in map(_keyword, contract.boxes_of(contract.identify_of(ev))):
        if kw not in out:
            out.append(kw)
    return out


def photo(ev: dict) -> dict:
    """One latest.json photo without its stars (those need the whole run, see stars())."""
    boxes = contract.boxes_of(contract.identify_of(ev))
    named = [b for b in boxes
             if contract.level_of(contract.species_of(b)) == "species" and contract.top_of(contract.species_of(b))]
    if named:
        top = contract.top_of(contract.species_of(max(named, key=lambda b: b["score"])))[0]
        group, species, level = top.get("common") or top["scientific"], top["scientific"], "species"
    elif boxes:
        best = max(boxes, key=lambda b: b["score"])
        group, species, level = REVIEW, "", contract.level_of(contract.species_of(best)) or "unconfirmed"
    else:
        group, species, level = NONE, "", "none"
    return {"path": ev["path"], "score": max(((b.get("quality") or {}).get("sharpness", 0.0) for b in boxes), default=0.0),
            "keywords": keywords(ev), "group": group, "species": species, "level": level}


def stars(photos: list[dict]) -> None:
    """Sets each photo's "stars": 1-5 by score quantile among photos with a box (about 20% each), 0 without one."""
    # ponytail: sharpness quantile as a stand-in for an aesthetic score; replace with the C2 aesthetic head.
    ranked = sorted((p for p in photos if p["level"] != "none"), key=lambda p: (p["score"], p["path"]))
    for p in photos:
        p["stars"] = 0
    for i, p in enumerate(ranked):
        p["stars"] = 1 + (5 * i) // len(ranked)


def write_latest(photos: list[dict], source: str, target: Path) -> None:
    """Temp file in the target's folder, then os.replace: the plugin's watcher never reads half a file."""
    doc = {"schema": SCHEMA, "run": datetime.now().astimezone().isoformat(timespec="microseconds"),
           "source": source, "photos": [{k: p[k] for k in ("path", "stars", "score", "keywords", "group",
                                                             "species", "level")} for p in photos]}
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".latest-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=1)
        os.replace(tmp, target)
    except BaseException:
        os.unlink(tmp)
        raise


def cmd_open(a) -> int:
    if not os.path.isfile(a.preds):
        print(f"error: no such preds file: {a.preds}", file=sys.stderr)
        return 1
    photos = [photo(ev) for ev in results(a.preds)]
    if not photos:
        print(f"error: no result lines in {a.preds} (write it with `bioscan run ... --json --out FILE`)",
              file=sys.stderr)
        return 1
    stars(photos)
    target = Path(a.to) if a.to else default_target()
    write_latest(photos, os.path.abspath(a.preds), target)
    print(f"{len(photos)} photos, {sum(p['stars'] > 0 for p in photos)} starred -> {target}")
    if sys.platform == "darwin" and not a.no_launch:
        subprocess.run(["open", "-a", "Adobe Lightroom Classic"])
    return 0


def cmd_install(a) -> int:
    dest = (Path(a.modules) if a.modules else default_modules()) / "bioscan.lrplugin"
    if dest.is_symlink() or dest.exists():
        print(f"already installed: {dest}")
        return 0
    dest.parent.mkdir(parents=True, exist_ok=True)
    if a.copy:
        shutil.copytree(PLUGIN, dest)
    else:
        dest.symlink_to(PLUGIN, target_is_directory=True)
    print(f"{'copied' if a.copy else 'linked'} {PLUGIN} -> {dest}; restart Lightroom Classic once")
    return 0


def add_parser(sub) -> None:
    """`bioscan lr ...` under the main parser's subcommands."""
    g = sub.add_parser("lr", help="Lightroom Classic: send a scan's results, install the plugin") \
        .add_subparsers(dest="lr_cmd", required=True)
    s = g.add_parser("open", help="write latest.json (stars, keywords, collections) from a preds.ndjson, open Lightroom")
    s.add_argument("preds")
    s.add_argument("--no-launch", action="store_true", help="write the file only, do not open Lightroom")
    s.add_argument("--to", help=f"write here instead of {default_target()}")
    s.set_defaults(func=cmd_open)
    s = g.add_parser("install", help="link the bioscan plugin into Lightroom's Modules folder")
    s.add_argument("--modules", help=f"Modules folder (default {default_modules()})")
    s.add_argument("--copy", action="store_true", help="copy instead of symlink")
    s.set_defaults(func=cmd_install)
