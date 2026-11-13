"""Splitting the speaker's script into sentences and the notes that are not spoken.

A sentence ends at 。！？ or ! ? (with any closing quote right after the mark) and at every
line break. Heading, quote, bullet and list-number marks at the start of a line are dropped, and
so are the Markdown marks inside a line: a link [text](url) keeps its text, and the paired
emphasis marks (**x**, __x__, ***x***, ~~x~~, `x`, *x*) keep what they wrap (see `markdown_marks`).
Notes in [...] or 【...】, and lines made only of （...） or (...), are not spoken: they become
Markers hung on the sentence that comes next. Pure text work, no I/O.
"""

import re
from dataclasses import dataclass

from pipeline.textnorm import Unit, split_units


@dataclass
class Sentence:
    id: str
    text: str
    units: list[Unit]  # spans point into `text`


@dataclass
class Marker:
    before: str | None  # id of the sentence it precedes; None when no sentence follows
    text: str  # as written, brackets included


_NEWLINE = re.compile(r"\r\n|\r|\n")
_HEADING = re.compile(r"^\s*#{1,6}(?:\s+|$)")
# Quote, bullet and list-number marks at the start of a line are not spoken: one or more ">",
# then one of "-", "*", "+" before whitespace, then an ordinal of 1 to 3 ASCII digits before
# ". " / ") " (or the end of the line) or "、". A "、" that is followed by another number
# ("1、2、3 都是", "3、4 號") is part of the text. Ordinals such as 一、 and lines like "3 個方法"
# or "2026年" are kept.
_LIST_MARKS = re.compile(r"^\s*(?:>\s*)*(?:[-*+]\s+)?(?:[0-9]{1,3}(?:[.)](?:\s+|$)|、(?!\s*\d)\s*))?")
_NOTE = re.compile(r"\[[^\[\]]*\]|【[^【】]*】")
# [text](target): the target has no space and no parenthesis, except one level inside it.
_LINK = re.compile(r"\[(?P<text>[^\[\]]*)\](?:\((?:[^()\s]|\([^()\s]*\))+\))")
# `x`: a code span whose first and last character are not a space.
_CODE = re.compile(r"`(?=[^`\s])[^`]*(?<=[^`\s])`")
# A pair of ***, **, __ or ~~ around text that does not start or end with a space, and a pair of single
# stars around such text when the character before the first and the one after the second are not
# ASCII letters or digits (so 5*3*2 is not emphasis).
_EMPHASIS = re.compile(
    r"(?P<mark>\*\*\*|\*\*|__|~~)(?=\S).+?(?<=\S)(?P=mark)"
    r"|(?<![0-9A-Za-z])\*(?=[^\s*])[^*]+?(?<=\S)\*(?![0-9A-Za-z])"
)
_SKIPPED = "\0"  # stands in for a character the emphasis rule must not look at
_END_MARKS = "。！？!?"
_CLOSING_QUOTES = "」』\"”"
_OPENING_PARENS = "（("
_CLOSING_PARENS = "）)"


def markdown_marks(text: str) -> list[tuple[int, int]]:
    """The half-open ranges of `text`, in order and apart, that Markdown syntax adds and nobody says:
    for a link [text](url) the "[" and the "](url)"; for a pair of emphasis marks (see _EMPHASIS) and
    for a code span the two marks. This is the one rule for them: parse_script removes the ranges
    (`strip_markdown`) and the captions skip them in a sentence text that still has them.

    Not touched: a lone mark (a single *, 5*3*2, file_name, 3~5) and a [...] or 【...】 note (it is
    kept as written, marks inside it too). The inside of a code span is not marked in this call, but
    `strip_markdown` goes on until no range is left, so once the span's marks are gone a pair of
    emphasis marks inside it (`__init__`) is seen on the next pass and goes too."""
    found: list[tuple[int, int]] = []
    seen = list(text)  # `text` with what emphasis marks must not pair through hidden
    for m in re.finditer(f"{_LINK.pattern}|{_NOTE.pattern}|{_CODE.pattern}", text):
        if m.group("text") is not None:  # a link: only its brackets and target go
            parts = [(m.start(), m.start("text")), (m.end("text"), m.end())]
            found += parts
            for lo, hi in parts:
                seen[lo:hi] = _SKIPPED * (hi - lo)
        elif m.group().startswith("`"):  # a code span: the two marks go, what is inside stays as it is
            found += [(m.start(), m.start() + 1), (m.end() - 1, m.end())]
            seen[m.start():m.end()] = _SKIPPED * len(m.group())
        else:  # a note
            seen[m.start():m.end()] = _SKIPPED * len(m.group())
    for m in _EMPHASIS.finditer("".join(seen)):
        size = len(m.group("mark") or "*")
        found += [(m.start(), m.start() + size), (m.end() - size, m.end())]
    return sorted(found)


def strip_markdown(text: str) -> str:
    """`text` without the ranges of `markdown_marks`. Removing the inner pair of *a **b** c* is what
    lets the outer one be seen, so it goes on until no range is left."""
    while ranges := markdown_marks(text):
        kept, at = [], 0
        for lo, hi in ranges:
            kept.append(text[at:lo])
            at = hi
        kept.append(text[at:])
        text = "".join(kept)
    return text


def _is_parenthesis_line(line: str) -> bool:
    """Whether the stripped `line` is one （...） or (...) group and nothing else."""
    if not line or line[0] not in _OPENING_PARENS:
        return False
    depth = 0
    for i, c in enumerate(line):
        if c in _OPENING_PARENS:
            depth += 1
        elif c in _CLOSING_PARENS:
            depth -= 1
            if depth == 0:
                return i == len(line) - 1
    return False


def parse_script(text: str) -> tuple[list[Sentence], list[Marker]]:
    sentences: list[Sentence] = []
    markers: list[Marker] = []
    waiting: list[str] = []  # notes read since the last sentence was closed

    def close(buf: list[str]) -> None:
        sentence_text = "".join(buf).strip()
        buf.clear()
        units = split_units(sentence_text)
        if not units:  # nothing to say: blank, or only punctuation
            return
        sid = f"s{len(sentences) + 1:02d}"
        sentences.append(Sentence(sid, sentence_text, units))
        markers.extend(Marker(sid, note) for note in waiting)
        waiting.clear()

    for line in _NEWLINE.split(text.removeprefix("\ufeff")):
        line = _LIST_MARKS.sub("", _HEADING.sub("", line, count=1), count=1)
        stripped = line.strip()
        if not stripped:
            continue
        if _is_parenthesis_line(stripped):
            waiting.append(stripped)
            continue
        line = strip_markdown(line)
        buf: list[str] = []
        i = 0
        while i < len(line):
            note = _NOTE.match(line, i)
            if note:
                waiting.append(note.group())
                i = note.end()
                continue
            buf.append(line[i])
            i += 1
            if line[i - 1] in _END_MARKS:
                while i < len(line) and line[i] in _END_MARKS + _CLOSING_QUOTES:
                    buf.append(line[i])
                    i += 1
                close(buf)
        close(buf)  # a line break ends the sentence too

    markers.extend(Marker(None, note) for note in waiting)
    return sentences, markers
