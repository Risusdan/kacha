"""Tests for pipeline.review_server: the review page on 127.0.0.1 behind a random key.

Every test that talks to a server gets one from the `served` fixture: a project from
helpers.edited_project_from_script (the sentences are made up) whose edit.json passed validate and whose review video
was made with real ffmpeg, and a ReviewServer on it with the key KEY. The speaker said extra words before sentence 2
(t01, rejected by default) and before sentence 3 (t03), and a filler inside sentence 2 (t02, accepted by default).
The cases of a parametrisation that change nothing in the project but the log of the requests share one such project,
made once for the module, each with a server of its own (`served_read_only`). Requests go through http.client, so
that a test can send any Host header and any Content-Type. Every server is stopped in a fixture finaliser, whatever
the test did.

The approval tests give the server FakeSteps (storyboard, approve and render that record their calls, and can wait or
raise) and fake shutil.disk_usage (the `disk` fixture), so that they run nothing real and do not depend on the free
space of the machine. No test opens a browser or a file manager: reveal is a recorder, and reveal_in_folder runs with
subprocess.run replaced.
"""

import http.client
import inspect
import json
import socket
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest
from helpers import edited_project_from_script, stand_in_png, stand_in_render_stills

from pipeline import review_server
from pipeline.assets import MIN_FREE_GB
from pipeline.edit import load_edit, validate_project
from pipeline.errors import EditInvalid, PipelineError
from pipeline.paths import studio_root
from pipeline.project import Project, ProjectBusy, ProjectLock, approve_gate, gate_ok, run_step, step_done
from pipeline.render import render
from pipeline.captions import break_lines, remap_units
from pipeline.review_actions import CAPTION_EMPTY_TEXT, CONFLICT_TEXT, GLYPH_TEXT, INVISIBLE_TEXT, REFUSED_TEXT
from pipeline.review_data import NOT_READY_TEXT, edit_version, page_data
from pipeline.review_server import (
    APPROVED_TEXT,
    BUSY_TEXT,
    DENIED_TEXT,
    DISK_TEXT,
    DRAIN_CAP,
    FAILED_TEXT,
    OUTPUT_BUSY_TEXT,
    PAGE_DIR,
    REQUEST_TIMEOUT_S,
    REVIEW_IDLE_S,
    SERVER_ERROR_TEXT,
    ReviewServer,
    Steps,
    reveal_command,
    reveal_in_folder,
    serve_review,
)
from pipeline.review_requests import RangeNotSatisfiable, byte_range, cookie_values
from pipeline.review_video import make_review_video, review_video_path
from pipeline.storyboard import NOT_VALIDATED_REFUSAL, OLDER_FILES_REFUSAL, build_storyboard
from pipeline.style import font_path, load_style, styles_root

SCRIPT = "第一句話。今天我們來聊剪輯。第三句話。"
ASR = "第一句話順便說一下今天我們來聊嗯剪輯另外補充一下第三句話"
KEY = "a-test-key-made-for-these-tests"
MAX_BODY = 65536


@dataclass(frozen=True)
class FromEnd:
    """SIZE - n in a parameter list: a byte offset n bytes before the end of the review video, whose size a test
    learns only once the video is made."""

    n: int

    def at(self, size: int) -> int:
        return size - self.n


class _Size:
    def __sub__(self, n: int) -> FromEnd:
        return FromEnd(n)


SIZE = _Size()  # the size of the review video, in a parameter list


def offset(value: int | FromEnd, size: int) -> int:
    return value.at(size) if isinstance(value, FromEnd) else value


@dataclass(frozen=True)
class Served:
    p: Project
    server: ReviewServer


@dataclass(frozen=True)
class Answer:
    status: int
    headers: http.client.HTTPMessage
    body: bytes

    def json(self):
        return json.loads(self.body)


@pytest.fixture
def serve(request):
    """A function that starts a ReviewServer on a project and has it stopped when the test ends."""

    def start(p: Project, **kwargs) -> ReviewServer:
        server = ReviewServer(p, **kwargs)
        request.addfinalizer(server.stop)
        server.start()
        return server

    return start


@pytest.fixture
def validated(tmp_path) -> Project:
    """A project from edited_project_from_script whose edit.json passed validate (no review video)."""
    p = edited_project_from_script(tmp_path, SCRIPT, ASR)
    validate_project(p)
    return p


@pytest.fixture
def served(validated, serve):
    make_review_video(validated)
    return Served(validated, serve(validated, key=KEY))


@pytest.fixture(scope="module")
def read_only_project(module_env) -> Project:
    """A project as `validated` makes it, with its review video, made once in the module's own studio for the cases
    that change nothing in it (served_read_only)."""
    assert studio_root().is_relative_to(module_env)  # checked before anything is written
    p = edited_project_from_script(module_env, SCRIPT, ASR)
    validate_project(p)
    make_review_video(p)
    return p


@pytest.fixture
def served_read_only(read_only_project, serve):
    """`served` on the module's shared project, with a server of the test's own: for a case that changes nothing in
    the project but the log of the requests."""
    return Served(read_only_project, serve(read_only_project, key=KEY))


def ask(server: ReviewServer, method: str, path: str, *, cookie: str | None = KEY, host: str | None = None,
        headers: tuple[tuple[str, str], ...] = (), body: bytes | None = None, length: str | None = "auto",
        timeout: float = 30) -> Answer:
    """Send one request as written, on a connection of its own. The Host header is 127.0.0.1:<port> unless `host`
    is given; the cookie review_key_<port> is `cookie` unless that is None. A `body` goes with a Content-Length of
    its size, or of `length` when that is not "auto" (None leaves the header out)."""
    connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=timeout)
    try:
        connection.putrequest(method, path, skip_host=host is not None, skip_accept_encoding=True)
        if host is not None:
            connection.putheader("Host", host)
        if cookie is not None:
            connection.putheader("Cookie", f"review_key_{server.port}={cookie}")
        for name, value in headers:
            connection.putheader(name, value)
        if body is not None and length == "auto":
            length = str(len(body))
        if body is not None and length is not None:
            connection.putheader("Content-Length", length)
        connection.endheaders(body)
        response = connection.getresponse()
        return Answer(response.status, response.headers, response.read())
    finally:
        connection.close()


def send_raw(server: ReviewServer, data: bytes) -> bytes:
    """Send `data` as it is on a connection of its own and return all the server sends back before it closes."""
    with socket.create_connection(("127.0.0.1", server.port), timeout=10) as connection:
        connection.sendall(data)
        pieces = []
        while piece := connection.recv(65536):
            pieces.append(piece)
    return b"".join(pieces)


def change_body(version: str, change: dict) -> bytes:
    return json.dumps({"version": version, "change": change}, ensure_ascii=False).encode("utf-8")


def post_change(server: ReviewServer, version: str, change: dict,
                content_type: str = "application/json; charset=utf-8") -> Answer:
    return ask(server, "POST", "/api/change", headers=(("Content-Type", content_type),),
               body=change_body(version, change))


def cut(trim: str, cut: bool = True) -> dict:
    return {"type": "cut", "trim": trim, "cut": cut}


def log_text(p: Project) -> str:
    path = p.work / "review.log"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def editing(version: str) -> dict:
    """GET /api/state of a page that may change the edit."""
    return {"version": version, "state": "editing", "step": None, "error": None, "outputs": None}


STATE_KEYS = ("state", "step", "error", "outputs")  # the keys of GET /api/state after the version


def data_state(server: ReviewServer, timeout: float = 30) -> dict:
    """The keys of GET /api/data that hold the state of the page (the ones GET /api/state has after its version):
    what the first view of the page shows before its first poll."""
    answer = ask(server, "GET", "/api/data", timeout=timeout)
    assert answer.status == 200
    document = answer.json()
    assert all(key in document for key in STATE_KEYS), sorted(document)
    return {key: document[key] for key in STATE_KEYS}


def assert_json(answer: Answer, status: int, document: dict, case: str = "") -> None:
    """`answer` is `document` as JSON with `status`; `case` names the request in the message of a failure."""
    assert answer.status == status, case
    assert answer.headers["Content-Type"] == "application/json; charset=utf-8", case
    assert answer.headers["Cache-Control"] == "no-store", case
    assert answer.json() == document, case


# ---- the key, the cookie and the Host ---------------------------------------------------------


def test_the_key_sets_a_cookie_and_redirects(served):
    server = served.server
    assert server.url == f"http://127.0.0.1:{server.port}/?key={KEY}"
    answer = ask(server, "GET", f"/?key={KEY}", cookie=None)
    assert answer.status == 303
    assert answer.headers["Location"] == "/"
    assert answer.headers["Cache-Control"] == "no-store"
    parts = [part.strip() for part in answer.headers["Set-Cookie"].split(";")]
    assert parts[0] == f"review_key_{server.port}={KEY}"
    assert set(parts[1:]) == {"HttpOnly", "SameSite=Strict", "Path=/"}

    page = ask(server, "GET", "/")
    assert page.status == 200
    assert page.headers["Content-Type"] == "text/html; charset=utf-8"
    assert page.headers["Cache-Control"] == "no-store"
    assert page.body == (PAGE_DIR / "index.html").read_bytes()
    assert "<title>審閱頁</title>" in page.body.decode("utf-8")


