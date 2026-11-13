# 第 0 期：M1 實測 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal：** 在這台 Apple M1（16 GB）上，用使用者的真實口播素材，量出語音辨識、動畫渲染器、ffmpeg 的實際表現，產出一份實測紀錄，讓 spec 裡所有「由實測決定」的項目定案。

**Architecture：** 實測用的腳本都是一次性程式，放在 `spikes/phase0/`。它們讀 `studio/inbox/spike/` 的素材，把原始輸出寫到 `studio/projects/_spike/`（不進 git），量測數字最後整理成 `docs/spikes/2026-11-11-phase0-results.md`。第 1 期要用的程式會重新設計、照 TDD 實作，不會直接沿用這裡的腳本。

**Tech Stack：** Python 3.12（以 uv 建 venv）、mlx-qwen3-asr、faster-whisper、sherpa-onnx、jiwer、opencc-python-reimplemented、fonttools、numpy、soundfile；Node 23（已安裝）、Remotion、HyperFrames；Homebrew `ffmpeg-full` 9.0.2。

**Spec：** `docs/superpowers/specs/2026-11-11-reels-workflow-design.md`，第 12 節為主，另參照第 8、11 節。

## Global Constraints

- 一次性腳本適用全域規則的 TDD 豁免（「one-off scripts」），驗證方式是每個步驟寫明的指令和預期輸出。
- 程式碼改動在 git worktree 裡做，分支 `spike/phase0`，從 `docs/reels-workflow-design` 分出（spec 在這個分支上）。
- 使用者的素材、腳本內容、逐字稿一律不進 git；`docs/spikes/` 只放量測數字和結論，不引用腳本或逐字稿原文。
- `studio/` 已在 `.gitignore`，所以 worktree 裡沒有它。執行前先把 `REELS_STUDIO` 設成主 checkout 裡 `studio/` 的絕對路徑。所有腳本只透過這個變數找 `studio/`，變數沒設就報錯結束。原始輸出一律寫到 `$REELS_STUDIO/projects/_spike/`；下文的 `studio/...` 都是指 `$REELS_STUDIO/...`。
- 用 `/usr/bin/time -l` 計時時，`wall_s` 取 real 秒數，`peak_mb` 取「peak memory footprint」那一行除以 1048576。三個語音辨識引擎都先用 10 秒片段跑一次，讓模型下載完成。正式量測時，`wall_s` 和 `peak_mb` 都包含從本機載入模型的時間和記憶體，三個引擎用同一套量法。
- ffmpeg 一律用 `$(brew --prefix ffmpeg-full)/bin/ffmpeg` 和同目錄的 `ffprobe`（keg-only，不在 PATH）。
- Python 用 `uv venv --python 3.12 spikes/phase0/.venv`；系統 Python 3.15 太新，MLX 和 sherpa-onnx 未必有 wheel。
- 輸出規格基準：1080×1920、30fps 固定幀率（spec 8.7）。
- 授權立場：一律視為商業用途；任何候選若不能商業使用，直接淘汰（spec 11）。
- 腳本輸出只用英文加 JSON；使用者看到的說明由 Claude 用中文轉述（spec 5.3）。
- 不得刪除 `studio/inbox/` 下的任何檔案；腳本若要清除輸出，只能清 `studio/projects/_spike/` 底下自己建立的子資料夾。
- 磁碟空間：開工時可用約 38 GB，本次實測尖峰預估用掉 18～21 GB（下載約 11 GB，其餘為安裝展開、測試輸出和素材）。每次大量下載或渲染之前（Task 1 Step 1、3，Task 3、4、5，Task 7、8）先跑 `df -h /`，把可用空間記進 ledger。如果可用空間低於 15 GB，或預估這一步做完會低於 15 GB，先停下來提醒使用者，不要繼續。

## Review Focus

