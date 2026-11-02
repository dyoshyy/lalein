import subprocess

from lalein.system import audio


def test_the_echo_cancel_scripts_ship_with_the_package():
    for script in (*audio.SCRIPTS.values(), "audio-lib.sh"):
        assert (audio.ASSETS / script).is_file()
    assert (audio.ASSETS / "pipewire" / "zundamon-jarvis-echo-cancel.conf").is_file()
    assert (audio.ASSETS / "systemd" / "zundamon-jarvis-aec-output.service").is_file()


def test_lalein_audio_runs_the_packaged_script_through_bash():
    ran = []

    def run(cmd):
        ran.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    assert audio.main(["install"], run, "linux") == 0
    assert audio.main(["output", "bluez"], run, "linux") == 0
    assert ran == [["bash", str(audio.ASSETS / "install-audio.sh")],
                   ["bash", str(audio.ASSETS / "aec-output.sh"), "bluez"]]
    assert audio.main(["install", "x"], run, "linux") == 2 and audio.main([], run, "linux") == 2
    assert audio.main(["install"], run, "mac") == 2  # PipeWire is Linux's
    assert len(ran) == 2