def test_without_a_key_the_server_makes_a_random_one(served, serve):
    first, second = serve(served.p), serve(served.p)
    keys = [server.url.split("/?key=", 1)[1] for server in (first, second)]
    assert first.url == f"http://127.0.0.1:{first.port}/?key={keys[0]}"
    assert keys[0] != keys[1] and KEY not in keys
    assert all(len(key) >= 43 for key in keys)  # 32 random bytes
    assert ask(first, "GET", f"/?key={keys[0]}", cookie=None).status == 303
    assert ask(first, "GET", f"/?key={keys[1]}", cookie=None).status == 403


def test_no_cookie_and_a_wrong_key_are_refused(served):
    server = served.server
    refused = [
        ask(server, "GET", "/", cookie=None),
        ask(server, "GET", "/api/data", cookie=None),
        ask(server, "GET", "/media/review.mp4", cookie=None),
        ask(server, "GET", "/font/caption.ttf", cookie=None),
        ask(server, "GET", "/nothing/here", cookie=None),
        ask(server, "GET", "/", cookie="wrong"),
        ask(server, "GET", "/api/state", cookie=KEY + "x"),
        ask(server, "GET", "/?key=wrong", cookie=None),
        ask(server, "GET", "/?key=", cookie=None),
        ask(server, "GET", f"/?key={KEY}&key={KEY}", cookie=None),
        ask(server, "POST", "/api/change", cookie=None, headers=(("Content-Type", "application/json"),),
            body=change_body(edit_version(served.p), cut("t01"))),
    ]
    for answer in refused:
        assert_json(answer, 403, {"error": DENIED_TEXT})
        assert "Set-Cookie" not in answer.headers
    assert load_edit(served.p.edit_path)["cuts"]["reject"] == ["t01", "t03"]


def test_the_key_under_another_cookie_name_is_refused(served):
    """The cookie is named after the port: a browser sends a host's cookies to every port of it."""
    server = served.server
    port = server.port
    for cookie in (f"review_key={KEY}", f"review_key_{port + 1}={KEY}", f"review_key_={KEY}",
                   f"review_key_{port}0={KEY}", f"x_review_key_{port}={KEY}"):
        answer = ask(server, "GET", "/api/state", cookie=None, headers=(("Cookie", cookie),))
        assert_json(answer, 403, {"error": DENIED_TEXT}, cookie)
    answer = ask(server, "GET", "/api/state", cookie=None, headers=(("Cookie", f"a=1; review_key_{port}={KEY}"),))
    assert answer.status == 200


def test_another_host_name_is_refused(served):
    server = served.server
    port = server.port
    for host in (f"localhost:{port}", "evil.test", "127.0.0.1", f"127.0.0.1:{port}.evil.test", f"[::1]:{port}"):
        for path, cookie in ((f"/?key={KEY}", None), ("/api/data", KEY), ("/", KEY)):
            answer = ask(server, "GET", path, cookie=cookie, host=host)
            assert_json(answer, 403, {"error": DENIED_TEXT})
            assert "Set-Cookie" not in answer.headers


def test_a_method_without_a_handler_is_refused_for_another_host_first(served):
    server = served.server
    for method in ("HEAD", "PUT", "OPTIONS", "DELETE"):
        foreign = ask(server, method, "/api/data", host="evil.test")
        assert (foreign.status, foreign.headers["Cache-Control"]) == (403, "no-store"), method
        ours = ask(server, method, "/api/data")
        assert ours.status == 501, method
        if method == "HEAD":  # an answer to HEAD has no body
            assert foreign.body == ours.body == b""
        else:
            assert_json(foreign, 403, {"error": DENIED_TEXT}, method)
            assert_json(ours, 501, {"error": SERVER_ERROR_TEXT}, method)


def test_cookie_values_finds_the_cookie_among_others():
    assert cookie_values(["a=1; review_key=K; b=2"], "review_key") == ["K"]
    assert cookie_values(["review_key=K1", "x=2;review_key=K2"], "review_key") == ["K1", "K2"]
    assert cookie_values(["review_key", "other_review_key=K", "review_keys=K"], "review_key") == []
    assert cookie_values([], "review_key") == []


# ---- routes -----------------------------------------------------------------------------------


def test_unknown_paths_are_404_and_reach_no_file(served):
    paths = [
        "/../state.json", "/review_page/x", "/media/../edit.json", "/review_page/index.html", "/index.html",
        "/state.json", "/media/edit.json", "/media/%2e%2e/edit.json", "/font/../edit.json", "//state.json",
        "/api/data/../../state.json", "/REVIEW.JS", "/review.js/", "/media/review.mp4/..", "/api/change",
    ]
    for path in paths:
        assert_json(ask(served.server, "GET", path), 404, {"error": SERVER_ERROR_TEXT})
    for path in ("/api/data", "/", "/api/change/x"):
        answer = ask(served.server, "POST", path, headers=(("Content-Type", "application/json"),), body=b"{}")
        assert_json(answer, 404, {"error": SERVER_ERROR_TEXT})


def test_the_page_files_are_served_by_their_fixed_names(served, tmp_path, monkeypatch):
    page = tmp_path / "page"
    page.mkdir()
    files = {
        "index.html": ("/", "text/html; charset=utf-8"),
        "review.js": ("/review.js", "text/javascript; charset=utf-8"),
        "logic.js": ("/logic.js", "text/javascript; charset=utf-8"),
        "review.css": ("/review.css", "text/css; charset=utf-8"),
    }
    for name in [*files, "other.js"]:
        (page / name).write_text(f"// {name} 的內容\n", encoding="utf-8")
    monkeypatch.setattr(review_server, "PAGE_DIR", page)
    for name, (route, kind) in files.items():
        answer = ask(served.server, "GET", route)
        assert (answer.status, answer.headers["Content-Type"]) == (200, kind)
        assert answer.headers["Cache-Control"] == "no-store"
        assert answer.body == (page / name).read_bytes()
    assert ask(served.server, "GET", "/other.js").status == 404
    (page / "review.css").unlink()
    assert_json(ask(served.server, "GET", "/review.css"), 404, {"error": SERVER_ERROR_TEXT})


PAGE_MODULES = ("review.js", "logic.js", "shared.js", "editor.js")  # the page's script: review.js, what it imports


def test_every_module_of_the_page_is_served_with_the_key_and_refused_without_it(served):
    # index.html loads review.js, which imports the others by relative URL: a browser asks for each of them by the
    # route of its own name. The routes are exactly the page's files (nothing else reaches PAGE_DIR).
    server = served.server
    assert set(review_server.PAGE_FILES) == {"/", "/review.css", *(f"/{name}" for name in PAGE_MODULES)}
    for name in PAGE_MODULES:
        route = f"/{name}"
        answer = ask(server, "GET", route)
        assert (answer.status, answer.headers["Content-Type"]) == (200, "text/javascript; charset=utf-8"), route
        assert answer.headers["Cache-Control"] == "no-store", route
        assert answer.body == (PAGE_DIR / name).read_bytes(), route
        for refused in (ask(server, "GET", route, cookie=None), ask(server, "GET", route, cookie="wrong")):
            assert_json(refused, 403, {"error": DENIED_TEXT}, route)
            assert "Set-Cookie" not in refused.headers, route


def test_the_caption_font_is_the_one_the_style_names(served):
    style = load_style(load_edit(served.p.edit_path)["style"])
    assert style["captions"]["bold"] is True
    answer = ask(served.server, "GET", "/font/caption.ttf")
    assert (answer.status, answer.headers["Content-Type"]) == (200, "font/ttf")
    assert answer.headers["Cache-Control"] == "no-store"
    assert answer.body == font_path(style, "bold").read_bytes()


def test_a_style_without_bold_captions_gives_the_regular_font(served, tmp_path, monkeypatch):
    """A copy of the default style with captions.bold false, under tmp_path, with stand-in font files whose bytes
    tell them apart (the real fonts are not touched)."""
    document = json.loads((styles_root() / "default" / "style.json").read_text(encoding="utf-8"))
    document["captions"]["bold"] = False
    folder = tmp_path / "styles" / "default"
    (folder / "fonts").mkdir(parents=True)
    (folder / "style.json").write_text(json.dumps(document), encoding="utf-8")
    for weight in ("regular", "bold"):
        (folder / document["fonts"][weight]).write_bytes(f"the {weight} font".encode())
    monkeypatch.setattr("pipeline.style.styles_root", lambda: tmp_path / "styles")
    answer = ask(served.server, "GET", "/font/caption.ttf")
    assert (answer.status, answer.body) == (200, b"the regular font")


# ---- the videos and byte ranges ----------------------------------------------------------------


@pytest.mark.parametrize("header, status, first, last", [
    ("bytes=0-1", 206, 0, 1), ("bytes=1000-", 206, 1000, SIZE - 1), ("bytes=-500", 206, SIZE - 500, SIZE - 1)])
def test_ranges_of_the_review_video(served_read_only, header, status, first, last):
    data = review_video_path(served_read_only.p).read_bytes()
    size = len(data)
    first, last = offset(first, size), offset(last, size)
    answer = ask(served_read_only.server, "GET", "/media/review.mp4", headers=(("Range", header),))
    assert answer.status == status
    assert answer.headers["Content-Type"] == "video/mp4"
    assert answer.headers["Accept-Ranges"] == "bytes"
    assert answer.headers["Cache-Control"] == "no-store"
    assert answer.headers["Content-Range"] == f"bytes {first}-{last}/{size}"
    assert answer.headers["Content-Length"] == str(last - first + 1)
    assert answer.body == data[first:last + 1]


