"""Nothing a run does may stop the queue: an Analyzer whose close() or analyze() raises ends that run
honestly and the next runs still execute; progress never reads 1.0 before a run ends; a deleted
default preset never leaves task creation broken."""
from __future__ import annotations

import os
import threading
from pathlib import Path

from core_fixtures import *  # noqa: F403 - fixtures (env, fakes)
from core_fixtures import BASE, FakeAnalyzer, create_task, make_jpg, ok_engine, photos, wait_task, wait_until
from fastapi.testclient import TestClient

from bioscan.gardener import tasks
from bioscan.gardener.api import create_gardener_app

API = "/api/v1"
CAPS = ["content"]


class CloseRaises(FakeAnalyzer):
    def close(self):
        raise RuntimeError("close failed")


class AnalyzeRaises(FakeAnalyzer):
    async def analyze(self, items, caps, should_stop):
        first = True
        async for r in super().analyze(items, caps, should_stop):
            yield r
            if first:
                first = False
                raise RuntimeError("analyzer exploded")


def run_of(c, t):
    return c.get(f"{API}/runs/{t['scan']['run_id']}").json()


def test_analyzer_close_error_ends_runs_honestly_and_the_queue_keeps_going(env, fakes, tmp_path):
    fakes.analyzer = lambda engine, **kw: CloseRaises(fakes, engine)
    srcs = [tmp_path / n for n in "abc"]
    for i, s in enumerate(srcs):
        photos(s, 1, start=1_700_000_000 + 1000 * i)
    with env.client() as c:
        ts = [create_task(c, [str(s)], caps=CAPS) for s in srcs]
        done = [wait_task(c, t["id"]) for t in ts]
        for t in done:
            run = run_of(c, t)
            assert t["scan"]["status"] == "partial" and run["summary"]["cleanup_error"] == "RuntimeError: close failed"
            assert run["summary"]["analyzed"]["content"] == 1 and run["finished_at"]
        assert wait_until(lambda: c.get(f"{API}/health").json()["worker"]["state"] == "idle", what="idle")
        d = tmp_path / "d"
        photos(d, 1, start=1_700_900_000)
        assert wait_task(c, create_task(c, [str(d)], caps=CAPS)["id"])["scan"]["status"] == "partial"


def test_analyze_raising_mid_run_fails_that_run_only(env, fakes, tmp_path):
    fakes.analyzer = lambda engine, **kw: AnalyzeRaises(fakes, engine)
    a, b = tmp_path / "a", tmp_path / "b"
    photos(a, 2)
    photos(b, 1, start=1_700_100_000)
    with env.client() as c:
        ta = create_task(c, [str(a)], caps=CAPS)
        tb = create_task(c, [str(b)], caps=CAPS)
        fa, fb = wait_task(c, ta["id"]), wait_task(c, tb["id"])
        assert fa["scan"]["status"] == "failed" and "analyzer exploded" in run_of(c, fa)["error"]
        assert run_of(c, fa)["summary"]["analyzed"]["content"] == 1         # what finished is kept
        assert fb["scan"]["status"] == "failed"                                # same analyzer, still ran
        assert c.get(f"{API}/health").json()["worker"]["state"] == "idle"


def test_progress_stays_below_one_until_the_run_ends(fakes, tmp_path):
    src = tmp_path / "card"
    photos(src, 3)
    gate = threading.Event()
    tk = fakes.toolkit()
    make = tk.make_preview

    def slow_preview(*a):
        gate.wait(10)
        return make(*a)

    tk.make_preview = slow_preview
    app = create_gardener_app(tmp_path / "data", engine=ok_engine(), toolkit=tk)
    with TestClient(app, base_url=BASE) as c:
        t = create_task(c, [str(src)], caps=["content", "quality"])
        scan = wait_until(lambda: (s := c.get(f"{API}/tasks/{t['id']}").json()["scan"])["phase"] == "preview" and s,
                          what="preview phase")
        for _ in range(5):
            scan = c.get(f"{API}/tasks/{t['id']}").json()["scan"]
            assert scan["progress"] < 1.0 and scan["analyzed"] <= scan["total_analysis"]
        assert scan["total_analysis"] == 6                                     # 3 photos x 2 model capabilities
        gate.set()
        seen = []
        while (s := c.get(f"{API}/tasks/{t['id']}").json()["scan"])["status"] == "running":
            seen.append(s)
            assert s["progress"] < 1.0 and s["analyzed"] <= s["total_analysis"]
        assert wait_task(c, t["id"])["scan"]["progress"] == 1.0


