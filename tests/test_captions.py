"""Caption lines, event times on the cut reel, caption placement, ASS output, the glyph check.

FONT is the bold static font, the one the render uses. T and TR are the Meijia transcript and trims
(conftest); the timing tests that need exact numbers build their own sentences with timed_sentence."""

import copy
import itertools
import re

import cv2
import numpy as np
import pytest
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont
from helpers import (
    caption_dictionary,
    default_edit,
    event,
    line_of,
    static_font,
    timed_sentence,
)

from pipeline import captions
from pipeline.captions import (
    Event,
    Line,
    Piece,
    ass_color,
    ass_time,
    break_lines,
    build_events,
    caption_source,
    caption_top_px,
    missing_glyphs,
    remap_units,
    text_px,
    width_units,
    write_ass,
)
from pipeline.edit import Segment, build_segments, map_time, total_duration
from pipeline.errors import PipelineError
from pipeline.media import run_ffmpeg
from pipeline.script import parse_script
from pipeline.style import font_path, load_style
from pipeline.textnorm import split_units
from pipeline.words import word_ends

FONT = font_path(load_style(), "bold")
FRAME = 1 / 30
NO_CUTS = {"cuts": {"extra": []}}


@pytest.fixture(autouse=True)
def bold_font_is_there():
    """Every test here reads FONT, so a machine where `pipeline setup` has not run fails each with that hint."""
    static_font("bold")


def texts(text: str) -> list[str]:
    return [line.text for line in line_of(text)]


def unit_indexes(lines: list[Line]) -> list[int]:
    return [piece.unit for line in lines for piece in line.pieces if piece.unit is not None]


def break_raw(text: str) -> list[Line]:
    """break_lines of `text` with the units split_units finds, without parse_script's tidying."""
    units = [{"text": u.text, "span": list(u.span)} for u in split_units(text)]
    style = load_style()
    return break_lines(text, units, FONT, style)


def reel(sentences: list[dict], segments: list[Segment], style: dict | None = None) -> list[Event]:
    return build_events({"sentences": sentences}, NO_CUTS, segments, FONT, style or load_style())


def advance_em(char: str) -> float:
    """The advance of `char` in FONT, in em, read straight from the font file."""
    font = TTFont(FONT)
    return font["hmtx"][font.getBestCmap()[ord(char)]][0] / font["head"].unitsPerEm


def cell_em(font_file=FONT) -> float:
    """The cell height libass takes as the font size (hhea ascent less descent), in em, from the font file."""
    font = TTFont(font_file)
    return (font["hhea"].ascent - font["hhea"].descent) / font["head"].unitsPerEm


def tiny_font(path, ascent: int, descent: int):
    """A one-glyph font with 1000 units per em and the given hhea ascent and (negative) descent."""
    builder = FontBuilder(1000, isTTF=True)
    builder.setupGlyphOrder([".notdef"])
    builder.setupCharacterMap({})
    builder.setupGlyf({".notdef": TTGlyphPen(None).glyph()})
    builder.setupHorizontalMetrics({".notdef": (500, 0)})
    builder.setupHorizontalHeader(ascent=ascent, descent=descent)
    builder.setupNameTable({"familyName": "Tiny", "styleName": "Regular"})
    builder.setupOS2()
    builder.setupPost()
    builder.save(path)
    return path


LONG_WORD = "Pneumonoultramicroscopicsilicovolcanoconiosis"           # 45 letters: wider than a line
URL_SENTENCE = "網址是https://example.com/a/very/long/path/to/one/page/of/the/site"
SUPER = "supercalifragilisticexpialidocious"                         # 34 letters


def everywhere(text: str) -> frozenset[int]:
    """A stand-in for word_ends: a word ends between every two characters."""
    return frozenset(range(1, len(text)))


def fits(text: str) -> bool:
    """Whether `text` fits one caption line of the default style."""
    c = load_style()["captions"]
    limit = 950 - 2 * c["outline_px"]
    return width_units(text) <= c["max_chars_per_line"] and text_px(text, FONT, c["size_px"]) <= limit


def squares(lines: list[str]) -> float:
    """The sum of squared differences between the pixel widths of `lines` and their mean."""
    widths = [text_px(line, FONT, load_style()["captions"]["size_px"]) for line in lines]
    mean = sum(widths) / len(widths)
    return sum((width - mean) ** 2 for width in widths)


def parts_by_unit(lines: list[Line]) -> dict[int, list[tuple[float, float]]]:
    """The `part` of every piece that shows a unit, by unit, in order."""
    found: dict[int, list[tuple[float, float]]] = {}
    for line in lines:
        for piece in line.pieces:
            if piece.unit is not None:
                found.setdefault(piece.unit, []).append(piece.part)
    return found


def assert_parts_tile(lines: list[Line]) -> None:
    """The pieces of every unit take shares of its time that start at 0, meet one another and end at 1."""
    for unit, parts in parts_by_unit(lines).items():
        assert parts[0][0] == 0 and parts[-1][1] == 1, (unit, parts)
        assert all(a[1] == b[0] for a, b in zip(parts, parts[1:])), (unit, parts)
        assert sum(hi - lo for lo, hi in parts) == pytest.approx(1), (unit, parts)


def best_breakings(text: str, ends: frozenset[int]) -> set[tuple[str, ...]]:
    """The line texts of every best breaking of the one script sentence `text`, found by trying each set of
    the places a line may break (words end at `ends`; every character boundary inside a stretch too wide
    for a line), fewest breaks first: then the fewest short lines, the fewest breaks that are not
    punctuation breaks, and the least sum of squared differences from the mean width."""
    (sentence,), _ = parse_script(text)
    units = [{"text": u.text, "span": list(u.span)} for u in sentence.units]
    words = captions._tidy(captions._tokens(sentence.text, units))
    limits = captions._limits(FONT, load_style())
    # one token per character, the places a line may break as character indexes, and a break that is not
    # a punctuation break between every two characters shown inside a stretch too wide for a line
    toks = [captions._Tok(Piece(c, t.piece.unit), t.kind, t.pos) for t in words for c in t.piece.text]
    starts = list(itertools.accumulate((len(t.piece.text) for t in words), initial=0))
    word_breaks = captions._breaks(words, ends)
    breaks = {starts[k]: punctuation for k, punctuation in word_breaks.items()}
    bounds = [0, *sorted(word_breaks), len(words)]
    for a, b in zip(bounds, bounds[1:]):
        if not limits.fits(words[a:b]):
            lo, hi = captions._shown_bounds(words[a:b])
            breaks.update(dict.fromkeys(range(starts[a + lo] + 1, starts[a + hi]), False))

    def shown(part) -> str:
        return "".join(tok.piece.text for tok in captions._trim(part))

    whole_is_short = width_units(shown(toks)) <= 2
    for count in range(len(breaks) + 1):
        found = []
        for chosen in itertools.combinations(sorted(breaks), count):
            bounds = [0, *chosen, len(toks)]
            parts = [toks[a:b] for a, b in zip(bounds, bounds[1:])]
            if not all(limits.fits(part) for part in parts):
                continue
            lines = tuple(shown(part) for part in parts)
            short = 0 if whole_is_short else sum(width_units(line) <= 2 for line in lines)
            found.append(((short, sum(not breaks[k] for k in chosen), squares(list(lines))), lines))
        if found:
            best = min(cost for cost, _ in found)
            return {lines for cost, lines in found if cost[:2] == best[:2] and cost[2] <= best[2] + 1e-6}
    raise AssertionError(f"no breaking of {text!r} fits")


# ---- line breaking ----------------------------------------------------------------------------


def test_trailing_punct_removed_one_line():
    assert [l.text for l in line_of("今天我們來聊短影音剪輯。")] == ["今天我們來聊短影音剪輯"]


def test_long_sentence_breaks_at_comma():
    assert [l.text for l in line_of("第一步是把多餘的停頓剪掉，這樣影片節奏才會變快很多。")] == [
        "第一步是把多餘的停頓剪掉", "這樣影片節奏才會變快很多"]


def test_short_phrases_packed_with_ideographic_space():
    assert [l.text for l in line_of("好，我們開始。")] == ["好　我們開始"]


