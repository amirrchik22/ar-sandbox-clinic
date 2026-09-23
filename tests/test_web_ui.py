"""Пульт специалиста в браузере: три экрана, режимы, пауза и заморозка.

Тесты читают сами файлы интерфейса (sandbox/console/web) и проверяют то, что
легко сломать незаметно: пропал экран, кнопка стала стучаться не по тому
адресу, в страницу приехала библиотека из интернета (а кабинет может быть без
сети). Браузер для этого не нужен.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1] / "sandbox" / "console" / "web"

INDEX = (WEB / "index.html").read_text(encoding="utf-8")
PULT = (WEB / "pult.js").read_text(encoding="utf-8")
STYLE = (WEB / "style.css").read_text(encoding="utf-8")
PROJECTOR = (WEB / "projector.html").read_text(encoding="utf-8")
LIVE = (WEB / "live.js").read_text(encoding="utf-8")

# Договор об API: других адресов пульт знать не должен.
CONTRACT = (
    "/api/state", "/api/people", "/api/session/start", "/api/session/stop",
    "/api/mode", "/api/pause", "/api/freeze", "/api/calibrate", "/api/snapshot",
    "/api/sessions", "/api/media", "/api/record", "/api/record/start", "/api/record/stop",
    "/api/settings", "/media/", "/openapi.json",
)


# ------------------------------------------------------------------ три экрана

def test_три_экрана_и_нижняя_панель():
    for screen in ("screen-now", "screen-kids", "screen-media"):
        assert f'id="{screen}"' in INDEX, f"пропал экран {screen}"
    for tab in ("tab-now", "tab-kids", "tab-media"):
        assert f'id="{tab}"' in INDEX, f"пропала кнопка экрана {tab}"
    assert 'role="tablist"' in INDEX
    assert INDEX.count('role="tabpanel"') == 3


def test_экран_сейчас_собран_из_нужных_частей():
    # выбор «кто ведёт» и «с кем занимаемся» одним касанием
    assert 'id="staffList"' in INDEX and 'id="childList"' in INDEX
    assert "Кто ведёт занятие" in INDEX and "С кем занимаемся" in INDEX
    # занятие: таймер, имена, предпросмотр, кнопки
    for part in ('id="timer"', 'id="who"', 'id="preview"', 'id="modes"',
                 'id="btnPause"', 'id="btnFreeze"', 'id="btnSnap"',
                 'id="btnCalib"', 'id="btnStop"'):
        assert part in INDEX, f"на экране «Сейчас» нет {part}"
    # мелкие настройки спрятаны в сворачиваемый блок
    assert "<details" in INDEX and "Мелкие настройки" in INDEX
    assert 'id="bright"' in INDEX and 'id="steps"' in INDEX


def test_экран_записи_показывает_отчёт():
    for part in ('id="sessionsList"', 'id="reportThree"', 'id="reportNote"',
                 'id="reportMedia"', 'id="btnSaveNote"', 'id="btnBackToList"'):
        assert part in INDEX, f"на экране «Записи» нет {part}"
    # три снимка отчёта: начало, середина, конец
    assert "'Начало', 'Середина', 'Конец'" in PULT
    # видео проигрывается тегом video
    assert "createElement('video')" in PULT


def test_экран_дети_умеет_добавить_и_убрать():
    assert 'id="kidsList"' in INDEX and 'id="btnAddKid"' in INDEX
    assert "/api/people/child" in PULT
    assert "Убрать из списка" in PULT


# ------------------------------------------------------------------ режимы

@pytest.mark.parametrize("mode,title", [
    ("map", "Карта"),
    ("calm", "Спокойный"),
    ("two", "Два цвета"),
    ("water", "Только вода"),
])
def test_четыре_режима_на_месте(mode, title):
    assert f"id: '{mode}'" in PULT, f"нет режима {mode}"
    assert f"title: '{title}'" in PULT, f"нет подписи «{title}»"


def test_пауза_и_заморозка_видны_издалека():
    # у кнопок есть явное состояние и для глаз, и для озвучки
    assert 'aria-pressed="false"' in INDEX
    assert "classList.toggle('on', paused)" in PULT
    assert "classList.toggle('on', frozen)" in PULT
    # включённые кнопки перекрашиваются целиком, а не только рамкой
    assert ".toggle.pause.on" in STYLE and ".toggle.freeze.on" in STYLE
    # в паузе предпросмотр гаснет, в заморозке — метка на картинке
    assert 'id="previewVeil"' in INDEX and 'id="previewChip"' in INDEX


# ------------------------------------------------------------------ договор об API

def test_пульт_ходит_только_по_договору():
    used = set(re.findall(r"'(/(?:api|media|openapi)[^']*)'", PULT))
    for path in used:
        assert any(path.startswith(ok) for ok in CONTRACT), f"адрес вне договора: {path}"


def test_есть_обращения_ко_всем_частям_договора():
    for path in ("/api/state", "/api/people", "/api/session/start", "/api/session/stop",
                 "/api/mode", "/api/pause", "/api/freeze", "/api/calibrate",
                 "/api/snapshot", "/api/sessions", "/api/media", "/api/record/start"):
        assert path in PULT, f"пульт не умеет {path}"


def test_ответ_сервера_никогда_не_роняет_страницу():
    # единственная точка общения с сервером ловит и разбор JSON, и обрыв связи
    assert "async function api(" in PULT
    assert "catch (e) {" in PULT
    # 404 на ещё не написанный обработчик — не ошибка, а «этой части пока нет»
    assert "answer.status === 404" in PULT
    assert "SOON" in PULT


# ------------------------------------------------------------------ страница проекции

def test_на_проекции_нет_ничего_кроме_картинки():
    body = PROJECTOR.split("<body>", 1)[1].split("<script", 1)[0]
    # в теле страницы — только картинка, чёрный экран паузы и кнопка «во весь экран»
    tags = re.findall(r"<(\w+)", body)
    assert sorted(tags) == ["button", "div", "div"], tags
    assert 'id="black"' in PROJECTOR
    assert "live.freeze()" in PROJECTOR and "live.thaw()" in PROJECTOR
    assert "/api/state" in PROJECTOR


def test_живая_картинка_умеет_замереть():
    for part in ("function freeze(", "function thaw(", "function stop(", "function start("):
        assert part in LIVE, f"в live.js нет {part}"
    # пустой src браузер понимает как адрес страницы и ругается в консоль
    assert ".src = ''" not in LIVE
    assert "removeAttribute('src')" in LIVE


# ------------------------------------------------------------------ кабинет без интернета

@pytest.mark.parametrize("name", ["index.html", "pult.js", "style.css",
                                  "projector.html", "live.js"])
def test_ничего_не_грузится_из_интернета(name):
    text = (WEB / name).read_text(encoding="utf-8")
    links = re.findall(r"https?://[^\s\"'()]+", text)
    # w3.org встречается только как имя пространства имён SVG, это не загрузка
    outside = [u for u in links if "www.w3.org" not in u]
    assert not outside, f"{name} тянет из сети: {outside}"
    assert "@import" not in text


def test_читается_на_телефоне_390():
    # ширина колонок задаётся долями, а не жёсткими пикселями
    assert "minmax(0, 1fr)" in STYLE
    assert "overflow-x: hidden" in STYLE
    # нижняя панель не наезжает на содержимое
    assert "--tabbar" in STYLE and "env(safe-area-inset-bottom)" in STYLE
    # крупные элементы: основные кнопки не ниже 60 точек
    assert "min-height: 60px" in STYLE


def test_отчёт_берёт_записи_у_библиотеки():
    """Кнопки «Скачать» и «Удалить» должны работать.

    У библиотеки записей и у отчёта занятия разные адреса одной и той же
    записи. Пока отчёт подставлял свои, кнопка «Удалить» всегда получала 404,
    а видео в списке не появлялось вовсе. Список берём у библиотеки.
    """
    место = PULT.index("async function showReport")
    кусок = PULT[место:PULT.index("function isPhoto")]
    assert "/api/media?session=" in кусок
    # запрос к библиотеке идёт раньше, чем запасной список из отчёта
    assert кусок.index("/api/media?session=") < кусок.index("asList(report.media)")
    assert "r.data.items" in кусок                 # media.py отдаёт список под именем items
    assert "s.exists !== false" in кусок           # стёртый снимок в отчёт не подставляем


def test_имена_детей_только_условные():
    assert "условное имя" in INDEX.lower()
    assert "Имена детей не записываем" in INDEX or "Имена детей не записываем" in PULT
    # в отчёте формулировка занятия без медицинских заявлений
    assert "развивающее и коррекционное" in PULT
