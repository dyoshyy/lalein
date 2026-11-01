"""Pure state -> animation mapping for the on-screen character (``zundamon-jarvisctl character``).

No GUI imports here: ``pose_for(state, t)`` turns the daemon's state (the ``state`` field of the
control socket's ``status`` reply) and a time in seconds into a ``Pose``, which the GTK drawing
code (``zundamon_jarvis.ui.character_app``) paints. Everything is deterministic in ``t`` so it is testable.

- listening: slow breathing, a blink every few seconds.
- thinking: sways side to side, eyes look up, dots count up above the head.
- speaking: the body bounces and the mouth opens and closes.
- paused: dim and sleepy, eyes shut, floating "z".
- limit: worried face, warm tint, a sweat drop.
- offline (or an unknown state / no daemon): grey, X eyes, almost transparent.
- hearing (the user's speech is being taken in; the ``hearing`` watch event): eyes wide open,
  sound waves beside the head and a lit antenna, on top of any awake state.

The character comes in several looks (``AVATARS``; ``body_rgb`` is each one's colour per tint,
``looks`` draws each figure). Every look shows the same poses.

Also the rest of the overlay that needs no GTK: ``Bubble`` decides what the speech bubble shows
(from the control socket's ``watch`` stream), ``Prefs`` is where the character was left, on which
monitor, in which look, whether the bubble is on and whether it is hidden; ``clamp_margins`` /
``is_click`` are the dragging rules. ``zundamon-jarvisctl character hide|show|toggle|avatar|monitor``
changes the prefs file (``update_prefs``) and the running overlay follows it.
"""

import json
import math
import os
import re
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from zundamon_jarvis.core import i18n, platform

STATES = ("listening", "thinking", "speaking", "paused", "limit", "offline")
# The overlay's layout, the same on every OS: the character at the bottom-right of a WIDTH x
# HEIGHT box, the speech bubble above it. Prefs' margins place the whole box.
CHAR_W, CHAR_H = 160, 180
BUBBLE_H = 110
WIDTH, HEIGHT = 300, CHAR_H + BUBBLE_H
CHAR_X, CHAR_Y = WIDTH - CHAR_W, HEIGHT - CHAR_H
BLINK_PERIOD_S = 4.0
BLINK_LENGTH_S = 0.16


@dataclass(frozen=True)
class Pose:
    body_dy: float = 0.0  # vertical offset, in units of the body height (+ is down)
    squash: float = 0.0  # +: taller and thinner, -: shorter and wider
    sway: float = 0.0  # rotation in radians around the feet
    eye_open: float = 1.0  # 0 shut .. 1 open
    eye_look_y: float = 0.0  # -1 up .. 1 down
    mouth_open: float = 0.0  # 0 smile .. 1 wide open
    mouth_flat: bool = False  # worried / flat mouth instead of a smile
    dots: int = 0  # thinking dots above the head
    zzz: float = -1.0  # sleep "z" phase 0..1, or -1 for none
    sweat: bool = False
    x_eyes: bool = False
    alpha: float = 1.0
    tint: str = "normal"  # normal | dim | warm | grey
    waves: float = -1.0  # hearing: sound waves phase 0..1 (moving outward), or -1 for none
    glow: bool = False  # hearing: the antenna bulb is lit


# ---- looks ----

AVATARS = ("bot", "robo", "cat", "ghost", "onigiri", "edamame")
DEFAULT_AVATAR = AVATARS[0]
_BASE = {"bot": (0.45, 0.75, 1.0), "robo": (0.72, 0.76, 0.82), "cat": (0.22, 0.22, 0.27),
         "ghost": (0.94, 0.95, 1.0), "onigiri": (0.98, 0.97, 0.92), "edamame": (0.56, 0.82, 0.36)}
_TINT = {"dim": ((0.35, 0.40, 0.60), 0.6), "warm": ((1.0, 0.58, 0.30), 0.8)}  # (towards, how far)
GREY = (0.60, 0.60, 0.62)


def body_rgb(avatar: str, tint: str) -> tuple[float, float, float]:
    """The body colour of ``avatar`` in a pose's ``tint``: offline is the same grey for all."""
    base = _BASE.get(avatar, _BASE[DEFAULT_AVATAR])
    if tint == "grey":
        return GREY
    if tint not in _TINT:
        return base
    towards, k = _TINT[tint]
    return tuple(round(b + (t - b) * k, 4) for b, t in zip(base, towards))  # type: ignore[return-value]


