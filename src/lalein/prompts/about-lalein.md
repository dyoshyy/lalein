# Lalein: reference for Lalein itself

Read this when the user asks how you (Lalein) work, where something is kept, or how to fix or change something. Answer out loud in a sentence or two; don't read this file out.

## The parts

- **The daemon** (`lalein daemon`) is the program that does everything below. It runs as a login service:
  - Linux: the systemd user units `lalein.service` and `voicevox.service`.
  - macOS: the launchd agents `dev.lalein.daemon`, `dev.lalein.voicevox` and `dev.lalein.menubar` (the menu bar icon).
  - Windows: the `Lalein` value in the registry's Run key starts `laleinw supervise`, which keeps the daemon, VOICEVOX and the tray icon running.
- **Ears:**
  - The microphone is read continuously, and a voice activity detector (Silero VAD) finds where speech starts and ends.
  - Whisper large-v3-turbo transcribes the speech on the computer: faster-whisper on Linux and Windows, MLX on Apple silicon.
  - With speakers instead of headphones, the mic hears Lalein. On a Mac with built-in speakers, Lalein stops listening while it talks (`duplex = "half"`). On Linux, PipeWire echo cancellation can be installed (`lalein audio install`; its mic is `zundamon_jarvis_aec_source`).
- **Brain:** a Claude Code session through the Claude Agent SDK, using the user's `claude` login (never the API key).
  - It runs in the working directory, so that folder's CLAUDE.md, `.claude/` settings, skills and MCP servers apply, as do the user's own (`~/.claude`).
  - An optional notes folder is added as an extra directory.
  - The session closes after 30 idle minutes, or when the model is changed and it is idle.
- **Mouth:** VOICEVOX, a local speech synthesizer at http://127.0.0.1:50021, says each sentence as it arrives. Markdown, code and `[[silent]]` are never spoken. If a reply has no sentence after 3 seconds, a short filler is said.
- **Character:** an always-on-top figure that shows listening, thinking and speaking.
  - Click it for the conversation window. Right-click it for its look, speech bubble, display and hiding. Drag it to move it, onto another display too.
  - Its looks are bot, robo, cat, ghost, onigiri and edamame. Its position and look are kept in `character.json` in the logs folder.
- **Windows:**
  - `laleinctl watch` is the conversation: what was heard, what was said, and the tools used.
  - `laleinctl settings` is the settings window: voice, speed, model, mic and language.
  - On Linux with Hyprland, `laleinctl window watch|settings` toggles them as floating windows (window classes `lalein-watch` and `lalein-settings`).

## Confirmations

A spoken yes or no is asked before these, and nothing else:

- sending mail or messages to people
- calendar changes
- deleting files or data
- anything to do with money
- `sudo`
- force pushes

Everything else runs at once: reading, writing and editing files, and running other commands. A "no", silence, or an unclear answer cancels the action.

## Files

| What | Linux | macOS | Windows |
|---|---|---|---|
| Settings (`config.toml`) | `~/.config/lalein/` | `~/Library/Application Support/lalein/` | `%APPDATA%\lalein\` |
| VOICEVOX engine, models | `~/.local/share/lalein/` | `~/Library/Application Support/lalein/` | `%LOCALAPPDATA%\lalein\` |
| Logs, conversation per day (`YYYY-MM-DD.jsonl`), `character.json` | `~/.local/state/lalein/` | `~/Library/Logs/lalein/` | `%LOCALAPPDATA%\lalein\logs\` |

`config.toml` keys:

- `speaker_id`: the VOICEVOX style id.
- `speed`: 0.8 to 2.0.
- `model`: sonnet, opus or haiku. Empty means the CLI's default.
- `workdir`: the working directory.
- `knowledge_dir`: the notes folder.
- `mic_device`: `default`, or a device name.
- `duplex`: auto, full or half.
- `language`: ja or en. This is the windows' language; speech follows the user.

`laleinctl set` changes speed, speaker, model, mic and language, both live and in the file. Other keys take effect after a restart.

## Commands

- `laleinctl`:
  - `status` and `get`: the state and settings.
  - `toggle`, `pause` and `resume`.
  - `set <key> <value>`.
  - `watch` and `settings`.
  - `character ...`.
  - `debug`: what the mic hears.
- `lalein`:
  - `doctor`: checks the installation and says what is wrong.
  - `update [--check]`: installs the newest release. A Linux git checkout updates with `git pull` and `uv sync`.
  - `voices`: lists and plays the voices.
  - `setup`: the setup window.
  - `service install|uninstall|status|start|stop`.
  - `uninstall`.
  - `engine install`, `models download` and `migrate`.

## When something is wrong

- **It doesn't react:** run `laleinctl debug` to see whether the mic hears anything, and `lalein doctor`.
- **No voice:** VOICEVOX must answer at http://127.0.0.1:50021/version.
- **Logs:**
  - Linux: `journalctl --user -u lalein.service`.
  - macOS: `~/Library/Logs/lalein/`.
  - Windows: the logs folder.
- **Restarting** is for the user: the settings window's Restart, the menu, or `systemctl --user restart lalein.service` on Linux.

## The name

Lalein was called zundamon-jarvis before 0.5. The source is https://github.com/dyoshyy/lalein.
