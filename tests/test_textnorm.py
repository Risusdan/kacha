"""Tests for pipeline.textnorm: comparison units, pinyin keys, number readings, s2twp."""

import pytest

from pipeline import textnorm
from pipeline.textnorm import (
    FILLERS,
    Unit,
    char_key,
    number_readings,
    split_units,
    to_taiwan,
    unit_alts,
    unit_key,
)


def test_split_units_mixed():
    us = split_units("用 Remotion 做 3 支")
    assert [u.text for u in us] == ["用", "remotion", "做", "3", "支"]
    assert [u.kind for u in us] == ["cjk", "latin", "cjk", "num", "cjk"] and us[1].span == (2, 10)


def test_split_units_fullwidth_digits_and_percent():
    assert [(u.text, u.percent) for u in split_units("１５０和50%")] == [("150", False), ("和", False), ("50", True)]


def test_percent_sign_belongs_to_the_number_span():
    assert split_units("５０％") == [Unit("50", "num", (0, 3), True)]
    assert split_units("1.5%")[0].span == (0, 4)


def test_percent_must_follow_the_digits_directly():
    assert [u.percent for u in split_units("50 %")] == [False]
    assert split_units("%") == []


def test_decimal_point_joins_digits_only_between_digits():
    assert [u.text for u in split_units("3.5倍")] == ["3.5", "倍"]
    assert [u.text for u in split_units("v3.5.2")] == ["v", "3.5", "2"]
    assert split_units("共3.") == [Unit("共", "cjk", (0, 1)), Unit("3", "num", (1, 2))]
    assert split_units("3. 然後") == [Unit("3", "num", (0, 1)), Unit("然", "cjk", (3, 4)), Unit("後", "cjk", (4, 5))]
    assert [u.text for u in split_units(".5")] == ["5"]


@pytest.mark.parametrize("text,expected", [
    ("1,500", [Unit("1500", "num", (0, 5))]),
    ("1,500,000", [Unit("1500000", "num", (0, 9))]),
    ("12,345", [Unit("12345", "num", (0, 6))]),
    ("123,456,789", [Unit("123456789", "num", (0, 11))]),
    ("共1,500元", [Unit("共", "cjk", (0, 1)), Unit("1500", "num", (1, 6)), Unit("元", "cjk", (6, 7))]),
    ("1,500.5", [Unit("1500.5", "num", (0, 7))]),
    ("1,500%", [Unit("1500", "num", (0, 6), True)]),
    ("1,500.5%", [Unit("1500.5", "num", (0, 8), True)]),
    ("１,５００", [Unit("1500", "num", (0, 5))]),
    ("1,500,5", [Unit("1500", "num", (0, 5)), Unit("5", "num", (6, 7))]),
])
def test_thousands_separator_joins_the_number(text, expected):
    assert split_units(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("1,2", ["1", "2"]),
    ("1,50", ["1", "50"]),
    ("共1,50元", ["共", "1", "50", "元"]),
    ("1,5000", ["1", "5000"]),
    ("1,500,5000", ["1500", "5000"]),
    ("1，500", ["1", "500"]),  # the full-width comma is a clause comma
    ("1,", ["1"]),
    ("1.5,000", ["1.5", "000"]),
    ("2026,2027", ["2026", "2027"]),
    ("1234,567", ["1234", "567"]),  # the leading group has at most three digits
    ("2026,500人", ["2026", "500", "人"]),
    ("0,500", ["0", "500"]),  # and does not start with 0
    ("01,500", ["01", "500"]),
])
def test_other_commas_end_the_number(text, expected):
    assert [u.text for u in split_units(text)] == expected


def test_thousands_number_keeps_its_span_and_matches_the_spoken_reading():
    text = "要1,500元"
    num = split_units(text)[1]
    assert text[slice(*num.span)] == "1,500" and unit_key(num) == frozenset({"#1500"})
    assert tuple(char_key(c) for c in "一千五百") in unit_alts(num)


def test_latin_runs_are_lowercased_and_digits_split_them():
    assert [(u.text, u.kind) for u in split_units("iPhone15 AI")] == [("iphone", "latin"), ("15", "num"), ("ai", "latin")]
    assert split_units("Ａｂ")[0] == Unit("ab", "latin", (0, 2))


