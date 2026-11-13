import json
from pathlib import Path

import pytest
import soundfile as sf
from helpers import project_with
from media_fixtures import (
    dominant_hz,
    first_sound_s,
    largest_audio_pts_step_s,
    last_sound_s,
    make_clip,
    make_mkv,
    video_stream_duration,
)

from pipeline.assets import YUNET_FILENAME
from pipeline.cli import main
from pipeline.errors import PipelineError
from pipeline.media import ffprobe_bin, probe, run_ffmpeg
from pipeline.prep import prep
from pipeline.project import file_sha256, load_state


def _remux(clip: Path, name: str, video_options: tuple[str, ...] = (), audio_options: tuple[str, ...] = ()) -> Path:
    """Copy the video of `clip` and the audio of `clip` into a new file, giving each input its own
    options (for example `-itsoffset` or `-t`), so the two streams get a different start or length."""
    out = clip.with_name(name)
    run_ffmpeg(
        ["-y", "-hide_banner", "-loglevel", "error", *video_options, "-i", str(clip), *audio_options, "-i", str(clip),
         "-map", "0:v", "-map", "1:a", "-c", "copy", str(out)],
        clip.with_name(name + ".log"),
    )
    return out


ONE_FRAME_S = 1 / 30  # the clips are 30 fps


def _files_under(folder: Path) -> list[str]:
    return sorted(str(path.relative_to(folder)) for path in folder.rglob("*"))


def test_prep_writes_both_wavs(tmp_path):
    p = project_with(make_clip(tmp_path, seconds=3))
    prep(p)
    a48, a16 = sf.info(p.work / "audio48k.wav"), sf.info(p.work / "audio16k.wav")
    assert (a48.samplerate, a48.channels, a16.samplerate, a16.channels) == (48000, 2, 16000, 1)
    assert (a48.subtype, a16.subtype) == ("PCM_16", "PCM_16")
    assert abs(a48.duration - 3.0) < 0.05
    assert abs(a16.duration - a48.duration) < 0.001


def test_prep_uses_first_audio_track(tmp_path):
    p = project_with(make_clip(tmp_path, audio="two_tracks"))
    prep(p)
    assert abs(dominant_hz(p.work / "audio48k.wav") - 440) < 10  # helper in media_fixtures
    assert abs(dominant_hz(p.work / "audio16k.wav") - 440) < 10


def test_prep_mono_becomes_stereo(tmp_path):
    p = project_with(make_clip(tmp_path, audio="mono"))
    prep(p)
    data, _ = sf.read(p.work / "audio48k.wav", always_2d=True)
    assert data.shape[1] == 2 and (data[:, 0] == data[:, 1]).all() and abs(data).max() > 0.3


def test_prep_16k_keeps_the_level(tmp_path):
    p = project_with(make_clip(tmp_path))
    prep(p)
    peak48 = abs(sf.read(p.work / "audio48k.wav")[0]).max()
    peak16 = abs(sf.read(p.work / "audio16k.wav")[0]).max()
    assert 0.9 < peak16 / peak48 < 1.1


def test_prep_aligns_delayed_audio(tmp_path):
    p = project_with(make_clip(tmp_path, seconds=3, audio_delay=0.5))
    prep(p)
    assert abs(first_sound_s(p.work / "audio48k.wav") - 0.5) < 0.02  # 素材音訊在影像 0.5 秒後才開始
    assert abs(first_sound_s(p.work / "audio16k.wav") - 0.5) < 0.02


def test_prep_trims_audio_that_starts_before_video(tmp_path):
    clip = _remux(make_clip(tmp_path, seconds=3), "late-video.mp4", video_options=("-itsoffset", "0.5"))
    assert probe(clip).video_start_s == 0.5 and probe(clip).audio_start_s == 0  # video starts 0.5 s after audio
    p = project_with(clip)
    prep(p)
    a48 = p.work / "audio48k.wav"
    assert first_sound_s(a48) < 0.02
    assert abs(last_sound_s(a48) - 2.5) < 0.02  # the audio ends at 3.0 s, which is 2.5 s after the first frame
    assert abs(sf.info(a48).duration - 3.0) < 0.02  # still the length of the video


