import plistlib
import sys
from pathlib import Path

import pytest

from lalein.system import service

# A launchd PATH is ':'-joined POSIX directories, built with the host's Path: only meaningful on a
# POSIX host (on Windows "C:\\Users\\..." has a ':' of its own).
posix_plist_path = pytest.mark.skipif(sys.platform == "win32", reason="launchd PATH needs POSIX paths")


@posix_plist_path
def test_plist_has_no_api_key_and_keeps_alive(tmp_path):
    data = service.launch_agent_plist("dev.lalein.daemon", ["/x/lalein", "daemon"], tmp_path / "d.log")
    p = plistlib.loads(data)
    assert p["Label"] == "dev.lalein.daemon"
    assert p["ProgramArguments"] == ["/x/lalein", "daemon"]
    assert p["RunAtLoad"] is True and p["KeepAlive"] is True
    assert p["ProcessType"] == "Interactive"
    assert p["StandardOutPath"] == p["StandardErrorPath"] == str(tmp_path / "d.log")
    env = p["EnvironmentVariables"]
    assert "ANTHROPIC_API_KEY" not in env
    parts = env["PATH"].split(":")
    for need in (str(Path.home() / ".local/bin"), "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"):
        assert need in parts


def test_plist_path_includes_claude_dir(tmp_path):
    p = plistlib.loads(service.launch_agent_plist("l", ["a"], tmp_path / "l", extra_path=["/opt/claude/bin"]))
    assert "/opt/claude/bin" in p["EnvironmentVariables"]["PATH"].split(":")


class Runner:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))

        class R:
            returncode = 0
            stdout = ""

        return R()


@posix_plist_path
def test_install_services_mac_writes_three_agents(tmp_path):
    run = Runner()
    service.install_services(
        "mac", run=run, home=tmp_path, uid=501, exe="/u/bin/lalein", claude="/c/bin/claude"
    )
    agents = tmp_path / "Library" / "LaunchAgents"
    names = sorted(p.name for p in agents.glob("*.plist"))
    assert names == [
        "dev.lalein.daemon.plist",
        "dev.lalein.menubar.plist",
        "dev.lalein.voicevox.plist",
    ]
    d = plistlib.loads((agents / "dev.lalein.daemon.plist").read_bytes())
    assert d["ProgramArguments"] == ["/u/bin/lalein", "daemon"]
    assert "/c/bin" in d["EnvironmentVariables"]["PATH"].split(":")
    m = plistlib.loads((agents / "dev.lalein.menubar.plist").read_bytes())
    assert m["ProgramArguments"] == ["/u/bin/lalein", "menubar"]
    v = plistlib.loads((agents / "dev.lalein.voicevox.plist").read_bytes())
    assert v["ProgramArguments"][1:] == ["--host", "127.0.0.1", "--port", "50021"]  # like voicevox.service
    assert v["ProgramArguments"][0].endswith("voicevox_engine/run")
    boots = [c for c in run.calls if c[:2] == ["launchctl", "bootstrap"]]
    assert [c[2] for c in boots] == ["gui/501"] * 3
    assert (tmp_path / "Library" / "Logs" / "lalein").is_dir()


def test_uninstall_services_mac(tmp_path):
    run = Runner()
    service.install_services("mac", run=run, home=tmp_path, uid=501, exe="/e", claude=None)
    run.calls.clear()
    service.uninstall_services("mac", run=run, home=tmp_path, uid=501)
    assert not list((tmp_path / "Library" / "LaunchAgents").glob("*.plist"))
    assert len([c for c in run.calls if c[:2] == ["launchctl", "bootout"]]) == 3


def test_install_services_skips_when_present(tmp_path):
    units = tmp_path / ".config" / "systemd" / "user"
    units.mkdir(parents=True)
    for n in ("voicevox.service", "lalein.service"):
        (units / n).write_text("x", encoding="utf-8")
    run = Runner()
    service.install_services("linux", run=run, home=tmp_path)
    assert run.calls == []
    assert (units / "voicevox.service").read_text(encoding="utf-8") == "x"


