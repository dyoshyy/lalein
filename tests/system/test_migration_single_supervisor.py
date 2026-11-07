"""Updating a Windows install from zundamon-jarvis must never leave two supervisors.

What happened on a real machine (0.6.1): the supervisor locked ``zundamon-jarvis\\run``, the
migration then created ``lalein``, every later process looked at ``lalein\\run``, found it
free, and ``lalein service start`` started a second supervisor. Two daemons answered the mic.
"""

import json
import logging
import socket
import subprocess
import sys
import time

import pytest

from lalein.core import control, platform
from lalein.system import migrate, service, supervisor
from lalein.system.supervisor import InstanceLock


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr("lalein.system.service.find_exe", lambda s=None: "lalein.exe")
    root = tmp_path / "Local"
    (root / platform.LEGACY_APP / "voicevox_engine").mkdir(parents=True)  # only the old folder
    (root / platform.LEGACY_APP / "voicevox_engine" / "run.exe").write_text("x", encoding="utf-8")
    logger = logging.getLogger()
    handlers, level = logger.handlers[:], logger.level
    yield root
    for h in logger.handlers:  # supervisor.main() points the root logger at its log file
        if h not in handlers:
            h.close()
    logger.handlers[:], logger.level = handlers, level


class Registry:
    def __init__(self, values=None):
        self.values = dict(values or {})

    def get(self, name):
        return self.values.get(name)

    def set(self, name, value):
        self.values[name] = value

    def delete(self, name):
        self.values.pop(name, None)


def migration_creates_the_new_folder(root):
    """What the migration does first, and what used to flip every later process to it."""
    (root / platform.APP / "logs").mkdir(parents=True)


def test_the_runtime_directory_does_not_move_with_the_migration(local):
    before = platform.runtime_dir("windows"), platform.socket_path("windows")
    migration_creates_the_new_folder(local)
    assert (platform.runtime_dir("windows"), platform.socket_path("windows")) == before
    assert before[0] == local / platform.APP / "run"


def test_making_the_runtime_directory_does_not_abandon_the_old_data(local):
    platform.runtime_dir("windows")  # creates lalein\run before anything has been moved
    assert platform.data_dir("windows") == local / platform.LEGACY_APP
    assert platform.log_dir("windows") == local / platform.LEGACY_APP / "logs"
    migration_creates_the_new_folder(local)
    assert platform.data_dir("windows") == local / platform.APP


def test_no_second_supervisor_after_the_migration_starts(local, monkeypatch):
    """The reproduction: one supervisor up, the new folder appears, then `service start`."""
    first = InstanceLock(platform.runtime_dir("windows") / supervisor.LOCK)
    assert first.acquire()
    try:
        migration_creates_the_new_folder(local)
        started = []
        assert service.start_services("windows", start=started.append, exew="laleinw.exe") is False
        assert started == []
        monkeypatch.setattr(supervisor, "child_specs", lambda *a: pytest.fail("must not start children"))
        assert supervisor.main([], system="windows") == supervisor.EXIT_ALREADY_RUNNING
    finally:
        first.release()


@pytest.fixture
def old_supervisor(local):
    """A supervisor started before this fix: its lock and pid in the old folder's ``run``."""
    run = local / platform.LEGACY_APP / "run"
    run.mkdir()
    (run / supervisor.PID).write_text("4321", encoding="utf-8")
    lock = InstanceLock(run / supervisor.LOCK)
    assert lock.acquire()
    yield lock
    lock.release()


def test_a_lock_left_in_the_old_folder_still_counts(local, old_supervisor, monkeypatch):
    """The locks are split, whichever way: the second supervisor still does not start."""
    migration_creates_the_new_folder(local)
    assert supervisor.running_pid("windows") == 4321
    started = []
    assert service.start_services("windows", start=started.append, exew="laleinw.exe") is False
    assert started == []
    monkeypatch.setattr(supervisor, "child_specs", lambda *a: pytest.fail("must not start children"))
    assert supervisor.main([], system="windows") == supervisor.EXIT_ALREADY_RUNNING
    assert not (local / platform.APP / "run" / supervisor.PID).exists()


def test_a_free_lock_in_the_old_folder_does_not_count(local):
    run = local / platform.LEGACY_APP / "run"
    run.mkdir()
    (run / supervisor.LOCK).write_text("", encoding="utf-8")  # left when that supervisor exited
    (run / supervisor.PID).write_text("4321", encoding="utf-8")
    assert supervisor.running_pid("windows") is None


def test_probing_does_not_create_the_old_run_folder(local):
    assert supervisor.running_pid("windows") is None
    assert not (local / platform.LEGACY_APP / "run").exists()


