import json
from pathlib import Path

import cv2
import numpy as np
import pytest
from helpers import project_with, yunet_model
from media_fixtures import make_clip, still_to_video

from pipeline.errors import PipelineError
from pipeline.faces import Box, detect_faces, sample_times
from pipeline.media import probe, run_ffmpeg
from pipeline.prep import prep

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PORTRAIT = FIXTURES / "face" / "portrait.jpg"


def _ffmpeg(args: list[str], log: Path) -> None:
    run_ffmpeg(["-y", "-hide_banner", "-loglevel", "error", *args], log)


def test_detects_face_in_portrait_video(tmp_path):
    video = still_to_video(PORTRAIT, tmp_path, seconds=4)
    boxes = detect_faces(video, yunet_model(), sample_times(4.0))
    assert len(boxes) >= 1
    b = boxes[0]
    assert 0 <= b.x0 < b.x1 <= 1 and 0 <= b.y0 < b.y1 <= 1 and b.score >= 0.8


def test_box_follows_the_face_in_the_displayed_frame(tmp_path):
    # The portrait is scaled to the 1080 width and centred in the 1080x1920 frame: its face is in
    # the upper middle. The box is 0-1 of that frame, not of the 540-wide frame that was searched.
    boxes = detect_faces(still_to_video(PORTRAIT, tmp_path, seconds=4), yunet_model(), sample_times(4.0))
    assert [b.t for b in boxes] == [1.0, 3.0]
    for b in boxes:
        assert 0.4 < (b.x0 + b.x1) / 2 < 0.6 and 0.3 < (b.y0 + b.y1) / 2 < 0.5
        assert 0.25 < b.x1 - b.x0 < 0.6 and 0.1 < b.y1 - b.y0 < 0.35


def test_keeps_only_the_largest_face_of_a_frame(tmp_path):
    # The small copy of the portrait goes top left and the big one bottom right, so that picking the
    # first face by position would pick the wrong one.
    canvas = tmp_path / "two_faces.jpg"
    _ffmpeg(
        ["-i", str(PORTRAIT), "-i", str(PORTRAIT), "-f", "lavfi", "-i", "color=c=gray:s=1080x1920",
         "-filter_complex", "[0]scale=300:-2[small];[1]scale=700:-2[big];"
                            "[2][small]overlay=60:150[a];[a][big]overlay=340:1000",
         "-frames:v", "1", str(canvas)],
        tmp_path / "canvas.log",
    )
    boxes = detect_faces(still_to_video(canvas, tmp_path, seconds=2), yunet_model(), [0.5, 1.5])
    assert len(boxes) == 2  # one per sample, though the frame holds two faces
    for b in boxes:
        assert (b.x0 + b.x1) / 2 > 0.4 and (b.y0 + b.y1) / 2 > 0.6


def test_box_is_in_the_displayed_frame_of_a_rotated_source(tmp_path):
    upright = still_to_video(PORTRAIT, tmp_path, seconds=2)
    sideways = tmp_path / "sideways.mp4"  # the pictures are turned 90 degrees left in a 1920x1080 file
    _ffmpeg(["-i", str(upright), "-vf", "transpose=2", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
             "-an", str(sideways)], tmp_path / "sideways.log")
    rotated = tmp_path / "rotated.mp4"  # ...and the display matrix turns them back
    _ffmpeg(["-display_rotation", "-90", "-i", str(sideways), "-map", "0", "-c", "copy", str(rotated)],
            tmp_path / "rotated.log")
    info = probe(rotated)
    assert (info.display_w, info.display_h) == (1080, 1920) and info.rotation != 0

    expected = detect_faces(upright, yunet_model(), [1.0])
    actual = detect_faces(rotated, yunet_model(), [1.0])
    assert len(expected) == len(actual) == 1
    for name in ("x0", "y0", "x1", "y1"):
        assert getattr(actual[0], name) == pytest.approx(getattr(expected[0], name), abs=0.03)


def test_no_face_in_test_pattern(tmp_path):
    assert detect_faces(make_clip(tmp_path, seconds=4), yunet_model(), sample_times(4.0)) == []


def test_frame_past_the_end_raises(tmp_path):
    video = make_clip(tmp_path, seconds=2)
    with pytest.raises(PipelineError, match="no frame"):
        detect_faces(video, yunet_model(), [1.0, 30.0])


def test_unreadable_video_raises(tmp_path):
    video = tmp_path / "broken.mp4"
    video.write_bytes(b"not a video")
    with pytest.raises(PipelineError, match="cannot grab"):
        detect_faces(video, yunet_model(), [1.0])


def test_missing_model_raises(tmp_path):
    with pytest.raises(PipelineError, match="pipeline setup"):
        detect_faces(make_clip(tmp_path), tmp_path / "no_such_model.onnx", [1.0])


def test_unloadable_model_raises(tmp_path):
    model = tmp_path / "broken.onnx"
    model.write_bytes(b"not a model")
    with pytest.raises(PipelineError, match="cannot load face model"):
        detect_faces(make_clip(tmp_path), model, [1.0])


def test_failed_model_load_restores_the_opencv_log_level(tmp_path):
    model = tmp_path / "broken.onnx"
    model.write_bytes(b"not a model")
    log = cv2.utils.logging
    original = log.getLogLevel()
    log.setLogLevel(log.LOG_LEVEL_DEBUG)  # not the level the loader sets for the call, so a missed restore shows
    try:
        with pytest.raises(PipelineError, match="cannot load face model"):
            detect_faces(tmp_path / "never_read.mp4", model, [1.0])
        assert log.getLogLevel() == log.LOG_LEVEL_DEBUG
    finally:
        log.setLogLevel(original)


