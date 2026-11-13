"""Qwen3-ASR via mlx-qwen3-asr CLI -> SPIKE_OUT/asr/qwen3-{0.6b,1.7b}.json (throwaway spike code).

usage: asr_qwen.py --model Qwen/Qwen3-ASR-1.7B [audio.wav] [out.json]
"""
import argparse
import json
import sys
from importlib.metadata import version
from pathlib import Path

import soundfile as sf

from common import SPIKE_OUT, run_timed, to_tokens

CLI = str(Path(sys.executable).parent / "mlx-qwen3-asr")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, choices=["Qwen/Qwen3-ASR-0.6B", "Qwen/Qwen3-ASR-1.7B"])
    ap.add_argument("audio", nargs="?", default=str(SPIKE_OUT / "audio16k.wav"))
    ap.add_argument("out", nargs="?")
    a = ap.parse_args()
    engine = "qwen3-" + a.model.split("-")[-1].lower()
    out = Path(a.out) if a.out else SPIKE_OUT / "asr" / f"{engine}.json"
    raw_dir = out.parent / f"raw-{engine}"
    raw_dir.mkdir(parents=True, exist_ok=True)
    timing = run_timed([CLI, a.audio, "--model", a.model, "--language", "Chinese", "--timestamps",
                        "-f", "json", "-o", str(raw_dir), "--no-progress"])
    if timing["returncode"] != 0:
        sys.exit(f"error: mlx-qwen3-asr failed with code {timing['returncode']}")
    raw = json.loads((raw_dir / (Path(a.audio).stem + ".json")).read_text())
    result = {
        "engine": engine,
        "runtime": f"mlx-qwen3-asr {version('mlx-qwen3-asr')} / {a.model} + Qwen/Qwen3-ForcedAligner-0.6B",
        "audio_s": round(sf.info(a.audio).duration, 3),
        "wall_s": timing["wall_s"],
        "peak_mb": timing["peak_mb"],
        "truncated": any(c.get("truncated") for c in raw.get("chunks", [])),
        "text": raw["text"],
        "tokens": to_tokens([(s["text"], s["start"], s.get("end")) for s in raw.get("segments", [])]),
    }
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1))
    print(json.dumps({k: result[k] for k in ("engine", "audio_s", "wall_s", "peak_mb", "truncated")}),
          len(result["tokens"]), "tokens")


if __name__ == "__main__":
    main()