def test_stop_reaches_a_supervisor_in_the_old_folder(local, old_supervisor):
    run = local / platform.LEGACY_APP / "run"
    sleeps = []

    def sleep(s):
        sleeps.append(s)
        assert (run / supervisor.STOP).exists()
        old_supervisor.release()  # it saw the stop file

    r = supervisor.stop("windows", quit_daemon=lambda: None, kill=lambda pid: pytest.fail("no kill"),
                        sleep=sleep)
    assert r == "stopped" and sleeps


def test_stop_kills_a_hung_supervisor_in_the_old_folder(local, old_supervisor):
    t = [0.0]
    killed = []

    def sleep(s):
        t[0] += s

    def kill(pid):
        killed.append(pid)
        old_supervisor.release()

    r = supervisor.stop("windows", quit_daemon=lambda: None, kill=kill, clock=lambda: t[0], sleep=sleep,
                        timeout_s=2)
    assert r == "killed" and killed == [4321]


def test_stop_asks_the_daemons_of_both_folders(local, monkeypatch):
    asked = []
    monkeypatch.setattr(control, "request", lambda cmd, path=None, timeout=3.0: asked.append((cmd, path)))
    (local / platform.LEGACY_APP / "run").mkdir()
    supervisor.stop("windows")
    assert asked == [("quit", local / platform.APP / "run" / "control.json"),
                     ("quit", local / platform.LEGACY_APP / "run" / "control.json")]


def test_a_daemon_that_does_not_answer_does_not_keep_stop_from_the_other(local, monkeypatch):
    asked = []

    def request(cmd, path=None, timeout=3.0):
        asked.append(path)
        raise ConnectionRefusedError

    monkeypatch.setattr(control, "request", request)
    (local / platform.LEGACY_APP / "run").mkdir()
    assert supervisor.stop("windows") == "not running"
    assert len(asked) == 2


@pytest.fixture
def running_main(local, monkeypatch):
    """Run ``supervisor.main`` up to its loop; ``body(quit_daemon)`` runs inside it."""

    def run(body):
        monkeypatch.setattr(supervisor, "serve",
                            lambda sup, should_stop, sleep, quit_daemon: body(quit_daemon))
        return supervisor.main([], system="windows")

    return run


def test_the_supervisors_quit_only_reaches_its_own_daemon(local, running_main, monkeypatch):
    """It must not stop a daemon in another folder (the one that is meant to stay)."""
    asked = []
    monkeypatch.setattr(control, "request", lambda cmd, path=None, timeout=3.0: asked.append((cmd, path)))
    own = local / platform.APP / "run" / "control.json"
    (local / platform.LEGACY_APP / "run").mkdir()

    def body(quit_daemon):
        migration_creates_the_new_folder(local)
        quit_daemon()

    assert running_main(body) == 0
    assert asked == [("quit", own)]


def test_the_supervisors_quit_keeps_the_endpoint_it_started_with(local, running_main, monkeypatch):
    """Whatever ``socket_path`` says later: the daemon was started with this one."""
    asked = []
    monkeypatch.setattr(control, "request", lambda cmd, path=None, timeout=3.0: asked.append(path))
    own = local / platform.APP / "run" / "control.json"

    def body(quit_daemon):
        monkeypatch.setattr(platform, "socket_path", lambda s=None: local / "elsewhere" / "control.json")
        quit_daemon()

    assert running_main(body) == 0
    assert asked == [own]


def test_a_second_daemon_finds_the_first_across_the_migration(local):
    """One endpoint before and after, so the second daemon exits 3 instead of answering too."""
    endpoint = platform.socket_path("windows")
    with socket.socket() as first_daemon:
        first_daemon.bind(("127.0.0.1", 0))
        first_daemon.listen()
        endpoint.write_text(json.dumps({"port": first_daemon.getsockname()[1], "token": "t"}), encoding="utf-8")
        migration_creates_the_new_folder(local)
        assert control.is_live(platform.socket_path("windows"))


# ---- with real processes: the OS's locks, not this process's view of them ----

SUPERVISE = """
import sys
from lalein.system import service, supervisor
service.find_exe = lambda s=None: "lalein.exe"
supervisor.child_specs = lambda *a: []  # the supervisor itself, with nothing that opens the mic
sys.exit(supervisor.main([], system="windows"))
"""


