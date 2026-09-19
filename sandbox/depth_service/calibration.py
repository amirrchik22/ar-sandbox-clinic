"""Профиль калибровки: плоскость дна, углы ящика, привязка к проектору.

Один профиль на положение ящика (пол / стол). Хранится в config/profiles/*.json,
в git не попадает.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np


@dataclass
class CalibrationProfile:
    name: str                                   # "floor" | "table"
    plane: list[float]                          # a, b, c, d: a*x + b*y + c*z + d = 0 в мм, пиксели датчика
    box_corners_px: list[list[float]]           # 4 угла ящика в пикселях кадра глубины
    projector_homography: list[list[float]]     # 3×3: точка датчика (x, y) → пиксель проектора
    height_range_mm: list[float] = field(default_factory=lambda: [0.0, 200.0])
    contour_step_mm: float = 10.0
    created: str = ""

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "CalibrationProfile":
        return cls(**json.loads(Path(path).read_text(encoding="utf-8")))

    def base_plane_mm(self, width: int, height: int) -> np.ndarray:
        """Расстояние до дна для каждого пикселя: z = -(a*x + b*y + d) / c."""
        a, b, c, d = self.plane
        xs = np.arange(width, dtype=np.float32)
        ys = np.arange(height, dtype=np.float32)
        xx, yy = np.meshgrid(xs, ys)
        return -(a * xx + b * yy + d) / c

    def box_mask(self, width: int, height: int) -> np.ndarray:
        """Маска ящика по четырём углам (выпуклый четырёхугольник)."""
        pts = np.array(self.box_corners_px, dtype=np.float32)
        xs, ys = np.meshgrid(np.arange(width), np.arange(height))
        inside = np.ones((height, width), dtype=bool)
        for i in range(4):
            x1, y1 = pts[i]
            x2, y2 = pts[(i + 1) % 4]
            inside &= ((x2 - x1) * (ys - y1) - (y2 - y1) * (xs - x1)) >= 0
        return inside


def fit_plane(depth_mm: np.ndarray, mask: np.ndarray) -> list[float]:
    """Плоскость дна по ровному песку методом наименьших квадратов."""
    ys, xs = np.nonzero(mask & (depth_mm > 0))
    zs = depth_mm[ys, xs].astype(np.float64)
    A = np.column_stack([xs, ys, np.ones_like(xs)])
    coef, *_ = np.linalg.lstsq(A, zs, rcond=None)        # z = p*x + q*y + r
    p, q, r = coef
    return [float(p), float(q), -1.0, float(r)]           # p*x + q*y - z + r = 0
