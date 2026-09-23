"""Обработка кадра: опорная плоскость, шкала, горизонтали, раскраска."""
import numpy as np

from sandbox.render.heightmap import (HeightmapProcessor, HeightmapSettings, auto_range,
                                      center_roi, colorize, contour_mask, crop_to_box,
                                      fit_reference_plane, height_from_plane, nice_step,
                                      roi_bounds)
from sandbox.render.palettes import PALETTE_ORDER, PALETTE_TITLES
from sandbox.sensors.fake import FakeSensor


def tilted(h=96, w=128, a=0.05, b=-0.03, c=1400.0, noise=0.0, seed=0):
    yy, xx = np.mgrid[0:h, 0:w]
    z = a * xx + b * yy + c
    if noise:
        z = z + np.random.default_rng(seed).normal(0, noise, z.shape)
    return z.astype(np.float32)


def test_plane_fit_finds_tilt():
    plane = fit_reference_plane(tilted(noise=1.5), None)
    assert abs(plane.a - 0.05) < 0.002
    assert abs(plane.b + 0.03) < 0.002
    assert abs(plane.c - 1400.0) < 1.0
    assert plane.residual_mm < 3.0


def test_plane_fit_ignores_object_on_sand():
    depth = tilted(noise=0.5)
    depth[30:50, 40:70] -= 150.0          # ведро на песке не должно утянуть «ноль»
    plane = fit_reference_plane(depth, None)
    assert abs(plane.c - 1400.0) < 3.0


def test_height_is_zero_where_sensor_blind():
    depth = tilted()
    depth[10:20, 10:20] = 0.0             # дырка в данных
    plane = fit_reference_plane(depth, None)
    height, ok = height_from_plane(depth, plane)
    assert not ok[10:20, 10:20].any()
    assert np.all(height[10:20, 10:20] == 0.0)
    assert np.abs(height[ok]).max() < 1.0


def test_scale_and_step():
    assert nice_step(120.0, 12) == 10.0
    assert nice_step(24.0, 12) == 2.0
    h = np.linspace(-50, 50, 128 * 96).reshape(96, 128).astype(np.float32)
    lo, hi = auto_range(h, np.ones_like(h, dtype=bool))
    assert -50 <= lo < hi <= 50
    lo2, hi2 = auto_range(np.zeros((96, 128), np.float32), np.ones((96, 128), bool))
    assert hi2 - lo2 >= 25.0              # ровный песок не превращаем в радугу


def test_contours_follow_step():
    ramp = np.tile(np.linspace(0, 100, 128, dtype=np.float32), (96, 1))
    ok = np.ones(ramp.shape, dtype=bool)
    lines_10 = contour_mask(ramp, ok, 10.0).sum()
    lines_20 = contour_mask(ramp, ok, 20.0).sum()
    assert lines_10 > lines_20 > 0
    assert contour_mask(ramp, ok, 0.0).sum() == 0


def test_colorize_output():
    h = np.tile(np.linspace(-20, 20, 64, dtype=np.float32), (48, 1))
    ok = np.ones(h.shape, dtype=bool)
    ok[:, :5] = False
    rgb = colorize(h, ok, -20, 20, palette="classic", contour_step_mm=10.0)
    assert rgb.shape == (48, 64, 3) and rgb.dtype == np.uint8
    assert (rgb[:, :5] == np.array([18, 18, 22], np.uint8)).all()   # нет данных — тёмный фон
    assert not np.array_equal(rgb[0, 10], rgb[0, 60])               # высота меняет цвет


def test_three_palettes_for_console():
    for name in ("calm", "classic", "contrast"):
        assert name in PALETTE_ORDER and PALETTE_TITLES[name]


def test_processor_end_to_end_and_hand_is_not_relief():
    sensor = FakeSensor(width=128, height=96, hand=False, noise_mm=1.0)
    sensor.start()
    proc = HeightmapProcessor(HeightmapSettings(palette="classic"), roi=center_roi((96, 128)))
    proc.calibrate_from_frames([sensor.depth_frame().depth_mm.astype(np.float32) for _ in range(3)])
    for _ in range(3):
        res = proc.process(sensor.depth_frame().depth_mm.astype(np.float32), 1 / 30)
    assert res.hand_mask.sum() == 0
    relief_before = res.height_mm.copy()

    sensor.hand = True                                   # ладонь над песком
    res = proc.process(sensor.depth_frame().depth_mm.astype(np.float32), 1 / 30)
    assert res.stats["hand_pct"] > 0.5
    moved = np.abs(res.height_mm - relief_before)[res.hand_mask]
    assert moved.max() < 30.0                            # под рукой рельеф не «вырос»
    assert res.rgb.shape == (72, 96, 3)       # кадр обрезан по рабочей зоне, без тёмной рамки
    assert res.roi_box == (12, 84, 16, 112)
    assert res.contour_step_mm > 0


# ------------------------------------------------------------------ рабочая зона


