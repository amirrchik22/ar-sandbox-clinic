"""Конвейер картинки: датчик → карта высот → цветная карта → JPEG для браузера.

Один фоновый поток непрерывно берёт кадры у датчика, отдаёт их обработке
sandbox/render/heightmap.py и держит наготове два готовых JPEG: большой — для
проектора, маленький — для предпросмотра на телефоне. Веб-сервер только отдаёт
эти байты, сам ничего не считает, поэтому пульт остаётся отзывчивым.

Если датчик не найден (не подключён, не собрана программа захвата), конвейер
переключается на синтетический рельеф sandbox/sensors/fake.py: интерфейс,
проекцию и показ заказчику можно смотреть без железа. Это же нужно для отладки.
"""
from __future__ import annotations

import io
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from sandbox.depth_service.calibration import CalibrationProfile
from sandbox.render.heightmap import (HeightmapProcessor, HeightmapSettings, ReferencePlane,
                                      center_roi, crop_to_box)
from sandbox.render.palettes import PALETTE_ORDER, PALETTE_TITLES
from sandbox.sensors import make_sensor
from sandbox.sensors.fake import FakeSensor

ROOT = Path(__file__).resolve().parents[2]

# Палитры для пульта: ключ, подпись, короткая подсказка.
PALETTE_HINTS = {
    "calm": "приглушённые цвета, без красного",
    "classic": "как на карте: вода, трава, горы",
    "contrast": "три яркие зоны, для слабого зрения",
    "topo": "вода, берег, луг, камень, снег",
    "mono": "один цвет, высота — яркость",
}
PALETTES = [{"id": p, "title": PALETTE_TITLES.get(p, p), "hint": PALETTE_HINTS.get(p, "")}
            for p in PALETTE_ORDER]
PALETTE_IDS = [p["id"] for p in PALETTES]

# Шаг горизонталей, мм. 0 = подбирается сам под текущий рельеф.
CONTOUR_STEPS = [
    {"value": 0, "title": "Авто"},
    {"value": 5, "title": "5 мм"},
    {"value": 10, "title": "10 мм"},
    {"value": 20, "title": "20 мм"},
    {"value": 50, "title": "50 мм"},
]
CONTOUR_VALUES = [c["value"] for c in CONTOUR_STEPS]


# ------------------------------------------------------------------- режимы
#
# Специалисту в живом занятии некогда крутить ползунки: нужно одно нажатие.
# Поэтому на пульте не настройки, а четыре режима. Каждый режим — это сразу и
# палитра, и скорость картинки, и плотность линий.

@dataclass(frozen=True)
class ModeProfile:
    """Что именно меняется, когда специалист нажимает режим."""
    palette: str             # палитра раскраски
    smoothing_alpha: float   # вес нового кадра: меньше — картинка спокойнее
    contour_lines: int       # к скольким горизонталям стремиться на весь рельеф
    color_ema: float         # плавность смены самой картинки, 1.0 = мгновенно
    range_ema: float         # плавность границ шкалы высот, 1.0 = мгновенно
    paint: str = ""          # своя раскраска вместо палитры: two | water


MODE_PROFILES: dict[str, ModeProfile] = {
    # Классическая топография: вода — берег — луг — холмы — снег, линии высоты.
    "map": ModeProfile(palette="topo", smoothing_alpha=0.45, contour_lines=12,
                       color_ema=1.0, range_ema=1.0),
    # Для детей с сенсорной чувствительностью: приглушённые цвета, мало линий и
    # главное — медленная картинка. Сглаживание высоты втрое сильнее обычного,
    # плюс сама картинка переливается в новую за полсекунды, а не рывком. Резких
    # вспышек и мельтешения при движении рук над песком не остаётся.
    "calm": ModeProfile(palette="calm", smoothing_alpha=0.12, contour_lines=5,
                        color_ema=0.14, range_ema=0.06),
    # Для слабовидящих и малышей: только «высоко» и «низко», белая граница между
    # ними. Самый сильный перепад яркости, какой может дать проектор.
    "two": ModeProfile(palette="mono", smoothing_alpha=0.30, contour_lines=0,
                       color_ema=0.45, range_ema=0.10, paint="two"),
    # Песок остаётся песочным, светится только вода в низинах: спокойный фон и
    # одна понятная игра — «копаем реку».
    "water": ModeProfile(palette="topo", smoothing_alpha=0.35, contour_lines=0,
                         color_ema=0.50, range_ema=0.10, paint="water"),
}

