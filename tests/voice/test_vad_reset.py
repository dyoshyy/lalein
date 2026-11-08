from lalein.voice.vad_reset import RESET_LOW_WINDOWS, RESET_MAX_PROB, VadReset

INTERVAL = 63  # windows of 32 ms in RESET_INTERVAL_S (2.0 s), rounded up


def resets(probs, quiet=True):
    """Indices of the windows after which a reset happens."""
    r = VadReset()
    qs = quiet if isinstance(quiet, list) else [quiet] * len(probs)
    return [i for i, (p, q) in enumerate(zip(probs, qs)) if r.due(p, quiet=q)]


def test_silence_resets_every_interval():
    assert resets([0.0] * (INTERVAL * 3 + 10)) == [INTERVAL - 1, 2 * INTERVAL - 1, 3 * INTERVAL - 1]


def test_never_resets_in_speech():
    assert resets([0.0] * (INTERVAL * 3), quiet=False) == []


def test_a_due_reset_waits_for_the_endpointer_to_be_quiet():
    quiet = [True] * 10 + [False] * (INTERVAL + 20) + [True] * 30
    assert resets([0.0] * len(quiet), quiet) == [INTERVAL + 30]  # the first quiet window


def test_a_probability_at_the_limit_carries_the_reset_over():
    # due at window INTERVAL - 1, but a window at RESET_MAX_PROB (not below it) breaks the run:
    # the reset waits for RESET_LOW_WINDOWS low windows in a row after it
    probs = [0.0] * (INTERVAL - 2) + [RESET_MAX_PROB] + [0.0] * 20
    assert resets(probs) == [INTERVAL - 2 + RESET_LOW_WINDOWS]


def test_a_soft_start_never_lets_the_reset_through():
    # below the speech threshold (0.35) but not clear non-speech: carried over until it ends
    probs = [0.0] * (INTERVAL - 10) + [0.2, 0.05, 0.3, 0.08] * 10 + [0.0] * 10
    got = resets(probs)
    last_high = len(probs) - 10 - 1 - 1  # the last 0.3 (the 0.08 after it is low)
    assert got == [last_high + RESET_LOW_WINDOWS]


def test_the_low_run_must_be_long_enough():
    probs = [0.5] * INTERVAL + [0.0] * (RESET_LOW_WINDOWS - 1) + [0.5] + [0.0] * RESET_LOW_WINDOWS
    assert resets(probs) == [len(probs) - 1]


def test_restart_counts_from_zero():
    r = VadReset()
    for _ in range(INTERVAL - 1):
        assert not r.due(0.0, quiet=True)
    r.restart()  # the VAD was reset elsewhere (pause, mic failure)
    assert [r.due(0.0, quiet=True) for _ in range(INTERVAL)] == [False] * (INTERVAL - 1) + [True]
