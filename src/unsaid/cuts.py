"""Turn "these words are removed" into "keep these time ranges".

Cuts land in the silence between kept words, never inside a kept word. When
words are removed, the pause left behind is the shorter of the two pauses that
surrounded them, so the edit keeps the speaker's natural rhythm.
"""
from __future__ import annotations

from dataclasses import dataclass

from .transcript import Transcript

Range = tuple[float, float]


@dataclass
class CutPlan:
    keep: list[Range]
    removed_words: set[int]
    duration: float

    @property
    def output_duration(self) -> float:
        return sum(b - a for a, b in self.keep)

    @property
    def saved(self) -> float:
        return self.duration - self.output_duration


def plan_cuts(
    transcript: Transcript,
    removed: set[int],
    max_pause: float | None = None,
    pad: float = 0.04,
    min_cut: float = 0.08,
) -> CutPlan:
    """
    removed:   indices of words to drop.
    max_pause: shorten any pause between kept words to at most this many seconds.
    pad:       always keep at least this much audio around a kept word (whisper's
               word boundaries are approximate; this avoids clipping consonants).
    min_cut:   ignore cuts shorter than this; they only add choppiness.
    """
    words = transcript.words
    kept = [i for i in range(len(words)) if i not in removed]
    cuts: list[Range] = []

    if not kept:
        return CutPlan(keep=[], removed_words=set(removed), duration=transcript.duration)

    # Removed words before the first kept word: cut from the first removed word's start.
    first = words[kept[0]]
    if kept[0] > 0:
        cuts.append((words[0].start, first.start - pad))

    for a, b in zip(kept, kept[1:]):
        wa, wb = words[a], words[b]
        gap = wb.start - wa.end
        if b - a > 1:
            # Words between a and b were removed. Keep the shorter of the pauses around them.
            run = words[a + 1:b]
            pause = max(0.0, min(run[0].start - wa.end, wb.start - run[-1].end))
        else:
            pause = gap
        if max_pause is not None:
            pause = min(pause, max_pause)
        half = max(pad, pause / 2)
        cuts.append((wa.end + half, wb.start - half))

    # Removed words after the last kept word: cut through the end of the last removed word.
    last = words[kept[-1]]
    if kept[-1] < len(words) - 1:
        cuts.append((last.end + pad, words[-1].end))

    cuts = _merge([(max(0.0, s), min(transcript.duration, e)) for s, e in cuts if e - s >= min_cut])
    return CutPlan(keep=_complement(cuts, transcript.duration), removed_words=set(removed),
                   duration=transcript.duration)


def _merge(ranges: list[Range]) -> list[Range]:
    out: list[Range] = []
    for s, e in sorted(ranges):
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def _complement(cuts: list[Range], duration: float, min_keep: float = 0.05) -> list[Range]:
    keep, t = [], 0.0
    for s, e in cuts:
        if s - t >= min_keep:
            keep.append((round(t, 3), round(s, 3)))
        t = max(t, e)
    if duration - t >= min_keep:
        keep.append((round(t, 3), round(duration, 3)))
    return keep