1. **素材超過模型單次上限**：Qwen3-ForcedAligner 單次最多 5 分鐘；FireRedASR2 有報告指出約 200 秒整段輸入會崩潰。預期三個引擎都能處理完整的 5 分鐘素材，不只 60 秒 → Task 3、4、5 用完整素材跑，FireRed 以 ≤60 秒分段。
2. **手機影片的旋轉資訊**：直拍影片可能存成 1920×1080 加 -90° 旋轉。預期 probe 回報的是顯示方向的寬高 → Task 2 的 probe 要讀 displaymatrix 並輸出 `display_w`、`display_h`。
3. **HDR 轉 SDR 顏色發白**：iPhone 的 HLG 或 Dolby Vision 素材，用錯轉換鏈會整片發白。預期轉換後膚色正常 → Task 8 抽一格並排比對，由人眼確認。
4. **腳本裡的標註、數字、英文混入參考文字**：`[B-roll: …]`、標題、阿拉伯數字（念成國字）會讓字錯誤率失真。預期參考文字已去掉標註，數字和英文另外計數 → Task 2 的 `normalize_text` 和 Task 6 的分項統計。
5. **WebM 透明通道被 ffmpeg 默默丟掉**：ffmpeg 內建的 VP9 解碼器不讀 alpha，必須指定 `-c:v libvpx-vp9`。預期合成後角落透明、卡片不透明 → Task 7 的 `render_check.py` 逐像素檢查。

---

## 檔案結構

```text
spikes/phase0/
├── README.md               一次性實測說明、重跑方式、「第 1 期不沿用」聲明
├── requirements.txt        Python 套件（固定版本，Task 1 安裝後寫回實際版本）
├── common.py               路徑、ffmpeg 路徑、計時與記憶體量測、文字正規化
├── probe.py                讀素材資訊
├── prepare_inputs.py       抽 16 kHz 音訊、產生參考文字
├── asr_qwen.py             Qwen3-ASR（0.6B、1.7B）
├── asr_breeze.py           Breeze-ASR-25（faster-whisper、CT2 int8）
├── asr_firered.py          FireRedASR2-CTC（sherpa-onnx）
├── asr_score.py            字錯誤率與時間戳偏差
├── listen_check.py         20 字波形確認頁
├── render_check.py         透明影片的合成與逐像素檢查
├── ffmpeg_bench.sh         編碼器、HDR 轉換、ASS 燒錄
├── meijia_check.sh         合成語音測試素材
├── remotion-bench/         Remotion 專案（create-video 產生）
└── hyperframes-bench/      HyperFrames 專案（hyperframes init 產生）
docs/spikes/2026-11-11-phase0-results.md   實測紀錄
```

---

### Task 1：環境

**Files:**
- Create: `spikes/phase0/README.md`、`spikes/phase0/requirements.txt`

**Interfaces:**
- Produces：`spikes/phase0/.venv`（Python 3.12）；ffmpeg-full 可用；Noto Sans TC 字型在 `$REELS_STUDIO/projects/_spike/fonts/NotoSansTC[wght].ttf`。

- [ ] **Step 1：安裝 ffmpeg-full**

Run: `brew install ffmpeg-full`
Expected：結束碼 0。

- [ ] **Step 2：確認 ffmpeg 具備必要的濾鏡和編解碼器**

Run:
```bash
FF="$(brew --prefix ffmpeg-full)/bin/ffmpeg"
"$FF" -hide_banner -filters | grep -E ' (ass|subtitles|zscale|tonemap|libvmaf|overlay|alphaextract) '
"$FF" -hide_banner -encoders | grep -E ' (libx264|h264_videotoolbox|prores_ks|libvpx-vp9) '
"$FF" -hide_banner -decoders | grep -E ' libvpx-vp9 '
```
Expected：每一行都有輸出，缺任何一項就停下來回報。

- [ ] **Step 3：建 venv 並安裝套件**

`requirements.txt` 內容：`mlx-qwen3-asr`、`faster-whisper`、`huggingface_hub`、`sherpa-onnx`、`soundfile`、`numpy`、`jiwer`、`opencc-python-reimplemented`、`fonttools`。