def avatar_label(avatar: str, lang: str | None = None) -> str:
    return i18n.text(TEXT, f"avatar.{avatar}", lang)


def normalize(state: object) -> str:
    return state if isinstance(state, str) and state in STATES else "offline"


def _blink(t: float) -> float:
    phase = t % BLINK_PERIOD_S
    if phase > BLINK_LENGTH_S:
        return 1.0
    return abs(phase / BLINK_LENGTH_S * 2 - 1)  # 1 -> 0 -> 1


HEARING_WAVE_S = 0.6  # one wave travels out in this long


def pose_for(state: object, t: float, hearing: bool = False) -> Pose:
    pose = _pose(normalize(state), t)
    if not hearing or normalize(state) in ("paused", "offline"):  # the ears are closed then
        return pose
    return replace(pose, waves=(t / HEARING_WAVE_S) % 1.0, glow=True, eye_open=1.0,
                   eye_look_y=0.0)


def _pose(s: str, t: float) -> Pose:
    if s == "listening":
        breath = math.sin(t * 2 * math.pi / 3.2)
        return Pose(squash=0.025 * breath, body_dy=-0.01 * breath, eye_open=_blink(t),
                    mouth_open=0.0)
    if s == "thinking":
        return Pose(sway=0.09 * math.sin(t * 2 * math.pi / 2.2), eye_look_y=-1.0,
                    eye_open=_blink(t + 1.3), dots=int(t * 2.5) % 4, mouth_flat=True)
    if s == "speaking":
        bounce = abs(math.sin(t * 2 * math.pi * 2.4))
        talk = 0.5 + 0.5 * math.sin(t * 2 * math.pi * 5.0 + 0.8 * math.sin(t * 2.3))
        return Pose(body_dy=-0.07 * bounce, squash=0.04 * (1 - bounce) - 0.02,
                    mouth_open=0.15 + 0.85 * talk, eye_open=_blink(t))
    if s == "paused":
        breath = math.sin(t * 2 * math.pi / 5.0)
        return Pose(squash=0.015 * breath, body_dy=0.04, sway=0.03 * breath, eye_open=0.0,
                    zzz=(t / 3.0) % 1.0, alpha=0.6, tint="dim")
    if s == "limit":
        shake = math.sin(t * 2 * math.pi * 1.2)
        return Pose(sway=0.02 * shake, eye_open=_blink(t), mouth_flat=True, sweat=True,
                    tint="warm")
    return Pose(body_dy=0.05, eye_open=0.0, x_eyes=True, mouth_flat=True, alpha=0.35,
                tint="grey")


# ---- speech bubble ----

MIN_SHOW_S = 2.5  # a line stays at least this long
LINGER_S = 2.5  # and this long after ZundamonJarvis was last thinking / speaking
NOTE_S = 1.5  # a note (the bubble was switched on / off) shows this long
TEXT_MAX = 140
_BUSY = ("thinking", "speaking")


@dataclass(frozen=True)
class Line:
    who: str  # "you" (what was heard) | "me" (what ZundamonJarvis says) | "note"
    text: str


def _fold(text: Any) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= TEXT_MAX else text[: TEXT_MAX - 1] + "…"


class Bubble:
    """What the speech bubble shows. ``feed`` takes ``watch`` events, ``show`` is asked every
    frame. Only live events count (the replayed history is old news). The last thing heard
    or said is shown while ZundamonJarvis works on it and fades after; the user starting
    to talk again clears what they said before (not the reply)."""

    def __init__(self) -> None:
        self._live = False
        self._line: Line | None = None
        self._since = 0.0
        self._until: float | None = None  # a note's fixed end
        self._busy_at = -math.inf

    def _set(self, line: Line | None, now: float, until: float | None = None) -> None:
        self._line, self._since, self._until = line, now, until

    def feed(self, ev: dict, now: float) -> None:
        kind = ev.get("kind")
        if kind == "hello":
            self._live = False
            self._set(None, now)
            return
        if kind == "live":
            self._live = True
            return
        if not self._live:
            return
        if kind == "heard":
            self._set(Line("you", _fold(ev.get("text"))), now)
        elif kind == "said":
            self._set(Line("me", _fold(ev.get("text"))), now)
        elif kind == "hearing" and ev.get("on") and self._line and self._line.who == "you":
            self._set(None, now)

    def note(self, text: str, now: float) -> None:
        self._set(Line("note", text), now, until=now + NOTE_S)


    def show(self, state: str, now: float) -> Line | None:
        if state in _BUSY:
            self._busy_at = now
        if self._line is None:
            return None
        end = self._until if self._until is not None else max(
            self._since + MIN_SHOW_S, self._busy_at + LINGER_S)
        if now > end:
            self._set(None, now)
        return self._line


