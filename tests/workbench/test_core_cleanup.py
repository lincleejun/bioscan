"""Cleanup plans (契约修订 5E, PRD §11, AC15, ADR 0003): the rejected assets in scope with every file of each group
and the last-photo-of-a-species impact; running re-checks each item and moves files to the Trash through an
injected function (tests never move a real file out of tmp); moved bytes are recoverable, never "freed"."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from core_fixtures import *  # noqa: F403 - fixtures (env, fakes)
from core_fixtures import create_task, make_jpg, owl_box, wait_task

from gardener.workbench import cleanup, query
from gardener.workbench import store as st

API = "/api/v1"
WREN = dict(scientific="Nannus pacificus", common="Pacific Wren", concept="avibase:wren")


class Bin:
    """A Trash in tmp: records and moves; paths in `fail` raise like a refused move."""

    def __init__(self, root: Path) -> None:
        self.root, self.moved, self.fail = root, [], set()
        root.mkdir()

    def __call__(self, path: str) -> None:
        if path in self.fail:
            raise OSError("permission denied")
        shutil.move(path, self.root / f"{len(self.moved)}-{Path(path).name}")
        self.moved.append(path)


def shoot(c, fakes, tmp_path):
    """IMG_0..2: owls; IMG_3: the only wren. IMG_0 has an XMP sidecar."""
    src = tmp_path / "card"
    for i in range(4):
        make_jpg(src / f"IMG_{i}.jpg", when=1_700_000_000 + 600 * i)   # minutes apart: no burst, so no burst vote on the wren
    (src / "IMG_0.xmp").write_text("<x:xmpmeta/>")
    fakes.boxes["IMG_3.jpg"] = [owl_box(**WREN)]
    t = wait_task(c, create_task(c, [str(src)])["id"])
    items = sorted(c.get(f"{API}/assets", params={"task": t["id"]}).json()["items"], key=lambda i: i["name"])
    return src, [i["id"] for i in items]


def test_a_plan_lists_rejected_groups_with_sidecars_bytes_and_last_species_photos(env, fakes, tmp_path):
    with env.client(trash=Bin(tmp_path / "bin")) as c:
        src, aids = shoot(c, fakes, tmp_path)
        c.post(f"{API}/marks", json={"asset_ids": [aids[0], aids[3]], "flag": "reject"})
        c.post(f"{API}/marks", json={"asset_ids": [aids[1]], "flag": "pick"})
        r = c.post(f"{API}/cleanup-plans", json={})
        assert r.status_code == 201, r.text
        p = r.json()
    assert p["status"] == "planned" and "清空废纸篓后才真正释放" in p["note"]
    assert [i["asset_id"] for i in p["items"]] == [aids[3], aids[0]]           # a species' last photo comes first
    wren, owl = p["items"]
    assert wren["species"][0] == {"concept": "avibase:wren", "taxon": "Nannus pacificus", "common": "Pacific Wren",
                                       "zh": None, "photos": 1, "photos_left": 0, "last_photo": True}
    assert owl["species"][0]["last_photo"] is False and owl["species"][0]["photos_left"] == 2
    assert [(f["path"], f["role"]) for f in owl["files"]] == [(str(src / "IMG_0.jpg"), "jpeg"),
                                                              (str(src / "IMG_0.xmp"), "sidecar")]
    size = os.path.getsize(src / "IMG_0.jpg") + os.path.getsize(src / "IMG_0.xmp")
    assert owl["bytes"] == size
    s = p["summary"]
    assert (s["in_scope"], s["rejected"], s["ready"], s["files"]) == (2, 2, 2, 3)
    assert [x["concept"] for x in s["last_species"]] == ["avibase:wren"]


def test_running_needs_confirm_rechecks_every_item_and_only_moves_to_the_trash(env, fakes, tmp_path):
    trash = Bin(tmp_path / "bin")
    with env.client(trash=trash) as c:
        src, aids = shoot(c, fakes, tmp_path)
        c.post(f"{API}/marks", json={"asset_ids": aids, "flag": "reject"})
        p = c.post(f"{API}/cleanup-plans", json={"asset_ids": aids}).json()
        assert c.post(f"{API}/cleanup-plans/{p['id']}/run", json={}).status_code == 400
        assert trash.moved == []
        c.post(f"{API}/marks", json={"asset_ids": [aids[1]], "flag": "pick"})          # kept after the plan was made
        os.utime(src / "IMG_0.xmp", (1, 1))                                            # a sidecar edited since
        (src / "IMG_2.jpg").unlink()                                                   # gone (or its disk is off)
        r = c.post(f"{API}/cleanup-plans/{p['id']}/run", json={"confirm": True})
        assert r.status_code == 200, r.text
        done = r.json()
        a3 = c.get(f"{API}/assets/{aids[3]}").json()
        a2 = c.get(f"{API}/assets/{aids[2]}").json()
    assert trash.moved == [str(src / "IMG_3.jpg")]
    assert sorted((s["asset_id"], s["reason"]) for s in done["report"]["skipped"]) == \
        sorted([(aids[0], "changed"), (aids[1], "not_rejected"), (aids[2], "offline")])
    rep = done["report"]
    assert rep["trashed_files"] == 1 and rep["trashed_bytes"] == os.path.getsize(trash.root / "0-IMG_3.jpg")
    assert rep["freed_bytes"] == 0 and rep["recoverable"] is True and done["status"] == "partial"
    assert (src / "IMG_1.jpg").exists() and (src / "IMG_0.jpg").exists() and (src / "IMG_0.xmp").exists()
    assert a3["online"] is False and a3["meta"]["trashed"]["plan"] == p["id"]      # the record stays (X4)
    assert a2["id"] == aids[2] and a2["flag"] == "reject"                         # offline is never "deleted"


def test_a_failed_move_is_reported_and_a_rerun_retries_only_what_is_left(env, fakes, tmp_path):
    trash = Bin(tmp_path / "bin")
    with env.client(trash=trash) as c:
        src, aids = shoot(c, fakes, tmp_path)
        c.post(f"{API}/marks", json={"asset_ids": [aids[0]], "flag": "reject"})
        p = c.post(f"{API}/cleanup-plans", json={"collection_id": next(
            x["id"] for x in c.get(f"{API}/collections").json() if x["group_kind"] == "all")}).json()
        assert p["summary"]["rejected"] == 1
        trash.fail.add(str(src / "IMG_0.xmp"))
        first = c.post(f"{API}/cleanup-plans/{p['id']}/run", json={"confirm": True}).json()
        assert [f["path"] for f in first["report"]["failed"]] == [str(src / "IMG_0.xmp")]
        assert first["status"] == "partial"
        trash.fail.clear()
        second = c.post(f"{API}/cleanup-plans/{p['id']}/run", json={"confirm": True}).json()
        listed = c.get(f"{API}/cleanup-plans").json()
    assert trash.moved == [str(src / "IMG_0.jpg"), str(src / "IMG_0.xmp")]
    assert second["status"] == "ran" and second["report"]["failed"] == []
    assert [f["result"] for f in second["items"][0]["files"]] == ["trashed", "trashed"]
    assert [x["id"] for x in listed] == [p["id"]] and "items" not in listed[0]


def test_a_stem_sidecar_shared_with_another_asset_is_left_out(tmp_path):
    """ARW and DNG of one stem are two assets (X1): IMG_1.xmp could be either's, so neither plan takes it;
    IMG_1.ARW.xmp names its file and goes with it."""
    d = tmp_path / "src"
    d.mkdir()
    for n in ("IMG_1.ARW", "IMG_1.DNG", "IMG_1.xmp", "IMG_1.ARW.xmp"):
        (d / n).write_bytes(b"x")
    store = st.Store(tmp_path / "data")
    with store.tx() as c:
        for aid, n in (("a_1", "IMG_1.ARW"), ("a_2", "IMG_1.DNG")):
            c.execute("INSERT INTO assets (id, media, pair_key, primary_path, created_at, updated_at) VALUES "
                      "(?, 'photo', ?, ?, ?, ?)", (aid, n, str(d / n), st.now(), st.now()))
            c.execute("INSERT INTO asset_files (path, asset_id, role, ext, size, mtime_ns, seen_at) VALUES "
                      "(?, ?, 'raw', ?, 1, 1, ?)", (str(d / n), aid, Path(n).suffix, st.now()))
        files = [Path(f["path"]).name for f in cleanup._files(c, "a_1")]
    assert files == ["IMG_1.ARW", "IMG_1.ARW.xmp"]


def test_a_photo_kept_while_the_run_is_going_is_not_moved(env, fakes, tmp_path):
    """Review finding B1: the use is checked again right before each group moves."""
    bin_, seen = tmp_path / "bin", {}
    bin_.mkdir()

    def trash(path):
        if not seen.get("moved"):                                    # while the first photo moves, the person keeps
            other = next(i for i in range(3) if Path(path).name != f"IMG_{i}.jpg")   # another one of the plan
            seen["kept"] = other
            seen["c"].post(f"{API}/marks", json={"asset_ids": [seen["aids"][other]], "use": "record"})
        shutil.move(path, bin_ / Path(path).name)
        seen.setdefault("moved", []).append(Path(path).name)

    with env.client(trash=trash) as c:
        seen["c"] = c
        src, aids = shoot(c, fakes, tmp_path)
        seen["aids"] = aids
        c.post(f"{API}/marks", json={"asset_ids": aids[:3], "use": "reject"})
        p = c.post(f"{API}/cleanup-plans", json={"asset_ids": aids[:3]}).json()
        report = c.post(f"{API}/cleanup-plans/{p['id']}/run", json={"confirm": True}).json()["report"]
    kept = seen["kept"]
    assert f"IMG_{kept}.jpg" not in seen["moved"] and (src / f"IMG_{kept}.jpg").exists()
    assert {"asset_id": aids[kept], "reason": "not_rejected"} in report["skipped"]


def test_the_last_photo_warning_ignores_photos_already_in_the_trash(env, fakes, tmp_path):
    """Review finding B2: same count as the species cards."""
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    with env.client(trash=lambda p: shutil.move(p, bin_ / Path(p).name)) as c:
        src, aids = shoot(c, fakes, tmp_path)
        owls = aids[:3]                                              # IMG_0..2 are the owls
        c.post(f"{API}/marks", json={"asset_ids": owls, "use": "reject"})
        first = c.post(f"{API}/cleanup-plans", json={"asset_ids": owls[:2]}).json()
        c.post(f"{API}/cleanup-plans/{first['id']}/run", json={"confirm": True})
        last = c.post(f"{API}/cleanup-plans", json={"asset_ids": [owls[2]]}).json()
    assert last["items"][0]["species"][0]["last_photo"] is True


def test_a_second_run_of_a_plan_already_running_is_refused(env, fakes, tmp_path):
    with env.client(trash=Bin(tmp_path / "bin")) as c:
        _src, aids = shoot(c, fakes, tmp_path)
        c.post(f"{API}/marks", json={"asset_ids": [aids[0]], "use": "reject"})
        p = c.post(f"{API}/cleanup-plans", json={"asset_ids": [aids[0]]}).json()
        cleanup._RUNNING.add(p["id"])                                # as if a first click were still running
        try:
            assert c.post(f"{API}/cleanup-plans/{p['id']}/run", json={"confirm": True}).status_code == 409
        finally:
            cleanup._RUNNING.discard(p["id"])
        assert c.post(f"{API}/cleanup-plans/{p['id']}/run", json={"confirm": True}).status_code == 200


# ---- 契约修订 10A: a file another asset still uses never goes to the Trash (#131) ----
def _twice(c, tmp_path):
    """The same card imported twice: the second import records the first one's managed copies (5D duplicate), so
    each managed file in lib/ (A.ARW, A.JPG, A.xmp, A.ARW.acr, B.JPG) belongs to two assets."""
    from test_core_imports import card, plan, run, scanned

    first, second, lib = card(tmp_path / "card"), tmp_path / "again", tmp_path / "lib"
    tid, ids1 = scanned(c, first)
    assert run(c, plan(c, tid, lib)["id"])["status"] == "completed"
    shutil.copytree(first, second)
    tid2, ids2 = scanned(c, second)
    assert run(c, plan(c, tid2, lib)["id"])["status"] == "completed"
    return ids1, ids2, next(lib.iterdir()), first, second


def test_rejecting_a_duplicate_never_moves_the_managed_copies_the_kept_photo_uses(env, tmp_path):
    """The P0 of #131: B (and A with its sidecars) are kept in task 1 and rejected in task 2; cleaning task 2 moves
    only the second card's files and shows every shared managed copy as skipped, reason `shared`."""
    trash = Bin(tmp_path / "bin")
    with env.client(trash=trash) as c:
        ids1, ids2, lib, _first, again = _twice(c, tmp_path)
        rej = [ids2["A.JPG"], ids2["B.JPG"]]
        c.post(f"{API}/marks", json={"asset_ids": rej, "use": "reject"})
        p = c.post(f"{API}/cleanup-plans", json={"asset_ids": rej}).json()
        done = c.post(f"{API}/cleanup-plans/{p['id']}/run", json={"confirm": True}).json()
        kept = c.get(f"{API}/assets/{ids1['B.JPG']}").json()
    shared = sorted(Path(f["path"]).name for i in done["items"] for f in i["files"] if f["reason"] == "shared")
    assert shared == ["A.ARW", "A.ARW.acr", "A.JPG", "A.xmp", "B.JPG"]
    assert all(f["path"].startswith(str(lib)) for i in done["items"] for f in i["files"] if f["reason"] == "shared")
    assert sorted(trash.moved) == sorted(str(again / n) for n in ("A.ARW", "A.JPG", "A.xmp", "B.JPG"))
    assert p["summary"]["shared"] == 5 and p["summary"]["files"] == 4 and done["status"] == "ran"
    assert all((lib / n).exists() for n in shared)
    assert str(lib / "B.JPG") in str(kept["managed"])


