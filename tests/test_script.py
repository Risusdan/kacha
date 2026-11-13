"""Tests for pipeline.script: sentence splitting, markers, heading and list marks, Markdown marks."""

import pytest

from pipeline.script import Marker, Sentence, markdown_marks, parse_script, strip_markdown


def test_sentences_and_ids():
    ss, _ = parse_script("今天，我們來聊 Reels！\n第二句。第三句？")
    assert [s.id for s in ss] == ["s01", "s02", "s03"] and ss[0].text == "今天，我們來聊 Reels！"


def test_markers_attach_to_next_sentence():
    ss, ms = parse_script("[B-roll: 咖啡店]今天開店。\n（這裡放咖啡店外觀）\n第二句。")
    assert ss[0].text == "今天開店。" and [(m.before, m.text) for m in ms] == [
        ("s01", "[B-roll: 咖啡店]"), ("s02", "（這裡放咖啡店外觀）")]


def test_heading_marks_removed():
    ss, _ = parse_script("# 標題\n內容。")
    assert [s.text for s in ss] == ["標題", "內容。"]


def test_unit_spans_point_into_sentence_text():
    s = parse_script("我們用 Remotion 做動畫。")[0][0]
    assert [s.text[a:b] for a, b in (u.span for u in s.units)] == ["我", "們", "用", "Remotion", "做", "動", "畫"]


def test_sentence_and_marker_are_plain_dataclasses():
    ss, ms = parse_script("[x]好。")
    assert ss == [Sentence("s01", "好。", ss[0].units)] and ms == [Marker("s01", "[x]")]


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_every_newline_style_breaks_sentences(newline):
    ss, _ = parse_script(f"第一行{newline}第二行{newline}第三句。")
    assert [s.text for s in ss] == ["第一行", "第二行", "第三句。"]


def test_blank_lines_and_crlf_do_not_leave_carriage_returns():
    ss, _ = parse_script("甲。\r\n\r\n乙。\r\n")
    assert [s.text for s in ss] == ["甲。", "乙。"]
    assert not any("\r" in s.text or "\n" in s.text for s in ss)


@pytest.mark.parametrize("text,expected", [
    ("真的嗎!是的?好。", ["真的嗎!", "是的?", "好。"]),
    ("真的嗎！是的？好。", ["真的嗎！", "是的？", "好。"]),
    ("Hello! Are you there? Yes.", ["Hello!", "Are you there?", "Yes."]),
])
def test_both_width_end_marks_end_a_sentence(text, expected):
    assert [s.text for s in parse_script(text)[0]] == expected


@pytest.mark.parametrize("text,expected", [
    ("他說：「好。」然後走了。", ["他說：「好。」", "然後走了。"]),
    ("他說：『好！』然後走了。", ["他說：『好！』", "然後走了。"]),
    ('他說："好。"然後走了。', ['他說："好。"', "然後走了。"]),
    ("他說：“好？”然後走了。", ["他說：“好？”", "然後走了。"]),
])
def test_closing_quote_after_the_end_mark_stays_with_the_sentence(text, expected):
    assert [s.text for s in parse_script(text)[0]] == expected


def test_runs_of_end_marks_stay_in_one_sentence():
    assert [s.text for s in parse_script("什麼？！真的。")[0]] == ["什麼？！", "真的。"]
    assert [s.text for s in parse_script("太好了!!!下一句。")[0]] == ["太好了!!!", "下一句。"]


def test_decimal_points_and_commas_do_not_end_sentences():
    ss, _ = parse_script("一共 3.5 倍，很多.")
    assert [s.text for s in ss] == ["一共 3.5 倍，很多."]


def test_empty_sentences_are_dropped():
    ss, ms = parse_script("。。\n！\n---\n   \n正文。\n……\n")
    assert [(s.id, s.text) for s in ss] == [("s01", "正文。")] and ms == []


def test_empty_script_has_nothing():
    assert parse_script("") == ([], [])
    assert parse_script("\n\r\n  \n") == ([], [])


def test_sentence_ids_pass_s99_without_wrapping():
    ss, _ = parse_script("\n".join(f"第{i}句。" for i in range(1, 106)))
    assert ss[0].id == "s01" and ss[98].id == "s99" and ss[99].id == "s100" and ss[104].id == "s105"
    assert len({s.id for s in ss}) == 105


