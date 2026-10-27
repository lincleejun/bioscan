"""契约修订 7B, Lightroom side: retrying only the photos that did not reach Lightroom, and develop presets with an
export step whose 修图进度 comes only from the plugin's per-photo results. Lightroom itself is not run here."""
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


def shoot(c, tmp_path, n=4):
    src = tmp_path / "card"
    for i in range(n):
        make_jpg(src / f"IMG_{i}.jpg", color=(30 * i, 80, 10), when=1_700_000_000 + 60 * i)
    t = wait_task(c, create_task(c, [str(src)])["id"])
    items = sorted(c.get(f"{API}/assets", params={"task": t["id"]}).json()["items"], key=lambda i: i["name"])
    return src, [i["id"] for i in items]


def doc_of(h):
    return json.loads(Path(h["doc_path"]).read_text())


def result(h, photos):
    Path(h["result_path"]).write_text(json.dumps({"schema": 2, "id": h["id"], "photos": photos}))


def ok(aid, **extra):
    return {"asset_id": aid, "status": "ok", "photo_id": 7, "uuid": "U-" + aid, "path": "/x",
            "stars": {"action": "same", "value": 0}, "keywords": {"action": "same", "value": []}} | extra


def test_retry_sends_only_failed_and_offline_photos_and_reuses_the_lightroom_identity(env, tmp_path):
    with env.client(lightroom_dir=tmp_path / "lr") as c:
        src, a = shoot(c, tmp_path)
        c.post(f"{API}/marks", json={"asset_ids": a, "use": "edit"})
        first = c.post(f"{API}/handoffs", json={"asset_ids": [a[1]]}).json()
        result(first, [ok(a[1])])                                        # IMG_1 is in the catalog now
        assert c.get(f"{API}/handoffs/{first['id']}").json()["status"] == "applied"

        (src / "IMG_3.jpg").rename(tmp_path / "IMG_3.jpg")                # the disk went away
        h = c.post(f"{API}/handoffs", json={"asset_ids": a, "name": "东滩"}).json()
        assert c.post(f"{API}/handoffs/{h['id']}/retry").status_code == 409   # no result yet: nothing to retry
        result(h, [ok(a[0]), {"asset_id": a[1], "status": "failed", "reason": "no write access"},
                   {"asset_id": a[2], "status": "skipped", "reason": "file_missing"}])
        got = c.get(f"{API}/handoffs/{h['id']}").json()
        assert got["status"] == "partial" and got["retryable"] == 3

        (tmp_path / "IMG_3.jpg").rename(src / "IMG_3.jpg")                # back online
        c.post(f"{API}/marks", json={"asset_ids": [a[2]], "use": "reject"})   # rejected since: not sent again
        r = c.post(f"{API}/handoffs/{h['id']}/retry")
        assert r.status_code == 201, r.text
        again = r.json()
        assert again["name"] == "东滩" and again["scope"]["retry_of"] == h["id"]
        assert [i["asset_id"] for i in again["items"]] == [a[1], a[3]]
        photos = {p["asset_id"]: p for p in doc_of(again)["photos"]}
        assert photos[a[1]]["uuid"] == "U-" + a[1]                         # found again, never imported twice
        assert photos[a[3]]["uuid"] is None

        result(again, [ok(a[1]), ok(a[3])])
        assert c.post(f"{API}/handoffs/{again['id']}/retry").status_code == 400    # nothing left to retry


# ---- develop preset and export step ----
# (wanted, base uuid, base fingerprint, current fingerprint) -> action
DEVELOP = [((None, None, None, "f1"), "none"), (("P", None, None, "f1"), "apply"), (("P", "P", "f1", "f1"), "same"),
           (("Q", "P", "f1", "f1"), "apply"),               # another preset, not edited since: apply it
           (("P", "P", "f1", "f2"), "conflict"),            # edited in Lightroom after Gardener applied: never overwritten
           (("Q", "P", "f1", "f2"), "conflict")]
