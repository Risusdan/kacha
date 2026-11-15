"""edit.json scenes: the animation cards, their checks in validate, the warnings that depend on the cuts, and where
each one lands in the reel (spec 2026-11-15-remotion-foundation-design, sections 5, 6 and 9). The sentences are
made up."""

import copy
import json
import re

import pytest
from helpers import default_edit, timed_sentence

from pipeline import scenes
from pipeline.captions import text_px
from pipeline.edit import OPTIONAL_KEYS, validate_edit
from pipeline.review_actions import remove_sentence, set_cut
from pipeline.scenes import CARDS, Placed, scene_spans, scene_warnings
from pipeline.style import font_path, load_style, styles_root


def times(start: float, step: float, count: int) -> list[tuple[float, float]]:
    return [(round(start + step * k, 3), round(start + step * (k + 1), 3)) for k in range(count)]


# s01 0.0-1.5 (5 characters of 0.3 s), s02 2.0-5.0 (6 of 0.5 s), s03 6.0-8.8 (7 of 0.4 s); s04 was not spoken.
TRANSCRIPT = {"sentences": [
    timed_sentence("s01", "今天聊省電。", times(0.0, 0.3, 5)),
    timed_sentence("s02", "亮度調低一點。", times(2.0, 0.5, 6)),
    timed_sentence("s03", "一天多撐兩小時。", times(6.0, 0.4, 7)),
    {"id": "s04", "text": "腳本有但沒講。", "display_source": "script", "spoken": False, "units": []},
]}
# t01 is a cut inside s02, from its third character (調, 3.0) to the start of its fifth (一, 4.0).
TRIMS = {
    "trims": [{"id": "t01", "kind": "filler", "sentence": "s02", "start": 3.0, "end": 4.0, "suggest": "list",
               "text": "調低", "length": 2, "reason": "made up"}],
    "bounds": {"s01": {"cut_in": 0.0, "cut_out": 1.6}, "s02": {"cut_in": 1.9, "cut_out": 5.1},
               "s03": {"cut_in": 5.9, "cut_out": 8.9}},
}


def scene(sid: str = "sc1", card: str = "number-slab", at: dict | None = None, until: dict | None = None,
          params: dict | None = None, **more) -> dict:
    return {"id": sid, "card": card, "at": at or {"s": "s03", "char": 4}, "until": until or {"s": "s03", "end": True},
            "params": params if params is not None else {"value": "兩小時", "label": "一天多撐"}, **more}


def edit_with(*items: dict, accept: tuple[str, ...] = (), extra: tuple[str, ...] = ()) -> dict:
    edit = default_edit(TRANSCRIPT, TRIMS)
    edit["cuts"]["accept"] = list(accept)
    edit["cuts"]["reject"] = [trim["id"] for trim in TRIMS["trims"] if trim["id"] not in accept]
    edit["cuts"]["extra"] = [{"from": sid, "to": sid, "reason": "離題"} for sid in extra]
    edit["sentences"] = [entry for entry in edit["sentences"] if entry["id"] not in extra]
    edit["scenes"] = list(items)
    return edit


def errors_of(*items: dict, **cuts) -> list[str]:
    return validate_edit(edit_with(*items, **cuts), TRANSCRIPT, TRIMS)


# ---- the cards ------------------------------------------------------------------------------------------------


def test_the_three_cards_of_the_design():
    assert list(CARDS) == ["keyword-pop", "number-slab", "bullet-list"]
    assert [card.label for card in CARDS.values()] == ["重點字", "數字卡", "清單"]
    assert not any(card.impact for card in CARDS.values())


def test_scenes_is_an_optional_key_now():
    assert "scenes" in OPTIONAL_KEYS
    assert validate_edit(default_edit(TRANSCRIPT, TRIMS), TRANSCRIPT, TRIMS) == []


def test_an_edit_with_one_scene_of_each_card_is_valid():
    assert errors_of(
        scene("sc1", "keyword-pop", {"s": "s01"}, {"s": "s01", "end": True}, {"text": "省電"}),
        scene("sc2", "bullet-list", {"s": "s02", "char": 0}, {"s": "s02", "char": 5}, {"items": ["亮度", "背景更新"]}),
        scene("sc3", layout="overlay"),
    ) == []


