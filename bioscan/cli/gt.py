"""Ground-truth builders: `gt folders` (own tier), `gt inat` (inat tier) and `gt scene` (scene tier,
Open Images V7 photos per scene label). Spec sections 7-8; docs/standards.md "scene tier"."""
import csv
import hashlib
import io
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
import tomllib
import urllib.parse
import urllib.request
from pathlib import Path

from bioscan.formats import list_images, subsec
from bioscan.naming import norm_label

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


def read_exif(paths: list[str]) -> dict[str, dict]:
    """path -> {lat, lon, taken_at} ('' when absent). Uses exiftool; all blank if it is missing."""
    blank = {p: {"lat": "", "lon": "", "taken_at": ""} for p in paths}
    if not paths or not shutil.which("exiftool"):
        if paths:
            print("warning: exiftool not found, lat/lon/taken_at left blank", file=sys.stderr)
        return blank
    tags = ["-GPSLatitude", "-GPSLongitude", "-DateTimeOriginal", "-SubSecTimeOriginal", "-OffsetTimeOriginal"]
    out = dict(blank)
    for i in range(0, len(paths), 500):  # keep argv bounded
        chunk = paths[i:i + 500]
        r = subprocess.run(["exiftool", "-j", "-n", "-q", *tags, *chunk], capture_output=True, text=True)
        for rec in json.loads(r.stdout or "[]"):
            out[rec["SourceFile"]] = {
                "lat": rec.get("GPSLatitude", ""),
                "lon": rec.get("GPSLongitude", ""),
                "taken_at": exif_time(rec.get("DateTimeOriginal"), rec.get("OffsetTimeOriginal"),
                                      rec.get("SubSecTimeOriginal")),
            }
    return out


def exif_time(dt, offset, sub=None) -> str:
    """'2025:12:24 16:41:44' + '-07:00' (+ sub-seconds '37') -> '2025-12-24T16:41:44(.37)-07:00',
    the same string the service's decode builds."""
    if not isinstance(dt, str) or not re.match(r"\d{4}:\d\d:\d\d \d\d:\d\d:\d\d", dt):
        return ""
    s = dt[:10].replace(":", "-") + "T" + dt[11:19] + subsec(sub)
    return s + offset if isinstance(offset, str) and re.fullmatch(r"[+-]\d\d:\d\d", offset) else s


