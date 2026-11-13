# User-supplied references (read 2026-11-11)

## video-shotcraft — github.com/Vincentwei1021/video-shotcraft
- Apache-2.0; 18,427 stars; created 2026-07-19; pushed 2026-11-09 (GitHub API).
- Scope: product promo videos from web/desktop products, 1920x1080 30fps h264, Remotion. Not talking-head.
- 124 shot recipe cards / 150 shots in 10 categories; each card = intent + params + "known pitfalls"
  (params that must not be downgraded) + exact demo TSX ("demo source is the parameter truth").
- Modes: template / autonomous / co-creation (gates: brief -> decisions -> visual direction -> shot map -> storyboard).
- Rules worth borrowing: deterministic render (no Date.now/Math.random, seeded PRNG); one motion idea per shot;
  hold >=1s after key info lands; camera-level slams <=3 per video; visual tokens derived from product design system.
- Independent final review by a clean-context subagent with full inputs (brief, storyboard, cards, frames),
  report with frame-number evidence; "maker has confirmation bias, first check never goes to user".
- Sound: Freesound search/download script (CC0 default, licence re-check, attribution + hash manifest);
  16 SFX categories; "riser -> impact -> sparkle" phrase; deliver two cuts: with BGM and without BGM (SFX kept).
- BGM beat sync via librosa (BPM/phase + kick/snare/hihat); post-render cut error <=3 frames.
- JianYing export (Mac JianYing 11.2 tested), browser "workbench" editor, Upload-Post publishing — out of our scope.

## video-talkcraft — github.com/Vincentwei1021/video-talkcraft
- PolyForm Noncommercial 1.0.0 — toolkit commercial use needs author's prior authorization.
  => ideas only; do not copy code, cards or text into our product.
- 2,247 stars; created 2026-08-22; pushed 2026-11-09. Default 1920x1080; vertical 1080x1920 supported.
- Input: script + finished voice track (+ optional host footage, B-roll). Chinese-first, zh/en mixed.
- voice_trim.py (pre-trim BEFORE timestamps): script is ground truth; cut only ASR inserts absent from script
  (filler from word list, repeat = stutter/restart equal to adjacent script text); never cut misheard script chars;
  off-script reworded retakes only reported unless --cut-unmatched; pauses >=0.7s -> 0.35s keeping real room tone;
  head/tail 0.25/0.5s; cut at energy valley then snap to 30fps grid; cuts.json EDL with source->new time map;
  same EDL cuts the host video; dry-run report reviewed by user first; flags "cut not in silence, listen".
- timestamps_cpu.py: FireRedASR2-CTC int8 via sherpa-onnx on CPU (767MB model) or faster-whisper fallback;
  align ASR to script per char, match key = trad/simp normalised + toneless pinyin (homophones not errors);
  Latin tokens interpolated between CJK anchors; per-sentence match <0.90 flagged for listening.
  FireRed chunked <=75s (memory grows quadratically, ~200s crashes).
  Self-reported: 110s zh/en clip vs GPU forced aligner, char offset median 20-40ms, worst 200ms.
- Semantic annotation: per sentence sem (26-term closed vocabulary) / weight main|sub / entities / need;
  drives card selection; validator enforces vocabulary and lexical rules.
- SHOTBOOK lint: same card >=3 consecutive shots FAIL; presentation cards >1/3 FAIL; every shot has a material line.
- "First shot first": build skeleton, render one sample shot with audio for user approval, then the rest.
- Segmented render: per-shot parallel segments + assemble; edit one shot -> re-render that segment +-neighbour.
  Self-reported (201s vertical, machine unknown): first render 13 -> 9 min; one-shot edit 53s; 43 stills ~1 min.
- QA: 3x4 contact sheets cut reviewer cost ~160k -> ~40k tokens; machine gates (freeze detect, jitter,
  per-cue SFX energy, face bbox via OpenCV YuNet); 1 review round, fix P0/P1, optional rounds capped at 3;
  two-pass loudnorm at delivery. Vertical: persistent elements bottom-left (right edge = platform buttons).

## Notion link supplied by user
- app.notion.com/p/3a8fc8e378a181ef82b1d6a6d484e4cf returned an empty shell ("Notion"); login-gated. Not read.
