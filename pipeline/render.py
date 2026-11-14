"""The final render: cut the source on the frame grid, burn in the captions, check the file, name it final.mp4.

render() refuses unless gate 1 was approved for the storyboard as it stands now (project.gate_ok). It then works in
work/render/: captions.ass, the two static fonts in fonts/, cut.wav (the audio cut at the segments, with a
short fade at every join), voice.wav (cut.wav through the voice treatment of pipeline.voice: cleaned up and brought
to -14 LUFS; its log is voice.log), the filter script, render.log. ffmpeg runs in that folder, so the graph names
only captions.ass and fonts (a studio path with a space or a quote cannot break it), and writes
out/final.tmp.mp4; that file is renamed to out/final.mp4 only after check_output and check_fontselect found
nothing wrong. ffmpeg gets two attempts. Each attempt overwrites render.log, so the file holds the log of the
last one. A render that runs removes the old out/final.mp4 first, so no failure leaves the file of an earlier
edit, and final.tmp.mp4 does not stay behind on a failure either.

The picture is cut by frame number: the leading setpts makes the first video frame time 0, which is also second 0
of work/audio48k.wav, fps=30 puts the frames on the grid the segments are on (and makes the pts of a frame its
number), and select keeps the frames whose number is inside a segment.

The captions sit at the fixed top caption_top_px gives (the style's captions.bottom_px less its line height), whatever
the picture shows, so the render reads no faces.json.

The video bitrate is 12 Mbps for a reel up to about 3 minutes and lower for a longer one, so that the file stays
under 300 MB (see video_bitrate); the audio is voice.wav as AAC at 192 kbit/s.

After the final, render makes out/preview.mp4 (540x960, under 30 MiB) from it as a step of its own (see make_preview):
a copy small enough to send in a chat, to look at, not to publish. It does not stand in the way of the render: when it
cannot be made, final.mp4 is still the result and the result carries a warning.
"""

import math
import os
import shutil
from collections.abc import Callable
from fractions import Fraction
from pathlib import Path

import numpy as np
import soundfile as sf

from pipeline.captions import build_events, caption_top_px, missing_glyphs, write_ass
from pipeline.edit import Segment, build_segments, load_document, require_valid, total_duration
from pipeline.errors import PipelineError
from pipeline.media import FFPROBE_TIMEOUT_S, ffprobe_json, run_ffmpeg
from pipeline.project import Project, first_stale_step, gate_ok, run_step
from pipeline.style import font_path, load_style
from pipeline.voice import treat_voice
from pipeline.words import caption_inputs

FADE_S = 0.01  # the fade at each end of a segment of the audio
ATTEMPTS = 2
WIDTH, HEIGHT, FPS = 1080, 1920, 30
AUDIO_BITRATE = 192_000  # bit/s; -b:a is given _ffmpeg_rate(AUDIO_BITRATE)
# The output spec (spec 8.7) check_output holds the file to.
MIN_AUDIO_BITRATE = 128_000
MAX_BITRATE = 25_000_000
MAX_BYTES = 300_000_000
# The video bitrate follows the length of the reel so the file stays under MAX_BYTES: MAX_VIDEO_BITRATE up to
# about 3 minutes, lower for a longer reel, and a reel that would need less than MIN_VIDEO_BITRATE is refused.
# SIZE_BUDGET (95 % of MAX_BYTES, the rest being the container overhead and what the encoder overshoots its
# target by) and MIN_VIDEO_BITRATE (4 Mbps) are initial values, to adjust after measurement.
SIZE_BUDGET = 0.95 * MAX_BYTES
MAX_VIDEO_BITRATE = 12_000_000
MIN_VIDEO_BITRATE = 4_000_000
VIDEO_ENCODER = "h264_videotoolbox"  # the H.264 encoder of the final, and of the preview made from it
# The phone preview (spec 7): out/preview.mp4, a copy of the final small enough to send in a chat. Its video
# bitrate is worked out from PREVIEW_BUDGET (90 % of PREVIEW_MAX_BYTES, 30 MiB; the rest being the container overhead
# and what the encoder overshoots its target by), capped at PREVIEW_MAX_VIDEO_BITRATE; a file that still comes out
# over PREVIEW_MAX_BYTES is encoded once more at a lower rate, and one that is over it again is not kept.
PREVIEW_W, PREVIEW_H = 540, 960
PREVIEW_AUDIO_BITRATE = 96_000  # bit/s; -b:a is given _ffmpeg_rate(PREVIEW_AUDIO_BITRATE)
PREVIEW_MAX_BYTES = 31_457_280
PREVIEW_BUDGET = 0.9
PREVIEW_MAX_VIDEO_BITRATE = 2_000_000
PREVIEW_OVER = "preview over 30 MiB: not kept"
LENGTH_TOLERANCE_S = 1 / FPS + 0.02  # the video against the length the segments add up to
SYNC_TOLERANCE_S = 0.05  # the audio against the video
FONT_PREFIX = "NotoSansTC-"  # what libass must report choosing: the static fonts it was given


