"""QR-код своими руками: без внешних библиотек и без интернета.

Нужен ровно для одного: показать на пульте квадрат, наведя на который камерой
телефона специалист открывает пульт. Поэтому сделано только то, что нужно для
короткой ссылки: байтовый режим, уровень коррекции L, версии 1–10
(до 271 байта). Итог — матрица из нулей и единиц и картинка SVG.

Алгоритм стандартный (ISO/IEC 18004): данные → коды с коррекцией
Рида — Соломона → раскладка по матрице змейкой → выбор маски по штрафам.
"""
from __future__ import annotations

# --- арифметика Галуа GF(256), образующий многочлен 0x11D -------------------
_EXP: list[int] = [0] * 512
_LOG: list[int] = [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _poly_mul(a: list[int], b: list[int]) -> list[int]:
    out = [0] * (len(a) + len(b) - 1)
    for i, av in enumerate(a):
        if av:
            for j, bv in enumerate(b):
                out[i + j] ^= _mul(av, bv)
    return out


def _generator(n: int) -> list[int]:
    """Порождающий многочлен кода Рида — Соломона на n проверочных байт."""
    p = [1]
    for i in range(n):
        p = _poly_mul(p, [1, _EXP[i]])
    return p


def _ec_bytes(data: list[int], n: int) -> list[int]:
    gen = _generator(n)
    rem = list(data) + [0] * n
    for i in range(len(data)):
        coef = rem[i]
        if coef:
            for j, g in enumerate(gen):
                rem[i + j] ^= _mul(g, coef)
    return rem[len(data):]


# --- таблицы версий для уровня коррекции L ---------------------------------
# версия: (проверочных байт на блок, [(сколько блоков, байт данных в блоке)])
_BLOCKS: dict[int, tuple[int, list[tuple[int, int]]]] = {
    1: (7, [(1, 19)]), 2: (10, [(1, 34)]), 3: (15, [(1, 55)]), 4: (20, [(1, 80)]),
    5: (26, [(1, 108)]), 6: (18, [(2, 68)]), 7: (20, [(2, 78)]), 8: (24, [(2, 97)]),
    9: (30, [(2, 116)]), 10: (18, [(2, 68), (2, 69)]),
}
# центры выравнивающих квадратов
_ALIGN: dict[int, list[int]] = {
    1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34],
    7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50],
}
# «хвостовые» биты после всех кодов
_REMAINDER: dict[int, int] = {1: 0, 2: 7, 3: 7, 4: 7, 5: 7, 6: 7, 7: 0, 8: 0, 9: 0, 10: 0}

