"""Captions for the edited video, timed to the cut rather than the original.

The transcript already has a timestamp for every word, so captions need no
second transcription: each kept word moves onto the output timeline (the time
removed before it is subtracted), then words are grouped into readable cues.
Sidecar files are SRT or WebVTT; burned-in captions are ASS, one short line at
a time with the word being spoken highlighted, the way Shorts and Reels do it.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass

from .cuts import Range
from .transcript import Transcript, Word

SENTENCE_END = (".", "?", "!")
MIN_SHOW = 0.7  # seconds a cue stays up, at least, when the next one leaves room
LINGER = 0.3  # keep a cue up this long after its last word, when the next one leaves room


@dataclass
class Cue:
    start: float
    end: float
    words: list[Word]

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)


def retime(transcript: Transcript, removed: set[int], keep: list[Range]) -> list[Word]:
    """
    The kept words with their times on the output timeline. A word is placed in
    the kept range it overlaps most and clipped to it: its timestamps can reach
    a little into a cut, since cuts land in the silence next to it.
    """
    starts = [s for s, _ in keep]
    offsets, total = [], 0.0
    for s, e in keep:
        offsets.append(total - s)
        total += e - s
    out = []
    for i, w in enumerate(transcript.words):
        if i in removed:
            continue
        j = bisect_right(starts, w.start) - 1
        candidates = [k for k in (j, j + 1) if 0 <= k < len(keep)]
        overlap = {k: min(w.end, keep[k][1]) - max(w.start, keep[k][0]) for k in candidates}
        k = max(candidates, key=overlap.get, default=None)
        if k is None or overlap[k] <= 0:
            continue  # entirely inside a cut: nothing of it is heard
        s, e = max(w.start, keep[k][0]), min(w.end, keep[k][1])
        out.append(Word(w.text, round(s + offsets[k], 3), round(e + offsets[k], 3), w.probability))
    return out


def make_cues(words: list[Word], max_chars: int = 42, max_gap: float = 1.0, max_duration: float = 6.0) -> list[Cue]:
    """
    Group words into cues of at most max_chars characters, breaking at the end
    of a sentence, at a pause longer than max_gap, and before a cue runs longer
    than max_duration seconds.
    """
    cues: list[Cue] = []
    cur: list[Word] = []
    for w in words:
        if cur and (len(" ".join(x.text for x in cur)) + 1 + len(w.text) > max_chars
                    or w.start - cur[-1].end > max_gap
                    or w.end - cur[0].start > max_duration):
            cues.append(Cue(cur[0].start, cur[-1].end, cur))
            cur = []
        cur.append(w)
        if w.text.endswith(SENTENCE_END):
            cues.append(Cue(cur[0].start, cur[-1].end, cur))
            cur = []
    if cur:
        cues.append(Cue(cur[0].start, cur[-1].end, cur))
    # Short cues flash by: keep each up a little longer, but never past the next.
    for a, b in zip(cues, cues[1:] + [None]):
        limit = b.start if b else a.end + MIN_SHOW
        a.end = round(min(max(a.end + LINGER, a.start + MIN_SHOW), max(limit, a.end)), 3)
    return cues


def _clock(t: float, sep: str) -> str:
    ms = round(t * 1000)
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def to_srt(cues: list[Cue]) -> str:
    return "".join(f"{n}\n{_clock(c.start, ',')} --> {_clock(c.end, ',')}\n{c.text}\n\n"
                   for n, c in enumerate(cues, 1))


def to_vtt(cues: list[Cue]) -> str:
    return "WEBVTT\n\n" + "".join(f"{_clock(c.start, '.')} --> {_clock(c.end, '.')}\n{c.text}\n\n" for c in cues)


def to_ass(cues: list[Cue], width: int, height: int) -> str:
    """
    Burned-in captions: bold white text with a dark outline, low in the frame,
    and each word turning yellow while it's spoken. Sized from the frame's
    shorter side, so they read the same on landscape and vertical video.
    """
    size = round(min(width, height) * 0.075)
    outline = max(1, round(size / 14))
    header = (
        "[Script Info]\nScriptType: v4.00+\nWrapStyle: 0\nScaledBorderAndShadow: yes\n"
        f"PlayResX: {width}\nPlayResY: {height}\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
        "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,Arial,{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,"
        f"{outline},0,2,{round(width * 0.06)},{round(width * 0.06)},{round(height * 0.1)},1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    events = []
    for c in cues:
        texts = [_ass_escape(w.text) for w in c.words]
        for k, w in enumerate(c.words):
            start = c.start if k == 0 else w.start
            end = c.words[k + 1].start if k + 1 < len(c.words) else c.end
            if end <= start:
                continue
            line = " ".join("{\\c&H00FFFF&}" + t + "{\\r}" if j == k else t for j, t in enumerate(texts))
            events.append(f"Dialogue: 0,{_ass_clock(start)},{_ass_clock(end)},Default,,0,0,0,,{line}")
    return header + "\n".join(events) + "\n"


def _ass_clock(t: float) -> str:
    cs = round(t * 100)
    h, cs = divmod(cs, 360_000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _ass_escape(text: str) -> str:
    """Braces start ASS override tags and backslashes start escapes; neither belongs in a caption."""
    return text.replace("\\", "/").replace("{", "(").replace("}", ")")
