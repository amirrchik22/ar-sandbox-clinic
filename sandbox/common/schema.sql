-- Схема локальной базы установки (SQLite). Имён детей здесь нет: только коды.
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS therapists (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  pin_hash TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('therapist', 'admin')),
  active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS children (
  id INTEGER PRIMARY KEY,
  code TEXT NOT NULL UNIQUE,          -- псевдоним, например 'К-017'
  birth_year INTEGER,
  consent_video INTEGER NOT NULL DEFAULT 0,
  consent_date TEXT,                  -- ISO 8601
  notes_ref TEXT                      -- ссылка на карту в системе клиники
);

CREATE TABLE IF NOT EXISTS presets (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  palette_id TEXT NOT NULL,
  contour_step_mm REAL NOT NULL DEFAULT 10,
  water_on INTEGER NOT NULL DEFAULT 0,
  sound_on INTEGER NOT NULL DEFAULT 0,
  transition_speed REAL NOT NULL DEFAULT 0.5,
  hand_mode INTEGER NOT NULL DEFAULT 1,   -- 0 красить руки, 1 не красить
  tasks_json TEXT,
  default_minutes INTEGER NOT NULL DEFAULT 20,
  high_contrast INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,                -- 'S-000123'
  child_id INTEGER REFERENCES children(id),
  therapist_id INTEGER REFERENCES therapists(id),
  preset_id INTEGER REFERENCES presets(id),
  planned_minutes INTEGER,
  started_at TEXT NOT NULL,           -- ISO 8601
  ended_at TEXT,
  status TEXT NOT NULL CHECK (status IN ('running', 'finished', 'aborted')),
  therapist_notes TEXT,
  calibration_profile TEXT
);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES sessions(id),
  t_rel_ms INTEGER NOT NULL,
  type TEXT NOT NULL,                 -- start marker mode_change task_done pause resume note end error
  payload_json TEXT
);

CREATE TABLE IF NOT EXISTS media (
  id INTEGER PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES sessions(id),
  kind TEXT NOT NULL,                 -- screenshot video_rgb video_render heightmap
  path TEXT NOT NULL,
  t_start_ms INTEGER,
  t_end_ms INTEGER,
  bytes INTEGER,
  sha256 TEXT
);

CREATE TABLE IF NOT EXISTS metrics (
  session_id TEXT NOT NULL REFERENCES sessions(id),
  minute INTEGER NOT NULL,
  sand_moved_l REAL,
  active_s REAL,
  touched_area_pct REAL,
  max_height_mm REAL,
  min_height_mm REAL,
  actions_count INTEGER,
  symmetry REAL,
  PRIMARY KEY (session_id, minute)
);

CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY,
  at TEXT NOT NULL,
  therapist_id INTEGER,
  action TEXT NOT NULL,
  object TEXT,
  details TEXT
);
