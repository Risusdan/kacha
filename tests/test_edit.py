"""edit.json validation (phase 1 keys, rules 1 and 7), kept sentences, segments and time mapping.

T and TR are the Meijia transcript and its trim.json (conftest). Tests that need a transcript with
a sentence the speaker skipped, or a pause in an exact place, build their own with
transcript_and_trims; tests of the segment arithmetic alone use hand-built documents."""

import copy
import json
import random
import re

import pytest
from helpers import default_edit, transcript_and_trims

from pipeline import captions
from pipeline.cli import main
from pipeline.edit import (
    FUTURE_KEYS,
    LINE_BREAK_CHARS,
    NOTE_PREFIX,
    NOTE_REASON,
    OPTIONAL_KEYS,
    SEM_TAGS,
    Segment,
    build_segments,
    caption_texts,
    kept_sentences,
    load_document,
    load_edit,
    map_time,
    require_valid,
    total_duration,
    validate_edit,
)
from pipeline.errors import EditInvalid, PipelineError
from pipeline.project import file_sha256, load_state, next_step, save_state
from pipeline.style import styles_root

FPS = 30
THREE = "第一句話。第二句話。第三句話。"
FOUR = "第一句話。第二句話。第三句話。第四句話。"


def at(edit: dict, path: tuple, value) -> dict:
    """`edit` with the value at `path` (keys and list indexes) replaced; returns `edit`."""
    target = edit
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return edit


def without(edit: dict, path: tuple) -> dict:
    """`edit` with the key at `path` removed; returns `edit`."""
    target = edit
    for key in path[:-1]:
        target = target[key]
    del target[path[-1]]
    return edit


def label(path: tuple) -> str:
    """The JSON path of `path` as the errors spell it: sentences[0].id."""
    out = ""
    for key in path:
        out += f"[{key}]" if isinstance(key, int) else (f".{key}" if out else key)
    return out


def frames(segments: list[Segment]) -> list[tuple[int, int]]:
    return [(round(s.start * FPS), round(s.end * FPS)) for s in segments]


def docs(
    bounds: dict[str, tuple[float, float]],
    trims: list[tuple[str, float, float]] = (),
    unspoken: tuple[str, ...] = (),
    extra: list[dict] = (),
    accept: list[str] | None = None,
) -> tuple[dict, dict, dict]:
    """(edit, transcript, trims) written by hand, with only what build_segments reads: sentence ids and
    spoken flags in order, trims with id, start and end, a cut_in and cut_out per sentence in `bounds`
    (an unspoken sentence has none), the accepted trim ids (all of them by default) and extra ranges."""
    transcript = {"sentences": [{"id": sid, "spoken": sid not in unspoken} for sid in bounds]}
    trim_doc = {
        "trims": [{"id": tid, "start": start, "end": end} for tid, start, end in trims],
        "bounds": {sid: {"cut_in": a, "cut_out": b} for sid, (a, b) in bounds.items() if sid not in unspoken},
    }
    edit = {"cuts": {"accept": [t[0] for t in trims] if accept is None else accept, "reject": [], "extra": list(extra)}}
    return edit, transcript, trim_doc


def extra(a: str, b: str, reason: str = "off topic") -> dict:
    return {"from": a, "to": b, "reason": reason}


# ---- the validation rules ---------------------------------------------------------------------


def test_default_edit_is_valid(T, TR):
    assert validate_edit(default_edit(T, TR), T, TR) == []


def test_unknown_and_future_keys(T, TR):
    e = default_edit(T, TR) | {"foo": 1, "scenes": []}
    errs = validate_edit(e, T, TR)
    assert any(x.startswith("foo:") for x in errs) and any("scenes" in x and "phase 2" in x for x in errs)


@pytest.mark.parametrize("key, phase", [("scenes", 2), ("broll", 2), ("punch_in", 2), ("sfx", 3), ("music", 3)])
def test_every_future_key_names_its_phase(T, TR, key, phase):
    errs = validate_edit(default_edit(T, TR) | {key: {}}, T, TR)
    assert errs == [f"{key}: not supported until phase {phase}"]
    assert FUTURE_KEYS[key] == phase


def test_future_keys_are_exactly_the_five(T, TR):
    assert FUTURE_KEYS == {"scenes": 2, "broll": 2, "punch_in": 2, "sfx": 3, "music": 3}


def test_the_optional_key_and_the_words_a_note_is_made_of():
    assert OPTIONAL_KEYS == ("notes",)
    assert (NOTE_PREFIX, NOTE_REASON) == ("建議", "。因為")


def test_notes_are_optional(T, TR):
    assert "notes" not in default_edit(T, TR)
    assert validate_edit(default_edit(T, TR), T, TR) == []


def test_notes_accept_a_trim_and_a_spoken_sentence(T, TR):
    sid = next(s["id"] for s in T["sentences"] if s["spoken"])
    notes = {
        TR["trims"][0]["id"]: "建議剪掉。因為這是贅字。",
        sid: "建議聽一下。因為字幕用的是辨識出來的字。",
    }
    assert validate_edit({**default_edit(T, TR), "notes": notes}, T, TR) == []


def test_notes_refuse_unknown_and_unspoken_ids():
    T2, TR2 = transcript_and_trims(THREE, asr="第一句話第三句話")
    assert [s["id"] for s in T2["sentences"] if not s["spoken"]] == ["s02"]
    notes = {"t999": "建議剪掉。因為重複。", "s02": "建議保留。因為好。"}
    assert validate_edit({**default_edit(T2, TR2), "notes": notes}, T2, TR2) == [
        "notes.t999: not a trim or a spoken sentence",
        "notes.s02: not a trim or a spoken sentence",
    ]


@pytest.mark.parametrize("text", ["剪掉。因為重複。", "建議剪掉", "建議剪掉，因為重複。", ""])
def test_a_note_starts_with_a_suggestion_and_gives_a_reason(T, TR, text):
    tid = TR["trims"][0]["id"]
    errs = validate_edit({**default_edit(T, TR), "notes": {tid: text}}, T, TR)
    assert errs == [f"notes.{tid}: must start with 建議 and contain 。因為"]


def test_notes_must_be_an_object_of_strings(T, TR):
    assert validate_edit({**default_edit(T, TR), "notes": []}, T, TR) == ["notes: must be an object, got an array"]
    tid = TR["trims"][0]["id"]
    errs = validate_edit({**default_edit(T, TR), "notes": {tid: 3}}, T, TR)
    assert errs == [f"notes.{tid}: must be a string, got a number"]


def test_caption_text_is_optional(T, TR):
    assert "text" not in default_edit(T, TR)["captions"]
    assert validate_edit(default_edit(T, TR), T, TR) == []


def test_validate_takes_caption_text_for_spoken_sentences_only(T, TR):
    e = default_edit(T, TR)
    e["captions"]["text"] = {"s01": "今天我們來聊短影音剪接", "s03": "這個方法可以省下 150 分鐘"}
    assert validate_edit(e, T, TR) == []
    e["captions"]["text"] = {}
    assert validate_edit(e, T, TR) == []


def test_caption_text_refuses_an_unspoken_and_an_unknown_sentence():
    T2, TR2 = transcript_and_trims(THREE, asr="第一句話第三句話")
    assert [s["id"] for s in T2["sentences"] if not s["spoken"]] == ["s02"]
    e = default_edit(T2, TR2)
    e["captions"]["text"] = {"s01": "第一句", "s02": "沒講的一句", "s99": "沒有的一句", "a b": "怪鍵"}
    assert validate_edit(e, T2, TR2) == [
        "captions.text.s02: not a spoken sentence",
        "captions.text.s99: not a spoken sentence",
        "captions.text['a b']: not a spoken sentence",
    ]


def test_a_removed_sentence_may_keep_its_caption_text(T, TR):
    e = default_edit(T, TR)
    e["cuts"]["extra"] = [extra("s02", "s02")]
    e["sentences"] = [s for s in e["sentences"] if s["id"] != "s02"]
    e["captions"]["text"] = {"s02": "拿掉以後留著的字"}
    assert validate_edit(e, T, TR) == []


@pytest.mark.parametrize("text, error", [
    ("", "must not be empty"),
    ("  ", "must not be empty"),
    ("　 \t", "must not be empty"),
    ("\n", "must not be empty"),
    ("一行\n兩行", "must be one line"),
    (3, "must be a string, got a number"),
    (None, "must be a string, got null"),
    (["字"], "must be a string, got an array"),
    ({"字": 1}, "must be a string, got an object"),
    (True, "must be a string, got a boolean"),
])
def test_validate_refuses_bad_caption_text(T, TR, text, error):
    e = default_edit(T, TR)
    e["captions"]["text"] = {"s02": text}
    assert validate_edit(e, T, TR) == [f"captions.text.s02: {error}"]


