"""Проверка подключения датчика до запуска: воткнут ли он, не через хаб ли, на полной ли скорости.

Зачем это нужно. Kinect v2 — самый капризный узел всей песочницы. Он отдаёт
около 300 МБ/с и требует прямого порта USB 3.0: через хаб или в порт USB 2.0 он
либо не открывается вовсе, либо отдаёт пустые кадры. Разбираться в этом по
сообщениям библиотеки невозможно — они на английском и про внутренности USB.
Поэтому при старте мы сами смотрим, что говорит система, и пишем по-русски:
что не так и что сделать руками.

Что и где смотрим:
  macOS  — ioreg -a -p IOUSB -l: дерево устройств USB в формате plist. У каждого
           устройства есть «Device Speed» (3 и выше = USB 3.0) и родитель: если
           родитель — тоже устройство, значит датчик висит на хабе.
  Linux  — /sys/bus/usb/devices: у каждого устройства файлы idVendor, idProduct
           и speed (в Мбит/с: 480 = USB 2.0, 5000 = USB 3.0). Родительская папка
           с именем usbN — это корневой порт, любая другая — внешний хаб.
  Windows— PowerShell Get-PnpDevice: находим датчик по коду VID/PID и смотрим
           свойство «родитель». ROOT_HUB30 в родителе означает контроллер USB 3.0.
           Скорость на Windows штатными средствами не видна — об этом честно
           пишем и даём совет, что проверить в Диспетчере устройств.

Модуль намеренно ничего не импортирует из остального пакета и ничего не ломает:
если проверку выполнить не удалось, программа просто запускается дальше.
"""
from __future__ import annotations

import os
import plistlib
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Коды устройств на шине USB: производитель (vendor) и модель (product).
MICROSOFT = 0x045E
KINECT2_PRODUCTS = {0x02C4: "Kinect v2 (Xbox NUI Sensor)",
                    0x02D8: "Kinect v2 (адаптер)",
                    0x02D9: "Kinect v2 для Windows"}
KINECT1_PRODUCTS = {0x02AE: "Kinect v1 (камера)",
                    0x02AD: "Kinect v1 (звук)",
                    0x02B0: "Kinect v1 (мотор)",
                    0x02BF: "Kinect v1 (камера)",
                    0x02C2: "Kinect v1 (камера)"}

# Скорость шины. Датчику v2 нужно 5000 Мбит/с и выше, иначе кадры не пролезут.
NEEDED_MBIT = 5000
MACOS_SPEED_MBIT = {0: 2, 1: 12, 2: 480, 3: 5000, 4: 10000}


def speed_title(mbit: int | None) -> str:
    """Скорость шины человеческими словами."""
    if not mbit:
        return "неизвестно"
    if mbit >= 10000:
        return "USB 3.1 (10 Гбит/с)"
    if mbit >= 5000:
        return "USB 3.0 (5 Гбит/с)"
    if mbit >= 480:
        return "USB 2.0 (480 Мбит/с)"
    return f"{mbit} Мбит/с"


@dataclass
class UsbFinding:
    """Что удалось выяснить про датчик на шине USB."""
    checked: bool = False              # проверка вообще прошла
    found: bool = False                # датчик найден
    title: str = ""                    # какой именно датчик
    kind: str = ""                     # kinect2 | kinect1
    speed_mbit: int | None = None      # скорость шины, Мбит/с; None — не удалось узнать
    via_hub: bool | None = None        # True — через хаб, False — прямой порт, None — неизвестно
    hub_title: str = ""                # название хаба, если он есть
    note: str = ""                     # почему проверка не удалась
    extra: list[str] = field(default_factory=list)   # что ещё стоит сказать

    @property
    def speed_ok(self) -> bool | None:
        if self.speed_mbit is None:
            return None
        return self.speed_mbit >= NEEDED_MBIT

    @property
    def ok(self) -> bool:
        """Всё хорошо: датчик есть, прямой порт, полная скорость."""
        return bool(self.found) and self.via_hub is not True and self.speed_ok is not False


# ---------------------------------------------------------------- готовые слова для человека