def test_prep_aligns_when_neither_stream_starts_at_zero(tmp_path):
    # The sound of this clip begins 0.5 s into the source, and so does the video: the sound is at the first frame.
    clip = _remux(make_clip(tmp_path, seconds=3, audio_delay=0.5), "both-late.mp4", video_options=("-itsoffset", "0.5"))
    assert probe(clip).video_start_s == 0.5 and 0.4 < probe(clip).audio_start_s < 0.5
    p = project_with(clip)
    prep(p)
    a48 = p.work / "audio48k.wav"
    assert first_sound_s(a48) < 0.02
    assert abs(last_sound_s(a48) - 2.5) < 0.03  # the audio ends 3.017 s into the source, 2.517 s after the first frame
    assert abs(sf.info(a48).duration - 3.0) < 0.02


def test_prep_pads_audio_shorter_than_video(tmp_path):
    clip = _remux(make_clip(tmp_path, seconds=3), "short-audio.mp4", audio_options=("-t", "1.5"))
    p = project_with(clip)
    prep(p)
    for name in ("audio48k.wav", "audio16k.wav"):
        assert abs(sf.info(p.work / name).duration - 3.0) < 0.02
    assert abs(last_sound_s(p.work / "audio48k.wav") - 1.5) < 0.03


def test_prep_trims_audio_longer_than_video(tmp_path):
    clip = _remux(make_clip(tmp_path, seconds=3), "short-video.mp4", video_options=("-t", "1.5"))
    info = probe(clip)
    assert info.duration_s > 2.9 and info.video_duration_s < 1.6  # the container lasts as long as its audio
    p = project_with(clip)
    prep(p)
    for name in ("audio48k.wav", "audio16k.wav"):
        assert abs(sf.info(p.work / name).duration - info.video_duration_s) < 0.02


def test_mkv_without_stream_duration_and_longer_audio(tmp_path):
    clip = make_mkv(tmp_path, seconds=2.0, audio_seconds=3.0)
    assert video_stream_duration(clip) is None  # ffprobe gives the video stream no duration
    assert probe(clip).duration_s > 3.0  # the container lasts as long as the longer audio
    p = project_with(clip)
    result = prep(p)
    # The video is 2.0 s (60 frames) long: both the recorded length and the wavs are that, not the container's.
    assert abs(result["duration_s"] - 2.0) <= ONE_FRAME_S
    assert abs(json.loads((p.work / "probe.json").read_text())["duration_s"] - 2.0) <= ONE_FRAME_S
    for name in ("audio48k.wav", "audio16k.wav"):
        assert abs(sf.info(p.work / name).duration - result["duration_s"]) <= ONE_FRAME_S


def test_prep_no_audio_errors(tmp_path):
    p = project_with(make_clip(tmp_path, audio=None))
    with pytest.raises(PipelineError, match="source has no audio track"):
        prep(p)
    assert not (p.work / "audio48k.wav").exists() and not (p.work / "probe.json").exists()


def test_prep_sdr_reads_original(tmp_path):
    clip = make_clip(tmp_path)
    p = project_with(clip)
    prep(p)
    pj = json.loads((p.work / "probe.json").read_text())
    assert pj["video_source"] == load_state(p)["source"]["video"] and not (p.work / "video.mp4").exists()


def test_prep_writes_probe_json(tmp_path):
    p = project_with(make_clip(tmp_path, seconds=3, vfr=True))
    prep(p)
    pj = json.loads((p.work / "probe.json").read_text())
    assert sorted(pj) == sorted(
        ["display_w", "display_h", "rotation", "fps_r", "fps_avg", "is_vfr", "color_transfer", "is_hdr",
         "duration_s", "audio_tracks", "video_source", "warnings"]
    )
    assert (pj["display_w"], pj["display_h"], pj["is_vfr"], pj["is_hdr"], pj["audio_tracks"]) == (
        1080, 1920, True, False, 1)
    assert abs(pj["duration_s"] - 3.0) < 0.05 and pj["warnings"] == []


def test_prep_duration_is_the_video_stream_duration(tmp_path):
    # The container lasts 3.017 s here because the delayed audio ends after the video does.
    p = project_with(make_clip(tmp_path, seconds=3, audio_delay=0.5))
    result = prep(p)
    assert probe(Path(load_state(p)["source"]["video"])).duration_s > 3.01
    assert json.loads((p.work / "probe.json").read_text())["duration_s"] == 3.0
    assert result["duration_s"] == 3.0
    # Audio three times as long as the video: the container follows the audio, duration_s the video.
    clip = _remux(make_clip(tmp_path, seconds=3), "short-video.mp4", video_options=("-t", "1.5"))
    p = project_with(clip)
    result = prep(p)
    assert abs(result["duration_s"] - probe(clip).video_duration_s) < 0.001 and result["duration_s"] < 1.6
    assert json.loads((p.work / "probe.json").read_text())["duration_s"] == result["duration_s"]
    assert abs(sf.info(p.work / "audio48k.wav").duration - result["duration_s"]) < 0.02


