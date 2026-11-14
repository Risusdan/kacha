"""Reading a request of the review server (pipeline.review_server): the Range header of a video, the Cookie headers,
and the Content-Length and the JSON body of a POST.

Every function here only reads what it is given (header values, bytes) and never touches a socket, a file or the
project, so the server's handler stays the one place that answers. byte_range reads the Range header of a video,
cookie_values the cookies of the Cookie headers, content_length the Content-Length of a POST. change_request,
caption_request, approve_request and reveal_request each read the body of one POST route (/api/change,
/api/caption-lines, /api/approve, /api/reveal): the media type must be application/json and the body the JSON object
that route takes, or BadRequest is raised. BadRequest is the server's 400 and RangeNotSatisfiable its 416; the message
of BadRequest is English, for work/review.log, and is never shown on the page. The most bytes a body may have
(MAX_BODY, DRAIN_CAP) are applied where the server reads the body, not here.
"""

import json
import re

_RANGE = re.compile(r"bytes=([0-9]{0,20})-([0-9]{0,20})", re.IGNORECASE)
_LENGTH = re.compile(r"[0-9]{1,20}")


class RangeNotSatisfiable(Exception):
    """A Range header that asks for bytes the file does not have: 416."""


class BadRequest(Exception):
    """A POST the server does not read: 400. The message is English, for work/review.log."""


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