Run:
```bash
uv venv --python 3.12 spikes/phase0/.venv
uv pip install --python spikes/phase0/.venv/bin/python -r spikes/phase0/requirements.txt
spikes/phase0/.venv/bin/python -c "import mlx_qwen3_asr, faster_whisper, sherpa_onnx, jiwer, opencc, fontTools; print('ok')"
```
Expected：最後一行印出 `ok`。接著用 `uv pip freeze` 把實際版本寫回 `requirements.txt`。

- [ ] **Step 4：下載字型並確認涵蓋繁中**

Run:
```bash
: "${REELS_STUDIO:?set REELS_STUDIO first}"
mkdir -p "$REELS_STUDIO/projects/_spike/fonts"
curl -fL -o "$REELS_STUDIO/projects/_spike/fonts/NotoSansTC[wght].ttf" 'https://raw.githubusercontent.com/google/fonts/main/ofl/notosanstc/NotoSansTC%5Bwght%5D.ttf'
spikes/phase0/.venv/bin/python -c "import os; from fontTools.ttLib import TTFont; c=TTFont(os.environ['REELS_STUDIO']+'/projects/_spike/fonts/NotoSansTC[wght].ttf').getBestCmap(); print(all(ord(x) in c for x in '三個步驟剪出不像的短影音臺灣'))"
```
Expected：印出 `True`。

- [ ] **Step 5：寫 README 並 commit**

README 說明：這是一次性實測；輸入放在 `studio/inbox/spike/`；各腳本的執行順序；第 1 期不沿用這些程式。

```bash
git add spikes/phase0/README.md spikes/phase0/requirements.txt
git commit -m "spike: phase 0 environment"
```

---

### Task 2：準備輸入

**這一步需要使用者：** 把一支真實口播素材（1～5 分鐘）和它的腳本放進 `studio/inbox/spike/`，檔名不拘。素材如果不是 4K HDR，請另外用手機錄一段 10～30 秒的 4K HDR 直拍，給 Task 8 使用。

**Files:**
- Create: `spikes/phase0/common.py`、`spikes/phase0/probe.py`、`spikes/phase0/prepare_inputs.py`

**Interfaces:**
- Produces：
  - `common.FFMPEG: str`、`common.FFPROBE: str`（取自 `brew --prefix ffmpeg-full`）、`common.SPIKE_OUT: Path`（`$REELS_STUDIO/projects/_spike`；`REELS_STUDIO` 沒設、是空字串或不是絕對路徑就報錯結束）
  - `common.run_timed(cmd: list[str]) -> dict`：用 `/usr/bin/time -l` 執行，回傳 `{"wall_s": float, "peak_mb": float, "returncode": int}`
  - `common.normalize_text(s: str) -> str`：NFKC → OpenCC `s2twp` → 移除 `[...]`、`【...】`、單獨成行的全形括號備註、Markdown 標題符號 → 移除標點和空白 → 英文轉小寫
  - `probe.py <file>` 印出 JSON：`display_w`、`display_h`、`rotation`、`fps_avg`、`fps_r`、`is_vfr`、`color_transfer`、`is_hdr`、`duration_s`、`codec`
  - `prepare_inputs.py` 產生 `SPIKE_OUT/audio16k.wav`（16 kHz 單聲道）、`SPIKE_OUT/ref.txt`（正規化後的參考文字）、`SPIKE_OUT/ref_stats.json`（`chars`、`digits`、`latin_tokens`）

- [ ] **Step 1：實作 `common.py`、`probe.py`、`prepare_inputs.py`**

`is_hdr` 在 `color_transfer` 是 `arib-std-b67` 或 `smpte2084` 時為真；`is_vfr` 在 `r_frame_rate` 和 `avg_frame_rate` 不同時為真；`rotation` 讀 side data 的 displaymatrix。

- [ ] **Step 2：自我檢查文字正規化**

Run: `cd spikes/phase0 && .venv/bin/python -c "from common import normalize_text as n; assert n('[B-roll: 咖啡店]今天，我們來聊 Reels！')=='今天我們來聊reels'; assert n('这个软件')=='這個軟體'; print('ok')"`
Expected：`ok`

