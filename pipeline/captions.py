"""Captions: line breaking, event times on the cut reel, the fixed placement, the ASS file.

A caption is one line of at most 16 characters (a full-width character counts 1, a half-width one
0.5, the sum rounded up) that is also no wider than the safe area minus the outline, measured with
the advance widths of the bold font the render uses. Breaking is done here, never by libass.

Display text: the separators are ，、；：。 and the half-width , ; : and . Shown as written instead, not
as separators: a half-width , : or . between two letters or digits that are ASCII after NFKC (1,5, １,５,
10:30, Node.js), a : right before // (https://), and a . next to another one (...). A caption ends at
every separator but 、 (see below), so no line shows one of them; 、 shows inside a line as written and goes
from the end of one; ？ ！ and the quotes and brackets stay. A run of spaces and separators between two
tokens is one character: 、 when that is the only separator in it, an ideographic space (SEPARATOR, where a
caption ends) when it holds another, and a half-width space when it holds none. Inside a unit's span,
where a caption text can put one (吃飯。啦: 啦 joins the unit of 飯), a run shows as it would between two
units: it goes after an opening mark and before a closing or terminating mark (吃飯。」啦 shows 吃飯」啦), and
elsewhere is that one character. The Markdown marks that survive in a sentence are not
shown, inside a unit's span either (兩**小**時 as a caption text), by the rule parse_script uses
(markdown_marks: **x**, __x__, ***x***, ~~x~~, `x`, *x* and a link's brackets); a lone * _ or ~ is text
(5*3, file_name, 3~5). Spaces between Latin words or numbers and CJK stay as one half-width space.

Where a line may break: after a separator, and after ？ or ！ with the marks and closing quotes that
follow it (a punctuation break); elsewhere across a space, or where a word ends (words.word_ends of the
sentence text), but never after an opening mark, before a closing or terminating mark or a separator,
next to any other sign, or inside a run of Latin letters and digits. One clause per caption: a caption
ends at every punctuation break but one after 、 alone, however short the clause before it, and each
clause is broken into lines on its own by the rules below; a clause that fits a line is one line. A unit
that shows a separator other than 、 (a caption text put one between two of its characters) is cut there,
as a caption ends there; a piece of it is on each side. Inside a stretch between two neighbouring breaks
that does not fit a line alone (a long word or address, a CJK run with no word end), a line may also break
between any two characters; nowhere else is a word cut. A space or 、 that a unit there shows (see Display
text) is cut as one between two units is: a line may break after it (a punctuation break after 、) but not
before it, and it goes from the end of a line. A unit cut there or at a separator has a piece on each of
its lines, with the share of its characters as its share of the unit's time (Piece.part; the share of a
space or separator that goes shows on no line), and a line that starts or ends inside such a stretch,
other than right after such a space or separator, has split_word set.

Of all the ways to break a clause the one taken is the best by, in this order: the fewest lines; the
fewest short lines (2 characters wide or less, not counted when the whole clause is that short: such a
clause is one line); the fewest breaks that are not punctuation breaks (a break between two characters
of a long word is one); the fewest lines after the first that start with a particle (PARTICLES: 的 地 得
了 著 們 and the particles that end a sentence, by the first character, whatever word it starts); the
least sum of squared differences between the line widths in pixels and their mean. Where a long word is
cut is chosen by the same rules, with all the other breaks. The best is found exactly, by dynamic
programming over the breaks.

Event times: an event starts when the first unit of its line is spoken and ends one hold after the last
one, or when the next event starts, whichever is first, and never after the reel ends. A piece of a unit
cut across lines starts and ends its share of the way through the unit's time. Every event is at least
one frame long: when the rules leave less, the later events move, not the text.

Caption text: edit.json captions.text can give a sentence the text its captions show in place of its own.
The sentence's units keep their times and take the characters of the new text that came from their own
(remap_units), so the new text shows when the old one was spoken; caption_source gives the text and units
that are broken into lines and timed. The rules above hold for the new text as they do for any other.
"""

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple

from fontTools.ttLib import TTFont, TTLibError

from pipeline.edit import LINE_BREAK_CHARS, Segment, caption_texts, kept_sentences, map_time, total_duration
from pipeline.errors import PipelineError
from pipeline.script import markdown_marks
from pipeline.style import font_path
from pipeline.words import word_ends

LONG_WORD_WARNING = "long word split across caption lines"
SEPARATOR = "　"  # U+3000: what a run of separators that ends a caption becomes; no line shows it
ENUMERATION_COMMA = "、"  # the one separator a line shows: a caption ends at every other
STYLE_NAME = "Caption"
SHORT_LINE = 2  # a line at most this wide (width_units) is short
# The characters a line had rather not start with: particles that belong to the words before them (問我用 / 的是哪一套
# reads badly). A line counts by its first character, not by the word it starts: the dictionary can join a
# particle to the character after it (認真地去面對: 地去).
PARTICLES = frozenset("的地得了著們嗎呢吧啊呀啦喔哦嘛耶囉")
EPS = 1e-9