def test_balanced_split_without_punct():
    assert [len(l.text) for l in line_of("一二三四五六七八九十一二三四五六七八九十")] == [10, 10]


def test_never_split_latin_or_number():
    lines = [l.text for l in line_of("我們今天要用 Remotion 和 HyperFrames 做出 2026 年的動畫")]
    assert len(lines) == 2
    for word in ("Remotion", "HyperFrames", "2026"):
        assert sum(word in l for l in lines) == 1


def test_line_limits_by_count_and_pixels():
    style = load_style()
    for l in line_of("這是一個非常非常長而且完全沒有任何標點符號的句子用來測試斷行有沒有超過限制"):
        c = style["captions"]
        assert width_units(l.text) <= 16 and text_px(l.text, FONT, c["size_px"]) <= 950 - 2 * c["outline_px"]


def test_inner_separators_become_ideographic_space():
    assert texts("好、我們；開始：吧。") == ["好　我們　開始　吧"]


def test_question_exclamation_and_quotes_are_kept():
    assert texts("他說「真的嗎？」") == ["他說「真的嗎？」"]
    assert texts("太好了！") == ["太好了！"]
    assert texts("《短影音》和『剪輯』") == ["《短影音》和『剪輯』"]
    assert texts("真的嗎？！") == ["真的嗎？！"]


def test_separator_inside_a_closing_quote_is_dropped():
    assert texts("他說「好。」") == ["他說「好」"]
    assert texts("他說「好，」") == ["他說「好」"]


def test_markdown_emphasis_is_dropped():
    assert texts("**重點**在這裡。") == ["重點在這裡"]
    assert texts("這是 `code` 和 ~~刪除~~ 和 __斜體__。") == ["這是 code 和 刪除 和 斜體"]
    assert texts("~~舊~~新") == ["舊新"]


def test_caption_has_no_emphasis_marks():
    assert texts("這*很重要*。") == ["這很重要"]
    assert texts("***x***") == ["x"]
    assert texts("*重點*") == ["重點"]
    assert texts("***重點***在這裡。") == ["重點在這裡"]
    assert [p.text for p in line_of("這*很重要*。")[0].pieces] == ["這", "很", "重", "要"]


def test_marks_that_come_with_the_text_are_dropped_by_the_same_rule_as_in_the_script():
    # a transcript.json whose sentence text still holds the marks: its unit spans point past them
    assert [line.text for line in break_raw("這*很重要*。")] == ["這很重要"]
    assert [line.text for line in break_raw("***x***")] == ["x"]
    assert [line.text for line in break_raw("**重點**和`code`和~~舊~~")] == ["重點和code和舊"]
    assert [line.text for line in break_raw("5*3*2 和 *強調*")] == ["5*3*2 和 強調"]
    assert [p.text for p in break_raw("這*很重要*。")[0].pieces] == ["這", "很", "重", "要"]
    assert unit_indexes(break_raw("**重點**在這裡。")) == [0, 1, 2, 3, 4]


def test_caption_shows_expanding_char_once():
    assert texts("約½杯。") == ["約½杯"]
    (line,) = line_of("約½杯。")
    assert [(p.text, p.unit) for p in line.pieces] == [("約", 0), ("½", 1), ("杯", 2)]
    assert texts("1㍿和5m²") == ["1㍿和5m²"]
    assert unit_indexes(line_of("1㍿和5m²")) == [0, 1, 2, 3, 4, 5]


def test_a_single_star_underscore_or_tilde_is_text():
    assert texts("要 3~5 天") == ["要 3~5 天"]
    assert texts("要 3～5 天") == ["要 3～5 天"]                  # full-width tilde
    assert texts("5*3 等於 15") == ["5*3 等於 15"]
    assert texts("改 file_name 就好") == ["改 file_name 就好"]
    assert texts("5*3*2") == ["5*3*2"]
    assert texts("一個 * 號") == ["一個 * 號"]
    assert [(p.text, p.unit) for p in line_of("3~5")[0].pieces] == [("3", 0), ("~", None), ("5", 1)]


def test_spaces_stay_single_and_never_touch_the_edges_or_a_separator():
    assert texts("我們用    Remotion \t 做動畫。") == ["我們用 Remotion 做動畫"]
    assert texts("好， 我們開始") == ["好　我們開始"]
    assert texts("好 ，我們開始") == ["好　我們開始"]
    assert texts("他說 「好」") == ["他說 「好」"]


def test_nothing_is_left_at_the_ends_or_inside_the_marks():
    assert [l.text for l in break_raw("  ，好 ，  ")] == ["好"]
    assert [l.text for l in break_raw("他說「 好 」")] == ["他說「好」"]
    assert [l.text for l in break_raw("真的嗎 ？")] == ["真的嗎？"]


def test_pieces_carry_the_unit_index_and_none_for_punctuation():
    (line,) = line_of("好，我們開始。")
    assert [(p.text, p.unit) for p in line.pieces] == [
        ("好", 0), ("　", None), ("我", 1), ("們", 2), ("開", 3), ("始", 4)]


def test_a_latin_piece_keeps_its_written_case_and_space_pieces_have_no_unit():
    (line,) = line_of("用 Remotion 做")
    assert [(p.text, p.unit) for p in line.pieces] == [
        ("用", 0), (" ", None), ("Remotion", 1), (" ", None), ("做", 2)]


def test_markdown_characters_leave_no_piece():
    (line,) = line_of("**重點**在這裡。")
    assert [p.text for p in line.pieces] == ["重", "點", "在", "這", "裡"]


@pytest.mark.parametrize("text", [
    "今天我們來聊短影音剪輯。",
    "第一步是把多餘的停頓剪掉，這樣影片節奏才會變快很多。",
    "我們今天要用 Remotion 和 HyperFrames 做出 2026 年的動畫",
    "一二三四五六七八九十一二三四五六七八九十一二三四五六七八九十一二三四五六七八九十",
    "好，我們開始，今天要講一個很長很長的故事，所以請大家坐好，不要走開，因為真的很精彩。",
    "他說「這個方法真的可以省下一百五十分鐘的時間嗎？」",
    "The quick brown fox jumps over the lazy dog and then runs away again quickly",
])
def test_every_unit_survives_breaking_in_order(text):
    (sentence,), _ = parse_script(text)
    assert unit_indexes(line_of(text)) == list(range(len(sentence.units)))


@pytest.mark.parametrize("text", [f"這是{LONG_WORD}的意思", f"{LONG_WORD}是什麼", f"你知道{LONG_WORD}"])
def test_a_word_wider_than_a_line_is_split_wherever_it_stands(text):
    caption_dictionary()
    lines = line_of(text)
    (sentence,), _ = parse_script(text)
    word = next(k for k, unit in enumerate(sentence.units) if unit.text == LONG_WORD.lower())
    assert all(fits(line.text) for line in lines) and "".join(line.text for line in lines) == text
    holding = [any(piece.unit == word for piece in line.pieces) for line in lines]
    assert holding.count(True) == 2 and [line.split_word for line in lines] == holding
    assert_parts_tile(lines)


def test_latin_text_is_limited_by_pixels_not_only_by_count():
    style = load_style()["captions"]
    limit = 950 - 2 * style["outline_px"]
    whole = "WWWWWWWWWWWW WWWWWWWWWWWW"
    assert width_units(whole) <= 16 and text_px(whole, FONT, style["size_px"]) > limit
    assert texts(whole) == ["WWWWWWWWWWWW", "WWWWWWWWWWWW"]
    for line in texts("The quick brown fox jumps over the lazy dog and then runs away again quickly"):
        assert width_units(line) <= 16 and text_px(line, FONT, style["size_px"]) <= limit


def test_a_phrase_that_fits_is_not_cut_to_even_out_the_lines():
    assert [l.text for l in break_raw("一二三四五六七八，九十一二三四五六七八九十一二三")] == [
        "一二三四五六七八", "九十一二三四五六七八九十一二三"]
    assert [l.text for l in break_raw("一二三四五六七八！九十一二三四五六七八九十一二三")] == [
        "一二三四五六七八！", "九十一二三四五六七八九十一二三"]


def test_a_number_followed_by_letters_is_not_split():
    for k in range(21):
        text = "一二三四五六七八九十一二三四五六七八九十"
        lines = texts(text[:k] + "3D" + text[k:])
        assert len(lines) == 2 and sum("3D" in line for line in lines) == 1, lines


