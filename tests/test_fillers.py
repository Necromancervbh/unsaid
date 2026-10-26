from conftest import make_transcript

from unsaid.fillers import find_fillers


def words_at(t, idx):
    return [t.words[i].text for i in sorted(idx)]


def test_hesitation_sounds_removed_regardless_of_case_and_punctuation():
    t = make_transcript("So,@0-0.3 Um,@0.4-0.6 I@0.7-0.8 uh@0.9-1.0 think@1.1-1.3 hmm...@1.4-1.6 yes.@1.7-1.9")
    assert words_at(t, find_fillers(t)) == ["Um,", "uh", "hmm..."]


def test_verbal_fillers_only_in_aggressive_mode():
    t = make_transcript("It@0-0.2 was,@0.3-0.5 like,@0.6-0.8 huge.@0.9-1.2")
    assert find_fillers(t) == set()
    assert words_at(t, find_fillers(t, aggressive=True)) == ["like,"]


def test_aggressive_keeps_verbal_fillers_in_normal_use():
    t = make_transcript("I@0-0.2 like@0.3-0.5 it.@0.6-0.8 You@0.9-1.0 know@1.1-1.2 the@1.3-1.4 answer.@1.5-1.8")
    assert find_fillers(t, aggressive=True) == set()


def test_aggressive_removes_comma_bounded_phrase():
    t = make_transcript("It's,@0-0.3 you@0.4-0.5 know,@0.6-0.8 fine.@0.9-1.2")
    assert words_at(t, find_fillers(t, aggressive=True)) == ["you", "know,"]


def test_extra_words_and_phrases():
    t = make_transcript("Right@0-0.3 okay@0.4-0.6 so@0.7-0.8 kind@0.9-1.0 of@1.1-1.2 yes@1.3-1.5")
    assert words_at(t, find_fillers(t, extra=("okay", "kind of"))) == ["okay", "kind", "of"]
