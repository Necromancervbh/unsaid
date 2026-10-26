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
