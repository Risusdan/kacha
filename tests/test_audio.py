"""Tests for pipeline.audio: the 10 ms loudness envelope, the noise floor and quiet-point cut snapping."""

import numpy as np
import pytest
import soundfile as sf
from helpers import synth_wav

from pipeline.audio import (
    HOP_S,
    Envelope,
    envelope,
    few_pauses,
    is_silent,
    noise_floor_db,
    silence_threshold_db,
    snap_cut,
)


def _on_grid(t: float, fps: int = 30) -> bool:
    return abs(t * fps - round(t * fps)) < 1e-9


# ---- envelope and noise floor -----------------------------------------------------------------


def test_noise_floor(tmp_path):
    env = envelope(synth_wav(tmp_path, [("noise", 2.0, -50), ("tone", 1.0, -20)]))
    assert abs(noise_floor_db(env) - (-50)) < 3


def test_noise_floor_is_the_10th_percentile():
    assert noise_floor_db(Envelope(np.arange(100.0), HOP_S)) == pytest.approx(9.9)


def test_noise_floor_leaves_out_digital_silence(tmp_path):
    # 20 % digital zero, then room noise at -65 dB and speech: the zero windows are not the background.
    env = envelope(synth_wav(tmp_path, [("silence", 1.0, 0), ("noise", 1.5, -65), ("tone", 2.5, -20)]))
    assert abs(noise_floor_db(env) - (-65)) < 2


def test_noise_floor_of_nothing_but_digital_silence_is_the_floor():
    assert noise_floor_db(Envelope(np.full(100, -100.0), HOP_S)) == -100.0


def test_silence_threshold_is_6_db_over_the_floor_when_there_are_pauses(tmp_path):
    env = envelope(synth_wav(tmp_path, [("noise", 2.0, -50), ("tone", 1.0, -20)]))
    assert silence_threshold_db(env) == pytest.approx(noise_floor_db(env) + 6)
    assert not few_pauses(env)


def test_silence_threshold_stays_20_db_under_the_speech_level():
    # Speech at -20 dB with dips of 10 dB and no pause: the quietest 10 % are the dips, the floor is -30.
    db = np.full(100, -20.0)
    db[::7] = -30.0
    env = Envelope(db, HOP_S)
    assert noise_floor_db(env) == pytest.approx(-30.0)
    assert silence_threshold_db(env) == pytest.approx(-40.0)  # p90 - 20, not floor + 6 = -24
    assert few_pauses(env)


def test_silence_threshold_ignores_digital_silence_too(tmp_path):
    env = envelope(synth_wav(tmp_path, [("silence", 1.0, 0), ("noise", 1.5, -65), ("tone", 2.5, -20)]))
    assert silence_threshold_db(env) == pytest.approx(noise_floor_db(env) + 6)
    assert not few_pauses(env)


def test_speech_level_is_measured_over_the_windows_above_the_floor_too():
    # Half the clip is digital zero, 42 windows are room noise and 8 are speech (16 % of what is left).
    env = Envelope(np.concatenate([np.full(50, -100.0), np.full(42, -60.0), np.full(8, -20.0)]), HOP_S)
    assert noise_floor_db(env) == pytest.approx(-60.0)
    assert silence_threshold_db(env) == pytest.approx(-54.0)  # the speech cap, -40, is not reached
    assert not few_pauses(env)


def test_few_pauses_is_a_spread_under_25_db():
    assert few_pauses(Envelope(np.concatenate([np.full(50, -60.0), np.full(50, -36.0)]), HOP_S))
    assert not few_pauses(Envelope(np.concatenate([np.full(50, -60.0), np.full(50, -35.0)]), HOP_S))  # exactly 25 dB
    assert not few_pauses(Envelope(np.concatenate([np.full(50, -60.0), np.full(50, -34.0)]), HOP_S))


def test_envelope_is_10ms_rms_in_dbfs(tmp_path):
    env = envelope(synth_wav(tmp_path, [("tone", 1.0, -20)]))
    assert env.hop_s == HOP_S == 0.01
    assert len(env.db) == 100
    assert np.allclose(env.db, -20, atol=0.2)


def test_envelope_window_k_covers_k_hop_to_k_plus_1_hop(tmp_path):
    env = envelope(synth_wav(tmp_path, [("silence", 0.5, 0), ("tone", 0.01, -20), ("silence", 0.49, 0)]))
    loud = np.flatnonzero(env.db > -90)
    assert list(loud) == [50]


def test_envelope_floor_is_minus_100(tmp_path):
    env = envelope(synth_wav(tmp_path, [("silence", 0.5, 0)]))
    assert np.all(env.db == -100)


def test_envelope_drops_a_partial_last_window(tmp_path):
    env = envelope(synth_wav(tmp_path, [("tone", 1.005, -20)]))
    assert len(env.db) == 100


