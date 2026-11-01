"""The character's looks, drawn with cairo: ``draw_character(cr, w, h, pose, avatar)``.

Each look (``character.AVATARS``) is its own figure with its own face, drawn by one function
here in a frame whose origin is at the character's feet (the pose's sway, bounce and squash are
already applied). Every look shows the same pose (``character.Pose``): its eyes open, shut, look
up or are crossed out, its mouth smiles, talks or goes flat, its colour follows the tint, and
it lights something up while hearing. A look returns where its head is, for the shared marks
drawn on top (the sweat drop and the sound waves).

It draws on a ``Canvas``: a ``cairo.Context`` (Linux, Windows) or the macOS Quartz canvas
(``quartz_canvas``), which has exactly these methods. ``draw_bubble`` draws the speech bubble on
one too (Windows, macOS; the Linux overlay lays its text out with Pango instead).
"""

import math
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, Protocol

from lalein.ui.character import (
    BUBBLE_H,
    CHAR_W,
    CHAR_X,
    CHAR_Y,
    DEFAULT_AVATAR,
    WIDTH,
    Line,
    Pose,
    body_rgb,
)

CANVAS_METHODS = frozenset("""
    save restore translate rotate scale
    move_to line_to curve_to arc rectangle close_path new_sub_path
    set_source_rgb set_source_rgba set_line_width set_line_cap
    fill fill_preserve stroke
    push_group pop_group_to_source paint_with_alpha
    select_font_face set_font_size text_extents show_text
""".split())
# cairo's values, which the Quartz canvas takes too
LINE_CAP_BUTT, LINE_CAP_ROUND = 0, 1
FONT_SLANT_NORMAL, FONT_WEIGHT_NORMAL, FONT_WEIGHT_BOLD = 0, 0, 1


class Canvas(Protocol):
    """The part of ``cairo.Context`` the looks use (``CANVAS_METHODS``)."""

    def __getattr__(self, name: str) -> Any: ...

RGB = tuple[float, float, float]
Head = tuple[float, float, float]  # centre x, centre y, radius

INK = (0.12, 0.14, 0.25)
CHEEK = (1.0, 0.55, 0.6)
LIT = (1.0, 0.95, 0.5)  # hearing: what lights up


@contextmanager
def _faded(cr: Canvas, alpha: float) -> Iterator[None]:
    """Draw at ``alpha`` as one layer, so overlapping parts do not show through each other."""
    if alpha >= 1.0:
        yield
        return
    cr.push_group()
    yield
    cr.pop_group_to_source()
    cr.paint_with_alpha(alpha)


def _shade(rgb: RGB, k: float) -> RGB:
    return (rgb[0] * k, rgb[1] * k, rgb[2] * k)


def _ellipse(cr: Canvas, x: float, y: float, rx: float, ry: float) -> None:
    cr.save()
    cr.translate(x, y)
    cr.scale(rx, max(ry, 0.01))
    cr.new_sub_path()
    cr.arc(0, 0, 1, 0, 2 * math.pi)
    cr.restore()


def _paint(cr: Canvas, fill: RGB, edge: RGB | None = None, width: float = 3.0) -> None:
    cr.set_source_rgb(*fill)
    if edge is None:
        cr.fill()
        return
    cr.fill_preserve()
    cr.set_source_rgb(*edge)
    cr.set_line_width(width)
    cr.stroke()


def _halo(cr: Canvas, x: float, y: float, r: float) -> None:
    cr.arc(x, y, r, 0, 2 * math.pi)
    cr.set_source_rgba(1.0, 0.9, 0.3, 0.45)
    cr.fill()


def _cheeks(cr: Canvas, p: Pose, xs: tuple[float, ...], y: float, rx: float, ry: float) -> None:
    if p.tint == "grey":
        return
    cr.set_source_rgba(*CHEEK, 0.5)
    for x in xs:
        _ellipse(cr, x, y, rx, ry)
        cr.fill()