def test_a_range_past_the_end_is_416(served):
    size = review_video_path(served.p).stat().st_size
    for header in (f"bytes={size}-", f"bytes={size + 100}-{size + 200}", "bytes=-0"):
        answer = ask(served.server, "GET", "/media/review.mp4", headers=(("Range", header),))
        assert answer.status == 416
        assert answer.headers["Content-Range"] == f"bytes */{size}"
        assert answer.headers["Accept-Ranges"] == "bytes"
        assert answer.headers["Cache-Control"] == "no-store"
        assert answer.body == b""


def test_without_a_range_the_whole_video_is_sent(served):
    data = review_video_path(served.p).read_bytes()
    for headers in ((), (("Range", "bytes=0-1,5-6"),), (("Range", "items=0-1"),)):
        answer = ask(served.server, "GET", "/media/review.mp4", headers=headers)
        assert answer.status == 200
        assert answer.headers["Accept-Ranges"] == "bytes"
        assert answer.headers["Content-Length"] == str(len(data))
        assert "Content-Range" not in answer.headers
        assert answer.body == data


def test_the_preview_is_served_once_it_exists(served):
    assert_json(ask(served.server, "GET", "/media/preview.mp4"), 404, {"error": SERVER_ERROR_TEXT})
    data = bytes(range(256)) * 40
    (served.p.out / "preview.mp4").write_bytes(data)
    whole = ask(served.server, "GET", "/media/preview.mp4")
    assert (whole.status, whole.headers["Content-Type"], whole.body) == (200, "video/mp4", data)
    part = ask(served.server, "GET", "/media/preview.mp4", headers=(("Range", "bytes=10-19"),))
    assert (part.status, part.headers["Content-Range"], part.body) == (206, f"bytes 10-19/{len(data)}", data[10:20])


@pytest.mark.parametrize("header, size, expected", [
    (None, 10, None),
    ("bytes=0-1", 10, (0, 1)),
    ("bytes=2-", 10, (2, 9)),
    ("bytes=-3", 10, (7, 9)),
    ("bytes=0-99", 10, (0, 9)),  # an end past the file stops at its last byte
    ("bytes=-99", 10, (0, 9)),  # a suffix longer than the file is all of it
    ("bytes=9-9", 10, (9, 9)),
    ("bytes=5-2", 10, None),  # what this server does not read is answered with the whole file
    ("bytes=0-1,4-5", 10, None),
    ("items=0-1", 10, None),
    ("bytes=-", 10, None),
    ("bytes=x-y", 10, None),
    ("bytes=" + "9" * 30 + "-", 10, None),
])
def test_byte_range_reads_one_range_and_ignores_the_rest(header, size, expected):
    assert byte_range(header, size) == expected


@pytest.mark.parametrize("header, size", [
    ("bytes=10-", 10), ("bytes=10-20", 10), ("bytes=-0", 10), ("bytes=0-", 0), ("bytes=-5", 0)])
def test_byte_range_at_or_past_the_end_is_not_satisfiable(header, size):
    with pytest.raises(RangeNotSatisfiable):
        byte_range(header, size)


# ---- the page data and its state --------------------------------------------------------------


def test_data_is_page_data_with_the_state(served):
    assert_json(ask(served.server, "GET", "/api/data"), 200,
                {**page_data(served.p), "state": "editing", "step": None, "error": None, "outputs": None})
    assert_json(ask(served.server, "GET", "/api/state"), 200, editing(edit_version(served.p)))


def test_no_key_of_the_state_is_a_key_of_the_page_data(served):
    assert not set(page_data(served.p)) & set(STATE_KEYS)


def test_data_takes_the_state_from_one_snapshot(served, monkeypatch):
    """state, step, error and outputs of the data come from one call of ReviewServer.state, so a job that ends
    between two reads cannot give a page "working" with the error of the job's end."""
    calls: list[int] = []

    def state(self) -> dict:
        calls.append(len(calls))
        name = f"call {len(calls)}"
        return {"state": name, "step": name, "error": name, "outputs": None}

    monkeypatch.setattr(ReviewServer, "state", state)
    document = ask(served.server, "GET", "/api/data").json()
    assert len(calls) == 1
    assert [document[key] for key in ("state", "step", "error")] == ["call 1"] * 3


def test_data_while_edit_json_is_not_validated_is_409_in_chinese(served):
    p = served.p
    seen = edit_version(p)
    edit = load_edit(p.edit_path)
    edit["captions"]["mode"] = "word"  # Claude wrote it; validate has not seen it
    p.edit_path.write_text(json.dumps(edit, ensure_ascii=False), encoding="utf-8")
    before = p.edit_path.read_bytes()
    assert_json(ask(served.server, "GET", "/api/data"), 409, {"error": NOT_READY_TEXT})
    assert_json(post_change(served.server, edit_version(p), cut("t01")), 409, {"error": NOT_READY_TEXT})
    assert_json(post_change(served.server, seen, cut("t01")), 409, {"error": NOT_READY_TEXT})
    assert_json(post_approve(served.server, edit_version(p)), 409, {"error": NOT_READY_TEXT})
    assert p.edit_path.read_bytes() == before
    assert_json(ask(served.server, "GET", "/api/state"), 200, editing(edit_version(p)))


def test_the_state_of_a_project_without_edit_json_has_a_version(served):
    p = served.p
    p.edit_path.unlink()  # Claude is writing it again
    assert_json(ask(served.server, "GET", "/api/state"), 200, editing(edit_version(p)))
    assert_json(ask(served.server, "GET", "/api/data"), 409, {"error": NOT_READY_TEXT})


def rewrite(path: Path) -> None:
    """Write the JSON document at `path` again with the same content in other bytes (as a step run again may)."""
    path.write_text(json.dumps(json.loads(path.read_text(encoding="utf-8")), ensure_ascii=False), encoding="utf-8")


# ---- changes ----------------------------------------------------------------------------------


def test_a_change_round_trip(served):
    p, server = served.p, served.server
    seen = ask(server, "GET", "/api/data").json()["version"]
    answer = post_change(server, seen, cut("t01"), content_type="Application/JSON ; charset=UTF-8")
    assert_json(answer, 200, {"version": edit_version(p)})
    assert answer.json()["version"] != seen
    assert "t01" in load_edit(p.edit_path)["cuts"]["accept"]
    data = ask(server, "GET", "/api/data").json()
    assert data["version"] == answer.json()["version"]
    assert next(row for row in data["trims"] if row["id"] == "t01")["cut"] is True


def test_a_change_with_an_old_version_is_409_conflict(served):
    p = served.p
    before = p.edit_path.read_bytes()
    assert_json(post_change(served.server, "0" * 64, cut("t01")), 409, {"error": CONFLICT_TEXT, "conflict": True})
    assert p.edit_path.read_bytes() == before


def test_a_refused_change_is_422_in_chinese(served):
    p = served.p
    before = p.edit_path.read_bytes()
    for change in (cut("t99"), {"type": "rename", "sentence": "s01"}):
        assert_json(post_change(served.server, edit_version(p), change), 422, {"error": REFUSED_TEXT})
    assert p.edit_path.read_bytes() == before
    assert "t99" in log_text(p)  # apply_change logged the English reason


def test_a_post_that_is_not_json_is_400(served):
    p, server = served.p, served.server
    before = p.edit_path.read_bytes()
    good = change_body(edit_version(p), cut("t01"))  # a change that would apply, were it let through
    padded = json.dumps({"version": edit_version(p), "change": cut("t01"), "pad": ""})
    too_long = padded.replace('"pad": ""', '"pad": "' + "a" * (MAX_BODY + 1 - len(padded)) + '"').encode()
    assert len(too_long) == MAX_BODY + 1
    cases = [  # (what the case is, Content-Type, body, Content-Length)
        ("text/plain", "text/plain", good, "auto"),
        ("a form", "application/x-www-form-urlencoded", b"version=1&change=2", "auto"),
        ("a multipart form", "multipart/form-data; boundary=x", good, "auto"),
        ("another media type", "application/jsonp", good, "auto"),
        ("no Content-Type", None, good, "auto"),
        ("not JSON", "application/json", b"{not json", "auto"),
        ("not UTF-8", "application/json", b"\xff\xfe\xfd", "auto"),
        ("nested too deep", "application/json", b"[" * 60000, "auto"),  # deeper than the JSON reader goes
        ("an array", "application/json", b"[]", "auto"),
        ("a string", "application/json", b'"text"', "auto"),
        ("no version", "application/json", json.dumps({"change": cut("t01")}).encode(), "auto"),
        ("a version not a string", "application/json",
         json.dumps({"version": 1, "change": cut("t01")}).encode(), "auto"),
        ("a change not an object", "application/json",
         json.dumps({"version": edit_version(p), "change": ["t01"]}).encode(), "auto"),
        ("no change", "application/json", json.dumps({"version": edit_version(p)}).encode(), "auto"),
        ("one byte too long", "application/json", too_long, "auto"),
        ("2 MB", "application/json", b"a" * 2_000_000, "auto"),  # read and dropped: unread, it resets the connection
        ("no Content-Length", "application/json", good, None),
        ("a Content-Length not a number", "application/json", good, "abc"),
        ("a negative Content-Length", "application/json", good, "-1"),
    ]
    for case, content_type, body, length in cases:
        headers = () if content_type is None else (("Content-Type", content_type),)
        answer = ask(server, "POST", "/api/change", headers=headers, body=body, length=length)
        assert_json(answer, 400, {"error": SERVER_ERROR_TEXT}, case)
    assert p.edit_path.read_bytes() == before
    # a body of exactly the most there may be is read: its old version makes it a conflict, not a 400
    at_most = json.dumps({"version": "0" * 64, "change": cut("t01"), "pad": ""})
    at_most = at_most.replace('"pad": ""', '"pad": "' + "a" * (MAX_BODY - len(at_most)) + '"').encode()
    answer = ask(server, "POST", "/api/change", headers=(("Content-Type", "application/json"),), body=at_most)
    assert len(at_most) == MAX_BODY and answer.status == 409


