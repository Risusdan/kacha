"""Punch-in: the scale and the anchor of every shot of the reel (spec 2026-11-15-punch-in-design)."""

import pytest

from pipeline import punch
from pipeline.edit import Segment, build_segments, segment_sentences
from pipeline.punch import MAX_SCALE, MIN_SHOT_S, Shot, anchors, base_window, shot_scales, window

# ---- which sentence a segment starts in --------------------------------------------------------

# Three spoken sentences, back to back but for a gap between s02 and s03; s04 was not spoken.
TRANSCRIPT = {"sentences": [{"id": "s01", "spoken": True}, {"id": "s02", "spoken": True},
                            {"id": "s03", "spoken": True}, {"id": "s04", "spoken": False}]}
BOUNDS = {"s01": {"cut_in": 0.0, "cut_out": 4.0}, "s02": {"cut_in": 4.0, "cut_out": 8.0},
          "s03": {"cut_in": 9.0, "cut_out": 14.0}}


def edit_with(accept: list[str], extra: list[dict] | None = None, punch_in: dict | None = None) -> dict:
    edit = {"cuts": {"accept": accept, "reject": [], "extra": extra or []}}
    if punch_in is not None:
        edit["punch_in"] = punch_in
    return edit


def trims_with(trims: list[dict]) -> dict:
    return {"trims": trims, "bounds": BOUNDS}


def test_a_segment_starts_in_the_sentence_whose_cut_points_hold_its_first_frame():
    # cuts at 2-3 (inside s01) and 5-6 (inside s02): segments start at 0 (s01), 3 (s01) and 6 (s02); the gap
    # between s02 and s03 is in the reel too, so the last segment runs from 6 to 14
    trims = trims_with([{"id": "t01", "start": 2.0, "end": 3.0}, {"id": "t02", "start": 5.0, "end": 6.0}])
    edit = edit_with(["t01", "t02"])
    segments = build_segments(edit, TRANSCRIPT, trims)
    assert segments == [Segment(0.0, 2.0), Segment(3.0, 5.0), Segment(6.0, 14.0)]
    assert segment_sentences(edit, TRANSCRIPT, trims, segments) == ["s01", "s01", "s02"]


def test_a_segment_that_starts_in_the_gap_between_two_sentences_belongs_to_the_next():
    trims = trims_with([{"id": "t01", "start": 7.0, "end": 8.5}])  # cut from inside s02 into the gap
    edit = edit_with(["t01"])
    segments = build_segments(edit, TRANSCRIPT, trims)
    assert segments == [Segment(0.0, 7.0), Segment(8.5, 14.0)]
    assert segment_sentences(edit, TRANSCRIPT, trims, segments) == ["s01", "s03"]


def test_a_segment_that_starts_where_a_sentence_ends_belongs_to_the_next():
    trims = trims_with([{"id": "t01", "start": 3.0, "end": 4.0}])
    edit = edit_with(["t01"])
    segments = build_segments(edit, TRANSCRIPT, trims)
    assert segment_sentences(edit, TRANSCRIPT, trims, segments) == ["s01", "s02"]


def test_a_removed_sentence_owns_no_segment():
    trims = trims_with([])
    edit = edit_with([], extra=[{"from": "s02", "to": "s02", "reason": "x"}])
    segments = build_segments(edit, TRANSCRIPT, trims)
    assert segments == [Segment(0.0, 4.0), Segment(8.0, 14.0)]
    assert segment_sentences(edit, TRANSCRIPT, trims, segments) == ["s01", "s03"]


# ---- scales -------------------------------------------------------------------------------------


def test_the_scales_of_the_design():
    assert (MAX_SCALE, MIN_SHOT_S) == (1.2, 1.0)
    assert not hasattr(punch, "DEFAULT_SCALE")  # nothing is punched in unless edit.json punch_in says so


def segs(*lengths: float) -> list[Segment]:
    out, t = [], 0.0
    for length in lengths:
        out.append(Segment(t, t + length))
        t += length + 1.0
    return out


