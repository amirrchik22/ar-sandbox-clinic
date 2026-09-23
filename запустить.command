#!/bin/bash
# AR-песочница: запуск на Маке двойным щелчком.
#
# Что делает: при первом запуске создаёт окружение Python и ставит библиотеки
# (нужен интернет один раз), дальше просто поднимает программу и открывает
# пульт в браузере. Адрес для телефона печатается в этом же окне.
#
# Если список библиотек (requirements.txt) поменялся, окружение обновляется
# само: сравниваем отпечаток файла с тем, что сохранён в .venv после прошлой
# установки. Иначе после обновления программы она падала бы с невнятной
# ошибкой «нет модуля такого-то», и заказчик не понял бы, что делать.
#
# Остановить: Ctrl+C в окне Терминала.

cd "$(dirname "$0")" || exit 1
ROOT="$(pwd)"
VENV="$ROOT/.venv"
REQ="$ROOT/requirements.txt"
STAMP="$VENV/requirements-stamp.txt"

echo "AR-песочница: подготовка…"

# 1. Python 3.11 или новее
PY=""
for candidate in python3.13 python3.12 python3.11 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
      PY="$(command -v "$candidate")"
      break
    fi
  fi
done
if [ -z "$PY" ]; then
  echo
  echo "Не нашёл Python 3.11 или новее."
  echo "Поставьте его командой:  brew install python@3.12"
  echo
  read -r -p "Нажмите Enter, чтобы закрыть окно…" _
  exit 1
fi

# 2. Окружение
if [ ! -x "$VENV/bin/python" ]; then
  echo "Первый запуск: готовлю окружение (нужен интернет, это разово)…"
  "$PY" -m venv "$VENV" || { echo "не удалось создать окружение"; read -r -p "Enter…" _; exit 1; }
fi

# 3. Библиотеки: ставим заново, только если список изменился
req_hash() { shasum -a 256 "$REQ" 2>/dev/null | awk '{print $1}'; }
WANT="$(req_hash)"
HAVE="$(cat "$STAMP" 2>/dev/null)"
if [ -z "$WANT" ] || [ "$WANT" != "$HAVE" ]; then
  if [ -n "$HAVE" ]; then
    echo "Список библиотек изменился — обновляю окружение…"
  else
    echo "Ставлю библиотеки (нужен интернет, это разово)…"
  fi
  "$VENV/bin/python" -m pip install --upgrade pip >/dev/null
  "$VENV/bin/pip" install -r "$REQ" || {
    echo "не удалось поставить библиотеки — проверьте интернет"; read -r -p "Enter…" _; exit 1; }
  printf '%s\n' "$WANT" > "$STAMP"
fi

# 4. Подсказка про программу захвата с датчика
if [ ! -x "$ROOT/build/kinect_grabber" ]; then
  echo
  echo "Программа захвата с датчика ещё не собрана — запущусь на демо-рельефе."
  echo "Чтобы работать с настоящим датчиком, соберите её один раз:"
  echo "    bash tools/build_grabber.sh"
  echo
fi

# 5. Запуск
# Не exec: если программа остановится с ошибкой (занят порт, не поставились
# библиотеки), окно Терминала должно остаться открытым — иначе заказчик увидит
# только мигнувшее и закрывшееся окно и не поймёт, что случилось.
"$VENV/bin/python" -m sandbox.console --open
CODE=$?
if [ "$CODE" -ne 0 ]; then
  echo
  echo "Программа остановилась с ошибкой. Что произошло — написано выше."
  read -r -p "Нажмите Enter, чтобы закрыть окно…" _
fi
exit "$CODE"
