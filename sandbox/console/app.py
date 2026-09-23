"""Веб-пульт специалиста и страница проекции.

Запуск: python -m sandbox.console  (или софт/запустить.command двойным щелчком)

Пароля нет. Вместо входа — выбор одним касанием: кто ведёт занятие и с кем
занимаемся. Это подпись под записью, а не защита: защита в том, что пульт виден
только в сети клиники.

Что отдаёт сервер:
    /              пульт специалиста — открывается с телефона, планшета, ноутбука
    /projector     страница проекции — её выводят на проектор во весь экран
    /stream.mjpg   поток картинки для проектора (multipart/x-mixed-replace)
    /preview.mjpg  тот же поток мельче — для предпросмотра на телефоне
    /frame.jpg     один кадр (запасной путь, если браузер не тянет поток)
    /qr.svg        QR-код на адрес пульта
    /api/...       состояние, люди, занятия, режимы, пауза, заморозка, снимки
    /media/...     снимки и видео из библиотеки записей

Картинку считает фоновый конвейер sandbox/console/pipeline.py, списки людей и
занятий лежат в SQLite (sandbox/common/db.py, people.py, sessions.py).
Сервер только связывает одно с другим.
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import socket
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from sandbox.common.db import Database

from . import qr
from .people import BRIGHTNESS_RANGE, People
from .pipeline import CONTOUR_STEPS, MODES, PALETTES, FrameServer
from .sessions import Sessions

ROOT = Path(__file__).resolve().parents[2]
WEB = Path(__file__).parent / "web"
DATA = Path(os.environ.get("SANDBOX_DATA") or (ROOT / "data"))
SESSIONS_DIR = DATA / "sessions"
DB_PATH = DATA / "sandbox.db"

BOUNDARY = "sandboxframe"

# Признак «программу просят остановиться». Ставится в __main__ по Ctrl+C.
# Потоки кадров (/stream.mjpg, /preview.mjpg) сами по себе бесконечные: браузер
# держит соединение сколько угодно. Пока они открыты, сервер не может закрыться,
# поэтому по этому признаку они доигрывают кадр и завершаются.
SHUTDOWN = threading.Event()


# --------------------------------------------------------------------- пульт

class Console:
    """Состояние пульта: конвейер кадров, списки людей, журнал занятий."""

    def __init__(self) -> None:
        self.frames = FrameServer(fps=int(os.environ.get("SANDBOX_FPS", "20")))
        self.db = Database(DB_PATH)
        self.people = People(self.db)
        self.sessions = Sessions(self.db, DATA)
        self.port = int(os.environ.get("SANDBOX_PORT", "8080"))

    # --- занятие ---

    def session(self) -> dict | None:
        """Идущее занятие или None. Правда лежит в базе, а не в памяти сервера."""
        return self.sessions.running()

    def start_session(self, staff_id: int | None, child_id: int | None) -> dict:
        """Начать занятие и сразу применить настройки выбранного ребёнка."""
        # Сначала проверяем подпись: если карточки нет, прошлое занятие не трогаем.
        staff = self.people.get_staff(staff_id) if staff_id else None
        child = self.people.get_child(child_id) if child_id else None
        closed = self.sessions.stop()                 # прошлое занятие не забываем закрыть

        view = self.frames.state()
        mode = child["mode"] if child else view["mode"]
        brightness = child["brightness"] if child else view["brightness"]
        minutes = child["minutes"] if child else None

        self.frames.set_mode(mode)
        self.frames.set_brightness(brightness)
        self.frames.set_pause(False)
        self.frames.set_freeze(False)

        session = self.sessions.start(staff, child, mode, brightness, minutes)
        return {"session": session, "closed": closed}

    def stop_session(self) -> dict | None:
        report = self.sessions.stop()
        self.frames.set_freeze(False)
        self.frames.set_pause(False)
        return report

    def log(self, kind: str, detail: str = "") -> None:
        """Отметить событие в журнале идущего занятия (если оно идёт)."""
        session = self.session()
        if session:
            self.sessions.log(session["id"], kind, detail)

    # --- снимок ---

    def snapshot_dir(self, session: dict | None) -> Path:
        """Папка снимков: по занятию, а вне занятия — по дню. Имена латиницей."""
        return SESSIONS_DIR / (session["id"] if session else time.strftime("free-%Y%m%d"))

    def snapshot_path(self, folder: Path) -> Path:
        """Имя файла снимка по времени.

        Два снимка в одну секунду — обычное дело (специалист жмёт кнопку
        дважды), поэтому занятое имя дополняем номером: иначе второй снимок
        затёр бы первый, и в отчёте «середина» и «конец» оказались бы одной
        и той же картинкой. Проверено живьём 23.09.
        """
        stamp = time.strftime("snap-%H%M%S")
        path, n = folder / f"{stamp}.jpg", 1
        while path.exists():
            path, n = folder / f"{stamp}-{n}.jpg", n + 1
        return path

    def take_snapshot(self) -> dict:
        session = self.session()
        folder = self.snapshot_dir(session)
        path = self.frames.snapshot(self.snapshot_path(folder))
        thumb = path.with_name(path.stem + "-small.jpg")
        media = self.sessions.add_media(session["id"] if session else None, "photo",
                                        path, thumb if thumb.exists() else None)
        media["file"] = path.name
        return media

    def snapshots(self) -> list[dict]:
        session = self.session()
        return self.sessions.media(session["id"], kind="photo") if session else []


CONSOLE = Console()


def media_state() -> dict | None:
    """Что сообщает о себе библиотека записей: идёт ли запись, много ли места."""
    if media_module is None:
        return None
    try:
        return media_module.media_state()
    except Exception as e:                            # noqa: BLE001 — пульт не должен падать
        return {"error": str(e)}


def guard(call, *args, **kwargs):
    """Ошибки хранилища → понятные коды HTTP: не нашли — 404, неверно — 400."""
    try:
        return call(*args, **kwargs)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


# --------------------------------------------------------------------- адреса

def local_ip() -> str:
    """Адрес машины в локальной сети — тот, по которому её видят телефон и планшет."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))                # ничего не отправляет, только выбирает маршрут
        return s.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"
    finally:
        s.close()


