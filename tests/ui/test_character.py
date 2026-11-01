import math

import pytest

from lalein.ui.character import (
    AVATARS,
    CLICK_SLOP_PX,
    DEFAULT_AVATAR,
    LINGER_S,
    MIN_SHOW_S,
    NOTE_S,
    STATES,
    TEXT_MAX,
    Bubble,
    Line,
    Prefs,
    avatar_label,
    body_rgb,
    clamp_margins,
    drag_target,
    drop_on,
    is_click,
    margins_in,
    menu_entries,
    place_in,
    screen_at,
    menu_text,
    monitor_label,
    load_prefs,
    normalize,
    pose_for,
    save_prefs,
    update_prefs,
)


def test_every_state_has_a_pose_at_any_time():
    for state in STATES:
        for i in range(200):
            p = pose_for(state, i * 0.037)
            assert 0.0 <= p.eye_open <= 1.0
            assert 0.0 <= p.mouth_open <= 1.0
            assert 0.0 < p.alpha <= 1.0
            assert math.isfinite(p.body_dy) and math.isfinite(p.sway)


def test_unknown_or_missing_state_is_offline():
    assert normalize(None) == "offline"
    assert normalize("off") == "offline"  # what laleinctl prints when no daemon runs
    assert pose_for("bogus", 1.0) == pose_for("offline", 1.0)


def test_listening_blinks_then_reopens():
    assert pose_for("listening", 0.08).eye_open < 0.1
    assert pose_for("listening", 2.0).eye_open == 1.0


def test_speaking_moves_mouth_and_bounces():
    poses = [pose_for("speaking", i * 0.02) for i in range(100)]
    assert max(p.mouth_open for p in poses) - min(p.mouth_open for p in poses) > 0.5
    assert min(p.body_dy for p in poses) < -0.05
    assert all(p.dots == 0 for p in poses)


def test_thinking_sways_and_counts_dots():
    poses = [pose_for("thinking", i * 0.1) for i in range(40)]
    assert {p.dots for p in poses} == {0, 1, 2, 3}
    assert min(p.sway for p in poses) < 0 < max(p.sway for p in poses)


def test_paused_is_dim_asleep_with_z():
    p = pose_for("paused", 1.0)
    assert p.eye_open == 0.0 and p.tint == "dim" and p.alpha < 1.0 and p.zzz >= 0.0


@pytest.mark.parametrize("state,attr", [("limit", "sweat"), ("offline", "x_eyes")])
def test_limit_and_offline_markers(state, attr):
    assert getattr(pose_for(state, 0.5), attr) is True


# ---- hearing ----

def test_hearing_shows_waves_and_a_lit_antenna_while_awake():
    for state in ("listening", "thinking", "speaking", "limit"):
        p = pose_for(state, 0.3, hearing=True)
        assert 0.0 <= p.waves < 1.0 and p.glow and p.eye_open == 1.0
        assert pose_for(state, 0.3).waves < 0 and not pose_for(state, 0.3).glow


def test_hearing_is_not_shown_asleep_or_offline():
    for state in ("paused", "offline"):
        assert pose_for(state, 0.3, hearing=True) == pose_for(state, 0.3)


def test_waves_move_outward_over_time():
    phases = {round(pose_for("listening", i * 0.1, hearing=True).waves, 2) for i in range(20)}
    assert len(phases) > 5


# ---- bubble ----

def live_bubble():
    b = Bubble()
    b.feed({"kind": "hello", "boot": "b1"}, 0.0)
    return b


def test_history_before_live_is_never_shown():
    b = live_bubble()
    b.feed({"kind": "said", "text": "昔の返事", "what": "reply"}, 0.0)
    assert b.show("speaking", 0.1) is None
    b.feed({"kind": "live"}, 0.2)
    b.feed({"kind": "said", "text": "今の返事", "what": "reply"}, 0.3)
    assert b.show("speaking", 0.4) == Line("me", "今の返事")


