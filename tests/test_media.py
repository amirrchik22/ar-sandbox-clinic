"""Библиотека записей: хранилище, запись видео, ручки пульта.

Тесты идут без железа и без ffmpeg: кадры подсовывает поддельный источник,
данные пишутся во временную папку, поиск ffmpeg выключен (SANDBOX_FFMPEG=off),
чтобы запись всегда шла запасным путём — кадрами.
"""
import json
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import namedtuple

import pytest

os.environ.setdefault("SANDBOX_SENSOR", "fake")

from fastapi import HTTPException                                            # noqa: E402

from sandbox.console.media import (LOW_DISK_PCT, MediaLibrary, human_size,   # noqa: E402
                                   media_id, router, safe_name)
from sandbox.recorder.heightmap_writer import (HeightmapWriter, count_frames,  # noqa: E402
                                               read_header, read_heightmaps)
from sandbox.recorder import main as recorder_main                           # noqa: E402
from sandbox.recorder.main import FRAMES_FPS, LandscapeRecorder              # noqa: E402
from sandbox.recorder.video import (VideoRecorder, concat_cmd,               # noqa: E402
                                    ffmpeg_mjpeg_segments_cmd, ffmpeg_path,
                                    ffmpeg_segments_cmd, has_ffmpeg)

# Однопиксельный JPEG — годится и как снимок, и как кадр записи.
JPEG = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb004300ffffffffffffffffffffffffffff"
    "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
    "ffffffffffffffffffffffffffffffffffffffffffc00011080001000103012200021101031101"
    "ffc4001f0000010501010101010100000000000000000102030405060708090a0bffc400b51000"
    "02010303020405030504040000017d01020300041105122131410613516107227114328191a108"
    "2342b1c11552d1f02433627282090a161718191a25262728292a3435363738393a434445464748"
    "494a535455565758595a636465666768696a737475767778797a838485868788898a9293949596"
    "9798999aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7b8b9bac2c3c4c5c6c7c8c9cad2d3d4d5d6d7d8d9"
    "dae1e2e3e4e5e6e7e8e9eaf1f2f3f4f5f6f7f8f9faffda0008010100003f00fbfeffd9")


class FakeFrames:
    """Поддельный конвейер: отдаёт готовый JPEG и считает номер кадра, как настоящий."""

    def __init__(self, data: bytes | None = JPEG):
        self.data = data
        self.seq = 0

    def latest(self, kind: str = "full"):
        self.seq += 1
        return self.data, self.seq


@pytest.fixture
def library(tmp_path, monkeypatch):
    """Чистое хранилище во временной папке, ffmpeg выключен."""
    monkeypatch.setenv("SANDBOX_FFMPEG", "off")
    lib = MediaLibrary(data_dir=tmp_path)
    lib.configure(frames=FakeFrames(), session_id=lambda: "S-20260923-101010")
    return lib


# ------------------------------------------------------------------ мелочи

def test_адрес_записи_устойчив():
    assert media_id("2026/09/23/S-1/snap.jpg") == media_id("2026/09/23/S-1/snap.jpg")
    assert media_id("2026/09/23/S-1/snap.jpg") != media_id("2026/09/23/S-2/snap.jpg")
    assert len(media_id("x")) == 12


def test_имя_папки_без_косых_черт():
    assert safe_name("../../etc") == "etc"          # наверх из папки не уйти
    assert "/" not in safe_name("S-1/../..")
    assert safe_name("   ") == "без-занятия"
    assert safe_name("S-20260923-101010") == "S-20260923-101010"


def test_размер_по_русски():
    assert human_size(512) == "512 Б"
    assert human_size(2048) == "2 КБ"
    assert human_size(5 * 1024 * 1024) == "5.0 МБ"


# ------------------------------------------------------------------ хранилище