def test_both_duplicates_rejected_in_one_plan_move_the_shared_copy_once(env, tmp_path):
    trash = Bin(tmp_path / "bin")
    with env.client(trash=trash) as c:
        ids1, ids2, lib, first, again = _twice(c, tmp_path)
        both = [ids1["B.JPG"], ids2["B.JPG"]]
        c.post(f"{API}/marks", json={"asset_ids": both, "use": "reject"})
        p = c.post(f"{API}/cleanup-plans", json={"asset_ids": both}).json()
        done = c.post(f"{API}/cleanup-plans/{p['id']}/run", json={"confirm": True}).json()
    assert sorted(trash.moved) == sorted([str(first / "B.JPG"), str(again / "B.JPG"), str(lib / "B.JPG")])
    assert done["status"] == "ran" and done["report"]["skipped"] == []


def test_a_duplicate_kept_after_planning_keeps_the_shared_copy(env, tmp_path):
    """Run-time re-check: both duplicates were rejected when planned; task 1's is kept before the run."""
    trash = Bin(tmp_path / "bin")
    with env.client(trash=trash) as c:
        ids1, ids2, lib, first, again = _twice(c, tmp_path)
        both = [ids2["B.JPG"], ids1["B.JPG"]]                     # the lib copy is planned under task 2's asset
        c.post(f"{API}/marks", json={"asset_ids": both, "use": "reject"})
        p = c.post(f"{API}/cleanup-plans", json={"asset_ids": both}).json()
        c.post(f"{API}/marks", json={"asset_ids": [ids1["B.JPG"]], "use": "record"})
        done = c.post(f"{API}/cleanup-plans/{p['id']}/run", json={"confirm": True}).json()
    assert trash.moved == [str(again / "B.JPG")] and (lib / "B.JPG").exists() and (first / "B.JPG").exists()
    assert {"asset_id": ids2["B.JPG"], "path": str(lib / "B.JPG"), "reason": "shared"} in done["report"]["skipped"]
    assert done["status"] == "partial"


