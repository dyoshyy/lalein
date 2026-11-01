import math

from lalein.ui import looks
from lalein.ui.character import AVATARS, Line, pose_for
from lalein.ui.quartz_canvas import QuartzCanvas


class FakeQuartz:
    """Records the CoreGraphics calls (name, args without the context)."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def __getattr__(self, name: str):
        if name == "CGRectMake":
            return lambda *a: ("rect", *a)
        if name == "CGContextCopyPath":
            return lambda ctx: self.calls.append(("CGContextCopyPath",)) or "path"
        return lambda ctx, *a: self.calls.append((name, *a))

    def names(self) -> list[str]:
        return [c[0] for c in self.calls]


class FakeText:
    def __init__(self) -> None:
        self.drawn: list[tuple] = []

    def measure(self, text, family, bold, size):
        return len(text) * size * 0.5

    def draw(self, text, x, y, family, bold, size, rgba):
        self.drawn.append((text, x, y, family, bold, size, rgba))


def canvas():
    cg, text = FakeQuartz(), FakeText()
    return QuartzCanvas("ctx", cg, text), cg, text


def test_an_arc_after_new_sub_path_starts_at_its_first_point():
    cr, cg, _ = canvas()
    cr.move_to(0, 0)
    cr.new_sub_path()
    cr.arc(10, 20, 5, 0, math.pi)
    assert cg.calls[1] == ("CGContextMoveToPoint", 15, 20)
    assert cg.calls[2] == ("CGContextAddArc", 10, 20, 5, 0, math.pi, 0)
    cr.arc(10, 20, 5, math.pi, 2 * math.pi)  # continuing: joined like cairo
    assert cg.names()[3:] == ["CGContextAddArc"]


def test_fill_preserve_keeps_the_path_for_the_stroke():
    cr, cg, _ = canvas()
    cr.rectangle(0, 0, 4, 4)
    cr.fill_preserve()
    cr.stroke()
    assert cg.names() == ["CGContextAddRect", "CGContextCopyPath", "CGContextFillPath", "CGContextAddPath",
                          "CGContextStrokePath"]


def test_a_group_is_replayed_inside_one_transparency_layer_at_its_alpha():
    cr, cg, _ = canvas()
    cr.push_group()
    cr.rectangle(0, 0, 4, 4)
    cr.fill()
    assert cg.calls == []  # nothing drawn until the alpha is known
    cr.pop_group_to_source()
    cr.paint_with_alpha(0.6)
    assert cg.names() == ["CGContextSaveGState", "CGContextSetAlpha", "CGContextBeginTransparencyLayer",
                          "CGContextAddRect", "CGContextFillPath", "CGContextEndTransparencyLayer",
                          "CGContextRestoreGState"]
    assert ("CGContextSetAlpha", 0.6) in cg.calls


def test_text_is_drawn_at_the_current_point_in_the_current_colour_and_font():
    cr, _, text = canvas()
    cr.set_source_rgb(1, 0, 0)
    cr.select_font_face("sans-serif", 0, 1)
    cr.set_font_size(16)
    cr.save()
    cr.set_font_size(30)
    cr.restore()  # like cairo, restore brings the font size back
    cr.move_to(5, 40)
    assert cr.text_extents("ab")[4] == 16.0
    cr.show_text("z")
    assert text.drawn == [("z", 5, 40, "", True, 16.0, (1, 0, 0, 1.0))]


def test_every_look_and_the_bubble_draw_on_it():
    for avatar in AVATARS:
        for state in ("listening", "paused", "offline"):
            cr, cg, _ = canvas()
            looks.draw_character(cr, 160, 180, pose_for(state, 0.7), avatar)
            assert cg.names().count("CGContextSaveGState") == cg.names().count("CGContextRestoreGState")
    cr, _, text = canvas()
    looks.draw_bubble(cr, Line("you", "明日の天気を教えて"), "")
    assert [d[0] for d in text.drawn] == ["明日の天気を教えて"]