_OPENERS = frozenset("「『《〈（([【〔“‘")
_CLOSERS = frozenset("」』》〉）)]】〕”’")
_TERMINATORS = frozenset("！？!?")
_SEPARATORS = frozenset("，、；：。,;:")  # and the half-width full stop; see _kind for the exceptions
_LINE_BREAKS = dict.fromkeys(map(ord, LINE_BREAK_CHARS), " ")
_OVERRIDE_CHARS = {ord("{"): "｛", ord("}"): "｝", ord("\\"): "＼", **_LINE_BREAKS}


@dataclass
class Piece:
    """A piece of a caption line. `unit` is the index of the sentence unit it shows (units[unit] of
    transcript.json), None for punctuation and spaces. `part` is the share of the unit's time the piece
    takes, from and to, as fractions of it: (0, 1) for a whole unit, the share of its characters for a
    piece of a unit cut across lines."""

    text: str
    unit: int | None
    part: tuple[float, float] = (0.0, 1.0)


@dataclass
class Line:
    """One caption line: its pieces in order. `text` is what it shows. `split_word` is True when the
    line starts or ends inside a stretch too wide for a line, broken between two of its characters (a
    long word split across lines)."""

    pieces: list[Piece]
    split_word: bool = False

    @property
    def text(self) -> str:
        return "".join(piece.text for piece in self.pieces)


@dataclass
class Event:
    """One caption on the cut reel: `start` and `end` in seconds, the line, and for every piece of the
    line the time it starts (None for a piece that has no time: punctuation, spaces)."""

    start: float
    end: float
    line: Line
    times: list[float | None]


# ---- the font ---------------------------------------------------------------------------------


@lru_cache(maxsize=8)
def _metrics(path: str, mtime_ns: int) -> tuple[dict[int, int], int]:
    """(advance, in font units, of every code point of the font; font units per em)."""
    try:
        font = TTFont(path)
        try:
            advances = font["hmtx"].metrics
            cmap = font.getBestCmap()
            return {code: advances[name][0] for code, name in cmap.items()}, font["head"].unitsPerEm
        finally:
            font.close()
    except (OSError, TTLibError, KeyError) as error:
        raise PipelineError(f"cannot read the font {path}: {error}") from error


@lru_cache(maxsize=8)
def _cell(path: str, mtime_ns: int) -> float:
    """The cell height of the font in em: hhea ascent less (negative) descent over the units per em."""
    try:
        font = TTFont(path)
        try:
            return (font["hhea"].ascent - font["hhea"].descent) / font["head"].unitsPerEm
        finally:
            font.close()
    except (OSError, TTLibError, KeyError) as error:
        raise PipelineError(f"cannot read the font {path}: {error}") from error


def _font_key(font: Path) -> tuple[str, int]:
    path = Path(font)
    try:
        return str(path), path.stat().st_mtime_ns
    except OSError as error:
        raise PipelineError(f"cannot read the font {path}: {error.strerror or error}") from error


def _font_metrics(font: Path) -> tuple[dict[int, int], int]:
    return _metrics(*_font_key(font))


def _advance(text: str, font: Path) -> int:
    """The width of `text` in font units: the sum of the advance widths of `font`, one em for a character
    the font has no glyph for. An integer, so widths add up and compare exactly."""
    advances, per_em = _font_metrics(font)
    return sum(advances.get(ord(c), per_em) for c in text)


def text_px(text: str, font: Path, size_px: float) -> float:
    """The width of `text` in pixels at `size_px`: the sum of the advance widths of `font`. A character
    the font has no glyph for counts as one em."""
    _, per_em = _font_metrics(font)
    return _advance(text, font) * size_px / per_em


def missing_glyphs(text: str, font: Path) -> set[str]:
    """The characters of `text` that `font` has no glyph for."""
    advances, _ = _font_metrics(font)
    return {c for c in text if ord(c) not in advances}


def width_units(text: str) -> int:
    """The length of `text` for the 16-character limit: a full-width character is 1, a half-width one
    0.5, rounded up. Letters and digits are half-width unless the East Asian width says full; so are
    the ambiguous signs (quotes, ellipsis, dash) that Chinese text sets full-width."""
    halves = 0
    for c in text:
        width = unicodedata.east_asian_width(c)
        halves += 2 if width in "FW" or (width == "A" and not c.isalnum()) else 1
    return (halves + 1) // 2


# ---- line breaking ----------------------------------------------------------------------------


class _Tok(NamedTuple):
    piece: Piece
    kind: str  # "unit", "open", "close", "term" (？！), "sep" (，、；：。,;: and .), "space" or "other"
    pos: int  # where it starts in the sentence text (for a letter of a unit, see _letters)


