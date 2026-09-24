"""Species name lists (AviList birds, MDD mammals) as BioCLIP 2.5 Huge text-embedding matrices.

Each list row gets the official TreeOfLife-200M text vector when its binomial matches a TreeOfLife
row of the same class, exactly or through `data/names/synonyms.csv` (birds: as recorded in the
committed `data/names/avilist_map.csv`, built by scripts/build_name_map.py; mammals: matched at
build time); otherwise the row is encoded with the text tower using TreeOfLife's own text format,
so both kinds of vectors live in one space. A list with a label map (birds: avilist_map.csv,
mammals: mdd_map.csv) also carries each row's BirdNET label from it, the location-prior label, and
the list's unlabelled policy (what a row without one gets). `load_lists` returns finished
lists: callers never patch fields onto a NameList. The matrix is cached per list as
`<cache_dir>/<model>-<list_sha>.npz`; labels and sha are not cached, they come from the map and key.

Everything else gets the all-taxa list (`ALL_TAXA`): the species-level TreeOfLife rows of the
animal kingdom minus the classes a curated list covers, taken with their official vectors as they
are (nothing to encode), stored float16 and cached the same way.
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
    sha: str = ""                # list hash (CSV + TreeOfLife map + synonyms + list_id + cache version), the cache key
    # BirdNET (location-prior) label per row, "" = none, "A|B" = a lump (geo.LABEL_SEP), and how it
    # matched (exact | synonym | none). Both empty when the list's label map is missing.
    birdnet: list[str] = field(default_factory=list)
    birdnet_how: list[str] = field(default_factory=list)
    unlabelled: str = "zero"     # location prior for a row with no label: zero | genus (geo.LocationPrior)
    label_map_sha: str = ""      # label_map_sha(label map file) the labels came from; "" = no map
    source: str = ""             # the taxonomy the rows follow (engine info), "" = not stated


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
    BirdNET label and, when it has `tol_name`/`tol_how` columns, its TreeOfLife name
    (scripts/build_name_map.py); a map without them (mdd_map.csv), or no map, leaves TreeOfLife
    matching to build time through synonyms.csv. No map = no BirdNET labels, no location prior.
    `unlabelled` is the list's unlabelled policy (geo.UNLABELLED_POLICIES; in the fingerprint)."""
    list_id: str
    subdir: str                  # under data_dir, holding exactly one CSV
    cls: str                     # the TreeOfLife class its rows match against
    reader: Callable[[Path], list[tuple[str, str, list[str]]]]
    label_map: str | None
    unlabelled: str = "zero"
    source: str = ""             # the taxonomy it follows, reported by engine info()


LISTS = {
    # zero: the 748 unlabelled AviList rows are mostly extinct or lumped sisters that must stay suppressed
    "bird": ListSource("avilist-2025", "avilist", "Aves", read_avilist, "avilist_map.csv", "zero", "AviList v2025"),
    # genus: BirdNET labels only 1,048 of 6,904 MDD species; the rest borrow their genus's presence
    "mammal": ListSource("mdd-2025", "mdd", "Mammalia", read_mdd, "mdd_map.csv", "genus", "MDD v2.5"),
}


class AllTaxaSource(NamedTuple):
    """The all-taxa list: species-level TreeOfLife rows of `kingdoms`, minus `exclude` classes."""
    list_id: str
    kind: str                    # the box kind it names
    kingdoms: tuple[str, ...]
    exclude: tuple[str, ...]     # classes with a curated list (LISTS)
    source: str


# Animals only. Plants and fungi stay out: the gate has no plant class, so no box could reach them,
# and they would add their rows to every other_animal softmax and to memory. A plant list is a
# second AllTaxaSource (kingdoms=("Plantae",)) plus a gate class, not a change to this one.
ALL_TAXA = AllTaxaSource("tol200m-animalia", "other_animal", ("Animalia",), tuple(s.cls for s in LISTS.values()),
                         f"TreeOfLife-200M @ {TOL_REVISION[:8]}")


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
    """A label map (avilist_map.csv, mdd_map.csv) -> scientific -> row; {} when the file is absent."""
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
    if _has_tol(amap):                        # the synonyms check is about the TreeOfLife + BirdNET map
        for problem in map_problems(amap, synonyms):
            log.warning("stale name map: %s", problem)
    return amap, map_path


def label_map_sha(path: Path | None) -> str:
    """12 hex characters of sha256 over a label map file; "" when there is none. Reported in
    engine info() and part of settings.fingerprint(): a labels-only map (mdd_map.csv) is not in the
    list sha, yet editing it changes answers."""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12] if path is not None and path.is_file() else ""