def test_deleting_the_default_preset_falls_back_to_the_builtin(env, tmp_path):
    src = tmp_path / "card"
    photos(src, 1)
    with env.client() as c:
        mine = c.post(f"{API}/presets", json={"name": "我的", "capabilities": ["content"]}).json()
        assert c.put(f"{API}/settings", json={"default_preset": mine["id"]}).json()["default_preset"] == mine["id"]
        r = c.delete(f"{API}/presets/{mine['id']}").json()
        assert r == {"ok": True, "default_preset": "photo"}
        assert c.get(f"{API}/settings").json()["default_preset"] == "photo"
        t = c.post(f"{API}/tasks", json={"title": "x", "sources": [str(src)]})
        assert t.status_code == 201 and t.json()["preset"] == "photo"
        # a setting that points at a missing preset (an older database) still creates tasks
        store = c.app.state.store
        with store.tx() as cx:
            store.put_setting(cx, "default_preset", "p_gone")
        t = c.post(f"{API}/tasks", json={"title": "y", "sources": [str(src)]})
        assert t.status_code == 201 and t.json()["preset"] == "photo"


def test_missing_found_before_a_pause_is_still_reported_after_resume(env, fakes, tmp_path):
    src = tmp_path / "card"
    gone = photos(src, 3)[0]
    with env.client() as c:
        t = wait_task(c, create_task(c, [str(src)], caps=CAPS)["id"])
        import os

        os.remove(gone)
        photos(src / "more", 2, start=1_700_500_000)
        fakes.gate.clear()
        run = c.post(f"{API}/tasks/{t['id']}/sources", json={"paths": [str(src)]}).json()
        wait_until(lambda: c.get(f"{API}/runs/{run['id']}").json()["phase"] == "analyze", what="analyze")
        c.post(f"{API}/tasks/{t['id']}/pause")
        fakes.gate.set()
        wait_until(lambda: c.get(f"{API}/runs/{run['id']}").json()["status"] == "paused", what="paused")
        c.post(f"{API}/tasks/{t['id']}/resume")
        r = wait_until(lambda: (x := c.get(f"{API}/runs/{run['id']}").json())["status"] in
                       ("completed", "partial", "failed") and x, what="end")
        assert (r["status"], r["summary"]["missing"]) == ("partial", 1)


def test_resume_right_after_pause_keeps_the_run_going(env, fakes, tmp_path):
    src = tmp_path / "card"
    photos(src, 3)
    fakes.gate.clear()
    with env.client() as c:
        t = create_task(c, [str(src)], caps=CAPS)
        wait_until(lambda: c.get(f"{API}/tasks/{t['id']}").json()["scan"]["phase"] == "analyze", what="analyze")
        assert c.post(f"{API}/tasks/{t['id']}/pause").status_code == 200
        assert c.post(f"{API}/tasks/{t['id']}/resume").status_code == 200     # before the pause landed
        fakes.gate.set()
        t = wait_task(c, t["id"])
        assert t["scan"]["status"] == "completed" and len(t["runs"]) == 1
        paths = [p for p, _ in fakes.analyzed]
        assert len(paths) == len(set(paths)) == 3                             # nothing analysed twice