def test_the_character_count_limit_holds_where_pixels_would_allow_more():
    text = " ".join("i" for _ in range(28))
    assert text_px(text, FONT, load_style()["captions"]["size_px"]) < 900
    lines = texts(text)
    assert len(lines) == 2 and all(width_units(line) <= 16 for line in lines)


def test_the_outline_is_taken_off_the_pixel_limit():
    style = load_style()["captions"]
    text = "WW" + "一二三四五六七八九十一二三四五"
    px = text_px(text, FONT, style["size_px"])
    assert width_units(text) == 16 and 950 - 2 * style["outline_px"] < px <= 950   # fits 950, not 942
    assert len(texts(text)) == 2


def test_no_line_ends_with_an_opening_mark_or_starts_with_a_closing_one():
    for n in range(1, 40):
        text = "一二三四五六七八「九十」" * 2 + "甲乙" * n
        for line in texts(text):
            assert line[-1] not in "「『《（" and line[0] not in "」』》）？！"


def test_a_line_never_holds_a_raw_newline():
    lines = break_raw("好\n我們\r\n開始")
    assert [l.text for l in lines] == ["好 我們 開始"]
    assert all("\n" not in l.text and "\r" not in l.text for l in lines)


def test_a_text_with_nothing_to_show_gives_no_line():
    assert break_raw("，") == []
    assert break_raw(" ， ") == []


def test_a_mark_with_no_partner_is_text():
    assert [line.text for line in break_raw("**")] == ["**"]


def test_fewest_lines_first():
    assert [l.text for l in line_of("今天來聊剪輯，也聊一下拍片技巧")] == ["今天來聊剪輯　也聊一下拍片技巧"]


def test_a_punctuation_break_beats_a_word_break():
    assert [l.text for l in line_of("第一步先把腳本寫好，再開始錄影跟剪輯影片")] == ["第一步先把腳本寫好", "再開始錄影跟剪輯影片"]


def breaks_in_text(text: str, lines: list[Line]) -> list[int]:
    """Where in the one script sentence `text` each line but the last ends: the end of its last unit."""
    (sentence,), _ = parse_script(text)
    last_units = [max(piece.unit for piece in line.pieces if piece.unit is not None) for line in lines[:-1]]
    return [sentence.units[unit].span[1] for unit in last_units]


@pytest.mark.parametrize("text,expected", [
    ("好，這個方法我自己用了很久真的很有效。", ["好　這個方法我自己", "用了很久真的很有效"]),
    ("這個方法我自己用了很久真的很有效，對。", ["這個方法我自己用了", "很久真的很有效　對"]),
    ("好，我們今天來聊聊怎麼把一支影片剪好", ["好　我們今天來聊聊", "怎麼把一支影片剪好"]),
])
def test_a_line_of_one_or_two_characters_is_avoided_before_a_punctuation_break(text, expected):
    # the fewest short lines come before the fewest breaks that are not punctuation breaks: a 好 or 對 on a
    # line of its own is avoided by a break where a word ends
    caption_dictionary()
    lines = line_of(text)
    shown = [line.text for line in lines]
    assert len(shown) == 2 and all(width_units(line) > 2 and fits(line) for line in shown), shown
    (sentence,), _ = parse_script(text)
    for end in breaks_in_text(text, lines):
        assert end in word_ends(sentence.text) or sentence.text[end] in "，。", (shown, end)
    assert shown == expected


def test_a_line_of_two_characters_is_avoided_when_another_break_ties_on_lines_and_punctuation(monkeypatch):
    # two places to break, each giving two lines and one break that is not a punctuation break. WWWW is
    # two characters wide but wider in pixels than 很好用, so breaking after it gives the more even lines,
    # and a line two characters wide: the break before 很好用 is taken
    monkeypatch.setattr("pipeline.captions.word_ends", lambda text: frozenset({4, 16}))
    lines = texts("WWWW這個網站今年推出的新功能很好用")
    assert lines == ["WWWW這個網站今年推出的新功能", "很好用"]
    assert squares(["WWWW", "這個網站今年推出的新功能很好用"]) < squares(lines)


def test_the_most_even_break_wins_the_rest(monkeypatch):
    monkeypatch.setattr("pipeline.captions.word_ends", lambda text: frozenset({4, 8, 13}))
    assert texts("我們今天要來聊聊怎麼把一支影片剪得更好看") == ["我們今天要來聊聊", "怎麼把一支影片剪得更好看"]


def test_lines_break_only_where_a_word_ends():
    caption_dictionary()
    text = "很多新手會犯的錯誤就是一開始就買太貴的器材然後就放著不用"
    lines = line_of(text)
    assert len(lines) == 2 and "".join(line.text for line in lines) == text
    ends = list(itertools.accumulate(len(line.text) for line in lines))[:-1]
    assert set(ends) <= word_ends(text), (ends, sorted(word_ends(text)))


def test_a_too_long_latin_word_is_split_into_even_lines():
    caption_dictionary()
    lines = line_of(URL_SENTENCE)
    assert all(fits(line.text) for line in lines) and "".join(line.text for line in lines) == URL_SENTENCE
    assert len(lines) == 3 and "https://" in lines[0].text      # a colon right before // is shown as written
    assert all(line.split_word for line in lines)                 # each starts or ends inside the address
    size = load_style()["captions"]["size_px"]
    widths = [text_px(line.text, FONT, size) for line in lines]
    assert max(widths) - min(widths) <= max(text_px(c, FONT, size) for c in URL_SENTENCE)
    assert_parts_tile(lines)


@pytest.mark.parametrize("text,count", [
    (SUPER + "floccinaucinihilipilificati" + "好", 2),                                           # 61 letters
    (SUPER + "antidisestablishmentarianism" + "floccinaucinihilipilification" + "好", 3),         # 91 letters
    ("electroencephalographically" + "floccinaucinihilipilification" + "hippopotomonstrosesquippedaliophobia" + "好",
     3),                                                                                         # 92 letters
    (SUPER + "這個字到底是什麼意思", 2),
])
def test_where_a_long_word_is_cut_is_chosen_with_the_other_breaks(monkeypatch, text, count):
    # cutting the word evenly first leaves what follows it a line of its own: one line more
    monkeypatch.setattr("pipeline.captions.word_ends", everywhere)
    lines = line_of(text)
    assert len(lines) == count and all(len(line.text) > 1 for line in lines)
    assert all(fits(line.text) for line in lines) and "".join(line.text for line in lines) == text
    assert all(line.split_word for line in lines)
    for line in lines:                                            # one piece per unit on a line
        assert all(a.unit is None or a.unit != b.unit for a, b in zip(line.pieces, line.pieces[1:]))
    assert_parts_tile(lines)


def test_a_word_that_fits_a_line_is_never_split(monkeypatch):
    # cutting the word would fit the sentence in two lines; whole, it takes three
    monkeypatch.setattr("pipeline.captions.word_ends", everywhere)
    lines = line_of("我們今天要來聊聊怎麼用incomprehensibilities做出很好看的影片")
    assert len(lines) == 3 and sum("incomprehensibilities" in line.text for line in lines) == 1
    assert not any(line.split_word for line in lines)


def test_a_cjk_stretch_without_word_boundaries_longer_than_a_line_is_split(monkeypatch):
    monkeypatch.setattr("pipeline.captions.word_ends", lambda text: frozenset())
    lines = line_of("我們今天要來聊聊怎麼把一支影片剪得更好看")
    assert [(line.text, line.split_word) for line in lines] == [("我們今天要來聊聊怎麼", True), ("把一支影片剪得更好看", True)]
    assert all(piece.part == (0.0, 1.0) for line in lines for piece in line.pieces)   # each unit is one character


def test_the_long_word_warning_text():
    assert captions.LONG_WORD_WARNING == "long word split across caption lines"


def test_a_line_that_is_not_split_says_so():
    (line,) = line_of("今天我們來聊短影音剪輯。")
    assert line.split_word is False
    assert Line([Piece("好", 0)]).split_word is False and Piece("好", 0).part == (0.0, 1.0)


