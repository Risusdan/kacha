"""edit.json scenes: the animation cards, the checks validate makes of them, and where each scene lands in the reel
(docs/superpowers/specs/2026-11-15-remotion-foundation-design.md, sections 5, 6 and 9).

A scene shows one card over the talking head: {"id", "card", "at", "until", "params"} and an optional "layout",
which can only be "overlay". `at` is {"s": sentence id, "char": n} (char 0 when left out); `until` is
{"s": sentence id, "end": true} or {"s": sentence id, "char": n}. A char counts the characters of the sentence's text
in transcript.json from 0. The scene starts when the unit (a character, or a run of letters and digits) holding
the `at` character starts to be spoken, and ends when the unit holding the `until` character, or the sentence's last
unit for "end", is done. A character no unit holds (a punctuation mark) goes with the next unit when a scene starts
at it and with the unit before it when a scene ends at it, or with the nearest unit when there is none that way.

Every check that needs only edit.json and the order of the sentences is an error of validate (check_scenes). What
the cuts do to a scene is a warning (scene_warnings): the review page changes the cuts and removes sentences
without touching scenes, and such a change must never be refused. Both times go through edit.map_time into the
reel, so a scene may run across a cut point, and a time that falls inside a cut is the start of the next segment.
"""

import json
import re
from pathlib import Path
from typing import NamedTuple

from pipeline.captions import text_px, width_units
from pipeline.edit import (
    FPS,
    LINE_BREAK_CHARS,
    _check_choice,
    _check_keys,
    _describe,
    _extra_ranges,
    _is_array,
    _is_object,
    _join,
    _removed,
    _show,
    _table,
    build_segments,
    map_time,
    to_frame,
)
from pipeline.errors import PipelineError
from pipeline.paths import repo_root
from pipeline.style import font_path


class Text(NamedTuple):
    """A text param: one line, from `least` to `most` characters wide (captions.width_units, spaces at its ends
    not counted)."""

    name: str
    least: int
    most: int
    optional: bool = False


class Items(NamedTuple):
    """A list param: from `fewest` to `most_items` texts, each from `least` to `most` characters wide."""

    name: str
    fewest: int
    most_items: int
    least: int
    most: int
    optional: bool = False


class Card(NamedTuple):
    label: str  # the card's name as the storyboard shows it
    impact: bool  # a big-impact effect, of which a reel may have MAX_IMPACT
    params: tuple[Text | Items, ...]


CARDS = {
    "keyword-pop": Card("重點字", False, (Text("text", 1, 10),)),
    "number-slab": Card("數字卡", False, (Text("value", 1, 6), Text("label", 0, 12, optional=True))),
    "bullet-list": Card("清單", False, (Items("items", 2, 4, 1, 12),)),
}
LAYOUTS = ("overlay",)
MAX_IMPACT = 3
MAX_RUN = 2  # the same card may follow itself once, not twice
MIN_SCENE_FRAMES = FPS  # a scene shorter than this after the cuts is warned of

Position = tuple[int, int]  # (place of the sentence in the script, index of the unit in it)


class Placed(NamedTuple):
    """A scene that shows in the reel: its index in edit.json scenes, the scene, the reel frame it starts at and its
    length in frames."""

    index: int
    scene: dict
    start: int
    frames: int


class Layout(NamedTuple):
    """Where a scene of a valid edit lands: its index in edit.json scenes, the scene, the sentence of it that cuts.extra
    removes (None when there is none), the source times it starts and ends at, and the reel frame it starts at and its
    length in frames (0 and 0 for a scene of a removed sentence)."""

    index: int
    scene: dict
    gone: str | None
    source_start: float
    source_end: float
    start: int
    frames: int


# ---- validation -----------------------------------------------------------------------------------------------


