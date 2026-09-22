"""Чертежи размещения у стены: вид сбоку и вид сверху (SVG), масштаб 240 px/м.

python tools/draw_wall.py            # пишет docs/img/wall-placement.svg и wall-plan.svg
Параметры — в блоке P ниже. Kinect v2 (базовый): датчик 1,30 м над песком, Г-образная консоль.
Датчик вынесен на SENS_X = 450 мм от стены и над центром ящика НЕ висит (там луч проектора).
Вынос фиксированный: при любых D и L положение датчика, поперечины и конуса поля не меняется.
"""
import math, pathlib

P = dict(
    GAP=0.020, PLY=0.018, D=0.750, L=1.000, WALL_H=0.250, SAND=0.150,
    SENS_ABOVE=1.30, FOV=(70, 60), SENS_RES=512, SENS_NAME="Kinect v2",
    SENS_X=0.450, SENS_BODY=0.067,      # ось датчика от стены (X) и глубина корпуса, м — вынос фиксирован, от размера ящика не зависит
    ARM_Y=0.30,                         # вынос консоли вдоль стены (Y), м
    THROW=1.5, W_IMG=1.2, OFFSET=1.0, X_LENS=0.08,
)
OUT = pathlib.Path(__file__).resolve().parents[1] / "docs" / "img"


def geometry(p):
    g = {}
    g["OUT_D"] = p["D"] + 2 * p["PLY"]; g["OUT_L"] = p["L"] + 2 * p["PLY"]
    g["XC"] = p["GAP"] + g["OUT_D"] / 2; g["BOX_TOP"] = p["PLY"] + p["WALL_H"]
    g["SENS_Z"] = p["SAND"] + p["SENS_ABOVE"]; g["ARM_Z"] = g["SENS_Z"] + 0.10      # низ поперечины: над верхом корпуса датчика нужны пластина, шаровая головка, люлька
    g["H_IMG"] = p["W_IMG"] * 3 / 4; g["DIST"] = p["THROW"] * p["W_IMG"]; g["LENS_Z"] = p["SAND"] + g["DIST"]
    a1 = math.degrees(math.atan((p["OFFSET"] - 1) * g["H_IMG"] / g["DIST"]))
    a2 = math.degrees(math.atan(p["OFFSET"] * g["H_IMG"] / g["DIST"]))
    g["tilt"] = math.degrees(math.atan(p["X_LENS"] / g["DIST"])) + a1
    g["near"] = p["X_LENS"] + g["DIST"] * math.tan(math.radians(a1 - g["tilt"]))
    g["far"] = p["X_LENS"] + g["DIST"] * math.tan(math.radians(a2 - g["tilt"]))
    hz = p["SENS_ABOVE"] * math.tan(math.radians(p["FOV"][0] / 2)); hx = p["SENS_ABOVE"] * math.tan(math.radians(p["FOV"][1] / 2))
    g["cov_l"], g["cov_d"], g["hz"], g["hx"] = 2 * hz, 2 * hx, hz, hx
    g["wall_in_frame_z"] = g["SENS_Z"] - p["SENS_X"] / math.tan(math.radians(p["FOV"][1] / 2))
    g["cov_x0"], g["cov_x1"] = p["SENS_X"] - hx, p["SENS_X"] + hx                      # зона датчика по глубине: за стену и в комнату
    dy = g["LENS_Z"] - g["ARM_Z"]
    g["beam_x_arm"] = p["X_LENS"] + dy * math.tan(math.radians(a2 - g["tilt"]))     # луч на высоте консоли, до X
    g["beam_y_arm"] = dy * (p["W_IMG"] / 2) / g["DIST"]                                # луч на высоте консоли, ±Y
    dz = g["LENS_Z"] - g["SENS_Z"]
    g["beam_x_sens"] = p["X_LENS"] + dz * math.tan(math.radians(a2 - g["tilt"]))    # луч на высоте датчика, до X
    g["sens_face"] = p["SENS_X"] - p["SENS_BODY"] / 2                                  # передняя грань корпуса датчика
    g["gap_sens"] = g["sens_face"] - g["beam_x_sens"]                                  # запас от корпуса до края луча
    g["px_mm"] = g["cov_l"] / p["SENS_RES"] * 1000
    return g


