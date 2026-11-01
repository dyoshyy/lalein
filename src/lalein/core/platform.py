"""OS-specific directories. Linux paths are the long-standing XDG-ish ones; macOS uses ~/Library;
Windows uses %APPDATA% (config) and %LOCALAPPDATA% (everything else).

Import as ``lalein.core.platform`` (absolute imports keep the stdlib ``platform`` intact).
Every function takes an optional ``sys_`` ("linux" | "mac" | "windows") so tests can pick the OS.

Before 0.5 the app was called zundamon-jarvis (``LEGACY_APP``). Until ``lalein migrate``
(``lalein.system.migrate``) moves them, its directories are used where there are no new
ones: ``config_dir``, ``data_dir`` and ``log_dir`` give the old one when only it exists. The
runtime directory and the control endpoint are made afresh at every start, so they are new
only. ``*_dir(..., legacy=False)`` is the new place whatever exists (the migration's target).

The control endpoint (``socket_path``) is a Unix socket on Linux and macOS. Windows has no
``asyncio`` Unix sockets, so there it is a small JSON file (``control.json``: the localhost TCP
port and a secret token, see ``lalein.core.transport``) in a per-user directory.
"""

import os
import stat
import sys
from pathlib import Path
from typing import Literal

APP = "lalein"
LEGACY_APP = "zundamon-jarvis"  # the name before 0.5


System = Literal["linux", "mac", "windows"]


def system() -> System:
    if sys.platform == "darwin":
        return "mac"
    if sys.platform == "win32":
        return "windows"
    return "linux"


def _sys(sys_: str | None) -> str:
    return sys_ or system()


def _mac_support(app: str) -> Path:
    return Path.home() / "Library" / "Application Support" / app


def _win_env(name: str, fallback: str) -> Path:
    return Path(os.environ.get(name) or str(Path.home() / fallback))


def _either(place, legacy: bool) -> Path:
    """``place(APP)``, or ``place(LEGACY_APP)`` when only that exists (not yet migrated)."""
    new = place(APP)
    if legacy and not new.exists() and (old := place(LEGACY_APP)).exists():
        return old
    return new


def config_dir(sys_: str | None = None, legacy: bool = True) -> Path:
    s = _sys(sys_)
    if s == "mac":
        return _either(_mac_support, legacy)
    if s == "windows":
        return _either(lambda app: _win_env("APPDATA", "AppData/Roaming") / app, legacy)
    return _either(lambda app: Path.home() / ".config" / app, legacy)


def data_dir(sys_: str | None = None, legacy: bool = True) -> Path:
    s = _sys(sys_)
    if s == "mac":
        return _either(_mac_support, legacy)
    if s == "windows":
        return _either(lambda app: _win_env("LOCALAPPDATA", "AppData/Local") / app, legacy)
    return _either(lambda app: Path.home() / ".local" / "share" / app, legacy)


def state_home(sys_: str | None = None) -> Path:
    """Parent of the log directory (and of the pre-rename ``jarvis`` one) on Linux."""
    s = _sys(sys_)
    if s == "mac":
        return Path.home() / "Library" / "Logs"
    if s == "windows":
        return data_dir(s)
    return Path(os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state"))


def log_dir(sys_: str | None = None, legacy: bool = True) -> Path:
    if _sys(sys_) == "windows":
        return data_dir("windows", legacy) / "logs"
    return _either(lambda app: state_home(sys_) / app, legacy)


def _private_dir(d: Path) -> Path:
    """Create ``d`` as a 0700 directory owned by us; refuse a symlink or someone else's directory.
    (Windows has no uid or mode bits: there the directory is under the user's own profile.)"""
    if not hasattr(os, "getuid"):
        d.mkdir(parents=True, exist_ok=True)
        return d
    try:
        d.mkdir(mode=0o700, parents=True)
    except FileExistsError:
        pass
    st = d.lstat()
    if not stat.S_ISDIR(st.st_mode):  # includes symlinks (lstat does not follow them)
        raise RuntimeError(f"{d} is not a real directory (symlink or file); refusing to use it")
    if st.st_uid != os.getuid():
        raise RuntimeError(f"{d} is owned by uid {st.st_uid}, not {os.getuid()}; refusing to use it")
    if stat.S_IMODE(st.st_mode) != 0o700:
        d.chmod(0o700)
    return d


def runtime_dir(sys_: str | None = None) -> Path:
    s = _sys(sys_)
    if s == "windows":
        return _private_dir(data_dir(s) / "run")
    if s == "mac":
        return _private_dir(Path(os.environ.get("TMPDIR") or "/tmp") / f"{APP}-{os.getuid()}")
    return Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")


def socket_path(sys_: str | None = None) -> Path:
    if _sys(sys_) == "windows":
        return runtime_dir(sys_) / "control.json"
    return runtime_dir(sys_) / f"{APP}.sock"
