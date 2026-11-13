"""The gate 1 review lists (pipeline.review_items) and edit.in_reel.

The documents come from helpers.transcript_and_trims: a fake recognition with no gap between units (0.2 s
per unit from 0 s unless a test gives the times), and an envelope that is quiet everywhere, or loud over the whole speech
(loud=True) so that every cut point is in speech and needs listening. The edit is helpers.default_edit
(every suggested cut accepted), changed where a test says so."""

import pytest
from helpers import default_edit, transcript_and_trims

from pipeline.edit import Segment, build_segments, in_reel
from pipeline.review_items import (
    EDGE_PAD_S,
    EXTRA_ASK_MIN,
    LISTEN_PAD_S,
    TRIM_PAD_S,
    Item,
    review_items,
    trims_in_reel,
)


def documents(script: str, asr: str | list[tuple[str, float, float]], loud: bool = False) -> tuple[dict, dict, dict]:
    """(transcript, trims, edit) for `script` said as `asr`; with `loud` the whole speech is loud."""
    transcript, trims = transcript_and_trims(script, asr)
    if loud:
        transcript, trims = transcript_and_trims(script, asr, loud=[(0.0, transcript["audio_s"])])
    return transcript, trims, default_edit(transcript, trims)


def items(edit: dict, transcript: dict, trims: dict) -> tuple[list[Item], list[Item]]:
    return review_items(edit, transcript, trims, build_segments(edit, transcript, trims))


def in_the_reel(edit: dict, transcript: dict, trims: dict) -> set[str]:
    return trims_in_reel(edit, transcript, trims, build_segments(edit, transcript, trims))


def only(trims: dict, kind: str) -> dict:
    found = [trim for trim in trims["trims"] if trim["kind"] == kind]
    assert len(found) == 1, found
    return found[0]


def accept(edit: dict, trim_id: str) -> None:
    edit["cuts"]["reject"].remove(trim_id)
    edit["cuts"]["accept"].append(trim_id)


def remove(edit: dict, sid: str) -> None:
    """Take sentence `sid` out of the reel with a cuts.extra range of its own."""
    edit["cuts"]["extra"].append({"from": sid, "to": sid, "reason": "離題"})
    edit["sentences"] = [item for item in edit["sentences"] if item["id"] != sid]


# ---- in_reel and trims_in_reel ----------------------------------------------------------------


def test_in_reel():
    segments = [Segment(1.0, 2.0), Segment(3.0, 4.0)]
    assert in_reel(1.5, 1.6, segments) and not in_reel(2.0, 3.0, segments) and in_reel(2.5, 3.1, segments)


def test_in_reel_needs_one_shared_frame():
    segments = [Segment(1.0, 2.0)]
    assert not in_reel(0.0, 1.0, segments) and not in_reel(2.0, 2.5, segments)  # touches an edge only
    assert in_reel(0.0, 1.04, segments) and in_reel(1.96, 2.5, segments)  # one frame inside
    assert in_reel(0.0, 5.0, segments)  # covers the segment
    assert not in_reel(1.0, 2.0, [])


def test_an_accepted_filler_inside_a_kept_sentence_is_in_the_reel():
    transcript, trims, edit = documents("今天我們來聊剪輯。", "今天我們嗯來聊剪輯")
    filler = only(trims, "filler")
    assert filler["id"] in edit["cuts"]["accept"]
    # its own cut takes it off the reel; it is in the reel as it would be without that cut
    assert not in_reel(filler["start"], filler["end"], build_segments(edit, transcript, trims))
    assert in_the_reel(edit, transcript, trims) == {filler["id"]}


def test_an_accepted_hesitation_inside_an_accepted_extra_cut_is_not_in_the_reel():
    transcript, trims, edit = documents("第一句話。第二句話。", "第一句話這個呃很重要第二句話")
    extra, hesitation = only(trims, "extra"), only(trims, "filler")
    assert extra["start"] < hesitation["start"] < hesitation["end"] < extra["end"]
    assert in_the_reel(edit, transcript, trims) == {extra["id"], hesitation["id"]}  # the extra is kept
    accept(edit, extra["id"])
    assert in_the_reel(edit, transcript, trims) == {extra["id"]}


def test_an_accepted_ng_take_before_the_first_sentence_is_not_in_the_reel():
    transcript, trims, edit = documents("今天我們來聊剪輯。第二句話。", "今天我們今天我們來聊剪輯第二句話")
    take = only(trims, "retake")
    assert take["id"] in edit["cuts"]["accept"] and take["end"] <= transcript["sentences"][0]["start"]
    assert in_the_reel(edit, transcript, trims) == set()


