"""The board: one window where Lalein shows the pages it makes (a diagram, a table, steps) while
it says the gist out loud. The conversation window (``watch``) stays as it is.

- ``laleinctl show <file.html>`` puts a page on the board. A file outside ``board_dir()`` is
  copied in first, so every page shown is kept there (``YYYYMMDD-HHMMSS-<name>.html``).
- A small server (``laleinctl board serve``, started by ``show`` when none answers, detached)
  listens on 127.0.0.1 only and serves the board page, the pages, and a stream of "show this"
  events (server-sent events). Every request needs the random token in ``STATE`` (runtime
  directory) and the right ``Host``, so other web pages can't read what is shown. The server
  ends after ``IDLE_EXIT_S`` with no board window open.
- When no board window is connected, ``show`` opens one: a browser app window (Chromium-family
  ``--app``, so no tabs or address bar; Omarchy's ``omarchy-launch-webapp`` on Omarchy), else
  the default browser. The window title is ``TITLE``, for window rules.
- A page runs in a sandboxed iframe (scripts yes, same origin no): it can load Mermaid or KaTeX
  from a CDN, but not reach the board or the token.
"""

import html
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from lalein.core import platform

TITLE = "Lalein Board"
STATE = f"{platform.APP}-board.json"  # in the runtime directory (shared on Linux): {"port", "token", "pid"}
IDLE_EXIT_S = 600.0  # no board window for this long: the server ends
KEEPALIVE_S = 15.0
ACK_WAIT_S = 1.5  # a window has this long to say it showed a page; else show opens a new one
START_WAIT_S = 5.0
OPEN_WAIT_S = 8.0  # a window that was opened has this long to show the page, else it is opened again
OPEN_TRIES = 2  # a browser launched while its last window is closing can quit without a window
PAGE_NAME = re.compile(r"^[\w.-]+\.html$")
CHROMIUMS = ("chromium", "chromium-browser", "google-chrome-stable", "google-chrome", "brave",
             "brave-browser", "microsoft-edge-stable", "microsoft-edge")
MAC_APPS = ("Google Chrome", "Microsoft Edge", "Brave Browser", "Chromium", "Arc")


def board_dir(sys_: str | None = None) -> Path:
    return platform.log_dir(sys_) / "board"


def state_path(sys_: str | None = None) -> Path:
    return platform.runtime_dir(sys_) / STATE


# ---- pages ----

def slug(name: str) -> str:
    s = re.sub(r"[^\w-]+", "-", Path(name).stem, flags=re.UNICODE).strip("-")
    return s[:60] or "page"


def place(src: Path, dest_dir: Path, now: float | None = None) -> Path:
    """``src`` as a page in ``dest_dir``: itself when it is already there, else a copy."""
    src = src.resolve()
    if src.parent == dest_dir.resolve() and PAGE_NAME.match(src.name):
        return src
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
    dest = dest_dir / f"{stamp}-{slug(src.name)}.html"
    shutil.copyfile(src, dest)
    return dest


def page_title(path: Path) -> str:
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:20000]
    except OSError:
        return path.stem
    m = re.search(r"<title[^>]*>(.*?)</title>", head, flags=re.DOTALL | re.IGNORECASE)
    return html.unescape(m.group(1)).strip() if m and m.group(1).strip() else path.stem


def pages(dest_dir: Path, limit: int = 50) -> list[dict[str, str]]:
    """The newest pages first: ``{"name", "title"}``."""
    if not dest_dir.is_dir():
        return []
    files = sorted((p for p in dest_dir.iterdir() if PAGE_NAME.match(p.name)), key=lambda p: p.name, reverse=True)
    return [{"name": p.name, "title": page_title(p)} for p in files[:limit]]


# ---- the server ----

