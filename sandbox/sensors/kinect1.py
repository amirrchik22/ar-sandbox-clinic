"""Kinect v1 (Xbox 360) через libfreenect — дешёвая альтернатива. Заготовка.

Базовый датчик объекта — Kinect v2 (kinect2.py). Kinect v1 остаётся в коде как
более дешёвый вариант на случай, если v2 не достанут или бюджет не потянет.

Что за датчик: кадр глубины 640×480, поле зрения 57°×43°, рабочая дистанция
0,8–4 м, подключение USB 2.0 плюс отдельный блок питания 12 В. На высоте
1,20 м над песком видит площадку 1,30×0,95 м, около 2,0 мм на пиксель, шум по
высоте примерно 3 мм. Честное сравнение с v2: точка у v1 мельче (2,0 мм против
3,6), но высота шумнее (3 мм против 2), и ИК-сетка теряет крутые склоны и
борта, которые v2 меряет временем полёта света. Консоль одна на оба датчика,
меняется только высота подвеса: с v1 датчик опускают до 1,20 м над песком.

Драйвер: libfreenect (проект OpenKinect), модуль Python называется ``freenect``.
Глубину берём сразу в миллиметрах (формат DEPTH_MM), чтобы не пересчитывать
сырые 11-битные значения. Бюджет задержки датчика ≈ 50 мс (кадр 33 + внутренняя).

Как поставить на Linux (Ubuntu 24.04, киоск):
  1. sudo apt install libfreenect0.5 libfreenect-dev python3-freenect
     (или собрать из исходников https://github.com/OpenKinect/libfreenect и
     поставить обёртку в .venv: pip install ./wrappers/python).
  2. Правило udev deploy/kiosk/99-sandbox-sensors.rules — доступ без root.
  3. Запретить ядру занимать датчик как веб-камеру:
     echo "blacklist gspca_kinect" | sudo tee /etc/modprobe.d/kinect.conf
  4. Датчик — в порт USB 2.0 материнской платы напрямую, без хабов
     (иначе растёт задержка и теряются кадры).

Как поставить на Windows (допустимо на этапе 0):
  Вариант А — Kinect for Windows SDK 1.8 (родной драйвер Microsoft); этот
  модуль тогда не нужен, для проверки датчика и задержки достаточно Magic Sand.
  Вариант Б — libfreenect: утилитой Zadig подменить штатные драйверы Kinect
  (Camera, Motor, Audio) на libusbK, затем собрать обёртку wrappers/python.
  Вариант А проще, вариант Б даёт тот же код, что и на Linux.

Реализовать, только если придётся вернуться к v1: открытие устройства,
поток глубины 30 к/с в мм, цветной кадр 640×480 по желанию, перезапуск при
потере кадров, наклон мотора в 0°, подсчёт к/с и ошибок для health().
"""
from __future__ import annotations

from .base import ColorFrame, DepthFrame, Intrinsics, SensorHealth

try:
    import freenect  # noqa: F401
except ImportError as e:  # модуль считается недоступным, make_sensor его не покажет
    raise ImportError("freenect (libfreenect) не установлен") from e


class Kinect1Sensor:
    name = "kinect1"

    # Паспортные данные датчика; точные параметры объектива даст калибровка.
    WIDTH, HEIGHT = 640, 480
    FOV_DEG = (57.0, 43.0)
    RANGE_MM = (800, 4000)
    NOMINAL_FX = NOMINAL_FY = 585.0   # пикселей, оценка по полю 57°; типично 580–595

    def __init__(self) -> None:
        raise NotImplementedError("Kinect v1: реализовать на этапе 1 (см. docs/roadmap.md)")

    def start(self) -> None: ...
    def stop(self) -> None: ...
    def depth_frame(self) -> DepthFrame: ...
    def color_frame(self) -> ColorFrame | None: ...
    def intrinsics(self) -> Intrinsics: ...
    def health(self) -> SensorHealth: ...
