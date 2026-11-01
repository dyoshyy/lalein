"""The on-screen character on macOS: ``laleinctl character`` (or the menu bar's item).

Two borderless, transparent windows above the others and on every Space (AppKit through PyObjC,
no Dock icon): one for the speech bubble, which ignores the mouse, and one for the character,
whose transparent pixels let clicks through. They move together as the overlay's WIDTH x HEIGHT
box (``character``'s layout), placed by the prefs' margins from the bottom-right corner of a
screen's visible frame (above the Dock), in points. Both views draw through
``quartz_canvas.QuartzCanvas`` (pycairo has no macOS wheels).

- drag the character to move it, onto another screen too,
- click it to open / close the conversation window,
- right-click (or control-click) it for the menu (``character.menu_entries``).

macOS's screen coordinates grow upwards from the main screen's bottom-left corner; the shared
placement helpers (``character.place_in`` / ``margins_in``) work downwards, so ``_down`` turns
one into the other. What it shows and the prefs come from ``overlay.Overlay``. AppKit is
imported in ``main`` only (the tests import this module on any OS).
"""

import sys
import threading
from typing import Any

from lalein.ui.character import (
    AVATARS,
    CHAR_H,
    CHAR_W,
    CHAR_X,
    HEIGHT,
    WIDTH,
    Prefs,
    Rect,
    is_click,
    margins_in,
    menu_entries,
    normalize,
    place_in,
    screen_at,
)
from lalein.ui.looks import draw_bubble, draw_character
from lalein.ui.overlay import POLL_S, Overlay, claim
from lalein.ui.quartz_canvas import QuartzCanvas

FPS_S = 1 / 30
BUBBLE_FONT = ""  # the system font, which finds Japanese glyphs too


def flip(top: float, height: float, main_height: float) -> float:
    """A y between the shared (downwards, top edge) and macOS (upwards, bottom edge) frames;
    the same both ways."""
    return main_height - top - height


def _build(AppKit: Any, Foundation: Any) -> Any:
    """The AppKit classes, made once AppKit is imported."""
    class OverlayView(AppKit.NSView):
        """Draws one part of the overlay (``paint``) and passes the mouse to ``owner``."""

        def isFlipped(self) -> bool:  # y down, like cairo
            return True

        def acceptsFirstMouse_(self, _event: Any) -> bool:  # a click works without activating first
            return True

        def drawRect_(self, _rect: Any) -> None:
            ctx = AppKit.NSGraphicsContext.currentContext().CGContext()
            self.paint(QuartzCanvas(ctx))

        def mouseDown_(self, event: Any) -> None:
            if event.modifierFlags() & AppKit.NSEventModifierFlagControl:
                self.owner.menu(event, self)
            else:
                self.owner.press()

        def mouseDragged_(self, _event: Any) -> None:
            self.owner.drag()

        def mouseUp_(self, _event: Any) -> None:
            self.owner.release()

        def rightMouseDown_(self, event: Any) -> None:
            self.owner.menu(event, self)

    class MenuTarget(Foundation.NSObject):
        def choose_(self, item: Any) -> None:
            self.owner.overlay.choose(tuple(item.representedObject()))

        def tick_(self, _timer: Any) -> None:
            self.owner.tick()

    return OverlayView, MenuTarget


