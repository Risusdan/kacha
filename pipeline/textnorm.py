"""Comparison units, toneless pinyin keys, number readings and Traditional-Chinese conversion.

The aligner compares the speaker's script with the ASR transcript one unit at a time. A
unit is one CJK character, one run of Latin letters, one run of digits, or one other letter.
A character that NFKC turns into several units (½ into 1 and 2, ㍿ into 株式会社) is one unit
on its own, so every character of the original text belongs to one unit only. Two units match
when their keys (sets of toneless pinyin) intersect, so homophones are not errors. A number has
several acceptable spoken forms, listed by `number_readings`.

Pure functions: no file, network or project state. (OpenCC loads its own bundled
dictionary the first time `to_taiwan` is called.)
"""

import unicodedata
from dataclasses import dataclass
from functools import lru_cache

from opencc import OpenCC
from pypinyin import Style, pinyin

Key = frozenset[str]

FILLERS = ("嗯", "呃", "欸", "那個", "就是")


@dataclass(frozen=True)
class Unit:
    """One comparison unit. `text` is the normalised form (NFKC, Latin lower-cased); `span`
    is the half-open range of the original string the unit came from, so for a number that
    carries a percent sign it covers the sign too. A character that NFKC expands into several
    units is one unit of kind "other" whose `text` is the whole expansion."""

    text: str
    kind: str  # "cjk", "latin", "num" or "other"
    span: tuple[int, int]
    percent: bool = False


_ASCII_DIGITS = frozenset("0123456789")


def _classify(c: str) -> str | None:
    """The unit kind of one NFKC-normalised character, or None for punctuation, spaces and
    symbols. Only ASCII digits count as "num"; digits of other scripts are single "other" units.
    An entry of several characters is an expansion `_normalised` keeps whole: "other"."""
    if len(c) > 1:
        return "other"
    if c in _ASCII_DIGITS:
        return "num"
    category = unicodedata.category(c)
    if category[0] == "L":
        name = unicodedata.name(c, "")
        if name.startswith("LATIN"):
            return "latin"
        if name.startswith(("CJK UNIFIED IDEOGRAPH", "CJK COMPATIBILITY IDEOGRAPH")):
            return "cjk"
        return "other"
    if category[0] in "NM":
        return "other"
    return None


def _normalised(text: str) -> list[tuple[str, int, int]]:
    """(character, start, end) for every character of `text` after NFKC, which is applied to
    one original character at a time so each result still knows where it came from. An original
    character whose expansion would make several units (½, ㍿) is one entry holding the whole
    expansion, so no two units share a character of `text`."""
    out = []
    for i, ch in enumerate(text):
        expanded = unicodedata.normalize("NFKC", ch)
        entries = [(c, i, i + 1) for c in expanded]
        if len(entries) > 1 and len(_cut(text, entries)) > 1:
            entries = [(expanded, i, i + 1)]
        out.extend(entries)
    return out


def _thousands_group_at(text: str, chars: list[tuple[str, int, int]], j: int) -> bool:
    """Whether chars[j] starts a thousands group inside a number: a comma written as an ASCII
    "," in `text` (NFKC turns the full-width clause comma into "," too), then exactly three
    digits and no fourth."""
    return (
        j + 3 < len(chars)
        and chars[j][0] == ","
        and text[chars[j][1]] == ","
        and all(chars[k][0] in _ASCII_DIGITS for k in (j + 1, j + 2, j + 3))
        and not (j + 4 < len(chars) and chars[j + 4][0] in _ASCII_DIGITS)
    )


def split_units(text: str) -> list[Unit]:
    return _cut(text, _normalised(text))


