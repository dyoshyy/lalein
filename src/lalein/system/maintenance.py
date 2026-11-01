"""Restart the background service, run the self-update, say which version runs: what the setup page
and the settings window's Restart / Update rows share (every OS)."""

import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

SERVICE = "lalein.service"
RESTART_TIMEOUT_S = 90  # the unit's TimeoutStopSec is 60
UPDATE_TIMEOUT_S = 900
OUTPUT_TAIL = 1500  # characters of command output shown on the page


def restart_daemon_mac(run: Callable[..., Any] = subprocess.run, uid: int | None = None) -> None:
    """``launchctl kickstart -k`` on the daemon's LaunchAgent; raises RuntimeError when it fails."""
    from lalein.system import service

    uid = os.getuid() if uid is None else uid
    try:
        r = run(["launchctl", "kickstart", "-k", f"gui/{uid}/{service.LABELS['daemon']}"],
                capture_output=True, text=True, timeout=RESTART_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as e:
        raise RuntimeError(str(e)) from e
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or f"exit {r.returncode}").strip())


def restart_daemon_windows() -> None:
    """Stop the supervisor and everything it runs, then start it again."""
    from lalein.system import service

    try:
        service.stop_services("windows")
        service.start_services("windows")
    except Exception as e:  # the registry, the process table: whatever it is, say it
        raise RuntimeError(str(e)) from e


def restart_daemon_for(sys_: str) -> Callable[[], None]:
    return {"mac": restart_daemon_mac, "windows": restart_daemon_windows}.get(sys_, restart_daemon_linux)


def restart_daemon_linux(run: Callable[..., Any] = subprocess.run) -> None:
    """``systemctl --user restart lalein.service``; raises RuntimeError when it fails."""
    try:
        r = run(["systemctl", "--user", "restart", SERVICE], capture_output=True, text=True,
                timeout=RESTART_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as e:
        raise RuntimeError(str(e)) from e
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or f"exit {r.returncode}").strip())
    from lalein.system import service

    try:  # the on-screen character is no unit: restart it too, so it runs the new code (best effort)
        run([service._find_ctl(), "character", "restart"], capture_output=True, text=True, timeout=RESTART_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError):
        pass


def run_update(sys_: str, lang: str | None, **overrides: Any) -> tuple[int, str]:
    """``lalein update --yes`` with its output captured: a git checkout does ``git pull
    --ff-only``, ``uv sync`` and a service restart; a uv tool install starts the detached helper
    (stop, ``uv tool install``, start). Returns (exit code, output); ``overrides`` replace
    ``update.Deps`` fields (the tests')."""
    from lalein.system import update as upd

    lines: list[str] = []

    def run_captured(argv: list[str], cwd: Path) -> int:
        lines.append("$ " + " ".join(argv))
        try:
            r = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=UPDATE_TIMEOUT_S)
        except (OSError, subprocess.SubprocessError) as e:
            lines.append(str(e))
            return 1
        out = ((r.stdout or "") + (r.stderr or "")).strip()
        if out:
            lines.append(out)
        return r.returncode

    fields: dict[str, Any] = {"system": sys_, "out": lines.append, "err": lines.append,
                              "run_visible": run_captured, "ask": lambda _p: "y", "lang": lang}
    fields.update(overrides)
    code = upd.main(["--yes"], upd.Deps(**fields))
    return code, "\n".join(lines)[-OUTPUT_TAIL:]


def service_installed(sys_: str, home: Path | None = None) -> bool:
    """The always-on service is set up on this machine (so a restart means something)."""
    home = home or Path.home()
    if sys_ == "mac":
        return (home / "Library/LaunchAgents/dev.lalein.daemon.plist").exists()
    if sys_ == "windows":
        from lalein.system import service

        try:
            return service.RunKey().get(service.RUN_VALUE) is not None
        except OSError:
            return False
    return (home / ".config/systemd/user/lalein.service").exists()


def version_text(installed: str | None, install: Any) -> str:
    """``0.2.0`` plus where it runs from: a git checkout adds the short commit, e.g. ``0.2.0 (git 0019967)``."""
    v = installed or "?"
    if install.kind in ("git", "editable-tool") and install.path:
        try:
            r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=str(install.path),
                               capture_output=True, text=True, timeout=5)
            if r.returncode == 0 and r.stdout.strip():
                return f"{v} (git {r.stdout.strip()})"
        except (OSError, subprocess.SubprocessError):
            pass
        return f"{v} (git)"
    return v
