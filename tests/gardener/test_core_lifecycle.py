"""One shoot through every stage the AfterShooting units added, on the real service and the fake toolkit:
identify (revision 4) -> uses (5B) -> import (5D) -> Lightroom handoff of the managed copy and cleanup of every
copy (5E) -> species counts. The seams between units are what this checks; each unit tests its own part."""
from __future__ import annotations

import json
from pathlib import Path

from core_fixtures import *  # noqa: F403 - fixtures (env, fakes)
from core_fixtures import create_task, make_jpg, owl_box, wait_task, wait_until
from test_core_cleanup import Bin

API = "/api/v1"
WREN = dict(scientific="Nannus pacificus", common="Pacific Wren", concept="avibase:wren")


def test_a_shoot_from_identification_to_import_handoff_and_cleanup(env, fakes, tmp_path):
    src, lib, trash = tmp_path / "card", tmp_path / "library", Bin(tmp_path / "bin")
    for i in range(3):
        make_jpg(src / f"IMG_{i}.jpg", color=(30 * i, 120, 30), when=1_700_000_000 + 3600 * i)
    fakes.boxes["IMG_2.jpg"] = [owl_box(**WREN)]                     # IMG_0, IMG_1 owls; IMG_2 the only wren
    with env.client(trash=trash, lightroom_dir=tmp_path / "lr") as c:
        t = wait_task(c, create_task(c, [str(src)])["id"])
        ids = {i["name"]: i["id"] for i in c.get(f"{API}/assets", params={"task": t["id"]}).json()["items"]}
        for name in ids:                                              # a person confirms every subject
            sid = c.get(f"{API}/assets/{ids[name]}").json()["subjects"][0]["id"]
            c.post(f"{API}/identifications", json={"subject_ids": [sid], "claim": {"accept": True}})
        species = lambda: {k["common"]: k["photos"] for k in c.get(f"{API}/species").json()["species"]}  # noqa: E731
        assert species() == {"Eastern Screech-Owl": 2, "Pacific Wren": 1}

        c.post(f"{API}/marks", json={"asset_ids": [ids["IMG_0.jpg"]], "use": "edit"})
        c.post(f"{API}/marks", json={"asset_ids": [ids["IMG_1.jpg"]], "use": "record"})
        c.post(f"{API}/marks", json={"asset_ids": [ids["IMG_2.jpg"]], "use": "reject"})
        p = c.post(f"{API}/tasks/{t['id']}/import-plan", json={"dest_root": str(lib), "place": "东滩"}).json()
        assert p["uses"]["edit"] == 1 and p["uses"]["record"] == 1 and p["not_copied"]["reject"] == 1
        c.post(f"{API}/imports/{p['id']}/run")
        done = wait_until(lambda: (x := c.get(f"{API}/imports/{p['id']}").json())["status"] in ("completed", "partial")
                          and x, what="import")
        assert done["status"] == "completed" and done["failed"] == []
        managed = c.get(f"{API}/assets/{ids['IMG_0.jpg']}").json()["managed"]
        assert len(managed) == 1 and Path(managed[0]["path"]).is_relative_to(lib)

        everything = next(x for x in c.get(f"{API}/collections", params={"task": t["id"]}).json() if x["group_kind"] == "all")
        h = c.post(f"{API}/handoffs", json={"collection_id": everything["id"]}).json()
        doc = json.loads(Path(h["doc_path"]).read_text())
        assert [p_["asset_id"] for p_ in doc["photos"]] == [ids["IMG_0.jpg"]]     # 选入修图 only by default
        assert doc["photos"][0]["paths"][0] == managed[0]["path"]               # the managed copy comes first

        c.post(f"{API}/marks", json={"asset_ids": [ids["IMG_1.jpg"]], "use": "reject"})
        p1 = c.post(f"{API}/tasks/{t['id']}/import-plan", json={"dest_root": str(lib)}).json()
        assert p1["not_copied"]["reject"] == 2
        plan = c.post(f"{API}/cleanup-plans", json={}).json()
        owl_files = next(i for i in plan["items"] if i["asset_id"] == ids["IMG_1.jpg"])["files"]
        assert {f["role"] for f in owl_files} == {"jpeg", "managed"}           # the imported copy goes too
        ran = c.post(f"{API}/cleanup-plans/{plan['id']}/run", json={"confirm": True}).json()
        assert ran["report"]["freed_bytes"] == 0 and ran["report"]["trashed_files"] >= 3
        assert species() == {"Eastern Screech-Owl": 1}                       # the trashed wren and owl left the counts
        owl = c.get(f"{API}/species").json()["species"][0]["concept"]
        enc = c.get(f"{API}/encounters", params={"task": t["id"], "concept": owl}).json()
        assert [a for e in enc["encounters"] for a in e["asset_ids"]] + enc["unassigned"] == [ids["IMG_0.jpg"]]  # and encounters


def test_an_offline_disk_never_changes_species_counts(env, fakes, tmp_path):
    from bioscan.gardener import store as st

    src = tmp_path / "card"
    make_jpg(src / "IMG_0.jpg")
    with env.client() as c:
        t = wait_task(c, create_task(c, [str(src)])["id"])
        (item,) = c.get(f"{API}/assets", params={"task": t["id"]}).json()["items"]
        sid = c.get(f"{API}/assets/{item['id']}").json()["subjects"][0]["id"]
        c.post(f"{API}/identifications", json={"subject_ids": [sid], "claim": {"accept": True}})
        with st.Store(env.data).tx() as db:                            # the disk is unplugged
            db.execute("UPDATE asset_files SET online = 0")
            db.execute("UPDATE assets SET online = 0")
        assert c.get(f"{API}/species").json()["confirmed_total"] == 1


def test_a_narrow_tasks_own_representative_is_in_its_species_encounters_and_import(env, fakes, tmp_path):
    """#102 per-task representatives in the task views: B covers only camera/, so its JPEG, a version of the RAW
    in the wider task A, is B's own photo there; library-wide counts still see one capture."""
    from test_core_groups import pattern

    shoot = tmp_path / "shoot"
    pattern(shoot / "raw" / "DSC01234.jpg", 1, size=(128, 96))
    pattern(shoot / "camera" / "DSC01234.jpg", 1)
    with env.client() as c:
        a = wait_task(c, create_task(c, [str(shoot)])["id"])
        b = wait_task(c, create_task(c, [str(shoot / "camera")])["id"])
        (cam,) = [i["id"] for i in c.get(f"{API}/assets", params={"task": b["id"]}).json()["items"]]
        owl = lambda **q: [k["photos"] for k in c.get(f"{API}/species", params=q).json()["to_confirm"]]  # noqa: E731
        assert owl(task=b["id"]) == [1] and owl(task=a["id"]) == [1] and owl() == [1]    # one capture in the library
        enc = c.get(f"{API}/encounters", params={"task": b["id"]}).json()
        assert [e["asset_ids"] for e in enc["encounters"]] == [[cam]] and enc["unassigned"] == []
        p = c.post(f"{API}/tasks/{b['id']}/import-plan", json={"dest_root": str(tmp_path / "lib")}).json()
        assert p["assets"] == 1 and p["not_copied"]["versions"] == 0
        pa = c.post(f"{API}/tasks/{a['id']}/import-plan", json={"dest_root": str(tmp_path / "lib")}).json()
        assert pa["assets"] == 1 and pa["not_copied"]["versions"] == 1               # A copies the RAW only