def test_roi_bounds_and_crop():
    roi = center_roi((96, 128))
    assert roi_bounds(roi) == (12, 84, 16, 112)
    assert roi_bounds(None) is None
    assert roi_bounds(np.zeros((8, 8), bool)) is None

    img = np.arange(96 * 128 * 3, dtype=np.uint8).reshape(96, 128, 3)
    cut = crop_to_box(img, roi_bounds(roi))
    assert cut.shape == (72, 96, 3)
    assert np.array_equal(cut, img[12:84, 16:112])
    assert crop_to_box(img, None) is img


def test_projector_frame_has_no_dark_border():
    """Главный хвост проверки 23.09: на проектор уходил кадр целиком, с рамкой."""
    sensor = FakeSensor(width=128, height=96, hand=False, noise_mm=0.5)
    sensor.start()
    roi = center_roi((96, 128))
    proc = HeightmapProcessor(HeightmapSettings(palette="classic"), roi=roi)
    proc.calibrate_from_frames([sensor.depth_frame().depth_mm.astype(np.float32) for _ in range(3)])
    res = proc.process(sensor.depth_frame().depth_mm.astype(np.float32), 1 / 30)

    empty = np.array([18, 18, 22], np.uint8)                 # цвет «нет данных» из colorize
    border = np.concatenate([res.rgb[0], res.rgb[-1], res.rgb[:, 0], res.rgb[:, -1]])
    assert not np.all(border == empty, axis=1).any()          # по краю уже рельеф, а не рамка
    assert res.rgb.shape[0] / res.rgb.shape[1] == 72 / 96

    # выключатель на случай отладки: без обрезки возвращается весь кадр
    proc.settings.crop_to_roi = False
    assert proc.process(sensor.depth_frame().depth_mm.astype(np.float32), 1 / 30).rgb.shape \
        == (96, 128, 3)


def test_roi_setter_updates_bounds():
    proc = HeightmapProcessor(HeightmapSettings())
    assert proc.roi is None
    proc.roi = center_roi((96, 128))                          # так делает console/pipeline.py
    assert roi_bounds(proc.roi) == (12, 84, 16, 112)
    proc.roi = None
    assert proc.roi is None


# ------------------------------------------------------------------ присмотр за калибровкой


def sand_frames(n=1, base=1400.0, relief=40.0, shape=(96, 128)):
    h, w = shape
    yy, xx = np.mgrid[0:h, 0:w]
    hill = relief * np.exp(-(((xx - w / 2) / (w / 6)) ** 2 + ((yy - h / 2) / (h / 6)) ** 2))
    return [(base - hill).astype(np.float32) for _ in range(n)]


def test_calibration_ok_right_after_calibrating():
    proc = HeightmapProcessor(HeightmapSettings(stale_frames=3))
    proc.calibrate_from_frames(sand_frames(3))
    for _ in range(10):
        res = proc.process(sand_frames()[0], 1 / 30)
    assert res.needs_calibration is False
    assert res.stats["calibration_ok"] is True
    assert res.stats["calibration_hint"] is None
    assert abs(res.stats["offset_mm"]) < 10.0


def test_stale_plane_asks_for_calibration():
    """Плоскость с прошлого запуска, а ящик с тех пор подвинули на 20 см."""
    proc = HeightmapProcessor(HeightmapSettings(stale_frames=3))
    proc.calibrate_from_frames(sand_frames(3, base=1400.0))
    moved = sand_frames(1, base=1200.0)[0]                    # песок на 200 мм ближе к датчику
    for _ in range(3):
        res = proc.process(moved, 1 / 30)
    assert res.needs_calibration is True
    assert res.stats["calibration_ok"] is False
    assert "калибров" in res.stats["calibration_hint"].lower()

    proc.calibrate(moved)                                     # «Запомнить ровный песок»
    assert proc.needs_calibration is False
    for _ in range(3):
        res = proc.process(moved, 1 / 30)
    assert res.needs_calibration is False


def test_one_bad_frame_does_not_raise_alarm():
    """Ребёнок на секунду накрыл датчик — предупреждение включаться не должно."""
    proc = HeightmapProcessor(HeightmapSettings(stale_frames=8))
    proc.calibrate_from_frames(sand_frames(3))
    for _ in range(8):
        proc.process(sand_frames()[0], 1 / 30)
    for _ in range(2):
        res = proc.process(np.zeros((96, 128), np.float32), 1 / 30)   # кадр без данных
    assert res.needs_calibration is False


def test_absurd_spread_asks_for_calibration():
    """В кадр попал пол рядом с ящиком: перепад больше, чем помещается в ящике."""
    proc = HeightmapProcessor(HeightmapSettings(stale_frames=2))
    flat = sand_frames(3, relief=0.0)
    proc.calibrate_from_frames(flat)
    deep = flat[0].copy()
    deep[:, :40] += 900.0                                     # треть кадра — далёкий пол
    for _ in range(2):
        res = proc.process(deep, 1 / 30)
    assert res.needs_calibration is True
    assert res.stats["spread_mm"] > 320.0
