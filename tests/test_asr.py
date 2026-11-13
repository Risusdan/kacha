"""Tests for pipeline.asr: running mlx-qwen3-asr, reading its JSON, the 0.6B fallback and the cache."""

import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from helpers import write_raw
from media_fixtures import to_16k_mono

from pipeline import asr
from pipeline.asr import MODELS, AsrToken, load_asr, run_asr, transcribe_audio
from pipeline.errors import AsrTruncatedError, PipelineError
from pipeline.project import file_sha256
from pipeline.textnorm import char_key

FIXTURES = Path(__file__).resolve().parent / "fixtures"
RAW = FIXTURES / "meijia" / "asr-qwen3-1.7b.json"


# ---- load_asr -------------------------------------------------------------------------------


def test_load_recorded_fixture():
    r = load_asr(RAW, MODELS["1.7b"])
    assert r.tokens and all(t.end >= t.start for t in r.tokens)
    assert [t.start for t in r.tokens] == sorted(t.start for t in r.tokens)


def test_recorded_fixture_has_one_token_per_segment_and_keeps_the_text():
    recorded = json.loads(RAW.read_text(encoding="utf-8"))
    r = load_asr(RAW, MODELS["1.7b"])
    assert r.model == MODELS["1.7b"] and r.text == recorded["text"]
    assert [t.text for t in r.tokens] == [s["text"] for s in recorded["segments"]]
    assert [(t.start, t.end) for t in r.tokens] == [(s["start"], s["end"]) for s in recorded["segments"]]


def test_multi_unit_segment_is_spread(tmp_path):
    raw = write_raw(tmp_path, [("Reels", 1.0, 1.4), ("我們", 2.0, 2.4)])  # helper in tests/helpers.py
    toks = load_asr(raw, "m").tokens
    assert [t.text for t in toks] == ["reels", "我", "們"]
    # pytest.approx does not take nested sequences, so the (start, end) pairs are flattened
    assert [v for t in toks for v in (t.start, t.end)] == pytest.approx([1.0, 1.4, 2.0, 2.2, 2.2, 2.4])


def test_tokens_carry_kind_and_key(tmp_path):
    raw = write_raw(tmp_path, [("Reels", 0.0, 0.4), ("我", 0.4, 0.6), ("150", 0.6, 1.0)])
    toks = load_asr(raw, "m").tokens
    assert toks == [
        AsrToken("reels", "latin", frozenset({"reels"}), 0.0, 0.4),
        AsrToken("我", "cjk", char_key("我"), 0.4, 0.6),
        AsrToken("150", "num", frozenset({"#150"}), 0.6, 1.0),
    ]


def test_zero_length_token_stays_zero_length(tmp_path):
    raw = write_raw(tmp_path, [("我", 1.52, 1.52), ("們", 1.52, 1.92)])
    assert [(t.start, t.end) for t in load_asr(raw, "m").tokens] == [(1.52, 1.52), (1.52, 1.92)]


def test_multi_unit_zero_length_segment(tmp_path):
    # A segment with several units and start == end gives every unit that one instant: each unit is a
    # zero-length token, the units stay in order, and none is dropped. Pinned as load_asr does it today.
    raw = write_raw(tmp_path, [("我們", 1.0, 1.0), ("好", 1.0, 1.4)])
    toks = load_asr(raw, "m").tokens
    assert [(t.text, t.start, t.end) for t in toks] == [("我", 1.0, 1.0), ("們", 1.0, 1.0), ("好", 1.0, 1.4)]


def test_segment_without_units_is_dropped(tmp_path):
    raw = write_raw(tmp_path, [("，", 0.0, 0.1), ("我", 0.1, 0.2)])
    assert [t.text for t in load_asr(raw, "m").tokens] == ["我"]


def test_silent_audio_has_no_tokens(tmp_path):
    raw = write_raw(tmp_path, [])
    r = load_asr(raw, "m")
    assert r.tokens == [] and r.text == ""


