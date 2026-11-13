# Local rough-cut and subtitle toolchain for Taiwan-Mandarin talking-head Reels (Claude Code, 2025-2026)

Research date: 2026-11-11. All dates below are the source's own date (publication, release, or page-generation date) where one was shown. "HYPOTHESIS" marks claims not checked against a primary source or a real run. Numbers quoted from a vendor or model author are self-reported unless stated otherwise.

## Key Question 1: Speech recognition with word/character timestamps for Taiwan Mandarin (code-switching, Traditional output)

### Takeaway
For accuracy on Taiwan Mandarin, the two strongest local candidates in 2026 are Qwen3-ASR-1.7B (Jan 2026; CV-zh-TW 3.77 vs Whisper-large-v3 7.84) plus Qwen3-ForcedAligner (character-level Chinese timestamps, ~33 ms average shift, 5-minute max per call), and MediaTek Breeze-ASR-25 (Whisper-large-v2 fine-tune, Traditional output, strongest published code-switching gains). Both have community MLX ports for Apple Silicon. Stock Whisper large-v3/turbo is clearly weaker on Chinese; WhisperX's Chinese aligner is a Simplified-Chinese wav2vec2 model; NVIDIA Parakeet/Canary do not support Chinese. No engine reliably outputs Taiwan-Traditional with Taiwan vocabulary, so OpenCC conversion is part of every pipeline.

### Cited Findings