def check_scenes(items: object, transcript: dict, errors: list[str], style: dict | None = None) -> None:
    """Every problem of edit.json scenes that does not depend on the cuts, as "<JSON path>: <problem>". With the
    edit's `style` (None when it cannot be loaded), scenes also need the style's animation zone, and a text of the
    params may not make its card wider than the zone (_check_widths)."""
    if not _is_array(items, "scenes", "scenes", errors):
        return
    if items and style is not None and "scenes" not in style:
        errors.append(f"scenes: style {style['dir'].name} has no animation zone (scenes in style.json)")
        style = None
    table = _table(transcript)
    sentences = {sentence["id"]: sentence for sentence in transcript["sentences"]}
    ids: dict[str, int] = {}
    placed: list[tuple[Position, Position, int, str]] = []
    cards: list[str] = []
    for index, item in enumerate(items):
        path = f"scenes[{index}]"
        if not _is_object(item, path, errors):
            continue
        _check_keys(item, path, ("id", "card", "at", "until", "params"), errors, optional=("layout",))
        if "id" in item:
            _check_id(item["id"], f"{path}.id", index, ids, errors)
        if "layout" in item:
            _check_choice(item["layout"], f"{path}.layout", LAYOUTS, errors)
        card = item.get("card")
        if "card" in item and not (isinstance(card, str) and card in CARDS):
            _check_choice(card, f"{path}.card", tuple(CARDS), errors)
            card = None
        if card is not None:
            cards.append(card)
            if "params" in item:
                found = len(errors)
                _check_params(item["params"], CARDS[card], f"{path}.params", errors)
                if style is not None and len(errors) == found:
                    _check_widths(card, item["params"], f"{path}.params", style, errors)
        start = _anchor(item["at"], f"{path}.at", table, sentences, False, errors) if "at" in item else None
        end = _anchor(item["until"], f"{path}.until", table, sentences, True, errors) if "until" in item else None
        if start is None or end is None:
            continue
        if end < start:
            errors.append(f"{path}.until: comes before at")
        elif card is not None:
            placed.append((start, end, index, card))
    _check_order(placed, errors)
    impact = sum(CARDS[card].impact for card in cards)
    if impact > MAX_IMPACT:
        errors.append(f"scenes: {impact} impact cards, at most {MAX_IMPACT}")


def _check_id(value: object, path: str, index: int, ids: dict[str, int], errors: list[str]) -> None:
    if not isinstance(value, str) or not value:
        shown = _show(value) if isinstance(value, str) else _describe(value)
        errors.append(f"{path}: must be a non-empty string, got {shown}")
    elif value in ids:
        name = value if re.fullmatch(r"\w{1,60}", value) else _show(value)
        errors.append(f"{path}: {name} is listed twice (also scenes[{ids[value]}])")
    else:
        ids[value] = index


def _check_params(params: object, card: Card, path: str, errors: list[str]) -> None:
    if not _is_object(params, path, errors):
        return
    required = tuple(param.name for param in card.params if not param.optional)
    optional = tuple(param.name for param in card.params if param.optional)
    _check_keys(params, path, required, errors, optional=optional)
    for param in card.params:
        if param.name not in params:
            continue
        where, value = _join(path, param.name), params[param.name]
        if isinstance(param, Text):
            _check_text(value, where, param.least, param.most, errors)
        elif not isinstance(value, list):
            errors.append(f"{where}: must be an array of {param.fewest} to {param.most_items} items, "
                          f"got {_describe(value)}")
        elif not param.fewest <= len(value) <= param.most_items:
            errors.append(f"{where}: must have {param.fewest} to {param.most_items} items, got {len(value)}")
        else:
            for k, entry in enumerate(value):
                _check_text(entry, f"{where}[{k}]", param.least, param.most, errors)


def _check_text(value: object, path: str, least: int, most: int, errors: list[str]) -> None:
    if not isinstance(value, str):
        errors.append(f"{path}: must be a string, got {_describe(value)}")
    elif any(char in LINE_BREAK_CHARS for char in value):
        errors.append(f"{path}: must be one line")
    else:
        width = width_units(value.strip())
        if not least <= width <= most:
            errors.append(f"{path}: must be {least} to {most} characters wide, got {width}")


def card_sizes() -> dict:
    """The sizes the cards are drawn at, from scenes/src/sizes.json, which the cards read too: for each card its
    padding_x and the size and weight ("regular" or "bold") of each param's text; keyword-pop's grow, the largest
    scale it reaches as it pops; bullet-list's tick and tick_gap, which come before each item."""
    return json.loads((repo_root() / "scenes" / "src" / "sizes.json").read_text(encoding="utf-8"))


