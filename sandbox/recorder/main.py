"""Рекордер: фоновая запись видео ландшафта во время занятия.

Кадры не считаются заново — берутся готовые у конвейера пульта
(sandbox/console/pipeline.py, метод latest("full") отдаёт JPEG проектора).
Поэтому запись почти ничего не стоит по процессору и не сбивает частоту кадров
на проекции.

Сам файл пишет sandbox/recorder/video.py: mp4 сегментами по 60 с, если в системе
есть ffmpeg, и серия кадров, если его нет.

Второй рекордер, CameraRecorder, пишет то, что видит цветная камера датчика.
Он не запускается сам никогда: его включает специалист в пульте, и по умолчанию
выбран режим «выключено». Причина в файле video.py, рядом с CAMERA_WARNING.

Проверить запись на машине без датчика:

    ./.venv/bin/python -m sandbox.recorder.main --seconds 5

Проверить запись с камеры на живом датчике (в кадре только ящик):

    ./.venv/bin/python -m sandbox.recorder.main --camera box --seconds 10
"""
from __future__ import annotations

import argparse
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Protocol

from .video import (CAMERA_MODES, CAMERA_OFF, CAMERA_QUALITY, VideoRecorder,
                    camera_jpeg, camera_zone, ffmpeg_path, ffmpeg_version,
                    normalize_camera_mode)

# Запасной режим (без ffmpeg) складывает кадры по одному — они весят куда больше
# видео, поэтому берём их реже: не плавное кино, а чтобы занятие не пропало.
FRAMES_FPS = int(os.environ.get("SANDBOX_RECORD_FRAMES_FPS", "4"))

# Совсем мало места — запись останавливается сама, чтобы не забить диск кабинета.
STOP_AT_FREE_PCT = 2.0

# Как часто (в кадрах) смотреть на свободное место.
DISK_CHECK_TICKS = 200


class FrameSource(Protocol):
    """Что рекордеру нужно от конвейера: последний готовый JPEG и его номер."""

    def latest(self, kind: str = "full") -> tuple[bytes | None, int]: ...


class CameraSource(Protocol):
    """Что рекордеру нужно от датчика: последний кадр цветной камеры или None."""

    def color_frame(self): ...


def free_pct(path: str | Path) -> float | None:
    """Сколько процентов диска свободно. None — посмотреть не вышло."""
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return None
    return 100 * usage.free / usage.total if usage.total else 100.0


class LandscapeRecorder:
    """Пишет видео ландшафта, пока идёт занятие.

    start() поднимает фоновый поток, он с частотой fps забирает у конвейера
    последний кадр и отдаёт его в VideoRecorder. stop() закрывает файл и
    возвращает итог записи.
    """

    def __init__(self, frames: FrameSource, out_dir: str | Path, prefix: str = "video",
                 fps: int = 12, session_id: str = "", hw: str = "none",
                 exe: str | None = None):
        self.frames = frames
        self.out_dir = Path(out_dir)
        self.prefix = prefix
        self.fps = max(1, int(fps))
        self.session_id = session_id
        self.video = VideoRecorder(self.out_dir, prefix=prefix, fps=self.fps, hw=hw, exe=exe)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._waited = 0                          # тиков без единого кадра от конвейера
        self.frames_fps = max(1, min(self.fps, FRAMES_FPS))
        self.stopped_by_disk = False

    # ---------- управление ----------

    def start(self) -> dict:
        if self._thread is not None:
            raise RuntimeError("запись уже идёт")
        self.video.start()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="record", daemon=True)
        self._thread.start()
        return self.state()

    def stop(self) -> dict:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=20)
        result = self.video.stop()
        result["session"] = self.session_id
        result["stopped_by_disk"] = self.stopped_by_disk
        return result

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def state(self) -> dict:
        with self._lock:
            frames = self.video.frames
        started = self.video.started_at or time.time()
        return {
            "on": self.running,
            "mode": self.video.mode,
            "session": self.session_id,
            "prefix": self.prefix,
            "frames": frames,
            "fps": self.fps,
            "seconds": round((self.video.stopped_at or time.time()) - started, 1),
            "started_at": self.video.started_at,
            "ffmpeg": bool(self.video.exe),
            "stopped_by_disk": self.stopped_by_disk,
            "notes": list(self.video.notes),
        }

    # ---------- фоновый поток ----------

    def _loop(self) -> None:
        last_seq = -1
        ticks = 0
        while not self._stop.is_set():
            tick = time.monotonic()
            mp4 = self.video.mode == "mp4"
            period = 1.0 / (self.fps if mp4 else self.frames_fps)

            data, seq = self.frames.latest("full")
            if data is None:
                self._waited += 1
                if self._waited == self.fps * 5:      # пять секунд тишины — сказать честно
                    self.video.note("конвейер пока не отдаёт кадры — жду")
            elif mp4 or seq != last_seq:
                # В mp4 пишем каждый тик: так длительность видео совпадает с занятием.
                # В запасном режиме повторы не сохраняем — незачем занимать диск.
                with self._lock:
                    self.video.feed(data)
                last_seq = seq

            ticks += 1
            if ticks % DISK_CHECK_TICKS == 0 and self._disk_is_full():
                break
            self._stop.wait(max(0.0, period - (time.monotonic() - tick)))

    def _disk_is_full(self) -> bool:
        """Диск почти кончился — честно останавливаем запись, занятие продолжается."""
        left = free_pct(self.out_dir)
        if left is None or left >= STOP_AT_FREE_PCT:
            return False
        self.stopped_by_disk = True
        self.video.note(f"на диске осталось {left:.1f}% — остановил запись, "
                        "чтобы ноутбук продолжил работать")
        return True


