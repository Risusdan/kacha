"""Tests for pipeline.review_data: what the review page shows.

The documents are made up (helpers.transcript_and_trims: a fake recognition, 0.2 s per unit from 0 s unless a test
gives the times, and an envelope that is quiet everywhere, or loud over the whole speech with loud=True so that every
cut point needs listening), written into a project by helpers.edited_project_from_documents."""

import hashlib
import json
import re

import pytest
from helpers import (
    default_edit,
    edited_project_from_documents,
    timed_sentence,
    transcript_and_trims,
    validated_storyboard,
)

from pipeline.captions import build_events, caption_top_px
from pipeline.edit import NOTE_PREFIX, NOTE_REASON, build_segments, require_valid, total_duration, validate_project
from pipeline.errors import EditInvalid, PipelineError
from pipeline.project import file_sha256
from pipeline.review_data import NOT_READY_TEXT, NotReady, edit_version, page_data, sentence_tokens
from pipeline.review_text import ASR_NOTE, LISTEN_NOTE, LOW_MATCH_NOTE, REMOVED_REASON, trim_label
from pipeline.style import font_path, load_style
from pipeline.trim import pause_reason


def project_for(tmp_path, transcript, trims, edit=None, validate=True):
    """A project whose work/ holds the documents; the edit has passed validate unless `validate` is False."""
    p = edited_project_from_documents(tmp_path, transcript, trims, edit)
    if validate:
        validate_project(p)
    return p


def only(trims: dict, kind: str) -> dict:
    found = [trim for trim in trims["trims"] if trim["kind"] == kind]
    assert len(found) == 1, found
    return found[0]


def page_data_labels(trims: dict) -> list[dict]:
    """The candidates of trim.json with the name the page gives their kind."""
    return [{"label": trim_label(trim)} for trim in trims["trims"]]


def shape(tokens: list[dict]) -> list[tuple]:
    """(kind, text, trim) of every token; a trim token and a pause have the trim, a char has none."""
    return [(t["k"], t.get("text"), t.get("trim")) for t in tokens]


def remove(edit: dict, sid: str, reason: str) -> None:
    """Take sentence `sid` out of the reel with a cuts.extra range of its own."""
    edit["cuts"]["extra"].append({"from": sid, "to": sid, "reason": reason})
    edit["sentences"] = [item for item in edit["sentences"] if item["id"] != sid]


# ---- the project: readiness, version, segments, captions --------------------------------------


def test_page_data_refuses_until_edit_json_passed_validate(tmp_path):
    transcript, trims = transcript_and_trims("今天我們來聊剪輯。", "今天我們來聊剪輯")
    p = project_for(tmp_path, transcript, trims, validate=False)
    with pytest.raises(NotReady) as refused:
        page_data(p)
    assert refused.value.message == NOT_READY_TEXT == "Claude 正在更新剪接設定，請稍候。"
    assert isinstance(refused.value, PipelineError)
    validate_project(p)
    assert page_data(p)["project"] == p.name
    # an edit.json that changed after validate is not ready either
    edit = json.loads(p.edit_path.read_text(encoding="utf-8"))
    edit["captions"]["mode"] = "word"
    p.edit_path.write_text(json.dumps(edit), encoding="utf-8")
    with pytest.raises(NotReady):
        page_data(p)


def test_not_ready_says_so_by_default():
    assert NotReady().message == NOT_READY_TEXT
    assert NotReady(NOT_READY_TEXT).message == NOT_READY_TEXT


def test_page_data_refuses_an_edit_that_require_valid_refuses(tmp_path, monkeypatch):
    transcript, trims = transcript_and_trims("今天我們來聊剪輯。", "今天我們來聊剪輯")
    p = project_for(tmp_path, transcript, trims)

    def refuse(_):
        raise EditInvalid(["cuts: trim t01 is neither accepted nor rejected"])

    monkeypatch.setattr("pipeline.review_data.require_valid", refuse)
    with pytest.raises(NotReady) as refused:
        page_data(p)
    assert refused.value.message == NOT_READY_TEXT


def version_of(edit: str | None, transcript: str | None, trim: str | None) -> str:
    """The version as review_data documents it: the sha256 of the JSON array of the three hashes, null for a file
    that is not there."""
    return hashlib.sha256(json.dumps([edit, transcript, trim]).encode("ascii")).hexdigest()


