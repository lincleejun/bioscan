-- Gardener SQLite schema v1 (docs/gardener/contract.md §2). Only the service writes this file.
-- Times are ISO 8601 UTC strings; ids are prefixed random hex (t_, r_, a_, c_, ...).
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);   -- JSON values

CREATE TABLE IF NOT EXISTS presets (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, capabilities TEXT NOT NULL,   -- JSON list
  builtin INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY, title TEXT NOT NULL, subtitle TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '',
  preset TEXT, capabilities TEXT NOT NULL,      -- JSON list: the task's current default config
  run_mode TEXT NOT NULL DEFAULT 'balanced',
  timezone TEXT,                                -- IANA name or ±HH:MM where the shoot was; NULL = unknown
  archived_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS sources (
  id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), path TEXT NOT NULL,
  online INTEGER NOT NULL DEFAULT 1, checked_at TEXT, added_at TEXT NOT NULL, added_run_id TEXT,
  UNIQUE (task_id, path)
);

-- One execution of a task: scan | append | rescan | retry | resume never rewrites an earlier run.
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), seq INTEGER NOT NULL,
  kind TEXT NOT NULL,            -- scan | append | rescan | retry
  status TEXT NOT NULL,          -- queued | running | paused | stopped | completed | partial | failed | cancelled | interrupted
  phase TEXT,                    -- discover | preview | analyze | group | NULL
  config TEXT NOT NULL,          -- JSON snapshot: capabilities, run_mode, preset, versions, options
  scope TEXT NOT NULL,           -- JSON snapshot: source paths and/or asset ids this run covers
  counts TEXT NOT NULL DEFAULT '{}',   -- JSON: discovered, files, bytes, previewed, preview_failed, analyzed, failed, skipped, unsupported
  summary TEXT,                  -- JSON written when the run ends
  error TEXT, queued_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,
  UNIQUE (task_id, seq)
);

