from types import SimpleNamespace

import faster_whisper
import pytest

from unsaid import transcribe as tr
from unsaid.transcribe import _load_model


class FakeModel:
    """Mimics an NVIDIA machine without CUDA libraries: 'auto' fails at first inference."""
    created: list[str] = []

    def __init__(self, model, device="auto", compute_type="default"):
        self.device = device
        FakeModel.created.append(device)

    def detect_language(self, audio):
        if self.device == "auto":
            raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")
        return "en", 1.0, []


def test_auto_device_falls_back_to_cpu_when_cuda_libs_missing(monkeypatch):
    FakeModel.created = []
    monkeypatch.setattr(faster_whisper, "WhisperModel", FakeModel)
    model = _load_model("base", "auto", progress=False)
    assert model.device == "cpu"
    assert FakeModel.created == ["auto", "cpu"]


def test_explicit_device_is_not_probed(monkeypatch):
    FakeModel.created = []
    monkeypatch.setattr(faster_whisper, "WhisperModel", FakeModel)
    assert _load_model("base", "cuda", progress=False).device == "cuda"
    assert FakeModel.created == ["cuda"]


def segment(spec: str, avg_logprob: float, no_speech_prob: float) -> SimpleNamespace:
    """'Good.@29.71-29.79' -> a faster-whisper-like segment with word timestamps."""
    words = []
    for tok in spec.split():
        text, times = tok.rsplit("@", 1)
        start, end = times.split("-")
        words.append(SimpleNamespace(word=" " + text, start=float(start), end=float(end), probability=0.9))
    return SimpleNamespace(start=words[0].start, end=words[-1].end, text="".join(w.word for w in words),
                           avg_logprob=avg_logprob, no_speech_prob=no_speech_prob, words=words)


class SegmentModel:
    def __init__(self, segments):
        self.segments = segments

    def transcribe(self, audio, **kwargs):
        return iter(self.segments), SimpleNamespace(duration=31.0)


def transcribed_words(monkeypatch, tmp_path, segments) -> list[str]:
    media = tmp_path / "talk.wav"
    media.write_bytes(b"")
    monkeypatch.setattr(faster_whisper, "decode_audio", lambda path: None)
    monkeypatch.setattr(tr, "_load_model", lambda *args: SegmentModel(segments))
    return [w.text for w in tr.transcribe(media, language="en", progress=False).words]


# Measured with whisper 'base' and the verbatim prompt: real speech, then the
# segment whisper made up after it.
SPEECH = segment("and@28.53-28.79 nothing@28.79-29.05 is@29.05-29.31 uploaded.@29.31-29.71", -0.14, 0.0)


@pytest.mark.parametrize("made_up", [
    segment("Good.@29.71-29.79", -1.16, 0.38),
    segment("All@29.71-29.73 right.@29.73-29.81", -1.13, 0.43),
])
def test_made_up_trailing_segment_is_dropped(monkeypatch, tmp_path, made_up):
    assert transcribed_words(monkeypatch, tmp_path, [SPEECH, made_up]) == ["and", "nothing", "is", "uploaded."]


@pytest.mark.parametrize("real", [
    segment("Um.@29.9-30.1", -1.2, 0.4),  # fillers only: kept even when it looks made up
    segment("Hmm,@29.9-30.0 uh.@30.1-30.2", -1.5, 0.5),
    segment("Okay.@29.9-30.2", -0.3, 0.05),  # confident
    segment("Right.@29.9-30.2", -1.2, 0.05),  # unsure of the words, sure it's speech
    segment("and@29.9-30.1 then@30.1-30.4 we@30.4-30.6 left.@30.6-31.0", -1.2, 0.4),  # too long
])
def test_short_real_segments_are_kept(monkeypatch, tmp_path, real):
    words = transcribed_words(monkeypatch, tmp_path, [SPEECH, real])
    assert words == ["and", "nothing", "is", "uploaded."] + [w.word.strip() for w in real.words]
