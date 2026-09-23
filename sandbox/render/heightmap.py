"""Кадр глубины → цветная карта высот для проектора.

Здесь собрана вся обработка одного кадра, проверенная живьём на датчике 23.09:

1. Опорная плоскость. Датчик висит не идеально ровно, поэтому «ноль» — не одно
   число, а наклонная плоскость z = a*x + b*y + c, подогнанная методом
   наименьших квадратов по ровному песку в рабочей зоне. Это и есть калибровка,
   кнопка «Запомнить ровный песок» на пульте.
2. Карта высот: расстояние до плоскости минус расстояние до песка.
3. Сглаживание по времени — чтобы картинка не дрожала от шума датчика (≈1,5 мм).
4. Отсечение рук: всё, что выше устойчивой поверхности, рельефом не считается,
   иначе ладонь над песком рисовалась бы «горой».
5. Раскраска палитрой с плавными переходами и горизонтали с автоматическим шагом.
6. Обрезка по рабочей зоне: на проектор уходит только прямоугольник ящика, а не
   весь кадр датчика. Без этого вокруг картинки шла тёмная рамка — примерно по
   12 % с каждой стороны (проверено живьём 23.09).
7. Присмотр за калибровкой: если «ноль» уехал — плоскость подтянулась с прошлого
   запуска, а датчик с тех пор подвинули или песок досыпали, — честно говорим
   «нужна калибровка», а не красим бессмыслицу.

Всё на numpy, без OpenCV и видеокарты: кадр 512×424 маленький, на процессоре
обрабатывается за единицы миллисекунд.
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass, field

import numpy as np

from sandbox.depth_service.filters import HandRejector, TemporalSmoother, median3

from .palettes import lut

# Расстояния вне этого окна датчик не меряет — такие точки считаем пустыми.
VALID_MM = (300.0, 4500.0)

# Удобные шаги горизонталей в миллиметрах: подбираем ближайший сверху.
NICE_STEPS_MM = (2.0, 5.0, 10.0, 20.0, 25.0, 50.0, 100.0, 200.0, 250.0, 500.0)


# ---------------------------------------------------------------- опорная плоскость

@dataclass(frozen=True)
class ReferencePlane:
    """Ровный песок: z = a*x + b*y + c, где x и y — пиксели кадра, z — мм до датчика."""
    a: float
    b: float
    c: float
    points: int = 0            # сколько точек участвовало в подгонке
    residual_mm: float = 0.0   # средний разброс вокруг плоскости, мм

    def depth(self, shape: tuple[int, int]) -> np.ndarray:
        """Расстояние до плоскости в каждой точке кадра, мм."""
        h, w = shape
        yy, xx = np.mgrid[0:h, 0:w]
        return (self.a * xx + self.b * yy + self.c).astype(np.float32)

    def tilt_mm(self, shape: tuple[int, int]) -> float:
        """Перекос плоскости по кадру, мм — насколько датчик висит криво."""
        h, w = shape
        d = self.depth((h, w))
        return float(d.max() - d.min())

    def to_profile_plane(self) -> list[float]:
        """Тот же результат в записи CalibrationProfile: a*x + b*y - z + c = 0."""
        return [self.a, self.b, -1.0, self.c]


def valid_mask(depth_mm: np.ndarray, valid_range: tuple[float, float] = VALID_MM) -> np.ndarray:
    lo, hi = valid_range
    d = np.asarray(depth_mm, dtype=np.float32)
    return np.isfinite(d) & (d > lo) & (d < hi)


def fit_reference_plane(depth_mm: np.ndarray, roi: np.ndarray | None = None,
                        valid_range: tuple[float, float] = VALID_MM,
                        step: int = 2, rounds: int = 2,
                        reject_mm: float = 12.0) -> ReferencePlane:
    """Подгонка опорной плоскости по ровному песку методом наименьших квадратов.

    step — берём каждую вторую точку, этого с запасом хватает и считается быстрее.
    rounds — повторяем подгонку, выбрасывая точки дальше reject_mm от плоскости:
    так случайный предмет на песке или край ящика не утягивают за собой «ноль».
    """
    d = np.asarray(depth_mm, dtype=np.float32)
    h, w = d.shape
    ok = valid_mask(d, valid_range)
    if roi is not None:
        ok = ok & roi
    sub = np.zeros_like(ok)
    sub[::step, ::step] = True
    ok = ok & sub

    ys, xs = np.nonzero(ok)
    if ys.size < 200:
        raise ValueError("мало точек для калибровки: датчик не видит песок")

    zs = d[ys, xs].astype(np.float64)
    coef = np.array([0.0, 0.0, float(np.median(zs))])
    residual = 0.0
    for _ in range(max(1, rounds)):
        A = np.column_stack([xs, ys, np.ones_like(xs, dtype=np.float64)])
        coef, *_ = np.linalg.lstsq(A, zs, rcond=None)
        resid = zs - A @ coef
        residual = float(np.sqrt(np.mean(resid ** 2)))
        keep = np.abs(resid) < max(reject_mm, 3.0 * residual)
        if keep.sum() < 200 or keep.all():
            break
        xs, ys, zs = xs[keep], ys[keep], zs[keep]

    return ReferencePlane(a=float(coef[0]), b=float(coef[1]), c=float(coef[2]),
                          points=int(zs.size), residual_mm=residual)


def height_from_plane(depth_mm: np.ndarray, plane: ReferencePlane,
                      valid_range: tuple[float, float] = VALID_MM) -> tuple[np.ndarray, np.ndarray]:
    """Высота песка над опорной плоскостью, мм (выше плоскости — плюс) и маска годных точек."""
    d = np.asarray(depth_mm, dtype=np.float32)
    ok = valid_mask(d, valid_range)
    height = plane.depth(d.shape) - d
    return np.where(ok, height, 0.0).astype(np.float32), ok


# ---------------------------------------------------------------- шкала и горизонтали

def auto_range(height_mm: np.ndarray, ok: np.ndarray, low_pct: float = 3.0, high_pct: float = 97.0,
               min_span_mm: float = 25.0) -> tuple[float, float]:
    """Границы шкалы по самому рельефу: отбрасываем по 3 % снизу и сверху."""
    vals = height_mm[ok]
    if vals.size < 50:
        return -min_span_mm / 2, min_span_mm / 2
    lo = float(np.percentile(vals, low_pct))
    hi = float(np.percentile(vals, high_pct))
    if hi - lo < min_span_mm:                       # ровный песок: не раздуваем шум в радугу
        mid = (hi + lo) / 2
        lo, hi = mid - min_span_mm / 2, mid + min_span_mm / 2
    return lo, hi


def nice_step(span_mm: float, target_lines: int = 12) -> float:
    """Удобный шаг горизонталей: около target_lines линий на весь размах высот."""
    raw = max(span_mm, 1.0) / max(target_lines, 1)
    for s in NICE_STEPS_MM:
        if s >= raw:
            return s
    return NICE_STEPS_MM[-1]


def contour_mask(height_mm: np.ndarray, ok: np.ndarray, step_mm: float) -> np.ndarray:
    """Горизонтали: точки, где соседняя точка уже в другой «ступеньке» высоты."""
    if step_mm <= 0:
        return np.zeros(height_mm.shape, dtype=bool)
    band = np.floor(height_mm / step_mm)
    line = np.zeros(height_mm.shape, dtype=bool)
    # сравниваем с соседом слева и сверху, обе точки должны быть годными
    line[:, 1:] |= (band[:, 1:] != band[:, :-1]) & ok[:, 1:] & ok[:, :-1]
    line[1:, :] |= (band[1:, :] != band[:-1, :]) & ok[1:, :] & ok[:-1, :]
    return line & ok


# ---------------------------------------------------------------- рабочая зона

def center_roi(shape: tuple[int, int], margin: float = 0.125) -> np.ndarray:
    """Временная рабочая зона — центральный прямоугольник кадра.

    Пока нет калибровки углов ящика, так отсекаются пол, стена и борта.
    Настоящую маску ящика даст CalibrationProfile.box_mask.
    """
    h, w = shape
    m = np.zeros((h, w), dtype=bool)
    mx, my = int(w * margin), int(h * margin)
    m[my:h - my, mx:w - mx] = True
    return m


def roi_bounds(roi: np.ndarray | None) -> tuple[int, int, int, int] | None:
    """Рамка рабочей зоны в пикселях: (y0, y1, x0, x1) под срез [y0:y1, x0:x1].

    None — если зоны нет или она пустая: тогда работаем со всем кадром.
    """
    if roi is None:
        return None
    m = np.asarray(roi, dtype=bool)
    if m.ndim != 2 or not m.any():
        return None
    rows = np.flatnonzero(m.any(axis=1))
    cols = np.flatnonzero(m.any(axis=0))
    return int(rows[0]), int(rows[-1]) + 1, int(cols[0]), int(cols[-1]) + 1


def crop_to_box(img: np.ndarray, box: tuple[int, int, int, int] | None) -> np.ndarray:
    """Обрезать картинку по рамке рабочей зоны.

    Зачем: рабочая зона — это только часть кадра датчика, в остальное попадают
    пол, стена и борта ящика. Раньше на проектор уходил весь кадр, и вокруг
    картинки оставалась тёмная рамка, а сам рельеф был мельче, чем мог быть.
    Теперь режем кадр по рамке, и проектор растягивает на песок ровно ящик.
    """
    if box is None:
        return img
    y0, y1, x0, x1 = box
    out = img[y0:y1, x0:x1]
    if not out.size:
        return img
    # Именно копия, а не вид на исходный массив: копию Pillow превращает в JPEG
    # напрямую, а вид всё равно пришлось бы копировать, да ещё и держал бы в
    # памяти весь кадр датчика, пока пульт показывает обрезанный.
    return np.ascontiguousarray(out)


# ---------------------------------------------------------------- раскраска

def colorize(height_mm: np.ndarray, ok: np.ndarray, lo: float, hi: float,
             palette: str = "classic", contour_step_mm: float = 0.0,
             roi: np.ndarray | None = None, dim_outside: float = 0.35,
             contour_darken: float = 0.5,
             empty_rgb: tuple[int, int, int] = (18, 18, 22)) -> np.ndarray:
    """Карта высот → картинка RGB uint8 (H×W×3) для проектора."""
    table = (lut(palette, 256) * 255.0).astype(np.float32)          # 256×3, 0..255
    span = max(hi - lo, 1e-3)
    t = np.clip((height_mm - lo) / span, 0.0, 1.0)
    idx = (t * 255.0).astype(np.uint8)
    rgb = table[idx]                                                 # H×W×3 float32

    # Дальше всё умножениями по маске, без выборок по индексам: так кадр красится
    # за пару миллисекунд и укладывается в бюджет 30 кадров в секунду.
    if contour_step_mm > 0:
        lines = contour_mask(height_mm, ok, contour_step_mm)
        rgb *= np.where(lines, np.float32(contour_darken), np.float32(1.0))[:, :, None]

    empty = np.array(empty_rgb, dtype=np.float32)
    if roi is not None:                                              # вне ящика — приглушаем
        k = np.where(roi, np.float32(1.0), np.float32(dim_outside))[:, :, None]
        rgb = rgb * k + empty * (1.0 - k)

    rgb = np.where(ok[:, :, None], rgb, empty)                       # нет данных — ровный тёмный фон
    return np.clip(rgb, 0, 255).astype(np.uint8)


# ---------------------------------------------------------------- шум датчика

class NoiseMeter:
    """Шум датчика по высоте: среднее отклонение точки за последние N кадров, мм.

    Нужен для проверки «датчик стоит хорошо»: на дистанции ~0,9 м норма 1–2 мм,
    заметно больше — мешает свет, вибрация или кабель.
    """

    def __init__(self, frames: int = 10):
        self.frames = frames
        self._ring: list[np.ndarray] = []

    def push(self, depth_mm: np.ndarray) -> None:
        self._ring.append(np.asarray(depth_mm, dtype=np.float32).copy())
        if len(self._ring) > self.frames:
            self._ring.pop(0)

    def ready(self) -> bool:
        return len(self._ring) >= max(2, self.frames)

    def value_mm(self, roi: np.ndarray | None = None,
                 valid_range: tuple[float, float] = VALID_MM) -> float:
        if len(self._ring) < 2:
            return 0.0
        stack = np.stack(self._ring)
        ok = np.all((stack > valid_range[0]) & (stack < valid_range[1]), axis=0)
        if roi is not None:
            ok = ok & roi
        if ok.sum() < 20:
            return 0.0
        return float(np.std(stack[:, ok], axis=0).mean())


# ---------------------------------------------------------------- весь кадр целиком

@dataclass
class HeightmapSettings:
    """Настройки обработки. Пульт меняет палитру, шаг горизонталей и сглаживание."""
    palette: str = "classic"
    contour_step_mm: float = 0.0        # 0 = шаг подбирается сам
    contour_lines: int = 12             # к скольким линиям стремиться при автошаге
    smoothing_alpha: float = 0.45       # вес нового кадра: меньше — плавнее, но медленнее
    hand_mm: float = 30.0               # выше поверхности на столько — уже не песок, а рука
    hand_max_rate_mm_s: float = 150.0
    hand_dilate_px: int = 4
    height_range_mm: tuple[float, float] | None = None   # None = шкала по самому рельефу
    min_span_mm: float = 25.0
    valid_range: tuple[float, float] = VALID_MM
    despeckle: bool = True              # медиана 3×3 против одиночных выбросов датчика
    crop_to_roi: bool = True            # на проектор отдавать только рабочую зону, без рамки
    # Присмотр за калибровкой. Пределы взяты из геометрии ящика заказчика:
    # слой песка 180 мм, борт 300 мм над дном — перепад больше 320 мм означает,
    # что в кадр лезет не песок, а пол или стена, то есть «ноль» неверный.
    max_offset_mm: float = 60.0         # насколько медиана песка может уехать от нуля
    max_spread_mm: float = 320.0        # предельный разумный перепад высот в ящике
    stale_frames: int = 15              # столько кадров подряд, прежде чем поднять тревогу


@dataclass
class HeightmapResult:
    rgb: np.ndarray                     # картинка для проектора, uint8; размер — по рабочей зоне
    height_mm: np.ndarray               # H×W float32 — рельеф без рук, весь кадр
    hand_mask: np.ndarray               # H×W bool — где сейчас руки
    ok: np.ndarray                      # H×W bool — где датчик видит
    lo_mm: float
    hi_mm: float
    contour_step_mm: float
    stats: dict = field(default_factory=dict)
    roi_box: tuple[int, int, int, int] | None = None   # по какой рамке обрезана rgb
    needs_calibration: bool = False                    # «ноль» уехал, нужна калибровка
    calibration_hint: str = ""                         # что сказать специалисту


class HeightmapProcessor:
    """Держит калибровку и сглаживание между кадрами; на выходе — готовая картинка.

        proc = HeightmapProcessor(HeightmapSettings())
        proc.calibrate(depth)            # «Запомнить ровный песок»
        res = proc.process(depth, dt)    # на каждый кадр
    """

    def __init__(self, settings: HeightmapSettings | None = None,
                 roi: np.ndarray | None = None):
        self.settings = settings or HeightmapSettings()
        self._roi: np.ndarray | None = None
        self._roi_box: tuple[int, int, int, int] | None = None
        self.roi = roi                                 # через свойство: сразу считается рамка
        self.plane: ReferencePlane | None = None
        self._smooth = TemporalSmoother(self.settings.smoothing_alpha)
        self._hands = HandRejector(hand_mm=self.settings.hand_mm,
                                   max_rate_mm_s=self.settings.hand_max_rate_mm_s,
                                   dilate_px=self.settings.hand_dilate_px)
        self._noise = NoiseMeter()
        self.needs_calibration = False
        self.calibration_hint = ""
        self._calib_reason = ""
        self._bad_frames = 0
        self._good_frames = 0

    # --- рабочая зона ---

    @property
    def roi(self) -> np.ndarray | None:
        """Маска рабочей зоны (ящика) в кадре датчика; None — весь кадр."""
        return self._roi

    @roi.setter
    def roi(self, mask: np.ndarray | None) -> None:
        # Рамку считаем один раз при назначении зоны, а не на каждом кадре:
        # зону ставят один раз при запуске (см. console/pipeline.py).
        self._roi = None if mask is None else np.asarray(mask, dtype=bool)
        self._roi_box = roi_bounds(self._roi)

    # --- калибровка ---

    def calibrate(self, depth_mm: np.ndarray) -> ReferencePlane:
        """Запомнить ровный песок как «ноль» высоты."""
        self.plane = fit_reference_plane(depth_mm, self.roi, self.settings.valid_range)
        self.reset_state()
        return self.plane

    def calibrate_from_frames(self, frames: list[np.ndarray]) -> ReferencePlane:
        """То же по нескольким кадрам: медиана давит шум, калибровка точнее."""
        if not frames:
            raise ValueError("нет кадров для калибровки")
        stack = np.stack([np.asarray(f, dtype=np.float32) for f in frames])
        ok = valid_mask(stack, self.settings.valid_range)
        stack = np.where(ok, stack, np.nan)
        with warnings.catch_warnings():                  # точки, пустые во всех кадрах, — это норма
            warnings.simplefilter("ignore", RuntimeWarning)
            med = np.nanmedian(stack, axis=0)
        return self.calibrate(np.nan_to_num(med, nan=0.0))

    def reset_state(self) -> None:
        """Сбросить сглаживание и память о поверхности — после калибровки или паузы."""
        self._smooth = TemporalSmoother(self.settings.smoothing_alpha)
        self._hands = HandRejector(hand_mm=self.settings.hand_mm,
                                   max_rate_mm_s=self.settings.hand_max_rate_mm_s,
                                   dilate_px=self.settings.hand_dilate_px)
        self.needs_calibration = False
        self.calibration_hint = ""
        self._calib_reason = ""
        self._bad_frames = self._good_frames = 0

    # --- присмотр за калибровкой ---

    def _watch_calibration(self, surface: np.ndarray, sand: np.ndarray) -> tuple[float, float]:
        """Сверить песок с запомненным нулём и, если тот уехал, поднять тревогу.

        Зачем. Опорную плоскость программа подтягивает с прошлого запуска, чтобы
        не калибровать каждое утро. Но между запусками датчик могли задеть, ящик
        подвинуть, песка досыпать. Тогда высоты считаются от неправильного нуля:
        картинка получается вроде бы цветная, а смысла в ней нет. Честнее сказать
        «нужна калибровка», чем молча красить бессмыслицу.

        Признаков два:
          - медиана песка далеко от нуля (плоскость уехала целиком);
          - перепад высот больше, чем физически помещается в ящике.

        Решение принимаем не по одному кадру: и тревога, и отбой включаются
        только после stale_frames подряд — иначе ребёнок, накрывший датчик
        ладонью на секунду, включал бы предупреждение.

        Возвращает (смещение, перепад) в миллиметрах — их же показываем в пульте.
        """
        s = self.settings
        offset = spread = 0.0
        if int(sand.sum()) < 200:
            reason = "датчик почти не видит песок"
        else:
            vals = surface[sand]
            offset = float(np.median(vals))
            spread = float(np.percentile(vals, 98) - np.percentile(vals, 2))
            if abs(offset) > s.max_offset_mm:
                reason = f"песок весь на {offset:+.0f} мм от запомненного нуля"
            elif spread > s.max_spread_mm:
                reason = f"перепад {spread:.0f} мм — больше, чем помещается в ящике"
            else:
                reason = ""

        limit = max(1, s.stale_frames)
        if reason:
            self._calib_reason = reason
            self._bad_frames = min(self._bad_frames + 1, limit)
            self._good_frames = 0
            if self._bad_frames >= limit:
                self.needs_calibration = True
        else:
            self._good_frames = min(self._good_frames + 1, limit)
            self._bad_frames = 0
            if self._good_frames >= limit:
                self.needs_calibration = False
        self.calibration_hint = (
            f"Нужна калибровка: {self._calib_reason}. "
            "Разровняйте песок и нажмите «Запомнить ровный песок»"
            if self.needs_calibration else "")
        return offset, spread

    # --- кадр ---

    def process(self, depth_mm: np.ndarray, dt: float = 1 / 30) -> HeightmapResult:
        s = self.settings
        raw = np.asarray(depth_mm, dtype=np.float32)
        self._noise.push(raw)
        if self.plane is None:
            self.calibrate(raw)                        # первый кадр сам становится «нулём»
        assert self.plane is not None

        ok_raw = valid_mask(raw, s.valid_range)
        depth = median3(raw) if s.despeckle else raw
        depth = np.where(ok_raw, depth, raw)           # пустые точки медианой не «залечиваем»
        depth = self._smooth(depth)

        height, ok = height_from_plane(depth, self.plane, s.valid_range)
        if self.roi is not None:
            ok = ok & self.roi
        surface, hands = self._hands(height, dt)       # рельеф без рук
        surface = np.where(ok, surface, 0.0).astype(np.float32)

        sand = ok & ~hands                             # песок без рук: по нему шкала и присмотр
        if s.height_range_mm is not None:
            lo, hi = s.height_range_mm
        else:
            lo, hi = auto_range(surface, sand, min_span_mm=s.min_span_mm)
        step = s.contour_step_mm if s.contour_step_mm > 0 else nice_step(hi - lo, s.contour_lines)

        rgb = colorize(surface, ok, lo, hi, palette=s.palette, contour_step_mm=step, roi=self.roi)
        # На проектор уходит только ящик. Красим весь кадр (так видно борта в
        # предпросмотре), а отдаём обрезанное — иначе по краям тёмная рамка.
        box = self._roi_box if s.crop_to_roi else None
        rgb = crop_to_box(rgb, box)

        offset_mm, spread_mm = self._watch_calibration(surface, sand)

        dist = float(np.median(raw[ok_raw])) if ok_raw.any() else 0.0
        stats = {
            "valid_pct": round(100.0 * float(ok_raw.mean()), 1),
            "distance_mm": round(dist, 1),
            "noise_mm": round(self._noise.value_mm(self.roi, s.valid_range), 2)
                        if self._noise.ready() else None,
            "relief_mm": round(hi - lo, 1),
            "hand_pct": round(100.0 * float(hands.mean()), 1),
            "tilt_mm": round(self.plane.tilt_mm(raw.shape), 1),
            "offset_mm": round(offset_mm, 1),
            "spread_mm": round(spread_mm, 1),
            "calibration_ok": not self.needs_calibration,
            "calibration_hint": self.calibration_hint or None,
        }
        return HeightmapResult(rgb=rgb, height_mm=surface, hand_mask=hands, ok=ok,
                               lo_mm=lo, hi_mm=hi, contour_step_mm=step, stats=stats,
                               roi_box=box, needs_calibration=self.needs_calibration,
                               calibration_hint=self.calibration_hint)
