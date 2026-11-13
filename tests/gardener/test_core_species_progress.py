"""Contract revision 16: per species, within a task, decided / started / total groups, computed on the server from the
marks of every frame of a group (not from what a browser happened to load)."""
from __future__ import annotations

from core_fixtures import *  # noqa: F403 - fixtures (env, fakes)
from core_fixtures import create_task, owl_box, photos, wait_task

API = "/api/v1"
OWL = "avibase:test-asio"
WREN = dict(scientific="Nannus pacificus", common="Pacific Wren", concept="avibase:avibase-CF5B7A9D")


def test_species_progress_goes_none_started_complete_over_every_frame_of_each_group(env, fakes, tmp_path):
    src = tmp_path / "card"
    photos(src, 6)                                                       # the fake cull: one burst of six
    fakes.boxes["IMG_0004.jpg"] = [owl_box(), owl_box((0.5, 0.5, 0.9, 0.9), **WREN)]   # a second bird, as big
    with env.client() as c:
        t = wait_task(c, create_task(c, [str(src)])["id"])
        a = [i["id"] for i in c.get(f"{API}/assets", params={"task": t["id"], "order": "time", "limit": 100}).json()["items"]]
        assert c.post(f"{API}/bursts/split", json={"asset_ids": [a[5]]}).status_code == 201   # [a0..a4] and a5 alone

        def prog():
            sp = c.get(f"{API}/species", params={"task": t["id"]}).json()
            return {k["concept"]: (k["groups"], k["decided"], k["started"]) for k in sp["species"] + sp["to_confirm"]}

        def mark(ids, **f):
            assert c.post(f"{API}/marks", json={"asset_ids": ids, **f}).status_code == 200

        def enc(concept):                                                # what Review lists for the species
            e = c.get(f"{API}/encounters", params={"task": t["id"], "concept": concept}).json()
            return sorted((tuple(b["asset_ids"]), b["kept"], b["rejected"]) for x in e["encounters"] for b in x["bursts"])

        # none: the owl has the five-frame burst and the single; the wren is only the one frame it is on in that burst
        assert prog() == {OWL: (2, 0, 0), WREN["concept"]: (1, 0, 0)}
        assert enc(OWL) == sorted([(tuple(a[:5]), 0, 0), ((a[5],), 0, 0)])
        assert enc(WREN["concept"]) == [((a[4],), 0, 0)]
        mark([a[5]], stars=3)                                            # a star keeps an undecided photo (#163)
        assert prog()[OWL] == (2, 1, 1)
        mark([a[5]], use="pending", stars=0)
        assert prog()[OWL] == (2, 0, 0)

        # started: two of the burst's five frames are marked; the other three are not, so the group is not decided
        mark([a[0]], use="record")
        mark([a[1]], use="reject")
        assert prog() == {OWL: (2, 0, 1), WREN["concept"]: (1, 0, 0)}
        assert enc(OWL) == sorted([(tuple(a[:5]), 1, 1), ((a[5],), 0, 0)])

        # the burst decided: the owl is one of two groups (the single is open), the wren's only group is decided
        mark(a[2:4], use="reject")
        mark([a[4]], use="edit")
        assert prog() == {OWL: (2, 1, 1), WREN["concept"]: (1, 1, 1)}

        # complete, then a mark taken back drops it again
        mark([a[5]], use="reject")
        assert prog()[OWL] == (2, 2, 2)
        assert enc(OWL) == sorted([(tuple(a[:5]), 2, 3), ((a[5],), 0, 1)])
        mark([a[5]], use="pending")
        assert prog()[OWL] == (2, 1, 1)

        # without a task the cards carry no progress (a group is a task's group)
        sp = c.get(f"{API}/species").json()
        assert all("groups" not in k for k in sp["species"] + sp["to_confirm"])


def test_species_picks_take_the_best_frame_of_different_groups_and_never_a_rejected_one(env, fakes, tmp_path):
    """Contract revision 19 (AI 选鸟): per species, the representative of up to N different groups; read-only."""
    src = tmp_path / "card"
    photos(src, 6)
    with env.client() as c:
        t = wait_task(c, create_task(c, [str(src)])["id"])
        a = [i["id"] for i in c.get(f"{API}/assets", params={"task": t["id"], "order": "time", "limit": 100}).json()["items"]]
        assert c.post(f"{API}/bursts/split", json={"asset_ids": [a[5]]}).status_code == 201   # [a0..a4] and a5 alone

        def picks(n):
            r = c.get(f"{API}/tasks/{t['id']}/picks", params={"per_species": n})
            assert r.status_code == 200
            return r.json()

        def rep(ids):                                                    # the frame Review shows for that group
            e = c.get(f"{API}/encounters", params={"task": t["id"], "concept": OWL}).json()
            return next(b["rep"] for x in e["encounters"] for b in x["bursts"] if set(b["asset_ids"]) == set(ids))

        one, two = picks(1), picks(2)
        assert one["species"] == 1 and len(one["asset_ids"]) == 1
        assert set(two["asset_ids"]) == {rep(a[:5]), a[5]}                # one frame of each group, not two of the burst
        assert set(picks(4)["asset_ids"]) == set(two["asset_ids"])        # only two groups: no second frame of a burst
        c.post(f"{API}/marks", json={"asset_ids": [a[5]], "use": "reject"})
        c.post(f"{API}/marks", json={"asset_ids": [a[2]], "stars": 5})
        assert picks(4)["asset_ids"] == [a[2]]                            # the rejected single is out; a star leads its group
        assert c.get(f"{API}/tasks/{t['id']}/picks", params={"per_species": 0}).status_code == 400
