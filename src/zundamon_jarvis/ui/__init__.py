"""What the user sees and types, all talking to the daemon through ``core.control``:
``zundamon-jarvisctl`` (``ctl``), the watch / settings windows (``desktop``, ``settings_app``),
the on-screen character (``character``: the pure state, ``character_app``: the GTK overlay), the
macOS menu bar (``menubar``) and the Windows tray (``tray``). Uses ``core``, ``voice``,
``assistant.live_settings`` and ``system``."""
