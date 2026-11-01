"""A ``looks.Canvas`` on a macOS Quartz ``CGContext``: the cairo calls the looks make, in
CoreGraphics (pycairo has no macOS wheels, so the Mac character draws through this).

The view it draws in is flipped (y down), like cairo. Paths are added in user space and kept in
device space by both, so a path built under a ``scale`` and stroked after ``restore`` gets an
even line in either. Three cairo habits need help:

- ``new_sub_path`` then ``arc``: Quartz would join the arc to the last point, so the arc starts
  with a move to its first point instead.
- ``fill_preserve``: Quartz empties the path when it fills, so it is copied and put back.
- ``push_group`` ... ``paint_with_alpha(a)``: a Quartz transparency layer needs its alpha when
  it begins, so the calls in between are recorded and replayed inside one at ``a``.

Text (``show_text``, ``text_extents``) goes through ``AppKitText``, which also finds the glyphs a
font lacks (Japanese in the bubble). ``cg`` and ``text`` are parameters, for the tests.
"""

import math
from collections.abc import Callable
from typing import Any


class AppKitText:
    """Measures and draws one line of text with AppKit, at a cairo-style baseline point."""

    def _font(self, family: str, bold: bool, size: float) -> Any:
        from AppKit import NSFont

        font = NSFont.fontWithName_size_(family, size) if family else None
        if font is None:
            font = NSFont.boldSystemFontOfSize_(size) if bold else NSFont.systemFontOfSize_(size)
        return font

    def _attrs(self, font: Any, rgba: tuple[float, float, float, float]) -> dict:
        from AppKit import NSColor, NSFontAttributeName, NSForegroundColorAttributeName

        return {NSFontAttributeName: font,
                NSForegroundColorAttributeName: NSColor.colorWithSRGBRed_green_blue_alpha_(*rgba)}

    def measure(self, text: str, family: str, bold: bool, size: float) -> float:
        from Foundation import NSString

        font = self._font(family, bold, size)
        return float(NSString.stringWithString_(text).sizeWithAttributes_(self._attrs(font, (0, 0, 0, 1))).width)

    def draw(self, text: str, x: float, y: float, family: str, bold: bool, size: float,
             rgba: tuple[float, float, float, float]) -> None:
        from Foundation import NSString

        font = self._font(family, bold, size)
        # in a flipped view the point is the text's top-left; cairo's is on the baseline
        NSString.stringWithString_(text).drawAtPoint_withAttributes_((x, y - font.ascender()),
                                                                     self._attrs(font, rgba))


def _quartz() -> Any:
    import Quartz

    return Quartz