def card_texts_px(card: str, params: dict, fonts: dict[str, Path]) -> list[tuple[str, float]]:
    """Each text of a card's valid params, in the card's param order (the items one by one, as items[k]), with the
    width in pixels of the card around that text alone, at rest: the text without the spaces at its ends at its size
    in `fonts` (weight -> font file), plus the card's padding on both sides and, before an item, the tick and its gap.
    A character a font has no glyph for counts as one em."""
    sizes = card_sizes()[card]
    found = []
    for param in CARDS[card].params:
        if param.name not in params:
            continue
        size, value = sizes[param.name], params[param.name]
        if isinstance(param, Text):
            texts, before = [(param.name, value)], 0
        else:
            texts = [(f"{param.name}[{k}]", text) for k, text in enumerate(value)]
            before = sizes["tick"] + sizes["tick_gap"]
        for key, text in texts:
            px = text_px(text.strip(), fonts[size["weight"]], size["size"])
            found.append((key, px + before + 2 * sizes["padding_x"]))
    return found


def card_width_px(card: str, params: dict, fonts: dict[str, Path]) -> float:
    """The width in pixels of a card at rest: that of its widest text (card_texts_px)."""
    return max(px for _, px in card_texts_px(card, params, fonts))


def _check_widths(card: str, params: dict, path: str, style: dict, errors: list[str]) -> None:
    """Each text of valid params whose card, at the largest scale it reaches (grow), is wider than the style's
    animation zone. Skipped when a font of the style cannot be read: render refuses that before it starts."""
    zone = style["scenes"]["zone"]
    room = zone["x1"] - zone["x0"]
    grow = card_sizes()[card].get("grow", 1)
    try:
        widths = card_texts_px(card, params, {weight: font_path(style, weight) for weight in ("regular", "bold")})
    except PipelineError:
        return
    for key, px in widths:
        if px * grow > room:
            errors.append(f"{path}.{key}: {px * grow:.0f} px wide on its card, wider than the {room} px animation zone")


def _anchor(value: object, path: str, table: dict, sentences: dict, ending: bool,
            errors: list[str]) -> Position | None:
    """The position an `at` (ending False) or `until` (ending True) names, or None after reporting what is wrong."""
    if not _is_object(value, path, errors):
        return None
    _check_keys(value, path, ("s",), errors, optional=("char", "end") if ending else ("char",))
    if "s" not in value:
        return None
    sid = value["s"]
    if not isinstance(sid, str):
        errors.append(f"{path}.s: must be a sentence id (a string), got {_describe(sid)}")
        return None
    if sid not in table:
        errors.append(f"{path}.s: unknown sentence {_show(sid)}")
        return None
    if not table[sid][1]:
        errors.append(f"{path}.s: {sid} was not spoken")
        return None
    sentence = sentences[sid]
    units = sentence["units"]
    if not units:
        errors.append(f"{path}.s: {sid} has no timed characters")
        return None
    if ending:
        if "end" in value and "char" in value:
            errors.append(f"{path}: must have end or char, not both")
            return None
        if "end" not in value and "char" not in value:
            errors.append(f"{path}: must have end or char")
            return None
        if "end" in value:
            if value["end"] is not True:
                errors.append(f"{path}.end: must be true, got {_show(value['end'])}")
                return None
            return table[sid][0], len(units) - 1
    char = value.get("char", 0)
    last = len(sentence["text"]) - 1
    if isinstance(char, bool) or not isinstance(char, int) or not 0 <= char <= last:
        shown = _show(char) if isinstance(char, (int, float)) and not isinstance(char, bool) else _describe(char)
        errors.append(f"{path}.char: must be a whole number from 0 to {last}, got {shown}")
        return None
    return table[sid][0], _unit_of(units, char, ending)


def _unit_of(units: list[dict], char: int, ending: bool) -> int:
    """The index of the unit a scene starts at (ending False) or ends at (ending True) for character `char`."""
    if ending:
        before = [k for k, unit in enumerate(units) if unit["span"][0] <= char]
        return before[-1] if before else 0
    return next((k for k, unit in enumerate(units) if unit["span"][1] > char), len(units) - 1)