def test_a_library_scanned_as_a_task_shares_its_files_with_the_imported_asset(env, tmp_path):
    """The source-file half of 10A: lib/B.JPG is a managed copy of the card's B and a source file of the lib scan's B."""
    from test_core_imports import by_name, card, plan, run

    trash = Bin(tmp_path / "bin")
    with env.client(trash=trash) as c:
        src = card(tmp_path / "card")
        t = wait_task(c, create_task(c, [str(src)], caps=[])["id"])
        cb = by_name(c, t["id"])["B.JPG"]
        c.post(f"{API}/marks", json={"asset_ids": [cb], "use": "record"})
        assert run(c, plan(c, t["id"], tmp_path / "lib")["id"])["status"] == "completed"
        lib = next((tmp_path / "lib").iterdir())
        lb = by_name(c, wait_task(c, create_task(c, [str(lib)], caps=[])["id"])["id"])["B.JPG"]
        moved = []
        for reject, keep in ((cb, lb), (lb, cb), (None, None)):
            both = [cb, lb]              # last round: both rejected; the card's asset claims every file of lib's
            c.post(f"{API}/marks", json={"asset_ids": [reject] if reject else both, "use": "reject"})
            if keep:
                c.post(f"{API}/marks", json={"asset_ids": [keep], "use": "record"})
            p = c.post(f"{API}/cleanup-plans", json={"asset_ids": [reject] if reject else both}).json()
            files = {f["path"]: f["reason"] for i in p["items"] for f in i["files"]}
            if reject:
                assert files[str(lib / "B.JPG")] == "shared"
                c.post(f"{API}/marks", json={"asset_ids": [reject], "use": "record"})
            else:
                done = c.post(f"{API}/cleanup-plans/{p['id']}/run", json={"confirm": True}).json()
                moved = trash.moved
    assert sorted(moved) == sorted([str(src / "B.JPG"), str(lib / "B.JPG")]) and done["status"] == "ran"


