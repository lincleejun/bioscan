"""Pretty terminal rendering of the /run NDJSON event stream (spec section 7)."""
import os
import time
from collections import Counter

from bioscan import contract

LEVEL_ZH = {"species": "种", "genus": "属", "family": "科"}
# taxonomy is 7 levels: kingdom phylum class order family genus species
RANK_INDEX = {"genus": 5, "family": 4}


def box_label(i: int, box: dict) -> str:
    sp = box.get("species")
    if not sp:  # other_animal (null) or --no-species (omitted)
        return f"[{i}] {box.get('kind', '?')} {box.get('score', 0):.2f}"
    level, top = sp.get("level"), sp.get("top") or []
    if level == "unconfirmed" or not top:
        return f"[{i}] unconfirmed"
    t0 = top[0]
    if level == "species":
        return f"[{i}] {t0.get('common') or t0['scientific']} {t0['posterior']:.2f} 种"
    # genus / family: name the rank and sum the posterior mass of top entries sharing it
    k = RANK_INDEX[level]
    name = t0["taxonomy"][k]
    mass = sum(t["posterior"] for t in top if len(t.get("taxonomy", [])) > k and t["taxonomy"][k] == name)
    return f"[{i}] {name} {mass:.2f} {LEVEL_ZH[level]}"


def result_line(ev: dict) -> str:
    name = os.path.basename(ev["path"])
    ident = (ev.get("products") or {}).get("identify")
    if ident is None:  # identify not requested
        return name
    cls = ident["gate"]["class"]
    boxes = ident.get("boxes") or []
    if not boxes:
        return f"{name}  {cls}"
    n = f"{len(boxes)} box" if len(boxes) == 1 else f"{len(boxes)} boxes"
    labels = "  ".join(box_label(i, b) for i, b in enumerate(boxes, 1))
    return f"{name}  {cls:<7} {n:<7}  {labels}"


class Renderer:
    def __init__(self):
        self.start = time.monotonic()
        self.species = Counter()
        self.unconfirmed = 0
        self.results = 0
        self.paths: set[str] = set()
        self.errors: list[dict] = []
        self.done: dict | None = None

    def feed(self, ev: dict) -> str | None:
        """Consume one event; return the line to print, if any."""
        t = ev.get("type")
        if t == contract.RESULT:
            self.results += 1
            self.paths.add(ev.get("path"))
            for b in ((ev.get("products") or {}).get("identify") or {}).get("boxes") or []:
                sp = b.get("species")
                if not sp:
                    continue
                if sp.get("level") == "species" and sp.get("top"):
                    t0 = sp["top"][0]
                    self.species[t0.get("common") or t0["scientific"]] += 1
                elif sp.get("level") == "unconfirmed":
                    self.unconfirmed += 1
            return result_line(ev)
        if t == contract.ERROR:
            self.errors.append(ev)
            self.paths.add(ev.get("path"))
            where = f" [{ev['product']}]" if ev.get("product") else " [decode]"
            return f"{os.path.basename(ev.get('path', '?'))}  ERROR{where} {ev.get('message', '')}"
        if t == contract.DONE:
            self.done = ev
        return None  # progress and unknown types are silent

    def summary(self) -> str:
        elapsed = self.done["elapsed_ms"] if self.done else (time.monotonic() - self.start) * 1000
        n = len(self.paths)
        out = ["", "── summary ──"]
        for name, c in self.species.most_common():
            out.append(f"  {c:>4}  {name}")
        out.append(f"  unconfirmed boxes: {self.unconfirmed}")
        out.append(f"  images: {self.results} ok, {len(self.errors)} failed")
        for e in self.errors:
            out.append(f"    ! {e.get('path')}: {e.get('product') or 'decode'}: {e.get('message')}")
        per = elapsed / n if n else 0
        out.append(f"  elapsed: {elapsed / 1000:.1f}s total, {per:.0f} ms/image")
        if self.done is None:
            out.append("  warning: stream ended without a `done` event")
        return "\n".join(out)