# (wanted, status, develop action) -> decision
EXPORT = [((False, "ok", None), "none"), ((True, "ok", None), "export"), ((True, "ok", "applied"), "export"),
          ((True, "ok", "conflict"), "export"), ((True, "ok", "failed"), "skip"), ((True, "failed", None), "skip"),
          ((True, "skipped", "applied"), "skip")]


@pytest.mark.parametrize("args,want", DEVELOP)
def test_develop_rules(args, want):
    assert handoff.decide_develop(*args) == want


@pytest.mark.parametrize("args,want", EXPORT)
def test_export_rules(args, want):
    assert handoff.decide_export(*args) == want


@pytest.mark.skipif(not shutil.which("lua"), reason="lua not installed")
def test_decide_lua_applies_the_same_develop_and_export_rules(tmp_path):
    """Decide.lua gives the same answers as handoff.py, and its develop fingerprint ignores key order but not values."""
    cases = [{"f": "develop", "a": list(a), "want": w} for a, w in DEVELOP] + \
            [{"f": "export", "a": list(a), "want": w} for a, w in EXPORT]
    (tmp_path / "cases.json").write_text(json.dumps(cases))
    driver = tmp_path / "driver.lua"
    driver.write_text(f"""package.path = {json.dumps(str(PLUGIN) + "/?.lua;")} .. package.path
local json, D = require "dkjson", require "Decide"
local f = io.open(arg[1]); local cases = json.decode(f:read("*a")); f:close()
local out = {{}}
for i, c in ipairs(cases) do
  local a = c.a
  if c.f == "develop" then out[i] = D.develop(a[1], a[2], a[3], a[4]) else out[i] = D.export(a[1], a[2], a[3]) end
end
local x = {{Exposure2012 = 0.5, ToneCurve = {{0, 0, 255, 255}}, WhiteBalance = "As Shot"}}
local y = {{WhiteBalance = "As Shot", ToneCurve = {{0, 0, 255, 255}}, Exposure2012 = 0.5}}
local z = {{WhiteBalance = "As Shot", ToneCurve = {{0, 0, 255, 255}}, Exposure2012 = 0.6}}
out[#out + 1] = D.fingerprint(x) == D.fingerprint(y) and D.fingerprint(x) ~= D.fingerprint(z)
print(json.encode(out))
""")
    got = json.loads(subprocess.run(["lua", str(driver), str(tmp_path / "cases.json")], check=True,
                                    capture_output=True, text=True).stdout)
    assert got == [c["want"] for c in cases] + [True]