def test_marker_after_the_last_sentence_has_no_next_sentence():
    ss, ms = parse_script("第一句。\n（結尾放 logo）\n[片尾]")
    assert [s.id for s in ss] == ["s01"]
    assert [(m.before, m.text) for m in ms] == [(None, "（結尾放 logo）"), (None, "[片尾]")]


def test_script_with_only_markers():
    ss, ms = parse_script("[只有標註]")
    assert ss == [] and ms == [Marker(None, "[只有標註]")]


def test_fullwidth_bracket_markers_and_half_width_parentheses_lines():
    ss, ms = parse_script("【畫面：咖啡機】煮咖啡。\n(停頓一下)\n好喝。")
    assert [s.text for s in ss] == ["煮咖啡。", "好喝。"]
    assert [(m.before, m.text) for m in ms] == [("s01", "【畫面：咖啡機】"), ("s02", "(停頓一下)")]


def test_several_markers_before_one_sentence_keep_their_order():
    ss, ms = parse_script("[甲]\n（乙）\n[丙]今天。")
    assert [(m.before, m.text) for m in ms] == [("s01", "[甲]"), ("s01", "（乙）"), ("s01", "[丙]")]


def test_marker_between_two_sentences_on_one_line_belongs_to_the_second():
    ss, ms = parse_script("第一句。[B-roll: 杯子]第二句。")
    assert [s.text for s in ss] == ["第一句。", "第二句。"]
    assert [(m.before, m.text) for m in ms] == [("s02", "[B-roll: 杯子]")]


def test_marker_inside_a_sentence_leaves_the_text_whole_and_stays_with_that_sentence():
    ss, ms = parse_script("今天[B-roll: 杯子]開店。第二句。")
    assert [s.text for s in ss] == ["今天開店。", "第二句。"]
    assert [(m.before, m.text) for m in ms] == [("s01", "[B-roll: 杯子]")]
    assert [ss[0].text[a:b] for a, b in (u.span for u in ss[0].units)] == ["今", "天", "開", "店"]


def test_parentheses_inside_a_line_are_ordinary_text():
    ss, ms = parse_script("咖啡（coffee）很好喝。\n(笑)真的。")
    assert [s.text for s in ss] == ["咖啡（coffee）很好喝。", "(笑)真的。"] and ms == []


def test_parenthesis_line_must_be_one_group():
    ss, ms = parse_script("（甲）乙（丙）\n(a (b) c)")
    assert [s.text for s in ss] == ["（甲）乙（丙）"]
    assert [(m.before, m.text) for m in ms] == [(None, "(a (b) c)")]


def test_marker_line_ignores_surrounding_whitespace():
    _, ms = parse_script("  （放圖）  \n好。")
    assert [m.text for m in ms] == ["（放圖）"]


def test_heading_marks_at_any_level_and_indent():
    ss, _ = parse_script("## 小標\n###### 最小\n  # 縮排\n#\n內容。")
    assert [s.text for s in ss] == ["小標", "最小", "縮排", "內容。"]


def test_hashtag_without_space_is_not_a_heading():
    ss, _ = parse_script("#咖啡 很好。\n####### 七個不是標題。")
    assert [s.text for s in ss] == ["#咖啡 很好。", "####### 七個不是標題。"]


def test_heading_marker_is_removed_before_marker_detection():
    ss, ms = parse_script("## （這裡放標題圖）\n內容。")
    assert [s.text for s in ss] == ["內容。"] and [(m.before, m.text) for m in ms] == [("s01", "（這裡放標題圖）")]


def test_byte_order_mark_is_ignored():
    ss, _ = parse_script("\ufeff# 標題\n內容。")
    assert [s.text for s in ss] == ["標題", "內容。"]


def test_sentence_text_is_stripped_but_inner_text_kept():
    ss, _ = parse_script("   用 Remotion  做動畫。   \n　全形空白開頭。")
    assert [s.text for s in ss] == ["用 Remotion  做動畫。", "全形空白開頭。"]


def test_spans_survive_a_removed_marker_and_leading_space():
    ss, _ = parse_script("[x] 我們用 Remotion。")
    s = ss[0]
    assert s.text == "我們用 Remotion。"
    assert [s.text[a:b] for a, b in (u.span for u in s.units)] == ["我", "們", "用", "Remotion"]


