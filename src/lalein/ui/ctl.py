"""`laleinctl toggle|pause|resume|status|get|set|bar|watch|window|settings` — talk to the daemon.

``debug`` prints where the ears are (see ``lalein.core.control``); ``get`` prints the status
and the live settings; ``set speed|speaker|model|mic <value>`` changes one (live, and in
config.toml; see ``lalein.core.control``). ``settings`` runs the settings TUI
(``lalein.ui.settings_app``, imported only then: the bar's ``laleinctl bar`` stays light).

``bar`` prints Waybar-style JSON for the Omarchy shell's command bar module, and nothing
at all when the daemon is not running (the module hides on empty output; ``{"text": ""}``
would be shown verbatim, since the shell falls back to the raw output).

``watch`` streams the conversation (control socket ``watch``): recent history, then live
lines; while the daemon is away it says so once and reconnects every 2 s, skipping what it has
already shown (same daemon run) or showing everything again (a restarted daemon). Ctrl-C ends it.
Its lines are in the display language (``lalein.core.i18n``), asked once per event, so a
change in the settings window shows from the next line on; the history replayed after a
reconnect follows it too (events carry data, not finished text: ``tool`` has ``labels`` per
language, ``state`` its ``reasons``). The JSON the other commands print stays as the daemon
sent it (scripts read it); ``bar``'s text and tooltip are shown, so they follow the language.

``character [state [look]]`` runs the on-screen character: on Linux the GTK overlay
(``lalein.ui.character_app``) under the system python, which has GTK; on Windows and macOS
``character_win`` / ``character_mac`` in this python. The optional state previews one animation
without a daemon (in the given look, or the remembered one).
``character hide|show|toggle``, ``character avatar <look>`` and ``character monitor <connector>``
change its prefs file, which the running overlay follows; ``show`` (and a ``toggle`` that shows
it) also starts it, or brings back the running one. On Windows and macOS, where a lock tells
whether it runs, ``toggle`` starts one that is not running instead of hiding it
(``spawn_character_toggle``: the tray's and menu bar's item).

``window [name]`` toggles a terminal window (``lalein.ui.desktop`` picks how: Hyprland by
window class, macOS Terminal/iTerm2, or any Linux terminal). ``WINDOWS`` lists the known ones
(``watch`` runs ``laleinctl watch``, ``settings`` runs ``laleinctl settings``); the
Hyprland window rule for each class (``desktop.CLASSES``: float, size, position) lives in the
user's Hyprland config.
"""

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

from lalein.core import i18n, platform, transport
from lalein.core.control import COMMANDS, request, socket_path, status_label
from lalein.ui import desktop

USAGE = ("laleinctl toggle|pause|resume|status|debug|get|bar|watch|settings"
         "|set speed|speaker|model|mic|language <value>|window [watch|settings]|character [state [look]]"
         "|character hide|show|toggle|restart|avatar <look>|monitor <connector>")
