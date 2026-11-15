# 第三方授權清冊

這個專案用到的程式庫、模型、字型和工具，都必須能商業使用（設計文件第 11 節）。這份清冊記下每一項的授權和查核日期。任何一項不能商業使用，或查不出授權，就停下來問使用者，不自己決定。

欄位：名稱、版本或來源、授權、能否商業使用、用途、查核日期。

## 直接相依

### Python 套件

`pyproject.toml` 列出的套件。版本是 `uv.lock` 鎖定的版本。下面會提到 wheel：Python 套件預先編譯好的發行檔，安裝套件時下載的就是它。下面也會提到 LGPL 和 GPL：LGPL 是 GNU 寬通用公共授權（GNU Lesser General Public License），GPL 是 GNU 通用公共授權（GNU General Public License）。兩者的差別在於，以動態函式庫的方式使用 LGPL 的函式庫，自己的程式不必跟著採用 LGPL，但函式庫要讓使用者能換掉，授權聲明也要保留；而把 GPL 程式碼併進（連結或內含於）自己的程式，整個程式都得採用 GPL。

| 名稱 | 版本或來源 | 授權 | 能否商業使用 | 用途 | 查核日期 |
|---|---|---|---|---|---|
| mlx-qwen3-asr | 0.4.4（pyproject 固定） | Apache-2.0 | 可 | 在本機執行 Qwen3-ASR 語音辨識 | 2026-11-11 |
| pypinyin | 0.55.0（pyproject 固定） | MIT | 可 | 比對腳本和辨識結果時，把字轉成無聲調拼音 | 2026-11-11 |
| opencc-python-reimplemented | 0.1.7 | Apache-2.0 | 可 | 把辨識結果轉成台灣用語的繁體中文 | 2026-11-11 |
| numpy | 2.5.4 | BSD-3-Clause；套件另含 0BSD、MIT、Zlib、CC0-1.0 授權的部分 | 可 | 音訊和數值運算 | 2026-11-11 |
| soundfile | 0.14.0 | BSD-3-Clause；wheel 另帶 LGPL-2.1 的 libsndfile | 可 | 讀寫 WAV 音訊 | 2026-11-11 |
| opencv-python-headless | 5.0.0.93 | Apache-2.0；wheel 另帶 LGPL 的 FFmpeg 等動態函式庫 | 可 | 讀影片畫格、執行 YuNet 臉部偵測 | 2026-11-11 |
| fonttools | 4.68.0 | MIT | 可 | 從可變字型切出靜態字型，檢查字型有沒有字幕用到的字 | 2026-11-11 |
| jieba | 0.42.1（pyproject 固定） | MIT（儲存庫的 LICENSE，Copyright (c) 2013 Sun Junyi；已安裝套件的 metadata 也標示 MIT） | 可 | 把繁體中文字幕斷成詞，字幕斷行只在詞和詞之間 | 2026-11-12 |
| pytest | 9.2.0 | MIT | 可 | 跑測試（只在開發時用） | 2026-11-11 |

### npm 套件

`scenes/package.json` 列出的套件，用來做動畫卡片。版本是 `scenes/package-lock.json` 鎖定的版本，`npm ci` 照這個檔案安裝。Remotion 授權（Remotion License）的條件見下方「人工確認的依據」。

| 名稱 | 版本或來源 | 授權 | 能否商業使用 | 用途 | 查核日期 |
|---|---|---|---|---|---|
| remotion | 4.0.542（package.json 固定） | Remotion License | 可（個人、員工 3 人以下的營利組織、非營利組織；更大的營利組織要買 Remotion 的公司授權） | 動畫卡片的元件和時間軸 | 2026-11-15 |
| @remotion/bundler | 4.0.542（package.json 固定） | Remotion License | 可（條件同上） | 把動畫卡片打包成網頁 | 2026-11-15 |
| @remotion/renderer | 4.0.542（package.json 固定） | Remotion License | 可（條件同上） | 用瀏覽器把動畫卡片畫成透明背景的影片 | 2026-11-15 |
| react | 19.3.1（package.json 固定） | MIT | 可 | 寫動畫卡片的畫面 | 2026-11-15 |
| react-dom | 19.3.1（package.json 固定） | MIT | 可 | 寫動畫卡片的畫面 | 2026-11-15 |

