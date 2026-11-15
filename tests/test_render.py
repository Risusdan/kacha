"""The final render: audio cut, filter graph, output spec checks, font check, retry and the render command.

approved_project is the Meijia project (conftest) with the storyboard built and gate 1 approved. Its video is
a gray counter (frame N is gray 16 + 4 * (N % 50)), so the tests can read which source frame every frame of
the output came from. Tests that need another kind of source build it with helpers.meijia_approved_project.
"""

import json
import math
import re
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from helpers import (
    MEIJIA,
    approve_project,
    approved_project_in,
    corner_luma,
    event,
    force_gate1_open,
    gray_index,
    loudness,
    meijia_approved_project,
    project_with,
    require_assets,
    sample_frame_pairs,
    segments_of,
    static_font,
    synth_wav,
    trim_project,
    write_default_edit,
)
from media_fixtures import make_clip

from pipeline import punch, words
from pipeline.captions import caption_top_px, write_ass
from pipeline.cli import main
from pipeline.edit import Segment
from pipeline.errors import PipelineError
from pipeline.media import ffmpeg_bin, ffprobe_bin, ffprobe_json, probe, run_ffmpeg
from pipeline.prep import prep
from pipeline.punch import Shot, base_window, window
from pipeline.project import Project, gate_ok, load_state, next_step, save_state, step_done
from pipeline.render import (
    AUDIO_BITRATE,
    LENGTH_TOLERANCE_S,
    PREVIEW_MAX_BYTES,
    PREVIEW_OVER,
    _balanced_sum,
    _ffmpeg_rate,
    build_ffmpeg_args,
    build_preview_args,
    check_fontselect,
    check_output,
    cut_audio,
    make_preview,
    preview_bitrate,
    render,
    select_expr,
    video_bitrate,
    zoom_exprs,
)
from pipeline.scene_render import render_scenes
from pipeline.scenes import Placed
from pipeline.style import load_style, styles_root

FPS = 30


# ---- helpers ----------------------------------------------------------------------------------


def reel_seconds(p) -> float:
    return sum(s.end - s.start for s in segments_of(p))


def recording(calls: list):
    """A runner that notes its arguments in `calls` and then runs ffmpeg for real."""

    def run(args, log, cwd=None):
        calls.append({"args": list(args), "log": log, "cwd": cwd})
        run_ffmpeg(args, log, cwd)

    return run


def assert_frame_accurate(out: Path, p, reference: Path | None = None) -> None:
    """The output has one frame for every kept source frame, and the first, middle and last frame of every
    segment shows the source frame it should."""
    segments = segments_of(p)
    assert len(segments) >= 3  # a render with one segment would not test the joins
    assert len(corner_luma(out)) == sum(round((s.end - s.start) * FPS) for s in segments)
    for reel_frame, source_frame in sample_frame_pairs(segments):
        assert gray_index(out, reel_frame, reference) == source_frame % 50, (reel_frame, source_frame)


def decoded_audio(video: Path, dest: Path) -> np.ndarray:
    """The sound of `video` as a mono float array at 48 kHz."""
    run_ffmpeg(["-y", "-i", str(video), "-vn", "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le", str(dest)],
               dest.with_suffix(".log"))
    data, rate = sf.read(str(dest), dtype="float32")
    assert rate == 48000
    return data


def envelope_ms(samples: np.ndarray) -> np.ndarray:
    """|samples| averaged over 1 ms (48 samples)."""
    usable = len(samples) // 48 * 48
    return np.abs(samples[:usable]).reshape(-1, 48).mean(axis=1)


def audio_lag_s(out_audio: np.ndarray, expected: np.ndarray, search_ms: int = 150) -> float:
    """How many seconds later than `expected` the sound of `out_audio` comes (negative: earlier), from the
    peak of the cross-correlation of their 1 ms envelopes."""
    a, b = envelope_ms(out_audio), envelope_ms(expected)
    n = min(len(a), len(b)) - 2 * search_ms
    a, b = a - a.mean(), b - b.mean()
    core = a[search_ms:search_ms + n]
    scores = {
        lag: float(np.dot(core, b[search_ms - lag:search_ms - lag + n])) for lag in range(-search_ms, search_ms + 1)
    }
    return max(scores, key=scores.get) / 1000


def expected_reel_audio(p) -> np.ndarray:
    """What the sound of the reel is: the fixture speech cut at the segments, no fades, from the
    original fixture.wav (not from the prep copy, so that prep's alignment is tested too)."""
    speech, rate = sf.read(str(MEIJIA / "fixture.wav"), dtype="float32")
    assert rate == 48000
    return np.concatenate([speech[round(s.start * rate):round(s.end * rate)] for s in segments_of(p)])


def test_audio_lag_helper_sees_a_delay():
    speech, _ = sf.read(str(MEIJIA / "fixture.wav"), dtype="float32")
    speech = speech[:10 * 48000]
    assert audio_lag_s(speech, speech) == 0
    assert audio_lag_s(np.concatenate([np.zeros(2400, dtype="float32"), speech]), speech) == pytest.approx(0.05)
    assert audio_lag_s(speech[2400:], speech) == pytest.approx(-0.05)


# ---- cut_audio --------------------------------------------------------------------------------


def test_cut_audio_length_and_fades(tmp_path):
    src = synth_wav(tmp_path, [("tone", 4.0, -6)], sr=48000, channels=2)
    out = tmp_path / "cut.wav"
    assert cut_audio(src, [Segment(0, 1), Segment(2, 3)], out) == pytest.approx(2.0)
    data, sr = sf.read(out)
    assert len(data) == 96000
    assert abs(data[48000][0]) < 0.01 and abs(data[47999][0]) < 0.01  # 接點兩側是淡入淡出的底


def test_cut_audio_takes_exactly_1600_samples_per_frame_and_leaves_the_middle_alone(tmp_path):
    src = synth_wav(tmp_path, [("tone", 4.0, -6)], sr=48000, channels=2)
    out = tmp_path / "cut.wav"
    assert cut_audio(src, [Segment(10 / FPS, 40 / FPS), Segment(70 / FPS, 71 / FPS)], out) == pytest.approx(31 / FPS)
    data, _ = sf.read(out, dtype="float32")
    source, _ = sf.read(src, dtype="float32")
    assert data.shape == (31 * 1600, 2)
    assert np.array_equal(data[480:48000 - 480], source[16000 + 480:16000 + 48000 - 480])  # between the fades
    assert np.array_equal(data[48000 + 480:48000 + 1600 - 480], source[112000 + 480:112000 + 1600 - 480])


def test_cut_audio_fades_are_linear_and_cover_ten_milliseconds(tmp_path):
    src = synth_wav(tmp_path, [("tone", 2.0, -6)], sr=48000, channels=2)
    out = tmp_path / "cut.wav"
    cut_audio(src, [Segment(0.5, 1.0)], out)
    data, _ = sf.read(out, dtype="float32")
    source, _ = sf.read(src, dtype="float32")
    piece = source[24000:48000]
    ramp = np.arange(480, dtype="float32") / 480
    assert np.allclose(data[:480], piece[:480] * ramp[:, None], atol=2e-4)  # 10 ms in
    assert np.allclose(data[-480:], piece[-480:] * ramp[::-1][:, None], atol=2e-4)  # 10 ms out


def test_cut_audio_fade_param_and_short_segment(tmp_path):
    src = synth_wav(tmp_path, [("tone", 1.0, -6)], sr=48000, channels=2)
    out = tmp_path / "cut.wav"
    assert cut_audio(src, [Segment(0.0, 0.01)], out) == pytest.approx(0.01)  # shorter than two fades
    assert len(sf.read(out)[0]) == 480
    assert cut_audio(src, [Segment(0.0, 0.5)], out, fade_s=0.0) == pytest.approx(0.5)  # no fade
    assert sf.read(out)[0][0][0] == sf.read(src)[0][0][0]


def test_cut_audio_refuses_a_segment_past_the_end_of_the_audio(tmp_path):
    src = synth_wav(tmp_path, [("tone", 1.0, -6)], sr=48000, channels=2)
    with pytest.raises(PipelineError, match="audio"):
        cut_audio(src, [Segment(0.5, 1.5)], tmp_path / "cut.wav")


# ---- the filter graph and the arguments -------------------------------------------------------


def test_select_expr():
    assert select_expr([Segment(0, 1), Segment(2, 2.5)]) == "gte(pts,0)*lt(pts,30)+gte(pts,60)*lt(pts,75)"


def test_select_expr_names_frames_by_number_not_by_time():
    """After fps=30 the pts of a frame is its number, so a boundary is an integer and cannot be off by a
    rounding error of the frame's time."""
    assert select_expr([Segment(111 / FPS, 114 / FPS), Segment(204 / FPS, 207 / FPS)]) == (
        "gte(pts,111)*lt(pts,114)+gte(pts,204)*lt(pts,207)"
    )
    assert select_expr([Segment(0.05, 0.5)]) == "gte(pts,2)*lt(pts,15)"  # off the grid: the nearest frame


def test_select_expr_maps_every_boundary_frame_to_its_own_integer():
    """Ten minutes of frame boundaries, start and end of every one-frame segment."""
    for k in range(FPS * 600):
        assert select_expr([Segment(k / FPS, (k + 1) / FPS)]) == f"gte(pts,{k})*lt(pts,{k + 1})", k


def pick_frames(tmp_path: Path, clip: Path, segments: list[Segment]) -> list[int]:
    """The counter values (frame number % 50) of the frames that `select_expr(segments)` keeps from `clip`."""
    expr = select_expr(segments)
    out = tmp_path / "picked.mkv"
    run_ffmpeg(["-y", "-i", str(clip), "-vf", f"setpts=PTS-STARTPTS,fps=30,select='{expr}',setpts=N/30/TB",
                "-fps_mode", "cfr", "-r", "30", "-c:v", "ffv1", str(out)], tmp_path / "picked.log")
    return [gray_index(out, k) for k in range(len(corner_luma(out)))]


def test_select_expr_keeps_exactly_the_listed_frames(tmp_path):
    """Run the expression on a ten second counter clip: a segment of one or two frames at every third frame."""
    clip = make_clip(tmp_path, w=64, h=64, seconds=10, audio=None, gray_counter=True)
    pieces = [(a, a + 1 + a % 2) for a in range(0, 291, 3)]
    assert len(pieces) == 97
    wanted = [frame % 50 for a, b in pieces for frame in range(a, b)]
    assert pick_frames(tmp_path, clip, [Segment(a / FPS, b / FPS) for a, b in pieces]) == wanted


def ones(n: int) -> list[Segment]:
    """n one-frame segments, at frames 0, 2, 4 ..."""
    return [Segment(2 * i / FPS, (2 * i + 1) / FPS) for i in range(n)]


def max_paren_depth(expr: str) -> int:
    depth = deepest = 0
    for c in expr:
        depth += (c == "(") - (c == ")")
        deepest = max(deepest, depth)
    return deepest


def sum_tree_depth(expr: str) -> int:
    """The depth of the tree that ffmpeg's expression parser makes of `expr` (made of gte(pts,N), lt(pts,N), *, + and
    parentheses). The parser reads `a+b+c` as `(a+b)+c`, so a chain of n terms is n deep, and it refuses a tree
    about a hundred deep."""

    def parse_sum(i: int) -> tuple[int, int]:
        depth, i = parse_product(i)
        while i < len(expr) and expr[i] == "+":
            other, i = parse_product(i + 1)
            depth = max(depth, other) + 1
        return depth, i

    def parse_product(i: int) -> tuple[int, int]:
        depth, i = parse_factor(i)
        while i < len(expr) and expr[i] == "*":
            other, i = parse_factor(i + 1)
            depth = max(depth, other) + 1
        return depth, i

    def parse_factor(i: int) -> tuple[int, int]:
        if expr[i] == "(":
            depth, i = parse_sum(i + 1)
            assert expr[i] == ")"
            return depth, i + 1
        return 1, expr.index(")", i) + 1  # a call such as gte(pts,111)

    depth, end = parse_sum(0)
    assert end == len(expr)
    return depth


