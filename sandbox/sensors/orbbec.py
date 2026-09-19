"""Orbbec Femto Bolt / Femto Mega через Orbbec SDK — заготовка для продукта."""
from __future__ import annotations

from .base import ColorFrame, DepthFrame, Intrinsics, SensorHealth

try:
    import pyorbbecsdk  # noqa: F401
except ImportError as e:
    raise ImportError("pyorbbecsdk не установлен") from e


class OrbbecSensor:
    name = "orbbec"

    def __init__(self) -> None:
        raise NotImplementedError("Orbbec: реализовать на этапе 3 (см. docs/roadmap.md)")

    def start(self) -> None: ...
    def stop(self) -> None: ...
    def depth_frame(self) -> DepthFrame: ...
    def color_frame(self) -> ColorFrame | None: ...
    def intrinsics(self) -> Intrinsics: ...
    def health(self) -> SensorHealth: ...