def test_папка_занятия_по_дате(library):
    directory = library.session_dir("S-20260923-101010", when=time.mktime(
        (2026, 9, 23, 10, 10, 10, 0, 0, -1)))
    assert directory.exists()
    assert directory.parts[-4:] == ("2026", "09", "23", "S-20260923-101010")


def test_снимок_ложится_в_занятие_и_виден_в_списке(library):
    entry = library.save_snapshot(JPEG, "S-20260923-101010")
    assert entry["kind"] == "snapshot"
    assert entry["session"] == "S-20260923-101010"
    assert entry["url"] == f"/media/{entry['id']}"
    assert entry["size"] == len(JPEG)
    items = library.list()
    assert [e["id"] for e in items] == [entry["id"]]
    assert library.find(entry["id"])["name"] == entry["name"]


def test_два_снимка_в_одну_секунду_не_затирают_друг_друга(library):
    when = time.time()
    first = library.save_snapshot(JPEG, "S-1", when=when)
    second = library.save_snapshot(JPEG, "S-1", when=when)
    assert first["name"] != second["name"]
    assert len(library.list()) == 2


def test_миниатюра_не_идёт_отдельной_строкой(library):
    entry = library.save_snapshot(JPEG, "S-1")
    path = library.path_of(entry)
    path.with_name(path.stem + "-small.jpg").write_bytes(JPEG)
    library.forget()
    items = library.list()
    assert len(items) == 1
    assert items[0]["thumb"].endswith("?thumb=1")


def test_список_фильтруется_по_занятию_и_типу(library):
    library.save_snapshot(JPEG, "S-1")
    library.save_snapshot(JPEG, "S-2")
    video = library.session_dir("S-2") / "video-101010_000.mp4"
    video.write_bytes(b"mp4" * 100)
    library.forget()
    assert len(library.list()) == 3
    assert len(library.list(session="S-2")) == 2
    assert len(library.list(session="S-2", kind="video")) == 1
    assert library.list(session="нет-такого") == []


def test_серия_кадров_это_одна_запись(library):
    directory = library.session_dir("S-1")
    frames_dir = directory / "video-101010.frames"
    frames_dir.mkdir()
    for i in range(1, 4):
        (frames_dir / f"k-{i:06d}.jpg").write_bytes(JPEG)
    library.forget()
    items = library.list()
    assert len(items) == 1                       # три кадра, а строка одна
    assert items[0]["kind"] == "frames"
    assert items[0]["frames"] == 3
    assert "ffmpeg" in items[0]["hint"]


def test_занятия_с_записями_считаются(library):
    library.save_snapshot(JPEG, "S-1")
    library.save_snapshot(JPEG, "S-1")
    (library.session_dir("S-2") / "video-1_000.mp4").write_bytes(b"x" * 10)
    library.forget()
    sessions = {s["session"]: s for s in library.sessions()}
    assert sessions["S-1"]["snapshots"] == 2
    assert sessions["S-2"]["videos"] == 1


def test_удаление_переносит_в_to_delete_с_запиской(library):
    entry = library.save_snapshot(JPEG, "S-1")
    path = library.path_of(entry)
    path.with_name(path.stem + "-small.jpg").write_bytes(JPEG)
    library.forget()

    result = library.remove(entry["id"])
    moved = library.trash / os.path.basename(result["moved_to"])
    assert not path.exists()                     # из библиотеки ушло
    assert moved.exists()                        # но файл цел
    note = moved.with_name(moved.name + ".note.md")
    assert note.exists()
    text = note.read_text(encoding="utf-8")
    assert "S-1" in text and "не стёрт" in text
    assert moved.with_name(moved.stem + "-small.jpg").exists()   # миниатюра уехала следом
    assert library.list() == []
    with pytest.raises(HTTPException):
        library.remove(entry["id"])              # второй раз — записи уже нет


def test_чужой_путь_не_отдаётся(library):
    with pytest.raises(HTTPException):
        library.path_of({"path": "../../секрет.txt"})


