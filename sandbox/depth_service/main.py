"""Цикл сервиса датчика.

python -m sandbox.depth_service --sensor fake     # без железа
python -m sandbox.depth_service --sensor kinect2  # базовый датчик объекта

Читает кадры, чистит шум, отсекает руки, считает высоту над дном и публикует
карту высот. Публикация в разделяемую память — этап 1; сейчас выводит статистику.
"""
from __future__ import annotations

import argparse
import time

import numpy as np

from sandbox.sensors import make_sensor
from .calibration import CalibrationProfile, fit_plane
from .filters import HandRejector, TemporalSmoother, height_from_depth, median3


def bootstrap_profile(sensor) -> CalibrationProfile:
    """Временный профиль по первому кадру: весь кадр — ящик, дно — плоскость по кадру.
    В рабочей версии профиль приходит из мастера калибровки."""
    intr = sensor.intrinsics()
    frame = sensor.depth_frame().depth_mm
    mask = np.ones(frame.shape, dtype=bool)
    plane = fit_plane(frame, mask)
    w, h = intr.width, intr.height
    return CalibrationProfile(name="bootstrap", plane=plane,
                              box_corners_px=[[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]],
                              projector_homography=np.eye(3).tolist())


def run(sensor_name: str, seconds: float | None) -> None:
    sensor = make_sensor(sensor_name)
    sensor.start()
    profile = bootstrap_profile(sensor)
    intr = sensor.intrinsics()
    base = profile.base_plane_mm(intr.width, intr.height)
    box = profile.box_mask(intr.width, intr.height)
    smooth, hands = TemporalSmoother(0.25), HandRejector()
    t_start = t_prev = time.monotonic()
    t_report = t_prev
    n = 0
    try:
        while True:
            fr = sensor.depth_frame()
            now = time.monotonic()
            dt, t_prev = now - t_prev, now
            depth = smooth(median3(fr.depth_mm))
            height = height_from_depth(depth, base, box)
            surface, mask = hands(height, dt)
            # TODO этап 1: FramePublisher.publish(surface, mask, fr.t)
            n += 1
            if now - t_report >= 1.0:
                print(f"{n:4d} к/с  песок {surface.mean():6.1f} мм  рука {mask.mean() * 100:4.1f}% кадра")
                n, t_report = 0, now
            if seconds is not None and now - t_start > seconds:
                break
            time.sleep(max(0.0, 1 / 30 - (time.monotonic() - now)))
    except KeyboardInterrupt:
        pass
    finally:
        sensor.stop()


def main() -> None:
    ap = argparse.ArgumentParser(description="Сервис датчика глубины")
    ap.add_argument("--sensor", default="fake",
                    help="fake (без железа) | kinect2 (базовый) | kinect1 (альтернатива) | orbbec")
    ap.add_argument("--seconds", type=float, default=None, help="остановиться через N секунд")
    args = ap.parse_args()
    run(args.sensor, args.seconds)


if __name__ == "__main__":
    main()