TEXT = {
    "usage": {"ja": "使い方: {usage}", "en": "usage: {usage}"},
    "tooltip": {"ja": "ラレイン: {state} — クリック: 会話、右クリック: 設定、{hotkey}: 一時停止/再開",
                "en": "Lalein: {state} — click: conversation, right-click: settings, {hotkey}: pause/resume"},
    "you": {"ja": "あなた ▸", "en": "You ▸"},
    "me": {"ja": "ラレイン ▸", "en": "Lalein ▸"},
    "confirm": {"ja": "❓ 確認: {text}", "en": "❓ Confirm: {text}"},
    "confirm.yes": {"ja": "✅ はい → 実行", "en": "✅ Yes → go"},
    "confirm.no": {"ja": "❌ いいえ → 取り消し", "en": "❌ No → cancelled"},
    "confirm.timeout": {"ja": "⌛ 返事なし → 取り消し", "en": "⌛ No answer → cancelled"},
    "confirm.unclear": {"ja": "❔ 聞き取れず → 取り消し", "en": "❔ Not understood → cancelled"},
    "confirm.cancelled": {"ja": "❌ 取り消し", "en": "❌ Cancelled"},
    "sub": {"ja": "裏: ", "en": "bg: "},
    "task.completed": {"ja": "🧵 バックグラウンドの作業が終わりました", "en": "🧵 Background task finished"},
    "task.failed": {"ja": "🧵 バックグラウンドの作業が失敗しました", "en": "🧵 Background task failed"},
    "task.stopped": {"ja": "🧵 バックグラウンドの作業を止めました", "en": "🧵 Background task stopped"},
    "task.other": {"ja": "🧵 バックグラウンドの作業: {status}", "en": "🧵 Background task: {status}"},
    "live": {"ja": "──── ここから最新 ────", "en": "──── live from here ────"},
    "restarted": {"ja": "──── ラレインが再起動しました ────", "en": "──── Lalein restarted ────"},
    "waiting": {"ja": "⏳ ラレインが動いていません。起動を待っています…",
                "en": "⏳ Lalein is not running. Waiting for it to start…"},
    "thinking": {"ja": "ラレインが考え中…", "en": "Lalein is thinking…"},
    "unknown_window": {"ja": "知らないウィンドウです: {name!r}（{names} のどれか）",
                       "en": "unknown window: {name!r} (use {names})"},
    "not_found": {"ja": "laleinctl character: {path} がありません", "en": "laleinctl character: {path} not found"},
    "unknown_avatar": {"ja": "知らない見た目です: {name!r}（{names} のどれか）",
                       "en": "unknown look: {name!r} (use {names})"},
}


def t(key: str, lang: str | None = None, **fields: object) -> str:
    return i18n.text(TEXT, key, lang, **fields)

HOTKEY = "SUPER+ALT+J"
ICON = {
    "listening": "\U000f036c",  # nf-md-microphone
    "paused": "\U000f036d",  # nf-md-microphone_off
    "thinking": "\U000f051f",  # nf-md-timer_sand
    "speaking": "\U000f057e",  # nf-md-volume_high
    "limit": "\U000f0026",  # nf-md-alert
    "offline": "\U000f0b5a",  # nf-md-cloud_off_outline
}


def bar_output(resp: dict, lang: str | None = None) -> dict:
    """Waybar's JSON: the keys and ``class`` (the state, for styling) are fixed; the text and
    tooltip are shown, so they are in the display language."""
    lang = lang or i18n.language()
    state, text = resp.get("state", ""), status_label(resp, lang)
    icon = ICON.get(state, "")
    return {
        "text": f"{icon} {text}".strip(),
        "tooltip": t("tooltip", lang, state=text, hotkey=HOTKEY),
        "class": state,
    }


# ---- watch ----

RETRY_S = 2.0
WINDOW_TITLE = "Lalein"

_RESET = "\033[0m"
_STYLE = {
    "heard": "\033[1;36m",  # bold cyan
    "reply": "\033[32m",  # green
    "dim": "\033[2m",
    "warn": "\033[33m",  # yellow
    "ok": "\033[32m",
    "no": "\033[31m",  # red
}
STATE_ICON = {"paused": "⏸", "listening": "▶", "limit": "⛔", "offline": "📴"}
_CONFIRM = {  # (ok, why) -> (style, TEXT key)
    (True, "answer"): ("ok", "confirm.yes"),
    (False, "answer"): ("no", "confirm.no"),
    (False, "timeout"): ("no", "confirm.timeout"),
    (False, "unclear"): ("no", "confirm.unclear"),
}
_TASK_STATUSES = ("completed", "failed", "stopped")


def _hhmm(ts: Any) -> str:
    try:
        return datetime.fromisoformat(ts).strftime("%H:%M")
    except (TypeError, ValueError):
        return "--:--"