def test_a_number_slab_may_leave_out_its_label_or_leave_it_empty():
    assert errors_of(scene(params={"value": "2"})) == []
    assert errors_of(scene(params={"value": "2", "label": ""})) == []


# ---- format errors --------------------------------------------------------------------------------------------


def test_scenes_must_be_an_array_of_objects():
    edit = edit_with()
    edit["scenes"] = {"sc1": {}}
    assert validate_edit(edit, TRANSCRIPT, TRIMS) == ["scenes: must be an array of scenes, got an object"]
    assert errors_of("sc1") == ["scenes[0]: must be an object, got a string"]


def test_a_scene_needs_its_keys_and_no_others():
    item = scene(foo=1)
    del item["params"]
    assert errors_of(item) == ["scenes[0].params: missing", "scenes[0].foo: unknown key"]


def test_the_only_layout_is_overlay():
    assert errors_of(scene(layout="full")) == ["scenes[0].layout: 'full' is not one of overlay"]


def test_an_unknown_card_is_refused():
    assert errors_of(scene(card="bar-chart")) == [
        "scenes[0].card: 'bar-chart' is not one of keyword-pop, number-slab, bullet-list"
    ]


@pytest.mark.parametrize("bad, error", [
    ("sc1", "scenes[0].params: must be an object, got a string"),
    ({"label": "x"}, "scenes[0].params.value: missing"),
    ({"value": "2", "unit": "x"}, "scenes[0].params.unit: unknown key"),
    ({"value": 2}, "scenes[0].params.value: must be a string, got a number"),
    ({"value": "兩小\n時"}, "scenes[0].params.value: must be one line"),
    ({"value": ""}, "scenes[0].params.value: must be 1 to 6 characters wide, got 0"),
    ({"value": "  "}, "scenes[0].params.value: must be 1 to 6 characters wide, got 0"),
    ({"value": "一二三四五六七"}, "scenes[0].params.value: must be 1 to 6 characters wide, got 7"),
    ({"value": "2", "label": "一二三四五六七八九十一二三"},
     "scenes[0].params.label: must be 0 to 12 characters wide, got 13"),
])
def test_the_params_of_a_number_slab(bad, error):
    assert errors_of(scene(params=bad)) == [error]


def test_widths_count_a_half_width_character_as_half():
    assert errors_of(scene(params={"value": "120000%"})) == []  # 7 half-width characters: 3.5, rounded up to 4
    # narrow letters, so that the pixel width of the card (test_a_card_text_wider_than_...) is not what refuses them
    assert errors_of(scene("sc1", "keyword-pop", params={"text": "i" * 20})) == []  # 10 wide
    assert errors_of(scene("sc1", "keyword-pop", params={"text": "i" * 21})) == [
        "scenes[0].params.text: must be 1 to 10 characters wide, got 11"
    ]


@pytest.mark.parametrize("bad, error", [
    ({"items": "亮度"}, "scenes[0].params.items: must be an array of 2 to 4 items, got a string"),
    ({"items": ["亮度"]}, "scenes[0].params.items: must have 2 to 4 items, got 1"),
    ({"items": ["一", "二", "三", "四", "五"]}, "scenes[0].params.items: must have 2 to 4 items, got 5"),
    ({"items": ["亮度", 3]}, "scenes[0].params.items[1]: must be a string, got a number"),
    ({"items": ["亮度", ""]}, "scenes[0].params.items[1]: must be 1 to 12 characters wide, got 0"),
    ({"items": ["亮度", "一二三四五六七八九十一二三"]},
     "scenes[0].params.items[1]: must be 1 to 12 characters wide, got 13"),
])
def test_the_params_of_a_bullet_list(bad, error):
    assert errors_of(scene("sc1", "bullet-list", params=bad)) == [error]


# ---- the width of a card in pixels ----------------------------------------------------------------------------


def default_fonts() -> dict:
    style = load_style("default")
    return {weight: font_path(style, weight) for weight in ("regular", "bold")}


WIDE = re.compile(r"(?P<path>scenes\[0\]\.params\.\S+): (?P<px>\d+) px wide on its card, "
                  r"wider than the 950 px animation zone")


