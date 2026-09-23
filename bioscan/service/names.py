"""Species name lists (AviList birds, MDD mammals) as BioCLIP 2.5 Huge text-embedding matrices.

Each list row gets the official TreeOfLife-200M text vector when its binomial matches a TreeOfLife
row of the same class, exactly or through `data/names/synonyms.csv` (birds: as recorded in the
committed `data/names/avilist_map.csv`, built by scripts/build_name_map.py; mammals: matched at
build time); otherwise the row is encoded with the text tower using TreeOfLife's own text format,
so both kinds of vectors live in one space. Birds also carry their BirdNET label from the map.
The result is cached per list as `<cache_dir>/<model>-<list_sha>.npz`.
"""
from __future__ import annotations

import csv
import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from bioscan.service.adapters.geo import _norm

log = logging.getLogger(__name__)

MODEL_NAME = "bioclip-2.5-vith14"
TOL_REPO = "imageomics/TreeOfLife-200M"
TOL_FILES = ("embeddings/txt_emb_bioclip-2.5-vith14.json", "embeddings/txt_emb_bioclip-2.5-vith14.npy")
DATA_DIR = Path(__file__).resolve().parents[2] / "data"
CACHE_DIR = Path("~/.cache/bioscan/names")
NAMES_DIR = "names"                 # under data_dir: synonyms.csv, avilist_map.csv
CACHE_VERSION = "2"  # bump when text format, matching or npz layout changes
DIM = 1024


@dataclass
class NameList:
    list_id: str                 # "avilist-2025" | "mdd-2025"
    kind: str                    # "bird" | "mammal"
    scientific: list[str]
    common: list[str]
    taxonomy: list[list[str]]    # 7 levels: kingdom .. species (binomial)
    matrix: np.ndarray           # (N, 1024) float32, L2-normalised
    tol_how: list[str]           # exact | synonym (official TreeOfLife vector) | none (self-encoded)
    sha: str = ""                # list hash (CSV + map + synonyms + list_id + cache version), the cache key
    birdnet: list[str] = field(default_factory=list)      # BirdNET label per row ("" = none); birds only
    birdnet_how: list[str] = field(default_factory=list)  # exact | synonym | none


def norm_binomial(name: str) -> str:
    """'Corvus_corax', ' corvus  Corax ' -> 'corvus corax' (match key, geo._norm after '_' -> ' ')."""
    return _norm(name.replace("_", " "))


def _taxonomy(cls: str, order: str, family: str, genus: str, epithet: str) -> list[str]:
    return ["Animalia", "Chordata", cls, order.strip().title(), family.strip().title(),
            genus.strip().capitalize(), f"{genus.strip().capitalize()} {epithet.strip().lower()}"]


def read_avilist(path: Path) -> list[tuple[str, str, list[str]]]:
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["Taxon_rank"] != "species":
                continue
            genus, epithet = norm_binomial(r["Scientific_name"]).split(" ", 1)
            tax = _taxonomy("Aves", r["Order"], r["Family"], genus, epithet)
            rows.append((tax[6], r["English_name_AviList"].strip(), tax))
    return rows


def read_mdd(path: Path) -> list[tuple[str, str, list[str]]]:
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            tax = _taxonomy("Mammalia", r["order"], r["family"], r["genus"], r["specificEpithet"])
            rows.append((tax[6], r["mainCommonName"].strip(), tax))
    return rows


# kind -> (list_id, data subdir, class, reader)
LISTS = {
    "bird": ("avilist-2025", "avilist", "Aves", read_avilist),
    "mammal": ("mdd-2025", "mdd", "Mammalia", read_mdd),
}


def tol_text(taxonomy: list[str], common: str) -> str:
    """Text exactly as TreeOfLife-toolbox make_txt_embedding.py builds it: the 7 ranks joined by
    spaces (species rank is the bare epithet), ' with common name X' when there is one, wrapped
    in its single template 'an image of {}.'."""
    name = " ".join(taxonomy[:6] + [taxonomy[6].split(" ")[-1]])
    if common:
        name += " with common name " + common
    return f"an image of {name}."


def read_synonyms(path: Path) -> list[dict[str, str]]:
    """synonyms.csv rows (avilist_scientific, alias, source, note); [] when the file is absent."""
    if not path.is_file():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return [r for r in csv.DictReader(f) if r.get("avilist_scientific") and r.get("alias")]