def report_lines(f: UsbFinding) -> list[str]:
    """Что напечатать в окне запуска. Простыми словами, с советом что делать."""
    if not f.checked:
        return [f"Подключение датчика проверить не удалось ({f.note or 'нет данных'})."]

    out: list[str] = []
    if not f.found:
        out += [
            "Датчик не найден на шине USB.",
            "Что сделать:",
            "  1. воткните кабель датчика в компьютер (у Kinect v2 ещё и блок питания в розетку);",
            "  2. на адаптере датчика должен гореть белый огонёк;",
            "  3. порт — прямой на корпусе компьютера, синий или с значком SS (USB 3.0), не хаб;",
            "  4. выньте и воткните кабель заново, подождите 5 секунд.",
            "Пока датчика нет, программа работает на демо-рельефе: интерфейс и проекцию",
            "посмотреть можно, песок — нет.",
        ]
        return out + f.extra

    out.append(f"Датчик найден: {f.title}.")

    if f.via_hub is True:
        out += [
            f"  ВНИМАНИЕ: датчик подключён через разветвитель «{f.hub_title or 'хаб'}».",
            "  Так он работать не будет: кадры не пролезают через хаб.",
            "  Что сделать: воткните датчик прямо в порт на корпусе компьютера.",
        ]
    elif f.via_hub is False:
        out.append("  Порт прямой, без разветвителя — правильно.")

    if f.speed_ok is True:
        out.append(f"  Скорость порта: {speed_title(f.speed_mbit)} — полная, правильно.")
    elif f.speed_ok is False:
        out += [
            f"  ВНИМАНИЕ: порт работает на скорости {speed_title(f.speed_mbit)}.",
            "  Датчику нужен USB 3.0. Что сделать: переставьте кабель в порт",
            "  с синей серединкой или значком SS; если все порты такие — нужен",
            "  другой кабель или другой компьютер.",
        ]
    else:
        out.append("  Скорость порта определить не удалось — см. подсказку ниже.")

    return out + f.extra


# ---------------------------------------------------------------- macOS

def _macos_walk(nodes: list[dict], parent: dict | None = None):
    """Обход дерева USB: отдаёт пары (устройство, его родитель)."""
    for node in nodes:
        yield node, parent
        yield from _macos_walk(node.get("IORegistryEntryChildren", []) or [], node)


def parse_macos(data) -> UsbFinding:
    """Разбор дерева `ioreg -a -p IOUSB -l` (уже прочитанного plistlib)."""
    f = UsbFinding(checked=True)
    nodes = data if isinstance(data, list) else [data]
    for node, parent in _macos_walk(nodes):
        if not isinstance(node, dict) or node.get("idVendor") != MICROSOFT:
            continue
        pid = node.get("idProduct")
        title = KINECT2_PRODUCTS.get(pid) or KINECT1_PRODUCTS.get(pid)
        if not title:
            continue
        if f.found and f.kind == "kinect2":
            continue                                  # v2 уже нашли, остальное не интересно
        f.found = True
        f.kind = "kinect2" if pid in KINECT2_PRODUCTS else "kinect1"
        f.title = title
        f.speed_mbit = MACOS_SPEED_MBIT.get(node.get("Device Speed"))
        # Родитель-устройство (у него есть idVendor) — это хаб. Родитель-контроллер
        # (AppleT8142USBXHCI и подобные) означает прямой порт на корпусе.
        if parent is None:
            f.via_hub = None
        elif parent.get("idVendor") is not None:
            f.via_hub = True
            f.hub_title = str(parent.get("USB Product Name")
                              or parent.get("IORegistryEntryName") or "разветвитель")
        else:
            f.via_hub = False
    return f


def check_macos(timeout: float = 5.0) -> UsbFinding:
    try:
        raw = subprocess.run(["ioreg", "-a", "-p", "IOUSB", "-l", "-w", "0"],
                             capture_output=True, timeout=timeout).stdout
        if not raw.strip():
            return UsbFinding(note="ioreg ничего не вернул")
        return parse_macos(plistlib.loads(raw))
    except Exception as e:                            # noqa: BLE001 — проверка не должна ронять запуск
        return UsbFinding(note=f"ioreg: {e}")


# ---------------------------------------------------------------- Linux

def _read(path: Path) -> str:
    try:
        return path.read_text(errors="replace").strip()
    except OSError:
        return ""


def parse_linux(sysfs: Path) -> UsbFinding:
    """Разбор /sys/bus/usb/devices. Отдельная функция — чтобы её можно было проверить тестом."""
    f = UsbFinding(checked=True)
    if not sysfs.is_dir():
        return UsbFinding(note=f"нет папки {sysfs}")
    for dev in sorted(sysfs.iterdir()):
        vid, pid = _read(dev / "idVendor"), _read(dev / "idProduct")
        if not vid or not pid:
            continue
        try:
            vid_i, pid_i = int(vid, 16), int(pid, 16)
        except ValueError:
            continue
        if vid_i != MICROSOFT:
            continue
        title = KINECT2_PRODUCTS.get(pid_i) or KINECT1_PRODUCTS.get(pid_i)
        if not title:
            continue
        if f.found and f.kind == "kinect2":
            continue
        f.found = True
        f.kind = "kinect2" if pid_i in KINECT2_PRODUCTS else "kinect1"
        f.title = title
        speed = _read(dev / "speed")                  # «480», «5000», «10000»
        try:
            f.speed_mbit = int(float(speed))
        except ValueError:
            f.speed_mbit = None
        # Имя папки: «1-3» — порт корневого хаба (прямой порт), «1-3.2» — за хабом.
        parent = (dev.resolve().parent if dev.is_symlink() else dev.parent)
        parent_name = parent.name
        if parent_name.startswith("usb"):
            f.via_hub = False
        elif re.fullmatch(r"\d+-[\d.]+", parent_name):
            f.via_hub = True
            f.hub_title = _read(parent / "product") or parent_name
        else:
            f.via_hub = None
    return f


