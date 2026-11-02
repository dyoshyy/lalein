"""What the voice session is told about itself: the system prompt appended to Claude Code's own.

Two parts, both package data (so a ``uv tool install`` has them):

- ``prompts/voice-mode.md``: how to talk (short spoken replies, the language, background work,
  confirmations);
- ``prompts/about-self.md``: what Lalein is and how a turn works, filled in with this install's
  facts when a session starts (version, OS, folders, settings, the ``laleinctl`` to change
  them). It points to ``prompts/about-lalein.md``, the full reference, which the session reads
  only when asked about itself, so the prompt stays short.

The facts are as of the session's start; the prompt says ``laleinctl get`` has the live ones.
"""

import importlib.metadata
import shutil
import sys
from importlib import resources
from pathlib import Path
from string import Template

from lalein.core import platform
from lalein.core.config import Config

PROMPTS = Path(str(resources.files("lalein") / "prompts"))
VOICE_MODE = PROMPTS / "voice-mode.md"
ABOUT_SELF = PROMPTS / "about-self.md"
REFERENCE = PROMPTS / "about-lalein.md"
OS_NAMES = {"linux": "Linux", "mac": "macOS", "windows": "Windows"}
PAUSE_KEYS = {  # how the user resumes listening
    "linux": "the pause key (SUPER+ALT+J if bound) or the bar",
    "mac": "the menu bar icon",
    "windows": "the tray icon",
}


def version() -> str:
    for dist in ("lalein", "zundamon-jarvis"):  # 0.5 was still distributed under the old name
        try:
            return importlib.metadata.version(dist)
        except importlib.metadata.PackageNotFoundError:
            continue
    return "(unknown version)"


def ctl_command(sys_: str) -> str:
    """``laleinctl`` as the session can run it: beside this python (the venv or the uv tool),
    else on PATH, else the bare name."""
    name = "laleinctl.exe" if sys_ == "windows" else "laleinctl"
    sibling = Path(sys.executable).parent / name
    if sibling.exists():
        return str(sibling)
    return shutil.which("laleinctl") or "laleinctl"


def facts(cfg: Config, sys_: str | None = None) -> dict[str, str]:
    sys_ = sys_ or platform.system()
    return {
        "version": version(),
        "os": OS_NAMES.get(sys_, sys_),
        "workdir": str(cfg.workdir),
        "knowledge": f"Notes folder: {cfg.knowledge_dir}." if cfg.knowledge_dir else "No notes folder.",
        "model": cfg.model or "the Claude Code default",
        "speaker": "not set (青山龍星)" if cfg.speaker_id is None else str(cfg.speaker_id),
        "speed": f"{cfg.speed:g}",
        "config": str(platform.config_dir(sys_) / "config.toml"),
        "logs": str(platform.log_dir(sys_)),
        "ctl": ctl_command(sys_),
        "hotkey": PAUSE_KEYS.get(sys_, "the menu"),
        "reference": str(REFERENCE),
    }


def about_self(cfg: Config, sys_: str | None = None) -> str:
    return Template(ABOUT_SELF.read_text(encoding="utf-8")).substitute(facts(cfg, sys_))


def system_prompt(cfg: Config | None = None, sys_: str | None = None) -> str:
    """The text appended to Claude Code's system prompt. Without ``cfg`` (setup's login check),
    only how to talk."""
    parts = [VOICE_MODE.read_text(encoding="utf-8")]
    if cfg is not None:
        parts.append(about_self(cfg, sys_))
    return "\n\n".join(parts)
