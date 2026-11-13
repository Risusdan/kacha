"""What the review page shows: page_data(p) is the document the page loads, built from edit.json, transcript.json,
trim.json and probe.json as the storyboard reads them.

page_data refuses with NotReady while edit.json has not passed validate on the files as they are now (or while
require_valid refuses it): Claude is changing it, and the page tries again later. The version (edit_version) is a
hash of edit.json, transcript.json and trim.json together; a change the page sends carries the version it saw.

Every time is in seconds rounded to 3 places: segments and the start and end of a sentence, a candidate and an
item of nav.listen are source time (what the review video and the transcript use), captions are time on the cut
reel. A time the page shows to the user is a string of the cut reel (storyboard.clock). Nothing the page shows holds
a sentence id, a candidate id or an English reason: ids are in the id, trim and sentence fields and in nav only.

The page numbers the decisions it asks (a reworded take, extra speech: review_items' reworded and extra) from 1 in
time order. A sentence whose captions show the recognised text is a decision of its own kind in review_items; the
page counts those apart (nav.asr), so its numbers differ from review_items'.

A sentence is "kept" (it is in the reel), "removed" (a cuts.extra range holds it; removal says whose reason) or
"unspoken" (the speaker did not say it). Its caption is the text its captions show: the caption text edit.json's
captions.text gives it (caption_edited is then true), else its own text (original, the text of transcript.json).
The transcript shows the caption: text is the caption, and the tokens are those of the caption with the units
captions.caption_source makes for it, so they keep the times the words were said. recognised is true when the
sentence's own text is the recognised one; asr is true for a kept recognised sentence without a caption text of its
own, whose captions show the recognised text, and nav.asr lists the same sentences. The note of a sentence is
Claude's, else ASR_NOTE when asr is true.

An item of nav.listen is about a candidate ({"trim", "start"}: its row has the note) or about a sentence
({"sentence", "kind", "start", "end", "note"}): a cut point of the reel (kind cut_point, start and end both its
time) or a sentence that matches the script poorly (low_match, from its start to its end). Its note is Claude's
note for the sentence, else LISTEN_NOTE or LOW_MATCH_NOTE.

cuts is the number of cuts the storyboard counts: the accepted candidates in the reel and the cuts.extra ranges.
"""

import hashlib
import json

from pipeline.captions import build_events, caption_source, caption_top_px
from pipeline.edit import (
    build_segments,
    caption_texts,
    kept_sentences,
    load_document,
    map_time,
    require_valid,
    total_duration,
)
from pipeline.errors import PipelineError
from pipeline.project import Project, edit_file_hashes, edit_stage
from pipeline.review_items import Item, review_items, trims_in_reel
from pipeline.review_text import (
    ASR_NOTE,
    LISTEN_NOTE,
    LOW_MATCH_NOTE,
    REMOVED_REASON,
    default_note,
    suggested_cut,
    trim_label,
)
from pipeline.storyboard import clock, removal_notes
from pipeline.style import font_path, load_style
from pipeline.trim import pause_lengths

NOT_READY_TEXT = "Claude 正在更新剪接設定，請稍候。"
SPAN_TOLERANCE_S = 0.001  # a unit lies inside a candidate's span when it is no more than this far outside it
PAGE_DECISIONS = ("reworded", "extra")  # the decisions the page numbers


class NotReady(PipelineError):
    """edit.json has not passed validate on the current files, so there is nothing to show yet."""

    def __init__(self, message: str = NOT_READY_TEXT, /, **extra):
        super().__init__(message, **extra)


def edit_version(p: Project) -> str:
    """The version of the edit the page works on: the sha256 of the JSON text json.dumps([edit, transcript, trim])
    (ASCII) of the sha256 of work/edit.json, work/transcript.json and work/trim.json (project.edit_file_hashes),
    each null when its file is not there. A rerun of transcribe or trim is thus a new version even when edit.json
    stays the same: the ids in it may name other sentences or candidates. Every file is read; a missing one is not an
    error."""
    hashes = edit_file_hashes(p)
    text = json.dumps([hashes["edit"], hashes["transcript"], hashes["trim"]])
    return hashlib.sha256(text.encode("ascii")).hexdigest()


def _seconds(t: float | None) -> float | None:
    return None if t is None else round(t, 3)