def test_text_without_timestamps_is_an_error(tmp_path):
    raw = write_raw(tmp_path, [("我們", 0.0, 0.4)])
    data = json.loads(raw.read_text(encoding="utf-8"))
    del data["segments"]
    raw.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(PipelineError, match="timestamps"):
        load_asr(raw, "m")


def test_truncated_chunk_raises(tmp_path):
    raw = write_raw(tmp_path, [("我們", 0.0, 0.4)], chunks=[{"truncated": True}])
    with pytest.raises(AsrTruncatedError):
        load_asr(raw, "m")


def test_truncated_later_chunk_raises(tmp_path):
    raw = write_raw(tmp_path, [("我們", 0.0, 0.4)], chunks=[{}, {"truncated": True}])
    with pytest.raises(AsrTruncatedError, match="1"):
        load_asr(raw, "m")


def test_untruncated_chunks_load(tmp_path):
    raw = write_raw(tmp_path, [("我們", 0.0, 0.4)], chunks=[{}, {}])
    assert len(load_asr(raw, "m").tokens) == 2


@pytest.mark.parametrize("content", ["not json", "[1, 2]", '{"language": "Chinese"}'])
def test_unreadable_result_is_a_pipeline_error(tmp_path, content):
    raw = tmp_path / "raw.json"
    raw.write_text(content, encoding="utf-8")
    with pytest.raises(PipelineError):
        load_asr(raw, "m")


def test_missing_result_is_a_pipeline_error(tmp_path):
    with pytest.raises(PipelineError):
        load_asr(tmp_path / "nope.json", "m")


# ---- transcribe_audio: fallback and cache ---------------------------------------------------


def _audio(tmp_path: Path) -> Path:
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"x")
    return audio


def test_falls_back_to_small_model_once(tmp_path):
    calls = []

    def runner(audio, out_dir, model_id):
        calls.append(model_id)
        if model_id.endswith("1.7B"):
            raise PipelineError("killed")
        return write_raw(out_dir, [("我", 0, 0.1)])

    audio = tmp_path / "a.wav"
    audio.write_bytes(b"x")
    _, model, fallback = transcribe_audio(audio, tmp_path / "asr", runner=runner)
    assert (calls, model, fallback) == ([MODELS["1.7b"], MODELS["0.6b"]], MODELS["0.6b"], True)


def test_both_models_fail(tmp_path):
    calls = []

    def runner(*a):
        calls.append(a[2])
        raise PipelineError("killed")

    audio = tmp_path / "a.wav"
    audio.write_bytes(b"x")
    with pytest.raises(PipelineError):
        transcribe_audio(audio, tmp_path / "asr", runner=runner)
    assert calls == [MODELS["1.7b"], MODELS["0.6b"]]  # the small model is not retried


def test_small_model_failure_alone_is_not_retried(tmp_path):
    calls = []

    def runner(*a):
        calls.append(a[2])
        raise PipelineError("killed")

    with pytest.raises(PipelineError):
        transcribe_audio(_audio(tmp_path), tmp_path / "asr", model_key="0.6b", runner=runner)
    assert calls == [MODELS["0.6b"]]


def test_failure_message_names_both_attempts(tmp_path):
    def runner(audio, out_dir, model_id):
        raise PipelineError("killed " + model_id)

    with pytest.raises(PipelineError) as caught:
        transcribe_audio(_audio(tmp_path), tmp_path / "asr", runner=runner)
    assert MODELS["1.7b"] in caught.value.message and MODELS["0.6b"] in caught.value.message


def test_unknown_model_key_is_refused(tmp_path):
    with pytest.raises(ValueError):
        transcribe_audio(_audio(tmp_path), tmp_path / "asr", model_key="9b")


