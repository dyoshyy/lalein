import numpy as np

from lalein.voice.endpoint import Endpointer, SpeechStarted, Utterance


def feed_seq(ep, probs):
    out = []
    for p in probs:
        out += ep.feed(np.zeros(512, np.float32), p)
    return out


def test_short_blip_is_ignored():  # 200ms < start_ms
    assert feed_seq(Endpointer(), [0.9] * 6 + [0.0] * 30) == []


def test_speech_emits_started_then_utterance():
    ev = feed_seq(Endpointer(), [0.9] * 10 + [0.0] * 47)  # 320ms speech, 1504ms silence
    assert isinstance(ev[0], SpeechStarted) and isinstance(ev[1], Utterance)


def test_utterance_includes_preroll():
    ev = feed_seq(Endpointer(), [0.0] * 20 + [0.9] * 10 + [0.0] * 47)
    assert len(ev[1].audio) >= 512 * (10 + 9)  # 300ms preroll


def test_utterance_keeps_a_hesitant_start():
    """A soft start: the VAD flickers around the threshold for ~0.8 s before a run of
    start_ms. Every flickering frame belongs to the utterance (the first words were lost
    with a 300 ms preroll)."""
    ep = Endpointer()
    probs = [0.0] * 40 + [0.5, 0.2] * 12 + [0.9] * 10 + [0.0] * 47
    out = []
    for i, p in enumerate(probs):
        out += ep.feed(np.full(512, i, np.float32), p)
    utt = next(e for e in out if isinstance(e, Utterance))
    assert utt.audio[0] <= 40 - 1  # starts before the first flicker frame (index 40)


def test_short_pause_does_not_split():
    ev = feed_seq(Endpointer(), [0.9] * 10 + [0.0] * 10 + [0.9] * 10 + [0.0] * 47)
    assert sum(isinstance(e, Utterance) for e in ev) == 1


def test_returns_to_idle_and_detects_next_utterance():
    ep = Endpointer()
    ev = feed_seq(ep, ([0.9] * 10 + [0.0] * 47) * 2)
    assert sum(isinstance(e, Utterance) for e in ev) == 2
    assert not ep.in_speech


def test_reset_clears_state():
    ep = Endpointer()
    feed_seq(ep, [0.9] * 10)
    assert ep.in_speech
    ep.reset()
    assert not ep.in_speech
    assert feed_seq(ep, [0.0] * 40) == []


def test_silero_vad_silence_is_low():
    from lalein.voice.vad import SileroVad

    p = SileroVad().prob(np.zeros(512, np.float32))
    assert isinstance(p, float) and 0.0 <= p < 0.5


# ---- max utterance length (a VAD that never falls silent must not hold the ears forever) ----

def test_endless_speech_is_cut_at_max_utterance_s():
    ep = Endpointer(max_utterance_s=1.0)  # 32 frames
    ev = feed_seq(ep, [0.9] * 40)
    kinds = [type(e).__name__ for e in ev]
    assert kinds == ["SpeechStarted", "Utterance", "SpeechStarted"]  # still talking: a new one
    cut = ev[1]
    assert cut.cut is True
    assert len(cut.audio) == 32 * 512  # cut exactly at the limit (preroll included)


def test_cut_then_continuing_speech_starts_a_new_utterance():
    ep = Endpointer(max_utterance_s=1.0, end_silence_ms=700)  # 22 silent frames end it
    ev = feed_seq(ep, [0.9] * 32 + [0.9] * 8 + [0.0] * 22)  # 30 frames: under the cap
    kinds = [type(e).__name__ for e in ev]
    assert kinds == ["SpeechStarted", "Utterance", "SpeechStarted", "Utterance"]
    assert ev[1].cut and not ev[3].cut


def test_default_max_is_30s_and_normal_utterances_are_not_cut():
    ep = Endpointer()
    assert ep.max_utterance_s == 30.0
    ev = feed_seq(ep, [0.9] * 10 + [0.0] * 47)  # 320ms speech, 1504ms silence
    assert ev[1].cut is False


def test_speech_duration_while_in_speech():
    ep = Endpointer()
    assert ep.utterance_s == 0.0
    feed_seq(ep, [0.9] * 20)
    assert ep.in_speech and abs(ep.utterance_s - 20 * 0.032) < 1e-9