_MASKS = [
    lambda r, c: (r + c) % 2 == 0,
    lambda r, c: r % 2 == 0,
    lambda r, c: c % 3 == 0,
    lambda r, c: (r + c) % 3 == 0,
    lambda r, c: (c // 3 + r // 2) % 2 == 0,
    lambda r, c: (c * r) % 2 + (c * r) % 3 == 0,
    lambda r, c: ((c * r) % 2 + (c * r) % 3) % 2 == 0,
    lambda r, c: ((c + r) % 2 + (c * r) % 3) % 2 == 0,
]


def _capacity(version: int) -> int:
    """Сколько байт полезных данных влезает в версию (режим байтов, уровень L)."""
    data_cw = sum(n * c for n, c in _BLOCKS[version][1])
    header_bits = 4 + (8 if version <= 9 else 16)
    return (data_cw * 8 - header_bits) // 8


def _pick_version(length: int) -> int:
    for v in sorted(_BLOCKS):
        if length <= _capacity(v):
            return v
    raise ValueError("слишком длинная строка для этого QR-кода (максимум %d байт)" % _capacity(10))


def _codewords(data: bytes, version: int) -> list[int]:
    ec_per, groups = _BLOCKS[version]
    data_cw = sum(n * c for n, c in groups)
    bits: list[int] = []

    def put(value: int, n: int) -> None:
        for i in range(n - 1, -1, -1):
            bits.append((value >> i) & 1)

    put(0b0100, 4)                                   # режим: байты
    put(len(data), 8 if version <= 9 else 16)        # длина
    for b in data:
        put(b, 8)
    put(0, min(4, data_cw * 8 - len(bits)))          # признак конца
    while len(bits) % 8:
        bits.append(0)
    fill = (0xEC, 0x11)
    k = 0
    while len(bits) < data_cw * 8:
        put(fill[k % 2], 8)
        k += 1

    plain = [int("".join(str(b) for b in bits[i:i + 8]), 2) for i in range(0, len(bits), 8)]
    blocks: list[list[int]] = []
    checks: list[list[int]] = []
    pos = 0
    for n, c in groups:
        for _ in range(n):
            blk = plain[pos:pos + c]
            pos += c
            blocks.append(blk)
            checks.append(_ec_bytes(blk, ec_per))
    out: list[int] = []
    for i in range(max(len(b) for b in blocks)):      # данные вперемешку по блокам
        for b in blocks:
            if i < len(b):
                out.append(b[i])
    for i in range(ec_per):                           # затем проверочные байты
        for e in checks:
            out.append(e[i])
    return out


def _skeleton(version: int) -> tuple[list[list[int | None]], list[list[bool]], int]:
    """Служебные узоры: поисковые квадраты, дорожки, выравнивание."""
    size = 17 + 4 * version
    m: list[list[int | None]] = [[None] * size for _ in range(size)]
    fixed = [[False] * size for _ in range(size)]

    def put(row: int, col: int, val: int) -> None:
        if 0 <= row < size and 0 <= col < size:
            m[row][col] = val
            fixed[row][col] = True

    for r0, c0 in ((0, 0), (0, size - 7), (size - 7, 0)):       # три больших квадрата
        for r in range(-1, 8):
            for c in range(-1, 8):
                inside = 0 <= r < 7 and 0 <= c < 7
                dark = inside and (r in (0, 6) or c in (0, 6) or (2 <= r <= 4 and 2 <= c <= 4))
                put(r0 + r, c0 + c, 1 if dark else 0)
    for i in range(size):                                       # дорожки-«линейки»
        if m[6][i] is None:
            put(6, i, 1 if i % 2 == 0 else 0)
        if m[i][6] is None:
            put(i, 6, 1 if i % 2 == 0 else 0)
    last = size - 7
    for r0 in _ALIGN[version]:                                  # квадратики выравнивания
        for c0 in _ALIGN[version]:
            if (r0, c0) in ((6, 6), (6, last), (last, 6)):
                continue                                # три угла заняты поисковыми квадратами
            for r in range(-2, 3):
                for c in range(-2, 3):
                    put(r0 + r, c0 + c, 1 if max(abs(r), abs(c)) != 1 else 0)
    put(size - 8, 8, 1)                                         # всегда тёмный модуль

    for i in range(9):                                          # место под формат
        if m[8][i] is None:
            fixed[8][i] = True
        if m[i][8] is None:
            fixed[i][8] = True
    for i in range(8):
        fixed[8][size - 1 - i] = True
        fixed[size - 1 - i][8] = True
    if version >= 7:                                            # место под номер версии
        for i in range(18):
            a, b = size - 11 + i % 3, i // 3
            fixed[b][a] = True
            fixed[a][b] = True
    return m, fixed, size


def _place_data(m, fixed, size: int, codes: list[int], remainder: int) -> None:
    bits = []
    for code in codes:
        for i in range(7, -1, -1):
            bits.append((code >> i) & 1)
    bits.extend([0] * remainder)
    i = 0
    col = size - 1
    while col >= 1:
        if col == 6:
            col = 5                                   # колонку-дорожку пропускаем
        for vert in range(size):
            for j in range(2):
                c = col - j
                upward = ((col + 1) & 2) == 0
                r = size - 1 - vert if upward else vert
                if not fixed[r][c] and i < len(bits):
                    m[r][c] = bits[i]
                    i += 1
        col -= 2


def _format_bits(mask: int) -> int:
    data = 0b01 << 3 | mask                           # 01 = уровень коррекции L
    rem = data
    for _ in range(10):
        rem = (rem << 1) ^ ((rem >> 9) * 0x537)
    return ((data << 10) | rem) ^ 0x5412


def _draw_format(m, size: int, mask: int) -> None:
    bits = _format_bits(mask)

    def bit(i: int) -> int:
        return (bits >> i) & 1

    for i in range(6):
        m[i][8] = bit(i)
    m[7][8] = bit(6)
    m[8][8] = bit(7)
    m[8][7] = bit(8)
    for i in range(9, 15):
        m[8][14 - i] = bit(i)
    for i in range(8):
        m[8][size - 1 - i] = bit(i)
    for i in range(8, 15):
        m[size - 15 + i][8] = bit(i)


def _draw_version(m, size: int, version: int) -> None:
    if version < 7:
        return
    rem = version
    for _ in range(12):
        rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
    bits = (version << 12) | rem
    for i in range(18):
        b = (bits >> i) & 1
        a, c = size - 11 + i % 3, i // 3
        m[c][a] = b
        m[a][c] = b


def _penalty(m: list[list[int]], size: int) -> int:
    score = 0
    finder = [1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0]
    for line in [[m[r][c] for c in range(size)] for r in range(size)] + \
                [[m[r][c] for r in range(size)] for c in range(size)]:
        run, prev = 1, line[0]                        # правило 1: длинные одноцветные полосы
        for v in line[1:]:
            if v == prev:
                run += 1
            else:
                if run >= 5:
                    score += 3 + (run - 5)
                run, prev = 1, v
        if run >= 5:
            score += 3 + (run - 5)
        for i in range(size - 10):                    # правило 3: узор, похожий на поисковый
            window = line[i:i + 11]
            if window == finder or window == finder[::-1]:
                score += 40
    for r in range(size - 1):                         # правило 2: квадраты 2×2
        for c in range(size - 1):
            v = m[r][c]
            if v == m[r][c + 1] == m[r + 1][c] == m[r + 1][c + 1]:
                score += 3
    dark = sum(sum(row) for row in m)                 # правило 4: перекос тёмного и светлого
    score += abs(dark * 100 // (size * size) - 50) // 5 * 10
    return score


def matrix(text: str) -> list[list[int]]:
    """Матрица QR-кода: список строк из 0 (светлый) и 1 (тёмный)."""
    data = text.encode("utf-8")
    version = _pick_version(len(data))
    codes = _codewords(data, version)
    best = None
    for mask in range(8):
        m, fixed, size = _skeleton(version)
        _place_data(m, fixed, size, codes, _REMAINDER[version])
        _draw_format(m, size, mask)
        _draw_version(m, size, version)
        grid = [[(0 if v is None else v) for v in row] for row in m]
        for r in range(size):
            for c in range(size):
                if not fixed[r][c] and _MASKS[mask](r, c):
                    grid[r][c] ^= 1
        p = _penalty(grid, size)
        if best is None or p < best[0]:
            best = (p, grid)
    assert best is not None
    return best[1]


def svg(text: str, module: int = 8, quiet: int = 4,
        dark: str = "#0b0f14", light: str = "#ffffff") -> str:
    """Картинка QR-кода в SVG: вставляется прямо в страницу, ничего не грузит."""
    grid = matrix(text)
    size = len(grid)
    side = (size + quiet * 2) * module
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {side} {side}" '
        f'width="{side}" height="{side}" shape-rendering="crispEdges">',
        f'<rect width="{side}" height="{side}" fill="{light}"/>',
        f'<g fill="{dark}">',
    ]
    for r, row in enumerate(grid):
        c = 0
        while c < size:
            if row[c]:
                run = 1
                while c + run < size and row[c + run]:
                    run += 1
                x = (c + quiet) * module
                y = (r + quiet) * module
                parts.append(f'<rect x="{x}" y="{y}" width="{run * module}" height="{module}"/>')
                c += run
            else:
                c += 1
    parts.append("</g></svg>")
    return "".join(parts)
