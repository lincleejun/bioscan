# ruff: noqa  (a one-off generator, kept as it ran)
import json, sys, urllib.request
from playwright.sync_api import sync_playwright
B = "http://127.0.0.1:8791"; T = "t_2438178fc35d"; OUT = sys.argv[1]
api = lambda p: json.load(urllib.request.urlopen(B + "/api/v1" + p))
sp = api(f"/species?task={T}"); cards = sorted(sp["species"] + sp["to_confirm"], key=lambda k: -k["photos"])
spar = next(k for k in cards if "Sparrow" in (k.get("common") or "") and "White" in k["common"])
boxes = {}
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    pg = b.new_page(viewport={"width": 1920, "height": 1080}, device_scale_factor=2)
    def shot(name, sels=()):
        pg.wait_for_timeout(900)
        pg.screenshot(path=f"{OUT}/ui/{name}.png")
        boxes[name] = {s: pg.locator(s).first.bounding_box() for s in sels if pg.locator(s).count()}
    pg.goto(f"{B}/#/task/{T}/scan"); pg.wait_for_timeout(2500); shot("scan")
    from urllib.parse import quote
    pg.goto(f"{B}/#/task/{T}/review/{quote(spar['concept'], safe='')}"); pg.wait_for_timeout(3000)
    pg.add_style_tag(content=".toast{display:none!important}")
    shot("review", [".fr-rail", ".fr-center", ".gr-bar", ".gt >> nth=0", ".fl-foot", "[data-action=ai-picks]"])
    for i in range(4):
        pg.locator(".gt").nth(i).hover(); pg.locator(".gt-ck").nth(i).click(); pg.wait_for_timeout(150)
        boxes[f"ck{i}"] = pg.locator(".gt-ck").nth(i).bounding_box()
    pg.mouse.move(1500, 1000)
    shot("selected", [".gr-bar", "[data-action=gr-sel-done]", "[data-action=gr-sel-none]"])
    pg.locator("[data-action=gr-sel-done]").click(); pg.wait_for_timeout(2500)
    shot("done", [".gr-bar"])
    for i in (4, 5):
        pg.locator(".gt").nth(i).hover(); pg.locator(".gt-ck").nth(i).click(); pg.wait_for_timeout(150)
        boxes[f"ck{i}"] = pg.locator(".gt-ck").nth(i).bounding_box()
    pg.mouse.move(1500, 1000)
    shot("selected2", ["[data-action=gr-sel-none]"])
    pg.locator("[data-action=gr-sel-none]").click(); pg.wait_for_timeout(2500)
    shot("rejected")
    pg.locator(".gt .et").nth(6).click(); pg.wait_for_timeout(3500); shot("compare")
    picks = api(f"/tasks/{T}/picks?per_species=2")
    req = urllib.request.Request(B + "/api/v1/collections", method="POST", headers={"content-type": "application/json"},
                                 data=json.dumps({"name": "AI 选鸟 · byxbee park · 每种 2 张", "asset_ids": picks["asset_ids"]}).encode())
    col = json.load(urllib.request.urlopen(req))
    pg.goto(f"{B}/#/review/{col['id']}"); pg.wait_for_timeout(4000); shot("picks", [".tile >> nth=0"])
    b.close()
ids = api(f"/tasks/{T}/picks?per_species=3")["asset_ids"][:48]
for i, a in enumerate(ids):
    urllib.request.urlretrieve(f"{B}/api/v1/assets/{a}/large", f"{OUT}/ph/p{i:02d}.jpg")
json.dump({"boxes": boxes, "species": spar["common"], "picks": len(picks["asset_ids"]), "pick_species": picks["species"], "photos": len(ids)},
          open(f"{OUT}/ui/boxes.json", "w"), indent=1)
print(json.dumps(boxes)[:1500])