@pytest.mark.parametrize("n", [1, 2, 3, 99, 100, 1000])
def test_select_expr_nests_as_deep_as_log2_of_the_number_of_segments(n):
    """ffmpeg cannot parse a chain of 100 terms or more (it fails with "Cannot allocate memory"): the sum is a
    balanced tree. Both the parentheses and the parser's tree stay about log2(n) deep."""
    expr = select_expr(ones(n))
    limit = math.ceil(math.log2(n)) + 2
    assert max_paren_depth(expr) <= limit
    assert sum_tree_depth(expr) <= limit


def test_select_expr_groups_the_terms_in_a_balanced_tree():
    one, two, three, four = (f"gte(pts,{a})*lt(pts,{a + 1})" for a in (0, 2, 4, 6))
    assert select_expr(ones(1)) == one
    assert select_expr(ones(2)) == f"{one}+{two}"
    assert select_expr(ones(3)) == f"{one}+({two}+{three})"
    assert select_expr(ones(4)) == f"({one}+{two})+({three}+{four})"


def test_select_expr_of_a_thousand_segments_keeps_exactly_the_listed_frames(tmp_path):
    """A reel can have more than the 99 segments a plain chain of terms allows: 1000 one-frame segments, a
    frame in every two, on a counter clip (2010 frames of 64x64, which is cheap)."""
    clip = make_clip(tmp_path, w=64, h=64, seconds=67, audio=None, gray_counter=True)
    assert pick_frames(tmp_path, clip, ones(1000)) == [2 * i % 50 for i in range(1000)]


def test_select_expr_is_exact_where_a_frame_time_is_a_six_place_number(tmp_path):
    """Frames 111, 207 and 222 are 3.7, 6.9 and 7.4 s: exact six-place numbers, and for some of them the time ffmpeg
    works out for the frame (frame number times 1/30, as a double) is a hair below the number written in the
    expression. Selecting by that time lost frame 111 and added frames 207 and 222."""
    clip = make_clip(tmp_path, w=64, h=64, seconds=9, audio=None, gray_counter=True)
    pieces = [(111, 114), (204, 207), (219, 222)]
    wanted = [frame % 50 for a, b in pieces for frame in range(a, b)]
    assert pick_frames(tmp_path, clip, [Segment(a / FPS, b / FPS) for a, b in pieces]) == wanted


BASE = (0, 0, 1080, 1920)  # the base window of a 1080x1920 picture: all of it


def flat(segments: list[Segment]) -> list[Shot]:
    """`segments` as shots of the base framing: scale 1, anchored at the centre."""
    return [Shot(s.start, s.end, "s01", 1.0, 0.5, 0.5) for s in segments]


GRAPH = (
    "[0:v]setpts=PTS-STARTPTS,fps=30,select='gte(pts,0)*lt(pts,30)',setpts=N/30/TB,"
    "crop=1080:1920:0:0,zoompan=z='1':x='0':y='0':d=1:s=1080x1920:fps=30,"
    "subtitles=filename=captions.ass:fontsdir=fonts,format=yuv420p[v]"
)

# Frames 15-44 of the source at the base framing, then frames 60-104 punched in to 1.2 about (0.25, 0.5).
TWO_SHOTS = [Shot(0.51, 1.49, "s01", 1.0, 0.25, 0.5), Shot(2.0, 3.5, "s02", 1.2, 0.25, 0.5)]


def test_zoom_exprs_hold_each_punched_in_shot_over_its_frames_of_the_reel():
    """The second shot is frames 30-74 of the reel (the first is 30 frames: 15 to 45, as select counts them). At 1.2
    about (0.25, 0.5) of a 1080x1920 base window it shows the 900x1600 part from x 45, which is odd: 44 (an even
    pixel, as yuv420 has one chroma sample per 2x2 pixels), and y 160."""
    assert zoom_exprs(TWO_SHOTS, BASE) == (
        "1+gte(on,30)*lt(on,75)*0.2", "gte(on,30)*lt(on,75)*44", "gte(on,30)*lt(on,75)*160")


def test_zoom_exprs_of_the_base_framing_alone_are_constants():
    assert zoom_exprs(flat([Segment(0, 1), Segment(2, 3)]), BASE) == ("1", "0", "0")


def test_zoom_exprs_place_the_window_in_pixels_of_the_base_window():
    # a 1920x1080 picture: the base window is 606x1080; x 0.25 * (1 - 1/1.2) * 606 = 25.25 -> 26, y 90
    assert zoom_exprs(TWO_SHOTS, (656, 0, 606, 1080)) == (
        "1+gte(on,30)*lt(on,75)*0.2", "gte(on,30)*lt(on,75)*26", "gte(on,30)*lt(on,75)*90")


def test_zoom_exprs_add_their_terms_up_as_a_balanced_tree():
    """Like select's, a sum of many terms is a balanced tree: ffmpeg cannot parse a chain of 100 terms or more.
    200 two-frame shots, every second one at 1.1 about the centre (x 0.5 * (1 - 1/1.1) * 1080 = 49.09, whose nearest
    even pixel is 50; y 87.27 -> 88)."""
    shots = [Shot(3 * i / FPS, (3 * i + 2) / FPS, "s01", 1.1 if i % 2 else 1.0, 0.5, 0.5) for i in range(200)]
    spans = [f"gte(on,{2 * i})*lt(on,{2 * i + 2})" for i in range(1, 200, 2)]
    assert zoom_exprs(shots, BASE) == (
        "1+" + _balanced_sum([f"{span}*0.1" for span in spans]),
        _balanced_sum([f"{span}*50" for span in spans]),
        _balanced_sum([f"{span}*88" for span in spans]),
    )


def test_the_graph_crops_the_base_window_and_zooms_each_shot(tmp_path):
    fs = tmp_path / "f.txt"
    build_ffmpeg_args(Path("/v.mov"), Path("/c.wav"), TWO_SHOTS, (656, 0, 606, 1080), tmp_path / "o.mp4", fs)
    assert fs.read_text() == (
        "[0:v]setpts=PTS-STARTPTS,fps=30,select='gte(pts,15)*lt(pts,45)+gte(pts,60)*lt(pts,105)',setpts=N/30/TB,"
        "crop=606:1080:656:0,"
        "zoompan=z='1+gte(on,30)*lt(on,75)*0.2':x='gte(on,30)*lt(on,75)*26':y='gte(on,30)*lt(on,75)*90'"
        ":d=1:s=1080x1920:fps=30,"
        "subtitles=filename=captions.ass:fontsdir=fonts,format=yuv420p[v]"
    )


def test_args_pin_output_spec(tmp_path):
    fs = tmp_path / "f.txt"
    args = build_ffmpeg_args(Path("/v.mov"), Path("/c.wav"), flat([Segment(0, 1)]), BASE, tmp_path / "o.mp4", fs)
    for want in ("h264_videotoolbox", "12000k", "192k", "48000", "aac", "+faststart"):
        assert want in args
    graph = fs.read_text()
    # every frame is scaled once, by zoompan, from the base window to 1080x1920
    assert "scale" not in graph and graph.count("zoompan=") == 1 and ":s=1080x1920:" in graph
    assert "fps=30" in graph and "fontsdir=fonts" in graph


def test_args_and_graph_are_exactly_the_specified_ones(tmp_path):
    fs = tmp_path / "f.txt"
    args = build_ffmpeg_args(Path("/v.mov"), Path("/c.wav"), flat([Segment(0, 1)]), BASE, tmp_path / "o.mp4", fs)
    assert fs.read_text().strip() == GRAPH
    assert args == [
        "-hide_banner", "-loglevel", "verbose", "-y", "-i", "/v.mov", "-i", "/c.wav",
        "-/filter_complex", str(fs), "-map", "[v]", "-map", "1:a",
        "-c:v", "h264_videotoolbox", "-b:v", "12000k", "-profile:v", "high", "-r", "30", "-fps_mode", "cfr",
        "-tag:v", "avc1", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart", str(tmp_path / "o.mp4"),
    ]


def test_the_graph_has_no_path_in_it(tmp_path):
    """Only relative names go into the graph, so a studio path with a space or a quote cannot break it."""
    fs = tmp_path / "my st'udio" / "f.txt"
    fs.parent.mkdir()
    build_ffmpeg_args(Path("/in box/it's.mov"), Path("/c.wav"), flat([Segment(0, 1)]), BASE, tmp_path / "o.mp4", fs,
                      scenes=[(0, Path("/my sc'enes/a.mov"))])
    graph = fs.read_text()
    assert not any(part in graph for part in ("in box", "it's", "c.wav", "o.mp4", "udio", "enes", "a.mov",
                                              str(tmp_path)))


def test_the_graph_overlays_each_scene_after_the_zoom_and_before_the_captions(tmp_path):
    """Each clip is an input of its own, after the picture and the sound. It is moved to the reel frame its scene
    starts at and overlaid on the zoomed picture, which shows as it is again when the clip ends; the captions are
    burnt in last, over the cards."""
    fs = tmp_path / "f.txt"
    clips = [(30, Path("/w/scenes/a.mov")), (75, Path("/w/scenes/b.mov"))]
    args = build_ffmpeg_args(Path("/v.mov"), Path("/c.wav"), flat([Segment(0, 4)]), BASE, tmp_path / "o.mp4", fs,
                             scenes=clips)
    assert fs.read_text() == (
        "[0:v]setpts=PTS-STARTPTS,fps=30,select='gte(pts,0)*lt(pts,120)',setpts=N/30/TB,"
        "crop=1080:1920:0:0,zoompan=z='1':x='0':y='0':d=1:s=1080x1920:fps=30[picture];"
        "[2:v]setpts=PTS-STARTPTS+30/30/TB[scene0];[picture][scene0]overlay=eof_action=pass[with0];"
        "[3:v]setpts=PTS-STARTPTS+75/30/TB[scene1];[with0][scene1]overlay=eof_action=pass[with1];"
        "[with1]subtitles=filename=captions.ass:fontsdir=fonts,format=yuv420p[v]"
    )
    assert args[:args.index("-/filter_complex")] == [
        "-hide_banner", "-loglevel", "verbose", "-y", "-i", "/v.mov", "-i", "/c.wav",
        "-i", "/w/scenes/a.mov", "-i", "/w/scenes/b.mov"]
    assert args[args.index("-map"):args.index("-map") + 4] == ["-map", "[v]", "-map", "1:a"]


# ---- the video bitrate follows the reel length ------------------------------------------------

AUDIO_BPS = 192_000
BUDGET_BYTES = 0.95 * 300_000_000  # what the file of a reel may weigh: 5 % under the 300 MB limit


def estimated_bytes(duration_s: float) -> float:
    return (video_bitrate(duration_s) + AUDIO_BPS) * duration_s / 8


def bitrate_arg(args: list[str]) -> str:
    return args[args.index("-b:v") + 1]


def test_video_bitrate_is_12_mbps_for_a_short_reel():
    assert video_bitrate(60.0) == 12_000_000
    assert video_bitrate(1.0) == 12_000_000


@pytest.mark.parametrize("duration_s", [196.0, 223.033])
def test_video_bitrate_is_below_12_mbps_where_12_mbps_would_pass_the_size_limit(duration_s):
    assert video_bitrate(duration_s) < 12_000_000


@pytest.mark.parametrize("duration_s", [196.0, 223.033, 300.0, 500.0])
def test_video_bitrate_keeps_the_estimated_file_within_the_size_budget_and_uses_it(duration_s):
    assert estimated_bytes(duration_s) <= BUDGET_BYTES  # 300 s and 500 s land on the budget exactly
    assert estimated_bytes(duration_s) < 300_000_000  # the limit itself, 5 % further up
    assert BUDGET_BYTES - estimated_bytes(duration_s) <= duration_s * 1000 / 8  # only the rounding to 1000 is left


