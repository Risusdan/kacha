"""Breeze-ASR-25 (CT2 int8 via faster-whisper) -> SPIKE_OUT/asr/breeze-25-int8.json (throwaway spike code).

usage: asr_breeze.py [audio.wav]      default audio: SPIKE_OUT/audio16k.wav
"""
import json
import sys
from pathlib import Path

from common import SPIKE_OUT, run_worker, to_tokens

REPO = "shdennlin/breeze-asr-25-ct2"


def worker(audio: str, out_json: str) -> None:
    import faster_whisper
    import soundfile as sf
    from faster_whisper import WhisperModel
    from huggingface_hub import snapshot_download

    path = snapshot_download(REPO, allow_patterns=["int8/*"], local_files_only=True)
    model = WhisperModel(f"{path}/int8", device="cpu", compute_type="int8")
    # pass samples, not the path: faster-whisper 1.2.1 decodes paths with an av.open() kwarg PyAV 19 removed
    samples, sr = sf.read(audio, dtype="float32")
    assert sr == 16000 and samples.ndim == 1, "expected 16 kHz mono audio"
    segments, _ = model.transcribe(samples, language="zh", word_timestamps=True, vad_filter=True)
    words, texts = [], []
    for seg in segments:
        texts.append(seg.text)
        words += [(w.word, w.start, w.end) for w in (seg.words or [])]
    Path(out_json).write_text(json.dumps({
        "engine": "breeze-25-int8",
        "runtime": f"faster-whisper {faster_whisper.__version__} / {REPO} int8",
        "audio_s": round(sf.info(audio).duration, 3),
        "text": "".join(texts),
        "tokens": to_tokens(words),
    }, ensure_ascii=False))


if __name__ == "__main__":
    if sys.argv[1:2] == ["--worker"]:
        worker(sys.argv[2], sys.argv[3])
    else:
        from huggingface_hub import snapshot_download
        snapshot_download(REPO, allow_patterns=["int8/*"])  # download outside the timed run
        audio = sys.argv[1] if len(sys.argv) > 1 else str(SPIKE_OUT / "audio16k.wav")
        out = Path(sys.argv[2]) if len(sys.argv) > 2 else SPIKE_OUT / "asr" / "breeze-25-int8.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        r = run_worker(__file__, [audio, str(out)], out)
        print(json.dumps({k: r[k] for k in ("engine", "audio_s", "wall_s", "peak_mb")}), len(r["tokens"]), "tokens")
