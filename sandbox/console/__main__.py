"""Запуск программы: python -m sandbox.console

Поднимает веб-сервер, печатает адрес, по которому пульт открывается с любого
устройства в той же сети, и (с ключом --open) сразу открывает пульт в браузере.

    python -m sandbox.console                # найти датчик сам, порт 8080
    python -m sandbox.console --sensor fake  # показ без датчика, демо-рельеф
    python -m sandbox.console --port 9000 --open

Перед запуском смотрит, как подключён датчик (воткнут ли, не через разветвитель
ли, на полной ли скорости порта), и пишет об этом по-русски — см.
sandbox/sensors/usb_check.py. Проверка ничего не запускает и запуску не мешает:
что бы она ни сказала, программа поднимается дальше.
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import webbrowser


def port_busy(host: str, port: int) -> bool:
    """Занят ли порт. Проверяем до запуска, чтобы сказать об этом по-русски."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind((host if host != "0.0.0.0" else "", port))
        return False
    except OSError:
        return True
    finally:
        s.close()


def print_usb_check() -> None:
    """Сказать по-русски, как подключён датчик: воткнут ли, не через хаб ли, на какой скорости.

    Печатаем до запуска сервера, чтобы заказчик увидел совет раньше, чем поток
    сообщений от датчика. Любая ошибка внутри проверки запуску не мешает —
    в худшем случае просто ничего не скажем.
    """
    try:
        from sandbox.sensors.usb_check import check_sensor_usb, report_lines   # noqa: PLC0415
        finding = check_sensor_usb()
        lines = report_lines(finding)
    except Exception as e:                            # noqa: BLE001
        print(f"Проверку подключения датчика выполнить не удалось: {e}", flush=True)
        return
    print("\n".join(["", "Подключение датчика:", *("  " + s for s in lines), ""]), flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="AR-песочница: пульт специалиста и проекция")
    ap.add_argument("--port", type=int, default=int(os.environ.get("SANDBOX_PORT", "8080")),
                    help="порт веб-сервера, по умолчанию 8080")
    ap.add_argument("--host", default="0.0.0.0",
                    help="на каком адресе слушать; 0.0.0.0 = видно со всех устройств сети")
    ap.add_argument("--sensor", default=os.environ.get("SANDBOX_SENSOR", ""),
                    help="kinect2 | kinect1 | fake (демо-рельеф); пусто — найти самому")
    ap.add_argument("--fps", type=int, default=int(os.environ.get("SANDBOX_FPS", "20")),
                    help="сколько кадров в секунду готовить для браузера")
    ap.add_argument("--open", action="store_true", help="открыть пульт в браузере")
    ap.add_argument("--no-usb-check", action="store_true",
                    help="не проверять подключение датчика при старте")
    args = ap.parse_args()

    os.environ["SANDBOX_PORT"] = str(args.port)
    os.environ["SANDBOX_FPS"] = str(args.fps)
    if args.sensor:
        os.environ["SANDBOX_SENSOR"] = args.sensor

    import uvicorn                                     # noqa: PLC0415

    from .app import SHUTDOWN, addresses, app          # импорт после настроек: их читает конвейер

    class Server(uvicorn.Server):
        """Тот же сервер, но по Ctrl+C сначала просит потоки кадров закончиться."""

        def handle_exit(self, sig, frame):               # noqa: D102 — переопределение
            SHUTDOWN.set()
            super().handle_exit(sig, frame)

    # Проверка подключения датчика. На демо-рельефе она не нужна: там датчика и нет.
    if not args.no_usb_check and args.sensor != "fake":
        print_usb_check()

    if port_busy(args.host, args.port):
        print("\n".join([
            "",
            f"Порт {args.port} уже занят — скорее всего программа уже запущена в другом окне.",
            "Что сделать:",
            "  - закройте то окно (Ctrl+C в нём) и запустите заново;",
            f"  - или запустите на другом порту:  python -m sandbox.console --port {args.port + 1}",
            "",
        ]), flush=True)
        sys.exit(1)

    where = addresses(args.port)
    line = "=" * 58
    print("\n".join([
        line,
        "  AR-песочница запущена",
        line,
        f"  Пульт специалиста (телефон, планшет):  {where['console']}",
        f"  Страница проекции (на проектор):       {where['projector']}",
        f"  На этом компьютере:                    http://localhost:{args.port}/",
        "",
        "  Телефон должен быть в той же сети Wi-Fi.",
        "  Остановить программу: Ctrl+C в этом окне.",
        line,
    ]), flush=True)

    if args.open:
        threading.Timer(1.5, webbrowser.open, (f"http://127.0.0.1:{args.port}/",)).start()

    # timeout_graceful_shutdown обязателен. Страница проекции и пульт держат
    # открытыми потоки кадров (/stream.mjpg, /preview.mjpg) — такое соединение
    # само не закрывается никогда. Без этого ограничения по Ctrl+C сервер
    # переставал слушать порт, но не выходил: висел вечно и держал датчик,
    # и запустить программу заново было уже нельзя. Проверено живьём 23.09.
    config = uvicorn.Config(app, host=args.host, port=args.port, log_level="warning",
                            timeout_graceful_shutdown=5)
    Server(config).run()
    print("AR-песочница остановлена.", flush=True)


if __name__ == "__main__":
    main()
