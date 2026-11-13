# 第 0 期實測（一次性程式）

這個資料夾裡的程式只用來量測第 0 期的數字，量完就不再維護。第 1 期的程式會重新設計、照 TDD 實作，不沿用這裡的任何腳本。

計畫：`docs/superpowers/plans/2026-11-11-phase0-spike.md`
結果：`docs/spikes/2026-11-11-phase0-results.md`

## 準備

```bash
export REELS_STUDIO=<主 checkout 的絕對路徑>/studio   # 沒設定，腳本會直接報錯
uv venv --python 3.12 spikes/phase0/.venv
uv pip install --python spikes/phase0/.venv/bin/python -r spikes/phase0/requirements.txt
brew install ffmpeg-full                              # 腳本用 $(brew --prefix ffmpeg-full)/bin/ffmpeg
```

字型：`$REELS_STUDIO/projects/_spike/fonts/NotoSansTC[wght].ttf`（Noto Sans TC 可變字型，OFL）。`font_instances.py` 由它切出靜態的 `NotoSansTC-Regular.ttf` 和 `NotoSansTC-Bold.ttf`；字幕燒錄和兩個渲染器用的都是靜態檔，渲染器那份要複製到 `remotion-bench/public/` 和 `hyperframes-bench/assets/`。

## 輸入

把素材和腳本放進 `$REELS_STUDIO/inbox/spike/`。素材不是 4K HDR 的話，另外放一段 10～30 秒的 4K HDR 直拍給 `ffmpeg_bench.sh` 用。腳本不會刪除或修改 inbox 裡的檔案。

## 執行順序

所有輸出寫到 `$REELS_STUDIO/projects/_spike/`。

1. `font_instances.py`（切出靜態字型）
2. `probe.py <素材>`、`prepare_inputs.py <素材> <腳本>`
3. `warmup.sh <10 秒左右的 16 kHz 音檔>`：先下載所有模型、每個引擎跑一次，讓之後的計時不含下載
4. `asr_qwen.py`、`asr_breeze.py`、`asr_firered.py`
5. `asr_score.py`，接著 `listen_check.py`（產生聽寫確認頁：用耳朵判斷字的起點，並選出爭議片段實際說的版本）
6. `remotion-bench/`、`hyperframes-bench/` 渲染（指令見下一節），接著 `render_check.py`
7. `ffmpeg_bench.sh`
8. `meijia_check.sh`

## 渲染指令

實測時用的指令如下，`$R` 是 `$REELS_STUDIO/projects/_spike/render`。每條都用 `/usr/bin/time -l -o "$R/<輸出檔名>.time"` 包起來計時，每個數字只量一次。

Remotion（在 `remotion-bench/` 裡執行）。`remotion.config.ts` 預設的圖片格式是 JPEG，以下指令用 `--image-format=png` 改成 PNG，透明背景才保得住。先跑一次 `still` 讓 Chromium 下載完，不計時。

```bash
npx remotion still src/index.ts Bench5 "$R/remotion-still.png" --frame=75 --image-format=png
npx remotion render src/index.ts Bench5 "$R/remotion-5s.mov" --codec=prores --prores-profile=4444 --pixel-format=yuva444p10le --image-format=png
npx remotion render src/index.ts Bench5 "$R/remotion-5s.webm" --codec=vp9 --pixel-format=yuva420p --image-format=png
npx remotion render src/index.ts Bench60 "$R/remotion-60s.mov" --codec=prores --prores-profile=4444 --pixel-format=yuva444p10le --image-format=png
```

HyperFrames（在 `hyperframes-bench/` 裡執行，環境變數 `HYPERFRAMES_NO_TELEMETRY=1 DO_NOT_TRACK=1` 關掉遙測）。先用 `--format mp4 --quality draft` 渲染一次暖機，不計時。`$f` 依序是 `mov` 和 `webm`。

```bash
npx -y hyperframes@0.9.31 render --format $f -o "$R/hyperframes-5s.$f" --quiet
npx -y hyperframes@0.9.31 render -c bench60.html --format $f -o "$R/hyperframes-60s.$f" --quiet
```