def test_surround_source_is_downmixed(tmp_path):
    clip = make_clip(tmp_path, seconds=3, audio="surround")  # 5.1, the tone in the centre channel only
    assert probe(clip).audio_channels == 6
    p = project_with(clip)
    prep(p)
    a48, a16 = sf.read(p.work / "audio48k.wav", always_2d=True)[0], sf.read(p.work / "audio16k.wav", always_2d=True)[0]
    assert a48.shape[1] == 2 and a16.shape[1] == 1
    # The downmix lowers the level (the tone peaks near 0.5 in the source, near 0.17 here), but both channels hear it.
    assert abs(a48[:, 0]).max() > 0.1 and abs(a48[:, 1]).max() > 0.1 and abs(a16).max() > 0.1


def test_audio_timestamp_gap(tmp_path):
    clip = make_clip(tmp_path, seconds=3, audio_gap=(1.0, 2.0))  # the tone is there from 0 to 1 s and from 2 to 3 s
    assert largest_audio_pts_step_s(clip) > 0.9  # the timestamps of the audio really do jump
    p = project_with(clip)
    result = prep(p)
    assert abs(sf.info(p.work / "audio48k.wav").duration - result["duration_s"]) <= ONE_FRAME_S
    data, rate = sf.read(p.work / "audio48k.wav", always_2d=True)

    def peak(start_s: float, end_s: float) -> float:
        return float(abs(data[round(start_s * rate):round(end_s * rate)]).max())

    assert peak(0.05, 0.95) > 0.3 and peak(2.05, 2.95) > 0.3  # the sound on both sides stays where it was
    assert peak(1.05, 1.95) < 0.001  # and the gap is silence
    assert abs(sf.info(p.work / "audio16k.wav").duration - result["duration_s"]) <= ONE_FRAME_S


def test_prep_hdr_makes_sdr_work_file(tmp_path):
    p = project_with(make_clip(tmp_path, w=1440, h=2560, hdr=True))
    prep(p)
    out = probe(p.work / "video.mp4")
    assert not out.is_hdr and (out.display_w, out.display_h) == (1350, 2400)
    assert out.audio_tracks == 0
    assert json.loads((p.work / "probe.json").read_text())["video_source"] == str(p.work / "video.mp4")


def test_prep_hdr_work_file_is_tagged_bt709(tmp_path):
    p = project_with(make_clip(tmp_path, w=1440, h=2560, hdr=True))
    prep(p)
    assert probe(p.work / "video.mp4").color_transfer == "bt709"


def test_prep_hdr_does_not_enlarge_small_sources(tmp_path):
    p = project_with(make_clip(tmp_path, w=1080, h=1920, hdr=True))
    prep(p)
    out = probe(p.work / "video.mp4")
    assert (out.display_w, out.display_h) == (1080, 1920)


def test_prep_hdr_applies_rotation_before_scaling(tmp_path):
    p = project_with(make_clip(tmp_path, w=2560, h=1440, hdr=True, rotate=90))  # displays as 1440x2560
    prep(p)
    out = probe(p.work / "video.mp4")
    assert (out.display_w, out.display_h, out.rotation) == (1350, 2400, 0)


def test_prep_warns_non_portrait(tmp_path):
    p = project_with(make_clip(tmp_path, w=1920, h=1080))
    r = prep(p)
    assert any("center-cropped" in w for w in r["warnings"])
    assert "aspect 16:9 will be center-cropped to 9:16" in r["warnings"]
    assert json.loads((p.work / "probe.json").read_text())["warnings"] == r["warnings"]


@pytest.mark.parametrize("model", ["absent", "wrong_content"])
def test_prep_checks_the_face_model_first(tmp_path, monkeypatch, model):
    models = tmp_path / "no_models"  # stands in for a machine where `pipeline setup` has not run
    models.mkdir()
    if model == "wrong_content":
        (models / YUNET_FILENAME).write_bytes(b"not the face model")
    monkeypatch.setattr("pipeline.assets.models_dir", lambda: models)
    p = project_with(make_clip(tmp_path))
    before, steps = _files_under(p.work), load_state(p)["steps"]
    ffmpeg_runs: list[list[str]] = []

    def counting_runner(args, log, cwd=None):
        ffmpeg_runs.append(args)
        run_ffmpeg(args, log, cwd)

    monkeypatch.setattr("pipeline.prep.run_ffmpeg", counting_runner)
    with pytest.raises(PipelineError) as raised:
        prep(p)
    assert raised.value.message == "face model missing: run `uv run pipeline setup`"
    assert ffmpeg_runs == []
    assert _files_under(p.work) == before and load_state(p)["steps"] == steps


