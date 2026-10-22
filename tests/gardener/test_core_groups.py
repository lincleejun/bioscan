"""Versions of one capture and people groups through the real service on the fake toolkit."""
from __future__ import annotations

import os
import random
from pathlib import Path

from core_fixtures import *  # noqa: F403 - fixtures (env, fakes)
from core_fixtures import FACE_A, FACE_B, FACE_C, create_task, make_jpg, wait_task, wait_until
from PIL import Image

from bioscan.gardener import versions

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


def rerun(c, task_id):
    rid = c.post(f"{API}/tasks/{task_id}/rescan", json={}).json()["id"]
    wait_until(lambda: c.get(f"{API}/runs/{rid}").json()["status"] in ("completed", "partial"), what="rescan")


def members(c, task_id, kind):
    col = next(x for x in c.get(f"{API}/collections", params={"task": task_id}).json() if x["group_kind"] == kind)
    return ids(c, collection=col["id"])


def test_a_task_over_part_of_a_folder_keeps_the_wider_tasks_versions_and_shows_its_own(env, fakes, tmp_path):
    shoot = tmp_path / "shoot"
    pattern(shoot / "raw" / "DSC01234.jpg", 1, size=(128, 96))
    pattern(shoot / "camera" / "DSC01234.jpg", 1)
    with env.client() as c:
        a = wait_task(c, create_task(c, [str(shoot)])["id"])
        [raw], [cam] = ids(c, task=a["id"]), ids(c, task=a["id"], versions="only")
        b = wait_task(c, create_task(c, [str(shoot / "camera")])["id"])
        for _ in range(2):                                   # and again after both rescan
            assert ids(c, task=a["id"]) == [raw] and ids(c, task=a["id"], versions="only") == [cam]
            assert c.get(f"{API}/assets/{cam}").json()["original"]["id"] == raw
            assert ids(c, task=b["id"]) == [cam] and ids(c, task=b["id"], versions="only") == []
            got = c.get(f"{API}/tasks/{b['id']}").json()
            assert got["counts"]["versions"] == 0 and got["review"]["total"] == 1
            assert cam in members(c, b["id"], "all") and cam in members(c, b["id"], "species")
            [item] = c.get(f"{API}/assets", params={"task": b["id"]}).json()["items"]   # analysed in B itself
            assert item["species"]["taxon"] == "Megascops asio" and item["ai"] and item["analysis"] == "done"
            assert ids(c, task=b["id"], species="Megascops") == [cam] and ids(c, task=b["id"], ai="pick") == [cam]
            rerun(c, b["id"])
            rerun(c, a["id"])


def test_a_wider_task_linking_later_leaves_the_narrow_tasks_collections_alone(env, fakes, tmp_path):
    shoot = tmp_path / "shoot"
    pattern(shoot / "raw" / "DSC01234.jpg", 1, size=(128, 96))
    pattern(shoot / "camera" / "DSC01234.jpg", 1)
    with env.client() as c:
        b = wait_task(c, create_task(c, [str(shoot / "camera")])["id"])
        [cam] = ids(c, task=b["id"])
        a = wait_task(c, create_task(c, [str(shoot)])["id"])
        assert ids(c, task=a["id"], versions="only") == [cam] and cam not in members(c, a["id"], "all")
        assert ids(c, task=b["id"]) == [cam] and cam in members(c, b["id"], "all")


def no_chains(store):
    return store.one("SELECT count(*) n FROM assets v JOIN assets r ON r.id = v.version_of "
                     "WHERE r.version_of IS NOT NULL")["n"] == 0


def links(store):
    return {r["id"]: r["version_of"] for r in store.all("SELECT id, version_of FROM assets")}


def test_overlapping_tasks_never_chain_versions_and_reruns_change_nothing(env, fakes, tmp_path):
    for wide_first in (True, False):
        shoot = tmp_path / f"shoot{int(wide_first)}"
        pattern(shoot / "raw" / "DSC01234.jpg", 1, size=(128, 96))
        pattern(shoot / "camera" / "DSC01234.jpg", 1, size=(112, 84))
        pattern(shoot / "camera" / "small" / "DSC01234.jpg", 1)
        with env.client() as c:
            s = c.app.state.store
            order = [str(shoot), str(shoot / "camera")]
            tids = []
            for src in order if wide_first else order[::-1]:
                tids.append(wait_task(c, create_task(c, [src])["id"])["id"])
                assert no_chains(s)
            a = tids[0] if wide_first else tids[1]
            [raw] = ids(c, task=a)
            assert len(c.get(f"{API}/assets/{raw}").json()["versions"]) == 2    # camera and small, both of raw
            before = links(s)
            for tid in tids + tids:
                rerun(c, tid)
                assert no_chains(s) and links(s) == before


def test_a_deleted_representative_hands_over_to_the_surviving_version(env, fakes, tmp_path):
    shoot = tmp_path / "shoot"
    raw = pattern(shoot / "raw" / "DSC01234.jpg", 1, size=(128, 96))
    pattern(shoot / "camera" / "DSC01234.jpg", 1)
    with env.client() as c:
        t = wait_task(c, create_task(c, [str(shoot)])["id"])
        [cam] = ids(c, task=t["id"], versions="only")
        os.remove(raw)
        run = c.post(f"{API}/tasks/{t['id']}/sources", json={"paths": [str(shoot)]}).json()
        wait_until(lambda: c.get(f"{API}/runs/{run['id']}").json()["status"] in ("completed", "partial"), what="append")
        assert cam in ids(c, task=t["id"]) and ids(c, task=t["id"], versions="only") == []
        d = c.get(f"{API}/assets/{cam}").json()
        assert d["original"] is None and d["caps"]["species"]["state"] == "done"      # analysed in its own right


def test_not_the_same_said_while_linking_is_not_overridden(env, fakes, tmp_path, monkeypatch):
    shoot = tmp_path / "shoot"
    pattern(shoot / "raw" / "DSC01234.jpg", 1, size=(128, 96))
    cam = pattern(shoot / "camera" / "DSC01234.jpg", 1)
    real, held = versions.plan, {}

    def split_meanwhile(rows, hashes):                      # a person's split lands after link read the rows
        out = real(rows, hashes)
        aid = next(r["id"] for r in rows if r["primary_path"].endswith("camera/DSC01234.jpg"))
        with held["store"].tx() as cx:
            versions.set_version(cx, aid, None, [], "2026-01-01T00:00:00.000000Z")
            cx.execute("UPDATE assets SET version_lock = 1 WHERE id = ?", (aid,))
        return out
    monkeypatch.setattr(versions, "plan", split_meanwhile)
    with env.client() as c:
        held["store"] = c.app.state.store
        t = wait_task(c, create_task(c, [str(shoot)])["id"])
        assert t["counts"]["versions"] == 0
        aid = held["store"].one("SELECT asset_id FROM asset_files WHERE path = ?", (str(Path(cam).resolve()),))["asset_id"]
        d = c.get(f"{API}/assets/{aid}").json()
        assert d["original"] is None and d["version_lock"]


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