@pytest.mark.parametrize("card, params, path", [
    ("keyword-pop", {"text": "BATTERY HEALTH CHECK"}, "scenes[0].params.text"),
    ("number-slab", {"value": "100000000000"}, "scenes[0].params.value"),
    ("bullet-list", {"items": ["亮度", "SCREEN BRIGHTNESS ON MAX"]}, "scenes[0].params.items[1]"),
])
def test_a_card_text_wider_than_the_animation_zone_is_refused(card, params, path):
    errors = errors_of(scene(card=card, params=params))
    assert len(errors) == 1, errors
    match = WIDE.fullmatch(errors[0])
    assert match and match["path"] == path and int(match["px"]) > 950, errors


def test_chinese_texts_at_the_width_limits_fit_the_zone():
    assert errors_of(scene(card="keyword-pop", params={"text": "省電省電省電省電省電"})) == []
    assert errors_of(scene(params={"value": "兩小時兩小時", "label": "一天多撐一天多撐一天多撐"})) == []
    assert errors_of(scene(card="bullet-list", params={"items": ["亮度亮度亮度亮度亮度亮度", "背景更新背景更新背景更新"]})) == []


def test_a_card_is_as_wide_as_each_text_at_its_size_with_the_card_padding():
    fonts = default_fonts()
    regular, bold = fonts["regular"], fonts["bold"]
    assert scenes.card_texts_px("keyword-pop", {"text": " 省電 "}, fonts) == [
        ("text", pytest.approx(text_px("省電", bold, 80) + 2 * 48))]
    assert scenes.card_texts_px("number-slab", {"value": "2", "label": "一天"}, fonts) == [
        ("value", pytest.approx(text_px("2", bold, 128) + 2 * 64)),
        ("label", pytest.approx(text_px("一天", regular, 56) + 2 * 64))]
    assert scenes.card_texts_px("bullet-list", {"items": ["亮度", "背景更新"]}, fonts) == [
        ("items[0]", pytest.approx(text_px("亮度", regular, 56) + 56 + 24 + 2 * 56)),
        ("items[1]", pytest.approx(text_px("背景更新", regular, 56) + 56 + 24 + 2 * 56))]
    assert scenes.card_width_px("number-slab", {"value": "2", "label": "一天"}, fonts) == pytest.approx(
        text_px("一天", regular, 56) + 2 * 64)


def test_a_keyword_pop_is_measured_at_its_largest_while_it_pops(monkeypatch):
    sizes = copy.deepcopy(scenes.card_sizes())
    assert sizes["keyword-pop"]["grow"] >= 1.038  # the largest scale of its spring, measured
    sizes["keyword-pop"]["grow"] = 2.0
    monkeypatch.setattr(scenes, "card_sizes", lambda: sizes)
    errors = errors_of(scene(card="keyword-pop", params={"text": "省電省電省電"}))  # 576 px at rest, 1152 popping
    assert [WIDE.fullmatch(error)["px"] for error in errors] == ["1152"], errors


def test_scenes_need_a_style_with_an_animation_zone(tmp_path, monkeypatch):
    document = json.loads((styles_root() / "default" / "style.json").read_text(encoding="utf-8"))
    del document["scenes"]
    folder = tmp_path / "styles" / "plain"
    folder.mkdir(parents=True)
    (folder / "style.json").write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setattr("pipeline.style.styles_root", lambda: tmp_path / "styles")
    edit = edit_with(scene())
    edit["style"] = "plain"
    assert validate_edit(edit, TRANSCRIPT, TRIMS) == ["scenes: style plain has no animation zone (scenes in style.json)"]
    edit["scenes"] = []
    assert validate_edit(edit, TRANSCRIPT, TRIMS) == []


def test_an_id_is_a_non_empty_string_used_once():
    assert errors_of(scene(""), ) == ["scenes[0].id: must be a non-empty string, got ''"]
    assert errors_of(scene(7)) == ["scenes[0].id: must be a non-empty string, got a number"]
    assert errors_of(scene("sc1"), scene("sc1", at={"s": "s01"}, until={"s": "s01", "end": True})) == [
        "scenes[1].id: sc1 is listed twice (also scenes[0])"
    ]


