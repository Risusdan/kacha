#!/bin/bash
# Download every ASR model and run each engine once on a 10 s clip, so timed runs exclude downloads.
#   warmup.sh <clip16k.wav>        (throwaway spike code)
set -u
: "${REELS_STUDIO:?set REELS_STUDIO to the studio folder of the main checkout}"
HERE="$(cd "$(dirname "$0")" && pwd)"
PY="$HERE/.venv/bin/python"
CLIP="$1"
D="$REELS_STUDIO/projects/_spike/selftest"
mkdir -p "$D"
disk() { echo "  disk free: $(df -h / | awk 'NR==2{print $4}')  $(sysctl -n vm.swapusage)"; }

disk
for m in 0.6B 1.7B; do
  "$PY" "$HERE/asr_qwen.py" --model "Qwen/Qwen3-ASR-$m" "$CLIP" "$D/warm-qwen3-$m.json" > "$D/warm-qwen3-$m.log" 2>&1
  echo "qwen3-$m rc=$?"; tail -1 "$D/warm-qwen3-$m.log"; disk
done
"$PY" "$HERE/asr_breeze.py" "$CLIP" "$D/warm-breeze.json" > "$D/warm-breeze.log" 2>&1
echo "breeze rc=$?"; tail -1 "$D/warm-breeze.log"; disk
"$PY" "$HERE/asr_firered.py" "$CLIP" "$D/warm-firered.json" > "$D/warm-firered.log" 2>&1
echo "firered rc=$?"; tail -1 "$D/warm-firered.log"; disk
