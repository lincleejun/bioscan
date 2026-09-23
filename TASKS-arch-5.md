# arch/5: one serve-config module

Base 7c011d5. Candidate 5 of the v1.3 plan (TASKS.md section "report candidates 1-6").

- [x] Golden behaviour recorded before any edit (flags x env -> resolved config, log lines, errors, plist bytes, --help, settings snapshot + fingerprint), saved outside the repo
- [x] `bioscan/serve_config.py` (stdlib only): defaults, flag -> env -> default, validation, detail-edge off rule, allow-roots parsing
- [x] `bioscan-serve` main() resolves once through it; `serve(config)` starts the service
- [x] `bioscan serve` resolves through it and hands the config to `serve()`; no os.environ hand-off
- [x] `--launchd` uses the module's defaults (same bytes)
- [x] decode.py takes MAX_EDGE from the module; create_app defaults use the constants
- [x] settings.snapshot derives rule thresholds from rules.py; fingerprint value unchanged (1dd3f33ab9c7)
- [x] Tests at the new interface replace tunables / detail_edge_from / allow_roots_from tests (every assertion kept)
- [x] README / README.zh-CN layout lists serve_config.py; settings table unchanged (behaviour unchanged)
- [x] ruff clean; pytest 3x green (147 passed, 8 skipped; base 141 / 8); import-light test green
- [x] Self-review vs 7c011d5: no blocking problems (one tidy-up: single import style in app.py)
- [ ] Golden behaviour re-recorded in a private scratch dir (shared scratchpad was clobbered): base tree vs HEAD tree at the same path

Found on the way:
- `bioscan serve --allow-root /x:y` used to become two roots (`/x`, relative `y`) through the env hand-off; now one root.
- `--launchd` ignores BIOSCAN_* and treats 0 as "use the default" without validation; kept for identical bytes (decision for the user).
