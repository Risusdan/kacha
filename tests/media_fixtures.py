"""Synthetic test clips built with ffmpeg's lavfi sources, cached under tests/.cache/.

make_clip never touches real footage: the picture is ffmpeg's testsrc2 pattern (or a uniform gray
that counts frames) and the sound is a sine tone.
"""

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf

from pipeline.media import ffmpeg_bin, ffprobe_bin, run_ffmpeg

CACHE = Path(__file__).resolve().parent / ".cache"
RECIPE_VERSION = 2  # bump when a recipe below changes, so cached clips are rebuilt
AUDIO_KINDS = ("tone440", "mono", "two_tracks", "surround", None)
FPS = 30
HLG_TAGS = "setparams=color_primaries=bt2020:color_trc=arib-std-b67:colorspace=bt2020nc:range=tv"


def _sine(hz: int, seconds: float) -> str:
    # `sine` is quiet (-18 dBFS); volume=4 brings it to -6 dBFS so level thresholds see it clearly.
    return f"sine=frequency={hz}:sample_rate=48000:duration={seconds},volume=4"


def _video_source(w: int, h: int, seconds: float, gray_counter: bool, vfr: bool) -> str:
    if gray_counter:
        # Frame N is uniform gray with luma 16 + 4 * (N % 50). Built tiny, then scaled up, because
        # geq evaluates its expression for every pixel.
        graph = (
            f"color=c=black:size=16x16:rate={FPS}:duration={seconds},"
            "geq=lum='16+4*mod(N,50)':cb=128:cr=128,"
            f"scale={w}:{h}:flags=neighbor"
        )
    else:
        graph = f"testsrc2=size={w}x{h}:rate={FPS}:duration={seconds}"
    if vfr:
        graph += ",select='not(eq(mod(n,4),1))'"  # drop every 4th frame, keeping the original timestamps
    return graph


def _audio_source(audio: str, hz: int, seconds: float, audio_gap: tuple[float, float] | None) -> str:
    graph = _sine(hz, seconds)
    if audio == "surround":
        graph += ",pan=5.1|FC=c0"  # the tone in the centre channel only; the other five are silent
    if audio_gap:
        # The audio frames that start inside the gap are dropped and the rest keep their timestamps,
        # so the timestamps of the stream jump over the gap.
        graph += f",aselect='lt(t,{audio_gap[0]})+gte(t,{audio_gap[1]})'"
    return graph


def _build(out: Path, log: Path, *, w, h, seconds, audio, hdr, rotate, vfr, audio_delay, gray_counter,
           video_delay=0.0, audio_gap=None) -> None:
    video = _video_source(w, h, seconds, gray_counter, vfr)
    args = ["-y", "-hide_banner", "-loglevel", "error"]
    if video_delay:
        args += ["-itsoffset", str(video_delay)]
    args += ["-f", "lavfi", "-i", video]
    tones = {"tone440": [440], "mono": [440], "two_tracks": [440, 1000], "surround": [440], None: []}[audio]
    for hz in tones:
        if audio_delay:
            args += ["-itsoffset", str(audio_delay)]
        args += ["-f", "lavfi", "-i", _audio_source(audio, hz, seconds, audio_gap)]
    args += ["-map", "0:v"]
    for index in range(len(tones)):
        args += ["-map", f"{index + 1}:a"]

    if hdr:
        # With ffmpeg 9.0.2 the -color_* options alone leave the transfer "unknown" in the file (the
        # frames coming out of the filter graph carry their own, unspecified, properties), so
        # setparams stamps the HLG / BT.2020 tags on the frames as well.
        args += [
            "-vf", "format=yuv420p10le," + HLG_TAGS,
            "-c:v", "libx265", "-preset", "ultrafast", "-crf", "28", "-pix_fmt", "yuv420p10le", "-tag:v", "hvc1",
            "-x265-params", "log-level=error",
            "-color_trc", "arib-std-b67", "-color_primaries", "bt2020", "-colorspace", "bt2020nc",
        ]
    else:
        args += [
            "-vf", "format=yuv420p",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        ]
    if vfr:
        args += ["-fps_mode", "passthrough"]
    if tones:
        channels = {"mono": 1, "surround": 6}.get(audio, 2)
        args += ["-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", str(channels)]
    args += ["-t", str(seconds + video_delay)]  # -t counts on the output timeline, which the delay shifts

    if not rotate:
        run_ffmpeg([*args, str(out)], log)
        return
    # The rotation is display-matrix metadata, not re-encoded pixels: write the unrotated clip,
    # then copy its streams with the rotation set on the input.
    unrotated = out.with_name(out.stem + ".unrotated.mp4")
    run_ffmpeg([*args, str(unrotated)], log)
    try:
        run_ffmpeg(["-y", "-hide_banner", "-loglevel", "error", "-display_rotation", str(rotate),
                    "-i", str(unrotated), "-map", "0", "-c", "copy", str(out)], log)
    finally:
        unrotated.unlink(missing_ok=True)


