"""The human layer and organisation: marks (global, versioned, undoable), collections (manual, smart,
split, merge, complete/zero/reopen, delete/restore, regrouping rules), tags, cache, export, files."""
from __future__ import annotations

import os
import time

from core_fixtures import *  # noqa: F403 - fixtures (env, fakes)
from core_fixtures import create_task, make_jpg, photos, wait_task, wait_until

from bioscan.gardener import store as st

API = "/api/v1"


def ids(c, **q):
    return [i["id"] for i in c.get(f"{API}/assets", params=q | {"limit": 1000}).json()["items"]]


def scanned(c, src, **kw):
    t = create_task(c, [str(src)], **kw)
    return wait_task(c, t["id"])


def manual(c, name, asset_ids, task_id=None):
    r = c.post(f"{API}/collections", json={"name": name, "kind": "manual", "asset_ids": asset_ids, "task_id": task_id})
    assert r.status_code == 201, r.text
    return r.json()


def finish(c, run_id):
    return wait_until(lambda: (x := c.get(f"{API}/runs/{run_id}").json())["status"] in
                      ("completed", "partial", "failed", "stopped") and x, what=f"run {run_id}")


def test_marks_are_global_across_collections_and_completion_is_independent(env, tmp_path):
    src = tmp_path / "card"
    photos(src, 3)
    with env.client() as c:
        t = scanned(c, src)
        a, b, x = ids(c, task=t["id"])
        cols = [manual(c, f"集合{i}", [a, b]) for i in range(3)]
        r = c.post(f"{API}/marks", json={"asset_ids": [a], "flag": "pick", "stars": 4}, headers={"X-Gardener-Actor": "ui"})
        assert r.status_code == 200 and r.json()["assets"] == [{"id": a, "use": "record", "flag": "pick", "stars": 4, "version": 1}]
        for col in cols:
            got = c.get(f"{API}/collections/{col['id']}").json()
            assert (got["picks"], got["judged"], got["review"]["unhandled"]) == (1, 1, 1)
        # omitted key = unchanged; null = clear (§10 X5)
        assert c.post(f"{API}/marks", json={"asset_ids": [a], "stars": 2}).json()["assets"][0]["flag"] == "pick"
        r = c.post(f"{API}/marks", json={"asset_ids": [a], "flag": None})
        assert r.json()["assets"][0] == {"id": a, "use": "pending", "flag": None, "stars": 2, "version": 3}
        # stale expect -> 409 with the current versions
        r = c.post(f"{API}/marks", json={"asset_ids": [a], "flag": "reject", "expect": {a: 1}})
        assert r.status_code == 409 and r.json()["conflicts"] == [{"id": a, "version": 3}]
        assert c.post(f"{API}/marks", json={"asset_ids": [a], "flag": "maybe"}).status_code == 400
        assert c.post(f"{API}/marks", json={"asset_ids": [a]}).status_code == 400
        # completing one collection leaves the others open; marks stay shared
        done = c.post(f"{API}/collections/{cols[0]['id']}/complete", json={"note": "看完"}).json()
        assert done["review"]["state"] == "completed" and done["review"]["unhandled"] == 1
        assert c.get(f"{API}/collections/{cols[1]['id']}").json()["review"]["state"] == "open"
        r = c.post(f"{API}/marks", json={"asset_ids": [a, b], "flag": "pick"})
        batch = r.json()["batch_id"]
        assert all(c.get(f"{API}/collections/{col['id']}").json()["picks"] == 2 for col in cols)
        assert c.post(f"{API}/collections/{cols[0]['id']}/reopen").json()["review"]["state"] == "open"
        # undo restores the batch; a later edit blocks undoing an older batch
        assert c.post(f"{API}/marks/undo", json={"batch_id": batch}).json()["undone"] == 2
        assert c.get(f"{API}/assets/{b}").json()["flag"] is None
        r1 = c.post(f"{API}/marks", json={"asset_ids": [x], "stars": 1}).json()["batch_id"]
        c.post(f"{API}/marks", json={"asset_ids": [x], "stars": 5})
        assert c.post(f"{API}/marks/undo", json={"batch_id": r1}).status_code == 409
        hist = c.get(f"{API}/marks/history", params={"asset_id": a}).json()
        assert hist[0]["id"] > hist[-1]["id"] and {h["actor"] for h in hist} >= {"ui", "api"}
        d = c.get(f"{API}/assets/{a}").json()
        assert d["mark_history"][0]["batch_id"] and {x["id"] for x in d["collections"]} >= {col["id"] for col in cols}
        assert c.get(f"{API}/tasks/{t['id']}").json()["review"]["judged"] == 2


