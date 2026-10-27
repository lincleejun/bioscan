"""Lightroom handoff (契约修订 5E, PRD §12, AC14, AC18): a frozen member snapshot written as one handoff file,
per-photo results read back from the plugin's result file, editor links with a sync baseline, and the field
conflict rules, which Decide.lua must apply exactly as handoff.py does. Lightroom itself is not run here."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from core_fixtures import *  # noqa: F403 - fixtures (env, fakes)
from core_fixtures import create_task, make_jpg, wait_task

from bioscan.gardener import handoff

API = "/api/v1"
PLUGIN = Path(__file__).resolve().parents[2] / "extensions" / "lightroom" / "bioscan.lrplugin"
OWL = ["Aves", "Megascops asio"]                 # no name lists in tests: Class|Scientific

# (current rating, baseline, wanted) -> action
STARS = [((0, None, 3), "write"), ((None, None, 3), "write"), ((4, None, 3), "conflict"), ((3, None, 3), "same"),
         ((4, None, 0), "none"), ((0, None, 0), "none"), ((3, 3, 5), "write"), ((4, 3, 5), "conflict"),
         ((3, 3, 0), "write"), ((5, 3, 5), "same"), ((2, 0, 0), "none")]
A, B, MINE = "Aves|Strigiformes|Strigidae|Megascops asio", "Aves|Passeriformes|Troglodytidae|Nannus pacificus", "trip|2026"
# (current chains, baseline, wanted) -> (action, add, remove)
KEYWORDS = [(([MINE], None, [A]), ("write", [A], [])), (([MINE, A], None, [A]), ("same", [], [])),
            (([MINE, A], [A], [B]), ("write", [B], [A])),          # a species correction swaps only Gardener's keyword
            (([MINE], [A], [B]), ("conflict", [], [])),            # the person removed Gardener's keyword in Lightroom
            (([A, B], [A, B], [B]), ("write", [], [A])), (([], [], []), ("same", [], []))]


@pytest.mark.parametrize("args,want", STARS)
def test_star_conflict_rules(args, want):
    assert handoff.decide_stars(*args) == want


@pytest.mark.parametrize("args,want", KEYWORDS)
def test_keyword_conflict_rules_never_touch_other_keywords(args, want):
    assert handoff.decide_keywords(*args) == want


@pytest.mark.skipif(not shutil.which("lua"), reason="lua not installed")
def test_decide_lua_applies_the_same_rules(tmp_path):
    """Decide.lua (what the plugin runs) gives the same answers as handoff.py on every case above."""
    cases = [{"f": "stars", "a": list(a), "want": w} for a, w in STARS] + \
            [{"f": "keywords", "a": list(a), "want": list(w)} for a, w in KEYWORDS]
    (tmp_path / "cases.json").write_text(json.dumps(cases))
    driver = tmp_path / "driver.lua"
    driver.write_text(f"""package.path = {json.dumps(str(PLUGIN) + "/?.lua;")} .. package.path
local json, D = require "dkjson", require "Decide"
local f = io.open(arg[1]); local cases = json.decode(f:read("*a")); f:close()
local out = {{}}
for i, c in ipairs(cases) do
  local a = c.a
  if c.f == "stars" then out[i] = D.stars(a[1], a[2], a[3])
  else local x, add, rm = D.keywords(a[1], a[2], a[3]); out[i] = {{x, add, rm}} end
