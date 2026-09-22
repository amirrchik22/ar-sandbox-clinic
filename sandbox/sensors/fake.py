"""Синтетический датчик: песок с горкой и ямой, над ним движется «рука».

Нужен для разработки и автотестов без железа. Может проигрывать записанные
кадры из .npz (массив frames: N×H×W uint16).

Умолчания повторяют базовый датчик Kinect v2: кадр 512×424, шум по высоте
≈ 2 мм, расстояние до дна 1400 мм (датчик 1,30 м над песком).
"""
from __future__ import annotations

import time

import numpy as np

from .base import ColorFrame, DepthFrame, Intrinsics, SensorHealth


class FakeSensor:
    name = "fake"

    def __init__(self, width: int = 512, height: int = 424, base_mm: float = 1400.0,
                 hand: bool = True, replay: str | None = None, noise_mm: float = 2.0,
                 seed: int = 1):
        self.w, self.h, self.base, self.hand, self.noise = width, height, base_mm, hand, noise_mm
        self._rng = np.random.default_rng(seed)
        xs = np.linspace(-0.5, 0.5, width, dtype=np.float32)
        ys = np.linspace(-0.375, 0.375, height, dtype=np.float32)
        self._xx, self._yy = np.meshgrid(xs, ys)
        self._replay = np.load(replay)["frames"] if replay else None
        self._t0 = 0.0
        self._n = 0

    def start(self) -> None:
        self._t0 = time.monotonic()
        self._n = 0

    def stop(self) -> None:
        pass

    def terrain_mm(self) -> np.ndarray:
        """Высота песка над дном, мм: волны + горка + яма."""
        xx, yy = self._xx, self._yy
        h = (60 + 30 * np.sin(xx * 7.5) * np.cos(yy * 9)
             + 45 * np.exp(-((xx - 0.16) ** 2 + (yy + 0.08) ** 2) / 0.03)
             - 50 * np.exp(-((xx + 0.2) ** 2 + (yy - 0.06) ** 2) / 0.022))
        return np.clip(h, 20, 190).astype(np.float32)

    def hand_mask(self, t: float) -> np.ndarray:
        cx, cy = 0.3 * np.cos(t * 0.8), 0.2 * np.sin(t * 0.8)
        return ((self._xx - cx) ** 2 / 0.012 + (self._yy - cy) ** 2 / 0.006) < 1.0

    def depth_frame(self) -> DepthFrame:
        t = time.monotonic() - self._t0
        if self._replay is not None:
            frame = self._replay[self._n % len(self._replay)]
        else:
            depth = self.base - self.terrain_mm()
            if self.hand:
                depth = np.where(self.hand_mask(t), depth - 180.0, depth)   # ладонь на 18 см выше песка
            depth += self._rng.normal(0.0, self.noise, depth.shape)
            frame = np.clip(depth, 0, 65535).astype(np.uint16)
        self._n += 1
        return DepthFrame(t, frame)

    def color_frame(self) -> ColorFrame | None:
        return None

    def intrinsics(self) -> Intrinsics:
        return Intrinsics(fx=365.0, fy=365.0, cx=self.w / 2, cy=self.h / 2, width=self.w, height=self.h)

    def health(self) -> SensorHealth:
        dt = max(time.monotonic() - self._t0, 1e-3)
        return SensorHealth(fps=self._n / dt, frames_total=self._n)
