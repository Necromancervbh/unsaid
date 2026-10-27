"""Edit by text: diff an edited copy of the transcript against the original."""
from __future__ import annotations

from difflib import SequenceMatcher

from .transcript import Transcript, normalize

SIMILAR = 0.6  # how alike two words must be to count as the same word, edited
MAX_ALIGN = 200_000  # skip aligning huge rewritten blocks (words x words)


def removed_by_script(transcript: Transcript, edited_text: str) -> tuple[set[int], int]:
    """
    Return (indices of words deleted in `edited_text`, number of original words
    that were changed rather than deleted). Changed words are kept: the audio
    can't say something different, and guessing which ones to cut is worse than
    leaving them in.
    """
    original = [w.norm for w in transcript.words]
    edited = [n for n in (normalize(t) for t in edited_text.split()) if n]
    removed: set[int] = set()
    changed = 0
    for op, i1, i2, j1, j2 in SequenceMatcher(None, original, edited, autojunk=False).get_opcodes():
        if op == "delete":
            removed.update(range(i1, i2))
        elif op == "replace":
            # difflib reports a deleted sentence next to a typo ("transcript" ->
            # "transcripts") as one replaced block. If every edited word is a
            # near-copy of an original one, the rest of the block was deleted.
            matched = _align(original[i1:i2], edited[j1:j2])
            if matched is None:
                changed += i2 - i1
            else:
                removed.update(i1 + k for k in range(i2 - i1) if k not in matched)
                changed += sum(original[i1 + k] != edited[j1 + n] for n, k in enumerate(matched))
    return removed, changed


def _align(original: list[str], edited: list[str]) -> set[int] | None:
    """
    Match every edited word to a distinct, similar original word, keeping order
    and maximizing total similarity. Return the matched original positions, or
    None when some edited word has no similar counterpart (the text was
    rewritten, not trimmed).
    """
    n, m = len(original), len(edited)
    if m > n or n * m > MAX_ALIGN:
        return None
    sim = [[SequenceMatcher(None, o, e).ratio() for e in edited] for o in original]
    NONE = float("-inf")
    # best[i][j]: best total similarity matching edited[:j] within original[:i]
    best = [[0.0] + [NONE] * m for _ in range(n + 1)]
    for i in range(1, n + 1):
        for j in range(1, min(i, m) + 1):
            skip = best[i - 1][j]
            take = best[i - 1][j - 1] + sim[i - 1][j - 1] if sim[i - 1][j - 1] >= SIMILAR else NONE
            best[i][j] = max(skip, take)
    if best[n][m] == NONE:
        return None
    matched, i, j = set(), n, m
    while j:
        if best[i][j] == best[i - 1][j]:
            i -= 1
        else:
            matched.add(i - 1)
            i, j = i - 1, j - 1
    return matched
