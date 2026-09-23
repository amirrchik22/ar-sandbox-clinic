#!/usr/bin/env bash
# Сборка программы захвата kinect_grabber одной командой.
#
#   bash tools/build_grabber.sh            # собрать всё, чего ещё нет
#   bash tools/build_grabber.sh --rebuild  # пересобрать с нуля
#
# Что делает:
#   1) кладёт исходники libfreenect2 в vendor/libfreenect2 (склонирует, если их нет);
#   2) собирает библиотеку в vendor/libfreenect2/build (в git не попадает);
#   3) собирает tools/kinect_grabber.cpp в build/kinect_grabber.
#
# Флаг -DCMAKE_POLICY_VERSION_MINIMUM=3.5 обязателен: свежий CMake иначе падает на
# "Compatibility with CMake < 3.5 has been removed".
# Драйвер OpenNI2 и CUDA нам не нужны — выключены, сборка быстрее.
#
# Про цветную камеру (ключ --color у kinect_grabber). Картинка идёт по USB сжатой
# в JPEG, и распаковывает её сама libfreenect2. Своего распаковщика мы не ставим:
# на Маке работает VideoToolbox (аппаратно, ничего доставлять не надо), на Linux —
# TurboJPEG из пакета libturbojpeg0-dev. Ниже, после сборки, скрипт честно пишет,
# нашёлся ли распаковщик: без него ключ --color включится, но цветных кадров не
# будет. Глубине распаковщик не нужен вовсе.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENDOR="$ROOT/vendor/libfreenect2"
LIBBUILD="$VENDOR/build"
OUT="$ROOT/build"
REPO="https://github.com/OpenKinect/libfreenect2.git"
JOBS="$( (sysctl -n hw.ncpu 2>/dev/null || nproc 2>/dev/null) || echo 4)"

REBUILD=0
[ "${1:-}" = "--rebuild" ] && REBUILD=1

if [ "$(uname -s)" = "Darwin" ]; then LIBFILE="$LIBBUILD/lib/libfreenect2.dylib"; else LIBFILE="$LIBBUILD/lib/libfreenect2.so"; fi

# 1. Исходники библиотеки
if [ ! -d "$VENDOR/.git" ]; then
  echo "== беру исходники libfreenect2 =="
  mkdir -p "$(dirname "$VENDOR")"
  git clone --depth 1 "$REPO" "$VENDOR"
fi

# 2. Библиотека
if [ "$REBUILD" = "1" ]; then rm -rf "$LIBBUILD"; fi
if [ ! -f "$LIBFILE" ]; then
  echo "== собираю libfreenect2 =="
  cmake -S "$VENDOR" -B "$LIBBUILD" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
    -DBUILD_OPENNI2_DRIVER=OFF \
    -DENABLE_CUDA=OFF \
    -DBUILD_EXAMPLES=OFF
  cmake --build "$LIBBUILD" -j "$JOBS"
fi
[ -f "$LIBFILE" ] || { echo "библиотека не собралась: $LIBFILE"; exit 1; }

# 3. Программа захвата
echo "== собираю kinect_grabber =="
mkdir -p "$OUT"
CXXFLAGS_EXTRA=""
for d in /opt/homebrew/include /usr/local/include; do [ -d "$d" ] && CXXFLAGS_EXTRA="$CXXFLAGS_EXTRA -I$d"; done
# shellcheck disable=SC2086
c++ -std=c++11 -O2 -Wall \
  -I"$VENDOR/include" -I"$LIBBUILD" $CXXFLAGS_EXTRA \
  "$ROOT/tools/kinect_grabber.cpp" \
  -o "$OUT/kinect_grabber" \
  -L"$LIBBUILD/lib" -lfreenect2 -Wl,-rpath,"$LIBBUILD/lib"

# 4. Что получилось с цветом: смотрим, что включила сборка библиотеки.
CFG="$LIBBUILD/libfreenect2/config.h"
if [ -f "$CFG" ]; then
  if grep -q "define LIBFREENECT2_WITH_VT_SUPPORT" "$CFG"; then
    echo "цветная камера: распаковка через VideoToolbox (аппаратно, macOS)"
  elif grep -q "define LIBFREENECT2_WITH_TURBOJPEG_SUPPORT" "$CFG"; then
    echo "цветная камера: распаковка через TurboJPEG"
  else
    echo "цветная камера: распаковщика JPEG нет — ключ --color не даст кадров."
    echo "  Ubuntu: sudo apt install libturbojpeg0-dev, затем bash tools/build_grabber.sh --rebuild"
  fi
fi

echo "готово: $OUT/kinect_grabber"
echo "проверка цвета: $OUT/kinect_grabber --color --frames 60 > /dev/null"
