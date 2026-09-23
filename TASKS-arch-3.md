# TASKS — arch 3: run module owning chunk / model turn / decode

Branch `arch/3-run-module`, base 7c011d5. Behaviour-preserving: /run events (types, order, fields,
values except timings), /health, exit codes unchanged.

Assumptions (stated before the first edit):
- The new module is `bioscan/service/run.py` with one class `RunQueue`: built once per app from the
  engine, decode pool, chunk and detail edge; it owns the model and CPU executors, the decode pool
  (self-healing rebuild) and the per-chunk FIFO model turn with its running/queued counters.
  Its interface: `events(inputs, want, opts, is_disconnected)` yields events, plus `running` / `queued`.
- app.py keeps HTTP: parsing, allow-roots, ensure -> 503, StreamingResponse, /health. `create_app`
  keeps its signature; `main`, `tunables`, `*_from` are sibling 5's lane and are left alone.
- `app.state.bioscan` stays and now points at the RunQueue (same `running` / `queued` attributes as State).
- Tests that reached into app internals (`run_events`, `app.DecodePool`, `app.timed_decode`) move to
  the new interface (`RunQueue.events`, `run.DecodePool`, `run.timed_decode`); none are deleted.

- [x] baseline: ruff clean, pytest 141 passed / 8 skipped at 7c011d5
- [x] golden /run event streams recorded before any edit (outside the repo): 110 scenarios, two runs
      byte-identical after normalising timings, elapsed_ms and 0x addresses in PIL messages
- [x] run.py: RunQueue (decode pool, model turn, executors), typed per-image record, progress in one place
- [x] app.py: HTTP only; create_app signature unchanged
- [x] tests moved to the new interface (disconnect, worker crash, pool rebuild)
- [x] README / README.zh-CN file maps mention run.py (dated design spec left as written)
- [x] golden streams regenerated and identical (same sha256 a75529da...)
- [x] ruff clean; pytest green 5x (141 passed / 8 skipped each run)
- [x] self-review of the diff vs 7c011d5 (locks, counters, cancellation, pool rebuild): no blocking findings
