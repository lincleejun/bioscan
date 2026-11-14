"""Download the bird and mammal lists into data/avilist and data/mdd from this project's release (a mirror: the
AviList site refuses scripts), checking each sha256. A file already there with the right hash is kept.
Run from the repo root: `uv run python scripts/names/fetch_lists.py`."""
import hashlib
import sys
import urllib.request
from pathlib import Path

BASE = "https://github.com/lincleejun/gardener/releases/download/names-2026.11/"
LISTS = (("data/avilist/AviList-v2026-20Oct2026-extended-version.csv",
          "02663bc97888d8940eab5628ef60b951c3818d998174f8d7549ccc94fe0c85d9"),
         ("data/mdd/MDD_v2.5_6904species.csv", "0d07a7e9409712fa86c1e3afadcf4c67bf4f9e16d5693a878e11ec1bf6860493"))


def main(root: Path = Path(".")) -> int:
    for rel, sha in LISTS:
        p = root / rel
        if p.is_file() and hashlib.sha256(p.read_bytes()).hexdigest() == sha:
            print("ok  ", rel)
            continue
        with urllib.request.urlopen(BASE + p.name, timeout=300) as r:
            body = r.read()
        if hashlib.sha256(body).hexdigest() != sha:
            print("sha256 mismatch for", rel, file=sys.stderr)
            return 1
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(body)
        print("got ", rel, f"{len(body) >> 20} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
