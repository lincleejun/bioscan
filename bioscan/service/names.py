"""Species name lists (AviList birds, MDD mammals) as BioCLIP 2.5 Huge text-embedding matrices.

Each list row gets the official TreeOfLife-200M text vector when its binomial matches a TreeOfLife
row of the same class, exactly or through `data/names/synonyms.csv` (birds: as recorded in the
committed `data/names/avilist_map.csv`, built by scripts/build_name_map.py; mammals: matched at
build time); otherwise the row is encoded with the text tower using TreeOfLife's own text format,
so both kinds of vectors live in one space. A list with a label map (birds: avilist_map.csv) also
carries each row's BirdNET label from it, the location-prior label. `load_lists` returns finished
lists: callers never patch fields onto a NameList. The matrix is cached per list as
`<cache_dir>/<model>-<list_sha>.npz`; labels and sha are not cached, they come from the map and key.
"""
from __future__ import annotations

import csv
import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, NamedTuple

import numpy as np

from bioscan.naming import DATA_DIR, NAMES_DIR, aliases, map_problems, norm_binomial, read_synonyms

log = logging.getLogger(__name__)

MODEL_NAME = "bioclip-2.5-vith14"
TOL_REPO = "imageomics/TreeOfLife-200M"
TOL_REVISION = "5f2dc493b3dc0e544438a04038ab15faa646b749"   # the snapshot data/README.md was measured on
TOL_FILES = ("embeddings/txt_emb_bioclip-2.5-vith14.json", "embeddings/txt_emb_bioclip-2.5-vith14.npy")
CACHE_DIR = Path("~/.cache/bioscan/names")
CACHE_VERSION = "2"  # bump when text format, matching or npz layout changes
DIM = 1024


@dataclass(frozen=True)
class NameList:
    """One species list, complete as `load_lists` returns it. Frozen: fields are set once, there."""
    list_id: str                 # "avilist-2025" | "mdd-2025"
    kind: str                    # "bird" | "mammal"
    scientific: list[str]
    common: list[str]
    taxonomy: list[list[str]]    # 7 levels: kingdom .. species (binomial)
    matrix: np.ndarray           # (N, 1024) float32, L2-normalised
    tol_how: list[str]           # exact | synonym (official TreeOfLife vector) | none (self-encoded)
    sha: str = ""                # list hash (CSV + map + synonyms + list_id + cache version), the cache key
    # BirdNET (location-prior) label per row, "" = none, and how it matched (exact | synonym | none).
    # Both empty when the list has no label map (mammals; birds when avilist_map.csv is missing).
    birdnet: list[str] = field(default_factory=list)
    birdnet_how: list[str] = field(default_factory=list)


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


class ListSource(NamedTuple):
    """Where a list comes from. `label_map` names a CSV under data/names that maps each row to its
    TreeOfLife name and BirdNET label (scripts/build_name_map.py); None = matched to TreeOfLife at
    build time through synonyms.csv, with no BirdNET labels and so no location prior."""
    list_id: str
    subdir: str                  # under data_dir, holding exactly one CSV
    cls: str                     # the TreeOfLife class its rows match against
    reader: Callable[[Path], list[tuple[str, str, list[str]]]]
    label_map: str | None


LISTS = {
    "bird": ListSource("avilist-2025", "avilist", "Aves", read_avilist, "avilist_map.csv"),
    "mammal": ListSource("mdd-2025", "mdd", "Mammalia", read_mdd, None),
}


def tol_text(taxonomy: list[str], common: str) -> str:
    """Text exactly as TreeOfLife-toolbox make_txt_embedding.py builds it: the 7 ranks joined by
    spaces (species rank is the bare epithet), ' with common name X' when there is one, wrapped
    in its single template 'an image of {}.'."""
    name = " ".join(taxonomy[:6] + [taxonomy[6].split(" ")[-1]])
    if common:
        name += " with common name " + common
    return f"an image of {name}."


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
    for (_sci, _common, tax), key in zip(rows, keys if keys is not None else [r[0] for r in rows]):
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
    return tuple(hf_hub_download(TOL_REPO, f, repo_type="dataset", revision=TOL_REVISION) for f in TOL_FILES)


def _built_with() -> dict[str, str]:
    """What a cached matrix depends on besides the lists: the text tower and the official vectors."""
    from bioscan.service.adapters import bioclip

    return {"bioclip_revision": bioclip.REVISION, "tol_revision": TOL_REVISION}