def _has_tol(amap: dict[str, dict[str, str]]) -> bool:
    """True when the map fixes each row's TreeOfLife name (avilist_map.csv); labels-only maps do not."""
    return bool(amap) and "tol_name" in next(iter(amap.values()))
ALL_TAXA_VERSION = "2"            # bump when the row filter, dedupe or file layout below changes
_GATHER_BLOCK = 64               # dimensions (dim-major file) or 64k rows (row-major) read at a time
# Kingdom names that mean animals, and the phyla that make a row with an empty kingdom an animal row.
ANIMAL_KINGDOMS = ("animalia", "metazoa")
ANIMAL_PHYLA = ("chordata", "arthropoda", "mollusca", "annelida", "cnidaria", "echinodermata", "platyhelminthes",
                "nematoda", "porifera", "bryozoa", "rotifera", "tardigrada", "nemertea", "brachiopoda", "ctenophora",
                "onychophora", "chaetognatha", "sipuncula", "acanthocephala", "nematomorpha", "priapulida",
                "hemichordata", "entoprocta", "phoronida", "gastrotricha", "kinorhyncha", "xenacoelomorpha")
# Why a TreeOfLife row is left out (all_taxa_select's `reasons`), in the order the rules are tried.
DROP_REASONS = ("not 7 ranks", "not an animal", "no kingdom, phylum not animal", "curated class (Aves/Mammalia)",
                "no class, curated order", "no genus", "no epithet (higher rank)", "infraspecific or unparsed epithet",
                "duplicate binomial")


def _rank(x) -> str:
    return x.strip() if isinstance(x, str) else ""


def all_taxa_select(tol_names, src: AllTaxaSource = ALL_TAXA, reasons: dict[str, int] | None = None
                    ) -> dict[int, list[str]]:
    """{TreeOfLife row index: its 7-rank taxonomy as the list stores it}, in file order. The rule:

    - 7 ranks (kingdom .. species epithet); a null rank reads as empty. Intermediate ranks (phylum,
      class, order, family) may be empty: some backbones leave reptiles and fish without a class.
    - An animal: kingdom Animalia or Metazoa (any case); an empty kingdom counts when the phylum is
      an animal phylum (ANIMAL_PHYLA), and is stored as Animalia so candidates can name it.
    - Not a curated class (`src.exclude`: Aves, Mammalia, any case). A row with an empty class is
      also left out when its order is one TreeOfLife files under those classes (a bird or mammal
      that lost its class must not re-enter by the back door).
    - Species level: a genus and a one-word epithet. An epithet that repeats the genus ("Crotalus
      oreganus" in the epithet column) is read as its second word; any other multi-word epithet is
      infraspecific or unparsed and left out, as are rows without genus or epithet (higher ranks).
    - A binomial listed more than once keeps one row, the first with a common name, else the first:
      duplicates would split one species' probability between rows.

    `reasons`, when given, counts every left-out row under its DROP_REASONS entry."""
    drop = reasons if reasons is not None else {}
    kingdoms = {k.lower() for k in src.kingdoms}
    animals = "animalia" in kingdoms
    kingdoms |= set(ANIMAL_KINGDOMS) if animals else set()
    exclude = {c.lower() for c in src.exclude}
    ranks_of = [[_rank(x) for x in tax] if isinstance(tax, (list, tuple)) else [] for tax, _c in tol_names]
    curated_orders = {r[3].lower() for r in ranks_of if len(r) == 7 and r[2].lower() in exclude and r[3]}
    keep: dict[str, tuple[int, list[str]]] = {}
    for i, r in enumerate(ranks_of):
        if len(r) != 7:
            why = "not 7 ranks"
        elif r[0] and r[0].lower() not in kingdoms:
            why = "not an animal"
        elif not r[0] and not (animals and r[1].lower() in ANIMAL_PHYLA):
            why = "no kingdom, phylum not animal"
        elif r[2].lower() in exclude:
            why = "curated class (Aves/Mammalia)"
        elif not r[2] and r[3].lower() in curated_orders:
            why = "no class, curated order"
        else:
            genus, words = r[5], r[6].split()
            if len(words) == 2 and words[0].lower() == genus.lower():
                words = words[1:]
            why = ("no genus" if not genus else "no epithet (higher rank)" if not words
                   else "infraspecific or unparsed epithet" if len(words) > 1 or " " in genus else "")
        if why:
            drop[why] = drop.get(why, 0) + 1
            continue
        tax = [r[0] or src.kingdoms[0], *r[1:5], genus, f"{genus} {words[0]}"]
        key = norm_binomial(tax[6])
        j = keep.get(key)
        if j is None or (_rank(tol_names[i][1]) and not _rank(tol_names[j[0]][1])):
            keep[key] = (i, tax)
        if j is not None:
            drop["duplicate binomial"] = drop.get("duplicate binomial", 0) + 1
    return dict(sorted(keep.values()))