def test_said_stays_while_speaking_then_lingers_and_goes():
    b = live_bubble()
    b.feed({"kind": "live"}, 0.0)
    b.feed({"kind": "said", "text": "明日はジムだよ。", "what": "reply"}, 1.0)
    for t in (1.5, 5.0, 9.0):
        assert b.show("speaking", t) is not None
    assert b.show("listening", 9.0 + LINGER_S - 0.1) is not None
    assert b.show("listening", 9.0 + LINGER_S + 0.1) is None
    assert b.show("listening", 99.0) is None  # gone for good


def test_said_shows_at_least_a_moment_even_if_speaking_was_never_seen():
    b = live_bubble()
    b.feed({"kind": "live"}, 0.0)
    b.feed({"kind": "said", "text": "うん。", "what": "filler"}, 10.0)
    assert b.show("listening", 10.0 + MIN_SHOW_S - 0.1) == Line("me", "うん。")
    assert b.show("listening", 10.0 + MIN_SHOW_S + 0.1) is None


def test_heard_shows_what_you_said_until_the_reply():
    b = live_bubble()
    b.feed({"kind": "live"}, 0.0)
    b.feed({"kind": "heard", "text": "明日の予定は？"}, 1.0)
    assert b.show("thinking", 20.0) == Line("you", "明日の予定は？")  # thinking keeps it
    b.feed({"kind": "said", "text": "ジムだよ。", "what": "reply"}, 21.0)
    assert b.show("speaking", 21.1) == Line("me", "ジムだよ。")


def test_speaking_again_clears_your_old_words_but_not_the_reply():
    b = live_bubble()
    b.feed({"kind": "live"}, 0.0)
    b.feed({"kind": "heard", "text": "一つ目"}, 1.0)
    b.feed({"kind": "hearing", "on": True}, 1.5)
    assert b.show("thinking", 1.6) is None
    b.feed({"kind": "said", "text": "返事", "what": "reply"}, 2.0)
    b.feed({"kind": "hearing", "on": True}, 2.5)
    assert b.show("speaking", 2.6) == Line("me", "返事")


def test_a_reconnect_starts_over():
    b = live_bubble()
    b.feed({"kind": "live"}, 0.0)
    b.feed({"kind": "said", "text": "返事", "what": "reply"}, 1.0)
    b.feed({"kind": "hello", "boot": "b2"}, 1.5)
    assert b.show("speaking", 1.6) is None
    b.feed({"kind": "said", "text": "履歴", "what": "reply"}, 1.7)
    assert b.show("speaking", 1.8) is None


def test_long_text_is_cut_and_whitespace_folded():
    b = live_bubble()
    b.feed({"kind": "live"}, 0.0)
    b.feed({"kind": "said", "text": "あ\n  い" + "う" * 500, "what": "reply"}, 1.0)
    line = b.show("speaking", 1.1)
    assert line.text.startswith("あ い") and len(line.text) == TEXT_MAX and line.text.endswith("…")


def test_a_note_shows_for_a_fixed_time_whatever_the_state():
    b = live_bubble()
    b.note("Speech bubble: off", 5.0)
    assert b.show("speaking", 5.0 + NOTE_S - 0.1) == Line("note", "Speech bubble: off")
    assert b.show("speaking", 5.0 + NOTE_S + 0.1) is None


# ---- moving it around ----

def test_small_moves_are_clicks():
    assert is_click(0, 0) and is_click(3, -4)
    assert not is_click(CLICK_SLOP_PX + 1, 0) and not is_click(0, -20)


def test_margins_keep_the_window_on_screen():
    assert clamp_margins(-50, 9999, (300, 290), (1920, 1080)) == (0, 1080 - 290)
    assert clamp_margins(100, 200, (300, 290), (1920, 1080)) == (100, 200)
    assert clamp_margins(100, 200, (300, 290), (200, 200)) == (0, 0)  # screen too small


