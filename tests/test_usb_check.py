"""Проверка подключения датчика: воткнут ли, не через разветвитель ли, на полной ли скорости.

Железо тестам не нужно: разбор ответов системы вынесен в чистые функции, а им
подкладываем то же, что печатают настоящие ioreg, /sys и PowerShell.
Образцы сняты живьём 23.09 на Маке с подключённым Kinect v2 (серийный 090373635147).
"""
import pytest

from sandbox.sensors.usb_check import (UsbFinding, check_sensor_usb, parse_linux, parse_macos,
                                       parse_windows, report_lines, speed_title)

# Так выглядит дерево ioreg -a -p IOUSB -l на этом Маке: контроллер, под ним
# датчик напрямую и отдельно разветвитель с переходником.
KINECT = {"IORegistryEntryName": "Xbox NUI Sensor", "USB Product Name": "Xbox NUI Sensor",
          "idVendor": 0x045E, "idProduct": 0x02C4, "Device Speed": 3,
          "USB Serial Number": "090373635147"}
HUB = {"IORegistryEntryName": "USB2.0 Hub", "USB Product Name": "USB2.0 Hub",
       "idVendor": 1507, "idProduct": 1544, "Device Speed": 2}


def controller(*children):
    return [{"IORegistryEntryName": "Root",
             "IORegistryEntryChildren": [
                 {"IORegistryEntryName": "AppleT8142USBXHCI",
                  "IORegistryEntryChildren": list(children)}]}]


# ------------------------------------------------------------------ macOS


def test_macos_direct_usb3_port_is_fine():
    f = parse_macos(controller(dict(KINECT)))
    assert f.checked and f.found
    assert f.kind == "kinect2"
    assert f.via_hub is False
    assert f.speed_mbit == 5000 and f.speed_ok is True
    assert f.ok is True


def test_macos_sensor_behind_hub_is_caught():
    hub = dict(HUB, IORegistryEntryChildren=[dict(KINECT)])
    f = parse_macos(controller(hub))
    assert f.found and f.via_hub is True
    assert "Hub" in f.hub_title
    assert f.ok is False
    text = " ".join(report_lines(f))
    assert "разветвитель" in text and "прямо в порт" in text


def test_macos_slow_port_is_caught():
    f = parse_macos(controller(dict(KINECT, **{"Device Speed": 2})))
    assert f.found and f.speed_ok is False
    assert f.ok is False
    assert "USB 3.0" in " ".join(report_lines(f))


def test_macos_no_sensor():
    f = parse_macos(controller(dict(HUB)))
    assert f.checked and not f.found
    text = " ".join(report_lines(f))
    assert "не найден" in text and "USB 3.0" in text


def test_macos_kinect1_is_recognised():
    f = parse_macos(controller(dict(KINECT, idProduct=0x02AE, **{"Device Speed": 2})))
    assert f.kind == "kinect1"
    assert "Kinect v1" in f.title


# ------------------------------------------------------------------ Linux


def sysfs(tmp_path, port="1-2", speed="5000", vid="045e", pid="02c4"):
    """Кусочек /sys, как он устроен на самом деле: ссылки из bus/usb/devices в devices/usbN."""
    real = tmp_path / "devices" / "usb1"
    chain = port.split(".")                           # 1-2.3 → папка 1-2, внутри 1-2.3
    node = real / chain[0]
    for i in range(1, len(chain)):
        node = node / ".".join(chain[:i + 1])
    node.mkdir(parents=True)
    (node / "idVendor").write_text(vid)
    (node / "idProduct").write_text(pid)
    (node / "speed").write_text(speed)
    if len(chain) > 1:                                # у хаба тоже есть имя
        (node.parent / "product").write_text("Generic USB Hub")
    bus = tmp_path / "bus" / "usb" / "devices"
    bus.mkdir(parents=True)
    (bus / port).symlink_to(node)
    return bus


def test_linux_direct_usb3_port(tmp_path):
    f = parse_linux(sysfs(tmp_path))
    assert f.found and f.kind == "kinect2"
    assert f.via_hub is False and f.speed_mbit == 5000
    assert f.ok is True


def test_linux_behind_hub(tmp_path):
    f = parse_linux(sysfs(tmp_path, port="1-2.3"))
    assert f.found and f.via_hub is True
    assert f.hub_title == "Generic USB Hub"
    assert f.ok is False


def test_linux_usb2_port(tmp_path):
    f = parse_linux(sysfs(tmp_path, speed="480"))
    assert f.speed_mbit == 480 and f.speed_ok is False


def test_linux_no_devices_dir(tmp_path):
    f = parse_linux(tmp_path / "нет-такой-папки")
    assert not f.checked and f.note
    assert "проверить не удалось" in " ".join(report_lines(f))


# ------------------------------------------------------------------ Windows


def test_windows_found_on_usb3_root_hub():
    out = ("FOUND|USB\\VID_045E&PID_02C4\\090373635147|Kinect Sensor|"
           "USB\\ROOT_HUB30\\4&1A2B3C4D&0")
    f = parse_windows(out)
    assert f.found and f.kind == "kinect2"
    assert f.via_hub is False and f.speed_ok is True
    text = " ".join(report_lines(f))
    assert "Диспетчер устройств" in text            # честно говорим, что скорость не мерили


def test_windows_behind_hub():
    out = "FOUND|USB\\VID_045E&PID_02C4\\090|Kinect|USB\\VID_05E3&PID_0608\\5&ABC"
    f = parse_windows(out)
    assert f.via_hub is True and f.ok is False


def test_windows_not_found():
    f = parse_windows("NONE")
    assert f.checked and not f.found


def test_windows_garbage_is_not_a_verdict():
    f = parse_windows("Get-PnpDevice : термин не распознан")
    assert not f.checked and f.note


# ------------------------------------------------------------------ общее


def test_speed_titles():
    assert speed_title(5000).startswith("USB 3.0")
    assert speed_title(480).startswith("USB 2.0")
    assert speed_title(None) == "неизвестно"


def test_check_is_switched_off_by_env(monkeypatch):
    monkeypatch.setenv("SANDBOX_SKIP_USB_CHECK", "1")
    assert check_sensor_usb().checked is False


def test_unknown_platform_says_so_and_does_not_crash(monkeypatch):
    monkeypatch.delenv("SANDBOX_SKIP_USB_CHECK", raising=False)
    f = check_sensor_usb(platform="freebsd14")
    assert not f.checked and "freebsd14" in f.note


def test_report_never_empty():
    for f in (UsbFinding(), UsbFinding(checked=True), UsbFinding(checked=True, found=True)):
        assert report_lines(f) and all(isinstance(s, str) for s in report_lines(f))


@pytest.mark.parametrize("system", ["darwin", "linux", "win32"])
def test_check_on_this_machine_never_raises(system):
    """Проверка не должна ронять запуск программы, что бы ни ответила система."""
    f = check_sensor_usb(platform=system)
    assert isinstance(f, UsbFinding)
    assert report_lines(f)
