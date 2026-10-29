"""Subjects and identifications (ADR 0002, contract revision 4): per-box species claims with ids that outlive
reruns, a person's claim over the model's, and the schema 1 -> 2 backfill from stored raw:identify results."""
from __future__ import annotations

from core_fixtures import *  # noqa: F403 - fixtures (env, fakes)
from core_fixtures import create_task, make_jpg, owl_box, wait_task

from bioscan.gardener import identities
from bioscan.gardener import store as st

API = "/api/v1"
WREN = dict(scientific="Nannus pacificus", common="Pacific Wren", concept="avibase:avibase-CF5B7A9D")


def asset(c, store, name="a.jpg"):
    aid = st.new_id("a")
    c.execute("INSERT INTO assets (id, media, pair_key, primary_path, created_at, updated_at) "
              "VALUES (?, 'photo', ?, ?, ?, ?)", (aid, name, f"/x/{name}", st.now(), st.now()))
    return aid


def test_a_rerun_box_that_overlaps_is_the_same_subject_and_an_unseen_one_retires(tmp_path):
    store = st.Store(tmp_path / "d")
    with store.tx() as c:
        aid = asset(c, store)
        assert identities.ingest(c, aid, "r1", "v1", {"boxes": [owl_box(), owl_box((0.6, 0.6, 0.9, 0.9), **WREN)]}) == 2
        first = identities.subjects(c, [aid])[aid]
        assert [s["current"]["concept"] for s in first] == ["avibase:test-asio", "avibase:avibase-CF5B7A9D"]
        identities.ingest(c, aid, "r2", "v2", {"boxes": [owl_box((0.12, 0.1, 0.52, 0.5), p=0.9)]})
        again = identities.subjects(c, [aid])[aid]
    assert [s["id"] for s in again] == [first[0]["id"]]                 # same owl; the wren box is gone and retired
    assert again[0]["current"]["p"] == 0.9 and again[0]["current"]["run_id"] == "r2"


def test_a_person_identification_wins_and_keeps_its_subject_when_the_detector_loses_it(tmp_path):
    store = st.Store(tmp_path / "d")
    with store.tx() as c:
        aid = asset(c, store)
        identities.ingest(c, aid, "r1", "v1", {"boxes": [owl_box()]})
        (s,) = identities.subjects(c, [aid])[aid]
        c.execute("INSERT INTO identifications (subject_id, asset_id, concept, taxon, common, level, source, batch_id, "
                  "actor, created_at) VALUES (?, ?, ?, ?, ?, 'species', 'user', 'b1', 'ui', ?)",
                  (s["id"], aid, WREN["concept"], WREN["scientific"], WREN["common"], st.now()))
        identities.ingest(c, aid, "r2", "v2", {"boxes": []})
        (kept,) = identities.subjects(c, [aid])[aid]
    assert kept["id"] == s["id"] and kept["human"] and kept["current"]["concept"] == WREN["concept"]
    assert kept["ai"]["concept"] == "avibase:test-asio"                  # the model's claim stays visible


def test_genus_level_and_species_off_claim_no_concept(tmp_path):
    store = st.Store(tmp_path / "d")
    with store.tx() as c:
        aid = asset(c, store)
        no_species = {k: v for k, v in owl_box((0.6, 0.6, 0.9, 0.9)).items() if k != "species"}
        assert identities.ingest(c, aid, "r1", "v1", {"boxes": [owl_box(level="genus"), no_species]}) == 1
        genus, box = identities.subjects(c, [aid])[aid]
    assert genus["current"]["concept"] is None and genus["current"]["taxon"] == "Megascops"
    assert genus["current"]["candidates"][0]["concept"] == "avibase:test-asio"
    assert box["current"] is None


def test_schema_1_databases_are_backfilled_from_raw_identify(tmp_path):
    store = st.Store(tmp_path / "d")
    with store.tx() as c:
        aid = asset(c, store)
        c.execute("INSERT INTO results (asset_id, run_id, capability, status, version, data, created_at) "
                  "VALUES (?, 'r1', 'raw:identify', 'done', 'v1', ?, ?)", (aid, st.dumps({"boxes": [owl_box()]}), st.now()))
        c.execute("UPDATE meta SET value = '1' WHERE key = 'schema_version'")
    store.close()
    again = st.Store(tmp_path / "d")
    assert again.one("SELECT value FROM meta WHERE key = 'schema_version'")["value"] == str(st.SCHEMA_VERSION)
    (s,) = identities.subjects(again.conn(), [aid])[aid]
    assert s["current"]["concept"] == "avibase:test-asio" and s["current"]["run_id"] == "r1"