def test_result_lands_in_a_folder_named_by_model_and_audio_hash(tmp_path):
    audio = _audio(tmp_path)

    def runner(audio, out_dir, model_id):
        return write_raw(out_dir, [("我", 0, 0.1)], stem=audio.stem)

    raw, model, fallback = transcribe_audio(audio, tmp_path / "asr", runner=runner)
    assert raw == tmp_path / "asr" / f"1.7b-{file_sha256(audio)[:12]}" / "a.json"
    assert (model, fallback) == (MODELS["1.7b"], False)


def test_cached_result_is_not_run_again(tmp_path):
    audio, calls = _audio(tmp_path), []

    def runner(audio, out_dir, model_id):
        calls.append(model_id)
        return write_raw(out_dir, [("我", 0, 0.1)], stem=audio.stem)

    first = transcribe_audio(audio, tmp_path / "asr", runner=runner)
    second = transcribe_audio(audio, tmp_path / "asr", runner=runner)
    assert first == second and calls == [MODELS["1.7b"]]


def test_unparsable_cached_result_is_run_again(tmp_path):
    audio, calls = _audio(tmp_path), []
    folder = tmp_path / "asr" / f"1.7b-{file_sha256(audio)[:12]}"
    folder.mkdir(parents=True)
    (folder / "a.json").write_text('{"text": "cut off mid-wri', encoding="utf-8")

    def runner(audio, out_dir, model_id):
        calls.append(model_id)
        return write_raw(out_dir, [("我", 0, 0.1)], stem=audio.stem)

    raw, _, _ = transcribe_audio(audio, tmp_path / "asr", runner=runner)
    assert calls == [MODELS["1.7b"]] and json.loads(raw.read_text(encoding="utf-8"))["text"] == "我"


def test_invalid_cached_result_is_redone(tmp_path):
    audio, calls = _audio(tmp_path), []
    folder = tmp_path / "asr" / f"1.7b-{file_sha256(audio)[:12]}"
    folder.mkdir(parents=True)
    cached = folder / "a.json"
    cached.write_text("{}", encoding="utf-8")  # a JSON object, but not an ASR result: load_asr refuses it

    def runner(audio, out_dir, model_id):
        calls.append(model_id)
        return write_raw(out_dir, [("我", 0, 0.1)], stem=audio.stem)

    raw, model, fallback = transcribe_audio(audio, tmp_path / "asr", runner=runner)
    assert calls == [MODELS["1.7b"]] and (model, fallback) == (MODELS["1.7b"], False)
    assert raw == cached and json.loads(cached.read_text(encoding="utf-8"))["text"] == "我"  # replaced, not kept


@pytest.mark.parametrize(
    "content",
    ['{"text": "x", "segments": 5}', '{"text": "", "segments": 5}', '{"text": "x", "chunks": 5}'],
    ids=["segments-number", "empty-text-segments-number", "chunks-number"],
)
def test_cached_result_of_the_wrong_shape_is_redone(tmp_path, content):
    audio, calls = _audio(tmp_path), []
    folder = tmp_path / "asr" / f"1.7b-{file_sha256(audio)[:12]}"
    folder.mkdir(parents=True)
    cached = folder / "a.json"
    cached.write_text(content, encoding="utf-8")  # a JSON object with text, but a field of the wrong type

    def runner(audio, out_dir, model_id):
        calls.append(model_id)
        return write_raw(out_dir, [("我", 0, 0.1)], stem=audio.stem)

    raw, model, fallback = transcribe_audio(audio, tmp_path / "asr", runner=runner)
    assert calls == [MODELS["1.7b"]] and (model, fallback) == (MODELS["1.7b"], False)
    assert raw == cached and json.loads(cached.read_text(encoding="utf-8"))["text"] == "我"  # replaced, not kept


def test_a_fresh_result_of_the_wrong_shape_counts_as_a_failure(tmp_path):
    calls = []

    def runner(audio, out_dir, model_id):
        calls.append(model_id)
        if model_id.endswith("1.7B"):
            out_dir.mkdir(parents=True, exist_ok=True)
            bad = out_dir / "a.json"
            bad.write_text('{"text": "x", "segments": 5}', encoding="utf-8")
            return bad
        return write_raw(out_dir, [("我", 0, 0.1)], stem="a")

    _, model, fallback = transcribe_audio(_audio(tmp_path), tmp_path / "asr", runner=runner)
    assert (calls, model, fallback) == ([MODELS["1.7b"], MODELS["0.6b"]], MODELS["0.6b"], True)


