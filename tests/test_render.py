import av
import pytest
from conftest import make_transcript

from unsaid.cli import main
from unsaid.render import RenderError, render, snap_to_frames
from unsaid.transcript import source_fingerprint, transcript_path


def video_frames(path) -> int:
    with av.open(str(path)) as c:
        return sum(1 for _ in c.decode(video=0))


def test_snap_to_frames():
    assert snap_to_frames([(0.013, 1.517), (2.0, 2.01)], 30) == [(0.0, 1.533333)]


@pytest.mark.parametrize("keep", [
    [(0.0, 2.0), (3.0, 4.5)],
    [(0.013, 1.517), (2.271, 3.98), (4.04, 5.333)],
    [(i * 0.1, i * 0.1 + 0.05) for i in range(0, 60, 2)],  # many tiny segments
])
def test_render_keeps_exact_frame_count(synthetic_video, tmp_path, keep):
    out = tmp_path / "out.mp4"
    render(synthetic_video, keep, out, quiet=True)
    expected = round(sum(e - s for s, e in snap_to_frames(keep, 30)) * 30)
    assert video_frames(out) == expected


def test_render_audio_only(tmp_path, synthetic_video):
    audio = tmp_path / "talk.m4a"
    import subprocess
    from unsaid.render import ffmpeg_exe
    subprocess.run([ffmpeg_exe(), "-loglevel", "error", "-y", "-i", str(synthetic_video), "-vn", "-c:a", "copy",
                    str(audio)], check=True)
    out = tmp_path / "out.m4a"
    render(audio, [(0.0, 1.0), (2.0, 3.0)], out, quiet=True)
    with av.open(str(out)) as c:
        assert not c.streams.video
        assert float(c.duration / 1e6) == pytest.approx(2.0, abs=0.05)


def test_render_refuses_empty_plan(synthetic_video, tmp_path):
    with pytest.raises(RenderError):
        render(synthetic_video, [], tmp_path / "out.mp4", quiet=True)


def test_cli_cut_end_to_end_with_cached_transcript(synthetic_video, capsys):
    t = make_transcript("Hello@0.5-0.9 um@1.2-1.6 world.@2.0-2.5 uh@3.0-3.6 bye.@4.0-4.5", duration=6.0)
    t.source = source_fingerprint(synthetic_video)
    t.save(transcript_path(synthetic_video))

    assert main(["cut", str(synthetic_video), "--quiet"]) == 0
    out = synthetic_video.with_name("talk.unsaid.mp4")
    assert capsys.readouterr().out.strip() == str(out)
    # um: pauses 0.3/0.4 -> keep 0.3, cut 1.05-1.85 (0.8s). uh: 0.5/0.4 -> keep 0.4, cut 2.7-3.8 (1.1s).
    assert video_frames(out) == round((6.0 - 0.8 - 1.1) * 30)


def test_cli_dry_run_marks_removed_words(synthetic_video, capsys):
    t = make_transcript("So@0.5-0.9 um@1.2-1.6 yes.@2.0-2.5", duration=6.0)
    t.source = source_fingerprint(synthetic_video)
    t.save(transcript_path(synthetic_video))

    assert main(["cut", str(synthetic_video), "--dry-run"]) == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == "So [um] yes."
    assert "removed 1 words (1 fillers)" in captured.err
    assert not synthetic_video.with_name("talk.unsaid.mp4").exists()