def _round_eyes(cr: Canvas, p: Pose, u: float, xs: tuple[float, ...], y: float, rx: float,
                ry: float, colour: RGB = INK, glint: bool = True) -> None:
    look = p.eye_look_y * u * 0.06
    for x in xs:
        cr.set_source_rgb(*colour)
        cr.set_line_width(3)
        if p.x_eyes:
            d = rx * 0.9
            cr.move_to(x - d, y - d); cr.line_to(x + d, y + d)
            cr.move_to(x + d, y - d); cr.line_to(x - d, y + d)
            cr.stroke()
        elif p.eye_open < 0.15:
            cr.new_sub_path()
            cr.arc(x, y, rx, 0.15 * math.pi, 0.85 * math.pi)
            cr.stroke()
        else:
            _ellipse(cr, x, y + look, rx, ry * p.eye_open)
            cr.fill()
            if glint:
                cr.set_source_rgb(1, 1, 1)
                cr.arc(x + rx * 0.33, y + look - ry * 0.33, max(rx * 0.25, 1.6), 0, 2 * math.pi)
                cr.fill()


def _mouth(cr: Canvas, p: Pose, x: float, y: float, s: float, colour: RGB = INK) -> None:
    """A smile, a talking oval or a flat wavy line, ``s`` wide either side."""
    cr.set_source_rgb(*colour)
    cr.set_line_width(3)
    if p.mouth_open > 0.08:
        _ellipse(cr, x, y, s * 1.1, s * (0.25 + 1.1 * p.mouth_open))
        cr.fill()
    elif p.mouth_flat:
        cr.move_to(x - s * 0.8, y + 2)
        cr.curve_to(x - s * 0.3, y - 3, x + s * 0.3, y + 5, x + s * 0.8, y)
        cr.stroke()
    else:
        cr.new_sub_path()
        cr.arc(x, y - s * 0.5, s, 0.2 * math.pi, 0.8 * math.pi)
        cr.stroke()


# ---- the looks ----

def _bot(cr: Canvas, p: Pose, u: float, rgb: RGB) -> Head:
    """A round blob with an antenna: the original."""
    rx, ry, cy = u * 0.85, u * 0.8, -u * 0.85
    top = cy - ry
    cr.set_source_rgb(*INK)
    cr.set_line_width(3)
    cr.move_to(0, top + 4)
    cr.curve_to(u * 0.05, top - u * 0.2, -u * 0.05, top - u * 0.25, 0, top - u * 0.35)
    cr.stroke()
    if p.glow:
        _halo(cr, 0, top - u * 0.4, 11)
    cr.arc(0, top - u * 0.4, 5, 0, 2 * math.pi)
    _paint(cr, LIT if p.glow else (1.0, 0.85, 0.35))
    for fx in (-u * 0.35, u * 0.35):
        _ellipse(cr, fx, -u * 0.06, u * 0.22, u * 0.12)
        _paint(cr, _shade(rgb, 0.7))
    _ellipse(cr, 0, cy, rx, ry)
    _paint(cr, rgb, _shade(rgb, 0.6))
    _cheeks(cr, p, (-rx * 0.62, rx * 0.62), cy + ry * 0.25, u * 0.14, u * 0.09)
    _round_eyes(cr, p, u, (-rx * 0.38, rx * 0.38), cy - ry * 0.12, u * 0.09, u * 0.12)
    _mouth(cr, p, 0, cy + ry * 0.35, u * 0.12)
    return 0, cy, rx


def _rounded_rect(cr: Canvas, x: float, y: float, w: float, h: float, r: float) -> None:
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 1.5 * math.pi)
    cr.close_path()