def rewrite(path) -> None:
    """Write the JSON document at `path` again with the same content in other bytes (as a step run again may)."""
    path.write_text(json.dumps(json.loads(path.read_text(encoding="utf-8")), ensure_ascii=False), encoding="utf-8")


def test_the_version_is_the_hash_of_edit_json_transcript_json_and_trim_json(tmp_path):
    transcript, trims = transcript_and_trims("今天我們來聊剪輯。", "今天我們來聊剪輯")
    p = project_for(tmp_path, transcript, trims)
    files = [p.edit_path, p.work / "transcript.json", p.work / "trim.json"]
    assert edit_version(p) == version_of(*map(file_sha256, files)) == page_data(p)["version"]


@pytest.mark.parametrize("name", ["transcript.json", "trim.json"])
def test_a_new_transcript_or_trim_is_a_new_version_with_the_same_edit_json(tmp_path, name):
    transcript, trims = transcript_and_trims("今天我們來聊剪輯。", "今天我們來聊剪輯")
    p = project_for(tmp_path, transcript, trims)
    seen, edit_bytes = edit_version(p), p.edit_path.read_bytes()
    rewrite(p.work / name)  # transcribe or trim ran again; Claude validated the same edit.json on it
    validate_project(p)
    assert p.edit_path.read_bytes() == edit_bytes
    assert edit_version(p) != seen
    assert page_data(p)["version"] == edit_version(p)


def test_the_version_of_a_project_without_edit_json(tmp_path):
    transcript, trims = transcript_and_trims("今天我們來聊剪輯。", "今天我們來聊剪輯")
    p = project_for(tmp_path, transcript, trims)
    p.edit_path.unlink()
    assert edit_version(p) == version_of(None, file_sha256(p.work / "transcript.json"),
                                         file_sha256(p.work / "trim.json"))


def test_segments_and_duration_are_build_segments(tmp_path):
    transcript, trims = transcript_and_trims("今天我們來聊剪輯。第二句話。", "今天我們嗯來聊剪輯第二句話")
    p = project_for(tmp_path, transcript, trims)
    edit, _, _ = require_valid(p)
    segments = build_segments(edit, transcript, trims)
    assert len(segments) == 2  # the accepted filler cuts the reel in two
    data = page_data(p)
    assert data["segments"] == [[round(s.start, 3), round(s.end, 3)] for s in segments]
    assert data["duration_s"] == round(total_duration(segments), 3)
    assert data["source_s"] == json.loads((p.work / "probe.json").read_text(encoding="utf-8"))["duration_s"]
    assert data["project"] == p.name and data["mode"] == "line"
    assert json.loads(json.dumps(data)) == data  # nothing but JSON values


def test_the_mode_is_the_caption_mode_of_the_edit(tmp_path):
    transcript, trims = transcript_and_trims("今天我們來聊剪輯。", "今天我們來聊剪輯")
    edit = default_edit(transcript, trims)
    edit["captions"]["mode"] = "word"
    assert page_data(project_for(tmp_path, transcript, trims, edit))["mode"] == "word"


def test_captions_are_build_events_on_the_reel(tmp_path):
    transcript, trims = transcript_and_trims("今天，我們來聊剪輯。第二句話。", "今天我們嗯來聊剪輯第二句話")
    p = project_for(tmp_path, transcript, trims)
    edit, _, _ = require_valid(p)
    style = load_style(edit["style"])
    events = build_events(transcript, edit, build_segments(edit, transcript, trims), font_path(style, "bold"), style)
    data = page_data(p)
    assert len(events) >= 2
    assert [(c["start"], c["end"], "".join(piece["text"] for piece in c["pieces"])) for c in data["captions"]] == [
        (round(event.start, 3), round(event.end, 3), event.line.text) for event in events
    ]
    assert [[piece["t"] for piece in c["pieces"]] for c in data["captions"]] == [
        [None if t is None else round(t, 3) for t in event.times] for event in events
    ]
    assert any(piece["t"] is None for c in data["captions"] for piece in c["pieces"])  # the comma has no time


