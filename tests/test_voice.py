"""The voice treatment (pipeline.voice): the filter chain the user chose by ear, then one gain that brings the voice to
the target loudness, then a limiter, so that a quiet recording comes out as loud as other reels without its pauses being
raised more than its speech."""

import numpy as np
import pytest
import soundfile as sf
from helpers import MEIJIA, loudness

from pipeline.errors import PipelineError
from pipeline.media import run_ffmpeg
from pipeline.voice import MAX_GAIN_DB, TARGET_LUFS, VOICE_FILTERS, treat_voice, voice_gain_db

RATE = 48000


def speech(gain_db: float = 0.0) -> np.ndarray:
    """The Meijia fixture speech (mono, 48 kHz), scaled by `gain_db`."""
    data, rate = sf.read(str(MEIJIA / "fixture.wav"), dtype="float32")
    assert rate == RATE and data.ndim == 1
    return data * np.float32(10 ** (gain_db / 20))


def level_db(samples: np.ndarray) -> float:
    return float(20 * np.log10(np.sqrt(np.mean(np.square(samples, dtype=np.float64))) + 1e-12))


def lag_s(out: np.ndarray, reference: np.ndarray, search_ms: int = 100) -> float:
    """How many seconds later than `reference` the sound of `out` comes, from the peak of the cross-correlation of
    their 1 ms envelopes."""
    def envelope(x):
        usable = len(x) // 48 * 48
        e = np.abs(x[:usable]).reshape(-1, 48).mean(axis=1)
        return e - e.mean()

    a, b = envelope(out), envelope(reference)
    n = min(len(a), len(b)) - 2 * search_ms
    core = a[search_ms:search_ms + n]
    scores = {lag: float(np.dot(core, b[search_ms - lag:search_ms - lag + n]))
              for lag in range(-search_ms, search_ms + 1)}
    return max(scores, key=scores.get) / 1000


def test_the_voice_filters_are_the_chain_the_user_chose_by_ear():
    """Version F of the listening test on 2026-11-14: a low cut at 80 Hz, FFT denoise (12 dB, noise floor -35 dB),
    2 dB less around 250 Hz, 3 dB more above 2.5 kHz; then -14 LUFS."""
    assert VOICE_FILTERS == "highpass=f=80,afftdn=nr=12:nf=-35,equalizer=f=250:t=o:w=1:g=-2,highshelf=f=2500:g=3"
    assert TARGET_LUFS == -14.0


@pytest.mark.parametrize(
    ("measured", "gain"),
    [(-25.5, 11.5), (-14.0, 0.0), (-8.0, -6.0), (-14.0 - MAX_GAIN_DB - 6, MAX_GAIN_DB), (-70.0, 0.0), (-75.0, 0.0)],
    ids=["quiet", "on-target", "loud", "too-quiet-capped", "silence", "below-silence"],
)
def test_voice_gain_db(measured, gain):
    assert voice_gain_db(measured) == pytest.approx(gain)


def test_treat_voice_brings_a_quiet_voice_to_the_target_with_peaks_under_minus_1_dbtp(tmp_path):
    src, out = tmp_path / "cut.wav", tmp_path / "voice.wav"
    quiet = speech(-10.0)
    sf.write(str(src), quiet, RATE, subtype="PCM_16")
    before, _ = loudness(src)
    assert before < -30  # the test starts from a voice far below the target

    result = treat_voice(src, out, tmp_path / "voice.log")

    integrated, true_peak = loudness(out)
    assert integrated == pytest.approx(TARGET_LUFS, abs=0.5)
    assert true_peak <= -1.0
    assert result["loudness_out_lufs"] == pytest.approx(integrated, abs=0.2)
    # the limiter takes some loudness back, so the gain is at least what the filtered sound alone was short by
    assert result["gain_db"] >= TARGET_LUFS - result["loudness_lufs"] - 0.01
    assert result["loudness_lufs"] == pytest.approx(before, abs=1.5)  # measured after the filters, near the input's
    data, rate = sf.read(str(out), dtype="float32", always_2d=True)
    assert rate == RATE and data.shape == (len(quiet), 1)  # every sample, same channels: the length is kept
    assert lag_s(data[:, 0], quiet) == 0  # nothing is delayed