def test_truncated_cached_result_stops_with_asr_truncated_error(tmp_path):
    # A truncated result is a complete record of what the engine did, and it comes from the length of the
    # audio: running again or asking the 0.6B model would not help. It is not an invalid cache entry.
    audio, calls = _audio(tmp_path), []
    folder = tmp_path / "asr" / f"1.7b-{file_sha256(audio)[:12]}"
    write_raw(folder, [("我們", 0.0, 0.4)], chunks=[{"truncated": True}], stem="a")

    def runner(audio, out_dir, model_id):
        calls.append(model_id)
        return write_raw(out_dir, [("我", 0, 0.1)], stem=audio.stem)

    with pytest.raises(AsrTruncatedError):
        transcribe_audio(audio, tmp_path / "asr", runner=runner)
    assert calls == []  # no re-run and no fallback


def test_truncated_fresh_result_stops_with_asr_truncated_error(tmp_path):
    calls = []

    def runner(audio, out_dir, model_id):
        calls.append(model_id)
        return write_raw(out_dir, [("我們", 0.0, 0.4)], chunks=[{}, {"truncated": True}], stem=audio.stem)

    with pytest.raises(AsrTruncatedError, match="chunk 1"):
        transcribe_audio(_audio(tmp_path), tmp_path / "asr", runner=runner)
    assert calls == [MODELS["1.7b"]]  # the 0.6B model is not tried


@pytest.mark.parametrize("content", ["{", "{}"])
def test_a_result_that_load_asr_refuses_counts_as_a_failure(tmp_path, content):
    calls = []

    def runner(audio, out_dir, model_id):
        calls.append(model_id)
        if model_id.endswith("1.7B"):
            out_dir.mkdir(parents=True, exist_ok=True)
            bad = out_dir / "a.json"
            bad.write_text(content, encoding="utf-8")
            return bad
        return write_raw(out_dir, [("我", 0, 0.1)], stem="a")

    _, model, fallback = transcribe_audio(_audio(tmp_path), tmp_path / "asr", runner=runner)
    assert (calls, model, fallback) == ([MODELS["1.7b"], MODELS["0.6b"]], MODELS["0.6b"], True)


def test_fallback_result_has_its_own_folder_and_is_reused(tmp_path):
    audio, calls = _audio(tmp_path), []

    def runner(audio, out_dir, model_id):
        calls.append(model_id)
        if model_id.endswith("1.7B"):
            raise PipelineError("killed")
        return write_raw(out_dir, [("我", 0, 0.1)], stem=audio.stem)

    first_raw, _, _ = transcribe_audio(audio, tmp_path / "asr", runner=runner)
    assert first_raw.parent.name == f"0.6b-{file_sha256(audio)[:12]}"
    # The 1.7B model is tried again (it may have failed only for lack of memory), but the 0.6B
    # result already on disk is used instead of running 0.6B a second time.
    second = transcribe_audio(audio, tmp_path / "asr", runner=runner)
    assert second == (first_raw, MODELS["0.6b"], True)
    assert calls == [MODELS["1.7b"], MODELS["0.6b"], MODELS["1.7b"]]


def test_small_model_asked_for_directly_is_cached_and_is_not_a_fallback(tmp_path):
    audio, calls = _audio(tmp_path), []

    def runner(audio, out_dir, model_id):
        calls.append(model_id)
        return write_raw(out_dir, [("我", 0, 0.1)], stem=audio.stem)

    transcribe_audio(audio, tmp_path / "asr", model_key="0.6b", runner=runner)
    raw, model, fallback = transcribe_audio(audio, tmp_path / "asr", model_key="0.6b", runner=runner)
    assert (model, fallback) == (MODELS["0.6b"], False) and calls == [MODELS["0.6b"]]