def _kind(text: str, i: int) -> str:
    """The kind of text[i], a character outside every unit (or inside a unit's span: _unit_text). A
    half-width comma, colon or full stop between two letters or digits that are ASCII after NFKC (1,5,
    １,５, 10:30, Node.js) is no separator but shown as written, as split_units, which compares after NFKC,
    keeps １,０００ one number; so is a colon right before // (https://) and a full stop next to another one
    (...)."""
    c = text[i]
    if c.isspace():
        return "space"
    if c in _OPENERS:
        return "open"
    if c in _CLOSERS:
        return "close"
    if c in _TERMINATORS:
        return "term"
    if c in ",:.":
        before, after = (unicodedata.normalize("NFKC", n) for n in (text[i - 1:i], text[i + 1:i + 2]))
        if (_is_word_char(before) and _is_word_char(after)) or (c == ":" and text.startswith("//", i + 1)):
            return "other"
        if c == "." and "." in (before, after):
            return "other"
    if c in _SEPARATORS or c == ".":
        return "sep"
    return "other"


def _blank(separators: set[str]) -> str:
    """The one character a run of spaces and separators between two tokens shows, by the separators in it:
    SEPARATOR when one of them is not 、 (a caption ends there, so no line shows it), 、 when that is the only
    one, and a half-width space when there is none."""
    if not separators:
        return " "
    return ENUMERATION_COMMA if separators == {ENUMERATION_COMMA} else SEPARATOR


def _unit_text(text: str, lo: int, hi: int, marks: set[int]) -> str:
    """What a unit whose span is text[lo:hi] shows: the span as written without the characters of `marks`
    (the indexes of the Markdown marks of the text), with each run of spaces and separators in it (by _kind
    on the whole text, so 1,5 10:30 Node.js and ... stay as written) shown as _tidy shows such a run between
    two tokens: dropped after an opening mark or before a closing or terminating mark, elsewhere one _blank.
    A caption text puts a mark or a separator inside a unit's span when it puts one between two characters
    of the unit (remap_units: 吃飯。啦 shows 吃飯　啦, where break_lines ends a caption as between two tokens;
    吃飯、啦 shows 吃飯、啦; 吃飯。」啦 shows 吃飯」啦; 兩**小**時 shows 兩小時)."""
    shown = [i for i in range(lo, hi) if i not in marks]
    kinds = [_kind(text, i) for i in shown]
    out: list[str] = []
    k = 0
    while k < len(shown):
        if kinds[k] not in ("space", "sep"):
            out.append(text[shown[k]])
            k += 1
            continue
        j = k
        while j < len(shown) and kinds[j] in ("space", "sep"):
            j += 1
        before = kinds[k - 1] if k else None
        after = kinds[j] if j < len(shown) else None
        if before != "open" and after not in ("close", "term"):
            out.append(_blank({text[shown[m]] for m in range(k, j) if kinds[m] == "sep"}))
        k = j
    return "".join(out)


def _tokens(text: str, units: list[dict]) -> list[_Tok]:
    """`text` as tokens: one per unit (the text of its span, as _unit_text shows it), one per other
    character. The characters that markdown_marks finds are left out, inside a unit's span too."""
    by_start = {}
    for index, unit in enumerate(units):
        lo, hi = unit["span"]
        if hi > lo:
            by_start[lo] = (hi, index)
    marks = {i for lo, hi in markdown_marks(text) for i in range(lo, hi)}
    toks: list[_Tok] = []
    i = 0
    while i < len(text):
        if i in by_start:
            end, index = by_start[i]
            toks.append(_Tok(Piece(_unit_text(text, i, end, marks), index), "unit", i))
            i = end
            continue
        if i not in marks:
            toks.append(_Tok(Piece(text[i], None), _kind(text, i), i))
        i += 1
    return toks


def _tidy(toks: list[_Tok]) -> list[_Tok]:
    """`toks` with each run of spaces and separators dropped when it is at either end of the text, after
    an opening mark, or before a closing or terminating mark; elsewhere it becomes one token, its _blank: a
    "sep" token when that is SEPARATOR or 、, a "space" token when it is a half-width space."""
    out: list[_Tok] = []
    i = 0
    while i < len(toks):
        if toks[i].kind not in ("space", "sep"):
            out.append(toks[i])
            i += 1
            continue
        j = i
        while j < len(toks) and toks[j].kind in ("space", "sep"):
            j += 1
        before = out[-1].kind if out else None
        after = toks[j].kind if j < len(toks) else None
        if before not in (None, "open") and after not in (None, "close", "term"):
            blank = _blank({t.piece.text for t in toks[i:j] if t.kind == "sep"})
            out.append(_Tok(Piece(blank, None), "space" if blank == " " else "sep", toks[i].pos))
        i = j
    return out


def _phrase_ends(toks: list[_Tok]) -> list[int]:
    """The punctuation breaks, each as the index of the token after it: after every separator, and after
    every ？ or ！ with the marks and the closing quotes that follow it."""
    ends = []
    ended = False  # a ？ or ！ since the last break
    for k, tok in enumerate(toks):
        ended = ended or tok.kind == "term"
        following = toks[k + 1].kind if k + 1 < len(toks) else None
        if tok.kind == "sep" or (ended and tok.kind in ("term", "close") and following not in ("term", "close")):
            ends.append(k + 1)
            ended = False
    return ends


