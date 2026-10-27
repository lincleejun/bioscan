"""Contract revision 5B: the four uses replace Pick/Reject (with the schema 3 migration), "keep this one,
reject similar frames", encounters that survive rescans, and the Smart Collection rule preview."""
from __future__ import annotations

from core_fixtures import *  # noqa: F403 - fixtures (env, fakes)
from core_fixtures import create_task, make_jpg, owl_box, photos, wait_task, wait_until

from bioscan.gardener import store as st

API = "/api/v1"
WREN = dict(scientific="Nannus pacificus", common="Pacific Wren", concept="avibase:avibase-CF5B7A9D")
T0 = 1_700_000_000.0


def ids(c, **q):
    return [i["id"] for i in c.get(f"{API}/assets", params=q | {"limit": 1000, "order": "time"}).json()["items"]]


def scanned(c, src, **kw):
    t = create_task(c, [str(src)], **kw)
    return wait_task(c, t["id"])


def mark(c, asset_ids, **body):
    r = c.post(f"{API}/marks", json={"asset_ids": asset_ids, **body})
    assert r.status_code == 200, r.text
    return r.json()


def test_four_uses_are_global_undoable_and_flag_reads_as_their_older_form(env, tmp_path):
    src = tmp_path / "card"
    photos(src, 4)
    with env.client() as c:
        t = scanned(c, src)
        a, b, x, y = ids(c, task=t["id"])
        assert c.get(f"{API}/assets/{a}").json()["use"] == "pending"
        r = mark(c, [a], use="edit")
        assert r["assets"] == [{"id": a, "use": "edit", "flag": "pick", "stars": 0, "version": 1}]
        assert mark(c, [a], flag="pick")["assets"][0]["use"] == "edit"       # an older client's Pick keeps edit
        mark(c, [b], use="record")
        mark(c, [x], flag="reject")
        assert ids(c, task=t["id"], use="edit") == [a] and ids(c, task=t["id"], use="record") == [b]
        assert ids(c, task=t["id"], use="reject") == [x] and ids(c, task=t["id"], use="pending") == [y]
        assert sorted(ids(c, task=t["id"], flag="pick")) == sorted([a, b])
        batch = mark(c, [a, b], use="pending")["batch_id"]
        assert c.get(f"{API}/assets/{a}").json()["judged"] is False
        assert c.post(f"{API}/marks/undo", json={"batch_id": batch}).json()["undone"] == 2
        assert [c.get(f"{API}/assets/{i}").json()["use"] for i in (a, b)] == ["edit", "record"]
        assert c.post(f"{API}/marks", json={"asset_ids": [a], "use": "maybe"}).status_code == 400
        assert c.post(f"{API}/marks", json={"asset_ids": [a], "use": "edit", "flag": "pick"}).status_code == 400
        smart = c.post(f"{API}/collections", json={"name": "待修图", "kind": "smart", "rule": {"use": "edit"}}).json()
        assert smart["size"] == 1


def test_schema_2_picks_become_records_and_rejects_stay_rejects_with_provenance(env, tmp_path):
    src = tmp_path / "card"
    photos(src, 3)
    with env.client() as c:
        t = scanned(c, src, caps=["meta", "subject", "species"])
        a, b, x = ids(c, task=t["id"])
    sd = st.Store(env.data)
    with sd.tx() as cx:                       # a schema 2 database: flags only, no encounters
        for aid, flag in ((a, "pick"), (b, "reject")):
            cx.execute("INSERT OR REPLACE INTO marks (asset_id, flag, stars, updated_at, version) VALUES (?, ?, 2, ?, 1)",
                       (aid, flag, st.now()))
        cx.execute("DELETE FROM encounter_members")
        cx.execute("DELETE FROM encounters")
        cx.execute("UPDATE meta SET value = '2' WHERE key = 'schema_version'")
    sd.close()
    with env.client() as c:
        got = {i: c.get(f"{API}/assets/{i}").json() for i in (a, b, x)}
        assert [(got[i]["use"], got[i]["flag"], got[i]["stars"]) for i in (a, b, x)] == [
            ("record", "pick", 2), ("reject", "reject", 2), ("pending", None, 0)]
        h = got[a]["mark_history"][-1]
        assert (h["field"], h["new"], h["actor"], h["reason"]) == ("use", "record", "system", "migrate:flag:pick")
        assert len(c.get(f"{API}/encounters", params={"task": t["id"]}).json()["encounters"]) == 1   # placed on start
        assert c.get(f"{API}/health").json()["schema_version"] == st.SCHEMA_VERSION


