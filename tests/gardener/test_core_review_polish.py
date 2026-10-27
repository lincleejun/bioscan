"""Contract revision 7A: a person's burst split that survives rescans (AC07), undoable encounter split/merge,
keep-best naming the other collections it touches, and species cards counting encounters."""
from __future__ import annotations

from core_fixtures import *  # noqa: F403 - fixtures (env, fakes)
from core_fixtures import create_task, make_jpg, owl_box, photos, wait_task, wait_until

API = "/api/v1"
WREN = dict(scientific="Nannus pacificus", common="Pacific Wren", concept="avibase:avibase-CF5B7A9D")
T0 = 1_700_000_000.0


def ids(c, **q):
    return [i["id"] for i in c.get(f"{API}/assets", params=q | {"limit": 1000, "order": "time"}).json()["items"]]


def scanned(c, src, **kw):
    t = create_task(c, [str(src)], **kw)
    return wait_task(c, t["id"])


def rescan(c, t):
    run = c.post(f"{API}/tasks/{t['id']}/rescan", json={}).json()
    wait_until(lambda: c.get(f"{API}/runs/{run['id']}").json()["status"] in ("completed", "partial"))


def bursts(c, asset_ids):
    return [(c.get(f"{API}/assets/{i}").json()["burst"] or {}) for i in asset_ids]


def species_state(c):
    return {i: [(s["id"], s["current"]["concept"]) for s in c.get(f"{API}/assets/{i}").json()["subjects"]]
            for i in ids(c)}


def test_a_split_burst_stays_split_across_rescans_and_species_identity_is_unaffected(env, fakes, tmp_path):
    src = tmp_path / "card"
    photos(src, 5)                            # the fake cull puts every photo of the task in one burst
    for n in ("IMG_0003.jpg", "IMG_0004.jpg"):
        fakes.boxes[n] = [owl_box(**WREN)]    # a wren slipped into the owl burst
    with env.client() as c:
        t = scanned(c, src)
        p0, p1, p2, p3, p4 = ids(c, task=t["id"])
        cards_before, subjects_before = c.get(f"{API}/species").json(), species_state(c)
        before = c.get(f"{API}/assets/{p3}").json()["updated_at"]
        r = c.post(f"{API}/bursts/split", json={"at": p3})
        assert r.status_code == 201, r.text
        new = r.json()["burst_id"]
        assert r.json()["moved"] == [p3, p4] and new.startswith(r.json()["from"] + "~")
        assert p3 in ids(c, task=t["id"], changed_after=before)               # pollers see it

        def check():
            b = bursts(c, [p0, p1, p2, p3, p4])
            assert [x["id"] for x in b] == [r.json()["from"]] * 3 + [new] * 2
            assert [(x["rank"], x["size"]) for x in b] == [(1, 3), (2, 3), (3, 3), (1, 2), (2, 2)]
            kb = c.post(f"{API}/marks/keep-best", json={"asset_id": p3, "dry_run": True}).json()
            assert kb["burst_id"] == new and kb["reject"] == [p4]               # keep-best stays in its half
            enc = c.get(f"{API}/encounters", params={"task": t["id"]}).json()["encounters"]
            assert sorted(b["size"] for e in enc for b in e["bursts"]) == [2, 3]

        check()
        runs = len(c.get(f"{API}/assets/{p3}").json()["ai_history"])
        rescan(c, t)                                                           # the reducer re-derives b1 for all five
        assert len(c.get(f"{API}/assets/{p3}").json()["ai_history"]) == runs + 1
        check()
        assert c.get(f"{API}/species").json() == cards_before and species_state(c) == subjects_before
        assert all(h["run_id"] != "user" for h in c.get(f"{API}/assets/{p3}").json()["ai_history"])
        one = c.post(f"{API}/bursts/split", json={"asset_ids": [p4]}).json()   # a part of one frame is no burst
        assert one["burst_id"] is None and bursts(c, [p3, p4]) == [{}, {}]
        assert c.post(f"{API}/bursts/split", json={"at": p0}).status_code == 400          # nothing left behind
        assert c.post(f"{API}/bursts/split", json={"asset_ids": [p0, p4]}).status_code == 400   # two bursts
        assert c.post(f"{API}/bursts/split", json={}).status_code == 400


def encounter_photos(src):
    """Owls a minute apart, then one much later: encounters [0, 1, 2], [3]."""
    for i, dt in enumerate((0, 60, 120, 3000)):
        make_jpg(src / f"IMG_{i}.jpg", color=(10 * i, 100, 30), when=T0 + dt)


