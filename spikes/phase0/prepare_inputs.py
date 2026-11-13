"""Extract 16 kHz mono audio and build the normalized reference text (throwaway spike code).

usage: prepare_inputs.py <video> <script.txt|script.md>
"""
import json
import subprocess
import sys
from pathlib import Path

import soundfile as sf

from common import FFMPEG, SPIKE_OUT, is_cjk, normalize_text, tokenize
from probe import probe


def main(video: str, script: str) -> None:
    if Path(script).suffix.lower() not in (".txt", ".md"):
        sys.exit("error: script must be .txt or .md (export other formats to plain text first)")
    wav = SPIKE_OUT / "audio16k.wav"
    # -map 0:a:0: iPhone files also carry a 4-channel APAC (spatial audio) track, and ffmpeg's
    # default picks the stream with the most channels; the first audio track is plain AAC stereo.
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", video,
                    "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav)], check=True)
    ref = normalize_text(Path(script).read_text(encoding="utf-8"))
    (SPIKE_OUT / "ref.txt").write_text(ref, encoding="utf-8")
    toks = tokenize(ref)
    stats = {
        "chars": len(toks),
        "cjk_chars": sum(1 for t in toks if len(t) == 1 and is_cjk(t)),
        "digits": sum(1 for t in toks if t.isdigit()),
        "latin_tokens": sum(1 for t in toks if t.isascii() and t.isalpha()),
    }
    (SPIKE_OUT / "ref_stats.json").write_text(json.dumps(stats, indent=2))
    audio_s = sf.info(str(wav)).duration
    video_s = probe(video)["duration_s"]
    print(json.dumps({**stats, "audio_s": round(audio_s, 3), "video_duration_s": video_s,
                      "duration_diff_s": round(abs(audio_s - video_s), 3)}, indent=2))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
