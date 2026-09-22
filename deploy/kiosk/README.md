# Киоск на Ubuntu 24.04 LTS

1. Установить Ubuntu с полнодисковым шифрованием (LUKS), пользователь `sandbox`
   с автовходом, без пароля на разблокировку экрана.
2. Отключить спящий режим, заставку, автообновления:
   `systemctl mask sleep.target suspend.target`, `apt-mark hold` на ядро и
   драйверы, unattended-upgrades выключить.
3. Правило udev для датчика без root: `deploy/kiosk/99-sandbox-sensors.rules`.
4. Базовый датчик Kinect v2 — в порт USB 3.0 материнской платы напрямую, без
   хаба; контроллер USB 3.0 только Intel или Renesas (на AMD и дешёвых хабах
   датчик часто не заводится). Драйвер — libfreenect2, обёртка `pylibfreenect2`.
   Отдельный блок питания датчика (Kinect Adapter) — в тот же ИБП, что и ПК.
5. Скопировать репозиторий в `/opt/ar-sandbox-clinic`, создать `.venv`,
   `pip install -e ".[render,console,kinect2]"`.
6. Скопировать `deploy/systemd/*.service` в `/etc/systemd/system/`,
   `systemctl enable --now sandbox-*.service`.
7. Второй дисплей (проектор) — режим «расширить», родное разрешение, без
   масштабирования.
8. Ночное копирование `data/` на сетевой накопитель клиники — `rsync` по cron.