def all_taxa_rows(tol_names, src: AllTaxaSource = ALL_TAXA) -> list[int]:
    """Indexes into TreeOfLife's names of the rows the all-taxa list keeps (all_taxa_select)."""
    return list(all_taxa_select(tol_names, src))


def all_taxa_census(tol_names, src: AllTaxaSource = ALL_TAXA, species: list[str] = ()) -> dict:
    """What the all-taxa build sees, for CI logs: rows per (kingdom, class or "(empty)"), rows left
    out per reason, the list size, and for each of `species` every TreeOfLife row whose genus and
    epithet (or epithet column alone) spell it or a subspecies of it, raw, with the taxonomy the list keeps for it (None
    = left out), plus how many rows carry its genus and one of them raw."""
    census: dict[tuple[str, str], int] = {}
    rows = [[_rank(x) for x in tax] if isinstance(tax, (list, tuple)) else [] for tax, _c in tol_names]
    for r in rows:
        key = (r[0] if r else "", (r[2] if len(r) > 2 else "") or "(empty)")
        census[key] = census.get(key, 0) + 1
    reasons: dict[str, int] = {}
    kept = all_taxa_select(tol_names, src, reasons)
    wanted = {norm_binomial(s): s for s in species}
    genera = {k.split(" ")[0]: s for k, s in wanted.items()}
    found: dict[str, dict] = {s: {"rows": [], "genus_rows": 0, "genus_example": None} for s in species}
    for i, r in enumerate(rows):
        if len(r) < 2:
            continue
        name = norm_binomial(f"{r[-2]} {r[-1]}")
        hit = (wanted.get(name) or wanted.get(norm_binomial(r[-1]))
               or wanted.get(" ".join(name.split(" ")[:2])))                        # a subspecies row counts too
        if hit is not None:
            found[hit]["rows"].append({"row": i, "raw": list(tol_names[i]), "kept": kept.get(i)})
        g = genera.get(norm_binomial(r[-2]))
        if g is not None:
            found[g]["genus_rows"] += 1
            found[g]["genus_example"] = found[g]["genus_example"] or list(tol_names[i])
    return {"rows": len(tol_names), "by_kingdom_class": sorted(census.items(), key=lambda kv: -kv[1]),
            "dropped": {k: reasons.get(k, 0) for k in DROP_REASONS}, "kept": len(kept), "species": found}


def _gather16(vecs: np.ndarray, idx: np.ndarray) -> np.ndarray:
    """vecs[idx] as float16 rows, L2-normalised. TreeOfLife's file is (dim, N): a row of the (N, dim)
    view is strided across the whole file, so read it a few dimensions at a time instead."""
    out = np.empty((len(idx), vecs.shape[1]), np.float16)
    raw = vecs.T
    if raw.flags.c_contiguous:                       # the (dim, N) file seen through .T
        for d in range(0, raw.shape[0], _GATHER_BLOCK):
            out[:, d:d + _GATHER_BLOCK] = np.asarray(raw[d:d + _GATHER_BLOCK])[:, idx].T
    else:
        for r in range(0, len(idx), _GATHER_BLOCK * 1024):
            out[r:r + _GATHER_BLOCK * 1024] = vecs[idx[r:r + _GATHER_BLOCK * 1024]]
    for r in range(0, len(out), _GATHER_BLOCK * 1024):
        x = out[r:r + _GATHER_BLOCK * 1024].astype(np.float32)
        out[r:r + _GATHER_BLOCK * 1024] = x / np.linalg.norm(x, axis=1, keepdims=True)
    return out


def all_taxa_sha(src: AllTaxaSource = ALL_TAXA) -> str:
    """The list sha: what the rows are made from (TreeOfLife snapshot, filter, layout version)."""
    key = "\0".join([CACHE_VERSION, ALL_TAXA_VERSION, src.list_id, TOL_REVISION, ",".join(src.kingdoms),
                     ",".join(src.exclude)])
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def all_taxa_path(cache_dir: Path = CACHE_DIR, src: AllTaxaSource = ALL_TAXA) -> Path:
    return Path(cache_dir).expanduser() / f"{MODEL_NAME}-{all_taxa_sha(src)}.npz"


