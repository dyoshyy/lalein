"""The Windows and macOS character windows on the real OS (CI runs them there): they open,
draw a frame, find the monitors and build the right-click menu. No input is sent."""

import sys

import pytest

from zundamon_jarvis.ui.overlay import Overlay


def preview(tmp_path, state="speaking", avatar="robo"):
    return Overlay(forced_state=state, forced_avatar=avatar, path=tmp_path / "character.json", live=False)


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 windows")
def test_windows_overlay_opens_draws_and_moves(tmp_path):
    from zundamon_jarvis.ui import character_win

    overlay = preview(tmp_path)
    windows = character_win.CharacterWindows(character_win._win32(), overlay)
    try:
        screens = windows.screens()
        assert screens and all(w > 0 and h > 0 for _x, _y, w, h in screens.values())
        windows.frame()
        overlay.choose(("bubble",))  # a note in the bubble: the bubble window draws too
        windows.frame()
        name = next(iter(screens))
        overlay.change(right=50, bottom=60, monitor=name)  # on_change places the windows
        x, y, w, h = screens[name]
        bw, bh = windows.size()
        assert windows.box == (x + w - 50 - bw, y + h - 60 - bh)
    finally:
        windows.close()


@pytest.mark.skipif(sys.platform != "darwin", reason="AppKit windows")
def test_macos_overlay_opens_draws_and_builds_its_menu(tmp_path):
    import AppKit
    import Foundation

    from zundamon_jarvis.ui import character_mac

    AppKit.NSApplication.sharedApplication()
    overlay = preview(tmp_path, avatar="cat")
    windows = character_mac.CharacterWindows(AppKit, Foundation, overlay)
    try:
        screens = windows.screens()
        assert screens
        overlay.choose(("bubble",))
        windows.tick()
        windows.char_view.display()  # drawRect_ now, through the Quartz canvas
        windows.bubble_view.display()
        menu = windows.build_menu()
        titles = [menu.itemAtIndex_(i).title() for i in range(menu.numberOfItems())]
        assert "見た目" in titles or "Look" in titles
        name = next(iter(screens))
        overlay.change(right=50, bottom=60, monitor=name)
        x, y, w, h = screens[name]
        assert windows.box == (x + w - 50 - 300, y + h - 60 - 290)
    finally:
        windows.close()