@pytest.mark.parametrize("char", ["\n", "\r", "\x0b", "\x0c", "\x85", "\u2028", "\u2029"])
def test_a_caption_text_has_no_line_break_of_any_kind(T, TR, char):
    e = default_edit(T, TR)
    e["captions"]["text"] = {"s02": f"前{char}後"}
    assert validate_edit(e, T, TR) == ["captions.text.s02: must be one line"]


def test_the_line_breaks_validate_refuses_are_the_ones_captions_replaces():
    assert set(LINE_BREAK_CHARS) == {"\n", "\r", "\x0b", "\x0c", "\x85", "\u2028", "\u2029"}
    assert set(captions._LINE_BREAKS) == {ord(char) for char in LINE_BREAK_CHARS}


@pytest.mark.parametrize("text", ["，。", "、", "！？", " ，", "「」", "**，**"])
def test_a_caption_text_with_no_character_to_show_is_refused(T, TR, text):
    e = default_edit(T, TR)
    e["captions"]["text"] = {"s02": text}
    assert validate_edit(e, T, TR) == ["captions.text.s02: must have a character to show"]


def test_a_caption_text_with_a_character_to_show_is_taken(T, TR):
    e = default_edit(T, TR)
    for text in ("好", "，好。", "**好**", "5", "…好", "+"):
        e["captions"]["text"] = {"s02": text}
        assert validate_edit(e, T, TR) == [], text


def test_a_caption_text_that_replaces_a_quoted_word_is_taken():
    """「好」。 becomes 棒: the place of 棒 falls on the 「, which holds no unit; 棒 takes the unit of 好."""
    T2, TR2 = transcript_and_trims("「好」。第二句話。", asr="好第二句話")
    assert [u["span"] for u in T2["sentences"][0]["units"]] == [[1, 2]]
    e = default_edit(T2, TR2)
    e["captions"]["text"] = {"s01": "棒"}
    assert validate_edit(e, T2, TR2) == []


def test_caption_texts_reads_what_is_there_and_ignores_what_is_malformed():
    assert caption_texts({"captions": {"mode": "line", "text": {"s01": "好", "s02": "對"}}}) == {"s01": "好", "s02": "對"}
    assert caption_texts({"captions": {"mode": "line"}}) == {}
    for edit in ({}, {"captions": []}, {"captions": None}, {"captions": "text"}, {"captions": {"text": []}},
                 {"captions": {"text": "好"}}, {"captions": {"text": None}}, [], None, "edit"):
        assert caption_texts(edit) == {}, edit
    assert caption_texts({"captions": {"text": {"s01": "好", "s02": 3, "s03": None, "s04": ["好"]}}}) == {"s01": "好"}


@pytest.mark.parametrize("value, got", [([], "an array"), ("字", "a string"), (3, "a number"), (None, "null")])
def test_caption_text_must_be_an_object(T, TR, value, got):
    e = default_edit(T, TR)
    e["captions"]["text"] = value
    assert validate_edit(e, T, TR) == [f"captions.text: must be an object, got {got}"]


def test_every_caption_text_error_is_reported(T, TR):
    e = default_edit(T, TR)
    e["captions"]["text"] = {"s01": " ", "s02": "一\n二", "s03": 5, "s09": "字"}
    assert validate_edit(e, T, TR) == [
        "captions.text.s01: must not be empty",
        "captions.text.s02: must be one line",
        "captions.text.s03: must be a string, got a number",
        "captions.text.s09: not a spoken sentence",
    ]


def test_caption_mode_is_still_checked_next_to_the_text(T, TR):
    e = default_edit(T, TR)
    e["captions"] |= {"mode": "karaoke", "text": {"s01": "好"}}
    assert validate_edit(e, T, TR) == ["captions.mode: 'karaoke' is not one of line, word"]


def test_unknown_keys_inside_objects_are_errors(T, TR):
    e = default_edit(T, TR)
    e["cuts"]["foo"] = 1
    e["cuts"]["extra"] = [extra("s02", "s02") | {"bar": 2}]
    e["sentences"][0]["baz"] = 3
    e["captions"]["qux"] = 4
    e["sentences"][1]["scenes"] = []  # a future key is only special at the top
    errs = validate_edit(e, T, TR)
    for path in ("cuts.foo", "cuts.extra[0].bar", "sentences[0].baz", "captions.qux", "sentences[1].scenes"):
        assert any(x.startswith(f"{path}:") and "unknown key" in x for x in errs), path


def test_version_and_style(T, TR):
    errs = validate_edit(default_edit(T, TR) | {"version": 2, "style": "nope"}, T, TR)
    assert any("version" in x for x in errs) and any("style" in x for x in errs)


@pytest.mark.parametrize("version", [0, 2, "1", 1.0, True, None, [1]])
def test_only_the_integer_one_is_a_version(T, TR, version):
    errs = validate_edit(at(default_edit(T, TR), ("version",), version), T, TR)
    assert any(x.startswith("version:") for x in errs)


@pytest.mark.parametrize("style", ["nope", "", "../default", "default/", ".", 5, None, ["default"]])
def test_style_must_name_a_style_folder(T, TR, style):
    errs = validate_edit(at(default_edit(T, TR), ("style",), style), T, TR)
    assert any(x.startswith("style:") for x in errs)


def test_missing_keys_are_reported_with_their_path(T, TR):
    for path in [("version",), ("style",), ("cuts",), ("sentences",), ("captions",), ("cuts", "accept"),
                 ("cuts", "reject"), ("captions", "mode"), ("sentences", 0, "id"), ("sentences", 0, "sem"),
                 ("sentences", 0, "weight")]:
        errs = validate_edit(without(default_edit(T, TR), path), T, TR)
        assert any(x.startswith(f"{label(path)}:") and "missing" in x for x in errs), path


def test_extra_items_need_from_to_and_reason(T, TR):
    for key in ("from", "to", "reason"):
        e = default_edit(T, TR)
        e["cuts"]["extra"] = [extra("s02", "s02")]
        errs = validate_edit(without(e, ("cuts", "extra", 0, key)), T, TR)
        assert any(x.startswith(f"cuts.extra[0].{key}:") and "missing" in x for x in errs), key


def test_extra_is_optional_and_means_none(T, TR):
    e = without(default_edit(T, TR), ("cuts", "extra"))
    assert validate_edit(e, T, TR) == []
    assert kept_sentences(e, T) == ["s01", "s02", "s03", "s04"]
    assert len(build_segments(e, T, TR)) == len(build_segments(default_edit(T, TR), T, TR))


def test_every_trim_decided_once(T, TR):
    e = default_edit(T, TR)
    missing = e["cuts"]["accept"].pop()
    assert any(missing in x for x in validate_edit(e, T, TR))
    e = default_edit(T, TR)
    e["cuts"]["reject"].append(e["cuts"]["accept"][0])
    assert validate_edit(e, T, TR)
    e = default_edit(T, TR)
    e["cuts"]["accept"].append("t99")
    assert any("t99" in x for x in validate_edit(e, T, TR))


def test_undecided_trim_error_names_the_trim(T, TR):
    e = default_edit(T, TR)
    gone = e["cuts"]["accept"].pop()
    assert validate_edit(e, T, TR) == [f"cuts: trim {gone} is neither accepted nor rejected"]


def test_unknown_duplicate_and_shared_trims_name_their_position(T, TR):
    e = default_edit(T, TR)
    first = e["cuts"]["accept"][0]
    e["cuts"]["accept"] += ["t99", first]
    e["cuts"]["reject"] += [e["cuts"]["accept"][1]]
    errs = validate_edit(e, T, TR)
    n = len(e["cuts"]["accept"])
    assert f"cuts.accept[{n - 2}]: unknown trim 't99'" in errs
    assert any(x.startswith(f"cuts.accept[{n - 1}]:") and "twice" in x for x in errs)
    assert any(x.startswith(f"cuts.reject[{len(e['cuts']['reject']) - 1}]:") and "also in cuts.accept" in x for x in errs)


def test_a_trim_listed_twice_in_reject_is_an_error(T, TR):
    e = default_edit(T, TR)
    e["cuts"]["reject"] = [e["cuts"]["accept"].pop(0)] * 2
    errs = validate_edit(e, T, TR)
    assert any(x.startswith("cuts.reject[1]:") and "twice" in x for x in errs)


