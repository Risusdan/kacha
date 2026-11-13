"""Face boxes found by the YuNet detector in frames sampled from a video.

The caption step places subtitles just below the speaker's face, so it needs one box per sampled
frame: the largest face, in 0-1 coordinates of the displayed (rotation applied) picture.
"""

import subprocess
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from pipeline.errors import PipelineError
from pipeline.media import ffmpeg_bin

MODEL_NAME = "yunet-2023mar"
FRAME_W = 540  # a frame is scaled to this width before it is searched
SCORE_THRESHOLD = 0.8
GRAB_TIMEOUT_S = 120


@dataclass(frozen=True)
class Box:
    """A face at time `t` (seconds): x0, y0 (top left) and x1, y1 (bottom right), 0-1 of the displayed frame."""

    t: float
    x0: float
    y0: float
    x1: float
    y1: float
    score: float


def sample_times(duration_s: float, every_s: float = 2.0, max_samples: int = 60) -> list[float]:
    """The middle of each `every_s` slice of the video. A video that needs more than `max_samples`
    of them is cut into exactly `max_samples` equal slices instead. Any video with a length gets at
    least one sample, and a video that gets only one is sampled in its middle."""
    if duration_s <= 0:
        return []
    count = max(1, int(duration_s // every_s))
    if count == 1:
        return [duration_s / 2]
    if count > max_samples:
        # Not `every_s = duration_s / max_samples` followed by the floor above: that floor can come
        # out one short (3.0 // (3.0 / 60) is 59.0), so the count is fixed here.
        every_s, count = duration_s / max_samples, max_samples
    return [every_s * (k + 0.5) for k in range(count)]


def _grab_frame(video: Path, t: float) -> np.ndarray:
    """The frame at `t` seconds, rotation applied, scaled to FRAME_W wide, as a BGR image."""
    command = [
        ffmpeg_bin(), "-nostdin", "-hide_banner", "-loglevel", "error",
        "-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1",
        "-vf", f"scale={FRAME_W}:-2", "-f", "image2pipe", "-c:v", "bmp", "-",  # a BMP says its own size
    ]
    try:
        done = subprocess.run(command, capture_output=True, timeout=GRAB_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as error:
        raise PipelineError(f"cannot grab a frame at {t:.3f}s of {video}: {error}") from error
    if done.returncode != 0:
        lines = [line for line in done.stderr.decode(errors="replace").splitlines() if line.strip()]
        raise PipelineError(f"cannot grab a frame at {t:.3f}s of {video}: {lines[-1] if lines else 'ffmpeg failed'}")
    frame = cv2.imdecode(np.frombuffer(done.stdout, dtype=np.uint8), cv2.IMREAD_COLOR) if done.stdout else None
    if frame is None:
        raise PipelineError(f"no frame at {t:.3f}s of {video}")
    return frame


def _load_detector(model: Path) -> "cv2.FaceDetectorYN":
    # OpenCV 5 prints a warning on every load ("Targets are not supported by the new graph engine");
    # it changes nothing here, so the log level is raised for the call only.
    previous = cv2.utils.logging.getLogLevel()
    cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
    try:
        return cv2.FaceDetectorYN.create(str(model), "", (FRAME_W, FRAME_W), SCORE_THRESHOLD)
    except cv2.error as error:
        raise PipelineError(f"cannot load face model {model}: {error}") from error
    finally:
        cv2.utils.logging.setLogLevel(previous)


def detect_faces(video: Path, model: Path, times: list[float]) -> list[Box]:
    """The largest face at each time in `times` (seconds from the first frame of `video`).

    A time with no face adds nothing. A frame that cannot be read (a time past the end of the video,
    a broken file) raises PipelineError rather than counting as "no face".
    """
    if not times:
        return []
    if not model.is_file():
        raise PipelineError(f"face model not found at {model}; run `pipeline setup`")
    detector = _load_detector(model)

    boxes: list[Box] = []
    for t in times:
        frame = _grab_frame(video, t)
        height, width = frame.shape[:2]
        detector.setInputSize((width, height))
        _, faces = detector.detect(frame)
        if faces is None or len(faces) == 0:
            continue
        # A row is x, y, w, h, five landmark points (10 numbers), score.
        x, y, w, h, *_, score = max(faces, key=lambda face: face[2] * face[3])
        boxes.append(
            Box(
                t=t,
                x0=_unit(x / width),
                y0=_unit(y / height),
                x1=_unit((x + w) / width),
                y1=_unit((y + h) / height),
                score=float(score),
            )
        )
    return boxes


def _unit(value: float) -> float:
    return min(1.0, max(0.0, float(value)))
