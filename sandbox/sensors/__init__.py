"""Датчики глубины. Все реализации отдают один интерфейс DepthSensor."""
from .base import ColorFrame, DepthFrame, DepthSensor, Intrinsics, SensorHealth
from .fake import FakeSensor

SENSORS = {"fake": FakeSensor}

try:  # необязательные драйверы
    from .kinect2 import Kinect2Sensor
    SENSORS["kinect2"] = Kinect2Sensor
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