def test_two_audios_share_a_cache_dir(tmp_path):
    first, second = tmp_path / "one" / "a.wav", tmp_path / "two" / "a.wav"  # the same file name on purpose
    for audio, content in ((first, b"x"), (second, b"y")):
        audio.parent.mkdir()
        audio.write_bytes(content)
    runs = []

    def runner(audio, out_dir, model_id):
        runs.append(audio)
        return write_raw(out_dir, [(audio.read_bytes().decode(), 0, 0.1)], stem=audio.stem)

    cache = tmp_path / "asr"
    raw_one = transcribe_audio(first, cache, runner=runner)[0]
    raw_two = transcribe_audio(second, cache, runner=runner)[0]
    assert raw_one != raw_two and (raw_one.parent.parent, raw_two.parent.parent) == (cache, cache)
    assert [load_asr(raw, "m").text for raw in (raw_one, raw_two)] == ["x", "y"]
    # each result is cached under its own audio: asking again runs nothing
    assert transcribe_audio(first, cache, runner=runner)[0] == raw_one
    assert transcribe_audio(second, cache, runner=runner)[0] == raw_two
    assert runs == [first, second]


def test_both_models_failing_names_both_logs(tmp_path, monkeypatch):
    _fake_cli(tmp_path, monkeypatch, exit_code=3)
    audio = _wav(tmp_path / "a.wav")

    def runner(audio, out_dir, model_id):
        return run_asr(audio, out_dir, model_id, timeout_s=30)

    with pytest.raises(PipelineError) as caught:
        transcribe_audio(audio, tmp_path / "asr", runner=runner)
    digest = file_sha256(audio)[:12]
    for key in ("1.7b", "0.6b"):
        assert str(tmp_path / "asr" / f"{key}-{digest}" / "a.asr.log") in caught.value.message


# ---- run_asr: the command line, the environment, failures -----------------------------------

FAKE_CLI = """#!{python}
import json, os, sys, time
from pathlib import Path

here = Path(sys.argv[0]).resolve().parent
args = sys.argv[1:]
model = args[args.index("--model") + 1]
(here / "call.json").write_text(json.dumps({{
    "args": args,
    "hf_hub_offline": os.environ.get("HF_HUB_OFFLINE"),
    "sentinel": os.environ.get("REELS_TEST_SENTINEL"),
    "hf_home": os.environ.get("HF_HOME"),
}}))
if {sleep_for!r} and {sleep_for!r} in model:
    time.sleep(60)  # a hung engine; run_asr is expected to kill it long before this ends
if {write_json!r}:  # like the real tool, the result is written before it exits
    out_dir = Path(args[args.index("-o") + 1])
    (out_dir / (Path(args[0]).stem + ".json")).write_text({result_text!r})
sys.stderr.write({stderr_text!r})
for i in range({noise_lines}):
    sys.stderr.write("noise-%05d " % i + "x" * 88 + "\\n")
sys.stderr.flush()
if {kill_with!r}:  # ended by a signal, as the kernel ends a process that took too much memory
    import signal
    os.kill(os.getpid(), getattr(signal, {kill_with!r}))
if {exit_code}:
    sys.exit({exit_code})
"""
VALID_RESULT = '{"text": "", "language": "Chinese"}'


