import subprocess

import pytest

from unsaid.render import ffmpeg_exe
from unsaid.transcript import Transcript, Word


def make_transcript(spec: str, duration: float | None = None) -> Transcript:
    """'Hello@0.5-0.9 um@1.2-1.4 world@1.8-2.3' -> Transcript."""
    words = []
    for tok in spec.split():
        text, times = tok.rsplit("@", 1)
        start, end = times.split("-")
        words.append(Word(text=text, start=float(start), end=float(end)))
    return Transcript(words=words, duration=duration if duration is not None else words[-1].end + 0.5)


@pytest.fixture
def synthetic_video(tmp_path):
    """A 6-second 30fps test pattern with a tone."""
    path = tmp_path / "talk.mp4"
    subprocess.run([ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc=duration=6:size=160x120:rate=30",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
                    "-c:v", "libx264", "-c:a", "aac", "-shortest", str(path)], check=True)
    return path
