"""`zundamon-jarvisctl character`: a small animated character at the bottom-right of the screen.

GTK4 + gtk4-layer-shell + cairo + Pango (system packages: python-gobject, gtk4, gtk4-layer-shell,
python-cairo). The window is transparent, borderless and above everything; only the character
itself takes the pointer (the bubble area is click-through):

- drag it to move it (it stays where it is left: ``character.Prefs``); let go over another
  monitor and it moves there (on Hyprland, which tells where the pointer is),
- click it to open / close the conversation window (``zundamon-jarvisctl window watch``),
- right-click it for a menu: the speech bubble on / off, its look, the monitor it is on, and
  hiding it (all remembered too). Starting it again (the app launcher) brings a hidden one back.

``zundamon-jarvisctl character hide|show|toggle|avatar|monitor`` writes the same prefs file; the
overlay looks at the file every ``POLL_S`` and follows it.

The state comes from the daemon's ``status`` command on the control socket (the same source as
the bar); the state -> animation mapping is ``zundamon_jarvis.ui.character.pose_for``, and each
look is drawn by ``zundamon_jarvis.ui.looks``. The bubble
and the "I hear you" cue come from the control socket's ``watch`` stream
(``character.Bubble``; ``hearing`` events).

This module needs the *system* python (the venv has no ``gi``); ``zundamon-jarvisctl character`` starts it
that way (see ``zundamon_jarvis.ui.ctl.run_character``). Without layer-shell support it falls back to a plain
window, which a Hyprland window rule can float and pin.
"""

import json
import math
import os
import socket
import subprocess
import sys
import threading
import time
from dataclasses import replace

import cairo
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango, PangoCairo  # noqa: E402

try:
    gi.require_version("Gtk4LayerShell", "1.0")
    from gi.repository import Gtk4LayerShell as LayerShell  # noqa: E402
except (ValueError, ImportError):
    LayerShell = None

from zundamon_jarvis.ui.character import (  # noqa: E402
    AVATARS,
    BUBBLE_H,
    CHAR_H,
    CHAR_W,
    CHAR_X,
    CHAR_Y,
    HEIGHT,
    WIDTH,
    Bubble,
    Line,
    Prefs,
    avatar_label,
    bubble_note,
    clamp_margins,
    drag_target,
    drop_on,
    is_click,
    load_prefs,
    menu_text,
    monitor_label,
    normalize,
    pose_for,
    prefs_path,
    save_prefs,
    update_prefs,
)
from zundamon_jarvis.ui.looks import BUBBLE_STYLE, draw_character  # noqa: E402
from zundamon_jarvis.ui.overlay import POLL_S, EventStream, StatePoller  # noqa: E402

APP_ID = "dev.dyoshyy.zundamon_jarvis.character"
NAMESPACE = "zundamon-jarvis-character"
FPS_MS = 33
BUBBLE_FONT = "sans 11"
BUBBLE_PAD = 10
BUBBLE_LINES = 4
# The right-click menu caps its own height below its content (with a scrollbar) in a layer-shell
# window, so it is given room for its longest page: a submenu's "back" row and its items.
MENU_ROW_PX, MENU_PAD_PX = 34, 24



def _rounded(cr: cairo.Context, x: float, y: float, w: float, h: float, r: float) -> None:
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 1.5 * math.pi)
    cr.close_path()


def draw_bubble(cr: cairo.Context, line: Line) -> None:
    """The bubble just above the character's head, its tail pointing at the head."""
    fill, ink = BUBBLE_STYLE[line.who]
    layout = PangoCairo.create_layout(cr)
    layout.set_font_description(Pango.FontDescription.from_string(BUBBLE_FONT))
    layout.set_width((WIDTH - 2 * BUBBLE_PAD - 8) * Pango.SCALE)
    layout.set_wrap(Pango.WrapMode.WORD_CHAR)
    layout.set_ellipsize(Pango.EllipsizeMode.END)
    layout.set_height(-BUBBLE_LINES)
    layout.set_text(line.text, -1)
    _ink, logical = layout.get_pixel_extents()
    w = min(logical.width + 2 * BUBBLE_PAD, WIDTH - 4)
    h = min(logical.height + 2 * BUBBLE_PAD, BUBBLE_H + 2)
    x, bottom = WIDTH - 4 - w, CHAR_Y + 6
    y = bottom - h
    tail_x = min(max(CHAR_X + CHAR_W * 0.35, x + 18), x + w - 18)

    def outline() -> None:
        _rounded(cr, x, y, w, h, 12)
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
    cr.move_to(x + BUBBLE_PAD - logical.x, y + BUBBLE_PAD)
    PangoCairo.show_layout(cr, layout)


