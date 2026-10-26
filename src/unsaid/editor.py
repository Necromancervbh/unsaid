"""`unsaid edit`: a local browser editor. Strike words in the transcript, preview the cut, export.

The server binds to 127.0.0.1 only and serves one media file. Edits are saved
next to the media as <stem>.unsaid.edit.json, so closing the tab loses nothing.
"""
from __future__ import annotations

import json
import re
import subprocess
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from urllib.parse import urlsplit

from .cuts import plan_cuts
from .fillers import find_fillers
from .render import RenderError, ffmpeg_exe, probe, render
from .transcript import Transcript, source_fingerprint

# Containers browsers can play directly. Anything else (.mkv, .avi, .mts, ...)
# gets a small H.264 preview copy; the export always renders from the original.
PLAYABLE = {
    ".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/mp4", ".webm": "video/webm",
    ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".aac": "audio/aac", ".wav": "audio/wav",
    ".ogg": "audio/ogg", ".oga": "audio/ogg", ".opus": "audio/ogg", ".flac": "audio/flac",
}
CHUNK = 256 * 1024


class Session:
    def __init__(self, media: Path, transcript: Transcript, output: Path, max_pause: float | None = None,
                 fillers: bool = True):
        self.media, self.transcript, self.output, self.max_pause = media, transcript, output, max_pause
        self.info = probe(media)
        self.fillers = find_fillers(transcript) if fillers else set()
        self.edit_path = media.with_name(media.stem + ".unsaid.edit.json")
        self.removed = self._load_edit()
        self.preview = media
        self.lock = threading.Lock()

    def _load_edit(self) -> set[int]:
        if self.edit_path.exists():
            data = json.loads(self.edit_path.read_text(encoding="utf-8"))
            if data.get("source") == source_fingerprint(self.media):
                return self._valid(data.get("removed", []))
        return set(self.fillers)

    def _valid(self, removed) -> set[int]:
        n = len(self.transcript.words)
        if not isinstance(removed, list) or not all(isinstance(i, int) and 0 <= i < n for i in removed):
            raise ValueError("removed must be a list of word indices")
        return set(removed)

    def plan(self) -> dict:
        p = plan_cuts(self.transcript, self.removed, max_pause=self.max_pause)
        return {"removed": sorted(self.removed), "keep": p.keep, "output_duration": p.output_duration}

    def state(self) -> dict:
        t = self.transcript
        return {"name": self.media.name, "output": self.output.name, "duration": t.duration,
                "has_video": self.info.has_video, "words": [[w.text, w.start, w.end] for w in t.words],
                "fillers": sorted(self.fillers), **self.plan()}

    def edit(self, removed) -> dict:
        with self.lock:
            self.removed = self._valid(removed)
            self.edit_path.write_text(json.dumps({"source": source_fingerprint(self.media),
                                                  "removed": sorted(self.removed)}), encoding="utf-8")
            return self.plan()

    def export(self) -> dict:
        with self.lock:
            p = plan_cuts(self.transcript, self.removed, max_pause=self.max_pause)
            render(self.media, p.keep, self.output, quiet=True)
            return {"output": str(self.output), "output_duration": p.output_duration}

    def make_preview(self, log=print) -> None:
        """Browsers can't play every container; give them a small H.264 copy instead."""
        if self.media.suffix.lower() in PLAYABLE:
            return
        preview = self.media.with_name(self.media.stem + ".unsaid.preview.mp4")
        if not preview.exists() or preview.stat().st_mtime < self.media.stat().st_mtime:
            log(f"making a browser preview of {self.media.name} (the export still uses the original)...")
            cmd = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(self.media)]
            if self.info.has_video:
                cmd += ["-vf", "scale=-2:min(480\\,ih)", "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
                        "-pix_fmt", "yuv420p"]
            cmd += ["-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(preview)]
            if subprocess.run(cmd).returncode != 0:
                raise RenderError(f"could not make a browser preview of {self.media}")
        self.preview = preview


def parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    """'bytes=a-b' -> (a, b) inclusive. None: serve the whole file. ValueError: unsatisfiable."""
    m = re.fullmatch(r"bytes=(\d*)-(\d*)", (header or "").strip())
    if not m or not (m.group(1) or m.group(2)):
        return None
    if m.group(1):
        start = int(m.group(1))
        end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
    else:  # suffix range: the last n bytes
        start, end = max(0, size - int(m.group(2))), size - 1
    if start >= size or start > end:
        raise ValueError(header)
    return start, end


class Handler(BaseHTTPRequestHandler):
    server: EditorServer

    def do_GET(self) -> None:
        if not self._local():
            return
        path = urlsplit(self.path).path
        if path == "/":
            page = resources.files("unsaid").joinpath("editor.html").read_bytes()
            self._send(HTTPStatus.OK, page, "text/html; charset=utf-8")
        elif path == "/media":
            self._send_media()
        elif path == "/api/state":
            self._json(self.server.session.state())
        else:
            self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")

    def do_POST(self) -> None:
        if not self._local():
            return
        # A JSON content type forces a CORS preflight, which this server never
        # answers, so other websites can't drive it from the user's browser.
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            return self._json({"error": "expected application/json"}, HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            path = urlsplit(self.path).path
            if path == "/api/edit":
                self._json(self.server.session.edit(body.get("removed")))
            elif path == "/api/export":
                self._json(self.server.session.export())
            else:
                self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        except (ValueError, json.JSONDecodeError) as e:
            self._json({"error": str(e)}, HTTPStatus.BAD_REQUEST)
        except RenderError as e:
            self._json({"error": str(e)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def _local(self) -> bool:
        """Refuse requests whose Host isn't this server (DNS rebinding)."""
        port = self.server.server_address[1]
        if self.headers.get("Host") in (f"127.0.0.1:{port}", f"localhost:{port}"):
            return True
        self._send(HTTPStatus.FORBIDDEN, b"forbidden", "text/plain")
        return False

    def _send_media(self) -> None:
        path = self.server.session.preview
        size = path.stat().st_size
        ctype = PLAYABLE.get(path.suffix.lower(), "video/mp4")
        try:
            rng = parse_range(self.headers.get("Range"), size)
        except ValueError:
            self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            self.send_header("Content-Range", f"bytes */{size}")
            self.end_headers()
            return
        start, end = rng or (0, size - 1)
        self.send_response(HTTPStatus.PARTIAL_CONTENT if rng else HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if rng:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        try:
            with path.open("rb") as f:
                f.seek(start)
                left = end - start + 1
                while left > 0 and (chunk := f.read(min(CHUNK, left))):
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except (ConnectionError, OSError):
            pass  # the browser cancels range requests all the time while seeking

    def _json(self, data: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        self._send(status, json.dumps(data).encode(), "application/json")

    def _send(self, status: HTTPStatus, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args) -> None:
        pass


class EditorServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, session: Session, port: int = 0):
        self.session = session
        super().__init__(("127.0.0.1", port), Handler)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}/"


def serve(session: Session, port: int = 0, open_browser: bool = True, log=print) -> None:
    session.make_preview(log)
    server = EditorServer(session, port)
    log(f"editing {session.media.name} at {server.url}  (Ctrl+C to stop)")
    if open_browser:
        webbrowser.open(server.url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
