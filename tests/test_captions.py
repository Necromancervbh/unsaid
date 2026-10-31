import av
import numpy as np
import pytest
from conftest import make_transcript

from unsaid.captions import Cue, make_cues, retime, to_ass, to_srt, to_vtt
from unsaid.cli import main
from unsaid.transcript import Word, source_fingerprint, transcript_path


def test_retime_subtracts_the_cut_time_before_each_word():
    t = make_transcript("Hello@0.5-0.9 um@1.2-1.6 world.@2.0-2.5", duration=3.0)
    words = retime(t, {1}, [(0.0, 1.05), (1.85, 3.0)])
    assert [(w.text, w.start, w.end) for w in words] == [("Hello", 0.5, 0.9), ("world.", 1.2, 1.7)]


def test_retime_clips_a_word_to_the_range_it_is_heard_in():
    # The cut starts 0.05s before "Hello" ends by its timestamp (it snapped into silence).
    t = make_transcript("Hello@0.5-0.9 world@2.0-2.5", duration=3.0)
    words = retime(t, set(), [(0.0, 0.85), (1.9, 3.0)])
    assert [(w.start, w.end) for w in words] == [(0.5, 0.85), (0.95, 1.45)]


def test_retime_drops_words_entirely_inside_a_cut():
    t = make_transcript("a@0.1-0.2 b@1.0-1.2 c@2.0-2.2", duration=3.0)
    assert [w.text for w in retime(t, set(), [(0.0, 0.5), (1.8, 3.0)])] == ["a", "c"]


def words(spec: str) -> list[Word]:
    return make_transcript(spec).words


def test_cues_break_at_sentences_pauses_and_length():
    cues = make_cues(words("One@0-0.3 two.@0.4-0.7 Three@0.8-1.0 four@2.5-2.8 fiveeee@2.9-3.2"), max_chars=12)
    assert [c.text for c in cues] == ["One two.", "Three", "four fiveeee"]


def test_cues_linger_but_never_overlap():
    cues = make_cues(words("Hi.@0-0.2 There.@0.4-0.6 Bye.@3.0-3.2"))
    assert [(c.start, c.end) for c in cues] == [(0.0, 0.4), (0.4, 1.1), (3.0, 3.7)]


def test_srt_and_vtt():
    cues = [Cue(1.5, 3.25, words("Hello@1.5-2 world@2.5-3")), Cue(3661.0, 3662.0, words("Bye@0-1"))]
    assert to_srt(cues) == ("1\n00:00:01,500 --> 00:00:03,250\nHello world\n\n"
                            "2\n01:01:01,000 --> 01:01:02,000\nBye\n\n")
    assert to_vtt(cues).startswith("WEBVTT\n\n00:00:01.500 --> 00:00:03.250\nHello world\n\n")


def test_ass_highlights_one_word_at_a_time():
    ass = to_ass([Cue(1.0, 2.5, words("Hi@1.0-1.4 {there}@1.6-2.0"))], 1080, 1920)
    assert "PlayResX: 1080\nPlayResY: 1920" in ass
    events = [line for line in ass.splitlines() if line.startswith("Dialogue")]
    assert events == [
        "Dialogue: 0,0:00:01.00,0:00:01.60,Default,,0,0,0,,{\\c&H00FFFF&}Hi{\\r} (there)",
        "Dialogue: 0,0:00:01.60,0:00:02.50,Default,,0,0,0,,Hi {\\c&H00FFFF&}(there){\\r}",
    ]


@pytest.fixture
def cached(synthetic_video):
    t = make_transcript("Hello@0.5-0.9 um@1.2-1.6 world.@2.0-2.5 uh@3.0-3.6 bye.@4.0-4.5", duration=6.0)
    t.source = source_fingerprint(synthetic_video)
    t.save(transcript_path(synthetic_video))
    return synthetic_video


def test_cli_writes_captions_timed_to_the_edit(cached):
    assert main(["cut", str(cached), "--quiet", "--captions", "srt"]) == 0
    srt = cached.with_name("talk.unsaid.srt").read_text(encoding="utf-8")
    # "um" (0.8s cut) is gone, so "world." starts 0.8s earlier; "bye." 1.9s earlier.
    # The first cue stays up until just before the second.
    assert srt == ("1\n00:00:00,500 --> 00:00:02,000\nHello world.\n\n"
                   "2\n00:00:02,100 --> 00:00:02,900\nbye.\n\n")


def test_cli_burns_captions_into_the_video(cached, tmp_path):
    plain, burned = tmp_path / "plain.mp4", tmp_path / "burned.mp4"
    assert main(["cut", str(cached), "--quiet", "-o", str(plain)]) == 0
    assert main(["cut", str(cached), "--quiet", "-o", str(burned), "--burn-captions"]) == 0

    def frame(path, t):
        with av.open(str(path)) as c:
            for f in c.decode(video=0):
                if f.time >= t:
                    return f.to_ndarray(format="rgb24").astype(int)

    # Captions are drawn while "Hello" is spoken, and not before the first word.
    assert np.abs(frame(plain, 0.7) - frame(burned, 0.7)).mean() > 1
    assert np.abs(frame(plain, 0.2) - frame(burned, 0.2)).mean() < 0.5


def test_cli_refuses_to_burn_captions_into_audio(cached, tmp_path, capsys):
    import subprocess
    from unsaid.render import ffmpeg_exe
    audio = cached.with_suffix(".m4a")
    subprocess.run([ffmpeg_exe(), "-loglevel", "error", "-y", "-i", str(cached), "-vn", "-c:a", "copy", str(audio)],
                   check=True)
    t = make_transcript("Hello@0.5-0.9 bye.@4.0-4.5", duration=6.0)
    t.source = source_fingerprint(audio)
    t.save(transcript_path(audio))
    assert main(["cut", str(audio), "--quiet", "--burn-captions"]) == 1
    assert "no video" in capsys.readouterr().err
