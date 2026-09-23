"""Kinect v2 (Xbox One) через libfreenect2 — базовый датчик объекта.

Что за датчик: кадр глубины 512×424 точки, поле зрения 70°×60°, рабочая
дистанция 0,5–4,5 м, подключение USB 3.0 плюс свой блок питания (адаптер
Kinect Adapter для ПК). Меряет расстояние временем полёта света, а не
ИК-сеткой, поэтому не теряет крутые склоны и борта ящика.

Как устроено чтение. Библиотека libfreenect2 написана на C++, готовой обёртки
для Python на этой машине нет. Поэтому кадры снимает маленькая программа на C++
build/kinect_grabber (исходник tools/kinect_grabber.cpp, сборка
tools/build_grabber.sh): она печатает кадры в стандартный вывод, а этот класс
запускает её подпроцессом и читает поток. Мост через трубу проще и надёжнее
разделяемой памяти и одинаково работает на macOS и на Ubuntu.

Формат кадра в потоке: "KIN2", номер кадра uint32, ширина uint32, высота
uint32, затем 512*424 расстояний float32 в миллиметрах (0 = нет данных).

Цветная камера. У датчика есть ещё и обычная камера, 1920x1080 и 30 кадров в
секунду. Занятию она не нужна, поэтому по умолчанию не включается вовсе: в
кадр попадает лицо ребёнка, а это персональные данные несовершеннолетнего.
Включается отдельно — Kinect2Sensor(color=True); тогда программа захвата
запускается с ключом --color и добавляет в тот же поток цветные кадры со своим
заголовком: "KINC", номер uint32, ширина uint32, высота uint32, формат uint32
(1 = BGR), длина uint32, дальше точки. Кадр приходит уменьшенным вдвое
(960x540) — так через трубу идёт 1,5 МБ на кадр вместо 8 МБ.

Главное правило потока: кадры глубины не теряются никогда, цветные роняются
свободно. Занятие ведётся по рельефу, видео с камеры — дело десятое.

Геометрия объекта: ось датчика 1,30 м над песком (1,45 м от пола), зона охвата
на этой высоте 1,82×1,50 м, 3,6 мм на точку (ящик 1,2 м — 337 точек поперёк),
шум по высоте ≈ 2 мм (живой замер 23.09 на дистанции 0,89 м дал 1,3 мм). Зона
охвата больше картинки проектора (1,24×0,95 м), поэтому размер ящика датчик не
ограничивает. Стена попадает в кадр ниже 0,86 м от пола — эту часть кадра
обрезаем маской ящика.

Проверено живьём 23.09 (macOS 26, Apple M5 Pro, датчик 090373635147):
  - обработчик глубины OpenCL на Маке выдаёт мусор, по умолчанию CpuPacketPipeline;
  - датчик не работает через USB-хабы, нужен прямой порт USB 3.0.
"""
from __future__ import annotations

import os
import struct
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

import numpy as np

from .base import ColorFrame, DepthFrame, Intrinsics, SensorHealth

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GRABBER = ROOT / "build" / "kinect_grabber"

MAGIC = b"KIN2"                       # кадр глубины
MAGIC_COLOR = b"KINC"                 # кадр цветной камеры
HEADER = struct.Struct("<4sIII")      # магическое слово, номер кадра, ширина, высота
DEPTH_TAIL = struct.Struct("<III")    # после магического слова: номер, ширина, высота
COLOR_TAIL = struct.Struct("<IIIII")  # номер, ширина, высота, формат, длина данных
COLOR_BGR = 1                         # единственный пока формат цветного кадра

# Больше этого цветной кадр быть не может — защита от сбившегося потока.
COLOR_MAX_BYTES = 1920 * 1080 * 3


class Kinect2Error(RuntimeError):
    """Датчик не запустился или пропал во время работы."""


