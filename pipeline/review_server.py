"""The review server: ReviewServer(p).start() serves the review page of project `p` on 127.0.0.1, behind a random key.

The server listens on 127.0.0.1 only, on a port the system picks, and start() returns the URL to open,
http://127.0.0.1:<port>/?key=<key>. GET /?key=<key> sets the cookie review_key_<port> (HttpOnly, SameSite=Strict)
and sends the browser on to /; every other request needs that cookie, or it gets 403 with DENIED_TEXT. The cookie
is named after the port because a browser sends the cookies of a host to every port of it: two servers at once
would otherwise overwrite each other's key. Before anything else, a request whose Host header is not exactly
127.0.0.1:<port> gets 403, also when the standard library refuses it on its own (a method it has no handler for):
a page of another site that reaches the port through a host name of its own (DNS rebinding) gets nothing.

Only the fixed routes answer. The path of a request (the part before "?") is looked up as a whole string, and no
part of it is ever joined to a file path: the page files are those PAGE_FILES names in PAGE_DIR, the font is the
style's caption font, and the videos are work/review.mp4 and out/preview.mp4, sent with byte ranges (byte_range),
which a browser needs to seek in a video. Any other path is 404.

GET /api/data is review_data.page_data with the state of the page; GET /api/state is {"version", "state", "step",
"error", "outputs"} (ReviewServer.status). The state is "working" while the approval job runs ("step" is the step it is
on: "storyboard", "approve" or "render"); "done" when gate 1 is approved for the current storyboard, the render step is
done and out/final.mp4 is there ("outputs": the paths of final.mp4, of preview.mp4 or None, and of out/); "failed"
after the job failed ("error": FAILED_TEXT), until a change lands, another approval starts, or a render from the chat
completes ("done" outranks "failed"); "approved" when gate 1 is approved without the output being done (the user
approved in the chat, and Claude renders); else "editing". Neither route takes the change lock, so the page can poll
during a render.

POST /api/change hands {"version", "change"} to review_actions.apply_change, one call at a time: 200 {"version"}, 409
{"error", "conflict": true} when the version is old, 422 {"error"} when the change is refused. POST
/api/caption-lines with {"sentence", "text"} is review_actions.preview_caption: 200 {"lines"}, the lines the captions
of that sentence would show with that caption text, or 422 {"error"} as the change of that caption text would be
refused; it writes nothing and, like the GET routes, takes no lock and answers in any state. POST /api/approve with
{"version"} starts the approval job: 202 with the state of the job, 409 conflict when the version is old, 507 DISK_TEXT
when the disk of the project has less than assets.MIN_FREE_GB GiB free, 423 OUTPUT_BUSY_TEXT while a storyboard,
approve or render from the chat holds the project's lock (project.ProjectLock). The job runs Steps (storyboard,
approve gate1, render) in a thread that is not a daemon and holds the change lock and the project's lock until it
ends, so no change lands meanwhile, no command from the chat makes the same files, and stop() waits for it. The job
fails when the version (review_data.edit_version: edit.json, transcript.json and trim.json) is no longer the approved
one once the storyboard is made (Claude wrote edit.json from the chat meanwhile, or transcribe or trim ran again).
While the job runs, changes and approvals are 423 BUSY_TEXT; once the state is "approved" or "done", they are 423
APPROVED_TEXT. An approval decides and starts its job in one turn of the locks, so two approvals at once start one
job. POST /api/reveal with {} shows out/final.mp4 in the file manager (204), 404 when there is none. While edit.json
has not passed validate on the current files, data, changes and approvals are 409 with NOT_READY_TEXT; so is a
caption preview. A POST to /api/change, /api/caption-lines, /api/approve or /api/reveal whose Content-Type is not
application/json, whose Content-Length is missing or over MAX_BODY, or whose body is not the object above is 400. A
body over MAX_BODY is read and dropped up to DRAIN_CAP bytes, so that the browser gets the 400 and not a reset
connection; one declared longer than that is not read at all, and the connection closes after the 400. Once stop()
has begun, no change is applied and no job starts (403), and stop() returns only after a change being applied is
written and a job that runs has ended.

The server stops itself when no request with the key came for idle_s seconds and no job runs (the end of a job counts
as a request); wait() then returns "idle". It decides that where changes and approvals decide (under the entry lock)
and closes there, so a change or an approval after the decision is refused as after stop(). serve_review starts the
server for the review command.

The page shows the "error" of an answer, so that text is always Chinese: DENIED_TEXT, the texts of review_data and
review_actions, the texts of the approval, and SERVER_ERROR_TEXT for everything else (400, 404, and any error a route
did not expect, which is 500). The English of an error goes to work/review.log, with one line for each request
(method, path cut at "?", status); so does the English of a failed approval job. The log never holds a request line or
the query, so it never holds the key; nothing goes to stderr. Every answer carries Cache-Control: no-store. A
connection that sends or reads nothing for REQUEST_TIMEOUT_S ends.

The server writes only work/review.log, and through apply_change and the steps what they write. It deletes nothing of
its own (storyboard and render delete only what they always have, inside the project).
"""

