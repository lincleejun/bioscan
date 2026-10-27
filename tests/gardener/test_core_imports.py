"""Import (契约修订 5D, PRD §06 正式导入, AC10, AC11): plans by use, sidecar groups, batch suffixes, exact
duplicates, verified copies registered as managed files, partial failure, idempotence and crash resume."""
from __future__ import annotations

import os
import threading
from datetime import datetime

import pytest
from core_fixtures import *  # noqa: F403 - fixtures (env, fakes)
from core_fixtures import create_task, make_jpg, wait_task, wait_until

from bioscan.gardener import imports

API = "/api/v1"
WHEN = datetime(2026, 10, 20, 9, 0).timestamp()
DAY = "2026-10-20"


def card(root, when=WHEN):
    """A (RAW + JPEG + two sidecars), B, C, D (JPEG only)."""
    root.mkdir(parents=True, exist_ok=True)
    for name, data in (("A.ARW", b"raw-a" * 100), ("A.xmp", b"<xmp/>"), ("A.ARW.acr", b"acr")):
        (root / name).write_bytes(data)
        os.utime(root / name, (when, when))
    for i, n in enumerate("ABCD"):
        make_jpg(root / f"{n}.JPG", color=(40 * i, 100, 30), when=when)
    return root


def snapshot(d):
    return {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in sorted(d.iterdir())}


def by_name(c, tid):
    return {i["name"]: i["id"] for i in c.get(f"{API}/assets", params={"task": tid, "limit": 100}).json()["items"]}


def scanned(c, src):
    t = create_task(c, [str(src)], caps=[])
    wait_task(c, t["id"])
    ids = by_name(c, t["id"])
    c.post(f"{API}/marks", json={"asset_ids": [ids["A.JPG"], ids["B.JPG"]], "flag": "pick"})
    c.post(f"{API}/marks", json={"asset_ids": [ids["C.JPG"]], "flag": "reject"})
    return t["id"], ids


def plan(c, tid, dest, **kw):
    r = c.post(f"{API}/tasks/{tid}/import-plan", json={"dest_root": str(dest), **kw})
    assert r.status_code == 201, r.text
    return r.json()


def run(c, pid):
    r = c.post(f"{API}/imports/{pid}/run")
    assert r.status_code == 200, r.text
    return wait_until(lambda: (p := c.get(f"{API}/imports/{pid}").json())["status"] in
                      ("completed", "partial", "failed") and p, what=f"import {pid}")


@pytest.fixture
def copies(monkeypatch):
    """Every source the runner copies, in order."""
    seen = []
    real = imports._copy

    def counted(src, dst):
        seen.append(os.path.basename(src))
        return real(src, dst)
    monkeypatch.setattr(imports, "_copy", counted)
    return seen


def test_plan_by_use_with_sidecars_folder_and_pending_switch(env, tmp_path):
    src = card(tmp_path / "card")
    with env.client() as c:
        tid, ids = scanned(c, src)
        p = plan(c, tid, tmp_path / "lib", place="崇明东滩")
        assert p["status"] == "planned" and p["folder"] == f"{DAY}_崇明东滩"
        assert p["uses"] == {"record": 2, "edit": 0, "pending": 1, "reject": 1, "total": 4}
        assert p["not_copied"] == {"reject": 1, "pending": 0, "versions": 0}
        # A is RAW + JPEG + xmp + acr; B and pending D one JPEG each; reject C not copied
        assert (p["assets"], p["files"], p["sidecars"]) == (3, 6, 2)
        assert p["needed_bytes"] == p["copy_bytes"] == p["bytes"] and p["enough_space"]
        assert not p["stale"]
        off = plan(c, tid, tmp_path / "lib", include_pending=False)
        assert off["folder"] == f"{DAY}_未标注地点" and off["assets"] == 2
        assert off["not_copied"]["pending"] == 1                       # still only on the card
        c.post(f"{API}/marks", json={"asset_ids": [ids["D.JPG"]], "flag": "reject"})
        assert c.get(f"{API}/imports/{off['id']}").json()["stale"]
        assert c.post(f"{API}/tasks/{tid}/import-plan", json={"dest_root": str(src / "x")}).status_code == 400
        assert c.post(f"{API}/tasks/{tid}/import-plan", json={"dest_root": "rel"}).status_code == 400


