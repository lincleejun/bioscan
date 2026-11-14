"""Rebuild data/names/names-zh.csv (scientific,zh) from Wikidata labels (CC0): every species item with an eBird taxon
ID (birds) or an ASM Mammal Diversity Database ID (mammals). zh-cn wins over zh-hans over zh; a label with no Chinese
character (a Latin name filed as the label) is dropped, and every name is written in simplified characters (many `zh`
labels are traditional). Run from the repo root, online:
`uv run --with opencc-python-reimplemented python scripts/names/build_names_zh.py`."""
import csv
import io
import re
import urllib.parse
import urllib.request
from pathlib import Path

from opencc import OpenCC

OUT = Path("data/names/names-zh.csv")
QUERY = """SELECT DISTINCT ?n ?a ?b ?c WHERE { ?i wdt:%s ?e ; wdt:P225 ?n ; wdt:P105 wd:Q7432 .
OPTIONAL { ?i rdfs:label ?a FILTER(LANG(?a)="zh-cn") } OPTIONAL { ?i rdfs:label ?b FILTER(LANG(?b)="zh-hans") }
OPTIONAL { ?i rdfs:label ?c FILTER(LANG(?c)="zh") } }"""
CJK = re.compile(r"[一-鿿]")


def fetch(prop: str) -> dict[str, str]:
    url = "https://query.wikidata.org/sparql?" + urllib.parse.urlencode({"query": QUERY % prop})
    req = urllib.request.Request(url, headers={"Accept": "text/csv", "User-Agent": "names-zh-build/1 (script)"})
    with urllib.request.urlopen(req, timeout=300) as r:
        rows = csv.DictReader(io.StringIO(r.read().decode("utf-8")))
        out: dict[str, str] = {}
        for x in rows:
            zh = next((x[k].strip() for k in "abc" if CJK.search(x[k] or "")), None)
            if zh:
                out.setdefault(x["n"].strip(), zh)
        return out


if __name__ == "__main__":
    names = fetch("P3444") | fetch("P12560")        # eBird taxon ID, ASM Mammal Diversity Database ID
    assert len(names) > 8000, len(names)            # about half of both lists have a Chinese label; fewer is a failed query
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["scientific", "zh"])
        t2s = OpenCC("t2s")
        w.writerows(sorted((k, t2s.convert(v)) for k, v in names.items()))
    print(len(names), "names ->", OUT)
