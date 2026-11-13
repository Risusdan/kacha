"""The changes the review page makes to work/edit.json: apply_change(p, version, change) is the one entry point.

A change is {"type": "cut", "trim": id, "cut": bool}, {"type": "remove", "sentence": id},
{"type": "restore", "sentence": id} or {"type": "caption", "sentence": id, "text": str or null} (null: the sentence
shows its own text again); anything else is refused. The page sends the version it saw (review_data.edit_version,
of edit.json, transcript.json and trim.json together). apply_change refuses with NotReady while edit.json has not
passed validate on the files as they are now (Claude is changing it), with VersionConflict when the version is not
the current one, before the files are read or once they are (Claude changed edit.json, or transcribe or trim ran
again, since the page loaded it), and with ChangeRefused when the new edit would not validate. Nothing is written in
any of these cases. The caller serialises the calls.

The new edit is built from a copy of the one on disk (set_cut, remove_sentence, restore_sentence, set_caption never
change the edit they are given) and checked in memory with edit.validate_edit before anything is written.
ChangeRefused carries Chinese text for the page: GLYPH_TEXT when the caption font lacks a character a new caption
text shows (checked first, by caption_lines), INVISIBLE_TEXT when all it lacks are characters that show nothing of
their own (a zero-width space pasted in), CAPTION_EMPTY_TEXT when a caption text would show nothing,
NOTHING_LEFT_TEXT when the edit would leave no sentence in the reel, else REFUSED_TEXT. The English reasons go to
work/review.log, which is where Claude looks when the page said to ask it. Then write_edit replaces edit.json (a temp
file of its own in work/, then os.replace, so a reader never sees half a file and two writers never share a temp
file) and validate_project records the pass in state.json. preview_caption gives the lines a caption text would get,
refused as its change would be, and writes nothing.

Removing a sentence adds a cuts.extra range for it (reason review_text.REMOVED_REASON), drops its entry from
sentences, and cuts the extra speech and the retakes of that sentence that were kept and are still in the reel: they
were said in front of it and would be left hanging. work/review-undo.json keeps what restoring needs: for each
sentence removed in the page, the entry it had in sentences and the candidates this changed from kept to cut. The
record holds the sha256 of transcript.json and trim.json it was made for, because the ids in it mean something else
once either file changes; a record of other files is ignored (as is one that cannot be read). Restoring takes off the
cuts.extra range that holds the sentence, all of it, and puts back every spoken sentence of the range, with the saved
entry where there is one and DEFAULT_SEM and DEFAULT_WEIGHT otherwise (a range Claude wrote has no record).
"""

import copy
import datetime
import json
import unicodedata
from contextlib import suppress
from pathlib import Path

from pipeline.captions import break_lines, missing_glyphs, remap_units
from pipeline.edit import build_segments, kept_sentences, require_valid, validate_edit, validate_project
from pipeline.errors import PipelineError
from pipeline.project import Project, edit_stage, file_sha256, write_text_atomically
from pipeline.review_data import NotReady, edit_version
from pipeline.review_items import trims_in_reel
from pipeline.review_text import REMOVED_REASON
from pipeline.style import font_path, load_style

UNDO_FILE = "review-undo.json"
LOG_FILE = "review.log"
DEFAULT_SEM, DEFAULT_WEIGHT = "claim", "main"  # the labels of a sentence that has no saved entry
CHANGE_TYPES = ("cut", "remove", "restore", "caption")  # the types of change the page sends
CUT_WITH_REMOVAL = ("extra", "retake")  # the kinds of candidate that go when the sentence they belong to goes
CONFLICT_TEXT = "決定剛被更新過，請再看一次。"
NOTHING_LEFT_TEXT = "至少要留下一句，這個改動沒有存。"
REFUSED_TEXT = "這個改動會讓剪接設定出錯，沒有存。請回對話請 Claude 看看。"
CAPTION_EMPTY_TEXT = "字幕至少要有一個字，沒有存。"
GLYPH_TEXT = "字型沒有「{chars}」這幾個字，沒有存。"  # {chars}: the characters the caption font lacks
INVISIBLE_TEXT = "字幕裡有看不見的字元，多半是貼上時帶進來的，沒有存。把貼上的那一段刪掉重打。"  # all it lacks
_NOTHING_LEFT = "nothing is left"  # what validate_edit says when the edit leaves no sentence, or no piece, in the reel
_CAPTION_PATH = "captions.text"  # validate_edit's errors about a caption text start with this
_NOTHING_SHOWN = (": must not be empty", ": must have a character to show")  # and end so when the text shows nothing
_LOGGED_CHARS = 100  # text the page sent goes into the log as its repr, cut after this many characters