def load_name_index(csv_paths: list[str]) -> dict[str, set[tuple[str, str]]]:
    """norm_label(common or scientific) -> {(scientific, kind)}. Columns are detected by header name
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
                keys = {norm_label(sci)}
                for c in common_cols:  # MDD otherCommonNames is '|'-separated
                    keys |= {norm_label(n) for n in re.split(r"[|;]", row[c] or "") if n.strip()}
                for k in keys:
                    index.setdefault(k, set()).add((sci, kind))
    return index


def match_folder(label: str, index: dict) -> tuple[str, str]:
    """Folder name -> (scientific, kind) by naming.norm_label ('Red-Tailed-Hawk' == 'Red-tailed Hawk',
    'Steller’s Jay' == "Steller's Jay"); ('', '') when unknown or ambiguous."""
    key = norm_label(label)
    hits = index.get(key) if index else None
    if hits is None:
        hits = {BUILTIN[key]} if key in BUILTIN else set()
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


# ---- gt scene: Open Images V7 photos per scene label (scene tier) --------------------------------
# Open Images V7 (https://storage.googleapis.com/openimages/web/download_v7.html): human-verified
# image-level labels of the validation and test subsets, every image CC BY 2.0 (per-image License,
# Author and OriginalLandingURL in the images CSV), mirrored by CVDF on S3. data/scene/oid-labels.toml
# maps class names to scene labels. Photos are fetched for measuring, never redistributed.
OID_CLASSES = "https://storage.googleapis.com/openimages/v7/oidv7-class-descriptions.csv"
OID_LABELS = {"validation": "https://storage.googleapis.com/openimages/v7/oidv7-val-annotations-human-imagelabels.csv",
              "test": "https://storage.googleapis.com/openimages/v7/oidv7-test-annotations-human-imagelabels.csv"}
OID_IMAGES = {"validation": "https://storage.googleapis.com/openimages/2018_04/validation/validation-images-with-rotation.csv",
              "test": "https://storage.googleapis.com/openimages/2018_04/test/test-images-with-rotation.csv"}
OID_PHOTO = "https://open-images-dataset.s3.amazonaws.com/{subset}/{image_id}.jpg"
SCENE_FIELDS = ["path", "tier", "scene", "scene_group", "light", "setting", "framing", "license", "attribution",
                "source", "url", "md5"]
SCENE_ATTRIBUTES = ("light", "setting", "framing")


def read_scene_map(path: str) -> dict:
    """data/scene/oid-labels.toml checked: {"label": {name: {group, scene, any, all, not}}, "attribute": {...}}."""
    with open(path, "rb") as f:
        d = tomllib.load(f)
    labels = d.get("label") or {}
    if not labels:
        raise ValueError(f"{path}: no [label.<name>] tables")
    for name, e in labels.items():
        if not re.fullmatch(r"[a-z0-9_]+", name) or not e.get("group"):
            raise ValueError(f"{path}: label {name!r} needs a lower-case name and a group")
        if not (e.get("any") or e.get("all")):
            raise ValueError(f"{path}: label {name!r} needs `any` or `all` classes")
    return d


def oid_cached(url: str, cache_dir: str, get=http_get, log=print) -> Path:
    """The file at `url` under cache_dir (downloaded once)."""
    p = Path(cache_dir) / url.rsplit("/", 1)[1]
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        log(f"fetching {url}")
        p.write_bytes(get(url))
    return p


def oid_class_ids(classes_csv: bytes | str, names: set[str]) -> dict[str, set[str]]:
    """lower-case DisplayName -> its MIDs (a name can have several) for the names asked for."""
    out: dict[str, set[str]] = {}
    text = classes_csv.decode("utf-8") if isinstance(classes_csv, bytes) else classes_csv
    for r in csv.DictReader(io.StringIO(text)):
        n = r["DisplayName"].strip().lower()
        if n in names:
            out.setdefault(n, set()).add(r["LabelName"])
    return out


def oid_positives(labels_csv: Path, wanted: set[str]) -> dict[str, set[str]]:
    """ImageID -> its human-verified positive MIDs among `wanted` (streamed; other rows skipped)."""
    out: dict[str, set[str]] = {}
    with open(labels_csv, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["LabelName"] in wanted and float(r["Confidence"] or 0) >= 1:
                out.setdefault(r["ImageID"], set()).add(r["LabelName"])
    return out


def scene_assign(positives: dict[str, set[str]], spec: dict, ids: dict[str, set[str]]) -> dict[str, list[str]]:
    """label -> ImageIDs (sorted) that qualify for that label and no other: `any` (one of), `all`
    (every one), `not` (none of), over the image's positive MIDs."""
    def mids(names: list[str]) -> list[set[str]]:
        return [ids.get(n.lower(), set()) for n in names]

    rules = {name: (mids(e.get("any", [])), mids(e.get("all", [])), set().union(*mids(e.get("not", []))))
             for name, e in spec.items()}
    out: dict[str, list[str]] = {name: [] for name in spec}
    for image_id, pos in positives.items():
        hits = [name for name, (any_, all_, not_) in rules.items()
                if (not any_ or any(pos & m for m in any_)) and all(pos & m for m in all_) and not (pos & not_)]
        if len(hits) == 1:
            out[hits[0]].append(image_id)
    return {k: sorted(v) for k, v in out.items()}


def scene_attributes(pos: set[str], spec: dict, ids: dict[str, set[str]]) -> dict[str, str]:
    """{light, setting, framing}: the first value whose classes the image has, else ""."""
    out = {}
    for attr in SCENE_ATTRIBUTES:
        out[attr] = next((v for v, names in (spec.get(attr) or {}).items()
                          if any(pos & ids.get(n.lower(), set()) for n in names)), "")
    return out


