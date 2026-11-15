"""A real render of each animation card through scenes/render.mjs: node, scenes/node_modules (npm ci), the browser
Remotion downloads and the style's fonts must all be there, or the tests are skipped. The cards are rendered once
for the module, each twice, so that the two renders can be compared frame by frame. The texts are made up."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from pipeline.media import ffmpeg_bin, ffprobe_bin
from pipeline.scenes import card_width_px

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
}
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
def rendered(tmp_path_factory) -> dict[str, tuple[Path, Path]]:
    """card -> (first render, second render). Run from a folder of its own (no package.json above it), as
    render.mjs must find the browser under scenes/ wherever it is run from."""
    node = requirements_or_skip()
    folder = tmp_path_factory.mktemp("cards")
    (folder / "tmp").mkdir()
    (folder / "cwd").mkdir()
    jobs = []
    for card, (params, frames) in CARDS.items():
        for run in ("a", "b"):
            jobs.append({"card": card, "params": params, "frames": frames, "out": str(folder / f"{card}-{run}.mov")})
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
    return {card: (folder / f"{card}-a.mov", folder / f"{card}-b.mov") for card in CARDS}


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
    fonts = {weight: STYLE_DIR / path for weight, path in STYLE["fonts"].items()}
    row = alpha(rendered[card][0], CARDS[card][1] // 2)[ZONE["y0"] + 40]
    assert abs(int((row >= 128).sum()) - card_width_px(card, CARDS[card][0], fonts)) <= 4


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
