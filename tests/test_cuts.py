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
