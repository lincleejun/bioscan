"""Ground-truth builders: `gt folders` (own tier) and `gt inat` (inat tier). Spec sections 7-8."""
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_EXT = "arw,dng,jpg,jpeg,raf,nef,cr3"
# spec 8 columns + `kind` (bird|mammal): eval groups by kind and the truth row is the only
# place that knows it. The inat extras (license, attribution) are from spec 7.
OWN_FIELDS = ["path", "scientific", "tier", "lat", "lon", "taken_at", "source", "kind"]
INAT_FIELDS = OWN_FIELDS + ["license", "attribution"]

# Minimal built-in map until the AviList/MDD CSVs are available (pass --names to use them).
BUILTIN = {
    "red tailed hawk": ("Buteo jamaicensis", "bird"),
    "steller's jay": ("Cyanocitta stelleri", "bird"),
    "western screech owl": ("Megascops kennicottii", "bird"),
}


def list_images(root: str, exts: set[str], recursive: bool) -> list[str]:
    """Absolute paths of image files under root (or root itself), sorted, dotfiles skipped."""
    root = os.path.abspath(root)
    if os.path.isfile(root):
        return [root]
    out = []
    for dirpath, dirnames, files in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".")) if recursive else []
        out += [os.path.join(dirpath, f) for f in files
                if not f.startswith(".") and f.rsplit(".", 1)[-1].lower() in exts]
    return sorted(out)


def read_exif(paths: list[str]) -> dict[str, dict]:
    """path -> {lat, lon, taken_at} ('' when absent). Uses exiftool; all blank if it is missing."""
    blank = {p: {"lat": "", "lon": "", "taken_at": ""} for p in paths}
    if not paths or not shutil.which("exiftool"):
        if paths:
            print("warning: exiftool not found, lat/lon/taken_at left blank", file=sys.stderr)
        return blank
    tags = ["-GPSLatitude", "-GPSLongitude", "-DateTimeOriginal", "-OffsetTimeOriginal"]
    out = dict(blank)
    for i in range(0, len(paths), 500):  # keep argv bounded
        chunk = paths[i:i + 500]
        r = subprocess.run(["exiftool", "-j", "-n", "-q", *tags, *chunk], capture_output=True, text=True)
        for rec in json.loads(r.stdout or "[]"):
            out[rec["SourceFile"]] = {
                "lat": rec.get("GPSLatitude", ""),
                "lon": rec.get("GPSLongitude", ""),
                "taken_at": exif_time(rec.get("DateTimeOriginal"), rec.get("OffsetTimeOriginal")),
            }
    return out


def exif_time(dt, offset) -> str:
    """'2025:12:24 16:41:44' + '-07:00' -> '2025-12-24T16:41:44-07:00'."""
    if not isinstance(dt, str) or not re.match(r"\d{4}:\d\d:\d\d \d\d:\d\d:\d\d", dt):
        return ""
    s = dt[:10].replace(":", "-") + "T" + dt[11:19]
    return s + offset if isinstance(offset, str) and re.fullmatch(r"[+-]\d\d:\d\d", offset) else s


def norm(name: str) -> str:
    """Fold a common/scientific name for matching: 'Red-Tailed-Hawk' == 'Red-tailed Hawk'."""
    s = name.replace("’", "'").replace("_", " ").replace("-", " ").lower()
    return " ".join(s.split())


def load_name_index(csv_paths: list[str]) -> dict[str, set[tuple[str, str]]]:
    """norm(common or scientific) -> {(scientific, kind)}. Columns are detected by header name
    (AviList: Scientific_name / English_name_*; MDD: sciName / mainCommonName / otherCommonNames)."""
    index: dict[str, set[tuple[str, str]]] = {}
    for p in csv_paths:
        kind = "mammal" if "mdd" in os.path.basename(p).lower() or "/mdd/" in p else "bird"
        with open(p, newline="", encoding="utf-8-sig") as f:
            rows = csv.DictReader(f)
            cols = rows.fieldnames or []
            sci_col = next((c for c in cols if re.search(r"sci", c, re.I)), None)
            common_cols = [c for c in cols if re.search(r"common|english", c, re.I)]
            rank_col = next((c for c in cols if re.search(r"rank", c, re.I)), None)
            if not sci_col:
                raise SystemExit(f"{p}: no scientific-name column in header {cols}")
            for row in rows:
                if rank_col and row[rank_col] and row[rank_col].strip().lower() != "species":
                    continue
                sci = " ".join(row[sci_col].replace("_", " ").split())
                if not sci:
                    continue
                keys = {norm(sci)}
                for c in common_cols:  # MDD otherCommonNames is '|'-separated
                    keys |= {norm(n) for n in re.split(r"[|;]", row[c] or "") if n.strip()}
                for k in keys:
                    index.setdefault(k, set()).add((sci, kind))
    return index