def test_preset_and_export_are_previewed_remembered_and_progress_comes_only_from_real_results(env, tmp_path):
    lr = tmp_path / "lr"
    lr.mkdir()
    (lr / "presets.json").write_text(json.dumps({"schema": 1, "at": "2026-10-27T08:00:00Z",
                                                 "develop": [{"uuid": "P1", "name": "Bird warm", "folder": "User"}],
                                                 "export": [{"name": "JPEG 2048", "path": "/x/JPEG 2048.lrtemplate"}]}))
    owl = "avibase:test-asio"
    develop = {"uuid": "P1", "name": "Bird warm"}
    export = {"preset": "JPEG 2048", "preset_path": "/x/JPEG 2048.lrtemplate", "dest_dir": str(tmp_path / "out"),
              "overwrite": "rename"}
    with env.client(lightroom_dir=lr) as c:
        src, a = shoot(c, tmp_path, n=3)
        c.post(f"{API}/marks", json={"asset_ids": a[:2], "use": "edit"})
        c.post(f"{API}/marks", json={"asset_ids": [a[1]], "stars": 5})
        assert c.get(f"{API}/lightroom/presets").json()["develop"][0]["name"] == "Bird warm"
        pv = c.post(f"{API}/handoffs/preview", json={"concept": owl}).json()
        assert (pv["count"], pv["offline"], pv["representatives"], pv["remembered"]) == (2, 0, [a[1], a[0]], None)
        assert pv["presets"]["available"] and pv["name"] == "Eastern Screech-Owl"

        for bad in ({"export": export | {"overwrite": "ask"}}, {"export": export | {"dest_dir": str(src / "x")}},
                    {"export": {k: v for k, v in export.items() if k != "preset_path"}}, {"develop": {"name": "x"}}):
            assert c.post(f"{API}/handoffs", json={"concept": owl} | bad).status_code == 400, bad
        h = c.post(f"{API}/handoffs", json={"concept": owl, "develop": develop, "export": export}).json()
        doc = doc_of(h)
        assert doc["develop"] == develop and doc["export"] == export | {"dest_dir": str((tmp_path / "out").resolve())}
        assert c.post(f"{API}/handoffs/preview", json={"concept": owl}).json()["remembered"]["develop"] == develop
        assert c.get(f"{API}/assets/{a[0]}").json()["lightroom"] is None      # sent is not done

        dev = {"action": "applied", "preset": "P1", "preset_name": "Bird warm", "fingerprint": "fp-0"}
        result(h, [ok(a[0], develop=dev, export={"action": "exported", "path": str(tmp_path / "out/IMG_0.jpg")}),
                   ok(a[1], develop=dev | {"fingerprint": "fp-1"},
                      export={"action": "failed", "reason": "disk full"})])
        got = c.get(f"{API}/handoffs/{h['id']}").json()
        assert got["counts"]["preset_applied"] == 2 and got["counts"]["exported"] == 1
        p0 = c.get(f"{API}/assets/{a[0]}").json()["lightroom"]["progress"]
        p1 = c.get(f"{API}/assets/{a[1]}").json()["lightroom"]["progress"]
        assert p0["state"] == "exported" and p0["exported_path"].endswith("IMG_0.jpg") and p0["preset"] == "Bird warm"
        assert p1["state"] == "preset_applied" and "exported_path" not in p1        # a failed export is not exported

        again = c.post(f"{API}/handoffs", json={"asset_ids": a[:2], "develop": develop}).json()
        base = {p["asset_id"]: p["baseline"]["develop"] for p in doc_of(again)["photos"]}
        assert base == {a[0]: {"uuid": "P1", "fingerprint": "fp-0"}, a[1]: {"uuid": "P1", "fingerprint": "fp-1"}}
        result(again, [ok(a[0], develop=dev | {"action": "conflict", "reason": "edited_in_lightroom"}), ok(a[1])])
        got = c.get(f"{API}/handoffs/{again['id']}").json()
        assert got["items"][0]["conflicts"] == ["develop"] and got["retryable"] == 0
        assert c.get(f"{API}/assets/{a[0]}").json()["lightroom"]["progress"]["state"] == "exported"   # never goes back
        nxt = c.post(f"{API}/handoffs", json={"asset_ids": [a[0]]}).json()
        assert doc_of(nxt)["photos"][0]["baseline"]["develop"]["fingerprint"] == "fp-0"   # a conflict keeps the baseline


def test_a_retry_keeps_the_preset_and_export(env, tmp_path):
    export = {"preset_path": "/x/A.lrtemplate", "dest_dir": str(tmp_path / "out"), "overwrite": "skip"}
    with env.client(lightroom_dir=tmp_path / "lr") as c:
        _src, a = shoot(c, tmp_path, n=1)
        h = c.post(f"{API}/handoffs", json={"asset_ids": a, "export": export, "remember": False}).json()
        result(h, [{"asset_id": a[0], "status": "failed", "reason": "no write access"}])
        again = c.post(f"{API}/handoffs/{h['id']}/retry").json()
        assert doc_of(again)["export"]["overwrite"] == "skip" and again["options"] == h["options"]