CROSS_CHECK = [
    "今天我們來聊短影音剪輯，第一步是把多餘的停頓剪掉，這樣影片節奏才會變快很多。",
    "好，我們開始，今天要講一個很長很長的故事，所以請大家坐好，不要走開，因為真的很精彩。",
    "我們今天要用 Remotion 和 HyperFrames 做出 2026 年的動畫，然後放到網站上給大家看",
    "他說「這個方法真的可以省下一百五十分鐘，也能讓節奏變快很多嗎？」",
    "WWWW 這個網站 iiii 今年推出的新功能很好用，WWWW 真的很棒",
    "好，這個方法我自己用了很久真的很有效。",                                # a short line or a word break
    SUPER + "這個字到底是什麼意思",                                         # a word wider than a line
    SUPER + "antidisestablishmentarianism" + "floccinaucinihilipilification" + "好",
]


@pytest.mark.parametrize("words", ["everywhere", "dictionary"])
@pytest.mark.parametrize("text", CROSS_CHECK)
def test_the_lines_are_the_best_of_every_breaking(monkeypatch, text, words):
    if words == "everywhere":
        monkeypatch.setattr("pipeline.captions.word_ends", everywhere)
    else:
        caption_dictionary()
    (sentence,), _ = parse_script(text)
    lines = line_of(text)
    assert tuple(line.text for line in lines) in best_breakings(text, captions.word_ends(sentence.text))


@pytest.mark.parametrize("text,lines", [
    ("第一, 第二; 第三: 第四. 結束", ["第一　第二　第三　第四　結束"]),
    ("價格是3.5元", ["價格是3.5元"]),
    ("用Node.js寫", ["用Node.js寫"]),
    ("然後呢...", ["然後呢..."]),
    ("花了1,000元", ["花了1,000元"]),
    ("差了1,5分", ["差了1,5分"]),
    ("早上10:30開始", ["早上10:30開始"]),
    ("選A;B都可以", ["選A　B都可以"]),
])
def test_half_width_punctuation(text, lines):
    assert [l.text for l in line_of(text)] == lines


def test_half_width_punctuation_breaks_and_leaves_the_end_of_a_line():
    assert texts("第一步是把多餘的停頓剪掉, 這樣影片節奏才會變快很多.") == [
        "第一步是把多餘的停頓剪掉", "這樣影片節奏才會變快很多"]


def test_width_units_counts_full_width_one_and_half_width_half_rounded_up():
    assert [width_units(t) for t in ("", "你好", "ab", "abc", "你a", "　", "？", "「", "……", "café")] == [
        0, 2, 1, 2, 2, 1, 1, 1, 2, 2]


def test_text_px_sums_the_advance_widths_of_the_font():
    assert text_px("你", FONT, 56) == pytest.approx(56 * advance_em("你"))
    expected = sum(advance_em(c) for c in "Remotion 2026") * 56
    assert text_px("Remotion 2026", FONT, 56) == pytest.approx(expected)
    assert text_px("Remotion 2026", FONT, 28) == pytest.approx(expected / 2)
    assert text_px("", FONT, 56) == 0


def test_a_missing_glyph_counts_as_one_em_in_text_px():
    assert text_px("\ue000", FONT, 56) == pytest.approx(56)
    assert text_px("a\ue000", FONT, 40) == pytest.approx(40 * (advance_em("a") + 1))


def test_missing_glyphs():
    assert missing_glyphs("臺灣體　？", FONT) == set() and missing_glyphs("\ue000", FONT) == {"\ue000"}


def test_missing_glyphs_names_each_missing_character_once():
    assert missing_glyphs("好\ue000\ue001\ue000嗎", FONT) == {"\ue000", "\ue001"}


# ---- event times ------------------------------------------------------------------------------


def test_events_follow_map_time_and_hold(T, TR):
    e = default_edit(T, TR); segs = build_segments(e, T, TR)
    ev = build_events(T, e, segs, FONT, load_style())
    first_unit = next(u for u in T["sentences"][0]["units"] if u["start"] is not None)
    assert ev[0].start == pytest.approx(map_time(first_unit["start"], segs))
    assert all(a.end <= b.start for a, b in zip(ev, ev[1:]))


def test_events_skip_extra_and_unspoken(T, TR):
    e = default_edit(T, TR); e["cuts"]["extra"] = [{"from": "s02", "to": "s02", "reason": "x"}]
    e["sentences"] = [s for s in e["sentences"] if s["id"] != "s02"]
    texts = "".join(x.line.text for x in build_events(T, e, build_segments(e, T, TR), FONT, load_style()))
    assert "第一步" not in texts


def test_an_unspoken_sentence_gets_no_event(T, TR):
    e = default_edit(T, TR)
    assert "150" in T["sentences"][2]["text"]
    T["sentences"][2]["spoken"] = False
    e["sentences"] = [s for s in e["sentences"] if s["id"] != "s03"]
    shown = [x.line.text for x in build_events(T, e, build_segments(e, T, TR), FONT, load_style())]
    assert not any("150" in line for line in shown) and len(shown) == 3


def test_meijia_default_edit_gives_one_event_per_kept_sentence(T, TR):
    e = default_edit(T, TR); segs = build_segments(e, T, TR)
    ev = build_events(T, e, segs, FONT, load_style())
    assert [x.line.text for x in ev] == [
        "今天我們來聊短影音剪輯", "第一步是把停頓剪掉　讓節奏變快", "這個方法可以省下 150 分鐘", "最後記得追蹤我"]
    assert all(x.end > x.start for x in ev) and ev[-1].end <= total_duration(segs) + 1e-9
    assert missing_glyphs("".join(x.line.text for x in ev), FONT) == set()


def test_event_start_end_and_piece_times_follow_the_units():
    s = timed_sentence("s01", "好，我們開始。", [(1.0, 1.2), (1.2, 1.4), (1.4, 1.6), (1.6, 1.8), (1.8, 2.0)])
    (ev,) = reel([s], [Segment(0.0, 10.0)])
    assert ev.start == pytest.approx(1.0) and ev.end == pytest.approx(2.0 + 0.5)
    assert [p.text for p in ev.line.pieces] == ["好", "　", "我", "們", "開", "始"]
    assert ev.times == [pytest.approx(1.0), None, pytest.approx(1.2), pytest.approx(1.4),
                        pytest.approx(1.6), pytest.approx(1.8)]


def test_an_event_ends_when_the_next_one_starts():
    a = timed_sentence("s01", "第一句。", [(1.0, 1.3), (1.3, 1.6), (1.6, 2.0)])
    b = timed_sentence("s02", "第二句。", [(2.2, 2.4), (2.4, 2.6), (2.6, 2.8)])
    ea, eb = reel([a, b], [Segment(0.0, 10.0)])
    assert ea.end == pytest.approx(2.2) and eb.start == pytest.approx(2.2)


def test_an_event_does_not_outlast_the_reel():
    s = timed_sentence("s01", "第一句。", [(1.0, 1.5), (1.5, 2.0), (2.0, 2.9)])
    (ev,) = reel([s], [Segment(0.0, 3.0)])
    assert ev.end == pytest.approx(3.0)


def test_times_are_mapped_through_the_cuts():
    s = timed_sentence("s01", "第一句。", [(5.0, 5.5), (5.5, 6.0), (6.0, 6.5)])
    (ev,) = reel([s], [Segment(0.0, 2.0), Segment(4.0, 10.0)])
    assert ev.start == pytest.approx(3.0) and ev.end == pytest.approx(2.0 + 2.5 + 0.5)
    assert ev.times == [pytest.approx(3.0), pytest.approx(3.5), pytest.approx(4.0)]


def test_a_unit_inside_a_cut_shows_where_the_next_segment_starts():
    s = timed_sentence("s01", "第一句。", [(1.0, 1.5), (3.0, 3.5), (4.5, 5.0)])
    (ev,) = reel([s], [Segment(0.0, 2.0), Segment(4.0, 10.0)])
    assert ev.times == [pytest.approx(1.0), pytest.approx(2.0), pytest.approx(2.5)]


def test_the_hold_comes_from_the_style():
    style = copy.deepcopy(load_style()); style["captions"]["hold_s"] = 1.25
    s = timed_sentence("s01", "第一句。", [(1.0, 1.5), (1.5, 2.0), (2.0, 2.5)])
    (ev,) = reel([s], [Segment(0.0, 10.0)], style)
    assert ev.end == pytest.approx(2.5 + 1.25)


