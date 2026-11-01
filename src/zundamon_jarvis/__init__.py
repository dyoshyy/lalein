"""zundamon-jarvis, in layers; each imports only the ones above it:

* ``core``: config, per-OS directories, display language, the control endpoint.
* ``voice``: microphone, speech detection, speech to text, VOICEVOX, playback.
* ``assistant``: the daemon, which wires ``voice`` to Claude.
* ``system``: the VOICEVOX engine, always-on services, self-update, ``doctor``.
* ``ui``: ``zundamon-jarvisctl``, the windows, the character, the menu bar / tray.
* ``setup``: the first run.

``cli`` is ``zundamon-jarvis``'s entry point and dispatches into all of them.
"""


def hello() -> str:
    return "Hello from zundamon_jarvis!"
