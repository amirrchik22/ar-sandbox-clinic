-- Схема локальной базы установки (SQLite, файл data/sandbox.db).
--
-- Имён детей здесь нет — только условные имена (псевдонимы), которые вводит
-- специалист. Медицинских данных в базе нет: это журнал развивающих и
-- коррекционных занятий под контролем специалиста.
--
-- Что изменилось против первого наброска (23.09, решения заказчика):
--   * пароля нет → у специалиста только имя, поле pin_hash убрано;
--   * не «пресеты», а режимы (map/calm/two/water) → таблица presets не нужна,
--     режим и яркость лежат прямо в карточке ребёнка и в записи занятия;
--   * таблица therapists переименована в staff, children.code → children.alias,
--     чтобы имена совпадали с договором об API (/api/people/staff, .../child).
--
-- Время: started_at/ended_at/created_at хранятся числом (секунды Unix, REAL) —
-- так их не нужно разбирать при каждом запросе; рядом лежит готовая строка для
-- показа человеку (started_text), чтобы пульт не занимался форматом даты.

PRAGMA journal_mode = WAL;

-- Специалисты. Это подпись под записью, а не вход в программу.
CREATE TABLE IF NOT EXISTS staff (
  id         INTEGER PRIMARY KEY,
  name       TEXT    NOT NULL,
  active     INTEGER NOT NULL DEFAULT 1,
  created_at REAL    NOT NULL
);

-- Карточки детей под условными именами.
CREATE TABLE IF NOT EXISTS children (
  id         INTEGER PRIMARY KEY,
  alias      TEXT    NOT NULL UNIQUE,          -- условное имя, например «Ёжик» или «К-017»
  mode       TEXT    NOT NULL DEFAULT 'map',   -- map | calm | two | water
  brightness INTEGER NOT NULL DEFAULT 100,     -- яркость проекции, проценты
  minutes    INTEGER NOT NULL DEFAULT 20,      -- обычная длительность занятия
  note       TEXT    NOT NULL DEFAULT '',      -- заметки специалиста о ребёнке
  active     INTEGER NOT NULL DEFAULT 1,       -- 0 = убран из списка, записи сохранены
  created_at REAL    NOT NULL
);

-- Занятия. staff_name и child_alias дублируются нарочно: если карточку потом
-- переименуют или уберут из списка, отчёт прошлого занятия не потеряет подпись.
CREATE TABLE IF NOT EXISTS sessions (
  id              TEXT    PRIMARY KEY,         -- 'S-20260923-204512'
  staff_id        INTEGER REFERENCES staff(id),
  child_id        INTEGER REFERENCES children(id),
  staff_name      TEXT    NOT NULL DEFAULT '',
  child_alias     TEXT    NOT NULL DEFAULT '',
  mode            TEXT    NOT NULL DEFAULT 'map',
  brightness      INTEGER NOT NULL DEFAULT 100,
  planned_minutes INTEGER,
  started_at      REAL    NOT NULL,
  started_text    TEXT    NOT NULL DEFAULT '',
  ended_at        REAL,
  status          TEXT    NOT NULL DEFAULT 'running'
                  CHECK (status IN ('running', 'finished')),
  note            TEXT    NOT NULL DEFAULT ''  -- заметка специалиста к занятию
);

CREATE INDEX IF NOT EXISTS sessions_started ON sessions (started_at DESC);

-- Снимки и видео. Путь хранится относительно папки data/, чтобы установку
-- можно было перенести на другой компьютер целиком.
CREATE TABLE IF NOT EXISTS media (
  id         TEXT    PRIMARY KEY,              -- 'M-20260923-204512-7f3a'
  session_id TEXT    REFERENCES sessions(id),  -- NULL = снято вне занятия
  kind       TEXT    NOT NULL,                 -- photo | video
  path       TEXT    NOT NULL,                 -- путь от data/
  thumb      TEXT,                             -- маленькая копия, путь от data/
  created_at REAL    NOT NULL,
  at_second  REAL,                             -- на какой секунде занятия сделано
  bytes      INTEGER,
  seconds    REAL,                             -- длительность видео
  note       TEXT    NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS media_session ON media (session_id, created_at);

-- Что происходило на занятии: смена режима, пауза, заморозка, снимок.
-- Нужно для отчёта родителю и для разбора, если что-то пошло не так.
CREATE TABLE IF NOT EXISTS events (
  id         INTEGER PRIMARY KEY,
  session_id TEXT    REFERENCES sessions(id),
  at         REAL    NOT NULL,
  at_second  REAL,
  kind       TEXT    NOT NULL,                 -- mode | pause | freeze | snapshot | calibrate
  detail     TEXT    NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS events_session ON events (session_id, at);

-- Метрики по минутам занятия — заготовка для рекордера (docs/roadmap.md).
CREATE TABLE IF NOT EXISTS metrics (
  session_id       TEXT    NOT NULL REFERENCES sessions(id),
  minute           INTEGER NOT NULL,
  sand_moved_l     REAL,
  active_s         REAL,
  touched_area_pct REAL,
  max_height_mm    REAL,
  min_height_mm    REAL,
  actions_count    INTEGER,
  PRIMARY KEY (session_id, minute)
);
