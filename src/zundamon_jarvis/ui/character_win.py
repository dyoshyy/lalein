"""The on-screen character on Windows: ``zundamon-jarvisctl character`` (or the tray's item).

Two borderless, always-on-top layered windows (Win32 through ctypes; drawn with pycairo, which
has Windows wheels): one for the speech bubble, which lets every click through, and one for the
character itself, whose fully transparent pixels let clicks through too. They move together as
the overlay's WIDTH x HEIGHT box (``character``'s layout), placed by the prefs' margins from the
bottom-right corner of a monitor's work area (so above the taskbar), in physical pixels and
scaled to the monitor's DPI.

- drag the character to move it, onto another monitor too (a window can cross them here),
- click it to open / close the conversation window,
- right-click it for the menu (``character.menu_entries``): the bubble, its look, its monitor,
  hiding it.

What it shows and the prefs come from ``overlay.Overlay``; ``zundamon-jarvisctl character
hide|show|...`` changes the prefs file and this follows. Nothing here runs at import (the tests
import it on any OS): the Win32 functions are declared in ``_win32``.
"""

import ctypes
import sys
import threading
from ctypes import wintypes
from typing import Any

from zundamon_jarvis.ui.character import (
    AVATARS,
    CHAR_H,
    CHAR_W,
    CHAR_X,
    CHAR_Y,
    HEIGHT,
    WIDTH,
    MenuEntry,
    Prefs,
    Rect,
    is_click,
    margins_in,
    menu_entries,
    normalize,
    place_in,
    screen_at,
)
from zundamon_jarvis.ui.looks import draw_bubble, draw_character
from zundamon_jarvis.ui.overlay import POLL_S, Overlay, claim

CLASS_NAME = "ZundamonJarvisCharacter"
FPS_MS = 33
BUBBLE_FONT = "Yu Gothic UI"

WS_POPUP = 0x80000000
WS_EX_LAYERED, WS_EX_TRANSPARENT, WS_EX_TOPMOST = 0x80000, 0x20, 0x8
WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = 0x80, 0x8000000
WM_DESTROY, WM_TIMER, WM_DISPLAYCHANGE = 0x0002, 0x0113, 0x007E
WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP, WM_RBUTTONUP = 0x0200, 0x0201, 0x0202, 0x0205
WM_MOUSEACTIVATE, MA_NOACTIVATE = 0x0021, 3
SW_HIDE, SW_SHOWNOACTIVATE = 0, 4
SWP_NOSIZE, SWP_NOZORDER, SWP_NOACTIVATE = 0x1, 0x4, 0x10
ULW_ALPHA, AC_SRC_OVER, AC_SRC_ALPHA = 0x2, 0x0, 0x1
BI_RGB, DIB_RGB_COLORS = 0, 0
MF_STRING, MF_POPUP, MF_CHECKED, MF_SEPARATOR = 0x0, 0x10, 0x8, 0x800
TPM_RETURNCMD, TPM_RIGHTBUTTON = 0x100, 0x2
MONITOR_DEFAULTTONEAREST = 2
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4

LRESULT = ctypes.c_ssize_t


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class SIZE(ctypes.Structure):
    _fields_ = [("cx", wintypes.LONG), ("cy", wintypes.LONG)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
                ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD), ("szDevice", wintypes.WCHAR * 32)]


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("style", wintypes.UINT), ("lpfnWndProc", ctypes.c_void_p),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR),
                ("hIconSm", wintypes.HICON)]


def device_name(device: str) -> str:
    r"""``\\.\DISPLAY1`` -> ``DISPLAY1``: how the prefs and the menu name a monitor."""
    return device.removeprefix("\\\\.\\")


