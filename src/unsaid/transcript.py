"""Word-level transcript model and its on-disk format."""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

FORMAT_VERSION = 1
_PUNCT = re.compile(r"[^\w']+", re.UNICODE)


def normalize(token: str) -> str:
    """Lowercase and strip punctuation so 'Um,' and 'um' compare equal."""
    return _PUNCT.sub("", token.lower()).strip("'")


@dataclass
class Word:
    text: str
    start: float
    end: float
    probability: float = 1.0

    @property
    def norm(self) -> str:
        return normalize(self.text)


@dataclass
class Transcript:
    words: list[Word]
    duration: float
    language: str = ""
    model: str = ""
    source: dict = field(default_factory=dict)

    def to_text(self) -> str:
        """Plain text, one sentence per line, for editing in any text editor."""
        lines, line = [], []
        for w in self.words:
            line.append(w.text)
            if w.text.endswith((".", "?", "!")):
                lines.append(" ".join(line))
                line = []
        if line:
            lines.append(" ".join(line))
        return "\n".join(lines) + "\n"

    def save(self, path: Path) -> None:
        data = {"version": FORMAT_VERSION, **asdict(self)}
        path.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> Transcript:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") != FORMAT_VERSION:
            raise ValueError(f"{path}: unsupported transcript format version {data.get('version')!r}")
        words = [Word(**w) for w in data["words"]]
        return cls(words=words, duration=data["duration"], language=data.get("language", ""),
                   model=data.get("model", ""), source=data.get("source", {}))


def source_fingerprint(media: Path) -> dict:
    st = media.stat()
    return {"name": media.name, "size": st.st_size, "mtime": int(st.st_mtime)}


def transcript_path(media: Path) -> Path:
    return media.with_name(media.stem + ".unsaid.json")


def load_cached(media: Path) -> Transcript | None:
    """Return the saved transcript for `media` if it exists and still matches the file."""
    path = transcript_path(media)
    if not path.exists():
        return None
    t = Transcript.load(path)
    return t if t.source == source_fingerprint(media) else None
