"""The review video: work/review.mp4, the render's picture at 540x960 and 30 fps with a key frame every half
second, and work/audio48k.wav as its sound, so that its times are the transcript's.

Every project is one from helpers.edited_project_from_script, whose prep ran on a gray-counter clip (frame N has
gray 16 + 4 * (N % 50)), so a test can read which source frame a frame of the review video is. The tests that only
read the review video share one, made once for the module with real ffmpeg (`made`); a test that makes it again,
removes it or has ffmpeg fail builds a project of its own.
"""

import json
import subprocess
from pathlib import Path

import pytest
from helpers import edited_project_from_script
from media_fixtures import frame_luma

from pipeline.errors import PipelineError
from pipeline.media import ffprobe_bin, ffprobe_json, probe, run_ffmpeg
from pipeline.paths import studio_root
from pipeline.project import step_done
from pipeline.render import VIDEO_ENCODER
from pipeline.review_video import (
    REVIEW_AUDIO_BITRATE,
    REVIEW_FPS,
    REVIEW_GOP_FRAMES,
    REVIEW_H,
    REVIEW_VIDEO_BITRATE,
    REVIEW_W,
    make_review_video,
    review_video_args,
    review_video_path,
)

SCRIPT = "今天來聊三個小技巧。第一是把亮度調低。"
FRAME_S = 1 / 30


def review_project(tmp_path):
    return edited_project_from_script(tmp_path, SCRIPT)


def recording(calls: list):
    """A runner that notes its arguments in `calls` and then runs ffmpeg for real."""

    def run(args, log, cwd=None):
        calls.append({"args": list(args), "log": log, "cwd": cwd})
        run_ffmpeg(args, log, cwd)

    return run


@pytest.fixture(scope="module")
def made(module_env):
    """(project, what make_review_video returned, the calls of its runner): the review video of the tests that only
    read it, made once with real ffmpeg (through `recording`) in the module's own studio."""
    assert studio_root().is_relative_to(module_env)  # checked before anything is written
    p = review_project(module_env)
    calls: list = []
    result = make_review_video(p, runner=recording(calls))
    return p, result, calls


def streams_of(path: Path) -> dict[str, dict]:
    """The streams of `path` by codec type."""
    return {s["codec_type"]: s for s in ffprobe_json(path, ["-show_streams"], 60)["streams"]}


