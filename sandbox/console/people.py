"""Кто ведёт занятие и с кем занимаемся: специалисты и карточки детей.

Пароля в программе нет. Специалист выбирается одним касанием — это подпись под
записью, а не защита. Защита в том, что пульт виден только в сети клиники.

Имён детей здесь нет и быть не может: только условное имя (псевдоним), которое
придумывает специалист — «Ёжик», «К-017». В карточке лежат настройки, с которыми
ребёнку комфортно (режим, яркость, длительность), и заметки специалиста. Выбрали
ребёнка — настройки применились сами, перенастраивать каждое занятие не нужно.

Хранение — SQLite, sandbox/common/db.py, таблицы staff и children.
"""
from __future__ import annotations

import time

from sandbox.common.db import Database, dict_of

from .pipeline import MODE_IDS, MODE_TITLES

# Границы настроек. Шире не нужно: яркость ниже 10 % уже не видно на песке,
# занятие дольше двух часов не бывает.
BRIGHTNESS_RANGE = (10, 100)
MINUTES_RANGE = (1, 120)
NAME_MAX = 60
NOTE_MAX = 4000

DEFAULT_MODE = "map"
DEFAULT_BRIGHTNESS = 100
DEFAULT_MINUTES = 20


# ------------------------------------------------------------------ проверки

def clean_name(value: str, what: str = "имя") -> str:
    """Убрать лишние пробелы и проверить длину. Пустое имя не принимаем."""
    text = " ".join(str(value or "").split())
    if not text:
        raise ValueError(f"{what}: пустая строка")
    if len(text) > NAME_MAX:
        raise ValueError(f"{what}: слишком длинно, не больше {NAME_MAX} знаков")
    return text


def clean_mode(value: str) -> str:
    mode = str(value or "").strip()
    if mode not in MODE_IDS:
        raise ValueError("неизвестный режим: " + ", ".join(MODE_IDS))
    return mode


