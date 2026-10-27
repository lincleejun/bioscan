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
    assert again.one("SELECT value FROM meta WHERE key = 'schema_version'")["value"] == "2"
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
