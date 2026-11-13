#!/bin/bash
# Can the system Meijia voice serve as a test fixture? (throwaway spike code)
#   meijia_check.sh [engine ...]      engines: qwen3-0.6b qwen3-1.7b breeze-25-int8 firered2-ctc-int8 (default: all)
# Builds a 3-sentence fixture (sentence 2 spoken twice, 1.2 s pauses), transcribes it, and writes
# SPIKE_OUT/meijia/score.json with CER against the spoken text and the gaps >= 1.0 s in token times.
set -euo pipefail
: "${REELS_STUDIO:?set REELS_STUDIO to the studio folder of the main checkout}"
HERE="$(cd "$(dirname "$0")" && pwd)"
PY="$HERE/.venv/bin/python"
FF="$(brew --prefix ffmpeg-full)/bin/ffmpeg"
OUT="$REELS_STUDIO/projects/_spike/meijia"
mkdir -p "$OUT"

S1="今天我們來聊聊短影音的剪輯流程。"
S2="第一步是把腳本寫好，再開始錄影。"
S3="最後記得加上字幕和背景音樂。"
printf '%s%s%s%s' "$S1" "$S2" "$S2" "$S3" > "$OUT/spoken.txt"
say -v Meijia -o "$OUT/fixture.aiff" "$S1 [[slnc 1200]] $S2 [[slnc 1200]] $S2 [[slnc 1200]] $S3"
"$FF" -hide_banner -loglevel error -y -i "$OUT/fixture.aiff" -ac 1 -ar 16000 -c:a pcm_s16le "$OUT/fixture.wav"

engines=("$@")
[ ${#engines[@]} -eq 0 ] && engines=(qwen3-0.6b qwen3-1.7b breeze-25-int8 firered2-ctc-int8)
for e in "${engines[@]}"; do
  case "$e" in
    qwen3-0.6b) "$PY" "$HERE/asr_qwen.py" --model Qwen/Qwen3-ASR-0.6B "$OUT/fixture.wav" "$OUT/$e.json" ;;
    qwen3-1.7b) "$PY" "$HERE/asr_qwen.py" --model Qwen/Qwen3-ASR-1.7B "$OUT/fixture.wav" "$OUT/$e.json" ;;
    breeze-25-int8) "$PY" "$HERE/asr_breeze.py" "$OUT/fixture.wav" "$OUT/$e.json" ;;
    firered2-ctc-int8) "$PY" "$HERE/asr_firered.py" "$OUT/fixture.wav" "$OUT/$e.json" ;;
    *) echo "unknown engine $e" >&2; exit 2 ;;
  esac
done

cd "$HERE" && "$PY" - "$OUT" "${engines[@]}" <<'EOF'
import json, sys, pathlib
from asr_score import cer_of
from common import normalize_text
out = pathlib.Path(sys.argv[1])
ref = normalize_text((out / "spoken.txt").read_text(encoding="utf-8"))
score = {}
# score every engine that has a result, so running one engine later keeps the others' scores
for e in [n for n in ("qwen3-0.6b", "qwen3-1.7b", "breeze-25-int8", "firered2-ctc-int8") if (out / f"{n}.json").exists()]:
    r = json.loads((out / f"{e}.json").read_text())
    toks = r["tokens"]
    gaps = []
    for a, b in zip(toks, toks[1:]):
        end = a["end"] if a["end"] is not None else a["start"]
        if b["start"] - end >= 1.0:
            gaps.append(round(b["start"] - end, 2))
    score[e] = {"cer": round(cer_of(normalize_text(r["text"]), ref), 4), "text": r["text"],
                "gaps_ge_1s": gaps, "pauses_found": len(gaps), "pauses_expected": 3}
(out / "score.json").write_text(json.dumps(score, indent=2, ensure_ascii=False))
print(json.dumps(score, indent=2, ensure_ascii=False))
EOF