def key_frame_times(path: Path) -> list[float]:
    done = subprocess.run(
        [ffprobe_bin(), "-v", "error", "-skip_frame", "nokey", "-select_streams", "v:0",
         "-show_entries", "frame=pts_time", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )
    return sorted(float(frame["pts_time"]) for frame in json.loads(done.stdout)["frames"])


def test_the_arguments_are_the_specified_ones():
    args = review_video_args(Path("in.mp4"), Path("in.wav"), Path("out.mp4"))
    assert (REVIEW_W, REVIEW_H, REVIEW_FPS, REVIEW_GOP_FRAMES) == (540, 960, 30, 15)
    assert (REVIEW_VIDEO_BITRATE, REVIEW_AUDIO_BITRATE) == ("1500k", "96k")
    assert args == [
        "-y", "-hide_banner", "-loglevel", "error",
        "-i", "in.mp4", "-i", "in.wav", "-map", "0:v:0", "-map", "1:a:0",
        "-vf", "setpts=PTS-STARTPTS,fps=30,scale=540:960:force_original_aspect_ratio=increase,"
               "crop=540:960,format=yuv420p",
        "-c:v", VIDEO_ENCODER, "-b:v", "1500k", "-g", "15", "-force_key_frames", "expr:gte(t,n_forced*0.5)",
        "-c:a", "aac", "-b:a", "96k", "-ac", "2", "-ar", "48000",
        "-movflags", "+faststart", "out.mp4",
    ]


def test_review_video_is_540x960_at_30_fps_with_sound(made):
    p, result, _ = made
    review = review_video_path(p)
    assert review == p.work / "review.mp4"
    assert result == {"video": str(review), "skipped": False}
    streams = streams_of(review)
    assert (streams["video"]["width"], streams["video"]["height"]) == (540, 960)
    assert streams["video"]["r_frame_rate"] == "30/1"
    assert streams["video"]["codec_name"] == "h264"
    assert streams["audio"]["codec_name"] == "aac"
    assert streams["audio"]["sample_rate"] == "48000"
    assert streams["audio"]["channels"] == 2


def test_a_key_frame_at_least_every_half_second(made):
    p, _, _ = made
    times = key_frame_times(review_video_path(p))
    assert len(times) >= 4  # the clip is a few seconds long
    assert times[0] == pytest.approx(0.0, abs=0.001)
    gaps = [later - earlier for earlier, later in zip(times, times[1:])]
    assert max(gaps) <= 0.5 + FRAME_S, gaps


def test_frame_n_of_the_review_video_is_frame_n_of_the_source(made):
    p, _, _ = made
    review = review_video_path(p)
    for n in (0, 45, 77):
        assert abs(frame_luma(review, n) - (16 + 4 * (n % 50))) <= 3, n


def test_review_video_lasts_as_long_as_the_source_video(made):
    p, _, _ = made
    duration = json.loads((p.work / "probe.json").read_text(encoding="utf-8"))["duration_s"]
    assert abs(probe(review_video_path(p)).video_duration_s - duration) <= FRAME_S + 0.02


def test_the_picture_is_the_video_source_of_probe_json_and_the_sound_is_audio48k(made):
    p, _, calls = made
    (call,) = calls
    inputs = [call["args"][i + 1] for i, arg in enumerate(call["args"]) if arg == "-i"]
    video_source = json.loads((p.work / "probe.json").read_text(encoding="utf-8"))["video_source"]
    assert inputs == [video_source, str(p.work / "audio48k.wav")]
    assert call["log"] == p.work / "review_video.log"
    assert call["args"][-1] == str(p.work / "review.part.mp4")


def test_second_run_is_skipped_and_force_remakes_it(tmp_path):
    p = review_project(tmp_path)
    calls: list = []
    first = make_review_video(p, runner=recording(calls))
    again = make_review_video(p, runner=recording(calls))
    assert first["skipped"] is False and again == {"video": first["video"], "skipped": True}
    assert len(calls) == 1
    forced = make_review_video(p, force=True, runner=recording(calls))
    assert forced == {"video": first["video"], "skipped": False}
    assert len(calls) == 2
    review_video_path(p).unlink()  # a review video that was removed is made again
    assert make_review_video(p, runner=recording(calls))["skipped"] is False
    assert len(calls) == 3


def test_no_part_file_is_left_when_ffmpeg_fails(tmp_path):
    p = review_project(tmp_path)
    part = p.work / "review.part.mp4"

    def broken(args, log, cwd=None):
        part.write_bytes(b"half a file")
        raise PipelineError("ffmpeg failed with exit code 1", log=str(log))

    with pytest.raises(PipelineError, match="ffmpeg failed"):
        make_review_video(p, runner=broken)
    assert not part.exists()
    assert not review_video_path(p).exists()
    assert not step_done(p, "review_video")


def test_a_failed_remake_leaves_the_earlier_file_but_not_the_step_record(tmp_path):
    p = review_project(tmp_path)
    make_review_video(p)
    original = review_video_path(p).read_bytes()

    def broken(args, log, cwd=None):
        (p.work / "review.part.mp4").write_bytes(b"half a file")
        raise PipelineError("ffmpeg failed with exit code 1", log=str(log))

    with pytest.raises(PipelineError):
        make_review_video(p, force=True, runner=broken)
    assert not (p.work / "review.part.mp4").exists()
    assert review_video_path(p).read_bytes() == original  # the finished file is replaced only by a finished one
    assert not step_done(p, "review_video")  # but the step is not reported as done