TEXT = {  # the overlay's own notes (what is heard and said is shown as it is)
    "bubble.on": {"ja": "吹き出し: オン", "en": "Speech bubble: on"},
    "bubble.off": {"ja": "吹き出し: オフ", "en": "Speech bubble: off"},
    "avatar.bot": {"ja": "まるボット", "en": "Bot"},
    "avatar.robo": {"ja": "ロボット", "en": "Robot"},
    "avatar.cat": {"ja": "くろねこ", "en": "Black cat"},
    "avatar.ghost": {"ja": "おばけ", "en": "Ghost"},
    "avatar.onigiri": {"ja": "おにぎり", "en": "Onigiri"},
    "avatar.edamame": {"ja": "えだまめ", "en": "Edamame"},
    "menu.bubble": {"ja": "吹き出しを表示", "en": "Show the speech bubble"},
    "menu.avatar": {"ja": "見た目", "en": "Look"},
    "menu.monitor": {"ja": "表示するモニター", "en": "Monitor"},
    "menu.hide": {"ja": "隠す", "en": "Hide"},  # starting it again (the app launcher) brings it back
}


def menu_text(key: str, lang: str | None = None) -> str:
    """The right-click menu's words, in the display language."""
    return i18n.text(TEXT, key, lang)


@dataclass(frozen=True)
class MenuEntry:
    """One item of the right-click menu, for a native menu to show: a checkable item with an
    ``action`` for ``Overlay.choose``, or a submenu (``children``)."""
    label: str
    action: tuple[str, ...] | None = None  # ("bubble",) | ("avatar", name) | ("monitor", name) | ("hide",)
    checked: bool = False
    children: tuple["MenuEntry", ...] = ()


def menu_entries(prefs: "Prefs", screens: list[tuple[str, str]], current: str | None,
                 lang: str | None = None) -> list[MenuEntry]:
    """The right-click menu: the bubble, the look, the monitor (``screens``: (name, label);
    only with more than one) and hiding; ``current`` is the monitor it is on now."""
    looks = tuple(MenuEntry(avatar_label(a, lang), ("avatar", a), a == prefs.avatar) for a in AVATARS)
    entries = [MenuEntry(menu_text("menu.bubble", lang), ("bubble",), prefs.bubble),
               MenuEntry(menu_text("menu.avatar", lang), children=looks)]
    if len(screens) > 1:
        on = prefs.monitor or current
        entries.append(MenuEntry(menu_text("menu.monitor", lang), children=tuple(
            MenuEntry(label, ("monitor", name), name == on) for name, label in screens)))
    return [*entries, MenuEntry(menu_text("menu.hide", lang), ("hide",))]


def monitor_label(connector: str, model: str | None, description: str | None) -> str:
    """How the menu names a monitor: its model and connector, or the connector alone when the
    model is missing or only a hex product code."""
    if model and not re.fullmatch(r"0x[0-9a-fA-F]+", model):
        return f"{model} ({connector})"
    return connector


def bubble_note(on: bool, lang: str | None = None) -> str:
    """The note shown when a right-click switches the speech bubble, in the display language."""
    return i18n.text(TEXT, "bubble.on" if on else "bubble.off", lang)


# ---- moving it around, and remembering where ----

MARGIN = 16  # default distance from the bottom-right corner
CLICK_SLOP_PX = 6  # a press that moves less than this is a click, not a drag


def is_click(dx: float, dy: float) -> bool:
    return abs(dx) <= CLICK_SLOP_PX and abs(dy) <= CLICK_SLOP_PX


def drag_target(start: tuple[int, int], pointer_start: tuple[float, float],
                pointer: tuple[float, float]) -> tuple[float, float]:
    """Right / bottom margins after the pointer (in screen coordinates) moved since the press.
    Screen coordinates, not the window's own: the window moves under the pointer while it is
    dragged, so offsets relative to it lag behind the compositor and make it jump about."""
    return (start[0] - (pointer[0] - pointer_start[0]), start[1] - (pointer[1] - pointer_start[1]))


