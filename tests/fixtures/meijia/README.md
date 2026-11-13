# Meijia speech fixture

A recorded stand-in for talking-head footage, used by `tests/test_asr.py` (and by the alignment and
rough-cut tests that read the recorded recognition result). The text is written for this repository;
it is not anyone's real script. The voice is a person reading `spoken.txt` into a phone in a quiet
room. The file is cut so that speech fills it from the first sample to the last, and three of the
pauses were shortened by cutting room tone out of them (see `make_fixture.sh`).

| File | What it is |
|---|---|
| `script.md` | The script as the speaker is supposed to read it: four sentences and one `[B-roll: ...]` marker. |
| `spoken.txt` | What the speaker actually reads. It differs from the script on purpose (the five events below). The notes in （...） are pauses, not words. |
| `fixture.wav` | The speech: 48 kHz, mono, 16-bit, 17.00 s. The sound of the first word starts at the first sample, and the file ends as the sound of the last word fades. |
| `asr-qwen3-1.7b.json` | The raw `mlx-qwen3-asr -f json` result of Qwen3-ASR 1.7B for `fixture.wav` converted to 16 kHz mono. Committed as written by the engine; it holds no path, user name or machine name. |
| `make_fixture.sh` | `bash tests/fixtures/meijia/make_fixture.sh <recording>` cuts `fixture.wav` from a recording and recognises it; without an argument it recognises the `fixture.wav` already here. Runs from anywhere. |

There is no added noise: the pauses hold the room as recorded. Measured with ffmpeg's astats, the RMS
level inside the long pause (2.75 to 3.75 s) is about -53 dBFS, and the first sentence is about
-25 dBFS RMS.

## The five designed events

Times are seconds in `fixture.wav`, taken from the recorded 1.7B result. The timestamps sit on an
80 ms grid.

1. **Pause after s01.** The speaker pauses about a second after the first sentence. The recording
   shows the last character of s01 ending at 2.56 and the first character of s02 starting at 3.92:
   a 1.36 s gap.
2. **Half a take, then the whole sentence (an NG take).** s02 is spoken as "the first seven
   characters, a short pause, then the complete sentence". The 1.7B transcript contains both, the
   partial one first and the full one right after it, with a comma between them in the text; the
   aligner has them as two runs of the same seven characters, 3.92 to 5.36 and 5.76 to 7.12, the
   second continuing into the rest of s02.
3. **Filler before s03.** The speaker says "嗯" before s03. The 1.7B transcript contains it, as one
   token of its own (10.16 to 10.40) with a comma after it in the text, between the end of s02
   (9.60) and the start of s03 (10.56).
4. **Number read differently from the script.** The script says `150`; the speaker says 一百五.
   The transcript has 一百五 as three Chinese-numeral tokens (12.40 to 12.88), not digits, so the
   aligner has to accept the shortened reading.
5. **Repeated word at the start of s04.** The speaker says 最後最後 before 記得追蹤我. The transcript
   has the word twice, with a comma between the two in the text and no gap between the four
   character tokens (14.80 to 15.68). A pause of 1.28 s comes before it.

## What the recognition output looks like

- The text is in Simplified Chinese (for example 们, 来, 辑, 顿, 钟, 后, 踪), even though the
  script and the speaker are Traditional. The aligner matches by toneless pinyin, so this does not
  disturb matching; any text taken from the recognition result has to go through `to_taiwan`.
- Every segment is one character, so the recording does not exercise the case where a segment
  holds several units (`tests/test_asr.py` covers that with a generated file).
- Four tokens have zero length: the 我 of 我們 (0.40), the 影 of 短影音 (1.52), and the 一 of 第一步
  in both takes (4.16 and 5.92). Start times never run backwards.
- Gaps around the filler: 0.56 s before it and 0.16 s after it. The gap inside the half take is 0.40 s.