def test_resume_landing_while_a_pausing_run_finishes_is_not_lost(env, fakes, tmp_path, monkeypatch):
    src = tmp_path / "card"
    photos(src, 3)
    real, fired = tasks.RunExec.summary, []

    def summary_then_resume(self):              # the resume lands between finish reading and writing the status
        out = real(self)
        if self.w.flags.get(self.id) == "pause" and not fired:
            fired.append(self.id)
            tasks.resume(self.store, self.w, self.task_id)
        return out
    monkeypatch.setattr(tasks.RunExec, "summary", summary_then_resume)
    fakes.gate.clear()
    with env.client() as c:
        t = create_task(c, [str(src)], caps=CAPS)
        wait_until(lambda: c.get(f"{API}/tasks/{t['id']}").json()["scan"]["phase"] == "analyze", what="analyze")
        c.post(f"{API}/tasks/{t['id']}/pause")
        fakes.gate.set()
        t = wait_task(c, t["id"], ("completed", "partial", "failed", "stopped", "cancelled", "paused"))
        assert fired and t["scan"]["status"] == "completed" and len(t["runs"]) == 1



def test_a_changed_file_deleted_before_it_is_recorded_reads_missing(env, fakes, tmp_path):
    src = tmp_path / "card"
    gone = photos(src, 2)[0]
    tk = fakes.toolkit()
    listed = tk.discover
    with TestClient(create_gardener_app(env.data, engine=ok_engine(), toolkit=tk), base_url=BASE) as c:
        t = wait_task(c, create_task(c, [str(src)], caps=CAPS)["id"])
        make_jpg(Path(gone), color=(1, 2, 3), size=(80, 60), when=1_700_000_500)   # changed: its hash is read

        def then_deleted(root):
            out = listed(root)
            os.remove(gone)
            return out
        tk.discover = then_deleted
        run = c.post(f"{API}/tasks/{t['id']}/sources", json={"paths": [str(src)]}).json()
        r = wait_until(lambda: (x := c.get(f"{API}/runs/{run['id']}").json())["status"] in
                       ("completed", "partial", "failed") and x, what="end")
        assert (r["status"], r["summary"]["missing"], r["error"]) == ("partial", 1, None)
        items = c.get(f"{API}/assets", params={"task": t["id"]}).json()["items"]
        assert sorted(i["online"] for i in items) == [False, True]


def test_append_source_row_is_committed_with_the_run(env, tmp_path, monkeypatch):
    a, b = tmp_path / "a", tmp_path / "b"
    photos(a, 1)
    photos(b, 2, start=1_700_100_000)
    with env.client() as c:
        t = wait_task(c, create_task(c, [str(a)], caps=CAPS)["id"])
        w = c.app.state.worker
        real = w.wake

        def wake_then_stall():                 # the worker runs before the API thread continues
            real()
            wait_until(lambda: c.app.state.store.active["run_id"] is None and
                       c.app.state.store.one("SELECT 1 FROM runs WHERE status = 'queued'") is None, 5, "worker")
        monkeypatch.setattr(w, "wake", wake_then_stall)
        run = c.post(f"{API}/tasks/{t['id']}/sources", json={"paths": [str(b)]}).json()
        r = wait_until(lambda: (x := c.get(f"{API}/runs/{run['id']}").json())["status"] in
                       ("completed", "partial", "failed") and x, what="end")
        assert r["status"] == "completed" and r["summary"]["discovered"] == 2, r["error"]
        assert {s["path"] for s in c.get(f"{API}/tasks/{t['id']}").json()["sources"]} == {str(a.resolve()),
                                                                                         str(b.resolve())}


def test_retry_after_stop_picks_up_never_analysed_items(env, fakes, tmp_path):
    src = tmp_path / "card"
    photos(src, 3)
    fakes.gate.clear()
    with env.client() as c:
        t = create_task(c, [str(src)], caps=CAPS)
        wait_until(lambda: c.get(f"{API}/tasks/{t['id']}").json()["scan"]["phase"] == "analyze", what="analyze")
        c.post(f"{API}/tasks/{t['id']}/stop")
        wait_task(c, t["id"], ("stopped",))
        fakes.gate.set()
        r = c.post(f"{API}/tasks/{t['id']}/retry", json={}).json()
        assert len(r["scope"]["asset_ids"]) == 3
        t = wait_task(c, t["id"])
        items = c.get(f"{API}/assets", params={"task": t["id"]}).json()["items"]
        assert t["scan"]["status"] == "completed" and all(i["analysis"] == "done" for i in items)
