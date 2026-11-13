"""#131: an asset's latest AI suggestion is read with a correlated max (index ai_asset), never a library-wide
`id IN (SELECT max(id) ... GROUP BY asset_id)` list, which held the write lock for seconds during a scan.
Results stay those of the old form."""
from __future__ import annotations

import re

import pytest

from gardener.workbench import review
from gardener.workbench import store as st

T0 = "2026-10-01T00:00:00.000000Z"
OLD_LATEST = "SELECT max(id) FROM ai_suggestions GROUP BY asset_id"


@pytest.fixture
def c(tmp_path):
    """Two tasks of six photos; every photo has three suggestion runs whose bursts and scores differ, so a query
    that read anything but the latest row would answer differently. Each photo shows one of two species."""
    c = st.Store(tmp_path).conn()
    c.execute("BEGIN")
    for t in ("t1", "t2"):
        c.execute("INSERT INTO tasks (id, title, capabilities, created_at, updated_at) VALUES (?, ?, '[]', ?, ?)",
                  (t, t, T0, T0))
        for i in range(6):
            a = f"{t}_a{i}"
            c.execute("INSERT INTO assets (id, media, pair_key, primary_path, taken_at, created_at, updated_at) "
                      "VALUES (?, 'photo', ?, ?, ?, ?, ?)", (a, a, f"/v/{a}.jpg", f"2026-09-01T06:00:{i * 2:02d}", T0, T0))
            c.execute("INSERT INTO task_assets VALUES (?, ?, NULL, 'r', ?)", (t, a, i))
            for run in range(3):        # run 0/1: stale bursts and scores; run 2 (latest): the real ones
                burst = f"{t}:b{(i + run) % 3}" if run < 2 else (f"{t}:b{i // 3}" if i != 5 else None)
                c.execute("INSERT INTO ai_suggestions (asset_id, run_id, verdict, score, burst_id, burst_rank, "
                          "burst_size, created_at) VALUES (?, ?, ?, ?, ?, ?, 3, ?)",
                          (a, f"r{run}", "reject" if (i + run) % 4 == 0 else "pick", (run * 7 + i * 3) % 10 / 10,
                           burst, i % 3 + 1, T0))
            c.execute("INSERT INTO subjects (id, asset_id, xyxy, kind, origin, created_at) "
                      "VALUES (?, ?, '[0,0,1,1]', 'bird', 'ai', ?)", (f"j_{a}", a, T0))
            c.execute("INSERT INTO identifications (subject_id, asset_id, concept, taxon, level, source, created_at) "
                      "VALUES (?, ?, ?, 'Aus bus', 'species', 'ai', ?)", (f"j_{a}", a, f"avibase:{i % 2}", T0))
    c.execute("COMMIT")
    return c


def test_latest_suggestion_reads_match_the_old_group_by_form(c):
    old_bursts = {r[0]: r[1] for r in c.execute(
        "SELECT s.asset_id, s.burst_id FROM ai_suggestions s JOIN task_assets ta ON ta.asset_id = s.asset_id "
        f"WHERE ta.task_id = 't1' AND s.burst_id IS NOT NULL AND s.id IN ({OLD_LATEST})")}
    assert review._task_bursts(c, "t1") == old_bursts and len(old_bursts) == 5
    old_ranked = c.execute(f"SELECT count(*) FROM ai_suggestions WHERE id IN ({OLD_LATEST}) AND asset_id LIKE 't1_%' "
                           "AND score IS NOT NULL").fetchone()[0]
    assert review.shortlist(c, "t1")["scored"] == old_ranked == 6
    kb = review.keep_best(c, "t1_a0", keep_use="record", actor="ui", dry_run=True)
    old_sibs = {r[0] for r in c.execute(f"SELECT asset_id FROM ai_suggestions WHERE id IN ({OLD_LATEST}) "
                                        "AND burst_id = 't1:b0' AND asset_id != 't1_a0'")}
    assert kb["burst_id"] == "t1:b0" and set(kb["reject"]) | {s["id"] for s in kb["skipped"]} == old_sibs == {
        "t1_a1", "t1_a2"}


def _plans(c, fn):
    sqls: list[str] = []
    c.set_trace_callback(sqls.append)
    try:
        fn()
    finally:
        c.set_trace_callback(None)
    # "suggestions": the reads go through the current_suggestions view (修订 15), whose plan still names ai_suggestions
    return {q: [r[3] for r in c.execute("EXPLAIN QUERY PLAN " + q)] for q in sqls if "suggestions" in q}


def test_task_scoped_latest_reads_never_walk_the_whole_library(c):
    # A library-wide GROUP BY shows as a LIST SUBQUERY; a SCAN of ai_suggestions reads every asset's history.
    for fn in (lambda: review._task_bursts(c, "t1"), lambda: review.shortlist(c, "t1"),
               lambda: review.assign_encounters(c, "t1")):
        plans = _plans(c, fn)
        assert plans
        for q, plan in plans.items():
            bad = [p for p in plan if "LIST SUBQUERY" in p or re.match(r"SCAN (s|x|ai_suggestions)\b", p)]
            assert not bad, (q, plan)


def test_keep_best_never_builds_a_library_wide_list(c):
    plans = _plans(c, lambda: review.keep_best(c, "t1_a0", keep_use="record", actor="ui", dry_run=True))
    assert plans and not [p for plan in plans.values() for p in plan if "LIST SUBQUERY" in p], plans
