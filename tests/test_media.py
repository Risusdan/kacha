import media_fixtures
import pytest
from media_fixtures import frame_luma, make_clip, make_mkv, video_stream_duration

from pipeline.errors import PipelineError
from pipeline.media import ffmpeg_bin, ffprobe_bin, probe, run_ffmpeg


def test_probe_portrait(tmp_path):
    p = probe(make_clip(tmp_path))
    assert (p.display_w, p.display_h, p.is_vfr, p.is_hdr, p.audio_tracks) == (1080, 1920, False, False, 1)


def test_probe_rotated_reports_display_dims(tmp_path):
    p = probe(make_clip(tmp_path, w=1920, h=1080, rotate=90))
    assert (p.display_w, p.display_h) == (1080, 1920) and abs(p.rotation) == 90


def test_probe_rotate_270(tmp_path):
    p = probe(make_clip(tmp_path, w=1920, h=1080, rotate=270))
    assert (p.display_w, p.display_h) == (1080, 1920)
    assert p.rotation == -90  # ffprobe reports 270 degrees as -90; the 90 clip above reports 90


def test_probe_vfr_and_hdr_flags(tmp_path):
    assert probe(make_clip(tmp_path, vfr=True)).is_vfr
    assert probe(make_clip(tmp_path, hdr=True)).is_hdr


def test_probe_missing_file_raises_pipeline_error(tmp_path):
    with pytest.raises(PipelineError, match="missing.mp4"):
        probe(tmp_path / "missing.mp4")


def test_probe_unreadable_file_raises_pipeline_error(tmp_path):
    junk = tmp_path / "junk.mp4"
    junk.write_bytes(b"not a video")
    with pytest.raises(PipelineError, match="junk.mp4"):
        probe(junk)


def test_probe_reports_audio_layout_and_start_times(tmp_path):
    p = probe(make_clip(tmp_path, audio="mono"))
    assert p.audio_channels == 1 and p.video_start_s == 0 and p.audio_start_s is not None
    none = probe(make_clip(tmp_path, audio=None))
    assert (none.audio_tracks, none.audio_channels, none.audio_start_s) == (0, 0, None)


def test_probe_audio_delay(tmp_path):
    p = probe(make_clip(tmp_path, audio_delay=0.5))
    # The AAC encoder's 1024 priming samples (21 ms at 48 kHz) make the first audio packet start that
    # much before the 0.5 s the audio was delayed to.
    assert p.audio_start_s == pytest.approx(0.5 - 1024 / 48000, abs=0.002)
    assert p.video_start_s == 0


def test_probe_two_tracks(tmp_path):
    assert probe(make_clip(tmp_path, audio="two_tracks")).audio_tracks == 2


def test_probe_reports_video_stream_duration(tmp_path):
    p = probe(make_clip(tmp_path, seconds=3, audio_delay=0.5))
    assert p.video_duration_s == 3.0
    assert p.duration_s > 3.01  # the container lasts until its delayed audio ends


def test_probe_video_duration_comes_from_the_last_packet_when_the_stream_has_none(tmp_path):
    clip = make_mkv(tmp_path, seconds=2.0, audio_seconds=3.0)
    assert video_stream_duration(clip) is None
    p = probe(clip)
    assert p.video_duration_s == pytest.approx(2.0, abs=0.001)  # the last frame shown ends at 2.0 s
    assert p.duration_s > 3.0  # the container lasts until the audio ends


def test_probe_video_duration_without_a_stream_duration_leaves_out_the_delay(tmp_path):
    clip = make_mkv(tmp_path, seconds=2.0, audio_seconds=3.0, video_delay=0.5)
    assert video_stream_duration(clip) is None
    p = probe(clip)
    assert p.video_start_s == pytest.approx(0.5)
    assert p.video_duration_s == pytest.approx(2.0, abs=0.001)  # as long as the video, as for a file with the duration


def test_make_clip_video_delay_starts_the_video_late_and_keeps_its_length(tmp_path):
    p = probe(make_clip(tmp_path, seconds=3, video_delay=0.5))
    assert p.video_start_s == pytest.approx(0.5)
    assert p.video_duration_s == pytest.approx(3.0)
    assert p.audio_start_s == pytest.approx(0.0)  # the sound is not delayed with the picture


def test_ffprobe_timeout(tmp_path, monkeypatch):
    slow = tmp_path / "slow_tools"
    slow.mkdir()
    (slow / "ffprobe").write_text("#!/bin/sh\nexec sleep 5\n")
    (slow / "ffprobe").chmod(0o755)
    monkeypatch.setenv("REELS_FFMPEG_DIR", str(slow))
    video = tmp_path / "any.mp4"
    with pytest.raises(PipelineError) as error:
        probe(video, timeout_s=1)
    assert str(error.value) == f"ffprobe timed out after 1 s: {video}"


def test_clip_fixture_leaves_no_empty_log(tmp_path, monkeypatch):
    cache = tmp_path / "clip_cache"  # an empty cache, so that the clip is built and not found
    monkeypatch.setattr(media_fixtures, "CACHE", cache)
    make_clip(tmp_path / "out", seconds=0.5)
    make_clip(tmp_path / "out", seconds=0.5, w=270, h=480, rotate=90)  # a rotated clip is built in two runs
    assert list(cache.glob("clip-*.mp4"))
    assert [log.name for log in cache.glob("clip-*.log") if log.stat().st_size == 0] == []


def test_run_ffmpeg_failure_reports_log(tmp_path):
    with pytest.raises(PipelineError) as e:
        run_ffmpeg(["-i", str(tmp_path / "missing.mp4"), str(tmp_path / "o.mp4")], tmp_path / "x.log")
    assert e.value.extra["log"].endswith("x.log")
    assert (tmp_path / "x.log").read_text(errors="replace").strip()


def test_run_ffmpeg_success_writes_log_and_runs_in_cwd(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    log = tmp_path / "ok.log"
    run_ffmpeg(["-f", "lavfi", "-i", "color=c=black:s=16x16:d=0.1", "-frames:v", "1", "out.png"], log, cwd=work)
    assert (work / "out.png").exists() and log.exists()


def test_ffmpeg_dir_can_be_overridden(tmp_path, monkeypatch):
    monkeypatch.setenv("REELS_FFMPEG_DIR", str(tmp_path))
    with pytest.raises(PipelineError, match="ffmpeg"):
        ffmpeg_bin()
    for name in ("ffmpeg", "ffprobe"):
        (tmp_path / name).write_text("#!/bin/sh\n")
    assert ffmpeg_bin() == str(tmp_path / "ffmpeg")
    assert ffprobe_bin() == str(tmp_path / "ffprobe")


def test_gray_counter_frame_n_has_luma_16_plus_4_times_n_mod_50(tmp_path):
    clip = make_clip(tmp_path, w=270, h=480, seconds=2.0, gray_counter=True)
    for n in (0, 1, 7, 49, 50):
        assert abs(frame_luma(clip, n) - (16 + 4 * (n % 50))) <= 2