def test_a_two_species_photo_shows_both_subjects_in_the_asset_detail(env, fakes, tmp_path):
    src = tmp_path / "card"
    make_jpg(src / "IMG_1.jpg")
    fakes.boxes["IMG_1.jpg"] = [owl_box(), owl_box((0.6, 0.6, 0.9, 0.9), **WREN)]
    with env.client() as c:
        t = wait_task(c, create_task(c, [str(src)])["id"])
        (item,) = c.get(f"{API}/assets", params={"task": t["id"]}).json()["items"]
        subjects = c.get(f"{API}/assets/{item['id']}").json()["subjects"]
    assert [(s["current"]["common"], s["origin"], s["human"]) for s in subjects] == \
        [("Eastern Screech-Owl", "ai", False), ("Pacific Wren", "ai", False)]


def burst_of_three(env, fakes, tmp_path, c):
    src = tmp_path / "card"
    for i in range(3):
        make_jpg(src / f"IMG_{i}.jpg", when=1_700_000_000 + i)
    t = wait_task(c, create_task(c, [str(src)])["id"])
    items = sorted(c.get(f"{API}/assets", params={"task": t["id"]}).json()["items"], key=lambda i: i["name"])
    assert len({i["burst"]["id"] for i in items}) == 1                  # the fake reducer: one burst per task
    return t, [c.get(f"{API}/assets/{i['id']}").json()["subjects"][0]["id"] for i in items], [i["id"] for i in items]


def currents(c, aids):
    return [(s["current"]["common"], s["current"]["source"]) for a in aids
            for s in c.get(f"{API}/assets/{a}").json()["subjects"]]


def test_correcting_one_frame_of_a_burst_corrects_the_burst_and_undo_puts_the_model_back(env, fakes, tmp_path):
    with env.client() as c:
        _t, sids, aids = burst_of_three(env, fakes, tmp_path, c)
        body = {"subject_ids": [sids[0]], "claim": {"level": "species", "taxon": WREN["scientific"], **WREN},
                "scope": "burst"}
        preview = c.post(f"{API}/identifications", json=body | {"dry_run": True}).json()
        assert preview["batch_id"] is None and [x["via"] for x in preview["changed"]] == ["target", "burst", "burst"]
        assert currents(c, aids) == [("Eastern Screech-Owl", "ai")] * 3        # a dry run writes nothing
        done = c.post(f"{API}/identifications", json=body).json()
        assert currents(c, aids) == [("Pacific Wren", "user")] * 3
        tiles = c.get(f"{API}/assets", params={"concept": WREN["concept"]}).json()["items"]
        assert [(i["species"]["common"], i["species"]["confirmed"]) for i in tiles] == [("Pacific Wren", True)] * 3
        assert c.post(f"{API}/identifications/undo", json={"batch_id": done["batch_id"]}).json() == {"undone": 3}
        assert currents(c, aids) == [("Eastern Screech-Owl", "ai")] * 3


def test_a_burst_claim_skips_frames_a_person_already_identified_and_undo_refuses_after_a_later_edit(env, fakes, tmp_path):
    with env.client() as c:
        _t, sids, aids = burst_of_three(env, fakes, tmp_path, c)
        wren = {"level": "species", "taxon": WREN["scientific"], **WREN}
        own = c.post(f"{API}/identifications", json={"subject_ids": [sids[2]], "claim": wren}).json()
        accept = c.post(f"{API}/identifications", json={"subject_ids": [sids[0]], "claim": {"accept": True},
                                                        "scope": "burst"}).json()
        assert [x["subject_id"] for x in accept["changed"]] == sids[:2]
        assert accept["skipped"] == [{"subject_id": sids[2], "asset_id": aids[2], "reason": "human"}]
        assert currents(c, aids) == [("Eastern Screech-Owl", "user")] * 2 + [("Pacific Wren", "user")]
        c.post(f"{API}/identifications", json={"subject_ids": [sids[2]], "claim": {"level": "unconfirmed"}})
        r = c.post(f"{API}/identifications/undo", json={"batch_id": own["batch_id"]})
        assert r.status_code == 409 and r.json()["subjects"] == [sids[2]]


