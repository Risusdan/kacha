"""A real render of each animation card through scenes/render.mjs: node, scenes/node_modules (npm ci), the browser
Remotion downloads and the style's fonts must all be there, or the tests are skipped. The cards are rendered once
for the module, each twice, so that the two renders can be compared frame by frame, and once more each placed to
one side of the zone. The texts are made up."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from pipeline.media import ffmpeg_bin, ffprobe_bin
from pipeline.scene_render import STILL_FRAME, STILL_FRAMES
from pipeline.scenes import card_height_px, card_width_px

ROOT = Path(__file__).resolve().parents[1]
SCENES = ROOT / "scenes"
STYLE_DIR = ROOT / "styles" / "default"
ZONE = {"x0": 65, "x1": 1015, "y0": 269, "y1": 1040}
STYLE = {
    "zone": ZONE,
    "colors": {"card": "#FFFFFF", "text": "#111111", "accent": "#FFD400", "muted": "#5C5C5C"},
    "radius_px": 32,
    "fonts": {"regular": "fonts/NotoSansTC-Regular.ttf", "bold": "fonts/NotoSansTC-Bold.ttf"},
}
CARDS = {
    "keyword-pop": ({"text": "亮度最耗電"}, 40),
    "number-slab": ({"value": "兩小時", "label": "一天多撐"}, 40),
    "bullet-list": ({"items": ["亮度", "背景更新", "定位"]}, 50),
    "title-card": ({"kicker": "三個省電技巧", "title": "亮度才是關鍵"}, 40),
    "bar-chart": ({"bars": [{"label": "前年", "value": "0%"}, {"label": "去年", "value": "49%"},
                            {"label": "今年", "value": "98%"}]}, 60),
    "versus-split": ({"left": "自動亮度", "left_note": "比較省電", "right": "手動", "right_note": "看得清楚"}, 40),
}
# Cards placed to one side of the zone, each rendered once: name -> (card, params, frames, align). Every card goes to
# one side, and keyword-pop, which grows, to both. Its text is long, so that a pill cut off at the zone's edge as it
# grows would be narrower by more than the tolerance. The title has wide spaces at its ends and a kicker of spaces
# only, which are not drawn, and the versus-split one note, so that its columns are of different heights.
SIDES = {
    "pop-left": ("keyword-pop", {"text": "亮度最耗電的設定"}, 40, "left"),
    "pop-right": ("keyword-pop", {"text": "亮度最耗電的設定"}, 40, "right"),
    "slab-right": ("number-slab", {"value": "兩小時", "label": "一天多撐"}, 40, "right"),
    "list-left": ("bullet-list", {"items": ["亮度", "背景更新", "定位"]}, 50, "left"),
    "title-right": ("title-card", {"kicker": " \u3000", "title": "\u3000亮度才是關鍵\u00a0"}, 40, "right"),
    "bars-left": ("bar-chart", {"bars": [{"label": "去年", "value": "49%"}, {"label": "今年", "value": "98%"}]}, 60,
                  "left"),
    "versus-right": ("versus-split", {"left": "自動", "right": "手動調整", "right_note": "比較耗電"}, 40, "right"),
}
# The stills (scene_render.render_stills): each card at rest in the centre, and one to the right.
STILLS = {**{f"still-{card}": (card, params, "center") for card, (params, _) in CARDS.items()},
          "still-slab-right": ("number-slab", {"value": "兩小時", "label": "一天多撐"}, "right")}
FONTS = {weight: STYLE_DIR / path for weight, path in STYLE["fonts"].items()}
W, H = 1080, 1920


def requirements_or_skip() -> str:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    if not (SCENES / "node_modules" / "@remotion" / "renderer").is_dir():
        pytest.skip("scenes/node_modules is not installed: npm ci in scenes/")
    if not (SCENES / "node_modules" / ".remotion" / "chrome-headless-shell").is_dir():
        pytest.skip("the browser Remotion renders with is not downloaded")
    for font in STYLE["fonts"].values():
        if not (STYLE_DIR / font).is_file():
            pytest.skip(f"{font} is missing: uv run pipeline setup")
    return node


@pytest.fixture(scope="module")
def rendered(tmp_path_factory) -> dict:
    """card -> (first render, second render), and name -> its render (SIDES). Run from a folder of its own (no
    package.json above it), as render.mjs must find the browser under scenes/ wherever it is run from."""
    node = requirements_or_skip()
    folder = tmp_path_factory.mktemp("cards")
    (folder / "tmp").mkdir()
    (folder / "cwd").mkdir()
    jobs = []
    for card, (params, frames) in CARDS.items():
        for run in ("a", "b"):
            jobs.append({"card": card, "params": params, "frames": frames, "align": "center",
                         "out": str(folder / f"{card}-{run}.mov")})
    for name, (card, params, frames, side) in SIDES.items():
        jobs.append({"card": card, "params": params, "frames": frames, "align": side, "out": str(folder / f"{name}.mov")})
    for name, (card, params, side) in STILLS.items():
        jobs.append({"kind": "still", "card": card, "params": params, "frames": STILL_FRAMES, "frame": STILL_FRAME,
                     "align": side, "out": str(folder / f"{name}.png")})
    jobs_file = folder / "jobs.json"
    jobs_file.write_text(json.dumps({"style_dir": str(STYLE_DIR), "style": STYLE, "jobs": jobs}), encoding="utf-8")
    done = subprocess.run([node, str(SCENES / "render.mjs"), str(jobs_file)], cwd=folder / "cwd",
                          capture_output=True, text=True, timeout=600, env={**os.environ, "TMPDIR": str(folder / "tmp")})
    assert not (folder / "cwd" / ".remotion").exists(), "Remotion downloaded a browser into the working folder"
    assert done.returncode == 0, done.stdout + done.stderr
    lines = [json.loads(line) for line in done.stdout.splitlines() if line.startswith("{")]
    assert [line["out"] for line in lines] == [job["out"] for job in jobs]
    assert all(line["ok"] for line in lines)
    assert not list(folder.glob("*.part.mov"))
    assert not list(folder.glob("*.part.png"))
    return {**{card: (folder / f"{card}-a.mov", folder / f"{card}-b.mov") for card in CARDS},
            **{name: folder / f"{name}.mov" for name in SIDES},
            **{name: folder / f"{name}.png" for name in STILLS}}


def probe(path: Path) -> dict:
    done = subprocess.run(
        [ffprobe_bin(), "-v", "error", "-count_frames", "-show_entries",
         "stream=codec_type,codec_name,profile,pix_fmt,width,height,r_frame_rate,nb_read_frames,color_space,"
         "color_range", "-of", "json",
         str(path)],
        capture_output=True, text=True, check=True, timeout=120)
    return json.loads(done.stdout)


def alpha(path: Path, frame: int) -> np.ndarray:
    """The alpha plane (0 to 255) of one frame, H rows by W columns."""
    done = subprocess.run(
        [ffmpeg_bin(), "-v", "error", "-i", str(path), "-vf", rf"select=eq(n\,{frame})", "-frames:v", "1",
         "-f", "rawvideo", "-pix_fmt", "rgba", "-"],
        capture_output=True, check=True, timeout=120)
    return np.frombuffer(done.stdout, dtype=np.uint8).reshape(H, W, 4)[:, :, 3]


def alpha_rows(path: Path, y: int) -> np.ndarray:
    """The alpha (0 to 255) of row `y` of every frame, one row of W per frame."""
    done = subprocess.run(
        [ffmpeg_bin(), "-v", "error", "-i", str(path), "-vf", f"crop={W}:1:0:{y}", "-f", "rawvideo", "-pix_fmt",
         "rgba", "-"],
        capture_output=True, check=True, timeout=120)
    return np.frombuffer(done.stdout, dtype=np.uint8).reshape(-1, W, 4)[:, :, 3]


def rgba_709(path: Path, frame: int) -> np.ndarray:
    """One frame as RGBA (0 to 255), H rows by W columns, decoded as BT.709 limited range, as the reels are."""
    done = subprocess.run(
        [ffmpeg_bin(), "-v", "error", "-i", str(path), "-vf",
         rf"select=eq(n\,{frame}),scale=in_color_matrix=bt709:in_range=limited,format=rgba", "-frames:v", "1",
         "-f", "rawvideo", "-"],
        capture_output=True, check=True, timeout=120)
    return np.frombuffer(done.stdout, dtype=np.uint8).reshape(H, W, 4)


def frame_hashes(path: Path) -> list[str]:
    done = subprocess.run([ffmpeg_bin(), "-v", "error", "-i", str(path), "-f", "framemd5", "-"],
                          capture_output=True, text=True, check=True, timeout=120)
    return [line.rsplit(",", 1)[-1].strip() for line in done.stdout.splitlines() if not line.startswith("#")]


@pytest.mark.parametrize("card", CARDS)
def test_a_card_is_a_transparent_prores_4444_clip_of_the_frames_asked_for(rendered, card):
    streams = probe(rendered[card][0])["streams"]
    assert [stream["codec_type"] for stream in streams] == ["video"]
    video = streams[0]
    assert (video["codec_name"], video["profile"]) == ("prores", "4444")
    assert video["pix_fmt"].startswith("yuva444")
    assert (video["width"], video["height"], video["r_frame_rate"]) == (W, H, "30/1")
    assert int(video["nb_read_frames"]) == CARDS[card][1]


def test_a_card_is_encoded_as_bt709_limited_range_like_the_reels_it_goes_over(rendered):
    video = probe(rendered["keyword-pop"][0])["streams"][0]
    assert (video["color_space"], video["color_range"]) == ("bt709", "tv")


def test_the_accent_of_a_card_decoded_as_bt709_is_the_accent_of_the_style(rendered):
    frame = rgba_709(rendered["keyword-pop"][0], CARDS["keyword-pop"][1] // 2)
    accent = np.array([int(STYLE["colors"]["accent"][i:i + 2], 16) for i in (1, 3, 5)])
    opaque = frame[frame[:, :, 3] == 255][:, :3].astype(int)
    near = np.abs(opaque - accent).max(axis=1) <= 3
    assert near.sum() > 10_000, f"{near.sum()} opaque pixels within 3 of {accent}"


@pytest.mark.parametrize("card", CARDS)
def test_the_card_is_opaque_on_the_zone_top_and_the_rest_of_the_frame_is_transparent(rendered, card):
    plane = alpha(rendered[card][0], CARDS[card][1] // 2)
    assert plane[ZONE["y0"] + 40, W // 2] == 255
    outside = np.ones_like(plane, dtype=bool)
    outside[ZONE["y0"]:ZONE["y1"], ZONE["x0"]:ZONE["x1"]] = False
    assert plane[outside].max() == 0


@pytest.mark.parametrize("card", CARDS)
def test_the_card_is_as_wide_as_validate_measures_it(rendered, card):
    """The middle frame, when every card is at rest: the opaque run of the row 40 px under the zone top is the
    width card_width_px gives from scenes/src/sizes.json and the style's fonts, to a few pixels."""
    row = alpha(rendered[card][0], CARDS[card][1] // 2)[ZONE["y0"] + 40]
    assert abs(int((row >= 128).sum()) - card_width_px(card, CARDS[card][0], FONTS)) <= 4


@pytest.mark.parametrize("name", SIDES)
def test_a_card_placed_to_one_side_is_flush_with_that_edge_of_the_zone(rendered, name):
    """The middle frame, at rest: the opaque run of the row 40 px under the zone top starts at the zone's left edge,
    or ends at its right edge, and is as wide as card_width_px gives."""
    card, params, frames, side = SIDES[name]
    opaque = np.flatnonzero(alpha(rendered[name], frames // 2)[ZONE["y0"] + 40] >= 128)
    if side == "left":
        assert abs(int(opaque[0]) - ZONE["x0"]) <= 1
    else:
        assert abs(int(opaque[-1]) + 1 - ZONE["x1"]) <= 1
    assert abs(len(opaque) - card_width_px(card, params, FONTS)) <= 4


@pytest.mark.parametrize("name", SIDES)
def test_a_card_placed_to_one_side_is_as_high_as_card_height_px_gives(rendered, name):
    """The middle frame, at rest: the opaque run of the card's middle column, from the zone top down."""
    card, params, frames, _ = SIDES[name]
    plane = alpha(rendered[name], frames // 2)
    across = np.flatnonzero(plane[ZONE["y0"] + 40] >= 128)
    opaque = np.flatnonzero(plane[:, (across[0] + across[-1]) // 2] >= 128)
    assert opaque[0] == ZONE["y0"]
    assert abs(int(opaque[-1] - opaque[0] + 1) - card_height_px(card, params)) <= 4


def test_the_two_big_words_of_a_versus_split_are_level_when_only_one_has_a_note(rendered):
    """The middle frame: the first row of near-black text in each column, the VS circle between them left out."""
    _, params, frames, _ = SIDES["versus-right"]
    frame = rgba_709(rendered["versus-right"], frames // 2)
    size = json.loads((SCENES / "src" / "sizes.json").read_text(encoding="utf-8"))["versus-split"]
    across = np.flatnonzero(frame[ZONE["y0"] + 40, :, 3] >= 128)
    column = (len(across) - 2 * size["padding_x"] - size["badge"] - 2 * size["badge_gap"]) // 2
    left = across[0] + size["padding_x"]
    right = across[-1] + 1 - size["padding_x"] - column
    dark = (frame[:, :, :3].max(axis=2) < 80) & (frame[:, :, 3] == 255)
    tops = [int(np.flatnonzero(dark[:, x:x + column].any(axis=1))[0]) for x in (left, right)]
    assert abs(tops[0] - tops[1]) <= 6, tops


@pytest.mark.parametrize("name", ["pop-left", "pop-right"])
def test_a_keyword_placed_to_one_side_grows_away_from_that_edge_and_is_not_cut_off(rendered, name):
    """As it pops, the pill reaches 1.038 of its width (grow in sizes.json is that, rounded up): scaled about its
    middle, the part past the zone's edge would not be drawn and the widest frame would be narrower."""
    card, params, _, _ = SIDES[name]
    widest = max(int((row >= 128).sum()) for row in alpha_rows(rendered[name], ZONE["y0"] + 40))
    assert abs(widest - 1.038 * card_width_px(card, params, FONTS)) <= 4


@pytest.mark.parametrize("card", CARDS)
def test_the_card_is_as_high_as_card_height_px_gives(rendered, card):
    """The middle frame, at rest: the opaque run of the middle column, from the zone top down, is the height
    card_height_px gives from scenes/src/sizes.json, to a few pixels."""
    column = alpha(rendered[card][0], CARDS[card][1] // 2)[:, W // 2]
    opaque = np.flatnonzero(column >= 128)
    assert opaque[0] == ZONE["y0"]
    assert abs(int(opaque[-1] - opaque[0] + 1) - card_height_px(card, CARDS[card][0])) <= 4


@pytest.mark.parametrize("card", CARDS)
def test_the_card_is_not_there_on_its_first_and_last_frame(rendered, card):
    frames = CARDS[card][1]
    assert alpha(rendered[card][0], 0).max() == 0
    assert alpha(rendered[card][0], frames - 1).max() == 0


@pytest.mark.parametrize("card", CARDS)
def test_two_renders_of_a_card_are_identical_frame_by_frame(rendered, card):
    first, second = (frame_hashes(path) for path in rendered[card])
    assert len(first) == CARDS[card][1]
    assert first == second


def test_each_bar_is_as_long_as_its_number_over_the_largest(rendered):
    """Once every bar has grown, before the exit (frame 50 of 60: the exit starts at 51): along the middle of each
    bar, the run of the style's accent is the full 420 px track for the largest number, half of it for half that
    number, and nothing for 0."""
    params, frames = CARDS["bar-chart"]
    size = json.loads((SCENES / "src" / "sizes.json").read_text(encoding="utf-8"))["bar-chart"]
    label = size["label"]["size"] * size["label"]["line_height"]
    value = size["value"]["size"] * size["value"]["line_height"]
    row = label + size["label_gap"] + max(size["bar"], value)
    frame = rgba_709(rendered["bar-chart"][0], frames - 10)
    accent = np.array([int(STYLE["colors"]["accent"][i:i + 2], 16) for i in (1, 3, 5)])
    runs = []
    for k in range(len(params["bars"])):
        y = round(ZONE["y0"] + size["padding_y"] + k * (row + size["row_gap"]) + label + size["label_gap"]
                  + max(size["bar"], value) / 2)
        line = frame[y, :, :3].astype(int)
        runs.append(int((np.abs(line - accent).max(axis=1) <= 8).sum()))
    track = size["track"]
    assert runs[0] == 0
    assert abs(runs[1] - track * 49 / 98) <= 4
    assert abs(runs[2] - track) <= 4


def opaque_box(plane: np.ndarray) -> tuple[int, int, int, int]:
    """x0, y0, x1, y1 of the pixels of an alpha plane at 128 or more."""
    ys, xs = np.nonzero(plane >= 128)
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def still_alpha(path: Path) -> np.ndarray:
    done = subprocess.run([ffmpeg_bin(), "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "rgba", "-"],
                          capture_output=True, check=True, timeout=120)
    return np.frombuffer(done.stdout, dtype=np.uint8).reshape(H, W, 4)[:, :, 3]


@pytest.mark.parametrize("card", CARDS)
def test_a_still_is_the_card_at_rest_as_its_clip_shows_it_before_the_exit(rendered, card):
    """The still is a 1080x1920 picture with transparency, and the card in it covers what it covers in the last frame
    of its clip before the exit (frames - 9): a card at rest looks the same however long it shows."""
    still = rendered[f"still-{card}"]
    video = probe(still)["streams"][0]
    assert (video["codec_name"], video["width"], video["height"]) == ("png", W, H)
    assert video["pix_fmt"] == "rgba"
    plane = still_alpha(still)
    assert plane.min() == 0
    frames = CARDS[card][1]
    clip = opaque_box(alpha(rendered[card][0], frames - 9))
    assert all(abs(a - b) <= 2 for a, b in zip(opaque_box(plane), clip, strict=True)), (opaque_box(plane), clip)


def test_a_still_placed_to_the_right_is_flush_with_the_right_edge_of_the_zone(rendered):
    x0, y0, x1, _ = opaque_box(still_alpha(rendered["still-slab-right"]))
    assert abs(x1 - ZONE["x1"]) <= 1 and y0 == ZONE["y0"]
    width = card_width_px("number-slab", {"value": "兩小時", "label": "一天多撐"}, FONTS)
    assert abs((x1 - x0) - width) <= 4
