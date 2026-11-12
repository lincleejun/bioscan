"""Contract revision 13: one name per burst (the burst vote), merging bursts, and grouping a task again from its
stored results without files or models."""
from __future__ import annotations

import sqlite3

from core_fixtures import *  # noqa: F403 - fixtures (env, fakes)
from core_fixtures import create_task, owl_box, photos, wait_task, wait_until

from bioscan.gardener import analyze

API = "/api/v1"
OWL = "avibase:test-asio"
WREN = dict(scientific="Nannus pacificus", common="Pacific Wren", concept="avibase:avibase-CF5B7A9D")
TIT = dict(scientific="Parus major", common="Great Tit", concept="avibase:test-tit")


def ids(c, **q):
    return [i["id"] for i in c.get(f"{API}/assets", params=q | {"limit": 1000, "order": "time"}).json()["items"]]


def scanned(c, src):
    return wait_task(c, create_task(c, [str(src)])["id"])


def names(c, aid):
    return [(s["current"]["level"], s["current"]["concept"]) for s in c.get(f"{API}/assets/{aid}").json()["subjects"]]


def run(c, t, what):
    r = c.post(f"{API}/tasks/{t['id']}/{what}", json={})
    assert r.status_code == 201, r.text
    wait_until(lambda: c.get(f"{API}/runs/{r.json()['id']}").json()["status"] in ("completed", "partial"))
    return c.get(f"{API}/runs/{r.json()['id']}").json()


def burst(c, aid):
    return c.get(f"{API}/assets/{aid}").json()["burst"] or {}


def test_a_burst_takes_its_majority_name_and_a_person_or_a_second_bird_is_never_outvoted(env, fakes, tmp_path):
    src = tmp_path / "card"
    photos(src, 5)                                                    # the fake cull: one burst
    fakes.boxes["IMG_0001.jpg"] = [owl_box(level="genus")]            # the model stopped at the genus here
    fakes.boxes["IMG_0003.jpg"] = [owl_box(**WREN)]                   # and called this frame something else
    fakes.boxes["IMG_0004.jpg"] = [owl_box(), owl_box((0.6, 0.6, 0.9, 0.9), **TIT)]   # an extra box, named once
    with env.client() as c:
        t = scanned(c, src)
        a = ids(c, task=t["id"])
        assert [names(c, i) for i in a[:4]] == [[("species", OWL)]] * 4          # genus and the odd name: the owl
        assert names(c, a[4]) == [("species", OWL), ("unconfirmed", None)]       # a name seen once in 5 frames: 待鉴定
        cards = c.get(f"{API}/species").json()["to_confirm"]
        assert [(k["common"], k["photos"]) for k in cards] == [("Eastern Screech-Owl", 5)]
        def rows():
            with sqlite3.connect(env.data / "gardener.sqlite") as db:
                return db.execute("SELECT count(*) FROM identifications").fetchone()[0]
        written = rows()
        run(c, t, "regroup")                                                     # idempotent: nothing new is written
        assert rows() == written
        # a person says the fourth frame is a wren: it stays a wren through every later vote
        sid = c.get(f"{API}/assets/{a[3]}").json()["subjects"][0]["id"]
        c.post(f"{API}/identifications", json={"subject_ids": [sid], "claim": {
            "level": "species", "concept": WREN["concept"], "taxon": WREN["scientific"], "common": WREN["common"]}})
        run(c, t, "rescan")
        assert names(c, a[3]) == [("species", WREN["concept"])] and names(c, a[0]) == [("species", OWL)]


def test_a_species_named_twice_in_a_burst_is_a_second_bird_and_a_split_gives_each_part_its_own_vote(env, fakes, tmp_path):
    src = tmp_path / "card"
    photos(src, 5)
    for n in ("IMG_0003.jpg", "IMG_0004.jpg"):
        fakes.boxes[n] = [owl_box(**WREN)]
    fakes.boxes["IMG_0002.jpg"] = [owl_box(level="genus")]
    with env.client() as c:
        t = scanned(c, src)
        a = ids(c, task=t["id"])
        assert names(c, a[2]) == [("genus", None)] and names(c, a[3]) == [("species", WREN["concept"])]   # no vote
        assert c.post(f"{API}/bursts/split", json={"at": a[3]}).status_code == 201
        assert names(c, a[2]) == [("species", OWL)]                              # three frames, two owls: the owl
        assert names(c, a[3]) == names(c, a[4]) == [("species", WREN["concept"])]


