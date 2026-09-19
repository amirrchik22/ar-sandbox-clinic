"""Отсечение рук: ладонь над песком не попадает в карту высот."""
import numpy as np

from sandbox.depth_service.filters import HandRejector, height_from_depth, median3
from sandbox.sensors.fake import FakeSensor


def test_hand_is_rejected_from_surface():
    s = FakeSensor(width=128, height=96, hand=True, noise_mm=0.0)
    s.start()
    base = np.full((96, 128), s.base, dtype=np.float32)
    box = np.ones((96, 128), dtype=bool)
    hands = HandRejector(hand_mm=30, max_rate_mm_s=150, dilate_px=2)
    terrain = s.terrain_mm()
    # первые кадры без руки — поверхность запоминается
    s.hand = False
    for _ in range(3):
        h = height_from_depth(s.depth_frame().depth_mm, base, box)
        surface, mask = hands(h, 1 / 30)
    assert mask.sum() == 0
    # появилась ладонь: поверхность под ней не изменилась, маска её накрыла
    s.hand = True
    fr = s.depth_frame()
    h = height_from_depth(fr.depth_mm, base, box)
    surface, mask = hands(h, 1 / 30)
    hand_px = s.hand_mask(fr.t)
    assert mask[hand_px].mean() > 0.95
    assert np.abs(surface[hand_px] - terrain[hand_px]).max() < 3.0


def test_median3_removes_spike():
    a = np.full((9, 9), 100.0, dtype=np.float32)
    a[4, 4] = 5000.0
    assert median3(a)[4, 4] == 100.0
