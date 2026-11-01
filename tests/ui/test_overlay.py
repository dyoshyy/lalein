from lalein.ui.character import Prefs, load_prefs, save_prefs, update_prefs
from lalein.ui.overlay import Overlay


def make(tmp_path, **kw):
    seen = []
    o = Overlay(path=tmp_path / "character.json", live=False, **kw)
    o.on_change = lambda old, new: seen.append((old, new))
    return o, seen


def test_a_preview_shows_its_state_in_its_look(tmp_path):
    o, _ = make(tmp_path, forced_state="speaking", forced_avatar="cat")
    pose, line, avatar = o.frame(1.0)
    assert pose.mouth_open > 0 and line is None and avatar == "cat"
    o, _ = make(tmp_path, forced_state="hearing")
    assert o.frame(1.0)[0].glow


def test_without_a_daemon_it_is_offline_in_the_remembered_look(tmp_path):
    save_prefs(tmp_path / "character.json", Prefs(avatar="ghost"))
    o, _ = make(tmp_path)
    pose, _line, avatar = o.frame(1.0)
    assert pose.x_eyes and avatar == "ghost"


def test_choosing_from_the_menu_changes_and_saves_the_prefs(tmp_path):
    o, seen = make(tmp_path)
    o.choose(("avatar", "robo"))
    o.choose(("monitor", "DP-2"))
    o.choose(("bubble",))
    o.choose(("hide",))
    assert o.prefs == Prefs(avatar="robo", monitor="DP-2", bubble=False, hidden=True)
    assert load_prefs(tmp_path / "character.json") == o.prefs
    assert [new for _old, new in seen][-1] == o.prefs and len(seen) == 4


def test_switching_the_bubble_shows_a_note_even_with_the_bubble_off(tmp_path):
    o, _ = make(tmp_path)
    o.choose(("bubble",))
    _pose, line, _avatar = o.frame()
    assert line is not None and line.who == "note"


def test_it_follows_changes_made_to_the_file_by_others(tmp_path):
    o, seen = make(tmp_path)
    o.follow_file()
    assert seen == []
    update_prefs(tmp_path / "character.json", hidden=True, avatar="onigiri")
    o.follow_file()
    assert o.prefs.hidden and o.prefs.avatar == "onigiri" and len(seen) == 1
    o.follow_file()
    assert len(seen) == 1


def test_only_live_events_reach_the_bubble_and_a_reconnect_clears_it(tmp_path):
    now = [100.0]
    o = Overlay(path=tmp_path / "character.json", live=False, clock=lambda: now[0])
    o.on_event({"kind": "said", "text": "old"})
    assert o.frame()[1] is None  # history replayed before "live"
    o.on_event({"kind": "live"})
    o.on_event({"kind": "hearing", "on": True})
    assert o.hearing
    o.on_event({"kind": "heard", "text": "hello"})
    assert o.frame()[1].text == "hello"
    o.on_event({"kind": "hello"})
    assert o.frame()[1] is None and not o.hearing
