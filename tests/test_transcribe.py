import faster_whisper

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