def listing(c, t):
    return [e["asset_ids"] for e in c.get(f"{API}/encounters", params={"task": t["id"]}).json()["encounters"]]


def test_encounter_split_and_merge_undo_as_one_batch_each_and_refuse_once_changed(env, tmp_path):
    src = tmp_path / "card"
    encounter_photos(src)
    with env.client() as c:
        t = scanned(c, src, caps=["meta", "subject", "species"])
        p0, p1, p2, p3 = ids(c, task=t["id"])
        es = c.get(f"{API}/encounters", params={"task": t["id"]}).json()["encounters"]
        split = c.post(f"{API}/encounters/{es[0]['id']}/split", json={"at": p1}).json()
        assert split["batch_id"] and listing(c, t) == [[p0], [p1, p2], [p3]]
        before = c.get(f"{API}/assets/{p1}").json()["updated_at"]
        assert c.post(f"{API}/encounters/undo", json={"batch_id": split["batch_id"]}).json()["undone"] == 2
        assert listing(c, t) == [[p0, p1, p2], [p3]] and p1 in ids(c, task=t["id"], changed_after=before)
        assert c.post(f"{API}/encounters/undo", json={"batch_id": split["batch_id"]}).status_code == 404   # once
        merge = c.post(f"{API}/encounters/merge", json={"ids": [es[0]["id"], es[1]["id"]]}).json()
        assert listing(c, t) == [[p0, p1, p2, p3]]
        later = c.post(f"{API}/encounters/{es[0]['id']}/split", json={"asset_ids": [p0]}).json()
        r = c.post(f"{API}/encounters/undo", json={"batch_id": merge["batch_id"]})
        assert r.status_code == 409 and es[0]["id"] in r.json()["encounters"]             # changed since
        assert c.post(f"{API}/encounters/undo", json={"batch_id": later["batch_id"]}).status_code == 200
        assert c.post(f"{API}/encounters/undo", json={"batch_id": merge["batch_id"]}).json()["undone"] == 1
        assert listing(c, t) == [[p0, p1, p2], [p3]]
        assert c.post(f"{API}/encounters/{es[1]['id']}/split", json={"at": p3}).status_code == 400   # live again
        rescan(c, t)
        assert listing(c, t) == [[p0, p1, p2], [p3]]


def test_keep_best_preview_names_the_other_collections_its_rejects_are_in(env, tmp_path):
    src = tmp_path / "card"
    photos(src, 3)
    with env.client() as c:
        t = scanned(c, src)
        keep, a, b = ids(c, task=t["id"])
        trip = c.post(f"{API}/collections", json={"name": "行程精选", "kind": "manual", "asset_ids": [a]}).json()
        r = c.post(f"{API}/marks/keep-best", json={"asset_id": keep, "dry_run": True}).json()
        got = {x["id"]: x for x in r["collections"]}
        assert got[trip["id"]] == {"id": trip["id"], "name": "行程精选", "asset_ids": [a]}


def test_species_cards_count_the_encounters_that_show_them(env, fakes, tmp_path):
    src, more = tmp_path / "card", tmp_path / "more"
    encounter_photos(src)                     # owls in two encounters
    make_jpg(more / "IMG_7.jpg", color=(1, 2, 3), when=T0)
    with env.client() as c:
        t = scanned(c, src, caps=["meta", "subject", "species"])
        u = scanned(c, more, caps=["meta", "subject", "species"])
        (s0,) = c.get(f"{API}/assets/{ids(c, task=t['id'])[0]}").json()["subjects"]
        c.post(f"{API}/identifications", json={"subject_ids": [s0["id"]], "claim": {"accept": True}})
        owl = next(k for k in c.get(f"{API}/species").json()["species"] if k["concept"] == "avibase:test-asio")
        assert owl["encounters"] == 3 and owl["photos"] == 5
        task = c.get(f"{API}/species", params={"task": t["id"]}).json()["species"][0]
        assert task["encounters"] == 2
        assert c.get(f"{API}/species", params={"task": u["id"]}).json()["to_confirm"][0]["encounters"] == 1
        detail = c.get(f"{API}/assets/{ids(c, task=t['id'])[0]}").json()
        assert [(e["task_id"], e["photos"]) for e in detail["encounters"]] == [(t["id"], 3)]