def test_trim_lists_must_hold_strings(T, TR):
    e = default_edit(T, TR)
    e["cuts"]["accept"][0] = 1
    e["cuts"]["accept"][1] = ["t02"]
    errs = validate_edit(e, T, TR)
    assert any(x.startswith("cuts.accept[0]:") for x in errs) and any(x.startswith("cuts.accept[1]:") for x in errs)


def test_extra_validation(T, TR):
    for ranges in ([{"from": "s09", "to": "s09", "reason": "x"}],
                   [{"from": "s03", "to": "s02", "reason": "x"}],
                   [{"from": "s02", "to": "s03", "reason": "x"}, {"from": "s03", "to": "s03", "reason": "y"}]):
        e = default_edit(T, TR)
        e["cuts"]["extra"] = ranges
        assert validate_edit(e, T, TR), ranges


def test_extra_error_messages(T, TR):
    e = default_edit(T, TR)
    e["cuts"]["extra"] = [extra("s09", "s02"), extra("s04", "s03"), extra("s02", "s03"), extra("s03", "s03")]
    e["sentences"] = [s for s in e["sentences"] if s["id"] in ("s01",)]
    errs = validate_edit(e, T, TR)
    assert "cuts.extra[0].from: unknown sentence 's09'" in errs
    assert any(x.startswith("cuts.extra[1]:") and "after" in x for x in errs)
    assert any(x.startswith("cuts.extra[3]:") and "overlaps cuts.extra[2]" in x for x in errs)


def test_extra_ranges_next_to_each_other_do_not_overlap(T, TR):
    e = default_edit(T, TR)
    e["cuts"]["extra"] = [extra("s02", "s02"), extra("s03", "s03")]
    e["sentences"] = [s for s in e["sentences"] if s["id"] in ("s01", "s04")]
    assert validate_edit(e, T, TR) == []


def test_extra_ranges_in_any_order_are_checked_for_overlap(T, TR):
    e = default_edit(T, TR)
    e["cuts"]["extra"] = [extra("s03", "s04"), extra("s01", "s03")]
    e["sentences"] = [s for s in e["sentences"] if s["id"] == "s02"]
    assert any(x.startswith("cuts.extra[1]:") and "overlaps cuts.extra[0]" in x for x in validate_edit(e, T, TR))


def overlap_errors(T, TR, ranges):
    e = default_edit(T, TR)
    e["cuts"]["extra"] = ranges
    return [x for x in validate_edit(e, T, TR) if "overlaps" in x]


def test_overlaps_reported_once_per_range(T, TR):
    overlaps = overlap_errors(T, TR, [extra("s02", "s03")] * 200)
    assert len(overlaps) == 199
    assert overlaps[0] == "cuts.extra[1]: overlaps cuts.extra[0] (s02 to s03)"
    assert all(x.endswith(" overlaps cuts.extra[0] (s02 to s03)") for x in overlaps)


def test_an_overlap_names_the_first_range_it_overlaps(T, TR):
    ranges = [extra("s01", "s02"), extra("s03", "s04"), extra("s02", "s03")]  # the third meets both
    assert overlap_errors(T, TR, ranges) == ["cuts.extra[2]: overlaps cuts.extra[0] (s01 to s02)"]


def test_extra_ends_must_be_spoken_sentences():
    T2, TR2 = transcript_and_trims(THREE, asr="第一句話第三句話")
    for ends in (("s02", "s03"), ("s01", "s02"), ("s02", "s02")):
        e = default_edit(T2, TR2)
        e["cuts"]["extra"] = [extra(*ends)]
        errs = validate_edit(e, T2, TR2)
        assert any("s02" in x and "not spoken" in x for x in errs), ends


def test_extra_may_run_over_an_unspoken_sentence():
    T2, TR2 = transcript_and_trims(FOUR, asr="第一句話第二句話第四句話")
    assert [s["spoken"] for s in T2["sentences"]] == [True, True, False, True]
    e = default_edit(T2, TR2)
    e["cuts"]["extra"] = [extra("s02", "s04")]
    e["sentences"] = [s for s in e["sentences"] if s["id"] == "s01"]
    assert validate_edit(e, T2, TR2) == []
    assert kept_sentences(e, T2) == ["s01"]


def test_sentences_must_match_kept(T, TR):
    e = default_edit(T, TR)
    e["sentences"].pop(1)
    assert any("s02" in x for x in validate_edit(e, T, TR))
    e = default_edit(T, TR)
    e["cuts"]["extra"] = [{"from": "s02", "to": "s02", "reason": "x"}]
    assert any("s02" in x for x in validate_edit(e, T, TR))  # a sentence that extra removes is not listed again


def test_sentence_list_error_messages(T, TR):
    e = default_edit(T, TR)
    e["sentences"].pop(1)  # s02 missing
    e["sentences"][2]["id"] = "s99"  # s04 missing, s99 unknown
    e["sentences"].append(dict(e["sentences"][0]))  # s01 twice
    errs = validate_edit(e, T, TR)
    assert "sentences: s02 is kept but not listed" in errs
    assert "sentences: s04 is kept but not listed" in errs
    assert "sentences[2].id: unknown sentence 's99'" in errs
    assert "sentences[3].id: s01 is listed twice (also sentences[0])" in errs


def test_a_sentence_removed_by_extra_must_not_be_listed(T, TR):
    e = default_edit(T, TR)
    e["cuts"]["extra"] = [extra("s02", "s03")]
    errs = validate_edit(e, T, TR)
    assert "sentences[1].id: s02 is removed by cuts.extra" in errs
    assert "sentences[2].id: s03 is removed by cuts.extra" in errs
    assert not any("s01" in x or "s04" in x for x in errs)


def test_a_sentence_without_speech_must_not_be_listed():
    T2, TR2 = transcript_and_trims(THREE, asr="第一句話第三句話")
    e = default_edit(T2, TR2)
    e["sentences"].insert(1, {"id": "s02", "sem": "claim", "weight": "main"})
    assert validate_edit(e, T2, TR2) == ["sentences[1].id: s02 was not spoken"]


def test_sem_vocabulary_weight_and_mode(T, TR):
    e = default_edit(T, TR)
    e["sentences"][0]["sem"] = "funny"
    e["sentences"][1]["weight"] = "big"
    e["captions"]["mode"] = "karaoke"
    errs = validate_edit(e, T, TR)
    assert any("funny" in x for x in errs) and any("big" in x for x in errs) and any("karaoke" in x for x in errs)
    assert any(x.startswith("sentences[0].sem:") for x in errs)
    assert any(x.startswith("sentences[1].weight:") for x in errs)
    assert any(x.startswith("captions.mode:") for x in errs)


def test_sem_tags_are_the_spec_vocabulary():
    assert SEM_TAGS == ("hook", "claim", "number", "list", "compare", "steps", "example", "quote", "define",
                        "warning", "cta", "aside")


def test_every_sem_tag_weight_and_mode_is_accepted(T, TR):
    for tag in SEM_TAGS:
        for weight in ("main", "sub"):
            e = default_edit(T, TR)
            e["sentences"][0] |= {"sem": tag, "weight": weight}
            assert validate_edit(e, T, TR) == [], (tag, weight)
    for mode in ("line", "word"):
        assert validate_edit(at(default_edit(T, TR), ("captions", "mode"), mode), T, TR) == []


def test_unspoken_sentence_not_required():
    T2, TR2 = transcript_and_trims(THREE, asr="第一句話第三句話")
    assert [s["id"] for s in default_edit(T2, TR2)["sentences"]] == ["s01", "s03"]
    assert validate_edit(default_edit(T2, TR2), T2, TR2) == []


def test_removing_everything_rejected(T, TR):
    e = default_edit(T, TR)
    e["cuts"]["extra"] = [{"from": "s01", "to": "s04", "reason": "x"}]
    e["sentences"] = []
    assert any("nothing" in x for x in validate_edit(e, T, TR))
    assert validate_edit(e, T, TR) == ["cuts.extra: nothing is left, every spoken sentence is removed"]


def test_nothing_left_when_no_sentence_was_spoken():
    T2, TR2 = transcript_and_trims("第一句話。", asr="")
    assert validate_edit(default_edit(T2, TR2), T2, TR2) == ["sentences: nothing is left, no sentence was spoken"]


def test_nothing_left_when_every_piece_is_too_short():
    edit, transcript, trims = docs({"s01": (1.0, 2.0)}, [("t01", 0.9, 2.1)])
    edit |= {"version": 1, "style": "default", "sentences": [{"id": "s01", "sem": "claim", "weight": "main"}],
             "captions": {"mode": "line"}}
    assert build_segments(edit, transcript, trims) == []
    assert any("nothing" in x for x in validate_edit(edit, transcript, trims))