def test_punctuation_and_space_make_no_units():
    assert split_units("，。 \n!?「」—…") == []
    assert split_units("") == []


def test_other_letters_are_one_unit_each():
    us = split_units("あ한д")
    assert [(u.text, u.kind) for u in us] == [("あ", "other"), ("한", "other"), ("д", "other")]


def test_nfkc_is_applied_one_character_at_a_time_and_spans_stay_original():
    assert split_units("㎏") == [Unit("kg", "latin", (0, 1))]
    assert split_units("①") == [Unit("1", "num", (0, 1))]
    assert split_units("a㎏b") == [Unit("akgb", "latin", (0, 3))]


def test_nfkc_expanding_char_is_one_unit():
    (half,) = split_units("½")
    assert half == Unit("1⁄2", "other", (0, 1))
    (company,) = split_units("㍿")
    assert company == Unit("株式会社", "other", (0, 1))
    assert split_units("㌀") == [Unit("アパート", "other", (0, 1))]


@pytest.mark.parametrize("text", ["½", "約 ½ 杯", "1㍿", "5m²", "⅓和⅔", "1½", "½1", "a½b", "㌀㌀", "1,½", "㍿½%", "ﬃ½㎏"])
def test_every_original_character_belongs_to_one_unit(text):
    spans = [u.span for u in split_units(text)]
    assert all(lo < hi for lo, hi in spans)
    assert all(a[1] <= b[0] for a, b in zip(spans, spans[1:])), spans  # in order, no overlap
    covered = {i for lo, hi in spans for i in range(lo, hi)}
    assert covered == {i for i, c in enumerate(text) if split_units(c)}  # every unit-making character is covered


def test_units_around_an_expanding_char_keep_their_own_spans():
    assert [(u.text, u.span) for u in split_units("約 ½ 杯")] == [("約", (0, 1)), ("1⁄2", (2, 3)), ("杯", (4, 5))]
    assert [(u.text, u.span) for u in split_units("1㍿")] == [("1", (0, 1)), ("株式会社", (1, 2))]
    assert [(u.text, u.span) for u in split_units("5m²")] == [("5", (0, 1)), ("m", (1, 2)), ("2", (2, 3))]
    assert [(u.text, u.span) for u in split_units("1½")] == [("1", (0, 1)), ("1⁄2", (1, 2))]


def test_an_expanding_char_is_matched_by_the_keys_of_its_expanded_text():
    (unit,) = split_units("㍿")
    (alt,) = unit_alts(unit)
    assert alt == tuple(char_key(c) for c in "株式会社")
    assert all(key & char_key(c) for key, c in zip(alt, "株式會社"))  # the spoken 株式會社 matches it
    assert unit_key(split_units("½")[0]) == char_key("1⁄2")


def test_decimal_then_comma_is_not_one_number():
    units = [u.text for u in split_units("0.5,000")]
    assert units == ["0.5", "000"]
    assert "0.5,000" not in units and "0.5000" not in units
    assert [u.text for u in split_units("共0.5,000元")] == ["共", "0.5", "000", "元"]


def test_unit_spans_slice_the_original_text():
    text = "我有１５０個 Ａpple，和50%"
    assert [text[a:b] for a, b in (u.span for u in split_units(text))] == [
        "我", "有", "１５０", "個", "Ａpple", "和", "50%"]


def test_homophones_share_key():
    assert char_key("的") & char_key("地") and char_key("体") & char_key("體")
    assert {"xing", "hang"} <= char_key("行")


def test_char_key_falls_back_to_the_lowercase_character():
    assert char_key("A") == frozenset({"a"})
    assert char_key("1") == frozenset({"1"})
    assert char_key("É") == frozenset({"é"})


@pytest.mark.parametrize("ch", ["\ud800", "\x00", "🙂", "한", "々", "", "ab", "𠀀"])
def test_char_key_never_raises(ch):
    key = char_key(ch)
    assert isinstance(key, frozenset) and key


def test_char_key_falls_back_when_pypinyin_raises(monkeypatch):
    def boom(*args, **kwargs):
        raise ValueError("cannot read this")

    char_key.cache_clear()
    monkeypatch.setattr(textnorm, "pinyin", boom)
    try:
        assert char_key("甲") == frozenset({"甲"})
    finally:
        monkeypatch.undo()
        char_key.cache_clear()


