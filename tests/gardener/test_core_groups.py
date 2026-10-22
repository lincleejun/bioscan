"""Versions of one capture and people groups through the real service on the fake toolkit."""
from __future__ import annotations

import os
import random

from core_fixtures import *  # noqa: F403 - fixtures (env, fakes)
from core_fixtures import FACE_A, FACE_B, FACE_C, create_task, make_jpg, wait_task, wait_until
from PIL import Image

API = "/api/v1"


def ids(c, **q):
    return [i["id"] for i in c.get(f"{API}/assets", params=q | {"limit": 1000}).json()["items"]]


def pattern(path, seed, size=(96, 72), when=1_700_000_000.0, rotate=0):
    """A JPEG with coarse random blocks (so difference hashes mean something), optionally turned 90°."""
    rnd = random.Random(seed)
    im = Image.new("RGB", (8, 6))
    im.putdata([(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256)) for _ in range(48)])
    im = im.resize(size, Image.Resampling.NEAREST).rotate(rotate, expand=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path, quality=95)
    os.utime(path, (when, when))
    return str(path)


def test_copies_and_exports_of_one_capture_become_versions_of_one_asset(env, fakes, tmp_path):
    src = tmp_path / "trip"
    pattern(src / "raw" / "DSC01234.jpg", 1, size=(128, 96))                     # largest: the representative
    pattern(src / "camera" / "DSC01234.jpg", 1)                                   # same number, time, camera
    pattern(src / "raw" / "DSC05678.jpg", 2, when=1_700_000_100.0)
    pattern(src / "exports" / "DSC05678-export.jpg", 2, rotate=90)                # no EXIF, turned sideways
    pattern(src / "raw" / "DSC09999.jpg", 3, when=1_700_000_200.0)
    pattern(src / "exports" / "DSC09999-export.jpg", 4)                           # same number, another picture
    with env.client() as c:
        t = wait_task(c, create_task(c, [str(src)])["id"])
        assert t["scan"]["status"] == "completed"
        assert t["counts"]["versions"] == 2 and t["review"]["total"] == 4
        run = c.get(f"{API}/runs/{t['scan']['run_id']}").json()
        assert run["summary"]["versions"] == {"same_capture": 1, "export": 1}
        listed = c.get(f"{API}/assets", params={"task": t["id"]}).json()["items"]
        names = sorted(i["name"] for i in listed)
        assert names == ["DSC01234.jpg", "DSC05678.jpg", "DSC09999-export.jpg", "DSC09999.jpg"]
        hidden = c.get(f"{API}/assets", params={"task": t["id"], "versions": "only"}).json()["items"]
        assert sorted(i["name"] for i in hidden) == ["DSC01234.jpg", "DSC05678-export.jpg"]
        rep = next(i for i in listed if i["name"] == "DSC01234.jpg")
        d = c.get(f"{API}/assets/{rep['id']}").json()
        assert d["files"][0]["path"].endswith("raw/DSC01234.jpg") and rep["versions"] == 1
        assert [(v["name"], v["reason"]) for v in d["versions"]] == [("DSC01234.jpg", "same_capture")]
        export = next(i for i in hidden if i["name"] == "DSC05678-export.jpg")
        e = c.get(f"{API}/assets/{export['id']}").json()
        assert e["original"]["name"] == "DSC05678.jpg" and e["caps"]["species"]["state"] == "not_applicable"
        assert not any("-export" in p and "DSC05678" in p for p, _ in fakes.analyzed)   # analysed once, as the original
        cols = c.get(f"{API}/collections", params={"task": t["id"]}).json()
        for col in cols:                                                          # in no auto collection
            assert export["id"] not in ids(c, collection=col["id"])

        r = c.post(f"{API}/assets/{export['id']}/split-version")                  # a person: "not the same picture"
        assert r.status_code == 200 and r.json()["original"] is None and r.json()["version_lock"]
        assert r.json()["caps"]["species"]["state"] == "pending"
        allc = next(x for x in cols if x["group_kind"] == "all")
        assert export["id"] in ids(c, collection=allc["id"])
        rid = c.post(f"{API}/tasks/{t['id']}/retry", json={}).json()["id"]
        wait_until(lambda: c.get(f"{API}/runs/{rid}").json()["status"] in ("completed", "partial"), what="retry")
        e = c.get(f"{API}/assets/{export['id']}").json()
        assert e["original"] is None and e["caps"]["species"]["state"] == "done"   # analysed, never linked again