def test_место_на_диске_и_предупреждение(library):
    disk = library.disk()
    assert disk["ok"] is True
    assert 0 <= disk["free_pct"] <= 100
    assert disk["low"] == (disk["free_pct"] < LOW_DISK_PCT)
    assert (disk["warning"] is None) == (not disk["low"])
    state = library.state()
    assert "recording" in state and "disk" in state
    assert state["disk_warning"] == disk["warning"]


def test_предупреждение_когда_места_мало(library, monkeypatch):
    """Диск почти полон — пульт обязан сказать об этом специалисту."""
    import shutil

    Usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda path: Usage(1000, 950, 50))
    disk = library.disk()
    assert disk["free_pct"] == 5.0
    assert disk["low"] is True
    assert "Мало места" in disk["warning"]


# ------------------------------------------------------------------ команды ffmpeg

def test_команда_mjpeg_пишет_сегментами_по_минуте():
    cmd = ffmpeg_mjpeg_segments_cmd(12, "/tmp/зап", "video", exe="ffmpeg")
    assert "image2pipe" in cmd and "mjpeg" in cmd
    assert cmd[cmd.index("-segment_time") + 1] == "60"
    assert cmd[cmd.index("-framerate") + 1] == "12"
    assert cmd[-1].endswith("video_%03d.mp4")
    assert "-an" in cmd                                  # звук не пишем


def test_команда_сырых_кадров_описывает_размер():
    cmd = ffmpeg_segments_cmd(1024, 768, 20, "/tmp/зап", "video")
    assert cmd[cmd.index("-s") + 1] == "1024x768"
    assert cmd[cmd.index("-pix_fmt") + 1] == "bgr24"
    assert "libx264" in cmd


def test_железный_кодировщик_объявляет_устройство_до_входа():
    cmd = ffmpeg_segments_cmd(640, 480, 15, "/tmp/зап", "v", hw="vaapi")
    assert cmd.index("-vaapi_device") < cmd.index("-i")
    assert "h264_vaapi" in cmd
    assert "h264_nvenc" in ffmpeg_segments_cmd(640, 480, 15, "/tmp", "v", hw="nvenc")


def test_склейка_без_перекодирования():
    cmd = concat_cmd("/tmp/list.txt", "/tmp/out.mp4")
    assert "concat" in cmd and cmd[cmd.index("-c") + 1] == "copy"


def test_ffmpeg_можно_выключить_переменной(monkeypatch):
    monkeypatch.setenv("SANDBOX_FFMPEG", "off")
    assert ffmpeg_path() is None
    assert has_ffmpeg() is False


# ------------------------------------------------------------------ запись

def test_без_ffmpeg_кадры_сохраняются_и_в_журнале_честно(tmp_path, monkeypatch):
    monkeypatch.setenv("SANDBOX_FFMPEG", "off")
    rec = VideoRecorder(tmp_path, prefix="video", fps=10)
    rec.start()
    for _ in range(3):
        rec.feed(JPEG)
    result = rec.stop()

    assert result["mode"] == "frames"
    assert result["frames"] == 3
    assert result["files"] == ["video.frames"]
    frames = sorted((tmp_path / "video.frames").glob("k-*.jpg"))
    assert len(frames) == 3 and frames[0].read_bytes() == JPEG
    журнал = (tmp_path / "video.log").read_text(encoding="utf-8")
    assert "ffmpeg" in журнал                       # сказано, почему нет видео


def test_запись_ландшафта_берёт_кадры_у_конвейера(tmp_path, monkeypatch):
    monkeypatch.setenv("SANDBOX_FFMPEG", "off")
    frames = FakeFrames()
    rec = LandscapeRecorder(frames, tmp_path, prefix="video", fps=20, session_id="S-1")
    rec.start()
    assert rec.running is True
    time.sleep(0.8)
    state = rec.state()
    result = rec.stop()

    assert state["on"] is True and state["mode"] == "frames"
    assert result["frames"] >= 2
    assert result["session"] == "S-1"
    assert rec.running is False


