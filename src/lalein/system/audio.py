"""``lalein audio install|uninstall|output [choice]``: the Linux echo-cancel extras, so Lalein can
talk through speakers without hearing itself.

PipeWire's WebRTC echo canceller makes a sink (``zundamon_jarvis_aec_sink``: what is played to it
is taken out of the mic) and a source (``zundamon_jarvis_aec_source``: the cleaned mic, for
``mic_device``). The scripts ship in ``assets/audio`` and keep their old names, which existing
configs use:

- ``install``: copies the PipeWire config, makes the sink the default, and installs
  ``zundamon-jarvis-aec-output.service``, which keeps the sink playing on the right device;
- ``uninstall``: takes it all out and restores the previous default sink;
- ``output [auto|bluez|hdmi|<sink>]``: where the sound goes (no argument: the choice and the sinks).
"""

import subprocess
import sys
from collections.abc import Callable
from importlib import resources
from pathlib import Path
from typing import Any

from lalein.core import platform

ASSETS = Path(str(resources.files("lalein") / "assets" / "audio"))
SCRIPTS = {"install": "install-audio.sh", "uninstall": "uninstall-audio.sh", "output": "aec-output.sh"}
USAGE = "usage: lalein audio install | uninstall | output [auto|bluez|hdmi|<sink name>]"


def command(args: list[str]) -> list[str] | None:
    """The bash command for ``args``, or None when they are not a known form. Run through bash:
    package data may not keep the scripts' executable bit."""
    if not args or args[0] not in SCRIPTS:
        return None
    if args[0] != "output" and len(args) > 1 or len(args) > 2:
        return None
    return ["bash", str(ASSETS / SCRIPTS[args[0]]), *args[1:]]


def main(args: list[str], run: Callable[..., Any] = subprocess.run, system: str | None = None) -> int:
    if (system or platform.system()) != "linux":
        print("lalein audio: the echo-cancel extras are for Linux (PipeWire)", file=sys.stderr)
        return 2
    cmd = command(args)
    if cmd is None:
        print(USAGE, file=sys.stderr)
        return 2
    return run(cmd).returncode
