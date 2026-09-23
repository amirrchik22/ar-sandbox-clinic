"""Запись с цветной камеры датчика: режимы, обрезка по ящику, ручка пульта.

Главное, что проверяют эти тесты, — обещание клинике: по умолчанию с камеры не
пишется ничего, а в режиме «только ящик» лицо в кадр не попадает. Поэтому здесь
много проверок «ничего не записалось» — это не перестраховка, это и есть предмет
договорённости.

Железо не нужно: датчик подменён, кадры рисуются numpy, ffmpeg выключен.
"""
from __future__ import annotations

import io
import json
import os
import struct
import time

import numpy as np
import pytest

os.environ.setdefault("SANDBOX_SENSOR", "fake")

from fastapi import HTTPException                                        # noqa: E402

from sandbox.console.media import CAMERA_PREFIX, MediaLibrary            # noqa: E402
from sandbox.recorder.main import CameraRecorder                         # noqa: E402
from sandbox.recorder.video import (CAMERA_FULL, CAMERA_MODES, CAMERA_OFF,  # noqa: E402
                                    CAMERA_WARNING, DEFAULT_CAMERA_ZONE,
                                    MAX_BOX_ZONE, camera_jpeg, camera_zone,
                                    crop_zone, encode_jpeg,
                                    normalize_camera_mode, normalize_zone)
from sandbox.sensors.base import ColorFrame                              # noqa: E402
from sandbox.sensors.kinect2 import (COLOR_TAIL, DEPTH_TAIL, Kinect2Error,  # noqa: E402
                                     Kinect2Sensor)

WIDTH, HEIGHT = 960, 540


