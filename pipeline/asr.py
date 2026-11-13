"""Speech recognition: run mlx-qwen3-asr, read its JSON into timed comparison tokens, fall back to the
small model when the large one fails, and keep results in a cache.

The engine writes one JSON file per audio file (`mlx-qwen3-asr -f json`): "text", "language",
"finish_reason", "truncated", "segments" (one {text, start, end} per aligner token, in seconds) and
"chunks" (one per stretch of audio it decoded, each with its own "truncated" flag). Timestamps sit
on an 80 ms grid and some tokens have zero length.
"""

import json
import os
import signal
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import soundfile as sf

from pipeline.errors import AsrTruncatedError, PipelineError
from pipeline.project import file_sha256
from pipeline.textnorm import Key, split_units, unit_key

MODELS = {"1.7b": "Qwen/Qwen3-ASR-1.7B", "0.6b": "Qwen/Qwen3-ASR-0.6B"}
FALLBACK_KEY = "0.6b"  # what a failed 1.7b run is retried with


@dataclass
class AsrToken:
    text: str  # normalised unit text (NFKC, Latin lower-cased), as split_units gives it
    kind: str  # "cjk", "latin", "num" or "other"
    key: Key
    start: float
    end: float


@dataclass
class AsrResult:
    model: str
    text: str
    tokens: list[AsrToken]


def asr_cli() -> Path:
    """The mlx-qwen3-asr command of the environment that is running this code."""
    return Path(sys.executable).parent / "mlx-qwen3-asr"


TIMEOUT_BASE_S = 300  # model load and start-up
TIMEOUT_PER_AUDIO_S = 3  # phase 0 measured 64 s for 189 s of audio (0.34 s per second), load included


def run_asr(audio16k: Path, out_dir: Path, model_id: str, timeout_s: float | None = None) -> Path:
    """Run the engine on `audio16k` and return `out_dir/<audio file stem>.json`, its raw result.

    HF_HUB_OFFLINE=1 is set for the engine (the rest of the environment is passed on), so a model that
    is not in the Hugging Face cache fails at once instead of downloading gigabytes. The engine's
    output goes to `out_dir/<stem>.asr.log`. The engine is killed after `timeout_s` seconds, by default
    300 plus 3 per second of audio, so a hung or swapping run cannot block the 0.6B fallback forever.

    Raises PipelineError, carrying that log, when the engine cannot run, times out, is ended by a signal,
    exits non-zero or leaves no result or one that is not a JSON object. `<stem>.json` is there
    afterwards only when the engine ran to the end and left a JSON object. On any other way out, an
    exception that is not a PipelineError (Ctrl-C) included, it is removed, so that a result an
    unfinished run left behind is never taken for a finished one."""
    out_dir.mkdir(parents=True, exist_ok=True)
    result = out_dir / f"{audio16k.stem}.json"
    log = out_dir / f"{audio16k.stem}.asr.log"
    result.unlink(missing_ok=True)  # so that a file found afterwards is this run's
    finished = False
    try:
        if timeout_s is None:
            try:
                audio_s = sf.info(str(audio16k)).duration
            except (OSError, RuntimeError) as error:
                raise PipelineError(f"cannot read the length of {audio16k}: {error}") from error
            timeout_s = TIMEOUT_BASE_S + TIMEOUT_PER_AUDIO_S * audio_s
        _run_engine(audio16k, out_dir, model_id, log, timeout_s)
        if not result.is_file():
            raise PipelineError(f"mlx-qwen3-asr left no result at {result}", log=str(log))
        if not _parses(result):
            raise PipelineError(f"the result {result} is not valid JSON", log=str(log))
        finished = True
    finally:
        if not finished:
            result.unlink(missing_ok=True)
    return result


def _run_engine(audio16k: Path, out_dir: Path, model_id: str, log: Path, timeout_s: float) -> None:
    command = [
        str(asr_cli()), str(audio16k), "--model", model_id, "--language", "Chinese", "--timestamps",
        "-f", "json", "-o", str(out_dir), "--no-progress",
    ]
    try:
        with open(log, "wb") as log_file:
            done = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                env={**os.environ, "HF_HUB_OFFLINE": "1"},
                timeout=timeout_s,
            )
    except subprocess.TimeoutExpired as error:
        raise PipelineError(f"mlx-qwen3-asr timed out after {timeout_s:g} s", log=str(log)) from error
    except OSError as error:
        raise PipelineError(f"cannot run mlx-qwen3-asr: {error}", log=str(log)) from error
    if done.returncode != 0:
        if done.returncode < 0:  # subprocess reports an end by signal N as -N
            number = -done.returncode
            hint = ", possibly out of memory" if number == signal.SIGKILL else ""
            ended = f"killed by signal {number} ({signal.Signals(number).name}){hint}"
        else:
            ended = f"failed with exit code {done.returncode}"
        raise PipelineError(f"mlx-qwen3-asr {ended}{_log_tail(log)}", log=str(log))


LOG_TAIL_LINES = 5  # how many of the last non-empty log lines a failure message quotes


def _log_tail(log: Path) -> str:
    """": " and the last non-empty lines of `log` joined with " | ", or "" when it has none."""
    lines = [line.strip() for line in log.read_text(errors="replace").splitlines() if line.strip()]
    return f": {' | '.join(lines[-LOG_TAIL_LINES:])}" if lines else ""