def side_view(p, g):
    S = 240; X0 = 60; Y0 = 560; Wc, Hc = 760, 668
    X = lambda m: X0 + m * S; Y = lambda m: Y0 - m * S
    o = []; a = o.append
    a(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {Wc} {Hc}" font-family="Helvetica, Arial, sans-serif" font-size="11">')
    a('<defs><pattern id="h" width="7" height="7" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="7" stroke="#9aa5b1" stroke-width="1"/></pattern></defs>')
    a(f'<rect width="{Wc}" height="{Hc}" fill="#ffffff"/>')
    a(f'<rect x="{X0-22}" y="0" width="22" height="{Y0}" fill="url(#h)" stroke="#6f7a86"/>')
    a(f'<rect x="{X0-22}" y="{Y0}" width="{Wc-40}" height="14" fill="url(#h)" stroke="#6f7a86"/>')
    a(f'<line x1="{X0}" y1="0" x2="{X0}" y2="{Y0}" stroke="#37434f" stroke-width="2"/>')
    a(f'<line x1="{X0-22}" y1="{Y0}" x2="{Wc-20}" y2="{Y0}" stroke="#37434f" stroke-width="2"/>')
    a(f'<rect x="{X0}" y="{Y(0.06):.1f}" width="{0.02*S:.1f}" height="{0.06*S:.1f}" fill="#c9c2b5" stroke="#6f7a86"/>')
    lens = (X(p["X_LENS"]), Y(g["LENS_Z"]))
    a(f'<polygon points="{lens[0]:.1f},{lens[1]:.1f} {X(g["near"]):.1f},{Y(p["SAND"]):.1f} {X(g["far"]):.1f},{Y(p["SAND"]):.1f}" fill="#f0a64a" opacity="0.12"/>')
    for xe in (g["near"], g["far"]):
        a(f'<line x1="{lens[0]:.1f}" y1="{lens[1]:.1f}" x2="{X(xe):.1f}" y2="{Y(p["SAND"]):.1f}" stroke="#c77a1d" stroke-width="1.3"/>')
    sc = (X(p["SENS_X"]), Y(g["SENS_Z"]))
    a(f'<polygon points="{sc[0]:.1f},{sc[1]:.1f} {X0},{Y(g["wall_in_frame_z"]):.1f} {X0},{Y(p["SAND"]):.1f} {X(g["cov_x1"]):.1f},{Y(p["SAND"]):.1f}" fill="#1a8c9c" opacity="0.08"/>')
    a(f'<line x1="{sc[0]:.1f}" y1="{sc[1]:.1f}" x2="{X0}" y2="{Y(g["wall_in_frame_z"]):.1f}" stroke="#1a8c9c" stroke-width="1.3" stroke-dasharray="5 3"/>')
    a(f'<line x1="{sc[0]:.1f}" y1="{sc[1]:.1f}" x2="{X(g["cov_x1"]):.1f}" y2="{Y(p["SAND"]):.1f}" stroke="#1a8c9c" stroke-width="1.3" stroke-dasharray="5 3"/>')
    bx0, bx1 = X(p["GAP"]), X(p["GAP"] + g["OUT_D"])
    a(f'<path d="M{X(p["GAP"]+p["PLY"]):.1f} {Y(p["PLY"]):.1f} V{Y(p["SAND"]):.1f} Q{X(p["GAP"]+0.2):.1f} {Y(p["SAND"]+0.03):.1f} {X(p["GAP"]+0.35):.1f} {Y(p["SAND"]):.1f} T{X(p["GAP"]+0.6):.1f} {Y(p["SAND"]-0.01):.1f} T{X(p["GAP"]+g["OUT_D"]-p["PLY"]):.1f} {Y(p["SAND"]):.1f} V{Y(p["PLY"]):.1f} Z" fill="#d9c79a"/>')
    a(f'<rect x="{bx0:.1f}" y="{Y(p["PLY"]):.1f}" width="{g["OUT_D"]*S:.1f}" height="{p["PLY"]*S:.1f}" fill="#8a6a45"/>')
    a(f'<rect x="{bx0:.1f}" y="{Y(g["BOX_TOP"]):.1f}" width="{p["PLY"]*S:.1f}" height="{p["WALL_H"]*S:.1f}" fill="#8a6a45"/>')
    a(f'<rect x="{bx1-p["PLY"]*S:.1f}" y="{Y(g["BOX_TOP"]):.1f}" width="{p["PLY"]*S:.1f}" height="{p["WALL_H"]*S:.1f}" fill="#8a6a45"/>')
    fx = X(p["GAP"] + g["OUT_D"] + 0.10); k = S / 130.0
    Pt = lambda dx, dz: f"{fx+dx*k:.1f} {Y0-dz*k:.1f}"
    a(f'<circle cx="{fx+2*k:.1f}" cy="{Y0-80*k:.1f}" r="{10.5*k:.1f}" fill="#9aa5b1"/>')
    a(f'<path d="M{Pt(0,66)} L{Pt(22,28)} L{Pt(-6,26)} L{Pt(0,2)} M{Pt(-8,2)} L{Pt(9,2)} M{Pt(0,64)} L{Pt(-18,48)} L{Pt(-40,27)}" stroke="#9aa5b1" stroke-width="{9*k:.1f}" stroke-linecap="round" stroke-linejoin="round" fill="none"/>')
    arm_top = g["ARM_Z"] + 0.04
    a(f'<rect x="{X0}" y="{Y(2.30):.1f}" width="{0.04*S:.1f}" height="{(2.30-1.30)*S:.1f}" fill="#6f7a86"/>')              # рейка 1,30…2,30
    a(f'<rect x="{X0}" y="{Y(arm_top):.1f}" width="{(p["SENS_X"]+0.04)*S:.1f}" height="{0.04*S:.1f}" fill="#6f7a86"/>')      # консоль (вынос до SENS_X)
    a(f'<rect x="{X(p["SENS_X"]-0.02):.1f}" y="{Y(arm_top):.1f}" width="{0.04*S:.1f}" height="{0.04*S:.1f}" fill="#4f5a66"/>')  # поперечина (торец)
    a(f'<rect x="{X(g["sens_face"]):.1f}" y="{Y(g["SENS_Z"]+0.033):.1f}" width="{p["SENS_BODY"]*S:.1f}" height="{0.066*S:.1f}" fill="#1a8c9c"/>')
    a(f'<rect x="{X0}" y="{Y(2.15):.1f}" width="{0.02*S:.1f}" height="{0.14*S:.1f}" fill="#6f7a86"/>')                        # пластина проектора
    a(f'<g transform="rotate({g["tilt"]:.2f} {lens[0]:.1f} {lens[1]:.1f})"><rect x="{X(p["X_LENS"]-0.05):.1f}" y="{Y(g["LENS_Z"]+0.25):.1f}" width="{0.10*S:.1f}" height="{0.25*S:.1f}" rx="4" fill="#c77a1d"/><circle cx="{lens[0]:.1f}" cy="{lens[1]:.1f}" r="4" fill="#1b1f24"/></g>')
    def vdim(x, z1, z2, label):
        a(f'<line x1="{x}" y1="{Y(z1):.1f}" x2="{x}" y2="{Y(z2):.1f}" stroke="#5d6771" stroke-width="1"/>')
        for z in (z1, z2): a(f'<line x1="{x-4}" y1="{Y(z):.1f}" x2="{x+4}" y2="{Y(z):.1f}" stroke="#5d6771"/>')
        a(f'<text transform="translate({x-5} {Y((z1+z2)/2):.1f}) rotate(-90)" text-anchor="middle" fill="#5d6771" font-size="10.5" stroke="#ffffff" stroke-width="3" stroke-linejoin="round" paint-order="stroke">{label}</text>')
    def hline(y, x1, x2, label, lx):
        a(f'<line x1="{X(x1):.1f}" y1="{y:.1f}" x2="{X(x2):.1f}" y2="{y:.1f}" stroke="#5d6771" stroke-width="1"/>')
        for x in (x1, x2): a(f'<line x1="{X(x):.1f}" y1="{y-4:.1f}" x2="{X(x):.1f}" y2="{y+4:.1f}" stroke="#5d6771"/>')
        a(f'<text x="{lx:.1f}" y="{y+4:.1f}" fill="#5d6771" font-size="10.5" stroke="#ffffff" stroke-width="3" stroke-linejoin="round" paint-order="stroke">{label}</text>')
    a(f'<line x1="{X(g["cov_x1"]):.1f}" y1="{Y(p["SAND"]):.1f}" x2="612" y2="{Y(p["SAND"]):.1f}" stroke="#5d6771" stroke-dasharray="2 3" opacity="0.7"/>')
    a(f'<line x1="{X(p["SENS_X"]+0.034):.1f}" y1="{Y(g["SENS_Z"]):.1f}" x2="612" y2="{Y(g["SENS_Z"]):.1f}" stroke="#5d6771" stroke-dasharray="2 3" opacity="0.7"/>')
    a(f'<line x1="{X(p["X_LENS"]+0.05):.1f}" y1="{Y(g["LENS_Z"]):.1f}" x2="644" y2="{Y(g["LENS_Z"]):.1f}" stroke="#5d6771" stroke-dasharray="2 3" opacity="0.7"/>')
    vdim(580, p["SAND"], g["SENS_Z"], f"датчик {p['SENS_ABOVE']:.2f} м над песком")
    vdim(612, p["SAND"], g["LENS_Z"], f"объектив {g['DIST']:.2f} м над песком")
    vdim(644, 0, g["LENS_Z"], f"объектив {g['LENS_Z']:.2f} м от пола")
    vdim(676, 0, p["SAND"], "песок 0,15")
    for xm in (0.0, p["X_LENS"], g["XC"], p["SENS_X"], g["far"], p["GAP"] + g["OUT_D"]):
        a(f'<line x1="{X(xm):.1f}" y1="{Y0+14}" x2="{X(xm):.1f}" y2="{Y0+70}" stroke="#5d6771" stroke-dasharray="2 3" opacity="0.6"/>')
    hline(Y0 + 30, 0, g["far"], f"картинка на песке от 0 до {g['far']*1000:.0f} мм от стены (вдоль стены {p['W_IMG']:.1f} м)", X(g["far"]) + 8)
    hline(Y0 + 48, 0, p["SENS_X"], f"ось датчика {p['SENS_X']*1000:.0f} мм от стены — фиксированно, от размера ящика не зависит; край луча здесь {g['beam_x_sens']*1000:.0f} мм", X(p["SENS_X"]) + 8)
    hline(Y0 + 66, 0, p["X_LENS"], f"объектив {p['X_LENS']*1000:.0f} мм от стены; ящик снаружи {p['GAP']*1000:.0f}…{(p['GAP']+g['OUT_D'])*1000:.0f} мм (центр {g['XC']*1000:.0f} мм), зазор под плинтус {p['GAP']*1000:.0f} мм", X(p["X_LENS"]) + 8)
    a(f'<text x="{X0-18}" y="{Y0+100}" fill="#1d232a" font-size="11">Вид сбоку, один масштаб по осям. Стена слева, ребёнок у переднего борта. Размеры от плоскости стены и от пола, мм.</text>')
    HALO = 'stroke="#ffffff" stroke-width="3" stroke-linejoin="round" paint-order="stroke"'
    lbl = lambda x, y, t: a(f'<text x="{x:.1f}" y="{y:.1f}" fill="#1d232a" font-size="11" {HALO}>{t}</text>')
    a(f'<line x1="{X0}" y1="10" x2="{X(p["SENS_X"]):.1f}" y2="10" stroke="#5d6771"/><line x1="{X0}" y1="6" x2="{X0}" y2="14" stroke="#5d6771"/><line x1="{X(p["SENS_X"]):.1f}" y1="6" x2="{X(p["SENS_X"]):.1f}" y2="14" stroke="#5d6771"/>')
    lbl(X(p["SENS_X"]) + 8, 14, f"вынос консоли {p['SENS_X']*1000:.0f} мм — фиксированный, от размера ящика не зависит")
    lbl(X0 + 48, Y(2.19) + 2, "рейка 40×40, три анкера M8, верх 2,30 м; проектор на своей пластине по центру")
    lbl(X(p["X_LENS"]) + 34, Y(2.08), "проектор объективом вниз, дном к стене, на универсальном кронштейне;")
    lbl(X(p["X_LENS"]) + 34, Y(2.03), f"наклон к стене {g['tilt']:.1f}°, отношение {p['THROW']}, картинка {p['W_IMG']}×{g['H_IMG']:.2f} м, страховочный трос")
    lbl(X(p["SENS_X"]) + 48, Y(g["SENS_Z"]) - 30, f"датчик {p['SENS_NAME']}: ось {p['SENS_X']*1000:.0f} мм от стены, {p['SENS_ABOVE']:.2f} м над песком")
    lbl(X(p["SENS_X"]) + 48, Y(g["SENS_Z"]) - 18, f"видит {g['cov_l']:.2f}×{g['cov_d']:.2f} м ({g['px_mm']:.1f} мм на точку), стена в кадре ниже {g['wall_in_frame_z']:.2f} м")
    lbl(X(p["SENS_X"]) + 48, Y(g["SENS_Z"]) - 6, f"грань корпуса {g['sens_face']*1000:.0f} мм, край луча {g['beam_x_sens']*1000:.0f} мм — запас {g['gap_sens']*1000:.0f} мм, тени нет")
    lbl(X(p["GAP"] + g["OUT_D"]) + 24, Y(0.82), "ребёнок 5 лет у переднего борта:")
    lbl(X(p["GAP"] + g["OUT_D"]) + 24, Y(0.77), "голова вне луча проектора, тени только от рук")
    lbl(X0 + 8, Y(0.42), "ящик на полу: борт 250, фанера 18, песок 150 от пола")
    lbl(X0 + 8, Y(0.365), "кабель-канал 40×25 рядом с рейкой, вниз к тумбе с ПК")
    a('</svg>')
    return "\n".join(o)


def top_view(p, g):
    S = 240; W, H = 820, 600
    px = lambda y: 90 + (y + 1.3) * S; py = lambda x: 44 + x * S
    OUT_D, OUT_L, XC, ARM_Y, SENS_X = g["OUT_D"], g["OUT_L"], g["XC"], p["ARM_Y"], p["SENS_X"]
    o = []; a = o.append
    a(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" font-family="Helvetica, Arial, sans-serif" font-size="11">')
    a('<defs><pattern id="h" width="7" height="7" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="7" stroke="#9aa5b1" stroke-width="1"/></pattern></defs>')
    a(f'<rect width="{W}" height="{H}" fill="#fff"/>')
    a(f'<rect x="20" y="24" width="{W-40}" height="20" fill="url(#h)" stroke="#6f7a86"/>')
    a(f'<line x1="20" y1="44" x2="{W-20}" y2="44" stroke="#37434f" stroke-width="2"/>')
    a(f'<text x="{W-24}" y="38" text-anchor="end" fill="#1d232a">стена</text>')
    a(f'<rect x="{px(-OUT_L/2-0.6):.1f}" y="{py(0):.1f}" width="{(OUT_L+1.2)*S:.1f}" height="{(p["GAP"]+OUT_D+0.6)*S:.1f}" fill="none" stroke="#9aa5b1" stroke-dasharray="6 4"/>')
    a(f'<text x="{px(-OUT_L/2-0.6)+4:.1f}" y="{py(p["GAP"]+OUT_D+0.6)-15:.1f}" fill="#5d6771" font-size="10.5" stroke="#ffffff" stroke-width="3" stroke-linejoin="round" paint-order="stroke">свободная зона 0,6 м с трёх сторон</text>')
    a(f'<rect x="{px(-p["W_IMG"]/2):.1f}" y="{py(0):.1f}" width="{p["W_IMG"]*S:.1f}" height="{g["far"]*S:.1f}" fill="#f0a64a" opacity="0.10" stroke="#c77a1d" stroke-width="1.3"/>')
    x0 = max(0.0, g["cov_x0"])
    a(f'<rect x="{px(-g["hz"]):.1f}" y="{py(x0):.1f}" width="{2*g["hz"]*S:.1f}" height="{(g["cov_x1"]-x0)*S:.1f}" fill="#1a8c9c" opacity="0.06" stroke="#1a8c9c" stroke-width="1.3" stroke-dasharray="5 3"/>')
    a(f'<rect x="{px(-OUT_L/2):.1f}" y="{py(p["GAP"]):.1f}" width="{OUT_L*S:.1f}" height="{OUT_D*S:.1f}" fill="#8a6a45"/>')
    a(f'<rect x="{px(-p["L"]/2):.1f}" y="{py(p["GAP"]+p["PLY"]):.1f}" width="{p["L"]*S:.1f}" height="{p["D"]*S:.1f}" fill="#d9c79a"/>')
    a(f'<text x="{px(0):.1f}" y="{py(0.71)+4:.1f}" text-anchor="middle" fill="#5d6771" stroke="#ffffff" stroke-width="3" stroke-linejoin="round" paint-order="stroke">песок 1000 × {p["D"]*1000:.0f} внутри, борт 18 мм</text>')
    a(f'<rect x="{px(-g["beam_y_arm"]):.1f}" y="{py(0.056):.1f}" width="{2*g["beam_y_arm"]*S:.1f}" height="{(g["beam_x_arm"]-0.056)*S:.1f}" fill="#c77a1d" opacity="0.28"/>')
    a(f'<line x1="{px(-0.45):.1f}" y1="{py(g["beam_x_sens"]):.1f}" x2="{px(0.45):.1f}" y2="{py(g["beam_x_sens"]):.1f}" stroke="#c77a1d" stroke-width="1.2" stroke-dasharray="6 4"/>')
    a(f'<text x="{px(-0.43):.1f}" y="{py(g["beam_x_sens"])-5:.1f}" text-anchor="start" fill="#8a5a12" font-size="10" stroke="#ffffff" stroke-width="3" stroke-linejoin="round" paint-order="stroke">край луча на высоте датчика — {g["beam_x_sens"]*1000:.0f} мм</text>')
    a(f'<rect x="{px(ARM_Y-0.02):.1f}" y="{py(0):.1f}" width="{0.04*S:.1f}" height="{(SENS_X+0.04)*S:.1f}" fill="#6f7a86"/>')            # продольная часть консоли, до SENS_X
    a(f'<rect x="{px(-0.06):.1f}" y="{py(SENS_X-0.02):.1f}" width="{(ARM_Y+0.08)*S:.1f}" height="{0.04*S:.1f}" fill="#4f5a66"/>')       # поперечина к Y = 0 на той же X
    a(f'<rect x="{px(-0.1245):.1f}" y="{py(g["sens_face"]):.1f}" width="{0.249*S:.1f}" height="{p["SENS_BODY"]*S:.1f}" fill="#1a8c9c"/>')  # датчик на SENS_X
    a(f'<rect x="{px(-0.15):.1f}" y="{py(0.03):.1f}" width="{0.30*S:.1f}" height="{0.10*S:.1f}" fill="#c77a1d"/>')
    a(f'<circle cx="{px(0):.1f}" cy="{py(p["X_LENS"]):.1f}" r="3.5" fill="#1b1f24"/>')
    a(f'<rect x="{px(ARM_Y+0.05):.1f}" y="{py(0):.1f}" width="{(1.05-ARM_Y-0.05)*S:.1f}" height="{0.025*S:.1f}" fill="#e8e4dc" stroke="#6f7a86"/>')
    a(f'<rect x="{px(0.85):.1f}" y="{py(0.02):.1f}" width="{0.40*S:.1f}" height="{0.40*S:.1f}" fill="#a79c8c" stroke="#6f7a86"/>')
    a(f'<text x="{px(1.05):.1f}" y="{py(0.20)+4:.1f}" text-anchor="middle" fill="#fff" font-size="10.5">тумба</text>')
    a(f'<text x="{px(1.05):.1f}" y="{py(0.26)+4:.1f}" text-anchor="middle" fill="#fff" font-size="10.5">ПК, ИБП, фильтр</text>')
    def kid(y, x, rot):
        a(f'<g transform="translate({px(y):.1f} {py(x):.1f}) rotate({rot})"><ellipse rx="{0.19*S:.1f}" ry="{0.09*S:.1f}" fill="#9aa5b1" opacity="0.6"/><circle cy="{-0.03*S:.1f}" r="{0.075*S:.1f}" fill="#9aa5b1"/></g>')
    kid(-0.26, p["GAP"] + OUT_D + 0.16, 0); kid(0.26, p["GAP"] + OUT_D + 0.16, 0); kid(-OUT_L/2 - 0.16, XC, 90); kid(OUT_L/2 + 0.16, XC, 90)
    def hdim(x, y1, y2, label):
        Y = py(x)
        a(f'<line x1="{px(y1):.1f}" y1="{Y:.1f}" x2="{px(y2):.1f}" y2="{Y:.1f}" stroke="#5d6771"/>')
        for y in (y1, y2): a(f'<line x1="{px(y):.1f}" y1="{Y-4:.1f}" x2="{px(y):.1f}" y2="{Y+4:.1f}" stroke="#5d6771"/>')
        a(f'<text x="{px((y1+y2)/2):.1f}" y="{Y+13:.1f}" text-anchor="middle" fill="#5d6771" font-size="10.5" stroke="#ffffff" stroke-width="3" stroke-linejoin="round" paint-order="stroke">{label}</text>')
    def vdim(y, x1, x2, label):
        Xp = px(y)
        a(f'<line x1="{Xp:.1f}" y1="{py(x1):.1f}" x2="{Xp:.1f}" y2="{py(x2):.1f}" stroke="#5d6771"/>')
        for x in (x1, x2): a(f'<line x1="{Xp-4:.1f}" y1="{py(x):.1f}" x2="{Xp+4:.1f}" y2="{py(x):.1f}" stroke="#5d6771"/>')
        a(f'<text transform="translate({Xp-5:.1f} {py((x1+x2)/2):.1f}) rotate(-90)" text-anchor="middle" fill="#5d6771" font-size="10.5" stroke="#ffffff" stroke-width="3" stroke-linejoin="round" paint-order="stroke">{label}</text>')
    hdim(1.30, -p["W_IMG"]/2, p["W_IMG"]/2, f"картинка проектора {p['W_IMG']*1000:.0f} мм")
    hdim(1.39, -OUT_L/2, OUT_L/2, f"ящик снаружи {OUT_L*1000:.0f} мм")
    hdim(1.48, -g["hz"], g["hz"], f"поле датчика {g['cov_l']*1000:.0f} мм")
    hdim(1.57, 0, ARM_Y, f"консоль +{ARM_Y*1000:.0f} мм от центра, поперечина обратно к Y = 0")
    vdim(-0.58, 0, XC, f"центр ящика {XC*1000:.0f}")
    vdim(-0.72, 0, SENS_X, f"ось датчика {SENS_X*1000:.0f}")
    vdim(-0.86, 0, p["GAP"] + OUT_D, f"ящик до {(p['GAP']+OUT_D)*1000:.0f}")
    vdim(-1.00, 0, g["far"], f"картинка до {g['far']*1000:.0f}")
    vdim(-1.14, 0, p["X_LENS"], f"объектив {p['X_LENS']*1000:.0f}")
    HALO = 'stroke="#ffffff" stroke-width="3" stroke-linejoin="round" paint-order="stroke"'
    lbl = lambda x, y, t, anchor="start", size=11, color="#1d232a": a(f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}" fill="{color}" font-size="{size}" {HALO}>{t}</text>')
    lbl(px(-0.17), py(0.09) + 4, "проектор, Y = 0", "end")
    lbl(px(ARM_Y + 0.04), py(0.12) + 4, f"рейка, Y = +{ARM_Y*1000:.0f}", "start", 10.5)
    lbl(px(-0.16), py(SENS_X) + 4, f"датчик {p['SENS_NAME']}, X = {SENS_X*1000:.0f}", "end")
    lbl(px(0.62), py(0.03) + 8, "кабель-канал → тумба", "start", 9.5, "#5d6771")
    lbl(px(0), py(p["GAP"] + OUT_D + 0.32) + 4, "двое детей спереди", "middle")
    lbl(px(OUT_L/2 + 0.16), py(XC + 0.24) + 4, "по одному с торцов", "middle", 10.5)
    notes = [f"Проектор на настенной пластине по центру ящика (Y = 0), объектив на {p['X_LENS']*1000:.0f} мм от стены и {g['LENS_Z']:.2f} м от пола.",
             f"Датчик {p['SENS_NAME']}: ось X = {SENS_X*1000:.0f} мм от стены, {g['SENS_Z']:.2f} м от пола. Вынос фиксированный: от размера ящика (его центр {XC*1000:.0f} мм) не зависит.",
             f"Поле {g['cov_l']:.2f} × {g['cov_d']:.2f} м ({g['px_mm']:.1f} мм на точку): по глубине от −{abs(g['cov_x0']):.2f} (в кадр попадает стена) до +{g['cov_x1']:.2f} м, вдоль стены ±{g['hz']*1000:.0f} мм — ящик целиком.",
             f"Консоль Г-образная: от стены на Y = +{ARM_Y*1000:.0f} до X = {SENS_X*1000:.0f} мм, поперечина оттуда к Y = 0 на той же X — вся вне луча.",
             f"Луч на высоте консоли: ±{g['beam_y_arm']*1000:.0f} мм по Y, до {g['beam_x_arm']*1000:.0f} мм от стены; на высоте датчика — до {g['beam_x_sens']*1000:.0f} мм.",
             f"Грань корпуса датчика на {g['sens_face']*1000:.0f} мм от стены: запас до луча {g['gap_sens']*1000:.0f} мм. Оранжевое у стены — сечение луча на высоте консоли.",
             "Кабель-канал по стене рядом с рейкой, затем вдоль плинтуса к тумбе; ни одного кабеля ниже 1,3 м в зоне детей."]
    for i, t in enumerate(notes): lbl(px(-1.3), py(1.68) + i * 14, t, "start", 10.5)
    lbl(W/2, H-12, "Вид сверху, масштаб один по осям. Стена сверху. X — от стены, Y — вдоль стены от центра ящика, мм.", "middle")
    a('</svg>')
    return "\n".join(o)


if __name__ == "__main__":
    g = geometry(P)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "wall-placement.svg").write_text(side_view(P, g), encoding="utf-8")
    (OUT / "wall-plan.svg").write_text(top_view(P, g), encoding="utf-8")
    keys = ["XC", "SENS_Z", "ARM_Z", "LENS_Z", "tilt", "near", "far", "cov_l", "cov_d", "cov_x0", "cov_x1", "px_mm", "wall_in_frame_z", "beam_x_arm", "beam_y_arm", "beam_x_sens", "sens_face", "gap_sens"]
    print(" ".join(f"{k}={g[k]:.3f}" for k in keys))
