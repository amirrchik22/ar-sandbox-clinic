"""Загрузка настроек: config/default.yaml + пресет режима."""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"


def load(name: str = "default") -> dict:
    return yaml.safe_load((CONFIG_DIR / f"{name}.yaml").read_text(encoding="utf-8"))


def load_preset(name: str) -> dict:
    return yaml.safe_load((CONFIG_DIR / "presets" / f"{name}.yaml").read_text(encoding="utf-8"))
