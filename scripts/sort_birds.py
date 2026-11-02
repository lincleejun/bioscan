"""Sort bird photos into one folder per species, by bioscan's own models only (no human, no LLM).

    uv run python scripts/sort_birds.py run   --places places.csv --out OUT [--url URL]
    uv run python scripts/sort_birds.py plan  --out OUT --dest /Volumes/Media/bird
    uv run python scripts/sort_birds.py apply --out OUT --dest /Volumes/Media/bird

places.csv: `folder,lat,lon` (folder = a directory to scan recursively; its photos have no GPS, so the
coordinate feeds the location prior; a photo's own GPS still wins). `run` writes OUT/preds/<n>.ndjson per
folder (identify + quality), resumable. `plan` writes OUT/plan.csv: one row per photo group (same folder,
same stem: RAW, JPEG, HIF, XMP move together) with the decision and the reason. `apply` moves the groups
marked `move`, never overwrites, and appends every move to OUT/moves.tsv (src, dst) so it can be undone.

A folder may be listed more than once (a trip at several places): a photo moves when it passes at any.
A name already in the species folder goes to <species>/<source folder>/; nothing is overwritten.
A group moves only when every rule below holds; any doubt is a skip with its reason:
person (gate person or P(person) >= PERSON_MAX), not_bird (gate not bird), no_box, other_kind (a non-bird
box), not_species (a bird box below species level: genus, family, unconfirmed), mixed_species, low_posterior
(best box below POSTERIOR_MIN), out_of_range (p_geo null or below GEO_MIN at the folder's place and week),
soft (quality: soft subject, motion or defocus, or blur too little detail to tell), error (decode failed).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

PERSON_MAX = 0.1      # gate P(person); above it a person may be in the frame
POSTERIOR_MIN = 0.8   # = summarize's single_sighting_max_posterior
GEO_MIN = 0.03        # BirdNET's default location-filter threshold (stricter than rules.RANGE_EPS 0.01)
SOFT = {"soft_subject", "motion", "defocus", "motion_or_defocus"}   # = cull.SOFT
RAW = {".arw", ".nef", ".nrw", ".cr2", ".cr3", ".raf", ".orf", ".rw2", ".pef", ".srw", ".dng"}


def decide(ev: dict) -> tuple[str, str, str]:
    """One result event -> (reason, scientific, common); reason 'ok' means move."""
    if ev.get("type") != "result":
        return "error", "", ""
    ident = (ev.get("products") or {}).get("identify") or {}
    gate = ident.get("gate") or {}
    if gate.get("class") == "person" or (gate.get("probs") or {}).get("person", 0.0) >= PERSON_MAX:
        return "person", "", ""
    if gate.get("class") != "bird":
        return "not_bird", "", ""
    boxes = ident.get("boxes") or []
    if not boxes:
        return "no_box", "", ""
    if any(b.get("kind") != "bird" for b in boxes):
        return "other_kind", "", ""
    sp = [b.get("species") or {} for b in boxes]
    if any(s.get("level") != "species" for s in sp):
        return "not_species", "", ""
    if len({s.get("taxon") for s in sp}) > 1:
        return "mixed_species", "", ""
    taxon = sp[0]["taxon"]
    tops = [next((t for t in s.get("top") or [] if t.get("scientific") == taxon), {}) for s in sp]
    if max(t.get("posterior") or 0.0 for t in tops) < POSTERIOR_MIN:
        return "low_posterior", taxon, ""
    if any(t.get("p_geo") is None or t["p_geo"] < GEO_MIN for t in tops):
        return "out_of_range", taxon, ""
    q = (ev.get("products") or {}).get("quality") or {}
    subject = q.get("subject") or {}
    if SOFT & set(q.get("reject_reasons") or []) or subject.get("blur") is None:
        return "soft", taxon, ""
    common = next((t.get("common") for t in tops if t.get("common")), None) or taxon
    return "ok", taxon, common


def folder_name(common: str, existing: list[str]) -> str:
    """The common name as a folder; an existing folder with the same letters (Red-Tailed-Hawk) is reused."""
    key = re.sub(r"[^a-z0-9]", "", common.lower())
    for e in existing:
        if re.sub(r"[^a-z0-9]", "", e.lower()) == key:
            return e
    return common.replace("/", "-").replace(":", "-")


def group_files(path: Path) -> list[Path]:
    """Every visible file in the photo's folder with the same stem (RAW, JPEG, HIF, XMP, ...)."""
    return sorted(f for f in path.parent.iterdir()
                  if f.is_file() and not f.name.startswith(".") and f.stem == path.stem)