def test_envelope_mixes_stereo_to_mono(tmp_path):
    left = np.sqrt(2) * 0.1 * np.sin(2 * np.pi * 500 * np.arange(48000) / 48000)  # RMS 0.1, -20 dBFS
    path = tmp_path / "stereo.wav"
    sf.write(str(path), np.stack([left, np.zeros(48000)], axis=1), 48000, subtype="PCM_16")
    env = envelope(path)
    assert np.allclose(env.db, -20 - 6.02, atol=0.2)  # the average of the channels, half the amplitude


# ---- snap_cut -----------------------------------------------------------------------------------


def test_snap_finds_quiet_dip_on_grid(tmp_path):
    env = envelope(synth_wav(tmp_path, [("tone", 1.05, -20), ("noise", 0.04, -50), ("tone", 1.0, -20)]))
    t = snap_cut(1.0, 0.0, 2.0, env)
    assert abs(t - 1.07) <= 1 / 30 and abs(t * 30 - round(t * 30)) < 1e-9


def test_snap_in_an_empty_envelope_is_the_start():
    assert snap_cut(1.0, 0.0, 2.0, Envelope(np.zeros(0), HOP_S)) == 0.0


def test_snap_respects_bounds(tmp_path):
    env = envelope(synth_wav(tmp_path, [("noise", 0.9, -60), ("tone", 1.1, -20)]))
    t = snap_cut(1.0, 0.95, 1.2, env)
    assert 0.95 - 1 / 60 <= t <= 1.2 + 1 / 60


def test_snap_search_stays_inside_lo_hi(tmp_path):
    # A quiet dip at 1.05 to 1.10, but the cut may only be placed in [0.9, 1.04]: all speech there.
    env = envelope(synth_wav(tmp_path, [("tone", 1.05, -20), ("silence", 0.05, 0), ("tone", 1.0, -20)]))
    assert snap_cut(1.0, 0.9, 1.04, env) <= 1.04 + 1 / 60


def test_snap_ignores_lo_hi_when_they_leave_less_than_20ms(tmp_path):
    env = envelope(synth_wav(tmp_path, [("tone", 1.05, -20), ("silence", 0.05, 0), ("tone", 1.0, -20)]))
    t = snap_cut(1.0, 1.0, 1.0, env)  # zero-width range: the plain +-0.15 s window
    assert t == pytest.approx(32 / 30)  # centre 1.055 rounds to frame 32


def test_snap_search_window_is_plus_minus_150ms(tmp_path):
    env = envelope(synth_wav(tmp_path, [("tone", 1.2, -20), ("silence", 0.05, 0), ("tone", 1.0, -20)]))
    assert snap_cut(1.0, 0.0, 3.0, env) == pytest.approx(1.0)  # the dip at 1.2 is 200 ms away: not found
    assert 1.2 - 1 / 60 <= snap_cut(1.0, 0.0, 3.0, env, window_s=0.3) <= 1.25 + 1 / 60  # found in the dip


def test_snap_equally_quiet_takes_the_one_closest_to_t(tmp_path):
    parts = [("tone", 0.6, -20), ("silence", 0.01, 0), ("tone", 0.84, -20), ("silence", 0.01, 0), ("tone", 0.54, -20)]
    env = envelope(synth_wav(tmp_path, parts))  # silent windows at 0.6 and 1.45
    t = snap_cut(1.0, 0.0, 2.0, env, window_s=0.6)
    assert t == pytest.approx(18 / 30)  # 0.605 is 0.395 from t, 1.455 is 0.455 from t
    t = snap_cut(1.3, 0.0, 2.0, env, window_s=0.9)
    assert t == pytest.approx(44 / 30)  # 1.455 is 0.155 from t, 0.605 is 0.695


def test_snap_all_silent_range_picks_the_window_at_t(tmp_path):
    env = envelope(synth_wav(tmp_path, [("silence", 2.0, 0)]))
    assert snap_cut(1.0, 0.0, 2.0, env) == pytest.approx(1.0)


def test_snap_clamps_to_the_audio():
    env = Envelope(np.full(99, -90.0), HOP_S)  # 0.99 s: the last window's centre 0.985 would round up to 1.0
    for t in (-0.2, 0.0, 0.5, 0.98, 0.99, 1.2):
        out = snap_cut(t, t - 0.1, t + 0.1, env)
        assert 0.0 <= out <= 0.99 and _on_grid(out)
    assert snap_cut(0.99, 0.9, 1.5, env) == pytest.approx(29 / 30)


def test_snap_uses_the_given_frame_rate():
    env = Envelope(np.full(200, -90.0), HOP_S)
    out = snap_cut(1.013, 0.0, 2.0, env, fps=25)
    assert _on_grid(out, 25) and abs(out - 1.013) < 1 / 25


# ---- is_silent ----------------------------------------------------------------------------------


def test_silent_means_at_most_the_threshold():
    assert is_silent(0.5, Envelope(np.full(100, -44.0), HOP_S), -44.0) is True
    assert is_silent(0.5, Envelope(np.full(100, -43.9), HOP_S), -44.0) is False