def test_a_two_line_sentence_gives_two_events_that_meet():
    s = timed_sentence("s01", "第一步是把多餘的停頓剪掉，這樣影片節奏才會變快很多。",
                       [(1.0 + 0.1 * k, 1.1 + 0.1 * k) for k in range(24)])
    first, second = reel([s], [Segment(0.0, 10.0)])
    assert first.line.text == "第一步是把多餘的停頓剪掉" and second.line.text == "這樣影片節奏才會變快很多"
    assert first.start == pytest.approx(1.0) and second.start == pytest.approx(2.2)
    assert first.end == pytest.approx(second.start)
    assert second.end == pytest.approx(1.1 + 0.1 * 23 + 0.5)    # the last unit ends at 3.4


@pytest.mark.parametrize("text,split", [
    (URL_SENTENCE, [True, True, True]),             # cut next to a slash and a full stop
    (f"這是{LONG_WORD}的意思", [True, True]),        # cut inside the word
])
def test_karaoke_of_a_split_word_adds_up(text, split):
    caption_dictionary()
    count = len(split_units(text))
    s = timed_sentence("s01", text, [(1.0 + 0.4 * k, 1.4 + 0.4 * k) for k in range(count)])
    events = reel([s], [Segment(0.0, 20.0)])
    assert [ev.line.split_word for ev in events] == split
    for ev in events:
        values = [v for v in captions._karaoke(ev) if v is not None]
        assert values and min(values) >= 0 and sum(values) == round((ev.end - ev.start) * 100)


def test_split_word_times_follow_the_parts():
    caption_dictionary()
    segs = [Segment(0.0, 1.0), Segment(1.5, 10.0)]                   # the reel loses 1.0 to 1.5
    s = timed_sentence("s01", LONG_WORD, [(2.0, 6.0)])
    first, second = reel([s], segs)
    share = len(first.line.text) / len(LONG_WORD)
    assert first.line.pieces[0].part == (0.0, share) and second.line.pieces[0].part == (share, 1.0)
    assert first.start == pytest.approx(map_time(2.0, segs)) == pytest.approx(1.5)
    assert second.start == pytest.approx(map_time(2.0 + share * 4.0, segs))
    # the first line's end is where its piece of the word ends, not where the word does
    row = captions._row("s01", first.line, s["units"], segs, 0.5, total_duration(segs))
    assert row.wish == pytest.approx(map_time(2.0 + share * 4.0, segs) + 0.5)
    assert second.end == pytest.approx(map_time(6.0, segs) + 0.5)


def test_piece_times_are_none_for_punctuation_and_stay_inside_the_event(T, TR):
    e = default_edit(T, TR); segs = build_segments(e, T, TR)
    for ev in build_events(T, e, segs, FONT, load_style()):
        assert len(ev.times) == len(ev.line.pieces)
        timed = [t for t in ev.times if t is not None]
        assert all(t is None for t, p in zip(ev.times, ev.line.pieces) if p.unit is None)
        assert timed == sorted(timed) and ev.start - 1e-9 <= timed[0] and timed[-1] <= ev.end + 1e-9


def test_lines_that_start_at_the_same_output_time_each_get_a_frame():
    a = timed_sentence("s01", "第一句。", [(2.2, 2.3), (2.3, 2.4), (2.4, 2.5)])  # all inside the cut
    b = timed_sentence("s02", "第二句。", [(4.0, 4.1), (4.1, 4.2), (4.2, 4.3)])  # right after it
    segs = [Segment(0.0, 2.0), Segment(4.0, 10.0)]
    assert map_time(2.2, segs) == map_time(4.0, segs) == 2.0
    ea, eb = reel([a, b], segs)
    assert [ea.line.text, eb.line.text] == ["第一句", "第二句"]
    assert ea.start == pytest.approx(2.0) and ea.end - ea.start >= FRAME - 1e-9
    assert eb.start >= ea.end - 1e-9 and eb.end > eb.start
    for ev in (ea, eb):  # B moved later, so its first unit's time moves with it
        assert all(ev.start - 1e-9 <= t <= ev.end + 1e-9 for t in ev.times if t is not None)
    assert eb.times[0] == pytest.approx(eb.start)


def test_events_at_the_very_end_still_fit_in_the_reel():
    a = timed_sentence("s01", "第一句。", [(9.97, 9.98), (9.98, 9.99), (9.99, 10.0)])
    b = timed_sentence("s02", "第二句。", [(10.0, 10.0), (10.0, 10.0), (10.0, 10.0)])
    ea, eb = reel([a, b], [Segment(0.0, 10.0)])
    for ev in (ea, eb):
        assert ev.end - ev.start >= FRAME - 1e-9 and 0 <= ev.start and ev.end <= 10.0 + 1e-9
    assert ea.end <= eb.start + 1e-9
    assert [ea.line.text, eb.line.text] == ["第一句", "第二句"]


def test_a_sentence_timed_before_the_one_ahead_of_it_still_follows_it():
    a = timed_sentence("s01", "第一句。", [(5.0, 5.1), (5.1, 5.2), (5.2, 5.3)])
    b = timed_sentence("s02", "第二句。", [(3.0, 3.1), (3.1, 3.2), (3.2, 3.3)])
    ea, eb = reel([a, b], [Segment(0.0, 10.0)])
    assert [ea.line.text, eb.line.text] == ["第一句", "第二句"]
    assert ea.end > ea.start and eb.end > eb.start and ea.end <= eb.start + 1e-9


def test_a_reel_shorter_than_its_captions_still_has_every_event_longer_than_zero():
    s = [timed_sentence(f"s0{k}", "一。", [(0.02 * k, 0.02 * k + 0.02)]) for k in range(1, 4)]
    events = reel(s, [Segment(0.0, 2 * FRAME)])
    assert [e.line.text for e in events] == ["一", "一", "一"]
    assert all(e.end > e.start >= 0 for e in events)
    assert all(a.end <= b.start + 1e-9 for a, b in zip(events, events[1:]))
    assert all(e.end - e.start == pytest.approx(FRAME) for e in events)   # as short as the rule lets them be


def test_a_line_with_units_but_no_time_is_an_error():
    s = timed_sentence("s01", "第一句。", [(1.0, 1.5), (1.5, 2.0), (2.0, 2.5)])
    for unit in s["units"]:
        unit["start"] = unit["end"] = None
    with pytest.raises(PipelineError, match="s01"):
        reel([s], [Segment(0.0, 10.0)])


# ---- caption text from edit.json --------------------------------------------------------------

OLD = "大概多成兩小時"  # units: 大概 / 多 / 成 / 兩小時


def units_of(text: str, spans: list[tuple[int, int]]) -> list[dict]:
    """One unit per span of `text`, with made-up times (unit k runs from 1 + 0.3 k s for 0.25 s)."""
    return [
        {"text": text[lo:hi], "span": [lo, hi], "start": 1.0 + 0.3 * k, "end": 1.25 + 0.3 * k, "src": "asr"}
        for k, (lo, hi) in enumerate(spans)
    ]


U = units_of(OLD, [(0, 2), (2, 3), (3, 4), (4, 7)])


def times(units: list[dict]) -> list[tuple[float, float]]:
    return [(unit["start"], unit["end"]) for unit in units]


def shown(units: list[dict]) -> list[tuple[str, list[int]]]:
    return [(unit["text"], unit["span"]) for unit in units]


def test_a_replaced_character_keeps_its_unit_and_time():
    r = remap_units(OLD, U, "大概多撐兩小時")
    assert [u["text"] for u in r] == ["大概", "多", "撐", "兩小時"] and times(r) == times(U)
    assert [u["span"] for u in r] == [[0, 2], [2, 3], [3, 4], [4, 7]]
    assert [u["src"] for u in r] == ["asr"] * 4


def test_remapping_leaves_the_units_it_was_given_alone():
    before = [dict(unit, span=list(unit["span"])) for unit in U]
    remap_units(OLD, U, "喔大概多撐了兩小時")
    assert U == before


