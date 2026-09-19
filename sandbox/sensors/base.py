"""Общий интерфейс датчика глубины.

Любой датчик (Kinect v2, Orbbec, RealSense, синтетический) отдаёт кадр
расстояний в миллиметрах и, если есть, цветной кадр. Остальной код о марке
датчика не знает.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import numpy as np


@dataclass(frozen=True)
class Intrinsics:
    """Параметры объектива: фокус и центр в пикселях, размер кадра."""
    fx: float
    fy: float
    cx: float
    cy: float
    width: int
    height: int


@dataclass
class SensorHealth:
    fps: float = 0.0
    frames_total: int = 0
    errors: int = 0
    last_error: str = ""


@dataclass
class DepthFrame:
    t: float                 # монотонное время, секунды
    depth_mm: np.ndarray     # uint16, HxW; 0 = нет данных


@dataclass
class ColorFrame:
    t: float
    bgr: np.ndarray          # uint8, HxWx3


class DepthSensor(Protocol):
    name: str

    def start(self) -> None: ...
    def stop(self) -> None: ...
    def depth_frame(self) -> DepthFrame: ...
    def color_frame(self) -> ColorFrame | None: ...
    def intrinsics(self) -> Intrinsics: ...
    def health(self) -> SensorHealth: ...