def _tool_label(ev: dict, lang: str) -> str:
    labels = ev.get("labels")
    label = labels.get(lang) if isinstance(labels, dict) else None
    return str(label or ev.get("label", ""))  # a daemon from before ``labels``: its English one


def _body(ev: dict, lang: str) -> tuple[str, str] | None:
    """(style, text) for one event, or None for kinds not shown."""
    kind = ev.get("kind")
    text = str(ev.get("text", ""))
    if kind == "heard":
        return "heard", f"{t('you', lang)}\n  {text}"
    if kind == "said":
        what = ev.get("what")
        if what == "question":
            return "warn", t("confirm", lang, text=text)
        if what == "filler":
            return "dim", f"{t('me', lang)}\n  ({text})"
        return ("warn" if what == "notice" else "reply"), f"{t('me', lang)}\n  {text}"
    if kind == "confirm":
        ok = bool(ev.get("ok"))
        style, key = _CONFIRM.get((ok, ev.get("why")), ("ok", "confirm.yes") if ok else ("no", "confirm.cancelled"))
        return style, t(key, lang)
    if kind == "tool":
        return "dim", f"  {t('sub', lang) if ev.get('sub') else ''}{_tool_label(ev, lang)}"
    if kind == "state":
        state = ev.get("state")
        style = "dim" if state == "listening" else "warn"
        label = status_label(ev, lang) if state else text
        return style, f"{STATE_ICON.get(state, '•')} {label or state}"
    if kind == "task":
        status = ev.get("status")
        head = t(f"task.{status}", lang) if status in _TASK_STATUSES else t("task.other", lang, status=status)
        summary = ev.get("summary")
        return "dim", f"{head}: {summary}" if summary else head
    return None


def speaker_of(ev: dict) -> str | None:
    """Who is talking in this event ("You" / "Lalein", ids not labels), or None for everything else."""
    kind = ev.get("kind")
    if kind == "heard":
        return "You"
    if kind == "said" and ev.get("what") != "question":
        return "Lalein"
    return None


def format_event(ev: dict, color: bool = False, same_speaker: bool = False, lang: str | None = None) -> str | None:
    """One event as printable text in ``lang`` (default the display language). ``same_speaker``:
    the previous line was the same speaker, so the time and name are left out and only the
    indented message is printed."""
    body = _body(ev, lang or i18n.language())
    if body is None:
        return None
    style, text = body
    if same_speaker and speaker_of(ev) is not None:
        line = text.split("\n", 1)[-1]
    else:
        line = f"{_hhmm(ev.get('ts'))} {text}"
    return f"{_STYLE[style]}{line}{_RESET}" if color else line


class WatchView:
    """Turns the stream (across reconnects) into the lines to print. ``lang`` fixes the
    language; None asks for the display language at each event (``self.lang`` is the last one,
    for the spinner, which draws ten times a second)."""

    def __init__(self, color: bool = False, lang: str | None = None) -> None:
        self.color = color
        self._fixed_lang = lang
        self.lang = lang or i18n.language()
        self._boot: str | None = None
        self._last_id = 0
        self._live_shown = False
        self._waiting = False
        self._thinking = False
        self._speaker: str | None = None  # who spoke on the last printed line

    @property
    def busy(self) -> bool:
        """True while the brain is working on a turn (live only, not while replaying history)."""
        return self._thinking and self._live_shown

    def _dim(self, line: str) -> str:
        return f"{_STYLE['dim']}{line}{_RESET}" if self.color else line

    def _refresh_lang(self) -> str:
        if self._fixed_lang is None:
            self.lang = i18n.language()
        return self.lang

    def feed(self, ev: dict) -> list[str]:
        kind = ev.get("kind")
        if kind not in ("thinking", "hearing"):  # those draw no line
            self._refresh_lang()
        if kind == "hello":
            self._waiting = False
            self._thinking = False
            boot = ev.get("boot")
            lines = []
            if self._boot is not None and boot != self._boot:
                lines.append(self._dim(t("restarted", self.lang)))
                self._last_id = 0
                self._live_shown = False
            self._boot = boot
            self._speaker = None
            return lines
        if kind == "live":
            if self._live_shown:
                return []
            self._live_shown = True
            self._speaker = None
            return [self._dim(t("live", self.lang))]
        ev_id = ev.get("id")
        if isinstance(ev_id, int):
            if ev_id <= self._last_id:
                return []  # already shown before the reconnect
            self._last_id = ev_id
        if kind == "thinking":
            self._thinking = bool(ev.get("on"))
            return []
        if kind == "hearing":  # the character's cue, not a line; the turn goes on
            return []
        self._thinking = False  # any other event means the turn moved on
        speaker = speaker_of(ev)
        line = format_event(ev, self.color, same_speaker=speaker is not None and speaker == self._speaker,
                            lang=self.lang)
        if line is None:
            return []
        self._speaker = speaker
        return [line]

    def disconnected(self) -> str | None:
        self._thinking = False
        self._speaker = None
        if self._waiting:
            return None
        self._waiting = True
        return t("waiting", self._refresh_lang())