# ---- anchors --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("at, error", [
    ({"s": "s09"}, "scenes[0].at.s: unknown sentence 's09'"),
    ({"s": "s04"}, "scenes[0].at.s: s04 was not spoken"),
    ({"s": 3}, "scenes[0].at.s: must be a sentence id (a string), got a number"),
    ({"s": "s03", "char": 8}, "scenes[0].at.char: must be a whole number from 0 to 7, got 8"),
    ({"s": "s03", "char": -1}, "scenes[0].at.char: must be a whole number from 0 to 7, got -1"),
    ({"s": "s03", "char": 1.5}, "scenes[0].at.char: must be a whole number from 0 to 7, got 1.5"),
    ({"s": "s03", "char": True}, "scenes[0].at.char: must be a whole number from 0 to 7, got a boolean"),
    ({"s": "s03", "end": True}, "scenes[0].at.end: unknown key"),
    ({"char": 1}, "scenes[0].at.s: missing"),
])
def test_where_a_scene_starts(at, error):
    assert errors_of(scene(at=at)) == [error]


@pytest.mark.parametrize("until, error", [
    ({"s": "s03"}, "scenes[0].until: must have end or char"),
    ({"s": "s03", "end": True, "char": 6}, "scenes[0].until: must have end or char, not both"),
    ({"s": "s03", "end": False}, "scenes[0].until.end: must be true, got False"),
    ({"s": "s03", "char": 9}, "scenes[0].until.char: must be a whole number from 0 to 7, got 9"),
    ({"s": "s04", "end": True}, "scenes[0].until.s: s04 was not spoken"),
])
def test_where_a_scene_ends(until, error):
    assert errors_of(scene(until=until)) == [error]


def test_a_scene_may_not_end_before_it_starts():
    assert errors_of(scene(at={"s": "s03", "char": 4}, until={"s": "s03", "char": 3})) == [
        "scenes[0].until: comes before at"
    ]
    assert errors_of(scene(at={"s": "s03"}, until={"s": "s02", "end": True})) == ["scenes[0].until: comes before at"]
    assert errors_of(scene(at={"s": "s03", "char": 4}, until={"s": "s03", "char": 4})) == []  # one character


def test_a_punctuation_mark_belongs_to_the_character_after_it_at_the_start_and_before_it_at_the_end():
    # s03's mark 。 (char 7) has no unit: at it, the scene would start after the sentence's last unit, so it is the
    # last unit; until it, the scene ends with the last unit
    assert errors_of(scene(at={"s": "s03", "char": 6}, until={"s": "s03", "char": 7})) == []


def test_two_scenes_may_not_overlap():
    first = scene("sc1", at={"s": "s02", "char": 0}, until={"s": "s03", "char": 1})
    second = scene("sc2", at={"s": "s03", "char": 1}, until={"s": "s03", "end": True})
    assert errors_of(first, second) == ["scenes[1]: overlaps scenes[0]"]
    after = scene("sc2", at={"s": "s03", "char": 2}, until={"s": "s03", "end": True})
    assert errors_of(first, after) == []


def test_scenes_are_ordered_by_where_they_start_whatever_their_order_in_the_list():
    later = scene("sc2", at={"s": "s03", "char": 2}, until={"s": "s03", "end": True})
    earlier = scene("sc1", at={"s": "s02", "char": 0}, until={"s": "s03", "char": 2})
    assert errors_of(later, earlier) == ["scenes[0]: overlaps scenes[1]"]


def keyword(sid: str, sentence: str, char: int) -> dict:
    return scene(sid, "keyword-pop", {"s": sentence, "char": char}, {"s": sentence, "char": char}, {"text": "省電"})


def test_the_same_card_three_times_in_a_row_is_refused():
    assert errors_of(keyword("sc1", "s01", 0), keyword("sc2", "s02", 0), keyword("sc3", "s03", 0)) == [
        "scenes[2]: the third keyword-pop in a row"
    ]
    between = scene("sc3", at={"s": "s02", "char": 3}, until={"s": "s02", "end": True})
    assert errors_of(keyword("sc1", "s01", 0), keyword("sc2", "s02", 0), between, keyword("sc4", "s03", 0)) == []