import datetime
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import traceback
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote

from pipeline.assets import MIN_FREE_GB
from pipeline.edit import load_edit, require_valid
from pipeline.errors import PipelineError
from pipeline.project import Project, ProjectBusy, ProjectLock, approve_gate, edit_stage, gate_ok, step_done
from pipeline.render import render
from pipeline.review_actions import LOG_FILE, ChangeRefused, VersionConflict, apply_change, preview_caption
from pipeline.review_data import NotReady, edit_version, page_data
from pipeline.review_video import make_review_video, review_video_path
from pipeline.storyboard import NOT_VALIDATED_REFUSAL, OLDER_FILES_REFUSAL, build_storyboard
from pipeline.style import font_path, load_style

REVIEW_IDLE_S = 1800
PAGE_DIR = Path(__file__).parent / "review_page"
DENIED_TEXT = "審閱頁的程式已經停了，請回對話請 Claude 重新打開。"
SERVER_ERROR_TEXT = "審閱頁的程式出了問題，請回對話請 Claude 看看。"
DISK_TEXT = "磁碟可用空間不到 15 GB，請先清出空間再輸出。"
BUSY_TEXT = "正在輸出，不能改決定。"
OUTPUT_BUSY_TEXT = "這支影片正在別處輸出（可能是在和 Claude 的對話裡），等它完成再按同意。"  # the project lock is held
APPROVED_TEXT = "已經同意了。要再改，請回對話跟 Claude 說。"
FAILED_TEXT = "輸出失敗。請回對話請 Claude 看記錄檔。"
JOB_STEPS = ("storyboard", "approve", "render")  # the steps of the approval job, in order
SETTLED = ("approved", "done")  # the states in which the page changes nothing more
HOST = "127.0.0.1"
COOKIE_PREFIX = "review_key_"  # the cookie of the server on port N is review_key_N
KEY_BYTES = 32  # secrets.token_urlsafe of these many random bytes: 43 characters
MAX_BODY = 65536  # the most bytes a POST body may have
DRAIN_CAP = 4 * 1024 * 1024  # a longer body is read and dropped up to this many bytes; one declared longer is not
CHUNK = 1 << 16  # files and drained bodies move in pieces of this many bytes
REQUEST_TIMEOUT_S = 60  # a connection that sends or reads nothing for this long ends
JSON_TYPE = "application/json; charset=utf-8"
PAGE_FILES = {  # route -> (the file in PAGE_DIR, its content type)
    "/": ("index.html", "text/html; charset=utf-8"),
    "/review.js": ("review.js", "text/javascript; charset=utf-8"),
    "/logic.js": ("logic.js", "text/javascript; charset=utf-8"),
    "/review.css": ("review.css", "text/css; charset=utf-8"),
}
FONT_ROUTE = "/font/caption.ttf"
MEDIA_ROUTES = {  # route -> the video of a project it sends
    "/media/review.mp4": review_video_path,
    "/media/preview.mp4": lambda p: p.out / "preview.mp4",
}
_RANGE = re.compile(r"bytes=([0-9]{0,20})-([0-9]{0,20})", re.IGNORECASE)
_LENGTH = re.compile(r"[0-9]{1,20}")


class RangeNotSatisfiable(Exception):
    """A Range header that asks for bytes the file does not have: 416."""


class BadRequest(Exception):
    """A POST the server does not read: 400. The message is English, for work/review.log."""


class Refused(Exception):
    """A change or an approval the server turns down with `status` and the Chinese `text`: 403 once stop() began,
    423 while the state does not allow it, 507 without the free space a render needs."""

    def __init__(self, status: int, text: str):
        super().__init__(text)
        self.status, self.text = status, text


