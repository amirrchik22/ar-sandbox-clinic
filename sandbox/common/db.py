"""Локальная база установки: один файл SQLite рядом со снимками занятий.

Почему SQLite, а не JSON-файлы:
    * пульт открыт сразу с нескольких устройств (телефон специалиста, ноутбук,
      страница проекции), и веб-сервер обслуживает запросы в нескольких потоках —
      SQLite пишет по одной транзакции за раз и не даёт двум запросам испортить
      друг другу список;
    * запись идёт по живому занятию (снимок, смена режима, заметка). JSON-файл
      каждый раз переписывается целиком: если ноутбук выключат в этот момент,
      файл останется обрезанным и пропадёт вся история. SQLite в режиме WAL так
      не ломается;
    * история занятий будет расти годами, а спрашивать её нужно кусками
      («последние 20», «снимки этого занятия») — это ровно то, что SQL делает
      одной строкой, а JSON заставляет читать всё целиком;
    * sqlite3 входит в стандартную библиотеку Python: на Windows, Mac и Linux
      ничего доустанавливать не надо, а это требование «любой ноутбук».

Один файл базы: data/sandbox.db. Его можно скопировать на флешку вместе с
папкой data/ — установка переедет целиком.
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


class Database:
    """Соединение с базой и замок вокруг него.

    Одно соединение на весь сервер: sqlite3 сам по себе не разрешает работать с
    соединением из разных потоков, поэтому check_same_thread=False плюс замок.
    Запросы короткие (единицы миллисекунд), очередь на замке не собирается.
    """

    def __init__(self, path: str | Path = ":memory:"):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")
        self.apply_schema()

    # --- схема ---

    def apply_schema(self, path: Path | None = None) -> None:
        """Создать недостающие таблицы. Существующие данные не трогает."""
        sql = (path or SCHEMA_PATH).read_text(encoding="utf-8")
        with self._lock:
            self._conn.executescript(sql)
            self._conn.commit()

    # --- запросы ---

    def rows(self, sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, params))

    def row(self, sql: str, params: tuple | dict = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def run(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        """Изменяющий запрос с немедленной фиксацией: после ответа сервера
        данные уже на диске, даже если ноутбук сразу выключат."""
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def dict_of(row: sqlite3.Row | None) -> dict | None:
    """Строка базы → обычный словарь (его уже можно отдать в JSON)."""
    return None if row is None else {k: row[k] for k in row.keys()}