def _robo(cr: Canvas, p: Pose, u: float, rgb: RGB) -> Head:
    """A boxy robot whose head is a screen: LED eyes and an LED mouth."""
    edge = _shade(rgb, 0.55)
    # legs, body, arms
    for lx in (-u * 0.28, u * 0.28):
        _rounded_rect(cr, lx - u * 0.1, -u * 0.32, u * 0.2, u * 0.32, 4)
        _paint(cr, _shade(rgb, 0.75), edge)
    _rounded_rect(cr, -u * 0.5, -u * 0.8, u * 1.0, u * 0.52, 8)
    _paint(cr, rgb, edge)
    cr.arc(0, -u * 0.55, u * 0.08, 0, 2 * math.pi)  # a chest light
    _paint(cr, (1.0, 0.45, 0.4) if p.tint == "warm" else (0.4, 0.9, 0.6) if p.tint != "grey" else (0.4, 0.4, 0.4))
    for side in (-1, 1):
        cr.save()
        cr.translate(side * u * 0.5, -u * 0.72)
        cr.rotate(side * (0.25 + p.mouth_open * 0.3))
        _rounded_rect(cr, -u * 0.07 if side > 0 else -u * 0.07, 0, u * 0.14, u * 0.4, 5)
        cr.restore()
        _paint(cr, _shade(rgb, 0.85), edge)
    # head: a box with ear bolts and an antenna
    hx0, hy0, hw, hh = -u * 0.72, -u * 1.85, u * 1.44, u * 1.02
    hcx, hcy = 0.0, hy0 + hh / 2
    for side in (-1, 1):
        _rounded_rect(cr, side * (hw / 2 + u * 0.06) - u * 0.06, hcy - u * 0.16, u * 0.12, u * 0.32, 3)
        _paint(cr, _shade(rgb, 0.7), edge)
    cr.set_source_rgb(*edge)
    cr.set_line_width(3)
    cr.move_to(0, hy0)
    cr.line_to(0, hy0 - u * 0.2)
    cr.stroke()
    if p.glow:
        _halo(cr, 0, hy0 - u * 0.24, 10)
    cr.arc(0, hy0 - u * 0.24, 5, 0, 2 * math.pi)
    _paint(cr, LIT if p.glow else (1.0, 0.4, 0.35))
    _rounded_rect(cr, hx0, hy0, hw, hh, 12)
    _paint(cr, rgb, edge)
    sx0, sy0, sw, sh = hx0 + u * 0.14, hy0 + u * 0.14, hw - u * 0.28, hh - u * 0.28
    _rounded_rect(cr, sx0, sy0, sw, sh, 8)
    _paint(cr, (0.08, 0.12, 0.16))
    led = (1.0, 0.45, 0.35) if p.tint == "warm" else (0.45, 0.95, 1.0) if p.tint != "grey" else (0.45, 0.45, 0.48)
    if p.tint == "dim":
        led = _shade(led, 0.6)
    ey = sy0 + sh * 0.4 + p.eye_look_y * u * 0.06
    for ex in (-u * 0.26, u * 0.26):
        cr.set_source_rgb(*led)
        cr.set_line_width(3)
        if p.x_eyes:
            d = u * 0.08
            cr.move_to(ex - d, ey - d); cr.line_to(ex + d, ey + d)
            cr.move_to(ex + d, ey - d); cr.line_to(ex - d, ey + d)
            cr.stroke()
        elif p.eye_open < 0.15:
            cr.rectangle(ex - u * 0.1, ey - 1.5, u * 0.2, 3)
            cr.fill()
        else:
            eh = u * 0.2 * p.eye_open
            cr.rectangle(ex - u * 0.08, ey - eh / 2, u * 0.16, eh)
            cr.fill()
    my = sy0 + sh * 0.78
    cr.set_source_rgb(*led)
    if p.mouth_open > 0.08:  # a level meter
        for i in range(5):
            bh = u * (0.04 + 0.16 * p.mouth_open * (1 - abs(i - 2) / 3))
            cr.rectangle(-u * 0.22 + i * u * 0.09, my - bh / 2, u * 0.06, bh)
        cr.fill()
    elif p.mouth_flat:
        cr.set_line_width(3)
        cr.move_to(-u * 0.18, my)
        for i in range(1, 7):
            cr.line_to(-u * 0.18 + i * u * 0.06, my + (3 if i % 2 else -3))
        cr.stroke()
    else:
        for i in range(5):
            x = -u * 0.2 + i * u * 0.1
            cr.rectangle(x - 2, my - 2 + (u * 0.05 if i in (1, 2, 3) else 0), 4, 4)
        cr.fill()
    return hcx, hcy, hw / 2


