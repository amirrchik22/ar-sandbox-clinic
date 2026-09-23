"""Режимы занятия, пауза, заморозка, яркость — на живом конвейере кадров.

Датчик синтетический (fake), поэтому тесты идут на любой машине без железа.
"""
from __future__ import annotations

import io
import time

import numpy as np
import pytest
from PIL import Image

from sandbox.console.pipeline import (BRIGHTNESS_RANGE, EMPTY_RGB, MODE_IDS, MODE_PROFILES,
                                      TWO_HIGH, TWO_LOW, FrameServer, colorize_two,
                                      colorize_water)
from sandbox.render.heightmap import center_roi


# ------------------------------------------------------------------ помощники

def mean_of(jpeg: bytes) -> float:
    """Средняя яркость кадра — по ней видно и чёрный экран, и приглушение."""
    img = Image.open(io.BytesIO(jpeg)).convert("RGB")
    return float(np.asarray(img, dtype=np.float32).mean())


def wait_frames(server: FrameServer, count: int = 1, timeout: float = 5.0) -> tuple[bytes, int]:
    """Дождаться, пока конвейер выдаст ещё count кадров."""
    start = server.latest("full")[1]
    end = time.time() + timeout
    while time.time() < end:
        data, seq = server.latest("full")
        if data is not None and seq >= start + count:
            return data, seq
        time.sleep(0.03)
    raise AssertionError("конвейер не выдал новый кадр")


@pytest.fixture(scope="module")
def frames(tmp_path_factory):
    server = FrameServer(width=320, height=240, fps=15, preview_width=160,
                         sensor_name="fake",
                         profile_path=tmp_path_factory.mktemp("modes") / "console.json")
    server.start()
    for _ in range(100):
        if server.latest("full")[0] is not None:
            break
        time.sleep(0.1)
    yield server
    server.stop()


# ------------------------------------------------------------------ раскраски

def ramp(height: int = 40, width: int = 30) -> np.ndarray:
    """Наклонный «рельеф» от -50 до +50 мм: внизу кадра низко, вверху высоко."""
    column = np.linspace(-50.0, 50.0, height, dtype=np.float32)
    return np.repeat(column[::-1, None], width, axis=1)


def test_два_цвета_это_ровно_два_цвета_и_граница():
    h = ramp()
    ok = np.ones(h.shape, dtype=bool)
    rgb = colorize_two(h, ok, -50.0, 50.0)
    colors = {tuple(c) for c in rgb.reshape(-1, 3)}
    assert tuple(rgb[0, 0]) == TWO_HIGH          # верх кадра — «высоко»
    assert tuple(rgb[-1, 0]) == TWO_LOW          # низ кадра — «низко»
    assert len(colors) == 3                      # низ, верх и белая граница между ними
    assert (255, 255, 255) in colors


def test_только_вода_заливает_низины_и_оставляет_песок():
    h = ramp()
    ok = np.ones(h.shape, dtype=bool)
    rgb = colorize_water(h, ok, -50.0, 50.0)
    low, high = rgb[-1, 0].astype(int), rgb[0, 0].astype(int)
    assert low[2] > low[0] + 40                  # в низине синего заметно больше красного
    assert high[0] > high[2] + 30                # наверху песочный: красного больше синего
    assert 120 < high.mean() < 245               # песок светлый, но не белый


def test_где_датчик_не_видит_там_ровный_фон():
    h = ramp()
    ok = np.ones(h.shape, dtype=bool)
    ok[:, :5] = False
    for paint in (colorize_two, colorize_water):
        rgb = paint(h, ok, -50.0, 50.0)
        assert {tuple(c) for c in rgb[:, :5].reshape(-1, 3)} == {EMPTY_RGB}


# ------------------------------------------------------------------ плавность

def test_спокойный_режим_меняет_картинку_постепенно(tmp_path):
    server = FrameServer(width=64, height=48, preview_width=32, sensor_name="fake",
                         profile_path=tmp_path / "console.json")
    white = np.full((8, 8, 3), 255, dtype=np.uint8)
    black = np.zeros((8, 8, 3), dtype=np.uint8)
    calm = MODE_PROFILES["calm"].color_ema

    server._fade(black, calm)
    step = server._fade(white, calm)
    assert step.mean() < 80                      # за один кадр вспышки не случилось
    for _ in range(40):
        step = server._fade(white, calm)
    assert step.mean() > 200                     # но за пару секунд картинка дошла

    assert server._fade(white, 1.0).mean() == 255.0   # «Карта» меняется мгновенно


def test_спокойный_режим_сильнее_сглаживает_рельеф(frames):
    assert MODE_PROFILES["calm"].smoothing_alpha < MODE_PROFILES["map"].smoothing_alpha
    frames.set_mode("calm")
    wait_frames(frames, 2)
    assert frames._settings.smoothing_alpha == MODE_PROFILES["calm"].smoothing_alpha
    assert frames._proc._smooth.alpha == MODE_PROFILES["calm"].smoothing_alpha
    frames.set_mode("map")
    wait_frames(frames, 2)
    assert frames._proc._smooth.alpha == MODE_PROFILES["map"].smoothing_alpha


# ------------------------------------------------------------------ режимы