- [ ] **Step 3：跑 probe 和 prepare_inputs**

Run（在 `spikes/phase0/` 下）：`.venv/bin/python probe.py "$REELS_STUDIO/inbox/spike/<素材>"` 以及 `.venv/bin/python prepare_inputs.py "$REELS_STUDIO/inbox/spike/<素材>" "$REELS_STUDIO/inbox/spike/<腳本>"`
Expected：probe 的 `display_w` < `display_h`（直拍）；`audio16k.wav` 長度和 `duration_s` 相差 0.1 秒以內；`ref_stats.json` 的 `chars` > 0。

- [ ] **Step 4：Commit**

```bash
git add spikes/phase0/common.py spikes/phase0/probe.py spikes/phase0/prepare_inputs.py
git commit -m "spike: probe and input preparation"
```

---

### Task 3：Qwen3-ASR（0.6B、1.7B）

**Files:**
- Create: `spikes/phase0/asr_qwen.py`

**Interfaces:**
- Consumes：`SPIKE_OUT/audio16k.wav`、`common.run_timed`
- Produces：`SPIKE_OUT/asr/qwen3-0.6b.json`、`SPIKE_OUT/asr/qwen3-1.7b.json`，三個引擎共用以下格式：
```json
{ "engine": "qwen3-1.7b", "runtime": "mlx-qwen3-asr 0.4.0", "audio_s": 183.2,
  "wall_s": 41.0, "peak_mb": 3900.0, "text": "…",
  "tokens": [{ "t": "我", "start": 1.23, "end": 1.41 }, { "t": "reels", "start": 2.0, "end": 2.4 }] }
```
中文一個字一個 token，英文一個詞一個 token，標點不列入；引擎只給起點時 `end` 為 `null`。

- [ ] **Step 1：查 CLI 的語言代碼和 JSON 輸出格式**

Run: `spikes/phase0/.venv/bin/mlx-qwen3-asr --list-languages`，再用 10 秒片段跑一次 `--timestamps -f json -o <暫存>`，看輸出的欄位名稱。
Expected：找到中文的語言名稱，JSON 裡有逐字或逐詞的時間。

- [ ] **Step 2：實作 `asr_qwen.py --model {Qwen/Qwen3-ASR-0.6B|Qwen/Qwen3-ASR-1.7B}`**

用 `run_timed` 包住 `mlx-qwen3-asr <audio> --model <m> --language <中文名稱> --timestamps -f json -o <dir>`，再把輸出轉成共用格式。

- [ ] **Step 3：用完整素材跑兩個模型**

Expected：兩個 JSON 都存在；`tokens` 數量和 `ref_stats.chars` 相差在 20% 以內；最後一個 token 的 `start` 在素材最後 5 秒內（代表沒有中途截斷）。

- [ ] **Step 4：Commit**

```bash
git add spikes/phase0/asr_qwen.py
git commit -m "spike: Qwen3-ASR runner"
```

---

### Task 4：Breeze-ASR-25

**Files:**
- Create: `spikes/phase0/asr_breeze.py`

**Interfaces:**
- Consumes：同 Task 3
- Produces：`SPIKE_OUT/asr/breeze-25-int8.json`（共用格式）

- [ ] **Step 1：實作 `asr_breeze.py`**

用 `huggingface_hub.snapshot_download("shdennlin/breeze-asr-25-ct2", allow_patterns=["int8/*"])` 下載，`faster_whisper.WhisperModel(<path>/int8, device="cpu", compute_type="int8")`，`transcribe(..., language="zh", word_timestamps=True, vad_filter=True)`。腳本分成兩部分：`asr_breeze.py --worker` 做轉錄並寫出 JSON；主程式用 `run_timed` 包住 worker，把量到的 `wall_s` 和 `peak_mb` 補進 JSON。

- [ ] **Step 2：用完整素材跑**