def test_zero_with_a_pick_is_a_conflict_and_clear_picks_is_undoable(env, tmp_path):
    src = tmp_path / "card"
    photos(src, 3)
    with env.client() as c:
        t = scanned(c, src)
        a, b, _ = ids(c, task=t["id"])
        one, other = manual(c, "坏天气", [a, b]), manual(c, "作品集", [a])
        c.post(f"{API}/marks", json={"asset_ids": [a], "flag": "pick"})
        r = c.post(f"{API}/collections/{one['id']}/zero", json={})
        assert r.status_code == 409 and r.json()["code"] == "conflict"
        picks = r.json()["picks"]
        assert picks[0]["id"] == a and other["id"] in {x["id"] for x in picks[0]["collections"]}
        assert one["id"] not in {x["id"] for x in picks[0]["collections"]}
        kept = c.post(f"{API}/collections/{one['id']}/zero", json={"resolution": "keep_picks"}).json()
        assert kept["review"]["state"] == "zeroed" and kept["batch_id"] is None
        assert c.get(f"{API}/assets/{a}").json()["flag"] == "pick"
        c.post(f"{API}/collections/{one['id']}/reopen")
        z = c.post(f"{API}/collections/{one['id']}/zero", json={"resolution": "clear_picks"}).json()
        assert z["review"]["state"] == "zeroed" and z["batch_id"]
        d = c.get(f"{API}/assets/{a}").json()
        assert d["flag"] is None and d["mark_history"][-1]["reason"] == f"zero:{one['id']}"   # null, not reject
        assert c.post(f"{API}/marks/undo", json={"batch_id": z["batch_id"]}).json()["undone"] == 1
        assert c.get(f"{API}/assets/{a}").json()["flag"] == "pick"
        cov = {x["collection_id"]: x for x in c.get(f"{API}/coverage", params={"task": t["id"]}).json()}
        assert all(x["state"] in ("covered", "uncovered", "zeroed") for x in cov.values())


def test_manual_split_survives_regroup_and_rerun(env, fakes, tmp_path):
    src = tmp_path / "card"
    photos(src, 4)
    with env.client() as c:
        t = scanned(c, src)
        group = next(x for x in c.get(f"{API}/collections", params={"task": t["id"]}).json()
                     if x["group_kind"] == "scene")
        members = ids(c, collection=group["id"])
        assert len(members) == 4
        new = c.post(f"{API}/collections/{group['id']}/split", json={"asset_ids": members[:2], "name": "清晨"}).json()
        c.patch(f"{API}/collections/{group['id']}", json={"name": "我的分组", "version": group["version"]})
        fakes.version = "fake2"
        finish(c, c.post(f"{API}/tasks/{t['id']}/rescan", json={}).json()["id"])
        got = c.get(f"{API}/collections/{group['id']}").json()
        assert got["size"] == 2 and got["name"] == "我的分组" and got["user_edited"]
        assert c.get(f"{API}/collections/{new['id']}").json()["size"] == 2
        same = [x for x in c.get(f"{API}/collections", params={"task": t["id"]}).json() if x["group_kind"] == "scene"]
        assert len(same) == 1                           # the suggestion matched the edited one, no duplicate
        r = c.patch(f"{API}/collections/{group['id']}", json={"name": "x", "version": 1})
        assert r.status_code == 409


