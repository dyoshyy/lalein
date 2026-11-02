"""``lalein migrate``: move an install from before the rename (zundamon-jarvis, up to 0.4) to its
new name.

What it moves:

- the directories (``platform``'s config, data and log directories, ``LEGACY_APP`` ->
  ``APP``): renamed in place, so the VOICEVOX engine (about 2 GB) is not copied. When both
  exist, the old one's entries the new one lacks are moved in, and the old one stays if
  anything is left;
- the always-on services: the old registrations (the ``zundamon-jarvis.service`` systemd unit
  and the old ``voicevox.service``, which runs the engine from the old directory; the
  ``dev.zundamon-jarvis.*`` launchd agents; the ``ZundamonJarvis`` Run value) are stopped and
  removed, then ``service.install_services`` installs the new ones and starts them;
- on Linux the app launcher entry and its icon, and the echo-cancel extras
  (``lalein audio install``): they keep their names, but their scripts and the chosen output
  are in the directories that move, so ``AEC_UNIT`` and the script copies are pointed there.

It runs once: ``MARKER`` in the new data directory says it is done. An install from before the
rename that updates to 0.5 keeps running under its old services, so ``lalein daemon`` starts
the migration on its own (``start_if_pending``), detached: it stops those services, the daemon
that started it among them. On Linux a process started from a unit dies with the unit, so it
runs in a transient unit of its own (``systemd-run``).

Everything outside (commands, the registry, the supervisor, the service install) comes in as
parameters, for the tests.
"""

import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from lalein.core import platform
from lalein.system import service, supervisor

LEGACY_LABELS = ("dev.zundamon-jarvis.daemon", "dev.zundamon-jarvis.voicevox", "dev.zundamon-jarvis.menubar")
LEGACY_UNITS = ("zundamon-jarvis.service", "voicevox.service")  # voicevox.service: same name, old path
LEGACY_RUN_VALUE = "ZundamonJarvis"
LEGACY_DESKTOP_ENTRY = Path(".local/share/applications/zundamon-jarvis.desktop")  # under home
LEGACY_ICON = Path(".local/share/icons/hicolor/256x256/apps/zundamon-jarvis.png")
MARKER = "migrated-from-zundamon-jarvis"
STARTED = "migrate.started"  # in the runtime directory: a migration was started at (time)
RESTART_AFTER_S = 15 * 60  # a start older than this failed or was interrupted: try again
LOG = "migrate.log"
AEC_UNIT = "zundamon-jarvis-aec-output.service"
AEC_PATHS = (  # (old, new) in the unit and in the copies of assets/audio/audio-lib.sh
    (".local/share/zundamon-jarvis/audio", ".local/share/lalein/audio"),
    ("/.config}/zundamon-jarvis/aec-output", "/.config}/lalein/aec-output"),
    ('/.local/state}/zundamon-jarvis"', '/.local/state}/lalein"'),
)


def moves(sys_: str) -> list[tuple[Path, Path]]:
    """(old, new) for every directory that is named after the app, each once (macOS keeps
    config and data in one; Windows keeps the logs inside the data directory)."""
    pairs: list[tuple[Path, Path]] = []
    for place in (platform.config_dir, platform.data_dir, platform.log_dir):
        new = place(sys_, legacy=False)
        old = new.parent / platform.LEGACY_APP if new.name == platform.APP else None
        if old is not None and (old, new) not in pairs:
            pairs.append((old, new))
    return pairs


def move_dir(old: Path, new: Path) -> list[str]:
    """Move ``old`` to ``new``: a rename when ``new`` is not there, else entry by entry (the ones
    ``new`` lacks). Reports what it did; leaves what it could not move."""
    if not old.is_dir():
        return []
    if not new.exists():
        new.parent.mkdir(parents=True, exist_ok=True)
        try:
            old.rename(new)
            return [f"moved {old} -> {new}"]
        except OSError:
            new.mkdir()  # e.g. another volume, or a file held open: go entry by entry
    out = []
    for entry in sorted(old.iterdir()):
        target = new / entry.name
        if target.exists():
            out.append(f"kept {entry} ({target} exists)")
            continue
        try:
            shutil.move(str(entry), str(target))
        except OSError as e:
            out.append(f"could not move {entry}: {e}")
    try:
        old.rmdir()
        out.append(f"moved {old} -> {new}")
    except OSError:
        out.append(f"left {old} (not empty)")
    return out


def _legacy_registered(sys_: str, home: Path, registry: Any) -> bool:
    if sys_ == "windows":
        return registry.get(LEGACY_RUN_VALUE) is not None
    if sys_ == "mac":
        return any((home / "Library" / "LaunchAgents" / f"{label}.plist").exists() for label in LEGACY_LABELS)
    return (home / ".config" / "systemd" / "user" / LEGACY_UNITS[0]).exists()


def pending(sys_: str | None = None, home: Path | None = None, registry: Any = None) -> bool:
    """An install from before the rename that has not been migrated yet."""
    sys_ = sys_ or platform.system()
    home = Path(home) if home else Path.home()
    if (platform.data_dir(sys_, legacy=False) / MARKER).exists():
        return False
    if any(old.is_dir() for old, _new in moves(sys_)):
        return True
    if sys_ == "windows" and registry is None:
        registry = service.RunKey()
    return _legacy_registered(sys_, home, registry)