def test_video_bitrate_never_rises_with_the_length_and_is_a_whole_number_of_kbit():
    rates = [video_bitrate(seconds / 2) for seconds in range(1, 1088)]  # 0.5 s to 543.5 s
    assert rates == sorted(rates, reverse=True)
    assert all(rate % 1000 == 0 for rate in rates)
    assert rates[0] == 12_000_000 and rates[-1] >= 4_000_000


@pytest.mark.parametrize("duration_s", [545.0, 612.0, 3600.0])
def test_video_bitrate_refuses_a_reel_that_needs_less_than_4_mbps(duration_s):
    with pytest.raises(PipelineError) as e:
        video_bitrate(duration_s)
    assert f"{duration_s:.1f} s" in e.value.message and "300 MB" in e.value.message and "4 Mbps" in e.value.message


def test_args_carry_the_bitrate_of_the_reel_length(tmp_path):
    fs, out = tmp_path / "f.txt", tmp_path / "o.mp4"
    one_long = build_ffmpeg_args(Path("/v.mov"), Path("/c.wav"), flat([Segment(0, 223.033)]), BASE, out, fs)
    assert bitrate_arg(one_long) == f"{video_bitrate(223.033) // 1000}k"
    assert video_bitrate(223.033) < 12_000_000
    two_pieces = [Segment(10, 110), Segment(200, 323.033)]  # the lengths add up, not the time they span
    assert bitrate_arg(build_ffmpeg_args(Path("/v.mov"), Path("/c.wav"), flat(two_pieces), BASE, out, fs)) == bitrate_arg(one_long)
    assert bitrate_arg(build_ffmpeg_args(Path("/v.mov"), Path("/c.wav"), flat([Segment(0, 1)]), BASE, out, fs)) == "12000k"


def test_args_of_a_reel_too_long_for_4_mbps_raise_before_the_filter_script_is_written(tmp_path):
    fs = tmp_path / "f.txt"
    with pytest.raises(PipelineError, match="612.0 s"):
        build_ffmpeg_args(Path("/v.mov"), Path("/c.wav"), flat([Segment(0, 612.0)]), BASE, tmp_path / "o.mp4", fs)
    assert not fs.exists()


def test_ffmpeg_rate_spelling():
    """Every rate is written in whole kilobits, the cap too (ffmpeg's k is 1000)."""
    assert _ffmpeg_rate(12_000_000) == "12000k"
    assert _ffmpeg_rate(4_003_000) == "4003k"
    assert _ffmpeg_rate(192_000) == "192k"
    assert AUDIO_BITRATE == 192_000 and _ffmpeg_rate(AUDIO_BITRATE) == "192k"  # what -b:a is given


def test_no_segments_is_a_pipeline_error(tmp_path):
    fs = tmp_path / "f.txt"
    with pytest.raises(PipelineError) as e:
        build_ffmpeg_args(Path("/v.mov"), Path("/c.wav"), [], BASE, tmp_path / "o.mp4", fs)
    assert e.value.message == "nothing to render: the reel has no segments"
    assert not fs.exists()


def test_exact_minimum_bitrate_is_accepted(tmp_path):
    """The size budget (285 MB, 2.28e9 bit) over 543.8 s is 4_192_718 bit/s; less the 192_000 of the audio, 4_000_718,
    which is 4_000_000 when rounded down to a whole kbit/s: the rate that is just allowed. A second longer (544.8 s) it
    is 3_993_000."""
    assert video_bitrate(543.8) == 4_000_000
    fs, out = tmp_path / "f.txt", tmp_path / "o.mp4"
    assert bitrate_arg(build_ffmpeg_args(Path("/v.mov"), Path("/c.wav"), flat([Segment(0, 543.8)]), BASE, out, fs)) == "4000k"
    with pytest.raises(PipelineError, match="544.8 s") as e:
        video_bitrate(544.8)
    assert "4 Mbps" in e.value.message


# ---- check_output -----------------------------------------------------------------------------


def test_check_output_flags_wrong_spec(tmp_path):
    bad = make_clip(tmp_path, w=720, h=1280, seconds=2)  # 不是 1080×1920
    errs = check_output(bad, 2.0)
    assert any("1080x1920" in e for e in errs)


def test_check_output_accepts_a_clip_that_meets_the_spec(tmp_path):
    good = make_clip(tmp_path, seconds=2, audio="tone440")
    assert check_output(good, 2.0) == []


@pytest.mark.parametrize(
    ("options", "expected_s", "word"),
    [
        ({"audio": "mono"}, 2.0, "channels"),
        ({"audio": None}, 2.0, "audio"),
        ({"vfr": True}, 2.0, "frame rate"),
        ({"hdr": True}, 2.0, "yuv420p"),
        ({}, 3.0, "length"),
        ({"audio_delay": 0.5, "seconds": 3}, 3.0, "audio"),
    ],
    ids=["mono", "no-audio", "vfr", "hdr", "wrong-length", "audio-out-of-step"],
)
def test_check_output_names_each_problem(tmp_path, options, expected_s, word):
    clip = make_clip(tmp_path, **options)
    errs = check_output(clip, expected_s)
    assert errs and any(word in e for e in errs), errs


def test_check_output_allows_one_frame_of_length_difference(tmp_path):
    clip = make_clip(tmp_path, seconds=2)
    assert check_output(clip, 2.0 + 1 / FPS) == []
    assert check_output(clip, 2.0 + 3 / FPS) != []


def test_check_output_gives_ffprobe_60_seconds_and_names_the_timeout(tmp_path, monkeypatch):
    """The ffprobe of the output check has the limit probe() has. A hung ffprobe is faked (nothing waits 60 s)."""
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "ffprobe").write_text("")  # only has to be there: the run is faked
    monkeypatch.setenv("REELS_FFMPEG_DIR", str(tools))
    seen = []

    def hung(command, **kwargs):
        seen.append(kwargs)
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", hung)
    target = tmp_path / "final.tmp.mp4"
    with pytest.raises(PipelineError) as e:
        check_output(target, 1.0)
    assert e.value.message == f"ffprobe timed out after 60 s: {target}"
    assert [call["timeout"] for call in seen] == [60]


# ---- check_fontselect -------------------------------------------------------------------------


def test_check_fontselect():
    ok = "[Parsed_subtitles_0] fontselect: (Noto Sans TC, 700, 0) -> NotoSansTC-Bold, 0, NotoSansTC-Bold"
    assert check_fontselect(ok) == []
    assert check_fontselect(ok.replace("-> NotoSansTC-Bold", "-> /usr/share/fonts/x.ttf"))
    assert check_fontselect("no subtitles here")


def test_check_fontselect_every_line_must_pass():
    good = "[Parsed_subtitles_0 @ 0x1] fontselect: (Noto Sans TC, 700, 0) -> NotoSansTC-Bold, 0, NotoSansTC-Bold"
    other = "[Parsed_subtitles_0 @ 0x1] fontselect: (Noto Sans TC, 400, 0) -> Helvetica, 0, Helvetica"
    path = "[Parsed_subtitles_0 @ 0x1] fontselect: (Noto Sans TC, 400, 0) -> NotoSansTC-Regular, 0, /x/y.ttf"
    assert check_fontselect(f"noise\n{good}\n{good}\nmore noise") == []
    assert check_fontselect(f"{good}\n{other}")
    assert check_fontselect(f"{good}\n{path}")


# ---- the render step --------------------------------------------------------------------------


def test_render_requires_gate1(edited_project, capsys):
    assert main(["render", edited_project.name]) == 1
    assert json.loads(capsys.readouterr().out)["error"] == (
        "gate1 not approved for the current storyboard: run storyboard, then approve"
    )


def test_render_names_the_stale_step(approved_project, capsys):
    p = approved_project
    with open(p.work / "audio16k.wav", "ab") as audio:  # transcribe read it; the storyboard did not
        audio.write(b"\0\0")
    assert gate_ok(p, "gate1") is False
    assert main(["render", p.name]) == 1
    error = json.loads(capsys.readouterr().out)["error"]
    assert "gate1" in error and "transcribe is out of date" in error
    assert error == (
        "gate1 not approved for the current storyboard: transcribe is out of date, "
        "run transcribe, then storyboard and approve"
    )
    assert not (p.out / "final.mp4").exists()


def test_render_refuses_after_edit_changed(approved_project, capsys):
    e = json.loads(approved_project.edit_path.read_text())
    e["captions"]["mode"] = "word"
    approved_project.edit_path.write_text(json.dumps(e))
    assert main(["render", approved_project.name]) == 1
    assert "gate1" in json.loads(capsys.readouterr().out)["error"]


def test_render_refuses_after_the_caption_word_list_changed(edited_project, tmp_path, monkeypatch, capsys):
    """The user saw the caption lines of the old word list: the render would break them by the new one."""
    p = edited_project
    word_list = tmp_path / "caption_words.txt"
    shutil.copyfile(words.WORDS_FILE, word_list)
    monkeypatch.setattr(words, "WORDS_FILE", word_list)
    approve_project(p)
    assert gate_ok(p, "gate1") is True
    with open(word_list, "a", encoding="utf-8") as stream:
        stream.write("分鏡腳本\n")
    assert gate_ok(p, "gate1") is False
    assert main(["render", p.name]) == 1
    assert json.loads(capsys.readouterr().out)["error"] == (
        "gate1 not approved for the current storyboard: run storyboard, then approve"
    )
    assert main(["status", p.name]) == 0
    assert json.loads(capsys.readouterr().out)["next"] == "storyboard"
    assert not (p.out / "final.mp4").exists()


def test_render_refuses_after_trim_json_changed_with_edit_json_unchanged(approved_project, capsys):
    """The user saw the storyboard of the old trim.json: its candidate ids may name other stretches now."""
    p = approved_project
    trims = json.loads((p.work / "trim.json").read_text())
    trims["trims"].reverse()  # same ids, other stretches behind them
    (p.work / "trim.json").write_text(json.dumps(trims))
    assert next_step(p) == "edit"  # edit.json was checked against the old trim.json: its ids are to be re-checked
    assert main(["render", p.name]) == 1
    assert "gate1" in json.loads(capsys.readouterr().out)["error"]
    assert not (p.out / "final.mp4").exists()


def test_render_checks_the_edit_before_anything_else(approved_project, capsys):
    """Gate 1 holds for this file, but the file is not valid: exit 2, nothing rendered."""
    e = json.loads(approved_project.edit_path.read_text())
    e["captions"]["mode"] = "bogus"
    approved_project.edit_path.write_text(json.dumps(e))
    force_gate1_open(approved_project)
    assert main(["render", approved_project.name]) == 2
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and any("captions.mode" in error for error in out["errors"])
    assert not (approved_project.work / "render").exists()


def test_render_retries_once(approved_project):
    calls = []

    def flaky(args, log, cwd=None):
        calls.append(1)
        if len(calls) == 1:
            raise PipelineError("boom", log=str(log))
        run_ffmpeg(args, log, cwd)

    assert render(approved_project, runner=flaky)["attempts"] == 2


def test_render_fails_after_two_attempts(approved_project):
    def broken(args, log, cwd=None):
        raise PipelineError("boom", log=str(log))

    with pytest.raises(PipelineError) as e:
        render(approved_project, runner=broken)
    assert e.value.extra["log"].endswith("render.log")
    assert "boom" in e.value.message


def test_a_failed_render_leaves_no_final_file_and_no_step_record(approved_project):
    p = approved_project
    (p.out / "final.mp4").write_bytes(b"the render of an earlier edit")
    calls = []

    def broken(args, log, cwd=None):
        calls.append(1)
        (p.out / "final.tmp.mp4").write_bytes(b"half a file")
        raise PipelineError("boom", log=str(log))

    with pytest.raises(PipelineError):
        render(p, runner=broken)
    assert len(calls) == 2
    assert sorted(path.name for path in p.out.iterdir()) == []  # no final.mp4 of an earlier edit, no half file
    assert "render" not in load_state(p)["steps"]