def test_bursts_merge_into_one_that_survives_rescans_and_single_photos_can_join(env, fakes, tmp_path):
    src = tmp_path / "card"
    photos(src, 5)
    with env.client() as c:
        t = scanned(c, src)
        a = ids(c, task=t["id"])
        c.post(f"{API}/bursts/split", json={"at": a[2]})                          # [a0 a1] [a2 a3 a4]
        c.post(f"{API}/bursts/split", json={"asset_ids": [a[4]]})                 # [a0 a1] [a2 a3] a4 alone
        assert burst(c, a[4]) == {} and burst(c, a[0])["id"] != burst(c, a[2])["id"]
        before = c.get(f"{API}/assets/{a[0]}").json()["updated_at"]
        r = c.post(f"{API}/bursts/merge", json={"task_id": t["id"], "asset_ids": [a[0], a[4]]})
        assert r.status_code == 201, r.text
        m = r.json()
        assert m["size"] == 3 and m["rep"] in (a[0], a[1], a[4])                  # a0's whole burst and the single

        def check():
            assert [burst(c, i)["id"] for i in (a[0], a[1], a[4])] == [m["burst_id"]] * 3
            assert sorted(burst(c, i)["rank"] for i in (a[0], a[1], a[4])) == [1, 2, 3]
            assert [(burst(c, i)["size"]) for i in (a[2], a[3])] == [2, 2] and burst(c, a[2])["id"] != m["burst_id"]

        check()
        assert a[1] in ids(c, task=t["id"], changed_after=before)                 # pollers see every moved frame
        run(c, t, "rescan")
        check()
        run(c, t, "regroup")
        check()
        assert c.post(f"{API}/bursts/merge", json={"task_id": t["id"], "asset_ids": [a[0], a[1]]}).status_code == 400
        assert c.post(f"{API}/bursts/merge", json={"task_id": t["id"], "asset_ids": [a[0]]}).status_code == 400
        assert c.post(f"{API}/bursts/merge", json={"task_id": "t_none", "asset_ids": [a[0], a[2]]}).status_code == 404


def test_regroup_reads_stored_results_only(env, fakes, tmp_path):
    src = tmp_path / "card"
    photos(src, 3)
    with env.client() as c:
        t = scanned(c, src)
        a = ids(c, task=t["id"])
        seen = len(fakes.analyzed)
        for f in src.iterdir():
            f.unlink()                                                            # the card is gone
        r = run(c, t, "regroup")
        assert r["kind"] == "regroup" and r["status"] in ("completed", "partial")
        assert len(fakes.analyzed) == seen                                        # nothing was analysed again
        assert burst(c, a[0])["size"] == 3 and len(c.get(f"{API}/assets/{a[0]}").json()["ai_history"]) == 2


def test_the_reducer_is_told_about_quality_and_gardener_groups_wider_than_the_album_profile(monkeypatch):
    def rows(times):
        return [{"asset_id": f"a{i}", "path": f"a{i}", "taken_at": f"2026-01-01T00:00:{t:06.3f}", "camera": "X",
                 "products": {"embed": {"vector": v}, "quality": {"subject": {"blur": 0.9 - 0.2 * i}}}}
                for i, (t, v) in enumerate(times)]
    like, unlike = [1.0, 0.0], [0.0, 1.0]
    # a0-a1 one shutter press apart with unlike frames; a2 a re-press 3 s later, alike; a3 alike but 9 s later
    out = analyze.reduce(rows([(0.0, like), (0.2, unlike), (3.2, unlike), (12.2, unlike)]), ["burst", "quality"])
    assert [out[f"a{i}"]["burst"] and out[f"a{i}"]["burst"]["size"] for i in range(4)] == [3, 3, 3, None]
    assert out["a2"]["burst"]["rank"] == 1                                        # the sharpest frame, not the first
    assert analyze.burst_order(rows([(0.0, like), (0.2, like)])) == ["a1", "a0"]


def test_photos_move_to_another_species_in_one_undoable_step(env, fakes, tmp_path):
    src = tmp_path / "card"
    photos(src, 4)
    fakes.boxes["IMG_0003.jpg"] = [owl_box(), owl_box((0.6, 0.6, 0.9, 0.9), **TIT)]
    wren = {"level": "species", "concept": WREN["concept"], "taxon": WREN["scientific"], "common": WREN["common"]}
    with env.client() as c:
        t = scanned(c, src)
        a = ids(c, task=t["id"])
        r = c.post(f"{API}/identifications/move", json={"asset_ids": a[:2], "from_concept": OWL, "claim": wren})
        assert r.status_code == 200, r.text
        assert len(r.json()["changed"]) == 2 and r.json()["batch_id"]
        assert names(c, a[0]) == names(c, a[1]) == [("species", WREN["concept"])] and names(c, a[2]) == [("species", OWL)]
        run(c, t, "regroup")                                          # a person's move is never outvoted by the burst
        assert names(c, a[0]) == [("species", WREN["concept"])]
        assert c.post(f"{API}/identifications/undo", json={"batch_id": r.json()["batch_id"]}).status_code == 200
        assert names(c, a[0]) == [("species", OWL)]
        # the whole card of a task: every subject called the owl, the second animal of the last photo stays
        r = c.post(f"{API}/identifications/move", json={"task_id": t["id"], "from_concept": OWL, "claim": wren}).json()
        assert len(r["changed"]) == 4 and names(c, a[3])[0] == ("species", WREN["concept"])
        assert [k["common"] for k in c.get(f"{API}/species").json()["species"]] == ["Pacific Wren"]
        # without from_concept only photos with a single animal move; nothing to move is an error, not a silent no-op
        r = c.post(f"{API}/identifications/move", json={"asset_ids": [a[3]], "claim": {"level": "unconfirmed"}})
        assert r.status_code == 400
        assert c.post(f"{API}/identifications/move", json={"claim": wren}).status_code == 400
        assert c.post(f"{API}/identifications/move", json={"asset_ids": a, "claim": {"level": "species"}}).status_code == 400
