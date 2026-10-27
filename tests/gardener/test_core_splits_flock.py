"""List splits reopen a person's claim (S7 part 2) and flocks are not named box by box (contract revision 4)."""
from __future__ import annotations

import csv
from types import SimpleNamespace

import pytest
from core_fixtures import *  # noqa: F403 - fixtures (env, fakes)
from core_fixtures import owl_box, wait_until
from test_core_identities import API, asset, lists_dir, task_of

from bioscan.gardener import identities, taxa
from bioscan.gardener import store as st

WILLET = "avibase:avibase-E7A14E91"            # v2025 Willet; AviList v2026 split it and retired the id
SALTMARSH, PRAIRIE = "avibase:avibase-6D372C9F", "avibase:avibase-750C014A"


def split_lists(tmp_path):
    """The tiny v2026 lists plus the two Willet daughters and a split table naming them."""
    root = lists_dir(tmp_path)
    with open(root / "avilist" / "avilist.csv", "a", newline="") as f:
        csv.writer(f).writerows([["species", "Charadriiformes", "Scolopacidae", "Tringa semipalmata", "Saltmarsh Willet",
                                  SALTMARSH.split(":")[1]],
                                 ["species", "Charadriiformes", "Scolopacidae", "Tringa inornata", "Prairie Willet",
                                  PRAIRIE.split(":")[1]]])
    with open(root / "names" / taxa.SPLITS_FILE, "w", newline="") as f:
        csv.writer(f).writerows([["list_version", "parent_scientific", "parent_concept", "concept", "scientific", "common"],
                                 ["avilist-2026", "Tringa semipalmata", WILLET, SALTMARSH, "Tringa semipalmata",
                                  "Saltmarsh Willet"],
                                 ["avilist-2026", "Tringa semipalmata", WILLET, PRAIRIE, "Tringa inornata",
                                  "Prairie Willet"]])
    return root


def test_a_split_reopens_a_persons_claim_until_they_pick_a_daughter_and_the_batch_undoes(env, fakes, tmp_path):
    willet = owl_box(scientific="Tringa semipalmata", common="Willet", concept=WILLET)
    with env.client() as c:                                     # confirmed under v2025
        t = task_of(c, tmp_path, "trip", ["A1.jpg", "A2.jpg"], fakes, [[willet], [willet]])
        items = {i["name"]: i["id"] for i in c.get(f"{API}/assets", params={"task": t["id"]}).json()["items"]}
        sid = c.get(f"{API}/assets/{items['A1.jpg']}").json()["subjects"][0]["id"]
        c.post(f"{API}/identifications", json={"subject_ids": [sid], "claim": {"accept": True}})
        assert c.get(f"{API}/species").json()["confirmed_total"] == 1
    lists = split_lists(tmp_path)
    with env.client(name_lists=lists) as c:                     # the v2026 list arrives
        sp = c.get(f"{API}/species").json()
        (s,) = c.get(f"{API}/assets/{items['A1.jpg']}").json()["subjects"]
        names = lambda **q: [i["name"] for i in c.get(f"{API}/assets", params=q).json()["items"]]  # noqa: E731
        assert sp["confirmed_total"] == 0 and sp["to_reconfirm"] == 1
        assert [k["concept"] for k in sp["to_confirm"]] == [WILLET]         # A2: the model's claim, untouched
        cur = s["current"]
        assert (cur["concept"], cur["qualifier"], cur["actor"], cur["reason"]) == (None, "slash", "system",
                                                                                  "split:avilist-2026")
        assert [x["concept"] for x in cur["candidates"]] == [SALTMARSH, PRAIRIE] and s["human"]
        assert names(id_state="reconfirm") == ["A1.jpg"] and names(id_state="confirmed") == []
        assert c.post(f"{API}/identifications/undo", json={"batch_id": cur["batch_id"]}).status_code == 200
        assert c.get(f"{API}/species").json()["confirmed_total"] == 1
    with env.client(name_lists=lists) as c:                     # applied once: a restart does not reopen it
        assert c.get(f"{API}/species").json()["to_reconfirm"] == 0
        c.post(f"{API}/identifications", json={"subject_ids": [sid], "claim": {
            "level": "species", "concept": PRAIRIE, "taxon": "Tringa inornata"}})
        (card,) = c.get(f"{API}/species").json()["species"]
    assert (card["concept"], card["common"]) == (PRAIRIE, "Prairie Willet")