def _cut(text: str, chars: list[tuple[str, int, int]]) -> list[Unit]:
    """The units of `chars`, the normalised characters of `text`."""
    n = len(chars)
    units: list[Unit] = []
    i = 0
    while i < n:
        c, start, end = chars[i]
        kind = _classify(c)
        if kind is None:
            i += 1
        elif kind == "latin":
            j = i + 1
            while j < n and _classify(chars[j][0]) == "latin":
                j += 1
            word = "".join(x[0] for x in chars[i:j]).lower()
            units.append(Unit(word, "latin", (start, chars[j - 1][2])))
            i = j
        elif kind == "num":
            j = i + 1
            while j < n and chars[j][0] in _ASCII_DIGITS:
                j += 1
            if j - i <= 3 and c != "0":  # only a leading group of 1 to 3 digits takes separators
                while _thousands_group_at(text, chars, j):
                    j += 4
            if j + 1 < n and chars[j][0] == "." and chars[j + 1][0] in _ASCII_DIGITS:
                j += 2
                while j < n and chars[j][0] in _ASCII_DIGITS:
                    j += 1
            digits = "".join(x[0] for x in chars[i:j] if x[0] != ",")
            percent = j < n and chars[j][0] == "%"
            last = chars[j][2] if percent else chars[j - 1][2]
            units.append(Unit(digits, "num", (start, last), percent))
            i = j + 1 if percent else j
        else:  # "cjk" or "other": one character per unit
            units.append(Unit(c, kind, (start, end)))
            i += 1
    return units


@lru_cache(maxsize=65536)
def char_key(ch: str) -> Key:
    """Every toneless pinyin reading of one character. A character pypinyin cannot read
    (Latin letters, digits, kana, symbols, anything it rejects) is its own key, lower-cased."""
    if len(ch) == 1:
        try:
            readings = pinyin(ch, style=Style.NORMAL, heteronym=True, errors=lambda _: [])
        except Exception:  # whatever pypinyin trips on, the character is its own key
            readings = []
        key = frozenset(r for group in readings for r in group if r)
        if key:
            return key
    return frozenset({ch.lower()})


def unit_key(u: Unit) -> Key:
    if u.kind == "latin":
        return frozenset({u.text})
    if u.kind == "num":
        return frozenset({"#" + u.text})
    return char_key(u.text)


_DIGIT_NAMES = "零一二三四五六七八九"
_SMALL_UNITS = ("", "十", "百", "千")
_GROUP_UNITS = ("", "萬", "億", "兆")
_MAX_GROUPED_DIGITS = 4 * len(_GROUP_UNITS)