def test_render_calls_ffmpeg_in_the_render_folder_with_the_specified_files(approved_project):
    p, calls = approved_project, []
    probe = json.loads((p.work / "probe.json").read_text())
    render(p, runner=recording(calls))
    call, preview_call = calls  # the final, then the preview made from it
    work = p.work / "render"
    assert preview_call["cwd"] == work and preview_call["log"] == work / "preview.log"
    assert preview_call["args"][preview_call["args"].index("-i") + 1] == str(p.out / "final.mp4")
    assert call["cwd"] == work and call["log"] == work / "render.log"
    args = call["args"]
    assert args[args.index("-i") + 1] == probe["video_source"]  # the video as probe.json spells it
    assert args[args.index("-/filter_complex") + 1] == str(work / "filter.txt")
    assert args[-1] == str(p.out / "final.tmp.mp4")
    assert (work / "cut.wav").is_file() and (work / "captions.ass").is_file()
    sound = args[[i for i, a in enumerate(args) if a == "-i"][1] + 1]
    assert sound == str(work / "voice.wav") and (work / "voice.wav").is_file()  # the treated voice, not cut.wav
    assert sorted(path.name for path in (work / "fonts").iterdir()) == ["NotoSansTC-Bold.ttf", "NotoSansTC-Regular.ttf"]


def test_render_brings_the_voice_to_minus_14_lufs_with_true_peaks_under_minus_1_dbtp(approved_project):
    """The fixture speech is at -24.9 LUFS; the final, after the voice treatment and the AAC encoder, is at the
    target, within 1 LU, and its true peak is at or under -1 dBTP."""
    out = Path(render(approved_project)["out"])
    integrated, true_peak = loudness(out)
    assert integrated == pytest.approx(-14.0, abs=1.0)
    assert true_peak <= -1.0


def test_a_render_made_before_the_voice_treatment_is_made_again(approved_project):
    """A render record of no version (made before the voice treatment) is not done: the next step is render, and
    render makes the final again instead of skipping it."""
    p = approved_project
    render(p)
    state = load_state(p)
    del state["steps"]["render"]["version"]
    save_state(p, state)
    assert not step_done(p, "render") and next_step(p) == "render"
    assert render(p)["skipped"] is False
    assert step_done(p, "render") and next_step(p) == "done"


def test_render_fixture_meets_spec(approved_project):
    p = approved_project
    assert next_step(p) == "render"
    r = render(p)
    assert next_step(p) == "done"
    out = Path(r["out"])
    assert out == p.out / "final.mp4" and out.is_file()
    assert not (p.out / "final.tmp.mp4").exists()
    assert check_output(out, reel_seconds(p)) == []
    assert check_fontselect((p.work / "render" / "render.log").read_text()) == []
    assert set(r) == {"out", "duration_s", "size_mb", "attempts", "video_bitrate", "skipped",
                      "preview", "preview_mb", "warnings"}
    assert r["video_bitrate"] == video_bitrate(reel_seconds(p)) == 12_000_000
    assert r["attempts"] == 1 and r["skipped"] is False
    assert r["duration_s"] == pytest.approx(reel_seconds(p), abs=0.001)
    assert r["size_mb"] == pytest.approx(out.stat().st_size / 1e6, abs=0.01)
    assert step_done(p, "render")
    preview = p.out / "preview.mp4"  # made by the render above (the encode of the preview is a real one here)
    assert r["preview"] == str(preview) and r["preview_mb"] == pytest.approx(preview.stat().st_size / 1e6, abs=0.01)
    assert r["warnings"] == [] and not (p.out / "preview.tmp.mp4").exists() and step_done(p, "preview")
    streams = {s["codec_type"]: s for s in ffprobe_json(preview, ["-show_streams"], 60)["streams"]}
    assert (streams["video"]["width"], streams["video"]["height"]) == (540, 960)
    assert (streams["video"]["codec_name"], streams["video"]["pix_fmt"], streams["video"]["r_frame_rate"]) == (
        "h264", "yuv420p", "30/1")
    assert (streams["audio"]["codec_name"], streams["audio"]["channels"]) == ("aac", 2)
    assert abs(float(streams["video"]["duration"]) - reel_seconds(p)) < LENGTH_TOLERANCE_S


def test_render_draws_captions_at_the_cell_scaled_size(approved_project):
    render(approved_project)
    ass = (approved_project.work / "render" / "captions.ass").read_text(encoding="utf-8")
    assert "Style: Caption,Noto Sans TC,81," in ass  # size_px 56 times the cell height of the bold font
    assert ass.count("Dialogue:") >= 4


def test_render_frame_accurate(approved_project):
    out = Path(render(approved_project)["out"])
    assert_frame_accurate(out, approved_project)


def test_render_runs_again_only_when_something_changed(approved_project):
    p = approved_project
    first = render(p)
    calls = []
    again = render(p, runner=recording(calls))
    assert again["skipped"] is True and calls == [] and again["out"] == first["out"]
    assert (again["duration_s"], again["size_mb"], again["attempts"]) == (first["duration_s"], first["size_mb"], 0)
    forced = render(p, force=True, runner=recording(calls))
    assert forced["skipped"] is False and len(calls) == 2  # the final, then the preview


def test_render_draws_every_caption_at_the_fixed_top_of_the_style(approved_project):
    render(approved_project)
    ass = (approved_project.work / "render" / "captions.ass").read_text(encoding="utf-8")
    dialogues = [line for line in ass.splitlines() if line.startswith("Dialogue:")]
    # bottom_px 1248 less line_height_px 90
    assert dialogues and all("{\\an8\\pos(540,1158)}" in line for line in dialogues)


def test_render_reads_faces_json(approved_project):
    p = approved_project
    (p.work / "faces.json").write_text("not json", encoding="utf-8")
    with pytest.raises(PipelineError, match="faces.json"):
        render(p)
    assert not (p.out / "final.mp4").exists()


def test_render_lists_faces_json_as_an_input(approved_project):
    p = approved_project
    render(p)
    assert set(load_state(p)["steps"]["render"]["inputs"]) == {
        "edit", "transcript", "trim", "style", "audio", "video", "probe", "faces", "words", "dictionary"
    }


def test_changing_faces_json_makes_the_render_run_again(approved_project):
    p = approved_project
    render(p)
    faces = json.loads((p.work / "faces.json").read_text(encoding="utf-8"))
    faces["boxes"] = [{"t": 2.0, "x0": 0.3, "y0": 0.6, "x1": 0.7, "y1": 0.97, "score": 0.9}]
    (p.work / "faces.json").write_text(json.dumps(faces), encoding="utf-8")
    calls = []
    again = render(p, runner=recording(calls))
    assert again["skipped"] is False and calls


def face_at(cx: float, cy: float) -> dict:
    return {"t": 1.0, "x0": cx - 0.1, "y0": cy - 0.1, "x1": cx + 0.1, "y1": cy + 0.1, "score": 0.9}


def test_render_frames_every_shot_of_the_edit_about_the_face(approved_project, monkeypatch):
    """The graph holds the shots of punch.shots for the edit and faces.json, in pixels of the base window of the
    picture the render reads."""
    p = approved_project
    (p.work / "faces.json").write_text(json.dumps({"boxes": [face_at(0.2, 0.3)]}), encoding="utf-8")
    edit = json.loads(p.edit_path.read_text(encoding="utf-8"))
    edit["punch_in"] = {edit["sentences"][-1]["id"]: 1.2}  # nothing is punched in without it
    p.edit_path.write_text(json.dumps(edit, ensure_ascii=False), encoding="utf-8")
    force_gate1_open(p)
    monkeypatch.setattr("pipeline.render.check_output", lambda path, seconds: [])
    render(p, runner=stand_in_encoder([]))
    edit, transcript, trims = (json.loads((p.work / name).read_text(encoding="utf-8"))
                               for name in ("edit.json", "transcript.json", "trim.json"))
    shots = punch.shots(edit, transcript, trims, {"boxes": [face_at(0.2, 0.3)]}, {"display_w": 1080, "display_h": 1920})
    assert any(shot.scale > 1 for shot in shots)  # else the test would show nothing
    z, x, y = zoom_exprs(shots, BASE)
    assert x != "0" and y != "0"
    graph = (p.work / "render" / "filter.txt").read_text(encoding="utf-8")
    assert f"crop=1080:1920:0:0,zoompan=z='{z}':x='{x}':y='{y}':d=1:" in graph


def test_render_frames_the_picture_it_reads_not_the_size_probe_json_gives(approved_project, monkeypatch):
    """probe.json gives the size of the source; for an HDR source the render reads prep's smaller SDR copy. The base
    window is of the picture the render reads: here probe.json says 2160x3840 and the video is 1080x1920."""
    p = approved_project
    path = p.work / "probe.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert (doc["display_w"], doc["display_h"]) == (1080, 1920)
    path.write_text(json.dumps({**doc, "display_w": 2160, "display_h": 3840}), encoding="utf-8")
    force_gate1_open(p)
    monkeypatch.setattr("pipeline.render.check_output", lambda path, seconds: [])
    render(p, runner=stand_in_encoder([]))
    assert ",crop=1080:1920:0:0,zoompan=" in (p.work / "render" / "filter.txt").read_text(encoding="utf-8")


FONT_LINE = "[Parsed_subtitles_0] fontselect: (Noto Sans TC, 700, 0) -> NotoSansTC-Bold, 0, NotoSansTC-Bold\n"


def stand_in_encoder(calls: list):
    """A runner that does not run ffmpeg: it notes its arguments in `calls`, writes some bytes as the output file and
    the font line libass logs when it takes the given font. Tests that use it also replace check_output."""

    def run(args, log, cwd=None):
        calls.append(list(args))
        Path(args[-1]).write_bytes(b"not a video")
        log.write_text(FONT_LINE, encoding="utf-8")

    return run


def test_lowered_rate_is_reported_as_encoded(approved_project, monkeypatch):
    """A reel of 223.033 s (the length is faked) is encoded at less than 12 Mbps; the result says the rate that went
    to ffmpeg as -b:v, in bit/s."""
    monkeypatch.setattr("pipeline.render.total_duration", lambda segments: 223.033)
    monkeypatch.setattr("pipeline.render.check_output", lambda path, seconds: [])
    calls = []
    result = render(approved_project, runner=stand_in_encoder(calls))
    args, preview_args = calls  # the final, then the preview
    assert Path(preview_args[-1]).name == "preview.tmp.mp4"
    assert bitrate_arg(args).endswith("k")
    encoded = int(bitrate_arg(args).removesuffix("k")) * 1000
    assert encoded < 12_000_000
    assert result["video_bitrate"] == encoded
    assert result["skipped"] is False and result["attempts"] == 1


def test_skipped_render_has_no_bitrate(approved_project):
    """A render that was skipped encoded nothing: it has no bitrate to report."""
    p = approved_project
    assert "video_bitrate" in render(p)
    calls = []
    again = render(p, runner=recording(calls))
    assert again["skipped"] is True and calls == []
    assert "video_bitrate" not in again
    assert set(again) == {"out", "duration_s", "size_mb", "attempts", "skipped", "preview", "preview_mb", "warnings"}