def test_a_sentence_that_has_no_cut_points_is_an_error(T, TR):
    del TR["bounds"]["s03"]
    errs = validate_edit(default_edit(T, TR), T, TR)
    assert errs == ["sentences: s03 has no cut points in trim.json (run trim again)"]


def test_all_errors_are_reported_not_just_the_first(T, TR):
    e = default_edit(T, TR) | {"version": 3, "foo": 1}
    e["cuts"]["accept"].append("t99")
    e["cuts"]["extra"] = [extra("s09", "s09")]
    e["sentences"][0]["sem"] = "funny"
    e["captions"]["mode"] = "karaoke"
    errs = validate_edit(e, T, TR)
    for start in ("foo:", "version:", "cuts.accept[", "cuts.extra[0].from:", "sentences[0].sem:", "captions.mode:"):
        assert any(x.startswith(start) for x in errs), start


def test_every_error_reads_path_colon_problem(T, TR):
    e = default_edit(T, TR) | {"version": 3, "foo": 1, "scenes": []}
    e["cuts"]["accept"] = [1, "t99"]
    e["cuts"]["extra"] = [extra("s03", "s02"), "s01", {}]
    e["sentences"][0]["sem"] = "funny"
    e["sentences"].append(5)
    e["captions"] = {"mode": "karaoke", "x": 1}
    errs = validate_edit(e, T, TR)
    assert len(errs) >= 10
    for error in errs:
        assert re.fullmatch(r"[A-Za-z_]\w*(\.\w+|\[\d+\])*: \S.*", error), error


@pytest.mark.parametrize("key, shown", [("", "''"), ("a\nb", "'a\\nb'"), ("a b ", "'a b '"), ("tab\t", "'tab\\t'")])
def test_error_for_odd_keys_is_one_line_with_a_path(T, TR, key, shown):
    e = default_edit(T, TR)
    e["sentences"] = [s for s in e["sentences"] if s["id"] != "s02"]
    e["cuts"]["extra"] = [extra("s02", "s02") | {key: 1}]
    for target in (e, e["cuts"], e["sentences"][0], e["captions"]):
        target[key] = 1
    errs = validate_edit(e, T, TR)
    assert all("\n" not in x and "\t" not in x for x in errs), errs
    assert [x for x in errs if "unknown key" in x] == [
        f"edit[{shown}]: unknown key",
        f"cuts[{shown}]: unknown key",
        f"cuts.extra[0][{shown}]: unknown key",
        f"sentences[0][{shown}]: unknown key",
        f"captions[{shown}]: unknown key",
    ]


def test_a_key_of_ten_thousand_characters_is_cut_in_its_path(T, TR):
    errs = validate_edit(default_edit(T, TR) | {"y" * 10000: 1}, T, TR)
    assert errs == ["edit['" + "y" * 59 + "…]: unknown key"]


LONG = "x" * 10000


@pytest.mark.parametrize("path, value", [
    (("version",), LONG),
    (("version",), list(range(10000))),
    (("style",), LONG),
    (("cuts", "accept"), [LONG]),
    (("cuts", "extra"), [{"from": LONG, "to": "s02", "reason": "r"}]),
    (("cuts", "extra"), [{"from": "s02", "to": LONG, "reason": "r"}]),
    (("sentences", 0, "id"), LONG),
    (("sentences", 0, "sem"), LONG),
    (("sentences", 0, "weight"), LONG),
    (("captions", "mode"), LONG),
])
def test_long_value_is_truncated(T, TR, path, value):
    errs = validate_edit(at(default_edit(T, TR), path, value), T, TR)
    assert any("…" in x for x in errs), errs
    assert all(len(x) < 300 and "\n" not in x for x in errs), errs


@pytest.mark.parametrize("value, error", [
    ("y" * 58, "version: must be 1, got '" + "y" * 58 + "'"),  # a repr of 60 characters is kept whole
    ("y" * 59, "version: must be 1, got '" + "y" * 59 + "…"),  # one more and it is cut after 60
    (LONG, "version: must be 1, got '" + "x" * 59 + "…"),
])
def test_a_value_is_shown_as_its_repr_cut_after_sixty_characters(T, TR, value, error):
    assert validate_edit(at(default_edit(T, TR), ("version",), value), T, TR) == [error]


def test_a_style_whose_caption_box_leaves_the_safe_area_is_reported_as_that(T, TR, tmp_path, monkeypatch):
    """The style folder exists, so validate must not call it unknown: it says what load_style refuses it for."""
    edit = at(default_edit(T, TR), ("style",), "bad")
    document = json.loads((styles_root() / "default" / "style.json").read_text(encoding="utf-8"))
    document["captions"]["bottom_px"] = 1300
    (tmp_path / "styles" / "bad").mkdir(parents=True)
    (tmp_path / "styles" / "bad" / "style.json").write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setattr("pipeline.style.styles_root", lambda: tmp_path / "styles")
    assert validate_edit(edit, T, TR) == [
        "style bad: the caption line box (bottom_px 1300, line_height_px 90) is not inside the safe area"
    ]


@pytest.mark.parametrize("style", ["a\nb", "x" * 300, "a\x00b"])
def test_a_style_name_that_is_no_folder_is_one_short_line(T, TR, style):
    errs = validate_edit(at(default_edit(T, TR), ("style",), style), T, TR)
    assert len(errs) == 1 and errs[0].startswith("style: ") and "\n" not in errs[0] and len(errs[0]) < 120, errs


def test_values_in_errors_are_shown_as_their_repr(T, TR):
    e = default_edit(T, TR)
    e["cuts"]["accept"] = ["t99"]
    e["sentences"][0]["id"] = "s99"
    errs = validate_edit(e, T, TR)
    assert "cuts.accept[0]: unknown trim 't99'" in errs and "sentences[0].id: unknown sentence 's99'" in errs


@pytest.mark.parametrize("path, value, expected", [
    (("cuts",), "x", "cuts:"),
    (("cuts",), None, "cuts:"),
    (("cuts", "accept"), "t01", "cuts.accept:"),
    (("cuts", "accept"), None, "cuts.accept:"),
    (("cuts", "accept"), {"t01": 1}, "cuts.accept:"),
    (("cuts", "accept"), [1, None, ["t01"], {"a": 1}], "cuts.accept[0]:"),
    (("cuts", "reject"), "t03", "cuts.reject:"),
    (("cuts", "extra"), "s01", "cuts.extra:"),
    (("cuts", "extra"), {"from": "s01"}, "cuts.extra:"),
    (("cuts", "extra"), ["s01"], "cuts.extra[0]:"),
    (("cuts", "extra"), [None], "cuts.extra[0]:"),
    (("cuts", "extra"), [{"from": 1, "to": ["s02"], "reason": 3}], "cuts.extra[0].from:"),
    (("cuts", "extra"), [{"from": 1, "to": ["s02"], "reason": 3}], "cuts.extra[0].to:"),
    (("cuts", "extra"), [{"from": 1, "to": ["s02"], "reason": 3}], "cuts.extra[0].reason:"),
    (("cuts", "extra"), [{"from": {"s": 1}, "to": {"s": 1}, "reason": ["x"]}], "cuts.extra[0].from:"),
    (("sentences",), {}, "sentences:"),
    (("sentences",), "s01", "sentences:"),
    (("sentences",), None, "sentences:"),
    (("sentences",), ["s01", 2, None, [], {}], "sentences[0]:"),
    (("sentences", 0, "id"), 5, "sentences[0].id:"),
    (("sentences", 0, "id"), ["s01"], "sentences[0].id:"),
    (("sentences", 0, "id"), None, "sentences[0].id:"),
    (("sentences", 0, "sem"), ["hook"], "sentences[0].sem:"),
    (("sentences", 0, "sem"), None, "sentences[0].sem:"),
    (("sentences", 0, "sem"), 3, "sentences[0].sem:"),
    (("sentences", 0, "weight"), {"a": 1}, "sentences[0].weight:"),
    (("sentences", 0, "weight"), True, "sentences[0].weight:"),
    (("captions",), [], "captions:"),
    (("captions",), "line", "captions:"),
    (("captions",), None, "captions:"),
    (("captions", "mode"), None, "captions.mode:"),
    (("captions", "mode"), ["line"], "captions.mode:"),
    (("captions", "mode"), 1, "captions.mode:"),
    (("style",), 5, "style:"),
    (("version",), "1", "version:"),
])
def test_a_wrong_type_is_an_error_not_an_exception(T, TR, path, value, expected):
    errs = validate_edit(at(default_edit(T, TR), path, value), T, TR)
    assert any(x.startswith(expected) for x in errs), errs


