"""Punch-in: the scale and the anchor of every shot of the reel (docs/superpowers/specs/2026-11-15-punch-in-design.md).

A shot is a segment of the reel (edit.build_segments): a stretch of the source between two cut points. Every shot is
at the base framing (scale 1) but those edit.json punch_in sets: it maps a sentence id to a scale (1 to MAX_SCALE),
and every shot that starts in that sentence (edit.segment_sentences) takes it. A shot shorter than MIN_SHOT_S that
punch_in does not set keeps the scale of the shot before it, so the picture does not flash for a frame or two.

The base window is the centre 9:16 crop of the displayed source, in even pixels: the framing of scale 1. A shot
punched in to scale s shows the part of it 1/s wide and high that keeps the anchor where it is on the screen: a
fixed-point zoom about the speaker's face, which stays still for the whole shot. The anchor, in 0-1 of the base
window, is the median centre of the faces work/faces.json found inside the shot, or the face sampled nearest the
middle of the shot when none was, or the centre when the video has no face at all; a face outside the base window
is moved to its nearest edge.
"""

import statistics
from dataclasses import dataclass

from pipeline.edit import FPS, Segment, build_segments, segment_sentences, to_frame

MAX_SCALE = 1.2
MIN_SHOT_S = 1.0  # a shot shorter than this keeps the scale of the shot before it


@dataclass(frozen=True)
class Shot:
    """A segment of the reel with its framing: the sentence it starts in, its scale and its anchor (ax, ay) in 0-1
    of the base window."""

    start: float
    end: float
    sentence: str
    scale: float
    ax: float
    ay: float


def shot_scales(segments: list[Segment], starts_in: list[str], overrides: dict) -> list[float]:
    """The scale of each of `segments`, the sentence each starts in being `starts_in` and edit.json punch_in being
    `overrides` (see the module text)."""
    scales: list[float] = []
    for segment, sid in zip(segments, starts_in):
        if sid in overrides:
            scale = float(overrides[sid])
        elif scales and to_frame(segment.end) - to_frame(segment.start) < MIN_SHOT_S * FPS:
            scale = scales[-1]
        else:
            scale = 1.0
        scales.append(scale)
    return scales


def _even(value: float) -> int:
    return int(value) // 2 * 2


def base_window(display_w: int, display_h: int) -> tuple[int, int, int, int]:
    """(x, y, w, h) of the base window in a displayed frame `display_w` x `display_h`: the centre 9:16 crop, every
    value even (yuv420 has a chroma sample per 2x2 pixels) and inside the frame."""
    if display_w * 16 >= display_h * 9:  # at least as wide as 9:16: the full height
        h = _even(display_h)
        w = _even(h * 9 / 16)
    else:
        w = _even(display_w)
        h = _even(w * 16 / 9)
    return _even((display_w - w) / 2), _even((display_h - h) / 2), w, h


def anchors(segments: list[Segment], boxes: list[dict], display_w: int, display_h: int) -> list[tuple[float, float]]:
    """The anchor of each of `segments` in 0-1 of the base window, from the face `boxes` of faces.json (t in source
    seconds, x0 y0 x1 y1 in 0-1 of the displayed frame); see the module text."""
    x, y, w, h = base_window(display_w, display_h)
    centres = [(b["t"], (b["x0"] + b["x1"]) / 2, (b["y0"] + b["y1"]) / 2) for b in boxes]
    found: list[tuple[float, float]] = []
    for segment in segments:
        if not centres:
            found.append((0.5, 0.5))
            continue
        inside = [c for c in centres if segment.start <= c[0] < segment.end]
        if inside:
            fx, fy = statistics.median(c[1] for c in inside), statistics.median(c[2] for c in inside)
        else:
            middle = (segment.start + segment.end) / 2
            _, fx, fy = min(centres, key=lambda c: abs(c[0] - middle))
        found.append((min(1.0, max(0.0, (fx * display_w - x) / w)), min(1.0, max(0.0, (fy * display_h - y) / h))))
    return found


def window(anchor: tuple[float, float], scale: float) -> tuple[float, float, float, float]:
    """(left, top, width, height) in 0-1 of the base window of what a shot at `scale` about `anchor` shows."""
    size = 1 / scale
    return anchor[0] * (1 - size), anchor[1] * (1 - size), size, size


def framings(edit: dict, transcript: dict, trims: dict) -> dict[str, list[tuple[float, float, float]]]:
    """Sentence id -> (source start, source end, scale) of each shot punched in (a scale over 1) that starts in the
    sentence, in the order of the reel, for a valid edit; a sentence no such shot starts in is not a key. No
    anchors, so no faces.json is read: what the storyboard shows."""
    segments = build_segments(edit, transcript, trims)
    starts = segment_sentences(edit, transcript, trims, segments)
    found: dict[str, list[tuple[float, float, float]]] = {}
    for segment, sid, scale in zip(segments, starts, shot_scales(segments, starts, edit.get("punch_in") or {})):
        if scale != 1:
            found.setdefault(sid, []).append((segment.start, segment.end, scale))
    return found


def shots(edit: dict, transcript: dict, trims: dict, faces: dict, probe: dict) -> list[Shot]:
    """The shots of a valid edit: its segments with their sentences, scales and anchors. `faces` is work/faces.json
    and `probe` work/probe.json (display_w, display_h)."""
    segments = build_segments(edit, transcript, trims)
    starts = segment_sentences(edit, transcript, trims, segments)
    overrides = edit.get("punch_in") or {}
    scales = shot_scales(segments, starts, overrides)
    points = anchors(segments, faces.get("boxes", []), probe["display_w"], probe["display_h"])
    return [Shot(segment.start, segment.end, sid, scale, ax, ay)
            for segment, sid, scale, (ax, ay) in zip(segments, starts, scales, points)]
