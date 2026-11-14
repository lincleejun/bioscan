"""The bird and mammal lists and their name vectors ship in the repo (data/avilist, data/mdd, data/names/cache), so a
fresh install downloads no TreeOfLife file. A list, synonym or map edit changes the cache key: this fails until the
cache is rebuilt (`uv run gardener-engine names stats`, then copy the new file from ~/.cache/bioscan/names)."""
import hashlib

from gardener.naming import read_synonyms
from gardener.service import names


def test_the_committed_lists_have_their_name_vectors_committed_too():
    d = names.DATA_DIR
    syn_path = d / names.NAMES_DIR / "synonyms.csv"
    synonyms = read_synonyms(syn_path)
    want = set()
    for kind, src in names.LISTS.items():
        amap, map_path = names._read_label_map(d, src, kind, synonyms)
        csv_path = names._list_file(d, src.subdir)
        tol_map = map_path if names._has_tol(amap) else None
        extra = b"".join(p.read_bytes() for p in (syn_path, tol_map) if p and p.is_file())
        sha = hashlib.sha256(f"{names.CACHE_VERSION}\0{src.list_id}\0".encode() + csv_path.read_bytes() + b"\0" + extra).hexdigest()[:16]
        want.add(f"{names.MODEL_NAME}-{sha}.npz")
    have = {p.name: p for p in (d / names.NAMES_DIR / "cache").glob("*.npz")}
    assert set(have) == want                       # none missing, none left over from an older list
    assert not any(names._stale(p) for p in have.values())      # built with the pinned BioCLIP and TreeOfLife revisions
