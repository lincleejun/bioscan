"""契约修订 10B (#131): a marked photo that becomes a version of a new representative hands its marks over.
Dual-slot camera: JPEGs on slot 2 are scanned and culled first, the RAWs on slot 1 are added later."""
from __future__ import annotations

import os

from core_fixtures import *  # noqa: F403 - fixtures (env, fakes)
from core_fixtures import create_task, make_jpg, wait_task
from test_core_review import finish

API = "/api/v1"
WHEN = 1_700_000_000


def cards(tmp_path):
    slot2, slot1 = tmp_path / "slot2", tmp_path / "slot1"
    make_jpg(slot2 / "DSC01234.JPG", when=WHEN)
    slot1.mkdir()
    (slot1 / "DSC01234.ARW").write_bytes(b"raw" * 1000)
    os.utime(slot1 / "DSC01234.ARW", (WHEN, WHEN))
    return slot2, slot1


def only(c, task_id):
    items = c.get(f"{API}/assets", params={"task": task_id}).json()["items"]
    assert len(items) == 1, items
    return items[0]


def add_source(c, task_id, path):
    run = finish(c, c.post(f"{API}/tasks/{task_id}/sources", json={"paths": [str(path)]}).json()["id"])
    assert run["status"] in ("completed", "partial"), run


def test_marks_follow_the_new_representative_and_undo_takes_them_back(env, tmp_path):
    slot2, slot1 = cards(tmp_path)
    with env.client() as c:
        t = wait_task(c, create_task(c, [str(slot2)])["id"])
        jpg = only(c, t["id"])["id"]
        c.post(f"{API}/marks", json={"asset_ids": [jpg], "use": "edit", "stars": 5})
        add_source(c, t["id"], slot1)
        raw = only(c, t["id"])
        assert raw["id"] != jpg and raw["name"] == "DSC01234.ARW"
        assert (raw["use"], raw["stars"]) == ("edit", 5)
        assert c.get(f"{API}/assets", params={"task": t["id"], "use": "edit"}).json()["total"] == 1
        p = c.post(f"{API}/tasks/{t['id']}/import-plan", json={"dest_root": str(tmp_path / "lib"),
                                                              "include_pending": False}).json()
        assert p["assets"] == 1 and p["uses"]["edit"] == 1 and p["not_copied"]["pending"] == 0
        d = c.get(f"{API}/assets/{raw['id']}").json()
        carry = [h for h in d["mark_history"] if h["reason"] == f"version:carry:{jpg}"]
        assert {h["field"] for h in carry} == {"use", "stars"} and {h["actor"] for h in carry} == {"system"}
        assert len({h["batch_id"] for h in carry}) == 1
        assert d["versions"][0]["id"] == jpg and not d["versions"][0]["marks_differ"]
        assert c.get(f"{API}/assets/{jpg}").json()["use"] == "edit"          # the version keeps its own marks
        r = c.post(f"{API}/marks/undo", json={"batch_id": carry[0]["batch_id"]})
        assert r.status_code == 200, r.text
        raw = only(c, t["id"])
        assert (raw["use"], raw["stars"]) == ("pending", 0)
        # a person's "not the same capture" stands the JPEG on its own again; the representative's marks stay
        c.post(f"{API}/marks", json={"asset_ids": [raw["id"]], "use": "record", "stars": 3})
        assert c.post(f"{API}/assets/{jpg}/split-version").status_code == 200
        a = c.get(f"{API}/assets/{raw['id']}").json()
        assert (a["use"], a["stars"]) == ("record", 3)


def test_a_marked_representative_keeps_its_marks_and_shows_the_difference(env, tmp_path):
    slot2, slot1 = cards(tmp_path)
    with env.client() as c:
        first = wait_task(c, create_task(c, [str(slot1)])["id"])          # the RAW, marked in another task
        raw = only(c, first["id"])["id"]
        c.post(f"{API}/marks", json={"asset_ids": [raw], "use": "record", "stars": 2})
        t = wait_task(c, create_task(c, [str(slot2)])["id"])
        jpg = only(c, t["id"])["id"]
        c.post(f"{API}/marks", json={"asset_ids": [jpg], "use": "edit", "stars": 5})
        add_source(c, t["id"], slot1)
        d = c.get(f"{API}/assets/{only(c, t['id'])['id']}").json()
        assert d["id"] == raw and (d["use"], d["stars"]) == ("record", 2)
        assert not [h for h in d["mark_history"] if h["actor"] == "system"]
        v = d["versions"][0]
        assert v["id"] == jpg and (v["use"], v["stars"], v["marks_differ"]) == ("edit", 5, True)
