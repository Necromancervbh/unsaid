import json

from conftest import make_transcript

from unsaid.transcript import Transcript


def test_round_trip_keeps_unheard(tmp_path):
    t = make_transcript("So@0.5-0.9 yes.@6.6-7.0", duration=8.0)
    t.unheard = [(1.0, 5.5)]
    t.save(tmp_path / "t.json")
    assert Transcript.load(tmp_path / "t.json").unheard == [(1.0, 5.5)]


def test_transcripts_saved_before_unheard_still_load(tmp_path):
    path = tmp_path / "old.json"
    path.write_text(json.dumps({"version": 1, "words": [], "duration": 3.0}), encoding="utf-8")
    assert Transcript.load(path).unheard == []
