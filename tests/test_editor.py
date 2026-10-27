import http.client
import json
import os
import subprocess
import threading

import av
import pytest
from conftest import make_transcript

from unsaid.editor import EditorServer, Session, parse_range
from unsaid.render import ffmpeg_exe


@pytest.mark.parametrize("header, expected", [
    (None, None),
    ("bytes=0-99", (0, 99)),
    ("bytes=100-", (100, 999)),
    ("bytes=-200", (800, 999)),
    ("bytes=900-5000", (900, 999)),
    ("bytes=0-1,5-9", None),  # multiple ranges: serve the whole file
    ("items=0-1", None),
])
def test_parse_range(header, expected):
    assert parse_range(header, 1000) == expected


@pytest.mark.parametrize("header", ["bytes=1000-", "bytes=5-2"])
def test_parse_range_unsatisfiable(header):
    with pytest.raises(ValueError):
        parse_range(header, 1000)


def new_session(media):
    t = make_transcript("So@0.5-0.9 um@1.2-1.6 yes.@2.0-2.5 Bye.@4.0-4.5", duration=6.0)
    return Session(media, t, media.with_name("out.mp4"))


@pytest.fixture
def editor(synthetic_video):
    server = EditorServer(new_session(synthetic_video))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


def request(server, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=60)
    headers = dict(headers or {})
    if body is not None:
        body = json.dumps(body)
        headers.setdefault("Content-Type", "application/json")
    conn.request(method, path, body=body, headers=headers)
    res = conn.getresponse()
    data = res.read()
    conn.close()
    return res, data


def test_page_and_state(editor):
    res, page = request(editor, "GET", "/")
    assert res.status == 200 and b"unsaid" in page
    res, data = request(editor, "GET", "/api/state")
    state = json.loads(data)
    assert [w[0] for w in state["words"]] == ["So", "um", "yes.", "Bye."]
    assert state["fillers"] == [1] and state["removed"] == [1]  # fillers start out cut
    assert state["has_video"] and state["name"] == "talk.mp4" and state["output"] == "out.mp4"
    assert state["output_duration"] == pytest.approx(6.0 - 0.8)


def test_edit_is_saved_and_reloaded(editor, synthetic_video):
    res, data = request(editor, "POST", "/api/edit", {"removed": [1, 3]})
    assert res.status == 200
    assert json.loads(data)["removed"] == [1, 3]
    assert new_session(synthetic_video).removed == {1, 3}


def test_edit_from_another_file_version_is_ignored(editor, synthetic_video):
    request(editor, "POST", "/api/edit", {"removed": [0, 1, 2]})
    later = synthetic_video.stat().st_mtime + 10
    os.utime(synthetic_video, (later, later))  # the file changed: its saved edit no longer applies
    assert new_session(synthetic_video).removed == {1}


@pytest.mark.parametrize("removed", [[7], ["1"], "1", None])
def test_bad_edits_are_rejected(editor, removed):
    res, _ = request(editor, "POST", "/api/edit", {"removed": removed})
    assert res.status == 400


def test_posts_must_be_json(editor):
    res, _ = request(editor, "POST", "/api/edit", body=None, headers={"Content-Type": "text/plain"})
    assert res.status == 415


def test_foreign_host_is_refused(editor):
    res, _ = request(editor, "GET", "/api/state", headers={"Host": "evil.example:80"})
    assert res.status == 403


def test_media_supports_ranges(editor, synthetic_video):
    blob = synthetic_video.read_bytes()
    res, data = request(editor, "GET", "/media")
    assert res.status == 200 and data == blob and res.getheader("Accept-Ranges") == "bytes"
    res, data = request(editor, "GET", "/media", headers={"Range": "bytes=100-199"})
    assert res.status == 206 and data == blob[100:200]
    assert res.getheader("Content-Range") == f"bytes 100-199/{len(blob)}"
    res, _ = request(editor, "GET", "/media", headers={"Range": f"bytes={len(blob)}-"})
    assert res.status == 416


def test_export_renders_the_current_edit(editor, synthetic_video):
    request(editor, "POST", "/api/edit", {"removed": [1]})
    res, data = request(editor, "POST", "/api/export", {})
    assert res.status == 200
    out = synthetic_video.with_name("out.mp4")
    assert json.loads(data)["output"] == str(out)
    with av.open(str(out)) as c:
        assert sum(1 for _ in c.decode(video=0)) == round((6.0 - 0.8) * 30)


def test_unplayable_container_gets_a_preview(synthetic_video):
    mkv = synthetic_video.with_suffix(".mkv")
    subprocess.run([ffmpeg_exe(), "-loglevel", "error", "-y", "-i", str(synthetic_video), "-c", "copy", str(mkv)],
                   check=True)
    session = new_session(mkv)
    session.make_preview(log=lambda m: None)
    assert session.preview == mkv.with_name("talk.unsaid.preview.mp4") and session.preview.exists()
    assert new_session(synthetic_video).preview == synthetic_video  # mp4 plays as is