def _pause_text(trim: dict) -> str:
    """The label of a pause on the transcript: 停頓 0.8→0.4 秒 (the pause, and what is left of it)."""
    lengths = pause_lengths(trim["reason"])
    if lengths is None:
        raise PipelineError(f"trim.json has a pause whose reason cannot be read: {trim['reason']}")
    return f"停頓 {lengths[0]:.1f}→{lengths[1]:.1f} 秒"


def sentence_tokens(sentence: dict, trims: list[dict]) -> list[dict]:
    """The text of `sentence` as tokens in order, with each of `trims` (the candidates it owns) where it was said.

    A unit span of the text is {"k": "unit", "text", "start", "end", "trim"}, a character outside every unit
    {"k": "char", "text"}. A candidate whose span takes whole units (each unit starts no earlier than the
    candidate less SPAN_TOLERANCE_S and ends no later than it plus that, was heard (its "src" is "asr": not a script
    word the speaker skipped, which has a time made up between its neighbours) and has a length) sets "trim" on
    those units, those that no earlier candidate (outer ones come first) has taken. Any other candidate is a token of its
    own, placed before the first unit that starts at or after its start, or at the end: {"k": "trim", "trim",
    "text"} with the candidate's text, and for a pause {"k": "pause", "trim", "text"} with its label. A pause is
    always a token of its own: the units it passes over are not the pause. So every candidate shows once at least."""
    text = sentence["text"]
    units = sorted(sentence["units"], key=lambda unit: unit["span"][0])
    tokens: list[dict] = []
    token_of: list[int] = []  # for each unit, the index of its token
    cursor = 0
    for unit in units:
        first, last = unit["span"]
        tokens.extend({"k": "char", "text": char} for char in text[cursor:first])
        token_of.append(len(tokens))
        tokens.append({"k": "unit", "text": text[first:last], "start": _seconds(unit["start"]),
                       "end": _seconds(unit["end"]), "trim": None})
        cursor = max(cursor, last)
    tokens.extend({"k": "char", "text": char} for char in text[cursor:])

    before: dict[int, list[dict]] = {}  # token index -> the tokens that go in front of it
    at_end: list[dict] = []
    for trim in sorted(trims, key=lambda trim: (trim["start"], -trim["end"])):
        if trim["kind"] != "pause":
            taken = [
                token_of[i] for i, unit in enumerate(units)
                if unit.get("src") == "asr" and unit["end"] > unit["start"]
                and unit["start"] >= trim["start"] - SPAN_TOLERANCE_S and unit["end"] <= trim["end"] + SPAN_TOLERANCE_S
                and tokens[token_of[i]]["trim"] is None
            ]
            if taken:
                for index in taken:
                    tokens[index]["trim"] = trim["id"]
                continue
        token = ({"k": "pause", "trim": trim["id"], "text": _pause_text(trim)} if trim["kind"] == "pause"
                 else {"k": "trim", "trim": trim["id"], "text": trim["text"]})
        position = next((token_of[i] for i, unit in enumerate(units) if unit["start"] >= trim["start"]), None)
        (at_end if position is None else before.setdefault(position, [])).append(token)

    result: list[dict] = []
    for index, token in enumerate(tokens):
        result.extend(before.get(index, []))
        result.append(token)
    return result + at_end