def test_people_found_on_previews_become_person_groups(env, fakes, tmp_path):
    src = tmp_path / "card"
    for i in range(3):
        make_jpg(src / f"IMG_10{i}.jpg", color=FACE_A, when=1_700_000_000 + i)
        make_jpg(src / f"IMG_20{i}.jpg", color=FACE_B, when=1_700_000_100 + i)
    make_jpg(src / "IMG_300.jpg", color=(90, 40, 30), when=1_700_000_200)
    with env.client() as c:
        t = wait_task(c, create_task(c, [str(src)], preset="photo")["id"])
        assert t["scan"]["status"] == "completed"
        run = c.get(f"{API}/runs/{t['scan']['run_id']}").json()
        assert run["config"]["versions"]["people"] == "people@fake1" and run["summary"]["analyzed"]["people"] == 7
        people = [x for x in c.get(f"{API}/collections", params={"task": t["id"]}).json()
                  if x["group_kind"] == "person" and not x["parent_id"]]
        members = sorted(sorted(i["name"] for i in c.get(f"{API}/assets", params={"collection": x["id"]}).json()["items"])
                         for x in people)
        assert members == [["IMG_100.jpg", "IMG_101.jpg", "IMG_102.jpg"],              # one group per person
                           ["IMG_200.jpg", "IMG_201.jpg", "IMG_202.jpg"]]
        lone = next(i for i in c.get(f"{API}/assets", params={"task": t["id"]}).json()["items"] if i["name"] == "IMG_300.jpg")
        d = c.get(f"{API}/assets/{lone['id']}").json()
        assert d["caps"]["people"]["state"] == "unrecognized"
        face = next(i for i in c.get(f"{API}/assets", params={"task": t["id"]}).json()["items"] if i["name"] == "IMG_100.jpg")
        f = c.get(f"{API}/assets/{face['id']}").json()["caps"]["people"]["data"]["faces"]
        assert f and "vec" not in f[0]                                           # identity features stay in the database


def person_groups(c, task_id):
    return {x["name"]: x for x in c.get(f"{API}/collections", params={"task": task_id}).json()
            if x["group_kind"] == "person"}


def test_naming_a_person_reaches_other_tasks_and_one_name_merges_two_groups(env, fakes, tmp_path):
    a, b = tmp_path / "trip1", tmp_path / "trip2"
    for i in range(3):
        make_jpg(a / f"IMG_10{i}.jpg", color=FACE_A, when=1_700_000_000 + i)
        make_jpg(a / f"IMG_20{i}.jpg", color=FACE_C, when=1_700_000_100 + i)      # the same person, another cluster
        make_jpg(b / f"IMG_30{i}.jpg", color=FACE_A, when=1_700_100_000 + i)
    make_jpg(b / "IMG_400.jpg", color=FACE_B, when=1_700_100_100)
    with env.client() as c:
        t1 = wait_task(c, create_task(c, [str(a)], preset="photo")["id"])
        groups = person_groups(c, t1["id"])
        assert len(groups) == 2
        first, second = (x for x in groups.values())
        r = c.patch(f"{API}/collections/{first['id']}", json={"name": "人物 9"})
        assert r.status_code == 409                                              # reserved for unnamed people
        named = c.patch(f"{API}/collections/{first['id']}", json={"name": "小明", "version": first["version"]}).json()
        assert named["id"] == first["id"] and named["name"] == "小明"
        assert [p["name"] for p in c.get(f"{API}/people").json()] == ["小明"]
        merged = c.patch(f"{API}/collections/{second['id']}", json={"name": "小明"}).json()
        assert merged["id"] == first["id"] and merged["size"] == 6               # two clusters, one person
        assert c.get(f"{API}/collections/{second['id']}").json()["deleted_at"]
        assert list(person_groups(c, t1["id"])) == ["小明"]

        t2 = wait_task(c, create_task(c, [str(b)], preset="photo")["id"])         # next scan: joins directly
        lone = ids(c, task=t2["id"], q="IMG_400")[0]
        groups = person_groups(c, t2["id"])
        assert sorted(groups) == sorted(["小明", lone]) and groups["小明"]["size"] == 3    # the fake names a cluster by its seed

        d = c.post(f"{API}/assets/{lone}/faces/0", json={"name": "小红"}).json()  # name one face
        assert d["caps"]["people"]["data"]["faces"][0]["person"] == {"id": d["caps"]["people"]["data"]["faces"][0]["person"]["id"],
                                                                     "name": "小红", "confirmed": True}
        assert "小红" in person_groups(c, t2["id"])
        assert c.post(f"{API}/assets/{lone}/faces/3", json={"name": "x"}).status_code == 404
        seen = c.get(f"{API}/assets/{ids(c, task=t2['id'], q='IMG_300')[0]}").json()["caps"]["people"]["data"]["faces"][0]
        assert seen["person"]["name"] == "小明" and seen["person"]["confirmed"] is False