def _fake_cli(
    tmp_path: Path, monkeypatch, exit_code: int = 0, write_json: bool = True, result_text: str = VALID_RESULT,
    sleep_for: str = "", stderr_text: str | None = None, noise_lines: int = 0, kill_with: str = "",
) -> Path:
    """A stand-in for mlx-qwen3-asr that records how it was called, and make run_asr use it.
    It writes `result_text` as the result (unless `write_json` is False), then writes `stderr_text` (by
    default one error line when `exit_code` is set, nothing otherwise) and `noise_lines` lines of 100
    bytes to stderr, then ends itself with the signal named `kill_with` or exits with `exit_code`. It
    hangs when the model id contains `sleep_for`."""
    if stderr_text is None:
        stderr_text = "Error: model not found\n" if exit_code else ""
    folder = tmp_path / "fake-cli"
    folder.mkdir()
    script = folder / "mlx-qwen3-asr"
    script.write_text(FAKE_CLI.format(
        python=sys.executable, exit_code=exit_code, write_json=write_json, result_text=result_text,
        sleep_for=sleep_for, stderr_text=stderr_text, noise_lines=noise_lines, kill_with=kill_with,
    ))
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setattr(asr, "asr_cli", lambda: script)
    return folder / "call.json"


def _wav(path: Path, seconds: float = 1.0) -> Path:
    """A silent 16 kHz mono WAV of `seconds`: run_asr reads the length of its audio."""
    sf.write(str(path), np.zeros(int(16000 * seconds), dtype=np.int16), 16000, subtype="PCM_16")
    return path


def test_run_asr_command_line_and_environment(tmp_path, monkeypatch):
    call = _fake_cli(tmp_path, monkeypatch)
    monkeypatch.setenv("REELS_TEST_SENTINEL", "kept")
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    audio = _wav(tmp_path / "voice.16k.wav")
    out_dir = tmp_path / "out"

    result = run_asr(audio, out_dir, MODELS["1.7b"])

    assert result == out_dir / "voice.16k.json" and result.is_file()
    recorded = json.loads(call.read_text())
    assert recorded["args"] == [
        str(audio), "--model", "Qwen/Qwen3-ASR-1.7B", "--language", "Chinese", "--timestamps",
        "-f", "json", "-o", str(out_dir), "--no-progress",
    ]
    assert recorded["hf_hub_offline"] == "1"  # a missing model fails fast instead of downloading
    assert recorded["sentinel"] == "kept"  # the rest of the environment is passed on
    assert recorded["hf_home"] == os.environ["HF_HOME"]


def test_run_asr_failure_reports_exit_code_and_last_line(tmp_path, monkeypatch):
    _fake_cli(tmp_path, monkeypatch, exit_code=3)
    audio = _wav(tmp_path / "a.wav")
    with pytest.raises(PipelineError) as caught:
        run_asr(audio, tmp_path / "out", MODELS["1.7b"])
    assert "exit code 3" in caught.value.message and "model not found" in caught.value.message
    assert "model not found" in Path(caught.value.extra["log"]).read_text()


def test_signal_exit_is_named(tmp_path, monkeypatch):
    _fake_cli(tmp_path, monkeypatch, kill_with="SIGKILL")
    with pytest.raises(PipelineError) as caught:
        run_asr(_wav(tmp_path / "a.wav"), tmp_path / "out", MODELS["1.7b"])
    assert "killed by signal 9 (SIGKILL)" in caught.value.message
    assert "possibly out of memory" in caught.value.message
    assert not (tmp_path / "out" / "a.json").exists()


def test_another_signal_is_named_without_the_memory_hint(tmp_path, monkeypatch):
    _fake_cli(tmp_path, monkeypatch, kill_with="SIGTERM")
    with pytest.raises(PipelineError) as caught:
        run_asr(_wav(tmp_path / "a.wav"), tmp_path / "out", MODELS["1.7b"])
    assert "killed by signal 15 (SIGTERM)" in caught.value.message
    assert "out of memory" not in caught.value.message


def test_failure_message_has_last_log_lines(tmp_path, monkeypatch):
    lines = [f"log-line-{i:02d}" for i in range(1, 11)]
    _fake_cli(tmp_path, monkeypatch, exit_code=1, stderr_text="\n".join(lines) + "\n")
    with pytest.raises(PipelineError) as caught:
        run_asr(_wav(tmp_path / "a.wav"), tmp_path / "out", MODELS["1.7b"])
    message = caught.value.message
    assert all(line in message for line in lines[5:])  # lines 6 to 10
    assert lines[4] not in message  # line 5 is the sixth from the end


