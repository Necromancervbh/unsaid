"""Edit by text: diff an edited copy of the transcript against the original."""
from __future__ import annotations

from difflib import SequenceMatcher

from .transcript import Transcript, normalize


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
    for op, i1, i2, _, _ in SequenceMatcher(None, original, edited, autojunk=False).get_opcodes():
        if op == "delete":
            removed.update(range(i1, i2))
        elif op == "replace":
            changed += i2 - i1
    return removed, changed