### 模型

模型檔不放在 repo 裡。Qwen3 的三個模型要事先放在本機的 Hugging Face 快取裡：`pipeline` 跑語音辨識時設成離線，不會下載它們，`uv run pipeline doctor` 檢查它們在不在。YuNet 和下一節的字幕斷行字典，由 `uv run pipeline setup` 下載到 `models/`。

| 名稱 | 版本或來源 | 授權 | 能否商業使用 | 用途 | 查核日期 |
|---|---|---|---|---|---|
| Qwen3-ASR-1.7B | Hugging Face `Qwen/Qwen3-ASR-1.7B` | Apache-2.0 | 可 | 語音辨識（預設） | 2026-11-11 |
| Qwen3-ASR-0.6B | Hugging Face `Qwen/Qwen3-ASR-0.6B` | Apache-2.0 | 可 | 語音辨識（1.7B 執行失敗時的備案） | 2026-11-11 |
| Qwen3-ForcedAligner-0.6B | Hugging Face `Qwen/Qwen3-ForcedAligner-0.6B` | Apache-2.0 | 可 | 取得每個字的時間戳 | 2026-11-11 |
| YuNet | `face_detection_yunet_2023mar.onnx`，opencv_zoo 的 `models/face_detection_yunet`（commit f12e127） | MIT（資料夾內的 LICENSE，Copyright (c) 2020 Shiqi Yu）；上游 libfacedetection.train 為 BSD-3-Clause | 可 | 偵測臉的位置（畫面推近的錨點） | 2026-11-11 |

### 字典

字典檔不放在 repo 裡，由 `uv run pipeline setup` 下載到 `models/`，`uv run pipeline doctor` 檢查它在不在、內容對不對。

| 名稱 | 版本或來源 | 授權 | 能否商業使用 | 用途 | 查核日期 |
|---|---|---|---|---|---|
| dict.txt.big | jieba 的儲存庫 `fxsjy/jieba`，`extra_dict/dict.txt.big`（commit 237dc66） | MIT（同一個儲存庫，儲存庫只有一份 MIT 授權）；README 沒有說明這份詞表的來源，見 `docs/spikes/2026-11-12-jieba-results.md` | 可 | 字幕斷行的繁體字典 | 2026-11-12 |

### 字型

| 名稱 | 版本或來源 | 授權 | 能否商業使用 | 用途 | 查核日期 |
|---|---|---|---|---|---|
| Noto Sans TC | google/fonts 的 `ofl/notosanstc`（可變字型 `NotoSansTC[wght].ttf`） | SIL OFL 1.1（SIL Open Font License，開放字型授權，下稱 OFL） | 可 | 字幕字型。`uv run pipeline setup` 從可變字型切出靜態的 Regular 和 Bold，這是 OFL 的修改版，只自用、不散布 | 2026-11-11 |

### 工具

| 名稱 | 版本或來源 | 授權 | 能否商業使用 | 用途 | 查核日期 |
|---|---|---|---|---|---|
| ffmpeg-full | Homebrew 的 `ffmpeg-full` 9.0.2 | GPL-3.0 以上 | 可（當工具用，輸出的影片不受 GPL 約束） | 剪接、燒字幕、編碼 | 2026-11-11 |
| Chrome Headless Shell | 158.0.8127.2，`@remotion/renderer` 4.0.542 下載到 `scenes/node_modules/.remotion/` | BSD-3-Clause（Chromium）；另附第三方元件的授權，見下載資料夾裡的 `LICENSE.headless_shell` | 可 | `@remotion/renderer` 用它畫出動畫卡片的每一格 | 2026-11-15 |

## 間接相依，以及允許清單以外、人工確認過的項目

