import pytest

from lalein.ui import looks
from lalein.ui.character import AVATARS, STATES, Line, pose_for


class RecordingCanvas:
    """Only what ``looks.Canvas`` promises, so a look that needs more fails here (the macOS
    canvas implements exactly this much)."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.font_size = 10.0

    def __getattr__(self, name: str):
        if name not in looks.CANVAS_METHODS:
            raise AttributeError(f"looks used {name!r}, which a Canvas does not have")
        return lambda *args: self.calls.append(name)

    def set_font_size(self, size: float) -> None:
        self.calls.append("set_font_size")
        self.font_size = size

    def text_extents(self, text: str) -> tuple[float, ...]:
        self.calls.append("text_extents")
        w = len(text) * self.font_size * 0.6
        return 0.0, -self.font_size, w, self.font_size, w, 0.0


@pytest.mark.parametrize("avatar", AVATARS)
@pytest.mark.parametrize("state", [*STATES, "hearing"])
def test_every_look_draws_every_state_with_canvas_calls_only(avatar, state):
    cr = RecordingCanvas()
    pose = pose_for("listening" if state == "hearing" else state, 1.3, hearing=state == "hearing")
    looks.draw_character(cr, 160, 180, pose, avatar)
    assert cr.calls.count("save") == cr.calls.count("restore")
    assert {"fill", "fill_preserve"} & set(cr.calls) and "stroke" in cr.calls


def test_a_faded_pose_is_drawn_as_one_layer():
    cr = RecordingCanvas()
    looks.draw_character(cr, 160, 180, pose_for("paused", 0.0))  # alpha 0.6
    assert cr.calls.count("push_group") == 1 and cr.calls[-1] != "push_group"
    assert "paint_with_alpha" in cr.calls


def test_the_bubble_draws_with_canvas_calls_only():
    cr = RecordingCanvas()
    looks.draw_bubble(cr, Line("me", "こんにちは、今日の予定は三つあります。" * 10), "Yu Gothic UI")
    assert cr.calls.count("show_text") == looks.BUBBLE_LINES


def measure(text: str) -> float:
    return len(text) * 10.0


def test_wrap_breaks_japanese_anywhere_and_english_at_spaces():
    assert looks.wrap("あいうえおかきくけこ", 50, measure, 4) == ["あいうえお", "かきくけこ"]
    assert looks.wrap("one two three", 80, measure, 4) == ["one two", "three"]
    assert looks.wrap("abcdefghij", 50, measure, 4) == ["abcde", "fghij"]  # a word longer than a line


def test_wrap_ends_the_last_line_with_an_ellipsis_when_it_runs_out():
    assert looks.wrap("あいうえおかきくけこさしす", 50, measure, 2) == ["あいうえお", "かきくけ…"]
    assert looks.wrap("", 50, measure, 2) == []
