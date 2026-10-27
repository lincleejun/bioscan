"""契约修订 7B, import side: the completion page's species stats (PRD §09: new species, additions, candidates to
confirm, sole-record impact) and the import batch as a manual collection."""
from __future__ import annotations

import os

from core_fixtures import *  # noqa: F403 - fixtures (env, fakes)
from core_fixtures import create_task, make_jpg, owl_box, wait_task, wait_until

from bioscan.gardener import imports

API = "/api/v1"
WREN = dict(scientific="Nannus pacificus", common="Pacific Wren", concept="avibase:test-wren")
ROBIN = dict(scientific="Erithacus rubecula", common="European Robin", concept="avibase:test-robin")
KESTREL = dict(scientific="Falco tinnunculus", common="Common Kestrel", concept="avibase:test-kestrel")


def by_name(c, tid):
    return {i["name"]: i["id"] for i in c.get(f"{API}/assets", params={"task": tid, "limit": 100}).json()["items"]}


def confirm(c, aid):
    sid = c.get(f"{API}/assets/{aid}").json()["subjects"][0]["id"]
    r = c.post(f"{API}/identifications", json={"subject_ids": [sid], "claim": {"accept": True}})
    assert r.status_code == 200, r.text


def plan(c, tid, dest, **kw):
    r = c.post(f"{API}/tasks/{tid}/import-plan", json={"dest_root": str(dest), **kw})
    assert r.status_code == 201, r.text
    return r.json()


def run(c, pid):
    assert c.post(f"{API}/imports/{pid}/run").status_code == 200
    return wait_until(lambda: (p := c.get(f"{API}/imports/{pid}").json())["status"] in
                      ("completed", "partial", "failed") and p, what=f"import {pid}")


def test_completion_stats_show_new_added_candidates_and_sole_records(env, fakes, tmp_path):
    """An owl confirmed earlier elsewhere: the batch adds to it. Wren and kestrel are confirmed only here: new
    species. A robin only the model names: to confirm. Wren, robin and kestrel have one photo each in the library:
    sole records; the rejected kestrel is not imported and stays only on the card."""
    old, card = tmp_path / "old", tmp_path / "card"
    make_jpg(old / "OLD.JPG", color=(1, 2, 3))
    for i, n in enumerate("ABCD"):
        make_jpg(card / f"{n}.JPG", color=(50 * i, 90, 20))
    fakes.boxes.update({"B.JPG": [owl_box(**WREN)], "C.JPG": [owl_box(**ROBIN)], "D.JPG": [owl_box(**KESTREL)]})
    with env.client() as c:
        t0 = wait_task(c, create_task(c, [str(old)])["id"])
        confirm(c, by_name(c, t0["id"])["OLD.JPG"])
        tid = wait_task(c, create_task(c, [str(card)])["id"])["id"]
        ids = by_name(c, tid)
        for n in "ABD":
            confirm(c, ids[f"{n}.JPG"])
        c.post(f"{API}/marks", json={"asset_ids": [ids["A.JPG"]], "use": "edit"})
        c.post(f"{API}/marks", json={"asset_ids": [ids["B.JPG"]], "use": "record"})
        c.post(f"{API}/marks", json={"asset_ids": [ids["D.JPG"]], "use": "reject"})
        p = plan(c, tid, tmp_path / "lib")
        sp = p["species"]
        assert sp["counts"] == {"new": 2, "added": 1, "to_confirm": 1, "sole": 3, "sole_left": 1}
        assert [(x["concept"], x["photos"], x["importing"], x["before"]) for x in sp["new"]] == \
            [("avibase:test-kestrel", 1, 0, 0), ("avibase:test-wren", 1, 1, 0)]
        assert [(x["concept"], x["photos"], x["importing"], x["before"]) for x in sp["added"]] == \
            [("avibase:test-asio", 1, 1, 1)]
        assert [(x["concept"], x["photos"]) for x in sp["to_confirm"]] == [("avibase:test-robin", 1)]
        assert [(x["name"], x["use"], x["imported"], x["confirmed"]) for x in sp["sole"]] == \
            [("D.JPG", "reject", False, True), ("C.JPG", "pending", True, False), ("B.JPG", "record", True, True)]
        assert "species" not in c.get(f"{API}/imports", params={"task": tid}).json()[0]     # lists stay light

        confirm(c, ids["C.JPG"])                         # live: a confirmation after planning shows at once
        sp = c.get(f"{API}/imports/{p['id']}").json()["species"]
        assert sp["counts"]["new"] == 3 and sp["counts"]["to_confirm"] == 0


def test_a_finished_batch_becomes_a_collection_that_later_runs_extend(env, tmp_path, monkeypatch):
    card = tmp_path / "card"
    for i, n in enumerate("ABC"):
        make_jpg(card / f"{n}.JPG", color=(60 * i, 10, 99))
    real, calls = imports._copy, []

    def flaky(s, d):
        calls.append(os.path.basename(s))
        if os.path.basename(s) == "B.JPG" and calls.count("B.JPG") == 1:
            raise OSError("模拟写入失败")
        return real(s, d)
    monkeypatch.setattr(imports, "_copy", flaky)
    with env.client() as c:
        tid = wait_task(c, create_task(c, [str(card)], caps=[])["id"])["id"]
        ids = by_name(c, tid)
        p = plan(c, tid, tmp_path / "lib", place="东滩")
        assert p["collection_id"] is None
        p = run(c, p["id"])
        assert p["status"] == "partial" and p["collection_id"]
        col = c.get(f"{API}/collections/{p['collection_id']}").json()
        assert col["name"] == p["folder"] and col["kind"] == "manual" and col["origin"] == "user"
        assert col["task_id"] == tid and col["size"] == 2                # only fully imported photos
        p = run(c, p["id"])                                                  # the retry adds B to the same one
        assert p["status"] == "completed" and c.get(f"{API}/collections/{p['collection_id']}").json()["size"] == 3
        members = c.get(f"{API}/assets", params={"collection": p["collection_id"]}).json()["items"]
        assert {m["id"] for m in members} == set(ids.values())

        c.delete(f"{API}/collections/{p['collection_id']}")
        again = c.post(f"{API}/imports/{p['id']}/collection")           # on request: made again
        assert again.status_code == 200 and again.json()["collection_id"] not in (None, p["collection_id"])

        empty = plan(c, tid, tmp_path / "other")
        assert c.post(f"{API}/imports/{empty['id']}/collection").status_code == 400   # nothing imported yet
