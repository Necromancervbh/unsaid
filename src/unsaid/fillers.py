"""Find filler words ("um", "uh", and optionally "like", "you know", ...) in a transcript."""
from __future__ import annotations

from .transcript import Transcript, normalize

# Pure hesitation sounds: safe to remove wherever they appear.
SOUNDS = frozenset({
    "um", "umm", "ummm", "uh", "uhh", "uhm", "uhum", "erm", "er", "err", "ah", "ahh",
    "hmm", "hm", "hmmm", "mm", "mmm", "mhm", "eh",
})

# Words that are only filler when set off by commas ("it was, like, huge"),
# never in normal use ("I like it", "you know the answer").
VERBAL = (("you", "know"), ("i", "mean"), ("like",), ("basically",), ("literally",), ("sort", "of"), ("kind", "of"))


def find_fillers(transcript: Transcript, aggressive: bool = False, extra: tuple[str, ...] = ()) -> set[int]:
    """Return indices of words to remove."""
    words = transcript.words
    norms = [w.norm for w in words]
    sounds = SOUNDS | {normalize(e) for e in extra if " " not in e.strip()}
    phrases = [tuple(normalize(p) for p in e.split()) for e in extra if " " in e.strip()]
    removed = {i for i, n in enumerate(norms) if n in sounds}

    for phrase in phrases:
        for i in _matches(norms, phrase):
            removed.update(range(i, i + len(phrase)))

    if aggressive:
        for phrase in VERBAL:
            for i in _matches(norms, phrase):
                j = i + len(phrase) - 1
                if _comma_bounded(words, i, j):
                    removed.update(range(i, j + 1))
    return removed


def _matches(norms: list[str], phrase: tuple[str, ...]):
    n = len(phrase)
    for i in range(len(norms) - n + 1):
        if tuple(norms[i:i + n]) == phrase:
            yield i


def _comma_bounded(words, i: int, j: int) -> bool:
    """True if words[i..j] is set off by punctuation: ', like,' / 'Like, ...' / '..., you know.'"""
    before = i == 0 or words[i - 1].text.endswith((",", ".", "?", "!", "..."))
    after = words[j].text.endswith((",", ".", "?", "!", "...")) or j == len(words) - 1
    return before and after