def test_completed_collection_counts_new_and_changed_members(env, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    photos(a, 2)
    photos(b, 1, start=1_700_000_200)
    with env.client() as c:
        t = scanned(c, a)
        allc = next(x for x in c.get(f"{API}/collections", params={"task": t["id"]}).json() if x["group_kind"] == "all")
        c.post(f"{API}/collections/{allc['id']}/complete", json={})
        finish(c, c.post(f"{API}/tasks/{t['id']}/sources", json={"paths": [str(b)]}).json()["id"])
        got = c.get(f"{API}/collections/{allc['id']}").json()
        assert got["review"]["state"] == "completed" and got["review"]["new_since"] == 1
        assert got["review"]["removed_since"] == 0 and got["size"] == 3
        assert c.get(f"{API}/tasks/{t['id']}").json()["review"]["collections_done"] >= 1


def test_content_change_clears_marks_as_an_undoable_system_batch(env, tmp_path):
    src = tmp_path / "card"
    p = photos(src, 2)[0]
    with env.client() as c:
        t = scanned(c, src)
        aid = next(i for i in ids(c, task=t["id"]) if c.get(f"{API}/assets/{i}").json()["files"][0]["path"] == p)
        c.post(f"{API}/marks", json={"asset_ids": [aid], "flag": "pick", "stars": 3})
        make_jpg(src / "IMG_0000.jpg", size=(80, 60), color=(1, 2, 3), when=time.time())   # same path, new content
        run = finish(c, c.post(f"{API}/tasks/{t['id']}/sources", json={"paths": [str(src)]}).json()["id"])
        assert run["status"] == "completed"
        d = c.get(f"{API}/assets/{aid}").json()
        assert d["id"] == aid and (d["flag"], d["stars"]) == (None, 0) and d["meta"]["content_changed_at"]
        assert d["meta"]["width"] == 80 and d["analysis"] == "done"
        sys = c.get(f"{API}/marks/history", params={"asset_id": aid, "actor": "system"}).json()
        assert sys and {h["reason"] for h in sys} == {"content_changed"}
        assert c.post(f"{API}/marks/undo", json={"batch_id": sys[0]["batch_id"]}).json()["undone"] == 1
        d = c.get(f"{API}/assets/{aid}").json()
        assert (d["flag"], d["stars"]) == ("pick", 3)
        assert len(ids(c, task=t["id"])) == 2


def test_raw_gaining_a_jpeg_keeps_its_asset_and_marks(env, tmp_path):
    src = tmp_path / "card"
    src.mkdir()
    (src / "DSC_1.arw").write_bytes(b"raw one")
    make_jpg(src / "DSC_2.jpg", when=1_700_000_000)
    with env.client() as c:
        t = scanned(c, src)
        before = {c.get(f"{API}/assets/{i}").json()["name"]: i for i in ids(c, task=t["id"])}
        c.post(f"{API}/marks", json={"asset_ids": list(before.values()), "flag": "pick"})
        make_jpg(src / "DSC_1.jpg", when=1_700_000_100)              # the RAW gets its JPEG
        (src / "DSC_2.arw").write_bytes(b"raw two")                   # the JPEG gets its RAW
        finish(c, c.post(f"{API}/tasks/{t['id']}/sources", json={"paths": [str(src)]}).json()["id"])
        after = ids(c, task=t["id"])
        assert sorted(after) == sorted(before.values())
        for aid in after:
            d = c.get(f"{API}/assets/{aid}").json()
            assert sorted(f["role"] for f in d["files"]) == ["jpeg", "raw"] and d["flag"] == "pick"
            assert d["name"].endswith(".jpg") and d["preview"] == "ready"
        assert c.get(f"{API}/marks/history", params={"actor": "system"}).json() == []


def test_delete_restore_keeps_marks_and_deleted_auto_collections_stay_deleted(env, fakes, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    photos(a, 2)
    photos(b, 1, start=1_700_000_300)
    with env.client() as c:
        t = scanned(c, a)
        cols = c.get(f"{API}/collections", params={"task": t["id"]}).json()
        scene = next(x for x in cols if x["group_kind"] == "scene")
        first = ids(c, collection=scene["id"])
        c.post(f"{API}/marks", json={"asset_ids": first, "flag": "pick"})
        assert c.delete(f"{API}/collections/{scene['id']}").json()["deleted_at"]
        assert scene["id"] not in {x["id"] for x in c.get(f"{API}/collections", params={"task": t["id"]}).json()}
        assert scene["id"] in {x["id"] for x in c.get(f"{API}/collections", params={"deleted": "1"}).json()}
        assert ids(c, task=t["id"], flag="pick") == ids(c, task=t["id"])          # marks unchanged
        run = finish(c, c.post(f"{API}/tasks/{t['id']}/sources", json={"paths": [str(b)]}).json()["id"])
        assert run["status"] == "completed" and run["summary"]["groups_skipped_deleted"] >= 1
        live = [x for x in c.get(f"{API}/collections", params={"task": t["id"]}).json() if x["group_kind"] == "scene"]
        assert live == []                                                          # not revived, no duplicate
        kids = [x for x in c.get(f"{API}/collections", params={"task": t["id"]}).json() if x["parent_id"] == scene["id"]]
        assert all(x["size"] == 2 for x in kids)                                   # no new children under a deleted parent
        restored = c.post(f"{API}/collections/{scene['id']}/restore").json()
        assert restored["deleted_at"] is None and restored["size"] == 2 and restored["picks"] == 2


def test_smart_collections_merge_save_as_manual_and_members(env, tmp_path):
    src = tmp_path / "card"
    photos(src, 3)
    with env.client() as c:
        t = scanned(c, src)
        a, b, x = ids(c, task=t["id"])
        c.post(f"{API}/marks", json={"asset_ids": [a, b], "flag": "pick"})
        smart = c.post(f"{API}/collections", json={"name": "已选", "kind": "smart",
                                                   "rule": {"task": t["id"], "flag": "pick"}}).json()
        assert smart["size"] == 2
        # 契约修订 8 (PRD §04): members = (rule hits ∪ pins) − exclusions; reset hands a photo back to the rule
        r = c.post(f"{API}/collections/{smart['id']}/members", json={"add": [x]}).json()
        assert (r["size"], r["rule_hits"], r["exceptions"]) == (3, 2, {"added": [x], "excluded": []})
        r = c.post(f"{API}/collections/{smart['id']}/members", json={"remove": [a]}).json()
        assert (r["size"], r["exceptions"]["excluded"]) == (2, [a]) and a not in ids(c, collection=smart["id"])
        r = c.post(f"{API}/collections/{smart['id']}/members", json={"reset": [x, a]}).json()
        assert (r["size"], r["exceptions"]) == (2, {"added": [], "excluded": []})
        assert c.post(f"{API}/collections/{smart['id']}/members", json={"reset": [a], "add": ["nope"]}).status_code == 404
        assert c.post(f"{API}/collections", json={"name": "y", "kind": "smart", "rule": {"bogus": 1}}).status_code == 400
        c.post(f"{API}/marks", json={"asset_ids": [x], "flag": "pick"})
        assert c.get(f"{API}/collections/{smart['id']}").json()["size"] == 3         # rules are live
        saved = c.post(f"{API}/collections/{smart['id']}/save-as-manual", json={"name": "快照"}).json()
        assert saved["kind"] == "manual" and saved["size"] == 3
        m1, m2 = manual(c, "m1", [a]), manual(c, "m2", [b])
        assert c.post(f"{API}/collections/{m1['id']}/members", json={"reset": [a]}).status_code == 400
        r = c.post(f"{API}/collections/{m1['id']}/members", json={"add": [x], "remove": [a]}).json()
        assert (r["added"], r["removed"], r["size"]) == (1, 1, 1)
        merged = c.post(f"{API}/collections/{m1['id']}/merge", json={"ids": [m2["id"]], "name": "合并"}).json()
        assert merged["size"] == 2 and c.get(f"{API}/collections/{m1['id']}").json()["deleted_at"]
        assert c.get(f"{API}/collections", params={"kind": "smart"}).json()[0]["id"] == smart["id"]
        assert len(ids(c, collection=smart["id"], q="img_000")) == 3


def test_tags_create_assign_merge_split_and_filter(env, tmp_path):
    src = tmp_path / "card"
    photos(src, 2)
    with env.client() as c:
        t = scanned(c, src)
        a, b = ids(c, task=t["id"])
        tags = {x["id"]: x for x in c.get(f"{API}/tags").json()}
        assert tags["scene:bird"]["parent_id"] == "scene:wildlife" and tags["scene:bird"]["count"] == 2
        assert len(ids(c, content="bird")) == 2 and len(ids(c, species="screech")) == 2
        trip = c.post(f"{API}/tags", json={"name": "旅行"}).json()
        c.post(f"{API}/assets/tags", json={"asset_ids": [a], "tag_id": trip["id"]})
        assert ids(c, tag="旅行") == [a]
        c.patch(f"{API}/tags/{trip['id']}", json={"name": "旅途", "aliases": ["trip"]})
        other = c.post(f"{API}/tags", json={"name": "出行"}).json()
        c.post(f"{API}/assets/tags", json={"asset_ids": [b], "tag_id": other["id"]})
        merged = c.post(f"{API}/tags/{other['id']}/merge", json={"into": trip["id"]}).json()
        assert merged["count"] == 2 and merged["aliases"] == ["trip"]
        kids = c.post(f"{API}/tags/{trip['id']}/split", json={"names": ["山", "海"], "assign": {"山": [a]}}).json()
        assert [k["parent_id"] for k in kids] == [trip["id"]] * 2 and kids[0]["count"] == 1
        d = c.get(f"{API}/assets/{a}").json()
        assert {(x["name"], x["source"]) for x in d["tags"]} >= {("山", "user"), ("鸟", "ai")}


def test_clear_cache_keeps_records_and_rebuilds_previews(env, tmp_path):
    src = tmp_path / "card"
    photos(src, 2)
    with env.client() as c:
        t = scanned(c, src)
        aid = ids(c, task=t["id"])[0]
        c.post(f"{API}/marks", json={"asset_ids": [aid], "stars": 5})
        before = c.get(f"{API}/storage").json()
        assert before["cache_files"] == 4 and before["db_bytes"] > 0
        r = c.post(f"{API}/storage/clear-cache").json()
        assert r["removed_files"] == 4 and r["freed_bytes"] > 0
        assert c.get(f"{API}/storage").json()["cache_files"] == 0
        assert c.get(f"{API}/assets/{aid}").json()["preview"] == "pending" and \
            c.get(f"{API}/assets/{aid}").json()["stars"] == 5
        assert c.get(f"{API}/assets/{aid}/large").status_code == 200        # rebuilt on request
        assert c.get(f"{API}/assets/{aid}").json()["preview"] == "ready"
        assert not any(n.endswith(".thumb.jpg") for n in os.listdir(src))


def test_export_refuses_a_destination_inside_a_source_and_passes_details(env, fakes, tmp_path):
    src = tmp_path / "card"
    photos(src, 2)
    with env.client() as c:
        t = scanned(c, src)
        allc = next(x for x in c.get(f"{API}/collections", params={"task": t["id"]}).json() if x["group_kind"] == "all")
        a = ids(c, task=t["id"])[0]
        c.post(f"{API}/marks", json={"asset_ids": [a], "flag": "pick"})
        r = c.post(f"{API}/export", json={"collection_id": allc["id"], "scope": "picked", "dest_dir": str(src / "out")})
        assert r.status_code == 400 and not (src / "out").exists() and fakes.exports == []
        r = c.post(f"{API}/export", json={"collection_id": allc["id"], "scope": "picked"})
        assert r.status_code == 200 and r.json()["assets"] == 1
        call = fakes.exports[0]
        assert call["title"] == "全部素材" and call["scope"] == {"kind": "collection", "value": allc["id"], "scope": "picked"}
        assert [x["id"] for x in call["assets"]] == [a] and call["assets"][0]["files"]
        assert call["collections"][0]["member_ids"] and src.resolve() in call["protected"]
        assert c.get(f"{API}/exports").json()[0]["report"]["xml_path"].startswith(str(env.data))
        r = c.post(f"{API}/export", json={"asset_ids": ids(c, task=t["id"]), "scope": "all"})
        assert r.json()["assets"] == 2 and fakes.exports[-1]["title"] == "筛选结果"


def test_original_range_reveal_presets_settings(env, fakes, tmp_path):
    src = tmp_path / "card"
    photos(src, 1)
    with env.client() as c:
        t = scanned(c, src, preset="quick")
        aid = ids(c, task=t["id"])[0]
        r = c.get(f"{API}/assets/{aid}/original", headers={"Range": "bytes=0-3"})
        assert r.status_code == 206 and r.headers["content-range"].startswith("bytes 0-3/") and len(r.content) == 4
        assert c.post(f"{API}/assets/{aid}/reveal").json() == {"ok": True}
        assert fakes.revealed == [str((src / "IMG_0000.jpg").resolve())]
        presets = {p["id"]: p for p in c.get(f"{API}/presets").json()}
        assert presets["wildlife"]["builtin"] and "species" in presets["wildlife"]["capabilities"]
        assert c.delete(f"{API}/presets/quick").status_code == 400
        mine = c.post(f"{API}/presets", json={"name": "我的", "capabilities": ["species"]}).json()
        assert mine["capabilities"] == ["meta", "subject", "species"]
        assert c.put(f"{API}/presets/{mine['id']}", json={"name": "改名"}).json()["name"] == "改名"
        assert c.delete(f"{API}/presets/{mine['id']}").json()["ok"] is True
        s = c.put(f"{API}/settings", json={"run_mode": "quiet", "default_preset": "photo"}).json()
        assert s == {"run_mode": "quiet", "default_preset": "photo", "capability_versions": {}}
        assert c.put(f"{API}/settings", json={"run_mode": "turbo"}).status_code == 400
        caps = c.get(f"{API}/capabilities").json()
        assert [x["id"] for x in caps][:2] == ["meta", "content"] and caps[0]["state"] == "available"
        assert c.post(f"{API}/tasks/{t['id']}/archive").json()["archived"] is True
        assert c.get(f"{API}/tasks").json() == [] and len(c.get(f"{API}/tasks", params={"archived": "all"}).json()) == 1
        sd = st.Store(env.data)
        assert sd.one("SELECT value FROM meta WHERE key = 'schema_version'")["value"] == str(st.SCHEMA_VERSION)


def test_capability_check_reports_a_model_that_does_not_load(tmp_path, fakes):
    from core_fixtures import Env

    from bioscan.service.engine import Engine, Loaders

    def broken(device):
        raise RuntimeError("weights missing")

    with Env(tmp_path, fakes, engine=Engine("cpu", Loaders(siglip2=broken))).client() as c:
        caps = {x["id"]: x for x in c.post(f"{API}/capabilities/check", json={"ids": ["content"]}).json()}
        assert caps["content"]["state"] == "unavailable" and "weights missing" in caps["content"]["reason"]
        assert caps["species"]["state"] == "unknown" and caps["place_event"]["state"] == "available"
        h = c.get(f"{API}/health").json()
        assert h["device"] == "cpu" and h["models_loaded"] == []


def test_task_timezone_reaches_grouping_and_collections_show_the_time_basis(env, fakes, tmp_path):
    src = tmp_path / "card"
    photos(src, 2)
    with env.client() as c:
        assert c.post(f"{API}/tasks", json={"title": "x", "sources": [str(src)], "timezone": "Mars/Base"}).status_code == 400
        t = wait_task(c, create_task(c, [str(src)], caps=["place_event"])["id"])
        assert t["timezone"] is None and fakes.tz_seen is None
        ev = next(x for x in c.get(f"{API}/collections", params={"task": t["id"]}).json() if x["group_kind"] == "other")
        assert ev["time_basis"] == "camera_local"
        assert c.patch(f"{API}/tasks/{t['id']}", json={"timezone": "+08:00"}).json()["timezone"] == "+08:00"
        assert c.patch(f"{API}/tasks/{t['id']}", json={"timezone": "+25:00"}).status_code == 400
        run = finish(c, c.post(f"{API}/tasks/{t['id']}/rescan", json={}).json()["id"])
        assert run["config"]["timezone"] == "+08:00" and fakes.tz_seen == "+08:00"
        ev = c.get(f"{API}/collections/{ev['id']}").json()
        assert ev["time_basis"] == "task_timezone"
        t2 = create_task(c, [str(src)], caps=["place_event"], timezone="Asia/Shanghai")
        assert t2["timezone"] == "Asia/Shanghai"
        day = c.get(f"{API}/assets/{ids(c, task=t['id'])[0]}").json()["taken_at"][:10]
        assert len(ids(c, task=t["id"], date_from=day, date_to=day)) >= 1        # camera-local wall clock


def test_tag_merge_and_parent_cycles_are_refused(env):
    with env.client() as c:
        a, b, k = (c.post(f"{API}/tags", json={"name": n}).json() for n in ("A", "B", "K"))
        assert c.post(f"{API}/tags/{a['id']}/merge", json={"into": b["id"]}).status_code == 200
        before = c.get(f"{API}/tags").json()
        assert c.post(f"{API}/tags/{b['id']}/merge", json={"into": a["id"]}).status_code == 400   # back again
        assert c.post(f"{API}/tags/{k['id']}/merge", json={"into": a["id"]}).status_code == 400   # into a merged tag
        assert c.get(f"{API}/tags").json() == before
        c.patch(f"{API}/tags/{k['id']}", json={"parent_id": b["id"]})
        r = c.patch(f"{API}/tags/{b['id']}", json={"parent_id": k["id"]})
        assert r.status_code == 400 and c.get(f"{API}/tags").json() != before   # k's own parent change stands
        child = next(x for x in c.get(f"{API}/tags").json() if x["id"] == k["id"])
        assert child["parent_id"] == b["id"]
        merged = c.post(f"{API}/tags/{b['id']}/merge", json={"into": k["id"]}).json()       # into its own child
        assert merged["parent_id"] is None


def test_cyclic_smart_rules_are_refused_and_stored_ones_do_not_break_reads(env, tmp_path):
    src = tmp_path / "card"
    photos(src, 1)
    with env.client() as c:
        t = scanned(c, src)
        a = ids(c, task=t["id"])[0]
        s1 = c.post(f"{API}/collections", json={"name": "s1", "kind": "smart", "rule": {"flag": "pick"}}).json()
        assert c.patch(f"{API}/collections/{s1['id']}", json={"rule": {"collection": s1["id"]}}).status_code == 400
        assert c.get(f"{API}/collections/{s1['id']}").json()["rule"] == {"flag": "pick"}
        s2 = c.post(f"{API}/collections", json={"name": "s2", "kind": "smart",
                                                "rule": {"collection": s1["id"]}}).json()
        assert c.patch(f"{API}/collections/{s1['id']}", json={"rule": {"collection": s2["id"]}}).status_code == 400
        store = c.app.state.store                       # a cycle already in an older database
        with store.tx() as cx:
            cx.execute("UPDATE collections SET rule = ? WHERE id = ?", (f'{{"collection": "{s2["id"]}"}}', s1["id"]))
        assert c.get(f"{API}/collections").status_code == 200
        assert c.get(f"{API}/collections/{s1['id']}").json()["size"] == 0
        assert c.get(f"{API}/assets/{a}").status_code == 200
        assert c.post(f"{API}/export", json={"asset_ids": [a], "scope": "all"}).status_code == 200


def test_combined_filters_across_tasks(env, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    photos(a, 3)
    photos(b, 2, start=1_710_000_000)
    (b / "clip.mov").write_bytes(b"\0" * 10)
    with env.client() as c:
        ta, tb = scanned(c, a, caps=["content"]), scanned(c, b, caps=["content"])
        ia, ib = ids(c, task=ta["id"], order="discovered"), ids(c, task=tb["id"], media="photo", order="discovered")
        vid = ids(c, task=tb["id"], media="video")[0]
        c.post(f"{API}/marks", json={"asset_ids": [ia[0], ib[0], vid], "flag": "pick"})
        c.post(f"{API}/marks", json={"asset_ids": [ia[0], vid], "stars": 4})

        def q(**f):
            return sorted(ids(c, **f))
        assert q(flag="pick") == sorted([ia[0], ib[0], vid])
        assert q(flag="pick", stars_min=4, media="photo") == [ia[0]]
        assert q(flag="pick", task=tb["id"], media="video") == [vid]
        assert q(judged="no", task=ta["id"]) == sorted(ia[1:])
        assert q(flag="none") == sorted(ia[1:] + ib[1:])
        assert q(q="img_0000") == sorted([ia[0], ib[0]])
        assert q(content="bird", flag="pick") == sorted([ia[0], ib[0]])   # a video gets no image-model labels
        assert q(date_to="2023-11-15", flag="pick") == [ia[0]]
        sm = c.post(f"{API}/collections", json={"name": "跨任务", "kind": "smart",
                                                "rule": {"flag": "pick", "stars_min": 4}}).json()
        assert q(collection=sm["id"]) == sorted([ia[0], vid])


def test_tag_merge_into_a_grandchild_leaves_no_parent_cycle(env):
    with env.client() as c:
        a, p, b = (c.post(f"{API}/tags", json={"name": n}).json() for n in ("A", "P", "B"))
        c.patch(f"{API}/tags/{p['id']}", json={"parent_id": a["id"]})
        c.patch(f"{API}/tags/{b['id']}", json={"parent_id": p["id"]})
        assert c.post(f"{API}/tags/{a['id']}/merge", json={"into": b["id"]}).status_code == 200
        parent = {x["id"]: x["parent_id"] for x in c.get(f"{API}/tags").json()}
        assert parent[b["id"]] is None and parent[p["id"]] == b["id"]


def test_clear_cache_tolerates_a_file_vanishing_meanwhile(env, tmp_path, monkeypatch):
    src = tmp_path / "card"
    photos(src, 2)
    with env.client() as c:
        t = scanned(c, src)
        aid = ids(c, task=t["id"])[0]
        real = os.path.getsize
        gone = []

        def getsize(p):              # a preview's .part renamed away between walk and stat
            if not gone:
                gone.append(p)
                os.remove(p)
            return real(p)
        monkeypatch.setattr(os.path, "getsize", getsize)
        r = c.post(f"{API}/storage/clear-cache")
        monkeypatch.undo()
        assert r.status_code == 200 and r.json()["removed_files"] == 3
        assert c.get(f"{API}/assets/{aid}").json()["preview"] == "pending"