def _cat(cr: Canvas, p: Pose, u: float, rgb: RGB) -> Head:
    """A black cat sitting, seen whole: tail, haunches, a head with ears, slit eyes."""
    edge = _shade(rgb, 0.55) if p.tint != "normal" else (0.06, 0.06, 0.08)
    # tail, curling up on the right
    wag = p.sway * 3 + p.body_dy * 4
    cr.set_line_cap(LINE_CAP_ROUND)
    for colour, width in ((edge, u * 0.16 + 6), (rgb, u * 0.16)):  # outlined, like the body
        cr.move_to(u * 0.45, -u * 0.12)
        cr.curve_to(u * 1.05, -u * 0.1, u * (0.95 + wag), -u * 0.7, u * (0.72 + wag), -u * 0.95)
        cr.set_source_rgb(*colour)
        cr.set_line_width(width)
        cr.stroke()
    cr.set_line_cap(LINE_CAP_BUTT)
    # body: a pear, two front paws
    cr.move_to(-u * 0.62, -u * 0.05)
    cr.curve_to(-u * 0.7, -u * 0.6, -u * 0.35, -u * 1.0, 0, -u * 1.0)
    cr.curve_to(u * 0.35, -u * 1.0, u * 0.7, -u * 0.6, u * 0.62, -u * 0.05)
    cr.close_path()
    _paint(cr, rgb, edge)
    for px in (-u * 0.2, u * 0.2):
        _ellipse(cr, px, -u * 0.06, u * 0.15, u * 0.09)
        _paint(cr, _shade(rgb, 1.25) if rgb[0] < 0.5 else rgb, edge, 2)
    # head and ears
    hx, hy, hr = 0.0, -u * 1.38, u * 0.55
    inner = LIT if p.glow else (0.95, 0.6, 0.7)
    for side in (-1, 1):
        cr.move_to(side * hr * 0.92, hy - hr * 0.1)
        cr.line_to(side * hr * 0.78, hy - hr * 1.25)
        cr.line_to(side * hr * 0.2, hy - hr * 0.78)
        cr.close_path()
        _paint(cr, rgb, edge)
        cr.move_to(side * hr * 0.76, hy - hr * 0.35)
        cr.line_to(side * hr * 0.72, hy - hr * 1.0)
        cr.line_to(side * hr * 0.38, hy - hr * 0.72)
        cr.close_path()
        _paint(cr, inner)
    _ellipse(cr, hx, hy, hr, hr * 0.9)
    _paint(cr, rgb, edge)
    # eyes: yellow-green almonds with slit pupils
    ey = hy - hr * 0.08
    look = p.eye_look_y * u * 0.05
    for ex in (-hr * 0.42, hr * 0.42):
        if p.x_eyes or p.eye_open < 0.15:
            _round_eyes(cr, p, u, (ex,), ey, u * 0.09, u * 0.1, colour=(0.85, 0.9, 0.5))
            continue
        _ellipse(cr, ex, ey, u * 0.13, u * 0.11 * p.eye_open)
        _paint(cr, (0.85, 0.92, 0.35) if p.tint != "dim" else (0.6, 0.65, 0.3))
        _ellipse(cr, ex, ey + look, u * 0.03, u * 0.1 * p.eye_open)
        _paint(cr, (0.05, 0.05, 0.05))
    # nose, mouth, whiskers
    ny = hy + hr * 0.28
    cr.move_to(-4, ny - 2); cr.line_to(4, ny - 2); cr.line_to(0, ny + 3); cr.close_path()
    _paint(cr, (0.95, 0.55, 0.65))
    if p.mouth_open > 0.08:
        _ellipse(cr, 0, ny + u * 0.1, u * 0.07, u * (0.02 + 0.08 * p.mouth_open))
        _paint(cr, (0.95, 0.45, 0.55))
    else:
        cr.set_source_rgb(0.85, 0.85, 0.9)
        cr.set_line_width(2)
        if p.mouth_flat:
            cr.move_to(-u * 0.08, ny + u * 0.1); cr.line_to(u * 0.08, ny + u * 0.1)
        else:  # ω
            cr.new_sub_path(); cr.arc(-u * 0.045, ny + u * 0.04, u * 0.045, 0.1 * math.pi, 0.95 * math.pi)
            cr.new_sub_path(); cr.arc(u * 0.045, ny + u * 0.04, u * 0.045, 0.05 * math.pi, 0.9 * math.pi)
        cr.stroke()
    cr.set_source_rgba(0.9, 0.9, 0.95, 0.8)
    cr.set_line_width(1.5)
    for side in (-1, 1):
        for k in (-1, 1):
            cr.move_to(side * hr * 0.45, ny + k * 3)
            cr.line_to(side * hr * 1.15, ny + k * 7 - 2)
        cr.stroke()
    return hx, hy, hr