前半是 `uv run python scripts/third_party.py --table` 的輸出：`uv.lock` 裡不是 `pyproject.toml` 直接列出的套件，授權讀自已安裝套件的 metadata（套件附帶、記錄授權的欄位）。授權在允許清單（MIT、BSD、Apache-2.0、ISC、PSF、MPL-2.0、Unlicense、0BSD、Zlib、HPND、LGPL）裡的，「能否商業使用」由程式填「可」；不在清單裡的，程式填「待查」，人工讀過官方授權頁之後才改成「可」。

後半是人工確認過的項目：直接相依裡授權不在清單內的 numpy，以及套件隨附、metadata 看不到的動態函式庫。

最後是 `scenes/package-lock.json` 裡授權不在允許清單上的 npm 套件，授權讀自這個檔案的 `license` 欄位。其餘的 npm 套件授權都在允許清單上，不列在這裡；`--table` 會印出全部。

`uv run python scripts/third_party.py --check` 檢查每個已安裝的相依，Python 和 npm 套件都一樣：授權在允許清單裡就通過，不在的必須有本節的一列，這一列「版本或來源」寫的版本等於已安裝的版本，而且「能否商業使用」是「可」。套件升級之後，版本對不上就不通過：要讀新版本的授權，確認之後再改那一列的版本。新增或升級套件之後要跑它。

