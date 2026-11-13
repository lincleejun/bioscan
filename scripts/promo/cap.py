# ruff: noqa  (a one-off generator, kept as it ran)
"""Screens and photos for build_promo.py, from a running `gardener serve` on a COPY of a library.
usage: cap.py OUT_DIR FOLDER   (FOLDER: a folder of photos under an --allow-root, not yet in the library)"""
import json, sys, urllib.request
from urllib.parse import quote
from playwright.sync_api import sync_playwright
B = "http://127.0.0.1:8791"; OUT, FOLDER = sys.argv[1], sys.argv[2]
api = lambda p: json.load(urllib.request.urlopen(B + "/api/v1" + p))
n = {k: api(f"/assets?{q}&limit=1")["total"] for k, q in (("all", ""), ("good", "pile=good"), ("bin", "pile=bin"))}
sp = api("/species"); n["species"] = len(sp["species"])
T = next(t["id"] for t in api("/tasks") if "byxbee" in t["title"])
tsp = api(f"/species?task={T}"); cards = sorted(tsp["species"] + tsp["to_confirm"], key=lambda k: -k["photos"])
spar = next(k for k in cards if "White" in (k.get("common") or "") and "Sparrow" in k["common"])
boxes = {}
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    pg = b.new_page(viewport={"width": 1920, "height": 1080}, device_scale_factor=2)
    pg.add_init_script("document.addEventListener('DOMContentLoaded',()=>{const s=document.createElement('style');s.textContent='.toast{display:none!important}';document.head.appendChild(s)})")
    def shot(name, sels=(), wait=2500):
        pg.wait_for_timeout(wait)
        pg.screenshot(path=f"{OUT}/ui/{name}.jpg", quality=88)
        boxes[name] = {s: pg.locator(s).first.bounding_box() for s in sels if pg.locator(s).count()}
    pg.goto(f"{B}/#/library"); shot("library", [".tile >> nth=9", "[data-nav=lib-bin]", "[data-nav=lib-good]"], 3500)
    pg.goto(f"{B}/#/library/good"); shot("good", ["h1"], 3500)
    pg.goto(f"{B}/#/species"); shot("species", [".kchip >> nth=1", "h1"], 3500)
    pg.goto(f"{B}/#/task/{T}/review/{quote(spar['concept'], safe='')}"); shot("review", [".fr-rail", ".gt >> nth=0"], 3500)
    pg.locator(".gt .et").nth(6).click(); shot("compare", [], 4000)
    # the bin: X on one photo in the library, then the bin page (a copy of the library: nothing real is marked)
    pg.goto(f"{B}/#/library"); pg.wait_for_timeout(3500)
    pg.locator(".tile").nth(9).click(); shot("selected", [".tile >> nth=9"], 600)
    pg.keyboard.press("x"); pg.wait_for_timeout(1200)
    pg.goto(f"{B}/#/library/bin"); shot("bin", ["[data-action=bin-empty]", "h1"], 3500)
    n["bin"] = api("/assets?pile=bin&limit=1")["total"]
    # a folder, one click, the library
    pg.goto(f"{B}/#/fs/{quote(FOLDER, safe='/')}"); shot("fs", ["[data-action=fs-add][data-kind=browse]", "[data-action=fs-add][data-kind=sort]"], 4000)
    pg.locator("[data-action=fs-add][data-kind=browse]").click(); shot("added", [".tile >> nth=0"], 7000)
    n["added"] = pg.locator(".tile").count()
    b.close()
good = [a["id"] for a in api("/assets?pile=good&limit=200")["items"]]
for i, a in enumerate(good[::max(1, len(good) // 12)][:12]):
    urllib.request.urlretrieve(f"{B}/api/v1/assets/{a}/large", f"{OUT}/ph/g{i:02d}.jpg")
json.dump({"boxes": boxes, "n": n}, open(f"{OUT}/ui/boxes.json", "w"), indent=1, ensure_ascii=False)
print(json.dumps({"n": n, "boxes": boxes}, ensure_ascii=False)[:2500])
