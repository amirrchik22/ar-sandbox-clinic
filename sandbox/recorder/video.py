"""Запись видео ландшафта: ffmpeg сегментами по 60 секунд, а если ffmpeg нет — серия кадров.

Конвейер (sandbox/console/pipeline.py) уже готовит JPEG для проектора. Рекордер
берёт эти готовые кадры и не считает картинку заново: JPEG уходит прямо в ffmpeg
(-f image2pipe -vcodec mjpeg), процессор почти не нагружается.

Два режима записи:
    mp4     — найден ffmpeg. Пишем сегментами по 60 с: если ноутбук выключится,
              потеряется не больше минуты, а готовые сегменты останутся целыми.
    frames  — ffmpeg не найден (или сломался посреди записи). Тогда не падаем и
              ничего не теряем: складываем кадры по одному в папку <имя>.frames,
              а в журнал пишем честную строку, почему видео не собралось.

Проверить, есть ли ffmpeg на машине: ffmpeg_path() вернёт путь или None.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

# Сколько секунд длится один сегмент mp4.
SEGMENT_SECONDS = 60

# Расширение папки с кадрами запасного режима.
FRAMES_SUFFIX = ".frames"


# --------------------------------------------------------------- поиск ffmpeg

def ffmpeg_path() -> str | None:
    """Путь к ffmpeg или None. Переменная SANDBOX_FFMPEG перебивает поиск в PATH."""
    forced = os.environ.get("SANDBOX_FFMPEG")
    if forced:
        if forced.lower() in ("", "0", "нет", "no", "off"):     # запрет для тестов и показа
            return None
        return forced if Path(forced).exists() else shutil.which(forced)
    return shutil.which("ffmpeg")


def has_ffmpeg() -> bool:
    return ffmpeg_path() is not None


def ffmpeg_version(path: str | None = None) -> str:
    """Первая строка `ffmpeg -version` — для журнала и для пульта. Пусто, если нет."""
    exe = path or ffmpeg_path()
    if not exe:
        return ""
    try:
        out = subprocess.run([exe, "-version"], capture_output=True, text=True, timeout=5)
        return out.stdout.splitlines()[0].strip() if out.stdout else ""
    except (OSError, subprocess.SubprocessError, IndexError):
        return ""


# --------------------------------------------------------------- команды ffmpeg

def _encoder(hw: str, bitrate: str) -> list[str]:
    """Кодировщик: программный x264 по умолчанию, железный — если о нём попросили."""
    if hw == "vaapi":                                  # Intel/AMD на Linux
        return ["-vf", "format=nv12,hwupload", "-c:v", "h264_vaapi", "-b:v", bitrate]
    if hw == "nvenc":                                  # NVIDIA
        return ["-c:v", "h264_nvenc", "-preset", "p4", "-b:v", bitrate]
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p"]


def _segments(out: str) -> list[str]:
    """Нарезка на сегменты по 60 с. Каждый файл самостоятельный и сразу играбельный."""
    return ["-an",                                     # звука нет вовсе
            "-f", "segment", "-segment_time", str(SEGMENT_SECONDS),
            "-segment_format", "mp4", "-reset_timestamps", "1",
            "-movflags", "+faststart", out]


def ffmpeg_segments_cmd(width: int, height: int, fps: int, out_dir: str | Path,
                        prefix: str, hw: str = "none", bitrate: str = "4M",
                        exe: str | None = None) -> list[str]:
    """ffmpeg читает сырые кадры BGR из stdin и пишет сегменты prefix_000.mp4, prefix_001.mp4…

    Путь для источника кадров, который отдаёт массивы, а не JPEG (например, запись
    прямо из numpy). Консольный конвейер пользуется ffmpeg_mjpeg_segments_cmd.
    """
    out = str(Path(out_dir) / f"{prefix}_%03d.mp4")
    # -vaapi_device — общая настройка, её место до входа, иначе устройство ещё не открыто.
    device = ["-vaapi_device", "/dev/dri/renderD128"] if hw == "vaapi" else []
    base = [exe or "ffmpeg", "-hide_banner", "-loglevel", "warning", "-y", *device,
            "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{width}x{height}",
            "-r", str(fps), "-i", "-"]
    return base + _encoder(hw, bitrate) + _segments(out)


def ffmpeg_mjpeg_segments_cmd(fps: int, out_dir: str | Path, prefix: str,
                              hw: str = "none", bitrate: str = "4M",
                              exe: str | None = None) -> list[str]:
    """ffmpeg читает готовые JPEG из stdin (один за другим) и пишет сегменты mp4.

    Так пишет пульт: картинка уже сжата конвейером, второй раз её разбирать незачем.
    """
    out = str(Path(out_dir) / f"{prefix}_%03d.mp4")
    device = ["-vaapi_device", "/dev/dri/renderD128"] if hw == "vaapi" else []
    base = [exe or "ffmpeg", "-hide_banner", "-loglevel", "warning", "-y", *device,
            "-f", "image2pipe", "-vcodec", "mjpeg", "-framerate", str(fps), "-i", "-"]
    return base + _encoder(hw, bitrate) + _segments(out)


def concat_cmd(list_file: str | Path, out_path: str | Path,
               exe: str | None = None) -> list[str]:
    """Склейка сегментов без перекодирования; list_file — строки вида file 'seg_000.mp4'."""
    return [exe or "ffmpeg", "-hide_banner", "-loglevel", "warning", "-y",
            "-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(out_path)]


# --------------------------------------------------------------- запись

class VideoRecorder:
    """Пишет видео из потока готовых JPEG. Сам выбирает режим и сам не падает.

    Порядок работы: start() → feed(jpeg) на каждый кадр → stop().
    stop() возвращает словарь с итогом: режим, файлы, число кадров, длительность,
    строка журнала.
    """

    def __init__(self, out_dir: str | Path, prefix: str = "video", fps: int = 15,
                 hw: str = "none", bitrate: str = "4M", exe: str | None = None):
        self.out_dir = Path(out_dir)
        self.prefix = prefix
        self.fps = max(1, int(fps))
        self.hw = hw
        self.bitrate = bitrate
        self.exe = exe if exe is not None else ffmpeg_path()

        self.mode = "frames" if not self.exe else "mp4"
        self.proc: subprocess.Popen | None = None
        self.frames = 0
        self.started_at: float | None = None
        self.stopped_at: float | None = None
        self.notes: list[str] = []
        self._log_file = None
        self._frames_dir: Path | None = None
        self._code: int | None = None            # с каким кодом вышел ffmpeg

    # ---------- журнал ----------

    def note(self, text: str) -> None:
        """Честная строка в журнал: и в файл рядом с записью, и в вывод программы."""
        line = f"{time.strftime('%H:%M:%S')} {text}"
        self.notes.append(text)
        print(f"запись видео: {text}")
        try:
            with (self.out_dir / f"{self.prefix}.log").open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass

    # ---------- жизнь записи ----------

    def start(self) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.started_at = time.time()
        if self.mode == "mp4":
            cmd = ffmpeg_mjpeg_segments_cmd(self.fps, self.out_dir, self.prefix,
                                            hw=self.hw, bitrate=self.bitrate, exe=self.exe)
            try:
                self._log_file = (self.out_dir / f"{self.prefix}.ffmpeg.log").open("wb")
                self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                             stdout=subprocess.DEVNULL, stderr=self._log_file)
                self.note(f"пишу mp4 сегментами по {SEGMENT_SECONDS} с ({self.exe})")
            except OSError as e:
                self.proc = None
                self.note(f"ffmpeg не запустился ({e}) — сохраняю кадры по одному")
                self.mode = "frames"
        else:
            self.note("ffmpeg на этой машине не найден — сохраняю кадры по одному, "
                       "ничего не теряется; видео можно собрать позже")
        if self.mode == "frames":
            self._frames_dir = self.out_dir / f"{self.prefix}{FRAMES_SUFFIX}"
            self._frames_dir.mkdir(parents=True, exist_ok=True)

    def feed(self, jpeg: bytes) -> None:
        """Один готовый кадр. Если ffmpeg оборвался — молча переходим на кадры."""
        if not jpeg:
            return
        if self.mode == "mp4" and self.proc and self.proc.stdin:
            try:
                self.proc.stdin.write(jpeg)
                self.frames += 1
                return
            except (BrokenPipeError, OSError, ValueError) as e:
                self.note(f"ffmpeg оборвался посреди записи ({e}) — дальше сохраняю кадры")
                self._switch_to_frames()
        if self.mode == "frames":
            self._write_frame(jpeg)

    def _switch_to_frames(self) -> None:
        self.mode = "frames"
        self._close_proc()
        self._frames_dir = self.out_dir / f"{self.prefix}{FRAMES_SUFFIX}"
        self._frames_dir.mkdir(parents=True, exist_ok=True)

    def _write_frame(self, jpeg: bytes) -> None:
        if self._frames_dir is None:
            return
        self.frames += 1
        try:
            (self._frames_dir / f"k-{self.frames:06d}.jpg").write_bytes(jpeg)
        except OSError as e:
            self.note(f"кадр не записался: {e}")

    def _close_proc(self) -> None:
        proc, self.proc = self.proc, None
        if proc is not None:
            try:
                if proc.stdin:
                    proc.stdin.close()
            except OSError:
                pass
            try:
                self._code = proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
                self._code = proc.wait(timeout=5)
            if self._code:
                self.note(f"ffmpeg вышел с кодом {self._code} — смотри "
                          f"{self.prefix}.ffmpeg.log")
        if self._log_file is not None:
            try:
                self._log_file.close()
            except OSError:
                pass
            self._log_file = None

    def stop(self) -> dict:
        if self.stopped_at is None:
            self.stopped_at = time.time()
        if self.proc is not None:
            self._close_proc()
        if self.mode == "mp4":
            files = sorted(p.name for p in self.out_dir.glob(f"{self.prefix}_*.mp4"))
            if not files:
                self.note("ffmpeg не оставил ни одного файла — смотри "
                          f"{self.prefix}.ffmpeg.log")
        else:
            files = [self._frames_dir.name] if self._frames_dir else []
        return self.result(files, self._code)

    def result(self, files: list[str] | None = None, code: int | None = None) -> dict:
        if code is None:
            code = self._code
        if files is None:
            files = (sorted(p.name for p in self.out_dir.glob(f"{self.prefix}_*.mp4"))
                     if self.mode == "mp4"
                     else ([self._frames_dir.name] if self._frames_dir else []))
        started = self.started_at or time.time()
        seconds = (self.stopped_at or time.time()) - started
        return {
            "mode": self.mode,
            "dir": str(self.out_dir),
            "prefix": self.prefix,
            "files": files,
            "frames": self.frames,
            "fps": self.fps,
            "seconds": round(seconds, 1),
            "started_at": started,
            "stopped_at": self.stopped_at,
            "notes": list(self.notes),
            "ffmpeg": bool(self.exe),
            "code": code,
        }

    @property
    def running(self) -> bool:
        return self.started_at is not None and self.stopped_at is None