def test_the_caption_style_is_the_style_of_the_edit(tmp_path):
    transcript, trims = transcript_and_trims("今天我們來聊剪輯。", "今天我們來聊剪輯")
    p = project_for(tmp_path, transcript, trims)
    style = load_style("default")
    assert page_data(p)["caption_style"] == {
        "size_px": 56, "color": "#FFFFFF", "highlight": "#FFD400", "outline_color": "#000000", "outline_px": 4,
        "bold": True, "top_px": caption_top_px(style), "canvas_w": 1080, "canvas_h": 1920,
    }


# ---- the sentence as tokens -------------------------------------------------------------------


def test_the_tokens_of_a_sentence_are_its_units_and_the_characters_between_them():
    transcript, trims = transcript_and_trims("今天，我們來聊剪輯。", "今天我們來聊剪輯")
    tokens = sentence_tokens(transcript["sentences"][0], [])
    assert "".join(t["text"] for t in tokens) == "今天，我們來聊剪輯。"
    assert [(t["k"], t["text"]) for t in tokens if t["k"] == "char"] == [("char", "，"), ("char", "。")]
    unit = tokens[0]
    assert unit == {"k": "unit", "text": "今", "start": 0.0, "end": 0.2, "trim": None}


def test_a_filler_is_inserted_where_it_was_said():
    transcript, trims = transcript_and_trims("今天我們來聊剪輯。", "今天我們嗯來聊剪輯")
    filler = only(trims, "filler")
    assert filler["start"] == 0.8  # between the fourth unit and the fifth
    tokens = sentence_tokens(transcript["sentences"][0], [filler])
    assert shape(tokens) == [
        ("unit", "今", None), ("unit", "天", None), ("unit", "我", None), ("unit", "們", None),
        ("trim", "嗯", filler["id"]),
        ("unit", "來", None), ("unit", "聊", None), ("unit", "剪", None), ("unit", "輯", None),
        ("char", "。", None),
    ]


def test_a_candidate_after_the_last_unit_is_inserted_at_the_end():
    transcript, _ = transcript_and_trims("今天我們來聊剪輯。", "今天我們來聊剪輯")
    trim = {"id": "t07", "kind": "extra", "start": 1.8, "end": 2.4, "sentence": "s01", "text": "對了", "reason": "",
            "suggest": "list", "length": 2, "needs_listen": False}
    tokens = sentence_tokens(transcript["sentences"][0], [trim])
    assert shape(tokens)[-2:] == [("char", "。", None), ("trim", "對了", "t07")]


def test_a_candidate_over_recognised_units_marks_them():
    transcript, _ = transcript_and_trims("這個方法可以省下時間。", "这招能帮你省很多事")
    sentence = transcript["sentences"][0]
    assert sentence["display_source"] == "asr" and sentence["text"] == "這招能幫你省很多事"
    repeat = {"id": "t03", "kind": "repeat", "start": 0.2, "end": 0.6, "sentence": "s01", "text": "招能", "reason": "",
              "suggest": "cut", "length": 2, "needs_listen": False}
    tokens = sentence_tokens(sentence, [repeat])
    assert shape(tokens) == [
        ("unit", "這", None), ("unit", "招", "t03"), ("unit", "能", "t03"), ("unit", "幫", None), ("unit", "你", None),
        ("unit", "省", None), ("unit", "很", None), ("unit", "多", None), ("unit", "事", None),
    ]


def test_a_span_that_takes_units_only_in_part_does_not_mark_them():
    sentence = timed_sentence("s01", "今天我們", [(0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8)])
    trim = {"id": "t01", "kind": "filler", "start": 0.45, "end": 0.55, "sentence": "s01", "text": "嗯", "reason": "",
            "suggest": "cut", "length": 0, "needs_listen": False}
    tokens = sentence_tokens(sentence, [trim])
    assert shape(tokens) == [("unit", "今", None), ("unit", "天", None), ("unit", "我", None), ("trim", "嗯", "t01"),
                             ("unit", "們", None)]  # before the first unit that starts after 0.45


def test_a_unit_without_a_length_is_not_taken_by_the_candidate_next_to_it():
    # 們 was not said: its unit has no length, at the end of the unit before it
    sentence = timed_sentence("s01", "今天們來", [(0.0, 0.2), (0.2, 0.4), (0.4, 0.4), (0.6, 0.8)])
    filler = {"id": "t01", "kind": "filler", "start": 0.4, "end": 0.6, "sentence": "s01", "text": "嗯", "reason": "",
              "suggest": "cut", "length": 0, "needs_listen": False}
    tokens = sentence_tokens(sentence, [filler])
    assert shape(tokens) == [("unit", "今", None), ("unit", "天", None), ("trim", "嗯", "t01"), ("unit", "們", None),
                             ("unit", "來", None)]