def test_failure_message_skips_blank_log_lines(tmp_path, monkeypatch):
    _fake_cli(tmp_path, monkeypatch, exit_code=1, stderr_text="first\n\n  \nsecond\n\n")
    with pytest.raises(PipelineError) as caught:
        run_asr(_wav(tmp_path / "a.wav"), tmp_path / "out", MODELS["1.7b"])
    assert "first" in caught.value.message and "second" in caught.value.message


def test_large_stderr(tmp_path, monkeypatch):
    # Over a megabyte of engine output: the run ends within its timeout (the output goes to a file, so
    # nothing waits for a reader of a full pipe) and the message holds the last lines, not the log.
    _fake_cli(tmp_path, monkeypatch, exit_code=1, stderr_text="", noise_lines=11_000)
    started = time.monotonic()
    with pytest.raises(PipelineError) as caught:
        run_asr(_wav(tmp_path / "a.wav"), tmp_path / "out", MODELS["1.7b"], timeout_s=30)
    assert time.monotonic() - started < 30
    message = caught.value.message
    assert "exit code 1" in message and "timed out" not in message
    assert "noise-10999" in message and "noise-10995" in message and "noise-10994" not in message
    assert len(message) < 1000
    assert Path(caught.value.extra["log"]).stat().st_size > 1_000_000  # the log itself keeps everything


def test_run_asr_without_a_result_file_is_an_error(tmp_path, monkeypatch):
    _fake_cli(tmp_path, monkeypatch, write_json=False)
    audio = _wav(tmp_path / "a.wav")
    with pytest.raises(PipelineError, match="no result"):
        run_asr(audio, tmp_path / "out", MODELS["1.7b"])


def test_run_asr_does_not_trust_an_old_result_file(tmp_path, monkeypatch):
    _fake_cli(tmp_path, monkeypatch, write_json=False)
    audio = _wav(tmp_path / "a.wav")
    stale = write_raw(tmp_path / "out", [("我", 0, 0.1)], stem="a")
    with pytest.raises(PipelineError, match="no result"):
        run_asr(audio, tmp_path / "out", MODELS["1.7b"])
    assert not stale.exists()


def test_run_asr_missing_cli_is_a_pipeline_error(tmp_path, monkeypatch):
    monkeypatch.setattr(asr, "asr_cli", lambda: tmp_path / "no-such-cli")
    audio = _wav(tmp_path / "a.wav")
    with pytest.raises(PipelineError, match="mlx-qwen3-asr"):
        run_asr(audio, tmp_path / "out", MODELS["1.7b"])


def test_run_asr_failed_run_leaves_no_result_behind(tmp_path, monkeypatch):
    # The engine wrote a complete result and then died (exit 139 is a segfault), so a later
    # transcribe_audio call must not find that file and take it for a finished run.
    _fake_cli(tmp_path, monkeypatch, exit_code=139)
    audio = _wav(tmp_path / "a.wav")
    with pytest.raises(PipelineError, match="exit code 139"):
        run_asr(audio, tmp_path / "out", MODELS["1.7b"])
    assert not (tmp_path / "out" / "a.json").exists()


def test_run_asr_unparsable_result_is_an_error_and_is_removed(tmp_path, monkeypatch):
    _fake_cli(tmp_path, monkeypatch, result_text="{")
    audio = _wav(tmp_path / "a.wav")
    with pytest.raises(PipelineError, match="not valid JSON"):
        run_asr(audio, tmp_path / "out", MODELS["1.7b"])
    assert not (tmp_path / "out" / "a.json").exists()


