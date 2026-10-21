"""The core loop: task create -> discovery -> progressive previews -> analysis -> groups -> summary,
and the run state machine (pause/resume/stop, restart recovery, append, rescan, retry, offline)."""
from __future__ import annotations

import os
import shutil

from core_fixtures import *  # noqa: F403 - fixtures (env, fakes)
from core_fixtures import BASE, create_task, make_jpg, photos, wait_task, wait_until

from bioscan.gardener import store as st

API = "/api/v1"


def assets(c, **q):
    return c.get(f"{API}/assets", params=q | {"limit": 1000}).json()


def test_create_scan_completes_with_previews_results_groups_and_summary(env, tmp_path):
    src = tmp_path / "card1"
    photos(src, 3)
    make_jpg(src / "sub" / "DSC_0001.jpg", when=1_700_000_500)
    (src / "sub" / "DSC_0001.arw").write_bytes(b"raw bytes")         # pairs with the JPEG: one asset
    (src / "notes.txt").write_text("x")                               # unsupported
    (src / "clip.mov").write_bytes(b"\0" * 100)
    with env.client() as c:
        t = create_task(c, [str(src)])
        assert t["scan"]["status"] in ("queued", "running") and t["sources"][0]["path"] == str(src.resolve())
        t = wait_task(c, t["id"])
        assert t["scan"]["status"] == "completed", c.get(f"{API}/runs/{t['scan']['run_id']}").json()
        assert t["scan"]["progress"] == 1.0
        assert t["counts"] == {"assets": 5, "photos": 4, "videos": 1, "files": 6, "bytes": t["counts"]["bytes"]}
        run = c.get(f"{API}/runs/{t['scan']['run_id']}").json()
        s = run["summary"]
        assert (s["discovered"], s["files"], s["by_media"], s["previewed"], s["unsupported"]) == \
            (5, 6, {"photo": 4, "video": 1}, 5, 1)
        assert s["analyzed"]["species"] == 5 and s["failed"] == [] and s["not_run"] == [] and s["unavailable"] == {}
        assert run["config"]["versions"]["species"] == "species@fake1"
        items = assets(c, task=t["id"], order="discovered")["items"]
        assert len(items) == 5 and all(i["preview"] == "ready" and i["analysis"] == "done" for i in items)
        assert items[0]["content_label_zh"] == "鸟" and items[0]["species"]["taxon"] == "Megascops asio"
        ai = [i["ai"]["verdict"] for i in items if i["media"] == "photo"]
        assert ai.count("pick") == 1 and next(i for i in items if i["media"] == "video")["ai"] is None and items[1]["burst"]["id"].startswith(t["id"] + ":")
        pair = next(i for i in items if i["name"] == "DSC_0001.jpg")
        d = c.get(f"{API}/assets/{pair['id']}").json()
        assert sorted(f["role"] for f in d["files"]) == ["jpeg", "raw"]
        assert d["caps"]["species"]["state"] == "done" and not any(k.startswith("raw:") for k in d["caps"])
        assert d["caps"]["place_event"]["state"] == "done" and d["meta"]["width"] == 64
        assert {x["id"] for x in d["tags"]} >= {"scene:wildlife", "scene:bird", "species:Megascops asio"}
        cols = c.get(f"{API}/collections", params={"task": t["id"]}).json()
        kinds = sorted(x["group_kind"] for x in cols)
        assert kinds == ["all", "event", "scene", "species"] and all(x["size"] for x in cols)
        assert cols[0]["group_kind"] == "all" and cols[0]["size"] == 5
        r = c.get(f"{API}/assets/{pair['id']}/thumb")
        assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
        h = c.get(f"{API}/health").json()
        assert h["status"] == "ok" and h["schema_version"] == 1 and h["worker"]["state"] == "idle"
        assert not any(p.name.endswith(".jpg") and "thumb" in p.name for p in src.rglob("*"))   # nothing written in src
        assert sorted(os.listdir(src)) == ["IMG_0000.jpg", "IMG_0001.jpg", "IMG_0002.jpg", "clip.mov", "notes.txt", "sub"]