SHELL = """<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root { --bg: #f6f5f2; --fg: #1d1d1f; --muted: #6b6b70; --line: #dddad3; }
@media (prefers-color-scheme: dark) { :root { --bg: #17171a; --fg: #ececf0; --muted: #9a9aa3; --line: #2c2c31; } }
* { box-sizing: border-box; }
[hidden] { display: none !important; }
html, body { margin: 0; height: 100%; background: var(--bg); color: var(--fg);
  font: 14px/1.4 system-ui, -apple-system, "Segoe UI", "Noto Sans JP", sans-serif; }
body { display: flex; flex-direction: column; }
header { display: flex; align-items: center; gap: 12px; padding: 8px 14px; border-bottom: 1px solid var(--line); }
header b { font-weight: 600; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
select { max-width: 45%; background: var(--bg); color: var(--fg); border: 1px solid var(--line); border-radius: 6px; padding: 3px 6px; font: inherit; }
iframe { flex: 1; width: 100%; border: 0; background: #fff; }
#empty { flex: 1; display: grid; place-items: center; color: var(--muted); }
</style></head>
<body>
<header><b id="title">Lalein</b><select id="history" aria-label="history"></select></header>
<div id="empty">ここに図や説明が出ます</div>
<iframe id="page" sandbox="allow-scripts allow-popups allow-popups-to-escape-sandbox" hidden></iframe>
<script>
const token = new URLSearchParams(location.search).get("t");
const frame = document.getElementById("page"), title = document.getElementById("title"),
      history_ = document.getElementById("history"), empty = document.getElementById("empty");
let list = [];
function open(name) {
  const p = list.find(x => x.name === name) || {name, title: name};
  frame.src = "/page/" + encodeURIComponent(name) + "?t=" + encodeURIComponent(token);
  frame.hidden = false; empty.hidden = true;
  title.textContent = p.title; document.title = p.title + " — __TITLE__";
  history_.value = name;
}
function fill(items) {
  list = items;
  history_.replaceChildren(...items.map(p => { const o = document.createElement("option");
    o.value = p.name; o.textContent = p.title; return o; }));
}
history_.onchange = () => open(history_.value);
const es = new EventSource("/events?t=" + encodeURIComponent(token));
es.onmessage = e => {
  const m = JSON.parse(e.data); fill(m.pages || []);
  if (m.show) { open(m.show); window.focus(); }
  fetch("/ack?t=" + encodeURIComponent(token) + "&s=" + m.serial, {method: "POST"});
};
</script></body></html>
"""


class Board:
    """What the server shares between its request threads."""

    def __init__(self, dest_dir: Path, token: str, now: Callable[[], float] = time.monotonic):
        self.dir = dest_dir
        self.token = token
        self.now = now
        self.cond = threading.Condition()
        self.current: str | None = None
        self.serial = 0  # bumped on every show
        self.clients = 0  # open event streams (one may be a window that just closed)
        self.acked = 0  # the last serial a window says it has shown
        self.last_client = now()

    def show(self, name: str, wait_s: float = ACK_WAIT_S) -> bool:
        """Send ``name`` to the open windows; whether one of them showed it in time. A closed
        window's stream is only noticed at the next write, so its count can't tell."""
        with self.cond:
            self.current = name
            self.serial += 1
            serial = self.serial
            self.cond.notify_all()
            if self.clients == 0:
                return False
            return self.cond.wait_for(lambda: self.acked >= serial, wait_s)

    def ack(self, serial: int) -> None:
        with self.cond:
            self.acked = max(self.acked, serial)
            self.cond.notify_all()

    def idle(self) -> bool:
        with self.cond:
            return self.clients == 0 and self.now() - self.last_client > IDLE_EXIT_S


