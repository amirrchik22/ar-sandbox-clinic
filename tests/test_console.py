"""Пульт специалиста: QR-код, конвейер кадров, веб-адреса.

Тесты идут без железа: датчик — синтетический (fake), данные и профиль
калибровки пишутся во временную папку.
"""
import json
import os
import socket
import tempfile
import threading
import time
import urllib.error
import urllib.request

import pytest

TMP = tempfile.mkdtemp(prefix="sandbox-test-")
os.environ["SANDBOX_SENSOR"] = "fake"                       # без датчика
os.environ["SANDBOX_DATA"] = os.path.join(TMP, "data")      # снимки во временную папку
os.environ["SANDBOX_PROFILE"] = os.path.join(TMP, "console.json")
os.environ["SANDBOX_FPS"] = "15"

from sandbox.console import qr                                      # noqa: E402
from sandbox.console.pipeline import CONTOUR_VALUES, PALETTE_IDS, FrameServer  # noqa: E402


# ------------------------------------------------------------------ QR-код

def test_qr_размер_растёт_с_длиной():
    small = qr.matrix("http://192.168.1.42:8080/")
    big = qr.matrix("x" * 200)
    assert len(small) == len(small[0]) == 25         # версия 2
    assert len(big) > len(small)


def test_qr_поисковые_квадраты_на_месте():
    m = qr.matrix("http://10.0.0.5:8080/")
    n = len(m)
    for r0, c0 in ((0, 0), (0, n - 7), (n - 7, 0)):
        assert all(m[r0][c0 + i] == 1 for i in range(7))          # верхняя грань
        assert m[r0 + 1][c0 + 1] == 0                             # светлое кольцо
        assert m[r0 + 3][c0 + 3] == 1                             # тёмная середина
    assert all(v in (0, 1) for row in m for v in row)


def test_qr_слишком_длинная_строка():
    with pytest.raises(ValueError):
        qr.matrix("y" * 500)


def test_qr_svg_рисуется():
    svg = qr.svg("http://192.168.1.42:8080/")
    assert svg.startswith("<svg") and svg.endswith("</svg>")
    assert "<rect" in svg


# ------------------------------------------------------------------ конвейер кадров

@pytest.fixture(scope="module")
def frames(tmp_path_factory):
    server = FrameServer(width=320, height=240, fps=15, preview_width=160,
                         sensor_name="fake",
                         profile_path=tmp_path_factory.mktemp("profile") / "console.json")
    server.start()
    for _ in range(100):                              # ждём первый кадр, но не вечно
        if server.latest("full")[0] is not None:
            break
        time.sleep(0.1)
    yield server
    server.stop()


def test_кадр_это_jpeg(frames):
    full, seq = frames.latest("full")
    preview, _ = frames.latest("preview")
    assert full is not None and preview is not None
    assert full[:2] == b"\xff\xd8" and preview[:2] == b"\xff\xd8"   # признак JPEG
    assert len(preview) < len(full)
    assert seq > 0


def test_палитра_и_шаг_меняются(frames):
    frames.set_palette("contrast")
    frames.set_contour_step(20)
    state = frames.state()
    assert state["palette"] == "contrast"
    assert state["contour_step_mm"] == 20
    with pytest.raises(ValueError):
        frames.set_palette("нет такой")
    with pytest.raises(ValueError):
        frames.set_contour_step(7)


def test_калибровка_сохраняет_профиль(frames):
    frames.calibrate(frames=3)
    for _ in range(100):
        if frames.state()["calibrated"]:
            break
        time.sleep(0.1)
    assert frames.state()["calibrated"] is True
    assert frames._profile_path.exists()


def test_снимок_пишет_файлы(frames, tmp_path):
    path = frames.snapshot(tmp_path / "snap.jpg")
    assert path.exists() and path.stat().st_size > 1000
    assert path.with_name("snap-small.jpg").exists()


# ------------------------------------------------------------------ веб-сервер

def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module")
def server():
    import uvicorn

    from sandbox.console.app import app

    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    running = uvicorn.Server(config)
    thread = threading.Thread(target=running.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            urllib.request.urlopen(base + "/health", timeout=1).read()
            break
        except Exception:
            time.sleep(0.1)
    yield base
    running.should_exit = True
    thread.join(timeout=5)


def get(base: str, path: str) -> tuple[int, bytes]:
    with urllib.request.urlopen(base + path, timeout=5) as r:
        return r.status, r.read()


def post(base: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else b""
    req = urllib.request.Request(base + path, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.status, json.loads(r.read())


def test_страницы_открываются(server):
    for path in ("/", "/projector"):
        status, body = get(server, path)
        assert status == 200 and b"<html" in body.lower()
    assert get(server, "/static/style.css")[0] == 200
    assert get(server, "/qr.svg")[1].startswith(b"<svg")


def test_состояние_и_адреса(server):
    status, body = get(server, "/api/state")
    state = json.loads(body)
    assert status == 200
    assert [p["id"] for p in state["palettes"]] == PALETTE_IDS
    assert [c["value"] for c in state["contour_steps"]] == CONTOUR_VALUES
    assert state["addresses"]["console"].startswith("http://")
    assert ":" in state["addresses"]["console"].split("//")[1]


def test_занятие_идёт_и_заканчивается(server):
    started = post(server, "/api/session/start")[1]["session"]
    assert started["running"] is True
    time.sleep(1.1)
    state = json.loads(get(server, "/api/state")[1])
    assert state["session"]["seconds"] >= 1
    stopped = post(server, "/api/session/stop")[1]["session"]
    assert stopped["running"] is False


def test_снимок_попадает_в_список(server):
    post(server, "/api/session/start")
    for _ in range(100):                              # ждём первый кадр конвейера
        try:
            if get(server, "/frame.jpg")[0] == 200:
                break
        except urllib.error.HTTPError:
            time.sleep(0.1)
    body = post(server, "/api/snapshot")[1]
    assert body["snapshot"]["url"].startswith("/snapshots/")
    assert len(body["snapshots"]) == 1
    assert get(server, body["snapshot"]["url"])[0] == 200
    assert get(server, body["snapshot"]["thumb"])[0] == 200


def test_настройки_проверяют_значения(server):
    status, body = post(server, "/api/settings", {"palette": "classic", "contour_step_mm": 10})
    assert status == 200 and body["view"]["palette"] == "classic"
    with pytest.raises(urllib.error.HTTPError) as e:
        post(server, "/api/settings", {"palette": "нет такой"})
    assert e.value.code == 400


def test_яркость_через_мелкие_настройки_доходит(server):
    """Ползунок яркости на пульте шлёт её вместе с остальной подстройкой.

    Раньше сервер отвечал «хорошо», а поле молча выбрасывал: проекция не
    тускнела. Проверяем, что доходит и что неверное значение отбивается.
    """
    status, body = post(server, "/api/settings", {"brightness": 40})
    assert status == 200 and body["view"]["brightness"] == 40
    with pytest.raises(urllib.error.HTTPError) as e:
        post(server, "/api/settings", {"brightness": 500})
    assert e.value.code == 400
    assert post(server, "/api/settings", {"brightness": 100})[1]["view"]["brightness"] == 100