def test_prefs_round_trip_and_survive_junk(tmp_path):
    path = tmp_path / "state" / "character.json"
    assert load_prefs(path) == Prefs()
    save_prefs(path, Prefs(right=120, bottom=40, bubble=False))
    assert load_prefs(path) == Prefs(right=120, bottom=40, bubble=False)
    path.write_text("{not json", encoding="utf-8")
    assert load_prefs(path) == Prefs()
    path.write_text('{"right": "x", "bottom": -5, "bubble": "yes", "extra": 1}', encoding="utf-8")
    assert load_prefs(path) == Prefs(bottom=0)


def test_prefs_keep_the_avatar_the_monitor_and_whether_it_is_hidden(tmp_path):
    path = tmp_path / "character.json"
    prefs = Prefs(right=5, bottom=6, bubble=True, avatar="cat", monitor="DP-2", hidden=True)
    save_prefs(path, prefs)
    assert load_prefs(path) == prefs
    path.write_text('{"right": 1, "bottom": 2, "bubble": false}', encoding="utf-8")  # an older file
    assert load_prefs(path) == Prefs(right=1, bottom=2, bubble=False, avatar=DEFAULT_AVATAR, monitor=None,
                                     hidden=False)
    path.write_text('{"avatar": "dragon", "monitor": 3, "hidden": "yes"}', encoding="utf-8")
    assert load_prefs(path) == Prefs()


def test_update_prefs_changes_only_what_it_is_given(tmp_path):
    path = tmp_path / "character.json"
    save_prefs(path, Prefs(right=120, bottom=40, avatar="ghost"))
    assert update_prefs(path, hidden=True) == Prefs(right=120, bottom=40, avatar="ghost", hidden=True)
    assert load_prefs(path) == Prefs(right=120, bottom=40, avatar="ghost", hidden=True)
    assert update_prefs(tmp_path / "new" / "character.json", monitor="HDMI-A-1") == Prefs(monitor="HDMI-A-1")


def test_the_first_avatar_is_the_original_and_there_are_others():
    assert AVATARS[0] == DEFAULT_AVATAR == "bot"
    assert len(AVATARS) >= 5 and len(set(AVATARS)) == len(AVATARS)


@pytest.mark.parametrize("avatar", AVATARS)
def test_every_avatar_has_a_colour_per_tint_and_a_name_per_language(avatar):
    colours = {tint: body_rgb(avatar, tint) for tint in ("normal", "dim", "warm", "grey")}
    assert all(len(c) == 3 and all(0.0 <= v <= 1.0 for v in c) for c in colours.values())
    assert len(set(colours.values())) == 4  # every state looks different
    assert avatar_label(avatar, "ja") and avatar_label(avatar, "en")


def test_avatars_differ_when_awake_and_look_alike_offline():
    assert len({body_rgb(a, "normal") for a in AVATARS}) == len(AVATARS)
    assert len({body_rgb(a, "grey") for a in AVATARS}) == 1
    assert body_rgb("dragon", "normal") == body_rgb(DEFAULT_AVATAR, "normal")  # unknown: the default


def test_dragging_follows_the_pointer_on_the_screen():
    # pointer moved 30 right, 10 down: the right / bottom margins shrink by as much
    assert drag_target((100, 200), (500.0, 400.0), (530.0, 410.0)) == (70.0, 190.0)
    assert drag_target((100, 200), (500.0, 400.0), (500.0, 400.0)) == (100.0, 200.0)


def test_bubble_note_follows_the_display_language():
    from lalein.ui.character import bubble_note

    assert bubble_note(True, "en") == "Speech bubble: on"
    assert bubble_note(False, "ja") == "吹き出し: オフ"
    assert bubble_note(True) == "吹き出し: オン"  # the tests' display language