@dataclass
class Steps:
    """What the approval job runs, in this order: storyboard(p), approve(p, "gate1"), render(p)."""

    storyboard: Callable = build_storyboard
    approve: Callable = approve_gate
    render: Callable = render


def reveal_command(path: Path, platform: str) -> list[str]:
    """The command that shows the file at `path` in the file manager of `platform` (sys.platform): selected in its
    folder where the file manager can do that, else its folder opened."""
    if platform == "darwin":
        return ["open", "-R", str(path)]
    if platform == "win32":
        return ["explorer", f"/select,{path}"]
    return ["xdg-open", str(path.parent)]


def reveal_in_folder(path: Path) -> None:
    """Show the file at `path` in the file manager of this machine. What the command says is not checked."""
    subprocess.run(reveal_command(path, sys.platform), check=False)


def free_gib(p: Project) -> float:
    """The free space on the disk of the project folder, in GiB."""
    return shutil.disk_usage(p.dir).free / 2**30


# ---- reading a request ------------------------------------------------------------------------


def byte_range(header: str | None, size: int) -> tuple[int, int] | None:
    """The bytes a Range header asks of a file of `size` bytes: (first, last), both included. One range of the
    forms bytes=a-b, bytes=a- and bytes=-n is read; an end past the file stops at its last byte, and a suffix longer
    than the file is all of it. A start at or past the size, or a suffix of 0 bytes, raises RangeNotSatisfiable. No
    header, or one in any other form (several ranges, another unit, an end before the start), gives None: the whole
    file is sent, as a server may do with a Range it does not read."""
    match = _RANGE.fullmatch(header.strip()) if header is not None else None
    if match is None or match.groups() == ("", ""):
        return None
    start, end = match.groups()
    if start == "":
        suffix = int(end)
        if suffix == 0 or size == 0:
            raise RangeNotSatisfiable(header)
        return max(0, size - suffix), size - 1
    first = int(start)
    if end != "" and int(end) < first:
        return None
    if first >= size:
        raise RangeNotSatisfiable(header)
    return first, size - 1 if end == "" else min(int(end), size - 1)


def cookie_values(headers: list[str], name: str) -> list[str]:
    """The values of the cookie `name` in the Cookie headers `headers`, in order."""
    values = []
    for header in headers:
        for pair in header.split(";"):
            key, equals, value = pair.partition("=")
            if equals and key.strip() == name:
                values.append(value.strip())
    return values


def content_length(value: str | None) -> int:
    """The Content-Length header `value` as a number; a missing or unreadable one raises BadRequest."""
    if value is None or not _LENGTH.fullmatch(value.strip()):
        raise BadRequest(f"Content-Length is {value!r}, not a number of bytes")
    return int(value)


def _json_body(content_type: str | None, body: bytes) -> object:
    """The JSON document of a POST body. Raises BadRequest unless the media type of `content_type` (before any ";",
    in any case) is application/json and `body` is JSON."""
    media_type = (content_type or "").split(";", 1)[0].strip().lower()
    if media_type != "application/json":
        raise BadRequest(f"Content-Type is {content_type!r}, not application/json")
    try:
        return json.loads(body)
    except (ValueError, RecursionError) as error:
        raise BadRequest(f"the body is not JSON: {type(error).__name__}: {error}") from None


def change_request(content_type: str | None, body: bytes) -> tuple[str, dict]:
    """(version, change) of the body of POST /api/change. Raises BadRequest unless the media type of
    `content_type` (before any ";", in any case) is application/json and `body` is a JSON object with a string
    "version" and an object "change"."""
    document = _json_body(content_type, body)
    if not (isinstance(document, dict) and isinstance(document.get("version"), str)
            and isinstance(document.get("change"), dict)):
        raise BadRequest('the body is not {"version": string, "change": object}')
    return document["version"], document["change"]


def caption_request(content_type: str | None, body: bytes) -> tuple[str, str]:
    """(sentence, text) of the body of POST /api/caption-lines, read as change_request reads its body: a JSON object
    with a string "sentence" and a string "text"."""
    document = _json_body(content_type, body)
    if not (isinstance(document, dict) and isinstance(document.get("sentence"), str)
            and isinstance(document.get("text"), str)):
        raise BadRequest('the body is not {"sentence": string, "text": string}')
    return document["sentence"], document["text"]


