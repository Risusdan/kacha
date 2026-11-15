"""Where a card goes on the zone top: centred, or to one side of the speaker's head (pipeline.card_place)."""

import pytest
from helpers import default_edit, timed_sentence

from pipeline import card_place, punch
from pipeline.edit import validate_edit
from pipeline.punch import Shot
from pipeline.scenes import card_height_px, card_width_px, scene_spans
from pipeline.style import font_path, load_style

CANVAS = (1080, 1920)
ZONE = {"x0": 65, "x1": 1015, "y0": 269, "y1": 1040}
PORTRAIT = (1080, 1920)  # a displayed source already 9:16: the base window is the whole frame


def face(t: float, x0: float, y0: float, x1: float, y1: float) -> dict:
    return {"t": t, "x0": x0, "y0": y0, "x1": x1, "y1": y1, "score": 0.9}


def shot(start: float, end: float, scale: float = 1.0, ax: float = 0.5, ay: float = 0.5) -> Shot:
    return Shot(start, end, "s01", scale, ax, ay)


SHOTS = [shot(0, 100)]


def align(boxes, width=400, height=150, start=10.0, end=12.0, shots=SHOTS, display=PORTRAIT):
    return card_place.card_align(width, height, ZONE, CANVAS, boxes, start, end, shots, display)


def test_without_a_face_the_card_is_centred():
    assert align([]) == "center"


def test_a_card_clear_of_the_head_is_centred():
    # a face low on the screen: even with the hair above it, the head starts under the card
    assert align([face(11, 0.35, 0.6, 0.65, 0.8)]) == "center"


def test_a_head_on_the_left_sends_the_card_right_and_one_on_the_right_sends_it_left():
    assert align([face(11, 0.0, 0.2, 0.4, 0.65)]) == "right"
    assert align([face(11, 0.6, 0.2, 1.0, 0.65)]) == "left"


def test_when_both_sides_are_clear_the_card_goes_to_the_one_away_from_the_head():
    # a small head a little left of the middle: the centre covers it, either side would not
    assert align([face(11, 0.38, 0.2, 0.48, 0.3)], width=300) == "right"
    assert align([face(11, 0.52, 0.2, 0.62, 0.3)], width=300) == "left"


def test_when_every_place_covers_the_head_the_card_goes_where_it_covers_least():
    # a head a little right of the middle, too wide to clear: the left side covers less of it than the right
    boxes = [face(11, 0.38, 0.2, 0.7, 0.65)]
    assert align(boxes, width=600) == "left"
    assert card_place.overlap(card_place.card_rect("left", 600, 150, ZONE), card_place.keep_out(
        [card_place.screen_box(boxes[0], SHOTS[0], PORTRAIT, CANVAS)])) > 0


def test_the_head_is_the_face_box_with_hair_above_and_a_margin_on_each_side():
    head = card_place.keep_out([(100.0, 500.0, 300.0, 900.0)])
    assert head == pytest.approx((100 - 0.15 * 200, 500 - 0.5 * 400, 300 + 0.15 * 200, 900))


def test_a_face_box_is_placed_on_the_screen_through_the_centre_crop():
    # a 16:9 source: the base window is its centre 9:16 crop, so the middle of the frame is the middle of the screen
    box = face(0, 0.45, 0.25, 0.55, 0.5)
    x0, y0, x1, y1 = card_place.screen_box(box, shot(0, 10), (1920, 1080), CANVAS)
    assert (x0 + x1) / 2 == pytest.approx(540, abs=2)
    assert (y0, y1) == pytest.approx((0.25 * 1920, 0.5 * 1920), abs=4)
    assert x1 - x0 == pytest.approx(0.1 * 1920 / 606 * 1080, abs=4)  # 192 source px of a 606 px wide window


def test_a_zoomed_shot_keeps_its_anchor_still_and_pushes_the_rest_out():
    box = face(0, 0.2, 0.4, 0.3, 0.5)
    x0, y0, x1, y1 = card_place.screen_box(box, shot(0, 10, scale=1.2, ax=0.2, ay=0.4), PORTRAIT, CANVAS)
    assert (x0, y0) == pytest.approx((0.2 * 1080, 0.4 * 1920))
    assert (x1, y1) == pytest.approx(((0.2 + 0.1 * 1.2) * 1080, (0.4 + 0.1 * 1.2) * 1920))


def test_only_the_faces_found_while_the_card_shows_count_and_else_the_nearest_one():
    left, right = face(11, 0.0, 0.2, 0.4, 0.65), face(30, 0.6, 0.2, 1.0, 0.65)
    assert align([left, right]) == "right"  # the card shows from 10 to 12 s: only the face at 11 s counts
    assert align([face(15, 0.6, 0.2, 1.0, 0.65), face(40, 0.0, 0.2, 0.4, 0.65)]) == "left"  # none inside: 15 s is nearest


def head_through(box: dict, shots: list[Shot]) -> str:
    """Where a 400 x 150 card goes for the head of `box` put on the screen through each of `shots`."""
    return card_place.choose_align(400, 150, ZONE, card_place.keep_out(
        [card_place.screen_box(box, each, PORTRAIT, CANVAS) for each in shots]))


def test_a_face_is_placed_with_every_framing_shown_while_the_card_shows():
    # the card shows from 10 to 12 s, across a cut into a shot zoomed 1.2x about the bottom edge
    box = face(11, 0.62, 0.32, 0.8, 0.5)
    shots = [shot(0, 10.5), shot(10.5, 20, scale=1.2, ax=0.5, ay=1.0)]
    assert head_through(box, shots[:1]) == "center"  # the first framing alone leaves the middle clear
    assert align([box], shots=shots) == "left"