def _shown_bounds(toks: list[_Tok]) -> tuple[int, int]:
    """(lo, hi): toks[lo:hi] is `toks` without the separators and spaces at either end."""
    lo, hi = 0, len(toks)
    while lo < hi and toks[lo].kind in ("sep", "space"):
        lo += 1
    while hi > lo and toks[hi - 1].kind in ("sep", "space"):
        hi -= 1
    return lo, hi


def _trim(toks: list[_Tok]) -> list[_Tok]:
    """`toks` without the separators and spaces at either end."""
    lo, hi = _shown_bounds(toks)
    return toks[lo:hi]


def _shown(toks: list[_Tok]) -> str:
    """The text `toks` show as one line: without the separators and spaces at either end."""
    return "".join(t.piece.text for t in _trim(toks))


@dataclass(frozen=True)
class _Limits:
    font: Path
    chars: int
    px: float  # the widest a line may be: the safe area less the outline on both sides
    size: float

    def fits_text(self, text: str) -> bool:
        return width_units(text) <= self.chars and text_px(text, self.font, self.size) <= self.px

    def fits(self, toks: list[_Tok]) -> bool:
        """Whether `toks` fit one line, the separators and spaces at either end left out."""
        return self.fits_text(_shown(toks))


def _limits(font: Path, style: dict) -> _Limits:
    """The line limits of `style` (captions.max_chars_per_line, size_px, outline_px and the safe area),
    measured with `font`."""
    caps, safe = style["captions"], style["safe_area"]
    return _Limits(font, caps["max_chars_per_line"], safe["x1"] - safe["x0"] - 2 * caps["outline_px"],
                   caps["size_px"])


def _is_word_char(c: str) -> bool:
    return c.isascii() and c.isalnum()


def _can_cut(left: _Tok, right: _Tok, spaced: bool) -> bool:
    """Whether a line may end after `left` and the next start with `right`. Across a space always;
    otherwise not after an opening mark, before a closing or terminating mark or a separator, next to
    any other sign, or inside a run of Latin letters and digits."""
    if spaced:
        return True
    if left.kind in ("open", "other") or right.kind in ("close", "term", "sep", "other"):
        return False
    return not (_is_word_char(left.piece.text[-1]) and _is_word_char(right.piece.text[0]))


def _cut_points(toks: list[_Tok], ends: frozenset[int]) -> list[int]:
    """The indexes of the tokens that a new line may start with, the punctuation breaks aside: where
    _can_cut allows it and the break is across a space or a word of `ends` ends where the token starts.
    The spaces before one stay at the end of the line before it, where trimming takes them off."""
    cuts = []
    i = 0
    while i < len(toks):
        if toks[i].kind == "space":
            i += 1
            continue
        j = i + 1
        while j < len(toks) and toks[j].kind == "space":
            j += 1
        spaced = j > i + 1
        if j < len(toks) and _can_cut(toks[i], toks[j], spaced) and (spaced or toks[j].pos in ends):
            cuts.append(j)
        i = j
    return cuts


def _punctuation_breaks(toks: list[_Tok]) -> dict[int, bool]:
    """The punctuation breaks of `toks` (_phrase_ends), as {index of the token the next line starts with:
    whether a caption ends there}. A caption ends at every one but a break after 、 alone. Each is moved past
    the spaces and separators after it, which trimming takes off either line anyway, so that no line starts
    with one."""
    found: dict[int, bool] = {}
    for k in _phrase_ends(toks):
        ends_caption = toks[k - 1].piece.text != ENUMERATION_COMMA
        while k < len(toks) and toks[k].kind in ("space", "sep"):
            k += 1
        if k < len(toks):
            found[k] = found.get(k, False) or ends_caption
    return found


def _clauses(toks: list[_Tok]) -> list[list[_Tok]]:
    """`toks` cut wherever a caption ends (_punctuation_breaks): the clauses, each broken into lines on its
    own. A clause starts with no space or separator; the spaces and separators it ends with are trimmed off
    its last line."""
    if not toks:
        return []
    bounds = [0, *sorted(k for k, ends_caption in _punctuation_breaks(toks).items() if ends_caption), len(toks)]
    return [toks[a:b] for a, b in zip(bounds, bounds[1:])]


def _open_units(toks: list[_Tok]) -> list[_Tok]:
    """`toks` with each unit that shows SEPARATOR (a caption text put a separator other than 、 between two
    of its characters: _unit_text) made into one token per character (_letters), so that a caption ends
    there as it does between two tokens."""
    return [t for tok in toks for t in (_letters(tok) if tok.kind == "unit" and SEPARATOR in tok.piece.text
                                         else [tok])]


def _breaks(toks: list[_Tok], ends: frozenset[int]) -> dict[int, bool]:
    """Every place a line of `toks` may break, as {index of the token the next line starts with: whether
    it is a punctuation break}; words end at `ends` (indexes into the sentence text). A punctuation break
    is moved past the spaces and separators after it (_punctuation_breaks)."""
    breaks = dict.fromkeys(_cut_points(toks, ends), False)
    breaks.update(dict.fromkeys(_punctuation_breaks(toks), True))
    return breaks