def test_a_script_word_that_was_skipped_is_not_taken_by_the_candidate_over_its_time():
    # 我 and 們 were not said: their times are made up between 天 and 來 (src "interp"), where the filler was said
    sentence = timed_sentence("s01", "今天我們來", [(0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.0)])
    for unit in sentence["units"][2:4]:
        unit["src"] = "interp"
    filler = {"id": "t01", "kind": "filler", "start": 0.4, "end": 0.8, "sentence": "s01", "text": "嗯", "reason": "",
              "suggest": "cut", "length": 0, "needs_listen": False}
    tokens = sentence_tokens(sentence, [filler])
    assert shape(tokens) == [("unit", "今", None), ("unit", "天", None), ("trim", "嗯", "t01"), ("unit", "我", None),
                             ("unit", "們", None), ("unit", "來", None)]
    assert tokens[2] == {"k": "trim", "trim": "t01", "text": "嗯"}


def test_a_pause_becomes_a_pause_token():
    transcript, trims = transcript_and_trims(
        "今天我們來聊剪輯。第二句話。", [("今天我們", 0.0, 0.8), ("來聊剪輯", 2.0, 2.8), ("第二句話", 3.0, 3.8)]
    )
    pause = only(trims, "pause")
    assert pause["reason"] == pause_reason(1.2, 0.33)
    tokens = sentence_tokens(transcript["sentences"][0], [pause])
    assert [(t["k"], t.get("trim")) for t in tokens].count(("pause", pause["id"])) == 1
    index = next(i for i, t in enumerate(tokens) if t["k"] == "pause")
    assert tokens[index - 1]["text"] == "們" and tokens[index + 1]["text"] == "來"
    assert tokens[index] == {"k": "pause", "trim": pause["id"], "text": "停頓 1.2→0.3 秒"}


def test_a_pause_never_marks_the_units_it_passes_over():
    sentence = timed_sentence("s01", "今天", [(0.0, 0.2), (0.2, 0.4)])
    pause = {"id": "t01", "kind": "pause", "start": 0.19, "end": 0.41, "sentence": "s01", "text": "",
             "reason": pause_reason(0.9, 0.4), "suggest": "cut", "length": 0, "needs_listen": False}
    tokens = sentence_tokens(sentence, [pause])
    assert [t.get("trim") for t in tokens if t["k"] == "unit"] == [None, None]
    assert [t["k"] for t in tokens].count("pause") == 1


def test_every_candidate_shows_in_the_tokens_even_when_they_overlap():
    sentence = timed_sentence("s01", "這個呃很重要", [(0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.0), (1.0, 1.2)])
    outer = {"id": "t01", "kind": "extra", "start": 0.0, "end": 1.2, "sentence": "s01", "text": "這個呃很重要",
             "reason": "", "suggest": "list", "length": 6, "needs_listen": False}
    inner = {"id": "t02", "kind": "filler", "start": 0.4, "end": 0.6, "sentence": "s01", "text": "呃",
             "reason": "", "suggest": "cut", "length": 0, "needs_listen": False}
    for order in ([outer, inner], [inner, outer]):
        tokens = sentence_tokens(sentence, order)
        shown = {t["trim"] for t in tokens if t.get("trim")}
        assert shown == {"t01", "t02"}
        assert [t["text"] for t in tokens if t["k"] == "unit"] == list("這個呃很重要")


# ---- the sentences ----------------------------------------------------------------------------


