from pathlib import Path

import pytest

from lalein.core import platform
from lalein.system import migrate


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("USERPROFILE", str(h))
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("TMPDIR", str(tmp_path / "tmp"))
    monkeypatch.setenv("APPDATA", str(h / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(h / "Local"))
    (tmp_path / "run").mkdir()
    return h


class Runner:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))


class FakeRegistry:
    def __init__(self, values=None):
        self.values = dict(values or {})

    def get(self, name):
        return self.values.get(name)

    def set(self, name, value):
        self.values[name] = value

    def delete(self, name):
        self.values.pop(name, None)


def legacy_dirs(sys_):
    """Make an install from before the rename: config.toml and the engine in the old places."""
    made = []
    for old, _new in migrate.moves(sys_):
        old.mkdir(parents=True, exist_ok=True)
        made.append(old)
    platform.config_dir(sys_).joinpath("config.toml").write_text('speed = 1.1\n', encoding="utf-8")
    engine = platform.data_dir(sys_) / "voicevox_engine"
    engine.mkdir(parents=True, exist_ok=True)
    (engine / "run").write_text("#!/bin/sh\n", encoding="utf-8")
    return made


@pytest.mark.parametrize("sys_", ["linux", "mac", "windows"])
def test_moves_names_each_app_directory_once(home, sys_):
    pairs = migrate.moves(sys_)
    assert pairs and all(old.name == "zundamon-jarvis" and new.name == "lalein" for old, new in pairs)
    assert len({new for _old, new in pairs}) == len(pairs)


def test_move_dir_renames_or_merges_and_keeps_what_it_cannot_move(tmp_path):
    old, new = tmp_path / "zundamon-jarvis", tmp_path / "lalein"
    (old / "voicevox_engine").mkdir(parents=True)
    migrate.move_dir(old, new)
    assert (new / "voicevox_engine").is_dir() and not old.exists()
    (old / "logs").mkdir(parents=True)
    (old / "config.toml").write_text("a", encoding="utf-8")
    (new / "config.toml").write_text("b", encoding="utf-8")
    lines = migrate.move_dir(old, new)
    assert (new / "logs").is_dir() and (new / "config.toml").read_text(encoding="utf-8") == "b"
    assert (old / "config.toml").exists() and any("kept" in line for line in lines)
    assert migrate.move_dir(tmp_path / "nothing", new) == []


def test_linux_migration_moves_everything_and_reinstalls_the_units(home):
    legacy_dirs("linux")
    units = home / ".config/systemd/user"
    units.mkdir(parents=True)
    for u in migrate.LEGACY_UNITS:
        (units / u).write_text("[Service]\n", encoding="utf-8")
    (home / migrate.LEGACY_DESKTOP_ENTRY).parent.mkdir(parents=True)
    (home / migrate.LEGACY_DESKTOP_ENTRY).write_text("[Desktop Entry]\n", encoding="utf-8")
    assert migrate.pending("linux", home)
    run, installed = Runner(), []
    migrate.migrate("linux", run=run, home=home, install=installed.append)
    assert ["systemctl", "--user", "disable", "--now", "zundamon-jarvis.service", "voicevox.service"] in run.calls
    assert not any((units / u).exists() for u in migrate.LEGACY_UNITS)
    assert not (home / migrate.LEGACY_DESKTOP_ENTRY).exists()
    assert (home / ".config/lalein/config.toml").read_text(encoding="utf-8") == "speed = 1.1\n"
    assert (home / ".local/share/lalein/voicevox_engine/run").exists()
    assert not (home / ".config/zundamon-jarvis").exists()
    assert installed == ["linux"]
    assert not migrate.pending("linux", home)
    assert platform.config_dir("linux") == home / ".config/lalein"