def test_a_split_waits_until_its_list_is_loaded(tmp_path):
    lists = split_lists(tmp_path)
    (lists / "avilist" / "avilist.csv").write_text("RANK,ORDER,FAMILY,SCIENTIFIC NAME,ENGLISH NAME: AVILIST,AVIBASE ID\n")
    store = st.Store(tmp_path / "d")
    taxa.sync(store, lists)
    assert store.one("SELECT value FROM meta WHERE key = 'splits_applied'") is None


def swift(xyxy, scientific="Apus apus", concept="avibase:test-apus"):
    return owl_box(xyxy, scientific=scientific, common=scientific, concept=concept)


def test_a_flock_is_claimed_at_its_shared_genus_and_a_few_birds_are_still_named(tmp_path):
    store = st.Store(tmp_path / "d")
    boxes = [swift((0.1 * i, 0.1, 0.1 * i + 0.05, 0.2)) for i in range(identities.FLOCK_MIN - 1)]
    boxes.append(swift((0.9, 0.1, 0.95, 0.2), "Apus pacificus", "avibase:test-pacificus"))
    with store.tx() as c:
        few, many, mixed = asset(c, store, "few.jpg"), asset(c, store, "many.jpg"), asset(c, store, "mixed.jpg")
        identities.ingest(c, few, "r1", "v1", {"boxes": boxes[1:]})
        identities.ingest(c, many, "r1", "v1", {"boxes": boxes})
        identities.ingest(c, mixed, "r1", "v1", {"boxes": boxes + [swift((0.5, 0.5, 0.6, 0.6), "Hirundo rustica")]})
        out = identities.subjects(c, [few, many, mixed])
    assert {s["current"]["concept"] for s in out[few]} == {"avibase:test-apus", "avibase:test-pacificus"}
    cur = [s["current"] for s in out[many]]
    assert {(x["level"], x["taxon"], x["concept"], x["reason"]) for x in cur} == {("genus", "Apus", None, "flock")}
    assert cur[-1]["candidates"][0]["concept"] == "avibase:test-pacificus"     # the per-box answer is kept
    assert {s["current"]["level"] for s in out[mixed]} == {"unconfirmed"}       # no shared genus or family


def test_a_flock_leaves_a_persons_claim_and_other_kinds_alone(tmp_path):
    store = st.Store(tmp_path / "d")
    boxes = [swift((0.1 * i, 0.1, 0.1 * i + 0.05, 0.2)) for i in range(identities.FLOCK_MIN)]
    deer = owl_box((0.5, 0.5, 0.9, 0.9), scientific="Odocoileus hemionus", concept="mdd:1")
    deer["kind"] = "mammal"
    with store.tx() as c:
        aid = asset(c, store)
        identities.ingest(c, aid, "r1", "v1", {"boxes": boxes[:1]})
        first = identities.subjects(c, [aid])[aid][0]["id"]
        identities.identify(c, [first], [], {"accept": True}, scope="subject", actor="ui", reason=None, dry_run=False)
        identities.ingest(c, aid, "r2", "v1", {"boxes": boxes + [deer]})
        out = identities.subjects(c, [aid])[aid]
    person = next(s for s in out if s["id"] == first)
    assert person["current"]["concept"] == "avibase:test-apus" and person["ai"]["reason"] == "flock"
    assert next(s for s in out if s["kind"] == "mammal")["current"]["concept"] == "mdd:1"


# ---- a split settled by place (contract revision 7D) -----------------------------------------------------------
DAUGHTERS = {WILLET: [{"concept": SALTMARSH, "scientific": "Tringa semipalmata", "common": "Saltmarsh Willet"},
                      {"concept": PRAIRIE, "scientific": "Tringa inornata", "common": "Prairie Willet"}]}


def reopened(store, lat=40.7, lon=-74.0):
    """A photo (with GPS unless lat is None) whose confirmed Willet a v2026 split reopened."""
    with store.tx() as c:
        aid = asset(c, store)
        c.execute("UPDATE assets SET lat = ?, lon = ?, taken_at = '2026-05-01T08:00:00' WHERE id = ?", (lat, lon, aid))
        identities.ingest(c, aid, "r1", "v1", {"boxes": [owl_box(scientific="Tringa semipalmata", concept=WILLET)]})
        sid = identities.subjects(c, [aid])[aid][0]["id"]
        identities.identify(c, [sid], [], {"accept": True}, scope="subject", actor="ui", reason=None, dry_run=False)
        identities.reconfirm(c, "avilist-2026", DAUGHTERS)
    return aid