def test_sentence_times_are_in_reel_clock_and_removed_ones_say_why(tmp_path):
    script = "第一句話。離題的一句。第三句話。拿掉的一句。這句沒講。"
    transcript, trims = transcript_and_trims(script, "第一句話離題的一句第三句話拿掉的一句")
    edit = default_edit(transcript, trims)
    remove(edit, "s02", "離題")
    remove(edit, "s04", REMOVED_REASON)
    data = page_data(project_for(tmp_path, transcript, trims, edit))
    rows = {row["id"]: row for row in data["sentences"]}
    assert [rows[sid]["state"] for sid in ("s01", "s02", "s03", "s04", "s05")] == [
        "kept", "removed", "kept", "removed", "unspoken"
    ]
    assert [rows[sid]["time"] for sid in ("s01", "s02", "s03", "s04", "s05")] == ["0:00.0", "", "0:00.8", "", ""]
    assert rows["s03"]["start"] == 1.8 and rows["s03"]["end"] == 2.6  # source time
    assert rows["s02"]["removal"] == {"reason": "離題", "by_page": False}
    assert rows["s04"]["removal"] == {"reason": REMOVED_REASON, "by_page": True}
    assert rows["s01"]["removal"] is None and rows["s05"]["removal"] is None
    assert rows["s05"]["text"] == "這句沒講。" and rows["s05"]["script"] == "這句沒講。"
    assert [t["text"] for t in rows["s03"]["tokens"] if t["k"] == "unit"] == list("第三句話")


def test_a_sentence_has_its_text_its_script_and_whether_the_captions_are_recognised(tmp_path):
    transcript, trims = transcript_and_trims("第一句話。這個方法可以省下時間。", "第一句話这招能帮你省很多事")
    data = page_data(project_for(tmp_path, transcript, trims))
    first, second = data["sentences"]
    assert (first["asr"], first["text"], first["script"], first["note"]) == (False, "第一句話。", "第一句話。", None)
    assert (second["asr"], second["text"], second["script"]) == (True, "這招能幫你省很多事", "這個方法可以省下時間。")
    assert second["note"] == ASR_NOTE
    assert data["nav"]["asr"] == ["s02"]


def test_page_data_marks_an_edited_caption(tmp_path):
    transcript, trims = transcript_and_trims("第一句話。這個方法可以省下時間。", "第一句話这招能帮你省很多事")
    heard = transcript["sentences"][1]
    assert heard["display_source"] == "asr" and heard["text"] == "這招能幫你省很多事"
    edit = default_edit(transcript, trims)
    edit["captions"]["text"] = {"s02": "這招能幫你省很多時間"}
    data = page_data(project_for(tmp_path, transcript, trims, edit))
    first, second = data["sentences"]
    assert (first["caption_edited"], first["recognised"], first["asr"]) == (False, False, False)
    assert first["caption"] == first["original"] == first["text"] == "第一句話。"
    # the edited sentence shows its caption, is no open typo any more, and keeps what it was
    assert (second["caption_edited"], second["recognised"], second["asr"]) == (True, True, False)
    assert second["caption"] == second["text"] == "這招能幫你省很多時間"
    assert second["original"] == "這招能幫你省很多事" and second["script"] == "這個方法可以省下時間。"
    assert second["note"] is None  # no ASR_NOTE: the captions no longer show the recognised text
    assert data["nav"]["asr"] == []
    units = [t for t in second["tokens"] if t["k"] == "unit"]
    assert "".join(t["text"] for t in second["tokens"]) == "這招能幫你省很多時間"
    assert [t["start"] for t in units] == [round(u["start"], 3) for u in heard["units"]]  # the times it was said
    assert "".join(piece["text"] for c in data["captions"] for piece in c["pieces"]).endswith("這招能幫你省很多時間")
    # Claude's note is the suggestion of the typo card
    claude_note = f"{NOTE_PREFIX}把「事」改成「時間」{NOTE_REASON}腳本是時間。"
    edit["notes"] = {"s02": claude_note}
    second = page_data(project_for(tmp_path / "again", transcript, trims, edit))["sentences"][1]
    assert second["note"] == claude_note


def test_a_sentence_without_a_caption_text_shows_its_own(tmp_path):
    transcript, trims = transcript_and_trims("第一句話。這個方法可以省下時間。第三句話。", "第一句話这招能帮你省很多事")
    edit = default_edit(transcript, trims)
    edit["captions"]["text"] = {"s01": "第一句話吧"}
    data = page_data(project_for(tmp_path, transcript, trims, edit))
    _, second, third = data["sentences"]
    assert (second["caption_edited"], second["recognised"], second["asr"]) == (False, True, True)
    assert second["caption"] == second["original"] == second["text"] == "這招能幫你省很多事"
    assert second["note"] == ASR_NOTE and data["nav"]["asr"] == ["s02"]
    assert (third["state"], third["caption_edited"], third["caption"], third["original"]) == (
        "unspoken", False, "第三句話。", "第三句話。")