def test_a_rescan_keeps_a_persons_identification(env, fakes, tmp_path):
    with env.client() as c:
        t, sids, aids = burst_of_three(env, fakes, tmp_path, c)
        c.post(f"{API}/identifications", json={"subject_ids": [sids[1]], "claim": {"level": "genus", "taxon": "Nannus"}})
        fakes.version = "fake2"
        c.post(f"{API}/tasks/{t['id']}/rescan", json={"capabilities": ["species"]})
        wait_task(c, t["id"])
        (s,) = c.get(f"{API}/assets/{aids[1]}").json()["subjects"]
    assert s["id"] == sids[1] and s["human"] and (s["current"]["level"], s["current"]["taxon"]) == ("genus", "Nannus")
    assert s["ai"]["version"] == "identify@fake2"                        # the rescan's claim is recorded, not applied


def test_a_drawn_box_becomes_a_subject_and_bad_claims_are_refused(env, fakes, tmp_path):
    with env.client() as c:
        _t, sids, aids = burst_of_three(env, fakes, tmp_path, c)
        r = c.post(f"{API}/identifications", json={"new_subjects": [{"asset_id": aids[0], "xyxy": [0.6, 0.6, 0.8, 0.9]}],
                                                   "claim": {"level": "species", "taxon": WREN["scientific"], **WREN}})
        assert r.status_code == 200 and r.json()["changed"][0]["via"] == "target"
        drawn = c.get(f"{API}/assets/{aids[0]}").json()["subjects"][-1]
        assert drawn["origin"] == "user" and drawn["current"]["common"] == "Pacific Wren"
        assert c.post(f"{API}/identifications/undo", json={"batch_id": r.json()["batch_id"]}).status_code == 200
        assert [s["origin"] for s in c.get(f"{API}/assets/{aids[0]}").json()["subjects"]] == ["ai"]   # the drawn box goes too
        for bad in ({"subject_ids": [sids[0]], "claim": {"level": "species", "taxon": "X"}},           # no concept
                    {"subject_ids": [sids[0]], "claim": {"level": "kingdom"}},
                    {"new_subjects": [{"asset_id": aids[0], "xyxy": [0.5, 0.5, 0.4, 0.9]}], "claim": {"accept": True}},
                    {"claim": {"accept": True}}):
            assert c.post(f"{API}/identifications", json=bad).status_code == 400, bad
        assert c.post(f"{API}/identifications", json={"subject_ids": ["s_nope"], "claim": {"accept": True}}).status_code == 404


def task_of(c, tmp_path, name, files, fakes, boxes, when=1_700_000_000):
    src = tmp_path / name
    for i, f in enumerate(files):
        make_jpg(src / f, when=when + i)
        fakes.boxes[f] = boxes[i]
    return wait_task(c, create_task(c, [str(src)])["id"])


def test_species_cards_one_per_concept_across_tasks_single_photo_species_and_two_species_photos(env, fakes, tmp_path):
    three = owl_box()
    three["species"]["top"] += [{"scientific": "Megascops kennicottii", "concept": "avibase:test-kenn",
                                 "common": "Western Screech-Owl", "posterior": 0.1},
                                {"scientific": "Otus scops", "concept": "avibase:test-scops", "common": "Scops Owl",
                                 "posterior": 0.05}]
    with env.client() as c:
        t1 = task_of(c, tmp_path, "trip1", ["A1.jpg", "A2.jpg"], fakes,
                     [[three], [owl_box(), owl_box((0.6, 0.6, 0.9, 0.9), **WREN)]])
        t2 = task_of(c, tmp_path, "trip2", ["B1.jpg", "B2.jpg"], fakes, [[owl_box()], [owl_box(level="genus")]],
                     when=1_710_000_000)
        lib = c.get(f"{API}/species").json()
        assert lib["confirmed_total"] == 0 and lib["species"] == []                 # nothing a person confirmed yet
        cards = {k["common"]: k for k in lib["to_confirm"]}
        assert sorted(cards) == ["Eastern Screech-Owl", "Pacific Wren"]             # top-3 runners-up make no cards
        assert (cards["Eastern Screech-Owl"]["photos"], cards["Pacific Wren"]["photos"]) == (3, 1)
        assert cards["Pacific Wren"]["only_one"] and cards["Pacific Wren"]["kind"] == "bird"
        assert lib["unresolved"] == {"genus_family": 1, "unconfirmed": 0}
        a2 = next(i for i in c.get(f"{API}/assets", params={"task": t1["id"]}).json()["items"] if i["name"] == "A2.jpg")
        wren_sid = c.get(f"{API}/assets/{a2['id']}").json()["subjects"][1]["id"]
        c.post(f"{API}/identifications", json={"subject_ids": [wren_sid], "claim": {"accept": True}})
        lib = c.get(f"{API}/species").json()
        assert lib["confirmed_total"] == 1 and [k["common"] for k in lib["species"]] == ["Pacific Wren"]
        assert lib["species"][0]["confirmed"] == 1 and lib["species"][0]["cover_id"] == a2["id"]
        only = c.get(f"{API}/species", params={"task": t2["id"]}).json()
        assert [k["photos"] for k in only["to_confirm"]] == [1] and only["unresolved"]["genus_family"] == 1
        names = lambda **q: sorted(i["name"] for i in c.get(f"{API}/assets", params=q).json()["items"])  # noqa: E731
        assert names(concept="avibase:test-asio") == ["A1.jpg", "A2.jpg", "B1.jpg"]
        assert names(concept=WREN["concept"], id_state="confirmed") == ["A2.jpg"]
        assert names(concept="avibase:test-asio", id_state="confirmed") == []
        assert names(id_state="unresolved") == ["B2.jpg"]
        c.patch(f"{API}/species/{WREN['concept']}", json={"hidden": True})       # hiding only changes the display
        lib = c.get(f"{API}/species").json()
        assert lib["confirmed_total"] == 1 and lib["species"] == []