def test_more_than_three_impact_cards_are_refused(monkeypatch):
    monkeypatch.setitem(CARDS, "keyword-pop", CARDS["keyword-pop"]._replace(impact=True))
    three = [keyword("sc1", "s01", 0), keyword("sc2", "s01", 2), scene("sc3", at={"s": "s02", "char": 0},
                                                                        until={"s": "s02", "char": 0})]
    assert errors_of(*three, keyword("sc4", "s02", 3)) == []
    assert errors_of(*three, keyword("sc4", "s02", 3), keyword("sc5", "s03", 0)) == [
        "scenes: 4 impact cards, at most 3"
    ]


def test_validate_never_raises_on_junk_in_scenes():
    junk = [None, 1, "x", [], {}, {"id": None, "card": None, "at": None, "until": None, "params": None},
            {"id": "a", "card": "bullet-list", "at": {"s": None, "char": "x"}, "until": {"s": [], "end": {}},
             "params": {"items": [None, {}]}}]
    for item in junk:
        assert validate_edit(edit_with(item), TRANSCRIPT, TRIMS)
    for item in junk:
        edit = edit_with()
        edit["scenes"] = item
        validate_edit(edit, TRANSCRIPT, TRIMS)


# ---- timing ---------------------------------------------------------------------------------------------------


def test_a_scene_inside_a_sentence_starts_when_its_character_is_spoken_and_ends_with_the_sentence():
    # at 兩 (s03 char 4, 7.6 s) until the end of s03 (8.8 s); nothing is cut
    edit = edit_with(scene())
    assert scene_spans(edit, TRANSCRIPT, TRIMS) == [Placed(0, edit["scenes"][0], 228, 36)]


def test_a_scene_across_a_cut_is_one_clip_as_long_as_its_span_in_the_reel():
    # from 亮 (2.0 s) to the end of 天 (s03 char 1, 6.8 s), with 3.0-4.0 cut: 2.0 to 5.8 s in the reel
    edit = edit_with(scene(at={"s": "s02", "char": 0}, until={"s": "s03", "char": 1}), accept=("t01",))
    assert scene_spans(edit, TRANSCRIPT, TRIMS) == [Placed(0, edit["scenes"][0], 60, 114)]


def test_a_scene_whose_character_is_cut_starts_where_the_next_segment_starts():
    # 低 (s02 char 3) is spoken at 3.5 s, inside the cut 3.0-4.0: the scene starts at 3.0 s in the reel; the end of
    # s02 (5.0 s) is 4.0 s in the reel
    edit = edit_with(scene(at={"s": "s02", "char": 3}, until={"s": "s02", "end": True}), accept=("t01",))
    assert scene_spans(edit, TRANSCRIPT, TRIMS) == [Placed(0, edit["scenes"][0], 90, 30)]
    assert scene_warnings(edit, TRANSCRIPT, TRIMS) == []


def test_a_scene_of_a_removed_sentence_is_valid_not_shown_and_warned_of():
    edit = edit_with(scene("sc1", at={"s": "s02"}, until={"s": "s02", "end": True}), scene("sc2"), extra=("s02",))
    assert validate_edit(edit, TRANSCRIPT, TRIMS) == []
    assert [placed.index for placed in scene_spans(edit, TRANSCRIPT, TRIMS)] == [1]
    assert scene_warnings(edit, TRANSCRIPT, TRIMS) == [
        "scenes[0]: s02 is removed by cuts.extra, so it is not shown"
    ]


def test_a_scene_the_cuts_take_away_whole_is_not_shown_and_warned_of():
    edit = edit_with(scene(at={"s": "s02", "char": 2}, until={"s": "s02", "char": 3}), accept=("t01",))
    assert validate_edit(edit, TRANSCRIPT, TRIMS) == []
    assert scene_spans(edit, TRANSCRIPT, TRIMS) == []
    assert scene_warnings(edit, TRANSCRIPT, TRIMS) == ["scenes[0]: nothing of it is left after the cuts, so it is not shown"]