@pytest.mark.parametrize("line,expected", [
    ("- 第一點。", "第一點。"),
    ("* 第三點。", "第三點。"),
    ("+ 第四點。", "第四點。"),
    ("-\t第五點。", "第五點。"),
    ("  - 縮排的點。", "縮排的點。"),
    ("> 引用一句。", "引用一句。"),
    (">引用一句。", "引用一句。"),
    ("> > 兩層引用。", "兩層引用。"),
    (">> 兩層引用。", "兩層引用。"),
    ("1. 開場。", "開場。"),
    ("12) 開場。", "開場。"),
    ("123. 開場。", "開場。"),
    ("1.\t開場。", "開場。"),
    ("1、開場。", "開場。"),
    ("12、 開場。", "開場。"),
    ("- 1. 開場。", "開場。"),
    ("> - 1. 開場。", "開場。"),
    ("## 1. 標題", "標題"),
    ("# - 標題", "標題"),
])
def test_list_and_quote_marks_at_line_start_are_removed(line, expected):
    ss, _ = parse_script(line)
    assert [s.text for s in ss] == [expected]


@pytest.mark.parametrize("line", [
    "3 個方法。",
    "2026年開始。",
    "1.5 倍很多。",
    "1.開場。",
    "1)開場。",
    "1234. 開場。",
    "1234、開場。",
    "一、開場。",
    "二) 開場。",
    "-第一點。",
    "--- 分隔。",
    "１. 開場。",
    "開場 1. 第二。",
    "他說 - 好。",
])
def test_lines_without_a_list_or_quote_shape_are_kept(line):
    assert [s.text for s in parse_script(line)[0]] == [line]


def test_ordinal_is_not_a_phantom_unit_and_spans_follow_the_stripped_text():
    s = parse_script("1. 我們用 Remotion")[0][0]
    assert s.text == "我們用 Remotion"
    assert [u.text for u in s.units] == ["我", "們", "用", "remotion"]
    assert [s.text[a:b] for a, b in (u.span for u in s.units)] == ["我", "們", "用", "Remotion"]


def test_numbered_and_bulleted_lines_become_separate_sentences():
    ss, _ = parse_script("1. 第一\n2. 第二\n- 第三\n> 第四")
    assert [s.text for s in ss] == ["第一", "第二", "第三", "第四"]
    assert all(not any(u.kind == "num" for u in s.units) for s in ss)


def test_marker_after_a_list_mark_is_still_a_marker():
    ss, ms = parse_script("- （放圖）\n1. [B-roll]好。")
    assert [s.text for s in ss] == ["好。"]
    assert [(m.before, m.text) for m in ms] == [("s01", "（放圖）"), ("s01", "[B-roll]")]


@pytest.mark.parametrize("line", ["1.", "10)", "3、", "  12.  ", "- 1.", "> 2)", "## 3、"])
def test_bare_ordinal_line_is_not_a_sentence(line):
    assert parse_script(line) == ([], [])


def test_bare_ordinals_between_sentences_leave_no_phantom_sentence():
    ss, _ = parse_script("第一句。\n1.\n10)\n3、\n第二句。")
    assert [(s.id, s.text) for s in ss] == [("s01", "第一句。"), ("s02", "第二句。")]


@pytest.mark.parametrize("line,expected", [
    ("1、2、3 都是。", "1、2、3 都是。"),
    ("3、4 號", "3、4 號"),
    ("1、 2 個方法", "1、 2 個方法"),
    ("1、２、３ 都是。", "1、２、３ 都是。"),
    ("1、今天", "今天"),
    ("1. 今天", "今天"),
    ("1、 今天", "今天"),
    ("12、一二三", "一二三"),
    ("1. 2 個方法", "2 個方法"),
])
def test_number_list_in_text_is_kept(line, expected):
    assert [s.text for s in parse_script(line)[0]] == [expected]


def test_link_keeps_its_text_only():
    ss, ms = parse_script("看[這裡](https://example.com)。")
    assert [s.text for s in ss] == ["看這裡。"] and ms == []
    assert [ss[0].text[a:b] for a, b in (u.span for u in ss[0].units)] == ["看", "這", "裡"]
    assert not any(u.kind in ("latin", "num") for u in ss[0].units)


@pytest.mark.parametrize("line,expected", [
    ("[官網](https://example.com/a_b?x=1&y=2)有說明。", "官網有說明。"),
    ("[維基](https://example.com/Foo_(bar))看。", "維基看。"),
    ("先[甲](a)再[乙](b)。", "先甲再乙。"),
    ("[**重點**](https://example.com)在這。", "重點在這。"),
    ("[](https://example.com)好。", "好。"),
])
def test_links_of_every_shape_keep_their_text_only(line, expected):
    ss, ms = parse_script(line)
    assert [s.text for s in ss] == [expected] and ms == []