def test_interrupt_after_write_leaves_no_result(tmp_path, monkeypatch):
    # The engine's result is complete, and then Ctrl-C arrives: the run did not finish, so no result stays.
    def interrupted(audio16k, out_dir, model_id, log, timeout_s):
        write_raw(out_dir, [("我", 0, 0.1)], stem=audio16k.stem)
        raise KeyboardInterrupt

    monkeypatch.setattr(asr, "_run_engine", interrupted)
    with pytest.raises(KeyboardInterrupt):
        run_asr(_wav(tmp_path / "a.wav"), tmp_path / "out", MODELS["1.7b"])
    assert not (tmp_path / "out" / "a.json").exists()


def test_run_asr_times_out_and_leaves_no_result(tmp_path, monkeypatch):
    _fake_cli(tmp_path, monkeypatch, sleep_for="1.7B")
    audio = _wav(tmp_path / "a.wav")
    started = time.monotonic()
    with pytest.raises(PipelineError, match=r"timed out after 1 s") as caught:
        run_asr(audio, tmp_path / "out", MODELS["1.7b"], timeout_s=1)
    assert time.monotonic() - started < 30  # the hung engine was killed, not waited for
    assert caught.value.extra["log"] == str(tmp_path / "out" / "a.asr.log")
    assert not (tmp_path / "out" / "a.json").exists()


def test_a_hung_large_model_falls_back_to_the_small_one(tmp_path, monkeypatch):
    _fake_cli(tmp_path, monkeypatch, sleep_for="1.7B")
    audio = _wav(tmp_path / "a.wav")

    def runner(audio, out_dir, model_id):
        # only the hung 1.7B run gets a short timeout; the 0.6B run must not time out on a loaded machine
        return run_asr(audio, out_dir, model_id, timeout_s=2 if model_id == MODELS["1.7b"] else 30)

    raw, model, fallback = transcribe_audio(audio, tmp_path / "asr", runner=runner)
    assert (model, fallback) == (MODELS["0.6b"], True) and raw.is_file()


def _spy_on_timeout(monkeypatch) -> list:
    """Record the timeout that run_asr hands to subprocess.run, and run the command as usual."""
    seen = []
    real_run = subprocess.run

    def spy(*args, **kwargs):
        seen.append(kwargs.get("timeout"))
        return real_run(*args, **kwargs)

    monkeypatch.setattr(asr.subprocess, "run", spy)
    return seen


def test_run_asr_default_timeout_is_300_s_plus_3_s_per_audio_second(tmp_path, monkeypatch):
    _fake_cli(tmp_path, monkeypatch)
    seen = _spy_on_timeout(monkeypatch)
    run_asr(_wav(tmp_path / "a.wav", seconds=10), tmp_path / "out", MODELS["1.7b"])
    assert seen == pytest.approx([330.0])


def test_run_asr_timeout_can_be_overridden(tmp_path, monkeypatch):
    _fake_cli(tmp_path, monkeypatch)
    seen = _spy_on_timeout(monkeypatch)
    run_asr(_wav(tmp_path / "a.wav", seconds=10), tmp_path / "out", MODELS["1.7b"], timeout_s=7)
    assert seen == [7]


def test_run_asr_audio_that_cannot_be_measured_is_a_pipeline_error(tmp_path, monkeypatch):
    _fake_cli(tmp_path, monkeypatch)
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"not a wav file")
    with pytest.raises(PipelineError, match="a.wav"):
        run_asr(audio, tmp_path / "out", MODELS["1.7b"])


def test_asr_cli_sits_next_to_the_running_python():
    assert asr.asr_cli() == Path(sys.executable).parent / "mlx-qwen3-asr"


# ---- the real engine --------------------------------------------------------------------------


@pytest.mark.slow
def test_real_engine_matches_recording(tmp_path):
    wav16 = to_16k_mono(FIXTURES / "meijia" / "fixture.wav", tmp_path)  # helper in media_fixtures
    new = load_asr(run_asr(wav16, tmp_path, MODELS["1.7b"]), MODELS["1.7b"]).tokens
    old = load_asr(RAW, MODELS["1.7b"]).tokens
    assert [t.text for t in new] == [t.text for t in old]
    assert all(abs(a.start - b.start) <= 0.08 for a, b in zip(new, old))
