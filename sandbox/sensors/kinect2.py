"""Kinect v2 через libfreenect2 (Linux) — запасной вариант, заготовка.

Базовый датчик объекта — Kinect v1 (kinect1.py). Kinect v2 берём, если v1 не
даст нужного качества или отклика: кадр глубины 512×424, поле зрения 70°×60°,
USB 3.0 (контроллер Intel или Renesas); на высоте 1,30 м над песком видит
1,82×1,50 м. Консоль одна на оба датчика, меняется только высота подвеса.

Реализовать: открытие устройства, поток глубины 512×424 и цвета 1920×1080,
перезапуск при потере кадров. Требует пакет pylibfreenect2 и правило udev для
доступа к USB без root. На Windows — Kinect SDK 2.0.
"""
from __future__ import annotations

from .base import ColorFrame, DepthFrame, Intrinsics, SensorHealth

try:
    import pylibfreenect2  # noqa: F401
except ImportError as e:  # модуль считается недоступным, make_sensor его не покажет
    raise ImportError("pylibfreenect2 не установлен") from e


class Kinect2Sensor:
    name = "kinect2"

    def __init__(self) -> None:
        raise NotImplementedError("Kinect v2: реализовать на этапе 1 (см. docs/roadmap.md)")

    def start(self) -> None: ...
    def stop(self) -> None: ...
    def depth_frame(self) -> DepthFrame: ...
    def color_frame(self) -> ColorFrame | None: ...
    def intrinsics(self) -> Intrinsics: ...
    def health(self) -> SensorHealth: ...