def test_a_rejected_extra_in_a_sentence_removed_by_cuts_extra_is_not_in_the_reel():
    transcript, trims, edit = documents("第一句話。今天我們來聊剪輯。第三句話。", "第一句話今天我們順便說一下來聊剪輯第三句話")
    extra = only(trims, "extra")
    assert extra["id"] in edit["cuts"]["reject"] and extra["sentence"] == "s02"
    assert in_the_reel(edit, transcript, trims) == {extra["id"]}
    remove(edit, "s02")
    assert in_the_reel(edit, transcript, trims) == set()


# ---- the decisions ----------------------------------------------------------------------------


def test_a_clean_take_gives_two_empty_lists():
    transcript, trims, edit = documents("今天我們來聊剪輯。", "今天我們來聊剪輯")
    assert items(edit, transcript, trims) == ([], [])


def test_a_sentence_shown_as_recognised_is_a_decision():
    transcript, trims, edit = documents("第一句話。這個方法可以省下時間。", "第一句話这招能帮你省很多事")
    s02 = transcript["sentences"][1]
    assert s02["display_source"] == "asr" and "low_match" in s02["reasons"]
    decisions, listen = items(edit, transcript, trims)
    clip = (s02["start"] - LISTEN_PAD_S, s02["end"] + LISTEN_PAD_S)
    assert decisions == [Item(1, "asr_text", s02["start"], "s02", None, "這招能幫你省很多事", "", None, clip)]
    assert listen == []  # its low match is the decision itself, not a place to listen to
    remove(edit, "s02")
    assert items(edit, transcript, trims) == ([], [])


def test_a_fixed_recognised_sentence_is_no_longer_a_decision():
    transcript, trims, edit = documents("第一句話。這個方法可以省下時間。", "第一句話这招能帮你省很多事")
    assert [item.sentence for item in items(edit, transcript, trims)[0]] == ["s02"]
    edit["captions"]["text"] = {"s01": "第一句話"}  # the text of another sentence fixes nothing
    assert [item.sentence for item in items(edit, transcript, trims)[0]] == ["s02"]
    edit["captions"]["text"] = {"s02": "這招能幫你省很多事"}  # the user has fixed it
    assert items(edit, transcript, trims) == ([], [])


def test_a_fixed_recognised_sentence_leaves_the_numbers_without_a_gap():
    transcript, trims, edit = documents(
        "一二三四五六七八九十。這個方法可以省下時間。第三句話。",
        "一二嗯三四五真的很重要六七八天地这招能帮你省很多事第三句話",
        loud=True,
    )
    decisions, listen = items(edit, transcript, trims)
    assert [(item.number, item.kind) for item in decisions] == [(1, "extra"), (2, "asr_text")]
    first = [item.number for item in listen]
    edit["captions"]["text"] = {"s02": "這招能幫你省很多事"}
    fixed, listen = items(edit, transcript, trims)
    assert [(item.number, item.kind) for item in fixed] == [(1, "extra")]
    assert [item.number for item in listen] == [number - 1 for number in first]


def test_a_reworded_take_in_the_reel_is_a_decision():
    transcript, trims, edit = documents("第一句話。今天我們來聊剪輯。", "第一句話今天咱們來講今天我們來聊剪輯")
    take = only(trims, "retake")
    assert take["suggest"] == "list" and take["start"] < TRIM_PAD_S
    decisions, listen = items(edit, transcript, trims)
    clip = (0.0, take["end"] + TRIM_PAD_S)  # the start is kept at 0
    assert decisions == [Item(1, "reworded", take["start"], "s02", take["id"], "今天咱們來講", "", False, clip)]
    assert listen == []
    accept(edit, take["id"])
    decisions, _ = items(edit, transcript, trims)
    assert [(item.kind, item.trim, item.cut) for item in decisions] == [("reworded", take["id"], True)]


@pytest.mark.parametrize("said,asked", [("很重要", True), ("好的", False)])
def test_extra_speech_of_three_units_is_a_decision_and_of_two_is_not(said, asked):
    transcript, trims, edit = documents("今天我們來聊剪輯。第二句話。", f"今天我們來聊剪輯{said}第二句話")
    extra = only(trims, "extra")
    assert extra["length"] == len(said) and (extra["length"] >= EXTRA_ASK_MIN) == asked
    assert extra["start"] > TRIM_PAD_S
    decisions, listen = items(edit, transcript, trims)
    clip = (extra["start"] - TRIM_PAD_S, extra["end"] + TRIM_PAD_S)
    expected = [Item(1, "extra", extra["start"], "s02", extra["id"], said, "", False, clip)]
    assert decisions == (expected if asked else []) and listen == []