def test_hiding_a_card_and_pinning_its_cover_touch_nothing_else(env, fakes, tmp_path):
    with env.client() as c:
        t = task_of(c, tmp_path, "trip", ["A1.jpg", "A2.jpg"], fakes, [[owl_box()], [owl_box()]])
        a1, a2 = sorted((i["name"], i["id"]) for i in c.get(f"{API}/assets", params={"task": t["id"]}).json()["items"])
        owl = "avibase:test-asio"
        assert c.patch(f"{API}/species/{owl}", json={"cover_asset_id": a1[1]}).json()["cover_pinned"] is True
        assert c.patch(f"{API}/species/{owl}", json={"hidden": True}).json()["hidden"] is True
        assert c.get(f"{API}/species").json()["to_confirm"] == []
        hidden = c.get(f"{API}/species", params={"hidden": "1"}).json()["to_confirm"]
        assert [(k["cover_id"], k["photos"]) for k in hidden] == [(a1[1], 2)]
        assert len(c.get(f"{API}/assets", params={"concept": owl}).json()["items"]) == 2
        assert c.patch(f"{API}/species/{owl}", json={"cover_asset_id": "a_other"}).status_code == 400
        assert c.patch(f"{API}/species/avibase:none", json={"hidden": True}).status_code == 404
        assert c.get(f"{API}/species", params={"hidden": "1"}).json()["to_confirm"][0]["concept"] == owl


def lists_dir(tmp_path):
    """A tiny avilist/ + mdd/ + names/synonyms.csv in the v2026 layout."""
    import csv

    root = tmp_path / "lists"
    for sub, header, rows in (
            ("avilist", ["RANK", "ORDER", "FAMILY", "SCIENTIFIC NAME", "ENGLISH NAME: AVILIST", "AVIBASE ID"],
             [["species", "Strigiformes", "Strigidae", "Megascops asio", "Eastern Screech-Owl", "test-asio"],
              ["species", "Passeriformes", "Troglodytidae", "Nannus pacificus", "Pacific Wren", "avibase-CF5B7A9D"],
              ["species", "Accipitriformes", "Accipitridae", "Aquila chrysaetos", "Golden Eagle", "test-aquila"]]),
            ("mdd", ["sciName", "id", "mainCommonName", "otherCommonNames", "order", "family", "genus", "specificEpithet"],
             [["Alces_alces", "1006285", "Moose", "Elk|Eurasian Elk", "Artiodactyla", "Cervidae", "Alces", "alces"]]),
            ("names", ["avilist_scientific", "alias", "source", "note"],
             [["Nannus pacificus", "Troglodytes pacificus", "birdnet", "renamed in v2026"]])):
        (root / sub).mkdir(parents=True)
        with open(root / sub / ("synonyms.csv" if sub == "names" else f"{sub}.csv"), "w", newline="") as f:
            csv.writer(f).writerows([header, *rows])
    return root