def _win32() -> Any:
    """user32 / gdi32 / shcore with the prototypes this module calls (64-bit handles)."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32")
    kernel32 = ctypes.WinDLL("kernel32")
    shcore = ctypes.WinDLL("shcore")
    H, P = wintypes.HANDLE, ctypes.c_void_p
    protos = {
        user32: {
            "DefWindowProcW": (LRESULT, [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]),
            "RegisterClassExW": (wintypes.ATOM, [P]),
            "CreateWindowExW": (wintypes.HWND, [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                                ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                                wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, P]),
            "ShowWindow": (wintypes.BOOL, [wintypes.HWND, ctypes.c_int]),
            "DestroyWindow": (wintypes.BOOL, [wintypes.HWND]),
            "KillTimer": (wintypes.BOOL, [wintypes.HWND, ctypes.c_size_t]),
            "SetWindowPos": (wintypes.BOOL, [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                             ctypes.c_int, ctypes.c_int, wintypes.UINT]),
            "UpdateLayeredWindow": (wintypes.BOOL, [wintypes.HWND, H, P, P, H, P, wintypes.DWORD, P, wintypes.DWORD]),
            "GetDC": (H, [wintypes.HWND]),
            "ReleaseDC": (ctypes.c_int, [wintypes.HWND, H]),
            "SetTimer": (ctypes.c_size_t, [wintypes.HWND, ctypes.c_size_t, wintypes.UINT, P]),
            "GetMessageW": (wintypes.BOOL, [P, wintypes.HWND, wintypes.UINT, wintypes.UINT]),
            "TranslateMessage": (wintypes.BOOL, [P]),
            "DispatchMessageW": (LRESULT, [P]),
            "PostQuitMessage": (None, [ctypes.c_int]),
            "PostMessageW": (wintypes.BOOL, [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]),
            "SetCapture": (wintypes.HWND, [wintypes.HWND]),
            "ReleaseCapture": (wintypes.BOOL, []),
            "GetCursorPos": (wintypes.BOOL, [P]),
            "LoadCursorW": (H, [wintypes.HINSTANCE, P]),
            "CreatePopupMenu": (wintypes.HMENU, []),
            "AppendMenuW": (wintypes.BOOL, [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR]),
            "TrackPopupMenu": (ctypes.c_int, [wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int,
                                              ctypes.c_int, wintypes.HWND, P]),
            "DestroyMenu": (wintypes.BOOL, [wintypes.HMENU]),
            "SetForegroundWindow": (wintypes.BOOL, [wintypes.HWND]),
            "EnumDisplayMonitors": (wintypes.BOOL, [H, P, P, wintypes.LPARAM]),
            "GetMonitorInfoW": (wintypes.BOOL, [H, P]),
            "MonitorFromPoint": (H, [POINT, wintypes.DWORD]),
            "SetProcessDpiAwarenessContext": (wintypes.BOOL, [P]),
        },
        kernel32: {"GetModuleHandleW": (wintypes.HMODULE, [wintypes.LPCWSTR])},
        shcore: {"GetDpiForMonitor": (ctypes.c_long, [H, ctypes.c_int, P, P])},
        gdi32: {
            "CreateCompatibleDC": (H, [H]),
            "DeleteDC": (wintypes.BOOL, [H]),
            "CreateDIBSection": (H, [H, P, wintypes.UINT, P, H, wintypes.DWORD]),
            "SelectObject": (H, [H, H]),
            "DeleteObject": (wintypes.BOOL, [H]),
        },
    }
    for dll, fns in protos.items():
        for name, (restype, argtypes) in fns.items():
            fn = getattr(dll, name)
            fn.restype, fn.argtypes = restype, argtypes

    class Win32:
        pass

    w = Win32()
    w.user32, w.gdi32, w.kernel32, w.shcore = user32, gdi32, kernel32, shcore
    return w


WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM) \
    if hasattr(ctypes, "WINFUNCTYPE") else None
MONITORENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HANDLE, wintypes.HDC, ctypes.POINTER(wintypes.RECT),
                                     wintypes.LPARAM) if hasattr(ctypes, "WINFUNCTYPE") else None


class CharacterWindows:
    """The bubble and character windows, their drawing, and the mouse."""

    def __init__(self, w32: Any, overlay: Overlay) -> None:
        import cairo

        self._cairo = cairo
        self.w32, self.o = w32, overlay
        self.u = w32.user32
        self._proc = WNDPROC(self._wndproc)  # kept: Windows calls it for as long as the windows live
        hinst = w32.kernel32.GetModuleHandleW(None)
        self.bubble = self.char = None  # messages arrive while they are being created
        wc = WNDCLASSEXW(cbSize=ctypes.sizeof(WNDCLASSEXW), lpfnWndProc=ctypes.cast(self._proc, ctypes.c_void_p),
                         hInstance=hinst, lpszClassName=CLASS_NAME,
                         hCursor=self.u.LoadCursorW(None, ctypes.c_void_p(32649)))  # IDC_HAND
        self.u.RegisterClassExW(ctypes.byref(wc))
        base = WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
        self.bubble = self.u.CreateWindowExW(base | WS_EX_TRANSPARENT, CLASS_NAME, "ZundamonJarvis bubble",
                                             WS_POPUP, 0, 0, 1, 1, None, None, hinst, None)
        self.char = self.u.CreateWindowExW(base, CLASS_NAME, "ZundamonJarvis character",
                                           WS_POPUP, 0, 0, 1, 1, None, None, hinst, None)
        self.box = (0, 0)  # the box's top-left, physical pixels
        self._scale = 1.0
        self._drag: dict | None = None
        self._drawn: object = ()  # the bubble's line and scale, last drawn
        self._ticks = 0
        overlay.on_change = self._on_change
        self.place()
        if not overlay.prefs.hidden or overlay.forced_state:
            self._show(True)
        self.u.SetTimer(self.char, 1, FPS_MS, None)

    # ---- monitors and placement ----

    def screens(self) -> dict[str, Rect]:
        """Every monitor's work area by device name, physical pixels."""
        found: dict[str, Rect] = {}

        def each(hmon, _hdc, _rect, _data) -> bool:
            info = MONITORINFOEXW(cbSize=ctypes.sizeof(MONITORINFOEXW))
            if self.u.GetMonitorInfoW(hmon, ctypes.byref(info)):
                r = info.rcWork
                found[device_name(info.szDevice)] = (r.left, r.top, r.right - r.left, r.bottom - r.top)
            return True

        self.u.EnumDisplayMonitors(None, None, MONITORENUMPROC(each), 0)
        return found

    def _dpi_scale(self, x: float, y: float) -> float:
        """The scale of the monitor at (or nearest) a point: 1.0 at 96 dpi."""
        hmon = self.u.MonitorFromPoint(POINT(int(x), int(y)), MONITOR_DEFAULTTONEAREST)
        dx, dy = wintypes.UINT(96), wintypes.UINT(96)
        if self.w32.shcore.GetDpiForMonitor(hmon, 0, ctypes.byref(dx), ctypes.byref(dy)) != 0:  # MDT_EFFECTIVE_DPI
            return 1.0
        return dx.value / 96

    def scale(self) -> float:
        """The scale where the box is now (it changes when it is dragged onto another monitor)."""
        x, y = self.box
        self._scale = self._dpi_scale(x + WIDTH * self._scale / 2, y + HEIGHT * self._scale / 2)
        return self._scale

    def size(self) -> tuple[int, int]:
        s = self.scale()
        return round(WIDTH * s), round(HEIGHT * s)

    def place(self) -> None:
        screens = self.screens()
        prefs = self.o.prefs
        name = prefs.monitor if prefs.monitor in screens else screen_at(screens, (0, 0))
        if name is None:
            return
        sx, sy, sw, sh = screens[name]
        self._scale = self._dpi_scale(sx + sw / 2, sy + sh / 2)
        self.box = place_in(screens[name], prefs.right, prefs.bottom,
                            (round(WIDTH * self._scale), round(HEIGHT * self._scale)))
        self._move()

    def _move(self) -> None:
        s = self._scale
        x, y = self.box
        flags = SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE
        self.u.SetWindowPos(self.bubble, None, x, y, 0, 0, flags)
        self.u.SetWindowPos(self.char, None, x + round(CHAR_X * s), y + round(CHAR_Y * s), 0, 0, flags)

    def _show(self, on: bool) -> None:
        for hwnd in (self.bubble, self.char):
            self.u.ShowWindow(hwnd, SW_SHOWNOACTIVATE if on else SW_HIDE)

    def _on_change(self, old: Prefs, new: Prefs) -> None:
        if (new.right, new.bottom, new.monitor) != (old.right, old.bottom, old.monitor):
            self.place()
        if new.hidden != old.hidden and not self.o.forced_state:
            self._show(not new.hidden)

    # ---- drawing ----

    def _push(self, hwnd: int, surface: Any, x: int, y: int) -> None:
        """Hand a premultiplied ARGB32 cairo surface (which is BGRA in memory: what layered
        windows take) to ``hwnd`` at ``x``, ``y``."""
        w, h = surface.get_width(), surface.get_height()
        surface.flush()
        data = bytes(surface.get_data())
        screen = self.u.GetDC(None)
        mem = self.w32.gdi32.CreateCompatibleDC(screen)
        header = BITMAPINFOHEADER(biSize=ctypes.sizeof(BITMAPINFOHEADER), biWidth=w, biHeight=-h, biPlanes=1,
                                  biBitCount=32, biCompression=BI_RGB)
        bits = ctypes.c_void_p()
        bitmap = self.w32.gdi32.CreateDIBSection(mem, ctypes.byref(header), DIB_RGB_COLORS, ctypes.byref(bits),
                                                 None, 0)
        try:
            ctypes.memmove(bits, data, min(len(data), surface.get_stride() * h))
            old = self.w32.gdi32.SelectObject(mem, bitmap)
            blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
            self.u.UpdateLayeredWindow(hwnd, screen, ctypes.byref(POINT(x, y)), ctypes.byref(SIZE(w, h)), mem,
                                       ctypes.byref(POINT(0, 0)), 0, ctypes.byref(blend), ULW_ALPHA)
            self.w32.gdi32.SelectObject(mem, old)
        finally:
            self.w32.gdi32.DeleteObject(bitmap)
            self.w32.gdi32.DeleteDC(mem)
            self.u.ReleaseDC(None, screen)

    def frame(self) -> None:
        cairo = self._cairo
        s = self.scale()
        pose, line, avatar = self.o.frame()
        x, y = self.box
        if (line, s) != self._drawn:  # the bubble is redrawn only when its line (or scale) changes
            self._drawn = (line, s)
            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, round(WIDTH * s), round(HEIGHT * s))
            if line is not None:
                cr = cairo.Context(surface)
                cr.scale(s, s)
                draw_bubble(cr, line, BUBBLE_FONT)
            self._push(self.bubble, surface, x, y)
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, round(CHAR_W * s), round(CHAR_H * s))
        cr = cairo.Context(surface)
        cr.scale(s, s)
        draw_character(cr, CHAR_W, CHAR_H, pose, avatar)
        self._push(self.char, surface, x + round(CHAR_X * s), y + round(CHAR_Y * s))

    # ---- the mouse ----

    def _cursor(self) -> tuple[int, int]:
        p = POINT()
        self.u.GetCursorPos(ctypes.byref(p))
        return p.x, p.y

    def _menu(self) -> None:
        screens = self.screens()
        x, y = self._cursor()
        on = screen_at(screens, (x, y))
        labels = [(name, f"{name} ({r[2]}×{r[3]})") for name, r in screens.items()]
        actions: dict[int, tuple[str, ...]] = {}

        def build(entries: list[MenuEntry] | tuple[MenuEntry, ...]) -> int:
            menu = self.u.CreatePopupMenu()
            for entry in entries:
                if entry.children:
                    self.u.AppendMenuW(menu, MF_POPUP, build(entry.children), entry.label)
                    continue
                if entry.action == ("hide",):
                    self.u.AppendMenuW(menu, MF_SEPARATOR, 0, None)
                actions[len(actions) + 1] = entry.action  # type: ignore[assignment]
                self.u.AppendMenuW(menu, MF_STRING | (MF_CHECKED if entry.checked else 0), len(actions),
                                   entry.label)
            return menu

        menu = build(menu_entries(self.o.prefs, labels, on))
        self.u.SetForegroundWindow(self.char)  # or the menu would not close on a click elsewhere
        chosen = self.u.TrackPopupMenu(menu, TPM_RETURNCMD | TPM_RIGHTBUTTON, x, y, 0, self.char, None)
        self.u.PostMessageW(self.char, 0, 0, 0)  # WM_NULL, as the docs ask after TrackPopupMenu
        self.u.DestroyMenu(menu)
        if chosen in actions:
            self.o.choose(actions[chosen])

    def _wndproc(self, hwnd: int, msg: int, wp: int, lp: int) -> int:
        try:
            if self.char is None:
                return self.u.DefWindowProcW(hwnd, msg, wp, lp)
            if msg == WM_TIMER and hwnd == self.char:
                self._ticks += 1
                if self._ticks % max(int(POLL_S * 1000 / FPS_MS), 1) == 0:
                    self.o.follow_file()
                if not self.o.prefs.hidden or self.o.forced_state:
                    self.frame()
                return 0
            if msg == WM_MOUSEACTIVATE:
                return MA_NOACTIVATE  # clicking it leaves the focus where it was
            if msg == WM_LBUTTONDOWN:
                self.u.SetCapture(hwnd)
                self._drag = {"pointer": self._cursor(), "box": self.box, "moved": False}
                return 0
            if msg == WM_MOUSEMOVE and self._drag is not None:
                (px, py), (bx, by) = self._drag["pointer"], self._drag["box"]
                cx, cy = self._cursor()
                if self._drag["moved"] or not is_click(cx - px, cy - py):
                    self._drag["moved"] = True
                    self.box = (bx + cx - px, by + cy - py)
                    self._move()
                return 0
            if msg == WM_LBUTTONUP and self._drag is not None:
                self.u.ReleaseCapture()
                drag, self._drag = self._drag, None
                if not drag["moved"]:
                    _open_watch()
                    return 0
                screens = self.screens()
                w, h = self.size()
                name = screen_at(screens, (self.box[0] + w / 2, self.box[1] + h / 2))
                if name is not None:
                    right, bottom = margins_in(screens[name], self.box[0], self.box[1], (w, h))
                    self.o.change(right=right, bottom=bottom, monitor=name)
                return 0
            if msg == WM_RBUTTONUP:
                self._menu()
                return 0
            if msg == WM_DISPLAYCHANGE:
                self.place()
            if msg == WM_DESTROY:
                self.u.PostQuitMessage(0)
                return 0
        except Exception as e:  # an exception must not cross back into Windows
            print(f"zundamon-jarvisctl character: {e!r}", file=sys.stderr)
        return self.u.DefWindowProcW(hwnd, msg, wp, lp)

    def close(self) -> None:
        self.u.KillTimer(self.char, 1)
        for hwnd in (self.char, self.bubble):
            self.u.DestroyWindow(hwnd)

    def run(self) -> int:
        msg = wintypes.MSG()
        while self.u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            self.u.TranslateMessage(ctypes.byref(msg))
            self.u.DispatchMessageW(ctypes.byref(msg))
        return 0


def _open_watch() -> None:
    from zundamon_jarvis.ui import ctl

    threading.Thread(target=ctl.open_window, args=("watch",), daemon=True).start()


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    forced = args[0] if args and (normalize(args[0]) == args[0] or args[0] == "hearing") else None
    look = args[1] if forced and len(args) > 1 and args[1] in AVATARS else None
    lock = claim(forced)
    if lock is None:
        return 0  # already running: it shows itself (the prefs say so)
    w32 = _win32()
    try:
        w32.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2))
    except (AttributeError, OSError):
        pass  # before Windows 10 1703: blurry on a scaled monitor, but there
    return CharacterWindows(w32, Overlay(forced, look)).run()