def test_a_candidate_outside_the_reel_is_no_item():
    # extra speech inside a sentence that cuts.extra removes
    transcript, trims, edit = documents("第一句話。今天我們來聊剪輯。第三句話。", "第一句話今天我們順便說一下來聊剪輯第三句話")
    extra = only(trims, "extra")
    assert extra["length"] >= EXTRA_ASK_MIN
    remove(edit, "s02")
    assert items(edit, transcript, trims) == ([], [])
    # an NG take before the first sentence, accepted, its cut point in speech
    transcript, trims, edit = documents("今天我們來聊剪輯。第二句話。", "今天我們今天我們來聊剪輯第二句話", loud=True)
    take = only(trims, "retake")
    assert take["id"] in edit["cuts"]["accept"] and take["needs_listen"]
    decisions, listen = items(edit, transcript, trims)
    assert decisions == [] and [item.trim for item in listen] == [None, None]  # the head and the tail cut points


# ---- the other places to listen to ------------------------------------------------------------


def test_an_accepted_cut_in_the_reel_whose_cut_point_is_in_speech_is_a_listen_item():
    transcript, trims, edit = documents("今天我們來聊剪輯。", "今天我們來聊嗯剪輯", loud=True)
    filler = only(trims, "filler")
    assert filler["needs_listen"] and filler["start"] > TRIM_PAD_S
    _, listen = items(edit, transcript, trims)
    clip = (filler["start"] - TRIM_PAD_S, filler["end"] + TRIM_PAD_S)
    assert [item for item in listen if item.kind == "trim_cut_point"] == [
        Item(2, "trim_cut_point", filler["start"], "s01", filler["id"], "嗯", "", True, clip)
    ]
    edit["cuts"]["accept"].remove(filler["id"])
    edit["cuts"]["reject"].append(filler["id"])
    _, listen = items(edit, transcript, trims)
    assert [item for item in listen if item.kind == "trim_cut_point"] == []  # kept: nothing is cut there


def test_a_hesitation_inside_an_accepted_extra_cut_is_no_listen_item():
    transcript, trims, edit = documents("第一句話。第二句話。", "第一句話這個呃很重要第二句話", loud=True)
    extra, hesitation = only(trims, "extra"), only(trims, "filler")
    assert extra["needs_listen"] and hesitation["needs_listen"]
    _, listen = items(edit, transcript, trims)
    assert [item.trim for item in listen if item.kind == "trim_cut_point"] == [hesitation["id"]]  # the extra is kept
    accept(edit, extra["id"])
    decisions, listen = items(edit, transcript, trims)
    assert [item.trim for item in listen if item.trim is not None] == []
    assert [(item.kind, item.trim, item.cut) for item in decisions] == [("extra", extra["id"], True)]


def test_a_decision_whose_cut_point_is_in_speech_is_not_listed_again():
    transcript, trims, edit = documents("第一句話。今天我們來聊剪輯。", "第一句話今天咱們來講今天我們來聊剪輯", loud=True)
    take = only(trims, "retake")
    accept(edit, take["id"])
    assert take["needs_listen"] and take["id"] in in_the_reel(edit, transcript, trims)
    decisions, listen = items(edit, transcript, trims)
    assert [(item.kind, item.trim) for item in decisions] == [("reworded", take["id"])]
    assert [item.trim for item in listen if item.trim is not None] == []


def test_head_and_tail_cut_points_of_the_only_kept_sentence_are_two_items():
    transcript, trims, edit = documents("今天我們來聊剪輯。", [("今天我們來聊剪輯", 0.5, 2.1)], loud=True)
    bounds, text = trims["bounds"]["s01"], transcript["sentences"][0]["text"]
    assert bounds["needs_listen"] and 0 < bounds["cut_in"] < EDGE_PAD_S
    decisions, listen = items(edit, transcript, trims)
    assert decisions == []
    assert listen == [
        Item(1, "cut_point", bounds["cut_in"], "s01", None, text, "片頭切點", None,
             (0.0, bounds["cut_in"] + EDGE_PAD_S)),  # the start is kept at 0
        Item(2, "cut_point", bounds["cut_out"], "s01", None, text, "片尾切點", None,
             (bounds["cut_out"] - EDGE_PAD_S, bounds["cut_out"] + EDGE_PAD_S)),
    ]


