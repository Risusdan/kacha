#!/usr/bin/env bash
# Rebuilds the two files in this folder that come from the recording:
#   fixture.wav            the speech, 48 kHz mono 16-bit, cut from a recording of a person reading spoken.txt
#   asr-qwen3-1.7b.json    the raw `mlx-qwen3-asr -f json` result for fixture.wav (Qwen3-ASR 1.7B)
#
#   bash tests/fixtures/meijia/make_fixture.sh <recording>   cuts fixture.wav from the recording, then recognises it
#   bash tests/fixtures/meijia/make_fixture.sh               recognises the fixture.wav already in this folder
#
# KEEP lists the stretches of the recording, in seconds, that fixture.wav is made of, joined in order.
# The first starts where the sound of the first word begins and the last ends as the sound of the last
# word fades, so that the reel's head and tail cut points lie at the ends of the file and in speech
# (the storyboard tests count on both). The three cuts between them take room tone out of pauses the
# speaker held longer than the five events in README.md need: the pause inside the half take, the one
# before 嗯 and the one after it. They belong to the recording the committed fixture.wav was cut from;
# a new recording needs its own.
#
# Needs the project environment (`uv sync`) and the Qwen3-ASR 1.7B and forced aligner models already
# in the Hugging Face cache (HF_HUB_OFFLINE=1, so nothing is downloaded).
#
# It writes only into this folder and into one folder made by mktemp under $TMPDIR (which must be
# set: there is no fallback to a shared folder). fixture.wav is written to fixture.wav.part in this
# folder first and moved into place, so a failed run never leaves a half-written fixture.wav. It
# deletes only the intermediate files it created, by name, and then the folder under $TMPDIR with rmdir.
# `uv run --frozen` takes uv.lock as it is: it never rewrites that file.
set -euo pipefail

KEEP=(2.44:8.09 8.49:12.70 12.94:13.56 14.04:20.56)

if [ -z "${TMPDIR:-}" ]; then
    echo "error: TMPDIR is not set; set it to a scratch folder (this script does not fall back to /tmp)" >&2
    exit 1
fi
if [ "$#" -gt 1 ]; then
    echo "usage: make_fixture.sh [recording]" >&2
    exit 2
fi
recording=""
if [ "$#" -eq 1 ]; then
    if [ ! -f "$1" ]; then
        echo "error: no such recording: $1" >&2
        exit 1
    fi
    recording="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
fi

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$here"
repo="$(cd ../../.. && pwd)"

# ffmpeg is found the same way the pipeline finds it.
ffmpeg="$(uv run --frozen --quiet --project "$repo" \
    python -c 'from pipeline.media import ffmpeg_bin; print(ffmpeg_bin())')"

tmp="$(mktemp -d "$TMPDIR/meijia.XXXXXX")"
cleanup() {
    rm -f "${here:?}/fixture.wav.part"
    rm -f "${tmp:?}/recording.wav" "${tmp:?}/fixture-16k.wav" "${tmp:?}/fixture-16k.json" "${tmp:?}/asr.log"
    rmdir "${tmp:?}" 2>/dev/null || echo "note: left $tmp in place (it holds files this script did not make)" >&2
}
trap cleanup EXIT

# 1. Speech: decode the whole recording to 48 kHz mono first, so the KEEP times count from its first
#    sample whatever the recording's format; then join the KEEP stretches.
if [ -n "$recording" ]; then
    "$ffmpeg" -nostdin -y -hide_banner -loglevel error \
        -i "$recording" -ar 48000 -ac 1 -c:a pcm_s16le "$tmp/recording.wav"
    graph="[0:a]asplit=${#KEEP[@]}"
    for i in "${!KEEP[@]}"; do graph+="[p$i]"; done
    joined=""
    for i in "${!KEEP[@]}"; do
        graph+=";[p$i]atrim=${KEEP[$i]},asetpts=PTS-STARTPTS[k$i]"
        joined+="[k$i]"
    done
    graph+=";${joined}concat=n=${#KEEP[@]}:v=0:a=1[out]"
    "$ffmpeg" -nostdin -y -hide_banner -loglevel error \
        -i "$tmp/recording.wav" -filter_complex "$graph" -map "[out]" \
        -ar 48000 -ac 1 -c:a pcm_s16le -f wav fixture.wav.part
    mv -f fixture.wav.part fixture.wav
fi

# 2. The engine reads 16 kHz mono, the same as `pipeline prep` hands it.
"$ffmpeg" -nostdin -y -hide_banner -loglevel error \
    -i fixture.wav -ar 16000 -ac 1 -c:a pcm_s16le "$tmp/fixture-16k.wav"

# 3. Recognise it with the 1.7B model; keep the raw JSON.
if ! HF_HUB_OFFLINE=1 uv run --frozen --quiet --project "$repo" mlx-qwen3-asr "$tmp/fixture-16k.wav" \
        --model Qwen/Qwen3-ASR-1.7B --language Chinese --timestamps -f json -o "$tmp" --no-progress \
        >"$tmp/asr.log" 2>&1; then
    cat "$tmp/asr.log" >&2
    exit 1
fi

# 4. The committed JSON must not carry a path, a user name or a machine name.
if grep -qF -e "$tmp" -e "$HOME" -e "$(whoami)" -e "$(hostname)" "$tmp/fixture-16k.json"; then
    echo "error: the recognition result names a local path, user or machine; fix this script" >&2
    exit 1
fi
cp "$tmp/fixture-16k.json" asr-qwen3-1.7b.json