def md5_file(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch_photo(url: str, path: Path, md5: str, get, throttle: Throttle) -> str:
    """The photo at `path`: kept when it is there with the expected md5 (or no md5 is known), else
    downloaded (throttled). Returns the file's md5; raises when a download does not match `md5`."""
    if path.exists() and (not md5 or md5_file(path) == md5):
        return md5 or md5_file(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    throttle.wait()
    data = get(url)
    got = hashlib.md5(data).hexdigest()
    if md5 and got != md5:
        raise ValueError(f"{url}: md5 {got} differs from the manifest's {md5}")
    path.write_bytes(data)
    return got


def gt_scene_from(manifest: str, out_dir: str, get=http_get, throttle: Throttle | None = None, log=print) -> list[dict]:
    """The scene set of a committed manifest (a groundtruth-scene.csv with url and md5, path ignored):
    each photo to out_dir/<scene or group>/<subset>_<id>.jpg unless already there with that md5, then
    out_dir/groundtruth-scene.csv with local paths. A download whose md5 differs fails the run."""
    throttle = throttle or Throttle(0.2)
    with open(manifest, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    missing = [c for c in ("url", "md5", "source", "scene_group") if rows and c not in rows[0]]
    if missing:
        raise ValueError(f"{manifest}: not a scene manifest, missing columns {', '.join(missing)}")
    out, kept = [], 0
    for r in rows:
        folder = r.get("scene") or r["scene_group"]
        path = Path(out_dir) / folder / (r["url"].rsplit("/", 2)[-2] + "_" + r["url"].rsplit("/", 1)[-1])
        before = path.exists()
        md5 = fetch_photo(r["url"], path, r.get("md5", ""), get, throttle)
        kept += before and md5 == r.get("md5", "")
        out.append({**{k: r.get(k, "") for k in SCENE_FIELDS}, "path": str(path.resolve()), "tier": "scene", "md5": md5})
    log(f"{len(out)} photos, {kept} already here, {len(out) - kept} downloaded")
    write_csv(str(Path(out_dir) / "groundtruth-scene.csv"), SCENE_FIELDS, out)
    return out


def gt_scene(map_path: str, out_dir: str, per_label: int, seed: int = 7, subsets: tuple[str, ...] = ("validation", "test"),
             cache_dir: str | None = None, dry_run: bool = False, get=http_get, throttle: Throttle | None = None,
             log=print) -> list[dict]:
    """Sample up to per_label unambiguous Open Images photos per scene label (seeded), download them to
    out_dir/<scene or group>/<subset>_<id>.jpg and write out_dir/groundtruth-scene.csv with each photo's url and
    md5 (the manifest `gt scene --from` re-fetches). The label CSVs (about 180 MB in all) are cached
    under cache_dir; --dry-run does everything but the photos and writes the CSV with the photo URL as
    path and no md5."""
    spec = read_scene_map(map_path)
    cache_dir = cache_dir or str(Path.home() / ".cache" / "bioscan" / "openimages")
    throttle = throttle or Throttle(0.2)
    names = {c.lower() for e in spec["label"].values() for k in ("any", "all", "not") for c in e.get(k, [])}
    names |= {c.lower() for a in (spec.get("attribute") or {}).values() for v in a.values() for c in v}
    ids = oid_class_ids(oid_cached(OID_CLASSES, cache_dir, get, log).read_bytes(), names)
    missing = sorted(names - set(ids))
    if missing:
        raise ValueError(f"{map_path}: class names not in Open Images: {', '.join(missing)}")
    wanted = set().union(*ids.values())
    rows, rng = [], random.Random(seed)
    for subset in subsets:
        positives = oid_positives(oid_cached(OID_LABELS[subset], cache_dir, get, log), wanted)
        with open(oid_cached(OID_IMAGES[subset], cache_dir, get, log), newline="", encoding="utf-8") as f:
            meta = {r["ImageID"]: r for r in csv.DictReader(f)}
        for label, image_ids in scene_assign(positives, spec["label"], ids).items():
            image_ids = [i for i in image_ids if i in meta]
            rng.shuffle(image_ids)
            e = spec["label"][label]
            for image_id in image_ids:
                if sum(r["scene_label"] == label for r in rows) >= per_label:
                    break
                m = meta[image_id]
                url = OID_PHOTO.format(subset=subset, image_id=image_id)
                path = Path(out_dir) / (e.get("scene") or e["group"]) / f"{subset}_{image_id}.jpg"   # as gt_scene_from
                md5 = "" if dry_run else fetch_photo(url, path, "", get, throttle)
                rows.append({"path": url if dry_run else str(path.resolve()), "tier": "scene", "scene": e.get("scene", ""),
                             "scene_group": e["group"], **scene_attributes(positives[image_id], spec.get("attribute") or {}, ids),
                             "license": m.get("License", ""), "attribution": f"{m.get('Author', '')} {m.get('OriginalLandingURL', '')}".strip(),
                             "source": f"openimages:{subset}:{image_id}", "url": url, "md5": md5, "scene_label": label})
    counts = {label: sum(r["scene_label"] == label for r in rows) for label in spec["label"]}
    for label, n in counts.items():
        log(f"{label}: {n} photos" + (f" (short of {per_label})" if n < per_label else ""))
    for r in rows:
        del r["scene_label"]
    if not dry_run or rows:
        write_csv(str(Path(out_dir) / "groundtruth-scene.csv"), SCENE_FIELDS, rows)
    return rows
