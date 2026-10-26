"""Render the kept ranges to a new file with ffmpeg (trim + concat, re-encoded)."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from fractions import Fraction
from functools import cache
from pathlib import Path

from .cuts import Range

FADE = 0.008  # seconds of audio fade at each cut, to avoid clicks


class RenderError(RuntimeError):
    pass


@cache
def ffmpeg_exe() -> str:
    """UNSAID_FFMPEG, then ffmpeg on PATH, then the binary bundled with imageio-ffmpeg."""
    exe = os.environ.get("UNSAID_FFMPEG") or shutil.which("ffmpeg")
    if exe:
        return exe
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


@cache
def _ffmpeg_version() -> tuple[int, int]:
    out = subprocess.run([ffmpeg_exe(), "-version"], capture_output=True, text=True).stdout
    m = re.search(r"version n?(\d+)\.(\d+)", out)
    return (int(m.group(1)), int(m.group(2))) if m else (99, 0)  # git builds: assume recent


@dataclass
class MediaInfo:
    has_video: bool
    has_audio: bool
    fps: Fraction | None = None


def probe(media: Path) -> MediaInfo:
    import av

    try:
        with av.open(str(media)) as c:
            video = [s for s in c.streams.video if not s.disposition & av.stream.Disposition.attached_pic]
            fps = (video[0].average_rate or video[0].guessed_rate) if video else None
            return MediaInfo(has_video=bool(video), has_audio=bool(c.streams.audio),
                             fps=Fraction(fps) if fps else None)
    except av.FFmpegError as e:
        raise RenderError(f"could not read {media}: {e}") from e


def snap_to_frames(keep: list[Range], fps: float) -> list[Range]:
    """
    Move cut points onto the frame grid. Video can only be cut between frames,
    so unsnapped cuts make each video segment up to a frame longer than its
    audio, and concat pads the audio with silence to stay in sync.
    """
    out = []
    for s, e in keep:
        s2, e2 = round(s * fps) / fps, round(e * fps) / fps
        if e2 > s2:
            out.append((round(s2, 6), round(e2, 6)))
    return out


def build_filtergraph(keep: list[Range], video: bool, fps: float | None = None) -> str:
    # With frame-snapped ranges, trim video half a frame early so no frame timestamp
    # sits exactly on a boundary: [s, e) then selects exactly (e - s) * fps frames,
    # matching the audio cut at s and e.
    shift = 0.5 / fps if fps else 0.0
    parts, pads = [], []
    for i, (s, e) in enumerate(keep):
        d = e - s
        if video:
            parts.append(f"[0:v]trim=start={max(0.0, s - shift):.6f}:end={e - shift:.6f},"
                         f"setpts=PTS-STARTPTS[v{i}]")
            pads.append(f"[v{i}]")
        fade = min(FADE, d / 4)
        parts.append(
            f"[0:a]atrim=start={s:.6f}:end={e:.6f},asetpts=PTS-STARTPTS,"
            f"afade=t=in:d={fade:.6f},afade=t=out:st={d - fade:.6f}:d={fade:.6f}[a{i}]"
        )
        pads.append(f"[a{i}]")
    outs = "[outv][outa]" if video else "[outa]"
    parts.append(f"{''.join(pads)}concat=n={len(keep)}:v={int(video)}:a=1{outs}")
    return ";\n".join(parts)


def render(media: Path, keep: list[Range], output: Path, crf: int = 18, preset: str = "veryfast",
           quiet: bool = False) -> None:
    if not keep:
        raise RenderError("nothing left to render: every word was removed")
    info = probe(media)
    if not info.has_audio:
        raise RenderError(f"{media} has no audio track")
    fps = info.fps if info.has_video else None
    if fps:
        keep = snap_to_frames(keep, float(fps))

    graph = build_filtergraph(keep, video=info.has_video, fps=float(fps) if fps else None)
    with tempfile.NamedTemporaryFile("w", suffix=".ffgraph", delete=False, encoding="utf-8") as f:
        f.write(graph)
        graph_file = f.name
    # The graph can be thousands of segments long, so pass it as a file, not an argument.
    graph_args = (["-/filter_complex", graph_file] if _ffmpeg_version() >= (7, 1)
                  else ["-filter_complex_script", graph_file])

    cmd = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-nostats" if quiet else "-stats",
           "-y", "-i", str(media), *graph_args]
    if info.has_video:
        cmd += ["-map", "[outv]", "-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p"]
        if fps:
            # Without an explicit rate the MP4 muxer drops the final frame of the edit.
            cmd += ["-r", f"{fps.numerator}/{fps.denominator}"]
    cmd += ["-map", "[outa]", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(output)]

    try:
        proc = subprocess.run(cmd, stderr=subprocess.PIPE if quiet else None, text=True)
    finally:
        os.unlink(graph_file)
    if proc.returncode != 0:
        raise RenderError(f"ffmpeg failed (exit {proc.returncode})" + (f":\n{proc.stderr}" if quiet else ""))


def default_output(media: Path, has_video: bool) -> Path:
    return media.with_name(f"{media.stem}.unsaid{'.mp4' if has_video else '.m4a'}")