def test_previews_are_visible_before_analysis_finishes(env, fakes, tmp_path):
    src = tmp_path / "card"
    photos(src, 4)
    fakes.gate.clear()                       # analysis waits; previews must not
    with env.client() as c:
        t = create_task(c, [str(src)])
        items = wait_until(lambda: [i for i in assets(c, task=t["id"])["items"] if i["preview"] == "ready"] or None,
                           what="first preview")
        assert items and c.get(f"{API}/tasks/{t['id']}").json()["scan"]["status"] == "running"
        wait_until(lambda: c.get(f"{API}/tasks/{t['id']}").json()["scan"]["phase"] == "analyze", what="analyze phase")
        cols = c.get(f"{API}/collections", params={"task": t["id"]}).json()
        assert {"all", "event"} <= {x["group_kind"] for x in cols}          # event groups before analysis ends
        first = items[0]["updated_at"]
        assert assets(c, task=t["id"], changed_after=first)["total"] <= 4
        fakes.gate.set()
        assert wait_task(c, t["id"])["scan"]["status"] == "completed"
        assert assets(c, task=t["id"], changed_after=first)["total"] == 4   # analysis bumped every asset


def test_pause_releases_the_slot_resume_continues_and_stop_keeps_results(env, fakes, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    photos(a, 4)
    photos(b, 2)
    fakes.gate.clear()
    with env.client() as c:
        ta = create_task(c, [str(a)])
        tb = create_task(c, [str(b)])
        wait_until(lambda: c.get(f"{API}/tasks/{ta['id']}").json()["scan"]["phase"] == "analyze", what="analyze")
        c.post(f"{API}/tasks/{ta['id']}/pause")
        fakes.gate.set()
        assert wait_task(c, ta["id"], ("paused",))["scan"]["status"] == "paused"
        assert wait_task(c, tb["id"])["scan"]["status"] == "completed"     # B ran while A was paused
        done_before = len([p for p, _ in fakes.analyzed if p.startswith(str(a.resolve()))])
        assert done_before < 4
        c.post(f"{API}/tasks/{ta['id']}/resume")
        assert wait_task(c, ta["id"])["scan"]["status"] == "completed"
        paths = [p for p, _ in fakes.analyzed if p.startswith(str(a.resolve()))]
        assert len(paths) == len(set(paths)) == 4                           # nothing analysed twice
        assert len(c.get(f"{API}/tasks/{ta['id']}").json()["runs"]) == 1    # resume is the same run

        s = tmp_path / "s"
        photos(s, 4)
        fakes.gate.clear()
        ts = create_task(c, [str(s)])
        wait_until(lambda: c.get(f"{API}/tasks/{ts['id']}").json()["scan"]["phase"] == "analyze", what="analyze")
        c.post(f"{API}/tasks/{ts['id']}/stop")
        fakes.gate.set()
        t = wait_task(c, ts["id"], ("stopped",))
        run = c.get(f"{API}/runs/{t['scan']['run_id']}").json()
        assert run["finished_at"] and run["summary"]["previewed"] == 4 and run["summary"]["not_run"]
        assert assets(c, task=ts["id"])["total"] == 4                       # results and records kept


def test_queued_run_is_cancelled_by_stop(env, fakes, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    photos(a, 2)
    photos(b, 1)
    fakes.gate.clear()
    with env.client() as c:
        ta = create_task(c, [str(a)])
        tb = create_task(c, [str(b)])
        c.post(f"{API}/tasks/{tb['id']}/stop")
        assert c.get(f"{API}/tasks/{tb['id']}").json()["scan"]["status"] == "cancelled"
        fakes.gate.set()
        wait_task(c, ta["id"])


def test_restart_recovery_continues_an_interrupted_run(env, fakes, tmp_path):
    src = tmp_path / "card"
    photos(src, 5)
    fakes.gate.clear()
    with env.client() as c:
        t = create_task(c, [str(src)])
        wait_until(lambda: c.get(f"{API}/tasks/{t['id']}").json()["scan"]["phase"] == "analyze", what="analyze")
        c.post(f"{API}/marks", json={"asset_ids": [assets(c, task=t["id"])["items"][0]["id"]], "flag": "pick"})
    # graceful shutdown left the run interrupted; now simulate a crash on top: status still 'running'
    s = st.Store(env.data)
    with s.tx() as cx:
        cx.execute("UPDATE runs SET status = 'running' WHERE task_id = ?", (t["id"],))
    s.close()
    fakes.gate.set()
    with env.client() as c:
        t2 = wait_task(c, t["id"])
        assert t2["scan"]["status"] == "completed" and len(t2["runs"]) == 1
        assert t2["runs"][0]["counts"]["interruptions"] == 1
        assert assets(c, task=t["id"], flag="pick")["total"] == 1           # human marks survive a restart
        paths = [p for p, _ in fakes.analyzed]
        assert len(paths) == len(set(paths)) == 5


def test_append_reuses_unchanged_assets_and_adds_new_ones(env, fakes, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    photos(a, 3)
    photos(b, 2, start=1_700_100_000)
    with env.client() as c:
        t = create_task(c, [str(a)])
        wait_task(c, t["id"])
        n = len(fakes.analyzed)
        r = c.post(f"{API}/tasks/{t['id']}/sources", json={"paths": [str(b)]})
        assert r.status_code == 201 and r.json()["kind"] == "append"
        t = wait_task(c, t["id"])
        assert t["counts"]["assets"] == 5 and len(fakes.analyzed) == n + 2
        # appending the first source again: everything reused, nothing re-analysed
        run = c.post(f"{API}/tasks/{t['id']}/sources", json={"paths": [str(a)]}).json()
        wait_until(lambda: c.get(f"{API}/runs/{run['id']}").json()["status"] == "completed", what="re-append")
        assert c.get(f"{API}/runs/{run['id']}").json()["summary"]["reused"] == 3 and len(fakes.analyzed) == n + 2


def test_rescan_subset_keeps_history_and_new_version(env, fakes, tmp_path):
    src = tmp_path / "card"
    photos(src, 3)
    with env.client() as c:
        t = create_task(c, [str(src)])
        wait_task(c, t["id"])
        one = assets(c, task=t["id"])["items"][0]["id"]
        fakes.version = "fake2"
        run = c.post(f"{API}/tasks/{t['id']}/rescan", json={"capabilities": ["species"], "asset_ids": [one]}).json()
        assert run["kind"] == "rescan"
        wait_until(lambda: c.get(f"{API}/runs/{run['id']}").json()["status"] == "completed", what="rescan")
        d = c.get(f"{API}/assets/{one}").json()
        assert d["caps"]["species"]["version"] == "species@fake2"
        assert d["caps"]["quality"]["version"] == "quality@fake1"           # not in the rescan: untouched
        other = assets(c, task=t["id"])["items"][1]["id"]
        assert c.get(f"{API}/assets/{other}").json()["caps"]["species"]["version"] == "species@fake1"
        s = st.Store(env.data)
        versions = [r["version"] for r in s.all("SELECT version FROM results WHERE asset_id = ? AND capability = "
                                                "'species' ORDER BY id", (one,))]
        assert versions == ["species@fake1", "species@fake2"]               # history kept


def test_failed_items_make_the_run_partial_and_retry_reruns_only_them(env, fakes, tmp_path):
    src = tmp_path / "card"
    photos(src, 2)
    make_jpg(src / "fail_me.jpg", when=1_700_000_900)
    with env.client() as c:
        t = create_task(c, [str(src)], caps=["content"])
        t = wait_task(c, t["id"])
        assert t["scan"]["status"] == "partial"
        run = c.get(f"{API}/runs/{t['scan']['run_id']}").json()
        assert {f["capability"] for f in run["summary"]["failed"]} == {"content"}
        assert assets(c, task=t["id"], analysis="failed")["total"] == 1
        os.rename(src / "fail_me.jpg", src / "fixed.jpg")
        shutil.copy(src / "IMG_0000.jpg", src / "fail_me.jpg")
        n = len(fakes.analyzed)
        r = c.post(f"{API}/tasks/{t['id']}/retry", json={}).json()
        assert r["kind"] == "retry" and len(r["scope"]["asset_ids"]) == 1
        wait_until(lambda: c.get(f"{API}/runs/{r['id']}").json()["status"] in ("completed", "partial"), what="retry")
        assert len(fakes.analyzed) == n + 1


def test_offline_source_keeps_records_partial_and_verify_sources_requeues(env, fakes, tmp_path):
    a = tmp_path / "card"
    photos(a, 3)
    with env.client() as c:
        t = create_task(c, [str(a)])
        wait_task(c, t["id"])
        hidden = tmp_path / "card-away"
        a.rename(hidden)                                        # the card is unplugged
        run = c.post(f"{API}/tasks/{t['id']}/rescan", json={}).json()
        r = wait_until(lambda: (x := c.get(f"{API}/runs/{run['id']}").json())["status"] in ("completed", "partial")
                       and x, what="rescan")
        assert c.post(f"{API}/tasks/{t['id']}/verify-sources").json()["queued_run_id"] is None
        t2 = c.get(f"{API}/tasks/{t['id']}").json()
        assert t2["sources"][0]["online"] is False
        assert r["status"] == "partial" and r["summary"]["offline_sources"] == [str(a.resolve())]
        items = assets(c, task=t["id"])["items"]
        assert len(items) == 3 and not any(i["online"] for i in items)
        assert c.get(f"{API}/assets/{items[0]['id']}/thumb").status_code == 200   # cached preview still served
        assert c.get(f"{API}/assets/{items[0]['id']}/original").status_code == 404
        hidden.rename(a)                                        # plugged back in
        v = c.post(f"{API}/tasks/{t['id']}/verify-sources").json()
        assert v["queued_run_id"] and v["sources"][0]["online"] is True
        wait_until(lambda: c.get(f"{API}/runs/{v['queued_run_id']}").json()["status"] == "completed", what="append")
        assert all(i["online"] for i in assets(c, task=t["id"])["items"])


def test_missing_file_is_offline_not_deleted(env, tmp_path):
    a = tmp_path / "card"
    paths = photos(a, 3)
    with env.client() as c:
        t = create_task(c, [str(a)])
        wait_task(c, t["id"])
        os.remove(paths[1])
        run = c.post(f"{API}/tasks/{t['id']}/sources", json={"paths": [str(a)]}).json()
        r = wait_until(lambda: (x := c.get(f"{API}/runs/{run['id']}").json())["status"] in ("completed", "partial")
                       and x, what="append")
        assert r["summary"]["missing"] == 1 and r["summary"]["offline_sources"] == []
        items = assets(c, task=t["id"])["items"]
        assert len(items) == 3 and sum(not i["online"] for i in items) == 1


def test_unavailable_models_record_unavailable_and_no_scores(tmp_path, fakes):
    from core_fixtures import Env

    from bioscan.service.engine import Engine, Loaders

    def broken(device):
        raise RuntimeError("weights missing")

    env = Env(tmp_path, fakes, engine=Engine("cpu", Loaders(siglip2=broken)))
    src = tmp_path / "card"
    photos(src, 2)
    with env.client() as c:
        t = wait_task(c, create_task(c, [str(src)], preset="photo")["id"])
        assert t["scan"]["status"] == "partial"
        run = c.get(f"{API}/runs/{t['scan']['run_id']}").json()
        assert "weights missing" in run["summary"]["unavailable"]["aesthetic"]
        for i in assets(c, task=t["id"])["items"]:
            assert i["ai"] is None and i["analysis"] == "unavailable" or i["analysis"] == "partial"
            d = c.get(f"{API}/assets/{i['id']}").json()
            assert d["caps"]["aesthetic"]["state"] == "unavailable" and d["caps"]["aesthetic"]["data"] is None
            assert d["caps"]["meta"]["state"] == "done" and i["preview"] == "ready"
        caps = {x["id"]: x for x in c.get(f"{API}/capabilities").json()}
        assert caps["aesthetic"]["state"] == "unavailable" and "weights missing" in caps["aesthetic"]["reason"]
        assert caps["meta"]["state"] == "available"


def test_task_validation_and_local_only(env, tmp_path):
    src = tmp_path / "card"
    photos(src, 1)
    with env.client() as c:
        assert c.post(f"{API}/tasks", json={"title": "x", "sources": ["relative"]}).json()["code"] == "bad_request"
        assert c.post(f"{API}/tasks", json={"title": "x", "sources": [str(tmp_path / "nope")]}).status_code == 400
        assert c.post(f"{API}/tasks", json={"title": "", "sources": [str(src)]}).status_code == 400
        assert c.post(f"{API}/tasks", json={"title": "x", "sources": [str(tmp_path)]}).status_code == 400  # holds data
        assert c.post(f"{API}/tasks", json={"title": "x", "sources": [str(src)],
                                            "capabilities": ["nope"]}).status_code == 400
        assert c.get(f"{API}/tasks/t_missing").json()["code"] == "not_found"
        assert c.get(f"{API}/health", headers={"host": "evil.example"}).status_code == 403
        r = c.post(f"{API}/tasks", json={"title": "x", "sources": [str(src)]}, headers={"origin": "http://evil.example"})
        assert r.status_code == 403
        assert c.get(f"{API}/health", headers={"origin": "http://evil.example"}).status_code == 200
    with env.client(allow_roots=[str(tmp_path / "elsewhere")]) as c:
        assert c.post(f"{API}/tasks", json={"title": "x", "sources": [str(src)]}).status_code == 400
    assert BASE.startswith("http://127.0.0.1")