| 名稱 | 版本或來源 | 授權 | 能否商業使用 | 用途 | 查核日期 |
|---|---|---|---|---|---|
| anyio | 4.16.0 | MIT | 可 | 由 httpx2 帶入 | 2026-11-11 |
| cffi | 2.1.1 | MIT-0 | 可 | 由 soundfile 帶入 | 2026-11-11 |
| click | 8.5.1 | BSD-3-Clause | 可 | 由 huggingface-hub 帶入 | 2026-11-11 |
| filelock | 4.2.10 | MIT | 可 | 由 huggingface-hub 帶入 | 2026-11-11 |
| fsspec | 2026.9.0 | BSD-3-Clause | 可 | 由 huggingface-hub 帶入 | 2026-11-11 |
| h11 | 0.16.0 | MIT | 可 | 由 httpcore2 帶入 | 2026-11-11 |
| hf-xet | 1.8.0 | Apache-2.0 | 可 | 由 huggingface-hub 帶入 | 2026-11-11 |
| httpcore2 | 2.16.0 | BSD-3-Clause | 可 | 由 httpx2 帶入 | 2026-11-11 |
| httpx2 | 2.16.0 | BSD-3-Clause | 可 | 由 huggingface-hub 帶入 | 2026-11-11 |
| huggingface-hub | 2.4.0 | Apache-2.0 | 可 | 由 mlx-qwen3-asr 帶入 | 2026-11-11 |
| idna | 3.20 | BSD-3-Clause | 可 | 由 anyio、httpx2 帶入 | 2026-11-11 |
| mlx | 0.32.4 | MIT | 可 | 由 mlx-qwen3-asr 帶入 | 2026-11-11 |
| mlx-metal | 0.32.4 | MIT | 可 | 由 mlx 帶入 | 2026-11-11 |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | 可 | 由 huggingface-hub 帶入 | 2026-11-11 |
| pycparser | 3.12 | BSD-3-Clause | 可 | 由 cffi 帶入 | 2026-11-11 |
| pyyaml | 6.0.3 | MIT | 可 | 由 huggingface-hub 帶入 | 2026-11-11 |
| regex | 2026.11.9 | Apache-2.0 AND CNRI-Python | 可 | 由 mlx-qwen3-asr 帶入 | 2026-11-11 |
| tqdm | 4.70.1 | MPL-2.0 AND MIT | 可 | 由 huggingface-hub 帶入 | 2026-11-11 |
| truststore | 0.10.5 | MIT | 可 | 由 httpcore2、httpx2 帶入 | 2026-11-11 |
| typing-extensions | 4.16.0 | PSF-2.0 | 可 | 由 anyio、httpx2、huggingface-hub、soundfile 帶入 | 2026-11-11 |
| numpy | 2.5.4 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | 可 | 直接相依（見上方 Python 套件表），授權含允許清單以外的 CC0-1.0，人工確認 | 2026-11-11 |
| libsndfile（soundfile 的 wheel 隨附） | soundfile 0.14.0 | LGPL-2.1 | 可 | soundfile 讀寫音訊時載入的動態函式庫，沒有修改 | 2026-11-11 |
| FFmpeg 等動態函式庫（opencv-python-headless 的 wheel 隨附） | opencv-python-headless 5.0.0.93 | LGPL-2.1、LGPL-3.0 | 可 | opencv 讀影片時載入的動態函式庫，沒有修改 | 2026-11-11 |
| remotion | 4.0.542 | Remotion License（套件內的 `LICENSE.md`） | 可（條件見下方） | 直接相依（見上方 npm 套件表） | 2026-11-15 |
| @remotion/bundler | 4.0.542 | Remotion License（套件內的 `LICENSE.md`） | 可（條件見下方） | 直接相依（見上方 npm 套件表） | 2026-11-15 |
| @remotion/renderer | 4.0.542 | Remotion License（套件內的 `LICENSE.md`） | 可（條件見下方） | 直接相依（見上方 npm 套件表） | 2026-11-15 |
| @remotion/player | 4.0.542 | Remotion License（套件內的 `LICENSE.md`） | 可（條件見下方） | 由 @remotion/sdk、@remotion/studio 帶入 | 2026-11-15 |
| @remotion/web-renderer | 4.0.542 | Remotion License（套件內的 `LICENSE.md`） | 可（條件見下方） | 由 @remotion/studio 帶入 | 2026-11-15 |
| @remotion/sdk | 4.0.542 | Remotion License（package.json 的標示；套件內沒有授權檔） | 可（條件見下方） | 由 @remotion/studio 帶入 | 2026-11-15 |
| @remotion/studio-protocol | 4.0.542 | Remotion License（package.json 的標示；套件內沒有授權檔） | 可（條件見下方） | 由 @remotion/codemods、@remotion/studio 帶入 | 2026-11-15 |
| @remotion/effects | 4.0.542 | package.json 標示 UNLICENSED，套件內沒有 Remotion 的授權檔；視為 Remotion License。內附的 `THIRD_PARTY_LICENSES.md` 說明部分著色器改寫自 EffectCraft，MIT 授權 | 可（條件見下方） | 由 @remotion/studio 帶入 | 2026-11-15 |
| @remotion/media | 4.0.542 | package.json 沒有標示，套件內沒有授權檔；視為 Remotion License | 可（條件見下方） | 由 @remotion/studio 帶入 | 2026-11-15 |
| @remotion/compositor-darwin-arm64 | 4.0.542 | package.json 沒有標示，套件內沒有授權檔；視為 Remotion License。內附的 ffmpeg 是 GPL 版本 | 可（當工具用，見下方） | 由 @remotion/renderer 帶入：Remotion 執行的 ffmpeg 等程式，把畫好的每一格編成影片 | 2026-11-15 |
| caniuse-lite | 1.0.30001826 | CC-BY-4.0 | 可 | 由 browserslist 帶入 | 2026-11-15 |
| graceful-fs | 4.2.12 | BlueOak-1.0.0 | 可 | 由 enhanced-resolve、watchpack、webpack 帶入 | 2026-11-15 |

### 人工確認的依據

三個套件的授權識別碼不在允許清單裡，兩個隨附函式庫的授權在 wheel 內的授權檔裡，都在 2026-11-11 讀過來源：