def test_failed_encode_leaves_nothing(tmp_path, monkeypatch):
    p = project_with(make_clip(tmp_path))
    tools, outputs = tmp_path / "stub_tools", tmp_path / "ffmpeg_outputs.txt"
    tools.mkdir()
    # The stub ffmpeg writes some bytes to its last argument, the file it was asked to make, and fails.
    (tools / "ffmpeg").write_text(
        f'#!/bin/sh\nfor last; do :; done\necho "$last" >> "{outputs}"\necho partial > "$last"\n'
        'echo "stub ffmpeg: encode failed" >&2\nexit 1\n'
    )
    (tools / "ffprobe").write_text(f'#!/bin/sh\nexec "{ffprobe_bin()}" "$@"\n')  # the real one, read before the switch
    for tool in tools.iterdir():
        tool.chmod(0o755)
    monkeypatch.setenv("REELS_FFMPEG_DIR", str(tools))

    with pytest.raises(PipelineError, match="ffmpeg failed with exit code 1: stub ffmpeg: encode failed"):
        prep(p)
    attempted = [Path(line) for line in outputs.read_text().splitlines()]
    assert len(attempted) == 1 and ".part" in attempted[0].name  # the stub did leave a partial file for prep to remove
    assert [name for name in _files_under(p.work) if ".part" in name] == []
    assert not (p.work / "audio48k.wav").exists() and not (p.work / "probe.json").exists()
    assert "prep" not in load_state(p)["steps"]


def test_prep_leaves_inbox_untouched(tmp_path):
    clip = make_clip(tmp_path)
    p = project_with(clip)
    src = Path(load_state(p)["source"]["video"])
    before = file_sha256(src)
    prep(p)
    assert file_sha256(src) == before
    assert sorted(path.name for path in src.parent.iterdir()) == sorted([src.name, "script.md"])


def test_prep_second_run_is_skipped_and_still_reports(tmp_path):
    p = project_with(make_clip(tmp_path, seconds=3))
    first = prep(p)
    second = prep(p)
    assert first["skipped"] is False and second["skipped"] is True
    assert {k: v for k, v in second.items() if k != "skipped"} == {k: v for k, v in first.items() if k != "skipped"}
    assert set(first) == {"video_source", "is_hdr", "is_vfr", "duration_s", "warnings", "skipped"}
    assert prep(p, force=True)["skipped"] is False


def test_prep_records_step_outputs(tmp_path):
    p = project_with(make_clip(tmp_path, w=1440, h=2560, hdr=True))
    prep(p)
    record = load_state(p)["steps"]["prep"]
    assert sorted(record["outputs"]) == [
        "work/audio16k.wav", "work/audio48k.wav", "work/faces.json", "work/probe.json", "work/video.mp4",
    ]
    assert record["inputs"]["video"]["path"] == load_state(p)["source"]["video"]


def test_prep_drops_a_stale_work_file_when_the_source_becomes_sdr(tmp_path):
    p = project_with(make_clip(tmp_path, w=1440, h=2560, hdr=True))
    prep(p)
    assert (p.work / "video.mp4").exists()
    Path(load_state(p)["source"]["video"]).write_bytes(make_clip(tmp_path / "sdr").read_bytes())
    prep(p)
    assert not (p.work / "video.mp4").exists()
    assert not probe(Path(json.loads((p.work / "probe.json").read_text())["video_source"])).is_hdr


def test_prep_command_prints_json(tmp_path, capsys):
    p = project_with(make_clip(tmp_path, seconds=3))
    assert main(["prep", p.name]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True and out["skipped"] is False and out["is_hdr"] is False
    assert out["video_source"] == load_state(p)["source"]["video"] and abs(out["duration_s"] - 3.0) < 0.05
    assert main(["prep", p.name]) == 0
    assert json.loads(capsys.readouterr().out)["skipped"] is True
    assert main(["prep", p.name, "--force"]) == 0
    assert json.loads(capsys.readouterr().out)["skipped"] is False