def remove_legacy_services(sys_: str, run: Callable = subprocess.run, home: Path | None = None,
                           uid: int | None = None, registry: Any = None,
                           stop: Callable[[], Any] | None = None) -> list[str]:
    """Stop the old services and remove their registrations. Returns what was there."""
    home = Path(home) if home else Path.home()
    out = []
    if sys_ == "windows":
        registry = registry or service.RunKey()
        if registry.get(LEGACY_RUN_VALUE) is not None:
            registry.delete(LEGACY_RUN_VALUE)
            out.append(f"removed the {LEGACY_RUN_VALUE} login entry")
        (stop or (lambda: supervisor.stop(sys_)))()
        return out
    if sys_ == "mac":
        uid = uid if uid is not None else _uid()
        for label in LEGACY_LABELS:
            run(["launchctl", "bootout", f"gui/{uid}/{label}"], capture_output=True)
            plist = home / "Library" / "LaunchAgents" / f"{label}.plist"
            if plist.exists():
                plist.unlink()
                out.append(f"removed {plist}")
        return out
    units = home / ".config" / "systemd" / "user"
    present = [u for u in LEGACY_UNITS if (units / u).exists()]
    if present:
        run(["systemctl", "--user", "disable", "--now", *present], capture_output=True)
        for u in present:
            (units / u).unlink()
            out.append(f"removed {units / u}")
        run(["systemctl", "--user", "daemon-reload"], capture_output=True)
    for p in (LEGACY_DESKTOP_ENTRY, LEGACY_ICON):
        if (home / p).exists():
            (home / p).unlink()
            out.append(f"removed {home / p}")
    return out


def repoint_audio(run: Callable = subprocess.run, home: Path | None = None) -> list[str]:
    """Linux: point the echo-cancel unit and its scripts at the new directories, and restart it."""
    home = Path(home) if home else Path.home()
    unit = home / ".config" / "systemd" / "user" / AEC_UNIT
    if not unit.exists():
        return []
    audio = platform.data_dir("linux", legacy=False) / "audio"
    changed = []
    for f in (unit, *sorted(audio.glob("*.sh"))):
        text = new = f.read_text(encoding="utf-8")
        for old, to in AEC_PATHS:
            new = new.replace(old, to)
        if new != text:
            f.write_text(new, encoding="utf-8")
            changed.append(f"pointed {f} at the new directories")
    if changed:
        run(["systemctl", "--user", "daemon-reload"], capture_output=True)
        run(["systemctl", "--user", "try-restart", AEC_UNIT], capture_output=True)
    return changed


def _uid() -> int:
    import os

    return os.getuid()


def migrate(sys_: str | None = None, run: Callable = subprocess.run, home: Path | None = None,
            uid: int | None = None, registry: Any = None, stop: Callable[[], Any] | None = None,
            install: Callable[[str], Any] = service.install_services) -> list[str]:
    """Move the directories and the services to the new name (see the module). Idempotent."""
    sys_ = sys_ or platform.system()
    home = Path(home) if home else Path.home()
    if sys_ == "windows":
        registry = registry or service.RunKey()
    had_services = _legacy_registered(sys_, home, registry)
    out = remove_legacy_services(sys_, run, home, uid, registry, stop) if had_services else []
    for old, new in moves(sys_):
        out += move_dir(old, new)
    if sys_ == "linux":
        out += repoint_audio(run, home)
    if had_services:
        install(sys_)
        out.append("installed the services under the new name")
    data = platform.data_dir(sys_, legacy=False)
    data.mkdir(parents=True, exist_ok=True)
    (data / MARKER).write_text(time.strftime("%Y-%m-%d %H:%M:%S\n"), encoding="utf-8")
    return out


def start_if_pending(sys_: str | None = None, exe: str | None = None, now: Callable[[], float] = time.time,
                     spawn: Callable[[Sequence[str], str, Path], Any] | None = None,
                     which: Callable[[str], str | None] = shutil.which) -> bool:
    """``lalein daemon``'s first step: an install from before the rename gets migrated, by a
    detached ``lalein migrate`` (it stops the services this daemon runs under). False when
    there is nothing to do or one was started a moment ago."""
    sys_ = sys_ or platform.system()
    try:
        if not pending(sys_):
            return False
        started = platform.runtime_dir(sys_) / STARTED
        try:
            if now() - float(started.read_text(encoding="utf-8")) < RESTART_AFTER_S:
                return False
        except (OSError, ValueError):
            pass
        started.write_text(f"{now():.0f}", encoding="utf-8")
        argv = [exe or service.find_exe(sys_), "migrate"]
        if sys_ == "linux" and (systemd_run := which("systemd-run")):
            argv = [systemd_run, "--user", "--collect", "--quiet", f"--unit=lalein-migrate-{int(now())}", *argv]
        if spawn is None:
            from lalein.system.update import spawn_detached as spawn
        spawn(argv, sys_, platform.log_dir(sys_) / LOG)
        return True
    except Exception as e:  # never keep the daemon from starting
        print(f"lalein: could not start the migration: {e}", file=sys.stderr)
        return False


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args:
        print("usage: lalein migrate", file=sys.stderr)
        return 2
    lines = migrate()
    print("\n".join(lines) or "nothing to migrate")
    return 0