def _ghost(cr: Canvas, p: Pose, u: float, rgb: RGB) -> Head:
    """A floating teardrop wisp with big dark eyes and little arms."""
    hx, hy, hr = 0.0, -u * 1.25, u * 0.62
    edge = _shade(rgb, 0.7)
    sway = p.sway * 2.5
    # body: a round head pulled into a curling tail
    cr.move_to(-hr, hy)
    cr.arc(hx, hy, hr, math.pi, 2 * math.pi)
    cr.curve_to(hr, hy + hr * 0.9, u * (0.55 + sway), -u * 0.35, u * (0.25 + sway), -u * 0.15)
    cr.curve_to(u * (0.05 + sway), -u * 0.02, -u * 0.3, -u * 0.25, -u * 0.18, -u * 0.42)
    cr.curve_to(-u * 0.5, -u * 0.5, -hr, hy + hr * 0.6, -hr, hy)
    cr.close_path()
    cr.set_source_rgba(*rgb, 0.92)
    cr.fill_preserve()
    cr.set_source_rgb(*edge)
    cr.set_line_width(3)
    cr.stroke()
    for side in (-1, 1):  # stubby arms
        _ellipse(cr, side * hr * 1.02, hy + hr * 0.55, u * 0.14, u * 0.09)
        _paint(cr, rgb, edge, 2.5)
    if p.glow:
        cr.set_source_rgba(1.0, 0.85, 0.3, 0.9)
        cr.set_line_width(4)
        _ellipse(cr, hx, hy - hr - u * 0.18, u * 0.32, u * 0.08)
        cr.stroke()
    _cheeks(cr, p, (-hr * 0.62, hr * 0.62), hy + hr * 0.32, u * 0.1, u * 0.06)
    _round_eyes(cr, p, u, (-hr * 0.35, hr * 0.35), hy - hr * 0.05, u * 0.12, u * 0.17, glint=False)
    if p.mouth_open > 0.08 or p.mouth_flat:
        _mouth(cr, p, 0, hy + hr * 0.42, u * 0.07)
    else:  # a little "o"
        cr.set_source_rgb(*INK)
        cr.set_line_width(2.5)
        _ellipse(cr, 0, hy + hr * 0.4, u * 0.04, u * 0.05)
        cr.stroke()
    return hx, hy, hr


