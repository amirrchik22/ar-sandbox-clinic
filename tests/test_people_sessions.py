"""Люди, занятия и отчёты: хранилище и ручки API.

Имён детей в тестах нет — только условные имена, как и в самой программе.
"""
from __future__ import annotations

import time
import uuid

import pytest

from sandbox.common.db import Database
from sandbox.console.people import People
from sandbox.console.sessions import Sessions, duration_text


@pytest.fixture()
def store(tmp_path):
    """Чистая база в памяти и своя папка под снимки — на каждый тест."""
    db = Database(":memory:")
    yield People(db), Sessions(db, tmp_path), tmp_path
    db.close()


def make_file(folder, session_id: str, name: str = "snap.jpg"):
    """Файл снимка там же, где его кладёт программа: data/sessions/<занятие>/."""
    path = folder / "sessions" / session_id / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xff\xd8" + b"0" * 100)
    return path


# ------------------------------------------------------------------ люди

def test_специалист_добавляется_один_раз(store):
    people, _, _ = store
    first = people.add_staff("  Анна  Петровна ")
    assert first["name"] == "Анна Петровна"            # лишние пробелы убраны
    again = people.add_staff("анна петровна")
    assert again["id"] == first["id"]                  # тот же человек, а не двойник
    assert len(people.staff()) == 1
    with pytest.raises(ValueError):
        people.add_staff("   ")


def test_карточка_ребёнка_хранит_настройки(store):
    people, _, _ = store
    child = people.add_child("Ёжик")
    assert (child["mode"], child["brightness"], child["minutes"]) == ("map", 100, 20)
    assert child["mode_title"] == "Карта"

    child = people.update_child(child["id"], mode="calm", brightness=60, minutes=15,
                                note="начинаем с воды, громкие звуки не любит")
    assert child["mode"] == "calm" and child["brightness"] == 60 and child["minutes"] == 15
    assert "воды" in child["note"]


def test_карточка_проверяет_что_ей_дают(store):
    people, _, _ = store
    child = people.add_child("К-017")
    with pytest.raises(ValueError):
        people.update_child(child["id"], mode="радуга")
    with pytest.raises(ValueError):
        people.update_child(child["id"], brightness=500)
    with pytest.raises(ValueError):
        people.update_child(child["id"], minutes=0)
    with pytest.raises(ValueError):
        people.add_child("к-017")                      # такое имя уже занято
    with pytest.raises(LookupError):
        people.get_child(999)


def test_ребёнка_убирают_из_списка_а_занятия_остаются(store):
    people, sessions, _ = store
    staff = people.add_staff("Анна")
    child = people.add_child("Ёжик")
    session = sessions.start(staff, child, "calm", 60, 15)

    people.hide_child(child["id"])
    assert people.children() == []                     # из списка пропал
    assert people.get_child(child["id"])["active"] is False
    report = sessions.report(session["id"])
    assert report["child"]["alias"] == "Ёжик"          # подпись под занятием осталась


# ------------------------------------------------------------------ занятие

def test_занятие_запоминает_подпись_и_режим(store):
    people, sessions, _ = store
    staff = people.add_staff("Анна")
    child = people.add_child("Ёжик")
    session = sessions.start(staff, child, "calm", 70, 15)
    assert session["running"] is True
    assert session["staff"]["name"] == "Анна"
    assert session["child"]["alias"] == "Ёжик"
    assert session["mode_title"] == "Спокойный"
    assert session["planned_minutes"] == 15
    assert sessions.running()["id"] == session["id"]

    time.sleep(1.05)
    report = sessions.stop()
    assert report["running"] is False
    assert report["seconds"] >= 1
    assert report["duration"] == duration_text(report["seconds"])
    assert sessions.running() is None


def test_занятие_без_подписи_тоже_записывается(store):
    _, sessions, _ = store
    session = sessions.start(None, None, "map", 100, None)
    assert session["staff"]["name"] == "" and session["child"]["alias"] == ""
    assert sessions.stop()["running"] is False


def test_отчёт_берёт_начало_середину_и_конец(store):
    people, sessions, folder = store
    child = people.add_child("Ёжик")
    session = sessions.start(None, child, "map", 100, 20)
    for n in range(5):
        sessions.add_media(session["id"], "photo",
                           make_file(folder, session["id"], f"snap-{n}.jpg"))
    report = sessions.stop()

    assert report["snapshots_count"] == 5
    assert [h["title"] for h in report["highlights"]] == ["начало", "середина", "конец"]
    files = [h["path"].split("/")[-1] for h in report["highlights"]]
    assert files == ["snap-0.jpg", "snap-2.jpg", "snap-4.jpg"]
    assert all(h["url"].startswith("/snapshots/") for h in report["highlights"])


