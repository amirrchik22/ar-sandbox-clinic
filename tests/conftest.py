"""Общая обвязка тестов: временные папки, синтетический датчик, живой сервер.

Тесты идут без железа: датчик — fake, данные (база, снимки) и профиль
калибровки пишутся во временную папку, настоящая data/ не трогается.
"""
from __future__ import annotations

import json
import os
import socket
import tempfile
import threading
import time
import urllib.error
import urllib.request

TMP = tempfile.mkdtemp(prefix="sandbox-tests-")
os.environ.setdefault("SANDBOX_SENSOR", "fake")
os.environ.setdefault("SANDBOX_DATA", os.path.join(TMP, "data"))
os.environ.setdefault("SANDBOX_PROFILE", os.path.join(TMP, "console.json"))
os.environ.setdefault("SANDBOX_FPS", "15")

import pytest  # noqa: E402 — только после переменных окружения: их читает конвейер


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Client:
    """Короткие запросы к серверу. Ошибка HTTP поднимается как исключение."""

    def __init__(self, base: str):
        self.base = base

    def call(self, method: str, path: str, body: dict | None = None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            raw = r.read()
        if raw[:1] in (b"{", b"["):
            return json.loads(raw)
        return raw

    def get(self, path: str):
        return self.call("GET", path)

    def post(self, path: str, body: dict | None = None):
        return self.call("POST", path, body)

    def patch(self, path: str, body: dict | None = None):
        return self.call("PATCH", path, body)

    def delete(self, path: str):
        return self.call("DELETE", path)

    def code(self, method: str, path: str, body: dict | None = None) -> int:
        """Код ответа для заведомо неверного запроса."""
        try:
            self.call(method, path, body)
        except urllib.error.HTTPError as e:
            return e.code
        return 200


@pytest.fixture(scope="module")
def client():
    """Поднять сервер пульта на свободном порту и вернуть к нему клиента."""
    import uvicorn

    from sandbox.console.app import app

    port = free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    api = Client(f"http://127.0.0.1:{port}")
    for _ in range(100):
        try:
            api.get("/health")
            break
        except Exception:
            time.sleep(0.1)
    for _ in range(100):                       # ждём первый кадр конвейера
        if api.code("GET", "/frame.jpg") == 200:
            break
        time.sleep(0.1)
    yield api
    server.should_exit = True
    thread.join(timeout=5)