def _letters(tok: _Tok) -> list[_Tok]:
    """`tok` as one token per character, each with its share of the unit's characters as `part`. The
    ideographic space, 、 or half-width space that _unit_text made of a run inside a unit is a "sep", "sep" or
    "space" token, as that run is between two tokens, so that _trim takes it off the end of a line. A letter's pos is
    the unit's pos plus its place in the text the unit shows, which is its place in the sentence text only
    while the unit shows its span as written (no run made one space, no mark left out); nothing reads the pos
    of a letter."""
    size = len(tok.piece.text)
    if size == 1:
        return [tok]
    blanks = {SEPARATOR: "sep", ENUMERATION_COMMA: "sep", " ": "space"} if tok.kind == "unit" else {}
    return [_Tok(Piece(c, tok.piece.unit, (k / size, (k + 1) / size)), blanks.get(c, tok.kind), tok.pos + k)
            for k, c in enumerate(tok.piece.text)]


def _split_wide(
    toks: list[_Tok], breaks: dict[int, bool], limits: _Limits
) -> tuple[list[_Tok], dict[int, bool], set[int]]:
    """`toks` with every stretch between two neighbouring breaks that does not fit a line alone made into
    one token per character, with a break between every two of them: (the tokens, the breaks as indexes
    into them, the breaks inside such stretches). A break inside is not a punctuation break; which of
    them are taken is left to _best_bounds, with all the other breaks. A stretch that fits is never cut.

    A space or separator from inside a unit (_letters) is cut as one between two tokens is: no break
    before it, and the break after it is no break inside the stretch (no word is cut there), a punctuation
    break if it is a separator."""
    bounds = [0, *sorted(breaks), len(toks)]
    out: list[_Tok] = []
    moved: dict[int, bool] = {}
    inside: set[int] = set()
    for a, b in zip(bounds, bounds[1:]):
        if a:
            moved[len(out)] = breaks[a]
        stretch = toks[a:b]
        if limits.fits(stretch):
            out.extend(stretch)
            continue
        lo, hi = _shown_bounds(stretch)
        out.extend(stretch[:lo])
        after = None  # the kind of the letter before when it is a space or a separator
        for k, tok in enumerate(t for tok in stretch[lo:hi] for t in _letters(tok)):
            if k and tok.kind not in ("space", "sep"):
                moved[len(out)] = after == "sep"
                if after is None:
                    inside.add(len(out))
            after = tok.kind if tok.kind in ("space", "sep") else None
            out.append(tok)
        out.extend(stretch[hi:])
    return out, moved, inside


def _merged(pieces: list[Piece]) -> list[Piece]:
    """`pieces` with each run of pieces of one unit joined into one piece, from the first's share of the
    unit's time to the last's."""
    out: list[Piece] = []
    for piece in pieces:
        if out and piece.unit is not None and out[-1].unit == piece.unit:
            last = out[-1]
            out[-1] = Piece(last.text + piece.text, piece.unit, (last.part[0], piece.part[1]))
        else:
            out.append(piece)
    return out


def _best_bounds(toks: list[_Tok], breaks: dict[int, bool], limits: _Limits) -> list[int]:
    """The bounds of the best lines of `toks` (0, the breaks taken, len(toks)), where `breaks` says where
    a line may break and whether that is a punctuation break. Best is, in this order: the fewest lines,
    the fewest short lines, the fewest breaks that are not punctuation breaks, the fewest lines after the
    first that start with one of PARTICLES, the least sum of squared differences between the line widths
    and their mean.

    Exact, by dynamic programming: first the fewest lines n, then over states (break reached, lines so far,
    their total width), each keeping the least (short lines, breaks not at punctuation, lines that start
    with a particle, sum of squared widths) of the ways to reach it. The total width is part of the state
    because breaking at a separator or a space drops it, so breakings into n lines differ in total. Whatever
    follows a state adds the same to each of those four sums and to the total, and the sum of squared
    differences from the mean is (n * sum(w²) - total²) / n, so the least of a state's ways is the least for
    every ending: keeping only it loses nothing. Widths are integers (font units), so every comparison is
    exact. A line starts with the token at its bound: no break is before a space or a separator."""
    bounds = [0, *sorted(breaks), len(toks)]
    last = len(bounds) - 1
    # lines[a]: (b, width, 1 if short) of every line that may run from bounds[a] to bounds[b]. A line only
    # grows with b, so the search stops at the first that does not fit. The line to the next bound always
    # counts: it fits, unless it is a single character too wide for a line.
    lines: list[list[tuple[int, int, int]]] = []
    for a in range(last):
        found = []
        for b in range(a + 1, last + 1):
            line = toks[bounds[a]:bounds[b]]
            if b > a + 1 and not limits.fits(line):
                break
            shown = _shown(line)
            # a clause short as a whole fits one line and never gets here: its exemption needs no code
            found.append((b, _advance(shown, limits.font), int(width_units(shown) <= SHORT_LINE)))
        lines.append(found)

    fewest = [len(bounds)] * (last + 1)
    fewest[0] = 0
    for a in range(last):
        for b, _, _ in lines[a]:
            fewest[b] = min(fewest[b], fewest[a] + 1)
    count = fewest[last]

    # states[b][(lines so far, their total width)] = ((short, not punctuation, particle, squares), the state
    # before)
    states: list[dict] = [{} for _ in range(last + 1)]
    states[0][(0, 0)] = ((0, 0, 0, 0), None)
    for a in range(last):
        for (made, total), (cost, _) in states[a].items():
            for b, width, short in lines[a]:
                if made + 1 > count or (b == last) != (made + 1 == count):
                    continue
                plain = int(b < last and not breaks[bounds[b]])
                particle = int(b < last and toks[bounds[b]].piece.text[:1] in PARTICLES)
                step = (cost[0] + short, cost[1] + plain, cost[2] + particle, cost[3] + width * width)
                key = (made + 1, total + width)
                if key not in states[b] or step < states[b][key][0]:
                    states[b][key] = (step, (a, (made, total)))

    def overall(item) -> tuple[int, int, int, int]:
        (_, total), (cost, _) = item
        return cost[0], cost[1], cost[2], count * cost[3] - total * total

    _, (_, came) = min(states[last].items(), key=overall)
    taken = [last]
    while came is not None:
        a, key = came
        taken.append(a)
        came = states[a][key][1]
    return [bounds[k] for k in reversed(taken)]