def test_render_command_prints_one_json_line(approved_project, capsys):
    assert main(["render", approved_project.name]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True and out["out"] == str(approved_project.out / "final.mp4")
    assert main(["render", approved_project.name, "--force"]) == 0
    assert json.loads(capsys.readouterr().out)["skipped"] is False


def test_render_refuses_a_character_the_font_cannot_draw(approved_project):
    p = approved_project
    transcript = json.loads((p.work / "transcript.json").read_text(encoding="utf-8"))
    sentence = next(s for s in transcript["sentences"] if s["spoken"])
    assert sentence["units"][0]["span"] == [0, 1]
    sentence["text"] = "\U0001f600" + sentence["text"][1:]  # an emoji: Noto Sans TC has no glyph for it
    sentence["units"][0]["text"] = "\U0001f600"
    (p.work / "transcript.json").write_text(json.dumps(transcript, ensure_ascii=False), encoding="utf-8")
    force_gate1_open(p)
    calls = []
    with pytest.raises(PipelineError) as e:
        render(p, runner=recording(calls))
    assert "\U0001f600" in e.value.message and calls == []


def test_render_refuses_when_the_font_log_shows_a_system_font(approved_project):
    p = approved_project

    def system_font(args, log, cwd=None):
        run_ffmpeg(args, log, cwd)
        log.write_text(log.read_text(errors="replace").replace("-> NotoSansTC-", "-> /Library/Fonts/NotoSansTC-"))

    with pytest.raises(PipelineError) as e:
        render(p, runner=system_font)
    assert "font" in e.value.message and e.value.extra["log"].endswith("render.log")
    assert not (p.out / "final.mp4").exists() and not (p.out / "final.tmp.mp4").exists()
    assert "render" not in load_state(p)["steps"]


def test_render_refuses_an_output_that_breaks_the_spec(approved_project, monkeypatch):
    import pipeline.render as render_module

    monkeypatch.setattr(render_module, "check_output", lambda path, seconds: ["video is 720x1280, expected 1080x1920"])
    with pytest.raises(PipelineError) as e:
        render(approved_project)
    assert "1080x1920" in str(e.value.extra["problems"])
    assert not (approved_project.out / "final.mp4").exists()
    assert not (approved_project.out / "final.tmp.mp4").exists()


@pytest.mark.parametrize("failure", ["check_output", "log", "replace"])
def test_temp_output_removed_when_check_raises(approved_project, monkeypatch, failure):
    """Whatever raises after ffmpeg wrote out/final.tmp.mp4 (the check of the file, the read of the log, the rename),
    the temporary file does not stay behind."""
    import pipeline.render as render_module

    p = approved_project
    real_replace = render_module.os.replace

    def runner(args, log, cwd=None):
        Path(args[-1]).write_bytes(b"not a video")
        if failure != "log":  # without a log the read of it raises
            log.write_text(FONT_LINE, encoding="utf-8")

    def boom(*args):
        raise OSError("disk trouble")

    def replace(src, dst):
        if Path(dst).name == "final.mp4":
            boom()
        real_replace(src, dst)

    monkeypatch.setattr(render_module, "check_output", boom if failure == "check_output" else lambda path, s: [])
    monkeypatch.setattr(render_module.os, "replace", replace)
    with pytest.raises(OSError):
        render(p, runner=runner)
    assert not (p.out / "final.tmp.mp4").exists() and not (p.out / "final.mp4").exists()
    assert "render" not in load_state(p)["steps"]


def test_render_in_path_with_space_and_quote(tmp_path, monkeypatch):
    studio = tmp_path / "my st'udio"
    (studio / "inbox").mkdir(parents=True)
    (studio / "projects").mkdir()
    monkeypatch.setenv("REELS_STUDIO", str(studio))
    p = approved_project_in(studio)  # helper：同 approved_project，但建在指定 studio
    assert check_output(Path(render(p)["out"]), sum(s.end - s.start for s in segments_of(p))) == []


# ---- an earlier render's final.mp4 -------------------------------------------------------------


def plant_old_final(p) -> bytes:
    """A stand-in for the final.mp4 of an earlier edit; returns its content."""
    content = b"the render of an earlier edit"
    (p.out / "final.mp4").write_bytes(content)
    return content


@pytest.mark.parametrize(
    ("owner", "name"),
    [("pipeline.render", "cut_audio"), ("pipeline.render", "treat_voice"), ("pipeline.render", "write_ass"),
     ("shutil", "copyfile")],
    ids=["cut_audio", "treat_voice", "write_ass", "font-copy"],
)
def test_a_render_that_fails_early_leaves_no_final_file_of_an_earlier_edit(approved_project, monkeypatch, owner, name):
    """The old file goes before anything else of the render is done, so no early failure keeps it."""
    import importlib

    p = approved_project
    plant_old_final(p)

    def boom(*args, **kwargs):
        raise PipelineError("boom")

    monkeypatch.setattr(importlib.import_module(owner), name, boom)
    with pytest.raises(PipelineError, match="boom"):
        render(p)
    assert not (p.out / "final.mp4").exists()


def test_a_refused_render_keeps_the_final_file_when_gate1_is_not_approved(edited_project, capsys):
    content = plant_old_final(edited_project)
    assert main(["render", edited_project.name]) == 1
    assert (edited_project.out / "final.mp4").read_bytes() == content


def test_a_refused_render_keeps_the_final_file_when_the_edit_is_invalid(approved_project, capsys):
    p = approved_project
    content = plant_old_final(p)
    e = json.loads(p.edit_path.read_text())
    e["captions"]["mode"] = "bogus"
    p.edit_path.write_text(json.dumps(e))
    force_gate1_open(p)
    assert main(["render", p.name]) == 2
    assert (p.out / "final.mp4").read_bytes() == content


def test_missing_font_is_a_json_error(approved_project, tmp_path, monkeypatch, capsys):
    """The style folder that the render reads has the Bold font but not the Regular one (a copy under tmp_path; the
    real fonts are not touched). The render refuses before it touches out/ or work/render/."""
    p = approved_project
    content = plant_old_final(p)
    folder = tmp_path / "other_styles" / "default"
    (folder / "fonts").mkdir(parents=True)
    shutil.copyfile(styles_root() / "default" / "style.json", folder / "style.json")
    shutil.copyfile(static_font("bold"), folder / "fonts" / "NotoSansTC-Bold.ttf")
    monkeypatch.setattr("pipeline.style.styles_root", lambda: tmp_path / "other_styles")
    assert main(["render", p.name]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False
    assert "NotoSansTC-Regular.ttf" in out["error"] and "pipeline setup" in out["error"]
    assert [path.name for path in p.out.iterdir()] == ["final.mp4"]
    assert (p.out / "final.mp4").read_bytes() == content
    assert not (p.work / "render").exists()
    assert "render" not in load_state(p)["steps"]


def test_a_refused_render_keeps_the_final_file_when_a_character_has_no_glyph(approved_project):
    p = approved_project
    content = plant_old_final(p)
    transcript = json.loads((p.work / "transcript.json").read_text(encoding="utf-8"))
    sentence = next(s for s in transcript["sentences"] if s["spoken"])
    sentence["text"] = "\U0001f600" + sentence["text"][1:]
    sentence["units"][0]["text"] = "\U0001f600"
    (p.work / "transcript.json").write_text(json.dumps(transcript, ensure_ascii=False), encoding="utf-8")
    force_gate1_open(p)
    with pytest.raises(PipelineError, match="glyph"):
        render(p)
    assert (p.out / "final.mp4").read_bytes() == content


def test_render_passes_the_bitrate_of_the_reel_length_to_ffmpeg(approved_project, monkeypatch):
    """A reel of 223.033 s (the length is faked: the segments of the fixture are short) gets the lower bitrate."""
    monkeypatch.setattr("pipeline.render.total_duration", lambda segments: 223.033)
    calls = []

    def stop(args, log, cwd=None):
        calls.append(list(args))
        raise PipelineError("stop")

    with pytest.raises(PipelineError, match="stop"):
        render(approved_project, runner=stop)
    assert [bitrate_arg(args) for args in calls] == [f"{video_bitrate(223.033) // 1000}k"] * 2


@pytest.mark.parametrize("force", [False, True])
def test_a_render_too_long_for_4_mbps_refuses_before_anything_is_written(approved_project, monkeypatch, force):
    p = approved_project
    render(p)  # a render that ran: its final.mp4, its work/render/ files and its step record
    content = (p.out / "final.mp4").read_bytes()
    preview_content = (p.out / "preview.mp4").read_bytes()
    record = load_state(p)["steps"]["render"]
    files = {path: path.stat().st_mtime_ns for path in (p.work / "render").rglob("*") if path.is_file()}
    monkeypatch.setattr("pipeline.render.total_duration", lambda segments: 612.0)
    calls = []

    def runner(args, log, cwd=None):
        calls.append(list(args))

    with pytest.raises(PipelineError, match="612.0 s") as e:
        render(p, force=force, runner=runner)
    assert "4 Mbps" in e.value.message
    assert calls == []
    assert sorted(path.name for path in p.out.iterdir()) == ["final.mp4", "preview.mp4"]
    assert (p.out / "final.mp4").read_bytes() == content
    assert (p.out / "preview.mp4").read_bytes() == preview_content
    assert {path: path.stat().st_mtime_ns for path in (p.work / "render").rglob("*") if path.is_file()} == files
    assert load_state(p)["steps"]["render"] == record  # the record of the render that ran is still there


# ---- a video that starts late, and an HDR one -------------------------------------------------


def test_render_of_a_video_that_starts_late_is_frame_accurate_and_in_sync(tmp_path):
    p = meijia_approved_project(tmp_path, video_delay=0.5)
    assert probe(Path(load_state(p)["source"]["video"])).video_start_s == pytest.approx(0.5)
    out = Path(render(p)["out"])
    assert_frame_accurate(out, p)
    assert check_output(out, reel_seconds(p)) == []
    lag = audio_lag_s(decoded_audio(out, tmp_path / "out.wav"), expected_reel_audio(p))
    assert abs(lag) <= 0.03, lag


BEEP_HZ, BEEP_AMPLITUDE, BEEP_S = 6000, 0.3, 0.1  # the speech of the fixture has less than 0.04 at 6 kHz anywhere
WHITE_FRAME = 360  # a frame number of the picture: 12.0 s after its first frame, inside a stretch the reel keeps


def late_picture_clip(dest: Path, wav: Path, seconds: float, delay: float) -> Path:
    """An MP4 with `wav` as its sound from second 0 and a picture that starts `delay` seconds later: `seconds` of black
    1080x1920 at 30 fps with one white frame, frame number WHITE_FRAME of the picture."""
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / "late-picture.mp4"
    picture = (
        f"color=c=black:size=16x16:rate={FPS}:duration={seconds},"
        f"geq=lum='if(eq(N,{WHITE_FRAME}),235,16)':cb=128:cr=128,scale=1080:1920:flags=neighbor"
    )
    run_ffmpeg(
        ["-y", "-hide_banner", "-loglevel", "error", "-itsoffset", str(delay), "-f", "lavfi", "-i", picture,
         "-i", str(wav), "-map", "0:v", "-map", "1:a", "-vf", "format=yuv420p",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", "-t", str(seconds + delay), str(out)],
        dest / f"{out.name}.log",
    )
    return out


def beep_onset_s(samples: np.ndarray, rate: int = 48000) -> float:
    """When the beep starts, in seconds: the first moment the BEEP_HZ content of `samples`, taken over 10 ms, is half
    of BEEP_AMPLITUDE. A window centred on the start of a beep is half full there."""
    window = rate // 100
    mixed = samples * np.exp(-2j * np.pi * BEEP_HZ * np.arange(len(samples)) / rate)
    total = np.cumsum(mixed)
    level = np.abs(total[window:] - total[:-window]) / window * 2  # level[i]: the 10 ms that start at sample i
    first = int(np.argmax(level > BEEP_AMPLITUDE / 2))
    assert level[first] > BEEP_AMPLITUDE / 2, "no beep in the audio"
    return (first + window / 2) / rate


def test_beep_onset_helper_finds_the_start_of_a_beep_in_speech():
    speech, rate = sf.read(str(MEIJIA / "fixture.wav"), dtype="float32")
    start = round(12.0 * rate)
    beep = BEEP_AMPLITUDE * np.sin(2 * np.pi * BEEP_HZ * np.arange(round(BEEP_S * rate)) / rate)
    with pytest.raises(AssertionError, match="no beep"):
        beep_onset_s(speech)  # the speech alone has none
    speech[start:start + len(beep)] += beep.astype("float32")
    assert beep_onset_s(speech) == pytest.approx(12.0, abs=0.002)


def test_render_video_starting_after_audio(tmp_path):
    """A source whose sound starts 0.5 s before its first frame, with a white frame and a beep at the same moment.
    prep drops the first 0.5 s of sound, and the render keeps the white frame and the beep together: they are less than
    a frame (1/30 s) apart in final.mp4. The recognition result is the recorded one of the Meijia fixture, which
    speaks from the first frame."""
    require_assets()
    speech, rate = sf.read(str(MEIJIA / "fixture.wav"), dtype="float32")
    first = round(WHITE_FRAME / FPS * rate)  # the beep starts with the white frame
    speech[first:first + round(BEEP_S * rate)] += BEEP_AMPLITUDE * np.sin(
        2 * np.pi * BEEP_HZ * np.arange(round(BEEP_S * rate)) / rate
    ).astype("float32")
    assert np.abs(speech).max() < 1.0  # nothing clips
    delay = 0.5
    wav = tmp_path / "sound.wav"
    sf.write(str(wav), np.concatenate([np.zeros(round(delay * rate), dtype="float32"), speech]), rate, subtype="PCM_16")
    clip = late_picture_clip(tmp_path / "clip", wav, seconds=round(len(speech) / rate, 2), delay=delay)
    source = probe(clip)
    assert source.video_start_s == pytest.approx(delay) and source.audio_start_s == pytest.approx(0.0)

    p = project_with(clip, script=MEIJIA / "script.md")
    prep(p)
    assert main(["transcribe", p.name, "--asr-json", str(MEIJIA / "asr-qwen3-1.7b.json")]) == 0
    approve_project(write_default_edit(trim_project(p)))
    segments = segments_of(p)
    moment = WHITE_FRAME / FPS
    kept = next(i for i, s in enumerate(segments) if s.start <= moment and moment + BEEP_S <= s.end)
    before = sum(round((s.end - s.start) * FPS) for s in segments[:kept])  # frames of the segments ahead of it
    reel_frame = before + WHITE_FRAME - round(segments[kept].start * FPS)

    out = Path(render(p)["out"])
    luma = corner_luma(out)
    white = int(np.argmax(luma))
    assert luma[white] > 200 and np.sort(luma)[-2] < 100  # one white frame in a black picture
    assert white == reel_frame
    onset = beep_onset_s(decoded_audio(out, tmp_path / "out.wav"))
    assert abs(onset - white / FPS) <= 1 / FPS, (onset, white / FPS)


def test_render_of_an_hdr_video_that_starts_late_is_frame_accurate_and_in_sync(tmp_path):
    p = meijia_approved_project(tmp_path, hdr=True, video_delay=0.5)
    source = probe(Path(load_state(p)["source"]["video"]))
    assert source.is_hdr and source.video_start_s == pytest.approx(0.5)
    calls = []
    out = Path(render(p, runner=recording(calls))["out"])
    work_video = p.work / "video.mp4"
    assert work_video.is_file() and str(work_video) in calls[0]["args"]  # prep's SDR copy, not the HDR file
    assert_frame_accurate(out, p, reference=work_video)
    assert check_output(out, reel_seconds(p)) == []
    lag = audio_lag_s(decoded_audio(out, tmp_path / "out.wav"), expected_reel_audio(p))
    assert abs(lag) <= 0.03, lag


# ---- other kinds of source, through the graph alone -------------------------------------------


def render_segments(tmp_path: Path, clip: Path, shots: list[Shot], base: tuple[int, int, int, int] = BASE, *,
                    events: list = (), scenes: list[tuple[int, Path]] = ()) -> Path:
    """The `shots` of `clip`, whose base window is `base`, through build_ffmpeg_args and ffmpeg (the filter graph read
    from its file), with the caption `events` (none by default) at the caption top of the default style, and the
    clips of `scenes` (reel start frame, clip) overlaid."""
    work = tmp_path / "work"
    (work / "fonts").mkdir(parents=True)
    style = load_style()
    for weight in ("regular", "bold"):
        font = static_font(weight)
        shutil.copyfile(font, work / "fonts" / font.name)
    write_ass(list(events), style, caption_top_px(style), "line", work / "captions.ass", font=static_font("bold"))
    full, wav = tmp_path / "full.wav", tmp_path / "cut.wav"
    run_ffmpeg(["-y", "-i", str(clip), "-vn", "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", str(full)],
               tmp_path / "wav.log")
    cut_audio(full, [Segment(s.start, s.end) for s in shots], wav)
    out = tmp_path / "out.mp4"
    run_ffmpeg(build_ffmpeg_args(clip, wav, shots, base, out, work / "filter.txt", scenes=scenes),
               work / "render.log", cwd=work)
    return out


def raw_gray_frame(video: Path, n: int) -> np.ndarray:
    done = subprocess.run(
        [ffmpeg_bin(), "-nostdin", "-v", "error", "-i", str(video), "-vf", f"select=eq(n\\,{n})", "-frames:v", "1",
         "-fps_mode", "passthrough", "-pix_fmt", "gray", "-f", "rawvideo", "-"],
        capture_output=True, check=True,
    )
    return np.frombuffer(done.stdout, dtype=np.uint8).astype(float)


def test_graph_turns_a_rotated_source_upright_before_it_scales(tmp_path):
    """A 1920x1080 file marked rotated by 90 degrees shows as 1080x1920. ffmpeg turns it upright on its own,
    and the scale and the crop that follow count on that."""
    clip = make_clip(tmp_path / "src", w=1920, h=1080, rotate=90, seconds=2)
    out = render_segments(tmp_path, clip, flat([Segment(0, 1)]))
    assert check_output(out, 1.0) == []
    upright, rendered = raw_gray_frame(clip, 10), raw_gray_frame(out, 10)  # clip is decoded upright, 1080x1920
    assert upright.size == rendered.size == 1080 * 1920
    assert np.abs(upright - rendered).mean() < 2  # only encoder noise apart


def pattern_clip(dest: Path, w: int, h: int, seconds: float) -> tuple[Path, np.ndarray]:
    """A still picture `w` x `h` whose gray changes with x and with y and repeats nowhere (a ramp and a wave of odd
    periods), with a tone: which part of it a frame shows can be told by comparing. Returns the clip and the
    picture."""
    ys, xs = np.mgrid[0:h, 0:w]
    wave = np.sin(2 * np.pi * xs / 197) * np.cos(2 * np.pi * ys / 311)
    picture = np.clip(70 + 0.04 * xs + 0.03 * ys + 60 * wave, 0, 255).astype(np.uint8)
    dest.mkdir(parents=True)
    still = dest / "picture.gray"
    still.write_bytes(picture.tobytes())
    clip = dest / "pattern.mp4"
    run_ffmpeg(["-y", "-stream_loop", "-1", "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{w}x{h}", "-framerate", "30",
                "-i", str(still), "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", str(seconds),
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "8", "-pix_fmt", "yuv420p", "-c:a", "aac", str(clip)],
               dest / "pattern.log")
    return clip, picture.astype(float)


def part_of(picture: np.ndarray, base: tuple[int, int, int, int], left: int, top: int, scale: float) -> np.ndarray:
    """What a 1080x1920 frame shows of `picture` when zoompan takes, from the base window `base`, the part `scale`
    times smaller (its size truncated, as zoompan does) whose top-left is (`left`, `top`): the nearest pixel to the
    centre of each output pixel."""
    bx, by, bw, bh = base
    w, h = int(bw / scale), int(bh / scale)
    xs = bx + left + np.clip(np.round((np.arange(1080) + 0.5) * w / 1080 - 0.5), 0, w - 1).astype(int)
    ys = by + top + np.clip(np.round((np.arange(1920) + 0.5) * h / 1920 - 0.5), 0, h - 1).astype(int)
    return picture[np.ix_(ys, xs)]


@pytest.mark.parametrize("w, h", [(1080, 1920), (1920, 1080)])
def test_a_punched_in_shot_shows_the_part_about_its_anchor_and_the_others_the_base_window(tmp_path, w, h):
    """Frames 0-29 of the reel are a shot at the base framing, frames 30-74 one punched in to 1.2 about (0.3, 0.4).
    Each frame shows the part of the picture it should and not the other; the frame count is the one select keeps.
    The 1920x1080 picture is cropped to its centre 9:16 first, and that window is enlarged. Measured: a frame is
    about 0.6 gray levels from its part on average, about 2.1 from a part two pixels off and over 30 from the
    other."""
    clip, picture = pattern_clip(tmp_path / "src", w, h, 4)
    base = base_window(w, h)
    shots = [Shot(0.0, 1.0, "s01", 1.0, 0.3, 0.4), Shot(2.0, 3.5, "s02", 1.2, 0.3, 0.4)]
    out = render_segments(tmp_path, clip, shots, base)
    assert check_output(out, 2.5) == []
    assert len(corner_luma(out)) == 75
    left, top, _, _ = window((0.3, 0.4), 1.2)
    whole = part_of(picture, base, 0, 0, 1.0)
    punched = part_of(picture, base, round(left * base[2] / 2) * 2, round(top * base[3] / 2) * 2, 1.2)
    for k, wanted, other in ((0, whole, punched), (29, whole, punched), (30, punched, whole), (74, punched, whole)):
        frame = raw_gray_frame(out, k).reshape(1920, 1080)
        assert np.abs(frame - wanted).mean() < 1.5, k
        assert np.abs(frame - other).mean() > 15, k


def rgb_frames(video: Path, frames: list[int]) -> dict[int, np.ndarray]:
    """Frames of `video` by number, as RGB (0 to 255, 1920 rows by 1080 columns) decoded as BT.709 limited range."""
    picked = "+".join(f"eq(n\\,{k})" for k in frames)
    done = subprocess.run(
        [ffmpeg_bin(), "-nostdin", "-v", "error", "-i", str(video), "-vf",
         f"select='{picked}',scale=in_color_matrix=bt709:in_range=limited,format=rgb24", "-fps_mode", "passthrough",
         "-f", "rawvideo", "-"],
        capture_output=True, check=True, timeout=120)
    planes = np.frombuffer(done.stdout, dtype=np.uint8).reshape(len(frames), 1920, 1080, 3).astype(int)
    return dict(zip(sorted(frames), planes))


def tagged_bt709(clip: Path, dest: Path) -> Path:
    """A copy of the H.264 `clip` marked BT.709 limited range, as phone footage is; the picture is not touched."""
    run_ffmpeg(["-y", "-i", str(clip), "-c", "copy", "-bsf:v", "h264_metadata=colour_primaries=1"
                ":transfer_characteristics=1:matrix_coefficients=1:video_full_range_flag=0", str(dest)],
               dest.with_suffix(".log"))
    return dest


def test_a_card_shows_over_the_picture_during_its_scene_only_and_the_captions_stay(tmp_path):
    """A real keyword-pop clip of 40 frames (the first and the last of them empty) from reel frame 30, over a
    4-second picture marked BT.709 with a caption all through it, against the same render without the scene.
    Frames 0-30 and 69-119 are as they were in the zone; frames 31-68 differ there; outside the zone and the caption
    band nothing changes beyond the noise of the encoder; the caption is drawn over the card frames as over the
    others; the reel keeps its 120 frames; and the pill has the accent of the style. The zone is taken to the 2x2
    blocks it touches, as yuv420 has one chroma sample per block: the pill on the zone's top row (269, odd) colours
    row 268 too. Measured: 17 500 to 37 500 changed pixels in the zone while the card shows, none before or after;
    at most 6 levels of difference outside the zone and the caption band, and 0.2 on average in the band; the pill
    (254, 210, 0)."""
    from test_scene_cards import requirements_or_skip  # skips without node, the modules, the browser or the fonts

    requirements_or_skip()
    style = load_style()
    p = Project("2026-11-15-cards", tmp_path / "project")
    p.work.mkdir(parents=True)
    scene = {"id": "sc1", "card": "keyword-pop", "params": {"text": "省電"}, "at": {"s": "s01", "char": 0},
             "until": {"s": "s01", "end": True}}
    (_, clip), = render_scenes(p, [Placed(0, scene, 30, 40)], style)
    picture = tagged_bt709(make_clip(tmp_path / "src", seconds=4, gray_counter=True), tmp_path / "picture.mp4")
    shots, captions = flat([Segment(0, 4)]), [event("今天聊省電", 0.0, 4.0)]
    plain = render_segments(tmp_path / "plain", picture, shots, events=captions)
    carded = render_segments(tmp_path / "carded", picture, shots, events=captions, scenes=[(30, clip)])
    assert len(corner_luma(carded)) == 120

    shown, frames = range(31, 69), [0, 29, 30, 31, 32, 42, 50, 66, 67, 68, 69, 70, 119]
    before, after = rgb_frames(plain, frames), rgb_frames(carded, frames)
    zone = style["scenes"]["zone"]
    inside = np.zeros((1920, 1080), dtype=bool)
    inside[zone["y0"] // 2 * 2:-(-zone["y1"] // 2) * 2, zone["x0"] // 2 * 2:-(-zone["x1"] // 2) * 2] = True
    band = np.zeros((1920, 1080), dtype=bool)
    band[caption_top_px(style) - 40:style["captions"]["bottom_px"] + 40] = True
    for k in frames:
        diff = np.abs(after[k] - before[k]).max(axis=2)
        changed = int((diff[inside] > 8).sum())
        assert (changed > 10_000) if k in shown else (changed == 0), (k, changed)
        assert diff[~inside & ~band].max() <= 12, k
        text = int((np.abs(after[k][band] - after[k][0, 0]).max(axis=1) > 60).sum())
        assert text > 5_000, (k, text)
        assert np.abs(after[k][band] - before[k][band]).mean() <= 1.0, k
    accent = np.array([int(style["scenes"]["colors"]["accent"][i:i + 2], 16) for i in (1, 3, 5)])
    pill = after[50][inside & (np.abs(after[50] - accent).max(axis=2) <= 12)]
    assert len(pill) > 20_000
    assert np.abs(np.median(pill, axis=0) - accent).max() <= 4, np.median(pill, axis=0)


def test_graph_makes_a_variable_frame_rate_source_constant(tmp_path):
    clip = make_clip(tmp_path / "src", seconds=2, vfr=True)
    assert probe(clip).is_vfr
    out = render_segments(tmp_path, clip, flat([Segment(0, 1)]))
    assert check_output(out, 1.0) == []
    assert len(corner_luma(out)) == 30


def test_graph_renders_a_reel_of_more_than_a_hundred_segments(tmp_path):
    """The whole graph and encode, with the graph read from its file: 150 one-frame segments, every second one
    punched in, so select and zoompan's z, x and y each add up 75 terms or more (a plain chain of 100 terms or more
    stops ffmpeg with "Cannot allocate memory"). The gray of a counter frame is the same all over, punched in or
    not."""
    clip = make_clip(tmp_path / "src", seconds=10, gray_counter=True)
    shots = [Shot(s.start, s.end, "s01", 1.1 if i % 2 else 1.0, 0.3, 0.6) for i, s in enumerate(ones(150))]
    out = render_segments(tmp_path, clip, shots)
    assert check_output(out, 150 / FPS) == []
    assert [gray_index(out, k) for k in range(150)] == [2 * i % 50 for i in range(150)]


def test_a_forced_low_video_bitrate_gives_a_file_of_that_overall_bit_rate(tmp_path, monkeypatch):
    """A real encode of 8 s of a busy picture at a forced 4 Mbps lands near 4 Mbps plus the 192 kbit/s of the audio
    (measured 4.27 Mbit/s overall: the target is held closely, which the size budget of video_bitrate relies on)."""
    monkeypatch.setattr("pipeline.render.video_bitrate", lambda duration_s: 4_000_000)
    clip = make_clip(tmp_path / "src", seconds=8)  # the testsrc2 pattern
    out = render_segments(tmp_path, clip, flat([Segment(0, 8)]))
    assert check_output(out, 8.0) == []
    done = subprocess.run(
        [ffprobe_bin(), "-v", "error", "-show_entries", "format=bit_rate", "-of", "json", str(out)],
        capture_output=True, text=True, check=True,
    )
    overall = float(json.loads(done.stdout)["format"]["bit_rate"])
    assert overall == pytest.approx(4_000_000 + 192_000, rel=0.1)


# ---- the phone preview ------------------------------------------------------------------------
# These tests encode nothing: the runner is a fake that writes files of the sizes the test names (sparse ones, so a
# 33 MB preview costs no disk), and the output check of the final is replaced.


@pytest.fixture
def no_output_check(monkeypatch):
    monkeypatch.setattr("pipeline.render.check_output", lambda path, seconds: [])


def fake_encoder(calls: list, previews=(1_000_000,)):
    """A runner that does not run ffmpeg. For the final it writes some bytes (other bytes on every call) and the
    font line libass logs. For the preview it notes the call and goes through `previews`, one entry per
    call (the last one is used again when the list runs out): a number is the size of the file it writes, an
    exception is half a file and then that exception, None is a return without any file."""
    results, finals = list(previews), []

    def run(args, log, cwd=None):
        out = Path(args[-1])
        calls.append({"args": list(args), "log": log, "cwd": cwd})
        if out.name == "final.tmp.mp4":
            finals.append(1)
            out.write_bytes(f"final {len(finals)}".encode())
            log.write_text(FONT_LINE, encoding="utf-8")
            return
        result = results.pop(0) if len(results) > 1 else results[0]
        if result is None:
            return
        if isinstance(result, Exception):
            out.write_bytes(b"half a file")
            raise result
        with open(out, "wb") as file:
            file.truncate(result)

    return run


def finals_of(calls: list) -> list[dict]:
    return [call for call in calls if Path(call["args"][-1]).name == "final.tmp.mp4"]


def previews_of(calls: list) -> list[dict]:
    return [call for call in calls if Path(call["args"][-1]).name == "preview.tmp.mp4"]


def preview_rate_of(call: dict) -> int:
    """The -b:v of a call, in bit/s."""
    return int(bitrate_arg(call["args"]).removesuffix("k")) * 1000


def cli_with(monkeypatch, runner):
    """main() renders with `runner`."""
    monkeypatch.setattr("pipeline.cli.render", lambda p, force=False: render(p, force=force, runner=runner))


def test_preview_constants():
    assert PREVIEW_MAX_BYTES == 30 * 1024 * 1024 == 31_457_280
    assert PREVIEW_OVER == "preview over 30 MiB: not kept"


def test_preview_bitrate():
    assert preview_bitrate(60.0) == 2_000_000
    assert preview_bitrate(223.0) == 919_000
    assert preview_bitrate(543.9) == 320_000


def test_preview_bitrate_is_a_whole_number_of_kbit_and_keeps_the_file_under_the_budget():
    for duration_s in (1, 30, 90, 150, 223.0, 300, 400, 543.9):
        rate = preview_bitrate(duration_s)
        assert rate % 1000 == 0 and 0 < rate <= 2_000_000
        assert (rate + 96_000) * duration_s / 8 <= 0.9 * PREVIEW_MAX_BYTES


def test_preview_args_scale_to_540_by_960_with_aac_96k(tmp_path):
    final, out = tmp_path / "final.mp4", tmp_path / "preview.tmp.mp4"
    args = build_preview_args(final, out, 919_000)
    final_args = build_ffmpeg_args(Path("/v.mov"), Path("/c.wav"), flat([Segment(0, 1)]), BASE, tmp_path / "o.mp4",
                                   tmp_path / "f.txt")
    assert args == [
        "-hide_banner", "-loglevel", "error", "-y", "-i", str(final), "-vf", "scale=540:960", "-r", "30",
        "-c:v", final_args[final_args.index("-c:v") + 1], "-b:v", "919k", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "96k", "-ac", "2", "-movflags", "+faststart", str(out),
    ]


def test_render_makes_a_preview_next_to_the_final(approved_project, no_output_check):
    p, calls = approved_project, []
    result = render(p, runner=fake_encoder(calls, [20_000_000]))
    assert result["preview"] == str(p.out / "preview.mp4") and (p.out / "preview.mp4").stat().st_size == 20_000_000
    assert result["preview_mb"] == 20.0 and result["warnings"] == []
    assert sorted(path.name for path in p.out.iterdir()) == ["final.mp4", "preview.mp4"]
    final_call, preview_call = calls
    assert final_call in finals_of(calls)
    args = preview_call["args"]
    assert args[args.index("-i") + 1] == str(p.out / "final.mp4")  # the preview is cut from the final
    assert args[-1] == str(p.out / "preview.tmp.mp4")
    assert preview_rate_of(preview_call) == preview_bitrate(reel_seconds(p)) == 2_000_000
    assert preview_call["log"] == p.work / "render" / "preview.log"  # render.log stays the log of the final
    assert "preview" in load_state(p)["steps"]


def test_a_long_reel_gets_the_lower_preview_bitrate(approved_project, monkeypatch, no_output_check):
    monkeypatch.setattr("pipeline.render.total_duration", lambda segments: 223.033)
    calls = []
    render(approved_project, runner=fake_encoder(calls))
    assert preview_rate_of(previews_of(calls)[0]) == preview_bitrate(223.033) == 919_000


def test_the_old_preview_is_gone_before_the_new_one_is_encoded(approved_project, no_output_check):
    p, calls = approved_project, []
    (p.out / "preview.mp4").write_bytes(b"the preview of an earlier edit")
    (p.out / "preview.tmp.mp4").write_bytes(b"half a file of an earlier run")
    seen = []
    encode = fake_encoder(calls, [10_000_000])

    def runner(args, log, cwd=None):
        if Path(args[-1]).name == "preview.tmp.mp4":
            seen.append(sorted(path.name for path in p.out.iterdir()))
        encode(args, log, cwd)

    render(p, runner=runner)
    assert seen == [["final.mp4"]]  # no old preview, no leftover temp file, only the final
    assert (p.out / "preview.mp4").stat().st_size == 10_000_000


def test_preview_is_encoded_again_once_when_it_is_too_big(approved_project, no_output_check):
    p, calls = approved_project, []
    result = render(p, runner=fake_encoder(calls, [33_000_000, 30_000_000]))
    first, second = previews_of(calls)
    assert preview_rate_of(first) == 2_000_000
    assert preview_rate_of(second) == int(2_000_000 * PREVIEW_MAX_BYTES / 33_000_000 * 0.95) // 1000 * 1000 == 1_811_000
    assert result["preview"] == str(p.out / "preview.mp4") and result["preview_mb"] == 30.0
    assert (p.out / "preview.mp4").stat().st_size == 30_000_000
    assert sorted(path.name for path in p.out.iterdir()) == ["final.mp4", "preview.mp4"]
    assert result["warnings"] == []


def test_make_preview_reports_the_rate_it_ended_with(approved_project, no_output_check):
    p, calls = approved_project, []
    render(p, runner=fake_encoder(calls, [10_000_000]))
    runner = fake_encoder(calls, [33_000_000, 30_000_000])
    made = make_preview(p, reel_seconds(p), runner, force=True)
    assert made["preview"] == str(p.out / "preview.mp4") and made["preview_mb"] == 30.0
    assert made["preview_bitrate"] == 1_811_000 and made["skipped"] is False


def test_a_preview_still_too_big_is_not_kept_and_the_render_succeeds(approved_project, capsys, monkeypatch,
                                                                    no_output_check):
    p, calls = approved_project, []
    (p.out / "preview.mp4").write_bytes(b"the preview of an earlier edit")
    cli_with(monkeypatch, fake_encoder(calls, [33_000_000, 32_000_000]))
    assert main(["render", p.name]) == 0
    captured = capsys.readouterr().out
    assert captured.count("\n") == 1  # one line of JSON
    out = json.loads(captured)
    assert out["ok"] is True and out["out"] == str(p.out / "final.mp4")
    assert out["preview"] is None and "preview_mb" not in out and out["warnings"] == [PREVIEW_OVER]
    assert len(previews_of(calls)) == 2  # the second encode is the last
    assert sorted(path.name for path in p.out.iterdir()) == ["final.mp4"]  # no preview, no temp file, no old preview
    assert "preview" not in load_state(p)["steps"]
    assert next_step(p) == "done"


@pytest.mark.parametrize("results", [
    [PipelineError("ffmpeg failed with exit code 1: Error while opening encoder")],
    [33_000_000, PipelineError("ffmpeg failed with exit code 1: Error while opening encoder")],
], ids=["first-encode", "second-encode"])
def test_an_ffmpeg_failure_of_the_preview_keeps_the_final(approved_project, capsys, monkeypatch, no_output_check,
                                                          results):
    p, calls = approved_project, []
    (p.out / "preview.mp4").write_bytes(b"the preview of an earlier edit")
    cli_with(monkeypatch, fake_encoder(calls, results))
    assert main(["render", p.name]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True and out["preview"] is None and "preview_mb" not in out
    assert out["warnings"] == ["preview failed: ffmpeg failed with exit code 1: Error while opening encoder"]
    assert len(previews_of(calls)) == len(results)  # a failed encode is not tried again
    assert sorted(path.name for path in p.out.iterdir()) == ["final.mp4"]  # the half file and the old preview are gone
    assert (p.out / "final.mp4").read_bytes() == b"final 1"
    assert "render" in load_state(p)["steps"] and "preview" not in load_state(p)["steps"]
    assert next_step(p) == "done"


def test_an_unchanged_reel_makes_only_the_missing_preview(approved_project, no_output_check):
    p, calls = approved_project, []
    runner = fake_encoder(calls, [10_000_000])
    render(p, runner=runner)
    final_bytes = (p.out / "final.mp4").read_bytes()
    (p.out / "preview.mp4").unlink()
    calls.clear()
    result = render(p, runner=runner)
    (call,) = calls  # one run, and it is the preview's
    assert call in previews_of(calls)
    assert result["skipped"] is True and result["attempts"] == 0  # the final was not made again
    assert (p.out / "final.mp4").read_bytes() == final_bytes
    assert result["preview"] == str(p.out / "preview.mp4") and result["preview_mb"] == 10.0
    assert result["warnings"] == []
    assert sorted(path.name for path in p.out.iterdir()) == ["final.mp4", "preview.mp4"]


def test_a_render_that_has_nothing_to_do_still_reports_the_preview(approved_project, no_output_check):
    p, calls = approved_project, []
    runner = fake_encoder(calls, [12_345_678])
    first = render(p, runner=runner)
    calls.clear()
    again = render(p, runner=runner)
    assert calls == [] and again["skipped"] is True
    assert (again["preview"], again["preview_mb"], again["warnings"]) == (first["preview"], 12.35, [])


def test_a_new_final_makes_a_new_preview(approved_project, no_output_check):
    p, calls = approved_project, []
    runner = fake_encoder(calls, [10_000_000, 11_000_000])
    render(p, runner=runner)
    first_record = load_state(p)["steps"]["preview"]
    edit = json.loads(p.edit_path.read_text())
    edit["captions"]["mode"] = "word"  # another edit: another final
    p.edit_path.write_text(json.dumps(edit))
    approve_project(p)
    calls.clear()
    result = render(p, runner=runner)
    assert len(finals_of(calls)) == 1 and len(previews_of(calls)) == 1  # the final and, after it, the preview
    assert result["skipped"] is False and result["preview_mb"] == 11.0
    assert (p.out / "preview.mp4").stat().st_size == 11_000_000
    record = load_state(p)["steps"]["preview"]
    assert record["inputs"]["final"]["sha256"] != first_record["inputs"]["final"]["sha256"]


def test_a_forced_render_makes_the_preview_again(approved_project, no_output_check):
    p, calls = approved_project, []
    runner = fake_encoder(calls, [10_000_000])
    render(p, runner=runner)
    calls.clear()
    render(p, force=True, runner=runner)
    assert len(finals_of(calls)) == 1 and len(previews_of(calls)) == 1


def test_a_missing_preview_does_not_change_what_status_reports_as_next(approved_project, no_output_check):
    p = approved_project
    render(p, runner=fake_encoder([], [10_000_000]))
    assert next_step(p) == "done"
    (p.out / "preview.mp4").unlink()  # a missing preview is not a step to do
    assert next_step(p) == "done"


def test_a_failed_final_leaves_no_preview_of_an_earlier_edit(approved_project):
    """The old preview goes with the old final, when the render really runs: a render that then fails has neither."""
    p = approved_project
    (p.out / "preview.mp4").write_bytes(b"the preview of an earlier edit")
    calls = []

    def broken(args, log, cwd=None):
        calls.append(1)
        raise PipelineError("boom", log=str(log))

    with pytest.raises(PipelineError, match="boom"):
        render(p, runner=broken)
    assert len(calls) == 2  # the final, tried twice; there is no preview encode
    assert sorted(path.name for path in p.out.iterdir()) == []


def test_a_preview_that_ffmpeg_did_not_write_is_a_warning(approved_project, capsys, monkeypatch, no_output_check):
    """A runner that returns but leaves no preview.tmp.mp4: the render still succeeds, with a warning."""
    p, calls = approved_project, []
    cli_with(monkeypatch, fake_encoder(calls, [None]))
    assert main(["render", p.name]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True and out["preview"] is None and "preview_mb" not in out
    assert out["warnings"] == ["preview failed: preview was not written"]
    assert sorted(path.name for path in p.out.iterdir()) == ["final.mp4"]
    assert "preview" not in load_state(p)["steps"]


# ---- the animation cards in the render -------------------------------------------------------------------------


def with_scene(p, params: dict | None = None) -> Project:
    """`p` with a keyword-pop scene over its first kept sentence, storyboard built and gate 1 approved again."""
    edit = json.loads(p.edit_path.read_text(encoding="utf-8"))
    first = edit["sentences"][0]["id"]
    edit["scenes"] = [{"id": "sc1", "card": "keyword-pop", "params": params or {"text": "省電"},
                       "at": {"s": first, "char": 0}, "until": {"s": first, "end": True}}]
    p.edit_path.write_text(json.dumps(edit, ensure_ascii=False), encoding="utf-8")
    return approve_project(p)


def stand_in_cards(monkeypatch, folder: Path) -> list:
    """Render the cards without Node: render's check_renderable lets everything through, and its render_scenes
    notes what it was given (in the list returned) and gives a clip file of a few bytes in `folder` for each scene."""
    calls = []

    def render_scenes(p, placed, style, run=None):
        calls.append(list(placed))
        clips = []
        for item in placed:
            clip = folder / f"clip {item.index}.mov"
            clip.write_bytes(b"a clip")
            clips.append((item, clip))
        return clips

    monkeypatch.setattr("pipeline.render.check_renderable", lambda p, placed, style: None)
    monkeypatch.setattr("pipeline.render.render_scenes", render_scenes)
    return calls


def scene_sources(folder: Path) -> set[str]:
    """The input names render gives the files of the card sources in `folder`."""
    files = [path for path in (folder / "src").rglob("*") if path.is_file()]
    names = {f"scenes/{path.relative_to(folder).as_posix()}" for path in files}
    return names | {"scenes/render.mjs", "scenes/package-lock.json"}


def test_render_overlays_the_clip_of_each_scene_at_its_reel_frame(approved_project, monkeypatch, tmp_path,
                                                                   no_output_check):
    from pipeline.scenes import scene_spans

    p = with_scene(approved_project)
    scenes, calls = stand_in_cards(monkeypatch, tmp_path), []
    render(p, runner=fake_encoder(calls))
    edit, transcript, trims = (json.loads(path.read_text(encoding="utf-8"))
                               for path in (p.edit_path, p.work / "transcript.json", p.work / "trim.json"))
    placed = scene_spans(edit, transcript, trims)
    assert len(placed) == 1 and scenes == [placed]
    final, = finals_of(calls)
    args = final["args"]
    assert args[args.index("-/filter_complex") - 2:args.index("-/filter_complex")] == [
        "-i", str(tmp_path / "clip 0.mov")]
    graph = (p.work / "render" / "filter.txt").read_text(encoding="utf-8")
    assert f"[2:v]setpts=PTS-STARTPTS+{placed[0].start}/30/TB[scene0]" in graph
    assert graph.index("overlay=eof_action=pass") < graph.index("subtitles=")


def test_render_places_a_card_away_from_the_head_found_while_it_shows(approved_project, monkeypatch, tmp_path,
                                                                    no_output_check):
    from pipeline.scenes import scene_layout

    p = with_scene(approved_project)
    edit, transcript, trims = (json.loads(path.read_text(encoding="utf-8"))
                               for path in (p.edit_path, p.work / "transcript.json", p.work / "trim.json"))
    item, = scene_layout(edit, transcript, trims)
    middle = (item.source_start + item.source_end) / 2
    head_left = {"t": middle, "x0": 0.0, "y0": 0.2, "x1": 0.4, "y1": 0.65, "score": 0.9}
    (p.work / "faces.json").write_text(json.dumps({"boxes": [head_left]}), encoding="utf-8")
    approve_project(p)  # faces.json is an input of the storyboard: its previews place the cards by it too
    scenes = stand_in_cards(monkeypatch, tmp_path)
    render(p, runner=fake_encoder([]))
    assert [placed.align for placed in scenes[0]] == ["right"]


def test_render_lists_the_card_sources_as_inputs_when_there_are_scenes(approved_project, monkeypatch, tmp_path,
                                                                        no_output_check):
    from pipeline.scene_render import scenes_dir

    p = with_scene(approved_project)
    stand_in_cards(monkeypatch, tmp_path)
    render(p, runner=fake_encoder([]))
    assert set(load_state(p)["steps"]["render"]["inputs"]) == {
        "edit", "transcript", "trim", "style", "audio", "video", "probe", "faces", "words", "dictionary",
    } | scene_sources(scenes_dir())


def test_changing_a_card_source_makes_the_render_run_again(approved_project, monkeypatch, tmp_path,
                                                            no_output_check):
    from pipeline.scene_render import scenes_dir

    copy = tmp_path / "scenes"
    shutil.copytree(scenes_dir() / "src", copy / "src")
    for name in ("render.mjs", "package-lock.json"):
        shutil.copyfile(scenes_dir() / name, copy / name)
    monkeypatch.setattr("pipeline.scene_render.scenes_dir", lambda: copy)
    p = with_scene(approved_project)
    stand_in_cards(monkeypatch, tmp_path)
    calls = []
    render(p, runner=fake_encoder(calls))
    assert render(p, runner=fake_encoder(calls))["skipped"] is True
    card = copy / "src" / "cards" / "keyword-pop.jsx"
    card.write_text(card.read_text(encoding="utf-8") + "\n// changed\n", encoding="utf-8")
    with pytest.raises(PipelineError, match="gate1 not approved"):  # the previews were drawn with the old code
        render(p, runner=fake_encoder(calls))
    approve_project(p)
    assert render(p, runner=fake_encoder(calls))["skipped"] is False
    assert len(finals_of(calls)) == 2


def test_a_render_without_scenes_reads_nothing_of_scenes_and_runs_no_node(approved_project, monkeypatch, tmp_path):
    def no_node():
        raise AssertionError("node was looked for")

    monkeypatch.setattr("pipeline.scene_render.scenes_dir", lambda: tmp_path / "not there")
    monkeypatch.setattr("pipeline.scene_render.node_bin", no_node)
    assert render(approved_project, force=True)["skipped"] is False


def test_a_refused_render_keeps_the_final_file_when_a_card_character_has_no_glyph(approved_project, monkeypatch,
                                                                                   tmp_path):
    p = with_scene(approved_project, {"text": "省電\U0001f50b"})
    content = plant_old_final(p)
    monkeypatch.setattr("pipeline.scene_render.node_bin", lambda: None)  # the glyphs are refused first
    with pytest.raises(PipelineError, match=r"the card font \S+ has no glyph for: \U0001f50b \(U\+1F50B\)"):
        render(p)
    assert (p.out / "final.mp4").read_bytes() == content


def test_a_refused_render_keeps_the_final_file_when_node_is_missing_for_a_clip_not_rendered_yet(approved_project,
                                                                                                monkeypatch):
    from pipeline.scene_render import NODE_MISSING

    p = with_scene(approved_project)
    content = plant_old_final(p)
    monkeypatch.setattr("pipeline.scene_render.node_bin", lambda: None)
    with pytest.raises(PipelineError, match=re.escape(NODE_MISSING)):
        render(p)
    assert (p.out / "final.mp4").read_bytes() == content