MODE_TITLES = {"map": "Карта", "calm": "Спокойный", "two": "Два цвета", "water": "Только вода"}
MODE_HINTS = {
    "map": "классическая топография: вода, берег, холмы",
    "calm": "приглушённые цвета и медленные переходы",
    "two": "только высоко и низко, максимальный контраст",
    "water": "песок песочный, подсвечена вода в низинах",
}
MODE_ORDER = ["map", "calm", "two", "water"]
MODES = [{"id": m, "title": MODE_TITLES[m], "hint": MODE_HINTS[m]} for m in MODE_ORDER]
MODE_IDS = list(MODE_ORDER)
DEFAULT_MODE = "map"

# Яркость проекции, проценты. Ниже 10 % на песке уже ничего не видно.
BRIGHTNESS_RANGE = (10, 100)

# На паузе проектор гасится в чёрное, а предпросмотр на телефоне остаётся живым,
# но притушенным: специалист должен видеть песок и понимать, что проекция снята.
PAUSE_PREVIEW_DIM = 0.45

# Цвета своих раскрасок.
EMPTY_RGB = (18, 18, 22)          # куда датчик не видит
DIM_OUTSIDE = 0.35                # вне рабочей зоны
TWO_LOW = (8, 16, 64)             # «низко» — почти чёрная синева
TWO_HIGH = (250, 228, 60)         # «высоко» — жёлтый
TWO_EDGE = (255, 255, 255)        # граница между ними
SAND_LOW = (176, 148, 108)        # песок в низинах
SAND_HIGH = (226, 205, 168)       # песок на вершинах
WATER_SHALLOW = (86, 176, 206)    # отмель
WATER_DEEP = (14, 48, 104)        # глубина
SHORE = (208, 240, 248)           # кромка воды


def _finish(rgb: np.ndarray, ok: np.ndarray, roi: np.ndarray | None) -> np.ndarray:
    """Общий хвост раскраски: вне рабочей зоны приглушить, где нет данных — фон."""
    empty = np.array(EMPTY_RGB, dtype=np.float32)
    if roi is not None:
        k = np.where(roi, np.float32(1.0), np.float32(DIM_OUTSIDE))[:, :, None]
        rgb = rgb * k + empty * (1.0 - k)
    rgb = np.where(ok[:, :, None], rgb, empty)
    return np.clip(rgb, 0, 255).astype(np.uint8)


def colorize_two(height_mm: np.ndarray, ok: np.ndarray, lo: float, hi: float,
                 roi: np.ndarray | None = None) -> np.ndarray:
    """Режим «Два цвета»: ниже середины рельефа — тёмное, выше — жёлтое."""
    mid = (lo + hi) / 2.0
    high = height_mm >= mid
    rgb = np.where(high[:, :, None],
                   np.array(TWO_HIGH, dtype=np.float32),
                   np.array(TWO_LOW, dtype=np.float32))
    edge = np.zeros(high.shape, dtype=bool)          # граница: сосед уже в другой зоне
    edge[:, 1:] |= high[:, 1:] != high[:, :-1]
    edge[1:, :] |= high[1:, :] != high[:-1, :]
    rgb = np.where(edge[:, :, None], np.array(TWO_EDGE, dtype=np.float32), rgb)
    return _finish(rgb, ok, roi)


def colorize_water(height_mm: np.ndarray, ok: np.ndarray, lo: float, hi: float,
                   roi: np.ndarray | None = None, level_frac: float = 0.32) -> np.ndarray:
    """Режим «Только вода»: песок песочный, низины залиты водой, берег светлый."""
    span = max(hi - lo, 1e-3)
    level = lo + level_frac * span                   # уровень воды по текущему рельефу
    above = np.clip((height_mm - level) / max(hi - level, 1e-3), 0.0, 1.0)[:, :, None]
    sand_lo = np.array(SAND_LOW, dtype=np.float32)
    sand = sand_lo + (np.array(SAND_HIGH, dtype=np.float32) - sand_lo) * above
    deep = np.clip((level - height_mm) / max(level - lo, 1e-3), 0.0, 1.0)[:, :, None]
    shallow = np.array(WATER_SHALLOW, dtype=np.float32)
    water = shallow + (np.array(WATER_DEEP, dtype=np.float32) - shallow) * deep
    rgb = np.where((height_mm < level)[:, :, None], water, sand)
    shore = np.abs(height_mm - level) < 0.02 * span
    rgb = np.where(shore[:, :, None], np.array(SHORE, dtype=np.float32), rgb)
    return _finish(rgb, ok, roi)


