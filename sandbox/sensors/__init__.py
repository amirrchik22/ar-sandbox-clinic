"""Датчики глубины. Все реализации отдают один интерфейс DepthSensor.

Порядок: fake (разработка и тесты), kinect2 (базовый датчик объекта),
kinect1 (дешёвая альтернатива), orbbec (будущий аналог через тот же интерфейс).
"""
from .base import ColorFrame, DepthFrame, DepthSensor, Intrinsics, SensorHealth
from .fake import FakeSensor

SENSORS = {"fake": FakeSensor}

try:  # необязательные драйверы: есть модуль — датчик появляется в списке
    from .kinect2 import Kinect2Sensor        # базовый датчик объекта
    SENSORS["kinect2"] = Kinect2Sensor
except ImportError:
    pass
try:
    from .kinect1 import Kinect1Sensor        # дешёвая альтернатива
    SENSORS["kinect1"] = Kinect1Sensor
except ImportError:
    pass
try:
    from .orbbec import OrbbecSensor
    SENSORS["orbbec"] = OrbbecSensor
except ImportError:
    pass


def make_sensor(name: str, **kw) -> DepthSensor:
    if name not in SENSORS:
        raise ValueError(f"датчик '{name}' недоступен; есть: {', '.join(SENSORS)}")
    return SENSORS[name](**kw)


__all__ = ["ColorFrame", "DepthFrame", "DepthSensor", "Intrinsics", "SensorHealth",
           "FakeSensor", "make_sensor", "SENSORS"]