class QuartzCanvas:
    """The ``looks.CANVAS_METHODS`` on ``ctx``."""

    def __init__(self, ctx: Any, cg: Any = None, text: Any = None) -> None:
        self._ctx = ctx
        self._cg = cg or _quartz()
        self._text = text or AppKitText()
        self._fresh = True  # no current point: an arc starts a new subpath
        self._point = (0.0, 0.0)
        self._style = {"rgba": (0.0, 0.0, 0.0, 1.0), "family": "", "bold": False, "size": 10.0}
        self._saved: list[dict] = []
        self._groups: list[list[Callable[[], None]]] = []  # calls recorded inside push_group
        self._pending: list[Callable[[], None]] = []  # a popped group, waiting for its alpha

    def _do(self, call: Callable[[], None]) -> None:
        if self._groups:
            self._groups[-1].append(call)
        else:
            call()

    # ---- state and transforms ----

    def save(self) -> None:
        def run() -> None:
            self._cg.CGContextSaveGState(self._ctx)
            self._saved.append(dict(self._style))
        self._do(run)

    def restore(self) -> None:
        def run() -> None:
            self._cg.CGContextRestoreGState(self._ctx)
            if self._saved:
                self._style = self._saved.pop()
        self._do(run)

    def translate(self, x: float, y: float) -> None:
        self._do(lambda: self._cg.CGContextTranslateCTM(self._ctx, x, y))

    def rotate(self, angle: float) -> None:
        self._do(lambda: self._cg.CGContextRotateCTM(self._ctx, angle))

    def scale(self, x: float, y: float) -> None:
        self._do(lambda: self._cg.CGContextScaleCTM(self._ctx, x, y))

    # ---- paths ----

    def move_to(self, x: float, y: float) -> None:
        def run() -> None:
            self._cg.CGContextMoveToPoint(self._ctx, x, y)
            self._fresh, self._point = False, (x, y)
        self._do(run)

    def line_to(self, x: float, y: float) -> None:
        def run() -> None:
            self._cg.CGContextAddLineToPoint(self._ctx, x, y)
            self._fresh, self._point = False, (x, y)
        self._do(run)

    def curve_to(self, x1: float, y1: float, x2: float, y2: float, x3: float, y3: float) -> None:
        def run() -> None:
            self._cg.CGContextAddCurveToPoint(self._ctx, x1, y1, x2, y2, x3, y3)
            self._fresh, self._point = False, (x3, y3)
        self._do(run)

    def arc(self, xc: float, yc: float, r: float, a1: float, a2: float) -> None:
        def run() -> None:
            if self._fresh:
                self._cg.CGContextMoveToPoint(self._ctx, xc + r * math.cos(a1), yc + r * math.sin(a1))
            # angles grow from +x towards +y in both (y down here); 0: that direction
            self._cg.CGContextAddArc(self._ctx, xc, yc, r, a1, a2, 0)
            self._fresh, self._point = False, (xc + r * math.cos(a2), yc + r * math.sin(a2))
        self._do(run)

    def rectangle(self, x: float, y: float, w: float, h: float) -> None:
        def run() -> None:
            self._cg.CGContextAddRect(self._ctx, self._cg.CGRectMake(x, y, w, h))
            self._fresh, self._point = False, (x, y)
        self._do(run)

    def close_path(self) -> None:
        self._do(lambda: self._cg.CGContextClosePath(self._ctx))

    def new_sub_path(self) -> None:
        def run() -> None:
            self._fresh = True
        self._do(run)

    # ---- painting ----

    def set_source_rgb(self, r: float, g: float, b: float) -> None:
        self.set_source_rgba(r, g, b, 1.0)

    def set_source_rgba(self, r: float, g: float, b: float, a: float) -> None:
        def run() -> None:
            self._cg.CGContextSetRGBFillColor(self._ctx, r, g, b, a)
            self._cg.CGContextSetRGBStrokeColor(self._ctx, r, g, b, a)
            self._style["rgba"] = (r, g, b, a)
        self._do(run)

    def set_line_width(self, width: float) -> None:
        self._do(lambda: self._cg.CGContextSetLineWidth(self._ctx, width))

    def set_line_cap(self, cap: int) -> None:
        self._do(lambda: self._cg.CGContextSetLineCap(self._ctx, int(cap)))  # cairo's and Quartz's agree

    def fill(self) -> None:
        def run() -> None:
            self._cg.CGContextFillPath(self._ctx)
            self._fresh = True
        self._do(run)

    def fill_preserve(self) -> None:
        def run() -> None:
            path = self._cg.CGContextCopyPath(self._ctx)
            self._cg.CGContextFillPath(self._ctx)
            self._cg.CGContextAddPath(self._ctx, path)
        self._do(run)

    def stroke(self) -> None:
        def run() -> None:
            self._cg.CGContextStrokePath(self._ctx)
            self._fresh = True
        self._do(run)

    def push_group(self) -> None:
        self._groups.append([])

    def pop_group_to_source(self) -> None:
        self._pending = self._groups.pop()

    def paint_with_alpha(self, alpha: float) -> None:
        calls, self._pending = self._pending, []

        def run() -> None:
            self._cg.CGContextSaveGState(self._ctx)
            self._cg.CGContextSetAlpha(self._ctx, alpha)
            self._cg.CGContextBeginTransparencyLayer(self._ctx, None)
            for call in calls:
                call()
            self._cg.CGContextEndTransparencyLayer(self._ctx)
            self._cg.CGContextRestoreGState(self._ctx)
        self._do(run)

    # ---- text ----

    def select_font_face(self, family: str, slant: int = 0, weight: int = 0) -> None:
        def run() -> None:
            self._style["family"] = "" if family in ("sans-serif", "sans") else family
            self._style["bold"] = bool(weight)
        self._do(run)

    def set_font_size(self, size: float) -> None:
        def run() -> None:
            self._style["size"] = float(size)
        self._do(run)

    def text_extents(self, text: str) -> tuple[float, float, float, float, float, float]:
        s = self._style
        w = self._text.measure(text, s["family"], s["bold"], s["size"])
        return 0.0, -s["size"], w, s["size"], w, 0.0

    def show_text(self, text: str) -> None:
        def run() -> None:
            s = self._style
            x, y = self._point
            self._text.draw(text, x, y, s["family"], s["bold"], s["size"], s["rgba"])
            self._fresh = True  # cairo's show_text leaves no path behind either
        self._do(run)