def addresses(port: int | None = None) -> dict:
    """Адреса пульта и проекции: имя машины в сети + тот порт, на который зашли."""
    port = port or CONSOLE.port
    ip = local_ip()
    return {"ip": ip, "port": port,
            "console": f"http://{ip}:{port}/",
            "projector": f"http://{ip}:{port}/projector"}


# --------------------------------------------------------------------- сервер

@asynccontextmanager
async def lifespan(app: FastAPI):
    CONSOLE.sessions.close_stale()      # занятия, оставшиеся от прошлого запуска
    CONSOLE.frames.start()
    yield
    CONSOLE.frames.stop()


app = FastAPI(title="AR-песочница: пульт специалиста", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(WEB)), name="static")

# Библиотека записей (снимки и видео) живёт в отдельном модуле media.py — его
# делает другой участок работы. Если файла ещё нет, сервер поднимается без него:
# простая раздача снимков и списка записей остаётся здесь, в конце файла.
try:
    from . import media as media_module
    media_router = media_module.router
except Exception as e:                                # noqa: BLE001 — модуля может не быть
    media_module, media_router = None, None
    print(f"библиотека записей пока не подключена: {e}")

if media_router is not None:
    app.include_router(media_router)
    # Библиотеке нужно знать, откуда брать кадр и какое занятие идёт сейчас.
    media_module.configure(frames=CONSOLE.frames,
                           session_id=lambda: (CONSOLE.session() or {}).get("id"),
                           data_dir=DATA)


def covered_paths() -> set[str]:
    """Какие адреса из договора уже взял на себя модуль записей.

    Сравниваем без имён параметров: /media/{entry_id} и /media/{media_id} —
    один и тот же адрес, и подменять его своим нельзя.
    """
    if media_router is None:
        return set()
    return {re.sub(r"\{[^}]*\}", "{}", getattr(r, "path", ""))
            for r in getattr(media_router, "routes", [])}


MEDIA_COVERED = covered_paths()


@app.get("/", include_in_schema=False)
def page_console() -> FileResponse:
    return FileResponse(WEB / "index.html")