def test_a_face_found_before_the_card_stands_in_with_the_framing_on_screen_while_the_card_shows():
    # faces every 4 s; the card shows from 10.6 to 12.6 s, all in a shot zoomed 1.2x from 10.5 s: the nearest face,
    # at 10 s, was found in the shot before, whose framing leaves the middle clear
    boxes = [face(t, 0.38, 0.3, 0.58, 0.45) for t in (2, 6, 10, 14)]
    shots = [shot(0, 10.5), shot(10.5, 16, scale=1.2, ax=0.5, ay=0.375)]
    assert head_through(boxes[2], shots[:1]) == "center"
    assert align(boxes, start=10.6, end=12.6, shots=shots) == "right"


def test_a_face_found_in_a_part_the_cuts_remove_does_not_count():
    # 10.5 to 11.5 s is cut: the face on the left at 11 s never shows with the card, the one on the right does
    removed, shown = face(11, 0.0, 0.2, 0.4, 0.65), face(10.2, 0.6, 0.2, 1.0, 0.65)
    assert align([removed, shown], shots=[shot(0, 10.5), shot(11.5, 20)]) == "left"


def test_without_a_shot_while_the_card_shows_the_faces_are_placed_with_the_nearest_shot():
    # a face right of the middle card; zoomed 1.2x about the right edge, it is pulled in over the middle
    box = face(11, 0.72, 0.2, 0.8, 0.65)
    near, far = shot(0, 9.5), shot(14, 30, scale=1.2, ax=1.0, ay=0.3)
    assert head_through(box, [near]) != head_through(box, [far])
    assert align([box], shots=[near, far]) == head_through(box, [near])


def test_a_head_wider_than_every_place_leaves_the_card_in_the_middle():
    assert align([face(11, 0.0, 0.2, 1.0, 0.65)]) == "center"


# ---- every card of an edit ------------------------------------------------------------------------------------

# s01 0.0-1.5 (5 characters of 0.3 s), s02 2.0-5.0 (6 of 0.5 s); nothing to cut. The sentences are made up.
TRANSCRIPT = {"sentences": [
    timed_sentence("s01", "今天聊省電。", [(round(0.3 * k, 3), round(0.3 * (k + 1), 3)) for k in range(5)]),
    timed_sentence("s02", "亮度調低一點。", [(2.0 + 0.5 * k, 2.5 + 0.5 * k) for k in range(6)]),
]}
TRIMS = {"trims": [], "bounds": {"s01": {"cut_in": 0.0, "cut_out": 1.6}, "s02": {"cut_in": 1.9, "cut_out": 5.1}}}
KEYWORD = {"id": "sc1", "card": "keyword-pop", "at": {"s": "s01"}, "until": {"s": "s01", "end": True},
           "params": {"text": "省電"}}
LIST = {"id": "sc2", "card": "bullet-list", "at": {"s": "s02"}, "until": {"s": "s02", "end": True},
        "params": {"items": ["亮度", "背景更新"]}}


def edit_of(*scenes: dict) -> dict:
    edit = default_edit(TRANSCRIPT, TRIMS)
    edit["scenes"] = list(scenes)
    assert validate_edit(edit, TRANSCRIPT, TRIMS) == []
    return edit


def placed_scenes(edit: dict, boxes: list[dict], style: dict | None = None) -> list:
    shots = punch.shots(edit, TRANSCRIPT, TRIMS, {"boxes": boxes}, {"display_w": 1080, "display_h": 1920})
    return card_place.placed_scenes(edit, TRANSCRIPT, TRIMS, style or load_style(), boxes, shots, PORTRAIT)


def test_each_card_of_an_edit_is_placed_away_from_the_head_found_while_it_shows():
    edit = edit_of(KEYWORD, LIST)
    boxes = [face(0.7, 0.0, 0.2, 0.4, 0.65), face(3.5, 0.6, 0.2, 1.0, 0.65)]  # left during s01, right during s02
    sides = [item.align for item in placed_scenes(edit, boxes)]
    assert sides == ["right", "left"]
    spans = scene_spans(edit, TRANSCRIPT, TRIMS)
    assert placed_scenes(edit, boxes) == [item._replace(align=side) for item, side in zip(spans, sides)]


def test_a_card_is_measured_at_the_largest_size_it_reaches_and_placed_over_the_source_times_it_shows(monkeypatch):
    seen = []

    def record(width, height, zone, canvas, boxes, start, end, shots, display):
        seen.append((width, height, zone, canvas, start, end))
        return "center"

    monkeypatch.setattr(card_place, "card_align", record)
    style = load_style()
    placed_scenes(edit_of(KEYWORD, LIST), [], style)
    fonts = {weight: font_path(style, weight) for weight in ("regular", "bold")}
    pop, items = KEYWORD["params"], LIST["params"]
    zone = style["scenes"]["zone"]
    assert seen == [
        (pytest.approx(card_width_px("keyword-pop", pop, fonts) * 1.04), pytest.approx(card_height_px("keyword-pop", pop) * 1.04),
         zone, CANVAS, 0.0, 1.5),
        (pytest.approx(card_width_px("bullet-list", items, fonts)), pytest.approx(card_height_px("bullet-list", items)),
         zone, CANVAS, 2.0, 5.0),
    ]


def test_an_edit_without_scenes_places_nothing_and_needs_no_animation_zone():
    style = {key: value for key, value in load_style().items() if key != "scenes"}
    assert placed_scenes(edit_of(), [face(1, 0.0, 0.2, 0.4, 0.65)], style) == []