def test_a_cut_point_of_a_removed_sentence_is_an_item_on_its_row():
    transcript, trims, edit = documents("第一句話。第二句話。第三句話。", "第一句話第二句話第三句話", loud=True)
    remove(edit, "s02")
    _, listen = items(edit, transcript, trims)
    s02 = transcript["sentences"][1]
    assert [(item.label, item.sentence, item.text, item.time) for item in listen] == [
        ("片頭切點", "s01", "第一句話。", trims["bounds"]["s01"]["cut_in"]),
        ("刪除段前切點", "s02", s02["text"], trims["bounds"]["s02"]["cut_in"]),
        ("刪除段後切點", "s02", s02["text"], trims["bounds"]["s02"]["cut_out"]),
        ("片尾切點", "s03", "第三句話。", trims["bounds"]["s03"]["cut_out"]),
    ]


def test_a_head_cut_point_owned_by_a_removed_sentence_is_an_item_on_the_first_kept_row():
    transcript, trims, edit = documents("第一句話。第二句話。第三句話。", "第一句話第二句話第三句話")
    bounds = trims["bounds"]
    bounds["s02"]["cut_in"] = bounds["s01"]["cut_out"] - 0.1  # neighbouring bounds overlap: s01 ends the cut later
    bounds["s01"]["needs_listen"] = True
    assert not bounds["s02"]["needs_listen"] and not bounds["s03"]["needs_listen"]
    remove(edit, "s01")
    assert build_segments(edit, transcript, trims)[0].start == bounds["s01"]["cut_out"]
    _, listen = items(edit, transcript, trims)
    cut_out = bounds["s01"]["cut_out"]
    assert listen == [
        Item(1, "cut_point", cut_out, "s02", None, "第二句話。", "片頭切點在刪除的 s01 之後", None,
             (0.0, cut_out + EDGE_PAD_S)),
    ]


def test_a_low_match_sentence_with_script_captions_is_a_listen_item():
    transcript, trims, edit = documents("一二三四五六七八九十。第二句話。", "一二三四五六七八天地第二句話")
    s01 = transcript["sentences"][0]
    assert s01["display_source"] == "script" and "low_match" in s01["reasons"]
    decisions, listen = items(edit, transcript, trims)
    clip = (0.0, s01["end"] + LISTEN_PAD_S)  # the start is kept at 0
    assert decisions == []
    assert listen == [Item(1, "low_match", s01["start"], "s01", None, s01["text"], "", None, clip)]
    remove(edit, "s01")
    assert items(edit, transcript, trims) == ([], [])


def test_a_low_match_sentence_with_a_caption_text_is_no_longer_a_place_to_listen():
    transcript, trims, edit = documents("一二三四五六七八九十。第二句話。", "一二三四五六七八天地第二句話")
    assert "low_match" in transcript["sentences"][0]["reasons"]
    edit["captions"]["text"] = {"s02": "第二句"}  # the text of another sentence changes nothing
    assert [item.kind for item in items(edit, transcript, trims)[1]] == ["low_match"]
    edit["captions"]["text"] = {"s01": "一二三四五六七八九"}  # the captions no longer use the script's words
    assert items(edit, transcript, trims) == ([], [])


# ---- order and numbers ------------------------------------------------------------------------


def test_numbers_run_through_decisions_then_listen_items_in_time_order():
    transcript, trims, edit = documents(
        "一二三四五六七八九十。這個方法可以省下時間。第三句話。",
        "一二嗯三四五真的很重要六七八天地这招能帮你省很多事第三句話",
        loud=True,
    )
    filler, extra = only(trims, "filler"), only(trims, "extra")
    assert [s["display_source"] for s in transcript["sentences"]] == ["script", "asr", "script"]
    decisions, listen = items(edit, transcript, trims)
    assert [(item.number, item.kind, item.sentence, item.trim) for item in decisions] == [
        (1, "extra", "s01", extra["id"]),  # extra speech inside the first sentence comes before the second
        (2, "asr_text", "s02", None),
    ]
    assert [(item.number, item.kind, item.sentence, item.trim, item.label) for item in listen] == [
        (3, "cut_point", "s01", None, "片頭切點"),  # at 0, as the low-match sentence: cut points first
        (4, "low_match", "s01", None, ""),
        (5, "trim_cut_point", "s01", filler["id"], ""),
        (6, "cut_point", "s03", None, "片尾切點"),
    ]
    for found in (decisions, listen):
        assert [item.time for item in found] == sorted(item.time for item in found)
    assert listen[0].time == listen[1].time == 0.0