def load_all_taxa(cache_dir: Path = CACHE_DIR, *, tol_files: tuple[Path, Path] | None = None, tol=None,
                  src: AllTaxaSource = ALL_TAXA) -> NameList | None:
    """The all-taxa NameList from its cache, built from the TreeOfLife files on a miss (no model:
    every row has its official vector). None when TreeOfLife has no row for it. Strings are kept
    as one JSON blob in the npz and interned on load: a few hundred thousand rows as numpy unicode
    arrays would cost more than the matrix."""
    path, sha = all_taxa_path(cache_dir, src), all_taxa_sha(src)
    if not (path.is_file() and not _stale(path)):
        names, vecs = tol if tol is not None else _read_tol(tol_files)
        reasons: dict[str, int] = {}
        selected = all_taxa_select(names, src, reasons)
        if not selected:
            return None
        keep, taxonomy = list(selected), list(selected.values())
        log.info("%s: taking %d TreeOfLife rows (of %d); left out: %s", src.list_id, len(keep), len(names),
                 {k: v for k, v in reasons.items() if v})
        blob = json.dumps({"scientific": [t[6] for t in taxonomy], "common": [_rank(names[i][1]) for i in keep],
                           "taxonomy": taxonomy}, ensure_ascii=False).encode()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with open(tmp, "wb") as f:
            np.savez(f, matrix=_gather16(vecs, np.asarray(keep)), rows=np.frombuffer(blob, np.uint8),
                     **{k: np.array(v) for k, v in _built_with().items()})
        os.replace(tmp, path)
    with np.load(path, allow_pickle=False) as z:
        cols, matrix = json.loads(z["rows"].tobytes().decode()), z["matrix"]
    pool: dict[str, str] = {}
    taxonomy = [[pool.setdefault(r, r) for r in t] for t in cols["taxonomy"]]   # ranks repeat: share them
    log.info("%s: %d species, matrix %s %.0f MiB", src.list_id, len(taxonomy), matrix.dtype, matrix.nbytes / 2**20)
    return NameList(src.list_id, src.kind, [t[6] for t in taxonomy], cols["common"], taxonomy, matrix,
                    ["exact"] * len(taxonomy), sha=sha, source=src.source)


def load_lists(model, tokenizer, device, cache_dir: Path = CACHE_DIR, *,
               data_dir: Path = DATA_DIR, tol_files: tuple[Path, Path] | None = None) -> dict[str, NameList]:
    """{"bird": NameList, "mammal": NameList, "other_animal": all-taxa NameList}. `model`/`tokenizer`
    are only used on a cache miss. `tol_files` = (names.json, vectors.npy) overrides the HF download
    (tests). The all-taxa list is left out, with a warning, when it cannot be loaded or built (say
    its cache is missing and the TreeOfLife files were deleted)."""
    cache_dir = Path(cache_dir).expanduser()
    syn_path = data_dir / NAMES_DIR / "synonyms.csv"
    synonyms = read_synonyms(syn_path)
    out, tol = {}, None
    for kind, src in LISTS.items():
        amap, map_path = _read_label_map(data_dir, src, kind, synonyms)
        csv_path = _list_file(data_dir, src.subdir)
        # The key hashes the label map only when it decides the matrix (TreeOfLife columns): a
        # labels-only map (mdd_map.csv) leaves the cached matrix, and so the key, as it was (v2).
        tol_map = map_path if _has_tol(amap) else None
        extra = b"".join(p.read_bytes() for p in (syn_path, tol_map) if p and p.is_file())
        sha = hashlib.sha256(f"{CACHE_VERSION}\0{src.list_id}\0".encode() + csv_path.read_bytes() + b"\0" + extra).hexdigest()[:16]
        path = cache_dir / f"{MODEL_NAME}-{sha}.npz"
        rows = src.reader(csv_path)
        if path.is_file() and not _stale(path):
            cols = _load(path)
        else:
            if tol is None:
                tol = _read_tol(tol_files)
            keys = None
            if tol_map:
                keys = [(m["tol_name"], m["tol_how"]) if (m := amap.get(r[0])) else ("", "none") for r in rows]
            cols = _build(src, rows, tol, model, tokenizer, device, keys, synonyms)
            _save(path, cols)
        labels = {}
        if amap:
            m = [amap.get(r[0], {}) for r in rows]
            labels = {"birdnet": [x.get("birdnet_label", "") for x in m],
                      "birdnet_how": [x.get("birdnet_how") or "none" for x in m],
                      "label_map_sha": label_map_sha(map_path)}
        out[kind] = NameList(src.list_id, kind, **cols, sha=sha, **labels, unlabelled=src.unlabelled, source=src.source)
    try:
        other = load_all_taxa(cache_dir, tol_files=tol_files, tol=tol)
    except Exception as exc:  # noqa: BLE001 - an optional list must not cost birds and mammals
        log.warning("all-taxa list unavailable (%s: %s): %s boxes get no species", type(exc).__name__, exc,
                    ALL_TAXA.kind)
    else:
        if other is not None:
            out[other.kind] = other
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