# ---- the audio ---------------------------------------------------------------------------------


def cut_audio(src_wav: Path, segments: list[Segment], out_wav: Path, fade_s: float = FADE_S) -> float:
    """Write the audio of `segments` (source seconds on the frame grid) one after the other to `out_wav`
    and return its length in seconds. The cut is by sample, so a frame is exactly rate / 30 samples (1600
    at 48 kHz). Each segment fades in and out linearly over `fade_s` (less when it is shorter than two
    fades), so a join does not click. The result is 16-bit PCM of the source's rate and channels."""
    try:
        data, rate = sf.read(str(src_wav), dtype="float32", always_2d=True)
    except (OSError, sf.LibsndfileError) as error:
        raise PipelineError(f"cannot read {src_wav}: {error}") from error
    fade = round(fade_s * rate)
    pieces = []
    for segment in segments:
        lo, hi = round(segment.start * rate), round(segment.end * rate)
        if lo < 0 or hi > len(data) or hi <= lo:
            raise PipelineError(
                f"segment {segment.start:.3f}-{segment.end:.3f}s is not inside the audio of {src_wav} "
                f"({len(data) / rate:.3f}s)"
            )
        piece = data[lo:hi].copy()
        n = min(fade, len(piece) // 2)
        if n:
            ramp = (np.arange(n, dtype=np.float32) / n)[:, None]
            piece[:n] *= ramp
            piece[-n:] *= ramp[::-1]
        pieces.append(piece)
    cut = np.concatenate(pieces) if pieces else data[:0]
    try:
        sf.write(str(out_wav), cut, rate, subtype="PCM_16")
    except (OSError, sf.LibsndfileError) as error:
        raise PipelineError(f"cannot write {out_wav}: {error}") from error
    return len(cut) / rate


# ---- the video bitrate -------------------------------------------------------------------------


def video_bitrate(duration_s: float) -> int:
    """The video bitrate in bit/s for a reel of `duration_s` seconds: MAX_VIDEO_BITRATE, or less for a reel so
    long that video at that rate plus the audio (AUDIO_BITRATE) would weigh more than SIZE_BUDGET bytes,
    rounded down to a whole kbit/s. Raises PipelineError, naming the length and the limit, for a reel
    that would need less than MIN_VIDEO_BITRATE."""
    fitting = math.floor(SIZE_BUDGET * 8 / duration_s) - AUDIO_BITRATE
    rate = min(MAX_VIDEO_BITRATE, fitting) // 1000 * 1000
    if rate < MIN_VIDEO_BITRATE:
        raise PipelineError(
            f"reel is {duration_s:.1f} s; at the {MAX_BYTES // 1_000_000} MB limit the video bitrate would fall "
            f"below {MIN_VIDEO_BITRATE // 1_000_000} Mbps"
        )
    return rate


# ---- the picture -------------------------------------------------------------------------------


def _balanced_sum(terms: list[str]) -> str:
    """`terms` added up as a balanced tree: the halves are parenthesised groups, a half of one term is the term
    itself, so one term stays as it is and two terms are `a+b`."""
    if len(terms) == 1:
        return terms[0]
    middle = len(terms) // 2
    halves = (terms[:middle], terms[middle:])
    return "+".join(half[0] if len(half) == 1 else f"({_balanced_sum(half)})" for half in halves)


def select_expr(segments: list[Segment]) -> str:
    """The select filter's expression that keeps the frames inside a segment: gte(pts,A)*lt(pts,B) for each,
    added up, with A and B the numbers of the segment's first frame and of the frame after its last (the nearest
    frame for a time that is not on the grid). Frames are chosen by number, not by time: after fps=30 the pts of a
    frame is its number (the time base is 1/30), so a boundary is an integer, where a boundary written as a time
    can lie a rounding error away from the time ffmpeg works out for the same frame and drop or add a frame.

    The terms are added up as a balanced tree (see _balanced_sum) because ffmpeg's expression parser reads
    `a+b+c` as `(a+b)+c` and fails on a tree about 100 deep: measured with ffmpeg 9.0.2, 99 terms in a chain
    work and 100 fail with "Cannot allocate memory". A balanced tree is about log2(n) deep; 20000 terms ran
    (and 400000 when the graph is read from a file, as the render does), so the expression length is no limit."""
    terms = [f"gte(pts,{round(s.start * FPS)})*lt(pts,{round(s.end * FPS)})" for s in segments]
    return _balanced_sum(terms) if terms else ""


def _ffmpeg_rate(bits_per_s: int) -> str:
    """`bits_per_s` the way ffmpeg reads it: in whole kilobits, always spelled with k, whatever the rate (ffmpeg's
    k is 1000), so 12 Mbit/s is 12000k."""
    return f"{bits_per_s // 1000}k"


def build_ffmpeg_args(
    video_src: Path, sound_wav: Path, segments: list[Segment], out: Path, filter_script: Path
) -> list[str]:
    """Write the filter graph to `filter_script` and return the ffmpeg arguments that render `out` from the
    picture of `video_src` and the sound of `sound_wav` (already cut, and treated: voice.wav). The graph names
    captions.ass and fonts relative to the folder ffmpeg runs in. A frame rate of 30 and one scale to cover 1080x1920 (every source is scaled
    once). The video bitrate is the one video_bitrate gives for the length the segments add up to; it is worked
    out first, so a reel too long for MIN_VIDEO_BITRATE raises before the script is written, as does a list of no
    segments."""
    if not segments:
        raise PipelineError("nothing to render: the reel has no segments")
    rate = video_bitrate(total_duration(segments))
    graph = (
        f"[0:v]setpts=PTS-STARTPTS,fps={FPS},select='{select_expr(segments)}',setpts=N/{FPS}/TB,"
        f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,crop={WIDTH}:{HEIGHT},"
        "subtitles=filename=captions.ass:fontsdir=fonts,format=yuv420p[v]"
    )
    Path(filter_script).write_text(graph, encoding="utf-8")
    return [
        "-hide_banner", "-loglevel", "verbose", "-y", "-i", str(video_src), "-i", str(sound_wav),
        "-/filter_complex", str(filter_script), "-map", "[v]", "-map", "1:a",
        "-c:v", VIDEO_ENCODER, "-b:v", _ffmpeg_rate(rate), "-profile:v", "high", "-r", str(FPS),
        "-fps_mode", "cfr", "-tag:v", "avc1",
        "-c:a", "aac", "-b:a", _ffmpeg_rate(AUDIO_BITRATE), "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart", str(out),
    ]


# ---- the checks --------------------------------------------------------------------------------


def _ffprobe(path: Path) -> dict:
    """The streams and the format of `path` as ffprobe reports them. Raises PipelineError when it cannot be read, or
    when ffprobe takes longer than FFPROBE_TIMEOUT_S (the limit probe() has)."""
    return ffprobe_json(path, ["-show_streams", "-show_format"], FFPROBE_TIMEOUT_S)


def _number(value, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):  # missing, or "N/A"
        return default


def _rate(value: object) -> Fraction | None:
    try:
        return Fraction(str(value))
    except (ValueError, ZeroDivisionError):
        return None


def check_output(path: Path, expected_s: float) -> list[str]:
    """What is wrong with the rendered file at `path` against the output spec, one sentence per problem
    (an empty list: nothing). `expected_s` is the length of the cut reel. The spec: 1080x1920, a constant 30
    frames per second (r_frame_rate and avg_frame_rate both 30/1), H.264 in yuv420p, AAC at 48000 Hz in two
    channels of at least 128000 bit/s, an overall bit rate under 25 Mbit/s, a file under 300 MB, a video
    length within a frame and 0.02 s of `expected_s`, and an audio length within 0.05 s of the video's."""
    info = _ffprobe(path)
    streams, container = info.get("streams", []), info.get("format", {})
    videos = [s for s in streams if s.get("codec_type") == "video"]
    audios = [s for s in streams if s.get("codec_type") == "audio"]
    errors: list[str] = []
    container_s = _number(container.get("duration"))

    video_s = None
    if not videos:
        errors.append("no video stream")
    else:
        video = videos[0]
        size = (video.get("width"), video.get("height"))
        if size != (WIDTH, HEIGHT):
            errors.append(f"video is {size[0]}x{size[1]}, expected {WIDTH}x{HEIGHT}")
        r_rate, avg_rate = _rate(video.get("r_frame_rate")), _rate(video.get("avg_frame_rate"))
        if r_rate != FPS or avg_rate != FPS:
            errors.append(
                f"frame rate is not a constant {FPS}/1 (r_frame_rate {video.get('r_frame_rate')}, "
                f"avg_frame_rate {video.get('avg_frame_rate')})"
            )
        if video.get("codec_name") != "h264":
            errors.append(f"video codec is {video.get('codec_name')}, expected h264")
        if video.get("pix_fmt") != "yuv420p":
            errors.append(f"pixel format is {video.get('pix_fmt')}, expected yuv420p")
        video_s = _number(video.get("duration"), container_s)
        if video_s is None:
            errors.append("video length is unknown")
        elif abs(video_s - expected_s) > LENGTH_TOLERANCE_S:
            errors.append(
                f"video length is {video_s:.3f} s, expected {expected_s:.3f} s "
                f"(at most {LENGTH_TOLERANCE_S:.3f} s apart)"
            )

    if not audios:
        errors.append("no audio stream")
    else:
        audio = audios[0]
        if audio.get("codec_name") != "aac":
            errors.append(f"audio codec is {audio.get('codec_name')}, expected aac")
        if audio.get("sample_rate") != "48000":
            errors.append(f"audio sample rate is {audio.get('sample_rate')} Hz, expected 48000")
        if audio.get("channels") != 2:
            errors.append(f"audio has {audio.get('channels')} channels, expected 2")
        bit_rate = _number(audio.get("bit_rate"), 0.0)
        if bit_rate < MIN_AUDIO_BITRATE:
            errors.append(f"audio bit rate is {bit_rate:.0f}, expected at least {MIN_AUDIO_BITRATE}")
        audio_s = _number(audio.get("duration"), container_s)
        if video_s is not None and audio_s is not None and abs(audio_s - video_s) > SYNC_TOLERANCE_S:
            errors.append(
                f"audio length {audio_s:.3f} s and video length {video_s:.3f} s "
                f"differ by more than {SYNC_TOLERANCE_S} s"
            )

    overall = _number(container.get("bit_rate"))
    if overall is None or overall >= MAX_BITRATE:
        errors.append(f"overall bit rate is {overall}, expected under {MAX_BITRATE}")
    size_bytes = _number(container.get("size"))
    if size_bytes is None or size_bytes >= MAX_BYTES:
        errors.append(f"file is {size_bytes} bytes, expected under {MAX_BYTES}")
    return errors


def check_fontselect(log_text: str) -> list[str]:
    """What is wrong with the font choices in the ffmpeg log. libass logs one "fontselect: (<request>) ->
    <font found>" line per style it draws with; there must be at least one, and every font found must be a
    static Noto Sans TC the render gave it (a name starting NotoSansTC-). A path after the arrow means
    libass went to a font of the system: it does that without any other sign."""
    lines = [line for line in log_text.splitlines() if "fontselect:" in line]
    if not lines:
        return ["no fontselect line in the render log: the captions did not select a font"]
    problems = []
    for line in lines:
        found = line.split("fontselect:", 1)[1].partition("->")[2].strip()
        if not found.startswith(FONT_PREFIX) or "/" in found:
            problems.append(
                f"libass did not use the Noto Sans TC font it was given, it chose {found!r}: {line.strip()}"
            )
    return problems


# ---- the phone preview -------------------------------------------------------------------------


def preview_bitrate(duration_s: float) -> int:
    """The video bitrate in bit/s of the preview of a reel of `duration_s` seconds: what is left of PREVIEW_BUDGET
    of PREVIEW_MAX_BYTES after the audio (PREVIEW_AUDIO_BITRATE), at most PREVIEW_MAX_VIDEO_BITRATE, rounded down to
    a whole kbit/s."""
    fitting = math.floor(PREVIEW_BUDGET * PREVIEW_MAX_BYTES * 8 / duration_s) - PREVIEW_AUDIO_BITRATE
    return min(PREVIEW_MAX_VIDEO_BITRATE, fitting) // 1000 * 1000


def build_preview_args(final: Path, out: Path, video_bitrate: int) -> list[str]:
    """The ffmpeg arguments that make `out` from `final`: the picture scaled to PREVIEW_W x PREVIEW_H at 30 frames
    per second (the captions are in the picture, so they shrink with it), the video encoder of the final at
    `video_bitrate` bit/s, the sound as AAC at PREVIEW_AUDIO_BITRATE in two channels."""
    return [
        "-hide_banner", "-loglevel", "error", "-y", "-i", str(final),
        "-vf", f"scale={PREVIEW_W}:{PREVIEW_H}", "-r", str(FPS),
        "-c:v", VIDEO_ENCODER, "-b:v", _ffmpeg_rate(video_bitrate), "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", _ffmpeg_rate(PREVIEW_AUDIO_BITRATE), "-ac", "2",
        "-movflags", "+faststart", str(out),
    ]


def _megabytes(path: Path) -> float:
    return round(path.stat().st_size / 1_000_000, 2)


def make_preview(p: Project, duration_s: float, runner: Callable, force: bool = False) -> dict:
    """Make out/preview.mp4 from out/final.mp4, for a reel of `duration_s` seconds, as the step "preview" (so it is
    made again only when final.mp4 has other content, or is not there; `force` makes it again). When the step runs it
    returns preview (path), preview_mb, preview_bitrate (bit/s, the -b:v the file was made with) and skipped
    (False); when it is skipped, because preview.mp4 is there for this final.mp4, it returns only {"skipped": True}.

    When the step runs, the old preview.mp4 is removed first, so no failure keeps the preview of an earlier
    final. ffmpeg writes out/preview.tmp.mp4 (log: work/render/preview.log), which is renamed to preview.mp4 only
    when it is within PREVIEW_MAX_BYTES. A file over that is made again once, at the rate that would have brought the
    first one to 95 % of the limit; one that is over it again raises PipelineError(PREVIEW_OVER). A failure of
    ffmpeg raises its PipelineError, and is not tried again. Whatever happens, preview.tmp.mp4 does not stay."""
    final, preview = p.out / "final.mp4", p.out / "preview.mp4"

    def run() -> dict:
        preview.unlink(missing_ok=True)
        work = p.work / "render"
        work.mkdir(parents=True, exist_ok=True)
        temp, log = p.out / "preview.tmp.mp4", work / "preview.log"

        def encode(rate: int) -> int:
            temp.unlink(missing_ok=True)
            runner(build_preview_args(final, temp, rate), log, cwd=work)
            try:
                return temp.stat().st_size
            except FileNotFoundError:
                raise PipelineError("preview was not written", log=str(log)) from None

        try:
            rate = preview_bitrate(duration_s)
            size = encode(rate)
            if size > PREVIEW_MAX_BYTES:
                rate = int(rate * PREVIEW_MAX_BYTES / size * 0.95) // 1000 * 1000
                if encode(rate) > PREVIEW_MAX_BYTES:
                    raise PipelineError(PREVIEW_OVER)
            os.replace(temp, preview)
        finally:
            temp.unlink(missing_ok=True)  # whatever raised, no half file stays (after the rename there is none)
        return {"preview": str(preview), "preview_mb": _megabytes(preview), "preview_bitrate": rate}

    return run_step(p, "preview", {"final": final}, [preview], run, force=force)


def _preview_of(p: Project, duration_s: float, runner: Callable, force: bool) -> dict:
    """The preview, preview_mb and warnings of the render result: make_preview, where a PipelineError (the preview is
    over the limit, ffmpeg failed, final.mp4 is not there) is a warning and no preview, not a failed render."""
    try:
        make_preview(p, duration_s, runner, force)
    except PipelineError as error:
        warning = error.message if error.message == PREVIEW_OVER else f"preview failed: {error.message}"
        return {"preview": None, "warnings": [warning]}
    preview = p.out / "preview.mp4"
    return {"preview": str(preview), "preview_mb": _megabytes(preview), "warnings": []}


# ---- the render --------------------------------------------------------------------------------


class _Plan:
    """What the render draws, read from the documents of the project."""

    def __init__(self, p: Project):
        edit, transcript, trims = require_valid(p)  # before build_segments, which needs a valid edit
        self.edit = edit
        self.style = load_style(edit["style"])
        for weight in ("regular", "bold"):  # the render copies both: a missing one is told before anything is touched
            font = font_path(self.style, weight)
            if not font.is_file():
                raise PipelineError(f"font {font.name} not found: run `uv run pipeline setup`")
        probe = load_document(p.work / "probe.json", "prep", ("video_source", "display_w", "display_h"))
        self.video = Path(probe["video_source"])
        self.segments = build_segments(edit, transcript, trims)
        self.duration_s = total_duration(self.segments)
        self.video_bitrate = video_bitrate(self.duration_s)  # refuses a reel too long for the size limit, early
        caps = self.style["captions"]
        self.font = font_path(self.style, "bold" if caps["bold"] else "regular")
        self.events = build_events(transcript, edit, self.segments, self.font, self.style)
        missing = sorted({c for event in self.events for c in missing_glyphs(event.line.text, self.font)})
        if missing:
            raise PipelineError(
                f"the caption font {self.font.name} has no glyph for: "
                + " ".join(f"{c} (U+{ord(c):04X})" for c in missing),
                characters=missing,
            )
        self.top_px = caption_top_px(self.style)


def _result(final: Path, plan: _Plan, attempts: int) -> dict:
    return {
        "out": str(final),
        "duration_s": round(plan.duration_s, 3),
        "size_mb": _megabytes(final),
        "attempts": attempts,
    }


def _make(p: Project, plan: _Plan, final: Path, runner: Callable) -> dict:
    final.unlink(missing_ok=True)  # first of all: a file of an earlier edit must not stay if this render fails
    (p.out / "preview.mp4").unlink(missing_ok=True)  # nor the preview made from it
    work = p.work / "render"
    (work / "fonts").mkdir(parents=True, exist_ok=True)
    p.out.mkdir(exist_ok=True)
    for weight in ("regular", "bold"):
        font = font_path(plan.style, weight)
        shutil.copyfile(font, work / "fonts" / font.name)
    write_ass(plan.events, plan.style, plan.top_px, plan.edit["captions"]["mode"], work / "captions.ass",
              font=plan.font)
    cut_audio(p.work / "audio48k.wav", plan.segments, work / "cut.wav")
    treat_voice(work / "cut.wav", work / "voice.wav", work / "voice.log")

    temp, log = p.out / "final.tmp.mp4", work / "render.log"
    args = build_ffmpeg_args(plan.video, work / "voice.wav", plan.segments, temp, work / "filter.txt")
    try:
        for attempts in range(1, ATTEMPTS + 1):
            temp.unlink(missing_ok=True)
            try:
                runner(args, log, cwd=work)
                break
            except PipelineError as error:
                failure = error
        else:
            raise PipelineError(f"render failed after {ATTEMPTS} attempts: {failure.message}", log=str(log),
                                attempts=ATTEMPTS)

        problems = check_output(temp, plan.duration_s) + check_fontselect(
            log.read_text(encoding="utf-8", errors="replace")
        )
        if problems:
            raise PipelineError("the render does not meet the output spec: " + "; ".join(problems),
                                problems=problems, log=str(log))
        os.replace(temp, final)
    finally:
        temp.unlink(missing_ok=True)  # whatever raised, no half file stays; after the rename there is none to remove
    return {**_result(final, plan, attempts), "video_bitrate": plan.video_bitrate}


def render(p: Project, force: bool = False, runner: Callable = run_ffmpeg) -> dict:
    """Render out/final.mp4 from the approved edit. Returns out (path), duration_s (of the cut reel), size_mb,
    attempts (ffmpeg runs: 2 after a retry, 0 when the step was skipped), video_bitrate (bit/s, the one given to
    ffmpeg as -b:v, see video_bitrate; left out of a skipped step, which encoded nothing) and skipped (of the
    final), then what the phone preview adds (see make_preview): preview (the path of out/preview.mp4, None when
    there is none), preview_mb (only when there is one) and warnings, a list of strings.

    Refuses with "gate1 not approved for the current storyboard" unless gate 1 was approved for the storyboard
    as it stands now (the same edit.json and the same inputs of the storyboard step) and prep, transcribe and
    trim are done, also when final.mp4 is there; when one of those three is out of date the message names the
    first. The step is skipped when no input changed since the last render and final.mp4 is still there;
    `force` renders again. `runner(args, log, cwd=...)` runs ffmpeg (run_ffmpeg); a PipelineError from it is an
    attempt that failed. An edit.json that does not pass raises EditInvalid; a caption character the font cannot
    draw, a reel so long that the size limit would need a video bitrate under MIN_VIDEO_BITRATE (refused before
    final.mp4 or anything else is touched), a style font or the caption dictionary that is not there (the same),
    an output that breaks the spec and a font libass took from the system raise PipelineError. The caption word
    list and the dictionary are inputs of the step, as they are of the storyboard (words.caption_inputs).

    A render that actually runs removes the old final.mp4 and the old preview.mp4 first, before it writes anything
    else, so a render that then fails leaves neither of an earlier edit (a refused or skipped render does not touch
    them). out/final.tmp.mp4 is removed whenever the render ends without making it final.mp4.

    The preview is made after the final, whether the final was made now or skipped, so that a project rendered
    before the preview existed gets it from another render, and a final that is unchanged but has lost its preview
    gets only that. A preview that cannot be made (PREVIEW_OVER, a failed ffmpeg) does not fail the render: the
    final stays, `preview` is None and `warnings` says why (PREVIEW_OVER as it is, anything else as "preview failed:
    <message>")."""
    if not gate_ok(p, "gate1"):
        stale = first_stale_step(p)
        if stale is not None:
            raise PipelineError(
                f"gate1 not approved for the current storyboard: {stale} is out of date, "
                f"run {stale}, then storyboard and approve"
            )
        raise PipelineError("gate1 not approved for the current storyboard: run storyboard, then approve")
    plan = _Plan(p)
    final = p.out / "final.mp4"
    inputs = {
        "edit": p.edit_path,
        "transcript": p.work / "transcript.json",
        "trim": p.work / "trim.json",
        "style": plan.style["dir"] / "style.json",
        "audio": p.work / "audio48k.wav",
        "video": plan.video,
        "probe": p.work / "probe.json",
        **caption_inputs(),  # the caption lines are broken where these say a word ends
    }

    def run() -> dict:
        # Read the documents again: run_step hashed the inputs just before, so what is rendered is at
        # least as new as the hash the step record keeps.
        return _make(p, _Plan(p), final, runner)

    done = run_step(p, "render", inputs, [final], run, force=force)
    if done["skipped"]:
        done = {**_result(final, plan, 0), "skipped": True}
    return {**done, **_preview_of(p, plan.duration_s, runner, force)}