def approve_request(content_type: str | None, body: bytes) -> str:
    """The version of the body of POST /api/approve, read as change_request reads its body: a JSON object with a
    string "version"."""
    document = _json_body(content_type, body)
    if not (isinstance(document, dict) and isinstance(document.get("version"), str)):
        raise BadRequest('the body is not {"version": string}')
    return document["version"]


def reveal_request(content_type: str | None, body: bytes) -> None:
    """Check the body of POST /api/reveal, read as change_request reads its body: a JSON object (the page sends
    {}). A page of another port of 127.0.0.1 is same-site for the cookie; the no-cors POST it can send has no JSON
    Content-Type, so it is refused here."""
    if not isinstance(_json_body(content_type, body), dict):
        raise BadRequest("the body is not a JSON object")


def _english(error: BaseException) -> str:
    """`error` as work/review.log says it: a PipelineError's message and extra fields, the traceback of any other."""
    if isinstance(error, PipelineError):
        return error.message + (f" {error.extra!r}" if error.extra else "")
    return "".join(traceback.format_exception(error)).rstrip()


def _same(given: str, key: str) -> bool:
    """Whether `given` is `key`, compared in constant time."""
    return hmac.compare_digest(given.encode("utf-8", "surrogatepass"), key.encode("utf-8", "surrogatepass"))


def _caption_font(p: Project) -> Path:
    """The font the captions use: the bold or regular font of edit.json's style, as its captions say."""
    style = load_style(load_edit(p.edit_path)["style"])
    return font_path(style, "bold" if style["captions"]["bold"] else "regular")