class Loader:
    """A one-line spinner at the bottom of the window while ``view.busy``; lines scroll above it."""

    FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    TICK_S = 0.1
    CLEAR = "\r\033[K"

    def __init__(self, out: TextIO, view: "WatchView") -> None:
        self._out = out
        self._view = view
        self._lock = threading.Lock()
        self._shown = False
        self._frame = 0

    def tick(self) -> None:
        with self._lock:
            if self._view.busy:
                frame = self.FRAMES[self._frame % len(self.FRAMES)]
                self._frame += 1
                text = f"{frame} {t('thinking', self._view.lang)}"
                self._out.write(self.CLEAR + (self._view._dim(text)))
                self._out.flush()
                self._shown = True
            else:
                self._erase()

    def line(self, text: str) -> None:
        with self._lock:
            self._erase()
            print(text, file=self._out, flush=True)

    def _erase(self) -> None:
        if self._shown:
            self._out.write(self.CLEAR)
            self._out.flush()
            self._shown = False

    def run(self, stop: threading.Event) -> None:
        while not stop.wait(self.TICK_S):
            self.tick()
        with self._lock:
            self._erase()


def _stream(path: Path, view: WatchView, emit: Callable[[str], None]) -> None:
    with transport.connect(path, 2) as s:
        s.sendall(b"watch\n")
        s.settimeout(None)
        with s.makefile("rb") as f:
            for raw in f:
                try:
                    ev = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(ev, dict):
                    for line in view.feed(ev):
                        emit(line)


def watch(
    path: Path | None = None,
    out: TextIO | None = None,
    sleep: Callable[[float], None] = time.sleep,
    color: bool | None = None,
) -> int:
    out = out or sys.stdout
    path = path or socket_path()
    tty = out.isatty()
    if color is None:
        color = tty and "NO_COLOR" not in os.environ
    view = WatchView(color)

    loader = Loader(out, view) if tty else None
    stop = threading.Event()

    def emit(line: str) -> None:
        if loader is not None:
            loader.line(line)
        else:
            print(line, file=out, flush=True)

    try:
        if tty:
            out.write(f"\033]0;{WINDOW_TITLE}\007")
        if loader is not None:
            threading.Thread(target=loader.run, args=(stop,), daemon=True).start()
        while True:
            try:
                _stream(path, view, emit)
            except OSError:
                pass  # not running, or it went away
            line = view.disconnected()
            if line is not None:
                emit(line)
            sleep(RETRY_S)
    except KeyboardInterrupt:
        return 0
    finally:
        stop.set()


# ---- window ----

WINDOWS: dict[str, list[str]] = {
    # name: laleinctl arguments to run in it (the Hyprland class lives in desktop.CLASSES)
    "watch": ["watch"],
    "settings": ["settings"],
}


