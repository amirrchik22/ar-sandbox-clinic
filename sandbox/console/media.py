"""Библиотека записей занятия: снимки, видео ландшафта, карты высот.

Что тут есть:
    MediaLibrary  — хранилище на файлах. Правда живёт в самих файлах, базы нет:
                    что лежит на диске, то и видно в пульте. Скопировали папку
                    занятия на флешку — она откроется и на другом ноутбуке.
    router        — ручки пульта: список записей, отдача файла, удаление,
                    старт и стоп записи видео.

Где лежат записи:

    data/sessions/<год>/<месяц>/<день>/<занятие>/
        snap-153012.jpg          снимок
        snap-153012-small.jpg    миниатюра снимка (в списке отдельной строкой не идёт)
        video_000.mp4            видео ландшафта, сегмент по 60 с
        video_001.mp4            следующий сегмент
        video.frames/            если ffmpeg не нашёлся — кадры по одному
        video.log                честный журнал записи
        camera-153012_000.mp4    видео с цветной камеры — только если его включили

Про камеру. У датчика есть цветная камера, 1920x1080 и тридцать кадров в
секунду. По умолчанию с неё не пишется ничего: в кадр попадает лицо ребёнка, а
это персональные данные несовершеннолетнего. Режим выбирает клиника — ручка
/api/record/camera, три значения: «выключено» (по умолчанию), «только ящик»
(обрезано по рабочей зоне, лицо в кадр не попадает) и «полностью». Выбор
хранится в data/camera-record.json и переживает перезапуск.

Идентификатор записи считается из её пути (blake2s, 12 знаков): один и тот же
файл всегда получает один и тот же адрес, даже после перезапуска программы, а
угадать чужой адрес нельзя.

Ничего не стирается: удаление переносит запись в data/_to_delete и кладёт рядом
записку — правило проекта.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from sandbox.recorder.main import CameraRecorder, LandscapeRecorder
from sandbox.recorder.video import (CAMERA_BOX, CAMERA_MODES, CAMERA_NOTES,
                                    CAMERA_OFF, CAMERA_TITLES, CAMERA_WARNING,
                                    FRAMES_SUFFIX, camera_jpeg, camera_zone,
                                    ffmpeg_path, ffmpeg_version,
                                    normalize_camera_mode, normalize_zone)

ROOT = Path(__file__).resolve().parents[2]

# Меньше этой доли свободного места — пульт показывает предупреждение.
LOW_DISK_PCT = 10.0

# Что считаем записью и с каким типом содержимого отдаём браузеру.
KINDS = {
    ".jpg": ("snapshot", "image/jpeg"),
    ".jpeg": ("snapshot", "image/jpeg"),
    ".png": ("snapshot", "image/png"),
    ".mp4": ("video", "video/mp4"),
    ".m4v": ("video", "video/mp4"),
    ".mov": ("video", "video/quicktime"),
    ".webm": ("video", "video/webm"),
    ".hmz": ("heightmap", "application/octet-stream"),
}

KIND_TITLES = {"snapshot": "Снимок", "video": "Видео", "frames": "Видео (кадры)",
               "heightmap": "Рельеф"}

# Записи с камеры называются иначе: специалист должен с одного взгляда видеть,
# где ландшафт, а где живая съёмка.
CAMERA_PREFIX = "camera-"
CAMERA_KIND_TITLES = {"video": "Камера", "frames": "Камера (кадры)"}

# Где хранится выбор клиники: режим записи с камеры и рабочая зона.
CAMERA_SETTINGS_FILE = "camera-record.json"

# Сколько кадров серии смотрим, чтобы посчитать её размер (чтобы не подвисать).
FRAMES_SCAN_LIMIT = 20000


# --------------------------------------------------------------- мелкие помощники

def media_id(rel_path: str) -> str:
    """Адрес записи из её пути внутри хранилища. Один файл — один адрес, навсегда."""
    return hashlib.blake2s(rel_path.encode("utf-8"), digest_size=6).hexdigest()


def human_size(size: int) -> str:
    if size >= 1024 * 1024 * 1024:
        return f"{size / 1024 / 1024 / 1024:.1f} ГБ"
    if size >= 1024 * 1024:
        return f"{size / 1024 / 1024:.1f} МБ"
    if size >= 1024:
        return f"{size / 1024:.0f} КБ"
    return f"{size} Б"


def human_seconds(seconds: float) -> str:
    seconds = int(round(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}" if seconds >= 60 else f"{seconds} с"


def safe_name(name: str) -> str:
    """Имя папки без сюрпризов: ни косых черт, ни выхода наверх."""
    cleaned = "".join(c for c in str(name) if c not in '/\\:*?"<>|').strip(" .")
    return cleaned or "без-занятия"


class RecordBody(BaseModel):
    """Тело запроса на старт записи. Всё необязательное — пульт может ничего не слать."""
    session: str | None = None
    fps: int | None = None


class CameraBody(BaseModel):
    """Выбор режима записи с цветной камеры и, если нужно, рабочей зоны."""
    mode: str | None = None
    zone: list[float] | None = None


# --------------------------------------------------------------- хранилище

class MediaLibrary:
    """Записи занятий на диске: разложить, найти, отдать, убрать."""

    def __init__(self, root: Path | None = None, data_dir: Path | None = None):
        self._root = Path(root) if root else None
        self._data = Path(data_dir) if data_dir else None
        self._frames = None                     # источник кадров (конвейер пульта)
        self._session_id = None                 # функция «какое занятие идёт сейчас»
        self._recorder: LandscapeRecorder | None = None
        self._last_record: dict | None = None
        self._camera = None                     # источник цветных кадров (датчик)
        self._camera_rec: CameraRecorder | None = None
        self._camera_settings: dict | None = None   # прочитанный выбор клиники
        self._camera_settings_from: Path | None = None
        self._lock = threading.Lock()
        # Короткий кэш обхода папок: страница библиотеки просит сразу десятки
        # миниатюр, незачем перечитывать диск на каждую картинку.
        self._cache_lock = threading.Lock()
        self._cache: list[dict] = []
        self._cache_at = 0.0
        self._cache_ttl = 1.0

    # ---------- где что лежит ----------

    @property
    def data_dir(self) -> Path:
        """Папка данных. Берётся из SANDBOX_DATA — тесты и показ пишут во временную."""
        return self._data or Path(os.environ.get("SANDBOX_DATA") or (ROOT / "data"))

    @property
    def root(self) -> Path:
        return self._root or (self.data_dir / "sessions")

    @property
    def trash(self) -> Path:
        return self.data_dir / "_to_delete"

    def session_dir(self, session_id: str | None = None, when: float | None = None) -> Path:
        """Папка занятия: data/sessions/<год>/<месяц>/<день>/<занятие>/ — и создать её."""
        when = when or time.time()
        day = time.localtime(when)
        name = safe_name(session_id or "без-занятия")
        path = (self.root / time.strftime("%Y", day) / time.strftime("%m", day)
                / time.strftime("%d", day) / name)
        path.mkdir(parents=True, exist_ok=True)
        return path

    # ---------- подключение к пульту ----------

    def configure(self, frames=None, session_id=None, root: Path | None = None,
                  data_dir: Path | None = None, camera=None) -> None:
        """Пульт передаёт сюда конвейер кадров и способ узнать текущее занятие."""
        if frames is not None:
            self._frames = frames
        if session_id is not None:
            self._session_id = session_id
        if camera is not None:
            self._camera = camera
        if root is not None:
            self._root = Path(root)
        if data_dir is not None:
            self._data = Path(data_dir)
        self._camera_settings = None            # у новой папки данных свой выбор
        self.forget()

    def frame_source(self):
        """Конвейер кадров: либо переданный через configure, либо общий у пульта."""
        if self._frames is not None:
            return self._frames
        try:                                    # запасной путь: пульт про нас не знает
            from .app import CONSOLE
            return CONSOLE.frames
        except Exception:
            return None

    def camera_source(self):
        """Датчик, у которого есть цветная камера. None — камеры нет или не включена.

        Второй раз датчик не открыть: Kinect v2 отдаётся одному процессу, и этот
        процесс — конвейер пульта. Поэтому камеру берём у него же.
        """
        if self._camera is not None:
            return self._camera
        source = getattr(self.frame_source(), "sensor", None)
        return source if hasattr(source, "color_frame") else None

    def camera_available(self) -> bool:
        """Отдаёт ли датчик цветные кадры прямо сейчас."""
        source = self.camera_source()
        if source is None:
            return False
        try:
            return source.color_frame() is not None
        except Exception:                        # noqa: BLE001 — недоступная камера не ошибка
            return False

    def current_session(self) -> str | None:
        if self._session_id is not None:
            try:
                return self._session_id()
            except Exception:
                return None
        try:
            from .app import CONSOLE
            return CONSOLE.session.id if CONSOLE.session else None
        except Exception:
            return None

    # ---------- чтение хранилища ----------

    def _session_of(self, container: Path) -> str:
        """Занятие, которому принадлежит запись, — по имени папки, где она лежит."""
        try:
            rel = container.relative_to(self.root)
        except ValueError:
            return ""
        return rel.parts[-1] if rel.parts else ""

    def _entry_for_file(self, path: Path) -> dict | None:
        kind_type = KINDS.get(path.suffix.lower())
        if kind_type is None or path.stem.endswith("-small"):
            return None                          # миниатюра идёт вместе со своим снимком
        kind, mime = kind_type
        stat = path.stat()
        rel = path.relative_to(self.root).as_posix()
        entry = self._base_entry(media_id(rel), kind, path, rel, stat.st_mtime, stat.st_size)
        entry["mime"] = mime
        thumb = path.with_name(path.stem + "-small.jpg")
        if kind == "snapshot" and thumb.exists():
            entry["thumb"] = f"/media/{entry['id']}?thumb=1"
        elif kind == "snapshot":
            entry["thumb"] = entry["url"]
        if kind == "video":
            part = path.stem.rsplit("_", 1)[-1]
            if part.isdigit() and int(part) > 0:
                entry["title"] += f", часть {int(part) + 1}"
        return entry

    def _entry_for_frames(self, path: Path) -> dict:
        """Серия кадров (ffmpeg не нашёлся) — одна запись, а не тысяча строк в списке."""
        frames = sorted(p for p in path.glob("k-*.jpg"))[:FRAMES_SCAN_LIMIT]
        size = sum(p.stat().st_size for p in frames)
        when = frames[0].stat().st_mtime if frames else path.stat().st_mtime
        rel = path.relative_to(self.root).as_posix()
        entry = self._base_entry(media_id(rel), "frames", path, rel, when, size)
        entry["mime"] = "image/jpeg"
        entry["frames"] = len(frames)
        entry["thumb"] = f"/media/{entry['id']}?frame=1"
        entry["hint"] = "видео не собралось: на этом ноутбуке нет ffmpeg — сохранены кадры"
        return entry

    def _base_entry(self, entry_id: str, kind: str, path: Path, rel: str,
                    when: float, size: int) -> dict:
        local = time.localtime(when)
        # Живая съёмка и ландшафт не должны путаться в списке: у записи с камеры
        # своё название и отдельная пометка.
        camera = path.name.startswith(CAMERA_PREFIX)
        titles = CAMERA_KIND_TITLES if camera else KIND_TITLES
        return {
            "id": entry_id,
            "kind": kind,
            "camera": camera,
            "session": self._session_of(path.parent),
            "name": path.name,
            "path": rel,
            "title": f"{titles.get(kind, KIND_TITLES.get(kind, kind))} "
                     f"{time.strftime('%H:%M:%S', local)}",
            "t": when,
            "time": time.strftime("%H:%M:%S", local),
            "date": time.strftime("%d.%m.%Y", local),
            "size": size,
            "size_h": human_size(size),
            "url": f"/media/{entry_id}",
            "download": f"/media/{entry_id}?download=1",
        }

    def scan(self) -> list[dict]:
        """Все записи хранилища, новые сверху. Читаем файлы, ничего не кэшируем."""
        root = self.root
        if not root.exists():
            return []
        items: list[dict] = []
        for path in root.rglob("*"):
            rel_parts = path.relative_to(root).parts
            if any(part.endswith(FRAMES_SUFFIX) for part in rel_parts[:-1]):
                continue                        # кадры внутри серии — не отдельные записи
            try:
                if path.is_dir():
                    if path.name.endswith(FRAMES_SUFFIX):
                        items.append(self._entry_for_frames(path))
                    continue
                entry = self._entry_for_file(path)
            except OSError:                     # файл унесли прямо сейчас — просто пропустим
                continue
            if entry:
                items.append(entry)
        items.sort(key=lambda e: e["t"], reverse=True)
        return items

    def cached_scan(self, fresh: bool = False) -> list[dict]:
        now = time.monotonic()
        with self._cache_lock:
            if not fresh and self._cache_at and now - self._cache_at < self._cache_ttl:
                return self._cache
        items = self.scan()
        with self._cache_lock:
            self._cache, self._cache_at = items, time.monotonic()
        return items

    def forget(self) -> None:
        """Сбросить кэш: что-то записали или убрали, список надо перечитать."""
        with self._cache_lock:
            self._cache_at = 0.0

    def list(self, session: str | None = None, kind: str | None = None,
             limit: int = 500) -> list[dict]:
        items = self.cached_scan()
        if session:
            items = [e for e in items if e["session"] == session]
        if kind:
            items = [e for e in items if e["kind"] == kind]
        return items[:max(1, limit)]

    def sessions(self) -> list[dict]:
        """Занятия, у которых есть записи: сколько снимков, сколько видео."""
        found: dict[str, dict] = {}
        for entry in self.cached_scan():
            name = entry["session"]
            item = found.setdefault(name, {"session": name, "t": entry["t"],
                                           "date": entry["date"], "snapshots": 0,
                                           "videos": 0, "size": 0})
            item["size"] += entry["size"]
            item["t"] = max(item["t"], entry["t"])
            if entry["kind"] == "snapshot":
                item["snapshots"] += 1
            elif entry["kind"] in ("video", "frames"):
                item["videos"] += 1
        items = sorted(found.values(), key=lambda e: e["t"], reverse=True)
        for item in items:
            item["size_h"] = human_size(item["size"])
        return items

    def find(self, entry_id: str) -> dict | None:
        for entry in self.cached_scan():
            if entry["id"] == entry_id:
                return entry
        for entry in self.cached_scan(fresh=True):      # не нашли — вдруг запись новая
            if entry["id"] == entry_id:
                return entry
        return None

    def path_of(self, entry: dict) -> Path:
        """Путь записи на диске — с проверкой, что он не вывел за пределы хранилища."""
        root = self.root.resolve()
        path = (root / entry["path"]).resolve()
        if root != path and root not in path.parents:
            raise HTTPException(404, "запись не найдена")
        return path

    # ---------- запись файлов ----------

    def save_snapshot(self, jpeg: bytes, session_id: str | None = None,
                      when: float | None = None) -> dict:
        """Сохранить готовый JPEG снимком занятия и вернуть карточку записи."""
        when = when or time.time()
        directory = self.session_dir(session_id or self.current_session(), when)
        path = directory / time.strftime("snap-%H%M%S.jpg", time.localtime(when))
        n = 1
        while path.exists():                    # два снимка в одну секунду — не редкость
            path = path.with_name(f"snap-{time.strftime('%H%M%S', time.localtime(when))}-{n}.jpg")
            n += 1
        path.write_bytes(jpeg)
        self.forget()
        entry = self._entry_for_file(path)
        if entry is None:                       # такого не бывает, но пусть будет честно
            raise HTTPException(500, "снимок не сохранился")
        return entry

    # ---------- уборка ----------

    def remove(self, entry_id: str) -> dict:
        """Убрать запись из библиотеки: перенести в data/_to_delete и положить записку."""
        entry = self.find(entry_id)
        if entry is None:
            raise HTTPException(404, "запись не найдена")
        path = self.path_of(entry)
        if not path.exists():
            raise HTTPException(404, "файл записи не найден")
        self.trash.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        target = self.trash / f"{stamp}-{safe_name(entry['session'])}-{path.name}"
        n = 1
        while target.exists():
            target = target.with_name(f"{stamp}-{n}-{safe_name(entry['session'])}-{path.name}")
            n += 1
        shutil.move(str(path), str(target))
        moved = [target.name]
        thumb = path.with_name(path.stem + "-small.jpg")      # миниатюра едет следом
        if thumb.exists():
            thumb_target = target.with_name(target.stem + "-small.jpg")
            shutil.move(str(thumb), str(thumb_target))
            moved.append(thumb_target.name)
        self._write_note(target, entry, moved)
        self.forget()
        return {"ok": True, "moved_to": str(target), "entry": entry}

    def _write_note(self, target: Path, entry: dict, moved: list[str]) -> None:
        note = (
            f"# {target.name}\n\n"
            f"Запись убрана из библиотеки пульта {time.strftime('%d.%m.%Y %H:%M')}.\n\n"
            f"- занятие: {entry['session'] or 'без занятия'}\n"
            f"- тип: {KIND_TITLES.get(entry['kind'], entry['kind'])}\n"
            f"- снято: {entry['date']} {entry['time']}\n"
            f"- размер: {entry['size_h']}\n"
            f"- было по пути: data/sessions/{entry['path']}\n"
            f"- перенесено: {', '.join(moved)}\n\n"
            "Файл не стёрт. Если убрали по ошибке — верните его на прежнее место.\n"
            "Окончательно удаляет только человек.\n")
        try:
            target.with_name(target.name + ".note.md").write_text(note, encoding="utf-8")
        except OSError:
            pass

    def disk(self) -> dict:
        """Сколько места осталось. Меньше 10 процентов — пульт предупреждает специалиста."""
        target = self.data_dir if self.data_dir.exists() else ROOT
        try:
            usage = shutil.disk_usage(target)
        except OSError as e:
            return {"ok": False, "low": False, "warning": None, "error": str(e)}
        free_pct = round(100 * usage.free / usage.total, 1) if usage.total else 0.0
        low = free_pct < LOW_DISK_PCT
        return {
            "ok": True,
            "free_pct": free_pct,
            "free_gb": round(usage.free / 1024 ** 3, 1),
            "total_gb": round(usage.total / 1024 ** 3, 1),
            "low": low,
            "warning": (f"Мало места на диске: свободно {free_pct}%. "
                        "Перенесите старые записи на флешку." if low else None),
        }

    # ---------- запись с цветной камеры ----------

    @property
    def camera_settings_path(self) -> Path:
        return self.data_dir / CAMERA_SETTINGS_FILE

    def camera_settings(self) -> dict:
        """Выбор клиники: режим и рабочая зона. Не читали — прочитаем с диска.

        Любая беда с файлом означает «выключено»: испорченная настройка не имеет
        права незаметно включить камеру.
        """
        path = self.camera_settings_path
        if self._camera_settings is not None and self._camera_settings_from == path:
            return dict(self._camera_settings)
        mode, zone = CAMERA_OFF, camera_zone()
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
            mode = normalize_camera_mode(saved.get("mode"))
            if saved.get("zone"):
                zone = normalize_zone(saved["zone"])
        except (OSError, ValueError, TypeError, AttributeError):
            pass                                 # файла нет или он испорчен — «выключено»
        settings = {"mode": mode, "zone": list(zone)}
        self._camera_settings, self._camera_settings_from = settings, path
        return dict(settings)

    def set_camera(self, mode: str | None = None, zone=None) -> dict:
        """Поменять режим записи с камеры. Чужое значение превращается в «выключено»."""
        current = self.camera_settings()
        if mode is not None:
            asked = str(mode).strip().lower()
            if asked not in CAMERA_MODES:
                raise HTTPException(422, f"режим бывает {', '.join(CAMERA_MODES)}")
            current["mode"] = asked
        if zone is not None:
            try:
                current["zone"] = list(normalize_zone(zone))
            except ValueError as e:
                raise HTTPException(422, str(e))
        path = self.camera_settings_path
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
        except OSError as e:
            raise HTTPException(500, f"настройка камеры не сохранилась: {e}")
        self._camera_settings, self._camera_settings_from = dict(current), path
        # «Выключено» действует сразу, даже если запись уже идёт: передумали —
        # значит с этой секунды с камеры не пишется ничего.
        if current["mode"] == CAMERA_OFF:
            self._stop_camera()
        return self.camera_state()

    def camera_state(self) -> dict:
        """Что показать в пульте: выбранный режим, список режимов, предупреждение."""
        settings = self.camera_settings()
        with self._lock:
            recorder = self._camera_rec
        modes = [{"id": name, "title": CAMERA_TITLES[name], "note": CAMERA_NOTES[name],
                  "warning": CAMERA_WARNING if name == "full" else None}
                 for name in CAMERA_MODES]
        state = {
            "mode": settings["mode"],
            "mode_title": CAMERA_TITLES[settings["mode"]],
            "modes": modes,
            "zone": settings["zone"],
            "warning": CAMERA_WARNING if settings["mode"] == "full" else None,
            "available": self.camera_available(),
            "on": bool(recorder is not None and recorder.running),
        }
        if recorder is not None and recorder.running:
            state["recording"] = recorder.state()
        if not state["available"] and settings["mode"] != CAMERA_OFF:
            state["hint"] = ("Режим выбран, но датчик не отдаёт цветные кадры: "
                             "камеру включают при запуске (SANDBOX_KINECT_COLOR=1).")
        return state

    def camera_preview(self, mode: str | None = None) -> bytes:
        """Один кадр — посмотреть, что попадёт в запись. Никуда не сохраняется.

        Ради этого метода всё и затевалось так: обещание «лицо в кадр не
        попадает» должно быть проверяемым глазами до того, как запись включат.
        Числа рабочей зоны посчитаны по чертежу; повесят датчик — проверят здесь
        и поправят зону, если камера встала иначе.
        """
        asked = normalize_camera_mode(mode or self.camera_settings()["mode"])
        if asked == CAMERA_OFF:
            asked = CAMERA_BOX                   # смотреть «ничего» незачем
        source = self.camera_source()
        frame = None
        if source is not None:
            try:
                frame = source.color_frame()
            except Exception as e:               # noqa: BLE001
                raise HTTPException(503, f"камера не отдала кадр: {e}")
        if frame is None:
            raise HTTPException(503, "камера не включена или кадров ещё не было")
        jpeg = camera_jpeg(frame.bgr, asked, self.camera_settings()["zone"])
        if jpeg is None:
            raise HTTPException(503, "кадр не получился")
        return jpeg

    def _start_camera(self, directory: Path, session: str, stamp: str) -> dict | None:
        """Запустить запись с камеры, если её включили. Молчаливо — если нет."""
        settings = self.camera_settings()
        if settings["mode"] == CAMERA_OFF:
            return None
        source = self.camera_source()
        if source is None:
            return None
        recorder = CameraRecorder(source, directory, prefix=f"{CAMERA_PREFIX}{stamp}",
                                  fps=int(os.environ.get("SANDBOX_CAMERA_FPS", "10")),
                                  mode=settings["mode"], zone=settings["zone"],
                                  session_id=session or "")
        recorder.start()
        self._camera_rec = recorder
        return recorder.state()

    def _stop_camera(self) -> dict | None:
        with self._lock:
            recorder, self._camera_rec = self._camera_rec, None
        if recorder is None:
            return None
        return recorder.stop()

    # ---------- запись видео ----------

    def record_state(self) -> dict:
        with self._lock:
            recorder, last = self._recorder, self._last_record
        if recorder is not None and recorder.running:
            return recorder.state()
        state = {"on": False, "mode": "mp4" if ffmpeg_path() else "frames",
                 "frames": 0, "seconds": 0.0, "ffmpeg": bool(ffmpeg_path()),
                 "session": self.current_session(), "notes": []}
        if last:
            state["last"] = last
        return state

    def start_record(self, session_id: str | None = None, fps: int | None = None) -> dict:
        with self._lock:
            if self._recorder is not None and self._recorder.running:
                raise HTTPException(409, "запись уже идёт")
            frames = self.frame_source()
            if frames is None:
                raise HTTPException(503, "конвейер кадров не подключён — записывать нечего")
            session = session_id or self.current_session()
            directory = self.session_dir(session)
            prefix = time.strftime("video-%H%M%S")
            recorder = LandscapeRecorder(frames, directory, prefix=prefix,
                                         fps=fps or int(os.environ.get("SANDBOX_RECORD_FPS", "12")),
                                         session_id=session or "")
            recorder.start()
            self._recorder = recorder
            state = recorder.state()
            # Камера — отдельной записью и только если её включили.
            camera = self._start_camera(directory, session or "",
                                        prefix.split("-", 1)[-1])
            if camera is not None:
                state["camera"] = camera
            return state

    def stop_record(self) -> dict:
        with self._lock:
            recorder, self._recorder = self._recorder, None
        if recorder is None:
            self._stop_camera()                  # камера без ландшафта не пишется
            raise HTTPException(409, "запись не идёт")
        result = recorder.stop()
        camera = self._stop_camera()
        if camera is not None:
            camera["seconds_h"] = human_seconds(camera.get("seconds", 0))
            result["camera"] = camera
        result["seconds_h"] = human_seconds(result.get("seconds", 0))
        self.forget()
        with self._lock:
            self._last_record = result
        return result

    def state(self) -> dict:
        """Всё про записи одной строкой — владельцу app.py для /api/state."""
        disk = self.disk()
        return {
            "recording": self.record_state(),
            "camera": self.camera_state(),
            "disk": disk,
            "disk_warning": disk.get("warning"),
            "ffmpeg": bool(ffmpeg_path()),
        }


MEDIA = MediaLibrary()


def configure(frames=None, session_id=None, root: Path | None = None,
              data_dir: Path | None = None, camera=None) -> None:
    """Короткий путь для app.py: media.configure(frames=..., session_id=...).

    camera — датчик с цветной камерой. Не передали — возьмём у конвейера пульта
    (frames.sensor), если он его показывает.
    """
    MEDIA.configure(frames=frames, session_id=session_id, root=root,
                    data_dir=data_dir, camera=camera)


def media_state() -> dict:
    """Что добавить в /api/state: идёт ли запись и не кончается ли место."""
    return MEDIA.state()


# --------------------------------------------------------------- ручки пульта

router = APIRouter(tags=["записи"])


@router.get("/api/media")
def api_media(session: str | None = None, kind: str | None = None,
              limit: int = Query(500, ge=1, le=5000)) -> dict:
    """Список записей: всех или одного занятия (?session=S-20260923-153012)."""
    items = MEDIA.list(session=session, kind=kind, limit=limit)
    # Один и тот же список под двумя именами: пульт читает media, договор об API
    # и запасная ручка в app.py — items. Так части сходятся, не переделывая друг друга.
    return {"ok": True, "session": session, "items": items, "media": items,
            "count": len(items), "recording": MEDIA.record_state(), "disk": MEDIA.disk()}


@router.get("/api/media/sessions")
def api_media_sessions() -> dict:
    """Занятия, у которых есть записи, — чтобы библиотека открывалась по папкам."""
    return {"ok": True, "sessions": MEDIA.sessions()}


@router.get("/media/{entry_id}", include_in_schema=False)
def media_file(entry_id: str, thumb: bool = False, frame: int = 1,
               download: bool = False) -> FileResponse:
    """Отдать файл записи.

    Без ?download=1 браузер показывает запись прямо на странице, с ним —
    предлагает сохранить. Видео идёт с перемоткой: FileResponse отвечает на
    заголовок Range, и ползунок в плеере работает, не скачивая файл целиком.
    """
    entry = MEDIA.find(entry_id)
    if entry is None:
        raise HTTPException(404, "запись не найдена")
    path = MEDIA.path_of(entry)
    shown = "attachment" if download else "inline"

    if entry["kind"] == "frames":                     # серия кадров: отдаём один кадр
        frames = sorted(path.glob("k-*.jpg"))
        if not frames:
            raise HTTPException(404, "в серии нет кадров")
        one = frames[min(max(frame, 1), len(frames)) - 1]
        return FileResponse(one, media_type="image/jpeg", filename=one.name,
                            content_disposition_type=shown)

    if thumb:
        small = path.with_name(path.stem + "-small.jpg")
        if small.exists():
            path = small

    if not path.is_file():
        raise HTTPException(404, "файл записи не найден")
    return FileResponse(path, media_type=entry["mime"], filename=path.name,
                        content_disposition_type=shown,
                        headers={"Cache-Control": "private, max-age=3600"})


@router.delete("/api/media/{entry_id}")
def api_media_delete(entry_id: str) -> dict:
    """Убрать запись: файл переезжает в data/_to_delete с запиской, а не стирается."""
    return MEDIA.remove(entry_id)


@router.get("/api/record")
def api_record_state() -> dict:
    return {"ok": True, "recording": MEDIA.record_state(), "disk": MEDIA.disk()}


@router.post("/api/record/start")
def api_record_start(body: RecordBody | None = None) -> dict:
    body = body or RecordBody()
    state = MEDIA.start_record(session_id=body.session, fps=body.fps)
    message = ("Пишу видео." if state.get("ffmpeg")
               else "Пишу кадрами: на этом ноутбуке нет ffmpeg, видео соберём позже.")
    return {"ok": True, "recording": state, "message": message, "disk": MEDIA.disk()}


@router.post("/api/record/stop")
def api_record_stop() -> dict:
    result = MEDIA.stop_record()
    return {"ok": True, "record": result,
            "items": MEDIA.list(session=result.get("session") or None),
            "recording": MEDIA.record_state(), "disk": MEDIA.disk()}


@router.get("/api/record/camera")
def api_camera_state() -> dict:
    """Режим записи с цветной камеры: какой выбран, какие бывают, чем грозит."""
    return {"ok": True, "camera": MEDIA.camera_state()}


@router.post("/api/record/camera")
def api_camera_set(body: CameraBody | None = None) -> dict:
    """Выбрать режим записи с камеры: off (по умолчанию) | box | full.

    Режим «полностью» пишет лицо ребёнка. Пульт обязан показать рядом с ним
    предупреждение: нужны согласия родителей и правила хранения — оно приходит
    в ответе полем warning.
    """
    body = body or CameraBody()
    state = MEDIA.set_camera(mode=body.mode, zone=body.zone)
    messages = {
        "off": "С камеры ничего не пишется.",
        "box": "Пишу только ящик: руки и песок, лицо в кадр не попадает.",
        "full": "Пишу весь кадр. Нужны согласия родителей и правила хранения.",
    }
    return {"ok": True, "camera": state, "message": messages[state["mode"]],
            "warning": state.get("warning")}


@router.get("/api/record/camera/preview", include_in_schema=False)
def api_camera_preview(mode: str | None = None) -> Response:
    """Показать один кадр так, как он ляжет в запись. Ничего не сохраняет.

    Без ?mode= берётся выбранный режим; если выбрано «выключено» — показывается
    «только ящик»: именно его и приходят проверять.
    """
    return Response(MEDIA.camera_preview(mode), media_type="image/jpeg",
                    headers={"Cache-Control": "no-store"})


@router.get("/api/media/ffmpeg", include_in_schema=False)
def api_ffmpeg() -> dict:
    """Есть ли на этом ноутбуке ffmpeg — видно в пульте, чтобы не гадать."""
    path = ffmpeg_path()
    return {"ok": True, "found": bool(path), "path": path or "",
            "version": ffmpeg_version(path) if path else "",
            "mode": "mp4" if path else "frames"}
