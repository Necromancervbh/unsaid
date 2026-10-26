"""Local speech-to-text with word timestamps, via faster-whisper."""
from __future__ import annotations

import sys
from pathlib import Path

from .fillers import SOUNDS
from .transcript import Transcript, Word, normalize, source_fingerprint

# Whisper tends to "clean up" disfluencies. Priming it with text that contains
# fillers makes it transcribe them verbatim, which filler removal depends on.
VERBATIM_PROMPT = "Umm, let me think like, hmm... Okay, here's what I'm, like, thinking. Uh, so, um, yeah."

# The prompt has a side effect: Whisper sometimes tacks a made-up segment
# ("Good.", "All right.") onto the end of the speech. Those are a fraction of a
# second long and decode with low confidence (avg log-prob around -1.15,
# no-speech probability around 0.4), while real speech sits near -0.15 and 0.
# Whisper's own silence check only drops segments with no-speech above 0.6, so
# it lets them through.
HALLUCINATION_LOGPROB = -1.0  # whisper's own log_prob_threshold
HALLUCINATION_NO_SPEECH = 0.2
HALLUCINATION_MAX_LEN = 0.5


def transcribe(
    media: Path,
    model: str = "small",
    language: str | None = None,
    device: str = "auto",
    verbatim: bool = True,
    progress: bool = True,
) -> Transcript:
    from faster_whisper import decode_audio  # heavy import, keep it lazy

    whisper = _load_model(model, device, progress)
    audio = decode_audio(str(media))
    if language is None:
        language, prob, _ = whisper.detect_language(audio)
        _log(f"detected language: {language} ({prob:.0%})", progress)

    segments, info = whisper.transcribe(
        audio,
        language=language,
        word_timestamps=True,
        vad_filter=True,
        condition_on_previous_text=False,
        # Not initial_prompt: with condition_on_previous_text off, faster-whisper
        # only uses that for the first 30-second window, and fillers vanish from
        # the rest of the recording. hotwords goes into every window's context
        # the same way.
        hotwords=VERBATIM_PROMPT if verbatim and language == "en" else None,
    )
    words: list[Word] = []
    for seg in segments:
        if _hallucinated(seg):
            _log(f"\rskipped {seg.text.strip()!r} at {seg.start:.1f}s (likely made up by whisper)", progress)
            continue
        for w in seg.words or []:
            text = w.word.strip()
            if text:
                words.append(Word(text=text, start=round(w.start, 3), end=round(w.end, 3),
                                  probability=round(w.probability, 3)))
        _log(f"\rtranscribed {seg.end:6.1f}s / {info.duration:.1f}s", progress, end="")
    _log("", progress)

    return Transcript(words=words, duration=round(info.duration, 3), language=language,
                      model=model, source=source_fingerprint(media))


def _hallucinated(seg) -> bool:
    """A short, low-confidence segment that whisper probably made up.

    Segments made only of fillers are always kept: if one is real, dropping it
    would leave the "um" in the video, and if it's made up, cutting it only
    trims a little silence.
    """
    words = seg.words or []
    if words and all(normalize(w.word) in SOUNDS for w in words):
        return False
    return (seg.avg_logprob < HALLUCINATION_LOGPROB
            and seg.no_speech_prob > HALLUCINATION_NO_SPEECH
            and seg.end - seg.start < HALLUCINATION_MAX_LEN)


def _load_model(model: str, device: str, progress: bool):
    import numpy as np
    from faster_whisper import WhisperModel

    if device != "auto":
        return WhisperModel(model, device=device, compute_type="auto")
    # "auto" picks CUDA whenever an NVIDIA GPU is present, even if the CUDA
    # libraries aren't installed; that only fails at the first inference. Probe
    # with a second of silence and fall back to CPU.
    try:
        whisper = WhisperModel(model, device="auto", compute_type="auto")
        whisper.detect_language(np.zeros(16000, dtype=np.float32))
        return whisper
    except RuntimeError as e:
        _log(f"GPU not usable ({e}); falling back to CPU", progress)
        return WhisperModel(model, device="cpu", compute_type="auto")


def _log(msg: str, enabled: bool, end: str = "\n") -> None:
    if enabled:
        print(msg, end=end, file=sys.stderr, flush=True)