def _parses(raw: Path) -> bool:
    """Whether `raw` is a file holding one JSON object."""
    try:
        return isinstance(json.loads(raw.read_text(encoding="utf-8")), dict)
    except (OSError, ValueError):
        return False


def _refusal(raw: Path) -> str | None:
    """Why load_asr refuses `raw` (missing, not JSON, no text, a malformed segment, a field of the wrong type...),
    or None when it takes it. A truncated result is not refused here: AsrTruncatedError goes on to the caller,
    because it is final."""
    try:
        load_asr(raw, "")
    except AsrTruncatedError:
        raise
    except PipelineError as error:
        return error.message
    except (TypeError, ValueError, KeyError, AttributeError) as error:  # a field of the wrong shape
        return f"{raw} is not an ASR result: {type(error).__name__}: {error}"
    return None


def transcribe_audio(
    audio16k: Path,
    cache_dir: Path,
    model_key: str = "1.7b",
    runner: Callable[[Path, Path, str], Path] = run_asr,
) -> tuple[Path, str, bool]:
    """Recognise `audio16k` and return (raw result path, model id, whether the fallback model was used).

    A model's result lives in `cache_dir/<model_key>-<first 12 hex of the audio's sha256>/` and counts
    as cached only when load_asr takes it; a cached result is not run again, and one that load_asr
    refuses (malformed, not a result at all) is run again and replaced. When the requested model is
    "1.7b" and it fails (the engine fails, or leaves a result that load_asr refuses), the 0.6B model
    is tried once, in its own folder, and a cached 0.6B result is used as is; a failing 0.6B model is
    not retried. The 1.7B model is tried again on every call until it has a cached result, since a
    failure may have been lack of memory at the time. Raises PipelineError when every model tried has
    failed; its message gives each model's failure and the path of the log of each run that has one.

    A truncated result, cached or fresh, is final: AsrTruncatedError is raised at once, with no re-run
    and no fallback, because truncation comes from the length of the audio and the transcript would
    stop short of its end."""
    if model_key not in MODELS:
        raise ValueError(f"unknown ASR model key {model_key!r}; use one of {sorted(MODELS)}")
    digest = file_sha256(audio16k)[:12]
    keys = [model_key, FALLBACK_KEY] if model_key == "1.7b" else [model_key]
    failures: list[PipelineError] = []
    for key in keys:
        folder = cache_dir / f"{key}-{digest}"
        cached = folder / f"{audio16k.stem}.json"
        if _refusal(cached) is None:
            return cached, MODELS[key], key != model_key
        try:
            raw = runner(audio16k, folder, MODELS[key])
        except PipelineError as error:
            failures.append(error)
            continue
        if (problem := _refusal(raw)) is not None:
            failures.append(PipelineError(f"the result at {raw} is not usable: {problem}"))
            continue
        return raw, MODELS[key], key != model_key
    detail = "; ".join(
        f"{MODELS[key]}: {error.message}" + (f" (log: {error.extra['log']})" if "log" in error.extra else "")
        for key, error in zip(keys, failures)
    )
    raise PipelineError(f"speech recognition failed ({detail})", **failures[-1].extra) from failures[-1]


def load_asr(raw: Path, model_id: str) -> AsrResult:
    """Read the engine's JSON in `raw` into timed tokens.

    Every segment is cut into comparison units (split_units); the units of one segment share its time
    equally, so two units of a 0.4 s segment get 0.2 s each. A segment with no unit (punctuation) is
    left out. Raises AsrTruncatedError when any chunk is truncated, because the transcript would then
    stop short of the end of the audio, and PipelineError when the file cannot be read or holds text
    without timestamps."""
    try:
        data = json.loads(raw.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise PipelineError(f"cannot read the ASR result {raw}: {error}") from error
    if not isinstance(data, dict) or not isinstance(data.get("text"), str):
        raise PipelineError(f"{raw} is not an ASR result: no text")
    chunks = data.get("chunks") or []
    truncated = [i for i, chunk in enumerate(chunks) if isinstance(chunk, dict) and chunk.get("truncated")]
    if truncated or data.get("truncated"):
        where = f" (chunk {', '.join(map(str, truncated))})" if truncated else ""
        raise AsrTruncatedError(
            f"the ASR result is truncated{where}: the engine stopped before the end of the audio", raw=str(raw)
        )
    segments = data.get("segments") or []
    if data["text"].strip() and not segments:
        raise PipelineError(f"the ASR result {raw} has text but no timestamps")
    tokens: list[AsrToken] = []
    for segment in segments:
        try:
            text, start, end = str(segment["text"]), float(segment["start"]), float(segment["end"])
        except (KeyError, TypeError, ValueError) as error:
            raise PipelineError(f"{raw} has a malformed segment: {segment!r}") from error
        units = split_units(text)
        for i, unit in enumerate(units):
            lo = start + (end - start) * i / len(units)
            hi = end if i == len(units) - 1 else start + (end - start) * (i + 1) / len(units)
            tokens.append(AsrToken(unit.text, unit.kind, unit_key(unit), lo, hi))
    return AsrResult(model=model_id, text=data["text"], tokens=tokens)