class Kinect2Sensor:
    name = "kinect2"

    # Паспортные данные датчика; точные параметры объектива даст калибровка.
    WIDTH, HEIGHT = 512, 424
    FOV_DEG = (70.0, 60.0)
    RANGE_MM = (500, 4500)
    NOMINAL_FX = NOMINAL_FY = 365.0   # пикселей, оценка по полю 70°; типично 360–370

    def __init__(self, grabber: str | os.PathLike | None = None, pipeline: str = "cpu",
                 serial: str | None = None, start_timeout: float = 60.0,
                 restart: bool = True, max_restarts: int = 20,
                 color: bool = False, color_fps: float = 10.0,
                 color_scale: int = 2) -> None:
        self.grabber = Path(grabber or os.environ.get("SANDBOX_KINECT_GRABBER") or DEFAULT_GRABBER)
        self.pipeline = pipeline            # cpu (проверено) | opencl (на Ubuntu с видеокартой)
        self.serial = serial
        self.start_timeout = start_timeout
        self.restart = restart              # перезапускать программу захвата, если она упала
        self.max_restarts = max_restarts
        # Цветная камера: по умолчанию выключена — так решено, в кадр попадает
        # лицо ребёнка. Переменная SANDBOX_KINECT_COLOR=1 включает её без правки
        # кода: пригодится, когда клиника решит, что запись с камеры ей нужна.
        self.color = bool(color or os.environ.get("SANDBOX_KINECT_COLOR") == "1")
        self.color_fps = float(color_fps)
        self.color_scale = int(color_scale)

        self._proc: subprocess.Popen | None = None
        self._reader: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._lock = threading.Condition()
        self._frame: np.ndarray | None = None     # последний кадр, float32 мм
        self._frame_t: float = 0.0                # монотонное время приёма
        self._seq: int = 0                        # счётчик принятых кадров
        self._taken: int = 0                      # номер кадра, отданного наружу
        self._stamps: deque[float] = deque(maxlen=60)
        self._running = False
        self._health = SensorHealth()
        self._stderr_tail: deque[str] = deque(maxlen=20)
        self._restarts = 0
        self._color: np.ndarray | None = None     # последний цветной кадр, BGR uint8
        self._color_t: float = 0.0
        self._color_seq: int = 0
        self._color_stamps: deque[float] = deque(maxlen=30)

    # ---------- запуск и остановка ----------

    def start(self) -> None:
        if self._running:
            return
        if not self.grabber.exists():
            raise Kinect2Error(
                f"нет программы захвата {self.grabber}; соберите её: bash tools/build_grabber.sh")
        with self._lock:                 # начинаем занятие с чистого счётчика кадров
            self._frame = None
            self._color = None
            self._color_seq = 0
            self._color_stamps.clear()
            self._seq = self._taken = 0
            self._stamps.clear()
            self._stderr_tail.clear()
            self._restarts = 0
        self._running = True
        self._spawn()
        self._reader = threading.Thread(target=self._read_loop, name="kinect2-reader", daemon=True)
        self._reader.start()
        # Ждём первый кадр. Датчику нужно время на прошивку и запуск потока, а если
        # он остался неприбранным после прошлого занятия — первая попытка уходит на
        # сброс по USB, и поднимает его уже следующий запуск программы захвата
        # (перезапуском занимается поток чтения).
        deadline = time.monotonic() + self.start_timeout
        with self._lock:
            while self._seq == 0 and time.monotonic() < deadline:
                if not self._running:
                    break
                self._lock.wait(0.2)
            got = self._seq
        if got == 0:
            err = " / ".join(list(self._stderr_tail)[-3:]) or "нет сообщений"
            self.stop()
            raise Kinect2Error(f"датчик не отдал ни одного кадра за {self.start_timeout:.0f} с ({err})")

    def stop(self) -> None:
        self._running = False
        proc, self._proc = self._proc, None
        _kill(proc)
        with self._lock:
            self._lock.notify_all()
        for th in (self._reader, self._stderr_thread):
            if th is not None and th.is_alive() and th is not threading.current_thread():
                th.join(timeout=2)
        self._reader = self._stderr_thread = None

    def __enter__(self) -> "Kinect2Sensor":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    # ---------- кадры ----------

    def depth_frame(self, timeout: float = 5.0) -> DepthFrame:
        """Свежий кадр расстояний, uint16 мм (как договорено в base.py; 0 = нет данных)."""
        t, frame = self._wait_frame(timeout)
        depth = np.nan_to_num(frame, nan=0.0, posinf=0.0, neginf=0.0)
        return DepthFrame(t, np.clip(np.rint(depth), 0, 65535).astype(np.uint16))

    def depth_frame_float(self, timeout: float = 5.0) -> tuple[float, np.ndarray]:
        """То же, но без округления: float32 мм. Нужен для калибровки и замера шума."""
        t, frame = self._wait_frame(timeout)
        return t, np.nan_to_num(frame, nan=0.0, posinf=0.0, neginf=0.0)

    def _wait_frame(self, timeout: float) -> tuple[float, np.ndarray]:
        deadline = time.monotonic() + timeout
        with self._lock:
            while True:
                if self._frame is not None and self._seq > self._taken:
                    self._taken = self._seq
                    return self._frame_t, self._frame
                if not self._running:
                    raise Kinect2Error("датчик остановлен")
                left = deadline - time.monotonic()
                if left <= 0:
                    err = " / ".join(list(self._stderr_tail)[-2:]) or "нет сообщений"
                    raise Kinect2Error(f"кадр не пришёл за {timeout:.1f} с ({err})")
                self._lock.wait(left)

    def color_frame(self) -> ColorFrame | None:
        """Последний кадр цветной камеры или None.

        None значит одно из двух: камера не включена (обычное дело — так стоит по
        умолчанию) или кадра ещё не было. Ждать здесь нечего: метод не блокирует.
        Занятие идёт по глубине, и цвет никогда его не задерживает.

        Кадр отдаётся BGR uint8, как договорено в base.py. Время t — монотонное,
        как у кадра глубины: по нему видно, новый это кадр или тот же самый.
        """
        if not self.color:
            return None
        with self._lock:
            frame, t = self._color, self._color_t
        if frame is None:
            return None
        return ColorFrame(t, frame)

    def color_stats(self) -> dict:
        """Что происходит с цветной камерой — для пульта и журнала."""
        with self._lock:
            seq, t = self._color_seq, self._color_t
            stamps = list(self._color_stamps)
        fps = 0.0
        if len(stamps) >= 2:
            span = stamps[-1] - stamps[0]
            if span > 0:
                fps = round((len(stamps) - 1) / span, 1)
        size = None
        with self._lock:
            if self._color is not None:
                size = (int(self._color.shape[1]), int(self._color.shape[0]))
        return {"on": self.color, "frames": seq, "fps": fps, "size": size,
                "last_t": t}

    def intrinsics(self) -> Intrinsics:
        return Intrinsics(fx=self.NOMINAL_FX, fy=self.NOMINAL_FY,
                          cx=self.WIDTH / 2, cy=self.HEIGHT / 2,
                          width=self.WIDTH, height=self.HEIGHT)

    def health(self) -> SensorHealth:
        with self._lock:
            stamps = list(self._stamps)
            self._health.frames_total = self._seq
        fps = 0.0
        if len(stamps) >= 2:
            span = stamps[-1] - stamps[0]
            if span > 0:
                fps = (len(stamps) - 1) / span
        self._health.fps = round(fps, 1)
        return self._health

    @property
    def messages(self) -> list[str]:
        """Последние служебные сообщения программы захвата — для пульта и журнала."""
        return list(self._stderr_tail)

    # ---------- внутреннее ----------

    def _command(self) -> list[str]:
        """Чем запускаем программу захвата. Отдельным методом — чтобы можно было проверить."""
        cmd = [str(self.grabber), "--pipeline", self.pipeline, "--quiet"]
        if self.serial:
            cmd += ["--serial", self.serial]
        if self.color:
            cmd += ["--color", "--color-fps", f"{self.color_fps:g}",
                    "--color-scale", str(self.color_scale)]
        return cmd

    def _spawn(self) -> None:
        self._proc = subprocess.Popen(self._command(), stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, bufsize=0)
        self._stderr_thread = threading.Thread(target=self._drain_stderr, args=(self._proc,),
                                               name="kinect2-stderr", daemon=True)
        self._stderr_thread.start()

    def _drain_stderr(self, proc: subprocess.Popen) -> None:
        """Служебные сообщения читаем всегда, иначе буфер трубы переполнится и захват встанет."""
        if proc.stderr is None:
            return
        for raw in iter(proc.stderr.readline, b""):
            line = raw.decode("utf-8", "replace").strip()
            if line:
                self._stderr_tail.append(line)

    def _read_loop(self) -> None:
        while self._running:
            proc = self._proc
            if proc is None or proc.stdout is None:
                break
            try:
                self._pump(proc)
            except Exception as e:                       # noqa: BLE001 — любую ошибку пишем в health
                self._health.errors += 1
                self._health.last_error = str(e)
            finally:
                # Старую копию программы захвата обязательно гасим, иначе она
                # продолжит держать датчик и новая его не откроет.
                _kill(proc)
            if not self._running:
                break
            # поток кончился: программа захвата упала или датчик отвалился
            self._health.errors += 1
            tail = " / ".join(list(self._stderr_tail)[-2:]) or "нет сообщений"
            self._health.last_error = f"захват прервался ({tail})"
            if not self.restart or self._restarts >= self.max_restarts:
                break
            self._restarts += 1
            time.sleep(min(1.0 + 0.5 * self._restarts, 5.0))   # короткая пауза: датчику дать вернуться на шину
            if not self._running:
                break
            try:
                self._spawn()
            except Exception as e:                       # noqa: BLE001
                self._health.last_error = f"перезапуск не вышел: {e}"
                break
        self._running = False
        with self._lock:
            self._lock.notify_all()

    def _pump(self, proc: subprocess.Popen) -> None:
        """Разбор потока: по первым четырём байтам видно, кадр какого рода пришёл."""
        stdout = proc.stdout
        assert stdout is not None
        while self._running:
            magic = _read_exactly(stdout, 4)
            if magic is None:
                return                                    # поток закончился
            if magic == MAGIC:
                if not self._read_depth(stdout):
                    return
            elif magic == MAGIC_COLOR:
                if not self._read_color(stdout):
                    return
            else:
                raise Kinect2Error("поток сбился: не найдено начало кадра")

    def _read_depth(self, stdout) -> bool:
        tail = _read_exactly(stdout, DEPTH_TAIL.size)
        if tail is None:
            return False
        _num, w, h = DEPTH_TAIL.unpack(tail)
        if not (0 < w <= 4096 and 0 < h <= 4096):
            raise Kinect2Error(f"странный размер кадра {w}×{h}")
        payload = _read_exactly(stdout, w * h * 4)
        if payload is None:
            return False
        frame = np.frombuffer(payload, dtype="<f4").reshape(h, w)
        now = time.monotonic()
        with self._lock:
            self._frame = frame
            self._frame_t = now
            self._seq += 1
            self._stamps.append(now)
            self._lock.notify_all()
        return True

    def _read_color(self, stdout) -> bool:
        """Цветной кадр. Держим только последний: прошлый никому не нужен."""
        tail = _read_exactly(stdout, COLOR_TAIL.size)
        if tail is None:
            return False
        _num, w, h, fmt, length = COLOR_TAIL.unpack(tail)
        if not (0 < w <= 4096 and 0 < h <= 4096) or length > COLOR_MAX_BYTES:
            raise Kinect2Error(f"странный цветной кадр {w}×{h}, {length} байт")
        payload = _read_exactly(stdout, length)
        if payload is None:
            return False
        if fmt != COLOR_BGR or length != w * h * 3:
            return True                          # непонятный кадр просто пропускаем
        frame = np.frombuffer(payload, dtype=np.uint8).reshape(h, w, 3)
        now = time.monotonic()
        with self._lock:
            self._color = frame
            self._color_t = now
            self._color_seq += 1
            self._color_stamps.append(now)
        return True


def _kill(proc: subprocess.Popen | None) -> None:
    """Гасим программу захвата: сначала вежливо, через три секунды — насовсем."""
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass
    except Exception:                    # noqa: BLE001 — процесс уже мог умереть сам
        pass


def _read_exactly(stream, n: int) -> bytes | None:
    """Читает ровно n байт; None, если поток закончился раньше."""
    chunks: list[bytes] = []
    left = n
    while left > 0:
        chunk = stream.read(left)
        if not chunk:
            return None
        chunks.append(chunk)
        left -= len(chunk)
    return b"".join(chunks) if len(chunks) > 1 else chunks[0]
