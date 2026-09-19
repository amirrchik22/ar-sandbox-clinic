"""Процесс рекордера — заготовка.

Этап 1: подписка на кадры (разделяемая память + ZeroMQ), запись .hmz 10 к/с,
видео с камеры и проекции через ffmpeg сегментами, скриншоты по событиям,
контрольные суммы, запись в SQLite (sandbox/common/schema.sql).
"""
from __future__ import annotations


def main() -> None:
    raise NotImplementedError("рекордер: реализовать на этапе 1 (docs/roadmap.md)")


if __name__ == "__main__":
    main()