class VersionConflict(PipelineError):
    """The page's version is not the current one (review_data.edit_version): the page has to load it again."""

    def __init__(self, message: str = CONFLICT_TEXT, /, **extra):
        super().__init__(message, **extra)


class ChangeRefused(PipelineError):
    """A change was refused and nothing was written. `message` is Chinese, for the page; `reasons` are the English
    lines for work/review.log (validate_edit's errors, or what is wrong with the change)."""

    def __init__(self, message: str = REFUSED_TEXT, /, *, reasons: list[str] | None = None):
        super().__init__(message)
        self.reasons = reasons or []


# ---- writing ----------------------------------------------------------------------------------


def _shown(value: object) -> str:
    """`value` as a log line says it: its repr (one line, whatever the page sent), cut after _LOGGED_CHARS."""
    text = repr(value)
    return text if len(text) <= _LOGGED_CHARS else text[:_LOGGED_CHARS] + "…"


def _write_atomically(path: Path, text: str) -> None:
    """`path` becomes `text`, all at once (project.write_text_atomically: a temp file of its own beside it, which
    replaces it). When that fails the temp file (and only that one) is removed and the file is as it was; the
    failure is a PipelineError."""
    try:
        write_text_atomically(path, text)
    except OSError as error:
        raise PipelineError(f"cannot write {path}: {error.strerror or error}") from error


def _json_text(document: dict) -> str:
    return json.dumps(document, ensure_ascii=False, indent=2) + "\n"


def write_edit(p: Project, edit: dict) -> None:
    """Replace work/edit.json with `edit`, atomically (see _write_atomically). Writes no validation: the caller has
    validated `edit` (apply_change does, in memory, before this)."""
    _write_atomically(p.edit_path, _json_text(edit))


def _log_refusal(p: Project, kind: str, reasons: list[str]) -> None:
    """Add one line to work/review.log for each of `reasons`. The log is for Claude to read: a log that cannot be
    written must not turn the refusal into something else, so a failure to write it is not reported."""
    stamp = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    lines = "".join(f"{stamp} refused {kind}: {reason}\n" for reason in reasons)
    with suppress(OSError), open(p.work / LOG_FILE, "a", encoding="utf-8") as log:
        log.write(lines)


# ---- the record of what a removal changed -----------------------------------------------------


def _document_hashes(p: Project) -> tuple[str, str]:
    """The sha256 of transcript.json and trim.json: what a record in UNDO_FILE is made for."""
    return file_sha256(p.work / "transcript.json"), file_sha256(p.work / "trim.json")


def _is_entry(entry: object) -> bool:
    """Whether `entry` has the shape of a record of one removal: {"sentences": [dict ...], "accepted": [id ...]}."""
    return (
        isinstance(entry, dict)
        and isinstance(entry.get("sentences"), list) and all(isinstance(item, dict) for item in entry["sentences"])
        and isinstance(entry.get("accepted"), list) and all(isinstance(item, str) for item in entry["accepted"])
    )