class _FixedDetector:
    """Stands in for cv2.FaceDetectorYN: finds one face, the same one in every frame, and notes the frame sizes."""

    def __init__(self, row):
        self.row = np.array([row], dtype=np.float32)
        self.input_sizes: list[tuple[int, int]] = []

    def setInputSize(self, size):
        self.input_sizes.append(tuple(size))

    def detect(self, frame):
        return 1, self.row


def test_face_box_normalised_for_landscape_source(tmp_path, monkeypatch):
    # A 1920x1080 frame is scaled to 540x304 before it is searched (540 * 1080 / 1920 = 303.75, which ffmpeg
    # rounds to the even 304). A detector row is x, y, w, h (pixels of that 540x304 picture), ten landmark
    # numbers and the score. The face at x=108, y=76, 54 wide and 76 tall is, in 0-1 of the picture:
    #   x0 = 108 / 540 = 0.2, x1 = (108 + 54) / 540 = 0.3, y0 = 76 / 304 = 0.25, y1 = (76 + 76) / 304 = 0.5
    # A mix-up of width and height would give 76 / 540 = 0.141 for y0.
    detector = _FixedDetector([108, 76, 54, 76, *[0] * 10, 0.875])
    monkeypatch.setattr("pipeline.faces._load_detector", lambda model: detector)
    model = tmp_path / "stub.onnx"
    model.write_bytes(b"")
    boxes = detect_faces(make_clip(tmp_path, w=1920, h=1080, seconds=2), model, [0.5, 1.5])
    assert detector.input_sizes == [(540, 304), (540, 304)]
    assert [b.t for b in boxes] == [0.5, 1.5]
    for b in boxes:
        assert (b.x0, b.y0, b.x1, b.y1) == pytest.approx((0.2, 0.25, 0.3, 0.5), abs=1e-6)
        assert b.score == 0.875


def test_no_sample_times_means_no_boxes_and_no_work(tmp_path):
    assert detect_faces(tmp_path / "not_even_there.mp4", yunet_model(), []) == []


def test_sample_times():
    assert sample_times(4.0) == [1.0, 3.0]
    assert len(sample_times(10.0, every_s=0.1, max_samples=5)) == 5


def test_sample_times_over_the_cap_are_midpoints_of_equal_slices():
    assert sample_times(10.0, every_s=0.1, max_samples=5) == pytest.approx([1.0, 3.0, 5.0, 7.0, 9.0])


def test_sample_times_over_the_cap_give_exactly_max_samples():
    # 3.0 // (3.0 / 60) is 59.0 in floating point: flooring the slice count would lose a sample.
    times = sample_times(3.0, every_s=0.01, max_samples=60)
    assert len(times) == 60
    assert times == sorted(times) and 0 < times[0] and times[-1] < 3.0
    assert times[0] == pytest.approx(0.025) and times[-1] == pytest.approx(2.975)


def test_short_source_gets_one_sample():
    times = sample_times(1.0)
    assert len(times) == 1 and 0 < times[0] < 1
    assert times == [0.5]


def test_sample_times_with_one_sample_is_the_middle_of_the_source():
    assert sample_times(1.9) == pytest.approx([0.95])
    assert sample_times(3.0) == [1.5]  # one slice of 2 s fits, so there is one sample, in the middle of the 3 s
    assert sample_times(0.04) == pytest.approx([0.02])


def test_source_with_no_length_gets_no_samples():
    assert sample_times(0.0) == [] and sample_times(-1.0) == []


def test_prep_writes_faces_json(tmp_path):
    p = project_with(make_clip(tmp_path, seconds=4))
    prep(p)
    fj = json.loads((p.work / "faces.json").read_text())
    assert fj["model"] == "yunet-2023mar" and fj["boxes"] == [] and fj["samples"] == 2


def test_prep_faces_json_holds_the_boxes_of_a_face_video(tmp_path):
    p = project_with(still_to_video(PORTRAIT, tmp_path, seconds=4))
    prep(p)
    fj = json.loads((p.work / "faces.json").read_text())
    assert fj["samples"] == 2 and [b["t"] for b in fj["boxes"]] == [1.0, 3.0]
    for b in fj["boxes"]:
        assert set(b) == {"t", "x0", "y0", "x1", "y1", "score"}
        assert 0 <= b["x0"] < b["x1"] <= 1 and 0 <= b["y0"] < b["y1"] <= 1 and b["score"] >= 0.8


def test_prep_reruns_when_faces_json_is_gone(tmp_path):
    p = project_with(make_clip(tmp_path, seconds=4))
    assert prep(p)["skipped"] is False
    assert prep(p)["skipped"] is True and (p.work / "faces.json").is_file()
    (p.work / "faces.json").unlink()
    assert prep(p)["skipped"] is False and (p.work / "faces.json").is_file()


def test_prep_samples_a_one_second_source_once(tmp_path):
    p = project_with(make_clip(tmp_path, seconds=1))
    prep(p)
    assert json.loads((p.work / "faces.json").read_text())["samples"] == 1


def test_prep_samples_faces_from_the_hdr_work_file(tmp_path, monkeypatch):
    p = project_with(make_clip(tmp_path, w=1080, h=1920, hdr=True, seconds=4))
    searched: list[Path] = []

    def recording_detect_faces(video, model, times):
        searched.append(video)
        return detect_faces(video, model, times)

    monkeypatch.setattr("pipeline.prep.detect_faces", recording_detect_faces)
    prep(p)
    assert searched == [p.work / "video.mp4"]  # the SDR copy, not the HDR source
    assert json.loads((p.work / "faces.json").read_text())["samples"] == 2


def test_box_is_a_frozen_record():
    box = Box(t=1.0, x0=0.1, y0=0.2, x1=0.3, y1=0.4, score=0.9)
    with pytest.raises(AttributeError):
        box.t = 2.0
