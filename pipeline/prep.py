"""Prep: what the later steps read from a source video.

Writes work/probe.json, work/audio48k.wav (stereo, for the render), work/audio16k.wav (mono, for
speech recognition), work/faces.json (the face boxes the punch-in of the render is anchored on, see
pipeline.punch, and the animation cards are kept off, see pipeline.card_place) and, for HDR sources only,
work/video.mp4 (an SDR copy). Both wavs start at the first video frame and last as long as the video stream, so a
time in a wav is the same time in the video.
"""

import json
import os
from dataclasses import asdict
from fractions import Fraction
from pathlib import Path

from pipeline.assets import yunet_ok, yunet_path
from pipeline.errors import PipelineError
from pipeline.faces import MODEL_NAME, detect_faces, sample_times
from pipeline.media import Probe, probe, run_ffmpeg
from pipeline.project import Project, load_state, run_step

RATE_48K = 48000
RATE_16K = 16000
WORK_W, WORK_H = 1350, 2400  # an HDR source is shrunk to fit this box; a smaller one is left alone
WORK_BITRATE = "30M"
# The tone mapping chain measured in phase 0 (docs/spikes/2026-11-11-phase0-results.md, section 4).
TONEMAP = (
    "zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=hable:desat=0,"
    "zscale=t=bt709:m=bt709:r=tv,format=yuv420p"
)
# The box is min(source, WORK_W x WORK_H), so a source that already fits is not enlarged. Sizes stay
# even because yuv420p needs that.
SCALE = (
    f"scale=w='min(iw,{WORK_W})':h='min(ih,{WORK_H})':"
    "force_original_aspect_ratio=decrease:force_divisible_by=2:flags=lanczos"
)
QUIET = ["-y", "-hide_banner", "-loglevel", "error"]


def _audio48k_args(source: Path, info: Probe, dest: Path) -> list[str]:
    """Extract the first audio track as 48 kHz stereo, aligned to the first video frame.

    -copyts keeps the timestamps of the source, so asetpts can move the audio timeline to the video
    stream's start; aresample then pads the beginning with silence when the audio starts after the
    first frame and cuts it when the audio starts before it. The tail is padded or cut to the length
    of the video stream. The counts are in samples, because the filters work on samples."""
    total = round(info.video_duration_s * RATE_48K)
    chain = ",".join(
        [
            f"asetpts=PTS-{info.video_start_s:.6f}/TB",
            f"aresample={RATE_48K}:async=1:first_pts=0",
            f"apad=whole_len={total}",
            f"atrim=end_sample={total}",
        ]
    )
    return [
        *QUIET, "-copyts", "-i", str(source), "-map", "0:a:0", "-af", chain,
        "-ac", "2", "-c:a", "pcm_s16le", str(dest),
    ]


def _audio16k_args(audio48k: Path, dest: Path) -> list[str]:
    return [*QUIET, "-i", str(audio48k), "-ac", "1", "-ar", str(RATE_16K), "-c:a", "pcm_s16le", str(dest)]


def _work_video_args(source: Path, dest: Path) -> list[str]:
    return [
        *QUIET, "-i", str(source), "-map", "0:v:0", "-an", "-vf", f"{SCALE},{TONEMAP}",
        "-c:v", "hevc_videotoolbox", "-b:v", WORK_BITRATE, "-tag:v", "hvc1", str(dest),
    ]


def _warnings(info: Probe) -> list[str]:
    ratio = Fraction(info.display_w, info.display_h)
    if ratio == Fraction(9, 16):
        return []
    return [f"aspect {ratio.numerator}:{ratio.denominator} will be center-cropped to 9:16"]


def _part(path: Path) -> Path:
    """The file ffmpeg writes while it makes `path`: `audio48k.wav` is made as `audio48k.part.wav`."""
    return path.with_suffix(".part" + path.suffix)


def _encode(args: list[str], log: Path, part: Path, dest: Path) -> None:
    """Run ffmpeg with `args`, which make the file `part`, then move `part` to `dest`. A failed run
    leaves no `part` behind."""
    try:
        run_ffmpeg(args, log)
        os.replace(part, dest)
    finally:
        part.unlink(missing_ok=True)


def _write_faces(dest: Path, video: Path, duration_s: float) -> None:
    """Find the faces in `video`, a frame every couple of seconds, and write them to `dest`."""
    times = sample_times(duration_s)
    boxes = detect_faces(video, yunet_path(), times)
    document = {
        "model": MODEL_NAME,
        "samples": len(times),
        "boxes": [{key: round(value, 4) for key, value in asdict(box).items()} for box in boxes],
    }
    dest.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def _summary(probe_json: dict) -> dict:
    return {key: probe_json[key] for key in ("video_source", "is_hdr", "is_vfr", "duration_s", "warnings")}


def prep(p: Project, force: bool = False) -> dict:
    """Run the prep step for `p`. Returns video_source, is_hdr, is_vfr, duration_s, warnings and
    skipped (True when nothing has changed since the last run). Raises PipelineError, before anything
    is run, when the face model is missing or is not the pinned file."""
    if not yunet_ok():
        raise PipelineError("face model missing: run `uv run pipeline setup`")
    source = Path(load_state(p)["source"]["video"])
    info = probe(source)
    if info.audio_tracks == 0:
        raise PipelineError("source has no audio track")

    probe_path, faces_path = p.work / "probe.json", p.work / "faces.json"
    audio48k, audio16k, work_video = p.work / "audio48k.wav", p.work / "audio16k.wav", p.work / "video.mp4"
    outputs = [probe_path, faces_path, audio48k, audio16k] + ([work_video] if info.is_hdr else [])

    def run() -> dict:
        part48, part16, part_video = _part(audio48k), _part(audio16k), _part(work_video)
        _encode(_audio48k_args(source, info, part48), p.work / "prep-audio48k.log", part48, audio48k)
        _encode(_audio16k_args(audio48k, part16), p.work / "prep-audio16k.log", part16, audio16k)
        if info.is_hdr:
            _encode(_work_video_args(source, part_video), p.work / "prep-video.log", part_video, work_video)
        else:
            work_video.unlink(missing_ok=True)  # left over from an earlier, HDR, version of the source
        result = {
            "display_w": info.display_w,
            "display_h": info.display_h,
            "rotation": info.rotation,
            "fps_r": info.fps_r,
            "fps_avg": info.fps_avg,
            "is_vfr": info.is_vfr,
            "color_transfer": info.color_transfer,
            "is_hdr": info.is_hdr,
            "duration_s": info.video_duration_s,  # the length the wavs have too, not the container's
            "audio_tracks": info.audio_tracks,
            "video_source": str(work_video if info.is_hdr else source),
            "warnings": _warnings(info),
        }
        probe_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        _write_faces(faces_path, Path(result["video_source"]), result["duration_s"])
        return _summary(result)

    done = run_step(p, "prep", {"video": source}, outputs, run, force=force)
    if done["skipped"]:
        done = {**_summary(json.loads(probe_path.read_text(encoding="utf-8"))), "skipped": True}
    return done
