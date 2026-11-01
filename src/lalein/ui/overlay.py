"""The on-screen character without a toolkit: what to draw each frame, and the prefs.

``StatePoller`` and ``EventStream`` follow the daemon (its ``status`` and its ``watch`` stream)
on threads, through the control endpoint (``core.transport``: a Unix socket, or localhost TCP on
Windows). ``Overlay`` puts them together for the Windows and macOS windows (``character_win``,
``character_mac``): ``frame()`` is the pose, the bubble's line and the look to draw now;
``choose`` runs a right-click menu entry (``character.menu_entries``); ``follow_file`` picks up
prefs that ``laleinctl character ...`` changed. Every prefs change, from here or the file,
goes to ``on_change(old, new)``, where the window moves, hides or shows.
"""

import json
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from lalein.core import platform, transport
from lalein.core.control import request, socket_path
from lalein.ui.character import (
    Bubble,
    Line,
    Pose,
    Prefs,
    bubble_note,
    load_prefs,
    normalize,
    pose_for,
    prefs_path,
    save_prefs,
    update_prefs,
)

POLL_S = 0.5
RETRY_S = 2.0
LOCK = "character.lock"


def claim(forced_state: str | None, path: Path | None = None) -> object | None:
    """Starting the character (not a preview): the lock that keeps it to one, or None when one
    is already running. Either way a hidden one comes back (it follows the prefs file)."""
    if forced_state:
        return object()
    path = path or prefs_path()
    if load_prefs(path).hidden:
        update_prefs(path, hidden=False)
    from lalein.system.supervisor import InstanceLock

    lock = InstanceLock(platform.runtime_dir() / LOCK)
    return lock if lock.acquire() else None


class StatePoller:
    """Polls the daemon's ``status`` on a thread; ``state`` is always safe to read."""

    def __init__(self) -> None:
        self.state = "offline"
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        while True:
            try:
                self.state = normalize(request("status", timeout=1.0).get("state"))
            except (OSError, ValueError, AttributeError):
                self.state = "offline"
            time.sleep(POLL_S)


class EventStream:
    """Follows the control endpoint's ``watch`` stream on a thread. Each event, and a
    ``{"kind": "hello"}`` on every disconnect (so nothing stale stays up), goes to ``deliver``
    on that thread: a toolkit hands it to its own main loop."""

    def __init__(self, deliver: Callable[[dict], Any]) -> None:
        self._deliver = deliver
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        while True:
            try:
                with transport.connect(socket_path(), 2) as s:
                    s.sendall(b"watch\n")
                    s.settimeout(None)
                    with s.makefile("rb") as f:
                        for raw in f:
                            try:
                                ev = json.loads(raw)
                            except ValueError:
                                continue
                            if isinstance(ev, dict):
                                self._deliver(ev)
            except OSError:
                pass
            self._deliver({"kind": "hello"})
            time.sleep(RETRY_S)


class Overlay:
    """The character's state, bubble and prefs. ``forced_state`` previews one animation (or
    "hearing") without a daemon, in ``forced_avatar`` if given; ``live=False`` does not follow
    the daemon at all (tests). Events may arrive on any thread."""

    def __init__(self, forced_state: str | None = None, forced_avatar: str | None = None,
                 path: Path | None = None, live: bool = True,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.path = path or prefs_path()
        self.prefs = load_prefs(self.path)
        self.forced_state, self.forced_avatar = forced_state, forced_avatar
        self.hearing = False
        self.on_change: Callable[[Prefs, Prefs], None] = lambda old, new: None
        self._clock = clock
        self._start = clock()
        self._bubble = Bubble()
        self._lock = threading.Lock()
        self._seen = self._stat()
        self._poller = StatePoller() if live and not forced_state else None
        if live and not forced_state:
            EventStream(self.on_event)

    # ---- what to draw ----

    def on_event(self, ev: dict) -> None:
        with self._lock:
            if ev.get("kind") == "hearing":
                self.hearing = bool(ev.get("on"))
            elif ev.get("kind") == "hello":
                self.hearing = False
            self._bubble.feed(ev, self._clock())

    def state(self) -> str:
        if self.forced_state:
            return "listening" if self.forced_state == "hearing" else self.forced_state
        return self._poller.state if self._poller else "offline"

    def frame(self, now: float | None = None) -> tuple[Pose, Line | None, str]:
        """The pose, the bubble's line (None: no bubble) and the look, at ``now``."""
        now = self._clock() if now is None else now
        state = self.state()
        with self._lock:
            line = self._bubble.show(state, now)
            hearing = self.hearing or self.forced_state == "hearing"
        if line is not None and not (self.prefs.bubble or line.who == "note"):
            line = None
        return (pose_for(state, now - self._start, hearing=hearing), line,
                self.forced_avatar or self.prefs.avatar)

    # ---- the prefs ----

    def _stat(self) -> tuple[int, int] | None:
        try:
            st = self.path.stat()
        except OSError:
            return None
        return st.st_mtime_ns, st.st_size

    def _apply(self, new: Prefs) -> None:
        old, self.prefs = self.prefs, new
        if new.bubble != old.bubble:
            with self._lock:
                self._bubble.note(bubble_note(new.bubble), self._clock())
        if new != old:
            self.on_change(old, new)

    def change(self, **changes: Any) -> None:
        new = replace(self.prefs, **changes)
        save_prefs(self.path, new)
        self._seen = self._stat()
        self._apply(new)

    def follow_file(self) -> None:
        """Pick up prefs written by someone else (``laleinctl character ...``)."""
        if (now := self._stat()) != self._seen:
            self._seen = now
            self._apply(load_prefs(self.path))

    def choose(self, action: tuple[str, ...]) -> None:
        """Run a right-click menu entry's action (``character.MenuEntry.action``)."""
        kind, *arg = action
        if kind == "bubble":
            self.change(bubble=not self.prefs.bubble)
        elif kind == "avatar":
            self.change(avatar=arg[0])
        elif kind == "monitor":
            self.change(monitor=arg[0])
        elif kind == "hide":
            self.change(hidden=True)