def test_unit_key_by_kind():
    latin, num, cjk = split_units("Remotion 150 中")
    assert unit_key(latin) == frozenset({"remotion"})
    assert unit_key(num) == frozenset({"#150"})
    assert unit_key(cjk) == char_key("中") == frozenset({"zhong"})


@pytest.mark.parametrize("digits,expected", [
    ("150", {"一百五十", "一百五", "一五零", "一五〇"}),
    ("2", {"二", "兩"}),
    ("2026", {"兩千零二十六", "二千零二十六", "二零二六"}),
    ("10", {"十", "一十"}),
    ("3.5", {"三點五"}),
    ("0", {"零", "〇"}),
    ("10005", {"一萬零五", "一零零零五"}),
    ("0.5", {"零點五", "〇點五"}),
    ("100", {"一百", "一零零", "一〇〇"}),
    ("15", {"十五", "一十五", "一五"}),
    ("1500", {"一千五百", "一千五"}),
    ("25000", {"二萬五千", "兩萬五千", "兩萬五", "二萬五"}),
    ("250", {"二百五十", "二百五", "兩百五十", "兩百五"}),
    ("1200", {"一千二百", "一千二"}),
    ("2200", {"二千二百", "兩千二百", "兩千兩百"}),
    ("12000", {"一萬二千", "一萬兩千"}),
    ("22000", {"二萬二千", "兩萬二千", "兩萬兩千"}),
    ("200000000", {"二億", "兩億"}),
    ("1020", {"一千零二十"}),
    ("1010", {"一千零一十"}),
    ("110", {"一百一十"}),
    ("100100", {"十萬零一百", "一十萬零一百"}),
    ("1001000", {"一百萬一千"}),
    ("120000", {"十二萬", "一十二萬"}),
    ("1000000000", {"十億", "一十億"}),
    ("123456789", {"一億二千三百四十五萬六千七百八十九", "一二三四五六七八九"}),
    ("100000005", {"一億零五"}),
    ("100001000", {"一億零一千"}),
    ("110000000", {"一億一千萬"}),
    ("1000200003", {"十億零二十萬零三"}),
    ("0.05", {"零點零五", "零點〇五", "〇點〇五"}),
    ("2.5", {"二點五", "兩點五"}),
    ("12.30", {"十二點三零", "十二點三〇"}),
    ("007", {"零零七", "〇〇七"}),
])
def test_number_readings(digits, expected):
    assert expected <= set(number_readings(digits))


@pytest.mark.parametrize("digits,first", [
    ("150", "一百五十"),
    ("2", "二"),
    ("2026", "二千零二十六"),
    ("10", "十"),
    ("0", "零"),
    ("10005", "一萬零五"),
    ("123456789", "一億二千三百四十五萬六千七百八十九"),
    ("3.5", "三點五"),
    ("2.5", "二點五"),
])
def test_number_readings_start_with_the_standard_reading(digits, first):
    assert number_readings(digits)[0] == first


@pytest.mark.parametrize("digits", ["150", "2200", "10", "0.05", "100", "2", "1"])
def test_number_readings_have_no_duplicates(digits):
    readings = number_readings(digits)
    assert len(readings) == len(set(readings))


@pytest.mark.parametrize("digits", ["20", "12", "22", "120", "2020", "200", "20000", "120000", "2200"])
def test_liang_is_never_used_beside_shi(digits):
    for reading in number_readings(digits):
        assert "兩十" not in reading and "十兩" not in reading


def test_liang_only_stands_as_the_coefficient_of_a_unit_or_alone():
    assert not any("兩" in r for r in number_readings("12"))
    assert not any("兩" in r for r in number_readings("102"))
    assert not any("兩" in r for r in number_readings("120000")), "十二萬, never 十兩萬"


def test_liang_skips_a_digit_whose_unit_was_dropped():
    # 2200 -> 兩千二 keeps the last 二: its 百 is left out, so no unit follows it.
    assert {"二千二", "兩千二"} <= set(number_readings("2200"))
    assert not {"兩千兩", "二千兩"} & set(number_readings("2200"))
    assert "兩萬二" in number_readings("22000") and "兩萬兩" not in number_readings("22000")