class CharacterWindows:
    """The bubble and character windows, their drawing, and the mouse."""

    def __init__(self, AppKit: Any, Foundation: Any, overlay: Overlay) -> None:
        self.ak, self.overlay = AppKit, overlay
        OverlayView, MenuTarget = _build(AppKit, Foundation)
        self.target = MenuTarget.alloc().init()
        self.target.owner = self
        self._pose: tuple = (None, None, None)
        self.bubble, self.bubble_view = self._window(OverlayView, WIDTH, HEIGHT, self._paint_bubble, mouse=False)
        self.char, self.char_view = self._window(OverlayView, CHAR_W, CHAR_H, self._paint_char, mouse=True)
        self.box = (0.0, 0.0)  # the box's top-left, shared (downwards) coordinates
        self._drag: dict | None = None
        self._ticks = 0
        overlay.on_change = self._on_change
        self.place()
        if not overlay.prefs.hidden or overlay.forced_state:
            self._show(True)
        Foundation.NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            FPS_S, self.target, "tick:", None, True)

    def _window(self, view_class: Any, w: int, h: int, paint: Any, mouse: bool) -> tuple[Any, Any]:
        ak = self.ak
        win = ak.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            ((0, 0), (w, h)), ak.NSWindowStyleMaskBorderless, ak.NSBackingStoreBuffered, False)
        win.setOpaque_(False)
        win.setBackgroundColor_(ak.NSColor.clearColor())
        win.setHasShadow_(False)
        win.setLevel_(ak.NSStatusWindowLevel)
        win.setIgnoresMouseEvents_(not mouse)
        win.setCollectionBehavior_(ak.NSWindowCollectionBehaviorCanJoinAllSpaces
                                   | ak.NSWindowCollectionBehaviorStationary
                                   | ak.NSWindowCollectionBehaviorIgnoresCycle
                                   | ak.NSWindowCollectionBehaviorFullScreenAuxiliary)
        win.setReleasedWhenClosed_(False)
        view = view_class.alloc().initWithFrame_(((0, 0), (w, h)))
        view.paint = paint
        view.owner = self
        win.setContentView_(view)
        return win, view

    # ---- screens and placement ----

    def _main_height(self) -> float:
        return float(self.ak.NSScreen.screens()[0].frame().size.height)

    def screens(self) -> dict[str, Rect]:
        """Every screen's visible frame by name, in shared (downwards) coordinates."""
        main_h = self._main_height()
        found: dict[str, Rect] = {}
        for screen in self.ak.NSScreen.screens():
            f = screen.visibleFrame()
            name = str(screen.localizedName()) if hasattr(screen, "localizedName") else str(len(found) + 1)
            found[name] = (int(f.origin.x), int(flip(f.origin.y, f.size.height, main_h)),
                           int(f.size.width), int(f.size.height))
        return found

    def place(self) -> None:
        screens = self.screens()
        prefs = self.overlay.prefs
        name = prefs.monitor if prefs.monitor in screens else next(iter(screens), None)
        if name is not None:
            self.box = place_in(screens[name], prefs.right, prefs.bottom, (WIDTH, HEIGHT))
            self._move()

    def _move(self) -> None:
        x, top = self.box
        y = flip(top, HEIGHT, self._main_height())  # the box's bottom edge, upwards
        self.bubble.setFrameOrigin_((x, y))
        self.char.setFrameOrigin_((x + CHAR_X, y))  # the character sits at the box's bottom

    def _show(self, on: bool) -> None:
        for win in (self.bubble, self.char):
            win.orderFrontRegardless() if on else win.orderOut_(None)

    def _on_change(self, old: Prefs, new: Prefs) -> None:
        if (new.right, new.bottom, new.monitor) != (old.right, old.bottom, old.monitor):
            self.place()
        if new.hidden != old.hidden and not self.overlay.forced_state:
            self._show(not new.hidden)

    # ---- drawing ----

    def _paint_bubble(self, cr: Any) -> None:
        _pose, line, _avatar = self._pose
        if line is not None:
            draw_bubble(cr, line, BUBBLE_FONT)

    def _paint_char(self, cr: Any) -> None:
        pose, _line, avatar = self._pose
        if pose is not None:
            draw_character(cr, CHAR_W, CHAR_H, pose, avatar)

    def tick(self) -> None:
        self._ticks += 1
        if self._ticks % max(int(POLL_S / FPS_S), 1) == 0:
            self.overlay.follow_file()
        if self.overlay.prefs.hidden and not self.overlay.forced_state:
            return
        old_line = self._pose[1]
        self._pose = self.overlay.frame()
        self.char_view.setNeedsDisplay_(True)
        if self._pose[1] != old_line:
            self.bubble_view.setNeedsDisplay_(True)

    # ---- the mouse ----

    def _pointer(self) -> tuple[float, float]:
        p = self.ak.NSEvent.mouseLocation()
        return float(p.x), float(-p.y)  # downwards, like the box (only differences are used)

    def press(self) -> None:
        self._drag = {"pointer": self._pointer(), "box": self.box, "moved": False}

    def drag(self) -> None:
        if self._drag is None:
            return
        (px, py), (bx, by) = self._drag["pointer"], self._drag["box"]
        cx, cy = self._pointer()
        if self._drag["moved"] or not is_click(cx - px, cy - py):
            self._drag["moved"] = True
            self.box = (bx + cx - px, by + cy - py)
            self._move()

    def release(self) -> None:
        drag, self._drag = self._drag, None
        if drag is None:
            return
        if not drag["moved"]:
            from lalein.ui import ctl

            threading.Thread(target=ctl.open_window, args=("watch",), daemon=True).start()
            return
        screens = self.screens()
        name = screen_at(screens, (self.box[0] + WIDTH / 2, self.box[1] + HEIGHT / 2))
        if name is not None:
            right, bottom = margins_in(screens[name], self.box[0], self.box[1], (WIDTH, HEIGHT))
            self.overlay.change(right=right, bottom=bottom, monitor=name)

    def menu(self, event: Any, view: Any) -> None:
        self.ak.NSMenu.popUpContextMenu_withEvent_forView_(self.build_menu(), event, view)

    def build_menu(self) -> Any:
        """The right-click menu as an NSMenu."""
        ak = self.ak
        screens = self.screens()
        on = screen_at(screens, (self.box[0] + WIDTH / 2, self.box[1] + HEIGHT / 2))

        def build(entries: Any) -> Any:
            menu = ak.NSMenu.alloc().initWithTitle_("")
            menu.setAutoenablesItems_(False)
            for entry in entries:
                if entry.action == ("hide",):
                    menu.addItem_(ak.NSMenuItem.separatorItem())
                item = ak.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                    entry.label, None if entry.children else "choose:", "")
                if entry.children:
                    item.setSubmenu_(build(entry.children))
                else:
                    item.setTarget_(self.target)
                    item.setRepresentedObject_(list(entry.action))
                    item.setState_(ak.NSControlStateValueOn if entry.checked else ak.NSControlStateValueOff)
                menu.addItem_(item)
            return menu

        return build(menu_entries(self.overlay.prefs, [(n, n) for n in screens], on))

    def close(self) -> None:
        for win in (self.bubble, self.char):
            win.orderOut_(None)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    forced = args[0] if args and (normalize(args[0]) == args[0] or args[0] == "hearing") else None
    look = args[1] if forced and len(args) > 1 and args[1] in AVATARS else None
    lock = claim(forced)
    if lock is None:
        return 0  # already running: it shows itself (the prefs say so)
    import AppKit
    import Foundation

    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)  # no Dock icon
    windows = CharacterWindows(AppKit, Foundation, Overlay(forced, look))  # noqa: F841 (kept alive)
    app.run()
    return 0