class CameraRecorder:
    """Запись с цветной камеры датчика. Сама по себе не включается никогда.

    Режимы берутся из video.py:
        off   — ничего не пишем. Так стоит по умолчанию, и start() в этом режиме
                отказывается работать: не бывает «случайно включившейся» камеры.
        box   — вид сверху, обрезанный по рабочей зоне: руки и песок, без лица.
        full  — весь кадр. Включать только с согласиями родителей на руках.

    Кадры берутся у датчика (метод color_frame). Если камера не включена или
    кадров нет, рекордер не падает и не тормозит занятие: он честно пишет в
    журнал, что писать нечего, и продолжает ждать.
    """

    def __init__(self, camera: CameraSource, out_dir: str | Path, prefix: str = "camera",
                 fps: int = 10, mode: str = CAMERA_OFF, zone=None, session_id: str = "",
                 quality: int = CAMERA_QUALITY, hw: str = "none", exe: str | None = None):
        self.camera = camera
        self.out_dir = Path(out_dir)
        self.prefix = prefix
        self.fps = max(1, min(int(fps), 30))
        self.mode = normalize_camera_mode(mode)
        self.zone = camera_zone(zone)
        self.session_id = session_id
        self.quality = int(quality)
        self.video = VideoRecorder(self.out_dir, prefix=prefix, fps=self.fps, hw=hw, exe=exe)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._last_t: float | None = None         # время последнего взятого кадра
        self.skipped = 0                          # тиков без нового кадра
        self.stopped_by_disk = False

    # ---------- управление ----------

    def start(self) -> dict:
        if self.mode == CAMERA_OFF:
            raise RuntimeError("режим записи с камеры «выключено» — писать нечего")
        if self._thread is not None:
            raise RuntimeError("запись с камеры уже идёт")
        self.video.start()
        self.video.note(f"камера: режим «{self.mode}», рабочая зона {self.zone}")
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="camera", daemon=True)
        self._thread.start()
        return self.state()

    def stop(self) -> dict:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=20)
        result = self.video.stop()
        result["session"] = self.session_id
        result["camera_mode"] = self.mode
        result["zone"] = list(self.zone)
        result["skipped"] = self.skipped
        result["stopped_by_disk"] = self.stopped_by_disk
        return result

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def state(self) -> dict:
        with self._lock:
            frames = self.video.frames
        started = self.video.started_at or time.time()
        return {
            "on": self.running,
            "mode": self.mode,
            "zone": list(self.zone),
            "session": self.session_id,
            "prefix": self.prefix,
            "frames": frames,
            "fps": self.fps,
            "seconds": round((self.video.stopped_at or time.time()) - started, 1),
            "started_at": self.video.started_at,
            "ffmpeg": bool(self.video.exe),
            "stopped_by_disk": self.stopped_by_disk,
            "notes": list(self.video.notes),
        }

    # ---------- фоновый поток ----------

    def _loop(self) -> None:
        period = 1.0 / self.fps
        ticks = 0
        while not self._stop.is_set():
            tick = time.monotonic()
            try:
                frame = self.camera.color_frame()
            except Exception as e:                # noqa: BLE001 — камера не должна ронять занятие
                frame = None
                if self.skipped == 0:
                    self.video.note(f"камера не отдала кадр ({e}) — жду дальше")
            if frame is None or getattr(frame, "t", None) == self._last_t:
                self.skipped += 1
                if self.skipped == self.fps * 5:
                    self.video.note("камера пока не отдаёт кадры — жду")
            else:
                self._last_t = frame.t
                jpeg = camera_jpeg(frame.bgr, self.mode, self.zone, self.quality)
                if jpeg:
                    with self._lock:
                        self.video.feed(jpeg)
            ticks += 1
            if ticks % DISK_CHECK_TICKS == 0 and self._disk_is_full():
                break
            self._stop.wait(max(0.0, period - (time.monotonic() - tick)))

    def _disk_is_full(self) -> bool:
        left = free_pct(self.out_dir)
        if left is None or left >= STOP_AT_FREE_PCT:
            return False
        self.stopped_by_disk = True
        self.video.note(f"на диске осталось {left:.1f}% — остановил запись с камеры")
        return True