def test_run_verifies_registers_and_rerun_never_copies_again(env, tmp_path, copies):
    src = card(tmp_path / "card")
    before = snapshot(src)
    with env.client() as c:
        tid, ids = scanned(c, src)
        p = run(c, plan(c, tid, tmp_path / "lib", place="东滩")["id"])
        assert p["status"] == "completed" and p["failed"] == []
        assert p["progress"] == {"files_total": 6, "files_done": 6, "files_failed": 0, "bytes_total": p["bytes"],
                                      "bytes_done": p["bytes"], "assets_total": 3, "assets_done": 3, "assets_failed": 0}
        folder = tmp_path / "lib" / f"{DAY}_东滩"
        assert sorted(x.name for x in folder.iterdir()) == ["A.ARW", "A.ARW.acr", "A.JPG", "A.xmp", "B.JPG", "D.JPG"]
        assert (folder / "A.ARW").read_bytes() == (src / "A.ARW").read_bytes()
        detail = c.get(f"{API}/assets/{ids['A.JPG']}").json()
        assert detail["id"] == ids["A.JPG"] and {m["path"] for m in detail["managed"]} == \
            {str(folder / n) for n in ("A.ARW", "A.ARW.acr", "A.JPG", "A.xmp")}
        assert {f["path"] for f in detail["files"]} == {str(src / "A.ARW"), str(src / "A.JPG")}   # identity stays
        assert len(copies) == 6
        assert c.post(f"{API}/imports/{p['id']}/run").json()["status"] == "completed"        # same plan: no-op
        again = run(c, plan(c, tid, tmp_path / "lib", place="东滩")["id"])                   # new plan, same batch
        assert again["status"] == "completed" and again["duplicates"]["reuse"] == 6 and again["copy_files"] == 0
        assert len(copies) == 6
        assert c.app.state.store.one("SELECT count(*) n FROM managed_files")["n"] == 6
    assert snapshot(src) == before


def test_name_conflicts_get_a_batch_suffix_on_the_whole_group(env, tmp_path):
    src = card(tmp_path / "card")
    folder = tmp_path / "lib" / f"{DAY}_未标注地点"
    folder.mkdir(parents=True)
    (folder / "a.arw").write_bytes(b"another camera")                   # case-insensitive clash, other content
    (folder / "B.JPG").write_bytes((src / "B.JPG").read_bytes())        # exactly the same file already there
    with env.client() as c:
        tid, _ = scanned(c, src)
        p = plan(c, tid, tmp_path / "lib")
        seq = p["seq"]
        assert sorted(os.path.basename(x["to"]) for x in p["renamed"]["items"]) == \
            sorted(f"A_B{seq}{e}" for e in (".ARW", ".ARW.acr", ".JPG", ".xmp"))
        assert p["duplicates"]["identical"] == 1 and p["duplicates"]["items"][0]["path"] == str(src / "B.JPG")
        assert run(c, p["id"])["status"] == "completed"
    assert (folder / "a.arw").read_bytes() == b"another camera"
    assert (folder / f"A_B{seq}.ARW").read_bytes() == (src / "A.ARW").read_bytes()


def test_same_name_from_two_card_folders_gets_the_batch_suffix(env, tmp_path):
    src = tmp_path / "card"
    make_jpg(src / "100MSDCF" / "DSC00001.JPG", color=(10, 100, 30), when=WHEN)
    make_jpg(src / "101MSDCF" / "DSC00001.JPG", color=(200, 100, 30), when=WHEN + 600)   # counter rollover
    with env.client() as c:
        t = create_task(c, [str(src)], caps=[])
        wait_task(c, t["id"])
        p = run(c, plan(c, t["id"], tmp_path / "lib")["id"])
        assert p["status"] == "completed"
    folder = tmp_path / "lib" / f"{DAY}_未标注地点"
    assert sorted(x.name for x in folder.iterdir()) == ["DSC00001.JPG", f"DSC00001_B{p['seq']}.JPG"]
    assert (folder / f"DSC00001_B{p['seq']}.JPG").read_bytes() == (src / "101MSDCF" / "DSC00001.JPG").read_bytes()


def test_exact_duplicates_from_another_card_path_are_reused_not_copied(env, tmp_path, copies):
    first, second = card(tmp_path / "card"), tmp_path / "again"
    with env.client() as c:
        tid, _ = scanned(c, first)
        assert run(c, plan(c, tid, tmp_path / "lib")["id"])["status"] == "completed"
        n = len(copies)
        import shutil
        shutil.copytree(first, second)                                  # the same card mounted elsewhere
        tid2, _ = scanned(c, second)
        p = plan(c, tid2, tmp_path / "lib")
        assert p["duplicates"]["duplicate"] == 4                        # RAW + JPEGs by fingerprint
        assert p["duplicates"]["identical"] == 2                        # sidecars: same name, same bytes in place
        dup = next(x for x in p["duplicates"]["items"] if x["path"].endswith("A.ARW"))
        assert dup["existing"] == str(tmp_path / "lib" / f"{DAY}_未标注地点" / "A.ARW")
        assert run(c, p["id"])["status"] == "completed"
        assert copies[n:] == [] and c.app.state.store.one("SELECT count(*) n FROM managed_files")["n"] == 12


