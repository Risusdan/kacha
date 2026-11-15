"""Where an animation card goes on the top edge of the animation zone: centred, or flush with the zone's left or right
edge, so that it covers as little of the speaker's head as it can.

The head is worked out from the face boxes of work/faces.json found while the card shows: in source seconds from the
start of its scene to its end, and not in a part the cuts remove (outside every shot). When none was, the box found
nearest to that time stands in, one outside the removed parts when there is any. Each box is put on the screen with
every framing shown while the card shows, that of each shot overlapping its time (or of the shot nearest to it, when
none does): the centre 9:16 crop of the displayed picture (punch.base_window), zoomed about the shot's anchor
(punch.window), and scaled to the canvas. A face box runs from the brows to the chin and leaves the ears out, so the
head is the box with HAIR of its height added above and SIDE of its width added on each side; the head of a card is
the smallest rectangle holding all these heads.

A card is centred when that leaves the head clear; else it goes to the side away from the head, unless that covers as
much of the head as the centre does (a head wider than the card's every place). The side towards the head is never
better: the left and right places are mirror images about the zone's middle, so the one nearer the head covers at
least as much of it. A card is placed at the largest size it reaches (keyword-pop's grow).
"""

from pipeline.punch import Shot, base_window, window
from pipeline.scenes import Placed, card_height_px, card_sizes, card_width_px, scene_layout, scene_spans
from pipeline.style import font_path

CANVAS = (1080, 1920)  # the reel's width and height in pixels, which the cards are drawn on

HAIR = 0.5  # of a face box's height, above it: the forehead, the hair and the top of the head
SIDE = 0.15  # of a face box's width, on each side: the ears and the hair

Rect = tuple[float, float, float, float]  # x0, y0, x1, y1 in canvas pixels


def screen_box(box: dict, shot: Shot, display: tuple[int, int], canvas: tuple[int, int]) -> Rect:
    """Face `box` of faces.json (0-1 of the displayed picture, `display` (w, h) pixels) on the canvas (w, h) of a
    reel showing it in `shot`."""
    bx, by, bw, bh = base_window(*display)
    left, top, size, _ = window((shot.ax, shot.ay), shot.scale)

    def x(value: float) -> float:
        return ((value * display[0] - bx) / bw - left) / size * canvas[0]

    def y(value: float) -> float:
        return ((value * display[1] - by) / bh - top) / size * canvas[1]

    return x(box["x0"]), y(box["y0"]), x(box["x1"]), y(box["y1"])


def keep_out(boxes: list[Rect]) -> Rect | None:
    """The head around face `boxes` on the canvas (see the module text), or None when there is no box."""
    if not boxes:
        return None
    heads = [(x0 - SIDE * (x1 - x0), y0 - HAIR * (y1 - y0), x1 + SIDE * (x1 - x0), y1) for x0, y0, x1, y1 in boxes]
    return (min(h[0] for h in heads), min(h[1] for h in heads), max(h[2] for h in heads), max(h[3] for h in heads))


def card_rect(align: str, width: float, height: float, zone: dict) -> Rect:
    """Where a card `width` x `height` is drawn on the top edge of `zone` when aligned `align`."""
    if align == "left":
        x0 = zone["x0"]
    elif align == "right":
        x0 = zone["x1"] - width
    else:
        x0 = zone["x0"] + (zone["x1"] - zone["x0"] - width) / 2
    return x0, zone["y0"], x0 + width, zone["y0"] + height


def overlap(a: Rect, b: Rect | None) -> float:
    """The area two rectangles share; 0 when `b` is None."""
    if b is None:
        return 0.0
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def choose_align(width: float, height: float, zone: dict, head: Rect | None) -> str:
    """"center", "left" or "right" for a card `width` x `height` on `zone`, given the `head` (see the module text)."""
    centre = overlap(card_rect("center", width, height, zone), head)
    if centre == 0:
        return "center"
    far = "right" if (head[0] + head[2]) / 2 < (zone["x0"] + zone["x1"]) / 2 else "left"
    return far if overlap(card_rect(far, width, height, zone), head) < centre else "center"


def _distance(t: float, start: float, end: float) -> float:
    return max(start - t, 0.0, t - end)


def card_align(width: float, height: float, zone: dict, canvas: tuple[int, int], boxes: list[dict],
               start: float, end: float, shots: list[Shot], display: tuple[int, int]) -> str:
    """Where a card `width` x `height` goes on `zone` (choose_align) while it shows from source second `start` to
    `end`, given the face `boxes` of faces.json, the reel's `shots` (punch.shots, not empty) and the displayed
    picture's size `display` (see the module text)."""
    on_screen = [s for s in shots if s.start < end and s.end > start]
    if not on_screen:
        on_screen = [min(shots, key=lambda s: max(s.start - end, 0.0, start - s.end))]
    kept = [box for box in boxes if any(s.start <= box["t"] < s.end for s in shots)] or boxes
    found = [box for box in kept if start <= box["t"] <= end]
    if not found and kept:
        found = [min(kept, key=lambda box: _distance(box["t"], start, end))]
    placed = [screen_box(box, each, display, canvas) for box in found for each in on_screen]
    return choose_align(width, height, zone, keep_out(placed))


def placed_scenes(edit: dict, transcript: dict, trims: dict, style: dict, boxes: list[dict], shots: list[Shot],
                  display: tuple[int, int]) -> list[Placed]:
    """The scenes of a valid edit that show (scenes.scene_spans), each with where its card goes (card_align), given
    the face `boxes` of faces.json, the reel's `shots` (punch.shots) and the displayed picture's size `display`.
    The style's animation zone and fonts are read only when a scene shows."""
    spans = scene_spans(edit, transcript, trims)
    if not spans:
        return []
    times = {item.index: (item.source_start, item.source_end) for item in scene_layout(edit, transcript, trims)}
    zone = style["scenes"]["zone"]
    fonts = {weight: font_path(style, weight) for weight in ("regular", "bold")}
    placed = []
    for item in spans:
        card, params = item.scene["card"], item.scene["params"]
        grow = card_sizes()[card].get("grow", 1)
        width, height = card_width_px(card, params, fonts) * grow, card_height_px(card, params) * grow
        start, end = times[item.index]
        align = card_align(width, height, zone, CANVAS, boxes, start, end, shots, display)
        placed.append(item._replace(align=align))
    return placed