def test_a_scene_shorter_than_a_second_is_shown_and_warned_of():
    edit = edit_with(scene(at={"s": "s01", "char": 0}, until={"s": "s01", "char": 1}))
    assert scene_spans(edit, TRANSCRIPT, TRIMS) == [Placed(0, edit["scenes"][0], 0, 18)]
    assert scene_warnings(edit, TRANSCRIPT, TRIMS) == ["scenes[0]: 0.6 s long after the cuts, under 1 s"]


def test_an_edit_without_scenes_has_no_spans_and_no_warnings():
    edit = default_edit(TRANSCRIPT, TRIMS)
    assert scene_spans(edit, TRANSCRIPT, TRIMS) == []
    assert scene_warnings(edit, TRANSCRIPT, TRIMS) == []


# ---- the review page ------------------------------------------------------------------------------------------


def test_the_review_page_may_remove_the_sentence_of_a_scene_or_cut_its_words():
    edit = edit_with(scene("sc1", at={"s": "s02", "char": 2}, until={"s": "s02", "char": 3}))
    removed, _ = remove_sentence(edit, TRANSCRIPT, TRIMS, "s02")
    assert validate_edit(removed, TRANSCRIPT, TRIMS) == []
    assert removed["scenes"] == edit["scenes"]
    cut = set_cut(copy.deepcopy(edit), "t01", True)
    assert validate_edit(cut, TRANSCRIPT, TRIMS) == []
    assert scene_warnings(cut, TRANSCRIPT, TRIMS) == [
        "scenes[0]: nothing of it is left after the cuts, so it is not shown"
    ]


# ---- what the storyboard and the review page show ---------------------------------------------------------------


def test_scene_layout_gives_every_scene_its_source_times_and_its_place_in_the_reel():
    # sc1 is in s01, which cuts.extra removes; sc2 runs from 亮 (2.0 s) to the end of 天 (s03 char 1, 6.8 s), across
    # the cut 3.0-4.0
    edit = edit_with(scene("sc1", card="keyword-pop", at={"s": "s01", "char": 0}, until={"s": "s01", "end": True},
                           params={"text": "省電"}),
                     scene("sc2", at={"s": "s02", "char": 0}, until={"s": "s03", "char": 1}),
                     accept=("t01",), extra=("s01",))
    layout = scenes.scene_layout(edit, TRANSCRIPT, TRIMS)
    assert [(item.index, item.gone, item.source_start, item.source_end) for item in layout] == [
        (0, "s01", 0.0, 1.5), (1, None, 2.0, 6.8)]
    assert (layout[0].start, layout[0].frames) == (0, 0)
    assert [Placed(item.index, item.scene, item.start, item.frames) for item in layout[1:]] == scene_spans(
        edit, TRANSCRIPT, TRIMS)
    assert scenes.scene_layout(edit_with(), TRANSCRIPT, TRIMS) == []


def test_scene_label_names_the_card_and_joins_its_texts():
    assert scenes.scene_label(scene(params={"value": "兩小時", "label": "一天多撐"})) == ("數字卡", "兩小時／一天多撐")
    assert scenes.scene_label(scene(params={"value": "兩小時"})) == ("數字卡", "兩小時")
    assert scenes.scene_label(scene(params={"value": " 兩小時 ", "label": " "})) == ("數字卡", "兩小時")
    assert scenes.scene_label(scene(card="keyword-pop", params={"text": "亮度最耗電"})) == ("重點字", "亮度最耗電")
    assert scenes.scene_label(scene(card="bullet-list", params={"items": ["亮度", "背景更新", "定位"]})) == (
        "清單", "亮度／背景更新／定位")


def test_scene_texts_are_the_texts_of_the_params_in_order():
    assert scenes.scene_texts(scene(params={"value": "兩小時", "label": "一天多撐"})) == ["兩小時", "一天多撐"]
    assert scenes.scene_texts(scene(params={"value": "兩小時"})) == ["兩小時"]
    assert scenes.scene_texts(scene(card="bullet-list", params={"items": ["亮度", "定位"]})) == ["亮度", "定位"]


def test_the_module_names_its_constants():
    assert (scenes.MIN_SCENE_FRAMES, scenes.MAX_IMPACT) == (30, 3)