def test_without_punch_in_every_shot_is_at_the_base_framing():
    assert shot_scales(segs(3, 3, 3, 3, 3), ["s01"] * 5, {}) == [1.0] * 5
    assert shot_scales(segs(3, 0.07, 3), ["s01"] * 3, {}) == [1.0] * 3


def test_a_reel_of_one_segment_is_never_punched_in():
    assert shot_scales(segs(30), ["s01"], {}) == [1.0]


def test_punch_in_sets_every_shot_that_starts_in_its_sentence_and_no_other():
    starts = ["s01", "s02", "s02", "s03", "s04"]
    assert shot_scales(segs(3, 3, 3, 3, 3), starts, {"s02": 1.1}) == [1.0, 1.1, 1.1, 1.0, 1.0]
    assert shot_scales(segs(3, 3, 3, 3, 3), starts, {"s03": 1.2}) == [1.0, 1.0, 1.0, 1.2, 1.0]
    assert shot_scales(segs(3, 3, 3, 3, 3), starts, {"s01": 1.15, "s04": 1.1}) == [1.15, 1.0, 1.0, 1.0, 1.1]
    assert shot_scales(segs(3, 3, 3), ["s01", "s02", "s03"], {"s02": 1}) == [1.0, 1.0, 1.0]


def test_a_shot_shorter_than_a_second_keeps_the_scale_before_it():
    starts = ["s01", "s02", "s03", "s04"]
    assert shot_scales(segs(3, 3, 0.5, 3), starts, {"s02": 1.1}) == [1.0, 1.1, 1.1, 1.0]
    assert shot_scales(segs(3, 3, 1.0, 3), starts, {"s02": 1.1}) == [1.0, 1.1, 1.0, 1.0]  # a second is long enough


def test_punch_in_holds_for_a_short_shot_too():
    assert shot_scales(segs(3, 0.5, 3), ["s01", "s02", "s03"], {"s02": 1.2}) == [1.0, 1.2, 1.0]


# ---- the base window and the anchors -----------------------------------------------------------


@pytest.mark.parametrize("w, h, window_", [
    (2160, 3840, (0, 0, 2160, 3840)),   # 9:16 already: the whole frame
    (1080, 1920, (0, 0, 1080, 1920)),
    (1920, 1080, (656, 0, 606, 1080)),  # landscape: the centre 9:16, even sizes and offsets
    (1081, 1921, (0, 0, 1080, 1920)),   # odd sizes: even and inside the frame
    (1080, 2400, (0, 240, 1080, 1920)),  # taller than 9:16: the centre band
])
def test_the_base_window_is_the_centre_9_16_crop_in_even_pixels(w, h, window_):
    x, y, bw, bh = base_window(w, h)
    assert (x, y, bw, bh) == window_
    assert all(v % 2 == 0 for v in (x, y, bw, bh)) and x + bw <= w and y + bh <= h
    assert abs(bw / bh - 9 / 16) < 0.01


def box(t: float, cx: float, cy: float, size: float = 0.2) -> dict:
    return {"t": t, "x0": cx - size / 2, "y0": cy - size / 2, "x1": cx + size / 2, "y1": cy + size / 2,
            "score": 0.9}


def test_the_anchor_is_the_median_face_centre_inside_the_shot():
    boxes = [box(1.0, 0.20, 0.40), box(3.0, 0.30, 0.50), box(5.0, 0.90, 0.45), box(11.0, 0.6, 0.6)]
    (ax, ay), = anchors([Segment(0.0, 6.0)], boxes, 1080, 1920)
    assert (ax, ay) == pytest.approx((0.30, 0.45))


def test_a_shot_without_a_face_sample_takes_the_sample_nearest_its_middle():
    boxes = [box(1.0, 0.2, 0.4), box(20.0, 0.7, 0.3)]
    assert anchors([Segment(8.0, 12.0)], boxes, 1080, 1920) == [pytest.approx((0.2, 0.4))]   # middle 10: 9 vs 10 away
    assert anchors([Segment(12.0, 16.0)], boxes, 1080, 1920) == [pytest.approx((0.7, 0.3))]


