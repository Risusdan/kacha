"""The review video: work/review.mp4, what the review page plays.

The page plays the whole uncut source and skips the cut ranges itself, so a time on the review video must be the
same time in the transcript. The picture is the one the render uses (probe.json's video_source: the source, or the
SDR copy prep made of an HDR source), cropped to 9:16 as the render crops it, and the sound is work/audio48k.wav,
which prep made to start at the first video frame. setpts makes the first frame time 0 and fps=30 puts the frames on
the grid the transcript's frame numbers are on (frame N is at N / 30 s).

The file is small (540x960, 1.5 Mbit/s) and has a key frame at least every half second (-force_key_frames; -g is the
longest run of frames the encoder may go without one), so that the browser can seek to any time quickly.

ffmpeg writes work/review.part.mp4 (log: work/review_video.log), which becomes review.mp4 only when ffmpeg finished;
the part file does not stay behind on a failure. The step "review_video" is skipped while the picture, the sound and
probe.json have the content they had when it ran and review.mp4 is still there.
"""

import os
from collections.abc import Callable
from pathlib import Path

from pipeline.edit import load_document
from pipeline.media import run_ffmpeg
from pipeline.project import Project, run_step
from pipeline.render import VIDEO_ENCODER

REVIEW_W, REVIEW_H = 540, 960
REVIEW_FPS = 30
REVIEW_GOP_FRAMES = 15  # the longest run of frames between two key frames; the half second below is 15 frames
REVIEW_KEY_FRAME_S = 0.5  # a key frame at least this often
REVIEW_VIDEO_BITRATE = "1500k"
REVIEW_AUDIO_BITRATE = "96k"
QUIET = ["-y", "-hide_banner", "-loglevel", "error"]


def review_video_path(p: Project) -> Path:
    return p.work / "review.mp4"


def review_video_args(video_src: Path, audio: Path, out: Path) -> list[str]:
    """The ffmpeg arguments that make `out` from the picture of `video_src` and the sound of `audio`."""
    picture = (
        f"setpts=PTS-STARTPTS,fps={REVIEW_FPS},"
        f"scale={REVIEW_W}:{REVIEW_H}:force_original_aspect_ratio=increase,"
        f"crop={REVIEW_W}:{REVIEW_H},format=yuv420p"
    )
    return [
        *QUIET, "-i", str(video_src), "-i", str(audio), "-map", "0:v:0", "-map", "1:a:0", "-vf", picture,
        "-c:v", VIDEO_ENCODER, "-b:v", REVIEW_VIDEO_BITRATE, "-g", str(REVIEW_GOP_FRAMES),
        "-force_key_frames", f"expr:gte(t,n_forced*{REVIEW_KEY_FRAME_S})",
        "-c:a", "aac", "-b:a", REVIEW_AUDIO_BITRATE, "-ac", "2", "-ar", "48000",
        "-movflags", "+faststart", str(out),
    ]


def _video_source(p: Project) -> Path:
    probe = load_document(p.work / "probe.json", "prep", ("video_source",))
    return Path(probe["video_source"])


def make_review_video(p: Project, force: bool = False, runner: Callable = run_ffmpeg) -> dict:
    """Make work/review.mp4 as the step "review_video". Returns video (its path) and skipped (True when nothing has
    changed since the last run). A failure of ffmpeg raises its PipelineError and leaves no part file."""
    review = review_video_path(p)
    audio = p.work / "audio48k.wav"
    inputs = {"video": _video_source(p), "audio": audio, "probe": p.work / "probe.json"}

    def run() -> dict:
        part = p.work / "review.part.mp4"
        try:
            runner(review_video_args(_video_source(p), audio, part), p.work / "review_video.log")
            os.replace(part, review)
        finally:
            part.unlink(missing_ok=True)
        return {"video": str(review)}

    done = run_step(p, "review_video", inputs, [review], run, force=force)
    return {"video": str(review), "skipped": done["skipped"]}
