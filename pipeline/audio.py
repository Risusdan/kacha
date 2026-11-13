"""Loudness envelope, noise floor and quiet-point cut snapping.

A cut in the final reel is placed at the quietest spot near the time the transcript gives, then
moved onto the 30 fps frame grid. Whether a point is in silence is judged against
silence_threshold_db; a cut that cannot be placed in silence is reported, so the user listens to it
before it is rendered.
"""

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

from pipeline.errors import PipelineError

HOP_S = 0.01  # the envelope has one value per 10 ms window
FLOOR_DB = -100.0  # the lowest level an envelope reports (digital silence)
NOISE_FLOOR_PERCENTILE = 10  # the noise floor is this percentile of the window levels
SEARCH_WINDOW_S = 0.15  # a cut is searched this far either side of the time asked for
MIN_RANGE_S = 0.02  # a cut range shorter than this is ignored in favour of the plain search window
SILENCE_OFFSET_DB = 6.0  # a point is in silence when it is at most this far above the noise floor
SPEECH_PERCENTILE = 90  # the speech level is this percentile of the window levels
SPEECH_MARGIN_DB = 20.0  # and a point is in silence only when it is at least this far under the speech level
FEW_PAUSES_DB = 25.0  # a speech level less than this over the noise floor: the pauses are too few to tell
_BLOCK_WINDOWS = 1000  # windows read from the file at a time


@dataclass
class Envelope:
    db: np.ndarray  # RMS level in dBFS of each window; window k covers [k * hop_s, (k + 1) * hop_s)
    hop_s: float


def envelope(wav: Path) -> Envelope:
    """The level of `wav` in dBFS, mixed to mono, in consecutive 10 ms windows of RMS. Digital silence
    is FLOOR_DB. The samples after the last whole window are left out."""
    try:
        info = sf.info(str(wav))
        rate = info.samplerate
        size = rate * HOP_S
        if abs(size - round(size)) > 1e-6:
            raise PipelineError(f"{wav}: a {rate} Hz sample rate does not divide into {HOP_S * 1000:g} ms windows")
        size = round(size)
        levels = []
        for block in sf.blocks(str(wav), blocksize=size * _BLOCK_WINDOWS, dtype="float64", always_2d=True):
            mono = block.mean(axis=1)
            whole = len(mono) // size
            power = np.mean(mono[:whole * size].reshape(whole, size) ** 2, axis=1)
            levels.append(10 * np.log10(np.maximum(power, 10 ** (FLOOR_DB / 10))))
    except (OSError, RuntimeError) as error:
        raise PipelineError(f"cannot read {wav}: {error}") from error
    return Envelope(np.concatenate(levels) if levels else np.zeros(0), HOP_S)


def _levels(env: Envelope) -> np.ndarray:
    """The window levels that tell quiet from speech: those above FLOOR_DB, since digital silence is
    neither room noise nor speech."""
    return env.db[env.db > FLOOR_DB]


def noise_floor_db(env: Envelope) -> float:
    """The level of the background: the 10th percentile of the window levels above FLOOR_DB, or
    FLOOR_DB when every window is digital silence."""
    levels = _levels(env)
    return float(np.percentile(levels, NOISE_FLOOR_PERCENTILE)) if len(levels) else FLOOR_DB


def speech_level_db(env: Envelope) -> float:
    """The level of the speech: the 90th percentile of the same window levels (FLOOR_DB when there are
    none)."""
    levels = _levels(env)
    return float(np.percentile(levels, SPEECH_PERCENTILE)) if len(levels) else FLOOR_DB


def silence_threshold_db(env: Envelope) -> float:
    """The level up to which a point is in silence: SILENCE_OFFSET_DB over the noise floor, but at
    least SPEECH_MARGIN_DB under the speech level. The second limit matters when the recording has too
    few pauses: the 10th percentile is then speech itself, and its dips would count as silence."""
    return min(noise_floor_db(env) + SILENCE_OFFSET_DB, speech_level_db(env) - SPEECH_MARGIN_DB)


def few_pauses(env: Envelope) -> bool:
    """Whether the speech is less than FEW_PAUSES_DB louder than the noise floor, so that the quiet
    windows are not told apart from speech reliably."""
    return speech_level_db(env) - noise_floor_db(env) < FEW_PAUSES_DB


def snap_cut(t: float, lo: float, hi: float, env: Envelope, window_s: float = SEARCH_WINDOW_S, fps: int = 30) -> float:
    """The quietest place for a cut near `t`, on the frame grid.

    The search range is [t - window_s, t + window_s] limited to [lo, hi]; when that leaves less than
    MIN_RANGE_S, the whole [t - window_s, t + window_s] is searched. The quietest 10 ms window of the
    range wins (the one closest to `t` among equals); its centre is rounded to the nearest 1/fps
    second and kept inside the audio. Whether that point is in silence is not decided here: is_silent
    judges it against silence_threshold_db."""
    start, stop = max(lo, t - window_s), min(hi, t + window_s)
    if stop - start < MIN_RANGE_S:
        start, stop = t - window_s, t + window_s
    count = len(env.db)
    if count == 0:
        return 0.0
    frames = math.floor(count * env.hop_s * fps + 1e-9)  # the last frame inside the audio
    first = max(0, math.ceil(start / env.hop_s - 0.5))
    last = min(count - 1, math.floor(stop / env.hop_s - 0.5))
    if first > last:  # the range lies outside the audio: the window nearest to it
        first = last = min(max(round(t / env.hop_s - 0.5), 0), count - 1)
    centres = (np.arange(first, last + 1) + 0.5) * env.hop_s
    levels = env.db[first:last + 1]
    near = np.flatnonzero(levels == levels.min())
    best = near[np.argmin(np.abs(centres[near] - t))]
    return min(max(round(centres[best] * fps), 0), frames) / fps


def is_silent(t: float, env: Envelope, threshold_db: float) -> bool:
    """Whether the window of `env` that contains `t` is at most `threshold_db`."""
    if len(env.db) == 0:
        return False
    window = min(max(math.floor(t / env.hop_s + 1e-9), 0), len(env.db) - 1)
    return bool(env.db[window] <= threshold_db)
