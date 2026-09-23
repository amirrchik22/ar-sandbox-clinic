"""Формат .hmz — «видео из чисел»: карта высот int16 (мм) с дельта-кодированием и zstd.

Зачем: цветная картинка — это уже решение о палитре, а .hmz хранит сам рельеф
в миллиметрах. По такой записи можно потом перерисовать занятие любой палитрой,
посчитать объём насыпанной горки или показать родителю «было — стало».
Весит он копейки: соседние кадры почти одинаковы, разница жмётся в разы.

Файл: строка заголовка JSON + кадры. Каждый кадр: 8 байт время (float64),
4 байта длина, затем zstd(дельта int16 относительно предыдущего кадра).
Первый кадр — полный. Индекс времени восстанавливается чтением заголовков.
"""
from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Iterator

import numpy as np

try:
    import zstandard as zstd
    _C = zstd.ZstdCompressor(level=3)
    _D = zstd.ZstdDecompressor()
    CODEC = "zstd"

    def _compress(b: bytes) -> bytes: return _C.compress(b)
    def _decompress(b: bytes) -> bytes: return _D.decompress(b)
except ImportError:  # запасной вариант без zstandard
    import zlib
    CODEC = "zlib"

    def _compress(b: bytes) -> bytes: return zlib.compress(b, 3)
    def _decompress(b: bytes) -> bytes: return zlib.decompress(b)

MAGIC = "HMZ1"
HEAD = struct.Struct("<dI")           # время кадра + длина сжатого куска


class HeightmapWriter:
    """Пишет карты высот кадр за кадром. Годится и как менеджер контекста:

        with HeightmapWriter(path, w, h, fps=10, session_id="S-…") as w:
            w.write(t, height_mm)
    """

    def __init__(self, path: str | Path, width: int, height: int, fps: float, session_id: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.width = int(width)
        self.height = int(height)
        self.fps = float(fps)
        self.session_id = session_id
        self.f = self.path.open("wb")
        header = {"magic": MAGIC, "w": self.width, "h": self.height, "fps": self.fps,
                  "session": session_id, "dtype": "int16", "codec": CODEC}
        self.f.write((json.dumps(header, ensure_ascii=False) + "\n").encode("utf-8"))
        self.prev: np.ndarray | None = None
        self.frames = 0
        self.first_t: float | None = None
        self.last_t: float | None = None
        self.closed = False

    def write(self, t: float, height_mm: np.ndarray) -> None:
        if self.closed:
            raise ValueError("запись уже закрыта")
        arr = np.asarray(height_mm)
        if arr.shape != (self.height, self.width):
            raise ValueError(f"кадр {arr.shape}, а заявлено "
                             f"{(self.height, self.width)} — размер не меняется по ходу записи")
        cur = np.rint(np.nan_to_num(arr, nan=0.0)).astype(np.int16)
        delta = cur if self.prev is None else (cur - self.prev)
        payload = _compress(np.ascontiguousarray(delta).tobytes())
        self.f.write(HEAD.pack(t, len(payload)))
        self.f.write(payload)
        self.prev = cur
        self.frames += 1
        if self.first_t is None:
            self.first_t = t
        self.last_t = t

    @property
    def seconds(self) -> float:
        """Сколько длится запись по времени кадров."""
        if self.first_t is None or self.last_t is None:
            return 0.0
        return max(0.0, self.last_t - self.first_t)

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self.f.close()

    def __enter__(self) -> HeightmapWriter:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def read_header(path: str | Path) -> dict:
    """Заголовок записи, не читая кадры: размер, частота, занятие."""
    with Path(path).open("rb") as f:
        header = json.loads(f.readline().decode("utf-8"))
    if header.get("magic") != MAGIC:
        raise ValueError("не .hmz файл")
    return header


def read_heightmaps(path: str | Path) -> Iterator[tuple[float, np.ndarray]]:
    """Кадры записи по одному: (время, карта высот в мм)."""
    with Path(path).open("rb") as f:
        header = json.loads(f.readline().decode("utf-8"))
        if header.get("magic") != MAGIC:
            raise ValueError("не .hmz файл")
        w, h = header["w"], header["h"]
        prev: np.ndarray | None = None
        while True:
            head = f.read(HEAD.size)
            if len(head) < HEAD.size:
                return
            t, n = HEAD.unpack(head)
            chunk = f.read(n)
            if len(chunk) < n:                      # файл оборвался (выключили питание)
                return
            delta = np.frombuffer(_decompress(chunk), dtype=np.int16).reshape(h, w)
            cur = delta if prev is None else (prev + delta).astype(np.int16)
            prev = cur
            yield t, cur


def count_frames(path: str | Path) -> int:
    """Сколько кадров в записи — без распаковки картинок."""
    n = 0
    with Path(path).open("rb") as f:
        f.readline()
        while True:
            head = f.read(HEAD.size)
            if len(head) < HEAD.size:
                return n
            _, size = HEAD.unpack(head)
            if len(f.read(size)) < size:
                return n
            n += 1