def make_clip(
    dest_dir: Path,
    *,
    w: int = 1080,
    h: int = 1920,
    seconds: float = 2.0,
    audio: str | None = "tone440",
    hdr: bool = False,
    rotate: int = 0,
    vfr: bool = False,
    audio_delay: float = 0.0,
    gray_counter: bool = False,
    video_delay: float = 0.0,
    audio_gap: tuple[float, float] | None = None,
) -> Path:
    """A small synthetic MP4 in `dest_dir`; the video is built once and cached under tests/.cache/.

    w, h: encoded size. With `rotate` 90 the display size is h x w (rotation is display-matrix
    metadata, so a 1920x1080 clip with rotate=90 displays as 1080x1920).
    audio: "tone440" (stereo 440 Hz), "mono" (one channel), "two_tracks" (track 0 is 440 Hz, track 1
        is 1000 Hz), "surround" (5.1 channels, the 440 Hz tone in the centre channel only) or None (no
        audio stream).
    hdr: HEVC 10-bit tagged HLG / BT.2020.
    vfr: every 4th frame dropped, so the frame rate varies.
    audio_delay: the audio stream starts this many seconds after the video.
    gray_counter: frame N is uniform gray with luma 16 + 4 * (N % 50), for checking which frame is where.
    video_delay: the video stream starts this many seconds after 0 (and still holds `seconds` of picture).
    audio_gap: (start, end) in seconds: the audio of that stretch is left out and the timestamps of the
        audio stream jump from `start` to `end` (to the nearest 21 ms audio frame); the file is as long as before.
    """
    if audio not in AUDIO_KINDS:
        raise ValueError(f"audio must be one of {AUDIO_KINDS}, got {audio!r}")
    if rotate % 90:
        raise ValueError(f"rotate must be a multiple of 90, got {rotate}")
    if (audio_delay or audio_gap) and audio is None:
        raise ValueError("audio_delay and audio_gap need an audio track")
    params = dict(w=w, h=h, seconds=seconds, audio=audio, hdr=hdr, rotate=rotate, vfr=vfr,
                  audio_delay=audio_delay, gray_counter=gray_counter)
    if video_delay:  # left out when 0, so the clips cached before this option existed keep their names
        params["video_delay"] = video_delay
    if audio_gap:  # same
        params["audio_gap"] = list(audio_gap)
    key = hashlib.sha256(json.dumps([RECIPE_VERSION, params], sort_keys=True).encode()).hexdigest()[:16]
    cached = CACHE / f"clip-{key}.mp4"
    if not cached.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        partial, log = CACHE / f"clip-{key}.partial.mp4", CACHE / f"clip-{key}.log"
        _build(partial, log, **params)
        partial.replace(cached)
        if log.stat().st_size == 0:  # ffmpeg said nothing, so there is nothing to keep (a failed build keeps its log)
            log.unlink()
    dest_dir.mkdir(parents=True, exist_ok=True)
    clip = dest_dir / cached.name
    shutil.copyfile(cached, clip)
    return clip


def make_mkv(dest_dir: Path, *, seconds: float = 2.0, audio_seconds: float = 3.0, video_delay: float = 0.0) -> Path:
    """A small 540x960 Matroska file in `dest_dir`, built on every call (it is not cached): `seconds` of the
    test pattern and `audio_seconds` of a stereo 440 Hz tone. ffmpeg writes no duration for the streams of a
    Matroska file (only a DURATION tag), so ffprobe reports none for the video stream, and the container
    lasts until the longer audio ends. `video_delay` starts the picture that many seconds after 0."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = dest_dir / "no-stream-duration.mkv"
    delay = ["-itsoffset", str(video_delay)] if video_delay else []
    run_ffmpeg(
        ["-y", "-hide_banner", "-loglevel", "error",
         *delay, "-f", "lavfi", "-i", f"testsrc2=size=540x960:rate={FPS}:duration={seconds}",
         "-f", "lavfi", "-i", _sine(440, audio_seconds),
         "-map", "0:v", "-map", "1:a", "-vf", "format=yuv420p",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2", str(out)],
        dest_dir / f"{out.name}.log",
    )
    return out


def video_stream_duration(path: Path) -> float | None:
    """The `duration` that ffprobe reports for the first video stream of `path`; None when it reports none."""
    done = subprocess.run(
        [ffprobe_bin(), "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=duration",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )
    value = json.loads(done.stdout)["streams"][0].get("duration")
    return None if value in (None, "N/A") else float(value)


def largest_audio_pts_step_s(path: Path) -> float:
    """The largest step, in seconds, between the timestamps of two neighbouring packets of the first audio
    stream of `path`: 0.021 (one AAC frame) while the timestamps run on, more where they jump."""
    done = subprocess.run(
        [ffprobe_bin(), "-v", "error", "-select_streams", "a:0", "-show_entries", "packet=pts_time",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )
    times = sorted(float(packet["pts_time"]) for packet in json.loads(done.stdout)["packets"])
    return max(later - earlier for earlier, later in zip(times, times[1:]))


def still_to_video(image: Path, dest_dir: Path, seconds: float = 2.0) -> Path:
    """A portrait 1080x1920, 30 fps clip in `dest_dir` that shows `image` for `seconds`, with a
    stereo 440 Hz track so prep can run on it. The picture is scaled to fit inside the frame, keeping
    its aspect ratio, and the rest is black."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = dest_dir / f"still-{image.stem}.mp4"
    fit = "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black"
    run_ffmpeg(
        ["-y", "-hide_banner", "-loglevel", "error",
         "-loop", "1", "-framerate", str(FPS), "-t", str(seconds), "-i", str(image),
         "-f", "lavfi", "-i", _sine(440, seconds),
         "-vf", f"{fit},format=yuv420p", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
         "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2", "-t", str(seconds), str(out)],
        dest_dir / f"{out.name}.log",
    )
    return out