def check_linux() -> UsbFinding:
    try:
        return parse_linux(Path("/sys/bus/usb/devices"))
    except Exception as e:                            # noqa: BLE001
        return UsbFinding(note=f"/sys: {e}")


# ---------------------------------------------------------------- Windows

# Одна строка PowerShell: найти датчик по коду VID/PID и показать его родителя.
WINDOWS_PS = (
    "$ErrorActionPreference='SilentlyContinue';"
    "$d=Get-PnpDevice -PresentOnly | Where-Object {"
    " $_.InstanceId -match 'VID_045E&PID_(02C4|02D8|02D9|02AE|02B0|02AD|02BF|02C2)' }"
    " | Select-Object -First 1;"
    "if($d){"
    " $p=(Get-PnpDeviceProperty -InstanceId $d.InstanceId -KeyName 'DEVPKEY_Device_Parent').Data;"
    " 'FOUND|'+$d.InstanceId+'|'+$d.FriendlyName+'|'+$p }"
    "else{'NONE'}"
)


def parse_windows(text: str) -> UsbFinding:
    """Разбор ответа PowerShell: FOUND|InstanceId|Имя|Родитель либо NONE."""
    line = next((s.strip() for s in text.splitlines() if s.strip()), "")
    if not line:
        return UsbFinding(note="PowerShell ничего не ответил")
    if line.upper().startswith("NONE"):
        return UsbFinding(checked=True, found=False)
    if not line.upper().startswith("FOUND"):
        return UsbFinding(note=f"непонятный ответ PowerShell: {line[:80]}")

    parts = line.split("|")
    instance = parts[1] if len(parts) > 1 else ""
    friendly = parts[2] if len(parts) > 2 else ""
    parent = parts[3] if len(parts) > 3 else ""

    f = UsbFinding(checked=True, found=True)
    m = re.search(r"PID_([0-9A-Fa-f]{4})", instance)
    pid = int(m.group(1), 16) if m else 0
    f.kind = "kinect2" if pid in KINECT2_PRODUCTS else "kinect1"
    f.title = KINECT2_PRODUCTS.get(pid) or KINECT1_PRODUCTS.get(pid) or (friendly or "датчик Kinect")

    up = parent.upper()
    if "ROOT_HUB" in up:
        f.via_hub = False
        # ROOT_HUB30 — корневой хаб контроллера USB 3.0: сам порт почти наверняка быстрый.
        f.speed_mbit = NEEDED_MBIT if "ROOT_HUB30" in up else 480
    elif up.startswith("USB\\VID_"):
        f.via_hub = True
        f.hub_title = "разветвитель USB"
    else:
        f.via_hub = None

    f.extra = [
        "  Скорость порта на Windows программа померить не может.",
        "  Проверьте руками: Диспетчер устройств → «Контроллеры USB» → датчик должен",
        "  быть под контроллером с надписью USB 3.0 (xHCI), а в порт воткнут напрямую.",
    ]
    return f


def check_windows(timeout: float = 20.0) -> UsbFinding:
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", WINDOWS_PS],
                           capture_output=True, text=True, timeout=timeout)
        return parse_windows(r.stdout or r.stderr)
    except Exception as e:                            # noqa: BLE001
        return UsbFinding(note=f"PowerShell: {e}")


# ---------------------------------------------------------------- общая точка входа

def check_sensor_usb(platform: str | None = None) -> UsbFinding:
    """Посмотреть, как подключён датчик. Ничего не запускает и не трогает сам датчик."""
    system = platform or sys.platform
    if os.environ.get("SANDBOX_SKIP_USB_CHECK"):
        return UsbFinding(note="проверка выключена переменной SANDBOX_SKIP_USB_CHECK")
    if system == "darwin":
        return check_macos()
    if system.startswith("linux"):
        return check_linux()
    if system in ("win32", "cygwin"):
        return check_windows()
    return UsbFinding(note=f"система {system} не поддерживается проверкой")