def test_a_body_declared_past_the_drain_cap_is_400_at_once(served):
    """The client declares a body longer than DRAIN_CAP and sends none of it: the server answers without waiting
    for it (draining it would wait for bytes that never come)."""
    assert DRAIN_CAP == 4 * 1024 * 1024
    began = time.monotonic()
    answer = ask(served.server, "POST", "/api/change", headers=(("Content-Type", "application/json"),), body=b"",
                 length=str(DRAIN_CAP + 1), timeout=10)
    assert time.monotonic() - began < 5
    assert_json(answer, 400, {"error": SERVER_ERROR_TEXT})


def test_changes_are_applied_one_at_a_time(served, monkeypatch):
    inside, most = 0, 0
    count = threading.Lock()

    def slow_change(p, version, change):
        nonlocal inside, most
        with count:
            inside += 1
            most = max(most, inside)
        time.sleep(0.2)
        with count:
            inside -= 1
        return {"version": version}

    monkeypatch.setattr(review_server, "apply_change", slow_change)
    answers = []
    threads = [threading.Thread(target=lambda: answers.append(post_change(served.server, "v", cut("t01"))))
               for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert [answer.status for answer in answers] == [200, 200, 200]
    assert most == 1


def test_a_caption_change_round_trip(served):
    p, server = served.p, served.server
    caption = {"type": "caption", "sentence": "s02", "text": "今天我們來聊剪接"}
    assert_json(post_change(server, edit_version(p), caption), 200, {"version": edit_version(p)})
    assert load_edit(p.edit_path)["captions"]["text"] == {"s02": "今天我們來聊剪接"}
    second = ask(server, "GET", "/api/data").json()["sentences"][1]
    assert (second["caption"], second["caption_edited"]) == ("今天我們來聊剪接", True)
    assert_json(post_change(server, edit_version(p), {**caption, "text": None}), 200, {"version": edit_version(p)})
    assert "text" not in load_edit(p.edit_path)["captions"]


def test_a_caption_change_the_font_cannot_show_is_422_in_chinese(served):
    p = served.p
    before = p.edit_path.read_bytes()
    caption = {"type": "caption", "sentence": "s02", "text": "今天\U0001F600"}
    assert_json(post_change(served.server, edit_version(p), caption), 422,
                {"error": GLYPH_TEXT.format(chars="\U0001F600")})
    assert_json(post_change(served.server, edit_version(p), {**caption, "text": "，"}), 422,
                {"error": CAPTION_EMPTY_TEXT})
    assert_json(post_change(served.server, edit_version(p), {**caption, "text": "今天\u200b"}), 422,
                {"error": INVISIBLE_TEXT})
    assert_json(post_change(served.server, edit_version(p), {**caption, "text": "今天\u200b\U0001F600"}), 422,
                {"error": GLYPH_TEXT.format(chars="\U0001F600")})
    assert p.edit_path.read_bytes() == before


# ---- the lines of a caption text, before it is saved ------------------------------------------


def post_lines(server: ReviewServer, document: object, content_type: str = "application/json") -> Answer:
    return ask(server, "POST", "/api/caption-lines", headers=(("Content-Type", content_type),),
               body=json.dumps(document, ensure_ascii=False).encode("utf-8"))


def project_tree(p: Project) -> dict[str, bytes]:
    """Every file of the project but work/review.log, which logs each request: name -> bytes."""
    return {str(path.relative_to(p.dir)): path.read_bytes() for path in sorted(p.dir.rglob("*"))
            if path.is_file() and path != p.work / "review.log"}


def test_caption_lines_preview_writes_nothing(served):
    p, server = served.p, served.server
    before = project_tree(p)
    text = "今天我們來聊一聊怎麼把一支直拍的影片剪成短影音"  # longer than one line
    answer = post_lines(server, {"sentence": "s02", "text": text})
    edit = load_edit(p.edit_path)
    style = load_style(edit["style"])
    sentence = json.loads((p.work / "transcript.json").read_text(encoding="utf-8"))["sentences"][1]
    lines = break_lines(text, remap_units(sentence["text"], sentence["units"], text), font_path(style, "bold"), style)
    assert len(lines) > 1
    assert_json(answer, 200, {"lines": [line.text for line in lines]})
    assert project_tree(p) == before
    assert "POST /api/caption-lines 200" in log_text(p)


@pytest.mark.parametrize("document, text", [
    ({"sentence": "s02", "text": "今天\U0001F603來\U0001F600聊\U0001F603"},
     GLYPH_TEXT.format(chars="\U0001F603\U0001F600")),
    ({"sentence": "s02", "text": "今天\u200b來聊"}, INVISIBLE_TEXT),
    ({"sentence": "s02", "text": "今天\u200b來\U0001F600聊"}, GLYPH_TEXT.format(chars="\U0001F600")),
    ({"sentence": "s02", "text": "  "}, CAPTION_EMPTY_TEXT),
    ({"sentence": "s02", "text": "，。"}, CAPTION_EMPTY_TEXT),
    ({"sentence": "s02", "text": "今天\u2028我們"}, REFUSED_TEXT),
    ({"sentence": "s99", "text": "好"}, REFUSED_TEXT),
])
def test_a_caption_text_that_would_be_refused_is_422_in_chinese(served_read_only, document, text):
    p, server = served_read_only.p, served_read_only.server
    before = project_tree(p)
    logged = len(log_text(p))  # the log is shared by the cases of this module: read only what this request adds
    assert_json(post_lines(server, document), 422, {"error": text})
    assert project_tree(p) == before
    assert "refused" not in log_text(p)[logged:]  # only a change is logged: a preview goes with every keystroke


def test_caption_lines_take_no_lock(served):
    server = served.server
    with server.change_lock:  # what a change being applied or the approval job holds
        answer = post_lines(server, {"sentence": "s01", "text": "第一句話吧"})
    assert_json(answer, 200, {"lines": ["第一句話吧"]})


def test_caption_lines_keep_the_rules_of_every_post(served):
    p, server = served.p, served.server
    good = {"sentence": "s01", "text": "第一句話吧"}
    for case, document, content_type in [
        ("text/plain", good, "text/plain"),
        ("no text", {"sentence": "s01"}, "application/json"),
        ("text null", {"sentence": "s01", "text": None}, "application/json"),
        ("text a number", {"sentence": "s01", "text": 5}, "application/json"),
        ("no sentence", {"text": "好"}, "application/json"),
        ("an array", ["s01", "好"], "application/json"),
    ]:
        assert_json(post_lines(server, document, content_type), 400, {"error": SERVER_ERROR_TEXT}, case)
    too_long = json.dumps({"sentence": "s01", "text": "好" * MAX_BODY}).encode()
    answer = ask(server, "POST", "/api/caption-lines", headers=(("Content-Type", "application/json"),),
                 body=too_long)
    assert_json(answer, 400, {"error": SERVER_ERROR_TEXT})
    body = json.dumps(good, ensure_ascii=False).encode("utf-8")
    json_type = (("Content-Type", "application/json"),)
    assert_json(ask(server, "POST", "/api/caption-lines", cookie=None, headers=json_type, body=body), 403,
                {"error": DENIED_TEXT})
    assert_json(ask(server, "POST", "/api/caption-lines", host="localhost", headers=json_type, body=body), 403,
                {"error": DENIED_TEXT})
    edit = load_edit(p.edit_path)
    edit["captions"]["mode"] = "word"  # Claude wrote it; validate has not seen it
    p.edit_path.write_text(json.dumps(edit, ensure_ascii=False), encoding="utf-8")
    assert_json(post_lines(server, good), 409, {"error": NOT_READY_TEXT})


# ---- errors and the log -----------------------------------------------------------------------


def test_an_unexpected_error_is_500_in_chinese_and_logged(served, monkeypatch):
    def failed_write(p, version, change):
        raise PipelineError("cannot write /somewhere/work/edit.json: No space left on device")

    def broken_data(p):
        raise RuntimeError("something broke inside page_data")

    monkeypatch.setattr(review_server, "apply_change", failed_write)
    monkeypatch.setattr(review_server, "page_data", broken_data)
    assert_json(post_change(served.server, edit_version(served.p), cut("t01")), 500, {"error": SERVER_ERROR_TEXT})
    assert_json(ask(served.server, "GET", "/api/data"), 500, {"error": SERVER_ERROR_TEXT})
    log = log_text(served.p)
    assert "No space left on device" in log
    assert "something broke inside page_data" in log


def test_requests_are_logged_to_review_log_and_not_to_stderr(served, capfd):
    server = served.server
    ask(server, "GET", f"/?key={KEY}", cookie=None)
    ask(server, "GET", "/api/state")
    ask(server, "GET", "/nothing")
    ask(server, "GET", "/api/data", cookie=None)
    assert_json(ask(server, "PUT", "/api/data"), 501, {"error": SERVER_ERROR_TEXT})  # the standard library refuses it
    out, err = capfd.readouterr()
    assert (out, err) == ("", "")
    log = log_text(served.p)
    for line in ("GET / 303", "GET /api/state 200", "GET /nothing 404", "GET /api/data 403", "PUT /api/data 501"):
        assert line in log
    assert KEY not in log


def test_a_request_line_that_cannot_be_read_leaves_the_key_out_of_the_log(served, capfd):
    server = served.server
    host = f"Host: 127.0.0.1:{server.port}\r\n".encode()
    syntax = send_raw(server, f"GET /?key={KEY} HTTP/1.0 HTTP/1.0\r\n".encode() + host + b"\r\n")
    assert syntax.startswith(b"HTTP/1.0 400 ")
    assert syntax.endswith(json.dumps({"error": SERVER_ERROR_TEXT}, ensure_ascii=False).encode())
    version = send_raw(server, f"GET /?key={KEY} HTTX/1.0\r\n".encode() + host + b"\r\n")
    assert version == json.dumps({"error": SERVER_ERROR_TEXT}, ensure_ascii=False).encode()  # read as HTTP/0.9
    assert capfd.readouterr() == ("", "")
    log = log_text(served.p)
    assert log.count(" 400\n") == 2
    assert KEY not in log and "key" not in log


def test_a_connection_that_sends_nothing_ends_after_the_timeout(served, monkeypatch, capfd):
    assert REQUEST_TIMEOUT_S == 60
    assert review_server._Handler.timeout == REQUEST_TIMEOUT_S
    monkeypatch.setattr(review_server._Handler, "timeout", 0.3)
    began = time.monotonic()
    assert send_raw(served.server, b"") == b""  # the server closes the connection
    assert time.monotonic() - began < 5
    assert capfd.readouterr() == ("", "")
    assert "Request timed out" in log_text(served.p)


# ---- starting and stopping --------------------------------------------------------------------


def test_stop_closes_the_port_and_wait_returns_stopped(served, serve):
    server = serve(served.p, key=KEY)
    assert server.idle_s == REVIEW_IDLE_S
    assert ask(server, "GET", "/api/state").status == 200
    done = []
    waiter = threading.Thread(target=lambda: done.append(server.wait()))
    waiter.start()
    server.stop()
    waiter.join(timeout=10)
    assert done == ["stopped"]
    with pytest.raises(ConnectionRefusedError):
        ask(server, "GET", "/api/state")
    server.stop()  # a second stop does nothing


def test_the_server_polls_often_so_that_stop_does_not_wait_half_a_second(read_only_project, serve, monkeypatch):
    """stop() calls shutdown(), which waits until serve_forever next looks at its flag: it looks every poll_interval
    seconds. The server passes SERVE_POLL_S, a twentieth of a second at most, not the standard library's default of
    half a second. A spy on serve_forever reads the interval it got, as a clock could not: the wait is anywhere
    from none to a whole interval."""
    original = review_server._HTTPServer.serve_forever
    polls = []

    def spy(self, *args, **kwargs):
        bound = inspect.signature(original).bind(self, *args, **kwargs)
        bound.apply_defaults()
        polls.append(bound.arguments["poll_interval"])
        return original(self, *args, **kwargs)

    monkeypatch.setattr(review_server._HTTPServer, "serve_forever", spy)
    server = serve(read_only_project, key=KEY)
    assert ask(server, "GET", "/api/state").status == 200
    server.stop()
    assert len(polls) == 1 and polls[0] <= 0.05
    assert polls == [review_server.SERVE_POLL_S]


def test_stop_waits_for_a_change_being_applied(served, monkeypatch):
    server = served.server
    inside, go = threading.Event(), threading.Event()

    def slow_change(p, version, change):
        inside.set()
        go.wait(10)
        return {"version": "after"}

    monkeypatch.setattr(review_server, "apply_change", slow_change)
    answers = []
    poster = threading.Thread(target=lambda: answers.append(post_change(server, "v", cut("t01"))))
    poster.start()
    assert inside.wait(10)
    stopper = threading.Thread(target=server.stop)
    stopper.start()
    deadline = time.monotonic() + 10
    while True:  # wait until the port is closed: stop() is then past server_close()
        try:
            socket.create_connection(("127.0.0.1", server.port), timeout=1).close()
        except ConnectionRefusedError:
            break
        assert time.monotonic() < deadline
        time.sleep(0.05)
    stopper.join(timeout=0.3)
    assert stopper.is_alive()  # the change still holds the lock
    go.set()
    stopper.join(timeout=10)
    poster.join(timeout=10)
    assert not stopper.is_alive()
    assert [(answer.status, answer.json()) for answer in answers] == [(200, {"version": "after"})]


def test_a_change_after_stop_began_is_not_applied(served):
    p, server = served.p, served.server
    before = p.edit_path.read_bytes()
    server.closed = True  # what stop() sets first
    assert_json(post_change(server, edit_version(p), cut("t01")), 403, {"error": DENIED_TEXT})
    assert p.edit_path.read_bytes() == before


# ---- approval from the page -------------------------------------------------------------------

GIB = 2**30
RENDER_FAILURE = "render failed after 2 attempts: the encoder stopped at frame 12"


class FakeSteps:
    """Stand-ins for storyboard, approve and render. Each call is recorded with its arguments, the version of
    edit.json when it began, and the step the server reports while it runs (once `server` is set). `hold` names a
    step that waits inside until release() (`holding` is set once it is there); `fail` names a step that raises
    `error`; `act` maps a step to what it does to the project."""

    def __init__(self, hold: str | None = None, fail: str | None = None, error: BaseException | None = None,
                 act: dict | None = None):
        self.hold, self.fail, self.act = hold, fail, act or {}
        self.error = PipelineError(RENDER_FAILURE) if error is None else error
        self.calls: list[tuple] = []
        self.steps_seen: list[str | None] = []
        self.versions: list[str] = []
        self.holding, self._go = threading.Event(), threading.Event()
        self.server: ReviewServer | None = None

    def steps(self) -> Steps:
        return Steps(storyboard=lambda p: self._run("storyboard", p),
                     approve=lambda p, gate: self._run("approve", p, gate),
                     render=lambda p: self._run("render", p))

    def release(self) -> None:
        self._go.set()

    def _run(self, name: str, p: Project, *args) -> dict:
        self.calls.append((name, p, *args))
        self.versions.append(edit_version(p))
        if self.server is not None:
            self.steps_seen.append(self.server.state()["step"])
        if name == self.hold:
            self.holding.set()
            self._go.wait(30)
        if name in self.act:
            self.act[name](p)
        if name == self.fail:
            raise self.error
        return {}


@pytest.fixture
def disk(monkeypatch):
    """shutil.disk_usage as the approval reads it: `free` bytes free (ample unless a test lowers it); `asked` lists
    the folders it was asked about."""
    state = SimpleNamespace(free=100 * GIB, asked=[])

    def usage(path):
        state.asked.append(Path(path))
        return SimpleNamespace(total=200 * GIB, used=200 * GIB - state.free, free=state.free)

    monkeypatch.setattr(review_server.shutil, "disk_usage", usage)
    return state


@pytest.fixture
def serve_job(request, serve, validated, disk):
    """A function that starts a ReviewServer with the steps of `fake` on the validated project. The fake is
    released when the test ends, before the server is stopped (stop waits for a job)."""

    def start(fake: FakeSteps, **kwargs) -> ReviewServer:
        server = serve(validated, key=KEY, steps=fake.steps(), **kwargs)
        fake.server = server
        request.addfinalizer(fake.release)  # finalisers run last first: this one before server.stop
        return server

    return start


def post_approve(server: ReviewServer, version: str, timeout: float = 30) -> Answer:
    return ask(server, "POST", "/api/approve", headers=(("Content-Type", "application/json"),),
               body=json.dumps({"version": version}).encode(), timeout=timeout)


def state_of(server: ReviewServer, timeout: float = 30) -> dict:
    answer = ask(server, "GET", "/api/state", timeout=timeout)
    assert answer.status == 200
    return answer.json()


def settled(server: ReviewServer, timeout: float = 30) -> dict:
    """GET /api/state once the approval job has ended."""
    deadline = time.monotonic() + timeout
    while (document := state_of(server))["state"] == "working":
        assert time.monotonic() < deadline, document
        time.sleep(0.05)
    return document


def in_thread(target) -> tuple[threading.Thread, list]:
    """Run `target()` in a thread of its own; its result lands in the returned list."""
    results: list = []
    thread = threading.Thread(target=lambda: results.append(target()))
    thread.start()
    return thread, results


def test_steps_and_reveal_default_to_the_real_ones(validated):
    assert (Steps().storyboard, Steps().approve, Steps().render) == (build_storyboard, approve_gate, render)
    server = ReviewServer(validated)
    assert server.steps == Steps()
    assert server.reveal is reveal_in_folder


def test_approve_runs_storyboard_approve_render_in_order(validated, serve_job, disk):
    p = validated
    fake = FakeSteps()
    server = serve_job(fake)
    version = edit_version(p)
    answer = post_approve(server, version)
    assert_json(answer, 202, {"version": version, "state": "working", "step": "storyboard", "error": None,
                              "outputs": None})
    assert settled(server) == editing(version)  # the fakes did not approve the gate
    assert fake.calls == [("storyboard", p), ("approve", p, "gate1"), ("render", p)]
    assert fake.steps_seen == ["storyboard", "approve", "render"]
    assert disk.asked == [p.dir]
    assert "approval" in log_text(p)


def test_approve_with_an_old_version_runs_nothing(validated, serve_job):
    fake = FakeSteps()
    server = serve_job(fake)
    assert_json(post_approve(server, "0" * 64), 409, {"error": CONFLICT_TEXT, "conflict": True})
    for content_type, body in (("application/json", b"{}"), ("application/json", b'{"version": 1}'),
                               ("text/plain", json.dumps({"version": edit_version(validated)}).encode())):
        answer = ask(server, "POST", "/api/approve", headers=(("Content-Type", content_type),), body=body)
        assert_json(answer, 400, {"error": SERVER_ERROR_TEXT}, repr(body))
    assert fake.calls == []
    assert state_of(server) == editing(edit_version(validated))


def test_approve_without_free_space_runs_nothing(validated, serve_job, disk):
    fake = FakeSteps()
    server = serve_job(fake)
    disk.free = MIN_FREE_GB * GIB - 1
    assert_json(post_approve(server, edit_version(validated)), 507, {"error": DISK_TEXT})
    assert fake.calls == []
    assert state_of(server)["state"] == "editing"
    disk.free = MIN_FREE_GB * GIB  # exactly the threshold is enough
    assert post_approve(server, edit_version(validated)).status == 202
    settled(server)
    assert [call[0] for call in fake.calls] == ["storyboard", "approve", "render"]
    assert disk.asked == [validated.dir, validated.dir]


def test_an_approval_while_the_project_is_busy_starts_nothing(validated, serve_job):
    fake = FakeSteps()
    server = serve_job(fake)
    version = edit_version(validated)
    with ProjectLock(validated):  # a storyboard, approve or render that runs from the chat
        assert_json(post_approve(server, version), 423, {"error": OUTPUT_BUSY_TEXT})
        assert state_of(server) == editing(version)
    assert OUTPUT_BUSY_TEXT == "這支影片正在別處輸出（可能是在和 Claude 的對話裡），等它完成再按同意。"
    assert fake.calls == []
    assert post_approve(server, version).status == 202  # once it has ended
    settled(server)
    assert [call[0] for call in fake.calls] == ["storyboard", "approve", "render"]


@pytest.mark.parametrize("fail", [None, "storyboard", "approve", "render"])
def test_the_job_holds_the_project_lock_through_its_steps_and_lets_it_go_at_its_end(validated, serve_job, fail):
    p = validated
    held: list[bool] = []  # for each step that ran: whether the project lock was held while it ran

    def lock_is_held(p: Project) -> None:
        lock = ProjectLock(p)
        try:
            lock.acquire()
        except ProjectBusy:
            held.append(True)
        else:
            lock.release()
            held.append(False)

    fake = FakeSteps(fail=fail, act={step: lock_is_held for step in ("storyboard", "approve", "render")})
    server = serve_job(fake)
    assert post_approve(server, edit_version(p)).status == 202
    assert settled(server)["state"] == ("editing" if fail is None else "failed")
    assert held == [True] * len(fake.calls) and len(fake.calls) > 0
    with ProjectLock(p):  # free once the job has ended, however it ended
        pass


def test_changes_wait_for_the_job_and_are_refused_while_it_runs(validated, serve_job):
    p = validated
    fake = FakeSteps(hold="render")
    server = serve_job(fake)
    version = edit_version(p)
    before = p.edit_path.read_bytes()
    assert post_approve(server, version).status == 202
    assert fake.holding.wait(10)
    # the polls never wait for the change lock, which the job holds
    assert state_of(server, timeout=5) == {"version": version, "state": "working", "step": "render", "error": None,
                                           "outputs": None}
    assert data_state(server, timeout=5) == {"state": "working", "step": "render", "error": None, "outputs": None}
    assert_json(post_change(server, version, cut("t01")), 423, {"error": BUSY_TEXT})
    assert_json(post_approve(server, version, timeout=5), 423, {"error": BUSY_TEXT})
    assert p.edit_path.read_bytes() == before
    fake.release()
    assert settled(server)["state"] == "editing"
    assert post_change(server, version, cut("t01")).status == 200  # the job is over: changes land again
    assert [call[0] for call in fake.calls] == ["storyboard", "approve", "render"]


def test_two_approvals_at_once_start_one_job_and_no_change_lands_before_it(validated, serve_job, monkeypatch):
    """The approval decides (version, state, disk) and starts its job in one turn of the lock: a second approval
    and a change that arrive while it decides find the job running, and the job sees the version that was
    approved."""
    p = validated
    deciding = threading.Event()

    def slow_usage(path):
        deciding.set()
        time.sleep(0.3)  # the other requests arrive while the approval is deciding
        return SimpleNamespace(total=200 * GIB, used=100 * GIB, free=100 * GIB)

    fake = FakeSteps(hold="storyboard")
    server = serve_job(fake)
    monkeypatch.setattr(review_server.shutil, "disk_usage", slow_usage)
    version = edit_version(p)
    first, first_answer = in_thread(lambda: post_approve(server, version))
    assert deciding.wait(10)
    second, second_answer = in_thread(lambda: post_approve(server, version))
    change, change_answer = in_thread(lambda: post_change(server, version, cut("t01")))
    for thread in (second, change):
        thread.join(timeout=20)
    assert fake.holding.wait(10)
    fake.release()
    first.join(timeout=20)
    assert first_answer[0].status == 202
    assert_json(second_answer[0], 423, {"error": BUSY_TEXT})
    assert_json(change_answer[0], 423, {"error": BUSY_TEXT})
    settled(server)
    assert [call[0] for call in fake.calls] == ["storyboard", "approve", "render"]
    assert fake.versions == [version, version, version]


@pytest.mark.parametrize("step, error, logged", [
    ("render", PipelineError(RENDER_FAILURE), ["PipelineError", RENDER_FAILURE]),
    ("storyboard", RuntimeError("the storyboard broke"), ["RuntimeError: the storyboard broke", "Traceback"]),
])
def test_a_failed_render_is_failed_in_chinese_and_logged(validated, serve_job, step, error, logged):
    p = validated
    fake = FakeSteps(fail=step, error=error)
    server = serve_job(fake)
    version = edit_version(p)
    assert post_approve(server, version).status == 202
    failed = {"version": version, "state": "failed", "step": None, "error": FAILED_TEXT, "outputs": None}
    assert settled(server) == failed
    order = ["storyboard", "approve", "render"]
    assert [call[0] for call in fake.calls] == order[:order.index(step) + 1]
    assert data_state(server) == {"state": "failed", "step": None, "error": FAILED_TEXT, "outputs": None}
    log = log_text(p)
    assert all(text in log for text in logged), log
    assert f"failed in {step}" in log

    answer = post_change(server, version, cut("t01"))  # a change that lands clears the failure
    assert answer.status == 200
    version = answer.json()["version"]
    assert state_of(server) == editing(version)

    assert post_approve(server, version).status == 202  # fails again
    assert settled(server) == {**failed, "version": version}
    fake.fail = None
    assert post_approve(server, version).status == 202  # an approval may follow a failure, and clears it
    assert settled(server) == editing(version)


@pytest.fixture
def meijia(edited_project) -> Project:
    """The short Meijia project (conftest's edited_project: prep, transcribe and trim really ran, so gate 1 can be
    approved), with its edit.json validated."""
    validate_project(edited_project)
    return edited_project


def test_an_edit_changed_while_the_storyboard_was_made_fails_the_job(validated, serve_job):
    """Claude writes another validated edit.json while the job makes the storyboard: the job does not approve a
    storyboard the user did not see."""
    p = validated

    def claude_changes_the_edit(p: Project) -> None:
        edit = load_edit(p.edit_path)
        edit["cuts"]["reject"].remove("t01")
        edit["cuts"]["accept"].append("t01")
        p.edit_path.write_text(json.dumps(edit, ensure_ascii=False), encoding="utf-8")
        validate_project(p)

    fake = FakeSteps(act={"storyboard": claude_changes_the_edit})
    server = serve_job(fake)
    version = edit_version(p)
    assert post_approve(server, version).status == 202
    outcome = settled(server)
    changed = edit_version(p)  # once the job has ended: the fake storyboard writes edit.json in the job's thread
    assert outcome == {"version": changed, "state": "failed", "step": None, "error": FAILED_TEXT, "outputs": None}
    assert changed != version
    assert [call[0] for call in fake.calls] == ["storyboard"]
    assert "the edit changed after the page approved it" in log_text(p)


def test_a_transcript_made_again_while_the_storyboard_was_made_fails_the_job(validated, serve_job):
    """transcribe runs again while the job makes the storyboard and Claude validates the same edit.json on it: the
    storyboard was made from files the user did not see, so it is not approved."""
    p = validated
    edit_bytes = p.edit_path.read_bytes()

    def transcribe_runs_again(p: Project) -> None:
        rewrite(p.work / "transcript.json")
        validate_project(p)

    fake = FakeSteps(act={"storyboard": transcribe_runs_again})
    server = serve_job(fake)
    version = edit_version(p)
    assert post_approve(server, version).status == 202
    assert settled(server)["state"] == "failed"
    assert p.edit_path.read_bytes() == edit_bytes and edit_version(p) != version
    assert [call[0] for call in fake.calls] == ["storyboard"]
    assert "the edit changed after the page approved it" in log_text(p)


@pytest.mark.parametrize("name", ["transcript.json", "trim.json"])
def test_a_change_or_an_approval_for_the_files_before_a_new_transcript_or_trim_is_a_conflict(validated, serve_job,
                                                                                            name):
    p = validated
    fake = FakeSteps()
    server = serve_job(fake)
    seen = state_of(server)["version"]
    edit_bytes = p.edit_path.read_bytes()
    rewrite(p.work / name)  # transcribe or trim ran again; Claude validated the same edit.json on it
    validate_project(p)
    now = state_of(server)["version"]
    assert now != seen and now == edit_version(p) == ask(server, "GET", "/api/data").json()["version"]
    assert_json(post_change(server, seen, cut("t01")), 409, {"error": CONFLICT_TEXT, "conflict": True})
    assert_json(post_approve(server, seen), 409, {"error": CONFLICT_TEXT, "conflict": True})
    assert fake.calls == []
    assert p.edit_path.read_bytes() == edit_bytes


def test_an_approval_in_the_chat_shows_as_approved(meijia, serve, disk):
    p = meijia
    server = serve(p, key=KEY)
    build_storyboard(p)
    approve_gate(p, "gate1")
    version = edit_version(p)
    before = p.edit_path.read_bytes()
    assert state_of(server) == {"version": version, "state": "approved", "step": None, "error": None,
                                "outputs": None}
    assert data_state(server) == {"state": "approved", "step": None, "error": None, "outputs": None}
    assert_json(post_change(server, version, cut("t01")), 423, {"error": APPROVED_TEXT})
    assert_json(post_approve(server, version), 423, {"error": APPROVED_TEXT})
    assert p.edit_path.read_bytes() == before
    assert gate_ok(p, "gate1")
    assert disk.asked == []


def _fake_render(p: Project) -> Path:
    """Record the render step as done with a stand-in out/final.mp4, as if the render had run."""
    final = p.out / "final.mp4"

    def run() -> dict:
        final.parent.mkdir(exist_ok=True)
        final.write_bytes(b"a stand-in for the final video")
        return {}

    run_step(p, "render", {"edit": p.edit_path}, [final], run)
    return final


def test_done_lists_the_outputs_and_reveal_calls_the_injected_function(meijia, serve, disk):
    p = meijia
    revealed: list[Path] = []
    server = serve(p, key=KEY, reveal=revealed.append)
    build_storyboard(p)
    approve_gate(p, "gate1")
    final = _fake_render(p)
    assert step_done(p, "render")
    version = edit_version(p)
    done = {"version": version, "state": "done", "step": None, "error": None,
            "outputs": {"final": str(final), "preview": None, "folder": str(p.out)}}
    assert state_of(server) == done
    assert data_state(server) == {key: done[key] for key in STATE_KEYS}
    (p.out / "preview.mp4").write_bytes(b"a stand-in for the preview")
    done["outputs"]["preview"] = str(p.out / "preview.mp4")
    assert state_of(server) == done
    assert data_state(server) == {key: done[key] for key in STATE_KEYS}
    assert_json(post_change(server, version, cut("t01")), 423, {"error": APPROVED_TEXT})
    assert_json(post_approve(server, version), 423, {"error": APPROVED_TEXT})

    as_json = (("Content-Type", "application/json"),)
    # only a JSON request, like every other POST: a page of another port of 127.0.0.1 is same-site for the cookie,
    # and its no-cors POST cannot send application/json
    for headers, body in (((), None), ((), b""), ((("Content-Type", "text/plain"),), b"{}"),
                          ((("Content-Type", "application/x-www-form-urlencoded"),), b"a=1"),
                          (as_json, None), (as_json, b"not json"), (as_json, b"[]")):
        answer = ask(server, "POST", "/api/reveal", headers=headers, body=body)
        assert_json(answer, 400, {"error": SERVER_ERROR_TEXT}, repr((headers, body)))
    assert revealed == []
    answer = ask(server, "POST", "/api/reveal", headers=as_json, body=b"{}")
    assert (answer.status, answer.body, answer.headers["Cache-Control"]) == (204, b"", "no-store")
    assert revealed == [final]

    final.unlink()
    assert state_of(server)["state"] == "approved"
    assert_json(ask(server, "POST", "/api/reveal", headers=as_json, body=b"{}"), 404, {"error": SERVER_ERROR_TEXT})
    assert revealed == [final]
    assert_json(ask(server, "POST", "/api/reveal", cookie=None, headers=as_json, body=b"{}"), 403,
                {"error": DENIED_TEXT})


@pytest.mark.parametrize("platform, command", [
    ("darwin", ["open", "-R", str(Path("/projects/p/out/final.mp4"))]),
    ("win32", ["explorer", "/select," + str(Path("/projects/p/out/final.mp4"))]),
    ("linux", ["xdg-open", str(Path("/projects/p/out"))]),
])
def test_reveal_command_selects_the_file_or_opens_its_folder(platform, command):
    assert reveal_command(Path("/projects/p/out/final.mp4"), platform) == command


def test_reveal_in_folder_runs_the_command_of_this_platform_and_does_not_check_it(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(review_server.subprocess, "run", lambda *args, **kwargs: calls.append((args, kwargs)))
    final = tmp_path / "out" / "final.mp4"
    reveal_in_folder(final)
    assert calls == [((reveal_command(final, sys.platform),), {"check": False})]


def test_an_approval_after_stop_began_is_not_started(validated, serve_job):
    fake = FakeSteps()
    server = serve_job(fake)
    server.closed = True  # what stop() sets first
    assert_json(post_approve(server, edit_version(validated)), 403, {"error": DENIED_TEXT})
    assert fake.calls == []


def test_stop_during_a_job_waits_for_the_job(validated, serve_job):
    fake = FakeSteps(hold="render")
    server = serve_job(fake)
    assert post_approve(server, edit_version(validated)).status == 202
    assert fake.holding.wait(10)
    stopper = threading.Thread(target=server.stop)
    stopper.start()
    deadline = time.monotonic() + 10
    while True:  # wait until the port is closed: stop() is then past server_close()
        try:
            socket.create_connection(("127.0.0.1", server.port), timeout=1).close()
        except ConnectionRefusedError:
            break
        assert time.monotonic() < deadline
        time.sleep(0.05)
    stopper.join(timeout=0.3)
    assert stopper.is_alive()  # the job holds the change lock
    fake.release()
    stopper.join(timeout=10)
    assert not stopper.is_alive()
    assert [call[0] for call in fake.calls] == ["storyboard", "approve", "render"]


def test_the_server_stops_when_idle_and_not_while_a_job_runs(validated, serve, serve_job):
    fake = FakeSteps(hold="render")
    server = serve_job(fake, idle_s=0.3)
    assert post_approve(server, edit_version(validated)).status == 202
    assert fake.holding.wait(10)
    waiter, stopped = in_thread(server.wait)
    time.sleep(1.0)  # more than idle_s without a request: the job keeps the server up
    assert waiter.is_alive()
    fake.release()
    waiter.join(timeout=10)
    assert stopped == ["idle"]
    with pytest.raises(ConnectionRefusedError):
        ask(server, "GET", "/api/state")

    polled = serve(validated, key=KEY, idle_s=1.0)  # requests keep it up
    waiter, stopped = in_thread(polled.wait)
    deadline = time.monotonic() + 2.5
    while time.monotonic() < deadline:
        assert ask(polled, "GET", "/api/state").status == 200
        time.sleep(0.1)
    assert waiter.is_alive()
    waiter.join(timeout=10)
    assert stopped == ["idle"]


def test_the_idle_decision_is_taken_under_the_entry_lock_and_closes_the_server_first(validated, serve_job,
                                                                                     monkeypatch):
    """While a change or an approval holds the entry lock, the server does not decide it is idle. Once it has
    decided, before stop() closes the port, an approval is refused as after stop() began, and no job starts."""
    fake = FakeSteps()
    server = serve_job(fake, idle_s=0.2)
    answers = []
    decided = threading.Event()
    real_stop = server.stop

    def stop_after_an_approval() -> None:  # an approval that comes between the idle decision and stop()
        decided.set()
        answers.append(post_approve(server, edit_version(validated)))
        real_stop()

    monkeypatch.setattr(server, "stop", stop_after_an_approval)
    with server._entry_lock:  # as a change or an approval would hold it
        waiter, stopped = in_thread(server.wait)
        time.sleep(1.0)  # five times idle_s
        assert not decided.is_set() and not server.closed
    waiter.join(timeout=10)
    assert stopped == ["idle"]
    assert_json(answers[0], 403, {"error": DENIED_TEXT})
    assert fake.calls == []


# ---- the stills of the cards ----------------------------------------------------------------------------------


def with_a_scene(p: Project) -> Project:
    """`p` with a keyword-pop scene over its first kept sentence, validated again."""
    edit = load_edit(p.edit_path)
    first = edit["sentences"][0]["id"]
    edit["scenes"] = [{"id": "sc1", "card": "keyword-pop", "params": {"text": "省電"},
                       "at": {"s": first, "char": 0}, "until": {"s": first, "end": True}}]
    p.edit_path.write_text(json.dumps(edit, ensure_ascii=False), encoding="utf-8")
    validate_project(p)
    return p


def counting_stills(calls: list, pause_s: float = 0.0):
    """A stand-in for render_stills that notes how many stills it is asked for, waits `pause_s`, and draws them."""
    def stills(p, placed, style, run=None):
        calls.append(len(placed))
        time.sleep(pause_s)
        return stand_in_render_stills(p, placed, style)
    return stills


def test_data_draws_the_missing_stills_first_and_then_finds_them(validated, serve):
    p = with_a_scene(validated)
    calls: list[int] = []
    server = serve(p, key=KEY, stills=counting_stills(calls))
    (scene,) = ask(server, "GET", "/api/data").json()["scenes"]
    assert calls == [1] and scene["still"].startswith("scene-stills/")
    assert ask(server, "GET", "/api/data").json()["scenes"] == [scene]
    assert calls == [1]
    answer = ask(server, "GET", "/" + scene["still"])
    assert answer.status == 200 and answer.headers["Content-Type"] == "image/png" and answer.body == stand_in_png()
    assert ask(server, "GET", "/" + scene["still"], cookie=None).status == 403


def test_two_requests_at_once_draw_the_stills_once(validated, serve):
    p = with_a_scene(validated)
    calls: list[int] = []
    server = serve(p, key=KEY, stills=counting_stills(calls, pause_s=0.5))
    answers: list[Answer] = []
    threads = [threading.Thread(target=lambda: answers.append(ask(server, "GET", "/api/data"))) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert calls == [1]
    assert [answer.json()["scenes"][0]["still"] is not None for answer in answers] == [True, True]


def test_stills_that_cannot_be_made_are_logged_once_and_not_tried_again(validated, serve):
    p = with_a_scene(validated)
    calls: list[int] = []

    def failing(p, placed, style, run=None):
        calls.append(len(placed))
        raise PipelineError("node not found: install Node.js 18.18 or later")

    server = serve(p, key=KEY, stills=failing)
    for _ in range(2):
        answer = ask(server, "GET", "/api/data")
        assert answer.status == 200
        (scene,) = answer.json()["scenes"]
        assert scene["still"] is None and scene["label"] == "動畫：重點字　省電"
    assert calls == [1]
    assert log_text(p).count("card previews not made: node not found: install Node.js 18.18 or later") == 1


def test_only_a_still_name_of_64_lowercase_hex_digits_is_served(validated, serve):
    p = with_a_scene(validated)
    server = serve(p, key=KEY, stills=counting_stills([]))
    (scene,) = ask(server, "GET", "/api/data").json()["scenes"]
    name = scene["still"].removeprefix("scene-stills/").removesuffix(".png")
    (p.work / "scenes" / f"{name}.mov").write_bytes(b"a clip")
    (p.work / "scenes" / f"{name.upper()}.png").write_bytes(b"not ours")
    for path in (f"/scene-stills/{name}.mov", f"/scene-stills/{name.upper()}.png", f"/scene-stills/{name[:-1]}.png",
                 f"/scene-stills/{name}0.png", f"/scene-stills/{name}.png/", f"/scene-stills/x{name}.png",
                 f"/scene-stills/../scenes/{name}.png", f"/scene-stills/%2e%2e/{name}.png", "/scene-stills/",
                 f"/scene-stills/{'0' * 64}.png"):
        assert_json(ask(server, "GET", path), 404, {"error": SERVER_ERROR_TEXT}, path)
    assert ask(server, "GET", f"/scene-stills/{name}.png").status == 200


def with_two_scenes(p: Project, first: str, last: str) -> Project:
    """`p` with a keyword-pop scene over its first kept sentence and one over its last, validated again."""
    edit = load_edit(p.edit_path)
    ids = [sentence["id"] for sentence in edit["sentences"]]
    edit["scenes"] = [{"id": f"sc{n}", "card": "keyword-pop", "params": {"text": text},
                       "at": {"s": sid, "char": 0}, "until": {"s": sid, "end": True}}
                      for n, (sid, text) in enumerate(((ids[0], first), (ids[-1], last)), 1)]
    p.edit_path.write_text(json.dumps(edit, ensure_ascii=False), encoding="utf-8")
    validate_project(p)
    return p


NO_GLYPH = "the card font NotoSansTC-Bold.ttf has no glyph for: \U0001F600 (U+1F600)"


def test_a_still_that_cannot_be_made_does_not_keep_the_others_of_its_run_from_being_drawn(validated, serve):
    """render_stills refuses a whole run for one card it cannot draw: each still is then tried on its own, once."""
    p = with_two_scenes(validated, "省電", "亮度\U0001F600")
    calls: list[list[str]] = []

    def stills(p, placed, style, run=None):
        texts = [item.scene["params"]["text"] for item in placed]
        calls.append(texts)
        if any("\U0001F600" in text for text in texts):
            raise PipelineError(NO_GLYPH)
        return stand_in_render_stills(p, placed, style)

    server = serve(p, key=KEY, stills=stills)
    first, last = ask(server, "GET", "/api/data").json()["scenes"]
    assert first["still"] is not None and last["still"] is None
    assert ask(server, "GET", "/api/data").json()["scenes"] == [first, last]
    assert calls == [["省電", "亮度\U0001F600"], ["省電"], ["亮度\U0001F600"]]
    assert log_text(p).count(f"card previews not made: {NO_GLYPH}") == 1


def test_an_os_error_while_drawing_the_stills_is_logged_and_the_data_answered(validated, serve):
    p = with_a_scene(validated)

    def failing(p, placed, style, run=None):
        raise OSError(28, "No space left on device")

    server = serve(p, key=KEY, stills=failing)
    answer = ask(server, "GET", "/api/data")
    assert answer.status == 200 and answer.json()["scenes"][0]["still"] is None
    assert "card previews not made: [Errno 28] No space left on device" in log_text(p)


def test_serve_review_draws_the_stills_before_it_announces(validated):
    p = with_a_scene(validated)
    events: list[tuple] = []
    servers: list[ReviewServer] = []

    def stills(p, placed, style, run=None):
        events.append(("stills", len(placed)))
        return stand_in_render_stills(p, placed, style)

    def factory(project: Project) -> ReviewServer:
        servers.append(ReviewServer(project, key=KEY, stills=stills))
        return servers[-1]

    def open_browser(url: str) -> None:
        threading.Thread(target=servers[0].stop).start()

    serve_review(p, open_browser=open_browser, announce=lambda info: events.append(("announce", info["url"])),
                 server_factory=factory)
    assert events == [("stills", 1), ("announce", servers[0].url)]


# ---- serve_review -----------------------------------------------------------------------------


def _refusal(call) -> PipelineError:
    with pytest.raises(PipelineError) as refused:
        call()
    return refused.value


def test_serve_review_refuses_an_unvalidated_edit_like_the_storyboard(tmp_path):
    p = edited_project_from_script(tmp_path, SCRIPT, ASR)  # edit.json written, never validated

    def never(*args):
        raise AssertionError("serve_review went on after refusing")

    def serve_it():
        serve_review(p, open_browser=never, announce=never, server_factory=never)

    assert _refusal(serve_it).message == _refusal(lambda: build_storyboard(p)).message == NOT_VALIDATED_REFUSAL
    validate_project(p)
    trim_json = p.work / "trim.json"
    trim_json.write_text(json.dumps(json.loads(trim_json.read_text(encoding="utf-8"))), encoding="utf-8")
    assert _refusal(serve_it).message == _refusal(lambda: build_storyboard(p)).message == OLDER_FILES_REFUSAL
    edit = load_edit(p.edit_path)
    edit["cuts"]["accept"].append("t99")
    p.edit_path.write_text(json.dumps(edit, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(EditInvalid):
        serve_it()
    assert not review_video_path(p).exists()


def test_serve_review_makes_the_video_announces_then_opens_the_browser(validated):
    p = validated
    events: list[tuple] = []
    servers: list[ReviewServer] = []

    def factory(project: Project) -> ReviewServer:
        servers.append(ReviewServer(project, key=KEY))
        return servers[-1]

    def open_browser(url: str) -> None:
        events.append(("open", url))
        assert ask(servers[0], "GET", "/api/state").status == 200  # it serves by now
        threading.Thread(target=servers[0].stop).start()

    result = serve_review(p, open_browser=open_browser, announce=lambda info: events.append(("announce", info)),
                          server_factory=factory)
    server = servers[0]
    assert result == {"stopped": "stopped"}
    assert events == [("announce", {"url": server.url, "port": server.port, "video": str(review_video_path(p))}),
                      ("open", server.url)]
    assert review_video_path(p).is_file()
    with pytest.raises(ConnectionRefusedError):
        ask(server, "GET", "/api/state")


# ---- one real approval ------------------------------------------------------------------------


def test_one_real_approval_renders_the_project(meijia, serve, disk):
    """The real Steps on the short Meijia project, with real ffmpeg. The free space reads as ample (the refusal has
    a test of its own), so that the test does not depend on the machine."""
    p = meijia
    server = serve(p, key=KEY)
    version = edit_version(p)
    assert post_approve(server, version).status == 202
    final, preview = p.out / "final.mp4", p.out / "preview.mp4"
    assert settled(server, timeout=600) == {
        "version": version, "state": "done", "step": None, "error": None,
        "outputs": {"final": str(final), "preview": str(preview) if preview.exists() else None,
                    "folder": str(p.out)},
    }
    assert final.is_file() and final.stat().st_size > 0
    assert gate_ok(p, "gate1") and step_done(p, "render")
    assert (p.review / "gate1" / "index.html").is_file()
