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
  flag TEXT,                     -- derived from `use` for older clients: pick (record|edit) | reject | NULL
  stars INTEGER NOT NULL DEFAULT 0, note TEXT, updated_at TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
  use TEXT                       -- record | edit | reject | NULL = pending (contract revision 5B)
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

-- Named people (契约修订 3): a person a user named, across every task; their confirmed faces are the reference
-- new faces are matched against. Features stay in this file only.
CREATE TABLE IF NOT EXISTS people (
  id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS person_faces (
  asset_id TEXT NOT NULL REFERENCES assets(id), face INTEGER NOT NULL,  -- index in the asset's latest people result
  person_id TEXT NOT NULL REFERENCES people(id), vec TEXT NOT NULL,     -- the face's feature (f16 base64)
  source TEXT NOT NULL,          -- face (one face named) | collection (a person group named)
  created_at TEXT NOT NULL, PRIMARY KEY (asset_id, face)
);

-- Species identity per subject (ADR 0002, contract revision 4). A subject is one animal in a photo with an id
-- that outlives reruns: a later run's box at IoU >= 0.5 is the same subject. Identifications are only appended.
CREATE TABLE IF NOT EXISTS subjects (
  id TEXT PRIMARY KEY, asset_id TEXT NOT NULL REFERENCES assets(id),
  xyxy TEXT NOT NULL,            -- JSON [x0, y0, x1, y1], 0-1 of the upright image
  kind TEXT,                     -- bird | mammal | other_animal (BioScan box kind) | NULL
  origin TEXT NOT NULL,          -- ai (a detector box) | user (drawn by a person)
  last_run_id TEXT, created_at TEXT NOT NULL,
  removed_at TEXT                -- an ai-only subject a later run no longer sees; never one a person identified
);
CREATE INDEX IF NOT EXISTS subjects_asset ON subjects (asset_id);
CREATE TABLE IF NOT EXISTS identifications (
  id INTEGER PRIMARY KEY AUTOINCREMENT, subject_id TEXT NOT NULL REFERENCES subjects(id),
  asset_id TEXT NOT NULL REFERENCES assets(id),
  concept TEXT,                  -- avibase:<id> | mdd:<id> | name:<scientific>; NULL below species or unconfirmed
  taxon TEXT, common TEXT,       -- the name shown at `level` (genus/family keep the rolled-up taxon)
  level TEXT NOT NULL,           -- species | genus | family | unconfirmed | not_animal
  qualifier TEXT,                -- NULL | slash (one of `candidates`, unresolved)
  candidates TEXT NOT NULL DEFAULT '[]',   -- JSON [{concept, scientific, common, p}]
  p REAL,
  source TEXT NOT NULL,          -- ai | user
  run_id TEXT, version TEXT,     -- ai: the run and model version
  batch_id TEXT, actor TEXT, reason TEXT, undone INTEGER NOT NULL DEFAULT 0,   -- user: one action, undoable
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS identifications_subject ON identifications (subject_id, source, id);
CREATE INDEX IF NOT EXISTS identifications_asset ON identifications (asset_id);
CREATE INDEX IF NOT EXISTS identifications_concept ON identifications (concept);
-- Each live subject's current identification: its latest live user row, else its latest ai row.
-- Derived, so recreated on every start: a changed definition reaches old databases too.
DROP VIEW IF EXISTS current_ids;
CREATE VIEW current_ids AS
SELECT s.asset_id, s.id AS subject_id, s.kind, i.id AS identification_id, i.concept, i.taxon, i.common, i.level,
       i.source, i.p, i.created_at,
       i.qualifier                    -- slash: one of `candidates`, to be confirmed again (a list split)
FROM subjects s JOIN identifications i ON i.id = coalesce(
  (SELECT max(x.id) FROM identifications x WHERE x.subject_id = s.id AND x.source = 'user' AND x.undone = 0),
  (SELECT max(x.id) FROM identifications x WHERE x.subject_id = s.id AND x.source = 'ai' AND x.undone = 0))
WHERE s.removed_at IS NULL;
-- What a person set on a species card (display only: hiding never touches photos or identifications).
CREATE TABLE IF NOT EXISTS species_prefs (
  concept TEXT PRIMARY KEY, cover_asset_id TEXT, hidden INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL
);
-- Every name a concept goes by (taxa.py): list names, synonyms, the person's Chinese names; rebuilt when its inputs change.
CREATE TABLE IF NOT EXISTS taxon_names (
  concept TEXT NOT NULL, name TEXT NOT NULL, norm TEXT NOT NULL,
  lang TEXT NOT NULL,            -- sci | en | zh | alias
  source TEXT NOT NULL,          -- avilist | mdd | synonym | user
  kind TEXT                      -- bird | mammal
);
CREATE INDEX IF NOT EXISTS taxon_names_concept ON taxon_names (concept);

-- Encounters (contract revision 5B): one continuous observation within a task, suggested from time gaps and
-- species continuity; bursts nest inside. The machine only places photos not placed yet, so a person's split
-- or merge survives every rescan.
CREATE TABLE IF NOT EXISTS encounters (
  id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
  origin TEXT NOT NULL,          -- auto (suggested) | user (split off by a person)
  merged_into TEXT,              -- a person merged it away: its photos now belong to that encounter
  actor TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS encounter_members (
  task_id TEXT NOT NULL REFERENCES tasks(id), asset_id TEXT NOT NULL REFERENCES assets(id),
  encounter_id TEXT NOT NULL REFERENCES encounters(id),
  source TEXT NOT NULL,          -- auto | user (moved by a split or merge)
  PRIMARY KEY (task_id, asset_id)
);
CREATE INDEX IF NOT EXISTS encounter_members_enc ON encounter_members (encounter_id);

-- Import (contract revision 5D, PRD §06 正式导入): a plan freezes which files of which assets go where; the runner
-- copies, verifies and commits each file; managed_files says where the verified managed copies of an asset are.
CREATE TABLE IF NOT EXISTS import_plans (
  id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
  seq INTEGER NOT NULL,          -- batch number (all plans): the _B<seq> suffix of renamed groups
  status TEXT NOT NULL,          -- planned | queued | running | completed | partial | failed
  dest_root TEXT NOT NULL, folder TEXT NOT NULL, place TEXT, include_pending INTEGER NOT NULL,
  summary TEXT NOT NULL,         -- JSON frozen at planning: uses, not_copied, unfinished
  error TEXT, interruptions INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT
);
CREATE TABLE IF NOT EXISTS import_items (
  plan_id TEXT NOT NULL REFERENCES import_plans(id), asset_id TEXT NOT NULL REFERENCES assets(id),
  src TEXT NOT NULL, dst TEXT NOT NULL,                -- dst frozen at planning, never re-derived
  role TEXT NOT NULL,            -- raw | jpeg | image | video | sidecar
  size INTEGER NOT NULL, mtime_ns INTEGER, quick_hash TEXT,
  action TEXT NOT NULL,          -- copy | identical (dst already holds it) | reuse (imported before) | duplicate (same content imported from elsewhere)
  state TEXT NOT NULL,           -- pending | copying | done | failed
  sha256 TEXT, error TEXT, done_at TEXT,
  PRIMARY KEY (plan_id, src)
);
CREATE TABLE IF NOT EXISTS managed_files (
  asset_id TEXT NOT NULL REFERENCES assets(id), path TEXT NOT NULL, source_path TEXT NOT NULL, role TEXT NOT NULL,
  size INTEGER NOT NULL, source_mtime_ns INTEGER, quick_hash TEXT, sha256 TEXT NOT NULL,
  plan_id TEXT NOT NULL, imported_at TEXT NOT NULL,
  PRIMARY KEY (asset_id, path)
);
CREATE INDEX IF NOT EXISTS managed_files_source ON managed_files (source_path);
CREATE INDEX IF NOT EXISTS managed_files_hash ON managed_files (size, quick_hash);

-- ---- 契约修订 5E: Lightroom handoff and cleanup plans (handoff.py, cleanup.py) ----
-- A handoff freezes its members when it is made; each item's state comes only from the plugin's result file.
CREATE TABLE IF NOT EXISTS handoffs (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, scope TEXT NOT NULL,   -- JSON: what the person handed off
  doc_path TEXT NOT NULL,        -- the handoff file the Lightroom plugin reads
  status TEXT NOT NULL,          -- written | applied | partial | failed (never "applied" before a result)
  actor TEXT NOT NULL, created_at TEXT NOT NULL, result_at TEXT
);
CREATE TABLE IF NOT EXISTS handoff_items (
  handoff_id TEXT NOT NULL REFERENCES handoffs(id), asset_id TEXT NOT NULL REFERENCES assets(id), ord INTEGER NOT NULL,
  sent TEXT,                     -- JSON: the photo entry as written (NULL = not sent, e.g. offline)
  status TEXT NOT NULL,          -- pending | ok | skipped | failed
  reason TEXT, result TEXT,      -- result: the plugin's per-photo JSON
  PRIMARY KEY (handoff_id, asset_id)
);
-- The editor's identity of an asset and the field values Gardener last wrote there (the sync baseline, PRD §15).
CREATE TABLE IF NOT EXISTS editor_links (
  asset_id TEXT NOT NULL REFERENCES assets(id), editor TEXT NOT NULL,   -- lightroom
  photo_id TEXT, uuid TEXT, path TEXT, baseline TEXT NOT NULL DEFAULT '{}',   -- JSON {stars, keywords}
  handoff_id TEXT, updated_at TEXT NOT NULL, PRIMARY KEY (asset_id, editor)
);
-- A cleanup plan: the rejected assets in a scope with every file of each (snapshot size/mtime), run only on confirm.
CREATE TABLE IF NOT EXISTS cleanup_plans (
  id TEXT PRIMARY KEY, scope TEXT NOT NULL, plan TEXT NOT NULL,   -- JSON: items with species impact, summary
  status TEXT NOT NULL,          -- planned | ran | partial
  actor TEXT NOT NULL, created_at TEXT NOT NULL, ran_at TEXT, report TEXT
);
CREATE TABLE IF NOT EXISTS cleanup_files (
  plan_id TEXT NOT NULL REFERENCES cleanup_plans(id), asset_id TEXT NOT NULL REFERENCES assets(id),
  path TEXT NOT NULL, role TEXT NOT NULL,    -- raw | jpeg | image | video | sidecar | ...
  size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL,
  result TEXT,                   -- NULL | trashed | failed | skipped
  reason TEXT, at TEXT, PRIMARY KEY (plan_id, path)
);

-- ---- 契约修订 7B: import batch collections, handoff presets/export, 修图进度 ----
-- Added columns (store.ADDED_COLUMNS): import_plans.collection_id (the batch's manual collection),
-- handoffs.options (JSON {develop, export, retry_of}), editor_links.progress (JSON 修图进度 from the plugin's real results).
-- A develop preset / export step a person chose for a species (concept:<k>) or a collection, e.g. a batch (collection:<id>).
CREATE TABLE IF NOT EXISTS editor_presets (
  scope_key TEXT PRIMARY KEY, develop TEXT, export TEXT, actor TEXT NOT NULL, updated_at TEXT NOT NULL

-- ---- 契约修订 7A: a person's burst split, undoable encounter split/merge (review.py) ----
-- A frame a person split off its burst: on every later run the reducer's burst for it is replaced by this id, so
-- the split survives rescans (the reducer re-derives ai_suggestions.burst_id each run). Only moved frames have rows.
CREATE TABLE IF NOT EXISTS burst_overrides (
  task_id TEXT NOT NULL REFERENCES tasks(id), asset_id TEXT NOT NULL REFERENCES assets(id),
  burst_id TEXT NOT NULL,        -- <task>:<machine burst>~<part>
  actor TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (task_id, asset_id)
);
-- One encounter split or merge, so it can be undone as one batch: detail = the moves and the state right after.
CREATE TABLE IF NOT EXISTS encounter_history (
  batch_id TEXT PRIMARY KEY, action TEXT NOT NULL,   -- split | merge
  detail TEXT NOT NULL,          -- JSON {moves: [[asset_id, from, to, source]], before: {eid: merged_into}, after: {eid: {members, merged_into}}}
  actor TEXT NOT NULL, at TEXT NOT NULL, undone INTEGER NOT NULL DEFAULT 0
);