def test_an_inserted_character_joins_the_unit_before_it():
    r = remap_units(OLD, U, "大概多撐了兩小時")  # 成 becomes 撐了
    assert shown(r) == [("大概", [0, 2]), ("多", [2, 3]), ("撐了", [3, 5]), ("兩小時", [5, 8])]
    assert times(r) == times(U)
    r = remap_units(OLD, U, "大概多成了兩小時")  # 了 only comes in
    assert shown(r) == [("大概", [0, 2]), ("多", [2, 3]), ("成了", [3, 5]), ("兩小時", [5, 8])]
    assert times(r) == times(U)


def test_a_character_inserted_at_the_head_joins_the_first_unit():
    r = remap_units(OLD, U, "喔大概多成兩小時")
    assert shown(r) == [("喔大概", [0, 3]), ("多", [3, 4]), ("成", [4, 5]), ("兩小時", [5, 8])]
    assert times(r) == times(U)


def test_a_unit_whose_characters_are_all_deleted_is_dropped():
    r = remap_units(OLD, U, "大概兩小時")
    assert shown(r) == [("大概", [0, 2]), ("兩小時", [2, 5])]
    assert times(r) == [times(U)[0], times(U)[3]]


def test_a_unit_replaced_by_punctuation_is_dropped():
    r = remap_units(OLD, U, "大概多，兩小時")  # 成 is gone and the comma is no unit's
    assert shown(r) == [("大概", [0, 2]), ("多", [2, 3]), ("兩小時", [4, 7])]


@pytest.mark.parametrize("new, where", [
    ("大概，多成兩小時", 2),   # between two units
    ("大概 多成兩小時", 2),    # a space
    ("大概多成兩小時。", 7),   # at the end
    ("，大概多成兩小時", 0),   # at the head
])
def test_inserted_punctuation_belongs_to_no_unit(new, where):
    r = remap_units(OLD, U, new)
    assert [u["text"] for u in r] == ["大概", "多", "成", "兩小時"]
    assert times(r) == times(U)
    assert not any(lo <= where < hi for lo, hi in (u["span"] for u in r))
    assert all(u["text"] == new[u["span"][0]:u["span"][1]] for u in r)


def test_punctuation_between_two_characters_of_one_unit_stays_in_it():
    r = remap_units(OLD, U, "大概多成兩，小時")
    assert shown(r) == [("大概", [0, 2]), ("多", [2, 3]), ("成", [3, 4]), ("兩，小時", [4, 8])]
    assert times(r) == times(U)


def test_a_longer_replacement_is_spread_over_the_old_units():
    r = remap_units(OLD, U, "大概確實有兩小時")  # 多成 (2 characters) becomes 確實有 (3)
    assert shown(r) == [("大概", [0, 2]), ("確實", [2, 4]), ("有", [4, 5]), ("兩小時", [5, 8])]
    assert times(r) == times(U)


def test_a_shorter_replacement_leaves_out_the_units_that_get_no_character():
    r = remap_units(OLD, U, "大概很兩小時")  # 多成 becomes 很: it takes the time of 多
    assert shown(r) == [("大概", [0, 2]), ("很", [2, 3]), ("兩小時", [3, 6])]
    assert times(r) == [times(U)[0], times(U)[1], times(U)[3]]


def test_a_character_outside_every_unit_owns_nothing():
    old = "大概，多成"
    units = units_of(old, [(0, 2), (3, 4), (4, 5)])
    r = remap_units(old, units, "大概，多撐")  # the comma is equal text that no unit holds
    assert shown(r) == [("大概", [0, 2]), ("多", [3, 4]), ("撐", [4, 5])]
    assert times(r) == times(units)


def test_the_same_text_gives_the_same_units():
    assert remap_units(OLD, U, OLD) == U


@pytest.mark.parametrize("text", [
    "用A+B", "Node.js", "R&D", "UI/UX", "3~5", "用A+B做UI/UX，3~5。", "第 3 步", "價格是1,000元，漲了50%",
])
def test_unchanged_text_with_symbols_outside_the_units_gives_the_same_units(text):
    """A symbol that no unit holds (+ . & / ~) is equal text: it stays no unit's, as it is in the sentence."""
    units = timed_sentence("s01", text, [(0.1 * k, 0.1 * k + 0.05) for k in range(len(split_units(text)))])["units"]
    assert remap_units(text, units, text) == units


def test_a_symbol_outside_the_units_stays_when_another_character_changes():
    old = "用A+B做"
    units = timed_sentence("s01", old, [(0.1 * k, 0.1 * k + 0.05) for k in range(len(split_units(old)))])["units"]
    r = remap_units(old, units, "用A+B寫")  # 做 becomes 寫: the + still belongs to no unit
    assert shown(r) == [("用", [0, 1]), ("A", [1, 2]), ("B", [3, 4]), ("寫", [4, 5])]
    assert times(r) == times(units)


def test_a_symbol_that_replaces_punctuation_joins_the_unit_before_it():
    old = "大概，多成"
    units = units_of(old, [(0, 2), (3, 4), (4, 5)])
    r = remap_units(old, units, "大概+多成")  # the + is new text, not the comma that was equal
    assert shown(r) == [("大概+", [0, 3]), ("多", [3, 4]), ("成", [4, 5])]


def test_a_plain_character_inserted_at_the_tail_joins_the_last_unit():
    r = remap_units(OLD, U, "大概多成兩小時了")
    assert shown(r) == [("大概", [0, 2]), ("多", [2, 3]), ("成", [3, 4]), ("兩小時了", [4, 8])]
    assert times(r) == times(U)


def test_a_character_deleted_in_the_middle_of_a_unit_leaves_the_unit_whole():
    r = remap_units(OLD, U, "大概多成兩時")  # 小 is gone
    assert shown(r) == [("大概", [0, 2]), ("多", [2, 3]), ("成", [3, 4]), ("兩時", [4, 6])]
    assert times(r) == times(U)


def test_markdown_marks_in_the_new_text_belong_to_no_unit():
    r = remap_units(OLD, U, "大概多成**兩小時**")
    assert shown(r) == [("大概", [0, 2]), ("多", [2, 3]), ("成", [3, 4]), ("兩小時", [6, 9])]
    assert times(r) == times(U)
    # a lone star is text, not a mark: the unit before it takes it
    assert shown(remap_units(OLD, U, "大概多成兩小時*"))[-1] == ("兩小時*", [4, 8])


def test_the_marks_a_caption_text_carries_are_not_shown():
    spoken = [(1.0 + 0.3 * k, 1.25 + 0.3 * k) for k in range(7)]
    s = timed_sentence("s01", OLD, spoken)
    edit = {"cuts": {"extra": []}, "captions": {"mode": "line", "text": {"s01": "大概多成**兩小時**"}}}
    (ev,) = build_events({"sentences": [s]}, edit, [Segment(0.0, 10.0)], FONT, load_style())
    assert ev.line.text == OLD and "*" not in ev.line.text
    assert ev.times == [pytest.approx(t) for t, _ in spoken]


@pytest.mark.parametrize("new", ["", "，。", " ", "「」！？", "**，**", "、、、"])
def test_a_text_with_no_character_to_show_gives_no_unit(new):
    assert remap_units(OLD, U, new) == []


def test_there_is_no_unit_without_units_or_without_old_text():
    assert remap_units(OLD, [], "好") == [] and remap_units("", [], "好") == [] and remap_units("", [], "") == []


def test_a_unit_with_an_empty_span_is_dropped():
    units = units_of(OLD, [(0, 2), (2, 2), (2, 3), (3, 4), (4, 7)])  # the second has no character
    r = remap_units(OLD, units, OLD)
    assert shown(r) == [("大概", [0, 2]), ("多", [2, 3]), ("成", [3, 4]), ("兩小時", [4, 7])]
    assert times(r) == [times(units)[k] for k in (0, 2, 3, 4)]


PAIRS = [
    (OLD, "大概多撐了兩小時"), (OLD, "喔，大概多成兩小時。"), (OLD, "兩小時"), (OLD, "多成兩小時大概"),
    (OLD, "大概，多，成，兩，小，時"), (OLD, "完全不一樣的句子"), (OLD, "**大概**多成兩小時"), (OLD, "大"),
    (OLD, "大概多成兩小時，也許更久一點。"), ("用A+B做UI/UX", "用A+B做UI/UX的設計"), ("用A+B做UI/UX", "拿C&D來做"),
    ("價格是1,000元", "價格是1,500元"), ("第一句，第二句", "第一句第二句"),
]