def test_без_ffmpeg_кадры_берутся_реже(tmp_path, monkeypatch):
    """Кадры весят больше видео — в запасном режиме берём их реже, чтобы не забить диск."""
    monkeypatch.setenv("SANDBOX_FFMPEG", "off")
    rec = LandscapeRecorder(FakeFrames(), tmp_path, prefix="video", fps=20)
    assert rec.frames_fps == FRAMES_FPS < rec.fps


def test_запись_сама_встаёт_когда_диск_кончается(tmp_path, monkeypatch):
    monkeypatch.setenv("SANDBOX_FFMPEG", "off")
    monkeypatch.setattr(recorder_main, "DISK_CHECK_TICKS", 1)
    Usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr(recorder_main.shutil, "disk_usage", lambda path: Usage(1000, 995, 5))

    rec = LandscapeRecorder(FakeFrames(), tmp_path, prefix="video", fps=20)
    rec.start()
    for _ in range(50):
        if not rec.running:
            break
        time.sleep(0.05)
    result = rec.stop()
    assert result["stopped_by_disk"] is True
    assert any("остановил запись" in note for note in result["notes"])


def test_пустой_конвейер_не_роняет_запись(tmp_path, monkeypatch):
    monkeypatch.setenv("SANDBOX_FFMPEG", "off")
    rec = LandscapeRecorder(FakeFrames(data=None), tmp_path, prefix="video", fps=20)
    rec.start()
    time.sleep(0.2)
    result = rec.stop()
    assert result["frames"] == 0                    # кадров нет, но никто не упал


def _stub_ffmpeg(tmp_path):
    """Поддельный ffmpeg: читает кадры из stdin и пишет файл, как настоящий.

    Нужен, чтобы проверить путь mp4 на машине, где ffmpeg не установлен:
    запуск подпроцесса, подача кадров, закрытие и поиск готовых файлов.
    """
    stub = tmp_path / "ffmpeg-stub.py"
    stub.write_text(
        "import sys\n"
        "data = sys.stdin.buffer.read()\n"
        "out = sys.argv[-1].replace('%03d', '000')\n"
        "open(out, 'wb').write(data)\n", encoding="utf-8")
    exe = tmp_path / "ffmpeg"
    exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{stub}" "$@"\n', encoding="utf-8")
    exe.chmod(0o755)
    return str(exe)


def test_с_подставным_ffmpeg_получается_файл(tmp_path):
    rec = VideoRecorder(tmp_path, prefix="video", fps=10, exe=_stub_ffmpeg(tmp_path))
    rec.start()
    assert rec.mode == "mp4"
    for _ in range(4):
        rec.feed(JPEG)
    result = rec.stop()

    assert result["mode"] == "mp4"
    assert result["files"] == ["video_000.mp4"]
    assert result["frames"] == 4
    assert result["code"] == 0
    assert (tmp_path / "video_000.mp4").read_bytes() == JPEG * 4


def test_оборванный_ffmpeg_не_теряет_кадры(tmp_path):
    """ffmpeg упал посреди занятия — дальше пишем кадрами, ничего не теряется."""
    rec = VideoRecorder(tmp_path, prefix="video", fps=10, exe=_stub_ffmpeg(tmp_path))
    rec.start()
    rec.feed(JPEG)
    rec.proc.stdin.close()                      # как будто ffmpeg закрыл вход
    rec.feed(JPEG)

    assert rec.mode == "frames"
    assert any("оборвался" in note for note in rec.notes)
    result = rec.stop()
    assert result["files"] == ["video.frames"]
    assert len(list((tmp_path / "video.frames").glob("k-*.jpg"))) == 1