def _handler(board: Board, port_of: Callable[[], int]) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a: Any) -> None:  # quiet
            pass

        def _allowed(self) -> bool:
            q = parse_qs(urlsplit(self.path).query)
            ok_host = self.headers.get("Host") in (f"127.0.0.1:{port_of()}", f"localhost:{port_of()}")
            if ok_host and secrets.compare_digest(q.get("t", [""])[0], board.token):
                return True
            self.send_error(403)
            return False

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if not self._allowed():
                return
            path = urlsplit(self.path).path
            if path == "/":
                self._send(200, SHELL.replace("__TITLE__", TITLE).encode(), "text/html; charset=utf-8")
            elif path.startswith("/page/"):
                name = path[len("/page/"):]
                f = board.dir / name
                if not PAGE_NAME.match(name) or not f.is_file():
                    self.send_error(404)
                    return
                self._send(200, f.read_bytes(), "text/html; charset=utf-8")
            elif path == "/events":
                self._events()
            else:
                self.send_error(404)

        def do_POST(self) -> None:
            if not self._allowed():
                return
            path = urlsplit(self.path).path
            if path == "/ack":
                try:
                    board.ack(int(parse_qs(urlsplit(self.path).query).get("s", [""])[0]))
                except ValueError:
                    self.send_error(400)
                    return
                self._send(204, b"", "text/plain")
                return
            if path != "/show":
                self.send_error(404)
                return
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                name = str(body["name"])
            except (ValueError, KeyError, TypeError):
                self.send_error(400)
                return
            if not PAGE_NAME.match(name) or not (board.dir / name).is_file():
                self.send_error(404)
                return
            self._send(200, json.dumps({"shown": board.show(name)}).encode(), "application/json")

        def _events(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            with board.cond:
                board.clients += 1
                seen = -1  # send the current page at once
            try:
                while True:
                    with board.cond:
                        if seen == board.serial:
                            board.cond.wait(KEEPALIVE_S)
                        changed, seen, current = seen != board.serial, board.serial, board.current
                    if changed:
                        msg = {"pages": pages(board.dir), "show": current, "serial": seen}
                        self.wfile.write(f"data: {json.dumps(msg, ensure_ascii=False)}\n\n".encode())
                    else:
                        self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
            except OSError:  # the window closed
                pass
            finally:
                with board.cond:
                    board.clients -= 1
                    board.last_client = board.now()

    return Handler


def make_server(board: Board) -> ThreadingHTTPServer:
    """The server on a free port of 127.0.0.1, showing the newest page to a window that connects."""
    latest = pages(board.dir, limit=1)
    board.current = latest[0]["name"] if latest else None
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(board, lambda: server.server_address[1]))
    server.daemon_threads = True
    return server


def serve(sys_: str | None = None) -> int:
    """``laleinctl board serve``: run the server until it has been idle for ``IDLE_EXIT_S``."""
    dest = board_dir(sys_)
    dest.mkdir(parents=True, exist_ok=True)
    board = Board(dest, secrets.token_urlsafe(24))
    server = make_server(board)
    st = state_path(sys_)
    tmp = st.with_suffix(".tmp")
    tmp.write_text(json.dumps({"port": server.server_address[1], "token": board.token, "pid": os.getpid()}),
                   encoding="utf-8")
    os.replace(tmp, st)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        while not board.idle():
            time.sleep(5)
    finally:
        server.shutdown()
        try:
            if json.loads(st.read_text(encoding="utf-8")).get("pid") == os.getpid():
                st.unlink()
        except (OSError, ValueError):
            pass
    return 0


# ---- the client side (laleinctl show) ----

def _state(sys_: str | None) -> dict[str, Any] | None:
    try:
        s = json.loads(state_path(sys_).read_text(encoding="utf-8"))
        return s if isinstance(s.get("port"), int) and isinstance(s.get("token"), str) else None
    except (OSError, ValueError):
        return None


def url(state: dict[str, Any]) -> str:
    return f"http://127.0.0.1:{state['port']}/?t={state['token']}"


