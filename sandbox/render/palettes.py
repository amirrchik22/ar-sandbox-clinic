"""Палитры режимов и ограничитель скорости смены цвета (защита от мерцания)."""
from __future__ import annotations

import numpy as np

# точки палитры: (доля высоты 0..1, RGB 0..255)
PALETTES: dict[str, list[tuple[float, tuple[int, int, int]]]] = {
    "classic": [(0.0, (31, 78, 156)), (0.25, (43, 143, 201)), (0.45, (59, 178, 122)),
                (0.65, (217, 199, 74)), (0.82, (230, 134, 46)), (1.0, (200, 64, 45))],
    # спокойная карта: приглушённые оттенки, без красного
    "calm": [(0.0, (70, 100, 140)), (0.4, (100, 140, 150)), (0.7, (150, 165, 120)), (1.0, (190, 175, 130))],
    # контраст: три насыщенные зоны для слабого зрения
    "contrast": [(0.0, (0, 40, 200)), (0.33, (0, 40, 200)), (0.34, (0, 170, 60)),
                 (0.66, (0, 170, 60)), (0.67, (240, 200, 0)), (1.0, (240, 200, 0))],
    # один цвет: высота = яркость
    "mono": [(0.0, (30, 45, 70)), (1.0, (200, 215, 235))],
}


def lut(name: str, size: int = 256) -> np.ndarray:
    """Таблица цветов size×3 float32 0..1 для текстуры палитры."""
    pts = PALETTES[name]
    xs = np.array([p[0] for p in pts])
    cols = np.array([p[1] for p in pts], dtype=np.float32) / 255.0
    t = np.linspace(0.0, 1.0, size)
    return np.stack([np.interp(t, xs, cols[:, i]) for i in range(3)], axis=1).astype(np.float32)


class ColorRateLimiter:
    """Ни один цвет палитры не меняется быстрее max_delta_per_s (доли от 0..1).

    Применяется к таблице палитры при смене режима, чтобы переход между
    палитрами был плавным и никогда не выглядел вспышкой. Ограничение на
    скорость перекраски самого рельефа задаётся сглаживанием в сервисе датчика.
    """

    def __init__(self, max_delta_per_s: float = 0.5):
        self.max_delta = max_delta_per_s
        self.current: np.ndarray | None = None

    def step(self, target: np.ndarray, dt: float) -> np.ndarray:
        if self.current is None:
            self.current = target.copy()
            return self.current
        limit = self.max_delta * dt
        delta = np.clip(target - self.current, -limit, limit)
        self.current = self.current + delta
        return self.current