@pytest.mark.skipif(not has_ffmpeg(), reason="на этой машине нет ffmpeg")
def test_с_ffmpeg_получается_mp4(tmp_path):
    rec = VideoRecorder(tmp_path, prefix="video", fps=10)
    rec.start()
    for _ in range(10):
        rec.feed(JPEG)
    result = rec.stop()
    assert result["mode"] == "mp4"
    assert result["files"] and result["files"][0].endswith(".mp4")


# ------------------------------------------------------------------ карта высот

def test_карта_высот_пишется_и_читается(tmp_path):
    import numpy as np

    path = tmp_path / "ландшафт.hmz"
    with HeightmapWriter(path, width=4, height=3, fps=10, session_id="S-1") as writer:
        writer.write(0.0, np.zeros((3, 4)))
        writer.write(0.1, np.full((3, 4), 12.4))
        assert writer.frames == 2
        with pytest.raises(ValueError):
            writer.write(0.2, np.zeros((2, 2)))      # размер по ходу записи не меняется

    header = read_header(path)
    assert header["w"] == 4 and header["h"] == 3 and header["session"] == "S-1"
    frames = list(read_heightmaps(path))
    assert len(frames) == 2 == count_frames(path)
    assert frames[1][1].tolist() == [[12] * 4] * 3   # миллиметры, округлённые


# ------------------------------------------------------------------ ручки пульта

def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    """Отдельный сервер только с ручками записей — без конвейера и датчика."""
    import uvicorn
    from fastapi import FastAPI

    from sandbox.console.media import MEDIA

    os.environ["SANDBOX_FFMPEG"] = "off"
    data = tmp_path_factory.mktemp("media-data")
    MEDIA.configure(data_dir=data, frames=FakeFrames(), session_id=lambda: "S-ТЕСТ")

    app = FastAPI()
    app.include_router(router)
    port = _free_port()
    running = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=running.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            urllib.request.urlopen(base + "/api/media", timeout=1).read()
            break
        except Exception:
            time.sleep(0.1)
    yield base, MEDIA
    running.should_exit = True
    thread.join(timeout=5)
    os.environ.pop("SANDBOX_FFMPEG", None)


def _url(base: str, path: str) -> str:
    """Условные имена бывают русскими — адрес надо закодировать, как это делает браузер."""
    return base + urllib.parse.quote(path, safe="/?=&")


def get(base: str, path: str, headers: dict | None = None):
    req = urllib.request.Request(_url(base, path), headers=headers or {})
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.status, r.read(), dict(r.headers)


