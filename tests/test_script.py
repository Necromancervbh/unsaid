from conftest import make_transcript

from unsaid.script import removed_by_script

T = make_transcript("So,@0-0.2 the@0.3-0.4 thing@0.5-0.7 is,@0.8-0.9 basically,@1.0-1.3 really@1.4-1.6 simple.@1.7-2.0 "
                    "Right?@2.5-2.8 Okay.@3.0-3.3")


def test_unchanged_script_removes_nothing():
    assert removed_by_script(T, T.to_text()) == (set(), 0)


def test_deleted_words_are_removed():
    removed, changed = removed_by_script(T, "So, the thing is really simple.\nOkay.")
    assert sorted(T.words[i].text for i in removed) == ["Right?", "basically,"]
    assert changed == 0


def test_punctuation_and_case_edits_are_not_removals():
    assert removed_by_script(T, "so the thing is basically really simple right okay") == (set(), 0)


def test_changed_words_are_kept_and_counted():
    removed, changed = removed_by_script(T, "So, the thing is, basically, very simple. Right? Okay.")
    assert removed == set()
    assert changed == 1


def test_typo_next_to_a_deleted_sentence_keeps_the_deletion():
    # difflib reports "simple. Right?" -> "simpel." as one replaced block.
    removed, changed = removed_by_script(T, "So, the thing is, basically, really simpel.\nOkay.")
    assert sorted(T.words[i].text for i in removed) == ["Right?"]
    assert changed == 1


def test_deleted_line_next_to_a_changed_word():
    t = make_transcript("It@0-0.2 edits@0.2-0.4 the@0.4-0.5 transcript.@0.6-1.0 So@1.5-1.6 if@1.6-1.7 I,@1.7-1.8 "
                        "no@1.9-2.0 wait.@2.0-2.2 If@3-3.1 I@3.1-3.2 delete@3.2-3.5")
    removed, changed = removed_by_script(t, "It edits the transcripts.\nIf I delete")
    assert sorted(t.words[i].text for i in removed) == ["I,", "So", "if", "no", "wait."]
    assert changed == 1


def test_rewritten_phrase_cuts_nothing():
    # "really simple" -> "dead easy" is a rewrite, not a deletion: keep the audio.
    removed, changed = removed_by_script(T, "So, the thing is, basically, dead easy. Right? Okay.")
    assert removed == set()
    assert changed == 2


def test_rewrite_shorter_than_the_original_cuts_nothing():
    removed, changed = removed_by_script(T, "So, the thing is, basically, trivial. Right? Okay.")
    assert removed == set()
    assert changed == 2