end
print(json.encode(out))
""")
    got = json.loads(subprocess.run(["lua", str(driver), str(tmp_path / "cases.json")], check=True,
                                    capture_output=True, text=True).stdout)
    assert got == [c["want"] for c in cases]


def test_the_plugin_decodes_json_into_plain_tables(tmp_path):
    """Found in Lightroom Classic 14.2: dkjson's default decode tags every array with a metatable, and the SDK
    (catalog:createKeyword's synonyms) rejects it with "Assertion failed: packed". Every decode in the plugin
    passes explicit nil metatables, and that gives plain tables."""
    import re

    for lua in PLUGIN.glob("*.lua"):
        if lua.name == "dkjson.lua":
            continue
        for call in re.findall(r"json\.decode\(([^\n]*)", lua.read_text()):
            assert call.rstrip().endswith("1, nil, nil, nil)") or "1, nil, nil, nil)" in call, (lua.name, call)
    driver = tmp_path / "plain.lua"
    driver.write_text(f"""package.path = {json.dumps(str(PLUGIN) + "/?.lua;")} .. package.path
local json = require "dkjson"
local doc = json.decode('{{"photos": [{{"keywords": [{{"synonyms": ["Great Blue Heron"]}}]}}]}}', 1, nil, nil, nil)
local syn = doc.photos[1].keywords[1].synonyms
print(getmetatable(doc) == nil and getmetatable(doc.photos) == nil and getmetatable(syn) == nil and syn[1])
""")
    out = subprocess.run(["lua", str(driver)], check=True, capture_output=True, text=True).stdout.strip()
    assert out == "Great Blue Heron"


def test_plugin_parses():
    luac = shutil.which("luac")
    if not luac:
        pytest.skip("luac not installed")
    subprocess.run([luac, "-p", *map(str, sorted(PLUGIN.glob("*.lua")))], check=True)


def shoot(env, tmp_path, c, n=3):
    src = tmp_path / "card"
    for i in range(n):
        make_jpg(src / f"IMG_{i}.jpg", when=1_700_000_000 + 60 * i)
    t = wait_task(c, create_task(c, [str(src)])["id"])
    items = sorted(c.get(f"{API}/assets", params={"task": t["id"]}).json()["items"], key=lambda i: i["name"])
    return src, [i["id"] for i in items]


def plugin_result(h, photos):
    """What Handoff.lua writes: one entry per photo of the handoff file."""
    Path(h["result_path"]).write_text(json.dumps({"schema": 2, "id": h["id"], "photos": photos}))


def ok(p, stars="write", keywords="write", uuid=None):
    return {"asset_id": p["asset_id"], "status": "ok", "path": p["paths"][0], "photo_id": 41, "uuid": uuid or "U-" + p["asset_id"],
            "stars": {"action": stars, "value": p["stars"]},
            "keywords": {"action": keywords, "value": ["|".join(k["chain"]) for k in p["keywords"]]}}


def test_a_handoff_freezes_edit_picks_writes_the_file_and_is_done_only_from_real_results(env, tmp_path):
    lr = tmp_path / "lr"
    with env.client(lightroom_dir=lr) as c:
        src, aids = shoot(env, tmp_path, c)
        c.post(f"{API}/marks", json={"asset_ids": aids[:2], "use": "edit", "stars": 4})
        c.post(f"{API}/marks", json={"asset_ids": [aids[2]], "flag": "reject"})
        sid = c.get(f"{API}/assets/{aids[0]}").json()["subjects"][0]["id"]
        c.post(f"{API}/identifications", json={"subject_ids": [sid], "claim": {"accept": True}})
        all_col = next(x for x in c.get(f"{API}/collections").json() if x["group_kind"] == "all")
        r = c.post(f"{API}/handoffs", json={"collection_id": all_col["id"], "name": "猫头鹰"})
        assert r.status_code == 201, r.text
        h = r.json()
        assert h["status"] == "written" and h["counts"]["pending"] == 2       # 选入修图 only; the reject stays out
        doc = json.loads(Path(h["doc_path"]).read_text())
        assert Path(h["doc_path"]).parent == lr / "handoffs" and doc["schema"] == 2 and doc["name"] == "猫头鹰"
        first, second = sorted(doc["photos"], key=lambda p: p["paths"][0])
        assert first["paths"] == [str(src / "IMG_0.jpg")] and first["stars"] == 4 and first["use"] == "edit"
        assert first["keywords"] == [{"chain": OWL, "synonyms": ["Eastern Screech-Owl"], "concept": "avibase:test-asio"}]
        assert second["keywords"] == []                                        # an AI-only species is no keyword
        assert first["baseline"] == {"stars": None, "keywords": None} and first["uuid"] is None
        assert c.get(f"{API}/handoffs/{h['id']}").json()["status"] == "written"   # writing the file is not done

        plugin_result(h, [ok(first), {"asset_id": second["asset_id"], "status": "failed", "reason": "import failed: x"}])
        got = c.get(f"{API}/handoffs/{h['id']}").json()
        assert got["status"] == "partial" and got["counts"] == {"ok": 1, "skipped": 0, "failed": 1, "pending": 0,
                                                                   "conflicts": 0, "preset_applied": 0, "exported": 0}
        assert [i["reason"] for i in got["items"] if i["status"] == "failed"] == ["import failed: x"]

        again = c.post(f"{API}/handoffs", json={"collection_id": all_col["id"], "name": "猫头鹰"}).json()
        doc2 = {p["asset_id"]: p for p in json.loads(Path(again["doc_path"]).read_text())["photos"]}
        assert doc2[first["asset_id"]]["uuid"] == "U-" + first["asset_id"]    # the link: found, never imported twice
        assert doc2[first["asset_id"]]["baseline"] == {"stars": 4, "keywords": ["|".join(OWL)]}
        assert doc2[second["asset_id"]]["baseline"] == {"stars": None, "keywords": None}
        assert [h["id"] for h in c.get(f"{API}/handoffs").json()] == [again["id"], h["id"]]
        plugin_result(again, [ok(doc2[first["asset_id"]], stars="same", keywords="same")])   # second never came back
        got = c.get(f"{API}/handoffs/{again['id']}").json()
        assert got["status"] == "partial" and [(i["status"], i["reason"]) for i in got["items"]] == \
            [("ok", None), ("failed", "插件未返回结果")]

        sp = c.post(f"{API}/handoffs", json={"concept": "avibase:test-asio"}).json()     # from the species page
        assert sp["name"] == "Eastern Screech-Owl" and {i["asset_id"] for i in sp["items"]} == set(aids[:2])
        Path(sp["result_path"]).write_text("{not json")
        bad = c.get(f"{API}/handoffs/{sp['id']}").json()
        assert bad["status"] == "failed" and bad["items"][0]["reason"].startswith("结果文件无法读取")


def test_a_conflicting_field_keeps_its_baseline_and_is_reported(env, tmp_path):
    with env.client(lightroom_dir=tmp_path / "lr") as c:
        _src, aids = shoot(env, tmp_path, c, n=1)
        c.post(f"{API}/marks", json={"asset_ids": aids, "use": "edit", "stars": 2})
        h = c.post(f"{API}/handoffs", json={"asset_ids": aids}).json()
        p = json.loads(Path(h["doc_path"]).read_text())["photos"][0]
        plugin_result(h, [ok(p)])
        assert c.get(f"{API}/handoffs/{h['id']}").json()["status"] == "applied"
        c.post(f"{API}/marks", json={"asset_ids": aids, "stars": 5})
        h2 = c.post(f"{API}/handoffs", json={"asset_ids": aids}).json()
        p2 = json.loads(Path(h2["doc_path"]).read_text())["photos"][0]
        assert p2["baseline"]["stars"] == 2 and p2["stars"] == 5
        r = ok(p2, stars="conflict")
        r["stars"]["value"] = 3                                                 # the person set 3 in Lightroom
        plugin_result(h2, [r])
        got = c.get(f"{API}/handoffs/{h2['id']}").json()
        assert got["counts"]["conflicts"] == 1 and got["items"][0]["conflicts"] == ["stars"]
        h3 = c.post(f"{API}/handoffs", json={"asset_ids": aids}).json()
        assert json.loads(Path(h3["doc_path"]).read_text())["photos"][0]["baseline"]["stars"] == 2


def test_offline_photos_are_skipped_not_sent_and_rejects_never_go(env, tmp_path):
    with env.client(lightroom_dir=tmp_path / "lr") as c:
        src, aids = shoot(env, tmp_path, c)
        c.post(f"{API}/marks", json={"asset_ids": [aids[2]], "flag": "reject"})
        (src / "IMG_1.jpg").unlink()
        h = c.post(f"{API}/handoffs", json={"asset_ids": aids}).json()
        assert [(i["asset_id"], i["status"], i["reason"]) for i in h["items"]] == \
            [(aids[0], "pending", None), (aids[1], "skipped", "offline")]
        assert [p["asset_id"] for p in json.loads(Path(h["doc_path"]).read_text())["photos"]] == [aids[0]]
        r = c.post(f"{API}/handoffs", json={"asset_ids": [aids[2]]})
        assert r.status_code == 400                                           # nothing to hand off
        assert c.post(f"{API}/handoffs", json={}).status_code == 400
        assert c.get(f"{API}/handoffs/h_nope").status_code == 404


def test_species_keywords_use_the_lists_hierarchy(monkeypatch):
    monkeypatch.setattr(handoff, "_ranks", lambda d: {"avibase:x": ["Aves", "Strigiformes", "Strigidae"]})
    assert handoff.keyword("avibase:x", "Megascops asio", "bird", Path("/lists")) == \
        ["Aves", "Strigiformes", "Strigidae", "Megascops asio"]
    assert handoff.keyword("mdd:9", "Vulpes vulpes", None, None) == ["Mammalia", "Vulpes vulpes"]
    assert handoff.keyword("name:Foo bar", "Foo bar", None, None) == ["Animalia", "Foo bar"]
