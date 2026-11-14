"""The voice treatment of the render: the sound of the cut reel is cleaned up and brought to the loudness of other
reels before it is encoded.

The chain (VOICE_FILTERS) was chosen by ear on 2026-11-14, from seven versions of a real phone recording that was
quiet (-25.6 LUFS) and had steady room noise below 1 kHz: a low cut at 80 Hz, FFT denoise, 2 dB less around 250 Hz
(the boom of a voice recorded from too far) and 3 dB more above 2.5 kHz (what the denoise takes from the voice, put
back; the room noise had almost nothing up there, so this brings no noise back).

The loudness is reached with one gain for the whole reel, then a limiter on the peaks. ffmpeg's loudnorm was not
used: when a recording is about 11 dB too quiet its peaks leave no room for one plain gain, so it falls back to its
dynamic mode, which raises the quiet parts more than the speech, and the noise in the pauses with them; measured on
that recording, it undid what the denoise had taken away.

treat_voice measures the loudness of the filtered sound (ebur128), writes it with the gain and the limiter, and
measures what it wrote. The limiter takes some loudness back where the peaks stand far above the speech (a 17 s test
voice came out 1.8 dB short), so the shortfall is added to the gain and the sound written again, at most
MAX_PASSES times in all.
"""

import re
from pathlib import Path

import soundfile as sf

from pipeline.errors import PipelineError
from pipeline.media import run_ffmpeg

LOW_CUT = "highpass=f=80"
DENOISE = "afftdn=nr=12:nf=-35"
EQUALIZER = "equalizer=f=250:t=o:w=1:g=-2,highshelf=f=2500:g=3"
VOICE_FILTERS = ",".join((LOW_CUT, DENOISE, EQUALIZER))
# afftdn gives its output DENOISE_DELAY_S late and drops as much of the end (measured with ffmpeg 9.0.2, by its
# output_mode=input: 1200 samples at 48 kHz, 1102 at 44.1 kHz, 400 at 16 kHz). _filters pads the end and trims the
# start by that much, so the voice stays on the picture and keeps its last words.
DENOISE_DELAY_S = 0.025
TARGET_LUFS = -14.0  # the integrated loudness of the voice: the usual target of social video, not a Meta rule
# The limiter holds the samples at -1.5 dBFS, so that the true peak of the AAC the render makes stays at or under
# -1 dBTP (the encoder and the peaks between samples add a little).
PEAK_LIMIT_DB = -1.5
MAX_GAIN_DB = 30.0  # a voice quieter than TARGET_LUFS - MAX_GAIN_DB is raised by this much only, so noise is not
SILENCE_LUFS = -70.0  # what ebur128 reports for silence (and anything under its gate): no gain at all
TOLERANCE_LU = 0.5  # how far from TARGET_LUFS the written sound may be before the gain is corrected
MAX_PASSES = 3  # writes of the sound at most: the first, and two corrections of the gain
_INTEGRATED = re.compile(r"Integrated loudness:\s*I:\s*(-?\d+(?:\.\d+)?) LUFS")


def voice_gain_db(loudness_lufs: float) -> float:
    """The gain in dB that brings a voice of `loudness_lufs` to TARGET_LUFS: at most MAX_GAIN_DB, and 0 for silence
    (SILENCE_LUFS or less)."""
    if loudness_lufs <= SILENCE_LUFS:
        return 0.0
    return min(TARGET_LUFS - loudness_lufs, MAX_GAIN_DB)


def _limiter() -> str:
    """alimiter at PEAK_LIMIT_DB: a 5 ms lookahead whose delay it takes back (latency), and no auto level (which
    would raise the whole sound to the limit)."""
    return f"alimiter=limit={10 ** (PEAK_LIMIT_DB / 20):.4f}:attack=5:release=50:level=0:latency=1"


def _filters(rate: int) -> str:
    """VOICE_FILTERS for sound at `rate` Hz, with the delay of the denoise taken back: the end padded with the
    samples it holds back, and as many samples cut from the start."""
    delay = int(rate * DENOISE_DELAY_S)
    return (f"apad=pad_len={delay},{LOW_CUT},{DENOISE},atrim=start_sample={delay},asetpts=PTS-STARTPTS,"
            f"{EQUALIZER}")


def _rate(wav: Path) -> int:
    try:
        return sf.info(str(wav)).samplerate
    except (OSError, RuntimeError) as error:  # soundfile's LibsndfileError is a RuntimeError
        raise PipelineError(f"cannot read {wav}: {error}") from error


def _loudness(wav: Path, filters: str, log: Path) -> float:
    """The integrated loudness in LUFS of `wav` through `filters`, as ebur128 measures it."""
    run_ffmpeg(["-hide_banner", "-nostats", "-i", str(wav), "-af", f"{filters},ebur128=framelog=quiet",
                "-f", "null", "-"], log)
    found = _INTEGRATED.findall(log.read_text(encoding="utf-8", errors="replace"))
    if not found:
        raise PipelineError("could not measure the loudness of the voice", log=str(log))
    return float(found[-1])


def treat_voice(src_wav: Path, out_wav: Path, log: Path) -> dict:
    """Write `src_wav` through VOICE_FILTERS (with the delay of the denoise taken back, see _filters), a gain and the
    limiter to `out_wav` (24-bit PCM, the rate, channels and length of `src_wav`, every sample where it was), so that
    it comes out within TOLERANCE_LU of TARGET_LUFS. The gain starts at what
    voice_gain_db gives for the filtered sound; while the written sound is short of the target (the limiter took
    some back), the shortfall is added and the sound written again, at most MAX_PASSES writes in all and never more
    than MAX_GAIN_DB. Silence is written once, with no gain.

    Returns loudness_lufs (of the filtered sound, before any gain), gain_db (the gain of the sound written) and
    loudness_out_lufs (of `out_wav`). ffmpeg's log goes to `log` (each run overwrites it); a failure of any run
    raises its PipelineError, which names `log`, and leaves no `out_wav`."""
    filters = _filters(_rate(src_wav))
    try:
        loudness = _loudness(src_wav, filters, log)
        gain = round(voice_gain_db(loudness), 2)
        for written in range(1, MAX_PASSES + 1):
            run_ffmpeg(["-hide_banner", "-nostats", "-y", "-i", str(src_wav),
                        "-af", f"{filters},volume={gain}dB,{_limiter()}", "-c:a", "pcm_s24le", str(out_wav)],
                       log)
            reached = _loudness(out_wav, "anull", log)
            shortfall = TARGET_LUFS - reached
            if (loudness <= SILENCE_LUFS or abs(shortfall) <= TOLERANCE_LU or gain >= MAX_GAIN_DB
                    or written == MAX_PASSES):
                break
            gain = round(min(gain + shortfall, MAX_GAIN_DB), 2)
    except PipelineError:
        out_wav.unlink(missing_ok=True)
        raise
    return {"loudness_lufs": loudness, "gain_db": gain, "loudness_out_lufs": reached}