def test_linux_migration_points_the_echo_cancel_extras_at_the_new_directories(home):
    legacy_dirs("linux")
    units = home / ".config/systemd/user"
    units.mkdir(parents=True)
    (units / migrate.AEC_UNIT).write_text(
        "[Service]\nExecStart=%h/.local/share/zundamon-jarvis/audio/aec-follow.sh\n", encoding="utf-8")
    audio = home / ".local/share/zundamon-jarvis/audio"
    audio.mkdir(parents=True)
    (audio / "audio-lib.sh").write_text(
        'BIN_DIR="$HOME/.local/share/zundamon-jarvis/audio"\n'
        'UNIT=zundamon-jarvis-aec-output.service\n'
        'CHOICE_FILE="${XDG_CONFIG_HOME:-$HOME/.config}/zundamon-jarvis/aec-output"\n'
        'STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/zundamon-jarvis"\n'
        'AEC_SINK=zundamon_jarvis_aec_sink\n', encoding="utf-8")
    run = Runner()
    migrate.migrate("linux", run=run, home=home, install=lambda s: None)
    unit = (units / migrate.AEC_UNIT).read_text(encoding="utf-8")
    lib = (home / ".local/share/lalein/audio/audio-lib.sh").read_text(encoding="utf-8")
    assert "ExecStart=%h/.local/share/lalein/audio/aec-follow.sh" in unit
    assert 'BIN_DIR="$HOME/.local/share/lalein/audio"' in lib
    assert '/.config}/lalein/aec-output"' in lib and '/.local/state}/lalein"' in lib
    assert "UNIT=zundamon-jarvis-aec-output.service" in lib and "zundamon_jarvis_aec_sink" in lib  # names kept
    assert ["systemctl", "--user", "try-restart", migrate.AEC_UNIT] in run.calls
    assert migrate.repoint_audio(Runner(), home) == []  # done: nothing left to change


def test_macos_migration_takes_the_old_agents_out(home):
    legacy_dirs("mac")
    agents = home / "Library/LaunchAgents"
    agents.mkdir(parents=True)
    for label in migrate.LEGACY_LABELS:
        (agents / f"{label}.plist").write_bytes(b"<plist/>")
    run, installed = Runner(), []
    migrate.migrate("mac", run=run, home=home, uid=501, install=installed.append)
    assert ["launchctl", "bootout", "gui/501/dev.zundamon-jarvis.daemon"] in run.calls
    assert not any(agents.iterdir())
    assert (home / "Library/Application Support/lalein/voicevox_engine/run").exists()
    assert installed == ["mac"]


def test_windows_migration_replaces_the_login_entry(home):
    legacy_dirs("windows")
    registry = FakeRegistry({migrate.LEGACY_RUN_VALUE: '"C:\\zundamon-jarvisw.exe" supervise'})
    stopped, installed = [], []
    migrate.migrate("windows", home=home, registry=registry, stop=lambda: stopped.append(1),
                    install=installed.append)
    assert migrate.LEGACY_RUN_VALUE not in registry.values and stopped == [1] and installed == ["windows"]
    assert (home / "Local/lalein/voicevox_engine/run").exists()
    assert (home / "Roaming/lalein/config.toml").exists()


def test_a_fresh_install_has_nothing_to_migrate(home):
    assert not migrate.pending("linux", home)
    installed = []
    assert migrate.migrate("linux", run=Runner(), home=home, install=installed.append) == []
    assert installed == []  # no old services: nothing to (re)install


def test_the_daemon_starts_one_detached_migration_outside_its_unit(home):
    legacy_dirs("linux")
    spawned, clock = [], [1000.0]
    kw = dict(exe="/r/.venv/bin/lalein", now=lambda: clock[0], spawn=lambda argv, s, log: spawned.append(argv),
              which=lambda name: "/usr/bin/systemd-run")
    assert migrate.start_if_pending("linux", **kw)
    assert spawned[0][:4] == ["/usr/bin/systemd-run", "--user", "--collect", "--quiet"]
    assert spawned[0][-2:] == ["/r/.venv/bin/lalein", "migrate"]
    assert not migrate.start_if_pending("linux", **kw)  # just started: not again
    clock[0] += migrate.RESTART_AFTER_S + 1
    assert migrate.start_if_pending("linux", **kw)  # that one never finished: try again
    assert len(spawned) == 2


def test_the_daemon_never_fails_to_start_because_of_the_migration(home):
    legacy_dirs("linux")

    def boom(*a):
        raise OSError("no")

    assert migrate.start_if_pending("linux", exe="/x", spawn=boom, which=lambda n: None) is False


def test_lalein_daemon_looks_for_a_pending_migration_first(monkeypatch):
    from lalein import cli
    from lalein.assistant import daemon

    seen = []
    monkeypatch.setattr(migrate, "start_if_pending", lambda: seen.append("migrate"))
    monkeypatch.setattr(daemon, "main", lambda argv: seen.append(("daemon", argv)) or 0)
    assert cli.main(["daemon", "--start-paused"]) == 0
    assert seen == ["migrate", ("daemon", ["--start-paused"])]


def test_lalein_migrate_runs_it(monkeypatch, capsys):
    from lalein import cli

    monkeypatch.setattr(migrate, "migrate", lambda: ["moved a -> b"])
    assert cli.main(["migrate"]) == 0
    assert "moved a -> b" in capsys.readouterr().out
    assert Path(migrate.__file__).name == "migrate.py"