def _read_undo(p: Project, hashes: tuple[str, str]) -> dict[str, dict]:
    """The removals recorded in UNDO_FILE, {sentence id: entry}, or {} when there is no such file, it cannot be read
    or is not in the shape this module writes, or it was made for other files than those with `hashes`. An entry in
    the wrong shape is left out."""
    try:
        record = json.loads((p.work / UNDO_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(record, dict) or (record.get("transcript"), record.get("trim")) != hashes:
        return {}
    removed = record.get("removed")
    if not isinstance(removed, dict):
        return {}
    return {sid: entry for sid, entry in removed.items() if _is_entry(entry)}


def _write_undo(p: Project, hashes: tuple[str, str], removed: dict[str, dict]) -> None:
    _write_atomically(p.work / UNDO_FILE, _json_text({"transcript": hashes[0], "trim": hashes[1], "removed": removed}))


# ---- the steps: each builds a new edit and leaves the one it is given -------------------------


def set_cut(edit: dict, trim_id: str, cut: bool) -> dict:
    """A copy of `edit` that cuts the candidate `trim_id` (cut) or keeps it, as a move between cuts.accept and
    cuts.reject. The id is not checked here: validate_edit refuses an id that is not a candidate."""
    new = copy.deepcopy(edit)
    cuts = new["cuts"]
    target, other = ("accept", "reject") if cut else ("reject", "accept")
    cuts[other] = [item for item in cuts[other] if item != trim_id]
    if trim_id not in cuts[target]:
        cuts[target].append(trim_id)
    return new


def remove_sentence(edit: dict, transcript: dict, trims: dict, sid: str) -> tuple[dict, dict]:
    """(a copy of `edit` without the kept sentence `sid`, what restoring it needs). The copy has a cuts.extra range
    for the sentence and no entry for it in sentences, and every candidate of the sentence that is an extra or a
    retake, is in cuts.reject and is still in the reel (trims_in_reel of the copy) is in cuts.accept instead. The
    second value is {"sentences": [the entry the sentence had], "accepted": [the candidates that were moved]}.
    A sentence that is not kept (not spoken, removed already, or not in the transcript) raises ChangeRefused."""
    if sid not in kept_sentences(edit, transcript):
        raise ChangeRefused(reasons=[f"{_shown(sid)} is not a kept sentence"])
    new = copy.deepcopy(edit)
    saved = [item for item in new["sentences"] if item["id"] == sid]
    new["sentences"] = [item for item in new["sentences"] if item["id"] != sid]
    cuts = new["cuts"]
    cuts["extra"] = [*cuts.get("extra", []), {"from": sid, "to": sid, "reason": REMOVED_REASON}]
    inside = trims_in_reel(new, transcript, trims, build_segments(new, transcript, trims))
    moved = [
        trim["id"] for trim in trims["trims"]
        if trim["sentence"] == sid and trim["kind"] in CUT_WITH_REMOVAL and trim["id"] in cuts["reject"]
        and trim["id"] in inside
    ]
    cuts["reject"] = [item for item in cuts["reject"] if item not in moved]
    cuts["accept"] = [*cuts["accept"], *moved]
    return new, {"sentences": saved, "accepted": moved}


def restore_sentence(edit: dict, transcript: dict, sid: str, undo: dict) -> dict:
    """A copy of `edit` without the cuts.extra range that holds the removed sentence `sid`, with every spoken
    sentence of that range back in sentences (in script order). `undo` maps a sentence id to the entry
    remove_sentence made for it: a sentence with one gets the entry it had, and each candidate of it that is cut now
    goes back to cuts.reject (it was kept before the removal); a sentence without one gets DEFAULT_SEM and
    DEFAULT_WEIGHT. A sentence that is not removed (kept, not spoken, or not in the transcript) raises
    ChangeRefused."""
    order = {sentence["id"]: i for i, sentence in enumerate(transcript["sentences"])}
    sentences = transcript["sentences"]
    held = None
    if sid in order and sentences[order[sid]]["spoken"]:
        held = next((i for i, item in enumerate(edit["cuts"].get("extra", []))
                     if order[item["from"]] <= order[sid] <= order[item["to"]]), None)
    if held is None:
        raise ChangeRefused(reasons=[f"{_shown(sid)} is not a removed sentence"])
    new = copy.deepcopy(edit)
    item = new["cuts"]["extra"].pop(held)
    back = [sentence["id"] for sentence in sentences[order[item["from"]]:order[item["to"]] + 1] if sentence["spoken"]]

    entries, cut_before = [], []
    for sentence_id in back:
        record = undo.get(sentence_id) or {"sentences": [], "accepted": []}
        entry = next((saved for saved in record["sentences"] if saved.get("id") == sentence_id), None)
        entries.append(copy.deepcopy(entry) or {"id": sentence_id, "sem": DEFAULT_SEM, "weight": DEFAULT_WEIGHT})
        cut_before += record["accepted"]
    new["sentences"] = sorted([*new["sentences"], *entries], key=lambda entry: order[entry["id"]])
    cuts = new["cuts"]
    undone = [trim_id for trim_id in cut_before if trim_id in cuts["accept"]]
    cuts["accept"] = [trim_id for trim_id in cuts["accept"] if trim_id not in undone]
    cuts["reject"] = [*cuts["reject"], *undone]
    return new


def set_caption(edit: dict, sid: str, text: str | None) -> dict:
    """A copy of `edit` whose captions.text gives sentence `sid` the caption text `text`, or, when `text` is None,
    has no text for it (the sentence shows its own again); a captions.text left empty is dropped. Nothing is
    checked here: validate_edit refuses an id that is not a spoken sentence and a text that cannot be shown."""
    new = copy.deepcopy(edit)
    captions = new["captions"]
    texts = {key: value for key, value in captions.get("text", {}).items() if key != sid}
    if text is not None:
        texts[sid] = text
    if texts:
        captions["text"] = texts
    else:
        captions.pop("text", None)
    return new


def caption_lines(edit: dict, transcript: dict, sid: str, text: str) -> list[str]:
    """The lines the captions of sentence `sid` show with the caption text `text`, as the render breaks them
    (captions.break_lines over captions.remap_units, in the caption font of the edit's style); [] when there is no
    such sentence (validate_edit refuses it). Raises ChangeRefused when the font lacks characters the lines show:
    GLYPH_TEXT with the visible ones, each once, in the order they show; INVISIBLE_TEXT when every one of them is
    invisible (_invisible), as GLYPH_TEXT would show nothing between its brackets. A character the lines do not
    show (a space at the end, a Markdown mark) is not checked, as the render does not check it."""
    sentence = next((item for item in transcript["sentences"] if item["id"] == sid), None)
    if sentence is None:
        return []
    style = load_style(edit["style"])
    font = font_path(style, "bold" if style["captions"]["bold"] else "regular")
    units = remap_units(sentence["text"], sentence["units"], text)
    lines = [line.text for line in break_lines(text, units, font, style)]
    shown = "".join(lines)
    missing = missing_glyphs(shown, font)
    if missing:
        chars = "".join(dict.fromkeys(char for char in shown if char in missing))
        codes = " ".join(f"U+{ord(char):04X}" for char in chars)  # the log stays one line whatever they are
        reason = f"captions.text of {_shown(sid)}: the caption font {font.name} has no glyph for {codes}"
        visible = "".join(char for char in chars if not _invisible(char))
        raise ChangeRefused(GLYPH_TEXT.format(chars=visible) if visible else INVISIBLE_TEXT, reasons=[reason])
    return lines


def _invisible(char: str) -> bool:
    """Whether `char` shows nothing of its own: a format character (Unicode category Cf: zero-width space,
    zero-width joiner, byte order mark ...) or a variation selector (U+FE00 to U+FE0F, U+E0100 to U+E01EF)."""
    code = ord(char)
    return unicodedata.category(char) == "Cf" or 0xFE00 <= code <= 0xFE0F or 0xE0100 <= code <= 0xE01EF


# ---- the entry points -------------------------------------------------------------------------


def _refusal(errors: list[str]) -> ChangeRefused:
    """The refusal of an edit that validate_edit finds `errors` in: CAPTION_EMPTY_TEXT when a caption text would
    show nothing (the user can fix that), NOTHING_LEFT_TEXT when no sentence would be left, else REFUSED_TEXT."""
    if any(error.startswith(_CAPTION_PATH) and error.endswith(_NOTHING_SHOWN) for error in errors):
        return ChangeRefused(CAPTION_EMPTY_TEXT, reasons=errors)
    if any(_NOTHING_LEFT in error for error in errors):
        return ChangeRefused(NOTHING_LEFT_TEXT, reasons=errors)
    return ChangeRefused(REFUSED_TEXT, reasons=errors)


def _is_caption(change: dict) -> bool:
    """Whether `change` is a change of a caption text: a sentence id, and a text or null."""
    return (change.get("type") == "caption" and isinstance(change.get("sentence"), str) and "text" in change
            and (change["text"] is None or isinstance(change["text"], str)))


def _build(change: object, edit: dict, transcript: dict, trims: dict,
           removed: dict[str, dict]) -> tuple[dict, dict[str, dict] | None]:
    """(the new edit for `change`, the removed record to write: None when the change leaves the record alone).
    A change that is none of the four the page sends raises ChangeRefused; so does a caption text with a character
    the caption font lacks (caption_lines), before anything else checks it."""
    if isinstance(change, dict):
        kind = change.get("type")
        if kind == "cut" and isinstance(change.get("trim"), str) and type(change.get("cut")) is bool:
            return set_cut(edit, change["trim"], change["cut"]), None
        if kind == "remove" and isinstance(change.get("sentence"), str):
            new, entry = remove_sentence(edit, transcript, trims, change["sentence"])
            return new, {**removed, change["sentence"]: entry}
        if kind == "restore" and isinstance(change.get("sentence"), str):
            new = restore_sentence(edit, transcript, change["sentence"], removed)
            used = {item["id"] for item in new["sentences"]} - {item["id"] for item in edit["sentences"]}
            return new, {sid: entry for sid, entry in removed.items() if sid not in used}
        if _is_caption(change):
            if change["text"] is not None:
                caption_lines(edit, transcript, change["sentence"], change["text"])
            return set_caption(edit, change["sentence"], change["text"]), None
    raise ChangeRefused(reasons=[f"not a change of the page (cut, remove, restore or caption): {_shown(change)}"])


def _documents(p: Project) -> tuple[dict, dict, dict]:
    """(edit, transcript, trims) as require_valid reads them; NotReady when it refuses them."""
    try:
        return require_valid(p)
    except PipelineError as error:
        raise NotReady() from error


def apply_change(p: Project, version: str, change: dict) -> dict:
    """Apply `change` (see the module text) to the edit the page saw as `version`; returns {"version": the new
    version}. Raises NotReady, VersionConflict or ChangeRefused, and writes nothing, when the change cannot be
    applied. The caller holds the lock."""
    if edit_stage(p) is not None:
        raise NotReady()
    if version != edit_version(p):
        raise VersionConflict()
    hashes = _document_hashes(p)  # before the files are read: the record is made for these
    edit, transcript, trims = _documents(p)
    if version != edit_version(p):  # a file changed while they were read: what was read may be newer than `version`
        raise VersionConflict()
    kind = change.get("type") if isinstance(change, dict) else None
    kind = kind if kind in CHANGE_TYPES else "change"  # what the log says: never text the page sent
    try:
        new, removed = _build(change, edit, transcript, trims, _read_undo(p, hashes))
        errors = validate_edit(new, transcript, trims)
        if errors:
            raise _refusal(errors)
    except ChangeRefused as refused:
        _log_refusal(p, kind, refused.reasons)
        raise
    write_edit(p, new)
    validate_project(p)
    if removed is not None:
        _write_undo(p, hashes, removed)
    return {"version": edit_version(p)}


def preview_caption(p: Project, sid: str, text: str) -> list[str]:
    """The lines (caption_lines) the captions of sentence `sid` would show with the caption text `text`, for the
    page to show while the user types; nothing is written or logged. Refuses as apply_change would refuse the
    change {"type": "caption", "sentence": sid, "text": text} once the version matched: NotReady, or
    ChangeRefused with GLYPH_TEXT, CAPTION_EMPTY_TEXT or REFUSED_TEXT. Takes no lock: it reads only."""
    if edit_stage(p) is not None:
        raise NotReady()
    edit, transcript, trims = _documents(p)
    lines = caption_lines(edit, transcript, sid, text)
    errors = validate_edit(set_caption(edit, sid, text), transcript, trims)
    if errors:
        raise _refusal(errors)
    return lines