def match_folder(label: str, index: dict) -> tuple[str, str]:
    """Folder name -> (scientific, kind); ('', '') when unknown or ambiguous."""
    hits = index.get(norm(label)) if index else None
    if hits is None:
        hits = {BUILTIN[norm(label)]} if norm(label) in BUILTIN else set()
    return next(iter(hits)) if len(hits) == 1 else ("", "")


def gt_folders(root: str, out: str, names: list[str], exts: set[str]) -> dict[str, int]:
    index = load_name_index(names) if names else {}
    rows, counts = [], {}
    for sub in sorted(d for d in os.listdir(root) if not d.startswith(".") and os.path.isdir(os.path.join(root, d))):
        sci, kind = match_folder(sub, index)
        if not sci:
            print(f"warning: folder {sub!r} has no unique match, scientific left blank", file=sys.stderr)
        paths = list_images(os.path.join(root, sub), exts, recursive=True)
        exif = read_exif(paths)
        for p in paths:
            rows.append({"path": p, "scientific": sci, "tier": "own", **exif[p], "source": f"folder:{sub}", "kind": kind})
        counts[sub] = len(paths)
    write_csv(out, OWN_FIELDS, rows)
    return counts


def write_csv(path: str, fields: list[str], rows: list[dict]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


# ---- iNaturalist -------------------------------------------------------------

INAT_API = "https://api.inaturalist.org/v1/observations"
PLACES = {"california": 14, "alaska": 6}
LICENSES = ("cc0", "cc-by", "cc-by-nc")
ICONIC_KIND = {"Aves": "bird", "Mammalia": "mammal"}
USER_AGENT = "bioscan-gt/0.1 (personal research harness)"


def read_taxa(path: str) -> list[tuple[str, str]]:
    with open(path, newline="") as f:
        return [(r["scientific"].strip(), r["common"].strip()) for r in csv.DictReader(f) if r["scientific"].strip()]


def inat_url(taxon: str, place_id: int | None, per_page: int) -> str:
    q = {"taxon_name": taxon, "quality_grade": "research", "photo_license": ",".join(LICENSES),
         "photos": "true", "per_page": per_page}
    if place_id is not None:
        q["place_id"] = place_id
    return INAT_API + "?" + urllib.parse.urlencode(q)


class Throttle:
    """At most one request per `interval` seconds across API and photo downloads."""

    def __init__(self, interval=1.0, clock=time.monotonic, sleep=time.sleep):
        self.interval, self.clock, self.sleep, self.last = interval, clock, sleep, None

    def wait(self):
        if self.last is not None:
            gap = self.interval - (self.clock() - self.last)
            if gap > 0:
                self.sleep(gap)
        self.last = self.clock()


def http_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def obs_row(obs: dict, scientific: str, path: str, photo: dict) -> dict:
    lat = lon = ""
    coords = (obs.get("geojson") or {}).get("coordinates")
    if coords:
        lon, lat = coords[0], coords[1]
    iconic = (obs.get("taxon") or {}).get("iconic_taxon_name", "")
    return {
        "path": path, "scientific": scientific, "tier": "inat", "lat": lat, "lon": lon,
        "taken_at": obs.get("time_observed_at") or obs.get("observed_on") or "",
        "source": obs.get("uri") or f"https://www.inaturalist.org/observations/{obs['id']}",
        "kind": ICONIC_KIND.get(iconic, ""), "license": photo.get("license_code", ""),
        "attribution": photo.get("attribution", ""),
    }


def gt_inat(taxa: list[tuple[str, str]], place_id: int | None, per_species: int, out_dir: str,
            dry_run: bool = False, get=http_get, throttle: Throttle | None = None, log=print) -> list[dict]:
    """Fetch up to per_species research-grade photos per taxon; write out_dir/groundtruth-inat.csv.
    If the place has no observations of a taxon (e.g. caribou in California) retry without place."""
    if dry_run:
        for sci, _ in taxa:
            log(inat_url(sci, place_id, per_species))
        return []
    throttle = throttle or Throttle()
    rows = []
    for sci, _ in taxa:
        results = []
        for pid in dict.fromkeys([place_id, None]):  # place first, then anywhere as fallback
            url = inat_url(sci, pid, per_species)
            throttle.wait()
            results = json.loads(get(url))["results"]
            if results:
                break
            log(f"{sci}: no observations at place_id={pid}" + (", retrying without place" if pid is not None else ""))
        d = Path(out_dir) / sci.replace(" ", "_")
        d.mkdir(parents=True, exist_ok=True)
        n = 0
        for obs in results[:per_species]:
            photo = next((p for p in obs.get("photos") or [] if (p.get("license_code") or "") in LICENSES), None)
            if not photo or not photo.get("url"):
                continue
            path = d / f"{obs['id']}_{photo['id']}.jpg"
            if not path.exists():
                throttle.wait()
                path.write_bytes(get(photo["url"].replace("/square.", "/medium.")))
            rows.append(obs_row(obs, sci, str(path.resolve()), photo))
            n += 1
        log(f"{sci}: {n} photos")
    write_csv(str(Path(out_dir) / "groundtruth-inat.csv"), INAT_FIELDS, rows)
    return rows