# Порядок поиска датчика: сначала настоящий, потом синтетический.
SENSOR_ORDER = ("kinect2", "kinect1", "fake")
SENSOR_TITLES = {"kinect2": "Kinect v2", "kinect1": "Kinect v1",
                 "fake": "демо-рельеф (датчик не подключён)"}


def open_sensor(preferred: str | None = None) -> tuple[object, str, bool, str]:
    """Открыть датчик. Вернёт (датчик, имя, демо-режим ли это, что пошло не так)."""
    names = [preferred] if preferred else list(SENSOR_ORDER)
    errors = []
    for name in names:
        try:
            sensor = make_sensor(name)
            sensor.start()
            sensor.depth_frame()                      # первый кадр = проверка, что поток пошёл
            return sensor, name, name == "fake", ""
        except Exception as e:                        # нет драйвера, нет устройства, занят порт
            errors.append(f"{name}: {e}")
            print(f"датчик {name} не открылся: {e}")
    sensor = FakeSensor()
    sensor.start()
    print("работаю на демо-рельефе — датчик не открылся")
    return sensor, "fake", True, "; ".join(errors)


class FrameServer:
    """Фоновый поток: кадр за кадром готовит картинку и держит её в памяти."""

    def __init__(self, width: int = 1024, height: int = 768, fps: int = 20,
                 preview_width: int = 512, sensor_name: str | None = None,
                 profile_path: Path | None = None):
        self.out_size = (width, height)
        self.preview_size = (preview_width, int(round(preview_width * height / width)))
        self.fps = fps
        self._sensor_name = sensor_name or os.environ.get("SANDBOX_SENSOR") or None
        self._profile_path = Path(profile_path or os.environ.get("SANDBOX_PROFILE")
                                  or (ROOT / "config" / "profiles" / "console.json"))

        self._settings = HeightmapSettings(palette=MODE_PROFILES[DEFAULT_MODE].palette,
                                           contour_step_mm=0.0)
        self._proc = HeightmapProcessor(self._settings)

        # Режим, пауза, заморозка, яркость — всё, чем специалист управляет одним
        # нажатием прямо во время занятия.
        self._mode = DEFAULT_MODE
        self._palette_override: str | None = None      # ручной выбор палитры поверх режима
        self._paused = False
        self._frozen = False
        self._brightness = 100
        self._color_state: np.ndarray | None = None    # картинка для плавных переходов
        self._range: tuple[float, float] | None = None  # сглаженные границы шкалы высот
        self._live_full: bytes | None = None           # последний живой кадр (не чёрный)
        self._live_preview: bytes | None = None
        self._black: bytes | None = None               # чёрный кадр паузы, считается один раз

        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._full: bytes | None = None
        self._preview: bytes | None = None
        self._rgb: np.ndarray | None = None
        self._seq = 0
        self._fps_measured = 0.0
        self._stats: dict = {}
        self._calib_left = 0                          # сколько кадров ещё набрать для калибровки
        self._calib_frames: list[np.ndarray] = []
        self._calibrated = False
        self._calibrated_at: float | None = None
        self._demo = True
        self._opening = True                          # пока ищем датчик, пульт так и пишет
        self._sensor_title = "ищу датчик…"
        self._sensor_error = ""
        self._error = ""

    # ---------- управление ----------

    def start(self) -> None:
        self._stop.clear()          # после остановки конвейер можно запустить заново
        self._thread = threading.Thread(target=self._loop, name="frames", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3.0)

    def set_palette(self, name: str) -> None:
        """Ручной выбор палитры поверх режима. Держится до смены режима."""
        if name not in PALETTE_IDS:
            raise ValueError("неизвестная палитра")
        with self._lock:
            self._palette_override = name
            self._settings.palette = name

    def set_contour_step(self, step_mm: int) -> None:
        if step_mm not in CONTOUR_VALUES:
            raise ValueError("неизвестный шаг горизонталей")
        with self._lock:
            self._settings.contour_step_mm = float(step_mm)

    def set_mode(self, name: str) -> str:
        """Режим занятия: map | calm | two | water. Одно нажатие на пульте."""
        if name not in MODE_IDS:
            raise ValueError("неизвестный режим: " + ", ".join(MODE_IDS))
        with self._lock:
            if self._mode != name:
                self._mode = name
                self._palette_override = None          # режим сам знает свою палитру
                self._settings.contour_step_mm = 0.0   # и свою плотность линий
                self._color_state = None               # новый режим показываем сразу
                self._range = None
            self._settings.palette = MODE_PROFILES[name].palette
        self._apply_smoothing(MODE_PROFILES[name].smoothing_alpha)
        return name

    def set_pause(self, on: bool) -> bool:
        """Пауза: проекция гаснет в чёрное, кадры продолжают считаться.

        Нужна, чтобы переключить внимание ребёнка на специалиста: картинка
        исчезает мгновенно, но занятие идёт и таймер не останавливается.
        """
        with self._lock:
            self._paused = bool(on)
            return self._paused

    def set_freeze(self, on: bool) -> bool:
        """Заморозка: на экране остаётся последний кадр, песок можно трогать."""
        with self._lock:
            self._frozen = bool(on)
            return self._frozen

    def set_brightness(self, percent: int) -> int:
        low, high = BRIGHTNESS_RANGE
        try:
            value = int(percent)
        except (TypeError, ValueError) as e:
            raise ValueError("яркость: нужно число") from e
        if not low <= value <= high:
            raise ValueError(f"яркость: от {low} до {high}")
        with self._lock:
            self._brightness = value
            return value

    def _apply_smoothing(self, alpha: float) -> None:
        """Сила сглаживания высоты по времени.

        Живёт внутри обработчика кадра (render/heightmap.py), который создаётся
        один раз; чтобы «Спокойный» режим включался сразу, а не после
        пересоздания обработчика, меняем сглаживание на ходу. Если обработчик
        когда-нибудь переделают, getattr просто ничего не найдёт и режим
        останется рабочим, только переключится не мгновенно.
        """
        self._settings.smoothing_alpha = alpha
        smoother = getattr(self._proc, "_smooth", None)
        if smoother is not None:
            smoother.alpha = alpha

    def calibrate(self, frames: int = 12) -> None:
        """«Запомнить ровный песок»: следующие N кадров уйдут в опорную плоскость."""
        with self._lock:
            self._calib_frames = []
            self._calib_left = frames
            self._color_state = None       # после калибровки шкала другая — показываем сразу
            self._range = None

    # ---------- выдача ----------

    def latest(self, kind: str = "full") -> tuple[bytes | None, int]:
        with self._lock:
            return (self._preview if kind == "preview" else self._full), self._seq

    def snapshot(self, path: Path) -> Path:
        """Сохранить текущий кадр в файл и рядом — маленькую копию для списка."""
        with self._lock:
            rgb = None if self._rgb is None else self._rgb.copy()
        if rgb is None:
            raise RuntimeError("кадра ещё нет")
        path.parent.mkdir(parents=True, exist_ok=True)
        img = Image.fromarray(rgb).resize(self.out_size, Image.BILINEAR)
        img.save(path, "JPEG", quality=88)
        small = (320, int(round(320 * self.out_size[1] / self.out_size[0])))
        img.resize(small, Image.BILINEAR).save(path.with_name(path.stem + "-small.jpg"),
                                               "JPEG", quality=70)
        return path

    def state(self) -> dict:
        with self._lock:
            return {
                "sensor": self._sensor_title,
                "demo": self._demo,
                "opening": self._opening,
                "sensor_error": self._sensor_error,
                "fps": round(self._fps_measured, 1),
                "calibrated": self._calibrated,
                "calibrating": self._calib_left > 0,
                "calibrated_at": self._calibrated_at,
                "mode": self._mode,
                "mode_title": MODE_TITLES.get(self._mode, self._mode),
                "paused": self._paused,
                "frozen": self._frozen,
                "brightness": self._brightness,
                "palette": self._settings.palette,
                "contour_step_mm": int(self._settings.contour_step_mm),
                "frames": self._seq,
                "stats": dict(self._stats),
                "error": self._error,
            }

    # ---------- калибровка на диске ----------

    def _load_profile(self) -> None:
        """Плоскость ровного песка с прошлого запуска — чтобы не калибровать заново."""
        if not self._profile_path.exists():
            return
        try:
            profile = CalibrationProfile.load(self._profile_path)
            a, b, c, d = profile.plane                 # запись a*x + b*y - z + d = 0
            self._proc.plane = ReferencePlane(a=float(a), b=float(b), c=float(d))
            self._proc.reset_state()
            with self._lock:
                self._calibrated = True
                self._calibrated_at = self._profile_path.stat().st_mtime
        except Exception as e:
            with self._lock:
                self._error = f"профиль калибровки не прочитался: {e}"

    def _save_profile(self, plane: ReferencePlane, shape: tuple[int, int]) -> None:
        h, w = shape
        profile = CalibrationProfile(
            name="console", plane=plane.to_profile_plane(),
            box_corners_px=[[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]],
            projector_homography=np.eye(3).tolist(),
            created=time.strftime("%Y-%m-%d %H:%M:%S"))
        try:
            self._profile_path.parent.mkdir(parents=True, exist_ok=True)
            profile.save(self._profile_path)
        except Exception as e:
            with self._lock:
                self._error = f"профиль не сохранился: {e}"

    # ---------- картинка режима ----------

    def _apply_mode(self) -> ModeProfile:
        """Перед каждым кадром привести настройки обработки к текущему режиму."""
        with self._lock:
            mode, override = self._mode, self._palette_override
            profile = MODE_PROFILES[mode]
            self._settings.palette = override or profile.palette
            self._settings.contour_lines = profile.contour_lines or 12
        if self._settings.smoothing_alpha != profile.smoothing_alpha:
            self._apply_smoothing(profile.smoothing_alpha)
        return profile

    def _smooth_range(self, lo: float, hi: float, ema: float) -> tuple[float, float]:
        """Границы шкалы высот тянутся за рельефом плавно: иначе при каждом
        движении руки вся картинка разом меняла бы цвет."""
        if self._range is None or ema >= 1.0:
            self._range = (lo, hi)
        else:
            plo, phi = self._range
            self._range = (plo + ema * (lo - plo), phi + ema * (hi - phi))
        return self._range

    def _fade(self, rgb: np.ndarray, ema: float) -> np.ndarray:
        """Плавная смена картинки. В «Спокойном» новый кадр проступает за
        полсекунды — резких вспышек нет совсем."""
        if ema >= 1.0:
            self._color_state = None
            return rgb
        target = rgb.astype(np.float32)
        if self._color_state is None or self._color_state.shape != target.shape:
            self._color_state = target
        else:
            self._color_state += ema * (target - self._color_state)
        return np.clip(self._color_state, 0, 255).astype(np.uint8)

    def _paint(self, result, profile: ModeProfile) -> np.ndarray:
        """Кадр обработки → картинка режима с нужной плавностью и яркостью."""
        with self._lock:
            brightness = self._brightness
        if profile.paint:
            lo, hi = self._smooth_range(result.lo_mm, result.hi_mm, profile.range_ema)
            paint = colorize_two if profile.paint == "two" else colorize_water
            rgb = paint(result.height_mm, result.ok, lo, hi, self._proc.roi)
            # Своя раскраска идёт от полного кадра датчика, а обработчик уже
            # обрезал свою картинку по рабочей зоне. Режем так же, иначе в
            # режимах «Два цвета» и «Только вода» вокруг проекции оставалась
            # тёмная рамка из пола и бортов ящика. Проверено живьём 23.09.
            rgb = crop_to_box(rgb, result.roi_box)
        else:
            rgb = result.rgb                       # раскрасил уже обработчик кадра
        rgb = self._fade(rgb, profile.color_ema)
        if brightness < 100:
            rgb = (rgb.astype(np.float32) * (brightness / 100.0)).astype(np.uint8)
        return rgb

    @staticmethod
    def _jpeg(img: Image.Image, quality: int) -> bytes:
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=quality)
        return buf.getvalue()

    def _black_frame(self) -> bytes:
        """Чёрный кадр паузы. Считается один раз и дальше просто отдаётся."""
        if self._black is None:
            self._black = self._jpeg(Image.new("RGB", self.out_size, (0, 0, 0)), 50)
        return self._black

    def _publish(self, rgb: np.ndarray) -> None:
        """Отдать кадр браузерам с учётом паузы и заморозки.

        Пауза: на проектор уходит чёрное, предпросмотр на телефоне остаётся
        живым, но притушенным. Заморозка: ничего не меняется вообще, на экране
        держится последний кадр — песок можно трогать, картинка стоит.
        """
        with self._lock:
            paused, frozen = self._paused, self._frozen
        if not frozen:
            if not paused:                         # на паузе большой кадр не нужен
                self._live_full = self._jpeg(
                    Image.fromarray(rgb).resize(self.out_size, Image.BILINEAR), 78)
            shown = rgb if not paused else (rgb.astype(np.float32)
                                            * PAUSE_PREVIEW_DIM).astype(np.uint8)
            self._live_preview = self._jpeg(
                Image.fromarray(shown).resize(self.preview_size, Image.BILINEAR), 62)
        full = self._black_frame() if paused else self._live_full
        preview = self._live_preview
        with self._lock:
            if frozen and full is self._full and preview is self._preview:
                return                             # заморожено и показывать нечего нового
            self._full, self._preview = full, preview
            if not frozen:
                self._rgb = rgb                    # снимок во время заморозки — тот же кадр
            self._seq += 1

    # ---------- фоновый поток ----------

    def _loop(self) -> None:
        sensor, name, demo, why = open_sensor(self._sensor_name)
        with self._lock:
            self._demo = demo
            self._opening = False
            self._sensor_title = SENSOR_TITLES.get(name, name)
            self._sensor_error = why
        # Рабочая зона: у настоящего датчика в кадр попадают пол, стена и борта —
        # берём середину кадра. На демо-рельефе песок занимает весь кадр.
        self._proc.roi = None if demo else center_roi((sensor.intrinsics().height,
                                                       sensor.intrinsics().width))
        self._load_profile()

        t_prev = time.monotonic()
        fps_t, fps_n = t_prev, 0
        while not self._stop.is_set():
            frame_start = time.monotonic()
            try:
                depth = sensor.depth_frame().depth_mm
            except Exception as e:
                with self._lock:
                    self._error = f"кадр не пришёл: {e}"
                time.sleep(0.5)
                continue

            with self._lock:
                need_calib = self._calib_left
            if need_calib:                            # копим кадры ровного песка
                self._calib_frames.append(np.asarray(depth, dtype=np.float32).copy())
                with self._lock:
                    self._calib_left -= 1
                    done = self._calib_left == 0
                if done:
                    try:
                        plane = self._proc.calibrate_from_frames(self._calib_frames)
                        self._save_profile(plane, np.shape(depth))
                        with self._lock:
                            self._calibrated = True
                            self._calibrated_at = time.time()
                            self._error = ""
                    except Exception as e:
                        with self._lock:
                            self._error = f"калибровка не получилась: {e}"
                    self._calib_frames = []

            now = time.monotonic()
            dt, t_prev = max(now - t_prev, 1e-3), now
            profile = self._apply_mode()              # палитра, линии и плавность режима
            try:
                result = self._proc.process(depth, dt)
            except Exception as e:
                with self._lock:
                    self._error = f"кадр не обработался: {e}"
                time.sleep(0.2)
                continue

            self._publish(self._paint(result, profile))

            fps_n += 1
            with self._lock:
                self._stats = dict(result.stats, step_mm=round(result.contour_step_mm, 1))
                if now - fps_t >= 1.0:
                    self._fps_measured = fps_n / (now - fps_t)
                    fps_t, fps_n = now, 0

            time.sleep(max(0.0, 1.0 / self.fps - (time.monotonic() - frame_start)))

        try:
            sensor.stop()
        except Exception:
            pass
