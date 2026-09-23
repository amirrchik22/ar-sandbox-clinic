"""Занятия: старт, длительность, снимки, заметка, отчёт.

Занятие — это запись в журнале: кто вёл, с кем занимались, каким режимом,
сколько шло, что снято. При завершении программа сама собирает отчёт: три
снимка (начало, середина, конец), длительность, режим и поле для заметки —
готовый материал родителю.

Медицинских заявлений в отчёте нет и не будет: это запись развивающего и
коррекционного занятия под контролем специалиста.

Хранение — SQLite, sandbox/common/db.py, таблицы sessions, media, events.
Файлы снимков и видео лежат в data/sessions/<занятие>/, в базе — путь от data/.
"""
from __future__ import annotations

import time
import uuid
from pathlib import Path

from sandbox.common.db import Database, dict_of

from .people import clean_note, when
from .pipeline import MODE_TITLES

# Сколько занятий отдаём в список по умолчанию.
HISTORY_LIMIT = 50


def duration_text(seconds: float) -> str:
    """Секунды → «12:22». Длительность занятия пульт показывает так же."""
    total = max(0, int(round(seconds)))
    return f"{total // 60:02d}:{total % 60:02d}"


class Sessions:
    """Журнал занятий и список записей к ним."""

    def __init__(self, db: Database, data_dir: Path):
        self.db = db
        self.data_dir = Path(data_dir)

    # ---------------------------------------------------------------- занятие

    def new_id(self) -> str:
        base = time.strftime("S-%Y%m%d-%H%M%S")
        session_id, n = base, 1
        while self.db.row("SELECT id FROM sessions WHERE id = ?", (session_id,)) is not None:
            n += 1
            session_id = f"{base}-{n}"
        return session_id

    def running(self) -> dict | None:
        row = self.db.row("SELECT * FROM sessions WHERE status = 'running' "
                          "ORDER BY started_at DESC LIMIT 1")
        return None if row is None else self.brief(dict_of(row))

    def start(self, staff: dict | None, child: dict | None, mode: str,
              brightness: int, minutes: int | None) -> dict:
        """Начать занятие. Подпись (специалист и ребёнок) может быть пустой:
        если специалист не выбрал никого, занятие всё равно запишется."""
        now = time.time()
        session_id = self.new_id()
        self.db.run(
            "INSERT INTO sessions (id, staff_id, child_id, staff_name, child_alias, mode, "
            "brightness, planned_minutes, started_at, started_text, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'running')",
            (session_id,
             staff["id"] if staff else None,
             child["id"] if child else None,
             staff["name"] if staff else "",
             child["alias"] if child else "",
             mode, int(brightness), minutes if minutes is None else int(minutes),
             now, when(now)))
        self.log(session_id, "start", mode)
        return self.get(session_id)

    def stop(self, session_id: str | None = None) -> dict | None:
        """Завершить занятие и собрать отчёт."""
        current = self.get(session_id) if session_id else self.running()
        if current is None:
            return None
        if current["running"]:
            self.db.run("UPDATE sessions SET ended_at = ?, status = 'finished' WHERE id = ?",
                        (time.time(), current["id"]))
            self.log(current["id"], "stop", "")
        return self.report(current["id"])

    def close_stale(self) -> list[str]:
        """Закрыть занятия, оставшиеся «идущими» после выключения программы.

        Занятие живёт, пока работает пульт. Если ноутбук выключили посреди
        занятия, при следующем запуске такая запись закрывается по последнему
        событию — иначе таймер показывал бы сутки.
        """
        rows = self.db.rows("SELECT id, started_at FROM sessions WHERE status = 'running'")
        closed = []
        for row in rows:
            last = self.db.row("SELECT max(at) AS t FROM events WHERE session_id = ?",
                               (row["id"],))
            ended = last["t"] if last and last["t"] else row["started_at"]
            self.db.run("UPDATE sessions SET status = 'finished', ended_at = ? WHERE id = ?",
                        (ended, row["id"]))
            closed.append(row["id"])
        return closed

    def set_note(self, session_id: str, note: str) -> dict:
        session = self.get(session_id)
        self.db.run("UPDATE sessions SET note = ? WHERE id = ?",
                    (clean_note(note), session["id"]))
        return self.report(session["id"])

    def get(self, session_id: str) -> dict:
        row = self.db.row("SELECT * FROM sessions WHERE id = ?", (str(session_id),))
        if row is None:
            raise LookupError("занятие не найдено")
        return self.brief(dict_of(row))

    def history(self, limit: int = HISTORY_LIMIT) -> list[dict]:
        rows = self.db.rows("SELECT * FROM sessions ORDER BY started_at DESC LIMIT ?",
                            (int(limit),))
        return [self.brief(dict_of(r)) for r in rows]

    def brief(self, data: dict) -> dict:
        """Короткая карточка занятия — то, что показывает пульт и список истории."""
        running = data["status"] == "running"
        seconds = (data["ended_at"] or time.time()) - data["started_at"]
        photos = self.media(data["id"], kind="photo")
        return {
            "id": data["id"],
            "running": running,
            "status": data["status"],
            "staff": {"id": data["staff_id"], "name": data["staff_name"]},
            "child": {"id": data["child_id"], "alias": data["child_alias"]},
            # то же вторым именем: пульт читает подпись плоскими полями
            "staff_name": data["staff_name"],
            "child_alias": data["child_alias"],
            "duration_min": data["planned_minutes"],
            "mode": data["mode"],
            "mode_title": MODE_TITLES.get(data["mode"], data["mode"]),
            "brightness": data["brightness"],
            "planned_minutes": data["planned_minutes"],
            "started_at": data["started_at"],
            "started_text": data["started_text"],
            "ended_at": data["ended_at"],
            "seconds": round(seconds),
            "duration": duration_text(seconds),
            "note": data["note"],
            "snapshots": photos,
            "snapshots_count": len(photos),
        }

    def report(self, session_id: str) -> dict:
        """Отчёт занятия: три снимка, длительность, режим, заметка.

        Три снимка — начало, середина, конец. Если снимков меньше трёх, берём
        какие есть: программа не выдумывает то, чего не было.
        """
        session = self.get(session_id)
        photos = session["snapshots"]
        if len(photos) >= 3:
            picks = [(("начало"), photos[0]),
                     (("середина"), photos[len(photos) // 2]),
                     (("конец"), photos[-1])]
        elif len(photos) == 2:
            picks = [("начало", photos[0]), ("конец", photos[1])]
        elif photos:
            picks = [("занятие", photos[0])]
        else:
            picks = []
        session["highlights"] = [dict(item, title=title) for title, item in picks]
        session["events"] = self.events(session["id"])
        session["media"] = self.media(session["id"])
        return session

    # ---------------------------------------------------------------- записи

    def media_id(self) -> str:
        return "M-" + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]

    def add_media(self, session_id: str | None, kind: str, path: Path,
                  thumb: Path | None = None, seconds: float | None = None,
                  note: str = "") -> dict:
        """Записать снимок или видео в журнал. path — файл на диске."""
        path = Path(path)
        now = time.time()
        at_second = None
        if session_id:
            try:
                at_second = round(now - self.get(session_id)["started_at"], 1)
            except LookupError:
                session_id = None
        media = {
            "id": self.media_id(),
            "session_id": session_id,
            "kind": kind,
            "path": self.relative(path),
            "thumb": self.relative(thumb) if thumb else None,
            "created_at": now,
            "at_second": at_second,
            "bytes": path.stat().st_size if path.exists() else None,
            "seconds": seconds,
            "note": note,
        }
        self.db.run(
            "INSERT INTO media (id, session_id, kind, path, thumb, created_at, at_second, "
            "bytes, seconds, note) VALUES (:id, :session_id, :kind, :path, :thumb, "
            ":created_at, :at_second, :bytes, :seconds, :note)", media)
        if session_id:
            self.log(session_id, "snapshot" if kind == "photo" else kind, media["id"])
        return self.get_media(media["id"])

    def media(self, session_id: str | None = None, kind: str | None = None) -> list[dict]:
        sql = "SELECT * FROM media"
        where, params = [], []
        if session_id:
            where.append("session_id = ?")
            params.append(str(session_id))
        if kind:
            where.append("kind = ?")
            params.append(kind)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at"
        return [self._media_dict(dict_of(r)) for r in self.db.rows(sql, tuple(params))]

    def get_media(self, media_id: str) -> dict:
        row = self.db.row("SELECT * FROM media WHERE id = ?", (str(media_id),))
        if row is None:
            raise LookupError("запись не найдена")
        return self._media_dict(dict_of(row))

    def delete_media(self, media_id: str, remove_file: bool = True) -> dict:
        """Убрать запись из библиотеки.

        Файл стирается с диска: это видео ребёнка, и «удалить» здесь должно
        означать именно удалить, а не спрятать. Занятие и его отчёт остаются.
        """
        media = self.get_media(media_id)
        if remove_file:
            for key in ("path", "thumb_path"):
                name = media[key]
                if not name:
                    continue
                file = self.absolute(name)
                try:
                    file.unlink(missing_ok=True)
                except OSError:
                    pass
        self.db.run("DELETE FROM media WHERE id = ?", (media["id"],))
        return media

    # --- пути ---

    def relative(self, path: Path) -> str:
        """Путь от папки data/ — чтобы установку можно было перенести целиком."""
        path = Path(path)
        try:
            return str(path.resolve().relative_to(self.data_dir.resolve()))
        except ValueError:
            return str(path)

    def absolute(self, stored: str) -> Path:
        path = Path(stored)
        return path if path.is_absolute() else (self.data_dir / path)

    def web_url(self, stored: str | None) -> str | None:
        """Прямая ссылка на файл занятия: /snapshots/<занятие>/<файл>.

        Ею открываются снимки в отчёте. Она работает всегда — и когда
        библиотекой записей занимается media.py со своими адресами /media/...,
        и когда модуля записей ещё нет.
        """
        if not stored:
            return None
        path = Path(stored)
        if path.is_absolute():
            try:
                path = path.relative_to(self.data_dir)
            except ValueError:
                return None
        inside = path.as_posix()
        prefix = "sessions/"
        return "/snapshots/" + inside[len(prefix):] if inside.startswith(prefix) else None

    def _media_dict(self, data: dict) -> dict:
        """Строка базы → то, что можно показать в браузере.

        path и thumb в базе — пути на диске от папки data/. Наружу отдаём их под
        именами path/thumb_path, а url и thumb — это уже адреса для браузера.
        """
        data["thumb_path"] = data.pop("thumb")
        data["url"] = self.web_url(data["path"]) or f"/media/{data['id']}"
        data["thumb"] = self.web_url(data["thumb_path"]) or data["url"]
        data["time"] = time.strftime("%H:%M:%S", time.localtime(data["created_at"]))
        data["at_text"] = duration_text(data["at_second"]) if data["at_second"] else ""
        data["exists"] = self.absolute(data["path"]).exists()
        return data

    # ---------------------------------------------------------------- события

    def log(self, session_id: str | None, kind: str, detail: str = "") -> None:
        """Отметка в журнале занятия: смена режима, пауза, заморозка, снимок."""
        if not session_id:
            return
        now = time.time()
        row = self.db.row("SELECT started_at FROM sessions WHERE id = ?", (session_id,))
        at_second = round(now - row["started_at"], 1) if row else None
        self.db.run("INSERT INTO events (session_id, at, at_second, kind, detail) "
                    "VALUES (?, ?, ?, ?, ?)", (session_id, now, at_second, kind, str(detail)))

    def events(self, session_id: str) -> list[dict]:
        rows = self.db.rows("SELECT * FROM events WHERE session_id = ? ORDER BY at",
                            (str(session_id),))
        out = []
        for r in rows:
            item = dict_of(r)
            item["at_text"] = duration_text(item["at_second"] or 0)
            out.append(item)
        return out
