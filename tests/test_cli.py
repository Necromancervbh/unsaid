import pytest

from unsaid.cli import _fmt


@pytest.mark.parametrize("seconds, text", [
    (0, "0:00.0"),
    (9.94, "0:09.9"),
    (59.96, "1:00.0"),
    (239.96, "4:00.0"),
    (3601.27, "60:01.3"),
])
def test_fmt(seconds, text):
    assert _fmt(seconds) == text
