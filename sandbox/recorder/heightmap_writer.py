"""Формат .hmz — «видео из чисел»: карта высот int16 (мм) с дельта-кодированием и zstd.

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

    def _compress(b: bytes) -> bytes: return _C.compress(b)
    def _decompress(b: bytes) -> bytes: return _D.decompress(b)
except ImportError:  # запасной вариант без zstandard
    import zlib

    def _compress(b: bytes) -> bytes: return zlib.compress(b, 3)
    def _decompress(b: bytes) -> bytes: return zlib.decompress(b)

MAGIC = "HMZ1"


class HeightmapWriter:
    def __init__(self, path: str | Path, width: int, height: int, fps: float, session_id: str):
        self.path = Path(path)
        self.f = self.path.open("wb")
        header = {"magic": MAGIC, "w": width, "h": height, "fps": fps, "session": session_id, "dtype": "int16"}
        self.f.write((json.dumps(header, ensure_ascii=False) + "\n").encode("utf-8"))
        self.prev: np.ndarray | None = None
        self.frames = 0

    def write(self, t: float, height_mm: np.ndarray) -> None:
        cur = np.rint(height_mm).astype(np.int16)
        delta = cur if self.prev is None else (cur - self.prev)
        payload = _compress(np.ascontiguousarray(delta).tobytes())
        self.f.write(struct.pack("<dI", t, len(payload)))
        self.f.write(payload)
        self.prev = cur
        self.frames += 1

    def close(self) -> None:
        self.f.close()


def read_heightmaps(path: str | Path) -> Iterator[tuple[float, np.ndarray]]:
    with Path(path).open("rb") as f:
        header = json.loads(f.readline().decode("utf-8"))
        assert header["magic"] == MAGIC, "не .hmz файл"
        w, h = header["w"], header["h"]
        prev: np.ndarray | None = None
        while True:
            head = f.read(12)
            if len(head) < 12:
                return
            t, n = struct.unpack("<dI", head)
            delta = np.frombuffer(_decompress(f.read(n)), dtype=np.int16).reshape(h, w)
            cur = delta if prev is None else (prev + delta).astype(np.int16)
            prev = cur
            yield t, cur
