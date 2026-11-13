#!/bin/bash
# ffmpeg benchmarks for the phase 0 spike (throwaway code).
#   ffmpeg_bench.sh enc <video>        libx264 CRF 18 vs h264_videotoolbox 12M, VMAF vs lossless reference
#   ffmpeg_bench.sh tonemap <hdr> [s]  HDR -> SDR working file at source size and at 1350x2400 (first s seconds)
#   ffmpeg_bench.sh ass                burn a Traditional Chinese ASS line
#   ffmpeg_bench.sh merge              merge the parts into bench.json
set -euo pipefail

: "${REELS_STUDIO:?set REELS_STUDIO to the studio folder of the main checkout}"
case "$REELS_STUDIO" in /*) ;; *) echo "error: REELS_STUDIO must be absolute" >&2; exit 1 ;; esac

HERE="$(cd "$(dirname "$0")" && pwd)"
PY="$HERE/.venv/bin/python"
FFDIR="$(brew --prefix ffmpeg-full)/bin"
FF="$FFDIR/ffmpeg"
OUT="$REELS_STUDIO/projects/_spike/ffmpeg"
FONTS="$REELS_STUDIO/projects/_spike/fonts"
mkdir -p "$OUT"

# timed <json-key-prefix> <cmd...> : run under /usr/bin/time -l, append wall/peak to $OUT/times.txt
timed() {
  local key="$1"; shift
  /usr/bin/time -l -o "$OUT/.time" "$@"
  local wall peak
  wall=$(awk '/ real /{print $1}' "$OUT/.time")
  peak=$(awk '/peak memory footprint/{printf "%.1f", $1/1048576}' "$OUT/.time")
  echo "$key $wall $peak" >> "$OUT/times.txt"
  echo "$wall"
}

write_json() { # write_json <file> <python-dict-expression using variables passed as argv>
  local file="$1"; shift
  "$PY" -c "import json,sys; a=sys.argv[1:]; json.dump(eval(a[0]), open('$file','w'), indent=2)" "$@"
}

cmd_enc() {
  local src="$1"
  local ref="$OUT/enc-ref-lossless.mkv"
  # the reference is what the encoders get in the real pipeline: SDR 8-bit 1080x1920 30 fps
  local vf="scale=1080:1920:flags=lanczos,fps=30,format=yuv420p"
  if "$PY" "$HERE/probe.py" "$src" | grep -q '"is_hdr": true'; then vf="$TONEMAP,$vf"; fi
  "$FF" -hide_banner -loglevel error -y -t 60 -i "$src" -map 0:v:0 \
    -vf "$vf" -c:v libx264 -qp 0 -preset ultrafast -an "$ref"
  local x264_s vt_s
  x264_s=$(timed x264 "$FF" -hide_banner -loglevel error -y -i "$ref" \
    -c:v libx264 -preset medium -crf 18 -pix_fmt yuv420p "$OUT/enc-x264.mp4")
  vt_s=$(timed vt "$FF" -hide_banner -loglevel error -y -i "$ref" \
    -c:v h264_videotoolbox -b:v 12M -pix_fmt yuv420p "$OUT/enc-vt.mp4")
  vmaf() {
    # pair frames by index, not by timestamp: mkv and mp4 timestamps differ slightly after fps=30
    "$FF" -hide_banner -loglevel error -i "$1" -i "$ref" \
      -lavfi "[0:v]setpts=N/(30*TB)[d];[1:v]setpts=N/(30*TB)[r];[d][r]libvmaf=log_fmt=json:log_path=$2:n_threads=8" -f null -
    "$PY" -c "import json,sys; print(round(json.load(open(sys.argv[1]))['pooled_metrics']['vmaf']['mean'],2))" "$2"
  }
  local xv vv selfv
  selfv=$(vmaf "$ref" "$OUT/vmaf-self.json")   # harness check: reference vs itself must be ~100
  echo "vmaf self-check: $selfv"
  xv=$(vmaf "$OUT/enc-x264.mp4" "$OUT/vmaf-x264.json")
  vv=$(vmaf "$OUT/enc-vt.mp4" "$OUT/vmaf-vt.json")
  local xs vs rs
  xs=$(stat -f %z "$OUT/enc-x264.mp4"); vs=$(stat -f %z "$OUT/enc-vt.mp4"); rs=$(stat -f %z "$ref")
  echo "lossless reference was $((rs / 1000000)) MB; removing it (measured)"
  rm -f "$ref"
  write_json "$OUT/enc.json" \
    "dict(x264_s=float(a[1]), vt_s=float(a[2]), x264_vmaf=float(a[3]), vt_vmaf=float(a[4]), x264_mb=round(int(a[5])/1e6,1), vt_mb=round(int(a[6])/1e6,1), vmaf_self=float(a[7]))" \
    "$x264_s" "$vt_s" "$xv" "$vv" "$xs" "$vs" "$selfv"
  cat "$OUT/enc.json"
}

TONEMAP='zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p'

cmd_tonemap() {
  local src="$1" limit="${2:-}"
  local dur
  dur=$("$PY" "$HERE/probe.py" "$src" | "$PY" -c "import json,sys; print(json.load(sys.stdin)['duration_s'])")
  local tin=()
  if [ -n "$limit" ]; then tin=(-t "$limit"); dur=$("$PY" -c "print(min($dur, $limit))"); fi
  local full_s s1350_s
  # -map 0:a:0: iPhone clips also carry an APAC spatial-audio track that mp4 cannot hold
  full_s=$(timed tonemap "$FF" -hide_banner -loglevel error -y "${tin[@]}" -i "$src" -map 0:v:0 -map 0:a:0 -vf "$TONEMAP" \
    -c:v libx264 -crf 16 -preset fast -c:a copy "$OUT/tonemap-full.mp4")
  s1350_s=$(timed tonemap1350 "$FF" -hide_banner -loglevel error -y "${tin[@]}" -i "$src" -map 0:v:0 -map 0:a:0 \
    -vf "$TONEMAP,scale=1350:2400:flags=lanczos" -c:v libx264 -crf 16 -preset fast -c:a copy "$OUT/tonemap-1350.mp4")
  "$FF" -hide_banner -loglevel error -y -ss 3 -i "$src" -frames:v 1 -vf "scale=1080:1920" "$OUT/frame-raw.png"
  "$FF" -hide_banner -loglevel error -y -ss 3 -i "$OUT/tonemap-full.mp4" -frames:v 1 -vf "scale=1080:1920" "$OUT/frame-full.png"
  "$FF" -hide_banner -loglevel error -y -ss 3 -i "$OUT/tonemap-1350.mp4" -frames:v 1 -vf "scale=1080:1920" "$OUT/frame-1350.png"
  "$FF" -hide_banner -loglevel error -y -i "$OUT/frame-raw.png" -i "$OUT/frame-full.png" -i "$OUT/frame-1350.png" \
    -filter_complex "hstack=inputs=3,scale=1620:960" "$OUT/tonemap-compare.png"
  local fs ss
  fs=$(stat -f %z "$OUT/tonemap-full.mp4"); ss=$(stat -f %z "$OUT/tonemap-1350.mp4")
  write_json "$OUT/tonemap.json" \
    "dict(src_duration_s=float(a[1]), tonemap_s_per_min=round(float(a[2])/float(a[1])*60,1), tonemap_mb_per_min=round(int(a[3])/1e6/float(a[1])*60,1), tonemap_1350_s_per_min=round(float(a[4])/float(a[1])*60,1), tonemap_1350_mb_per_min=round(int(a[5])/1e6/float(a[1])*60,1))" \
    "$dur" "$full_s" "$fs" "$s1350_s" "$ss"
  cat "$OUT/tonemap.json"
  echo "compare image (raw | full | 1350): $OUT/tonemap-compare.png"
}

cmd_ass() {
  local ass="$OUT/ass-test.ass"
  cat > "$ass" <<'EOF'
[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Regular,Noto Sans TC,72,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,4,0,2,65,65,900,1
Style: Bold,Noto Sans TC,72,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,4,0,2,65,65,760,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:05.00,Regular,,0,0,0,,這是繁體中文字幕測試：臺灣
Dialogue: 0,0:00:00.00,0:00:05.00,Bold,,0,0,0,,這是繁體中文字幕測試：臺灣
EOF
  "$FF" -hide_banner -loglevel verbose -y -f lavfi -i "color=c=0x3366CC:s=1080x1920:r=30:d=5" \
    -vf "subtitles=$ass:fontsdir=$FONTS" -c:v libx264 -crf 18 -pix_fmt yuv420p "$OUT/ass-test.mp4" 2> "$OUT/ass-test.log"
  "$FF" -hide_banner -loglevel error -y -ss 2.5 -i "$OUT/ass-test.mp4" -frames:v 1 "$OUT/ass-frame.png"
  # libass falls back to system fonts silently. Fonts loaded from fontsdir show up without a path
  # ("-> NotoSansTC-Bold, 0, ..."); system fallbacks show a file path. Require every hit to be ours.
  grep 'fontselect:' "$OUT/ass-test.log" | sed 's/.*fontselect: //' | sort -u > "$OUT/ass-fontselect.txt"
  "$PY" - "$OUT" "$FONTS" <<'EOF'
import json, sys, pathlib
out = pathlib.Path(sys.argv[1])
hits = [l for l in (out / "ass-fontselect.txt").read_text().splitlines() if l]
targets = [l.split("->", 1)[1].strip() for l in hits]
ok = bool(hits) and all(t.startswith("NotoSansTC-") and "/" not in t for t in targets)
(out / "ass.json").write_text(json.dumps({"ass_font_ok": ok, "ass_fontselect": hits}, indent=2))
print(json.dumps({"ass_font_ok": ok, "ass_fontselect": hits}, indent=2, ensure_ascii=False))
EOF
  echo "frame: $OUT/ass-frame.png  (inspect glyphs, then record ass_ok)"
}

cmd_merge() {
  "$PY" - "$OUT" <<'EOF'
import json, sys, pathlib
out = pathlib.Path(sys.argv[1]); bench = {}
for part in ("enc.json", "tonemap.json", "ass.json"):
    p = out / part
    if p.exists():
        bench.update(json.loads(p.read_text()))
(out / "bench.json").write_text(json.dumps(bench, indent=2))
print(json.dumps(bench, indent=2))
EOF
}

sub="${1:-}"; shift || true
case "$sub" in
  enc) cmd_enc "$@" ;;
  tonemap) cmd_tonemap "$@" ;;
  ass) cmd_ass ;;
  merge) cmd_merge ;;
  *) echo "usage: $0 enc <video> | tonemap <hdr-video> | ass | merge" >&2; exit 2 ;;
esac