def test_treat_voice_lowers_steady_low_noise_more_than_the_voice(tmp_path):
    """A low hum of room noise under the whole recording, alone for its first 3 s: after the treatment, the noise
    sits at least 4 dB further under the speech than before."""
    rng = np.random.default_rng(7)
    voice = speech()
    lead = 3 * RATE
    white = rng.standard_normal(lead + len(voice))
    kernel = np.ones(96) / 96  # a moving average over 2 ms: noise mostly below 500 Hz, as the real room noise was
    noise = np.convolve(white, kernel, mode="same")
    noise *= 10 ** (-48 / 20) / np.sqrt(np.mean(noise ** 2))  # -48 dBFS, about the room noise of the real recording
    mixed = (np.concatenate([np.zeros(lead), voice]) + noise).astype("float32")
    src, out = tmp_path / "cut.wav", tmp_path / "voice.wav"
    sf.write(str(src), mixed, RATE, subtype="PCM_16")

    treat_voice(src, out, tmp_path / "voice.log")

    treated, _ = sf.read(str(out), dtype="float32")
    pause, talk = slice(RATE // 2, lead - RATE // 2), slice(lead, lead + len(voice))
    gap_before = level_db(mixed[talk]) - level_db(mixed[pause])
    gap_after = level_db(treated[talk]) - level_db(treated[pause])
    assert gap_after >= gap_before + 4, (gap_before, gap_after)


def test_treat_voice_leaves_silence_silent(tmp_path):
    src, out = tmp_path / "cut.wav", tmp_path / "voice.wav"
    sf.write(str(src), np.zeros(2 * RATE, dtype="float32"), RATE, subtype="PCM_16")
    result = treat_voice(src, out, tmp_path / "voice.log")
    data, _ = sf.read(str(out), dtype="float32")
    assert result["gain_db"] == 0.0
    assert len(data) == 2 * RATE and np.abs(data).max() == 0.0


def test_treat_voice_keeps_the_last_words(tmp_path):
    """The denoise holds back the end of the sound; the treatment gets it out: a tone in the last 20 ms of the input
    is in the last 20 ms of the output."""
    tone = 0.3 * np.sin(2 * np.pi * 1000 * np.arange(960) / RATE)
    data = np.concatenate([speech(), np.zeros(RATE // 2), tone]).astype("float32")
    src, out = tmp_path / "cut.wav", tmp_path / "voice.wav"
    sf.write(str(src), data, RATE, subtype="PCM_16")
    treat_voice(src, out, tmp_path / "voice.log")
    treated, _ = sf.read(str(out), dtype="float32")
    assert len(treated) == len(data)
    assert level_db(treated[-960:]) > level_db(treated[-RATE // 2:-RATE // 4]) + 20  # the tone, over the gap before it


def test_treat_voice_refuses_a_file_it_cannot_read(tmp_path):
    with pytest.raises(PipelineError, match="cannot read .*missing.wav"):
        treat_voice(tmp_path / "missing.wav", tmp_path / "voice.wav", tmp_path / "voice.log")
    assert not (tmp_path / "voice.wav").exists()


def test_a_failed_ffmpeg_run_names_its_log_and_leaves_no_voice_file(tmp_path, monkeypatch):
    src, out, log = tmp_path / "cut.wav", tmp_path / "voice.wav", tmp_path / "voice.log"
    sf.write(str(src), speech(), RATE, subtype="PCM_16")

    def failing_write(args, log_path, cwd=None):
        if str(out) in args:  # the run that writes the voice: it leaves half a file, then fails
            out.write_bytes(b"half a file")
            raise PipelineError("ffmpeg failed with exit code 1", log=str(log_path))
        run_ffmpeg(args, log_path, cwd)

    monkeypatch.setattr("pipeline.voice.run_ffmpeg", failing_write)
    with pytest.raises(PipelineError) as error:
        treat_voice(src, out, log)
    assert error.value.extra.get("log") == str(log)
    assert not out.exists()