def test_отчёт_берёт_сколько_есть(store):
    _, sessions, folder = store
    session = sessions.start(None, None, "map", 100, None)
    assert sessions.report(session["id"])["highlights"] == []
    sessions.add_media(session["id"], "photo", make_file(folder, session["id"], "one.jpg"))
    assert len(sessions.report(session["id"])["highlights"]) == 1
    sessions.add_media(session["id"], "photo", make_file(folder, session["id"], "two.jpg"))
    titles = [h["title"] for h in sessions.stop()["highlights"]]
    assert titles == ["начало", "конец"]


def test_заметка_специалиста_сохраняется(store):
    _, sessions, _ = store
    session = sessions.start(None, None, "map", 100, None)
    sessions.stop()
    saved = sessions.set_note(session["id"], "  долго копал реку, звал маму смотреть ")
    assert saved["note"] == "долго копал реку, звал маму смотреть"
    assert sessions.get(session["id"])["note"].startswith("долго")
    with pytest.raises(LookupError):
        sessions.set_note("S-нет-такого", "х")


def test_события_занятия_попадают_в_журнал(store):
    _, sessions, folder = store
    session = sessions.start(None, None, "map", 100, None)
    sessions.log(session["id"], "mode", "calm")
    sessions.log(session["id"], "pause", "on")
    sessions.add_media(session["id"], "photo", make_file(folder, session["id"]))
    kinds = [e["kind"] for e in sessions.stop()["events"]]
    assert kinds == ["start", "mode", "pause", "snapshot", "stop"]


def test_висящее_занятие_закрывается_после_перезапуска(store):
    _, sessions, _ = store
    session = sessions.start(None, None, "map", 100, None)
    assert sessions.close_stale() == [session["id"]]
    assert sessions.running() is None
    assert sessions.get(session["id"])["status"] == "finished"
    assert sessions.close_stale() == []


def test_история_идёт_от_свежих_к_старым(store):
    _, sessions, _ = store
    first = sessions.start(None, None, "map", 100, None)
    sessions.stop()
    second = sessions.start(None, None, "calm", 80, None)
    sessions.stop()
    assert [s["id"] for s in sessions.history()][:2] == [second["id"], first["id"]]


# ------------------------------------------------------------------ записи

def test_запись_удаляется_вместе_с_файлом(store):
    _, sessions, folder = store
    session = sessions.start(None, None, "map", 100, None)
    path = make_file(folder, session["id"], "snap.jpg")
    thumb = make_file(folder, session["id"], "snap-small.jpg")
    media = sessions.add_media(session["id"], "photo", path, thumb)
    assert media["exists"] is True and media["bytes"] > 0
    assert media["url"] == f"/snapshots/{session['id']}/snap.jpg"
    assert media["thumb"].endswith("snap-small.jpg")

    sessions.delete_media(media["id"])
    assert path.exists() is False and thumb.exists() is False
    assert sessions.media(session["id"]) == []
    with pytest.raises(LookupError):
        sessions.get_media(media["id"])


# ------------------------------------------------------------------ ручки API

def unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:6]}"


def test_сценарий_занятия_целиком(client):
    """Тот же путь, что проходит специалист: выбрал — начал — вёл — завершил."""
    staff = client.post("/api/people/staff", {"name": unique("Анна")})["staff"]
    child = client.post("/api/people/child", {"alias": unique("Ёжик")})["child"]
    child = client.patch(f"/api/people/child/{child['id']}",
                         {"mode": "calm", "brightness": 60, "minutes": 15,
                          "note": "любит воду"})["child"]

    started = client.post("/api/session/start",
                          {"staff_id": staff["id"], "child_id": child["id"]})
    session = started["session"]
    assert session["running"] is True
    assert session["staff"]["name"] == staff["name"]
    assert session["child"]["alias"] == child["alias"]
    # настройки карточки применились сами
    assert started["view"]["mode"] == "calm" and started["view"]["brightness"] == 60

    state = client.get("/api/state")
    assert state["session"]["id"] == session["id"]
    assert state["mode"] == "calm" and state["mode_title"] == "Спокойный"
    assert [m["id"] for m in state["modes"]] == ["map", "calm", "two", "water"]

    assert client.post("/api/mode", {"mode": "water"})["mode"] == "water"
    assert client.post("/api/pause", {"on": True})["paused"] is True
    assert client.post("/api/pause", {"on": False})["paused"] is False
    assert client.post("/api/freeze", {"on": True})["frozen"] is True
    assert client.post("/api/freeze", {"on": False})["frozen"] is False

    snapshot = client.post("/api/snapshot")["snapshot"]
    assert snapshot["url"].startswith("/snapshots/")
    assert client.get(snapshot["url"])[:2] == b"\xff\xd8"

    report = client.post("/api/session/stop")["report"]
    assert report["running"] is False
    assert report["snapshots_count"] == 1
    assert len(report["highlights"]) == 1
    assert report["mode"] == "calm"                     # режим, с которого начали
    assert [e["kind"] for e in report["events"]][:2] == ["start", "mode"]

    saved = client.patch(f"/api/sessions/{session['id']}", {"note": "копал реку"})["session"]
    assert saved["note"] == "копал реку"
    assert client.get(f"/api/sessions/{session['id']}")["session"]["note"] == "копал реку"
    assert session["id"] in [s["id"] for s in client.get("/api/sessions")["sessions"]]

    client.delete(f"/api/people/child/{child['id']}")
    assert child["id"] not in [c["id"] for c in client.get("/api/people")["children"]]