def break_lines(text: str, units: list[dict], font: Path, style: dict) -> list[Line]:
    """The caption lines of a sentence, broken by the rules of the module text. `text` and `units` (each
    with a `span` into `text`) are those of a transcript.json sentence; `font` is the static font the
    render uses; the limits come from style captions.max_chars_per_line, size_px, outline_px and the safe
    area. The result has the pieces of every unit, in order (a unit cut across lines one on each of its
    lines), and no line is empty. A clause too wide for one line needs the caption dictionary (word_ends
    raises PipelineError without it)."""
    limits = _limits(font, style)
    lines: list[Line] = []
    ends: frozenset[int] | None = None
    for clause in _clauses(_open_units(_tidy(_tokens(text, units)))):
        if limits.fits(clause):
            lines.append(Line(_merged([tok.piece for tok in _trim(clause)])))
            continue
        if ends is None:
            ends = word_ends(text)
        toks, breaks, inside = _split_wide(clause, _breaks(clause, ends), limits)
        bounds = _best_bounds(toks, breaks, limits)
        lines.extend(Line(_merged([tok.piece for tok in _trim(toks[a:b])]), split_word=a in inside or b in inside)
                     for a, b in zip(bounds, bounds[1:]))
    return lines


# ---- caption text from edit.json --------------------------------------------------------------


def remap_units(old: str, units: list[dict], new: str) -> list[dict]:
    """The `units` of the sentence text `old` as units of the caption text `new`: each keeps its other
    fields (its times) and gets the characters of `new` that came from its own, so that the new characters
    show when the old ones were spoken. `old` and `new` are compared character by character
    (difflib.SequenceMatcher). A character of `new` that is kept belongs to the unit of its old character.
    Where old[i1:i2] is replaced by new[j1:j2], the k-th new character belongs to the unit of the old
    character i1 + k * (i2 - i1) // (j2 - j1), so the old units are spread over the new characters; when no
    unit holds that character (an opening mark, a symbol, a space), to the unit of the nearest character of
    old[i1:i2] that one holds, the earlier of two as near (「好 replaced by 棒: 棒 takes the unit of 好), and
    to none yet when no character of old[i1:i2] has a unit. An inserted character belongs to none yet.

    Four kinds of character stay no unit's: a space or a punctuation mark (a _kind other than "other"), a
    Markdown mark of `new` (markdown_marks: the captions do not show it), and a kept character that no
    unit holds in `old` either (the + of A+B), so that the same text gives the same units. A unit's new span
    runs from its first character to its last, so one of them between two characters of a unit lies inside
    it, and none starts or ends a span. Every other character without a unit takes the unit of the nearest
    character before it, and at the head of the text the one after it. A unit left with no character is
    dropped; `units` is not changed."""
    owner_of_old: list[int | None] = [None] * len(old)
    for index, unit in enumerate(units):
        for i in range(*unit["span"]):
            owner_of_old[i] = index
    owner: list[int | None] = [None] * len(new)
    bare = {j for lo, hi in markdown_marks(new) for j in range(lo, hi)}  # the characters no unit may take
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if tag == "equal":
            owner[j1:j2] = owner_of_old[i1:i2]
            bare.update(j1 + k for k in range(i2 - i1) if owner_of_old[i1 + k] is None)
        elif tag == "replace":
            held = [i for i in range(i1, i2) if owner_of_old[i] is not None]
            for k in range(j2 - j1):
                i = i1 + (k * (i2 - i1)) // (j2 - j1)
                if owner_of_old[i] is None and held:
                    i = min(held, key=lambda h: (abs(h - i), h))  # the nearest, the earlier of two as near
                owner[j1 + k] = owner_of_old[i]
    bare.update(j for j in range(len(new)) if _kind(new, j) != "other")
    for j in bare:
        owner[j] = None
    current = next((index for index in owner if index is not None), None)  # what the head takes
    for j, index in enumerate(owner):
        if index is not None:
            current = index
        elif j not in bare:
            owner[j] = current
    first: dict[int, int] = {}
    last: dict[int, int] = {}
    for j, index in enumerate(owner):
        if index is not None:
            first.setdefault(index, j)
            last[index] = j
    return [{**unit, "text": new[first[index]:last[index] + 1], "span": [first[index], last[index] + 1]}
            for index, unit in enumerate(units) if index in first]