Expected：同 Task 3 Step 3 的三個條件。若轉錄速度慢於即時（`wall_s` > `audio_s`），照樣記錄，不改用其他執行環境。

- [ ] **Step 3：Commit**

```bash
git add spikes/phase0/asr_breeze.py
git commit -m "spike: Breeze-ASR-25 runner"
```

---

### Task 5：FireRedASR2-CTC

**Files:**
- Create: `spikes/phase0/asr_firered.py`

**Interfaces:**
- Consumes：同 Task 3
- Produces：`SPIKE_OUT/asr/firered2-ctc-int8.json`（共用格式；`end` 可為 `null`）

- [ ] **Step 1：下載模型**

Run:
```bash
mkdir -p "$REELS_STUDIO/projects/_spike/models" && cd "$REELS_STUDIO/projects/_spike/models"
curl -fLO https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-fire-red-asr2-ctc-zh_en-int8-2026-02-25.tar.bz2
tar xf sherpa-onnx-fire-red-asr2-ctc-zh_en-int8-2026-02-25.tar.bz2 && ls sherpa-onnx-fire-red-asr2-ctc-zh_en-int8-2026-02-25
```
Expected：看到 `.onnx` 模型檔和 `tokens.txt`。確認後刪掉壓縮檔（`rm sherpa-onnx-fire-red-asr2-ctc-zh_en-int8-2026-02-25.tar.bz2`，單一檔案，約 0.5 GB）。

- [ ] **Step 2：實作 `asr_firered.py`**

`sherpa_onnx.OfflineRecognizer.from_fire_red_asr_ctc(model=..., tokens=..., num_threads=4)`。音訊切成每段 ≤60 秒：在每段 50～60 秒之間找 10 ms 能量最低的位置下刀；每段結果的 `stream.result.tokens` 和 `stream.result.timestamps` 加上該段的起點偏移後合併。量測方式和 Task 4 相同（`--worker` 加 `run_timed`）。

- [ ] **Step 3：用完整素材跑**

Expected：同 Task 3 Step 3。若 `stream.result.timestamps` 是空的，在 JSON 加 `"timestamps": false` 並照樣記錄字錯誤率；這個引擎就不能用於字幕時間。

- [ ] **Step 4：Commit**

```bash
git add spikes/phase0/asr_firered.py
git commit -m "spike: FireRedASR2-CTC runner"
```

---

### Task 6：評分

**Files:**
- Create: `spikes/phase0/asr_score.py`、`spikes/phase0/listen_check.py`

**Interfaces:**
- Consumes：`SPIKE_OUT/asr/*.json`、`SPIKE_OUT/ref.txt`、`common.normalize_text`
- Produces：
  - `asr_score.cer_of(hyp: str, ref: str) -> float`：兩個已正規化的字串切成單字（英文詞保持一個單位）後，用 `jiwer.cer` 算字錯誤率
  - `SPIKE_OUT/score.json`：每個引擎的 `cer`、`cer_cjk_only`、`native_traditional_ratio`、`rtf`（`wall_s / audio_s`）、`peak_mb`、`start_dev_ms_median`、`start_dev_ms_p90`
  - `SPIKE_OUT/listen/index.html`：20 個字的波形確認頁

- [ ] **Step 1：實作 `asr_score.py`**

- `cer`：`jiwer.cer` 比較 `normalize_text(text)` 和 `ref.txt`，兩者都先切成單字（英文詞保持一個單位）。
- `cer_cjk_only`：只保留中日韓統一表意文字後再算一次，排除數字和英文的干擾。
- `native_traditional_ratio`：引擎原始輸出中，經 OpenCC `s2twp` 轉換後沒有改變的中文字比例。
- 時間偏差：以 `qwen3-1.7b` 的時間戳為參考（它的強制對齊器公開數據是平均偏移 33 ms），兩邊的 token 用 `difflib.SequenceMatcher` 對齊，只比較相同字的 `start`，算中位數和 P90。

- [ ] **Step 2：自我檢查評分**