**Breeze-ASR-25 (MediaTek Research)**
- Fine-tuned from openai/whisper-large-v2; Apache-2.0; ~2B params (BF16); paper published 2025-06-10 (arXiv 2506.11130); "optimized for Taiwanese Mandarin", example output is Traditional; supports `return_timestamps=True` and is described as having "enhanced time alignment, suitable for automatic captioning"; no limitations listed. — [HF model card](https://huggingface.co/MediaTek-Research/Breeze-ASR-25)
- Model-card error rates (card labels the metric "WER"; for Chinese this is likely character- or mixed-error based but the card excerpt does not say — UNVERIFIED metric definition). Columns: Whisper-large-v2 auto / Whisper-large-v3 auto / COOL-Whisper / Breeze-ASR-25:
  - CommonVoice16-zh-TW: 9.84 / 8.95 / 11.86 / **7.97**
  - CSZS-zh-en (code-switch): 29.49 / 26.43 / 20.90 / **13.01** (-55.88% vs v2)
  - ASCEND-MIX (code-switch): 21.01 / 25.13 / 17.34 / **16.38**
  - ASCEND-ZH: 17.49 / 17.41 / 18.90 / **16.04**
  - ML-lecture-2021-long (code-switch, long-form): 6.13 / 6.41 / 6.37 / **4.98**
  - Formosa-Go 15.03 / 14.90 / 16.83 / **13.61**; Formosa-Show 29.18 / 27.80 / 29.78 / **27.58**; Formosa-Course **9.50** / 9.67 / 11.12 / 9.94 (Breeze slightly worse here) — [HF model card](https://huggingface.co/MediaTek-Research/Breeze-ASR-25)
- MediaTek press claim (2025-07-01): ~10% accuracy gain over Whisper, 56% better code-switching. — [DIGITIMES](https://www.digitimes.com/news/a20250701PD240.html)
- Community conversions exist for every Mac runtime: CTranslate2 for faster-whisper ([shdennlin/breeze-asr-25-ct2](https://huggingface.co/shdennlin/breeze-asr-25-ct2)), GGML for whisper.cpp ([shdennlin/breeze-asr-25-ggml](https://huggingface.co/shdennlin/breeze-asr-25-ggml)), MLX ([MarkChen1214/Breeze-ASR-25-MLX-5bit](https://huggingface.co/MarkChen1214/Breeze-ASR-25-MLX-5bit), [BRlin/Breeze-ASR-25-mlx-bf16](https://huggingface.co/BRlin/Breeze-ASR-25-mlx-bf16)), GGUF ([handy-computer/Breeze-ASR-25-gguf](https://huggingface.co/handy-computer/Breeze-ASR-25-gguf)). All are unofficial.
- MLX port benchmark (Apple M4 Pro, 48 GB; date not shown, collection "updated Aug 8"): Common Voice 16.1 zh-TW full test (4,976 clips), CER after OpenCC s2tw on both sides: BF16 9.78% CER at 14.7x realtime; Q5 10.30% CER at 17.7x realtime. Mandarin-English code-switch subset (250 ML2021 rows): BF16 10.05% / 8.6x; Q5 10.44% / 10.3x. Disk: Q5 ≈1.07 GB, BF16 ≈3.08 GB. Caveats from the author: optimized path "does not provide word timestamps with fused attention"; optimized path is short-form only (longer audio is rejected); community conversion, not official. — [MarkChen1214/Breeze-ASR-25-MLX-5bit](https://huggingface.co/MarkChen1214/Breeze-ASR-25-MLX-5bit)
- GGUF conversion page reports FLEURS-zh CER ≈8.1% for the full-precision model, scored after converting both sides to Simplified (search snippet only, page not fetched). — [handy-computer/Breeze-ASR-25-gguf](https://huggingface.co/handy-computer/Breeze-ASR-25-gguf)

**Qwen3-ASR + Qwen3-ForcedAligner (Alibaba Qwen)**
- Released 2026-01-29 (0.6B, 1.7B, ForcedAligner-0.6B), Apache-2.0; native Hugging Face Transformers support added 2026-06-26. ASR covers 30 languages + 22 Chinese dialects (incl. Minnan, Cantonese); ForcedAligner covers 11 languages incl. Chinese and English; aligner max 5 minutes of speech per call; streaming mode cannot return timestamps. Official README mentions only CUDA examples, no Apple Silicon/MPS/MLX; whether Mandarin output can be Traditional is not stated. — [QwenLM/Qwen3-ASR GitHub](https://github.com/QwenLM/Qwen3-ASR)
- Taiwan benchmark (CV-zh-tw, README labels it WER): Qwen3-ASR-1.7B **3.77**, 0.6B 5.59, Doubao-ASR 4.06, GPT-4o-Transcribe 6.32, Gemini-2.5-Pro 7.31, Whisper-large-v3 7.84. — [QwenLM/Qwen3-ASR GitHub](https://github.com/QwenLM/Qwen3-ASR)
- Mandarin CER (1.7B vs Whisper-large-v3): AISHELL-2 2.71 vs 5.06; SpeechIO 2.88 vs 7.56; Fleurs-zh 2.41 vs 4.09; WenetSpeech net/meeting 4.97–5.88 vs 9.86–19.11. — [Qwen3-ASR Technical Report, arXiv 2601.21337 v2, 2026-01-30](https://arxiv.org/html/2601.21337)
- ForcedAligner: word- or character-level timestamps; Chinese accumulated average shift (AAS) 33.1 ms (raw) / 36.5 ms (concatenated up to 300 s), vs NeMo Forced Aligner 109.8 / 235.0 ms and Monotonic-Aligner 161.1 / 1742.4 ms. WhisperX has no Chinese result in the report. Code-switch (cross-lingual) alignment AAS 34.2 ms (MFA-labelled), 42.5 ms (human-labelled). No code-switching ASR CER reported. — [arXiv 2601.21337](https://arxiv.org/html/2601.21337)
- Community MLX port `mlx-qwen3-asr` v0.4.0 (benchmarks dated 2026-09-07, Apple M4 Pro 48 GB): 0.6B fp16 RTF 0.029 on a 10 s clip, 0.6B 4-bit RTF 0.018, 1.7B fp16 RTF 0.077; memory ≈1.2 GB (0.6B fp16) and ≈3.4 GB (1.7B); native MLX forced aligner, timing MAE 5.69 ms vs official backend, 0.21 s vs 0.56 s mean latency (2.64x faster); long audio split at low-energy points into 30 s chunks; `pip install mlx-qwen3-asr`. Parity vs PyTorch on 100 multilingual clips: CER 5.43% vs 5.64%. Chinese-specific timestamp granularity not stated on the page. — [moona3k/mlx-qwen3-asr](https://github.com/moona3k/mlx-qwen3-asr/)
- `mlx-audio` (Blaizzy) also supports Qwen3-ASR and Qwen3-ForcedAligner via mlx-community weights (search-result summary, not fetched). — [mlx-audio docs index](https://docsearch.algolia.com/mcp/docs/repo/blaizzy/mlx-audio)

**FireRedASR / FireRedASR2 (Xiaohongshu FireRed team)**
- FireRedASR (Jan 2025): LLM variant 8.3B, avg Mandarin CER 3.05%; AED variant 1.1B, 3.18%. — [arXiv 2501.14350](https://arxiv.org/abs/2501.14350)
- FireRedASR2S released 2026-02-12 (AED + VAD + LID + punctuation), LLM weights 2026-02-25, tech report 2026-03-12; Apache-2.0. Avg Mandarin CER: FireRedASR2-LLM 2.89, FireRedASR2-AED 3.05, Doubao-ASR 3.69, Qwen3-ASR 3.76; dialect avg 11.55 / 11.67 / 15.39 / 11.85. No zh-TW results, no word/character-timestamp statement, no Mac notes on the card. — [FireRedASR2-AED README](https://huggingface.co/FireRedTeam/FireRedASR2-AED/blob/refs%2Fpr%2F1/README.md); [arXiv 2603.10420](https://arxiv.org/abs/2603.10420)

**FunASR family (Paraformer, SenseVoice, Fun-ASR-Nano)**
- Paraformer-zh is the FunASR route for character-level timestamps (ms) and hotwords; SenseVoiceSmall is listed as ASR + emotion + audio events (zh/en/ja/ko/yue) with no timestamp claim; Fun-ASR-Nano's open checkpoint is reported to lack trained CTC/timestamp-head weights, so its timestamps are unreliable or absent (multiple community sources; conflicting with FunASR docs that list "ASR + timestamps"). — [FunASR model_selection.md mirror](https://gh.mise.run.place/modelscope/FunASR/blob/main/docs/model_selection.md); [mlx-audio Fun-ASR-Nano page](https://blaizzy.github.io/mlx-audio/models/stt/fun-asr-nano/)
- FunASR's own benchmark page (audited 2026-09-07, explicitly labelled "historical claims" with incomplete provenance; NVIDIA H100; 184 long-form Chinese files, 11,539 s): CER SenseVoice-Small 7.81%, Fun-ASR-Nano 8.06%, Paraformer-Large 10.18%, Whisper-large-v3 20.02%, Whisper-large-v3-turbo 21.71%; CPU speed SenseVoice-Small 17.2x realtime, Paraformer-Large 15.6x, Fun-ASR-Nano 3.6x. Output script not stated. — [FunASR benchmark](https://modelscope.github.io/FunASR/benchmark.html)
- Fun-ASR technical report (Sept 2025). — [arXiv 2509.12508](https://arxiv.org/pdf/2509.12508)

**Whisper large-v3 / large-v3-turbo and runtimes**
- Base-model Chinese CER reported on the Belle fine-tune card: AISHELL-1 large-v3 8.085 vs turbo 8.639; WenetSpeech-net 11.72 vs 13.507; HKUST-dev 28.597 vs 37.324 (turbo worse on every set; model author's numbers). — [BELLE-2/Belle-whisper-large-v3-turbo-zh](https://huggingface.co/BELLE-2/Belle-whisper-large-v3-turbo-zh)
- Industry paper (2025) reports fine-tuned whisper-large-v3 producing "abnormal repetition of single Chinese characters/phrases", calling it a well-recognised large-v3 problem. — [arXiv 2507.16843](https://arxiv.org/pdf/2507.16843)
- Deepgram (2023-11-14) found Whisper-v3 hallucinated ~4x more often than v2 on real-world, mostly English data (not Chinese, not turbo). — [Deepgram](https://deepgram.com/learn/whisper-v3-results)
- Whisper has no setting for Simplified vs Traditional; Traditional-text prompting can steer output but is reported unreliable on large-v3; OpenCC post-processing (s2twp) is the suggested fix. — [openai/whisper discussion #277](https://github.com/openai/whisper/discussions/277); [voicebox issue #754](https://github.gg22.workers.dev/jamiepine/voicebox/issues/754); [Whisper prompting paper, arXiv 2406.05806](https://web3.arxiv.org/pdf/2406.05806)
- faster-whisper: `word_timestamps=True` API; on Mac runs CPU (int8) only, no Metal (comparison-site claim; CTranslate2 docs not checked). — [faster-whisper PyPI](https://pypi.org/project/faster-whisper/0.4.1); [promptquorum comparison](https://www.promptquorum.com/power-local-llm/local-whisper-stt-comparison-2026)
- whisper.cpp: Metal + optional Core ML encoder (Neural Engine) on Apple Silicon; word-level timestamps are "experimental" (`-ml 1`, `-ojson`). — [whisper.cpp README mirror](https://huggingface.co/spaces/natasa365/whisper.cpp/blob/328702af7ee57a9473c3430e2e8e36d09e7bed31/README.md)
- mlx-whisper vs whisper.cpp, same model (large-v3-turbo), Apple M1 Ultra 32 GB, page updated 2026-01-09: 13.1 s vs 26.7 s on the same speech file (audio length not stated), i.e. ~2x faster with MLX. — [Bill Mill notes](https://notes.billmill.org/link_blog/2024/08/mlx-whisper.html)

**WhisperX**
- Backend faster-whisper; "70x realtime with large-v2" (GPU batched); on Mac the README gives only CPU int8 instructions (no MPS). Numbers/currency like "2014." or "£13.60" not in the aligner dictionary historically got no timing. — [m-bain/whisperX](https://github.com/m-bain/whisperX)
- Chinese alignment model: `zh` → `jonatasgrosman/wav2vec2-large-xlsr-53-chinese-zh-cn` (a Simplified-Chinese-trained model); `zh` and `ja` are in `LANGUAGES_WITHOUT_SPACES`, so each character becomes its own "word" with its own timestamp. Current code maps characters missing from the model dictionary (digits, Latin letters, symbols, foreign script) to a wildcard emission column instead of dropping them; characters that still fail get `None` start/end. — [whisperX alignment.py](https://raw.githubusercontent.com/m-bain/whisperX/main/whisperx/alignment.py)

**NVIDIA Parakeet / Canary (2025)**
- Canary-1B-v2 and Parakeet-TDT-0.6B-v3 support 25 "primarily European" languages; Chinese is not among them. — [arXiv 2509.14128](https://arxiv.org/pdf/2509.14128); [whispernotes blog (secondary)](https://whispernotes.app/blog/parakeet-v3-default-mac-model)

**OpenCC**
- Current release 1.4.2; `pip install opencc`. Configs: `s2tw` (Simplified → Taiwan-standard characters), `s2twp` (adds Taiwan phrases, e.g. 数据库→資料庫, 连接池→連線池, 鼠標→滑鼠), `t2tw`, `tw2s`, etc.; user dictionaries can override specific words (e.g. 服务器→伺服器) by placing them first in `group.dicts`. — [BYVoid/OpenCC](https://github.com/BYVoid/OpenCC)

### Inferences
- Shortlist to bench on the user's own footage: (a) Qwen3-ASR-1.7B (or 0.6B on 8 GB Macs) + Qwen3-ForcedAligner via mlx-qwen3-asr; (b) Breeze-ASR-25 via MLX or whisper.cpp GGML with word timestamps. Whisper large-v3/turbo stock is a fallback only: both Qwen3 and Breeze beat it on zh-TW by a wide margin on the published numbers.
- Breeze-ASR-25 is Whisper-v2-based, so it should inherit Whisper's long-form behaviour (silence hallucinations, repetition loops); use VAD-segmented input. HYPOTHESIS, not tested.
- Because WhisperX's Chinese aligner is a zh-CN model and OpenCC `s2twp` can change string length (phrase substitution, e.g. 软件→軟體 keeps length but U盘→隨身碟 does not), the safe order is: transcribe → align character timestamps on the text the aligner understands → convert per subtitle cue (not per character) with `s2twp`, or per character with `s2tw` (character-for-character). HYPOTHESIS on the length-change risk for specific s2twp entries; confirm with `opencc --inspect` (or equivalent) on real output.
- Qwen3-ForcedAligner is limited to 5 minutes per call, which fits a 3–5 minute Reel in one call; longer source recordings need chunking.
- A term dictionary (proper nouns, English product names) should be fed as an initial prompt/hotword list where the engine supports it and also applied as a post-ASR replacement table that Claude edits; Claude should not re-read the whole transcript for spelling fixes.

### Gaps
- No independent (third-party) Taiwan-Mandarin code-switching CER comparison of Qwen3-ASR vs Breeze-ASR-25 vs FireRedASR2 was found; each vendor reports on different sets.
- Whether Qwen3-ASR, FireRedASR2, SenseVoice or Paraformer emit Traditional characters for Taiwan speech is not documented; must be tested (OpenCC handles it either way).
- FireRedASR2 timestamp support and Apple Silicon runtime: not found.
- Hallucination rates for Breeze-ASR-25 and large-v3-turbo on Chinese: no measurements found.
- Breeze-ASR-25 card's metric definition (WER vs CER vs MER for Chinese) not confirmed; the arXiv paper 2506.11130 was not fetched.
- Whether SenseVoice in current FunASR releases exposes CTC-based character timestamps was not confirmed.

## Key Question 2: Rough-cut automation (silence, fillers, retakes, transcript-driven cutting) and what agent workflows do

### Takeaway
The established agent pattern (2025-2026) is transcript-first: a local script produces a word-timestamp JSON plus a compact readable transcript; Claude reads only the compact transcript, writes a JSON cut list with a reason per cut, the user approves, and a deterministic ffmpeg script renders. Silence/filler/stutter removal is rule-based code; Claude is reserved for retakes, tangents and repeated sentences. auto-editor remains the standard volume/motion-based silence cutter.

### Cited Findings

**auto-editor**
- Version 31.6.1 (reference page generated 2026-09-07). `--edit` default `audio`; `--margin` default `0.2s`; `--when-silent`/`--when-inactive` default `cut`; `--cut`/`--cut-out` and `--keep`/`--add-in` take explicit time ranges; `--set-speed-for-range`; `--audio-normalize ebu|peak`; `--export` exists (target names not listed on the options page). — [auto-editor options](https://auto-editor.com/ref/options)
- Edit methods: `audio [threshold=0.04]`, `motion [threshold=0.02, width=400, blur=9, region x/y/w/h]`, `blackdetect`, `subtitle`/`regex` (marks subtitle-stream lines matching a regex), `word` (whole-word match in a subtitle stream); combinable with `or`/`and`/`xor`/`not`, e.g. `--edit "(or (audio 0.05) (motion 0.02))"`. — [auto-editor --edit reference](https://auto-editor.com/ref/edit)
- A Taiwan practitioner (page updated 2026-11-01) reports auto-editor over-cutting intentional pauses because it judges by volume, needing threshold tuning; also that Chinese subtitles render as tofu boxes if no CJK font is installed. — [maplefeather.com (zh-TW)](https://maplefeather.com/article/ai-auto-video-editing-claude-code-2026)

**Transcript-driven agent skill: social-media-skills/video-editing-skill (DEV post, Oct 30, year not shown; listed as "11 days ago" in a 2026-11 search)**
- ASR: existing captions, Groq/OpenAI API, or local faster-whisper; a "disfluent prompt" is used so fillers are transcribed; user terms via `--terms`. Local faster-whisper `small` ≈0.5–1x realtime (hardware not stated).
- Claude reads `transcript.txt` (one timestamped line per sentence); cuts are made on `transcript.json` (per-word start/end). Agent writes `edit.json` (not an EDL): style, extra fillers, `cut` ranges with reasons (e.g. `[61.2, 74.8, "tangent about the weather"]`), keep ranges, replace map, chapters.
- Rules in code: default fillers um/uh/er/ah/hmm cut only when a gap borders them; stutters = repeated word with <0.25 s gap and no comma (stop list protects "very", "no"); retake cues ("scratch that") cut, plus the preceding sentence only if the next repeats ≥50% of its words; restarts = unfinished fragment ≤8 words followed by a sentence with the same opening → keep last take; repeated finished sentences are never auto-cut, only listed for the agent/user.
- Render: trim/atrim + setpts + concat in one filter graph (groups of ≤60 pieces), edges rounded to the frame grid, 4 ms audio fades; punch-ins 1.1x crop (cap 1.2x) anchored at an eye line at 38% of frame height, applied at large cuts and after 4 s (2.5 s in "tight" style) on the same framing; loudness -14 LUFS (podcast -16), limiter peaks at -2 dB. No Chinese support mentioned. — [DEV Community](https://dev.to/fheijdenrijk/video-editing-skill-for-claude-code-an-agent-that-plans-every-cut-then-renders-with-ffmpeg-518a)

**browser-use/video-use (MIT)**
- Uses ElevenLabs Scribe (cloud) for word timestamps, diarization and audio events; no local ASR option documented. LLM reads a phrase-level ~12 KB `takes_packed.md`; `timeline_view` PNGs (filmstrip + waveform + word labels) loaded only at decision points; EDL step; proposes strategy and waits for approval; cuts fillers, false starts, dead space; 30 ms audio fade per cut; after render, self-checks every cut boundary for visual jumps, audio pops and hidden subtitles, re-rendering up to 3 times. Token argument: naive frame dump ≈30,000 frames × ~1,500 tokens ≈45M tokens vs ~12 KB transcript + a few PNGs. Chinese support not mentioned. — [browser-use/video-use](https://github.com/browser-use/video-use)

**Taiwan/Chinese-language practitioner reports**
- 追日Gucci (2026-07-05): Claude Code pipeline with 12 skills and six human approval checkpoints; fillers/retakes/dead air flagged in high/medium confidence tiers; each retake splice previewed as a ~7 s before/after clip; a term dictionary learns subtitle corrections; Shorts use TikTok-style word-highlight subtitles; 28-minute recording → ~14 minutes; post-production 6–7 h → ~30 min of approvals (self-reported, promotional). ASR model not named. — [guccidgi.com (zh-TW)](https://www.guccidgi.com/2026/07/claude-code-video-editing-pipeline/)
- maplefeather (updated 2026-11-01): six steps — record → Whisper SRT → auto-editor de-pause → ImageMagick title cards → ffmpeg burn subtitles/music/intro-outro → 9:16 export; recommends proofreading Whisper's Chinese typos and punctuation before burn-in. — [maplefeather.com](https://maplefeather.com/article/ai-auto-video-editing-claude-code-2026)
- Other guides describing the same transcript → cut list → ffmpeg pattern (not fetched): [Kapwing](https://www.kapwing.com/resources/how-to-edit-videos-with-claude-code-free-ai-video-editing-skill/), [gettakes.app](https://gettakes.app/claude-code-video-editing), [jordanhongtai.com](https://www.jordanhongtai.com/guides/claude-code-video-editor).

### Inferences
- For Mandarin, the English filler/stutter rules need replacing: fillers (呃, 嗯, 啊, 那個, 就是, 然後) should be cut only when isolated by gaps, because 那個/就是/然後 are also content words; stutter/restart detection must work on characters or jieba-style tokens, not spaces. A character n-gram or edit-distance comparison of adjacent sentences (e.g. ≥50% overlap → candidate retake, keep the last) mirrors the DEV skill's rule. HYPOTHESIS: thresholds need tuning on real footage.
- Token budget: Claude should see the sentence-level transcript (with sentence IDs and times) plus a precomputed list of candidate retakes/repeats, and emit only an `edit.json` of IDs/ranges with reasons; word-level JSON stays out of context. This matches both reference skills.
- Combine auto-editor-style silence detection with word boundaries: cut silences only between words (snap to word end/start plus a small margin, e.g. auto-editor's 0.2 s default) so that cuts never clip a syllable.
- Jump-cut smoothing seen in practice is punch-in zoom alternation plus 4–30 ms audio fades; neither reference skill uses video crossfades for talking heads.

### Gaps
- No source evaluated filler/retake detection accuracy for Mandarin.
- auto-editor `--export` target names were not confirmed on the fetched page (likely NLE timeline formats; check `auto-editor --help`).
- ffmpeg `silencedetect` parameters were not re-fetched from ffmpeg docs in this session ([ffmpeg filters docs](https://ffmpeg.org/ffmpeg-filters.html#silencedetect)).

## Key Question 3: Subtitles — format, CJK line breaking, word highlight, re-timing after cuts, safe zones, face avoidance

### Takeaway
Use ASS (libass) for burn-in when styling or word-by-word highlight is wanted (`\k`/`\kf` karaoke tags), but insert line breaks yourself: FFmpeg does not wrap CJK text unless libass was built with libunibreak, and for native ASS input the Unicode wrap option is off. Keep each cue to one line within Meta's safe area (top 14%, bottom 35%, sides 6% → on 1080x1920 roughly y=269–1248, x=65–1015). Re-time subtitles by mapping each word's source timestamp through the kept-span list rather than re-transcribing.

### Cited Findings
- Netflix Traditional Chinese timed-text guide: max 16 characters per line, max 2 lines (prefer one; bottom-heavy split), adult reading speed ≤9 characters/second; full-width punctuation; no periods; no comma at line/subtitle end; half-width Arabic numerals with no thousands comma; 「」 then 『』 for quotes; English brand names kept where known. — [Netflix Partner Help](https://partnerhelp.netflixstudios.com/hc/en-us/articles/215994807-Traditional-Chinese-Timed-Text-Style-Guide)
- FFmpeg `subtitles` filter gained a `wrap_unicode` option (May 2023) that uses Unicode line-breaking so CJK can wrap; requires libass ≥0x01600010 built with libunibreak; on by default except for native ASS input. — [FFmpeg-devel commit notice](https://ffmpeg.org/pipermail/ffmpeg-devel/2023-May/310087.html)
- mpv's libass code notes that CJK subtitles without spaces "might overflow the screen" if no extra line breaks are inserted, "and ffmpeg doesn't"; mpv sets WrapStyle 1 and enables Unicode wrapping explicitly. — [mpv osd_libass.c](https://git.tjdev.de/mirror/mpv/src/commit/180b4faed0d7aed075c22cf3ac52cf9fc4a64860/sub/osd_libass.c)
- ASS karaoke tags are in centiseconds (`{\k50}` = 0.5 s); variants `\k`, `\kf` (left-to-right fill), `\ko`; burn-in via `ffmpeg -i in.mp4 -vf "ass=karaoke.ass" -c:v libx264 -crf 18 -c:a copy out.mp4`. — [n8n karaoke workflow](https://n8n.io/workflows/18486-burn-word-highlighted-karaoke-captions-onto-videos-with-ffmpeg-and-ffprobe/); [R subtitles package reference](https://ftp.fau.de/cran/web/packages/subtitles/refman/subtitles.html)
- A search-surfaced page claims Homebrew's default ffmpeg lacks libass (so `subtitles`/`ass` filters fail) and `ffmpeg-full` is the fix — UNVERIFIED; check with `ffmpeg -filters | grep -E "ass|subtitles"`. — [skillselion ffmpeg-karaoke page](https://skillselion.com/skills/josiahsiegel/claude-plugin-marketplace/ffmpeg-karaoke-animated-text)
- Re-timing after cuts: the DEV video-editing skill keeps every word's source timestamp and maps it onto the edited timeline with `to_out()` by walking the kept spans; captions use only kept words (≤2 lines × 42 chars, 1–7 s each, gaps <0.3 s closed), rendered as Pillow PNGs and overlaid with ffmpeg; chapters use the same mapping. — [DEV Community](https://dev.to/fheijdenrijk/video-editing-skill-for-claude-code-an-agent-that-plans-every-cut-then-renders-with-ffmpeg-518a)
- video-use default caption style: 2-word UPPERCASE chunks, customizable; self-check flags hidden subtitles at cut boundaries. — [browser-use/video-use](https://github.com/browser-use/video-use)
- Official Meta Ads Guide (Instagram Reels, 9:16): keep text/logos out of the top ≥14%, bottom ~35%, and ~6% on each side. — [Meta Ads Guide: Instagram Reels video](https://www.facebook.com/business/ads-guide/update/video/instagram-reels)
- Third-party safe-zone figures conflict: Meta-percentage converters give 269 px top / 672 px bottom / 65 px sides on 1080x1920 and a central ~895x980 box ([admanage.ai checker](https://admanage.ai/instagram-reels-safe-zone)); others give fixed 108/320/60/120 px (top/bottom/left/right) or 250 top / 340–350 bottom ([Hopper HQ](https://www.hopperhq.com/blog/instagram-reel-size/), [ScreenSnap](https://www.screensnap.pro/blog/instagram-reels-size-guide)).
- Face avoidance: Adobe Research (Express) found creators disliked captions over faces, but captions that move constantly to dodge faces look jittery — hold position rather than track. — [Adobe Research](https://research.adobe.com/news/content-aware-video-captioning-in-adobe-express/)
- MediaPipe face detection runs fast on CPU and handles angled faces; used as the primary detector (YuNet/Haar fallbacks) in a vertical auto-crop pipeline. — [Habr article](https://habr.com/en/articles/1022298/)
- Taiwan practitioner: Chinese ASR sentence breaks are often unnatural; separate rules are needed for line breaks, fillers and number formatting. — [guccidgi.com](https://www.guccidgi.com/2026/07/claude-code-video-editing-pipeline/)

### Inferences
- Pixel math (from Meta's percentages, 1080x1920): top 14% = 269 px, bottom 35% = 672 px, sides 6% = 65 px → safe box x 65–1015 (950 px wide), y 269–1248. A subtitle band around y ≈ 1050–1240 sits inside the safe box but low enough to clear a face framed in the upper half. HYPOTHESIS: verify with a mock-up overlay of the current IG app UI; organic Reels UI is lighter than the ad UI on which Meta's numbers are based.
- Characters per line for 9:16: with a 950 px safe width and a ~64–72 px font, 13–14 full-width characters fit; for fast-read Reels, one line of ≤12–14 characters per cue, ≤9 chars/s (Netflix pace) is a reasonable start. HYPOTHESIS: no Reels-specific CJK guideline was found.
- Line breaking should be done in code before writing the ASS file (break at punctuation, then at word boundaries from a Chinese tokenizer; never split an English term or number), then strip end-of-cue commas/periods per the Netflix rules.
- Face-aware placement: run MediaPipe once per few seconds, compute the face box's lowest y over the clip (or per shot), and choose a fixed subtitle y below it (or above, if the face is low); do not move per frame.
- Word-by-word highlight for Chinese: generate one ASS Dialogue per cue with `\kf` durations computed from per-character (or per-word) timestamps; character-level timestamps from Qwen3-ForcedAligner or WhisperX make this direct.

### Gaps
- No official Instagram document gives organic (non-ad) Reels safe zones in pixels.
- No source compared libass WrapStyle behaviour with karaoke lines.
- No measured data on face-detection-driven subtitle placement quality.

## Key Question 4: Export — IG Reels encode settings, loudness, VFR and iPhone HDR pitfalls

### Takeaway
Officially: 9:16, H.264, fixed frame rate, progressive, square pixels, stereo AAC ≥128 kbps, ≥30 fps, ≥720 px; the Graph API caps video bitrate at 25 Mbps and files at 300 MB. Meta publishes no loudness target; -14 LUFS integrated / -1 dBTP is the common creator convention. iPhone footage is often HDR (HLG/Dolby Vision 10-bit HEVC) and must be tone-mapped to BT.709 SDR explicitly, and frame rate must be forced constant.

### Cited Findings
- Instagram Help Center: Reels aspect ratio 1.91:1 to 9:16; minimum frame rate 30 fps; minimum resolution 720 px. — [Instagram Help: Reel size & aspect ratios](https://help.instagram.com/1038071743007909/)
- Meta Ads Guide (Reels ads): recommended 1440x2560, MP4/MOV, H.264, square pixels, fixed frame rate, progressive scan, stereo AAC ≥128 kbps, ≤4 GB, up to 15 minutes. — [Meta Ads Guide](https://www.facebook.com/business/ads-guide/update/video/instagram-reels)
- Instagram Graph API (publishing via API): 9:16 recommended; 25 Mbps max video bitrate; 300 MB max file; 15 min max. — [IG User Media reference](https://developers.facebook.com/documentation/instagram-platform/instagram-graph-api/reference/ig-user/media)
- Third-party bitrate advice conflicts (3.5–5 Mbps, up to 8 Mbps, 8–12 Mbps, 8–15 Mbps); common consensus 1080x1920, 30 fps, H.264, AAC 48 kHz ≥128 kbps. — [visnalize](https://visnalize.com/blog/instagram-media-specs-compression-guide); [socialrails](https://socialrails.com/sizes/instagram/reels)
- Loudness: no official Meta figure found; most third-party sources say -14 LUFS integrated, one vendor preset says -16. video-use's render reportedly normalizes to -14 LUFS, -1 dBTP, LRA 11 (AI-generated repo explainer, secondary). — [OpenClip normalization targets](https://openclip.app/learn/audio-normalization); [instagit explainer of video-use](https://instagit.com/browser-use/video-use/social-media-loudness-normalization-targets.md); [-16 LUFS vendor page](https://d1lex0gkhzvix9.cloudfront.net/tiktok-instagram-reels-loudness)
- iPhones (from iPhone 12) shoot HLG BT.2020 Dolby Vision HEVC 10-bit. — [Doom9 forum](https://forum.doom9.org/archive/index.php/t-185185.html)
- HDR→SDR in ffmpeg: route on `color_transfer` (`smpte2084` = PQ, `arib-std-b67` = HLG → tone-map; BT.2020 primaries with BT.709 transfer = wide-gamut SDR, gamut-convert only). A plain `scale,format=yuv420p` produces a washed-out image. Chains: CPU `zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=hable:desat=0:peak=10,zscale=t=bt709:m=bt709:r=tv,format=yuv420p` (needs libzimg); GPU `libplacebo=tonemapping=bt.2390:...:colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv:format=yuv420p` (needs Vulkan); FFmpeg 8+ `scale=out_color_matrix=bt709:out_primaries=bt709:out_transfer=bt709:out_range=tv:intent=perceptual,format=yuv420p`. Set `desat=0` and an explicit `peak` (nits/100). Dolby Vision not discussed. — [ez-ffmpeg recipes (docs.rs)](https://docs.rs/crate/ez-ffmpeg/0.17.0/source/src/core/recipes/mod.rs)
- Forum consensus favours libplacebo for HDR→SDR quality; zscale is missing from some ffmpeg builds. — [Doom9 thread](https://forum.doom9.org/archive/index.php/t-186132.html); [Cinelerra list, 2025-04](https://lists.cinelerra-gg.org/pipermail/cin/2025-April/009646.html)

### Inferences
- A reasonable final encode (HYPOTHESIS, assembled from the official limits above, not a Meta-published recipe): `-vf "fps=30,<tonemap if HDR>,scale=1080:1920:flags=lanczos,ass=subs.ass" -c:v libx264 -profile:v high -pix_fmt yuv420p -crf 18 -maxrate 20M -bufsize 40M -r 30 -movflags +faststart -c:a aac -b:a 192k -ar 48000 -ac 2 -af loudnorm=I=-14:TP=-1:LRA=11` (two-pass loudnorm for accuracy). The `fps=30` filter + `-r 30` satisfies Meta's "fixed frame rate" requirement for VFR phone footage. Keep the file under 300 MB if publishing through the API.
- Do all cutting on an intermediate (or in a single filter graph) and encode once, so HDR tone mapping, subtitle burn-in and loudness happen in one final pass; keep the original timeline's timestamps for the subtitle mapping.
- HEVC upload: no official Meta statement found either way; H.264 is the documented codec, so it is the safe default.
- Apple's hardware encoder (`h264_videotoolbox`) would be much faster than libx264 on Apple Silicon, at some quality-per-bit cost. HYPOTHESIS: not measured in this session.

### Gaps
- No official Meta loudness target; no official Reels bitrate recommendation for organic uploads.
- No source on Dolby Vision profile 8.4 handling specifically for iPhone files in ffmpeg (base layer HLG is assumed usable).
- No measurement of Instagram's re-encode quality for H.264 vs HEVC uploads.

## Key Question 5: End-to-end speed for a 3–5 minute clip on Apple Silicon with 8–16 GB RAM

### Takeaway
On published M4 Pro numbers, ASR for 5 minutes of audio takes roughly 9–25 seconds (Qwen3-ASR MLX 0.6B/1.7B) or ~20 seconds (Breeze-ASR-25 MLX BF16), and forced alignment adds seconds; models fit in 1.2–3.4 GB. Base M1/M2 8 GB machines will be slower and memory-tight for 1.7B/BF16 models. No published end-to-end measurement for this exact pipeline exists; the render (tone map + subtitle burn-in + encode) is likely the longest step.

### Cited Findings
- Qwen3-ASR MLX (M4 Pro 48 GB, 2026-09-07): RTF 0.029 (0.6B fp16), 0.018 (0.6B 4-bit), 0.077 (1.7B fp16); memory ≈1.2 GB (0.6B) / ≈3.4 GB (1.7B); forced alignment 0.21 s per LibriSpeech utterance. — [mlx-qwen3-asr](https://github.com/moona3k/mlx-qwen3-asr/)
- Breeze-ASR-25 MLX (M4 Pro 48 GB): 14.7x realtime (BF16, 3.08 GB weights), 17.7x (Q5, 1.07 GB); optimized path is short-form only and lacks word timestamps. — [MarkChen1214/Breeze-ASR-25-MLX-5bit](https://huggingface.co/MarkChen1214/Breeze-ASR-25-MLX-5bit)
- mlx-whisper large-v3-turbo ≈2x faster than whisper.cpp on M1 Ultra (13.1 s vs 26.7 s, same file; audio length not stated). — [Bill Mill notes, updated 2026-01-09](https://notes.billmill.org/link_blog/2024/08/mlx-whisper.html)
- faster-whisper on Mac is CPU-only; one comparison site reports ~3x realtime CPU vs ~10x realtime whisper.cpp Metal for large-v3 (low-confidence source). — [promptquorum](https://www.promptquorum.com/power-local-llm/local-whisper-stt-comparison-2026)
- Local faster-whisper `small`: ≈0.5–1x realtime in the DEV skill (hardware unspecified). — [DEV Community](https://dev.to/fheijdenrijk/video-editing-skill-for-claude-code-an-agent-that-plans-every-cut-then-renders-with-ffmpeg-518a)
- Practitioner wall-clock claims are about human approval time, not compute: 6–7 h → ~30 min of approvals for a long-form video. — [guccidgi.com](https://www.guccidgi.com/2026/07/claude-code-video-editing-pipeline/)

### Inferences
- Rough budget for a 5-minute clip on an M4-class Mac (HYPOTHESIS, extrapolated from the RTFs above, not measured): audio extract <5 s; ASR 9–25 s; alignment <10 s; rule-based filler/silence/retake candidates <2 s; Claude judgement on a ~1,200–1,500-character transcript one round-trip (tens of seconds); subtitle generation <1 s; final render with HDR tone mapping + ASS burn-in + libx264 at 1080x1920 30 fps likely 1–4 minutes (videotoolbox much faster). Total compute ≈2–6 minutes; human review dominates.
- On a base M1/M2 with 8 GB: expect ASR 2–4x slower than M4 Pro figures (fewer GPU cores), and prefer the 0.6B Qwen3-ASR or Breeze Q5 to stay under ~1.5 GB of weights while ffmpeg and Claude Code run. HYPOTHESIS.
- Mandarin speech runs at roughly 4–5 characters per second, so a 5-minute transcript is about 1,200–1,500 characters; Claude reading that once plus writing a short cut list is a small token cost compared with any frame-based approach (video-use's 45M-token frame-dump estimate). HYPOTHESIS on speech rate; not sourced in this session.

### Gaps
- No published end-to-end timing on an 8–16 GB M1/M2/M3 base chip for any of these ASR models.
- No measured libx264 vs h264_videotoolbox encode times for 1080x1920 on Apple Silicon found.
- Memory headroom of Qwen3-ASR 1.7B + aligner concurrently on 8 GB not measured.