def _onigiri(cr: Canvas, p: Pose, u: float, rgb: RGB) -> Head:
    """A rice ball: a soft triangle of rice, a sheet of nori, an umeboshi on top."""
    edge = _shade(rgb, 0.72)
    w, top, base = u * 1.05, -u * 1.95, -u * 0.05
    cr.move_to(0, top)
    cr.curve_to(u * 0.3, top, w * 0.95, base - u * 0.55, w, base - u * 0.2)
    cr.curve_to(w * 1.02, base, w * 0.8, base, w * 0.6, base)
    cr.line_to(-w * 0.6, base)
    cr.curve_to(-w * 0.8, base, -w * 1.02, base, -w, base - u * 0.2)
    cr.curve_to(-w * 0.95, base - u * 0.55, -u * 0.3, top, 0, top)
    cr.close_path()
    _paint(cr, rgb, edge)
    # rice grains
    cr.set_source_rgba(*edge, 0.6)
    cr.set_line_width(1.5)
    for gx, gy, a in ((-0.35, -1.25, 0.6), (0.3, -1.35, -0.4), (0.6, -0.95, 0.2), (-0.65, -0.85, -0.3)):
        cr.save()
        cr.translate(gx * u, gy * u)
        cr.rotate(a)
        _ellipse(cr, 0, 0, u * 0.06, u * 0.03)
        cr.restore()
        cr.stroke()
    # nori
    nori = (0.10, 0.18, 0.14) if p.tint != "grey" else (0.3, 0.3, 0.32)
    _rounded_rect(cr, -u * 0.48, -u * 0.62, u * 0.96, u * 0.6, 4)
    _paint(cr, nori)
    # umeboshi
    if p.glow:
        _halo(cr, 0, top + u * 0.32, u * 0.2)
    cr.arc(0, top + u * 0.32, u * 0.12, 0, 2 * math.pi)
    _paint(cr, LIT if p.glow else (0.85, 0.2, 0.25) if p.tint != "grey" else (0.45, 0.45, 0.45))
    hy = -u * 0.95
    _cheeks(cr, p, (-u * 0.45, u * 0.45), hy + u * 0.12, u * 0.1, u * 0.06)
    _round_eyes(cr, p, u, (-u * 0.24, u * 0.24), hy - u * 0.05, u * 0.06, u * 0.07, glint=False)
    _mouth(cr, p, 0, hy + u * 0.16, u * 0.08)
    return 0.0, hy, u * 0.8


def _edamame(cr: Canvas, p: Pose, u: float, rgb: RGB) -> Head:
    """A tall edamame pod with three beans, a stem on top and twig arms; the face on the top bean."""
    edge = _shade(rgb, 0.6)
    beans = [(0.0, -1.5, 0.48), (0.04, -0.92, 0.42), (-0.02, -0.4, 0.38)]
    lean = p.sway * 1.5
    for fill in (False, True):  # the outline of all three, then the beans over it: one pod
        for bx, by, br in beans:
            cr.new_sub_path()
            cr.arc((bx + lean * (by + 0.4)) * u, by * u, br * u, 0, 2 * math.pi)
        if fill:
            _paint(cr, rgb)
        else:
            cr.set_source_rgb(*edge)
            cr.set_line_width(6)
            cr.stroke()
    cr.set_source_rgba(1, 1, 1, 0.35)  # a shine down the pod
    cr.set_line_width(4)
    cr.set_line_cap(LINE_CAP_ROUND)
    cr.move_to(-u * 0.26, -u * 0.62)
    cr.curve_to(-u * 0.32, -u * 0.9, -u * 0.3, -u * 1.1, -u * 0.22, -u * 1.25)
    cr.stroke()
    cr.set_line_cap(LINE_CAP_BUTT)
    hx, hy = lean * -1.1 * u, -1.5 * u
    # stem, and a leaf that lights up while hearing
    cr.set_source_rgb(*_shade(rgb, 0.55))
    cr.set_line_width(4)
    cr.move_to(hx, hy - u * 0.46)
    cr.curve_to(hx + u * 0.02, hy - u * 0.62, hx + u * 0.12, hy - u * 0.7, hx + u * 0.2, hy - u * 0.66)
    cr.stroke()
    if p.glow:
        _halo(cr, hx - u * 0.12, hy - u * 0.62, u * 0.16)
    cr.save()
    cr.translate(hx - u * 0.12, hy - u * 0.62)
    cr.rotate(-0.5)
    _ellipse(cr, 0, 0, u * 0.14, u * 0.06)
    cr.restore()
    _paint(cr, LIT if p.glow else (0.3, 0.6, 0.25))
    # twig arms, waving while it talks
    cr.set_source_rgb(*_shade(rgb, 0.55))
    cr.set_line_width(3)
    for side in (-1, 1):
        lift = p.mouth_open * 0.25 if side > 0 else 0
        cr.move_to(side * u * 0.4, -u * 0.95)
        cr.line_to(side * u * 0.72, -u * (0.75 + lift * 1.6))
        cr.stroke()
    _cheeks(cr, p, (hx - u * 0.3, hx + u * 0.3), hy + u * 0.12, u * 0.08, u * 0.05)
    _round_eyes(cr, p, u, (hx - u * 0.16, hx + u * 0.16), hy - u * 0.04, u * 0.06, u * 0.09)
    _mouth(cr, p, hx, hy + u * 0.18, u * 0.07)
    return hx, hy, u * 0.48