def clamp_margins(right: float, bottom: float, size: tuple[int, int],
                  screen: tuple[int, int]) -> tuple[int, int]:
    """Margins from the bottom-right corner that keep a ``size`` window on a ``screen``."""
    (w, h), (sw, sh) = size, screen
    return (int(min(max(right, 0), max(sw - w, 0))), int(min(max(bottom, 0), max(sh - h, 0))))


Rect = tuple[int, int, int, int]  # x, y, width, height in the compositor's (logical) screen space


def place_in(screen: Rect, right: float, bottom: float, size: tuple[int, int]) -> tuple[int, int]:
    """The top-left corner of a ``size`` box ``right`` / ``bottom`` from ``screen``'s corner
    (y down), kept on the screen."""
    x, y, sw, sh = screen
    r, b = clamp_margins(right, bottom, size, (sw, sh))
    return x + sw - r - size[0], y + sh - b - size[1]


def margins_in(screen: Rect, left: float, top: float, size: tuple[int, int]) -> tuple[int, int]:
    """``place_in`` backwards: the margins of a box whose top-left is at ``left`` / ``top``."""
    x, y, sw, sh = screen
    return clamp_margins(x + sw - left - size[0], y + sh - top - size[1], size, (sw, sh))


def screen_at(screens: dict[str, Rect], point: tuple[float, float]) -> str | None:
    """The screen under ``point``, or the nearest one when it is off every screen."""
    def distance(rect: Rect) -> float:
        x, y, w, h = rect
        return math.hypot(max(x - point[0], 0, point[0] - (x + w)), max(y - point[1], 0, point[1] - (y + h)))

    return min(screens, key=lambda name: distance(screens[name]), default=None)


def drop_on(start: tuple[int, int], monitor: str, pointer_start: tuple[float, float],
            pointer: tuple[float, float], size: tuple[int, int],
            monitors: dict[str, Rect]) -> tuple[str, int, int] | None:
    """Where a drag that began at ``start`` margins on ``monitor`` ends: the monitor under the
    pointer (or the same one, off every monitor) and the margins there that keep the window
    where the pointer took it, on screen. None when ``monitor`` is not among ``monitors``."""
    if monitor not in monitors:
        return None
    (mx, my, mw, mh), (w, h) = monitors[monitor], size
    left = mx + mw - start[0] - w + (pointer[0] - pointer_start[0])
    top = my + mh - start[1] - h + (pointer[1] - pointer_start[1])
    px, py = pointer
    name = next((n for n, (x, y, rw, rh) in monitors.items() if x <= px < x + rw and y <= py < y + rh), monitor)
    x, y, rw, rh = monitors[name]
    return (name, *clamp_margins(x + rw - left - w, y + rh - top - h, size, (rw, rh)))


@dataclass(frozen=True)
class Prefs:
    right: int = MARGIN
    bottom: int = MARGIN
    bubble: bool = True
    avatar: str = DEFAULT_AVATAR
    monitor: str | None = None  # a connector name ("DP-1"); None: wherever the compositor puts it
    hidden: bool = False


def prefs_path() -> Path:
    return platform.log_dir() / "character.json"


def load_prefs(path: Path) -> Prefs:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Prefs()
    if not isinstance(raw, dict):
        return Prefs()
    d = Prefs()

    def margin(key: str, default: int) -> int:
        v = raw.get(key)
        ok = isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
        return max(int(v), 0) if ok else default

    bubble, avatar, monitor, hidden = (raw.get(k) for k in ("bubble", "avatar", "monitor", "hidden"))
    return Prefs(right=margin("right", d.right), bottom=margin("bottom", d.bottom),
                 bubble=bubble if isinstance(bubble, bool) else d.bubble,
                 avatar=avatar if avatar in AVATARS else d.avatar,
                 monitor=monitor if isinstance(monitor, str) and monitor else d.monitor,
                 hidden=hidden if isinstance(hidden, bool) else d.hidden)


def save_prefs(path: Path, prefs: Prefs) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(asdict(prefs)), encoding="utf-8")
    os.replace(tmp, path)


def update_prefs(path: Path, **changes: Any) -> Prefs:
    """Change some prefs in the file and keep the rest (``zundamon-jarvisctl character ...``)."""
    prefs = replace(load_prefs(path), **changes)
    save_prefs(path, prefs)
    return prefs
