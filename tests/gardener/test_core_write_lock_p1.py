"""#131 P1: the start of a run's analysis never holds the write lock across the whole task (a POST /marks waited
240-340 ms behind it at 20k), and the 2 s `changed_after` poll reads by `assets.updated_at` instead of scanning
every asset."""
from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from bioscan.gardener import store as st
from bioscan.gardener import tasks

T0 = "2026-10-01T00:00:00.000000Z"
N = 1200            # more than two 500-asset chunks


@pytest.fixture
def s(tmp_path):
    s = st.Store(tmp_path)
    c = s.conn()
    c.execute("BEGIN")
    c.execute("INSERT INTO tasks (id, title, capabilities, created_at, updated_at) VALUES ('t1', 't1', '[]', ?, ?)",
              (T0, T0))
    for i in range(N):
        a = f"a{i:05d}"
        c.execute("INSERT INTO assets (id, media, pair_key, primary_path, created_at, updated_at) "
                  "VALUES (?, 'photo', ?, ?, ?, ?)", (a, a, f"/v/{a}.jpg", T0, T0))
        c.execute("INSERT INTO task_assets VALUES ('t1', ?, NULL, 'r', ?)", (a, i))
    c.execute("COMMIT")
    return s


def _run(s):
    return SimpleNamespace(store=s, id="r1", caps=["aesthetic", "species"], enabled={"aesthetic", "species"})


def test_mark_pending_commits_in_short_transactions(s, monkeypatch):
    bumped: list[int] = []                    # assets whose updated_at each transaction bumps
    real = st.Store.tx

    @contextmanager
    def counted(self):
        with real(self) as c:
            n = [0]
            c.set_trace_callback(lambda q: n.__setitem__(0, n[0] + q.startswith("UPDATE assets SET updated_at")))
            try:
                yield c
            finally:
                c.set_trace_callback(None)
            bumped.append(n[0])
    monkeypatch.setattr(st.Store, "tx", counted)
    units = {r["id"]: ["aesthetic", "species"] for r in s.all("SELECT id FROM assets")}
    tasks.RunExec.mark_pending(_run(s), units)
    assert sum(bumped) == N and max(bumped) <= 500
    assert s.one("SELECT count(*) n FROM asset_caps WHERE state = 'pending' AND run_id = 'r1'")["n"] == 2 * N
    assert s.one("SELECT count(*) n FROM assets WHERE updated_at > ?", (T0,))["n"] == N


def test_mark_pending_crash_keeps_the_chunks_already_committed(s, monkeypatch):
    """A crash part-way leaves whole chunks marked and the rest untouched; `units()` reads an asset with no row as
    pending, so the resumed run marks and analyses the rest."""
    real = st.Store.tx

    @contextmanager
    def dies_on_second_chunk(self):
        if self.one("SELECT count(*) n FROM asset_caps WHERE state = 'pending'")["n"]:     # one chunk is in
            raise RuntimeError("crash")
        with real(self) as c:
            yield c
    monkeypatch.setattr(st.Store, "tx", dies_on_second_chunk)
    units = {r["id"]: ["aesthetic"] for r in s.all("SELECT id FROM assets ORDER BY id")}
    with pytest.raises(RuntimeError):
        tasks.RunExec.mark_pending(SimpleNamespace(store=s, id="r1", caps=["aesthetic"], enabled=set(tasks.MODEL_CAPS)),
                                   units)
    assert s.one("SELECT count(*) n FROM asset_caps WHERE state = 'pending'")["n"] == 500


def test_changed_after_poll_reads_the_updated_at_index(s):
    """After a bulk write the UI pages every changed asset of the task; each page must not SCAN all assets."""
    c = s.conn()
    sqls: list[str] = []
    c.set_trace_callback(sqls.append)
    try:
        out = s.search({"task": "t1", "changed_after": "2026-10-02T00:00:00Z"}, order="discovered")
    finally:
        c.set_trace_callback(None)
    assert out["total"] == 0
    reads = [q for q in sqls if "a.updated_at >" in q]
    assert len(reads) == 2                                       # the count and the page
    for q in reads:
        plan = [r[3] for r in c.execute("EXPLAIN QUERY PLAN " + q)]
        assert any("USING INDEX assets_updated" in p for p in plan) and not any(p == "SCAN a" for p in plan), plan