def test_a_note_without_a_target_is_still_a_note():
    ss, ms = parse_script("看[注意]這裡。\n[B-roll: 咖啡店] (停頓)好。")
    assert [s.text for s in ss] == ["看這裡。", "(停頓)好。"]
    assert [(m.before, m.text) for m in ms] == [("s01", "[注意]"), ("s02", "[B-roll: 咖啡店]")]


def test_link_and_note_on_one_line_each_keep_their_own_rule():
    ss, ms = parse_script("[B-roll]看[這裡](https://example.com)吧。")
    assert [s.text for s in ss] == ["看這裡吧。"] and [(m.before, m.text) for m in ms] == [("s01", "[B-roll]")]


def test_marks_inside_a_note_are_not_touched():
    ss, ms = parse_script("好[B-roll: **咖啡** 和 `杯子`]吧。")
    assert [s.text for s in ss] == ["好吧。"] and [m.text for m in ms] == ["[B-roll: **咖啡** 和 `杯子`]"]


@pytest.mark.parametrize("line,expected", [
    ("這是**重點**。", "這是重點。"),
    ("這是__重點__。", "這是重點。"),
    ("這是~~舊的~~新的。", "這是舊的新的。"),
    ("這是`code`。", "這是code。"),
    ("這*很重要*。", "這很重要。"),
    ("***x***", "x"),
    ("***很重要***", "很重要"),
    ("**重點** 開場。", "重點 開場。"),
    ("*強調* 開場。", "強調 開場。"),
    ("*a*", "a"),
    ("**一**和**二**。", "一和二。"),
    ("**第一句。第二句。**", "第一句。第二句。"),
    ("*a **b** c*", "a b c"),
    ("`a*b*c` 和 `x`", "a*b*c 和 x"),
    ("**好[B-roll]**。", "好。"),
])
def test_emphasis_marks_are_removed(line, expected):
    ss, _ = parse_script(line)
    assert "".join(s.text for s in ss) == expected


def test_a_pair_that_spans_sentences_leaves_clean_sentences():
    ss, _ = parse_script("**第一句。第二句。**")
    assert [s.text for s in ss] == ["第一句。", "第二句。"]


@pytest.mark.parametrize("line", [
    "5*3*2",
    "5*3 等於 15",
    "file_name",
    "3~5",
    "3～5",
    "一個 * 號",
    "2 * 3 * 4",
    "a * b",
    "*甲*b",  # the closing star is followed by an ASCII letter: not emphasis
    "a*甲*",
    "x __ y",
    "5 ** 3 ** 2",
    "一個 ` 符號 ` 在這",
    "__init",
])
def test_star_and_underscore_in_text_are_kept(line):
    assert [s.text for s in parse_script(line)[0]] == [line]


def test_spans_follow_the_text_after_marks_are_removed():
    s = parse_script("這是**重點**，看[這裡](https://example.com)用 `Remotion` 做。")[0][0]
    assert s.text == "這是重點，看這裡用 Remotion 做。"
    assert [s.text[a:b] for a, b in (u.span for u in s.units)] == [
        "這", "是", "重", "點", "看", "這", "裡", "用", "Remotion", "做"]


def test_markdown_marks_are_the_ranges_strip_markdown_removes():
    text = "**a**和[b](u)和*c*和`d`"
    ranges = markdown_marks(text)
    assert ranges == sorted(ranges) and all(lo < hi for lo, hi in ranges)
    assert all(a[1] <= b[0] for a, b in zip(ranges, ranges[1:]))
    kept = [c for i, c in enumerate(text) if not any(lo <= i < hi for lo, hi in ranges)]
    assert "".join(kept) == strip_markdown(text) == "a和b和c和d"


def test_strip_markdown_is_stable():
    for text in ("****x****", "*a **b** c*", "5*3*2", "***x***", "[a](u)*b*"):
        once = strip_markdown(text)
        assert strip_markdown(once) == once and markdown_marks(once) == []


@pytest.mark.parametrize("text", [
    "*", "**", "***", "****", "`", "``", "__", "~~", "a * b", "5*3*2", "[注意]", "[a]", "(u)",
])
def test_marks_with_no_partner_stay_as_written(text):
    assert strip_markdown(text) == text and markdown_marks(text) == []
