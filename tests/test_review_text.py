"""Tests for pipeline.review_text: the Chinese kind name and the suggestion of each rough-cut candidate."""

import pytest

from pipeline.edit import NOTE_PREFIX, NOTE_REASON
from pipeline.errors import PipelineError
from pipeline.review_text import (
    ASR_NOTE,
    LISTEN_NOTE,
    LOW_MATCH_NOTE,
    REMOVED_REASON,
    default_note,
    suggested_cut,
    trim_label,
)
from pipeline.trim import pause_reason


def _trim(kind: str, suggest: str, text: str, reason: str = "") -> dict:
    return {"kind": kind, "start": 1.0, "end": 1.4, "sentence": "s01", "text": text, "reason": reason,
            "suggest": suggest, "length": 1}


PAUSE = _trim("pause", "cut", "", pause_reason(0.82, 0.37))
FILLER = _trim("filler", "cut", "嗯", "filler 嗯")
REPEAT = _trim("repeat", "cut", "我我", "repeated 我我")
NG = _trim("retake", "cut", "我們先講第一點", "earlier take of s02")
REWORDED = _trim("retake", "list", "我們先講第一點", "reworded take of s02")
EXTRA = _trim("extra", "list", "對了順便說一下", "extra speech")
ALL_KINDS = [PAUSE, FILLER, REPEAT, NG, REWORDED, EXTRA]


@pytest.mark.parametrize("trim, label, note", [
    (PAUSE, "停頓", "建議剪掉。因為這是 0.8 秒的停頓，剪成 0.4 秒。"),
    (FILLER, "贅字", "建議剪掉。因為「嗯」是贅字。"),
    (REPEAT, "重複", "建議剪掉。因為「我我」是重複的字。"),
    (NG, "NG", "建議剪掉。因為這一段講錯了，後面有重講的一次。"),
    (REWORDED, "改詞重講", "建議剪掉。因為後面有照腳本講的一次；這一次有腳本沒有的內容的話，就選保留。"),
    (EXTRA, "腳本外的話", "建議保留。因為這是腳本外的話，臨場的話常是自然的口語。"),
])
def test_label_and_default_note_by_kind(trim, label, note):
    assert trim_label(trim) == label
    assert default_note(trim) == note


def test_every_default_note_has_the_note_shape():
    for trim in ALL_KINDS:
        note = default_note(trim)
        assert note.startswith(NOTE_PREFIX) and NOTE_REASON in note


def test_a_candidate_that_cannot_be_read_is_an_error():
    for trim in (_trim("pause", "cut", "", "pause a while"), _trim("music", "cut", "")):
        with pytest.raises(PipelineError):
            default_note(trim)
    with pytest.raises(PipelineError):
        trim_label(_trim("music", "cut", ""))


def test_the_fixed_texts():
    assert ASR_NOTE == "建議聽一下這一句。因為字幕用的是辨識出來的字，可能有錯字。"
    assert LISTEN_NOTE == "建議聽一下前後。因為剪接處不在靜音裡，可能切到字。"
    for note in (ASR_NOTE, LISTEN_NOTE):
        assert note.startswith(NOTE_PREFIX) and NOTE_REASON in note
    assert REMOVED_REASON == "在審閱頁拿掉"


def test_the_low_match_note_is_a_suggestion_to_listen():
    assert LOW_MATCH_NOTE == "建議聽一下這一句。因為講的話和腳本對不太起來，字幕用的是腳本的字。"
    assert LOW_MATCH_NOTE.startswith(NOTE_PREFIX) and NOTE_REASON in LOW_MATCH_NOTE
    assert suggested_cut(LOW_MATCH_NOTE) is None


@pytest.mark.parametrize("note, cut", [
    ("建議剪掉。因為…", True),
    ("建議保留。因為…", False),
    ("建議聽一下。因為…", None),
    ("", None),
])
def test_suggested_cut_reads_the_start_of_the_note(note, cut):
    assert suggested_cut(note) is cut