def caption_source(sentence: dict, edit: dict) -> tuple[str, list[dict]]:
    """(text, units) that the captions of `sentence` are made of: the sentence's own, or, when
    captions.text of `edit` has a text for it, that text and the units remap_units makes of the sentence's
    for it."""
    text = caption_texts(edit).get(sentence["id"])
    if text is None:
        return sentence["text"], sentence["units"]
    return text, remap_units(sentence["text"], sentence["units"], text)


# ---- event times ------------------------------------------------------------------------------


@dataclass
class _Row:
    line: Line
    times: list[float | None]  # on the cut reel, per piece
    start: float
    wish: float  # the end the hold gives, the reel's end at most


def _at(unit: dict, share: float, segments: list[Segment]) -> float | None:
    """The time on the cut reel `share` of the way through `unit` (0 its start, 1 its end), or None when
    the unit lacks a time this needs."""
    start, end = unit.get("start"), unit.get("end")
    if share == 0:
        t = start
    elif share == 1:
        t = end
    else:
        t = None if start is None or end is None else start + share * (end - start)
    return None if t is None else map_time(t, segments)


def _row(sid: str, line: Line, units: list[dict], segments: list[Segment], hold: float, total: float) -> _Row | None:
    """The row of one line: each piece starts at its part[0] of its unit's time, and the line's last
    spoken moment is the last piece's part[1] of its unit's time."""
    times = [None if p.unit is None else _at(units[p.unit], p.part[0], segments) for p in line.pieces]
    shown = [p for p in line.pieces if p.unit is not None]
    if not shown:
        return None
    timed = [t for t in times if t is not None]
    if not timed:
        raise PipelineError(f"sentence {sid} has no unit with a time (run transcribe again)")
    ends = [e for e in (_at(units[p.unit], p.part[1], segments) for p in shown) if e is not None]
    return _Row(line, times, timed[0], min(max(ends[-1] if ends else timed[-1], timed[-1]) + hold, total))


def _schedule(rows: list[_Row], total: float, frame: float) -> tuple[list[float], list[float]]:
    """(starts, ends) of the events. Front to back an event ends at the earlier of its wish and the
    next start, is at least a frame long, and pushes the next start later when it needs the room. If
    that runs past the reel, the events are moved back from the end instead."""
    starts = [row.start for row in rows]
    ends: list[float] = []
    for i, row in enumerate(rows):
        end = min(row.wish, starts[i + 1]) if i + 1 < len(rows) else row.wish
        end = max(end, starts[i] + frame)
        ends.append(end)
        if i + 1 < len(rows):
            starts[i + 1] = max(starts[i + 1], end)
    if ends and ends[-1] > total + EPS:
        back_starts, back_ends = list(starts), list(ends)
        back_ends[-1] = total
        for i in range(len(rows) - 1, -1, -1):
            back_starts[i] = min(back_starts[i], back_ends[i] - frame)
            if i:
                back_ends[i - 1] = min(back_ends[i - 1], back_starts[i])
        if back_starts[0] >= -EPS:  # else the reel is shorter than the captions need: keep them whole
            starts, ends = back_starts, back_ends
    return starts, ends


def build_events(transcript: dict, edit: dict, segments: list[Segment], font: Path, style: dict) -> list[Event]:
    """The caption events of the kept sentences, in script order, one per line, on the cut reel (see the
    module text), of the text caption_source gives for each. A sentence that was not spoken or that
    cuts.extra removes has none."""
    hold, frame = style["captions"]["hold_s"], 1 / style["canvas"]["fps"]
    total = total_duration(segments)
    kept = set(kept_sentences(edit, transcript))
    rows: list[_Row] = []
    for sentence in transcript["sentences"]:
        if sentence["id"] not in kept:
            continue
        text, units = caption_source(sentence, edit)
        for line in break_lines(text, units, font, style):
            row = _row(sentence["id"], line, units, segments, hold, total)
            if row is not None:
                rows.append(row)
    starts, ends = _schedule(rows, total, frame)
    events = []
    for row, start, end in zip(rows, starts, ends):
        floor, times = start, []
        for t in row.times:  # in order, and inside the event
            if t is not None:
                floor = min(max(t, floor), end)
            times.append(None if t is None else floor)
        events.append(Event(start, end, row.line, times))
    return events


