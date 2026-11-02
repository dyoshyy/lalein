import re
from pathlib import Path

import pytest

from lalein.assistant import harness
from lalein.assistant.brain import ClaudeBrain
from lalein.core.config import Config


class FakeClient:
    def __init__(self, options):
        self.options = options


@pytest.mark.parametrize("sys_", ["linux", "mac", "windows"])
def test_the_session_is_told_what_it_is_with_this_installs_facts(sys_):
    cfg = Config(speaker_id=43, speed=1.4, model="sonnet", workdir=Path("/w"), knowledge_dir=Path("/notes"))
    text = harness.about_self(cfg, sys_)
    assert "${" not in text and "Lalein" in text and "ラレイン" in text
    assert str(Path("/w")) in text and str(Path("/notes")) in text
    assert "style id 43, speed 1.4" in text and "Model: sonnet" in text
    assert harness.version() in text and harness.OS_NAMES[sys_] in text
    assert harness.PAUSE_KEYS[sys_] in text
    assert f"{harness.ctl_command(sys_)} set speed" in text
    assert str(harness.REFERENCE) in text and harness.REFERENCE.is_file()


def test_unset_values_are_said_plainly():
    text = harness.about_self(Config(speaker_id=None, model=None, knowledge_dir=None), "linux")
    assert "not set (青山龍星)" in text and "the Claude Code default" in text and "No notes folder." in text


def test_the_prompt_is_how_to_talk_then_what_it_is():
    voice = harness.VOICE_MODE.read_text(encoding="utf-8")
    assert harness.system_prompt() == voice  # setup's login check: only how to talk
    full = harness.system_prompt(Config(), "linux")
    assert full.startswith(voice) and "# About yourself: Lalein" in full


def test_what_it_says_about_itself_matches_the_code():
    """The prompt and the reference name real looks and real ``laleinctl set`` keys."""
    from lalein.core import control
    from lalein.ui import character

    for path in (harness.ABOUT_SELF, harness.REFERENCE):
        text = path.read_text(encoding="utf-8")
        for looks in re.findall(r"\b(bot[|,] ?robo\w*.*?edamame)", text):
            assert tuple(re.split(r"\s*(?:\||,|\band\b)\s*", looks)) == character.AVATARS
    doc = control.__doc__ or ""
    for key in re.findall(r"set (\w+) ", harness.ABOUT_SELF.read_text(encoding="utf-8")):
        assert f"set {key} " in doc


def test_the_brain_builds_its_prompt_when_a_session_starts():
    calls = []

    def build():
        calls.append(1)
        return f"prompt {len(calls)}"

    b = ClaudeBrain(Path("/w"), client_factory=FakeClient, instructions=build)
    assert b._options().system_prompt["append"] == "prompt 1"
    assert b._options().system_prompt["append"] == "prompt 2"  # fresh facts for each session
    plain = ClaudeBrain(Path("/w"), client_factory=FakeClient)
    assert plain._options().system_prompt["append"] == harness.VOICE_MODE.read_text(encoding="utf-8")