- numpy 的 CC0-1.0：Creative Commons 的說明頁寫明可以複製、修改、散布，包含商業用途，不必事先同意。numpy 本身是 BSD-3-Clause（GitHub 上 numpy 的 LICENSE.txt，PyPI 頁面的授權標示與已安裝套件的 metadata 相同）。
- regex 的 CNRI-Python：regex 自帶的 LICENSE.txt 寫明，從 CPython 的 `re` 模組沿用的部分是 CNRI 的 Python 1.6 授權，其餘新增的部分是 Apache-2.0。SPDX（公開的授權識別碼清單）頁面上的授權全文允許使用、複製、修改和散布，沒有使用領域的限制。
- cffi 的 MIT-0：PyPI 頁面標示 MIT-0。SPDX 頁面上的授權全文是去掉姓名標示條件的 MIT，允許使用、複製、修改、散布和販售。
- libsndfile：它的 GitHub 頁面標示 LGPL-2.1，soundfile 的 wheel 內附的授權檔（`COPYING`）也是 LGPL-2.1。
- opencv-python-headless 隨附的函式庫：PyPI 頁面寫明每個 wheel 都帶 LGPL-2.1 的 FFmpeg；wheel 內的 `LICENSE-3RD-PARTY.txt` 列出這個 wheel 另外帶的函式庫，授權是 LGPL-2.1 或 LGPL-3.0。

npm 套件和 Chrome Headless Shell 在 2026-11-15 讀過下載下來的檔案：

- Remotion License：`remotion`、`@remotion/bundler`、`@remotion/renderer`、`@remotion/player`、`@remotion/web-renderer` 各附一份 `LICENSE.md`，五份內容相同。個人、員工 3 人以下的營利組織、非營利組織，可以免費用 Remotion 做影片和圖片，包含商業用途，也可以為了自己的用途修改它。員工超過 3 人的營利組織，要買 Remotion 的公司授權（Company License），價格見 <https://www.remotion.pro/license>。不可以複製或修改 Remotion 的程式碼，做成自己的版本販售、出租或再授權。
- `@remotion/sdk`、`@remotion/studio-protocol`：package.json 標示 Remotion License，套件內沒有授權檔。
- `@remotion/effects`（package.json 標示 UNLICENSED）、`@remotion/media` 和 `@remotion/compositor-darwin-arm64`（沒有標示）：套件內都沒有 Remotion 的授權檔。`@remotion/effects` 另附 `THIRD_PARTY_LICENSES.md`：Fractal Noise、Shatter、Film Burn、Magnify 等著色器改寫自 EffectCraft（<https://github.com/storytold/effectcraft>），MIT 授權，全文附在檔案裡；MIT 允許商業使用，條件是散布時附上授權全文，本專案不散布這個套件。三個套件 package.json 的 `repository` 都指向 Remotion 的儲存庫 `remotion-dev/remotion`，和上面的套件是同一個儲存庫，所以視為 Remotion License 涵蓋的套件。升級 Remotion 時要再確認一次：新版本多出沒有標示授權的套件，要另外決定。
- `@remotion/compositor-darwin-arm64` 內附的 ffmpeg、ffprobe 和 FFmpeg 函式庫，編譯設定含 `--enable-gpl`、`--enable-libx264`、`--enable-libx265`、`--enable-libfdk-aac`。本專案只執行它們，不散布：`node_modules/` 不進 git，`npm ci` 從 npm 下載到使用者自己的電腦。輸出的影片不受 GPL 約束。
- caniuse-lite 的 CC-BY-4.0：套件內的 `LICENSE` 是 Creative Commons 姓名標示 4.0 國際授權條款，沒有非商業的限制，可以複製、散布和改作，條件是標示出處。
- graceful-fs 的 BlueOak-1.0.0：套件內的 `LICENSE.md` 是 Blue Oak Model License 1.0.0，允許做任何原本會侵害著作權的事；散布時要附上授權全文或連結。
- Chrome Headless Shell：下載資料夾裡的 `LICENSE.headless_shell` 開頭是 Chromium 的 BSD-3-Clause 授權，後面是各個第三方元件的授權，包括 FFmpeg 的 LGPL-2.1。全文沒有限制商業使用的條款。

### 沒有列進來的套件

- `colorama` 和 `httpx2-jsfetch`：`uv.lock` 的條件讓它們只在 Windows 和 WebAssembly 環境安裝，本機不裝。
- 開發群組（pytest 和它的相依）只在跑測試時用，`--check` 不檢查它們。
- `scenes/package-lock.json` 裡標成 optional、給其他平台用的 npm 套件，例如其他平台的 `@remotion/compositor-*` 和 `@rspack/binding-*`：本機不裝，`--check` 把它們列成 note，不檢查。