@pytest.mark.parametrize("name", ["accept", "reject"])
def test_a_trim_list_of_the_wrong_type_is_the_only_error_about_it(T, TR, name):
    errs = validate_edit(at(default_edit(T, TR), ("cuts", name), None), T, TR)
    assert errs == [f"cuts.{name}: must be an array of trim ids, got null"]


@pytest.mark.parametrize("edit", [[], None, "edit", 3, [{"version": 1}]])
def test_an_edit_that_is_not_an_object_is_an_error(T, TR, edit):
    errs = validate_edit(edit, T, TR)
    assert len(errs) == 1 and errs[0].startswith("edit: must be an object")


def test_an_empty_edit_reports_every_missing_key(T, TR):
    errs = validate_edit({}, T, TR)
    for key in ("version", "style", "cuts", "sentences", "captions"):
        assert f"{key}: missing" in errs


def test_malformed_edits_do_not_break_the_helpers(T, TR):
    for bad in ({}, {"cuts": "x"}, {"cuts": {"extra": "x", "accept": 5}}, {"cuts": {"extra": [1, {"from": 3}]}}):
        assert kept_sentences(bad, T) == ["s01", "s02", "s03", "s04"]
        assert build_segments(bad, T, TR)[0].start == TR["bounds"]["s01"]["cut_in"]


def test_validation_does_not_change_its_inputs(T, TR):
    e = default_edit(T, TR)
    e["cuts"]["extra"] = [extra("s02", "s03")]
    e["sentences"][0]["sem"] = "funny"
    before = copy.deepcopy((e, T, TR))
    validate_edit(e, T, TR)
    build_segments(e, T, TR)
    kept_sentences(e, T)
    assert (e, T, TR) == before


JUNK = [None, True, 0, 1.5, "", "x", "s01", "s09", "t01", "t99", [], {}, ["s01"], [None], {"from": "s01"},
        [{"from": "s01", "to": "s02", "reason": "r"}], "line", "claim", float("nan")]


