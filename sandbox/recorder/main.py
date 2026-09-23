"""Рекордер: фоновая запись видео ландшафта во время занятия.

Кадры не считаются заново — берутся готовые у конвейера пульта
(sandbox/console/pipeline.py, метод latest("full") отдаёт JPEG проектора).
Поэтому запись почти ничего не стоит по процессору и не сбивает частоту кадров
на проекции.

Сам файл пишет sandbox/recorder/video.py: mp4 сегментами по 60 с, если в системе
есть ffmpeg, и серия кадров, если его нет.

Проверить запись на машине без датчика:

    ./.venv/bin/python -m sandbox.recorder.main --seconds 5
"""
from __future__ import annotations

import argparse
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Protocol

from .video import VideoRecorder, ffmpeg_path, ffmpeg_version

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
        try:
            usage = shutil.disk_usage(self.out_dir)
        except OSError:
            return False
        free_pct = 100 * usage.free / usage.total if usage.total else 100.0
        if free_pct >= STOP_AT_FREE_PCT:
            return False
        self.stopped_by_disk = True
        self.video.note(f"на диске осталось {free_pct:.1f}% — остановил запись, "
                        "чтобы ноутбук продолжил работать")
        return True


# --------------------------------------------------------------- запуск руками

def main() -> None:
    parser = argparse.ArgumentParser(description="Пробная запись видео ландшафта")
    parser.add_argument("--seconds", type=float, default=5.0, help="сколько секунд писать")
    parser.add_argument("--fps", type=int, default=12)
    parser.add_argument("--sensor", default="fake", help="fake | kinect2 | kinect1")
    parser.add_argument("--out", default="", help="куда положить запись")
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
    time.sleep(args.seconds)
    result = recorder.stop()
    server.stop()
    print(f"режим: {result['mode']}, кадров: {result['frames']}, "
          f"{result['seconds']} с, файлы: {result['files']}")
    print(f"папка: {out}")


if __name__ == "__main__":
    main()
