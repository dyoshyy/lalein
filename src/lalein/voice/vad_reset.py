"""When to reset the Silero VAD's recurrent state while nobody speaks (after Pipecat's
``SileroVADAnalyzer._maybe_reset_model``).

Long non-speech drifts the model's state so it under-reports the speech that follows: after
30 s or more of a quiet room, onset came 1.2-1.6 s late and the first words were lost. In full
duplex nothing else resets it (only pause, a mic failure or a cut utterance do), so it is
reset every ``RESET_INTERVAL_S`` of processed audio, but only in clear non-speech: the
endpointer is not in speech and the last ``RESET_LOW_WINDOWS`` windows (about 160 ms) all
scored below ``RESET_MAX_PROB``. One low window can be a dip inside a word or a soft start;
a run of them can't. A due reset that finds speech waits, window by window, until it can.

Time is counted in windows scored, not by the wall clock, so where resets land depends only
on the audio. Kept free of onnxruntime so the daemon can import it before any heavy import.
"""

SAMPLE_RATE = 16000
FRAME_SAMPLES = 512  # one VAD window, 32 ms

RESET_INTERVAL_S = 2.0  # of processed audio between resets
RESET_MAX_PROB = 0.1  # a window counts as clear non-speech below this (threshold is 0.35)
RESET_LOW_WINDOWS = 5  # this many in a row (~160 ms) before a due reset happens


class VadReset:
    def __init__(self) -> None:
        self.restart()

    def restart(self) -> None:
        """The VAD was reset by someone else (pause, mic failure, ...): count from zero."""
        self._windows = 0  # scored since the last reset
        self._low = 0  # most recent windows in a row below RESET_MAX_PROB

    def due(self, prob: float, *, quiet: bool) -> bool:
        """After each window is scored: reset the VAD now (it takes effect from the next
        window)? ``quiet``: the endpointer is not in speech."""
        self._windows += 1
        self._low = self._low + 1 if prob < RESET_MAX_PROB else 0
        if (
            self._windows * FRAME_SAMPLES >= RESET_INTERVAL_S * SAMPLE_RATE
            and quiet
            and self._low >= RESET_LOW_WINDOWS
        ):
            self._windows = 0
            return True
        return False