@pytest.mark.parametrize("old, new", PAIRS)
def test_remapped_units_are_inside_the_text_apart_and_on_their_own_times(old, new):
    units = timed_sentence("s01", old, [(0.1 * k, 0.1 * k + 0.05) for k in range(len(split_units(old)))])["units"]
    result = remap_units(old, units, new)
    spans = [unit["span"] for unit in result]
    assert all(0 <= lo < hi <= len(new) for lo, hi in spans)
    assert all(a[1] <= b[0] for a, b in zip(spans, spans[1:]))  # in order, none shares a character
    assert all(unit["text"] == new[lo:hi] for unit, (lo, hi) in zip(result, spans))
    assert {(u["start"], u["end"], u["src"]) for u in result} <= {(u["start"], u["end"], u["src"]) for u in units}


def test_caption_source_without_an_override_is_the_sentence_as_it_is(T):
    sentence = T["sentences"][1]
    assert caption_source(sentence, NO_CUTS) == (sentence["text"], sentence["units"])
    assert caption_source(sentence, NO_CUTS)[1] is sentence["units"]
    edit = {"captions": {"mode": "line", "text": {"s01": "別的句子"}}}  # another sentence's text
    assert caption_source(sentence, edit)[1] is sentence["units"]


def test_caption_source_with_an_override_is_the_new_text_and_its_remapped_units():
    sentence = timed_sentence("s01", OLD, [(1.0 + 0.3 * k, 1.25 + 0.3 * k) for k in range(7)])
    edit = {"captions": {"mode": "line", "text": {"s01": "大概多撐兩小時"}}}
    text, units = caption_source(sentence, edit)
    assert text == "大概多撐兩小時"
    assert units == remap_units(OLD, sentence["units"], text)
    assert [u["text"] for u in units] == list("大概多撐兩小時") and times(units) == times(sentence["units"])


def test_build_events_shows_the_edited_text_at_the_spoken_times():
    spoken = [(1.0 + 0.3 * k, 1.25 + 0.3 * k) for k in range(7)]
    s = timed_sentence("s01", OLD + "。", spoken)
    edit = {"cuts": {"extra": []}, "captions": {"mode": "line", "text": {"s01": "大概多撐了兩小時"}}}
    (plain_event,) = reel([s], [Segment(0.0, 10.0)])
    (ev,) = build_events({"sentences": [s]}, edit, [Segment(0.0, 10.0)], FONT, load_style())
    assert plain_event.line.text == OLD and ev.line.text == "大概多撐了兩小時"
    assert (ev.start, ev.end) == (plain_event.start, plain_event.end)
    assert [p.text for p in ev.line.pieces] == ["大", "概", "多", "撐了", "兩", "小", "時"]
    # a piece starts when the unit it came from is spoken: 撐了 with 成
    assert ev.times == [pytest.approx(t) for t, _ in spoken]


def test_build_events_shows_the_edited_text_of_a_real_transcript(T, TR):
    e = default_edit(T, TR); segs = build_segments(e, T, TR)
    before = build_events(T, e, segs, FONT, load_style())
    assert before[1].line.text == "第一步是把停頓剪掉　讓節奏變快"
    e["captions"]["text"] = {"s02": "第一步是把停頓剪光，讓節奏變快"}
    after = build_events(T, e, segs, FONT, load_style())
    assert [x.line.text for x in after] == [
        before[0].line.text, "第一步是把停頓剪光　讓節奏變快", before[2].line.text, before[3].line.text]
    for old, new in zip(before, after):
        assert (new.start, new.end) == (old.start, old.end) and new.times == old.times


def test_an_edited_sentence_that_is_not_kept_has_no_event(T, TR):
    e = default_edit(T, TR)
    e["cuts"]["extra"] = [{"from": "s02", "to": "s02", "reason": "x"}]
    e["sentences"] = [s for s in e["sentences"] if s["id"] != "s02"]
    e["captions"]["text"] = {"s02": "留著不用的字"}
    texts = "".join(x.line.text for x in build_events(T, e, build_segments(e, T, TR), FONT, load_style()))
    assert "留著不用的字" not in texts


# ---- ASS output -------------------------------------------------------------------------------


def dialogues(path) -> list[str]:
    return [l for l in path.read_text(encoding="utf-8").splitlines() if l.startswith("Dialogue")]