def _paths(node, prefix=()):
    """Every path below `node`, as tuples of keys and indexes."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield prefix + (key,)
            yield from _paths(value, prefix + (key,))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield prefix + (index,)
            yield from _paths(value, prefix + (index,))


def _damage(edit: dict, rng: random.Random) -> dict:
    """A copy of `edit` with one to three places replaced by junk, deleted, or given a new key."""
    edit = copy.deepcopy(edit)
    for _ in range(rng.choice([1, 1, 2, 3])):
        path = rng.choice(list(_paths(edit)))
        parent = edit
        for key in path[:-1]:
            parent = parent[key]
        action = rng.choice(["replace", "replace", "delete", "add"])
        if action == "replace":
            parent[path[-1]] = copy.deepcopy(rng.choice(JUNK))
        elif action == "delete":
            del parent[path[-1]]
        elif isinstance(parent, dict):
            parent[rng.choice(["foo", "scenes", "from", "reason"])] = copy.deepcopy(rng.choice(JUNK))
    return edit


def test_random_damage_never_raises_and_an_edit_that_passes_keeps_its_promises(T, TR):
    T2, TR2 = transcript_and_trims(FOUR, asr="第一句話第二句話第四句話")
    rng = random.Random(11)
    passed = 0
    for _ in range(3000):
        t, tr = rng.choice([(T, TR), (T2, TR2)])
        base = default_edit(t, tr)
        if rng.random() < 0.5:
            base["cuts"]["extra"] = [extra("s02", "s03")]
            base["sentences"] = [x for x in base["sentences"] if x["id"] not in ("s02", "s03")]
        edit = _damage(base, rng)
        before = repr(edit)
        errs = validate_edit(edit, t, tr)  # must not raise, whatever was damaged
        kept = kept_sentences(edit, t)
        segs = build_segments(edit, t, tr)
        assert repr(edit) == before
        assert all(re.fullmatch(r"[A-Za-z_]\w*(\.\w+|\[\d+\])*: \S.*", x) for x in errs), errs
        if not errs:
            passed += 1
            assert segs and sorted(x["id"] for x in edit["sentences"]) == sorted(kept)
    assert passed > 50  # the damage often lands somewhere harmless, so the check above is not empty


# ---- kept sentences ---------------------------------------------------------------------------


def test_kept_sentences_are_the_spoken_ones_outside_extra_in_script_order(T, TR):
    assert kept_sentences(default_edit(T, TR), T) == ["s01", "s02", "s03", "s04"]
    e = default_edit(T, TR)
    e["cuts"]["extra"] = [extra("s03", "s03"), extra("s01", "s02")]  # given out of order
    assert kept_sentences(e, T) == ["s04"]


def test_kept_sentences_leave_out_the_unspoken():
    T2, TR2 = transcript_and_trims(THREE, asr="第一句話第三句話")
    assert kept_sentences(default_edit(T2, TR2), T2) == ["s01", "s03"]


# ---- segments ---------------------------------------------------------------------------------


def test_segments_skip_accepted_and_keep_rejected(T, TR):
    e = default_edit(T, TR)
    segs = build_segments(e, T, TR)
    for t in TR["trims"]:
        mid = (t["start"] + t["end"]) / 2
        assert not any(s.start <= mid < s.end for s in segs)
    e["cuts"]["reject"] += e["cuts"]["accept"]
    e["cuts"]["accept"] = []
    assert len(build_segments(e, T, TR)) == 1


def test_meijia_segments_are_what_the_default_edit_keeps(T, TR):
    # The fixture's trims: pause + retake 2.8 to 5.6333, filler 9.9333 to 10.4667, pause + repeat 13.8333 to
    # 15.2; the reel runs from the first sentence's cut_in (0) to the last one's cut_out (17.0).
    assert frames(build_segments(default_edit(T, TR), T, TR)) == [(0, 84), (169, 298), (314, 415), (456, 510)]


def test_the_timeline_runs_from_the_first_kept_cut_in_to_the_last_kept_cut_out(T, TR):
    e = default_edit(T, TR)
    e["cuts"]["reject"] += e["cuts"]["accept"]
    e["cuts"]["accept"] = []
    e["cuts"]["extra"] = [extra("s01", "s01"), extra("s04", "s04")]
    (only,) = build_segments(e, T, TR)
    assert (only.start, only.end) == (TR["bounds"]["s02"]["cut_in"], TR["bounds"]["s03"]["cut_out"])


def test_extra_removes_sentence(T, TR):
    e = default_edit(T, TR)
    e["cuts"]["extra"] = [{"from": "s03", "to": "s03", "reason": "x"}]
    e["sentences"] = [s for s in e["sentences"] if s["id"] != "s03"]
    s3 = next(s for s in T["sentences"] if s["id"] == "s03")
    assert not any(s.start <= (s3["start"] + s3["end"]) / 2 < s.end for s in build_segments(e, T, TR))


def test_extra_runs_from_the_first_cut_in_to_the_last_cut_out(T, TR):
    e = default_edit(T, TR)
    e["cuts"]["reject"] += e["cuts"]["accept"]
    e["cuts"]["accept"] = []
    e["cuts"]["extra"] = [extra("s02", "s03")]
    segs = build_segments(e, T, TR)
    assert [(s.start, s.end) for s in segs] == [(TR["bounds"]["s01"]["cut_in"], TR["bounds"]["s02"]["cut_in"]),
                                                (TR["bounds"]["s03"]["cut_out"], TR["bounds"]["s04"]["cut_out"])]


def test_extra_over_an_unspoken_sentence_removes_it_with_the_rest():
    T2, TR2 = transcript_and_trims(FOUR, asr="第一句話第二句話第四句話")
    e = default_edit(T2, TR2)
    e["cuts"]["extra"] = [extra("s02", "s04")]
    e["sentences"] = [s for s in e["sentences"] if s["id"] == "s01"]
    (only,) = build_segments(e, T2, TR2)
    assert (only.start, only.end) == (TR2["bounds"]["s01"]["cut_in"], TR2["bounds"]["s02"]["cut_in"])


def test_an_unspoken_sentence_between_kept_ones_stays_in_the_reel():
    T2, TR2 = transcript_and_trims(THREE, asr="第一句話第三句話")
    e = default_edit(T2, TR2)
    e["cuts"]["reject"] += e["cuts"]["accept"]
    e["cuts"]["accept"] = []
    (only,) = build_segments(e, T2, TR2)
    assert (only.start, only.end) == (TR2["bounds"]["s01"]["cut_in"], TR2["bounds"]["s03"]["cut_out"])


def test_a_cut_point_inside_an_accepted_pause_is_harmless():
    # A 1.4 s pause: the pause trim keeps 0.175 s on each side, but the 0.3 s lead puts s01's cut_out and
    # s02's cut_in deeper inside it. The segments are the timeline minus the pause, nothing more.
    T2, TR2 = transcript_and_trims("第一句話。第二句話。", asr=[("第一句話", 0.0, 0.8), ("第二句話", 2.2, 3.0)])
    (pause,) = TR2["trims"]
    assert pause["kind"] == "pause" and pause["start"] < TR2["bounds"]["s02"]["cut_in"] < pause["end"]
    assert pause["start"] < TR2["bounds"]["s01"]["cut_out"] < pause["end"]
    segs = build_segments(default_edit(T2, TR2), T2, TR2)
    assert [(s.start, s.end) for s in segs] == [(TR2["bounds"]["s01"]["cut_in"], pause["start"]),
                                                (pause["end"], TR2["bounds"]["s02"]["cut_out"])]
    rejected = default_edit(T2, TR2)
    rejected["cuts"]["reject"], rejected["cuts"]["accept"] = rejected["cuts"]["accept"], []
    (whole,) = build_segments(rejected, T2, TR2)
    assert (whole.start, whole.end) == (TR2["bounds"]["s01"]["cut_in"], TR2["bounds"]["s02"]["cut_out"])


def test_an_accepted_pause_over_the_first_cut_in_moves_the_start():
    edit, transcript, trims = docs({"s01": (1.0, 2.0), "s02": (3.0, 4.0)}, [("t01", 0.5, 1.5)])
    assert frames(build_segments(edit, transcript, trims)) == [(45, 120)]  # starts where the pause ends


def test_accepted_trims_and_extra_ranges_are_removed_as_a_union():
    edit, transcript, trims = docs(
        {"s01": (0.0, 1.0), "s02": (2.0, 3.0), "s03": (4.0, 5.0), "s04": (6.0, 7.0)},
        [("t01", 1.5, 3.5), ("t02", 3.0, 4.0)],  # the two trims overlap each other and extra
        extra=[extra("s02", "s03")],  # 2.0 to 5.0
    )
    assert frames(build_segments(edit, transcript, trims)) == [(0, 45), (150, 210)]


def test_trims_outside_the_timeline_do_nothing():
    edit, transcript, trims = docs({"s01": (1.0, 2.0)}, [("t01", 0.0, 0.5), ("t02", 2.5, 3.0)])
    assert frames(build_segments(edit, transcript, trims)) == [(30, 60)]


def test_rejected_trims_are_kept():
    edit, transcript, trims = docs({"s01": (0.0, 3.0)}, [("t01", 1.0, 2.0)], accept=[])
    assert frames(build_segments(edit, transcript, trims)) == [(0, 90)]


def test_a_piece_shorter_than_two_frames_is_dropped():
    edit, transcript, trims = docs({"s01": (0.0, 1.0)}, [("t01", 1 / 30, 0.5)])  # a piece of one frame at 0
    assert frames(build_segments(edit, transcript, trims)) == [(15, 30)]
    edit, transcript, trims = docs({"s01": (0.0, 1.0)}, [("t01", 2 / 30, 0.5)])  # a piece of two frames at 0
    assert frames(build_segments(edit, transcript, trims)) == [(0, 2), (15, 30)]


def test_a_short_piece_at_the_end_is_dropped_too():
    edit, transcript, trims = docs({"s01": (0.0, 1.0)}, [("t01", 0.5, 29 / 30)])
    assert frames(build_segments(edit, transcript, trims)) == [(0, 15)]
    edit, transcript, trims = docs({"s01": (0.0, 1.0)}, [("t01", 0.5, 28 / 30)])
    assert frames(build_segments(edit, transcript, trims)) == [(0, 15), (28, 30)]


def test_segment_edges_stay_on_the_frame_grid_even_when_the_inputs_have_six_decimals():
    edit, transcript, trims = docs({"s01": (0.2, 6.0)}, [("t01", 3.4, 4.233333)])
    segs = build_segments(edit, transcript, trims)
    assert frames(segs) == [(6, 102), (127, 180)]
    assert all(abs(x * 30 - round(x * 30)) < 1e-9 for s in segs for x in (s.start, s.end))


def test_segments_on_grid_and_map_time(T, TR):
    segs = build_segments(default_edit(T, TR), T, TR)
    assert all(abs(x * 30 - round(x * 30)) < 1e-9 for s in segs for x in (s.start, s.end))
    assert map_time(segs[0].start, segs) == 0
    half = (segs[1].end - segs[1].start) / 2
    assert map_time(segs[1].start + half, segs) == pytest.approx(segs[0].end - segs[0].start + half)
    assert map_time((segs[0].end + segs[1].start) / 2, segs) == pytest.approx(segs[0].end - segs[0].start)
    ts = [x / 10 for x in range(0, 200)]
    assert [map_time(t, segs) for t in ts] == sorted(map_time(t, segs) for t in ts)


def test_segments_are_ordered_and_apart(T, TR):
    for edit in (default_edit(T, TR), default_edit(T, TR) | {"cuts": {"accept": [], "reject": [t["id"] for t in TR["trims"]]}}):
        segs = build_segments(edit, T, TR)
        assert all(a.end < b.start for a, b in zip(segs, segs[1:]))
        assert all(s.start < s.end for s in segs)


def test_no_kept_sentence_gives_no_segment(T, TR):
    e = default_edit(T, TR)
    e["cuts"]["extra"] = [extra("s01", "s04")]
    assert build_segments(e, T, TR) == []


def test_segment_is_a_frozen_pair():
    s = Segment(1.0, 2.0)
    assert (s.start, s.end) == (1.0, 2.0)
    with pytest.raises(AttributeError):
        s.start = 0.0


# ---- map_time ---------------------------------------------------------------------------------

SEGS = [Segment(1.0, 2.0), Segment(3.0, 3.5), Segment(5.0, 6.0)]


@pytest.mark.parametrize("t, expected", [
    (-1.0, 0.0), (0.0, 0.0), (0.99, 0.0), (1.0, 0.0),  # before the timeline and at its start
    (1.5, 0.5), (1.999, 0.999),  # inside the first segment
    (2.0, 1.0), (2.5, 1.0), (2.999, 1.0),  # in a cut: where the next segment begins
    (3.0, 1.0), (3.25, 1.25), (3.5, 1.5),  # second segment, then its end
    (4.0, 1.5), (4.999, 1.5), (5.0, 1.5), (5.5, 2.0), (6.0, 2.5),  # the cut, the last segment, its end
    (6.001, 2.5), (100.0, 2.5),  # after the timeline: the total length
])
def test_map_time_values(t, expected):
    assert map_time(t, SEGS) == pytest.approx(expected)


def test_map_time_is_exact_at_the_ends():
    segs = [Segment(a / 30, b / 30) for a, b in [(7, 58), (91, 133), (140, 311), (400, 401)]]
    assert map_time(segs[0].start, segs) == 0
    assert map_time(0.0, segs) == 0
    assert map_time(segs[-1].end, segs) == total_duration(segs)
    assert map_time(segs[-1].end + 5, segs) == total_duration(segs)
    assert total_duration(segs) == pytest.approx(sum(s.end - s.start for s in segs))


def test_map_time_is_continuous_at_the_edges_of_a_cut():
    segs = [Segment(a / 30, b / 30) for a, b in [(7, 58), (91, 133), (140, 311)]]
    for a, b in zip(segs, segs[1:]):
        assert map_time(a.end, segs) == map_time(b.start, segs)


def test_map_time_never_goes_backwards():
    segs = [Segment(a / 30, b / 30) for a, b in [(7, 58), (91, 133), (140, 311), (400, 401)]]
    ts = [x / 1000 for x in range(-500, 15000)]
    out = [map_time(t, segs) for t in ts]
    assert all(a <= b for a, b in zip(out, out[1:]))
    assert out[0] == 0 and out[-1] == total_duration(segs)


def test_map_time_without_segments_is_zero():
    assert map_time(3.0, []) == 0 and total_duration([]) == 0


# ---- load_edit, require_valid and the validate command ----------------------------------------


def test_load_edit_reads_the_json(tmp_path, T, TR):
    path = tmp_path / "edit.json"
    path.write_text(json.dumps(default_edit(T, TR), ensure_ascii=False), encoding="utf-8")
    assert load_edit(path) == default_edit(T, TR)


@pytest.mark.parametrize("text", ['{"version": 1,', "", "not json", '{"a": 1} {"b": 2}'])
def test_load_edit_reports_bad_json_as_an_invalid_edit(tmp_path, text):
    path = tmp_path / "edit.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(EditInvalid) as caught:
        load_edit(path)
    assert len(caught.value.errors) == 1 and caught.value.errors[0].startswith("edit.json: not valid JSON")


def test_load_edit_reports_bytes_that_are_not_text(tmp_path):
    path = tmp_path / "edit.json"
    path.write_bytes(b'{"style": "\xff\xfe"}')
    with pytest.raises(EditInvalid) as caught:
        load_edit(path)
    assert caught.value.errors[0].startswith("edit.json: not valid UTF-8")


@pytest.mark.parametrize("text", ["[]", "3", '"x"', "null"])
def test_load_edit_wants_an_object(tmp_path, text):
    path = tmp_path / "edit.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(EditInvalid) as caught:
        load_edit(path)
    assert caught.value.errors[0].startswith("edit.json: must be an object")


def test_deeply_nested_edit_is_invalid(tmp_path):
    path = tmp_path / "edit.json"
    path.write_text("[" * 100000, encoding="utf-8")
    with pytest.raises(EditInvalid) as caught:
        load_edit(path)
    assert len(caught.value.errors) == 1 and caught.value.errors[0].startswith("edit.json: not valid JSON")


def test_huge_integer_is_invalid(tmp_path):
    path = tmp_path / "edit.json"
    path.write_text('{"version": ' + "9" * 5000 + "}", encoding="utf-8")
    with pytest.raises(EditInvalid) as caught:
        load_edit(path)
    assert len(caught.value.errors) == 1 and caught.value.errors[0].startswith("edit.json: not valid JSON")
    assert len(caught.value.errors[0]) < 200


def test_a_document_as_deep_as_the_parser_takes_is_read_without_recursion(tmp_path):
    path = tmp_path / "edit.json"
    path.write_text('{"version": ' + "[" * 5000 + "]" * 5000 + "}", encoding="utf-8")
    data = load_edit(path)
    depth, node = 0, data["version"]
    while node:
        depth, node = depth + 1, node[0]
    assert depth == 4999


def duplicate_errors(tmp_path, text):
    path = tmp_path / "edit.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(EditInvalid) as caught:
        load_edit(path)
    return caught.value.errors


def test_duplicate_key_is_invalid(tmp_path, T, TR):
    text = json.dumps(default_edit(T, TR), ensure_ascii=False)[:-1] + ', "cuts": {}}'
    assert duplicate_errors(tmp_path, text) == ["cuts: duplicate key 'cuts'"]


@pytest.mark.parametrize("old, new, error", [
    ('"mode": "line"', '"mode": "line", "mode": "word"', "captions.mode: duplicate key 'mode'"),
    ('"reject"', '"accept": [], "reject"', "cuts.accept: duplicate key 'accept'"),
    ('"sem": "claim"', '"sem": "claim", "sem": "hook"', "sentences[0].sem: duplicate key 'sem'"),
    ('{"mode"', '{"x": {"y": 1, "y": 2}, "mode"', "captions.x.y: duplicate key 'y'"),
])
def test_a_duplicate_key_in_a_nested_object_names_its_path(tmp_path, T, TR, old, new, error):
    text = json.dumps(default_edit(T, TR), ensure_ascii=False)
    assert old in text
    assert duplicate_errors(tmp_path, text.replace(old, new, 1)) == [error]


def test_every_repeated_key_is_reported_once_in_the_order_of_the_file(tmp_path):
    text = '{"b": 1, "a": 2, "b": 3, "b": 4, "c": {"d": 1, "d": 2}, "a": 5}'
    assert duplicate_errors(tmp_path, text) == [
        "b: duplicate key 'b'", "a: duplicate key 'a'", "c.d: duplicate key 'd'"
    ]


def test_a_duplicate_inside_a_repeated_object_is_reported_too(tmp_path):
    text = '{"cuts": {"x": 1, "x": 2}, "cuts": {"y": 1}}'
    assert duplicate_errors(tmp_path, text) == ["cuts: duplicate key 'cuts'", "cuts.x: duplicate key 'x'"]


@pytest.mark.parametrize("key, shown", [("", "''"), ("a\nb", "'a\\nb'")])
def test_a_duplicate_of_an_odd_key_is_one_line(tmp_path, key, shown):
    text = "{" + f"{json.dumps(key)}: 1, {json.dumps(key)}: 2" + "}"
    assert duplicate_errors(tmp_path, text) == [f"edit[{shown}]: duplicate key {shown}"]


def test_a_document_without_duplicates_reads_as_it_did_with_its_key_order(tmp_path):
    path = tmp_path / "edit.json"
    path.write_text('{"b": [1, {"d": null, "c": 2.5}], "a": "x"}', encoding="utf-8")
    data = load_edit(path)
    assert data == {"b": [1, {"d": None, "c": 2.5}], "a": "x"}
    assert list(data) == ["b", "a"] and list(data["b"][1]) == ["d", "c"]
    assert type(data) is dict and type(data["b"]) is list and type(data["b"][1]) is dict


def test_load_edit_of_a_missing_file_is_an_execution_error_not_an_invalid_edit(tmp_path):
    with pytest.raises(PipelineError) as caught:
        load_edit(tmp_path / "edit.json")
    assert not isinstance(caught.value, EditInvalid) and "edit.json" in caught.value.message


def _documents(p):
    transcript = json.loads((p.work / "transcript.json").read_text(encoding="utf-8"))
    trims = json.loads((p.work / "trim.json").read_text(encoding="utf-8"))
    return transcript, trims


def _write_edit(p, edit):
    p.edit_path.write_text(json.dumps(edit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _set_version(path, version):
    document = json.loads(path.read_text(encoding="utf-8"))
    document["version"] = version
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def test_require_valid_refuses_a_transcript_of_an_older_version(edited_project):
    _set_version(edited_project.work / "transcript.json", 1)
    with pytest.raises(PipelineError) as refused:
        require_valid(edited_project)
    assert refused.value.message == "transcript.json was made by an older pipeline: run transcribe again"


def test_require_valid_refuses_a_trim_json_of_an_older_version(edited_project):
    _set_version(edited_project.work / "trim.json", 1)
    with pytest.raises(PipelineError) as refused:
        require_valid(edited_project)
    assert refused.value.message == "trim.json was made by an older pipeline: run trim again"


def test_load_document_checks_the_version_only_when_it_is_given_and_the_document_has_one(tmp_path):
    path = tmp_path / "doc.json"
    path.write_text(json.dumps({"version": 2, "items": []}), encoding="utf-8")
    assert load_document(path, "trim", ("items",), version=2) == {"version": 2, "items": []}
    assert load_document(path, "trim", ("items",)) == {"version": 2, "items": []}  # no version asked for
    with pytest.raises(PipelineError, match="doc.json was made by an older pipeline: run trim again"):
        load_document(path, "trim", ("items",), version=3)
    path.write_text(json.dumps({"items": []}), encoding="utf-8")  # a hand-written document has no version
    assert load_document(path, "trim", ("items",), version=3) == {"items": []}


def test_require_valid_returns_edit_transcript_and_trims(trimmed_project):
    p = trimmed_project
    transcript, trims = _documents(p)
    edit = default_edit(transcript, trims)
    _write_edit(p, edit)
    assert require_valid(p) == (edit, transcript, trims)


def test_require_valid_raises_with_every_error(trimmed_project):
    p = trimmed_project
    transcript, trims = _documents(p)
    edit = default_edit(transcript, trims) | {"scenes": [], "version": 9}
    edit["captions"]["mode"] = "karaoke"
    _write_edit(p, edit)
    with pytest.raises(EditInvalid) as caught:
        require_valid(p)
    assert caught.value.errors == validate_edit(edit, transcript, trims)
    assert len(caught.value.errors) == 3


def test_require_valid_reports_a_duplicate_key_together_with_the_other_errors(trimmed_project):
    p = trimmed_project
    transcript, trims = _documents(p)
    text = json.dumps(default_edit(transcript, trims) | {"version": 9}, ensure_ascii=False)
    p.edit_path.write_text(text[:-1] + ', "captions": {"mode": "line", "mode": "word"}}', encoding="utf-8")
    with pytest.raises(EditInvalid) as caught:
        require_valid(p)
    assert caught.value.errors[:2] == ["captions: duplicate key 'captions'", "captions.mode: duplicate key 'mode'"]
    assert "version: must be 1, got 9" in caught.value.errors


def test_require_valid_without_an_edit_names_the_file(trimmed_project):
    with pytest.raises(PipelineError) as caught:
        require_valid(trimmed_project)
    assert not isinstance(caught.value, EditInvalid) and "edit.json" in caught.value.message


@pytest.mark.parametrize("name", ["transcript.json", "trim.json"])
def test_require_valid_without_the_other_documents_names_the_file(trimmed_project, name):
    p = trimmed_project
    transcript, trims = _documents(p)
    _write_edit(p, default_edit(transcript, trims))
    (p.work / name).write_text("{ not json", encoding="utf-8")
    with pytest.raises(PipelineError) as caught:
        require_valid(p)
    assert not isinstance(caught.value, EditInvalid) and name in caught.value.message
    (p.work / name).unlink()
    with pytest.raises(PipelineError) as caught:
        require_valid(p)
    assert name in caught.value.message


@pytest.mark.parametrize("name, key", [("transcript.json", "sentences"), ("trim.json", "trims"),
                                       ("trim.json", "bounds")])
def test_require_valid_names_a_document_that_lacks_its_main_key(trimmed_project, name, key):
    p = trimmed_project
    transcript, trims = _documents(p)
    _write_edit(p, default_edit(transcript, trims))
    document = json.loads((p.work / name).read_text(encoding="utf-8"))
    del document[key]
    (p.work / name).write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(PipelineError) as caught:
        require_valid(p)
    assert not isinstance(caught.value, EditInvalid) and name in caught.value.message and key in caught.value.message


def test_validate_command_reports_segments_duration_and_kept_sentences(trimmed_project, capsys):
    p = trimmed_project
    transcript, trims = _documents(p)
    edit = default_edit(transcript, trims)
    _write_edit(p, edit)
    capsys.readouterr()
    assert main(["validate", p.name]) == 0
    out = json.loads(capsys.readouterr().out)
    segs = build_segments(edit, transcript, trims)
    assert out == {"ok": True, "segments": len(segs), "duration_s": round(total_duration(segs), 3), "kept_sentences": 4}
    assert out["segments"] == 4 and 0 < out["duration_s"] < transcript["audio_s"]


def test_validate_command_counts_what_extra_removes(trimmed_project, capsys):
    p = trimmed_project
    transcript, trims = _documents(p)
    edit = default_edit(transcript, trims)
    edit["cuts"]["extra"] = [extra("s02", "s03")]
    edit["sentences"] = [s for s in edit["sentences"] if s["id"] in ("s01", "s04")]
    _write_edit(p, edit)
    capsys.readouterr()
    assert main(["validate", p.name]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["kept_sentences"] == 2 and out["segments"] == len(build_segments(edit, transcript, trims))


def test_validate_command_fails_with_exit_code_2_and_every_error(trimmed_project, capsys):
    p = trimmed_project
    transcript, trims = _documents(p)
    edit = default_edit(transcript, trims) | {"broll": []}
    edit["sentences"][0]["sem"] = "funny"
    edit["cuts"]["accept"].append("t99")
    _write_edit(p, edit)
    capsys.readouterr()
    assert main(["validate", p.name]) == 2
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and out["errors"] == validate_edit(edit, transcript, trims)
    assert len(out["errors"]) == 3 and any("phase 2" in x for x in out["errors"])


def test_validate_command_without_an_edit_is_an_execution_error(trimmed_project, capsys):
    capsys.readouterr()
    assert main(["validate", trimmed_project.name]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and "edit.json" in out["error"] and "errors" not in out


def test_validate_command_for_an_unknown_project_is_an_execution_error(capsys):
    assert main(["validate", "no-such-project"]) == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False


# ---- validate records what it checked edit.json against ----------------------------------------


def _validate(p, capsys):
    """(exit code, the JSON line) of the validate command."""
    capsys.readouterr()
    code = main(["validate", p.name])
    return code, json.loads(capsys.readouterr().out)


def _edit_checked(p):
    return {
        "edit": file_sha256(p.edit_path),
        "transcript": file_sha256(p.work / "transcript.json"),
        "trim": file_sha256(p.work / "trim.json"),
    }


def _change_trim_json(p):
    """A harmless change to trim.json: the edit that was checked against the old one still passes."""
    trims = json.loads((p.work / "trim.json").read_text(encoding="utf-8"))
    trims["warnings"] = [*trims.get("warnings", []), "few pauses: quiet-point detection unreliable"]
    (p.work / "trim.json").write_text(json.dumps(trims, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


UNCHANGED_EDIT_WARNING = (
    "edit.json is unchanged since it was checked against an older transcript.json or trim.json: "
    "trim and sentence ids may now point at different candidates"
)


def test_validate_records_edit_checked(edited_project, capsys):
    p = edited_project
    assert "edit_checked" not in load_state(p)
    assert _validate(p, capsys)[0] == 0
    recorded = load_state(p)["edit_checked"]
    assert recorded == _edit_checked(p)
    assert all(re.fullmatch(r"[0-9a-f]{64}", sha) for sha in recorded.values())


def test_a_validate_that_fails_records_nothing_and_keeps_the_earlier_record(edited_project, capsys):
    p = edited_project
    assert _validate(p, capsys)[0] == 0
    before = load_state(p)["edit_checked"]
    edit = json.loads(p.edit_path.read_text(encoding="utf-8"))
    edit["sentences"] = []
    _write_edit(p, edit)
    assert _validate(p, capsys)[0] == 2
    assert load_state(p)["edit_checked"] == before


def test_a_validate_that_fails_on_a_project_never_checked_records_nothing(edited_project, capsys):
    p = edited_project
    _write_edit(p, {"version": 1})
    assert _validate(p, capsys)[0] == 2
    assert "edit_checked" not in load_state(p)


def test_next_is_validate_after_edit_changes(edited_project, capsys):
    p = edited_project
    assert next_step(p) == "validate"  # edit.json was never checked
    assert _validate(p, capsys)[0] == 0
    assert next_step(p) == "storyboard"
    edit = json.loads(p.edit_path.read_text(encoding="utf-8"))
    edit["cuts"]["accept"].remove("t03")
    edit["cuts"]["reject"].append("t03")
    _write_edit(p, edit)
    assert next_step(p) == "validate"
    assert _validate(p, capsys)[0] == 0
    assert next_step(p) == "storyboard"


def test_next_is_edit_after_trim_changes(edited_project, capsys):
    p = edited_project
    assert _validate(p, capsys)[0] == 0
    _change_trim_json(p)
    assert next_step(p) == "edit"
    assert _validate(p, capsys)[0] == 0  # the director checked the ids and ran validate again
    assert next_step(p) == "storyboard"


def test_validate_warns_when_edit_is_unchanged_but_trim_changed(edited_project, capsys):
    p = edited_project
    code, first = _validate(p, capsys)
    assert code == 0 and "warnings" not in first
    _change_trim_json(p)
    code, out = _validate(p, capsys)
    assert code == 0 and out["ok"] is True
    assert out["warnings"] == [UNCHANGED_EDIT_WARNING]
    assert load_state(p)["edit_checked"] == _edit_checked(p)  # the check is recorded all the same
    code, again = _validate(p, capsys)  # nothing changed since: nothing to warn about
    assert code == 0 and "warnings" not in again


def test_validate_does_not_warn_when_edit_changed_with_the_trim(edited_project, capsys):
    p = edited_project
    assert _validate(p, capsys)[0] == 0
    _change_trim_json(p)
    edit = json.loads(p.edit_path.read_text(encoding="utf-8"))
    edit["cuts"]["accept"].remove("t03")
    edit["cuts"]["reject"].append("t03")
    _write_edit(p, edit)
    code, out = _validate(p, capsys)
    assert code == 0 and "warnings" not in out


def test_validate_does_not_warn_without_an_earlier_record(edited_project, capsys):
    p = edited_project
    _change_trim_json(p)  # a project from before edit_checked, or one never validated: nothing to compare with
    code, out = _validate(p, capsys)
    assert code == 0 and "warnings" not in out


def test_validate_treats_a_malformed_record_like_a_missing_one(edited_project, capsys):
    p = edited_project
    state = load_state(p)
    state["edit_checked"] = {"edit": file_sha256(p.edit_path), "transcript": 7}
    save_state(p, state)
    code, out = _validate(p, capsys)
    assert code == 0 and "warnings" not in out
    assert load_state(p)["edit_checked"] == _edit_checked(p)
