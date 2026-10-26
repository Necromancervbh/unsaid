"""Turn "these words are removed" into "keep these time ranges".

Cuts land in the silence between kept words, never inside a kept word. When
words are removed, the pause left behind is the shorter of the two pauses that
surrounded them, so the edit keeps the speaker's natural rhythm. Whisper's word
timestamps can be off by a tenth of a second or more, so when the transcript
has a loudness track, cut edges then move into the silence the audio actually
shows next to the kept words.
"""
from __future__ import annotations

from dataclasses import dataclass

from .transcript import ENERGY_HOP, ENERGY_STEPS_PER_DB, Transcript, Word

Range = tuple[float, float]
QUIET_DB = 6  # how far above the quietest point still counts as quiet
DIP_DB = 12  # ...and how far below the loudest point it must be
SNAP_MARGIN = 0.03  # seconds a cut edge keeps from a kept word's sound
SNAP_SEARCH = 0.15  # look this far past a kept word's timestamp for where its sound really stops
SNAP_INTO = 0.03  # but never move a cut edge more than this into the kept word's timestamps
MIN_SILENCE = 0.04  # quiet shorter than this is a dip inside a word, not a gap between words


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

    # Whisper's word boundaries can be off by a tenth of a second or more. With
    # the transcript's loudness track, a cut's start moves into the silence
    # after the kept word before it, and its end into the silence before the
    # kept word after it, keeping SNAP_MARGIN from both. Edges already in those
    # silences stay put, so planned pauses survive, and only real cuts move:
    # this never turns a pause into a cut.
    energy = transcript.energy_frames() if transcript.energy else b""

    def add(s: float, e: float, left: tuple[Word, Word] | None, right: tuple[Word, Word] | None) -> None:
        """left: (kept word before the cut, the word after it); right: (word before, kept word after)."""
        if e - s < min_cut:
            return
        if energy and left and (q := _silence(energy, *left, after_a=True)):
            s = _clamp(s, min(q[0] + SNAP_MARGIN, q[1]), q[1])
            s = max(s, left[0].end - SNAP_INTO)
        if energy and right and (q := _silence(energy, *right, after_a=False)):
            e = _clamp(e, q[0], max(q[1] - SNAP_MARGIN, q[0]))
            e = min(e, right[1].start + SNAP_INTO)
        cuts.append((s, e))

    # Removed words before the first kept word: cut from the first removed word's start.
    first = words[kept[0]]
    if kept[0] > 0:
        add(words[0].start, first.start - pad, None, (words[kept[0] - 1], first))

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
        add(wa.end + half, wb.start - half, (wa, words[a + 1]), (words[b - 1], wb))

    # Removed words after the last kept word: cut through the end of the last removed word.
    last = words[kept[-1]]
    if kept[-1] < len(words) - 1:
        add(last.end + pad, words[-1].end, (last, words[kept[-1] + 1]), None)

    # Speech whisper skipped has no words, so it looks like a pause. Never cut into it.
    cuts = _subtract(cuts, transcript.unheard)
    cuts = _merge([(max(0.0, s), min(transcript.duration, e)) for s, e in cuts if e - s >= min_cut])
    return CutPlan(keep=_complement(cuts, transcript.duration), removed_words=set(removed),
                   duration=transcript.duration)


def _silence(energy: bytes, a: Word, b: Word, after_a: bool) -> Range | None:
    """
    The silence between words a and b: the first one (after_a, a is kept) or
    the last one (b is kept). It's searched for no further than SNAP_SEARCH
    beyond either word's timestamp (and not past either word's middle), so it
    can't reach past a removed word's sound to a silence on its far side.
    "Silence" is at least MIN_SILENCE of audio within QUIET_DB of the quietest
    point there and DIP_DB below the loudest, so a shallow dip between words
    that run together counts when it's all there is, but the brief dips and
    softer sounds inside a word don't.
    """
    lo = max((a.start + a.end) / 2, a.end - SNAP_SEARCH)
    hi = min((b.start + b.end) / 2, b.start + SNAP_SEARCH)
    i0, i1 = max(0, int(lo / ENERGY_HOP)), min(len(energy), int(hi / ENERGY_HOP) + 1)
    if i1 - i0 < 3:
        return None
    # Quiet means near the quietest point here, and well below the loudest:
    # when words run together with no real gap, nothing qualifies and the
    # planned edge stays.
    window = energy[i0:i1]
    limit = min(min(window) + QUIET_DB * ENERGY_STEPS_PER_DB, max(window) - DIP_DB * ENERGY_STEPS_PER_DB)
    runs, start = [], None
    for i in range(i0, i1 + 1):
        if i < i1 and energy[i] <= limit:
            start = i if start is None else start
        elif start is not None:
            if (i - start) * ENERGY_HOP >= MIN_SILENCE:
                runs.append((start * ENERGY_HOP, i * ENERGY_HOP))
            start = None
    if not runs:
        return None
    return runs[0] if after_a else runs[-1]


def _clamp(x: float, lo: float, hi: float) -> float:
    return min(max(x, lo), hi)


def _subtract(cuts: list[Range], protected: list[Range]) -> list[Range]:
    """The parts of each cut that don't overlap a protected range."""
    for ps, pe in protected:
        cuts = [piece for s, e in cuts for piece in ((s, min(e, ps)), (max(s, pe), e)) if piece[1] > piece[0]]
    return cuts


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