def test_a_planned_file_that_is_no_longer_a_plain_file_is_reported_as_changed(env, fakes, tmp_path):
    """#131: a planned path still on disk but no longer one of the asset's files (here a dangling link) used to
    raise KeyError in the re-check."""
    trash = Bin(tmp_path / "bin")
    with env.client(trash=trash) as c:
        src, aids = shoot(c, fakes, tmp_path)
        c.post(f"{API}/marks", json={"asset_ids": [aids[1]], "use": "reject"})
        p = c.post(f"{API}/cleanup-plans", json={"asset_ids": [aids[1]]}).json()
        (src / "IMG_1.jpg").unlink()
        (src / "IMG_1.jpg").symlink_to(tmp_path / "nowhere.jpg")
        r = c.post(f"{API}/cleanup-plans/{p['id']}/run", json={"confirm": True})
    assert r.status_code == 200, r.text
    assert trash.moved == [] and r.json()["report"]["skipped"] == [{"asset_id": aids[1], "reason": "changed"}]


def test_the_bin_is_what_a_person_rejected_plus_what_only_the_ai_rejected_and_emptying_it_plans_both(env, fakes, tmp_path, monkeypatch):
    """修订 21: pile=bin / pile=good read the person's mark first and the AI suggestion only where nobody marked the
    photo; 放回 (use record) takes a photo out of the bin for good; emptying turns the AI-only rejects into the
    person's and plans them."""
    import sqlite3

    with env.client(trash=Bin(tmp_path / "bin")) as c:
        _, aids = shoot(c, fakes, tmp_path)
        with sqlite3.connect(env.data / "gardener.sqlite") as db:
            db.execute("DELETE FROM ai_suggestions")
            for a, verdict in zip(aids, ("reject", "reject", "pick", "pick")):
                db.execute("INSERT INTO ai_suggestions (asset_id, run_id, verdict, created_at) VALUES (?, 'r', ?, ?)",
                           (a, verdict, "2026-11-13T00:00:00.000000Z"))

        def pile(name):
            return sorted(i["id"] for i in c.get(f"{API}/assets", params={"pile": name}).json()["items"])
        assert pile("bin") == sorted(aids[:2]) and pile("good") == sorted(aids[2:])
        c.post(f"{API}/marks", json={"asset_ids": [aids[0]], "use": "record"})       # 放回: a person keeps it, an ordinary photo again
        c.post(f"{API}/marks", json={"asset_ids": [aids[2]], "use": "reject"})       # X on a photo the AI liked
        assert pile("bin") == sorted([aids[1], aids[2]]) and pile("good") == [aids[3]]
        c.post(f"{API}/marks", json={"asset_ids": [aids[3]], "use": "record"})       # 完成 keeps it: still the AI's pick
        assert pile("good") == [aids[3]]
        with sqlite3.connect(env.data / "gardener.sqlite") as db:     # no picks: the best-ranked frames, up to the share
            db.execute("UPDATE ai_suggestions SET verdict = 'none', rank = 2 WHERE asset_id = ?", (aids[3],))
            db.execute("UPDATE ai_suggestions SET verdict = 'none', rank = 3 WHERE asset_id = ?", (aids[0],))
        assert pile("good") == []                                      # a tenth of 4 suggestions is none
        monkeypatch.setattr(query, "GOOD_SHARE", 0.5)                  # half of 4: ranks 1 and 2
        assert pile("good") == [aids[3]]
        p = c.post(f"{API}/cleanup-plans", json={"pile": "bin"}).json()
        assert p["scope"] == {"pile": "bin"} and sorted(i["asset_id"] for i in p["items"]) == sorted([aids[1], aids[2]])
        assert sorted(i["id"] for i in c.get(f"{API}/assets", params={"use": "reject"}).json()["items"]) == sorted([aids[1], aids[2]])