def prior(values, calls=None):
    def p_geo_of(concepts, lat, lon, taken_at):
        if calls is not None:
            calls.append((tuple(concepts), lat, lon, taken_at))
        return {k: values[k] for k in concepts if k in values}
    return p_geo_of


def current(store, aid):
    with store.tx() as c:
        return identities.subjects(c, [aid])[aid][0]["current"]


def test_a_place_where_only_one_daughter_lives_settles_the_split_in_its_own_undoable_batch(tmp_path):
    store = st.Store(tmp_path / "d")
    aid, calls = reopened(store), []
    with store.tx() as c:
        out = identities.resolve_splits(c, prior({SALTMARSH: 0.31, PRAIRIE: 0.004}, calls))
    assert calls == [((SALTMARSH, PRAIRIE), 40.7, -74.0, "2026-05-01T08:00:00")]
    cur = current(store, aid)
    assert (cur["concept"], cur["level"], cur["qualifier"], cur["source"], cur["actor"], cur["reason"]) == (
        SALTMARSH, "species", None, "user", "system", "split-geo:avilist-2026")
    assert [x["p"] for x in cur["candidates"]] == [0.31, 0.004] and out["batch_id"] == cur["batch_id"]
    with store.tx() as c:
        assert [k["concept"] for k in identities.species(c)["species"]] == [SALTMARSH]
        identities.undo(c, out["batch_id"])                                    # back to the person
        assert identities.resolve_splits(c, prior({SALTMARSH: 0.31, PRAIRIE: 0.004}))["changed"] == []
    assert current(store, aid)["qualifier"] == "slash"                          # tried once: an undo sticks


@pytest.mark.parametrize("values", [{SALTMARSH: 0.31, PRAIRIE: 0.02},        # the other is not clearly absent
                                    {SALTMARSH: 0.31, PRAIRIE: 0.2},         # both live here
                                    {SALTMARSH: 0.04, PRAIRIE: 0.0},         # the one is below the in-range bar
                                    {SALTMARSH: 0.31}])                      # the prior cannot speak for one
def test_an_unclear_place_leaves_the_split_for_the_person(tmp_path, values):
    store = st.Store(tmp_path / "d")
    aid = reopened(store)
    with store.tx() as c:
        assert identities.resolve_splits(c, prior(values)) == {"batch_id": None, "changed": []}
    assert current(store, aid)["qualifier"] == "slash"


def test_a_photo_without_gps_never_asks_the_prior(tmp_path):
    store = st.Store(tmp_path / "d")
    aid, calls = reopened(store, lat=None), []
    with store.tx() as c:
        assert identities.resolve_splits(c, prior({SALTMARSH: 0.9, PRAIRIE: 0.0}, calls))["changed"] == []
    assert calls == [] and current(store, aid)["qualifier"] == "slash"


def test_a_run_with_the_location_prior_loaded_settles_splits(env, fakes, tmp_path):
    import numpy as np

    concepts = ["avibase:other", SALTMARSH, PRAIRIE]
    bird = SimpleNamespace(p_geo=lambda lat, lon, t: np.array([0.5, 0.3, 0.001]), direct=np.array([True] * 3))
    env.engine.priors, env.engine.names = {"bird": bird}, {"bird": SimpleNamespace(concept=concepts)}
    willet = owl_box(scientific="Tringa semipalmata", common="Willet", concept=WILLET)
    with env.client() as c:
        t = task_of(c, tmp_path, "trip", ["A1.jpg"], fakes, [[willet]])
        (item,) = c.get(f"{API}/assets", params={"task": t["id"]}).json()["items"]
        sid = c.get(f"{API}/assets/{item['id']}").json()["subjects"][0]["id"]
        c.post(f"{API}/identifications", json={"subject_ids": [sid], "claim": {"accept": True}})
    store = st.Store(env.data)
    with store.tx() as c:
        c.execute("UPDATE assets SET lat = 40.7, lon = -74.0 WHERE id = ?", (item["id"],))
        identities.reconfirm(c, "avilist-2026", DAUGHTERS)
    store.close()
    with env.client() as c:
        rid = c.post(f"{API}/tasks/{t['id']}/rescan", json={}).json()["id"]
        wait_until(lambda: c.get(f"{API}/runs/{rid}").json()["status"] in ("completed", "partial"), what="rescan")
        cur = c.get(f"{API}/assets/{item['id']}").json()["subjects"][0]["current"]
    assert (cur["concept"], cur["reason"]) == (SALTMARSH, "split-geo:avilist-2026")
