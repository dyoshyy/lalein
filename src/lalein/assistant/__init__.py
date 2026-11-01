"""The always-listening assistant: ``daemon`` wires ``voice`` to Claude and runs it. ``orchestrator``
decides who speaks when, ``brain`` talks to Claude, ``policy`` decides which tool calls need a
spoken yes/no; ``activity`` and ``labels`` turn what happens into watch events, ``convlog`` writes
the conversation log, ``live_settings`` changes settings while it runs. Uses ``core`` and
``voice``."""