def test_a_removed_sentence_keeps_its_caption_text(tmp_path):
    transcript, trims = transcript_and_trims("第一句話。第二句話。", "第一句話第二句話")
    edit = default_edit(transcript, trims)
    edit["captions"]["text"] = {"s02": "第二句吧"}
    remove(edit, "s02", REMOVED_REASON)
    second = page_data(project_for(tmp_path, transcript, trims, edit))["sentences"][1]
    assert (second["state"], second["caption_edited"], second["caption"], second["original"]) == (
        "removed", True, "第二句吧", "第二句話。")


def test_a_removed_recognised_sentence_is_no_typo_card(tmp_path):
    transcript, trims = transcript_and_trims("第一句話。這個方法可以省下時間。第三句話。", "第一句話这招能帮你省很多事第三句話")
    edit = default_edit(transcript, trims)
    remove(edit, "s02", REMOVED_REASON)
    data = page_data(project_for(tmp_path, transcript, trims, edit))
    second = data["sentences"][1]
    assert second["state"] == "removed" and second["asr"] is False and second["note"] is None
    assert data["nav"]["asr"] == []


# ---- the candidates ---------------------------------------------------------------------------


def test_decisions_are_numbered_from_one_in_time_order(tmp_path):
    # the recognised sentence comes first: review_items numbers it 1, the page counts it apart
    transcript, trims = transcript_and_trims(
        "這個方法可以省下時間。今天我們來聊剪輯。第三句話。",
        "这招能帮你省很多事今天咱們來講今天我們來聊剪輯很重要第三句話",
    )
    data = page_data(project_for(tmp_path, transcript, trims))
    first, second = [t for t in data["trims"] if t["decision"]]
    assert (first["label"], second["label"]) == ("腳本外的話", "腳本外的話")
    assert [t["number"] for t in data["trims"] if t["decision"]] == [1, 2]
    assert data["nav"]["decisions"] == [first["id"], second["id"]]
    assert data["nav"]["asr"] == ["s01"]
    assert first["start"] < second["start"]


def test_a_reworded_take_is_a_decision_and_a_filler_is_not(tmp_path):
    transcript, trims = transcript_and_trims("第一句話。今天我們來聊剪輯。", "第一句話今天咱們來講嗯今天我們來聊剪輯")
    data = page_data(project_for(tmp_path, transcript, trims))
    by_label = {t["label"]: t for t in data["trims"]}
    assert set(by_label) == {"改詞重講", "贅字"}
    reworded, filler = by_label["改詞重講"], by_label["贅字"]
    assert (reworded["decision"], reworded["number"], reworded["cut"]) == (True, 1, False)
    assert (filler["decision"], filler["number"], filler["cut"]) == (False, None, True)
    assert reworded["in_reel"] and reworded["time"] != "" and "今天咱們來講" in reworded["text"]
    assert data["nav"]["decisions"] == [reworded["id"]]


def test_a_candidate_outside_the_reel_has_no_time(tmp_path):
    transcript, trims = transcript_and_trims(
        "第一句話。今天我們來聊剪輯。第三句話。", "第一句話今天我們順便說一下來聊剪輯第三句話"
    )
    extra = only(trims, "extra")
    edit = default_edit(transcript, trims)
    remove(edit, "s02", "離題")
    row = page_data(project_for(tmp_path, transcript, trims, edit))["trims"][0]
    assert row["id"] == extra["id"] and not row["in_reel"] and row["time"] == ""
    assert not row["decision"] and row["number"] is None and not row["listen"]


def test_the_cut_of_a_candidate_listens_when_its_cut_point_is_in_speech(tmp_path):
    transcript, trims = transcript_and_trims("今天我們來聊剪輯。", "今天我們來聊嗯剪輯", loud=[(0.0, 2.0)])
    filler = only(trims, "filler")
    assert filler["needs_listen"]
    data = page_data(project_for(tmp_path, transcript, trims))
    row = data["trims"][0]
    assert (row["cut"], row["in_reel"], row["listen"], row["listen_note"]) == (True, True, True, LISTEN_NOTE)
    assert row["time"] == "0:01.2"
    assert {"trim": filler["id"], "start": filler["start"]} in data["nav"]["listen"]
    head = {"sentence": "s01", "kind": "cut_point", "start": 0.0, "end": 0.0, "note": LISTEN_NOTE}
    assert head in data["nav"]["listen"]  # the head of the reel
    for entry in data["nav"]["listen"]:
        assert set(entry) in ({"trim", "start"}, {"sentence", "kind", "start", "end", "note"})
    edit = default_edit(transcript, trims)
    edit["cuts"]["accept"].remove(filler["id"])
    edit["cuts"]["reject"].append(filler["id"])
    row = page_data(project_for(tmp_path / "again", transcript, trims, edit))["trims"][0]
    assert (row["cut"], row["listen"], row["listen_note"]) == (False, False, None)