def with_audio(video: Path, wav: Path, dest_dir: Path, audio_delay: float = 0.0) -> Path:
    """The picture of `video` (copied, not re-encoded) with `wav` as its only sound track, AAC 48 kHz
    stereo, as `<video stem>-<wav stem>.mp4` in `dest_dir`. `audio_delay` starts the sound that many
    seconds after 0: the delay of a video made with make_clip(video_delay=...), so that picture and
    sound stay together."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = dest_dir / f"{video.stem}-{wav.stem}.mp4"
    shift = ["-copyts", "-itsoffset", str(audio_delay)] if audio_delay else []  # -copyts keeps the video's own start
    run_ffmpeg(
        ["-y", "-hide_banner", "-loglevel", "error", "-i", str(video), *shift, "-i", str(wav),
         "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
         str(out)],
        dest_dir / f"{out.name}.log",
    )
    return out


def to_16k_mono(src: Path, dest_dir: Path) -> Path:
    """`src` as a 16 kHz mono 16-bit WAV named `<stem>-16k.wav` in `dest_dir`: the form `pipeline prep`
    hands the ASR engine."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = dest_dir / f"{src.stem}-16k.wav"
    run_ffmpeg(
        ["-y", "-hide_banner", "-loglevel", "error", "-i", str(src),
         "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(out)],
        dest_dir / f"{out.name}.log",
    )
    return out


def frame_luma(video: Path, n: int) -> float:
    """Mean of the Y plane (raw 0-255 values, no range conversion) of decoded frame number `n`."""
    done = subprocess.run(
        [ffmpeg_bin(), "-nostdin", "-v", "error", "-i", str(video),
         "-vf", f"select=eq(n\\,{n}),extractplanes=y", "-frames:v", "1", "-fps_mode", "passthrough",
         "-f", "rawvideo", "-"],
        capture_output=True, check=True,
    )
    return float(np.frombuffer(done.stdout, dtype=np.uint8).mean())


def dominant_hz(wav: Path) -> float:
    """The frequency with the strongest FFT peak in the first channel of `wav`."""
    data, rate = sf.read(str(wav), dtype="float32", always_2d=True)
    samples = data[:, 0] * np.hanning(len(data))
    spectrum = np.abs(np.fft.rfft(samples))
    return float(np.argmax(spectrum) * rate / len(samples))


def _loud_samples(wav: Path, threshold: float) -> tuple[np.ndarray, int]:
    """(indices of the samples whose absolute value, in any channel, exceeds `threshold`, sample rate) of `wav`.
    Raises ValueError when the file is silent."""
    data, rate = sf.read(str(wav), dtype="float32", always_2d=True)
    loud = np.flatnonzero(np.abs(data).max(axis=1) > threshold)
    if not len(loud):
        raise ValueError(f"no sound above {threshold} in {wav}")
    return loud, rate


def first_sound_s(wav: Path, threshold: float = 0.05) -> float:
    """Seconds into `wav` of the first sample whose absolute value, in any channel, exceeds `threshold`
    (full scale is 1.0; the test tones peak near 0.5). Raises ValueError when the file is silent."""
    loud, rate = _loud_samples(wav, threshold)
    return float(loud[0] / rate)


def last_sound_s(wav: Path, threshold: float = 0.05) -> float:
    """Seconds into `wav` just after the last sample whose absolute value, in any channel, exceeds
    `threshold`. Raises ValueError when the file is silent."""
    loud, rate = _loud_samples(wav, threshold)
    return float((loud[-1] + 1) / rate)