def test_partial_failure_is_reported_and_only_failures_are_retried(env, tmp_path, monkeypatch):
    src = card(tmp_path / "card")
    real, calls = imports._copy, []

    def flaky(s, d):
        calls.append(os.path.basename(s))
        if os.path.basename(s) == "B.JPG" and calls.count("B.JPG") == 1:
            raise OSError("模拟写入失败")
        return real(s, d)
    monkeypatch.setattr(imports, "_copy", flaky)
    with env.client() as c:
        tid, ids = scanned(c, src)
        p = run(c, plan(c, tid, tmp_path / "lib")["id"])
        assert p["status"] == "partial"
        assert p["failed"] == [{"asset_id": ids["B.JPG"], "path": str(src / "B.JPG"), "error": "OSError: 模拟写入失败"}]
        assert (p["progress"]["assets_done"], p["progress"]["assets_failed"]) == (2, 1)
        p = run(c, p["id"])
        assert p["status"] == "completed" and calls.count("B.JPG") == 2 and len(calls) == 7


class Crash(BaseException):
    """The process dying mid-copy: not caught by the runner."""


def test_crash_mid_import_resumes_without_copying_verified_files_again(env, tmp_path, monkeypatch):
    src = card(tmp_path / "card")
    real, calls = imports._copy, []

    def dying(s, d):
        calls.append(os.path.basename(s))
        if len(calls) == 3:
            raise Crash()
        return real(s, d)
    monkeypatch.setattr(imports, "_copy", dying)
    monkeypatch.setattr(threading, "excepthook", lambda args: None)
    with env.client() as c:
        tid, _ = scanned(c, src)
        pid = plan(c, tid, tmp_path / "lib")["id"]
        c.post(f"{API}/imports/{pid}/run")
        wait_until(lambda: len(calls) == 3 and not c.app.state.importer.thread.is_alive(), what="crash")
        p = c.get(f"{API}/imports/{pid}").json()
        assert p["status"] == "running" and p["progress"]["files_done"] == 2
    folder = tmp_path / "lib" / f"{DAY}_未标注地点"
    (folder / ".D.JPG.123.deadbeef.gardener-part").write_bytes(b"half")  # a temporary file the crash left
    with env.client() as c:                                              # restart: recovered and finished
        p = wait_until(lambda: (x := c.get(f"{API}/imports/{pid}").json())["status"] == "completed" and x,
                       what="resumed import")
        assert p["interruptions"] == 1 and p["progress"]["files_done"] == 6
        assert c.app.state.store.one("SELECT count(*) n FROM managed_files")["n"] == 6
    done_first = calls[:2]
    assert all(calls.count(n) == 1 for n in done_first) and len(calls) == 7   # crashed file tried twice
    assert not any(n.endswith(".gardener-part") for n in os.listdir(folder))


def test_not_enough_space_refuses_to_start(env, tmp_path, monkeypatch):
    src = card(tmp_path / "card")
    with env.client() as c:
        tid, _ = scanned(c, src)
        pid = plan(c, tid, tmp_path / "lib")["id"]
        monkeypatch.setattr(imports, "free_bytes", lambda d: 10)
        r = c.post(f"{API}/imports/{pid}/run")
        assert r.status_code == 409 and r.json()["free_bytes"] == 10 and r.json()["needed_bytes"] > 10
        assert c.get(f"{API}/imports", params={"task": tid}).json()[0]["status"] == "planned"


def test_sidecars_are_found_next_to_their_files(tmp_path):
    from bioscan.gardener.scan import sidecars
    for n in ("IMG_1.ARW", "IMG_1.JPG", "img_1.XMP", "IMG_1.ARW.xmp", "IMG_10.xmp", "._IMG_1.xmp", "IMG_1.acr"):
        (tmp_path / n).write_bytes(b"x")
    got = sidecars([str(tmp_path / "IMG_1.ARW"), str(tmp_path / "IMG_1.JPG")])
    assert [os.path.basename(p) for p in got[str(tmp_path / "IMG_1.ARW")]] == ["IMG_1.ARW.xmp", "IMG_1.acr", "img_1.XMP"]
    assert [os.path.basename(p) for p in got[str(tmp_path / "IMG_1.JPG")]] == ["IMG_1.acr", "img_1.XMP"]
    assert imports.folder_name(["2026-10-20", "2026-10-18"], " 青海湖 ") == "2026-10-18~2026-10-20_青海湖"
    assert imports.renamed("IMG_1.ARW.xmp", "IMG_1", "_B3") == "IMG_1_B3.ARW.xmp"