def test_bare_hundred_is_not_a_reading_of_100():
    readings = number_readings("100")
    assert "百" not in readings and "一百" in readings


@pytest.mark.parametrize("digits,absent", [
    ("1050", {"一千五", "一千零五"}),
    ("1005", {"一百五", "一千五"}),
    ("125", {"一百二", "一百二五"}),
    ("12500", {"一萬二千五", "一萬二"}),
    ("150000", {"十五", "一百五"}),
    # a shortened reading is only for the lowest group: 一億五萬 would be 100050000, not 150000000
    ("1500000", {"一百五萬", "一百五"}),
    ("15000000", {"一千五萬", "一千五"}),
    ("150000000", {"一億五萬", "一億五"}),
])
def test_truncated_readings_only_for_two_adjacent_significant_digits(digits, absent):
    assert absent.isdisjoint(number_readings(digits))


def test_decimals_read_each_fraction_digit_one_by_one():
    readings = number_readings("3.14")
    assert "三點一四" in readings and "三點十四" not in readings
    assert "零點零零五" in number_readings("0.005")


@pytest.mark.parametrize("digits", ["", "abc", ".", "-", "1,500", "٣٤", "3.5.2", "٣.٥", "00", "0000", "000.5", "12345678901234567890"])
def test_odd_input_never_raises(digits):
    readings = number_readings(digits)
    assert isinstance(readings, list) and len(readings) == len(set(readings))


def test_leading_zeros_read_digit_by_digit():
    readings = number_readings("007")
    assert {"零零七", "〇〇七"} <= set(readings)
    assert not any(r in readings for r in ("七", "十七"))


def test_very_long_digit_strings_still_have_digit_by_digit_readings():
    readings = number_readings("9" * 5000)
    assert "九" * 5000 in readings
    assert "一" * 17 in number_readings("1" * 17)


def test_numbers_up_to_sixteen_digits_are_grouped():
    assert number_readings("1000000000000")[0] == "一兆"
    assert number_readings("9999999999999999")[0] == "九千九百九十九兆九千九百九十九億九千九百九十九萬九千九百九十九"


def test_number_readings_percent():
    assert "百分之五十" in number_readings("50", percent=True)


def test_percent_prefixes_every_reading():
    readings = number_readings("3.5", percent=True)
    assert "百分之三點五" in readings and all(r.startswith("百分之") for r in readings)
    assert not any(r.startswith("百分之") for r in number_readings("3.5"))


def test_one_hundred_percent_has_the_idiomatic_reading():
    readings = number_readings("100", percent=True)
    assert readings[0] == "百分之一百" and "百分之百" in readings
    assert "百分之百" not in number_readings("1000", percent=True)


def test_num_unit_alts_include_digits():
    alts = unit_alts(split_units("150")[0])
    assert (frozenset({"#150"}),) in alts and any(len(a) == 3 for a in alts)


def test_num_unit_alts_are_per_character_keys_and_unique():
    alts = unit_alts(split_units("150")[0])
    assert tuple(char_key(c) for c in "一百五十") in alts
    assert alts[-1] == (frozenset({"#150"}),)
    assert len(alts) == len(set(alts))


def test_percent_unit_alts_start_with_the_percent_word():
    alts = unit_alts(split_units("50%")[0])
    prefix = (char_key("百"), char_key("分"), char_key("之"))
    assert all(a[:3] == prefix for a in alts[:-1])
    assert tuple(char_key(c) for c in "百分之五十") in alts
    assert alts[-1] == (frozenset({"#50"}),)


def test_other_unit_alts_are_the_single_unit_key():
    for u in split_units("你 Remotion"):
        assert unit_alts(u) == [(unit_key(u),)]


def test_fillers():
    assert FILLERS == ("嗯", "呃", "欸", "那個", "就是")


def test_to_taiwan():
    assert to_taiwan("这个软件") == "這個軟體"


def test_to_taiwan_keeps_other_text():
    assert to_taiwan("Remotion 3 支，好！") == "Remotion 3 支，好！"
    assert to_taiwan("") == ""
