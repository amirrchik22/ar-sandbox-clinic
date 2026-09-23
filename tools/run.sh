#!/usr/bin/env bash
# AR-песочница: запуск на Linux (Ubuntu) и на Маке из терминала.
#
#   bash tools/run.sh                 # найти датчик сам, порт 8080
#   bash tools/run.sh --sensor fake   # показ без датчика
#
# При первом запуске создаёт окружение Python и ставит библиотеки (нужен
# интернет один раз). Если список библиотек потом изменится, окружение
# обновится само — сравниваем отпечаток requirements.txt с сохранённым.
# Дальше поднимает программу и открывает пульт в браузере. Остановить: Ctrl+C.

set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$ROOT/.venv"
REQ="$ROOT/requirements.txt"
STAMP="$VENV/requirements-stamp.txt"
cd "$ROOT" || exit 1

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
  echo "Нужен Python 3.11 или новее:  sudo apt install python3.12 python3.12-venv"
  exit 1
fi

if [ ! -x "$VENV/bin/python" ]; then
  echo "Первый запуск: готовлю окружение (нужен интернет, это разово)…"
  "$PY" -m venv "$VENV" || exit 1
fi

# Отпечаток списка библиотек: sha256sum есть на Linux, shasum — на Маке.
req_hash() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$REQ" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then shasum -a 256 "$REQ" | awk '{print $1}'
  fi
}
WANT="$(req_hash)"
HAVE="$(cat "$STAMP" 2>/dev/null)"
if [ -z "$WANT" ] || [ "$WANT" != "$HAVE" ]; then
  [ -n "$HAVE" ] && echo "Список библиотек изменился — обновляю окружение…" \
                 || echo "Ставлю библиотеки (нужен интернет, это разово)…"
  "$VENV/bin/python" -m pip install --upgrade pip >/dev/null
  "$VENV/bin/pip" install -r "$REQ" || exit 1
  printf '%s\n' "$WANT" > "$STAMP"
fi

if [ ! -x "$ROOT/build/kinect_grabber" ]; then
  echo "Программа захвата с датчика не собрана — будет демо-рельеф."
  echo "Собрать один раз:  bash tools/build_grabber.sh"
fi

"$VENV/bin/python" -m sandbox.console --open "$@"
CODE=$?
[ "$CODE" -ne 0 ] && echo "Программа остановилась с ошибкой, смотрите сообщение выше."
exit "$CODE"
