import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from lalein.ui import board


@pytest.fixture
def server(tmp_path):
    (tmp_path / "20261101-120000-old.html").write_text("<title>古い図</title>", encoding="utf-8")
    b = board.Board(tmp_path, "tok")
    srv = board.make_server(b)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield b, {"port": srv.server_address[1], "token": "tok"}
    srv.shutdown()


def _get(state, path, token="tok", host=None):
    req = urllib.request.Request(f"http://127.0.0.1:{state['port']}{path}?t={token}")
    if host:
        req.add_header("Host", host)
    with urllib.request.urlopen(req, timeout=3) as r:
        return r.status, r.read().decode()


def test_a_page_from_anywhere_is_kept_in_the_board_folder(tmp_path):
    src = tmp_path / "work" / "How DNS works!.html"
    src.parent.mkdir()
    src.write_text("<html><title>DNS の仕組み</title></html>", encoding="utf-8")
    dest = tmp_path / "board"
    kept = board.place(src, dest, now=0)
    assert kept.parent == dest and kept.name.endswith("-How-DNS-works.html")
    assert board.place(kept, dest) == kept  # already there: shown as it is
    assert board.pages(dest) == [{"name": kept.name, "title": "DNS の仕組み"}]


def test_the_server_shows_pages_only_with_the_token_and_its_own_host(server):
    b, state = server
    assert _get(state, "/")[1].count(board.TITLE) >= 1
    assert "古い図" in _get(state, "/page/20261101-120000-old.html")[1]
    for bad in (dict(token="nope"), dict(host="evil.example:80")):
        with pytest.raises(urllib.error.HTTPError) as e:
            _get(state, "/", **bad)
        assert e.value.code == 403
    for name in ("../secret.html", "nothing.html"):
        with pytest.raises(urllib.error.HTTPError):
            _get(state, f"/page/{name}")


def test_show_reaches_an_open_window_and_says_how_many_there_are(server, tmp_path):
    b, state = server
    assert board.post_show(state, "20261101-120000-old.html") is False  # no window yet: show opens one
    got = []
    stream = urllib.request.urlopen(f"http://127.0.0.1:{state['port']}/events?t=tok", timeout=5)

    def read():
        for line in stream:
            if line.startswith(b"data: "):
                got.append(json.loads(line[6:]))
                if len(got) == 2:
                    return

    t = threading.Thread(target=read, daemon=True)
    t.start()
    for _ in range(50):
        if got:
            break
        threading.Event().wait(0.05)
    assert got[0]["show"] == "20261101-120000-old.html"  # the newest at once

    def ack_after_showing():  # what the board page does
        for _ in range(100):
            if len(got) == 2:
                urllib.request.urlopen(urllib.request.Request(
                    f"http://127.0.0.1:{state['port']}/ack?t=tok&s={got[1]['serial']}", method="POST"), timeout=3)
                return
            threading.Event().wait(0.02)

    (tmp_path / "20261102-090000-new.html").write_text("<title>新しい図</title>", encoding="utf-8")
    threading.Thread(target=ack_after_showing, daemon=True).start()
    assert board.post_show(state, "20261102-090000-new.html") is True
    t.join(5)
    assert got[1]["show"] == "20261102-090000-new.html" and got[1]["pages"][0]["title"] == "新しい図"
    stream.close()
    assert b.show("20261102-090000-new.html", wait_s=0.1) is False  # a stream left open, but no window answers
    with pytest.raises(urllib.error.HTTPError):
        board.post_show(state, "missing.html")


def test_the_window_is_an_app_window_where_a_chromium_is_there():
    which = {"omarchy-launch-webapp": "/o/omarchy-launch-webapp", "chromium": "/usr/bin/chromium"}.get
    assert board.window_command("U", "linux", which) == ["/o/omarchy-launch-webapp", "U"]
    assert board.window_command("U", "linux", lambda n: "/usr/bin/chromium" if n == "chromium" else None) == [
        "/usr/bin/chromium", "--app=U"]
    assert board.window_command("U", "linux", lambda n: None) is None  # the default browser
    assert board.window_command("U", "mac", lambda n: None, lambda p: p.name == "Google Chrome.app") == [
        "open", "-na", "Google Chrome", "--args", "--app=U"]
    assert board.window_command("U", "mac", lambda n: None, lambda p: False) is None
    assert board.window_command("U", "windows", lambda n: r"C:\e\msedge.exe", lambda p: True) == [
        r"C:\e\msedge.exe", "--app=U"]


def test_show_needs_a_page(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(board, "board_dir", lambda sys_=None: tmp_path / "board")
    assert board.show(tmp_path / "missing.html") == 1
    assert board.show(None) == 1 and "nothing has been shown" in capsys.readouterr().err
    assert Path(board.__file__).name == "board.py"