def test_names_resolve_scientific_english_replaced_and_chinese_names_to_concepts(env, fakes, tmp_path):
    (env.data).mkdir(parents=True, exist_ok=True)
    (env.data / "names-zh.csv").write_text("scientific,zh\nAquila chrysaetos,金雕\nNannus pacificus,太平洋鹪鹩\n"
                                          "Nobody nowhere,无名\n", encoding="utf-8")
    with env.client(name_lists=lists_dir(tmp_path)) as c:
        task_of(c, tmp_path, "trip", ["A1.jpg"], fakes, [[owl_box()]])
        q = lambda s: [(x["concept"], x["matched"]) for x in c.get(f"{API}/names", params={"q": s}).json()]  # noqa: E731
        assert q("金雕") == [("avibase:test-aquila", "金雕")]
        assert q("troglodytes pacificus") == [("avibase:avibase-CF5B7A9D", "Troglodytes pacificus")]  # the replaced name
        assert q("Eurasian elk") == [("mdd:1006285", "Eurasian Elk")]
        (owl,) = c.get(f"{API}/names", params={"q": "screech"}).json()
        assert (owl["scientific"], owl["common"], owl["zh"], owl["kind"], owl["photos"]) == \
            ("Megascops asio", "Eastern Screech-Owl", None, "bird", 1)
        wren = c.get(f"{API}/names", params={"q": "太平洋"}).json()[0]
        assert (wren["common"], wren["zh"], wren["photos"]) == ("Pacific Wren", "太平洋鹪鹩", 0)
        assert c.get(f"{API}/names", params={"q": " "}).json() == []


def test_the_species_filter_follows_current_identifications_and_smart_rules_use_it(env, fakes, tmp_path):
    with env.client(name_lists=lists_dir(tmp_path)) as c:
        t = task_of(c, tmp_path, "trip", ["A1.jpg", "A2.jpg"], fakes, [[owl_box()], [owl_box()]])
        names = lambda **q: sorted(i["name"] for i in c.get(f"{API}/assets", params=q).json()["items"])  # noqa: E731
        assert names(species="screech-owl") == ["A1.jpg", "A2.jpg"]
        smart = c.post(f"{API}/collections", json={"name": "鹪鹩", "kind": "smart", "rule": {"species": "Troglodytes pacificus"},
                                                   "task_id": t["id"]}).json()
        a1 = next(i["id"] for i in c.get(f"{API}/assets", params={"task": t["id"]}).json()["items"] if i["name"] == "A1.jpg")
        sid = c.get(f"{API}/assets/{a1}").json()["subjects"][0]["id"]
        c.post(f"{API}/identifications", json={"subject_ids": [sid], "claim": {"level": "species", "taxon": WREN["scientific"],
                                                                              **WREN}})
        assert names(species="screech-owl") == ["A2.jpg"] and names(species="pacific wren") == ["A1.jpg"]
        assert names(collection=smart["id"]) == ["A1.jpg"]            # the rule names the wren by its old name


def test_the_name_index_rebuilds_only_when_its_inputs_change(tmp_path):
    from bioscan.gardener import taxa

    store, lists = st.Store(tmp_path / "d"), lists_dir(tmp_path)
    assert taxa.sync(store, lists) and not taxa.sync(store, lists)
    (tmp_path / "d" / "names-zh.csv").write_text("scientific,zh\nMegascops asio,东美角鸮\n", encoding="utf-8")
    assert taxa.sync(store, lists)
    assert [x["zh"] for x in taxa.search(store.conn(), "东美")] == ["东美角鸮"]


def test_the_web_ui_revalidates_so_an_upgrade_is_never_served_stale(env):
    with env.client() as c:
        r = c.get("/app.js")
        assert r.status_code == 200 and r.headers["cache-control"] == "no-cache" and r.headers.get("etag")
        assert "cache-control" not in c.get(f"{API}/health").headers


def test_a_renamed_species_shows_its_current_names_and_keeps_what_was_said(env, fakes, tmp_path):
    (env.data).mkdir(parents=True, exist_ok=True)
    (env.data / "names-zh.csv").write_text("scientific,zh\nNannus pacificus,太平洋鹪鹩\n", encoding="utf-8")
    old = owl_box(scientific="Troglodytes pacificus", common="Pacific Wren", concept=WREN["concept"])   # a v2025 answer
    with env.client(name_lists=lists_dir(tmp_path)) as c:
        t = task_of(c, tmp_path, "trip", ["A1.jpg"], fakes, [[old]])
        (card,) = c.get(f"{API}/species").json()["to_confirm"]
        (item,) = c.get(f"{API}/assets", params={"task": t["id"]}).json()["items"]
        (s,) = c.get(f"{API}/assets/{item['id']}").json()["subjects"]
    assert (card["taxon"], card["zh"]) == ("Nannus pacificus", "太平洋鹪鹩")
    assert (item["species"]["taxon"], item["species"]["zh"]) == ("Nannus pacificus", "太平洋鹪鹩")
    assert s["current"]["taxon"] == "Nannus pacificus" and s["current"]["said"]["taxon"] == "Troglodytes pacificus"