LOOKS: dict[str, Callable[[Canvas, Pose, float, RGB], Head]] = {
    "bot": _bot, "robo": _robo, "cat": _cat, "ghost": _ghost, "onigiri": _onigiri, "edamame": _edamame,
}


def draw_character(cr: Canvas, w: float, h: float, p: Pose, avatar: str = DEFAULT_AVATAR) -> None:
    look = LOOKS.get(avatar, LOOKS[DEFAULT_AVATAR])
    cr.save()
    cr.translate(w / 2, h * 0.92)  # origin at the feet
    u = min(w, h * 0.8) / 2
    cr.rotate(p.sway)
    cr.translate(0, p.body_dy * u * 2)
    cr.scale(1 - p.squash, 1 + p.squash)
    with _faded(cr, p.alpha):
        _draw_figure(cr, look, p, u, avatar)
    cr.restore()
    _draw_marks(cr, w, h, u, p)


def _draw_figure(cr: Canvas, look: Callable[[Canvas, Pose, float, RGB], Head], p: Pose, u: float,
                 avatar: str) -> None:
    hx, hy, hr = look(cr, p, u, body_rgb(avatar, p.tint))
    if p.sweat:
        cr.set_source_rgba(0.5, 0.8, 1.0, 0.9)
        sx, sy = hx + hr * 0.8, hy - hr * 0.5
        cr.move_to(sx, sy - 9)
        cr.curve_to(sx + 7, sy + 2, sx + 5, sy + 9, sx, sy + 9)
        cr.curve_to(sx - 5, sy + 9, sx - 7, sy + 2, sx, sy - 9)
        cr.fill()
    if p.waves >= 0:  # hearing: sound waves travel out from both sides of the head
        cr.set_line_width(3)
        for k in range(2):
            phase = (p.waves + k * 0.5) % 1.0
            d = hr + u * 0.12 + phase * u * 0.3
            cr.set_source_rgba(0.95, 0.55, 0.2, 0.9 * (1 - phase))
            for side in (-1, 1):
                start = -0.25 * math.pi if side > 0 else 0.75 * math.pi
                cr.new_sub_path()
                cr.arc(hx, hy, d, start, start + 0.5 * math.pi)
                cr.stroke()


def _draw_marks(cr: Canvas, w: float, h: float, u: float, p: Pose) -> None:
    # thinking dots and sleep z live in the unrotated frame, top right, clear of the head
    top, left = h * 0.92 - u * 2.2, w / 2 + u * 0.75
    if p.dots:
        cr.set_source_rgba(*INK, 0.8)
        for i in range(p.dots):
            cr.arc(left + i * 13, top + 4, 4.5, 0, 2 * math.pi)
            cr.fill()
    if p.zzz >= 0:
        cr.set_source_rgba(0.85, 0.9, 1.0, 1 - p.zzz)
        cr.select_font_face("sans-serif", FONT_SLANT_NORMAL, FONT_WEIGHT_BOLD)
        cr.set_font_size(16 + 10 * p.zzz)
        cr.move_to(left + 20 * p.zzz, top + 18 - 14 * p.zzz)
        cr.show_text("z")