def test_a_cut_point_of_a_sentence_says_to_listen_around_it_and_never_shows_the_typo_note(tmp_path):
    # the sentence at the head of the reel shows the recognised text: its own note is ASR_NOTE, its cut point's is not
    transcript, trims = transcript_and_trims("這個方法可以省下時間。第二句話。", "这招能帮你省很多事第二句話",
                                             loud=[(0.0, 4.0)])
    p = project_for(tmp_path, transcript, trims)
    data = page_data(p)
    first = data["sentences"][0]
    assert first["asr"] and first["note"] == ASR_NOTE
    edges = [entry for entry in data["nav"]["listen"] if entry.get("kind") == "cut_point"]
    assert [entry["sentence"] for entry in edges] == ["s01", "s02"]  # the head and the tail of the reel
    tail_end = data["sentences"][1]["end"]
    assert edges == [
        {"sentence": "s01", "kind": "cut_point", "start": 0.0, "end": 0.0, "note": LISTEN_NOTE},
        {"sentence": "s02", "kind": "cut_point", "start": edges[1]["start"], "end": edges[1]["start"],
         "note": LISTEN_NOTE},
    ]
    assert abs(edges[1]["start"] - tail_end) < 0.5  # the tail is at the end of the last sentence
    edit = default_edit(transcript, trims)
    claude_note = f"{NOTE_PREFIX}聽一下開頭{NOTE_REASON}第一個字講得很輕。"
    edit["notes"] = {"s01": claude_note}
    edges = [entry for entry in page_data(project_for(tmp_path / "again", transcript, trims, edit))["nav"]["listen"]
             if entry.get("kind") == "cut_point"]
    assert [entry["note"] for entry in edges] == [claude_note, LISTEN_NOTE]  # Claude's note for that sentence wins


def test_a_low_match_sentence_says_to_listen_to_the_whole_sentence(tmp_path):
    transcript, trims = transcript_and_trims("一二三四五六七八九十。第二句話。", "一二三四五六七八天地第二句話")
    first = transcript["sentences"][0]
    assert first["display_source"] == "script" and "low_match" in first["reasons"]
    data = page_data(project_for(tmp_path, transcript, trims))
    assert data["sentences"][0]["note"] is None
    assert data["nav"]["listen"] == [
        {"sentence": "s01", "kind": "low_match", "start": first["start"], "end": first["end"], "note": LOW_MATCH_NOTE},
    ]
    edit = default_edit(transcript, trims)
    claude_note = f"{NOTE_PREFIX}聽一下這一句{NOTE_REASON}講得比腳本快。"
    edit["notes"] = {"s01": claude_note}
    [entry] = page_data(project_for(tmp_path / "again", transcript, trims, edit))["nav"]["listen"]
    assert entry["note"] == claude_note


def test_the_cuts_are_counted_as_the_storyboard_counts_them(tmp_path):
    script = "第一句話。離題的一句。第三句話。拿掉的一句。第五句話。"
    transcript, trims = transcript_and_trims(script, "第一句話嗯離題的一句第三句話拿掉的一句第五句話")
    edit = default_edit(transcript, trims)
    remove(edit, "s02", "離題")
    remove(edit, "s04", REMOVED_REASON)
    p = project_for(tmp_path, transcript, trims, edit)
    data = page_data(p)
    assert data["cuts"] == validated_storyboard(p)["cuts"]
    accepted_in_reel = [t for t in data["trims"] if t["cut"] and t["in_reel"]]
    assert data["cuts"] == len(accepted_in_reel) + 2
    edit["cuts"]["extra"] = [{"from": "s02", "to": "s04", "reason": "離題"}]  # one range of three sentences
    edit["sentences"] = [item for item in edit["sentences"] if item["id"] not in ("s02", "s03", "s04")]
    again = project_for(tmp_path / "again", transcript, trims, edit)
    assert page_data(again)["cuts"] == validated_storyboard(again)["cuts"]