def aliases(synonyms: list[dict[str, str]], sources: tuple[str, ...]) -> dict[str, list[str]]:
    """norm(list name) -> aliases usable on one side. tol/birdnet/inat apply only to that side;
    spelling applies everywhere."""
    out: dict[str, list[str]] = {}
    for r in synonyms:
        if r["source"].strip() in sources:
            out.setdefault(norm_binomial(r["avilist_scientific"]), []).append(r["alias"].strip())
    return out


def match_names(scis: list[str], targets: dict[str, str], alias: dict[str, list[str]]) -> list[tuple[str, str]]:
    """(target name, how) per list name: exact normalised match, else the first alias that
    matches, else ("", "none"). `targets` maps norm_binomial(name) -> name as the target spells it."""
    out = []
    for sci in scis:
        key = norm_binomial(sci)
        if key in targets:
            out.append((targets[key], "exact"))
            continue
        hit = next((targets[norm_binomial(a)] for a in alias.get(key, ()) if norm_binomial(a) in targets), None)
        out.append((hit, "synonym") if hit else ("", "none"))
    return out


def tol_targets(tol_names, cls: str) -> dict[str, str]:
    return {norm_binomial(f"{t[5]} {t[6]}"): f"{t[5]} {t[6]}" for t, _c in tol_names if t[2] == cls}


def read_map(path: Path) -> dict[str, dict[str, str]]:
    """avilist_map.csv -> scientific -> row; {} when the file is absent."""
    if not path.is_file():
        return {}
    with open(path, newline="", encoding="utf-8") as f:
        return {r["scientific"]: r for r in csv.DictReader(f)}


def match_tol(rows, tol_names, cls: str, keys: list[str] | None = None) -> list[int | None]:
    """Index into `tol_names` for each row, or None. `keys` overrides the binomial looked up per row
    (a synonym; "" = no match). Only rows of class `cls` count; when a binomial appears more than
    once, the row with the same family wins, else the first."""
    index: dict[str, list[tuple[int, str]]] = {}
    for i, (tax, _common) in enumerate(tol_names):
        if tax[2] == cls:
            index.setdefault(norm_binomial(f"{tax[5]} {tax[6]}"), []).append((i, tax[4].lower()))
    out = []
    for (sci, _common, tax), key in zip(rows, keys if keys is not None else [r[0] for r in rows]):
        hits = index.get(norm_binomial(key)) if key else None
        if not hits:
            out.append(None)
            continue
        out.append(next((i for i, fam in hits if fam == tax[4].lower()), hits[0][0]))
    return out


def encode(texts: list[str], model, tokenizer, device, batch: int = 256) -> np.ndarray:
    import torch
    chunks = []
    with torch.no_grad():
        for start in range(0, len(texts), batch):
            feats = model.encode_text(tokenizer(texts[start:start + batch]).to(device)).float()
            chunks.append(torch.nn.functional.normalize(feats, dim=-1).cpu().numpy())
    return np.concatenate(chunks) if chunks else np.zeros((0, DIM), np.float32)


def _list_file(data_dir: Path, sub: str) -> Path:
    files = sorted((data_dir / sub).glob("*.csv"))
    if len(files) != 1:
        raise FileNotFoundError(f"expected exactly one CSV in {data_dir / sub}, found {len(files)}")
    return files[0]


def _read_tol(tol_files):
    names_path, npy_path = tol_files or _download_tol()
    with open(names_path, encoding="utf-8") as f:
        names = json.load(f)
    vecs = np.load(npy_path, mmap_mode="r")
    # TreeOfLife stores (dim, N); accept (N, dim) as well.
    if vecs.shape[0] != len(names):
        vecs = vecs.T
    if vecs.shape[0] != len(names):
        raise ValueError(f"TreeOfLife vectors {vecs.shape} do not match {len(names)} names")
    return names, vecs


def _download_tol():
    from huggingface_hub import hf_hub_download
    return tuple(hf_hub_download(TOL_REPO, f, repo_type="dataset") for f in TOL_FILES)