def post_show(state: dict[str, Any], name: str, timeout: float = ACK_WAIT_S + 2.0) -> bool:
    """Tell the server to show ``name``; whether an open board window showed it. Raises OSError
    when no server answers."""
    req = urllib.request.Request(f"http://127.0.0.1:{state['port']}/show?t={state['token']}",
                                 data=json.dumps({"name": name}).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return bool(json.loads(r.read()).get("shown"))


def window_command(target: str, sys_: str, which: Callable[[str], str | None] = shutil.which,
                   exists: Callable[[Path], bool] = Path.exists) -> list[str] | None:
    """How to open ``target`` as an app window; None: the default browser will do."""
    if sys_ == "linux":
        if launcher := which("omarchy-launch-webapp"):
            return [launcher, target]
        for name in CHROMIUMS:
            if exe := which(name):
                return [exe, f"--app={target}"]
        return None
    if sys_ == "mac":
        for app in MAC_APPS:
            if exists(Path("/Applications") / f"{app}.app"):
                return ["open", "-na", app, "--args", f"--app={target}"]
        return None
    edge = [which("msedge")] + [str(Path(os.environ[v]) / "Microsoft/Edge/Application/msedge.exe")
                                for v in ("ProgramFiles(x86)", "ProgramFiles") if os.environ.get(v)]
    for exe in edge:
        if exe and exists(Path(exe)):
            return [exe, f"--app={target}"]
    return None


def open_window(target: str, sys_: str | None = None) -> None:
    sys_ = sys_ or platform.system()
    cmd = window_command(target, sys_)
    if cmd is None:
        webbrowser.open(target)
        return
    flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
    subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=os.name != "nt", creationflags=flags)


def _start_server(sys_: str | None) -> dict[str, Any] | None:
    from lalein.ui import ctl  # its laleinctl, beside this python

    flags = 0x08000000 if os.name == "nt" else 0
    subprocess.Popen([ctl._laleinctl(), "board", "serve"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=os.name != "nt", creationflags=flags)
    deadline = time.monotonic() + START_WAIT_S
    while time.monotonic() < deadline:
        time.sleep(0.1)
        if (s := _state(sys_)) is not None:
            return s
    return None


def show(path: Path | None, sys_: str | None = None) -> int:
    """``laleinctl show [file]``: put ``file`` (or the latest page) on the board, opening the
    board window if none is open. Prints where the page is kept."""
    dest = board_dir(sys_)
    if path is not None:
        if not path.is_file():
            print(f"laleinctl show: {path} not found", file=sys.stderr)
            return 1
        name = place(path, dest).name
        print(dest / name)
    else:
        latest = pages(dest, limit=1)
        if not latest:
            print("laleinctl show: nothing has been shown yet", file=sys.stderr)
            return 1
        name = latest[0]["name"]
    state = _state(sys_)
    try:
        if state is None:
            raise OSError("no server")
        shown = post_show(state, name)
    except (OSError, ValueError):
        state = _start_server(sys_)
        if state is None:
            print("laleinctl show: the board server did not start", file=sys.stderr)
            return 1
        shown = post_show(state, name)
    if not shown and not open_and_wait(state, name, sys_):
        print("laleinctl show: the board window did not open", file=sys.stderr)
        return 1
    return 0


def open_and_wait(state: dict[str, Any], name: str, sys_: str | None = None,
                  opener: Callable[[str, str | None], None] = open_window,
                  shown: Callable[[dict[str, Any], str], bool] = post_show,
                  clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep) -> bool:
    """Open a board window and wait until it shows ``name``; open it again if it never does."""
    for _ in range(OPEN_TRIES):
        opener(url(state), sys_)
        deadline = clock() + OPEN_WAIT_S
        while clock() < deadline:
            if shown(state, name):
                return True
            sleep(0.3)
    return False


def main(args: list[str]) -> int:
    if args == ["serve"]:
        return serve()
    if args in ([], ["open"]):
        return show(None)
    print("usage: laleinctl board [serve] | laleinctl show [file.html]", file=sys.stderr)
    return 2