# ---- the speech bubble (Windows, macOS) ----

BUBBLE_PAD = 10
BUBBLE_LINES = 4
BUBBLE_FONT_SIZE = 14
# (fill, text) per speaker
BUBBLE_STYLE = {"me": ((1.0, 1.0, 1.0, 0.96), (0.12, 0.14, 0.25)),
                "you": ((0.86, 0.96, 0.87, 0.96), (0.10, 0.30, 0.15)),
                "note": ((0.12, 0.14, 0.25, 0.88), (1.0, 1.0, 1.0))}


def wrap(text: str, width: float, measure: Callable[[str], float], max_lines: int) -> list[str]:
    """``text`` in lines no wider than ``width``: broken at the last space when the line has one
    (English), anywhere otherwise (Japanese); the last line ends in "…" when text is left over."""
    lines: list[str] = []
    rest = text.strip()
    while rest and len(lines) < max_lines:
        n = 1
        while n < len(rest) and measure(rest[: n + 1]) <= width:
            n += 1
        if n < len(rest) and " " in rest[1:n + 1]:
            n = rest.rindex(" ", 1, n + 1)
        lines.append(rest[:n].rstrip())
        rest = rest[n:].lstrip()
    if rest and lines:
        last = lines[-1]
        while last and measure(last + "…") > width:
            last = last[:-1]
        lines[-1] = last.rstrip() + "…"
    return lines


def draw_bubble(cr: Canvas, line: Line, font: str) -> None:
    """The bubble just above the character's head (in the overlay's WIDTH x HEIGHT box), its
    tail pointing at the head; ``font`` is a family name the canvas knows."""
    fill, ink = BUBBLE_STYLE[line.who]
    cr.select_font_face(font, FONT_SLANT_NORMAL, FONT_WEIGHT_NORMAL)
    cr.set_font_size(BUBBLE_FONT_SIZE)
    lines = wrap(line.text, WIDTH - 2 * BUBBLE_PAD - 8, lambda t: cr.text_extents(t)[4], BUBBLE_LINES)
    if not lines:
        return
    step = BUBBLE_FONT_SIZE * 1.35
    w = min(max(cr.text_extents(t)[4] for t in lines) + 2 * BUBBLE_PAD, WIDTH - 4)
    h = min(len(lines) * step + 2 * BUBBLE_PAD - (step - BUBBLE_FONT_SIZE), BUBBLE_H + 2)
    x, bottom = WIDTH - 4 - w, CHAR_Y + 6
    y = bottom - h
    tail_x = min(max(CHAR_X + CHAR_W * 0.35, x + 18), x + w - 18)

    def outline() -> None:
        _rounded_rect(cr, x, y, w, h, 12)
        cr.move_to(tail_x - 9, bottom - 1)
        cr.line_to(CHAR_X + CHAR_W * 0.38, CHAR_Y + 30)
        cr.line_to(tail_x + 9, bottom - 1)
        cr.close_path()

    # border first, then the fill over its inner half: no seam where the tail meets the box
    outline()
    cr.set_source_rgba(0.12, 0.14, 0.25, 0.3)
    cr.set_line_width(3)
    cr.stroke()
    outline()
    cr.set_source_rgba(*fill[:3], 1.0)
    cr.fill()
    cr.set_source_rgb(*ink)
    for i, text in enumerate(lines):
        cr.move_to(x + BUBBLE_PAD, y + BUBBLE_PAD + BUBBLE_FONT_SIZE * 0.85 + i * step)
        cr.show_text(text)
