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

Здесь же — правила записи с цветной камеры датчика (camera_jpeg и соседи).
Камера умеет 1920x1080 и тридцать кадров в секунду, но по умолчанию с неё не
пишется ничего: в кадр попадает лицо ребёнка, а это персональные данные
несовершеннолетнего — нужны согласия родителей, шифрование и срок хранения.
Режим выбирает клиника, в пульте; по умолчанию «выключено».
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import numpy as np

# Сколько секунд длится один сегмент mp4.
SEGMENT_SECONDS = 60

# Расширение папки с кадрами запасного режима.
FRAMES_SUFFIX = ".frames"


# ------------------------------------------------- запись с цветной камеры

# Три режима, и по умолчанию первый. Менять порядок нельзя: пульт показывает
# их в этом порядке, и первый всегда самый осторожный.
CAMERA_OFF, CAMERA_BOX, CAMERA_FULL = "off", "box", "full"
CAMERA_MODES = (CAMERA_OFF, CAMERA_BOX, CAMERA_FULL)

CAMERA_TITLES = {
    CAMERA_OFF: "Выключено",
    CAMERA_BOX: "Только ящик",
    CAMERA_FULL: "Полностью",
}

CAMERA_NOTES = {
    CAMERA_OFF: "С камеры не пишется ничего. Так стоит по умолчанию.",
    CAMERA_BOX: ("Пишется вид сверху, обрезанный по рабочей зоне: видны руки и песок, "
                 "лицо в кадр не попадает — всё, что за бортами, в запись не идёт."),
    CAMERA_FULL: "Пишется весь кадр камеры целиком, вместе с лицом ребёнка.",
}

# Этот текст пульт обязан показать рядом с выбором «полностью».
CAMERA_WARNING = ("Запись лица ребёнка — персональные данные несовершеннолетнего. "
                  "Нужны письменные согласия родителей, порядок хранения и срок, "
                  "после которого записи удаляются.")

# Рабочая зона в долях цветного кадра: слева, сверху, ширина, высота.
#
# Откуда числа. Цветная камера датчика видит 84,1° в ширину и 53,8° в высоту.
# Ось датчика 1,45 м от пола, поверхность песка 198 мм — значит до песка 1,252 м.
# На этой дистанции камера охватывает 2*1,252*tg(42,05°) = 2,26 м в ширину и
# 2*1,252*tg(26,9°) = 1,27 м в высоту. Ящик внутри 1,2 x 0,8 м, то есть занимает
# 1,2/2,26 = 0,53 ширины кадра и 0,8/1,27 = 0,63 высоты. Добавлен запас, чтобы
# в кадр попали борта ящика и было видно, что песок не обрезан.
#
# Числа верны для расчётной геометрии. Повесят датчик — зону поправят из пульта
# (ручка /api/record/camera) или переменной SANDBOX_CAMERA_ZONE="x,y,ш,в",
# не трогая код.
DEFAULT_CAMERA_ZONE = (0.21, 0.17, 0.58, 0.68)

# В режиме «только ящик» зона не может занимать почти весь кадр: иначе режим
# перестанет отличаться от «полностью», а вместе с ним пропадёт и его обещание.
MAX_BOX_ZONE = 0.9

# Качество JPEG для записи с камеры: 80 — видно руки и песок, файл вменяемый.
CAMERA_QUALITY = int(os.environ.get("SANDBOX_CAMERA_QUALITY", "80"))


def camera_mode_title(mode: str) -> str:
    return CAMERA_TITLES.get(mode, mode)


def normalize_camera_mode(mode: str | None) -> str:
    """Чужое или пустое значение всегда превращается в «выключено» — так безопаснее."""
    value = str(mode or "").strip().lower()
    return value if value in CAMERA_MODES else CAMERA_OFF


def normalize_zone(zone) -> tuple[float, float, float, float]:
    """Четыре доли кадра: привести к числам, обрезать по краям, не дать выродиться."""
    try:
        x, y, w, h = (float(v) for v in zone)
    except (TypeError, ValueError):
        raise ValueError("рабочая зона задаётся четырьмя числами: x, y, ширина, высота")
    x = min(max(x, 0.0), 1.0)
    y = min(max(y, 0.0), 1.0)
    w = min(max(w, 0.05), MAX_BOX_ZONE)
    h = min(max(h, 0.05), MAX_BOX_ZONE)
    if x + w > 1.0:
        x = max(0.0, 1.0 - w)
    if y + h > 1.0:
        y = max(0.0, 1.0 - h)
    return (round(x, 4), round(y, 4), round(w, 4), round(h, 4))


def camera_zone(zone=None) -> tuple[float, float, float, float]:
    """Рабочая зона: переданная, или из SANDBOX_CAMERA_ZONE, или расчётная."""
    if zone is not None:
        return normalize_zone(zone)
    raw = os.environ.get("SANDBOX_CAMERA_ZONE", "").strip()
    if raw:
        try:
            return normalize_zone(raw.replace(";", ",").split(","))
        except ValueError:
            pass                                  # мусор в переменной — берём расчётную
    return normalize_zone(DEFAULT_CAMERA_ZONE)


def crop_zone(bgr: np.ndarray, zone=None) -> np.ndarray:
    """Вырезать рабочую зону. Всё, что за бортами, в результат не попадает вовсе.

    Обрезаем, а не затемняем: затемнённое можно высветлить обратно, а вырезанного
    в файле просто нет. Для обещания «лицо в кадр не попадает» это важнее.
    """
    if bgr is None or getattr(bgr, "size", 0) == 0:
        raise ValueError("пустой кадр")
    h, w = bgr.shape[0], bgr.shape[1]
    zx, zy, zw, zh = camera_zone(zone)
    x0 = int(round(zx * w))
    y0 = int(round(zy * h))
    x1 = min(w, max(x0 + 1, int(round((zx + zw) * w))))
    y1 = min(h, max(y0 + 1, int(round((zy + zh) * h))))
    return np.ascontiguousarray(bgr[y0:y1, x0:x1])


def encode_jpeg(bgr: np.ndarray, quality: int = CAMERA_QUALITY) -> bytes:
    """Кадр BGR -> JPEG. Pillow ждёт RGB, поэтому разворачиваем последнюю ось."""
    from PIL import Image                          # тяжёлый импорт — только когда пишем

    import io
    rgb = np.ascontiguousarray(bgr[:, :, ::-1])
    buf = io.BytesIO()
    Image.fromarray(rgb, mode="RGB").save(buf, format="JPEG", quality=int(quality))
    return buf.getvalue()


def camera_jpeg(bgr: np.ndarray, mode: str, zone=None,
                quality: int = CAMERA_QUALITY) -> bytes | None:
    """Единственное место, где кадр камеры превращается в запись.

    Возвращает None в режиме «выключено» — и это единственный правильный ответ:
    ни один кадр с камеры не уходит в файл мимо этой проверки.
    """
    mode = normalize_camera_mode(mode)
    if mode == CAMERA_OFF or bgr is None:
        return None
    if mode == CAMERA_BOX:
        bgr = crop_zone(bgr, zone)
    return encode_jpeg(bgr, quality)


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
