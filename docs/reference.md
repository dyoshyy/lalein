# Reference

## Installer options

| variable | effect |
|---|---|
| `ZJ_VERSION=v0.1.0` | install that release instead of the latest |
| `ZJ_DRY_RUN=1` | print what would run, change nothing |
| `ZJ_LANG=ja\|en` | installer language (default: the OS's) |
| `ZJ_CLI=1` (Mac) | do everything in the terminal (`engine install`, `models download`, `setup --cli`) |

On Windows set them with `$env:ZJ_VERSION = 'v0.1.0'` before the one-liner.

## Setup

`lalein setup` serves the setup page from 127.0.0.1 and shows it in an app window (pywebview: WKWebView
on Mac, WebView2 on Windows). On Linux, or when no window can be made, it opens in the browser instead and
the terminal prints the URL. `setup --cli` asks the same questions in the terminal. Every answer is saved as soon as it is given.

## `config.toml`

| key | meaning |
|---|---|
| `workdir` | where Claude Code runs (its `CLAUDE.md`, memory and hooks apply). Default: home |
| `knowledge_dir` | a notes folder given to Claude as an extra directory. Default: none |
| `model` | `sonnet` (default), `opus` or `haiku` |
| `speed` | voice speed, 0.8–2.0 (default 1.2) |
| `speaker_id` | VOICEVOX style id; audition with `lalein voices` |
| `mic_device` | input device name or PortAudio index. Default: the OS default |
| `duplex` | `auto` (default), `full` (barge-in) or `half` (mic ignored while she speaks). `auto` = half on Mac built-in speakers and on Windows without headphones |
| `language` | display language `ja` / `en`. Default: the OS's |

Comments and unknown keys are kept when the tool edits the file. `laleinctl get` / `set speed|speaker|model|mic|language <value>` changes them live.

## Where things are

| | Linux | macOS | Windows |
|---|---|---|---|
| config | `~/.config/lalein/` | `~/Library/Application Support/lalein/` | `%APPDATA%\lalein\` |
| engine, models | `~/.local/share/lalein/` | `~/Library/Application Support/lalein/` | `%LOCALAPPDATA%\lalein\` |
| logs | `~/.local/state/lalein/` | `~/Library/Logs/lalein/` | `%LOCALAPPDATA%\lalein\logs\` |
| service | systemd user units | LaunchAgents | `Lalein` in `HKCU\…\CurrentVersion\Run` |

## What Lalein knows about itself

Each conversation starts with a short description of Lalein appended to Claude Code's system prompt: how a turn works, this install's version, folders and settings, and the `laleinctl` commands that change them. So you can ask it how it works, where its logs are, or tell it to talk faster or switch looks. For anything else about itself it reads a fuller reference that ships with it. The text is in `src/lalein/prompts/` (`voice-mode.md`, `about-self.md`, `about-lalein.md`).

## The character

`laleinctl character` puts it on screen. On Mac and Windows, the menu's **Show / hide the character** does the same. On Linux it is also in the app launcher.

- Drag it to move it, onto another display too. On Linux this needs Hyprland.
- Click it for the conversation.
- Right-click it for the speech bubble, its look (bot, robot, cat, ghost, onigiri, edamame), the display it is on, and hiding it.
- `laleinctl character hide | show | toggle` hides or shows it. `toggle` suits a key binding, and it starts the character if it isn't running.
- `character avatar <look>` and `character monitor <name>` change its look and display. The display name is `DP-1` on Linux, `DISPLAY1` on Windows, and the display's name on Mac.
- `character <state> [look]` previews one animation.
- Where it is, its look and whether it is hidden are kept in `character.json` in the logs folder.

## Windows service

Windows has no per-user service manager, so `lalein supervise` does the job. It is started at login
by the Run key, through the windowless `laleinw.exe`. It runs these without console windows:

- the VOICEVOX engine
- the daemon
- the tray icon

It restarts them the way the Linux systemd unit does: it gives up after 5 crashes in 5 minutes, and a quit tray stays quit.

Commands:

- `lalein service status | stop | start`
- `lalein uninstall` removes the login entry.

If it doesn't start at login, check that **Lalein** is on in Settings > Apps > Startup.

## Updating

`lalein update` (`--check` only looks: exit 1 = an update is out; `--yes` skips the question).

- **Mac and Windows:**
  - The menu checks at most every 12 h and offers the update.
  - The update runs in the background: it stops the service, reinstalls with `uv tool install`, and starts it again.
  - It logs to `update.log` in the logs folder.
- **A git checkout (the Linux install):** `git pull --ff-only`, `uv sync`, then a restart. It only runs when the tree is clean.
- **Publishing a release:** "latest" is the newest GitHub release.
  1. Bump `version` in `pyproject.toml`, run `uv lock`, and merge to main.
  2. Run `git tag -a vX.Y.Z -m "<release notes>" && git push origin vX.Y.Z`.
  3. The `release` workflow checks that the tag matches the version, runs the tests, and publishes the release with the tag's message as its notes.

## Linux extras

- **Without the setup page:**
  1. `lalein engine install` (add `--cpu` for the CPU build)
  2. `lalein models download`
  3. `lalein setup --cli`
- **Units:** `lalein service install` writes `lalein.service` and `voicevox.service` to `~/.config/systemd/user/` and starts them.
  - Logs: `journalctl --user -u lalein`.
- **Echo cancellation** (PipeWire WebRTC AEC, lets you use speakers):
  1. Run `lalein audio install`.
  2. Set `mic_device = "zundamon_jarvis_aec_source"`.
  - `lalein audio output auto|bluez|hdmi|<sink>` chooses where the sound goes. `lalein audio uninstall` takes it all out.
  - To pin a mic, edit `~/.config/pipewire/pipewire.conf.d/zundamon-jarvis-echo-cancel.conf` after installing, then restart PipeWire.
- **From a git clone** (for development): `git clone https://github.com/dyoshyy/lalein && cd lalein && uv sync && uv run lalein setup`. `lalein update` then runs `git pull` and `uv sync`.
- **Omarchy / Hyprland:**
  - Bind a key to `laleinctl toggle`.
  - Add a bar module running `laleinctl bar`. Click opens `laleinctl window`; right-click opens `window settings`.
  - Float the classes `lalein-watch` and `lalein-settings`.
  - Bind a key to `laleinctl character toggle` to show or hide [the character](#the-character).

## The rename (zundamon-jarvis → Lalein)

0.5 renamed the app. An install from before moves itself the first time `lalein daemon` starts (`lalein migrate` does the same by hand):

- **Folders:** `zundamon-jarvis` becomes `lalein` in the config, data and log folders. The VOICEVOX engine is moved, not downloaded again. Until the move, the old folders are used.
- **Login services:**
  - Linux: `zundamon-jarvis.service` becomes `lalein.service`, and `voicevox.service` is rewritten for the new folder.
  - macOS: the `dev.zundamon-jarvis.*` agents become `dev.lalein.*`.
  - Windows: the `ZundamonJarvis` Run value becomes `Lalein`. A running supervisor is stopped for the move and started again afterwards.
- **Commands:** `lalein`, `laleinctl` and `laleinw`. 0.5 kept the old names as aliases; 0.6 dropped them.
- **By hand:**
  - Window rules and key bindings that name `zundamon-jarvis-watch`, `zundamon-jarvis-settings` or `zundamon-jarvis-character` need the new names: `lalein-watch`, `lalein-settings` and `lalein-character`.
  - The Linux echo-cancel setup keeps its names, such as `zundamon_jarvis_aec_source`.
- **Updates:** 0.5 was still distributed as `zundamon-jarvis`. From 0.6 the distribution is `lalein`, and 0.5's updater switches over by itself. The updater of 0.4 or earlier asks for `zundamon-jarvis` and fails on 0.6, leaving the old version as it was: on macOS and Windows, run the installer again. A Linux git checkout updates with `git pull`, but its old unit runs `.venv/bin/zundamon-jarvis`, which 0.6 no longer has: run `.venv/bin/lalein migrate` once.

## Troubleshooting

- **Mac, mic unavailable:** System Settings > Privacy & Security > Microphone. Turn on **python3.12** (the daemon) and your terminal.
- **Windows, mic unavailable:** Settings > Privacy & security > Microphone. Turn on **Let desktop apps access your microphone**.
- **Windows, nothing after login:** run `lalein service status`. Then check `supervisor.log`, `daemon.log`, `engine.log` and `tray.log` in the logs folder.
- **Not logged in:** run `claude` and log in.
- **She hears herself:** use headphones. On Linux you can also set up echo cancellation.
- **Anything else:** attach the output of `lalein doctor` to an issue. The report leaves out speech, names, paths and e-mail addresses.