# ---- placement --------------------------------------------------------------------------------


def caption_top_px(style: dict) -> int:
    """The top of the caption line on the canvas in pixels: the line box (line_height_px: the font cell and
    both outlines) ends at captions.bottom_px, the same place for every reel. load_style has checked that
    the box lies inside the safe area."""
    caps = style["captions"]
    return caps["bottom_px"] - caps["line_height_px"]


# ---- ASS --------------------------------------------------------------------------------------


def ass_color(hex_rgb: str) -> str:
    """The ASS colour (&HAABBGGRR, opaque) of "#RRGGBB"."""
    match = None
    if isinstance(hex_rgb, str):
        match = re.fullmatch(r"#([0-9A-Fa-f]{2})([0-9A-Fa-f]{2})([0-9A-Fa-f]{2})", hex_rgb)
    if match is None:
        raise PipelineError(f"not a #RRGGBB colour: {hex_rgb!r}")
    red, green, blue = match.groups()
    return f"&H00{blue}{green}{red}".upper()


def ass_time(t: float) -> str:
    """h:mm:ss.cc of `t` seconds, rounded to the centisecond (0 for a negative time)."""
    cs = max(0, int(t * 100 + 0.5))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def _escape(text: str) -> str:
    """`text` with the characters that ASS reads as override tags or line breaks made harmless: { } \\
    become their full-width forms and a line break becomes a space."""
    return text.translate(_OVERRIDE_CHARS)


def _karaoke(event: Event) -> list[int | None]:
    """The \\k value, in centiseconds, in front of each piece (None: no tag). A tag goes in front of
    every piece that shows a unit and has a time; it runs to the next such piece, the last one to the
    end of the event, and the first one starts at the event's start, so the values add up to the
    event's length."""
    length = max(0, round((event.end - event.start) * 100))
    at, floor = [], 0
    for piece, t in zip(event.line.pieces, event.times):
        if piece.unit is None or t is None:
            at.append(None)
            continue
        floor = min(max(round((t - event.start) * 100), floor), length)
        at.append(floor)
    first = next((i for i, v in enumerate(at) if v is not None), None)
    if first is not None:
        at[first] = 0
    marks = [(i, v) for i, v in enumerate(at) if v is not None]
    values: list[int | None] = [None] * len(at)
    for (i, v), following in zip(marks, [v for _, v in marks[1:]] + [length]):
        values[i] = following - v
    return values


def _dialogue(event: Event, top_px: int, mode: str, x: int) -> str:
    text = ""
    values = _karaoke(event) if mode == "word" else [None] * len(event.line.pieces)
    for piece, value in zip(event.line.pieces, values):
        text += (f"{{\\k{value}}}" if value is not None else "") + _escape(piece.text)
    return (f"Dialogue: 0,{ass_time(event.start)},{ass_time(event.end)},{STYLE_NAME},,0,0,0,,"
            f"{{\\an8\\pos({x},{top_px})}}{text}")


def write_ass(
    events: list[Event], style: dict, top_px: int, mode: str, path: Path, *, font: Path | None = None
) -> None:
    """Write the captions as an ASS file at `path` (UTF-8). `mode` "line" shows each line in the colour
    `color`; "word" shows it in `color` and turns each unit to `highlight` as it is spoken (\\k tags).
    Every line sits with its top centre at (canvas width / 2, top_px) and is never wrapped.

    `size_px` is the em of the text. libass takes the ASS Fontsize as the height of the font's cell (hhea
    ascent less descent, 1.448 em for Noto Sans TC), so the file gets size_px times that, read from
    `font` (default: the bold static font of the style, the one the render uses)."""
    if mode not in ("line", "word"):
        raise PipelineError(f"unknown caption mode {mode!r}; use 'line' or 'word'")
    for event in events:
        if len(event.times) != len(event.line.pieces):
            raise PipelineError(f"caption {event.line.text!r} has {len(event.times)} times for "
                                f"{len(event.line.pieces)} pieces")
    caps, canvas, safe = style["captions"], style["canvas"], style["safe_area"]
    color = ass_color(caps["color"])
    primary, secondary = (color, color) if mode == "line" else (ass_color(caps["highlight"]), color)
    top = round(top_px)
    fontsize = round(caps["size_px"] * _cell(*_font_key(font if font is not None else font_path(style, "bold"))))
    row = [STYLE_NAME, style["fonts"]["family"], fontsize, primary, secondary, ass_color(caps["outline_color"]),
           "&H00000000", -1 if caps["bold"] else 0, 0, 0, 0, 100, 100, 0, 0, 1, caps["outline_px"], 0, 8,
           safe["x0"], canvas["w"] - safe["x1"], 0, 1]
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {canvas['w']}",
        f"PlayResY: {canvas['h']}",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding",
        "Style: " + ",".join(str(v) for v in row),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        *(_dialogue(event, top, mode, canvas["w"] // 2) for event in events),
    ]
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
