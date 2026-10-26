import base64

import pytest
from conftest import make_transcript

from unsaid.cuts import plan_cuts

T = make_transcript("Hello@0.5-0.9 um@1.2-1.4 world.@1.8-2.3 This@3.8-4.0 is@4.1-4.2 it.@4.3-4.6", duration=5.0)


def test_nothing_removed_keeps_everything():
    plan = plan_cuts(T, set())
    assert plan.keep == [(0.0, 5.0)]
    assert plan.saved == 0


def test_removed_word_leaves_shorter_surrounding_pause():
    plan = plan_cuts(T, {1})
    # Pauses around "um" were 0.3s and 0.4s, so 0.3s stays: cut 1.05 -> 1.65.
    assert plan.keep == [(0.0, 1.05), (1.65, 5.0)]
    assert plan.saved == pytest.approx(0.6)


def test_cuts_never_overlap_kept_words():
    plan = plan_cuts(T, {1, 3})
    for w_i in (0, 2, 4, 5):
        w = T.words[w_i]
        assert any(s <= w.start and w.end <= e for s, e in plan.keep), w.text


def test_max_pause_shortens_long_gaps_only():
    plan = plan_cuts(T, set(), max_pause=0.5)
    # Only the 1.5s gap between "world." and "This" exceeds 0.5s.
    assert plan.keep == [(0.0, 2.55), (3.55, 5.0)]


def test_leading_and_trailing_removals():
    t = make_transcript("Um@0.2-0.5 hello@0.8-1.2 there@1.3-1.6 uh@1.9-2.1", duration=3.0)
    plan = plan_cuts(t, {0, 3})
    assert plan.keep == [(0.0, 0.2), (0.76, 1.64), (2.1, 3.0)]


def test_everything_removed():
    assert plan_cuts(T, set(range(len(T.words)))).keep == []


def test_tiny_cuts_are_skipped():
    t = make_transcript("a@0.0-0.5 uh@0.5-0.55 b@0.55-1.0", duration=1.0)
    assert plan_cuts(t, {1}).keep == [(0.0, 1.0)]


def test_never_cuts_into_speech_whisper_skipped():
    # Whisper heard nothing between "So" and "um", but 1.0-5.5s is speech.
    t = make_transcript("So@0.5-0.9 um@6.0-6.3 yes.@6.6-7.0", duration=8.0)
    t.unheard = [(1.0, 5.5)]
    keep = plan_cuts(t, {1}).keep
    assert any(s <= 1.0 and e >= 5.5 for s, e in keep)
    assert keep == [(0.0, 5.5), (6.45, 8.0)]  # still cuts the "um" after it


def test_max_pause_leaves_skipped_speech_alone():
    t = make_transcript("a@0-0.5 b@5-5.5", duration=6.0)
    t.unheard = [(1.0, 4.0)]
    assert plan_cuts(t, set(), max_pause=0.5).keep == [(0.0, 0.75), (1.0, 4.0), (4.75, 6.0)]


def with_energy(t, loud: list[tuple[float, float]]):
    """Give a transcript a loudness track: speech in `loud`, near silence elsewhere."""
    frames = bytearray([30] * int(t.duration / 0.01))
    for s, e in loud:
        frames[int(s / 0.01):int(e / 0.01)] = bytes([200]) * (int(e / 0.01) - int(s / 0.01))
    t.energy = base64.b64encode(bytes(frames)).decode()
    return t


def test_cut_edges_follow_the_audio_when_timestamps_are_off():
    # Modeled on a real clip: whisper ends "of" a little late and starts
    # "Internet" 0.25 s late with zero length. The "uh" really sits at
    # 1.40-1.62 and "Internet" starts at 1.70. Timestamps alone cut 1.46-1.91:
    # half the "uh" stays and "Inter" is lost.
    t = make_transcript("of@1.0-1.42 uh@1.42-1.75 Internet@1.95-1.95 interconnection.@1.95-3.0", duration=4.0)
    with_energy(t, [(1.0, 1.30), (1.40, 1.62), (1.70, 3.0)])
    (_, s), (e, _) = plan_cuts(t, {1}).keep
    assert 1.30 <= s <= 1.40  # the whole "uh" goes...
    assert 1.62 <= e <= 1.70 - 0.02  # ...and "Internet" keeps its start


def test_words_running_together_keep_the_planned_edges():
    # "uh" runs straight into "you", and "you" has a softer middle. That
    # softer stretch is still speech, not a gap, so the edge stays put.
    t = make_transcript("uh@1.0-1.3 you@1.3-1.8 have@1.8-2.0", duration=3.0)
    planned = plan_cuts(t, {0}).keep
    frames = bytearray([30] * 300)
    frames[100:200] = bytes([237]) * 100  # about -7 dB
    frames[140:146] = bytes([215]) * 6  # about -16 dB: within 6 dB of the window's quietest, but speech
    t.energy = base64.b64encode(bytes(frames)).decode()
    assert plan_cuts(t, {0}).keep == planned


def test_brief_dip_inside_a_kept_word_is_not_a_gap():
    # A 20 ms dip inside "on", before its middle, must not look like the
    # silence before it: that would move the cut end into the word.
    t = make_transcript("regulate@1.0-2.0 um@2.39-2.91 on@3.89-4.21 the@4.21-4.33", duration=5.0)
    with_energy(t, [(1.0, 2.0), (2.6, 2.84), (3.86, 4.01), (4.03, 4.33)])
    (_, s), (e, _) = plan_cuts(t, {1}).keep
    assert e <= 3.86 - 0.03


def test_cuts_in_real_silence_stay_where_planned():
    t = make_transcript("Hello@0.5-0.9 um@1.2-1.4 world.@1.8-2.3", duration=3.0)
    planned = plan_cuts(t, {1}).keep
    with_energy(t, [(0.5, 0.9), (1.2, 1.4), (1.8, 2.3)])
    assert plan_cuts(t, {1}).keep == planned


def test_snapping_never_turns_a_pause_into_a_cut():
    t = with_energy(make_transcript("Hello@0.5-0.9 world.@1.8-2.3", duration=3.0), [(0.5, 0.9), (1.8, 2.3)])
    assert plan_cuts(t, set()).keep == [(0.0, 3.0)]


def test_cut_end_never_jumps_back_past_the_removed_word():
    # Modeled on a real clip: whisper starts "um" 0.46 s early; the real "um"
    # (2.86-3.08, then a nasal tail) runs straight into "because", and the
    # only real silence is before the "um". The cut end must not retreat there.
    t = make_transcript("interconnection,@1.0-2.0 um,@2.4-3.1 because@3.25-3.5", duration=4.0)
    frames = bytearray([140] * 400)
    frames[100:185] = bytes([210]) * 85  # "interconnection"
    frames[205:220] = bytes([130]) * 15  # the silence after it
    frames[220:275] = bytes([150]) * 55  # breath and room noise
    frames[286:308] = bytes([215]) * 22  # the "um"
    frames[308:325] = bytes([185]) * 17  # its nasal tail
    frames[326:350] = bytes([215]) * 24  # "because"
    t.energy = base64.b64encode(bytes(frames)).decode()
    (_, s), (e, _) = plan_cuts(t, {1}).keep
    assert s <= 2.86 and e >= 3.08  # the "um" itself is cut
