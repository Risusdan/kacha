"""Where ffmpeg lives, what a video file contains (probe), and running ffmpeg with a log."""

import json
import os
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from functools import cache
from pathlib import Path

from pipeline.errors import PipelineError

HDR_TRANSFERS = ("arib-std-b67", "smpte2084")  # HLG and PQ
FFPROBE_TIMEOUT_S = 60  # what an ffprobe run may take before it is given up


@cache
def _brew_ffmpeg_dir() -> Path:
    try:
        done = subprocess.run(
            ["brew", "--prefix", "ffmpeg-full"], capture_output=True, text=True, check=True, timeout=60
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise PipelineError(
            "cannot locate ffmpeg-full through brew; install it or set REELS_FFMPEG_DIR to its bin folder"
        ) from error
    return Path(done.stdout.strip()) / "bin"


def ffmpeg_dir() -> Path:
    """The folder holding ffmpeg and ffprobe: $REELS_FFMPEG_DIR, else ffmpeg-full's bin folder."""
    override = os.environ.get("REELS_FFMPEG_DIR", "")
    return Path(override) if override else _brew_ffmpeg_dir()


def _tool(name: str) -> str:
    path = ffmpeg_dir() / name
    if not path.is_file():
        raise PipelineError(f"{name} not found at {path}; set REELS_FFMPEG_DIR to the folder that holds it")
    return str(path)


def ffmpeg_bin() -> str:
    return _tool("ffmpeg")


def ffprobe_bin() -> str:
    return _tool("ffprobe")


@dataclass
class Probe:
    display_w: int
    display_h: int
    rotation: int
    fps_r: float
    fps_avg: float
    is_vfr: bool
    color_transfer: str
    is_hdr: bool
    duration_s: float
    audio_tracks: int
    video_start_s: float  # start time of the video stream
    video_duration_s: float  # duration of the video stream; when it has none, from its last packet (not the container)
    audio_start_s: float | None  # start time of the first audio stream; None without audio
    audio_channels: int  # channels of the first audio stream; 0 without audio


def _fraction(text: str | None) -> Fraction | None:
    try:
        value = Fraction(text or "0/0")
    except (ValueError, ZeroDivisionError):
        return None
    return value if value > 0 else None


def _start_s(stream: dict) -> float:
    try:
        return round(float(stream.get("start_time", 0.0)), 6)
    except ValueError:  # "N/A"
        return 0.0


def ffprobe_json(path: Path, args: list[str], timeout_s: float) -> dict:
    """What ffprobe prints, as JSON, for `args` and the file at `path`. Raises PipelineError when it cannot be
    read, or when ffprobe takes longer than `timeout_s` seconds."""
    try:
        done = subprocess.run(
            [ffprobe_bin(), "-v", "error", *args, "-of", "json", "-i", str(path)],
            capture_output=True,
            text=True,
            check=True,
            timeout=timeout_s,
        )
        return json.loads(done.stdout)
    except subprocess.TimeoutExpired as error:
        raise PipelineError(f"ffprobe timed out after {timeout_s:g} s: {path}") from error
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or "").strip().splitlines()
        raise PipelineError(f"cannot read {path}: {detail[-1] if detail else 'ffprobe failed'}") from error
    except (OSError, json.JSONDecodeError) as error:
        raise PipelineError(f"cannot read {path}: {error}") from error


def _video_end_s(path: Path, stream_index: int, timeout_s: float) -> float:
    """When the last frame of stream number `stream_index` of `path` stops being shown, in seconds on the file's
    timeline: the largest pts_time + duration_time of its packets. Not the last packet in the file, which
    can be a frame shown earlier than another one (B-frames are stored out of order). Reads every packet
    of the stream, so it is for a stream whose duration the container does not give."""
    packets = ffprobe_json(
        path, ["-select_streams", str(stream_index), "-show_entries", "packet=pts_time,duration_time"], timeout_s
    ).get("packets", [])
    ends = []
    for packet in packets:
        try:
            shown_at = float(packet["pts_time"])
        except (KeyError, ValueError):  # missing or "N/A"
            continue
        try:
            shown_for = float(packet["duration_time"])
        except (KeyError, ValueError):
            shown_for = 0.0
        ends.append(shown_at + shown_for)
    if not ends:
        raise PipelineError(f"no duration in {path}")
    return max(ends)


def probe(path: Path, timeout_s: float = FFPROBE_TIMEOUT_S) -> Probe:
    """Facts about the video file at `path`. Raises PipelineError when it cannot be read, or when ffprobe
    takes longer than `timeout_s` seconds."""
    info = ffprobe_json(path, ["-show_streams", "-show_format"], timeout_s)

    streams = info.get("streams", [])
    videos = [
        s for s in streams if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")
    ]
    audios = [s for s in streams if s.get("codec_type") == "audio"]
    if not videos:
        raise PipelineError(f"no video stream in {path}")
    video = videos[0]

    rotation = 0
    for side_data in video.get("side_data_list", []):
        if side_data.get("side_data_type") == "Display Matrix":
            rotation = int(round(float(side_data.get("rotation", 0))))
    width, height = int(video["width"]), int(video["height"])
    if rotation % 180 == 90:
        width, height = height, width

    r_frame = _fraction(video.get("r_frame_rate"))
    avg_frame = _fraction(video.get("avg_frame_rate")) or r_frame
    if r_frame is None or avg_frame is None:
        raise PipelineError(f"no frame rate in {path}")
    color_transfer = video.get("color_transfer", "unknown")
    try:
        duration = float(info.get("format", {}).get("duration") or video.get("duration"))
    except (TypeError, ValueError) as error:
        raise PipelineError(f"no duration in {path}") from error
    try:
        video_duration = float(video["duration"])
    except (KeyError, TypeError, ValueError):  # missing or "N/A", as in Matroska files
        # The container's duration would include an audio track that outlasts the video.
        video_duration = _video_end_s(path, video["index"], timeout_s) - _start_s(video)

    return Probe(
        display_w=width,
        display_h=height,
        rotation=rotation,
        fps_r=round(float(r_frame), 3),
        fps_avg=round(float(avg_frame), 3),
        is_vfr=r_frame != avg_frame,
        color_transfer=color_transfer,
        is_hdr=color_transfer in HDR_TRANSFERS,
        duration_s=round(duration, 3),
        audio_tracks=len(audios),
        video_start_s=_start_s(video),
        video_duration_s=round(video_duration, 3),
        audio_start_s=_start_s(audios[0]) if audios else None,
        audio_channels=int(audios[0].get("channels", 0)) if audios else 0,
    )


def run_ffmpeg(args: list[str], log: Path, cwd: Path | None = None) -> None:
    """Run ffmpeg with `-nostdin` plus `args`; stderr goes to `log` (overwritten).

    Raises PipelineError carrying `log` when ffmpeg exits non-zero.
    """
    command = [ffmpeg_bin(), "-nostdin", *args]
    log.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(log, "wb") as log_file:
            done = subprocess.run(
                command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log_file, cwd=cwd
            )
    except OSError as error:
        raise PipelineError(f"cannot run ffmpeg: {error}", log=str(log)) from error
    if done.returncode != 0:
        lines = [line for line in log.read_text(errors="replace").splitlines() if line.strip()]
        detail = f": {lines[-1]}" if lines else ""
        raise PipelineError(f"ffmpeg failed with exit code {done.returncode}{detail}", log=str(log))