Run: `cd spikes/phase0 && .venv/bin/python -c "from asr_score import cer_of as c; assert c('今天天氣很好','今天天氣很好')==0; assert abs(c('今天天氣很好','今天天汽很好')-1/6)<1e-9; print('ok')"`
Expected：`ok`

- [ ] **Step 3：實作 `listen_check.py`**

用固定種子（`random.Random(42)`）從參考引擎的 token 中，平均分布地挑 20 個中文字。每個字用 ffmpeg `showwavespic` 產生該字起點前後各 0.6 秒的波形圖（1200×200），並用 `atrim` 切出同一段音訊。產生一個淺色 HTML 頁：每個字一列，顯示該字和前後各兩字、波形圖、播放器，並用絕對定位的直線標出各引擎預測的起點，每個引擎一種顏色，附圖例。

- [ ] **Step 4：跑評分並請使用者確認波形**

Run: `cd spikes/phase0 && .venv/bin/python asr_score.py && .venv/bin/python listen_check.py`，接著請使用者用瀏覽器開 `$REELS_STUDIO/projects/_spike/listen/index.html`。
**這一步需要使用者：** 對每個字判斷參考引擎的起點線是否落在該字發音開始處（容許約一個字的 1/4 寬），回報 20 個裡有幾個正確。
Expected：`score.json` 每個引擎都有完整欄位；拿到使用者回報的「參考引擎 20 字正確數」。若少於 16 個，在實測紀錄註明參考不可靠，時間偏差數據只作參考。

- [ ] **Step 5：Commit**

```bash
git add spikes/phase0/asr_score.py spikes/phase0/listen_check.py
git commit -m "spike: ASR scoring and listening check"
```

---

### Task 7：動畫渲染器

**Files:**
- Create: `spikes/phase0/remotion-bench/`（由 create-video 產生後修改）、`spikes/phase0/hyperframes-bench/`（由 hyperframes init 產生後修改）、`spikes/phase0/render_check.py`

**Interfaces:**
- Consumes：Noto Sans TC 字型、`common.FFMPEG`
- Produces：`SPIKE_OUT/render/{remotion,hyperframes}-{5s,60s}.{mov,webm}`、`SPIKE_OUT/render/check.json`（每個輸出檔一筆，欄位有 `file`、`alpha_ok`、`cjk_ok`、`wall_s`、`peak_mb`、`sec_per_output_sec`）

兩個渲染器做同一個畫面：1080×1920、30fps、背景全透明。一張圓角卡片（`#FFFFFF` 不透明、寬 900、高 360、圓角 32）在 0～0.5 秒從畫面下方滑到 y=780，卡片上是標題「三個步驟，剪出不像 AI 的短影音」，字型是本機的 Noto Sans TC 700、字級 64。5 秒版只播一次；60 秒版讓同一段動畫每 5 秒重播一次。字型一律用本機檔案載入，不連 Google Fonts。

- [ ] **Step 1：Remotion 專案**

Run: `cd spikes/phase0 && npx create-video@latest --yes --blank remotion-bench`，接著建立 `Bench5`（150 格）和 `Bench60`（1800 格）兩個 composition，把字型複製到 `public/` 並用 `staticFile()` 載入，渲染前等字型載入完成。記下 `npx remotion versions` 的輸出。

- [ ] **Step 2：Remotion 渲染並計時**

Run（在 `remotion-bench/` 下，每條指令前加 `/usr/bin/time -l`）：
```bash
R="$REELS_STUDIO/projects/_spike/render"; mkdir -p "$R"
npx remotion render src/index.ts Bench5 "$R/remotion-5s.mov" --codec=prores --prores-profile=4444 --pixel-format=yuva444p10le --image-format=png
npx remotion render src/index.ts Bench5 "$R/remotion-5s.webm" --codec=vp9 --pixel-format=yuva420p --image-format=png
```
60 秒版用 `Bench60` 只輸出比較快的那一種格式。Chromium 下載不計入時間：先跑一次 `npx remotion still` 暖機。
Expected：檔案都存在且 ffprobe 讀得到。