def _build(src: ListSource, rows, tol, model, tokenizer, device, keys: list[tuple[str, str]] | None,
           synonyms: list[dict[str, str]]) -> dict:
    """The cached columns of one list. `keys` = (TreeOfLife binomial, how) per row from the label
    map; None = match here."""
    names, vecs = tol
    if keys is None:
        keys = match_names([r[0] for r in rows], tol_targets(names, src.cls), aliases(synonyms, ("tol", "spelling")))
    hits = match_tol(rows, names, src.cls, [k for k, _ in keys])
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
        log.info("%s: encoding %d names with the text tower", src.list_id, len(missing))
        texts = [tol_text(rows[i][2], rows[i][1]) for i in missing]
        matrix[missing] = encode(texts, model, tokenizer, device)
    matrix /= np.linalg.norm(matrix, axis=1, keepdims=True)
    return {"scientific": [r[0] for r in rows], "common": [r[1] for r in rows], "taxonomy": [r[2] for r in rows],
            "matrix": matrix, "tol_how": tol_how}


def _save(path: Path, cols: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "wb") as f:
        np.savez(f, scientific=np.array(cols["scientific"]), common=np.array(cols["common"]),
                 taxonomy=np.array(cols["taxonomy"]), matrix=cols["matrix"], tol_how=np.array(cols["tol_how"]),
                 **{k: np.array(v) for k, v in _built_with().items()})
    os.replace(tmp, path)


def _stale(path: Path) -> bool:
    """True when the cache was built with another BioCLIP or TreeOfLife revision. Caches written
    before revisions were recorded are trusted (they were built with the revisions now pinned)."""
    with np.load(path, allow_pickle=False) as z:
        recorded = {k: str(z[k]) for k in _built_with() if k in z.files}
    if not recorded:
        log.info("%s predates recorded model revisions; using it", path.name)
    return any(recorded[k] != v for k, v in _built_with().items() if k in recorded)


def _load(path: Path) -> dict:
    with np.load(path, allow_pickle=False) as z:
        return {"scientific": z["scientific"].tolist(), "common": z["common"].tolist(),
                "taxonomy": z["taxonomy"].tolist(), "matrix": z["matrix"], "tol_how": z["tol_how"].tolist()}


def _read_label_map(data_dir: Path, src: ListSource, kind: str, synonyms) -> tuple[dict[str, dict[str, str]], Path | None]:
    """(label map, its path); ({}, None) when the list has none or the file is missing (warned)."""
    if src.label_map is None:
        return {}, None
    map_path = data_dir / NAMES_DIR / src.label_map
    amap = read_map(map_path)
    if not amap:
        log.warning("%s missing: %ss matched to TreeOfLife here, no BirdNET labels (no geo prior)", map_path, kind)
        return {}, None
    for problem in map_problems(amap, synonyms):
        log.warning("stale name map: %s", problem)
    return amap, map_path


def load_lists(model, tokenizer, device, cache_dir: Path = CACHE_DIR, *,
               data_dir: Path = DATA_DIR, tol_files: tuple[Path, Path] | None = None) -> dict[str, NameList]:
    """{"bird": NameList, "mammal": NameList}. `model`/`tokenizer` are only used on a cache miss.
    `tol_files` = (names.json, vectors.npy) overrides the HF download (tests)."""
    cache_dir = Path(cache_dir).expanduser()
    syn_path = data_dir / NAMES_DIR / "synonyms.csv"
    synonyms = read_synonyms(syn_path)
    out, tol = {}, None
    for kind, src in LISTS.items():
        amap, map_path = _read_label_map(data_dir, src, kind, synonyms)
        csv_path = _list_file(data_dir, src.subdir)
        # The key hashes the label map only when one is used (cache key unchanged since v2).
        extra = b"".join(p.read_bytes() for p in (syn_path, map_path) if p and p.is_file())
        sha = hashlib.sha256(f"{CACHE_VERSION}\0{src.list_id}\0".encode() + csv_path.read_bytes() + b"\0" + extra).hexdigest()[:16]
        path = cache_dir / f"{MODEL_NAME}-{sha}.npz"
        rows = src.reader(csv_path)
        if path.is_file() and not _stale(path):
            cols = _load(path)
        else:
            if tol is None:
                tol = _read_tol(tol_files)
            keys = None
            if amap:
                keys = [(m["tol_name"], m["tol_how"]) if (m := amap.get(r[0])) else ("", "none") for r in rows]
            cols = _build(src, rows, tol, model, tokenizer, device, keys, synonyms)
            _save(path, cols)
        labels = {}
        if amap:
            m = [amap.get(r[0], {}) for r in rows]
            labels = {"birdnet": [x.get("birdnet_label", "") for x in m],
                      "birdnet_how": [x.get("birdnet_how") or "none" for x in m]}
        out[kind] = NameList(src.list_id, kind, **cols, sha=sha, **labels)
    for s in stats(out).values():
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