def test_keep_best_previews_its_reach_skips_protected_and_only_record_frames_and_undoes_as_one(env, fakes, tmp_path):
    src = tmp_path / "card"
    names = photos(src, 5)                    # the fake cull puts every photo of the task in one burst
    fakes.boxes["IMG_0003.jpg"] = [owl_box(**WREN)]
    with env.client() as c:
        t = scanned(c, src)
        keep, protected, p3, wren, p4 = ids(c, task=t["id"])
        assert len(names) == 5
        mark(c, [protected], stars=3)
        r = c.post(f"{API}/marks/keep-best", json={"asset_id": keep, "dry_run": True}).json()
        assert r["dry_run"] and r["batch_id"] is None and sorted(r["reject"]) == sorted([p3, p4])
        assert {(s["id"], s["reason"]) for s in r["skipped"]} == {(protected, "human"), (wren, "only_record")}
        assert next(s for s in r["skipped"] if s["id"] == wren)["concepts"] == [WREN["concept"]]
        assert c.get(f"{API}/assets/{p3}").json()["use"] == "pending"          # a dry run writes nothing
        done = c.post(f"{API}/marks/keep-best", json={"asset_id": keep, "keep_use": "edit", "expect": r["expect"]}).json()
        uses = {i: c.get(f"{API}/assets/{i}").json()["use"] for i in (keep, protected, wren, p3, p4)}
        assert uses == {keep: "edit", protected: "pending", wren: "pending", p3: "reject", p4: "reject"}
        assert c.post(f"{API}/marks/undo", json={"batch_id": done["batch_id"]}).json()["undone"] == 3
        assert all(c.get(f"{API}/assets/{i}").json()["use"] == "pending" for i in (keep, p3, p4))
        stale = c.post(f"{API}/marks/keep-best", json={"asset_id": keep, "expect": r["expect"]})
        assert stale.status_code == 409                                       # versions moved on since the preview
        assert c.post(f"{API}/marks/keep-best", json={"asset_id": "a_missing"}).status_code == 404


def encounter_photos(src):
    """IMG_0/1 owl a minute apart; IMG_2 a wren 200 s later (a short pause, other species); IMG_3 the wren
    much later. Expected encounters: [0, 1], [2], [3]."""
    for i, dt in enumerate((0, 60, 260, 3000)):
        make_jpg(src / f"IMG_{i}.jpg", color=(10 * i, 100, 30), when=T0 + dt)


def listing(c, **q):
    r = c.get(f"{API}/encounters", params=q)
    assert r.status_code == 200, r.text
    return r.json()