- [ ] **Step 3：HyperFrames 專案**

Run: `cd spikes/phase0 && npx hyperframes@latest init hyperframes-bench`，再看 `npx hyperframes render --help` 確認透明輸出的參數名稱。寫一個同樣畫面的 `index.html`，用 GSAP 的暫停時間軸，字型用 `@font-face` 指向本機檔案。記下 CLI 版本。

- [ ] **Step 4：HyperFrames 渲染並計時**

Run: 依 `--help` 的參數輸出 WebM 透明版（研究筆記記載為 `--format webm`）；如果支援 MOV ProRes 4444，也輸出一份。5 秒和 60 秒各一次，每條指令前加 `/usr/bin/time -l`。
Expected：檔案都存在且 ffprobe 讀得到。

- [ ] **Step 5：實作並執行 `render_check.py`**

對每個輸出檔：
- WebM 要用 `-c:v libvpx-vp9` 放在 `-i` 前面解碼。
- 抽第 75 格（2.5 秒處）輸出成 `rgba` raw 格式，讀進 numpy。
- 檢查左上角 (10,10) 的 alpha 是 0，卡片中心 (540,960) 的 alpha 是 255。
- 疊到純色背景 `#3366CC` 上，輸出 PNG。

`wall_s` 和 `peak_mb` 取自 Step 2、4 的 `/usr/bin/time -l` 輸出。

Expected：每個輸出檔都有一筆。接著用 Read 工具看每張合成 PNG，確認中文字形正常、沒有方框、字重是粗體；看完的結果記進 `cjk_ok`。

- [ ] **Step 6：Commit**

只 commit 自己寫的原始碼，不放 `node_modules/` 和輸出檔。

```bash
git add spikes/phase0/render_check.py spikes/phase0/remotion-bench spikes/phase0/hyperframes-bench
git commit -m "spike: renderer benchmarks"
```

---

### Task 8：ffmpeg

**Files:**
- Create: `spikes/phase0/ffmpeg_bench.sh`

**Interfaces:**
- Consumes：使用者素材、4K HDR 樣本、字型、`probe.py`
- Produces：`SPIKE_OUT/ffmpeg/bench.json`，欄位有 `x264_s`、`vt_s`、`x264_vmaf`、`vt_vmaf`、`tonemap_s_per_min`、`tonemap_mb_per_min`、`tonemap_1350_s_per_min`、`tonemap_1350_mb_per_min`、`ass_ok`

- [ ] **Step 1：編碼器比較**

取素材前 60 秒，先縮成 1080×1920、30fps，輸出一份無損參考檔（`-c:v libx264 -qp 0`）。再從參考檔各編碼一次：
- `-c:v libx264 -preset medium -crf 18 -pix_fmt yuv420p`
- `-c:v h264_videotoolbox -b:v 12M -pix_fmt yuv420p`

各自計時，並用 `libvmaf` 對參考檔算平均 VMAF。

- [ ] **Step 2：4K HDR 轉 SDR 工作檔**

對 4K HDR 樣本用以下轉換鏈，保留原始解析度輸出 H.264 `-crf 16 -preset fast`：

```text
zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p
```

記錄每分鐘素材的處理時間和檔案大小。再加一次 `scale=1350:2400` 的版本做比較。兩版各抽第 3 秒的畫面，和原始 HDR 畫面（不轉換、直接抽格）並排成一張 PNG。
**這一步需要使用者：** 看並排圖，確認轉換後的膚色和亮度正常、沒有發白。

- [ ] **Step 3：ASS 繁中燒錄**

寫一個 5 秒的 ASS 檔，一行字幕「這是繁體中文字幕測試：臺灣」，字型名稱 `Noto Sans TC`。用 `subtitles=<ass>:fontsdir=$REELS_STUDIO/projects/_spike/fonts` 燒進 5 秒純色影片，抽一格，用 Read 工具確認字形正確、沒有方框。結果記進 `ass_ok`。

- [ ] **Step 4：Commit**

