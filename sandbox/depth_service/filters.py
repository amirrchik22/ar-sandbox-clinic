"""Фильтры кадра глубины: шум, сглаживание, отсечение рук.

Работает на numpy без OpenCV, чтобы тесты шли на любой машине.
"""
from __future__ import annotations

import numpy as np


def median3(a: np.ndarray) -> np.ndarray:
    """Медиана 3×3: убирает одиночные выбросы датчика."""
    p = np.pad(a.astype(np.float32), 1, mode="edge")
    h, w = a.shape
    stack = np.stack([p[i:i + h, j:j + w] for i in range(3) for j in range(3)])
    return np.median(stack, axis=0)


def dilate(mask: np.ndarray, px: int) -> np.ndarray:
    """Расширение маски на px пикселей (по 3×3 за шаг)."""
    m = mask.copy()
    for _ in range(px):
        p = np.pad(m, 1, mode="constant")
        h, w = m.shape
        acc = np.zeros_like(m)
        for i in range(3):
            for j in range(3):
                acc |= p[i:i + h, j:j + w]
        m = acc
    return m


class TemporalSmoother:
    """Экспоненциальное сглаживание по времени. alpha 0.2 ≈ 0,15 с при 30 к/с."""

    def __init__(self, alpha: float = 0.2):
        self.alpha = alpha
        self.state: np.ndarray | None = None

    def __call__(self, frame: np.ndarray) -> np.ndarray:
        f = frame.astype(np.float32)
        if self.state is None:
            self.state = f.copy()
        else:
            self.state += self.alpha * (f - self.state)
        return self.state


def height_from_depth(depth_mm: np.ndarray, base_plane_mm: np.ndarray,
                      box_mask: np.ndarray, h_max_mm: float = 300.0) -> np.ndarray:
    """Высота песка над дном: опорная плоскость минус расстояние. Вне ящика — 0."""
    h = base_plane_mm.astype(np.float32) - depth_mm.astype(np.float32)
    h = np.clip(h, 0.0, h_max_mm)
    return np.where(box_mask, h, 0.0).astype(np.float32)


class HandRejector:
    """Руки и всё, что не песок, не попадают в карту высот.

    Рука — пиксели, которые выше устойчивой поверхности больше чем на hand_mm
    или меняются быстрее max_rate_mm_s (песок так быстро не пересыпается).
    Под маской карта высот замораживается на последнем «песочном» значении,
    маска расширяется на dilate_px, чтобы край ладони не давал «гор».
    """

    def __init__(self, hand_mm: float = 30.0, max_rate_mm_s: float = 150.0, dilate_px: int = 6,
                 relax: float = 0.3):
        self.hand_mm = hand_mm
        self.max_rate = max_rate_mm_s
        self.dilate_px = dilate_px
        self.relax = relax                 # как быстро доверяем новому песку вне маски
        self.surface: np.ndarray | None = None
        self.prev: np.ndarray | None = None

    def __call__(self, height_mm: np.ndarray, dt: float) -> tuple[np.ndarray, np.ndarray]:
        h = height_mm.astype(np.float32)
        if self.surface is None or self.prev is None:
            self.surface = h.copy()
            self.prev = h.copy()
            return self.surface, np.zeros(h.shape, dtype=bool)
        above = (h - self.surface) > self.hand_mm
        fast = np.abs(h - self.prev) / max(dt, 1e-3) > self.max_rate
        mask = dilate(above | fast, self.dilate_px)
        # вне маски поверхность подтягивается к новому значению, под маской не меняется
        self.surface = np.where(mask, self.surface, self.surface + self.relax * (h - self.surface))
        self.prev = h
        return self.surface, mask