def test_a_note_in_edit_json_wins_over_the_default(tmp_path):
    transcript, trims = transcript_and_trims(
        "今天我們來聊剪輯。這個方法可以省下時間。第三句話。", "今天我們來聊嗯剪輯这招能帮你省很多事第三句話"
    )
    filler = only(trims, "filler")
    edit = default_edit(transcript, trims)
    kept_note = f"{NOTE_PREFIX}保留{NOTE_REASON}這個嗯是講者的口氣。"
    sentence_note = f"{NOTE_PREFIX}聽一下前後{NOTE_REASON}這一句講得很快。"
    typo_note = f"{NOTE_PREFIX}拿掉這一句{NOTE_REASON}有錯字，重錄比較好。"
    edit["notes"] = {filler["id"]: kept_note, "s01": sentence_note, "s02": typo_note}
    data = page_data(project_for(tmp_path, transcript, trims, edit))
    row = data["trims"][0]
    assert row["note"] == kept_note and row["suggest_cut"] is False  # the default would have cut it
    first, second, third = data["sentences"]
    assert (first["note"], second["note"], third["note"]) == (sentence_note, typo_note, None)
    edit["notes"] = {}
    row = page_data(project_for(tmp_path / "again", transcript, trims, edit))["trims"][0]
    assert row["note"] == f"{NOTE_PREFIX}剪掉{NOTE_REASON}「嗯」是贅字。" and row["suggest_cut"] is True


def test_the_suggestion_of_a_note_that_suggests_neither_is_none(tmp_path):
    transcript, trims = transcript_and_trims("今天我們來聊剪輯。", "今天我們來聊嗯剪輯")
    edit = default_edit(transcript, trims)
    edit["notes"] = {only(trims, "filler")["id"]: LISTEN_NOTE}
    row = page_data(project_for(tmp_path, transcript, trims, edit))["trims"][0]
    assert row["note"] == LISTEN_NOTE and row["suggest_cut"] is None


def test_nothing_shown_holds_an_english_reason(tmp_path):
    transcript, trims = transcript_and_trims(
        "第一句話。今天我們來聊剪輯。這個方法可以省下時間。第四句話。",
        [("第一句話", 0, 0.8), ("今天我們", 0.8, 1.4), ("今天我們", 1.4, 2.2), ("嗯", 2.2, 2.4), ("來聊剪輯", 2.4, 3.2),
         ("很重要", 3.2, 3.8), ("這個辦法可以省下時間", 5.0, 7.0), ("這個方法可以省下時間", 7.0, 9.0),
         ("很重要", 9.0, 9.6), ("第第四句話", 9.6, 10.6)],
        loud=[(0.0, 11.0)],
    )
    assert {t["label"] for t in page_data_labels(trims)} == {"NG", "贅字", "改詞重講", "停頓", "腳本外的話"}
    data = page_data(project_for(tmp_path, transcript, trims))
    shown = [t["label"] for t in data["trims"]] + [t["note"] for t in data["trims"]]
    shown += [t["listen_note"] for t in data["trims"] if t["listen_note"]] + [t["time"] for t in data["trims"]]
    shown += [s["note"] for s in data["sentences"] if s["note"]] + [s["time"] for s in data["sentences"]]
    shown += [t["text"] for s in data["sentences"] for t in s["tokens"] if t["k"] == "pause"]
    assert len(shown) > 10
    assert not any(re.search(r"[A-Za-z]{3,}", s) for s in shown), shown
    assert not any(re.search(r"\b[st]\d\d\b", s) for s in shown), shown


def test_page_data_holds_nothing_but_json_values(tmp_path):
    transcript, trims = transcript_and_trims(
        "今天我們來聊剪輯。第二句話。", [("今天我們", 0.0, 0.8), ("來聊剪輯", 2.0, 2.8), ("第二句話", 3.0, 3.8)]
    )
    data = page_data(project_for(tmp_path, transcript, trims))
    assert json.loads(json.dumps(data)) == data
    for sentence in data["sentences"]:
        for key in ("start", "end"):
            assert sentence[key] == round(sentence[key], 3)