# --------------------------------------------------------------- запуск руками

def main() -> None:
    parser = argparse.ArgumentParser(description="Пробная запись видео ландшафта")
    parser.add_argument("--seconds", type=float, default=5.0, help="сколько секунд писать")
    parser.add_argument("--fps", type=int, default=12)
    parser.add_argument("--sensor", default="fake", help="fake | kinect2 | kinect1")
    parser.add_argument("--out", default="", help="куда положить запись")
    parser.add_argument("--camera", default=CAMERA_OFF, choices=list(CAMERA_MODES),
                        help="запись с цветной камеры датчика: off (по умолчанию) | box | full")
    parser.add_argument("--camera-fps", type=int, default=10)
    args = parser.parse_args()

    from sandbox.console.pipeline import FrameServer      # тяжёлый импорт — только здесь

    if ffmpeg_path():
        print(f"ffmpeg: {ffmpeg_version()}")
    else:
        print("ffmpeg не найден — сохраню серию кадров, ничего не потеряется")

    out = Path(args.out) if args.out else Path("data") / "sessions" / time.strftime("probe-%H%M%S")
    server = FrameServer(fps=max(args.fps, 10), sensor_name=args.sensor)
    server.start()
    for _ in range(100):                                  # ждём первый кадр
        if server.latest("full")[0] is not None:
            break
        time.sleep(0.1)

    recorder = LandscapeRecorder(server, out, prefix="probe", fps=args.fps)
    recorder.start()

    camera_rec = None
    if args.camera != CAMERA_OFF:
        # Камеру берём у того же датчика, что снимает рельеф: второй раз его
        # не открыть — Kinect v2 отдаётся одному процессу.
        source = getattr(server, "sensor", None)
        if source is None or source.color_frame() is None:
            print("камера не отдаёт кадров: датчик запущен без цвета "
                  "(SANDBOX_KINECT_COLOR=1 или Kinect2Sensor(color=True))")
        else:
            camera_rec = CameraRecorder(source, out, prefix="camera", fps=args.camera_fps,
                                        mode=args.camera)
            camera_rec.start()

    time.sleep(args.seconds)
    result = recorder.stop()
    camera_result = camera_rec.stop() if camera_rec is not None else None
    server.stop()
    print(f"режим: {result['mode']}, кадров: {result['frames']}, "
          f"{result['seconds']} с, файлы: {result['files']}")
    if camera_result:
        print(f"камера: режим {camera_result['camera_mode']}, "
              f"кадров {camera_result['frames']}, файлы: {camera_result['files']}")
    print(f"папка: {out}")


if __name__ == "__main__":
    main()