def _check_order(placed: list[tuple[Position, Position, int, str]], errors: list[str]) -> None:
    """Scenes in the order they start: none may overlap one that starts before it (or with it), and no card may
    come more than MAX_RUN times in a row."""
    ordered = sorted(placed)
    for n, (start, _, index, card) in enumerate(ordered):
        earlier = next((index0 for _, end0, index0, _ in ordered[:n] if start <= end0), None)
        if earlier is not None:
            errors.append(f"scenes[{index}]: overlaps scenes[{earlier}]")
        if n >= MAX_RUN and all(other[3] == card for other in ordered[n - MAX_RUN:n]):
            errors.append(f"scenes[{index}]: the third {card} in a row")


# ---- where the scenes land ------------------------------------------------------------------------------------


def _times(scene: dict, sentences: dict) -> tuple[float, float]:
    """The source times a valid scene starts and ends at."""
    at, until = scene["at"], scene["until"]
    units = sentences[at["s"]]["units"]
    start = units[_unit_of(units, at.get("char", 0), False)]["start"]
    units = sentences[until["s"]]["units"]
    last = len(units) - 1 if until.get("end") is True else _unit_of(units, until["char"], True)
    return start, units[last]["end"]


def scene_layout(edit: dict, transcript: dict, trims: dict) -> list[Layout]:
    """Every scene of a valid edit, in edit.json order, and where it lands (Layout)."""
    items = edit.get("scenes") or []
    if not items:
        return []
    table = _table(transcript)
    removed = _removed(table, _extra_ranges(edit, table))
    segments = build_segments(edit, transcript, trims)
    sentences = {sentence["id"]: sentence for sentence in transcript["sentences"]}
    found = []
    for index, item in enumerate(items):
        start, end = _times(item, sentences)
        gone = next((sid for sid in (item["at"]["s"], item["until"]["s"]) if sid in removed), None)
        if gone is not None:
            found.append(Layout(index, item, gone, start, end, 0, 0))
            continue
        first, last = to_frame(map_time(start, segments)), to_frame(map_time(end, segments))
        found.append(Layout(index, item, None, start, end, first, max(0, last - first)))
    return found


def scene_spans(edit: dict, transcript: dict, trims: dict) -> list[Placed]:
    """The scenes of a valid edit that show in the reel, in edit.json order: not in a sentence cuts.extra removes,
    and at least one frame long after the cuts."""
    return [Placed(item.index, item.scene, item.start, item.frames)
            for item in scene_layout(edit, transcript, trims) if item.gone is None and item.frames > 0]


def scene_warning(item: Layout) -> str | None:
    """What the cuts do to one scene, or None when it shows at MIN_SCENE_FRAMES or more: a scene of a removed sentence
    and one the cuts leave no frame of are not shown; one shorter than MIN_SCENE_FRAMES is."""
    path = f"scenes[{item.index}]"
    if item.gone is not None:
        return f"{path}: {item.gone} is removed by cuts.extra, so it is not shown"
    if item.frames == 0:
        return f"{path}: nothing of it is left after the cuts, so it is not shown"
    if item.frames < MIN_SCENE_FRAMES:
        return f"{path}: {item.frames / FPS:.1f} s long after the cuts, under {MIN_SCENE_FRAMES / FPS:g} s"
    return None


def scene_warnings(edit: dict, transcript: dict, trims: dict) -> list[str]:
    """For a valid edit, the scene_warning of each of its scenes that has one, in edit.json order."""
    return [warning for item in scene_layout(edit, transcript, trims) if (warning := scene_warning(item))]


def scene_texts(scene: dict) -> list[str]:
    """The texts of a valid scene's params, in the order of its card's params (the items of a list one by one); an
    optional param that is left out has none."""
    params = scene["params"]
    found = []
    for param in CARDS[scene["card"]].params:
        value = params.get(param.name)
        if value is not None:
            found.extend(value if isinstance(param, Items) else [value])
    return found


def scene_label(scene: dict) -> tuple[str, str]:
    """(the card's name, its texts joined by ／) for a valid scene, as the storyboard and the review page show it:
    ("數字卡", "兩小時／一天多撐"). A text is shown without the spaces around it; an empty one is left out."""
    texts = [text.strip() for text in scene_texts(scene)]
    return CARDS[scene["card"]].label, "／".join(text for text in texts if text)