# ---- the handler ------------------------------------------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    """One request (HTTP/1.0: one request a connection). self.server.review is the ReviewServer."""

    timeout = REQUEST_TIMEOUT_S  # the socket timeout: a connection that sends or reads nothing ends

    def do_GET(self) -> None:
        self._answer(self._get)

    def do_POST(self) -> None:
        self._answer(self._post)

    # -- the checks every request goes through

    def _host_is_ours(self) -> bool:
        return self.headers.get_all("Host") == [f"{HOST}:{self.server.review.port}"]

    def _answer(self, route) -> None:
        review: ReviewServer = self.server.review
        self._answered = False
        path, _, query = self.path.partition("?")
        try:
            if not self._host_is_ours():
                return self._json(403, {"error": DENIED_TEXT})
            keys = parse_qs(query, keep_blank_values=True).get("key") if (self.command, path) == ("GET", "/") else None
            if keys is not None:  # GET /?key=<key>: the cookie, and on to the page
                if len(keys) != 1 or not _same(keys[0], review.key):
                    return self._json(403, {"error": DENIED_TEXT})
                review.touch()
                cookie = f"{review.cookie_name}={review.key}; HttpOnly; SameSite=Strict; Path=/"
                return self._empty(303, [("Location", "/"), ("Set-Cookie", cookie)])
            cookies = cookie_values(self.headers.get_all("Cookie") or [], review.cookie_name)
            if not any(_same(value, review.key) for value in cookies):
                return self._json(403, {"error": DENIED_TEXT})
            review.touch()
            route(review, path)
        except BadRequest as error:
            review.log(f"refused {self.command} {path}: {error}")
            self._json(400, {"error": SERVER_ERROR_TEXT})
        except NotReady as error:
            if error.__cause__ is not None:  # what require_valid said
                review.log(f"not ready: {_english(error.__cause__)}")
            self._json(409, {"error": error.message})
        except VersionConflict as conflict:
            self._json(409, {"error": conflict.message, "conflict": True})
        except ChangeRefused as refused:  # apply_change logged the reasons
            self._json(422, {"error": refused.message})
        except Refused as refused:
            self._json(refused.status, {"error": refused.text})
        except (ConnectionError, TimeoutError):
            self.close_connection = True  # the browser went away or stopped reading (it does with a video)
        except Exception as error:
            review.log(f"error in {self.command} {path}: {_english(error)}")
            if self._answered:  # too late for another answer: the connection ends
                self.close_connection = True
            else:
                self._json(500, {"error": SERVER_ERROR_TEXT})

    # -- the routes

    def _get(self, review: "ReviewServer", path: str) -> None:
        p = review.p
        if path in PAGE_FILES:
            name, kind = PAGE_FILES[path]
            self._file(PAGE_DIR / name, kind)
        elif path == FONT_ROUTE:
            self._file(_caption_font(p), "font/ttf")
        elif path in MEDIA_ROUTES:
            self._file(MEDIA_ROUTES[path](p), "video/mp4", ranges=True)
        elif path == "/api/data":
            self._json(200, {**page_data(p), "state": review.state()["state"]})
        elif path == "/api/state":
            self._json(200, review.status())
        else:
            self._not_found()

    def _post(self, review: "ReviewServer", path: str) -> None:
        content_type = self.headers.get("Content-Type")
        if path == "/api/change":
            version, change = change_request(content_type, self._body())
            self._json(200, review.change(version, change))
        elif path == "/api/caption-lines":  # reads only: no lock, so it answers while a change is applied
            sentence, text = caption_request(content_type, self._body())
            self._json(200, {"lines": preview_caption(review.p, sentence, text)})
        elif path == "/api/approve":
            self._json(202, review.approve(approve_request(content_type, self._body())))
        elif path == "/api/reveal":
            reveal_request(content_type, self._body())
            final = review.p.out / "final.mp4"
            if not final.is_file():
                return self._not_found()
            review.reveal(final)
            self._headers(204, [])  # no Content-Length: a 204 has no body
        else:
            self._not_found()

    def _body(self) -> bytes:
        """The body of the request, read whole. One longer than MAX_BODY raises BadRequest, as a missing or
        unreadable length does. Up to DRAIN_CAP it is read and dropped first, so that the browser gets the answer
        and not a reset connection; one declared longer is not read, and the connection closes after the answer."""
        length = content_length(self.headers.get("Content-Length"))
        if length > DRAIN_CAP:
            self.close_connection = True
            raise BadRequest(f"the body is declared {length} bytes, more than {DRAIN_CAP}: not read")
        if length > MAX_BODY:
            while length > 0 and (piece := self.rfile.read(min(CHUNK, length))):
                length -= len(piece)
            raise BadRequest(f"the body is longer than {MAX_BODY} bytes")
        return self.rfile.read(length)

    # -- answers

    def end_headers(self) -> None:
        self._answered = True
        super().end_headers()

    def _headers(self, status: int, headers: list[tuple[str, str]]) -> None:
        self.send_response(status)
        for name, value in [*headers, ("Cache-Control", "no-store")]:
            self.send_header(name, value)
        self.end_headers()

    def _json(self, status: int, document: dict) -> None:
        body = json.dumps(document, ensure_ascii=False).encode("utf-8")
        self._headers(status, [("Content-Type", JSON_TYPE), ("Content-Length", str(len(body)))])
        if self.command != "HEAD":  # the answer to HEAD is the headers alone
            self.wfile.write(body)

    def _empty(self, status: int, headers: list[tuple[str, str]]) -> None:
        self._headers(status, [*headers, ("Content-Length", "0")])

    def _not_found(self) -> None:
        self._json(404, {"error": SERVER_ERROR_TEXT})

    def _file(self, path: Path, kind: str, ranges: bool = False) -> None:
        """Send the file at `path` (404 when there is none); with `ranges`, the bytes its Range header asks for."""
        try:
            handle = open(path, "rb")
        except FileNotFoundError:
            return self._not_found()
        with handle:
            size = os.fstat(handle.fileno()).st_size
            first, last, status = 0, size - 1, 200
            headers = [("Content-Type", kind)]
            if ranges:
                try:
                    span = byte_range(self.headers.get("Range"), size)
                except RangeNotSatisfiable:
                    return self._empty(416, [("Accept-Ranges", "bytes"), ("Content-Range", f"bytes */{size}")])
                headers.append(("Accept-Ranges", "bytes"))
                if span is not None:
                    (first, last), status = span, 206
                    headers.append(("Content-Range", f"bytes {first}-{last}/{size}"))
            self._headers(status, [*headers, ("Content-Length", str(last - first + 1))])
            handle.seek(first)
            left = last - first + 1
            while left > 0 and (piece := handle.read(min(CHUNK, left))):
                self.wfile.write(piece)
                left -= len(piece)

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        """What the standard library refuses on its own (a request line it cannot read, a method with no do_
        method such as HEAD, PUT or OPTIONS) is answered like any other refusal: 403 with DENIED_TEXT when the
        headers were read and the Host is not ours, else `code` with SERVER_ERROR_TEXT. `message` is not logged: it
        may hold the request line, and so the key. The log keeps what log_request writes (method, path, code)."""
        self.close_connection = True
        if getattr(self, "headers", None) is not None and not self._host_is_ours():
            return self._json(403, {"error": DENIED_TEXT})
        self._json(code, {"error": SERVER_ERROR_TEXT})

    # -- the log: work/review.log, never stderr, never a request line or the query (it holds the key)

    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        code = code.value if isinstance(code, HTTPStatus) else code
        path = (getattr(self, "path", None) or "").partition("?")[0]  # a request line that could not be read has none
        self.server.review.log(f"{self.command or '-'} {path or '-'} {code}")

    def log_message(self, format: str, *args) -> None:
        """The standard library's own lines. With send_error and log_request replaced, the one left is a request
        that timed out ("Request timed out: TimeoutError(...)"), which holds no request text."""
        self.server.review.log(format % args)