def test_режимы_переключаются_и_рисуют(frames):
    for mode in MODE_IDS:
        frames.set_mode(mode)
        data, _ = wait_frames(frames, 2)
        state = frames.state()
        assert state["mode"] == mode
        assert state["palette"] == MODE_PROFILES[mode].palette
        assert data[:2] == b"\xff\xd8"           # кадр остался настоящим JPEG
        assert mean_of(data) > 5                 # и не чёрным
    frames.set_mode("map")


def test_неизвестный_режим_не_принимается(frames):
    with pytest.raises(ValueError):
        frames.set_mode("радуга")


def test_смена_режима_снимает_ручную_палитру(frames):
    frames.set_palette("mono")
    assert frames.state()["palette"] == "mono"
    frames.set_mode("calm")
    wait_frames(frames, 2)
    assert frames.state()["palette"] == MODE_PROFILES["calm"].palette
    frames.set_mode("map")


# ------------------------------------------------------------------ пауза

def test_пауза_гасит_проекцию_а_предпросмотр_живёт(frames):
    frames.set_mode("map")
    wait_frames(frames, 2)
    assert mean_of(frames.latest("full")[0]) > 5

    frames.set_pause(True)
    wait_frames(frames, 3)
    assert frames.state()["paused"] is True
    assert mean_of(frames.latest("full")[0]) < 1.0        # на проектор идёт чёрное
    assert mean_of(frames.latest("preview")[0]) > 3       # телефон продолжает показывать песок

    frames.set_pause(False)
    wait_frames(frames, 3)
    assert mean_of(frames.latest("full")[0]) > 5          # проекция вернулась сама


def test_на_паузе_кадры_продолжают_считаться(frames):
    frames.set_pause(True)
    before = frames.state()["frames"]
    wait_frames(frames, 3)
    assert frames.state()["frames"] > before
    frames.set_pause(False)
    wait_frames(frames, 2)


# ------------------------------------------------------------------ заморозка

def test_заморозка_держит_кадр(frames):
    frames.set_freeze(True)
    assert frames.state()["frozen"] is True
    time.sleep(0.4)                              # кадр, начатый до заморозки, успевает выйти
    data, seq = frames.latest("full")
    time.sleep(0.6)
    assert frames.latest("full")[1] == seq       # картинка не менялась
    assert frames.latest("full")[0] is data

    frames.set_freeze(False)
    wait_frames(frames, 2)                       # после снятия — снова живая


def test_пауза_гасит_даже_замороженный_кадр(frames):
    frames.set_freeze(True)
    time.sleep(0.4)
    frames.set_pause(True)
    for _ in range(100):
        if mean_of(frames.latest("full")[0]) < 1.0:
            break
        time.sleep(0.05)
    assert mean_of(frames.latest("full")[0]) < 1.0
    frames.set_pause(False)
    for _ in range(100):                         # замороженный кадр вернулся, а не живой
        if mean_of(frames.latest("full")[0]) > 5:
            break
        time.sleep(0.05)
    assert mean_of(frames.latest("full")[0]) > 5
    frames.set_freeze(False)
    wait_frames(frames, 2)


# ------------------------------------------------------------------ яркость

def test_яркость_приглушает_картинку(frames):
    frames.set_mode("map")
    frames.set_brightness(100)
    bright = mean_of(wait_frames(frames, 3)[0])
    frames.set_brightness(25)
    dim = mean_of(wait_frames(frames, 3)[0])
    assert dim < bright * 0.6
    frames.set_brightness(100)
    wait_frames(frames, 3)


def test_яркость_проверяет_границы(frames):
    low, high = BRIGHTNESS_RANGE
    assert frames.set_brightness(high) == high
    with pytest.raises(ValueError):
        frames.set_brightness(low - 1)
    with pytest.raises(ValueError):
        frames.set_brightness(500)
    with pytest.raises(ValueError):
        frames.set_brightness("ярко")


# ------------------------------------------------------------------ рамка рабочей зоны

def test_свои_раскраски_обрезаются_по_ящику_как_палитры(tmp_path):
    """На проекции не должно быть тёмной рамки ни в одном режиме.

    Обработчик кадра режет свою картинку по рабочей зоне (пол, стена и борта
    ящика на проектор не идут). «Два цвета» и «Только вода» красят кадр сами,
    и раньше отдавали его целиком — вокруг проекции оставалось тёмное поле.
    Проверяем, что теперь все четыре режима отдают одну и ту же рамку.
    """
    server = FrameServer(width=320, height=240, fps=15, preview_width=160,
                         sensor_name="fake", profile_path=tmp_path / "console.json")
    shape = (48, 64)
    server._proc.roi = center_roi(shape)               # как у настоящего датчика

    depth = np.full(shape, 1000.0, dtype=np.float32)
    depth[14:34, 18:46] -= 40.0                        # горка посреди ящика

    result = server._proc.process(depth, 1 / 15)
    assert result.roi_box is not None                  # рамка рабочей зоны посчитана
    полный = server._paint(result, MODE_PROFILES["map"]).shape
    assert полный[:2] < shape                          # кадр обрезан, а не отдан целиком

    for mode in ("two", "water"):
        server.set_mode(mode)
        свой = server._paint(server._proc.process(depth, 1 / 15),
                             MODE_PROFILES[mode]).shape
        assert свой == полный, f"режим «{mode}» отдаёт кадр с рамкой: {свой} вместо {полный}"
