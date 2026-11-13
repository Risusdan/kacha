"""The words the review page uses for the rough-cut candidates of work/trim.json: the name of each kind
and the suggestion written under it.

A suggestion is a note in the sense of pipeline.edit: it starts with NOTE_PREFIX, says what the
director suggests, and gives the reason after NOTE_REASON. default_note is the one for a candidate
that has no note of its own in edit.json."""

from pipeline.edit import NOTE_PREFIX, NOTE_REASON
from pipeline.errors import PipelineError
from pipeline.trim import pause_lengths

ASR_NOTE = f"{NOTE_PREFIX}聽一下這一句{NOTE_REASON}字幕用的是辨識出來的字，可能有錯字。"
LISTEN_NOTE = f"{NOTE_PREFIX}聽一下前後{NOTE_REASON}剪接處不在靜音裡，可能切到字。"
LOW_MATCH_NOTE = f"{NOTE_PREFIX}聽一下這一句{NOTE_REASON}講的話和腳本對不太起來，字幕用的是腳本的字。"
REMOVED_REASON = "在審閱頁拿掉"  # the reason of a cuts.extra range for a sentence removed in the page

_CUT = f"{NOTE_PREFIX}剪掉"
_KEEP = f"{NOTE_PREFIX}保留"


def trim_label(trim: dict) -> str:
    """The name of a candidate's kind: 停頓, 贅字, 重複, NG, 改詞重講 or 腳本外的話."""
    kind = trim["kind"]
    if kind == "pause":
        return "停頓"
    if kind == "filler":
        return "贅字"
    if kind == "repeat":
        return "重複"
    if kind == "retake":
        return "NG" if trim["suggest"] == "cut" else "改詞重講"
    if kind == "extra":
        return "腳本外的話"
    raise PipelineError(f"trim.json has a candidate of an unknown kind: {kind}")


def default_note(trim: dict) -> str:
    """The suggestion for a candidate that has no note of its own."""
    kind = trim["kind"]
    text = trim["text"]
    if kind == "pause":
        lengths = pause_lengths(trim["reason"])
        if lengths is None:
            raise PipelineError(f"trim.json has a pause whose reason cannot be read: {trim['reason']}")
        gap, left = lengths
        return f"{_CUT}{NOTE_REASON}這是 {gap:.1f} 秒的停頓，剪成 {left:.1f} 秒。"
    if kind == "filler":
        return f"{_CUT}{NOTE_REASON}「{text}」是贅字。"
    if kind == "repeat":
        return f"{_CUT}{NOTE_REASON}「{text}」是重複的字。"
    if kind == "retake" and trim["suggest"] == "cut":
        return f"{_CUT}{NOTE_REASON}這一段講錯了，後面有重講的一次。"
    if kind == "retake":
        return f"{_CUT}{NOTE_REASON}後面有照腳本講的一次；這一次有腳本沒有的內容的話，就選保留。"
    if kind == "extra":
        return f"{_KEEP}{NOTE_REASON}這是腳本外的話，臨場的話常是自然的口語。"
    raise PipelineError(f"trim.json has a candidate of an unknown kind: {kind}")


def suggested_cut(note: str) -> bool | None:
    """What a note suggests: True to cut, False to keep, None when it suggests neither (for example listening)."""
    if note.startswith(_CUT):
        return True
    if note.startswith(_KEEP):
        return False
    return None