class _HTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer(("127.0.0.1", 0), _Handler), with daemon threads and its errors in the log."""

    daemon_threads = True

    def __init__(self, review: "ReviewServer"):
        self.review = review
        super().__init__((HOST, 0), _Handler)

    def handle_error(self, request, client_address) -> None:
        self.review.log(f"error in a request: {traceback.format_exc().rstrip()}")


# ---- the server -------------------------------------------------------------------------------


def _state(state: str, *, step: str | None = None, error: str | None = None, outputs: dict | None = None) -> dict:
    """A state of the page as GET /api/state gives it, after the version."""
    return {"state": state, "step": step, "error": error, "outputs": outputs}


class ReviewServer:
    """The review page of project `p` (see the module text). `key` is the key of the URL (a random one when not
    given). `idle_s` is the seconds without a request after which the server stops itself (see wait). `steps` is
    what an approval runs (the real storyboard, approve and render when not given); `reveal(path)` shows a file in
    the file manager.

    The locks: change_lock is held by a change while it is applied, by the approval job from its start to its end
    (approve() takes it, the job's thread releases it) and by stop() for a moment. A change and an approval take
    _entry_lock first and check under it that no job runs; a job starts only under _entry_lock, so neither ever
    waits for a job, and two approvals cannot both start one. The GET routes take neither lock. stop() takes
    change_lock, so it is never called with that lock held; the job never calls it. The project's lock
    (project.ProjectLock, shared with the storyboard, approve and render commands) is taken by approve() last, without
    waiting, and let go by the job at its end, before it shows as ended."""

    def __init__(self, p: Project, *, key: str | None = None, idle_s: float = REVIEW_IDLE_S,
                 steps: Steps | None = None, reveal: Callable[[Path], None] = reveal_in_folder):
        self.p = p
        self.key = secrets.token_urlsafe(KEY_BYTES) if key is None else key
        self.idle_s = idle_s
        self.steps = Steps() if steps is None else steps
        self.reveal = reveal
        self.port = 0
        self.url = ""
        self.change_lock = threading.Lock()  # one change at a time, and none while the approval job runs
        self.closed = False  # set by stop(): no change is applied and no job starts after it
        self._entry_lock = threading.Lock()  # taken before change_lock by a change and an approval (see above)
        self._job_step: str | None = None  # the step the approval job is on; None while no job runs
        self._failed = False  # the last job failed, and no change has landed since
        self._last_active = time.monotonic()  # when the last request with the key came, or the last job ended
        self._log_lock = threading.Lock()
        self._httpd: _HTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._stopped = threading.Event()

    def start(self) -> str:
        """Start serving, in a thread of its own, and return the URL to open."""
        self._httpd = _HTTPServer(self)
        self.port = self._httpd.server_address[1]
        self.url = f"http://{HOST}:{self.port}/?key={quote(self.key, safe='')}"
        self._thread = threading.Thread(target=self._httpd.serve_forever, name="review-server", daemon=True)
        self._thread.start()
        return self.url

    @property
    def cookie_name(self) -> str:
        """The name of the cookie that holds the key: review_key_<port>."""
        return f"{COOKIE_PREFIX}{self.port}"

    def touch(self) -> None:
        """A request with the key came: the server is not idle."""
        self._last_active = time.monotonic()

    def wait(self) -> str:
        """Block until the server stops. Returns "stopped" once stop() was called, and "idle" once the server stopped
        itself because no request with the key came for idle_s seconds while no job ran. The end of a job counts as
        a request, so that a page opened again has idle_s to show the outcome."""
        while True:
            quiet = time.monotonic() - self._last_active
            if self._stopped.wait(self.idle_s if self._job_step is not None else max(self.idle_s - quiet, 0)):
                return "stopped"
            if self._idle() and self._close_if_idle():
                self.stop()  # not under a lock: stop() takes change_lock
                return "idle"

    def _idle(self) -> bool:
        return self._job_step is None and time.monotonic() - self._last_active >= self.idle_s

    def _close_if_idle(self) -> bool:
        """Decide under _entry_lock, where changes and approvals decide, that the server is idle, and close it there:
        a change or an approval that comes after the decision is refused as after stop() began. False when a request
        came or a job started meanwhile."""
        with self._entry_lock:
            if not self._idle():
                return False
            self.closed = True
            return True

    def stop(self) -> None:
        """Stop serving and close the port. From the moment stop() begins no change is applied and no approval job
        starts, and it returns only once a change that was being applied is written and a job that runs has ended.
        Stopping a server that has stopped does nothing."""
        self.closed = True
        httpd, self._httpd = self._httpd, None
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()
            self._thread.join()
        with self.change_lock:  # a change being applied, or the job, holds it until it is done
            pass
        self._stopped.set()

    # -- the state

    def state(self) -> dict:
        """{"state", "step", "error", "outputs"} of the page (see the module text). Takes no lock."""
        step = self._job_step
        if step is not None:
            return _state("working", step=step)
        approved = gate_ok(self.p, "gate1")
        final = self.p.out / "final.mp4"
        if approved and step_done(self.p, "render") and final.is_file():
            preview = self.p.out / "preview.mp4"
            return _state("done", outputs={"final": str(final), "preview": str(preview) if preview.is_file() else None,
                                           "folder": str(self.p.out)})
        if self._failed:  # read after _job_step: a job that ends sets it first
            return _state("failed", error=FAILED_TEXT)
        return _state("approved" if approved else "editing")

    def status(self) -> dict:
        """GET /api/state: the version (review_data.edit_version), then the state."""
        return {"version": edit_version(self.p), **self.state()}

    # -- changes and the approval

    def _refuse_closed_or_busy(self) -> None:
        if self.closed:
            raise Refused(403, DENIED_TEXT)
        if self._job_step is not None:
            raise Refused(423, BUSY_TEXT)

    def _refuse_settled(self) -> None:
        if self.state()["state"] in SETTLED:
            raise Refused(423, APPROVED_TEXT)

    def change(self, version: str, change: dict) -> dict:
        """Apply a change the page sent (review_actions.apply_change, whose refusals pass through) and return
        {"version"}. Refused once stop() began, while the approval job runs, and once the state is approved or done.
        A change that lands ends a failed state."""
        with self._entry_lock:
            self._refuse_closed_or_busy()
            with self.change_lock:  # no job runs, so only another change or stop() holds it, for a moment
                self._refuse_closed_or_busy()  # stop() sets closed, then waits for change_lock: for this change
                self._refuse_settled()
                result = apply_change(self.p, version, change)
                self._failed = False
                return result

    def approve(self, version: str) -> dict:
        """Start the approval job for the edit the page saw as `version`, and return its state as it starts (202).
        Refused once stop() began, while a job runs, while edit.json has not passed validate (NotReady), when
        `version` is old (VersionConflict), once the state is approved or done, when the disk of the project has
        less than MIN_FREE_GB GiB free, and while a storyboard, approve or render from the chat holds the project's
        lock (423 OUTPUT_BUSY_TEXT; ProjectLock, taken here without waiting and let go by the job at its end). The
        checks and the start are one turn of the locks: no change lands between them, and a second approval finds
        the job running."""
        with self._entry_lock:
            self._refuse_closed_or_busy()
            self.change_lock.acquire()  # no job runs, so only a change or stop() holds it, for a moment
            try:
                self._refuse_closed_or_busy()
                if edit_stage(self.p) is not None:
                    raise NotReady()
                if version != edit_version(self.p):
                    raise VersionConflict()
                self._refuse_settled()
                if free_gib(self.p) < MIN_FREE_GB:
                    raise Refused(507, DISK_TEXT)
                project_lock = ProjectLock(self.p)
                try:
                    project_lock.acquire()
                except ProjectBusy:
                    raise Refused(423, OUTPUT_BUSY_TEXT) from None
                try:
                    return self._start_job(version, project_lock)
                except BaseException:
                    project_lock.release()
                    raise
            except BaseException:
                self.change_lock.release()
                raise

    def _start_job(self, version: str, project_lock: ProjectLock) -> dict:
        """Start the approval job in a thread that is not a daemon, so that a stopped server still finishes its render
        before the process ends. The caller holds change_lock and `project_lock`; the job releases both at its end.
        Nothing raises once the thread has started (the caller would release the locks the job holds)."""
        started = {"version": version, **_state("working", step=JOB_STEPS[0])}
        self._job_step = JOB_STEPS[0]
        try:
            threading.Thread(target=self._run_job, args=(version, project_lock), name="review-approval",
                             daemon=False).start()
        except BaseException:
            self._job_step = None
            raise
        return started

    def _run_job(self, version: str, project_lock: ProjectLock) -> None:
        """The approval job for the edit the page saw as `version`: storyboard, approve gate1 and render, in order,
        holding change_lock and `project_lock` to the end, however it ends. A step that raises ends the job as
        failed; the English of the error goes to the log. So does a version that is no longer `version` once the
        storyboard is made (Claude wrote edit.json from the chat meanwhile, or transcribe or trim ran again): the user
        did not see that storyboard, so it is not approved. After that, approve_gate approves only a storyboard of the
        current files, and render renders only an approved one."""
        p, steps = self.p, self.steps
        calls = ((steps.storyboard, (p,)), (steps.approve, (p, "gate1")), (steps.render, (p,)))
        failed = False
        try:
            self.log("approval: storyboard, approve and render begin")
            for step, (run, args) in zip(JOB_STEPS, calls):
                self._job_step = step
                run(*args)
                if step == "storyboard" and edit_version(p) != version:
                    raise PipelineError("the edit changed after the page approved it (edit.json, transcript.json or "
                                        "trim.json): the storyboard is not the one the user saw",
                                        approved=version, now=edit_version(p))
            self.log("approval: done")
        except Exception as error:
            failed = True
            detail = _english(error)
            if isinstance(error, PipelineError):  # the traceback of any other names its type
                detail = f"{type(error).__name__}: {detail}"
            self.log(f"the approval failed in {self._job_step}: {detail}")
        finally:
            self._failed = failed  # before _job_step: a poll that sees no job sees the outcome
            self._last_active = time.monotonic()  # the end of a job counts as a request (see wait)
            with suppress(OSError):  # before _job_step: an approval that sees no job finds the project free
                project_lock.release()
            self._job_step = None
            self.change_lock.release()

    def log(self, text: str) -> None:
        """Add `text` to work/review.log, after the time. A log that cannot be written is not reported: it must not
        turn an answer into an error."""
        stamp = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
        with self._log_lock, suppress(OSError), open(self.p.work / LOG_FILE, "a", encoding="utf-8") as log:
            log.write(f"{stamp} {text}\n")


# ---- the review command -----------------------------------------------------------------------


def serve_review(p: Project, *, open_browser: Callable[[str], object], announce: Callable[[dict], None],
                 server_factory: Callable[[Project], ReviewServer] = ReviewServer) -> dict:
    """Serve the review page of `p` until the server stops, and return {"stopped": "idle" or "stopped"} (what wait
    says). Refuses as the storyboard does, before anything is made: an edit.json that does not pass (EditInvalid), and
    one that validate has not passed on the files as they are now (OLDER_FILES_REFUSAL, NOT_VALIDATED_REFUSAL). Then
    it makes the review video, starts server_factory(p), calls announce({"url", "port", "video"}) and then
    open_browser(url). The server is stopped however this ends."""
    require_valid(p)
    stage = edit_stage(p)
    if stage == "edit":
        raise PipelineError(OLDER_FILES_REFUSAL)
    if stage == "validate":
        raise PipelineError(NOT_VALIDATED_REFUSAL)
    video = make_review_video(p)["video"]
    server = server_factory(p)
    try:
        url = server.start()
        announce({"url": url, "port": server.port, "video": video})
        open_browser(url)
        return {"stopped": server.wait()}
    finally:
        server.stop()
