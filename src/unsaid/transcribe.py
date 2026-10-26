"""Local speech-to-text with word timestamps, via faster-whisper."""
from __future__ import annotations

import sys
from pathlib import Path

from .transcript import Transcript, Word, source_fingerprint

# Whisper tends to "clean up" disfluencies. Priming it with text that contains
# fillers makes it transcribe them verbatim, which filler removal depends on.
VERBATIM_PROMPT = "Umm, let me think like, hmm... Okay, here's what I'm, like, thinking. Uh, so, um, yeah."


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
        initial_prompt=VERBATIM_PROMPT if verbatim and language == "en" else None,
    )
    words: list[Word] = []
    for seg in segments:
        for w in seg.words or []:
            text = w.word.strip()
            if text:
                words.append(Word(text=text, start=round(w.start, 3), end=round(w.end, 3),
                                  probability=round(w.probability, 3)))
        _log(f"\rtranscribed {seg.end:6.1f}s / {info.duration:.1f}s", progress, end="")
    _log("", progress)

    return Transcript(words=words, duration=round(info.duration, 3), language=language,
                      model=model, source=source_fingerprint(media))


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