def _build(kind, rows, tol, model, tokenizer, device, keys: list[tuple[str, str]] | None,
           synonyms: list[dict[str, str]]) -> NameList:
    """`keys` = (TreeOfLife binomial, how) per row from the map; None = match here."""
    list_id, _sub, cls, _reader = LISTS[kind]
    names, vecs = tol
    if keys is None:
        keys = match_names([r[0] for r in rows], tol_targets(names, cls), aliases(synonyms, ("tol", "spelling")))
    hits = match_tol(rows, names, cls, [k for k, _ in keys])
    tol_how = [how if h is not None else "none" for (_k, how), h in zip(keys, hits)]
    official = np.array([h is not None for h in hits], dtype=bool)
    matrix = np.zeros((len(rows), vecs.shape[1]), np.float32)
    if official.any():
        idx = np.array([h for h in hits if h is not None])
        order = np.argsort(idx)  # sorted reads are much kinder to the memmap
        taken = np.empty((len(idx), vecs.shape[1]), np.float32)
        taken[order] = vecs[idx[order]]
        matrix[official] = taken
    missing = np.flatnonzero(~official)
    if len(missing):
        log.info("%s: encoding %d names with the text tower", list_id, len(missing))
        texts = [tol_text(rows[i][2], rows[i][1]) for i in missing]
        matrix[missing] = encode(texts, model, tokenizer, device)
    matrix /= np.linalg.norm(matrix, axis=1, keepdims=True)
    return NameList(list_id, kind, [r[0] for r in rows], [r[1] for r in rows], [r[2] for r in rows],
                    matrix, tol_how)


def _save(path: Path, nl: NameList) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "wb") as f:
        np.savez(f, scientific=np.array(nl.scientific), common=np.array(nl.common),
                 taxonomy=np.array(nl.taxonomy), matrix=nl.matrix, tol_how=np.array(nl.tol_how))
    os.replace(tmp, path)


def _load(path: Path, kind: str) -> NameList:
    with np.load(path, allow_pickle=False) as z:
        return NameList(LISTS[kind][0], kind, z["scientific"].tolist(), z["common"].tolist(),
                        z["taxonomy"].tolist(), z["matrix"], z["tol_how"].tolist())


def load_lists(model, tokenizer, device, cache_dir: Path = CACHE_DIR, *,
               data_dir: Path = DATA_DIR, tol_files: tuple[Path, Path] | None = None) -> dict[str, NameList]:
    """{"bird": NameList, "mammal": NameList}. `model`/`tokenizer` are only used on a cache miss.
    `tol_files` = (names.json, vectors.npy) overrides the HF download (tests)."""
    cache_dir = Path(cache_dir).expanduser()
    syn_path, map_path = data_dir / NAMES_DIR / "synonyms.csv", data_dir / NAMES_DIR / "avilist_map.csv"
    synonyms = read_synonyms(syn_path)
    amap = read_map(map_path)
    if not amap:
        log.warning("%s missing: birds matched to TreeOfLife here, no BirdNET labels (no geo prior)", map_path)
    out, tol = {}, None
    for kind, (list_id, sub, _cls, reader) in LISTS.items():
        src = _list_file(data_dir, sub)
        mapped = kind == "bird" and bool(amap)
        extra = b"".join(p.read_bytes() for p in (syn_path, map_path if mapped else None) if p and p.is_file())
        sha = hashlib.sha256(f"{CACHE_VERSION}\0{list_id}\0".encode() + src.read_bytes() + b"\0" + extra).hexdigest()[:16]
        path = cache_dir / f"{MODEL_NAME}-{sha}.npz"
        rows = reader(src)
        if path.is_file():
            out[kind] = _load(path, kind)
        else:
            if tol is None:
                tol = _read_tol(tol_files)
            keys = None
            if mapped:
                keys = [(m["tol_name"], m["tol_how"]) if (m := amap.get(r[0])) else ("", "none") for r in rows]
            out[kind] = _build(kind, rows, tol, model, tokenizer, device, keys, synonyms)
            _save(path, out[kind])
        if mapped:
            m = [amap.get(r[0], {}) for r in rows]
            out[kind].birdnet = [x.get("birdnet_label", "") for x in m]
            out[kind].birdnet_how = [x.get("birdnet_how") or "none" for x in m]
        out[kind].sha = sha
    for kind, s in stats(out).items():
        log.info("names %s: %d species, TreeOfLife %s, BirdNET %s", s["list_id"], s["total"], s["tol"], s["birdnet"])
    return out


def stats(lists: dict[str, NameList]) -> dict:
    out = {}
    for kind, nl in lists.items():
        total = len(nl.scientific)
        tol = {h: nl.tol_how.count(h) for h in ("exact", "synonym", "none")}
        official = tol["exact"] + tol["synonym"]
        out[kind] = {"list_id": nl.list_id, "total": total, "official": official, "encoded": total - official,
                     "coverage": official / total if total else 0.0, "tol": tol,
                     "birdnet": {h: nl.birdnet_how.count(h) for h in ("exact", "synonym", "none")}
                     if nl.birdnet else None}
    return out
