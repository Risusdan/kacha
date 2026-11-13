"""FireRedASR2-CTC int8 via sherpa-onnx -> SPIKE_OUT/asr/firered2-ctc-int8.json (throwaway spike code).

Audio is cut into chunks of at most 60 s (memory grows fast with input length) at the quietest
10 ms frame between 50 s and 60 s; chunk token times are shifted back onto the full timeline.

usage: asr_firered.py [audio.wav] [out.json]      default audio: SPIKE_OUT/audio16k.wav
"""
import json
import sys
from pathlib import Path

from common import SPIKE_OUT, run_worker, to_tokens

REPO = "csukuangfj2/sherpa-onnx-fire-red-asr2-ctc-zh_en-int8-2026-02-25"
MODEL_DIR = SPIKE_OUT / "models" / REPO.split("/")[1]
SR = 16000


def fetch_model() -> Path:
    from huggingface_hub import snapshot_download
    snapshot_download(REPO, allow_patterns=["model.int8.onnx", "tokens.txt", "README.md"], local_dir=MODEL_DIR)
    return MODEL_DIR


def chunk_bounds(samples, min_s: float = 50.0, max_s: float = 60.0) -> list[tuple[int, int]]:
    import numpy as np
    frame = SR // 100  # 10 ms
    bounds, start, n = [], 0, len(samples)
    while n - start > max_s * SR:
        lo, hi = start + int(min_s * SR), start + int(max_s * SR)
        win = samples[lo:hi]
        energy = (win[: len(win) // frame * frame].reshape(-1, frame) ** 2).mean(axis=1)
        cut = lo + int(np.argmin(energy)) * frame + frame // 2
        bounds.append((start, cut))
        start = cut
    bounds.append((start, n))
    return bounds


def merge_pieces(tokens: list[str], times: list[float]) -> list[tuple[str, float, None]]:
    """Join BPE pieces of Latin words ('▁re' + 'els' -> 'reels'); CJK tokens stay single."""
    words: list[list] = []
    for tok, t in zip(tokens, times):
        piece = tok.lstrip("▁")
        new_word = tok.startswith("▁") or not words or not piece.isascii() or not words[-1][0][-1:].isascii()
        if new_word:
            words.append([piece, t])
        else:
            words[-1][0] += piece
    return [(w, t, None) for w, t in words]


def worker(audio: str, out_json: str) -> None:
    import sherpa_onnx
    import soundfile as sf

    samples, sr = sf.read(audio, dtype="float32")
    assert sr == SR, f"expected 16 kHz audio, got {sr}"
    rec = sherpa_onnx.OfflineRecognizer.from_fire_red_asr_ctc(
        model=str(MODEL_DIR / "model.int8.onnx"), tokens=str(MODEL_DIR / "tokens.txt"), num_threads=4)
    words, texts, has_ts = [], [], True
    bounds = chunk_bounds(samples)
    for a, b in bounds:
        stream = rec.create_stream()
        stream.accept_waveform(SR, samples[a:b])
        rec.decode_stream(stream)
        res = stream.result
        texts.append(res.text)
        if not res.timestamps:
            has_ts = False
            continue
        words += merge_pieces(list(res.tokens), [a / SR + t for t in res.timestamps])
    Path(out_json).write_text(json.dumps({
        "engine": "firered2-ctc-int8",
        "runtime": f"sherpa-onnx {sherpa_onnx.__version__} / {REPO}",
        "audio_s": round(len(samples) / SR, 3),
        "chunks_s": [round((b - a) / SR, 2) for a, b in bounds],
        "timestamps": has_ts,
        "text": "".join(texts),
        "tokens": to_tokens(words),
    }, ensure_ascii=False))


if __name__ == "__main__":
    if sys.argv[1:2] == ["--worker"]:
        worker(sys.argv[2], sys.argv[3])
    else:
        fetch_model()  # download outside the timed run
        audio = sys.argv[1] if len(sys.argv) > 1 else str(SPIKE_OUT / "audio16k.wav")
        out = Path(sys.argv[2]) if len(sys.argv) > 2 else SPIKE_OUT / "asr" / "firered2-ctc-int8.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        r = run_worker(__file__, [audio, str(out)], out)
        print(json.dumps({k: r[k] for k in ("engine", "audio_s", "wall_s", "peak_mb")}), len(r["tokens"]), "tokens")