def test_without_any_face_the_anchor_is_the_centre():
    assert anchors([Segment(0.0, 3.0), Segment(4.0, 6.0)], [], 1080, 1920) == [(0.5, 0.5), (0.5, 0.5)]


def test_a_face_of_a_landscape_frame_is_placed_in_the_base_window_and_clamped_to_its_edge():
    # 1920x1080: the base window is x 656..1262; a face at the frame centre is at its centre, one at x 0.1 of the
    # frame (192 px) is left of the window: its edge
    (centre,) = anchors([Segment(0.0, 2.0)], [box(1.0, 0.5, 0.5)], 1920, 1080)
    assert centre == pytest.approx(((960 - 656) / 606, 0.5))
    (left,) = anchors([Segment(0.0, 2.0)], [box(1.0, 0.1, 0.3)], 1920, 1080)
    assert left == pytest.approx((0.0, 0.3))


@pytest.mark.parametrize("anchor", [(0.0, 0.0), (1.0, 1.0), (0.18, 0.43), (0.5, 0.5), (0.9, 0.1)])
@pytest.mark.parametrize("scale", [1.0, 1.1, 1.2])
def test_the_window_keeps_the_anchor_in_place_and_stays_inside_the_base(anchor, scale):
    left, top, w, h = window(anchor, scale)
    assert (w, h) == pytest.approx((1 / scale, 1 / scale))
    assert 0 <= left and left + w <= 1 + 1e-9 and 0 <= top and top + h <= 1 + 1e-9
    # the anchor is at the same place of the window as of the base: (anchor - left) / w == anchor
    assert ((anchor[0] - left) / w, (anchor[1] - top) / h) == pytest.approx(anchor)


# ---- the framings by sentence (what the storyboard shows) --------------------------------------


def test_framings_list_the_span_and_scale_of_every_punched_in_shot_by_the_sentence_it_starts_in():
    # segments start at 0 (s01, 2 s long), 3 (s01, 2 s) and 6 (s02, 8 s)
    trims = trims_with([{"id": "t01", "start": 2.0, "end": 3.0}, {"id": "t02", "start": 5.0, "end": 6.0}])
    assert punch.framings(edit_with(["t01", "t02"], punch_in={"s02": 1.2}), TRANSCRIPT, trims) == {
        "s02": [(6.0, 14.0, 1.2)]}
    assert punch.framings(edit_with(["t01", "t02"], punch_in={"s01": 1.1}), TRANSCRIPT, trims) == {
        "s01": [(0.0, 2.0, 1.1), (3.0, 5.0, 1.1)]}


def test_framings_without_punch_in_are_empty():
    trims = trims_with([{"id": "t01", "start": 2.0, "end": 3.0}])
    assert punch.framings(edit_with(["t01"]), TRANSCRIPT, trims) == {}
    assert punch.framings(edit_with([]), TRANSCRIPT, trims_with([])) == {}


# ---- the shots ------------------------------------------------------------------------------------


def test_shots_put_the_scales_the_anchors_and_the_sentences_together():
    trims = trims_with([{"id": "t01", "start": 2.0, "end": 3.0}, {"id": "t02", "start": 5.0, "end": 6.0}])
    edit = edit_with(["t01", "t02"], punch_in={"s02": 1.2})
    faces = {"boxes": [box(1.0, 0.2, 0.4), box(4.0, 0.3, 0.5), box(10.0, 0.4, 0.6)]}
    probe = {"display_w": 1080, "display_h": 1920}
    got = punch.shots(edit, TRANSCRIPT, trims, faces, probe)
    assert [(s.start, s.end, s.sentence, s.scale) for s in got] == [
        (0.0, 2.0, "s01", 1.0), (3.0, 5.0, "s01", 1.0), (6.0, 14.0, "s02", 1.2)]
    assert not hasattr(got[0], "changed")  # every scale but 1 comes from punch_in
    assert [(s.ax, s.ay) for s in got] == [pytest.approx((0.2, 0.4)), pytest.approx((0.3, 0.5)),
                                           pytest.approx((0.4, 0.6))]
    assert isinstance(got[0], Shot)