def page_data(p: Project) -> dict:
    """The document the review page loads (see the module text). Raises NotReady unless edit.json has passed
    validate on the current files and require_valid accepts it."""
    if edit_stage(p) is not None:
        raise NotReady()
    version = edit_version(p)  # before the files are read: a file that changes meanwhile shows as a new version
    try:
        edit, transcript, trims = require_valid(p)
    except PipelineError as error:
        raise NotReady() from error
    probe = load_document(p.work / "probe.json", "prep", ("duration_s",))
    style = load_style(edit["style"])
    caps = style["captions"]
    segments = build_segments(edit, transcript, trims)
    events = build_events(transcript, edit, segments, font_path(style, "bold" if caps["bold"] else "regular"), style)
    decisions, listen = review_items(edit, transcript, trims, segments)
    inside = trims_in_reel(edit, transcript, trims, segments)
    accepted = set(edit["cuts"]["accept"])
    notes = edit.get("notes", {})
    fixed = caption_texts(edit)

    asked = [item.trim for item in decisions if item.kind in PAGE_DECISIONS]
    numbers = {trim_id: number for number, trim_id in enumerate(asked, 1)}
    recognised = [item.sentence for item in decisions if item.kind == "asr_text"]

    owned: dict[str, list[dict]] = {}
    for trim in trims["trims"]:
        owned.setdefault(trim["sentence"], []).append(trim)
    kept = set(kept_sentences(edit, transcript))
    reasons = removal_notes(edit, transcript)
    sentences_by_id = {sentence["id"]: sentence for sentence in transcript["sentences"]}

    sentences = []
    for sentence in transcript["sentences"]:
        sid = sentence["id"]
        state = "unspoken" if not sentence["spoken"] else "kept" if sid in kept else "removed"
        heard = sentence["display_source"] == "asr"
        edited = sid in fixed
        asr = state == "kept" and heard and not edited
        caption, units = caption_source(sentence, edit)
        sentences.append({
            "id": sid,
            "state": state,
            "time": clock(map_time(sentence["start"], segments)) if state == "kept" else "",
            "start": _seconds(sentence["start"]),
            "end": _seconds(sentence["end"]),
            "tokens": sentence_tokens({**sentence, "text": caption, "units": units}, owned.get(sid, [])),
            "asr": asr,
            "recognised": heard,
            "caption_edited": edited,
            "text": caption,
            "caption": caption,
            "original": sentence["text"],
            "script": sentence["script_text"],
            "note": notes.get(sid) or (ASR_NOTE if asr else None),
            "removal": ({"reason": reasons[sid], "by_page": reasons[sid] == REMOVED_REASON}
                        if state == "removed" else None),
        })

    rows = []
    for trim in trims["trims"]:
        trim_id = trim["id"]
        cut, in_reel = trim_id in accepted, trim_id in inside
        hear = bool(trim["needs_listen"] and cut and in_reel)
        note = notes.get(trim_id) or default_note(trim)
        rows.append({
            "id": trim_id,
            "label": trim_label(trim),
            "text": trim["text"],
            "start": _seconds(trim["start"]),
            "end": _seconds(trim["end"]),
            "time": clock(map_time(trim["start"], segments)) if in_reel else "",
            "cut": cut,
            "in_reel": in_reel,
            "decision": trim_id in numbers,
            "listen": hear,
            "number": numbers.get(trim_id),
            "note": note,
            "suggest_cut": suggested_cut(note),
            "listen_note": LISTEN_NOTE if hear else None,
        })

    return {
        "project": p.name,
        "version": version,
        "mode": edit["captions"]["mode"],
        "source_s": _seconds(float(probe["duration_s"])),
        "duration_s": _seconds(total_duration(segments)),
        "segments": [[_seconds(segment.start), _seconds(segment.end)] for segment in segments],
        "captions": [
            {"start": _seconds(event.start), "end": _seconds(event.end),
             "pieces": [{"text": piece.text, "t": _seconds(t)} for piece, t in zip(event.line.pieces, event.times)]}
            for event in events
        ],
        "caption_style": {
            "size_px": caps["size_px"], "color": caps["color"], "highlight": caps["highlight"],
            "outline_color": caps["outline_color"], "outline_px": caps["outline_px"], "bold": caps["bold"],
            "top_px": caption_top_px(style), "canvas_w": style["canvas"]["w"], "canvas_h": style["canvas"]["h"],
        },
        "sentences": sentences,
        "trims": rows,
        "cuts": len(accepted & inside) + len(edit["cuts"].get("extra", [])),
        "nav": {
            "decisions": asked,
            "listen": [
                {"trim": item.trim, "start": _seconds(item.time)} if item.trim is not None
                else _sentence_listen(item, sentences_by_id[item.sentence], notes)
                for item in listen
            ],
            "asr": recognised,
        },
    }


def _sentence_listen(item: Item, sentence: dict, notes: dict) -> dict:
    """The nav.listen entry of a listen item about a sentence: a cut point of the reel (cut_point) plays around
    its time, a sentence that matches the script poorly (low_match) plays the whole sentence. The note is Claude's
    note for the sentence, else the fixed one of the kind."""
    low_match = item.kind == "low_match"
    return {
        "sentence": item.sentence,
        "kind": item.kind,
        "start": _seconds(item.time),
        "end": _seconds(sentence["end"] if low_match else item.time),
        "note": notes.get(item.sentence) or (LOW_MATCH_NOTE if low_match else LISTEN_NOTE),
    }