def hypr_cursor() -> tuple[float, float] | None:
    """The pointer in screen coordinates from Hyprland's IPC socket, or None elsewhere."""
    sig = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    if not sig:
        return None
    run_dir = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(0.2)
            s.connect(f"{run_dir}/hypr/{sig}/.socket.sock")
            s.sendall(b"j/cursorpos")
            data = b""
            while chunk := s.recv(4096):
                data += chunk
        pos = json.loads(data)
        return float(pos["x"]), float(pos["y"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def open_watch_window() -> None:
    """``zundamon-jarvisctl window watch`` (it toggles), without our GTK preload / PYTHONPATH."""
    ctl = os.environ.get("ZUNDAMON_JARVISCTL") or "zundamon-jarvisctl"
    env = {k: v for k, v in os.environ.items()
           if k not in ("LD_PRELOAD", "PYTHONPATH", "ZUNDAMON_JARVISCTL")}
    try:
        proc = subprocess.Popen([ctl, "window", "watch"], env=env, start_new_session=True,
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
    except OSError as e:
        print(f"zundamon-jarvisctl character: could not open the watch window: {e}",
              file=sys.stderr)
        return
    threading.Thread(target=proc.wait, daemon=True).start()  # reap it


def monitors(display: Gdk.Display) -> list[Gdk.Monitor]:
    model = display.get_monitors()
    return [model.get_item(i) for i in range(model.get_n_items())]


def _variant(s: str) -> GLib.Variant:
    return GLib.Variant.new_string(s)


def build_window(app: Gtk.Application, forced_state: str | None = None,
                 forced_avatar: str | None = None) -> Gtk.Window:
    poller = None if forced_state else StatePoller()
    bubble = Bubble()
    hearing = {"on": False}
    path = prefs_path()
    prefs = load_prefs(path)

    def on_event(ev: dict) -> None:
        if ev.get("kind") == "hearing":
            hearing["on"] = bool(ev.get("on"))
        elif ev.get("kind") == "hello":
            hearing["on"] = False
        bubble.feed(ev, time.monotonic())

    if not forced_state:  # its events arrive on its thread: handle them on GTK's
        EventStream(lambda ev: GLib.idle_add(lambda: on_event(ev) and False))

    win = Gtk.ApplicationWindow(application=app)
    win.set_title("ZundamonJarvis character")
    win.set_decorated(False)
    win.set_default_size(WIDTH, HEIGHT)
    css = Gtk.CssProvider()
    css.load_from_string("window, window.background { background: transparent; }")
    Gtk.StyleContext.add_provider_for_display(
        win.get_display(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    area = Gtk.DrawingArea()
    area.set_content_width(WIDTH)
    area.set_content_height(HEIGHT)
    area.set_cursor(Gdk.Cursor.new_from_name("pointer"))
    start = time.monotonic()

    def on_draw(_a, cr, _w, _h) -> None:
        now = time.monotonic()
        state = ("listening" if forced_state == "hearing" else forced_state) or poller.state
        line = bubble.show(state, now)
        if line is not None and (prefs.bubble or line.who == "note"):
            draw_bubble(cr, line)
        cr.save()
        cr.translate(CHAR_X, CHAR_Y)
        draw_character(cr, CHAR_W, CHAR_H,
                       pose_for(state, now - start, hearing=hearing["on"] or forced_state == "hearing"),
                       forced_avatar or prefs.avatar)
        cr.restore()

    area.set_draw_func(on_draw)
    win.set_child(area)
    GLib.timeout_add(FPS_MS, lambda: (area.queue_draw(), True)[1])

    def input_on_character(_w) -> None:
        surface = win.get_surface()
        if surface is not None:  # everything but the character is click-through
            surface.set_input_region(cairo.Region(cairo.RectangleInt(CHAR_X, CHAR_Y, CHAR_W, CHAR_H)))

    win.connect("realize", input_on_character)

    layered = LayerShell is not None and LayerShell.is_supported()
    if layered:
        LayerShell.init_for_window(win)
        LayerShell.set_namespace(win, NAMESPACE)
        LayerShell.set_layer(win, LayerShell.Layer.OVERLAY)
        LayerShell.set_keyboard_mode(win, LayerShell.KeyboardMode.NONE)
        LayerShell.set_exclusive_zone(win, -1)
        for edge in (LayerShell.Edge.BOTTOM, LayerShell.Edge.RIGHT):
            LayerShell.set_anchor(win, edge, True)

    def chosen_monitor() -> Gdk.Monitor | None:
        """The monitor the prefs name, if it is connected."""
        return next((m for m in monitors(win.get_display()) if m.get_connector() == prefs.monitor), None)

    def current_monitor() -> Gdk.Monitor | None:
        surface = win.get_surface()
        return (layered and chosen_monitor()) or (surface and win.get_display().get_monitor_at_surface(surface))

    def screen() -> tuple[int, int] | None:
        monitor = current_monitor()
        if not monitor:
            return None
        geo = monitor.get_geometry()
        return geo.width, geo.height

    def place(right: float, bottom: float) -> tuple[int, int]:
        size = screen()
        r, b = clamp_margins(right, bottom, (WIDTH, HEIGHT), size) if size else (int(right), int(bottom))
        if layered:
            LayerShell.set_margin(win, LayerShell.Edge.RIGHT, r)
            LayerShell.set_margin(win, LayerShell.Edge.BOTTOM, b)
        return r, b

    if layered:
        LayerShell.set_monitor(win, chosen_monitor())
    place(prefs.right, prefs.bottom)
    win.connect("map", lambda _w: place(prefs.right, prefs.bottom))  # clamp once the monitor is known

    # the prefs: changed here (drag, menu) or in the file (zundamon-jarvisctl character ...)
    actions = Gio.SimpleActionGroup()
    bubble_action = Gio.SimpleAction.new_stateful("bubble", None, GLib.Variant.new_boolean(prefs.bubble))
    avatar_action = Gio.SimpleAction.new_stateful("avatar", GLib.VariantType.new("s"), _variant(prefs.avatar))
    monitor_action = Gio.SimpleAction.new_stateful("monitor", GLib.VariantType.new("s"),
                                                   _variant(prefs.monitor or ""))
    hide_action = Gio.SimpleAction.new("hide", None)
    for action in (bubble_action, avatar_action, monitor_action, hide_action):
        actions.add_action(action)
    area.insert_action_group("character", actions)

    def apply(new: Prefs) -> None:
        nonlocal prefs
        old, prefs = prefs, new
        if layered and new.monitor != old.monitor:
            LayerShell.set_monitor(win, chosen_monitor())
        if (new.right, new.bottom, new.monitor) != (old.right, old.bottom, old.monitor):
            place(new.right, new.bottom)
        if new.bubble != old.bubble:
            bubble.note(bubble_note(new.bubble), time.monotonic())
        if new.hidden != old.hidden and not forced_state:  # a preview stays up
            win.set_visible(not new.hidden)
        bubble_action.set_state(GLib.Variant.new_boolean(new.bubble))
        avatar_action.set_state(_variant(new.avatar))
        monitor_action.set_state(_variant(new.monitor or ""))

    def stat() -> tuple[int, int] | None:
        try:
            st = path.stat()
        except OSError:
            return None
        return st.st_mtime_ns, st.st_size

    seen = {"stat": stat()}

    def change(**changes) -> None:
        new = replace(prefs, **changes)
        save_prefs(path, new)
        seen["stat"] = stat()
        apply(new)

    def follow_file() -> bool:
        if (now := stat()) != seen["stat"]:
            seen["stat"] = now
            apply(load_prefs(path))
        return True

    GLib.timeout_add(int(POLL_S * 1000), follow_file)
    bubble_action.connect("change-state", lambda _a, v: change(bubble=v.get_boolean()))
    avatar_action.connect("change-state", lambda _a, v: change(avatar=v.get_string()))
    monitor_action.connect("change-state", lambda _a, v: change(monitor=v.get_string() or None))
    hide_action.connect("activate", lambda _a, _v: change(hidden=True))

    # drag = move, click = watch window. On Hyprland the pointer is read in screen coordinates
    # (``drag_target``). Elsewhere the gesture's offsets, which are relative to the moving
    # window: the pointer's movement is the offset plus how far the window has moved (this
    # lags behind the compositor, so it wobbles a little).
    drag = Gtk.GestureDrag(button=Gdk.BUTTON_PRIMARY)
    moving = {"start": (0, 0), "pointer": None, "moved": (0, 0), "dragged": False}

    def total(ox: float, oy: float) -> tuple[float, float]:
        if moving["pointer"] is not None and (now := hypr_cursor()) is not None:
            return now[0] - moving["pointer"][0], now[1] - moving["pointer"][1]
        return ox + moving["moved"][0], oy + moving["moved"][1]

    def on_begin(_g, _x, _y) -> None:
        moving.update(start=(prefs.right, prefs.bottom), pointer=hypr_cursor(), moved=(0, 0),
                      dragged=False)

    def on_update(_g, ox: float, oy: float) -> None:
        dx, dy = total(ox, oy)
        if not moving["dragged"] and is_click(dx, dy):
            return
        if not moving["dragged"]:
            moving["dragged"] = True
            area.set_cursor(Gdk.Cursor.new_from_name("grabbing"))
        r0, b0 = moving["start"]
        r, b = place(*drag_target((r0, b0), (0.0, 0.0), (dx, dy)))
        moving["moved"] = (r0 - r, b0 - b)

    def on_end(_g, ox: float, oy: float) -> None:
        area.set_cursor(Gdk.Cursor.new_from_name("pointer"))
        if not moving["dragged"]:
            open_watch_window()
            return
        r0, b0 = moving["start"]
        mx, my = moving["moved"]
        on = current_monitor()
        pointer = moving["pointer"] is not None and layered and on and hypr_cursor()
        if pointer:  # let go over another monitor: it moves there (the layer surface can't span two)
            rects = {}
            for m in monitors(win.get_display()):
                g = m.get_geometry()
                rects[m.get_connector()] = (g.x, g.y, g.width, g.height)
            if dropped := drop_on((r0, b0), on.get_connector(), moving["pointer"], pointer, (WIDTH, HEIGHT), rects):
                name, r, b = dropped
                change(right=r, bottom=b, monitor=name)
                return
        change(right=int(r0 - mx), bottom=int(b0 - my))

    drag.connect("drag-begin", on_begin)
    drag.connect("drag-update", on_update)
    drag.connect("drag-end", on_end)
    area.add_controller(drag)

    right_click = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)

    def menu_model() -> Gio.Menu:
        menu = Gio.Menu()
        menu.append(menu_text("menu.bubble"), "character.bubble")
        looks = Gio.Menu()
        for avatar in AVATARS:
            item = Gio.MenuItem.new(avatar_label(avatar), None)
            item.set_action_and_target_value("character.avatar", _variant(avatar))
            looks.append_item(item)
        menu.append_submenu(menu_text("menu.avatar"), looks)
        screens = monitors(win.get_display())
        if layered and len(screens) > 1:
            where = Gio.Menu()
            for m in screens:
                item = Gio.MenuItem.new(monitor_label(m.get_connector(), m.get_model(), m.get_description()), None)
                item.set_action_and_target_value("character.monitor", _variant(m.get_connector()))
                where.append_item(item)
            menu.append_submenu(menu_text("menu.monitor"), where)
        end = Gio.Menu()
        end.append(menu_text("menu.hide"), "character.hide")
        menu.append_section(None, end)
        return menu

    popover = Gtk.PopoverMenu.new_from_model(None)
    popover.set_parent(area)
    popover.set_has_arrow(False)
    # the menu needs the keyboard while it is open (layer-shell: none otherwise)
    popover.connect("closed", lambda _p: layered and LayerShell.set_keyboard_mode(
        win, LayerShell.KeyboardMode.NONE))

    def on_right_click(_g, _n, x: float, y: float) -> None:
        on = current_monitor()  # tick where it is now, even when the compositor chose
        monitor_action.set_state(_variant(prefs.monitor or (on.get_connector() if on else "")))
        popover.set_menu_model(menu_model())
        rows = max(4, 1 + len(AVATARS), 1 + len(monitors(win.get_display())))
        popover.set_size_request(-1, rows * MENU_ROW_PX + MENU_PAD_PX)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        popover.set_pointing_to(rect)
        if layered:
            LayerShell.set_keyboard_mode(win, LayerShell.KeyboardMode.ON_DEMAND)
        popover.popup()

    right_click.connect("released", on_right_click)
    area.add_controller(right_click)
    return win


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    # preview a state (or "hearing": listening with the hearing cue), optionally in a look
    forced = args[0] if args and (normalize(args[0]) == args[0] or args[0] == "hearing") else None
    look = args[1] if forced and len(args) > 1 and args[1] in AVATARS else None
    # One character: launching it again (the app launcher) just brings it back to the front.
    # A preview is its own instance, next to a running character.
    app = Gtk.Application(application_id=APP_ID, flags=Gio.ApplicationFlags.NON_UNIQUE
                          if forced else Gio.ApplicationFlags.DEFAULT_FLAGS)

    def activate(a: Gtk.Application) -> None:
        if not forced and load_prefs(prefs_path()).hidden:  # started again: come back
            update_prefs(prefs_path(), hidden=False)
        (a.get_windows() or [build_window(a, forced, look)])[0].present()

    app.connect("activate", activate)
    return app.run([])


if __name__ == "__main__":
    sys.exit(main())