def test_two_real_supervisors_across_the_migration(local):
    run = platform.runtime_dir("windows")

    def supervise():
        return subprocess.Popen([sys.executable, "-c", SUPERVISE], stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    first = supervise()
    try:
        deadline = time.monotonic() + 60
        while not (run / supervisor.PID).exists():
            assert first.poll() is None and time.monotonic() < deadline
            time.sleep(0.1)
        migration_creates_the_new_folder(local)

        assert supervisor.running_pid("windows")  # its pid (a venv's python may be a launcher: not first.pid)
        started = []
        assert service.start_services("windows", start=started.append, exew="laleinw.exe") is False
        assert started == []
        assert supervise().wait(60) == supervisor.EXIT_ALREADY_RUNNING
        assert first.poll() is None  # the second one's attempt left the first alone

        assert supervisor.stop("windows", quit_daemon=lambda: None) == "stopped"
        assert first.wait(60) == 0
    finally:
        if first.poll() is None:
            first.kill()
            first.wait(60)


# ---- the migration itself ----

def run_migrate(registry=None, **kw):
    kw.setdefault("install", lambda s: pytest.fail("no old login entry: nothing to install"))
    return migrate.migrate("windows", registry=registry or Registry({service.RUN_VALUE: "x"}), **kw)


def test_migration_stops_a_running_supervisor_moves_and_starts_it_again(local):
    """No ``ZundamonJarvis`` Run value (the case on the real machine): it used to stay running,
    with its files open in the folder being moved."""
    events = []

    def stop():
        events.append("stop")
        return "stopped"

    def start(sys_):
        events.append(("start", sys_, supervisor.running_pid(sys_),
                       (platform.data_dir(sys_, legacy=False) / migrate.MARKER).exists()))

    run_migrate(stop=stop, start=start)
    assert events == ["stop", ("start", "windows", None, True)]
    assert (local / platform.APP / "voicevox_engine" / "run.exe").exists()
    assert not (local / platform.LEGACY_APP).exists()


def test_migration_starts_nothing_when_no_supervisor_ran(local):
    run_migrate(stop=lambda: "not running", start=lambda s: pytest.fail("none ran"))
    assert (local / platform.APP / "voicevox_engine" / "run.exe").exists()


def test_no_supervisor_can_start_while_the_folders_move(local, monkeypatch):
    seen = []
    real = migrate.move_dir

    def move_dir(old, new):
        seen.append(supervisor.main([], system="windows"))
        return real(old, new)

    monkeypatch.setattr(migrate, "move_dir", move_dir)
    monkeypatch.setattr(supervisor, "child_specs", lambda *a: pytest.fail("must not start children"))
    run_migrate(stop=lambda: "not running", start=lambda s: None)
    assert seen and set(seen) == {supervisor.EXIT_ALREADY_RUNNING}
    assert supervisor.running_pid("windows") is None  # released afterwards


def test_migration_moves_nothing_while_a_supervisor_will_not_stop(local, old_supervisor):
    lines = run_migrate(stop=lambda: "killed", start=lambda s: pytest.fail("one still runs"))
    assert any("supervisor" in line for line in lines)
    assert (local / platform.LEGACY_APP / "voicevox_engine" / "run.exe").exists()
    assert not (local / platform.APP / migrate.MARKER).exists()  # tried again at the next start


def test_a_blocked_migration_still_registers_the_login_entry(local, old_supervisor):
    """The old Run value is gone by then, and the next try will not see it."""
    installed = []
    registry = Registry({migrate.LEGACY_RUN_VALUE: "x"})
    migrate.migrate("windows", registry=registry, stop=lambda: None, install=installed.append,
                    start=lambda s: pytest.fail("install starts it"))
    assert installed == ["windows"]
    assert not (local / platform.APP / migrate.MARKER).exists()


def test_the_old_run_folder_does_not_keep_the_old_folder_alive(local):
    run = local / platform.LEGACY_APP / "run"
    run.mkdir()
    (run / supervisor.LOCK).write_text("", encoding="utf-8")
    (run / "control.json").write_text("{}", encoding="utf-8")
    run_migrate(stop=lambda: "not running", start=lambda s: None)
    assert not (local / platform.LEGACY_APP).exists()


def test_the_migration_is_done_after_the_real_sequence(local):
    """Start, migrate, start again: the start after it must not begin another migration."""
    platform.runtime_dir("windows")
    run_migrate(stop=lambda: "stopped", start=lambda s: None)
    assert not migrate.pending("windows", registry=Registry({service.RUN_VALUE: "x"}))
    assert platform.data_dir("windows") == local / platform.APP


# ---- `lalein daemon` ----

@pytest.mark.parametrize("started, sys_, runs_daemon", [
    (True, "windows", False),  # the migration is about to stop the supervisor that runs us
    (False, "windows", True),
    (True, "linux", True),  # the unit is stopped by systemd: unchanged
])
def test_daemon_yields_to_the_migration_it_started_on_windows(monkeypatch, started, sys_, runs_daemon):
    """A daemon still loading its models has no control endpoint, so it could not be asked to
    quit: the supervisor would wait 60 s and kill its tree, the detached migration included."""
    from lalein import cli
    from lalein.assistant import daemon

    ran = []
    monkeypatch.setattr(migrate, "start_if_pending", lambda: started)
    monkeypatch.setattr(platform, "system", lambda: sys_)
    monkeypatch.setattr(daemon, "main", lambda argv: ran.append(argv) or 0)
    assert cli.main(["daemon"]) == 0
    assert bool(ran) is runs_daemon