def cmd_run(a: argparse.Namespace) -> None:
    preds = Path(a.out) / "preds"
    preds.mkdir(parents=True, exist_ok=True)
    with open(a.places, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        name = hashlib.sha1(f"{r['folder']}|{r['lat']},{r['lon']}".encode()).hexdigest()[:10] + ".ndjson"
        out = preds / name
        if out.exists():
            print(f"done already: {r['folder']}", flush=True)
            continue
        part = out.with_suffix(".part")
        print(f"run: {r['folder']}", flush=True)
        cmd = [sys.executable, "-c", "from bioscan.cli.main import main; main()", "--url", a.url, "run", r["folder"], "-r",
               "--want", "identify,quality", "--lat", r["lat"], "--lon", r["lon"], "--json", "--out", str(part)]
        code = subprocess.run(cmd).returncode
        if code not in (0, 1):   # 1 = some images failed (e.g. a broken file): their error events are kept
            sys.exit(f"bioscan run failed ({code}) on {r['folder']}; rerun to resume")
        part.rename(out)


def cmd_plan(a: argparse.Namespace) -> None:
    dest = Path(a.dest)
    existing = [d.name for d in dest.iterdir() if d.is_dir()] if dest.is_dir() else []
    groups: dict[tuple[str, str], list[tuple[Path, dict]]] = {}
    for f in sorted((Path(a.out) / "preds").glob("*.ndjson")):
        for line in open(f, encoding="utf-8"):
            ev = json.loads(line)
            if ev.get("type") in ("result", "error") and ev.get("path"):
                p = Path(ev["path"])
                groups.setdefault((str(p.parent), p.stem), []).append((p, ev))
    rows, targets = [], {}
    for members in groups.values():
        raws = [m for m in members if m[0].suffix.lower() in RAW]
        # the RAW's verdict decides for its JPEG partner; a folder listed at several places (a trip) moves
        # when the photo passes at any of them
        verdicts = [(p, decide(e)) for p, e in (raws or members)]
        path, (reason, sci, common) = next((v for v in verdicts if v[1][0] == "ok"), verdicts[0])
        if not path.exists():
            reason = "moved_already"
        folder = folder_name(common, existing) if reason == "ok" else ""
        files = group_files(path) if path.exists() else [path]
        if reason == "ok":
            # never overwrite: a name already in the species folder goes under <species>/<source folder>/
            for sub in (folder, f"{folder}/{path.parent.name}"):
                if not any((dest / sub / fp.name).exists() or dest / sub / fp.name in targets for fp in files):
                    folder = sub
                    targets.update({dest / sub / fp.name: fp for fp in files})
                    break
            else:
                reason, folder = "name_taken", ""
        rows.append({"path": str(path), "decision": "move" if reason == "ok" else "skip", "reason": reason,
                     "scientific": sci, "common": common, "folder": folder,
                     "files": "|".join(fp.name for fp in files)})
    rows.sort(key=lambda r: r["path"])
    with open(Path(a.out) / "plan.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]) if rows else ["path"])
        w.writeheader()
        w.writerows(rows)
    by_reason: dict[str, int] = {}
    by_species: dict[str, int] = {}
    for r in rows:
        by_reason[r["reason"]] = by_reason.get(r["reason"], 0) + 1
        if r["decision"] == "move":
            by_species[r["folder"]] = by_species.get(r["folder"], 0) + 1
    print(json.dumps({"groups": len(rows), "reasons": by_reason,
                      "species": dict(sorted(by_species.items(), key=lambda kv: -kv[1]))},
                     ensure_ascii=False, indent=1))


def cmd_apply(a: argparse.Namespace) -> None:
    dest, moved, skipped = Path(a.dest), 0, 0
    with open(Path(a.out) / "plan.csv", newline="", encoding="utf-8") as fh, \
         open(Path(a.out) / "moves.tsv", "a", encoding="utf-8") as log:
        for r in csv.DictReader(fh):
            if r["decision"] != "move":
                continue
            src_dir = Path(r["path"]).parent
            pairs = [(src_dir / n, dest / r["folder"] / n) for n in r["files"].split("|")]
            if any(not s.exists() or d.exists() for s, d in pairs):
                skipped += 1
                continue
            (dest / r["folder"]).mkdir(parents=True, exist_ok=True)
            for s, d in pairs:
                log.write(f"{s}\t{d}\n")
                log.flush()
                shutil.move(str(s), str(d))
            moved += 1
    print(f"moved {moved} photo groups, skipped {skipped} (source gone or target exists); log {a.out}/moves.tsv")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--places", required=True)
    r.add_argument("--url", default="http://127.0.0.1:8765")
    for p in (r, sub.add_parser("plan"), sub.add_parser("apply")):
        p.add_argument("--out", required=True)
        if p is not r:
            p.add_argument("--dest", required=True)
    a = ap.parse_args()
    {"run": cmd_run, "plan": cmd_plan, "apply": cmd_apply}[a.cmd](a)


if __name__ == "__main__":
    main()