def style_fields(path) -> dict[str, str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    names = [n.strip() for n in next(l for l in lines if l.startswith("Format: Name")).split(":", 1)[1].split(",")]
    values = next(l for l in lines if l.startswith("Style:")).split(":", 1)[1].strip().split(",")
    return dict(zip(names, values))


def ks(path) -> list[int]:
    return [int(k) for k in re.findall(r"\\k(\d+)", path.read_text(encoding="utf-8"))]


def test_ass_header_and_line_mode(tmp_path):
    p = tmp_path / "c.ass"; write_ass([event("今天開始", 1.0, 2.0)], load_style(), 1040, "line", p)
    s = p.read_text(encoding="utf-8")
    assert "WrapStyle: 2" in s and "PlayResX: 1080" in s and "PlayResY: 1920" in s
    assert "{\\an8\\pos(540,1040)}今天開始" in s and "0:00:01.00,0:00:02.00" in s


def test_ass_scales_border_and_has_the_sections_libass_needs(tmp_path):
    p = tmp_path / "c.ass"; write_ass([event("今天開始", 1.0, 2.0)], load_style(), 1040, "line", p)
    s = p.read_text(encoding="utf-8")
    assert "ScaledBorderAndShadow: yes" in s and "[Script Info]" in s and "[V4+ Styles]" in s and "[Events]" in s
    assert "\\k" not in s


def test_ass_style_line_follows_the_style_file(tmp_path):
    c = load_style()["captions"]
    p = tmp_path / "c.ass"; write_ass([event("今天開始", 1.0, 2.0)], load_style(), 1040, "line", p)
    f = style_fields(p)
    # libass takes Fontsize as the cell height (hhea ascent less descent), not the em: size_px is the em
    assert f["Fontname"] == "Noto Sans TC" and f["Bold"] == "-1"
    assert f["Fontsize"] == str(round(c["size_px"] * cell_em())) == "81"
    assert f["Outline"] == str(c["outline_px"]) and f["OutlineColour"] == ass_color(c["outline_color"])
    assert f["PrimaryColour"] == ass_color(c["color"]) == "&H00FFFFFF" and f["Alignment"] == "8"
    assert f["Shadow"] == "0" and f["BorderStyle"] == "1"


def test_ass_fontsize_comes_from_the_font_given(tmp_path):
    tiny = tiny_font(tmp_path / "tiny.ttf", ascent=900, descent=-300)           # a 1.2 em cell
    p = tmp_path / "c.ass"
    write_ass([event("今天開始", 1.0, 2.0)], load_style(), 1040, "line", p, font=tiny)
    assert style_fields(p)["Fontsize"] == str(round(56 * 1.2))
    write_ass([event("今天開始", 1.0, 2.0)], load_style(), 1040, "line", p)
    assert style_fields(p)["Fontsize"] == "81"                                  # the bold font of the style


def test_the_line_box_covers_the_font_cell_and_both_outlines():
    c = load_style()["captions"]
    assert c["line_height_px"] >= c["size_px"] * cell_em() + 2 * c["outline_px"]


def test_ass_word_mode_colours_are_highlight_then_color(tmp_path):
    p = tmp_path / "c.ass"; write_ass([event("今天開始", 1.0, 2.0)], load_style(), 1040, "word", p)
    f = style_fields(p)
    assert f["PrimaryColour"] == "&H0000D4FF" and f["SecondaryColour"] == "&H00FFFFFF"


def test_ass_a_style_that_is_not_bold_writes_bold_off(tmp_path):
    style = copy.deepcopy(load_style()); style["captions"]["bold"] = False
    p = tmp_path / "c.ass"; write_ass([event("今天開始", 1.0, 2.0)], style, 1040, "line", p)
    assert style_fields(p)["Bold"] == "0"


def test_ass_word_mode_k_tags(tmp_path):
    p = tmp_path / "c.ass"; write_ass([event("今天開始", 1.0, 2.0)], load_style(), 1040, "word", p)
    ks = [int(k) for k in re.findall(r"\\k(\d+)", p.read_text(encoding="utf-8"))]
    assert len(ks) == 4 and abs(sum(ks) - 100) <= 2


def test_ass_k_runs_to_the_next_timed_piece_and_skips_punctuation(tmp_path):
    ev = Event(1.0, 2.0, Line([Piece("今", 0), Piece("　", None), Piece("天", 1)]), [1.0, None, 1.5])
    p = tmp_path / "c.ass"; write_ass([ev], load_style(), 1040, "word", p)
    assert dialogues(p)[0].endswith("{\\an8\\pos(540,1040)}{\\k50}今　{\\k50}天")


def test_ass_k_values_sum_to_the_event_and_are_never_negative(tmp_path):
    line = Line([Piece(c, i) for i, c in enumerate("一二三四五")])
    ev = Event(1.0, 2.0, line, [1.0, 1.7, 1.2, 5.0, 1.9])  # out of order, and one past the end
    p = tmp_path / "c.ass"; write_ass([ev], load_style(), 1040, "word", p)
    values = ks(p)
    assert len(values) == 5 and min(values) >= 0 and abs(sum(values) - 100) <= 2


def test_ass_the_first_k_starts_at_the_event_start(tmp_path):
    ev = Event(1.0, 2.0, Line([Piece("今", 0), Piece("天", 1)]), [1.3, 1.6])
    p = tmp_path / "c.ass"; write_ass([ev], load_style(), 1040, "word", p)
    assert ks(p) == [60, 40]


def test_ass_a_unit_without_a_time_gets_no_k(tmp_path):
    ev = Event(1.0, 2.0, Line([Piece("今", 0), Piece("天", 1), Piece("好", 2)]), [1.0, None, 1.5])
    p = tmp_path / "c.ass"; write_ass([ev], load_style(), 1040, "word", p)
    assert ks(p) == [50, 50] and dialogues(p)[0].endswith("{\\k50}今天{\\k50}好")


def test_ass_k_in_a_built_event_sums_to_its_length(tmp_path, T, TR):
    e = default_edit(T, TR); segs = build_segments(e, T, TR)
    events = build_events(T, e, segs, FONT, load_style())
    p = tmp_path / "c.ass"; write_ass(events, load_style(), 1040, "word", p)
    for ev, row in zip(events, dialogues(p)):
        values = [int(k) for k in re.findall(r"\\k(\d+)", row)]
        assert min(values) >= 0 and abs(sum(values) - round((ev.end - ev.start) * 100)) <= 2


def test_ass_escapes_override_chars(tmp_path):
    p = tmp_path / "c.ass"; write_ass([event("a{b}c\\d", 0, 1)], load_style(), 1040, "line", p)
    line = [l for l in p.read_text(encoding="utf-8").splitlines() if l.startswith("Dialogue")][0]
    assert line.endswith("a｛b｝c＼d") and "{b" not in line


def test_ass_escapes_in_word_mode_too(tmp_path):
    p = tmp_path / "c.ass"; write_ass([event("a{b}", 0, 1)], load_style(), 1040, "word", p)
    assert dialogues(p)[0].endswith("{\\k25}a{\\k25}｛{\\k25}b{\\k25}｝")


def test_ass_a_dialogue_is_one_physical_line(tmp_path):
    p = tmp_path / "c.ass"; write_ass([event("a\nb\rc\r\nd", 0, 1)], load_style(), 1040, "line", p)
    rows = dialogues(p)
    assert len(rows) == 1 and rows[0].endswith("}a b c  d")
    assert b"\r" not in p.read_bytes()


def test_ass_one_dialogue_per_event_in_order_with_ten_fields(tmp_path):
    events = [event("今天", 0.5, 1.5), event("開始", 1.5, 3.25)]
    p = tmp_path / "c.ass"; write_ass(events, load_style(), 1176, "line", p)
    rows = dialogues(p)
    assert len(rows) == 2 and all(len(r.split(",", 9)) == 10 for r in rows)
    assert rows[0].startswith("Dialogue: 0,0:00:00.50,0:00:01.50,") and "\\pos(540,1176)}今天" in rows[0]
    assert rows[1].startswith("Dialogue: 0,0:00:01.50,0:00:03.25,") and rows[1].endswith("開始")


@pytest.mark.parametrize("text, width_checked", [
    ("我們今天要用剪輯軟體把停頓剪掉吧", True),            # 16 CJK characters: 16 * 56 = 896 px
    ("Typography gjpqy jumps", False),                     # descenders reach the lowest
])
def test_libass_draws_the_line_as_wide_and_as_low_as_the_style_means(tmp_path, text, width_checked):
    style = copy.deepcopy(load_style())
    style["captions"]["color"] = style["captions"]["outline_color"] = "#FFFFFF"   # so the outline counts as ink
    c, top = style["captions"], 1040
    ass, png, log = tmp_path / "c.ass", tmp_path / "frame.png", tmp_path / "ffmpeg.log"
    write_ass([event(text, 0.0, 2.0)], style, top, "line", ass)
    run_ffmpeg(["-y", "-hide_banner", "-loglevel", "verbose", "-f", "lavfi", "-i", "color=c=black:s=1080x1920:r=30:d=2",
                "-vf", f"subtitles=filename='{ass}':fontsdir='{style['dir'] / 'fonts'}'",
                "-ss", "1", "-frames:v", "1", str(png)], log)
    assert "-> NotoSansTC-Bold" in log.read_text(errors="replace")             # the static bold font, not a substitute
    ys, xs = np.nonzero(cv2.imread(str(png), cv2.IMREAD_GRAYSCALE) > 128)
    assert ys.size
    if width_checked:
        expected = text_px(text, FONT, c["size_px"])
        assert abs((xs.max() - xs.min() + 1) - expected) <= 0.03 * expected, (xs.max() - xs.min() + 1, expected)
    assert ys.max() <= top + c["line_height_px"], (ys.max(), top + c["line_height_px"])


def test_ass_unknown_mode_is_an_error(tmp_path):
    with pytest.raises(PipelineError, match="karaoke"):
        write_ass([event("今天開始", 1.0, 2.0)], load_style(), 1040, "karaoke", tmp_path / "c.ass")


def test_ass_an_event_with_a_time_missing_is_an_error_not_a_lost_piece(tmp_path):
    ev = Event(1.0, 2.0, Line([Piece("今", 0), Piece("天", 1)]), [1.0])
    with pytest.raises(PipelineError, match="今天"):
        write_ass([ev], load_style(), 1040, "word", tmp_path / "c.ass")


def test_ass_color_and_time():
    assert ass_color("#FFD400") == "&H0000D4FF" and ass_time(3723.456) == "1:02:03.46"


def test_ass_color_is_blue_green_red_and_accepts_lower_case():
    assert ass_color("#000000") == "&H00000000" and ass_color("#ff0000") == "&H000000FF"
    assert ass_color("#102030") == "&H00302010"


def test_ass_color_refuses_what_is_not_a_hex_colour():
    for bad in ("FFD400", "#FFD40", "#GGD400", "", "red"):
        with pytest.raises(PipelineError):
            ass_color(bad)


def test_ass_time_rounds_to_centiseconds_and_carries():
    assert ass_time(0) == "0:00:00.00" and ass_time(59.999) == "0:01:00.00" and ass_time(0.005) == "0:00:00.01"
    assert ass_time(3599.994) == "0:59:59.99" and ass_time(36000) == "10:00:00.00" and ass_time(-1) == "0:00:00.00"


# ---- placement --------------------------------------------------------------------------------


def test_caption_top_is_bottom_px_less_the_line_height():
    assert caption_top_px(load_style()) == 1158


def test_caption_top_reads_bottom_px_and_the_line_height_from_the_style():
    style = copy.deepcopy(load_style())
    style["captions"]["bottom_px"] = 1000
    assert caption_top_px(style) == 910
    style["captions"]["line_height_px"] = 100
    assert caption_top_px(style) == 900