def post(base: str, path: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else b""
    req = urllib.request.Request(_url(base, path), data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status, json.loads(r.read())


def delete(base: str, path: str):
    req = urllib.request.Request(_url(base, path), method="DELETE")
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.status, json.loads(r.read())


def test_ручка_списка_отдаёт_записи_и_место(server):
    base, media = server
    media.save_snapshot(JPEG, "S-ТЕСТ")
    status, body, _ = get(base, "/api/media")
    answer = json.loads(body)
    assert status == 200 and answer["ok"] is True
    assert answer["count"] >= 1
    assert answer["items"][0]["session"] == "S-ТЕСТ"
    assert "free_pct" in answer["disk"]
    assert answer["recording"]["on"] is False

    # Тот же список под именем media: пульт читает его так, договор — как items.
    assert answer["media"] == answer["items"]

    только_чужое = json.loads(get(base, "/api/media?session=S-ДРУГОЕ")[1])
    assert только_чужое["items"] == [] and только_чужое["media"] == []


def test_ручка_отдаёт_снимок_картинкой(server):
    base, media = server
    entry = media.save_snapshot(JPEG, "S-ТЕСТ")
    status, body, headers = get(base, entry["url"])
    assert status == 200
    assert headers["content-type"] == "image/jpeg"
    assert body == JPEG
    with pytest.raises(urllib.error.HTTPError) as e:
        get(base, "/media/000000000000")
    assert e.value.code == 404


def test_запись_показывается_в_браузере_и_скачивается(server):
    """Без ?download=1 — смотрим на странице, с ним — сохраняем файл."""
    base, media = server
    entry = media.save_snapshot(JPEG, "S-ТЕСТ")
    assert "inline" in get(base, entry["url"])[2]["content-disposition"]
    assert "attachment" in get(base, entry["download"])[2]["content-disposition"]


def test_видео_перематывается(server):
    base, media = server
    path = media.session_dir("S-ТЕСТ") / "video-090000_000.mp4"
    path.write_bytes(bytes(range(256)) * 20)
    media.forget()
    entry = next(e for e in media.list(kind="video") if e["name"] == path.name)

    status, body, headers = get(base, entry["url"])
    assert status == 200 and headers.get("accept-ranges") == "bytes"
    assert headers["content-type"] == "video/mp4"

    status, part, headers = get(base, entry["url"], {"Range": "bytes=10-19"})
    assert status == 206                                  # браузер получил кусок
    assert part == path.read_bytes()[10:20]
    assert headers["content-range"] == f"bytes 10-19/{path.stat().st_size}"


def test_кадр_серии_отдаётся_по_номеру(server):
    base, media = server
    frames_dir = media.session_dir("S-ТЕСТ") / "video-080000.frames"
    frames_dir.mkdir(exist_ok=True)
    (frames_dir / "k-000001.jpg").write_bytes(JPEG)
    (frames_dir / "k-000002.jpg").write_bytes(JPEG + b"\x00")
    media.forget()
    entry = next(e for e in media.list(kind="frames") if e["name"] == frames_dir.name)

    assert get(base, entry["url"])[1] == JPEG                       # по умолчанию первый
    assert get(base, entry["url"] + "?frame=2")[1] == JPEG + b"\x00"
    assert get(base, entry["url"] + "?frame=99")[1] == JPEG + b"\x00"   # дальше последнего
    assert get(base, entry["thumb"])[0] == 200


def test_удаление_через_ручку(server):
    base, media = server
    entry = media.save_snapshot(JPEG, "S-УБРАТЬ")
    status, answer = delete(base, f"/api/media/{entry['id']}")
    assert status == 200 and answer["ok"] is True
    assert media.trash.exists()
    with pytest.raises(urllib.error.HTTPError) as e:
        get(base, entry["url"])
    assert e.value.code == 404
    with pytest.raises(urllib.error.HTTPError) as e:
        delete(base, f"/api/media/{entry['id']}")
    assert e.value.code == 404


def test_запись_стартует_и_останавливается(server):
    base, media = server
    started = post(base, "/api/record/start", {"session": "S-ЗАПИСЬ"})[1]
    assert started["ok"] is True
    assert started["recording"]["on"] is True
    assert started["recording"]["mode"] == "frames"          # ffmpeg выключен
    assert "ffmpeg" in started["message"]

    with pytest.raises(urllib.error.HTTPError) as e:         # второй старт не пускаем
        post(base, "/api/record/start")
    assert e.value.code == 409

    assert json.loads(get(base, "/api/record")[1])["recording"]["on"] is True
    time.sleep(0.5)

    stopped = post(base, "/api/record/stop")[1]
    assert stopped["record"]["frames"] >= 1
    assert stopped["record"]["session"] == "S-ЗАПИСЬ"
    assert stopped["recording"]["on"] is False
    assert any(item["kind"] == "frames" for item in stopped["items"])

    with pytest.raises(urllib.error.HTTPError) as e:         # стоп без записи
        post(base, "/api/record/stop")
    assert e.value.code == 409


def test_ручка_про_ffmpeg_говорит_правду(server):
    base, _ = server
    answer = json.loads(get(base, "/api/media/ffmpeg")[1])
    assert answer["found"] is False                           # в тестах поиск выключен
    assert answer["mode"] == "frames"
