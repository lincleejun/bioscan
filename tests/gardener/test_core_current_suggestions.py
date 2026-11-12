"""Contract revision 15: one `current_suggestions` view (an asset's latest ai_suggestions row) and one rule for a
group's representative (review.representative), returned by GET /encounters and on every frame's `burst` in
GET /assets."""
from __future__ import annotations

import sqlite3

from core_fixtures import *  # noqa: F403 - fixtures (env, fakes)
from core_fixtures import create_task, photos, wait_task

from bioscan.gardener import review
from bioscan.gardener import store as st

API = "/api/v1"
T0 = "2026-10-01T00:00:00.000000Z"


def test_a_store_made_before_the_view_gets_it_on_open(tmp_path):
    st.Store(tmp_path).migrate()
    with sqlite3.connect(tmp_path / "gardener.sqlite") as db:          # an older store: no view, no burst columns
        db.execute("DROP VIEW IF EXISTS current_suggestions")
        db.execute("ALTER TABLE ai_suggestions DROP COLUMN burst_rank")
        db.execute("ALTER TABLE ai_suggestions DROP COLUMN burst_size")
        db.execute("INSERT INTO assets (id, media, pair_key, primary_path, created_at, updated_at) "
                   "VALUES ('a1', 'photo', 'a1', '/v/a1.jpg', ?, ?)", (T0, T0))
        for run, verdict in (("r1", "reject"), ("r2", "pick")):
            db.execute("INSERT INTO ai_suggestions (asset_id, run_id, verdict, created_at) VALUES ('a1', ?, ?, ?)",
                       (run, verdict, T0))
    s = st.Store(tmp_path)
    s.migrate()
    assert s.all("SELECT asset_id, run_id, verdict, burst_rank FROM current_suggestions") == [
        {"asset_id": "a1", "run_id": "r2", "verdict": "pick", "burst_rank": None}]


def test_the_api_returns_the_one_representative_when_rank_and_capture_order_differ(env, tmp_path):
    src = tmp_path / "card"
    photos(src, 4)                                    # the fake cull: one burst, ranked in capture order
    with env.client() as c:
        t = wait_task(c, create_task(c, [str(src)])["id"])
        items = c.get(f"{API}/assets", params={"task": t["id"], "order": "time"}).json()["items"]
        a = [i["id"] for i in items]
        burst = items[0]["burst"]["id"]
        with sqlite3.connect(env.data / "gardener.sqlite") as db:     # a later run ranks them the other way round
            for i, aid in enumerate(a):
                db.execute("INSERT INTO ai_suggestions (asset_id, run_id, verdict, score, burst_id, burst_rank, "
                           "burst_size, created_at) VALUES (?, 'r_late', 'none', 1.0, ?, ?, 4, ?)",
                           (aid, burst, len(a) - i, T0))

        def reps():
            grid = {i["burst"]["rep"] for i in c.get(f"{API}/assets", params={"task": t["id"]}).json()["items"]}
            enc = [b["rep"] for e in c.get(f"{API}/encounters", params={"task": t["id"]}).json()["encounters"]
                   for b in e["bursts"]]
            with sqlite3.connect(env.data / "gardener.sqlite") as db:
                db.row_factory = sqlite3.Row
                frames = [dict(r) for r in db.execute(
                    "SELECT a.id, m.use, coalesce(m.stars, 0) stars, s.burst_rank, s.score FROM current_suggestions s "
                    "JOIN assets a ON a.id = s.asset_id LEFT JOIN marks m ON m.asset_id = a.id ORDER BY a.taken_at, a.id")]
            return grid, enc, review.representative(frames)["id"]

        assert reps() == ({a[-1]}, [a[-1]], a[-1])     # rank 1 is now the last frame taken, not the first
        assert c.post(f"{API}/marks", json={"asset_ids": [a[1]], "use": "record"}).status_code == 200
        assert reps() == ({a[1]}, [a[1]], a[1])        # a person's choice comes first, on every page alike