def _laleinctl() -> str:
    """This venv's laleinctl, by absolute path (the bar runs commands without our PATH;
    on Windows it is ``laleinctl.exe`` beside python.exe)."""
    for name in ("laleinctl", "laleinctl.exe"):
        exe = Path(sys.executable).with_name(name)
        if exe.exists():
            return str(exe)
    return shutil.which("laleinctl") or "laleinctl"


def _enable_vt() -> None:
    """Let a classic Windows console (conhost) render the watch view's colors: it prints escape
    codes raw until asked. Windows Terminal always renders them; elsewhere this does nothing."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        k = ctypes.windll.kernel32  # type: ignore[attr-defined]
        out, mode = k.GetStdHandle(-11), ctypes.c_uint32()  # STD_OUTPUT_HANDLE
        if k.GetConsoleMode(out, ctypes.byref(mode)):
            k.SetConsoleMode(out, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except (AttributeError, OSError):
        pass


def open_window(name: str) -> int:
    if name not in WINDOWS:
        print(t("unknown_window", name=name, names="/".join(WINDOWS)), file=sys.stderr)
        return 2
    try:
        desktop.toggle_window(name, [_laleinctl(), *WINDOWS[name]])  # type: ignore[arg-type]
    except SystemExit as e:  # no terminal found: desktop already printed the command
        return int(e.code or 0)
    except (OSError, subprocess.SubprocessError) as e:
        print(f"laleinctl window: {e}", file=sys.stderr)
        return 1
    return 0


# ---- character ----

SYSTEM_PYTHON = "/usr/bin/python3"  # GTK's bindings (python-gobject) are system packages
LAYER_SHELL_LIB = "/usr/lib/libgtk4-layer-shell.so"


def character_command(preview: str | None = None, look: str | None = None) -> tuple[list[str], dict[str, str]]:
    """The system-python command and extra environment that run the character overlay."""
    src = str(Path(__file__).resolve().parents[2])
    env = {"PYTHONPATH": src, "LALEINCTL": _laleinctl()}  # a click opens watch
    if Path(LAYER_SHELL_LIB).exists():  # layer-shell must be loaded before libwayland-client
        env["LD_PRELOAD"] = LAYER_SHELL_LIB
    args = [preview, *([look] if look else [])] if preview else []
    return [SYSTEM_PYTHON, "-m", "lalein.ui.character_app", *args], env


def _character(preview: str | None = None, look: str | None = None, execve: Callable[..., Any] = os.execve) -> int:
    sys_ = platform.system()
    if sys_ in ("windows", "mac"):  # drawn in this python (cairo / Quartz), no GTK needed
        import importlib

        module = importlib.import_module(f"lalein.ui.character_{'win' if sys_ == 'windows' else 'mac'}")
        return module.main([a for a in (preview, look) if a])
    cmd, extra = character_command(preview, look)
    if not Path(cmd[0]).exists():
        print(t("not_found", path=cmd[0]), file=sys.stderr)
        return 1
    execve(cmd[0], cmd, {**os.environ, **extra})
    return 0  # only reached by a fake execve


def _character_running() -> bool | None:
    """Whether the character runs (Windows, macOS: its lock is held), or None where that is not
    known (Linux: GTK keeps it to one instead)."""
    if platform.system() not in ("windows", "mac"):
        return None
    from lalein.system.supervisor import is_running
    from lalein.ui.overlay import LOCK

    return is_running(platform.runtime_dir() / LOCK)


def spawn_character(action: str = "toggle") -> int:
    """``laleinctl character <action>`` in the background (the tray's and menu bar's item,
    and the settings window's, which must not be replaced by the overlay)."""
    flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW: no console flashes up
    proc = subprocess.Popen([_laleinctl(), "character", action], stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags,
                            start_new_session=os.name != "nt")
    threading.Thread(target=proc.wait, daemon=True).start()  # reap it
    return 0


CHARACTER_PATTERN = "lalein.ui.character_app"  # the overlay's `python -m` module, as pkill -f sees it


def restart_character(run: Callable[..., Any] = subprocess.run, spawn: Callable[..., Any] = subprocess.Popen) -> int:
    """Linux: stop the running overlay and start it again, so it runs the code on disk (the
    daemon's restart does not reach it: it is no unit). Does nothing if none runs (hidden on
    purpose, or no desktop). pkill never matches itself, and this process's own command line
    (``... character restart``) does not contain the module name."""
    if platform.system() != "linux":
        return 0
    if run(["pkill", "-f", CHARACTER_PATTERN]).returncode != 0:  # 1: nothing matched
        return 0
    for _ in range(50):  # wait for it to go (GTK keeps to one instance)
        if run(["pgrep", "-f", CHARACTER_PATTERN], stdout=subprocess.DEVNULL).returncode != 0:
            break
        time.sleep(0.1)
    cmd, extra = character_command()
    spawn(cmd, env={**os.environ, **extra}, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
          stderr=subprocess.DEVNULL, start_new_session=True)
    return 0


def spawn_character_toggle() -> None:
    spawn_character("toggle")


def character_hidden() -> bool:
    """Whether the character is out of sight: hidden in the prefs, or (Windows, macOS) not running."""
    from lalein.ui import character

    return character.load_prefs(character.prefs_path()).hidden or _character_running() is False


def _character_prefs(args: list[str], launch: Callable[[], int] = lambda: _character(None)) -> int | None:
    """``character hide|show|toggle|avatar <look>|monitor <connector>``, or None for other args.
    ``launch`` starts the character when it is to be shown (the settings window spawns it)."""
    from lalein.ui import character

    path = character.prefs_path()
    if args in (["hide"], ["show"], ["toggle"]):
        hidden = {"hide": True, "show": False}.get(args[0], not character.load_prefs(path).hidden)
        if args == ["toggle"] and _character_running() is False:  # not running: start it, shown
            hidden = False
        character.update_prefs(path, hidden=hidden)
        return 0 if hidden else launch()
    if len(args) == 2 and args[0] == "avatar":
        if args[1] not in character.AVATARS:
            print(t("unknown_avatar", name=args[1], names=", ".join(character.AVATARS)), file=sys.stderr)
            return 2
        character.update_prefs(path, avatar=args[1])
        return 0
    if len(args) == 2 and args[0] == "monitor":
        character.update_prefs(path, monitor=args[1])
        return 0
    return None


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args == ["watch"]:
        _enable_vt()
        return watch()
    if args == ["settings"]:
        from lalein.ui.settings_app import main as settings_main

        return settings_main()
    if args == ["character", "restart"]:
        return restart_character()
    if args[:1] == ["character"] and (code := _character_prefs(args[1:])) is not None:
        return code
    if args[:1] == ["character"] and len(args) <= 3:
        return _character(*args[1:])
    if args[:1] == ["window"] and len(args) <= 2:
        return open_window(args[1] if len(args) == 2 else "watch")
    if args[:1] == ["set"] and len(args) == 3:
        cmd = " ".join(args)
    elif len(args) == 1 and args[0] in (*COMMANDS, "get", "bar"):
        cmd = args[0]
    else:
        print(t("usage", usage=USAGE), file=sys.stderr)
        return 2
    try:
        resp = request("status" if cmd == "bar" else cmd)
    except (OSError, ValueError):  # not running (or a garbled reply)
        if cmd == "bar":
            return 0
        print(json.dumps({"state": "off", "text": "Stopped"}, ensure_ascii=False))
        return 1
    if cmd == "bar":
        print(json.dumps(bar_output(resp), ensure_ascii=False))
        return 0
    print(json.dumps(resp, ensure_ascii=False))
    return 2 if "error" in resp else 0