def clean_number(value, low: int, high: int, what: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as e:
        raise ValueError(f"{what}: нужно число") from e
    if not low <= number <= high:
        raise ValueError(f"{what}: от {low} до {high}")
    return number


def clean_note(value: str) -> str:
    note = str(value or "").strip()
    if len(note) > NOTE_MAX:
        raise ValueError(f"заметка: слишком длинно, не больше {NOTE_MAX} знаков")
    return note


def same_name(a: str, b: str) -> bool:
    """Одно и то же имя без оглядки на регистр.

    Сравниваем в Python, а не запросом SQL: lower() в SQLite приводит к нижнему
    регистру только латиницу, и «Анна» с «анна» для базы остались бы разными.
    """
    return str(a).casefold() == str(b).casefold()


def when(ts: float) -> str:
    """Время для показа человеку: 23.09.2026 20:45."""
    return time.strftime("%d.%m.%Y %H:%M", time.localtime(ts))


# ------------------------------------------------------------------ хранилище

class People:
    """Списки специалистов и детей. Ничего не удаляет насовсем: только прячет."""

    def __init__(self, db: Database):
        self.db = db

    def _find(self, table: str, field: str, value: str, skip: int | None = None):
        """Найти строку по имени без учёта регистра (см. same_name)."""
        for row in self.db.rows(f"SELECT * FROM {table}"):
            if row["id"] != skip and same_name(row[field], value):
                return row
        return None

    # --- специалисты ---

    def staff(self, hidden: bool = False) -> list[dict]:
        where = "" if hidden else "WHERE active = 1"
        rows = self.db.rows(f"SELECT * FROM staff {where} ORDER BY name COLLATE NOCASE")
        return [self._staff_dict(r) for r in rows]

    def add_staff(self, name: str) -> dict:
        name = clean_name(name, "имя специалиста")
        same = self._find("staff", "name", name)
        if same is not None:                      # тот же человек — просто вернём его
            if not same["active"]:
                self.db.run("UPDATE staff SET active = 1 WHERE id = ?", (same["id"],))
            return self.get_staff(int(same["id"]))
        cur = self.db.run("INSERT INTO staff (name, active, created_at) VALUES (?, 1, ?)",
                          (name, time.time()))
        return self.get_staff(int(cur.lastrowid))

    def get_staff(self, staff_id: int) -> dict:
        row = self.db.row("SELECT * FROM staff WHERE id = ?", (int(staff_id),))
        if row is None:
            raise LookupError("специалист не найден")
        return self._staff_dict(row)

    def hide_staff(self, staff_id: int) -> dict:
        person = self.get_staff(staff_id)
        self.db.run("UPDATE staff SET active = 0 WHERE id = ?", (person["id"],))
        return self.get_staff(person["id"])

    @staticmethod
    def _staff_dict(row) -> dict:
        d = dict_of(row)
        d["active"] = bool(d["active"])
        d["created_text"] = when(d["created_at"])
        return d

    # --- дети ---

    def children(self, hidden: bool = False) -> list[dict]:
        where = "" if hidden else "WHERE active = 1"
        rows = self.db.rows(f"SELECT * FROM children {where} ORDER BY alias COLLATE NOCASE")
        return [self._child_dict(r) for r in rows]

    def add_child(self, alias: str, mode: str | None = None, brightness: int | None = None,
                  minutes: int | None = None, note: str | None = None) -> dict:
        alias = clean_name(alias, "условное имя")
        same = self._find("children", "alias", alias)
        if same is not None:
            if same["active"]:
                raise ValueError("такое условное имя уже есть в списке")
            self.db.run("UPDATE children SET active = 1 WHERE id = ?", (same["id"],))
            return self.get_child(int(same["id"]))
        cur = self.db.run(
            "INSERT INTO children (alias, mode, brightness, minutes, note, active, created_at) "
            "VALUES (?, ?, ?, ?, ?, 1, ?)",
            (alias,
             clean_mode(mode) if mode is not None else DEFAULT_MODE,
             clean_number(brightness, *BRIGHTNESS_RANGE, "яркость")
             if brightness is not None else DEFAULT_BRIGHTNESS,
             clean_number(minutes, *MINUTES_RANGE, "длительность")
             if minutes is not None else DEFAULT_MINUTES,
             clean_note(note) if note is not None else "",
             time.time()))
        return self.get_child(int(cur.lastrowid))

    def get_child(self, child_id: int) -> dict:
        row = self.db.row("SELECT * FROM children WHERE id = ?", (int(child_id),))
        if row is None:
            raise LookupError("карточка ребёнка не найдена")
        return self._child_dict(row)

    def update_child(self, child_id: int, alias: str | None = None, mode: str | None = None,
                     brightness: int | None = None, minutes: int | None = None,
                     note: str | None = None) -> dict:
        child = self.get_child(child_id)
        fields: dict = {}
        if alias is not None:
            alias = clean_name(alias, "условное имя")
            busy = self._find("children", "alias", alias, skip=child["id"])
            if busy is not None:
                raise ValueError("такое условное имя уже есть в списке")
            fields["alias"] = alias
        if mode is not None:
            fields["mode"] = clean_mode(mode)
        if brightness is not None:
            fields["brightness"] = clean_number(brightness, *BRIGHTNESS_RANGE, "яркость")
        if minutes is not None:
            fields["minutes"] = clean_number(minutes, *MINUTES_RANGE, "длительность")
        if note is not None:
            fields["note"] = clean_note(note)
        if fields:
            sets = ", ".join(f"{k} = :{k}" for k in fields)
            self.db.run(f"UPDATE children SET {sets} WHERE id = :id",
                        dict(fields, id=child["id"]))
        return self.get_child(child["id"])

    def hide_child(self, child_id: int) -> dict:
        """Убрать из списка. Занятия, снимки и отчёты остаются на месте."""
        child = self.get_child(child_id)
        self.db.run("UPDATE children SET active = 0 WHERE id = ?", (child["id"],))
        return self.get_child(child["id"])

    @staticmethod
    def _child_dict(row) -> dict:
        d = dict_of(row)
        d["active"] = bool(d["active"])
        d["mode_title"] = MODE_TITLES.get(d["mode"], d["mode"])
        d["created_text"] = when(d["created_at"])
        # Те же настройки вторым именем: пульт читает их вложенным объектом
        # settings, а длительность называет duration_min. Отдаём и так, и так,
        # чтобы части программы сходились без переделки обеих сторон.
        d["duration_min"] = d["minutes"]
        d["settings"] = {"mode": d["mode"], "brightness": d["brightness"],
                         "duration_min": d["minutes"], "minutes": d["minutes"]}
        return d

    # --- всё сразу для пульта ---

    def everyone(self) -> dict:
        return {"staff": self.staff(), "children": self.children()}