def test_install_services_linux_installs_and_enables(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    for n in ("voicevox.service", "lalein.service"):
        (src / n).write_text(n, encoding="utf-8")
    run = Runner()
    service.install_services("linux", run=run, home=tmp_path / "h", unit_src=src)
    assert (tmp_path / "h/.config/systemd/user/voicevox.service").read_text(encoding="utf-8") == "voicevox.service"
    assert ["systemctl", "--user", "daemon-reload"] in run.calls
    assert ["systemctl", "--user", "enable", "--now", "voicevox.service", "lalein.service"] in run.calls


def test_the_linux_unit_runs_this_install_wherever_it_is_checked_out(tmp_path):
    from importlib import resources

    template = (resources.files("lalein") / "assets" / "systemd" / "lalein.service").read_text(encoding="utf-8")
    unit = service.render_unit(template, "/home/u/src/zj/.venv/bin/lalein")
    assert "ExecStart=/home/u/src/zj/.venv/bin/lalein daemon" in unit.splitlines()
    assert "WorkingDirectory=/home/u/src/zj" in unit.splitlines()
    tool = service.render_unit(template, "/home/u/.local/bin/lalein")  # a uv tool install
    assert "ExecStart=/home/u/.local/bin/lalein daemon" in tool.splitlines()
    assert "WorkingDirectory=%h" in tool.splitlines()
    assert service.render_unit("ExecStart=/x/run\n", "/e/lalein") == "ExecStart=/x/run\n"  # voicevox.service


def test_install_services_linux_writes_the_exe_into_the_unit(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "voicevox.service").write_text("[Service]\nExecStart=%h/.local/share/lalein/voicevox_engine/run\n")
    (src / "lalein.service").write_text("[Service]\nWorkingDirectory=%h/repos/lalein\n"
                                        "ExecStart=%h/repos/lalein/.venv/bin/lalein daemon\n")
    service.install_services("linux", run=Runner(), home=tmp_path / "h", unit_src=src, exe="/r/zj/.venv/bin/lalein")
    unit = (tmp_path / "h/.config/systemd/user/lalein.service").read_text(encoding="utf-8")
    assert "ExecStart=/r/zj/.venv/bin/lalein daemon" in unit and "WorkingDirectory=/r/zj" in unit


def test_menubar_agent_restarts_only_on_crash(tmp_path):
    service.install_services("mac", run=Runner(), home=tmp_path, uid=1, exe="/e", claude=None)
    a = tmp_path / "Library/LaunchAgents"
    m = plistlib.loads((a / "dev.lalein.menubar.plist").read_bytes())
    assert m["KeepAlive"] == {"SuccessfulExit": False}
    d = plistlib.loads((a / "dev.lalein.daemon.plist").read_bytes())
    assert d["KeepAlive"] is True


def test_packaged_unit_templates_exist():
    from importlib import resources

    base = resources.files("lalein") / "assets" / "systemd"
    assert (base / "voicevox.service").is_file() and (base / "lalein.service").is_file()


def test_linux_install_adds_the_character_to_the_app_launcher(tmp_path):
    units = tmp_path / ".config" / "systemd" / "user"
    units.mkdir(parents=True)
    for n in ("voicevox.service", "lalein.service"):
        (units / n).write_text("x", encoding="utf-8")  # already installed: the launcher entry is still written
    service.install_services("linux", run=Runner(), home=tmp_path, ctl="/opt/zj/bin/laleinctl")
    entry = (tmp_path / ".local/share/applications/lalein.desktop").read_text(encoding="utf-8")
    assert "\nExec=/opt/zj/bin/laleinctl character\n" in entry
    assert "\nIcon=lalein\n" in entry and "\nType=Application\n" in entry
    icon = tmp_path / ".local/share/icons/hicolor/256x256/apps/lalein.png"
    assert icon.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_linux_uninstall_removes_the_launcher_entry(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    for n in ("voicevox.service", "lalein.service"):
        (src / n).write_text(n, encoding="utf-8")
    service.install_services("linux", run=Runner(), home=tmp_path, ctl="/x/laleinctl",
                             unit_src=src)
    service.uninstall_services("linux", run=Runner(), home=tmp_path)
    assert not (tmp_path / ".local/share/applications/lalein.desktop").exists()
    assert not (tmp_path / ".local/share/icons/hicolor/256x256/apps/lalein.png").exists()


# ---- Windows: the Run key and the supervisor ----

class FakeRegistry:
    def __init__(self, values=None, approved=None):
        self.values = dict(values or {})
        self._approved = approved

    def get(self, name):
        return self.values.get(name)

    def set(self, name, value):
        self.values[name] = value

    def delete(self, name):
        self.values.pop(name, None)

    def approved(self, name):
        return self._approved


EXEW = r"C:\Users\Some User\AppData\Roaming\uv\tools\lalein\Scripts\laleinw.exe"


def test_windows_install_registers_the_quoted_launcher_and_restarts_the_supervisor():
    reg, events = FakeRegistry(), []
    service.install_services("windows", registry=reg, exe=EXEW,
                             start=lambda argv: events.append(("start", argv)),
                             stop=lambda: events.append(("stop",)))
    assert reg.values == {"Lalein": f'"{EXEW}" supervise'}
    assert events == [("stop",), ("start", [EXEW, "supervise"])]  # stop first: a re-run loads the new config


def test_windows_uninstall_removes_the_entry_and_stops_everything():
    reg, stopped = FakeRegistry({"Lalein": "x", "Other": "y"}), []
    service.uninstall_services("windows", registry=reg, stop=lambda: stopped.append(1))
    assert reg.values == {"Other": "y"} and stopped == [1]


def test_windows_status_reports_each_part():
    reg = FakeRegistry({"Lalein": f'"{EXEW}" supervise'})
    out = service.service_status("windows", registry=reg, supervisor_pid=lambda: 1234,
                                 daemon=lambda: {"state": "listening", "text": "Listening"},
                                 engine_up=lambda: True)
    assert out.splitlines() == [
        rf'autostart (HKCU\Software\Microsoft\Windows\CurrentVersion\Run\Lalein): "{EXEW}" supervise',
        "supervisor: running (pid 1234)",
        "daemon: Listening",
        "engine: running",
    ]


def test_windows_status_when_nothing_is_there():
    out = service.service_status("windows", registry=FakeRegistry(), supervisor_pid=lambda: None,
                                 daemon=lambda: None, engine_up=lambda: False)
    assert "not registered" in out
    assert "supervisor: not running" in out and "daemon: not running" in out and "engine: not running" in out


def test_windows_status_says_when_startup_apps_switched_it_off():
    reg = FakeRegistry({"Lalein": "x"}, approved=False)
    out = service.service_status("windows", registry=reg, supervisor_pid=lambda: None,
                                 daemon=lambda: None, engine_up=lambda: False)
    assert "Settings > Apps > Startup" in out.splitlines()[0]


def test_startup_approved_bytes():
    assert service.startup_approved(bytes([2] + [0] * 11)) is True
    assert service.startup_approved(bytes([6] + [0] * 11)) is True
    assert service.startup_approved(bytes([3] + [0] * 11)) is False
    assert service.startup_approved(b"") is None and service.startup_approved("x") is None


def test_windows_start_only_when_not_running():
    started = []
    assert service.start_services("windows", start=started.append, exew="w.exe", running=lambda: False)
    assert not service.start_services("windows", start=started.append, exew="w.exe", running=lambda: True)
    assert started == [["w.exe", "supervise"]]


def test_find_exew_prefers_the_windowless_launcher_beside_python(tmp_path, monkeypatch):
    scripts = tmp_path / "Scripts"
    scripts.mkdir()
    (scripts / "lalein.exe").write_text("", encoding="utf-8")
    monkeypatch.setattr(service.sys, "executable", str(scripts / "python.exe"))
    monkeypatch.setattr(service.shutil, "which", lambda n: None)
    assert service.find_exew("windows") == str(scripts / "lalein.exe")  # no w: the console one
    (scripts / "laleinw.exe").write_text("", encoding="utf-8")
    assert service.find_exew("windows") == str(scripts / "laleinw.exe")
    assert service.find_exe("windows") == str(scripts / "lalein.exe")


def test_start_detached_breaks_away_and_falls_back(monkeypatch):
    calls = []

    def popen(argv, **kw):
        calls.append(kw["creationflags"])
        if kw["creationflags"] & service.CREATE_BREAKAWAY_FROM_JOB:
            raise PermissionError("access denied")

    monkeypatch.setattr(service.subprocess, "Popen", popen)
    service.start_detached(["w.exe", "supervise"])
    assert calls[0] & service.CREATE_BREAKAWAY_FROM_JOB and calls[0] & service.DETACHED_PROCESS
    assert calls[1] == service.DETACHED_PROCESS | service.CREATE_NEW_PROCESS_GROUP
