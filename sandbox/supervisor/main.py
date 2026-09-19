"""Наблюдатель — заготовка.

Логика этапа 1:
- слушает пульс каждого процесса (ZeroMQ, раз в секунду);
- сервис датчика молчит 2 с → systemctl restart sandbox-depth;
- три перезапуска подряд без кадров → сброс питания датчика через управляемую
  USB-розетку и предупреждение на планшет;
- проектор по PJLink каждые 30 с: питание, температура, наработка;
- диск < 10 % → предупреждение, < 3 % → запрет старта сеанса;
- всё пишется в журнал с ротацией.
"""
from __future__ import annotations

SERVICES = ["sandbox-depth", "sandbox-render", "sandbox-recorder", "sandbox-console"]
HEARTBEAT_TIMEOUT_S = 2.0


def main() -> None:
    raise NotImplementedError("наблюдатель: реализовать на этапе 1 (docs/roadmap.md)")


if __name__ == "__main__":
    main()