def frame_with_face() -> np.ndarray:
    """Кадр камеры: песок в рабочей зоне, «лицо» — яркое пятно у нижнего края.

    Лицо нарочно лежит ниже рабочей зоны: так и будет живьём, датчик смотрит
    сверху вниз, ребёнок стоит у ближнего борта.
    """
    bgr = np.zeros((HEIGHT, WIDTH, 3), np.uint8)
    bgr[:, :, 0] = 40                                  # общий фон — пол кабинета
    x, y, w, h = DEFAULT_CAMERA_ZONE
    x0, y0 = int(x * WIDTH), int(y * HEIGHT)
    x1, y1 = int((x + w) * WIDTH), int((y + h) * HEIGHT)
    bgr[y0:y1, x0:x1] = (60, 170, 220)                 # песок
    bgr[HEIGHT - 60:, WIDTH // 2 - 40:WIDTH // 2 + 40] = (255, 255, 255)   # лицо
    return bgr


# Где в кадре нарисовано «лицо» — его не должно остаться после обрезки.
FACE = (slice(HEIGHT - 60, HEIGHT), slice(WIDTH // 2 - 40, WIDTH // 2 + 40))

NO_FRAME = object()          # «камера кадров не отдаёт» — не то же самое, что «по умолчанию»


class FakeCamera:
    """Датчик с цветной камерой: отдаёт новый кадр на каждый вызов."""

    def __init__(self, frame=NO_FRAME, still: bool = False):
        self.frame = frame_with_face() if frame is NO_FRAME else frame
        self.still = still                    # True = время кадра не меняется
        self.calls = 0

    def color_frame(self):
        self.calls += 1
        if self.frame is None:
            return None
        t = 100.0 if self.still else time.monotonic()
        return ColorFrame(t, self.frame)


# ------------------------------------------------------------------ режимы

def test_режимов_три_и_первый_выключено():
    assert CAMERA_MODES == ("off", "box", "full")
    assert CAMERA_MODES[0] == CAMERA_OFF


@pytest.mark.parametrize("value", [None, "", "  ", "видео", "ON", 5, "offf"])
def test_непонятный_режим_это_выключено(value):
    assert normalize_camera_mode(value) == CAMERA_OFF


def test_знакомый_режим_переживает_регистр_и_пробелы():
    assert normalize_camera_mode(" BOX ") == "box"
    assert normalize_camera_mode("full") == "full"


# ------------------------------------------------------------------ рабочая зона

def test_зона_по_умолчанию_это_расчёт_по_геометрии():
    # Ящик 1,2 x 0,8 м в кадре камеры на высоте 1,252 м над песком.
    assert camera_zone() == normalize_zone(DEFAULT_CAMERA_ZONE)
    x, y, w, h = camera_zone()
    assert 0.5 < w < 0.65 and 0.6 < h < 0.75
    assert x + w <= 1.0 and y + h <= 1.0


def test_зону_можно_задать_переменной_окружения(monkeypatch):
    monkeypatch.setenv("SANDBOX_CAMERA_ZONE", "0.3, 0.2, 0.4, 0.5")
    assert camera_zone() == (0.3, 0.2, 0.4, 0.5)


def test_мусор_в_переменной_не_ломает_зону(monkeypatch):
    monkeypatch.setenv("SANDBOX_CAMERA_ZONE", "совсем не числа")
    assert camera_zone() == normalize_zone(DEFAULT_CAMERA_ZONE)


def test_зона_не_вылезает_за_кадр_и_не_вырождается():
    x, y, w, h = normalize_zone((0.9, 0.9, 0.8, 0.8))
    assert x + w <= 1.0 and y + h <= 1.0        # съехавшую зону вернули в кадр
    x, y, w, h = normalize_zone((-1, -1, 0.001, 5))
    assert (x, y) == (0.0, 0.0)
    assert w == 0.05                            # меньше не бывает: кадр не должен схлопнуться
    assert h == MAX_BOX_ZONE                    # больше не бывает: это уже «полностью»


def test_зона_не_из_четырёх_чисел_это_ошибка():
    with pytest.raises(ValueError):
        normalize_zone((0.1, 0.2))


# ------------------------------------------------------------------ обрезка

def test_обрезка_оставляет_только_рабочую_зону():
    cropped = crop_zone(frame_with_face())
    x, y, w, h = camera_zone()
    assert cropped.shape[1] == pytest.approx(w * WIDTH, abs=2)
    assert cropped.shape[0] == pytest.approx(h * HEIGHT, abs=2)


def test_после_обрезки_лица_в_кадре_нет():
    face = frame_with_face()
    assert (face[FACE] == 255).all()            # лицо в исходном кадре есть
    cropped = crop_zone(face)
    assert not (cropped == 255).any()           # а в записи его уже нет


def test_пустой_кадр_обрезать_нельзя():
    with pytest.raises(ValueError):
        crop_zone(np.zeros((0, 0, 3), np.uint8))


# ------------------------------------------------------------------ кадр в файл

def test_выключено_не_отдаёт_ни_одного_байта():
    assert camera_jpeg(frame_with_face(), CAMERA_OFF) is None
    assert camera_jpeg(frame_with_face(), "непонятно что") is None
    assert camera_jpeg(None, "box") is None


def test_только_ящик_легче_целого_кадра():
    box = camera_jpeg(frame_with_face(), "box")
    full = camera_jpeg(frame_with_face(), CAMERA_FULL)
    assert box.startswith(b"\xff\xd8") and full.startswith(b"\xff\xd8")
    assert len(box) < len(full)


def test_записанный_кадр_разбирается_обратно_и_лица_в_нём_нет():
    from PIL import Image

    img = Image.open(io.BytesIO(camera_jpeg(frame_with_face(), "box")))
    rgb = np.asarray(img)
    x, y, w, h = camera_zone()
    assert rgb.shape[1] == pytest.approx(w * WIDTH, abs=2)
    assert rgb.shape[0] == pytest.approx(h * HEIGHT, abs=2)
    assert rgb.min() > 30                       # белого пятна лица в кадре нет
    assert rgb[:, :, 0].mean() > rgb[:, :, 2].mean()   # песок остался песочным


def test_цвета_не_перепутаны_местами():
    """BGR у датчика, RGB у картинки: красное должно остаться красным."""
    from PIL import Image

    red_bgr = np.zeros((20, 20, 3), np.uint8)
    red_bgr[:, :, 2] = 230                      # третий байт BGR — красный
    rgb = np.asarray(Image.open(io.BytesIO(encode_jpeg(red_bgr))))
    assert rgb[:, :, 0].mean() > 200 and rgb[:, :, 1].mean() < 60


# ------------------------------------------------------------------ рекордер

@pytest.fixture
def no_ffmpeg(monkeypatch):
    monkeypatch.setenv("SANDBOX_FFMPEG", "off")     # пишем кадрами, ffmpeg не нужен


def frames_of(recorder: CameraRecorder) -> list:
    folder = recorder.out_dir / f"{recorder.prefix}.frames"
    return sorted(folder.glob("k-*.jpg")) if folder.exists() else []


def test_выключенная_камера_не_запускается(tmp_path, no_ffmpeg):
    recorder = CameraRecorder(FakeCamera(), tmp_path, mode=CAMERA_OFF)
    with pytest.raises(RuntimeError):
        recorder.start()
    assert not any(tmp_path.iterdir())


def test_только_ящик_пишет_обрезанные_кадры(tmp_path, no_ffmpeg):
    camera = FakeCamera()
    recorder = CameraRecorder(camera, tmp_path, prefix="camera-101010", fps=20, mode="box")
    recorder.start()
    time.sleep(0.5)
    result = recorder.stop()

    assert result["camera_mode"] == "box"
    assert result["frames"] > 0
    written = frames_of(recorder)
    assert written, "кадры не записались"

    from PIL import Image
    rgb = np.asarray(Image.open(written[0]))
    assert rgb.shape[0] < HEIGHT and rgb.shape[1] < WIDTH     # кадр обрезан
    assert rgb.max() < 250                                    # лица в нём нет


def test_камеры_нет_запись_не_падает(tmp_path, no_ffmpeg):
    recorder = CameraRecorder(FakeCamera(frame=None), tmp_path, fps=20, mode="box")   # кадров нет
    recorder.start()
    time.sleep(0.3)
    result = recorder.stop()
    assert result["frames"] == 0
    assert recorder.skipped > 0


def test_один_и_тот_же_кадр_дважды_не_пишется(tmp_path, no_ffmpeg):
    recorder = CameraRecorder(FakeCamera(still=True), tmp_path, fps=30, mode="box")
    recorder.start()
    time.sleep(0.4)
    result = recorder.stop()
    assert result["frames"] == 1                # время кадра не менялось — записали один раз


def test_сбойная_камера_не_роняет_запись(tmp_path, no_ffmpeg):
    class Broken:
        def color_frame(self):
            raise RuntimeError("камера отвалилась")

    recorder = CameraRecorder(Broken(), tmp_path, fps=20, mode="box")
    recorder.start()
    time.sleep(0.3)
    result = recorder.stop()
    assert result["frames"] == 0
    assert any("камера не отдала кадр" in note for note in result["notes"])


# ------------------------------------------------------------------ выбор клиники

@pytest.fixture
def library(tmp_path, monkeypatch):
    monkeypatch.setenv("SANDBOX_FFMPEG", "off")
    lib = MediaLibrary(data_dir=tmp_path)
    lib.configure(camera=FakeCamera(), session_id=lambda: "S-20260924-101010")
    return lib


def test_по_умолчанию_камера_выключена(library):
    state = library.camera_state()
    assert state["mode"] == CAMERA_OFF
    assert state["mode_title"] == "Выключено"
    assert state["warning"] is None
    assert [m["id"] for m in state["modes"]] == list(CAMERA_MODES)


def test_рядом_с_полностью_стоит_предупреждение(library):
    full = [m for m in library.camera_state()["modes"] if m["id"] == "full"][0]
    assert full["warning"] == CAMERA_WARNING
    assert "согласия родителей" in full["warning"]
    state = library.set_camera("full")
    assert state["warning"] == CAMERA_WARNING


def test_выбор_переживает_перезапуск(library, tmp_path):
    library.set_camera("box", zone=[0.25, 0.2, 0.5, 0.6])
    saved = json.loads((tmp_path / "camera-record.json").read_text(encoding="utf-8"))
    assert saved["mode"] == "box" and saved["zone"] == [0.25, 0.2, 0.5, 0.6]

    again = MediaLibrary(data_dir=tmp_path)
    assert again.camera_settings()["mode"] == "box"


def test_испорченный_файл_настройки_это_выключено(library, tmp_path):
    library.set_camera("full")
    (tmp_path / "camera-record.json").write_text("{не json", encoding="utf-8")
    again = MediaLibrary(data_dir=tmp_path)
    assert again.camera_settings()["mode"] == CAMERA_OFF


def test_выдуманный_режим_не_принимается(library):
    with pytest.raises(HTTPException) as e:
        library.set_camera("пиши всё")
    assert e.value.status_code == 422
    assert library.camera_settings()["mode"] == CAMERA_OFF


def test_кривая_зона_не_принимается(library):
    with pytest.raises(HTTPException) as e:
        library.set_camera("box", zone=[0.1, 0.2])
    assert e.value.status_code == 422


# ------------------------------------------------------------------ запись занятия

def test_при_выключенной_камере_запись_идёт_без_неё(library):
    class Frames:
        def latest(self, kind="full"):
            return b"\xff\xd8\xff\xd9", 1

    library.configure(frames=Frames())
    state = library.start_record(session_id="S-1")
    assert "camera" not in state
    result = library.stop_record()
    assert "camera" not in result
    assert not list(library.root.rglob(f"{CAMERA_PREFIX}*"))


def test_включённая_камера_пишется_отдельной_записью(library):
    class Frames:
        def latest(self, kind="full"):
            return b"\xff\xd8\xff\xd9", 1

    library.configure(frames=Frames())
    library.set_camera("box")
    state = library.start_record(session_id="S-1")
    assert state["camera"]["mode"] == "box"
    time.sleep(0.4)
    result = library.stop_record()
    assert result["camera"]["camera_mode"] == "box"
    assert result["camera"]["frames"] > 0
    assert list(library.root.rglob(f"{CAMERA_PREFIX}*"))


def test_выключить_камеру_можно_прямо_во_время_записи(library):
    class Frames:
        def latest(self, kind="full"):
            return b"\xff\xd8\xff\xd9", 1

    library.configure(frames=Frames())
    library.set_camera("box")
    library.start_record(session_id="S-1")
    time.sleep(0.2)
    state = library.set_camera("off")
    assert state["on"] is False
    result = library.stop_record()
    assert "camera" not in result               # камеру уже остановили
    library.forget()


def test_запись_с_камеры_видно_в_списке_отдельно(library):
    class Frames:
        def latest(self, kind="full"):
            return b"\xff\xd8\xff\xd9", 1

    library.configure(frames=Frames())
    library.set_camera("box")
    library.start_record(session_id="S-1")
    time.sleep(0.4)
    library.stop_record()
    camera_entries = [e for e in library.list() if e.get("camera")]
    assert camera_entries, "запись с камеры не попала в библиотеку"
    assert camera_entries[0]["title"].startswith("Камера")


# ------------------------------------------------------------------ поток датчика

def color_bytes(num: int, w: int, h: int, fill: int = 7) -> bytes:
    payload = bytes([fill]) * (w * h * 3)
    return b"KINC" + COLOR_TAIL.pack(num, w, h, 1, len(payload)) + payload


def depth_bytes(num: int, w: int, h: int) -> bytes:
    payload = np.full((h, w), 1250.0, dtype="<f4").tobytes()
    return b"KIN2" + DEPTH_TAIL.pack(num, w, h) + payload


def sensor() -> Kinect2Sensor:
    return Kinect2Sensor(grabber="/нет/такого", color=True)


def test_ключ_color_попадает_в_команду_захвата():
    assert "--color" not in Kinect2Sensor(grabber="x")._command()
    cmd = Kinect2Sensor(grabber="x", color=True, color_fps=12, color_scale=4)._command()
    assert "--color" in cmd
    assert cmd[cmd.index("--color-fps") + 1] == "12"
    assert cmd[cmd.index("--color-scale") + 1] == "4"


def test_камеру_можно_включить_переменной_окружения(monkeypatch):
    monkeypatch.setenv("SANDBOX_KINECT_COLOR", "1")
    assert Kinect2Sensor(grabber="x").color is True


def test_без_ключа_цветного_кадра_нет():
    assert Kinect2Sensor(grabber="x").color_frame() is None


def test_цветной_кадр_читается_из_потока():
    s = sensor()
    stream = io.BytesIO(color_bytes(0, 8, 4)[4:])       # магическое слово уже снято
    assert s._read_color(stream) is True
    frame = s.color_frame()
    assert frame is not None
    assert frame.bgr.shape == (4, 8, 3)
    assert (frame.bgr == 7).all()
    assert s.color_stats()["frames"] == 1
    assert s.color_stats()["size"] == (8, 4)


def test_глубина_и_цвет_разбираются_в_одном_потоке():
    s = sensor()
    stream = io.BytesIO(depth_bytes(0, 4, 2) + color_bytes(0, 8, 4) + depth_bytes(1, 4, 2))
    s._running = True
    s._pump(type("P", (), {"stdout": stream})())
    assert s._seq == 2                                  # оба кадра глубины на месте
    assert s.color_frame().bgr.shape == (4, 8, 3)


def test_в_памяти_живёт_только_последний_цветной_кадр():
    s = sensor()
    for i in range(5):
        s._read_color(io.BytesIO(color_bytes(i, 8, 4, fill=i)[4:]))
    assert (s.color_frame().bgr == 4).all()
    assert s.color_stats()["frames"] == 5


def test_сбившийся_поток_виден_сразу():
    s = sensor()
    s._running = True
    stream = io.BytesIO(b"XXXX" + b"\x00" * 100)   # не "KIN2" и не "KINC"
    with pytest.raises(Kinect2Error):
        s._pump(type("P", (), {"stdout": stream})())


def test_слишком_большой_цветной_кадр_отвергается():
    s = sensor()
    head = COLOR_TAIL.pack(0, 4000, 4000, 1, 4000 * 4000 * 3)
    with pytest.raises(Kinect2Error):
        s._read_color(io.BytesIO(head))


def test_непонятный_формат_цветного_кадра_просто_пропускается():
    s = sensor()
    payload = b"\x00" * 24
    head = COLOR_TAIL.pack(0, 8, 4, 99, len(payload))   # формат 99 нам неизвестен
    assert s._read_color(io.BytesIO(head + payload)) is True
    assert s.color_frame() is None


def test_обрыв_потока_не_считается_кадром():
    s = sensor()
    assert s._read_color(io.BytesIO(b"")) is False
    assert s.color_frame() is None


# ------------------------------------------------------------------ ручки пульта

def test_пульт_показывает_выключенную_камеру(client):
    camera = client.get("/api/record/camera")["camera"]
    assert camera["mode"] == CAMERA_OFF
    assert [m["id"] for m in camera["modes"]] == list(CAMERA_MODES)


def test_состояние_пульта_знает_про_камеру(client):
    state = client.get("/api/state")
    assert state["media"]["camera"]["mode"] in CAMERA_MODES


def test_режим_переключается_через_пульт(client):
    try:
        answer = client.post("/api/record/camera", {"mode": "box"})
        assert answer["camera"]["mode"] == "box"
        assert "лицо в кадр не попадает" in answer["message"]
        assert answer["warning"] is None

        answer = client.post("/api/record/camera", {"mode": "full"})
        assert answer["camera"]["mode"] == "full"
        assert answer["warning"] == CAMERA_WARNING
        assert client.get("/api/record/camera")["camera"]["mode"] == "full"
    finally:
        client.post("/api/record/camera", {"mode": "off"})   # вернуть как было


def test_выдуманный_режим_пульт_не_принимает(client):
    assert client.code("POST", "/api/record/camera", {"mode": "пиши всё"}) == 422
    assert client.get("/api/record/camera")["camera"]["mode"] == CAMERA_OFF


def test_зону_можно_поправить_из_пульта(client):
    try:
        answer = client.post("/api/record/camera", {"mode": "box", "zone": [0.2, 0.2, 0.5, 0.5]})
        assert answer["camera"]["zone"] == [0.2, 0.2, 0.5, 0.5]
    finally:
        client.post("/api/record/camera", {"mode": "off"})


# ------------------------------------------------------------------ предпросмотр зоны

def test_предпросмотр_показывает_обрезанный_кадр(library):
    from PIL import Image

    jpeg = library.camera_preview()
    rgb = np.asarray(Image.open(io.BytesIO(jpeg)))
    assert rgb.shape[0] < HEIGHT and rgb.shape[1] < WIDTH
    assert rgb.max() < 250                       # лица в предпросмотре нет


def test_предпросмотр_при_выключенной_камере_показывает_только_ящик(library):
    from PIL import Image

    assert library.camera_settings()["mode"] == CAMERA_OFF
    rgb = np.asarray(Image.open(io.BytesIO(library.camera_preview())))
    assert rgb.shape[:2] != (HEIGHT, WIDTH)      # не целый кадр


def test_предпросмотр_целого_кадра_только_по_явной_просьбе(library):
    from PIL import Image

    rgb = np.asarray(Image.open(io.BytesIO(library.camera_preview("full"))))
    assert rgb.shape[:2] == (HEIGHT, WIDTH)


def test_нет_камеры_нет_предпросмотра(library):
    library._camera = FakeCamera(frame=None)
    with pytest.raises(HTTPException) as e:
        library.camera_preview()
    assert e.value.status_code == 503


def test_предпросмотр_ничего_не_сохраняет(library, tmp_path):
    library.camera_preview()
    assert not list(tmp_path.rglob("*.jpg"))