def _spell_integer(s: str, liang: str = "none", yi_shi: bool = False, drop_unit_at: int | None = None) -> str:
    """The Chinese reading of the integer `s` (no leading zero, at most 16 digits).

    liang: "none" reads every 2 as 二; "all" reads a 2 as 兩 wherever it is the coefficient of
    a 百 or 千 that is spoken, or is a whole 萬/億/兆 group on its own (兩萬, 兩億), or is the
    whole number; "lead" does that only for the first digit. 十 never takes 兩.
    yi_shi: a number that starts 10 to 19 reads 十… by default and 一十… when set.
    drop_unit_at: the position (0 = ones) whose 十/百/千 is left out, for 150 -> 一百五.
    """
    if s == "0":
        return "零"
    n = len(s)
    group_count = -(-n // 4)
    padded = s.zfill(group_count * 4)
    out: list[str] = []
    skipped_group = False
    for gi in range(group_count):
        group = padded[gi * 4:(gi + 1) * 4]
        if group == "0000":
            skipped_group = bool(out)
            continue
        base = 4 * (group_count - 1 - gi)
        text: list[str] = []
        if out and (skipped_group or group[0] == "0"):
            text.append("零")
        skipped_group = False
        started = False
        zero_run = False
        for j, d in enumerate(group):
            if d == "0":
                zero_run = started
                continue
            if zero_run:
                text.append("零")
                zero_run = False
            local = 3 - j
            pos = base + local
            numeral = _DIGIT_NAMES[int(d)]
            if d == "2":
                takes_liang = (
                    (local >= 2 and pos != drop_unit_at)
                    or n == 1
                    or (local == 0 and group == "0002" and gi < group_count - 1)
                )
                if takes_liang and (liang == "all" or (liang == "lead" and pos == n - 1)):
                    numeral = "兩"
            if d == "1" and local == 1 and not out and not started and not yi_shi:
                numeral = ""
            started = True
            text.append(numeral)
            if local and pos != drop_unit_at:
                text.append(_SMALL_UNITS[local])
        text.append(_GROUP_UNITS[group_count - 1 - gi])
        out.append("".join(text))
    return "".join(out)


def _integer_spellings(s: str) -> list[str]:
    """Standard reading first, then the 兩, 一十 and shortened (150 -> 一百五) variants.
    Empty when `s` is not read as one number: a leading zero or more than 16 digits."""
    n = len(s)
    if (n > 1 and s[0] == "0") or n > _MAX_GROUPED_DIGITS:
        return []
    # A shortened reading needs exactly two significant digits side by side, with the
    # second one's unit (十, 百 or 千 of the lowest group) being what the speaker drops.
    droppable = 3 <= n <= 5 and s[1] != "0" and set(s[2:]) == {"0"}
    out = []
    for drop in ((None, n - 2) if droppable else (None,)):
        for liang in ("none", "lead", "all"):
            for yi_shi in (False, True):
                out.append(_spell_integer(s, liang, yi_shi, drop))
    return list(dict.fromkeys(out))


def _by_digit(s: str, zero: str) -> str:
    """Read the digits of `s` one by one, with `zero` for 0 and 點 for a decimal point.
    Other characters are skipped."""
    names = _DIGIT_NAMES.replace("零", zero)
    return "".join("點" if c == "." else names[int(c)] for c in s if c == "." or c in _ASCII_DIGITS)


def _is_digits(s: str) -> bool:
    return bool(s) and all(c in _ASCII_DIGITS for c in s)


def number_readings(digits: str, percent: bool = False) -> list[str]:
    """Every way a speaker may read the number `digits`, the standard reading first.

    An integer reads its 萬/億/兆 groups with 零 for the skipped places (10005 -> 一萬零五,
    up to 16 digits). The variants are 兩 for 2 (兩千, 兩百, 兩萬), 一十 for 10 to 19, the
    shortened 150 -> 一百五 and 1500 -> 一千五, and digit by digit with 零 or 〇. A decimal
    reads its integer part like an integer and every digit after 點 on its own. Odd input
    (leading zeros, more than 16 digits, stray characters) never raises and always keeps the
    digit-by-digit readings; input with no ASCII digit has no readings. With `percent`,
    every reading starts with 百分之, and 100 also reads 百分之百."""
    s = unicodedata.normalize("NFKC", digits).strip()
    if not any(c in _ASCII_DIGITS for c in s):
        return []
    whole, dot, frac = s.partition(".")
    if dot and not frac:  # "3." reads as 3
        s, dot = whole, ""
    spoken: list[str] = []
    if _is_digits(whole) and (not dot or _is_digits(frac)):
        fractions = [""]
        if dot:
            fractions = ["點" + _by_digit(frac, "零")]
            if "0" in frac:
                fractions.append("點" + _by_digit(frac, "〇"))
        spoken = [w + f for w in _integer_spellings(whole) for f in fractions]
    if percent and s == "100":
        spoken.append("百")  # 百分之百 is how 100% is said
    spoken += [_by_digit(s, "零"), _by_digit(s, "〇")]
    readings = list(dict.fromkeys(spoken))
    return ["百分之" + r for r in readings] if percent else readings


def unit_alts(u: Unit) -> list[tuple[Key, ...]]:
    """The ways a unit may be matched, each one a sequence of per-character keys. A number
    can be spoken as any of its readings or kept as digits (the "#150" key). A character that
    NFKC expanded (㍿ into 株式会社) is matched by one key per character of the expansion."""
    if u.kind == "other" and len(u.text) > 1:
        return [tuple(char_key(c) for c in u.text)]
    if u.kind != "num":
        return [(unit_key(u),)]
    alts = [tuple(char_key(c) for c in reading) for reading in number_readings(u.text, u.percent)]
    alts.append((frozenset({"#" + u.text}),))
    return list(dict.fromkeys(alts))


@lru_cache(maxsize=1)
def _s2twp() -> OpenCC:
    return OpenCC("s2twp")


def to_taiwan(text: str) -> str:
    """Simplified to Traditional Chinese with Taiwan wording (OpenCC s2twp)."""
    return _s2twp().convert(text)