```bash
git add spikes/phase0/ffmpeg_bench.sh
git commit -m "spike: ffmpeg benchmarks"
```

---

### Task 9：合成語音素材和授權查核

**Files:**
- Create: `spikes/phase0/meijia_check.sh`

**Interfaces:**
- Produces：`SPIKE_OUT/meijia/fixture.wav`、`SPIKE_OUT/meijia/score.json`、`SPIKE_OUT/licenses.json`

- [ ] **Step 1：Meijia 合成語音能不能當測試素材**

用 `say -v Meijia -o fixture.aiff` 念一段固定的 3 句腳本（第 2 句故意重複一次，句間插入 `[[slnc 1200]]` 製造 1.2 秒停頓），轉成 16 kHz wav。用 Task 6 選出的最佳引擎轉錄並算字錯誤率。
Expected：`score.json` 有 `cer`；停頓位置在逐字時間戳上能看出 ≥1.0 秒的間隔。

- [ ] **Step 2：授權查核**

逐一打開以下項目的授權檔或模型卡，記錄授權名稱、能否商業使用、查核日期和網址：
- Qwen3-ASR 模型、mlx-qwen3-asr
- Breeze-ASR-25、shdennlin/breeze-asr-25-ct2、faster-whisper
- FireRedASR2、sherpa-onnx
- Remotion（LICENSE.md，確認個人商業使用免費）
- HyperFrames、GSAP（HyperFrames 的範例會用到）
- Noto Sans TC、ffmpeg-full
- jiwer、opencc-python-reimplemented

授權不明的標 `unknown`，不推測。

- [ ] **Step 3：Commit**

```bash
git add spikes/phase0/meijia_check.sh
git commit -m "spike: synthetic speech fixture check"
```

---

### Task 10：實測紀錄和定案

**Files:**
- Create: `docs/spikes/2026-11-11-phase0-results.md`

**Interfaces:**
- Consumes：`score.json`、使用者回報的波形確認結果、`render/check.json`、`ffmpeg/bench.json`、`meijia/score.json`、`licenses.json`

- [ ] **Step 1：寫實測紀錄**

依序寫以下內容：
1. 測試環境：機型、記憶體、各工具版本，以及素材的長度、解析度、是否 HDR。
2. 語音辨識比較表。
3. 渲染器比較表。
4. ffmpeg 結果。
5. Meijia 結果。
6. 授權表。
7. 依 spec 第 12 節的選擇規則，對以下項目各寫一個決定和理由：語音辨識引擎、渲染器、透明影片格式、編碼器、工作檔解析度策略、測試素材來源。
8. 15 分鐘時間預算：以 60 秒成品估算機器運算時間，逐步列出。

數字一律直接取自上述 JSON；任何沒量到的項目標「未量測」，並寫出原因。紀錄裡不引用使用者的腳本或逐字稿原文。

- [ ] **Step 2：請使用者審閱**

把實測紀錄的決定摘要給使用者，等確認。使用者要求改動的話，改完再確認一次。

- [ ] **Step 3：Commit**

```bash
git add docs/spikes/2026-11-11-phase0-results.md
git commit -m "docs: phase 0 spike results"
```

- [ ] **Step 4：清理空間（使用者確認後才刪）**

實測紀錄 commit 之後，列出以下候選和各自大小，給使用者確認：
- `$REELS_STUDIO/projects/_spike/` 底下的大型測試輸出（`render/`、`ffmpeg/` 的影片檔、`audio16k.wav`）。小的 JSON 和字型保留。
- 沒被選中的語音辨識模型：Hugging Face 快取裡的 Qwen3 和 Breeze 模型用 `huggingface_hub.scan_cache_dir()` 找出、`delete_revisions(...).execute()` 刪除；FireRed 模型在 `_spike/models/` 底下，刪它的資料夾。

使用者確認後才刪。資料夾刪除依全域規則：路徑先檢查不是空字串、相對路徑、`/` 或 HOME，shell 裡寫成 `${VAR:?}`。刪完跑 `df -h /`，把釋放的空間告訴使用者。