def test_карточка_принимает_оба_написания_настроек(client):
    """Пульт шлёт настройки вложенным объектом — сервер понимает и так, и плоско."""
    child = client.post("/api/people/child", {"alias": unique("Ёжик")})["child"]
    saved = client.patch(f"/api/people/child/{child['id']}",
                         {"settings": {"mode": "water", "brightness": 40,
                                       "duration_min": 25}, "note": "любит воду"})["child"]
    assert saved["mode"] == "water" and saved["brightness"] == 40
    assert saved["minutes"] == 25 and saved["duration_min"] == 25
    assert saved["settings"] == {"mode": "water", "brightness": 40,
                                 "duration_min": 25, "minutes": 25}
    assert saved["note"] == "любит воду"

    flat = client.patch(f"/api/people/child/{child['id']}", {"minutes": 10})["child"]
    assert flat["minutes"] == 10 and flat["settings"]["duration_min"] == 10
    client.delete(f"/api/people/child/{child['id']}")


def test_занятие_отдаёт_подпись_плоскими_полями(client):
    staff = client.post("/api/people/staff", {"name": unique("Анна")})["staff"]
    child = client.post("/api/people/child", {"alias": unique("Ёжик")})["child"]
    session = client.post("/api/session/start",
                          {"staff_id": staff["id"], "child_id": child["id"]})["session"]
    assert session["staff_name"] == staff["name"]
    assert session["child_alias"] == child["alias"]
    assert session["duration_min"] == child["minutes"]
    client.post("/api/session/stop")
    client.delete(f"/api/people/child/{child['id']}")


def test_новое_занятие_закрывает_прошлое(client):
    first = client.post("/api/session/start")["session"]
    second = client.post("/api/session/start")
    assert second["closed"]["id"] == first["id"]
    assert second["closed"]["running"] is False
    assert second["session"]["running"] is True
    client.post("/api/session/stop")


def test_ошибки_понятны_и_с_нужным_кодом(client):
    assert client.code("POST", "/api/people/child", {"alias": "  "}) == 400
    assert client.code("POST", "/api/mode", {"mode": "радуга"}) == 400
    assert client.code("PATCH", "/api/people/child/99999", {"note": "х"}) == 404
    assert client.code("GET", "/api/sessions/S-no-such") == 404
    assert client.code("POST", "/api/brightness", {"value": 999}) == 400


def test_снимки_подряд_не_затирают_друг_друга(client):
    """Два снимка в одну секунду — обычное дело: файлов должно стать два."""
    client.post("/api/session/start")
    first = client.post("/api/snapshot")["snapshot"]
    second = client.post("/api/snapshot")
    assert second["snapshot"]["url"] != first["url"]
    assert len(second["snapshots"]) == 2
    report = client.post("/api/session/stop")["report"]
    assert len({h["url"] for h in report["highlights"]}) == 2
    assert all(client.code("GET", h["url"]) == 200 for h in report["highlights"])


def test_снимки_занятия_открываются_по_прямой_ссылке(client):
    """Снимок и его миниатюра должны открываться всегда — отчёт держится на них."""
    client.post("/api/session/start")
    snapshot = client.post("/api/snapshot")["snapshot"]
    assert snapshot["url"].startswith("/snapshots/")
    assert client.get(snapshot["url"])[:2] == b"\xff\xd8"
    assert client.get(snapshot["thumb"])[:2] == b"\xff\xd8"

    report = client.post("/api/session/stop")["report"]
    assert client.get(report["highlights"][0]["url"])[:2] == b"\xff\xd8"
    assert client.code("GET", "/api/media") == 200          # список записей отвечает