def test_monitors_are_named_by_model_and_connector():
    assert monitor_label("DP-1", "MAG325CQRF-QD", "Microstep MAG325CQRF-QD (DP-1)") == "MAG325CQRF-QD (DP-1)"
    assert monitor_label("HDMI-A-1", "0x1920", "Invalid Vendor Codename - RTK 0x1920") == "HDMI-A-1"
    assert monitor_label("eDP-1", None, None) == "eDP-1"


def test_menu_follows_the_display_language():
    assert menu_text("menu.avatar", "en") == "Look"
    assert menu_text("menu.avatar") == "見た目"


# DP-1 2560x1440 at (1920, 0); DP-2 1080x1920 (portrait) at (840, 0), to its left
SCREENS = {"DP-1": (1920, 0, 2560, 1440), "DP-2": (840, 0, 1080, 1920)}


def test_dropping_on_the_same_monitor_follows_the_pointer():
    # 30 left and 10 up: the margins from the bottom-right corner grow by as much
    assert drop_on((100, 200), "DP-1", (3000.0, 900.0), (2970.0, 890.0), (300, 290), SCREENS) == ("DP-1", 130, 210)


def test_dropping_on_another_monitor_moves_it_there_under_the_pointer():
    # the window's top-left was at (1920 + 2560 - 100 - 300, 1440 - 200 - 290) = (4080, 950);
    # the pointer went 3000 left: (1080, 950) on DP-2 -> right 1920 - 1380, bottom 1920 - 1240
    assert drop_on((100, 200), "DP-1", (4100.0, 1000.0), (1100.0, 1000.0), (300, 290), SCREENS) == ("DP-2", 540, 680)


def test_a_drop_outside_every_monitor_stays_on_the_same_one_and_on_screen():
    assert drop_on((100, 200), "DP-1", (4100.0, 1000.0), (9999.0, 1000.0), (300, 290), SCREENS) == ("DP-1", 0, 200)
    assert drop_on((100, 200), "gone", (0.0, 0.0), (5.0, 5.0), (300, 290), SCREENS) is None


def test_place_in_and_margins_in_are_inverse_and_keep_it_on_screen():
    screen = (1920, 0, 2560, 1440)
    assert place_in(screen, 100, 200, (300, 290)) == (1920 + 2560 - 100 - 300, 1440 - 200 - 290)
    assert margins_in(screen, 1920 + 2560 - 100 - 300, 1440 - 200 - 290, (300, 290)) == (100, 200)
    assert place_in(screen, 99999, -5, (300, 290)) == (1920, 1440 - 290)
    assert margins_in(screen, 0, 5000, (300, 290)) == (2560 - 300, 0)


def test_screen_at_finds_the_screen_under_a_point_or_the_nearest():
    screens = {"A": (0, 0, 1920, 1080), "B": (1920, 0, 2560, 1440)}
    assert screen_at(screens, (100, 100)) == "A"
    assert screen_at(screens, (2000, 1200)) == "B"
    assert screen_at(screens, (5000, 100)) == "B"  # off every screen: the nearest
    assert screen_at({}, (0, 0)) is None


def test_menu_entries_are_the_right_click_menu_with_ticks():
    menu = menu_entries(Prefs(bubble=False, avatar="cat"), [("A", "Dell (A)"), ("B", "B")], "B", "en")
    assert [(e.label, e.action, e.checked) for e in menu if not e.children] == [
        ("Show the speech bubble", ("bubble",), False), ("Hide", ("hide",), False)]
    looks_menu, where = (e for e in menu if e.children)
    assert looks_menu.label == "Look" and len(looks_menu.children) == len(AVATARS)
    assert [c.action for c in looks_menu.children if c.checked] == [("avatar", "cat")]
    assert [(c.label, c.checked) for c in where.children] == [("Dell (A)", False), ("B", True)]
    one_screen = menu_entries(Prefs(), [("A", "A")], "A", "en")
    assert [e.label for e in one_screen if e.children] == ["Look"]  # no monitor submenu