def test_encounters_follow_time_gaps_and_species_and_a_persons_split_and_merge_survive_rescans(env, fakes, tmp_path):
    src = tmp_path / "card"
    encounter_photos(src)
    for n in ("IMG_2.jpg", "IMG_3.jpg"):
        fakes.boxes[n] = [owl_box(**WREN)]
    caps = ["meta", "subject", "species"]              # no burst: every photo stands alone inside its encounter
    with env.client() as c:
        t = scanned(c, src, caps=caps)
        p0, p1, p2, p3 = ids(c, task=t["id"])
        es = listing(c, task=t["id"])["encounters"]
        assert [e["asset_ids"] for e in es] == [[p0, p1], [p2], [p3]]
        assert [s["concept"] for s in es[0]["species"]] == ["avibase:test-asio"] and es[0]["photos"] == 2
        assert [b["size"] for b in es[0]["bursts"]] == [1, 1] and len(es[0]["candidates"]) == 2
        assert [e["asset_ids"] for e in listing(c, concept=WREN["concept"])["encounters"]] == [[p2], [p3]]
        merged = c.post(f"{API}/encounters/merge", json={"ids": [es[1]["id"], es[2]["id"]]}).json()["id"]
        split = c.post(f"{API}/encounters/{es[0]['id']}/split", json={"at": p1})
        assert split.status_code == 201
        want = [[p0], [p1], [p2, p3]]
        assert [e["asset_ids"] for e in listing(c, task=t["id"])["encounters"]] == want
        assert c.post(f"{API}/encounters/{es[0]['id']}/split", json={"at": p0}).status_code == 400   # nothing left
        assert c.post(f"{API}/encounters/{es[2]['id']}/split", json={"at": p3}).status_code == 400   # merged away
        run = c.post(f"{API}/tasks/{t['id']}/rescan", json={}).json()
        wait_until(lambda: c.get(f"{API}/runs/{run['id']}").json()["status"] in ("completed", "partial"))
        again = listing(c, task=t["id"])["encounters"]
        assert [e["asset_ids"] for e in again] == want and again[2]["id"] == merged
        more = tmp_path / "more"
        make_jpg(more / "IMG_9.jpg", color=(200, 100, 30), when=T0 + 120)
        r = c.post(f"{API}/tasks/{t['id']}/sources", json={"paths": [str(more)]}).json()
        wait_until(lambda: c.get(f"{API}/runs/{r['id']}").json()["status"] in ("completed", "partial"))
        p9 = next(i for i in ids(c, task=t["id"]) if i not in (p0, p1, p2, p3))
        got = [e["asset_ids"] for e in listing(c, task=t["id"])["encounters"]]
        assert got == [[p0], [p1, p9], [p2, p3]]                  # joins its nearest placed photo, nothing else moves
        assert c.get(f"{API}/encounters").status_code == 400


def test_identify_scope_encounter_reaches_the_other_photos_of_the_encounter_only(env, tmp_path):
    src = tmp_path / "card"
    encounter_photos(src)                     # all owls here: one encounter [0, 1, 2] (a 200 s pause), then [3]
    with env.client() as c:
        t = scanned(c, src, caps=["meta", "subject", "species"])
        p0, p1, p2, p3 = ids(c, task=t["id"])
        (s0,) = c.get(f"{API}/assets/{p0}").json()["subjects"]
        claim = {"level": "species", "concept": WREN["concept"], "taxon": WREN["scientific"]}
        r = c.post(f"{API}/identifications", json={"subject_ids": [s0["id"]], "claim": claim, "scope": "encounter"}).json()
        assert sorted((x["asset_id"], x["via"]) for x in r["changed"]) == sorted([(p0, "target"), (p1, "encounter"), (p2, "encounter")])


def test_rule_preview_reports_hits_and_the_change_against_current_members_without_saving(env, tmp_path):
    src = tmp_path / "card"
    photos(src, 3)
    with env.client() as c:
        t = scanned(c, src)
        a, b, x = ids(c, task=t["id"])
        mark(c, [a], use="record")
        mark(c, [b], use="edit")
        smart = c.post(f"{API}/collections", json={"name": "s", "kind": "smart", "rule": {"use": "record"}}).json()
        r = c.post(f"{API}/collections/{smart['id']}/preview-rule", json={"rule": {"use": "edit"}}).json()
        assert r == {"total": 1, "current": 1, "added_count": 1, "removed_count": 1, "added": [b], "removed": [a]}
        assert c.get(f"{API}/collections/{smart['id']}").json()["rule"] == {"use": "record"}
        assert c.post(f"{API}/collections/{smart['id']}/preview-rule", json={"rule": {"nope": 1}}).status_code == 400
        man = c.post(f"{API}/collections", json={"name": "m", "kind": "manual", "asset_ids": [x]}).json()
        assert c.post(f"{API}/collections/{man['id']}/preview-rule", json={"rule": {}}).status_code == 400