-- A logical asset: one photo (RAW + same-stem JPEG in one folder are one asset) or one whole video.
CREATE TABLE IF NOT EXISTS assets (
  id TEXT PRIMARY KEY, media TEXT NOT NULL,          -- photo | video
  pair_key TEXT NOT NULL UNIQUE,                     -- folder + lowercase stem
  primary_path TEXT NOT NULL,                        -- the file previewed and analysed
  taken_at TEXT, camera TEXT, lens TEXT, width INTEGER, height INTEGER, orientation INTEGER,
  lat REAL, lon REAL, duration_s REAL, meta TEXT NOT NULL DEFAULT '{}',   -- JSON: everything else read
  sha256 TEXT,                                       -- of primary_path, once analysed
  preview_status TEXT NOT NULL DEFAULT 'pending',    -- pending | ready | failed
  preview_method TEXT, preview_error TEXT,
  online INTEGER NOT NULL DEFAULT 1,
  version_of TEXT,                                   -- another asset of the same capture stands for this one
  version_reason TEXT,                               -- same_capture | export
  version_lock INTEGER NOT NULL DEFAULT 0,           -- a person said: its own capture (never linked again)
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS assets_taken ON assets (taken_at);

CREATE TABLE IF NOT EXISTS asset_files (
  path TEXT PRIMARY KEY, asset_id TEXT NOT NULL REFERENCES assets(id), role TEXT NOT NULL,  -- raw | jpeg | image | video
  ext TEXT NOT NULL, size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL, quick_hash TEXT,
  online INTEGER NOT NULL DEFAULT 1, seen_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS asset_files_asset ON asset_files (asset_id);

CREATE TABLE IF NOT EXISTS task_assets (
  task_id TEXT NOT NULL REFERENCES tasks(id), asset_id TEXT NOT NULL REFERENCES assets(id),
  source_id TEXT REFERENCES sources(id), first_run_id TEXT NOT NULL, ord INTEGER NOT NULL,
  PRIMARY KEY (task_id, asset_id)
);

-- Current state per asset x capability (the UI's 未启用/待运行/运行中/完成/失败/未识别/不可用).
CREATE TABLE IF NOT EXISTS asset_caps (
  asset_id TEXT NOT NULL REFERENCES assets(id), capability TEXT NOT NULL,
  state TEXT NOT NULL,           -- not_enabled | pending | running | done | failed | unrecognized | unavailable
  run_id TEXT, version TEXT, error TEXT, updated_at TEXT NOT NULL,
  PRIMARY KEY (asset_id, capability)
);

-- Every machine result ever produced, per run: history is never overwritten.
CREATE TABLE IF NOT EXISTS results (
  id INTEGER PRIMARY KEY AUTOINCREMENT, asset_id TEXT NOT NULL REFERENCES assets(id), run_id TEXT NOT NULL,
  capability TEXT NOT NULL, status TEXT NOT NULL,    -- done | failed | unrecognized | unavailable
  version TEXT, data TEXT, error TEXT, created_at TEXT NOT NULL
);
-- The assets a task run covers (its scope once discovered): resume, summaries and progress read it.
CREATE TABLE IF NOT EXISTS run_assets (
  run_id TEXT NOT NULL REFERENCES runs(id), asset_id TEXT NOT NULL REFERENCES assets(id),
  PRIMARY KEY (run_id, asset_id)
);

CREATE INDEX IF NOT EXISTS results_asset ON results (asset_id, capability, id);

-- AI suggestion layer, separate from the human layer (marks). One row per asset per run.
CREATE TABLE IF NOT EXISTS ai_suggestions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, asset_id TEXT NOT NULL REFERENCES assets(id), run_id TEXT NOT NULL,
  verdict TEXT NOT NULL,         -- pick | reject | none
  score REAL, rank INTEGER, reasons TEXT NOT NULL DEFAULT '[]', burst_id TEXT, burst_rank INTEGER, burst_size INTEGER,
  version TEXT, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ai_asset ON ai_suggestions (asset_id, id);

-- Human layer: global per asset, shared by every collection.
CREATE TABLE IF NOT EXISTS marks (
  asset_id TEXT PRIMARY KEY REFERENCES assets(id),
  flag TEXT,                     -- pick | reject | NULL
  stars INTEGER NOT NULL DEFAULT 0, note TEXT, updated_at TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS mark_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT, batch_id TEXT NOT NULL, asset_id TEXT NOT NULL,
  field TEXT NOT NULL, old TEXT, new TEXT, actor TEXT NOT NULL, reason TEXT, undone INTEGER NOT NULL DEFAULT 0,
  at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS mark_history_batch ON mark_history (batch_id);

CREATE TABLE IF NOT EXISTS collections (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL,           -- manual | smart
  origin TEXT NOT NULL,          -- auto (a grouping suggestion) | user
  group_kind TEXT,               -- species | person | animal | scene | other | day | place | all | NULL (user); event | place (older runs)
  auto_key TEXT,                 -- grouping key of an auto collection, unique per task
  parent_id TEXT,                -- the subject collection a day/place collection splits
  task_id TEXT REFERENCES tasks(id), name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
  rule TEXT,                     -- JSON asset query (smart only)
  summary TEXT,                  -- JSON: the grouping suggestion's summary (time_basis, uncertain_ids, ...)
  user_edited INTEGER NOT NULL DEFAULT 0,             -- renamed/edited by a person: regrouping never renames it
  deleted_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
  UNIQUE (task_id, auto_key)
);
CREATE TABLE IF NOT EXISTS collection_members (
  collection_id TEXT NOT NULL REFERENCES collections(id), asset_id TEXT NOT NULL REFERENCES assets(id),
  added_by TEXT NOT NULL,        -- auto | user
  added_at TEXT NOT NULL, removed_at TEXT, removed_by TEXT,   -- a person's removal is a tombstone auto never undoes
  PRIMARY KEY (collection_id, asset_id)
);
CREATE TABLE IF NOT EXISTS collection_reviews (
  id INTEGER PRIMARY KEY AUTOINCREMENT, collection_id TEXT NOT NULL REFERENCES collections(id),
  action TEXT NOT NULL,          -- complete | zero | reopen
  members TEXT NOT NULL,         -- JSON list of asset ids at that moment
  unhandled INTEGER NOT NULL DEFAULT 0, resolution TEXT, actor TEXT NOT NULL, note TEXT, at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS collection_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT, collection_id TEXT NOT NULL, action TEXT NOT NULL,  -- create|rename|members|merge|split|delete|restore|rule
  detail TEXT NOT NULL DEFAULT '{}', actor TEXT NOT NULL, at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tags (
  id TEXT PRIMARY KEY,           -- stable: scene:<label>, species:<scientific>, user:<hex>
  name TEXT NOT NULL, parent_id TEXT REFERENCES tags(id), aliases TEXT NOT NULL DEFAULT '[]',
  active INTEGER NOT NULL DEFAULT 1, merged_into TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tag_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT, tag_id TEXT NOT NULL, action TEXT NOT NULL, detail TEXT NOT NULL, at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS asset_tags (
  asset_id TEXT NOT NULL REFERENCES assets(id), tag_id TEXT NOT NULL REFERENCES tags(id),
  source TEXT NOT NULL,          -- ai | user
  run_id TEXT, confidence REAL, created_at TEXT NOT NULL,
  PRIMARY KEY (asset_id, tag_id, source)
);

CREATE TABLE IF NOT EXISTS exports (
  id TEXT PRIMARY KEY, created_at TEXT NOT NULL, request TEXT NOT NULL, report TEXT NOT NULL
);