@app.get("/projector", include_in_schema=False)
def page_projector() -> FileResponse:
    return FileResponse(WEB / "projector.html")


# --- картинка ---

async def _stream(kind: str):
    """Поток кадров: браузер держит одно соединение и сам меняет картинку без мигания."""
    last = -1
    while not SHUTDOWN.is_set():
        data, seq = CONSOLE.frames.latest(kind)
        if data is not None and seq != last:
            last = seq
            head = (f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\n"
                    f"Content-Length: {len(data)}\r\n\r\n").encode("ascii")
            yield head + data + b"\r\n"
        await asyncio.sleep(0.02)


@app.get("/stream.mjpg", include_in_schema=False)
def stream_full() -> StreamingResponse:
    return StreamingResponse(_stream("full"),
                             media_type=f"multipart/x-mixed-replace; boundary={BOUNDARY}",
                             headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


@app.get("/preview.mjpg", include_in_schema=False)
def stream_preview() -> StreamingResponse:
    return StreamingResponse(_stream("preview"),
                             media_type=f"multipart/x-mixed-replace; boundary={BOUNDARY}",
                             headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


@app.get("/frame.jpg", include_in_schema=False)
def frame(kind: str = "full") -> Response:
    data, _ = CONSOLE.frames.latest("preview" if kind == "preview" else "full")
    if data is None:
        raise HTTPException(503, "кадра ещё нет")
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.get("/snapshots/{name:path}", include_in_schema=False)
def snapshot_file(name: str) -> FileResponse:
    """Прямая ссылка на файл занятия: /snapshots/<папка занятия>/<файл>.

    Этим адресом открываются снимки в отчёте — он работает всегда, даже если
    библиотека записей ещё не подключена.
    """
    path = (SESSIONS_DIR / name).resolve()
    if SESSIONS_DIR.resolve() not in path.parents or not path.is_file():
        raise HTTPException(404, "снимок не найден")
    return FileResponse(path)


@app.get("/qr.svg", include_in_schema=False)
def qr_code(request: Request, text: str | None = None) -> Response:
    try:
        svg = qr.svg(text or addresses(request.url.port)["console"], module=8, quiet=3)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return Response(svg, media_type="image/svg+xml", headers={"Cache-Control": "no-store"})


# --- общее состояние ---

@app.get("/api/state")
def api_state(request: Request) -> dict:
    """Всё, что нужно пульту, одним запросом: занятие, режим, датчик, люди."""
    view = CONSOLE.frames.state()
    session = CONSOLE.session()
    return {
        "session": session,
        "snapshots": session["snapshots"] if session else [],
        "mode": view["mode"],
        "mode_title": view["mode_title"],
        "modes": MODES,
        "paused": view["paused"],
        "frozen": view["frozen"],
        "brightness": view["brightness"],
        "sensor": {"title": view["sensor"], "demo": view["demo"], "opening": view["opening"],
                   "error": view["sensor_error"], "fps": view["fps"]},
        "calibration": {"done": view["calibrated"], "running": view["calibrating"],
                        "at": view["calibrated_at"]},
        "people": CONSOLE.people.everyone(),
        "view": view,                                  # полное состояние конвейера
        "palettes": PALETTES,
        "contour_steps": CONTOUR_STEPS,
        "media_api": media_router is not None,
        "media": media_state(),
        "addresses": addresses(request.url.port),
        "server_time": time.time(),
    }


# --- люди: кто ведёт и с кем занимаемся ---

class StaffIn(BaseModel):
    name: str


class ChildIn(BaseModel):
    alias: str


class ChildPatch(BaseModel):
    """Правка карточки.

    Пульт присылает настройки вложенным объектом settings и зовёт длительность
    duration_min, сервер внутри держит плоские поля. Принимаем оба написания:
    так части программы сходятся, не переделывая друг друга.
    """
    alias: str | None = None
    mode: str | None = None
    brightness: int | None = None
    minutes: int | None = None
    duration_min: int | None = None
    note: str | None = None
    settings: dict | None = None

    def fields(self) -> dict:
        inner = self.settings or {}

        def pick(*values):
            return next((v for v in values if v is not None), None)

        return {
            "alias": self.alias,
            "mode": pick(self.mode, inner.get("mode")),
            "brightness": pick(self.brightness, inner.get("brightness")),
            "minutes": pick(self.minutes, self.duration_min,
                            inner.get("minutes"), inner.get("duration_min")),
            "note": pick(self.note, inner.get("note")),
        }


@app.get("/api/people")
def api_people() -> dict:
    return CONSOLE.people.everyone()


@app.post("/api/people/staff")
def api_people_staff(body: StaffIn) -> dict:
    staff = guard(CONSOLE.people.add_staff, body.name)
    return {"ok": True, "staff": staff, "people": CONSOLE.people.everyone()}


@app.post("/api/people/child")
def api_people_child(body: ChildIn) -> dict:
    child = guard(CONSOLE.people.add_child, body.alias)
    return {"ok": True, "child": child, "people": CONSOLE.people.everyone()}


@app.patch("/api/people/child/{child_id}")
def api_people_child_patch(child_id: int, body: ChildPatch) -> dict:
    fields = body.fields()
    child = guard(CONSOLE.people.update_child, child_id, **fields)
    session = CONSOLE.session()
    if session and session["child"]["id"] == child["id"]:      # правят того, с кем занимаемся
        if fields["mode"] is not None:
            CONSOLE.frames.set_mode(child["mode"])
        if fields["brightness"] is not None:
            CONSOLE.frames.set_brightness(child["brightness"])
    return {"ok": True, "child": child, "people": CONSOLE.people.everyone()}


@app.delete("/api/people/child/{child_id}")
def api_people_child_delete(child_id: int) -> dict:
    """Убрать из списка. Занятия, снимки и отчёты остаются на месте."""
    child = guard(CONSOLE.people.hide_child, child_id)
    return {"ok": True, "child": child, "people": CONSOLE.people.everyone()}


# --- занятие ---

class SessionStart(BaseModel):
    staff_id: int | None = None
    child_id: int | None = None


class NoteIn(BaseModel):
    note: str = ""


@app.post("/api/session/start")
def api_session_start(body: SessionStart | None = None) -> dict:
    body = body or SessionStart()
    started = guard(CONSOLE.start_session, body.staff_id, body.child_id)
    return {"ok": True, "session": started["session"], "closed": started["closed"],
            "view": CONSOLE.frames.state()}


@app.post("/api/session/stop")
def api_session_stop() -> dict:
    report = CONSOLE.stop_session()
    return {"ok": True, "session": report, "report": report}


@app.get("/api/sessions")
def api_sessions(limit: int = 50) -> dict:
    return {"sessions": CONSOLE.sessions.history(limit)}


@app.get("/api/sessions/{session_id}")
def api_session(session_id: str) -> dict:
    return {"session": guard(CONSOLE.sessions.report, session_id)}


@app.patch("/api/sessions/{session_id}")
def api_session_note(session_id: str, body: NoteIn) -> dict:
    return {"ok": True, "session": guard(CONSOLE.sessions.set_note, session_id, body.note)}


# --- режимы, пауза, заморозка ---

class ModeIn(BaseModel):
    mode: str


class Toggle(BaseModel):
    on: bool = True


class BrightnessIn(BaseModel):
    value: int


@app.post("/api/mode")
def api_mode(body: ModeIn) -> dict:
    mode = guard(CONSOLE.frames.set_mode, body.mode)
    CONSOLE.log("mode", mode)
    return {"ok": True, "mode": mode, "view": CONSOLE.frames.state()}


@app.post("/api/pause")
def api_pause(body: Toggle) -> dict:
    """Пауза: проекция гаснет в чёрное, занятие и таймер идут дальше."""
    on = CONSOLE.frames.set_pause(body.on)
    CONSOLE.log("pause", "on" if on else "off")
    return {"ok": True, "paused": on, "view": CONSOLE.frames.state()}


@app.post("/api/freeze")
def api_freeze(body: Toggle) -> dict:
    """Заморозка: картинка замирает, песок можно трогать."""
    on = CONSOLE.frames.set_freeze(body.on)
    CONSOLE.log("freeze", "on" if on else "off")
    return {"ok": True, "frozen": on, "view": CONSOLE.frames.state()}


@app.post("/api/brightness")
def api_brightness(body: BrightnessIn) -> dict:
    value = guard(CONSOLE.frames.set_brightness, body.value)
    CONSOLE.log("brightness", str(value))
    return {"ok": True, "brightness": value, "view": CONSOLE.frames.state()}


# --- калибровка, снимок, старые настройки ---

@app.post("/api/calibrate")
def api_calibrate() -> dict:
    CONSOLE.frames.calibrate()
    CONSOLE.log("calibrate")
    return {"ok": True, "message": "Запоминаю ровный песок…"}


@app.post("/api/snapshot")
def api_snapshot() -> dict:
    try:
        item = CONSOLE.take_snapshot()
    except RuntimeError as e:
        raise HTTPException(503, str(e)) from e
    return {"ok": True, "snapshot": item, "snapshots": CONSOLE.snapshots()}


class ViewSettings(BaseModel):
    """Ручная подстройка поверх режима: палитра, шаг горизонталей, яркость.

    Яркость принимаем и здесь: ползунок «Мелкие настройки» на пульте шлёт её
    вместе с остальной подстройкой одним адресом. Раньше она молча терялась —
    сервер отвечал «хорошо», а проекция оставалась прежней. Проверено 23.09.
    """
    palette: str | None = None
    contour_step_mm: int | None = None
    brightness: int | None = None


@app.post("/api/settings")
def api_settings(body: ViewSettings) -> dict:
    if body.palette is not None:
        guard(CONSOLE.frames.set_palette, body.palette)
    if body.contour_step_mm is not None:
        guard(CONSOLE.frames.set_contour_step, body.contour_step_mm)
    if body.brightness is not None:
        value = guard(CONSOLE.frames.set_brightness, body.brightness)
        CONSOLE.log("brightness", str(value))
    return {"ok": True, "view": CONSOLE.frames.state()}


# --- библиотека записей: запасные ручки, пока нет media.py ---
#
# Если модуль записей на месте, эти адреса заняты им и здесь не объявляются:
# двух обработчиков на один адрес быть не должно.

if "/api/media" not in MEDIA_COVERED:

    @app.get("/api/media")
    def api_media(session: str | None = None) -> dict:
        items = CONSOLE.sessions.media(session)
        return {"ok": True, "session": session, "items": items, "media": items,
                "count": len(items)}

if "/media/{}" not in MEDIA_COVERED:

    @app.get("/media/{media_id}", include_in_schema=False)
    def media_file(media_id: str, size: str | None = None) -> FileResponse:
        media = guard(CONSOLE.sessions.get_media, media_id)
        stored = media["thumb_path"] if (size == "small" and media["thumb_path"]) \
            else media["path"]
        path = CONSOLE.sessions.absolute(stored)
        if not path.is_file():
            raise HTTPException(404, "файл записи не найден")
        return FileResponse(path)

if "/api/media/{}" not in MEDIA_COVERED:

    @app.delete("/api/media/{media_id}")
    def api_media_delete(media_id: str) -> dict:
        media = guard(CONSOLE.sessions.delete_media, media_id)
        return {"ok": True, "media": media}

if "/api/record/start" not in MEDIA_COVERED:

    @app.post("/api/record/start")
    @app.post("/api/record/stop")
    def api_record_missing() -> dict:
        raise HTTPException(503, "запись видео появится вместе с модулем media.py")


@app.get("/health")
def health() -> dict:
    view = CONSOLE.frames.state()
    session = CONSOLE.session()
    free_pct = None
    try:
        usage = shutil.disk_usage(DATA if DATA.exists() else ROOT)
        free_pct = round(100 * usage.free / usage.total, 1)
    except OSError:
        pass
    return {"ok": True, "sensor": view["sensor"], "demo": view["demo"], "fps": view["fps"],
            "mode": view["mode"], "paused": view["paused"], "frozen": view["frozen"],
            "brightness": view["brightness"],
            "session": session["id"] if session else None,
            "brightness_range": list(BRIGHTNESS_RANGE),
            "disk_free_pct": free_pct}
