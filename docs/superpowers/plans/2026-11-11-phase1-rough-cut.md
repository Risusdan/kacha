# 第 1 期：有字幕的順剪 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal：** 從 `studio/inbox/` 的一支口播素材和它的腳本，經過轉錄對齊、粗剪候選、導演寫的 `edit.json`、關卡 1 分鏡稿，輸出一支有繁中字幕的順剪 mp4（spec 第 13 節第 1 期）。

**Architecture：** `pipeline/` 是 Python 套件，入口 `uv run pipeline <指令>`。每個指令讀寫一個專案資料夾 `studio/projects/<日期-代稱>/`，在 stdout 印一行英文 JSON。前處理、轉錄、對齊、粗剪候選、分鏡稿、渲染都是固定程式；Claude 只寫 `work/edit.json`，由 `reel` 和 `storyboard` 兩個 skill 帶著走。渲染時原始素材只解碼一次：先轉 30 fps 固定幀率，用 `select` 依片段取格，縮放一次到 1080×1920，再燒 ASS 字幕；聲音由 Python 依同一組片段逐取樣剪接，所以影音長度一定相同。

**Tech Stack：** Python 3.12（uv）、mlx-qwen3-asr 0.4.4（Qwen3-ASR-1.7B，備案 0.6B，加 Qwen3-ForcedAligner-0.6B）、pypinyin 0.55.0、opencc-python-reimplemented、numpy、soundfile、opencv-python-headless（YuNet 臉部偵測）、fonttools、pytest；Homebrew `ffmpeg-full` 9.0.2（libass、h264_videotoolbox、hevc_videotoolbox）。

**Spec：** `docs/superpowers/specs/2026-11-11-reels-workflow-design.md`（第 13 節第 1 期，並參照第 4～8、10、11 節）。選型依 `docs/spikes/2026-11-11-phase0-results.md` 第 7 節。

## Global Constraints

- 分支 `feat/phase1-rough-cut`，從 `docs/reels-workflow-design` 分出，在 git worktree 裡做。Commit 訊息不加任何 AI 或工具署名；會開編輯器的 git 指令一律加 `--no-edit` 或 `-m`。
- 行為改動一律先寫失敗的測試（spec 10.2）。文件、設定檔例外。
- 只支援 macOS（Apple M1、16 GB）。ffmpeg 一律用 `$(brew --prefix ffmpeg-full)/bin/ffmpeg` 和同目錄的 `ffprobe`；環境變數 `REELS_FFMPEG_DIR` 可覆寫這個資料夾。
- 授權：第三方的程式庫、模型、字型都必須能商業使用（spec 11）。video-talkcraft 只參考公開文件的做法，不安裝、不執行，也不複製它的程式碼、卡片和文字。
- `studio/` 不進 git。位置由環境變數 `REELS_STUDIO` 決定：沒設或是空字串時用 `<repo>/studio`，設了但不是絕對路徑就報錯。資料夾名稱一律用英文。
- `inbox/` 只讀不寫。遞迴刪除一律經過 `pipeline.safe_delete.safe_rmtree`，它拒絕空路徑、相對路徑、`/`、家目錄及其上層目錄（spec 10.1）。
- `edit.json` 是唯一由 Claude 寫的檔案。程式不產生也不改寫它；測試裡的 `edit.json` 由 `tests/helpers.py` 組出來。
- `pipeline` 指令的輸出用英文加 JSON（stdout 一行 JSON），由 Claude 轉述成中文。結束碼：0 成功，1 執行錯誤，2 `edit.json` 檢查不通過。
- 使用者的素材、腳本、逐字稿不進 git，也不寫進 docs。測試只用 `tests/fixtures/` 裡自己寫的腳本。
- 測試環境：`tests/conftest.py` 的 autouse fixture 先記下真實的 Hugging Face 快取位置（`HF_HOME`，沒設就是真實家目錄下的 `.cache/huggingface`），再把 `HOME` 指到暫存目錄裡的假家目錄、`REELS_STUDIO` 指到暫存 studio，並把 `HF_HOME` 設回真實位置（否則會重新下載 4.7 GB 的模型）。執行 pytest 時，`TMPDIR` 指到當下 session 的 scratchpad。
- 標 `@pytest.mark.slow` 的測試會跑真的語音辨識（模型要已在快取裡）。`uv run pytest` 預設跳過它們，`uv run pytest -m slow` 才跑。
- 輸出規格（spec 8.7）：1080×1920、30fps 固定幀率、H.264、yuv420p、AAC 48 kHz 立體聲 128 kbps 以上，位元率低於 25 Mbps，檔案小於 300 MB。編碼器 h264_videotoolbox 12 Mbps（實測決定），音訊 AAC 192 kbps。本期只輸出 `out/final.mp4`；`final-nobgm.mp4` 屬第 3 期。
- 縮放：每個來源只縮放一次（spec 8.7）。唯一例外是 HDR 備案的工作檔，先縮到 1350×2400 以內（實測紀錄第 7 節）。
- 工作檔：SDR 素材不做工作檔，渲染時直接讀原始檔，在濾鏡裡轉成 30fps 固定幀率。這和 spec 8.1「輸出工作檔到 `work/`」不同，依據是實測紀錄第 7 節：要不要做工作檔，等量過 SDR 素材再決定（Task 17 量）。HDR 素材才在前處理做工作檔。
- 字幕（spec 8.4）：每行最多 16 字，最多 2 行，預設 1 行；逐字高亮用 ASS 的 `\k` 標籤；安全區 x 65～1015、y 269～1248；在臉部偵測框下方；每支影片固定一個位置；字幕全程顯示；斷行在程式裡完成，不依賴 libass 的自動換行。本期每個字幕事件只出 1 行。
- 字型：Noto Sans TC 的靜態 Regular（400）和 Bold（700），由 `pipeline setup` 從可變字型切出。直接用可變字型時，libass 會悄悄改用系統字型（實測紀錄第 4 節），所以渲染要檢查 fontselect 紀錄。
- 粗剪（spec 8.3）：只剪贅字（嗯、呃、欸、那個、就是）、重講、NG 段；辨識錯的腳本文字永遠不剪；措辭和腳本不同的重講段落只列出來，不自動剪；停頓超過 0.7 秒壓到 0.35 秒，保留真實環境音；切點先找音量最低處，再對齊到 30fps 的格線；切點不在靜音裡的標 `needs_listen`。
- 對齊（spec 8.2）：比對前轉成無聲調拼音，同音字不算錯；英文詞的時間依前後的中文字內插；每句吻合度低於 0.90 標 `needs_listen`；和腳本對不上的句子改用辨識結果，經 OpenCC `s2twp` 轉換，並標 `needs_listen`。數字要接受多種念法（實測紀錄 D9、D10）。
- 語音辨識：`Qwen/Qwen3-ASR-1.7B`；執行失敗時自動改用 `Qwen/Qwen3-ASR-0.6B` 重跑一次，並記錄在 `transcript.json`。
- 渲染失敗自動重試一次，還失敗就回報錯誤訊息和 log 位置（spec 10.1）。
- 本期新定的初始值，集中寫成常數，實測後再調：切點搜尋範圍 ±150 毫秒；靜音門檻為噪音底（10 毫秒窗 RMS 的第 10 百分位）加 6 dB；改用辨識文字的吻合度門檻 0.50；NG 段相似度門檻 0.80，措辭不同的重講 0.50～0.80；片頭片尾各留最多 0.3 秒；切點兩側 10 毫秒淡入淡出；字幕在句子最後一個字後多留 0.5 秒；HDR 工作檔 hevc_videotoolbox 30 Mbps。
- 本期 `edit.json` 只接受 `version`、`style`、`cuts`、`sentences`、`captions` 五個鍵。`scenes`、`broll`、`punch_in`、`sfx`、`music` 屬第 2、3 期，出現就報錯。所以 `validate` 本期實作規則 1 和 7；規則 2～6 檢查的對象（場景、B-roll、音效、音樂）本期還不存在，跟著那些鍵在第 2、3 期加。錨點（`at`、`until`）換算時間也屬第 2 期。
- 磁碟：每次下載或渲染之前跑 `df -h /`，可用空間低於 15 GB 就停下來提醒使用者。

## Review Focus

1. **素材超過 5 分鐘**：時間對齊模型單次上限 5 分鐘，第 0 期沒量過更長的素材。mlx-qwen3-asr 若回報某段 `truncated`，轉錄必須報錯停下，不能產生缺了後段的逐字稿 → Task 8 `test_truncated_chunk_raises`。
2. **講者整句沒講，或整句臨場改詞**：沒講的句子不出字幕、不要求寫進 `edit.json`、分鏡稿標「沒講到」；改詞的句子改用辨識文字並標「請聽」 → Task 9 `test_skipped_sentence_not_spoken`、`test_reworded_sentence_uses_asr_text`；Task 11 `test_unspoken_sentence_not_required`。
3. **腳本含 ASS 或 HTML 的特殊字元**（`{`、`}`、`\`、`<`、`&`）：字幕照字顯示，不被當成 ASS 標籤；分鏡稿網頁不被破壞 → Task 12 `test_ass_escapes_override_chars`；Task 13 `test_html_escapes_script_text`。
4. **studio 路徑含空白或單引號**：ffmpeg 的濾鏡參數照樣正確 → Task 14 `test_render_in_path_with_space_and_quote`。
5. **偵測不到臉，或臉太低、字幕放在臉下方會超出安全區**：改用預設位置或安全區底部，並在分鏡稿上警告 → Task 12 `test_caption_top_no_face`、`test_caption_top_face_too_low_warns`；Task 13 `test_storyboard_shows_face_warning`。

---

## 檔案結構

```text
pyproject.toml / uv.lock         uv 專案，相依套件固定版本
pipeline/
├── __main__.py, cli.py          指令列：子指令註冊、JSON 輸出、結束碼
├── errors.py                    PipelineError、EditInvalid、UnsafeDeleteError、AsrTruncatedError
├── paths.py                     repo、studio、projects 的位置
├── safe_delete.py               安全刪除
├── project.py                   專案資料夾、state.json、步驟略過、關卡核准
├── media.py                     ffmpeg 位置、probe、執行 ffmpeg 並寫 log
├── assets.py                    setup（字型、YuNet 模型）和 doctor
├── style.py                     讀風格設定檔
├── prep.py                      前處理：音訊、HDR 工作檔、臉部框
├── faces.py                     YuNet 臉部偵測
├── textnorm.py                  比對單位、拼音鍵、數字念法、s2twp
├── script.py                    腳本切句、標註
├── asr.py                       執行 mlx-qwen3-asr、讀結果、0.6B 備案
├── align.py                     動態規劃對齊、插入段分類、transcript.json
├── audio.py                     音量包絡、噪音底、切點對齊
├── trim.py                      粗剪候選、句子切點、trim.json
├── edit.py                      edit.json 檢查、片段、時間換算
├── captions.py                  字幕斷行、位置、ASS、缺字檢查
├── storyboard.py                關卡 1 網頁和 storyboard.md
└── render.py                    剪接音訊、合成、輸出規格檢查、重試
styles/default/style.json        風格設定檔（本期只有畫布、安全區、字型、字幕）
styles/default/fonts/            setup 產生的靜態字型（不進 git）
models/                          setup 下載的 YuNet 模型（不進 git）
scripts/third_party.py           間接相依的授權檢查
tests/                           單元和整合測試；tests/.cache/ 放測試時產生的影片（不進 git）
tests/fixtures/meijia/           Meijia 合成語音素材、腳本、錄好的辨識結果
tests/fixtures/face/             公有領域人像照片和來源說明
CLAUDE.md、THIRD_PARTY.md
.claude/skills/reel/SKILL.md、.claude/skills/storyboard/SKILL.md
docs/acceptance/                 第 1 期驗收紀錄（Task 17）
```

## 資料格式

跨 task 共用的檔案，欄位以這裡為準。時間一律是原始素材的秒數（浮點數），`map_time` 換算後才是成品時間。

`state.json`：

```json
{"version": 1, "slug": "coffee", "name": "2026-11-12-coffee", "created_at": "2026-11-12T10:00:00+08:00",
 "source": {"video": "/abs/studio/inbox/a/clip.mov", "video_sha256": "…", "script": "/abs/…/script.md", "script_sha256": "…"},
 "steps": {"prep": {"done_at": "…", "inputs": {"video": {"path": "/abs/…/clip.mov", "sha256": "…"}}, "outputs": ["work/probe.json"]}},
 "gates": {"gate1": {"approved_at": "…", "edit_sha256": "…"}}}
```

`work/probe.json`：`display_w`、`display_h`、`rotation`、`fps_r`、`fps_avg`、`is_vfr`、`color_transfer`、`is_hdr`、`duration_s`、`audio_tracks`（數量）、`video_source`（渲染要讀的影片：SDR 是原始檔，HDR 是 `work/video.mp4`）、`warnings`（英文字串陣列）。

`work/faces.json`：`{"model": "yunet-2023mar", "samples": 30, "boxes": [{"t": 2.0, "x0": 0.31, "y0": 0.22, "x1": 0.69, "y1": 0.47, "score": 0.93}]}`，座標以顯示畫面為 0～1，每個取樣格只留最大的臉。

`work/transcript.json`：

```json
{"version": 1, "asr": {"model": "Qwen/Qwen3-ASR-1.7B", "fallback_used": false, "runtime": "mlx-qwen3-asr 0.4.4"},
 "audio_s": 189.35,
 "tokens": [{"text": "今", "start": 0.48, "end": 0.56, "op": "match", "s": "s01"}],
 "runs": [{"h0": 12, "h1": 19, "kind": "retake", "text": "第一步是把停頓", "similarity": 1.0, "sentence": "s02"}],
 "sentences": [{"id": "s01", "text": "今天我們來聊短影音剪輯。", "display_source": "script", "spoken": true,
   "start": 0.48, "end": 2.9, "ratio": 0.97, "needs_listen": false, "reasons": [],
   "units": [{"text": "今", "span": [0, 1], "start": 0.48, "end": 0.56, "src": "asr"}]}],
 "markers": [{"before": "s03", "text": "[B-roll: 剪輯軟體畫面]"}]}
```

- `tokens[].op` 是 `match`、`sub`、`ins` 之一；`s` 是對到的句子代號，`ins` 為 `null`。
- `runs[].kind` 是 `filler`、`repeat`、`retake`、`reworded`、`extra` 之一。`h0`、`h1` 是 `tokens` 的索引範圍（左閉右開）。
- `sentences[].reasons` 的值：`low_match`、`extra_speech`、`asr_text`。`units[].src` 是 `asr` 或 `interp`。

`work/trim.json`：

```json
{"version": 1, "noise_floor_db": -52.3,
 "trims": [{"id": "t01", "kind": "pause", "start": 3.4, "end": 4.233333, "sentence": "s01", "text": "",
            "reason": "pause 1.44s -> 0.35s", "suggest": "cut", "needs_listen": false}],
 "bounds": {"s01": {"cut_in": 0.2, "cut_out": 3.233333, "needs_listen": false}}}
```

`trims[].kind` 是 spec 7.1 的 `filler`、`repeat`、`pause`、`retake` 之一；措辭不同的重講也是 `retake`，但 `suggest` 為 `list`。所有 `start`、`end`、`cut_in`、`cut_out` 都在 30fps 格線上。

`work/edit.json`（本期）：

```json
{"version": 1, "style": "default",
 "cuts": {"accept": ["t01", "t02"], "reject": ["t03"], "extra": [{"from": "s05", "to": "s06", "reason": "off topic"}]},
 "sentences": [{"id": "s01", "sem": "hook", "weight": "main"}],
 "captions": {"mode": "line"}}
```

`styles/default/style.json`：

```json
{"name": "default",
 "canvas": {"w": 1080, "h": 1920, "fps": 30},
 "safe_area": {"x0": 65, "x1": 1015, "y0": 269, "y1": 1248},
 "fonts": {"family": "Noto Sans TC", "regular": "fonts/NotoSansTC-Regular.ttf", "bold": "fonts/NotoSansTC-Bold.ttf"},
 "captions": {"size_px": 56, "bold": true, "color": "#FFFFFF", "highlight": "#FFD400", "outline_color": "#000000",
              "outline_px": 4, "line_height_px": 90, "max_chars_per_line": 16, "max_lines": 2,
              "default_top_px": 1040, "face_margin_px": 40, "hold_s": 0.5}}
```

---

### Task 1：專案骨架、路徑、指令列

**Files:**
- Create: `pyproject.toml`、`uv.lock`、`pipeline/__init__.py`、`pipeline/__main__.py`、`pipeline/cli.py`、`pipeline/errors.py`、`pipeline/paths.py`、`tests/conftest.py`、`tests/helpers.py`（測試專用的小工具，後面的 task 陸續加）、`tests/test_paths.py`、`tests/test_cli.py`
- Modify: `.gitignore`（加 `tests/.cache/`、`styles/default/fonts/`、`models/`）

**Interfaces:**
- Produces:
  - `pipeline.errors`：`PipelineError(message, **extra)`（`exit_code = 1`，`extra` 併進錯誤 JSON）；`EditInvalid(errors: list[str])`（`exit_code = 2`）；`UnsafeDeleteError(PipelineError)`；`AsrTruncatedError(PipelineError)`。
  - `pipeline.paths`：`repo_root() -> Path`、`studio_root() -> Path`、`projects_root() -> Path`、`inbox_root() -> Path`。
  - `pipeline.cli`：`command(name: str)` 裝飾器，註冊 `handler(args: argparse.Namespace) -> dict`，同時可加 argparse 參數（`command(name, args=[("name",{}), ("--force",{"action":"store_true"})])`）；`registered_commands() -> list[str]`；`main(argv: list[str] | None = None) -> int`。成功時印 `{"ok": true, **handler 的結果}`，`PipelineError` 印 `{"ok": false, "error": …, **extra}` 並回傳它的 `exit_code`，argparse 的錯誤也轉成 JSON、回傳 1。
  - `tests/conftest.py`：autouse fixture `isolated_env`（見 Global Constraints）；fixture `studio`（回傳暫存 studio 的 `Path`，已建好 `inbox/`、`projects/`）。

- [ ] **Step 1：建 uv 專案**

`pyproject.toml`：`requires-python = "==3.12.*"`；相依 `mlx-qwen3-asr==0.4.4`、`pypinyin==0.55.0`、`opencc-python-reimplemented`、`numpy`、`soundfile`、`opencv-python-headless`、`fonttools`；dev 群組 `pytest`；`[project.scripts] pipeline = "pipeline.cli:main"`；`[tool.pytest.ini_options]` 設 `markers = ["slow: runs the real ASR engine"]`、`addopts = "-m 'not slow'"`、`testpaths = ["tests"]`。

Run: `uv sync && uv run python -c "import mlx_qwen3_asr, cv2, pypinyin, opencc, soundfile, fontTools"`
Expected: 無錯誤；`uv.lock` 產生。

- [ ] **Step 2：寫失敗的測試**

```python
# tests/test_paths.py
def test_studio_root_default_is_repo_studio(monkeypatch):
    monkeypatch.delenv("REELS_STUDIO", raising=False)
    assert studio_root() == repo_root() / "studio"

def test_studio_root_empty_means_default(monkeypatch):
    monkeypatch.setenv("REELS_STUDIO", "")
    assert studio_root() == repo_root() / "studio"

def test_studio_root_rejects_relative(monkeypatch):
    monkeypatch.setenv("REELS_STUDIO", "studio")
    with pytest.raises(PipelineError, match="absolute"):
        studio_root()

def test_projects_and_inbox_under_studio(studio):
    assert projects_root() == studio / "projects" and inbox_root() == studio / "inbox"

# tests/test_cli.py
def test_unknown_command_prints_json_error(capsys):
    assert main(["frobnicate"]) == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False

def test_registered_handler_output(capsys):
    @command("_echo_test")
    def _echo(args): return {"hello": "世界"}
    assert main(["_echo_test"]) == 0
    assert json.loads(capsys.readouterr().out) == {"ok": True, "hello": "世界"}

def test_edit_invalid_exit_code_2(capsys):
    @command("_bad_edit_test")
    def _bad(args): raise EditInvalid(["cuts.accept: unknown trim t99"])
    assert main(["_bad_edit_test"]) == 2
    assert json.loads(capsys.readouterr().out)["errors"] == ["cuts.accept: unknown trim t99"]
```

- [ ] **Step 3：執行測試確認失敗**

Run: `uv run pytest tests/test_paths.py tests/test_cli.py -v`
Expected: FAIL（`ModuleNotFoundError: pipeline.paths` 等）

- [ ] **Step 4：實作** `pipeline/errors.py`、`pipeline/paths.py`、`pipeline/cli.py`、`pipeline/__main__.py`（呼叫 `cli.main` 並 `sys.exit`）、`tests/conftest.py`。`repo_root()` 用 `Path(__file__).resolve().parents[1]`。JSON 用 `ensure_ascii=False`。

- [ ] **Step 5：執行測試確認通過**

Run: `uv run pytest -v`
Expected: 全部 PASS

- [ ] **Step 6：Commit**

```bash
git add pyproject.toml uv.lock .gitignore pipeline tests
git commit -m "feat: pipeline package skeleton, studio paths and JSON CLI"
```

---

### Task 2：安全刪除

**Files:**
- Create: `pipeline/safe_delete.py`、`tests/test_safe_delete.py`

**Interfaces:**
- Produces:
  - `check_delete_target(path: str | Path, *, allowed_root: Path, home: str | None) -> Path`：純檢查，不刪任何東西；通過時回傳解析後的絕對路徑，否則拋 `UnsafeDeleteError`。
  - `safe_rmtree(path: str | Path, *, allowed_root: Path, remover: Callable[[Path], None] = shutil.rmtree) -> None`：用 `os.environ.get("HOME")` 呼叫 `check_delete_target`，通過後才呼叫 `remover`。
- 拒絕的情況（每一條都要有測試）：原始參數是空字串或 `Path("")`；相對路徑（含 `.`）；解析後是 `/`；`home` 是 `None` 或空字串（不得改用 `os.path.expanduser` 或帳號資料庫找家目錄）；等於家目錄；是家目錄的上層；不在 `allowed_root` 底下，或就是 `allowed_root` 本身；`allowed_root` 是相對路徑、`/`、家目錄或其上層；路徑本身或任何一層上層是符號連結（`Path(path).resolve() != Path(os.path.abspath(path))`）。

依全域規則，傳給 `safe_rmtree` 的路徑一律是 `tmp_path` 底下的假資料夾，且 `remover` 是紀錄器；`/`、真實家目錄這類真正危險的路徑只交給不會刪東西的 `check_delete_target`。第一個測試確認紀錄器真的接上了。

- [ ] **Step 1：寫失敗的測試**

```python
# tests/test_safe_delete.py（測試順序照這裡，紀錄器的檢查排第一）
@pytest.fixture
def root(tmp_path, monkeypatch):
    home = tmp_path / "fakehome"; home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    r = tmp_path / "studio" / "projects"; r.mkdir(parents=True)
    return r

def test_recorder_is_wired(root):
    target = root / "p1" / "review"; target.mkdir(parents=True)
    rec = []
    safe_rmtree(target, allowed_root=root, remover=rec.append)
    assert rec == [target.resolve()] and target.exists()

def test_deletes_inside_root(root):
    target = root / "p1" / "tmp"; (target / "x").mkdir(parents=True)
    safe_rmtree(target, allowed_root=root)
    assert not target.exists() and (root / "p1").exists()

@pytest.mark.parametrize("bad", ["", Path(""), ".", "relative/dir"])
def test_refuses_empty_and_relative(root, bad):
    rec = []
    with pytest.raises(UnsafeDeleteError):
        safe_rmtree(bad, allowed_root=root, remover=rec.append)
    assert rec == []

def test_check_refuses_filesystem_root(tmp_path):
    with pytest.raises(UnsafeDeleteError):
        check_delete_target("/", allowed_root=tmp_path / "projects", home=str(tmp_path / "fakehome"))

def test_refuses_home_and_its_ancestor(root, tmp_path):   # 都是假家目錄
    for target in (tmp_path / "fakehome", tmp_path):
        with pytest.raises(UnsafeDeleteError):
            safe_rmtree(target, allowed_root=root, remover=lambda p: pytest.fail("deleted"))

@pytest.mark.parametrize("home", [None, ""])
def test_refuses_when_home_unset_or_empty(root, monkeypatch, home):
    target = root / "p1" / "review"; target.mkdir(parents=True)
    if home is None: monkeypatch.delenv("HOME")
    else: monkeypatch.setenv("HOME", home)
    with pytest.raises(UnsafeDeleteError, match="HOME"):
        safe_rmtree(target, allowed_root=root, remover=lambda p: pytest.fail("deleted"))

def test_refuses_outside_root_and_root_itself(root, tmp_path):
    outside = tmp_path / "elsewhere"; outside.mkdir()
    for target in (outside, root):
        with pytest.raises(UnsafeDeleteError):
            safe_rmtree(target, allowed_root=root, remover=lambda p: pytest.fail("deleted"))

def test_refuses_symlink_and_symlinked_parent(root, tmp_path):
    real = tmp_path / "realdir"; (real / "sub").mkdir(parents=True)   # 連結目標也在暫存目錄裡
    (root / "link").symlink_to(real)
    for target in (root / "link", root / "link" / "sub"):
        with pytest.raises(UnsafeDeleteError):
            safe_rmtree(target, allowed_root=root, remover=lambda p: pytest.fail("deleted"))

def test_refuses_dangerous_allowed_root(tmp_path):
    home = tmp_path / "fakehome"; home.mkdir()
    for bad_root in (Path("rel"), Path("/"), home, tmp_path):
        with pytest.raises(UnsafeDeleteError):
            check_delete_target(home / "x", allowed_root=bad_root, home=str(home))
```

- [ ] **Step 2：執行測試確認失敗**

Run: `uv run pytest tests/test_safe_delete.py -v`
Expected: FAIL（`ImportError`）

- [ ] **Step 3：實作** `pipeline/safe_delete.py`，介面和拒絕規則照上面。

- [ ] **Step 4：執行測試確認通過**

Run: `uv run pytest tests/test_safe_delete.py -v`
Expected: 全部 PASS，且 `test_recorder_is_wired` 是第一個跑的。

- [ ] **Step 5：Commit**

```bash
git add pipeline/safe_delete.py tests/test_safe_delete.py
git commit -m "feat: safe recursive delete guarded against empty, relative, root and home paths"
```

---

### Task 3：專案資料夾和 state.json

**Files:**
- Create: `pipeline/project.py`、`tests/test_project.py`
- Modify: `pipeline/cli.py`（註冊 `new`、`status`、`approve`）、`tests/helpers.py`、`tests/conftest.py`

**Interfaces:**
- Consumes：Task 1 的 `projects_root()`、`command`、`PipelineError`。
- Produces（`pipeline.project`）：
  - `@dataclass(frozen=True) class Project: name: str; dir: Path`，屬性 `work`、`review`、`out`、`state_path`、`edit_path`（`work/edit.json`）、`md_path`（`project.md`）。
  - `file_sha256(path: Path) -> str`
  - `create_project(video: Path, script: Path, slug: str, date: str | None = None) -> Project`：`slug` 要符合 `^[a-z0-9]+(-[a-z0-9]+)*$`；資料夾名 `<date>-<slug>`，`date` 預設今天（`YYYY-MM-DD`）；建 `work/`、`review/`、`out/`、`state.json`、`project.md`（標題加「## 決定」「## 使用者的意見」兩節）；專案已存在就報錯。
  - `load_project(name: str) -> Project`：接受完整資料夾名或只給 slug（唯一時）。
  - `load_state(p: Project) -> dict`、`save_state(p: Project, state: dict) -> None`（先寫暫存檔再 `os.replace`）。
  - `run_step(p: Project, step: str, inputs: dict[str, Path], outputs: list[Path], fn: Callable[[], dict], force: bool = False) -> dict`：輸入的雜湊值都沒變、輸出都在，就不跑 `fn`，回傳 `{"skipped": True}`；否則跑 `fn`，記錄 `done_at`、輸入雜湊、輸出，回傳 `fn()` 的結果加 `"skipped": False`。
  - `step_done(p: Project, step: str) -> bool`
  - `approve_gate(p: Project, gate: str) -> dict`：`gate1` 要求 `steps.storyboard.inputs.edit.sha256` 等於目前 `edit.json` 的雜湊，否則報錯 `run storyboard first`。所以 Task 13 的 `run_step` 必須用 `edit` 這個鍵登記 `edit.json`。
  - `gate_ok(p: Project, gate: str) -> bool`：核准時記錄的 `edit_sha256` 等於目前 `edit.json` 的雜湊。
  - `next_step(p: Project) -> str`：依序回傳 `prep`、`transcribe`、`trim`、`edit`（還沒有 `edit.json`）、`storyboard`、`approve`、`render`、`done`。
- CLI：`new <video> <script> --slug S [--date D]` → `{"project": name, "dir": …}`；`status [name]`（不給名字就列出全部專案和各自的 `next`）；`approve <name> gate1`。

- [ ] **Step 1：寫失敗的測試**

```python
# tests/test_project.py（inbox_files fixture 在暫存 studio/inbox/a/ 放 clip.mp4、script.md 兩個小檔）
def test_new_creates_layout(inbox_files):
    p = create_project(*inbox_files, slug="coffee", date="2026-11-12")
    assert p.name == "2026-11-12-coffee"
    assert all((p.dir / d).is_dir() for d in ("work", "review", "out"))
    st = load_state(p)
    assert st["source"]["video_sha256"] == file_sha256(inbox_files[0]) and st["steps"] == {}
    assert p.md_path.read_text(encoding="utf-8").count("## ") == 2

@pytest.mark.parametrize("slug", ["Coffee", "a b", "../x", "", "-a"])
def test_new_rejects_bad_slug(inbox_files, slug):
    with pytest.raises(PipelineError): create_project(*inbox_files, slug=slug)

def test_new_refuses_existing(inbox_files):
    create_project(*inbox_files, slug="c", date="2026-11-12")
    with pytest.raises(PipelineError, match="exists"):
        create_project(*inbox_files, slug="c", date="2026-11-12")

def test_new_leaves_inbox_untouched(inbox_files):
    before = {f: (file_sha256(f), f.stat().st_mtime_ns) for f in inbox_files}
    create_project(*inbox_files, slug="c")
    assert before == {f: (file_sha256(f), f.stat().st_mtime_ns) for f in inbox_files}

def test_run_step_skips_rerun_and_reruns_on_change(project):
    src = project.work / "in.txt"; src.write_text("a"); out = project.work / "out.txt"
    calls = []
    def fn(): calls.append(1); out.write_text("x"); return {"n": 1}
    assert run_step(project, "s", {"in": src}, [out], fn)["skipped"] is False
    assert run_step(project, "s", {"in": src}, [out], fn)["skipped"] is True
    src.write_text("b"); run_step(project, "s", {"in": src}, [out], fn)
    out.unlink(); run_step(project, "s", {"in": src}, [out], fn)
    assert len(calls) == 3

def test_gate_ok_turns_false_when_edit_changes(project):
    project.edit_path.write_text("{}")
    run_step(project, "storyboard", {"edit": project.edit_path}, [], lambda: {})
    approve_gate(project, "gate1"); assert gate_ok(project, "gate1")
    project.edit_path.write_text('{"x":1}'); assert not gate_ok(project, "gate1")

def test_approve_requires_current_storyboard(project, capsys):
    project.edit_path.write_text("{}")
    assert main(["approve", project.name, "gate1"]) == 1
    assert "storyboard" in json.loads(capsys.readouterr().out)["error"]

def test_next_step_starts_with_prep(project):
    assert next_step(project) == "prep"
```

- [ ] **Step 2：執行測試確認失敗**

Run: `uv run pytest tests/test_project.py -v`
Expected: FAIL（`ImportError`）

- [ ] **Step 3：實作** `pipeline/project.py` 和三個 CLI 指令；`project` fixture 加進 `tests/conftest.py`。

- [ ] **Step 4：執行測試確認通過**

Run: `uv run pytest -v`
Expected: 全部 PASS

- [ ] **Step 5：Commit**

```bash
git add pipeline tests
git commit -m "feat: project folders, state.json step skipping and gate approval"
```

---

### Task 4：ffmpeg、probe、setup、doctor、風格設定檔

**Files:**
- Create: `pipeline/media.py`、`pipeline/assets.py`、`pipeline/style.py`、`styles/default/style.json`、`tests/media_fixtures.py`、`tests/test_media.py`、`tests/test_assets.py`
- Modify: `pipeline/cli.py`（註冊 `setup`、`doctor`）

**Interfaces:**
- Produces：
  - `pipeline.media`：`ffmpeg_bin() -> str`、`ffprobe_bin() -> str`；`@dataclass class Probe`（欄位同「資料格式」的 `probe.json`，`video_source` 和 `warnings` 除外，另加 `video_start_s`、`audio_start_s`、`audio_channels`）；`probe(path: Path) -> Probe`（旋轉 ±90 度時寬高對調；`is_vfr = r_frame_rate != avg_frame_rate`；`is_hdr = color_transfer in ("arib-std-b67", "smpte2084")`）；`run_ffmpeg(args: list[str], log: Path, cwd: Path | None = None) -> None`（stderr 寫進 `log`，失敗時拋 `PipelineError(..., log=str(log))`）。
  - `pipeline.style`：`load_style(name: str = "default") -> dict`（多一個鍵 `dir`，指向風格資料夾）；`font_path(style: dict, weight: str) -> Path`。
  - `pipeline.assets`：`FONT_URL = "https://raw.githubusercontent.com/google/fonts/main/ofl/notosanstc/NotoSansTC%5Bwght%5D.ttf"`、`FONT_SHA256 = "864727d210d54f2537bbe23b3a839436c3992af72de9322af5270897246bd44f"`；`YUNET_URL`、`YUNET_SHA256`（Step 1 查定）；`fetch(url: str, sha256: str, dest: Path, download: Callable[[str], bytes] = _urlopen) -> Path`（雜湊不符就拋錯，什麼都不寫）；`setup_fonts(dest: Path) -> list[Path]`（用 `instancer.instantiateVariableFont(vf, {"wght": w}, updateFontNames=True)` 切出 400 和 700）；`setup_models(dest: Path) -> Path`；`doctor() -> dict`。
  - `tests/media_fixtures.py`：`make_clip(dest_dir: Path, *, w=1080, h=1920, seconds=2.0, audio="tone440", hdr=False, rotate=0, vfr=False, audio_delay=0.0, gray_counter=False) -> Path`，依參數雜湊快取在 `tests/.cache/`。`audio` 可為 `"tone440"`、`"mono"`、`"two_tracks"`（第 0 軌 440 Hz、第 1 軌 1000 Hz）、`None`；`gray_counter=True` 時第 N 幀整片亮度為 `16 + 4 * (N % 50)`。
- CLI：`setup`（下載並驗證字型、切出靜態字型、下載 YuNet）；`doctor` → `{"ffmpeg": path, "libass": bool, "h264_videotoolbox": bool, "hevc_videotoolbox": bool, "fonts": bool, "yunet": bool, "asr_cli": bool, "asr_models": {"1.7b": bool, "0.6b": bool}, "free_gb": float, "warnings": [...]}`；可用空間低於 15 GB 時加警告。必要項目缺任何一個，`ok` 為 false。

- [ ] **Step 1：查定 YuNet 模型**

從 opencv/opencv_zoo 的 `models/face_detection_yunet/` 取得 `face_detection_yunet_2023mar.onnx`。先讀該資料夾的 LICENSE，確認可以商業使用（預期是 MIT，**HYPOTHESIS**，以讀到的檔案為準）；不能商業使用就停下來問使用者。下載後算 sha256，把網址和雜湊寫進 `YUNET_URL`、`YUNET_SHA256`，授權記進 ledger，Task 15 要用。

- [ ] **Step 2：寫失敗的測試**

```python
# tests/test_media.py
def test_probe_portrait(tmp_path):
    p = probe(make_clip(tmp_path))
    assert (p.display_w, p.display_h, p.is_vfr, p.is_hdr, p.audio_tracks) == (1080, 1920, False, False, 1)

def test_probe_rotated_reports_display_dims(tmp_path):
    p = probe(make_clip(tmp_path, w=1920, h=1080, rotate=90))
    assert (p.display_w, p.display_h) == (1080, 1920) and abs(p.rotation) == 90

def test_probe_vfr_and_hdr_flags(tmp_path):
    assert probe(make_clip(tmp_path, vfr=True)).is_vfr
    assert probe(make_clip(tmp_path, hdr=True)).is_hdr

def test_run_ffmpeg_failure_reports_log(tmp_path):
    with pytest.raises(PipelineError) as e:
        run_ffmpeg(["-i", str(tmp_path / "missing.mp4"), str(tmp_path / "o.mp4")], tmp_path / "x.log")
    assert e.value.extra["log"].endswith("x.log")

# tests/test_assets.py
def test_fetch_rejects_bad_checksum(tmp_path):
    with pytest.raises(PipelineError, match="sha256"):
        fetch("https://example.invalid/f", "0" * 64, tmp_path / "f", download=lambda u: b"wrong")
    assert not (tmp_path / "f").exists()

def test_static_fonts_have_family_and_weight():      # 需要先跑過 uv run pipeline setup
    style = load_style()
    for weight, cls in (("regular", 400), ("bold", 700)):
        f = TTFont(font_path(style, weight))
        assert f["name"].getDebugName(1) == "Noto Sans TC" and f["OS/2"].usWeightClass == cls

def test_style_values_match_spec():
    s = load_style()
    assert s["safe_area"] == {"x0": 65, "x1": 1015, "y0": 269, "y1": 1248}
    assert s["captions"]["max_chars_per_line"] == 16 and s["captions"]["max_lines"] == 2
```

- [ ] **Step 3：執行測試確認失敗**

Run: `uv run pytest tests/test_media.py tests/test_assets.py -v`
Expected: FAIL（`ImportError`）

- [ ] **Step 4：實作** `media.py`、`style.py`、`assets.py`、`style.json`、`tests/media_fixtures.py`、兩個 CLI 指令。HDR 測試片用 libx265 10 位元、`-color_trc arib-std-b67 -color_primaries bt2020 -colorspace bt2020nc`；旋轉用輸入選項 `-display_rotation`（ffmpeg 9.0.2 若不接受，改用它接受的寫法，記 ruling）；VFR 用 `select` 丟掉幾格並以 `-fps_mode passthrough` 輸出。

- [ ] **Step 5：下載資源並確認環境**

Run: `df -h / && uv run pipeline setup && uv run pipeline doctor`
Expected: `setup` 的 `ok` 為 true；`doctor` 各項為 true，`free_gb` 高於 15。

- [ ] **Step 6：執行測試確認通過**

Run: `uv run pytest -v`
Expected: 全部 PASS

- [ ] **Step 7：Commit**

```bash
git add pipeline styles/default/style.json tests
git commit -m "feat: ffmpeg probe, font and model setup, doctor, default style"
```

---

### Task 5：前處理（音訊、HDR 工作檔）

**Files:**
- Create: `pipeline/prep.py`、`tests/test_prep.py`
- Modify: `pipeline/cli.py`（註冊 `prep`）、`tests/helpers.py`、`tests/conftest.py`、`tests/media_fixtures.py`

**Interfaces:**
- Consumes：Task 3 的 `run_step`、`Project`；Task 4 的 `probe`、`run_ffmpeg`。
- Produces：`prep(p: Project, force: bool = False) -> dict`，寫出：
  - `work/probe.json`（含 `video_source`、`warnings`）。
  - `work/audio48k.wav`：只取第一條音軌（`-map 0:a:0`，iPhone 的第二條是 APAC），48 kHz、立體聲、16 位元；單聲道複製成兩聲道，多於兩聲道就混成兩聲道。音訊和影像的起始時間不同時，補或裁到 wav 的 0 秒正好是第一個影格。
  - `work/audio16k.wav`：16 kHz 單聲道，給語音辨識用。
  - `work/video.mp4`：只有 HDR 素材才產生。濾鏡依序：`scale=w=1350:h=2400:force_original_aspect_ratio=decrease:flags=lanczos`（只在比 1350×2400 大時縮）、實測紀錄的色調轉換鏈 `zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p`；`hevc_videotoolbox -b:v 30M -tag:v hvc1`，不含音訊。
  - 臉部框（`work/faces.json`）在 Task 6 接上。
  - 錯誤：沒有音軌就報錯 `source has no audio track`。警告：長寬比不是 9:16 時加 `aspect W:H will be center-cropped to 9:16`。
- CLI：`prep <name> [--force]` → `{"video_source": …, "is_hdr": …, "is_vfr": …, "duration_s": …, "warnings": [...], "skipped": …}`

- [ ] **Step 1：寫失敗的測試**

```python
# tests/test_prep.py（project_with(clip) 是 conftest 的 helper：把 clip 和一份腳本放進 inbox，再 create_project）
def test_prep_writes_both_wavs(tmp_path):
    p = project_with(make_clip(tmp_path, seconds=3)); prep(p)
    a48, a16 = sf.info(p.work / "audio48k.wav"), sf.info(p.work / "audio16k.wav")
    assert (a48.samplerate, a48.channels, a16.samplerate, a16.channels) == (48000, 2, 16000, 1)
    assert abs(a48.duration - 3.0) < 0.05

def test_prep_uses_first_audio_track(tmp_path):
    p = project_with(make_clip(tmp_path, audio="two_tracks")); prep(p)
    assert abs(dominant_hz(p.work / "audio48k.wav") - 440) < 10      # helper in media_fixtures

def test_prep_mono_becomes_stereo(tmp_path):
    p = project_with(make_clip(tmp_path, audio="mono")); prep(p)
    assert sf.info(p.work / "audio48k.wav").channels == 2

def test_prep_aligns_delayed_audio(tmp_path):
    p = project_with(make_clip(tmp_path, seconds=3, audio_delay=0.5)); prep(p)
    assert abs(first_sound_s(p.work / "audio48k.wav") - 0.5) < 0.02  # 素材音訊在影像 0.5 秒後才開始

def test_prep_no_audio_errors(tmp_path):
    with pytest.raises(PipelineError, match="no audio"):
        prep(project_with(make_clip(tmp_path, audio=None)))

def test_prep_sdr_reads_original(tmp_path):
    clip = make_clip(tmp_path); p = project_with(clip); prep(p)
    pj = json.loads((p.work / "probe.json").read_text())
    assert pj["video_source"] == load_state(p)["source"]["video"] and not (p.work / "video.mp4").exists()

def test_prep_hdr_makes_sdr_work_file(tmp_path):
    p = project_with(make_clip(tmp_path, w=1440, h=2560, hdr=True)); prep(p)
    out = probe(p.work / "video.mp4")
    assert not out.is_hdr and (out.display_w, out.display_h) == (1350, 2400)

def test_prep_warns_non_portrait(tmp_path):
    p = project_with(make_clip(tmp_path, w=1920, h=1080)); r = prep(p)
    assert any("center-cropped" in w for w in r["warnings"])

def test_prep_leaves_inbox_untouched(tmp_path):
    clip = make_clip(tmp_path); p = project_with(clip)
    src = Path(load_state(p)["source"]["video"]); before = file_sha256(src)
    prep(p); assert file_sha256(src) == before
```

- [ ] **Step 2：執行測試確認失敗**

Run: `uv run pytest tests/test_prep.py -v`
Expected: FAIL（`ImportError`）

- [ ] **Step 3：實作** `pipeline/prep.py` 和 `prep` 指令。`run_step` 的輸入是素材影片，輸出是上面列的檔案。

- [ ] **Step 4：執行測試確認通過**

Run: `uv run pytest -v`
Expected: 全部 PASS

- [ ] **Step 5：Commit**

```bash
git add pipeline tests
git commit -m "feat: prep extracts aligned audio and makes an SDR work file for HDR sources"
```

---

### Task 6：臉部偵測

**Files:**
- Create: `pipeline/faces.py`、`tests/test_faces.py`、`tests/fixtures/face/portrait.jpg`、`tests/fixtures/face/SOURCE.md`
- Modify: `pipeline/prep.py`（寫 `work/faces.json`）、`tests/media_fixtures.py`（加 `still_to_video`）

**Interfaces:**
- Consumes：Task 4 的 `ffmpeg_bin()`、`YUNET` 模型路徑；Task 5 的 `prep`。
- Produces：
  - `@dataclass(frozen=True) class Box: t: float; x0: float; y0: float; x1: float; y1: float; score: float`（以顯示畫面為 0～1）
  - `sample_times(duration_s: float, every_s: float = 2.0, max_samples: int = 60) -> list[float]`：`[every_s * (k + 0.5) for k in range(int(duration_s // every_s))]`；超過 `max_samples` 格時，把間隔改成 `duration_s / max_samples` 再算。
  - `detect_faces(video: Path, model: Path, times: list[float]) -> list[Box]`：ffmpeg 在每個時間點取一格、縮到寬 540（會自動套用旋轉），用 `cv2.FaceDetectorYN`（`score_threshold=0.8`）偵測，每格只留面積最大的一張臉。
  - `prep` 多寫 `work/faces.json`（格式見「資料格式」）。

- [ ] **Step 1：準備測試照片**

從 Wikimedia Commons 選一張正面、公有領域的人像（例如美國聯邦政府的官方肖像，屬美國政府作品），縮到高 720 px 存成 `portrait.jpg`。`SOURCE.md` 記下網址、作者、授權。

- [ ] **Step 2：寫失敗的測試**

```python
# tests/test_faces.py
def test_detects_face_in_portrait_video(tmp_path):
    video = still_to_video(FIXTURES / "face" / "portrait.jpg", tmp_path, seconds=4)   # helper in media_fixtures
    boxes = detect_faces(video, yunet_model(), sample_times(4.0))
    assert len(boxes) >= 1
    b = boxes[0]; assert 0 <= b.x0 < b.x1 <= 1 and 0 <= b.y0 < b.y1 <= 1 and b.score >= 0.8

def test_no_face_in_test_pattern(tmp_path):
    assert detect_faces(make_clip(tmp_path, seconds=4), yunet_model(), sample_times(4.0)) == []

def test_sample_times():
    assert sample_times(4.0) == [1.0, 3.0]
    assert len(sample_times(10.0, every_s=0.1, max_samples=5)) == 5

def test_prep_writes_faces_json(tmp_path):
    p = project_with(make_clip(tmp_path, seconds=4)); prep(p)
    fj = json.loads((p.work / "faces.json").read_text())
    assert fj["model"] == "yunet-2023mar" and fj["boxes"] == [] and fj["samples"] == 2
```

- [ ] **Step 3：執行測試確認失敗**

Run: `uv run pytest tests/test_faces.py -v`
Expected: FAIL（`ImportError`）

- [ ] **Step 4：實作** `pipeline/faces.py`，並在 `prep` 裡呼叫（讀 `video_source`，用 `sample_times(duration_s)`）。conftest 加 `yunet_model()`：回傳 `models/` 裡的模型路徑，檔案不在就讓測試失敗並提示先跑 `uv run pipeline setup`（不可跳過）。

- [ ] **Step 5：執行測試確認通過**

Run: `uv run pytest -v`
Expected: 全部 PASS

- [ ] **Step 6：Commit**

```bash
git add pipeline tests
git commit -m "feat: YuNet face boxes sampled during prep"
```

---

### Task 7：比對單位、拼音鍵、數字念法、腳本切句

**Files:**
- Create: `pipeline/textnorm.py`、`pipeline/script.py`、`tests/test_textnorm.py`、`tests/test_script.py`

**Interfaces:**
- Produces（`pipeline.textnorm`）：
  - `Key = frozenset[str]`；`FILLERS = ("嗯", "呃", "欸", "那個", "就是")`
  - `@dataclass(frozen=True) class Unit: text: str; kind: str; span: tuple[int, int]; percent: bool = False`；`kind` 是 `cjk`、`latin`、`num`、`other`。
  - `split_units(text: str) -> list[Unit]`：逐字先做 NFKC 再判斷；拉丁字母連續成一個單位（存小寫）；數字連續成一個單位（可含一個小數點，後面緊跟 `%` 時 `percent=True`）；中日韓表意文字一字一個單位；其他屬於 Unicode L、N、M 類的字元一字一個；標點和空白不成單位。`span` 指回原始字串。
  - `char_key(ch: str) -> Key`：pypinyin `Style.NORMAL`、`heteronym=True` 的所有讀音；非漢字回傳 `{ch.lower()}`。
  - `unit_key(u: Unit) -> Key`：`latin` 回傳 `{text}`，`num` 回傳 `{"#" + text}`，其他用 `char_key`。
  - `number_readings(digits: str, percent: bool = False) -> list[str]`：至少包含標準念法、「兩」的變體（2、200、2000 等開頭的 2）、省略尾數的念法（150 → 一百五、1500 → 一千五）、逐位念法（零和〇兩種）、10～19 的「一十」變體、小數「點」；`percent=True` 時每個念法前加「百分之」。
  - `unit_alts(u: Unit) -> list[tuple[Key, ...]]`：`num` 回傳每個念法逐字的拼音鍵，再加一個 `(frozenset({"#" + digits}),)`；其他回傳 `[(unit_key(u),)]`。
  - `to_taiwan(text: str) -> str`：OpenCC `s2twp`。
- Produces（`pipeline.script`）：
  - `@dataclass class Sentence: id: str; text: str; units: list[Unit]`（`units[].span` 指向 `text`）
  - `@dataclass class Marker: before: str | None; text: str`
  - `parse_script(text: str) -> tuple[list[Sentence], list[Marker]]`：先移除行首的 Markdown 標題符號；`[...]`、`【...】`、整行只有 `（...）` 或 `(...)` 的內容變成 `Marker`，掛在下一句前面；斷句點是 `。！？!?`（連同緊接的 `」』"”`）和換行；去掉空句；代號 `s01`、`s02`…（兩位數，超過 99 句照樣遞增為 `s100`）。

- [ ] **Step 1：寫失敗的測試**

```python
# tests/test_textnorm.py
def test_split_units_mixed():
    us = split_units("用 Remotion 做 3 支")
    assert [u.text for u in us] == ["用", "remotion", "做", "3", "支"]
    assert [u.kind for u in us] == ["cjk", "latin", "cjk", "num", "cjk"] and us[1].span == (2, 10)

def test_split_units_fullwidth_digits_and_percent():
    assert [(u.text, u.percent) for u in split_units("１５０和50%")] == [("150", False), ("和", False), ("50", True)]

def test_homophones_share_key():
    assert char_key("的") & char_key("地") and char_key("体") & char_key("體")
    assert {"xing", "hang"} <= char_key("行")

@pytest.mark.parametrize("digits,expected", [
    ("150", {"一百五十", "一百五", "一五零", "一五〇"}),
    ("2", {"二", "兩"}),
    ("2026", {"兩千零二十六", "二千零二十六", "二零二六"}),
    ("10", {"十", "一十"}),
    ("3.5", {"三點五"}),
])
def test_number_readings(digits, expected):
    assert expected <= set(number_readings(digits))

def test_number_readings_percent():
    assert "百分之五十" in number_readings("50", percent=True)

def test_num_unit_alts_include_digits():
    alts = unit_alts(split_units("150")[0])
    assert (frozenset({"#150"}),) in alts and any(len(a) == 3 for a in alts)

def test_to_taiwan():
    assert to_taiwan("这个软件") == "這個軟體"

# tests/test_script.py
def test_sentences_and_ids():
    ss, _ = parse_script("今天，我們來聊 Reels！\n第二句。第三句？")
    assert [s.id for s in ss] == ["s01", "s02", "s03"] and ss[0].text == "今天，我們來聊 Reels！"

def test_markers_attach_to_next_sentence():
    ss, ms = parse_script("[B-roll: 咖啡店]今天開店。\n（這裡放咖啡店外觀）\n第二句。")
    assert ss[0].text == "今天開店。" and [(m.before, m.text) for m in ms] == [
        ("s01", "[B-roll: 咖啡店]"), ("s02", "（這裡放咖啡店外觀）")]

def test_heading_marks_removed():
    ss, _ = parse_script("# 標題\n內容。")
    assert [s.text for s in ss] == ["標題", "內容。"]

def test_unit_spans_point_into_sentence_text():
    s = parse_script("我們用 Remotion 做動畫。")[0][0]
    assert [s.text[a:b] for a, b in (u.span for u in s.units)] == ["我", "們", "用", "Remotion", "做", "動", "畫"]
```

- [ ] **Step 2：執行測試確認失敗**

Run: `uv run pytest tests/test_textnorm.py tests/test_script.py -v`
Expected: FAIL（`ImportError`）

- [ ] **Step 3：實作** `pipeline/textnorm.py` 和 `pipeline/script.py`。

- [ ] **Step 4：執行測試確認通過**

Run: `uv run pytest -v`
Expected: 全部 PASS

- [ ] **Step 5：Commit**

```bash
git add pipeline tests
git commit -m "feat: comparison units, toneless pinyin keys, number readings, script parsing"
```

---

### Task 8：語音辨識和 Meijia 測試素材

**Files:**
- Modify: `tests/helpers.py`（加 `write_raw`）、`tests/media_fixtures.py`（加 `to_16k_mono`）
- Create: `pipeline/asr.py`、`tests/test_asr.py`、`tests/fixtures/meijia/script.md`、`tests/fixtures/meijia/spoken.txt`、`tests/fixtures/meijia/make_fixture.sh`、`tests/fixtures/meijia/fixture.wav`、`tests/fixtures/meijia/asr-qwen3-1.7b.json`、`tests/fixtures/meijia/README.md`

**Interfaces:**
- Consumes：Task 7 的 `split_units`、`unit_key`、`Key`。
- Produces（`pipeline.asr`）：
  - `MODELS = {"1.7b": "Qwen/Qwen3-ASR-1.7B", "0.6b": "Qwen/Qwen3-ASR-0.6B"}`
  - `@dataclass class AsrToken: text: str; kind: str; key: Key; start: float; end: float`
  - `@dataclass class AsrResult: model: str; text: str; tokens: list[AsrToken]`
  - `run_asr(audio16k: Path, out_dir: Path, model_id: str) -> Path`：執行 venv 裡的 `mlx-qwen3-asr <audio> --model <id> --language Chinese --timestamps -f json -o <out_dir> --no-progress`，回傳 `<out_dir>/<audio 檔名主幹>.json`；結束碼非 0 就拋 `PipelineError`。
  - `transcribe_audio(audio16k: Path, cache_dir: Path, model_key: str = "1.7b", runner=run_asr) -> tuple[Path, str, bool]`：回傳（原始結果路徑、模型代號、是否用了備案）。快取資料夾是 `cache_dir/<model_key>-<audio sha256 前 12 碼>/`，已有結果就不重跑。1.7B 失敗時改用 0.6B 重跑一次；0.6B 本身失敗就不再重試。
  - `load_asr(raw: Path, model_id: str) -> AsrResult`：任何 `chunks[].truncated` 為真就拋 `AsrTruncatedError`；每個 `segments[]` 用 `split_units` 切成單位，多單位的段落把時間平均分給各單位。

**Meijia 測試素材**（自己寫的內容，可以進 git）：

`script.md`：

```text
今天我們來聊短影音剪輯。
第一步是把停頓剪掉，讓節奏變快。
[B-roll: 剪輯軟體畫面]
這個方法可以省下 150 分鐘。
最後記得追蹤我。
```

`spoken.txt`（交給 `say -v Meijia`，`[[slnc N]]` 是 N 毫秒的停頓）：

```text
今天我們來聊短影音剪輯。[[slnc 1200]] 第一步是把停頓 [[slnc 400]] 第一步是把停頓剪掉，讓節奏變快。[[slnc 500]] 嗯，[[slnc 200]] 這個方法可以省下一百五分鐘。[[slnc 500]] 最後最後記得追蹤我。
```

設計好的事件：s01 後停 1.2 秒（停頓）；s02 先講一半再重講（NG 段）；s03 前有「嗯」（贅字）；s03 把 150 念成「一百五」（數字念法）；s04 開頭「最後最後」（重講）。`README.md` 寫明這五件事。

- [ ] **Step 1：產生並錄好測試素材**

`make_fixture.sh`：`say -v Meijia -o fixture.aiff -f spoken.txt`，再用 ffmpeg 混入粉紅雜訊（`anoisesrc=color=pink:amplitude=0.003`，約 -50 dBFS，模擬環境音），輸出 48 kHz 單聲道 16 位元的 `fixture.wav`。接著轉 16 kHz 單聲道，用 `mlx-qwen3-asr`（1.7B）辨識，把原始 JSON 存成 `asr-qwen3-1.7b.json`；若 JSON 裡有絕對路徑，改成只留檔名。

Run: `bash tests/fixtures/meijia/make_fixture.sh && python3 -c "import json;print(json.load(open('tests/fixtures/meijia/asr-qwen3-1.7b.json'))['text'])"`
Expected: 辨識文字裡有兩次「第一步是把停頓」、有「最後最後」、有「一百五」或「150」。若沒有「嗯」，把 `spoken.txt` 的贅字依序改成「呃」「欸」重錄；三個都辨識不出來，就拿掉贅字並記 ruling（贅字改由 Task 9、10 的合成測試涵蓋）。

- [ ] **Step 2：寫失敗的測試**

```python
# tests/test_asr.py
RAW = FIXTURES / "meijia" / "asr-qwen3-1.7b.json"

def test_load_recorded_fixture():
    r = load_asr(RAW, MODELS["1.7b"])
    assert r.tokens and all(t.end >= t.start for t in r.tokens)
    assert [t.start for t in r.tokens] == sorted(t.start for t in r.tokens)

def test_multi_unit_segment_is_spread(tmp_path):
    raw = write_raw(tmp_path, [("Reels", 1.0, 1.4), ("我們", 2.0, 2.4)])     # helper in tests/helpers.py
    toks = load_asr(raw, "m").tokens
    assert [t.text for t in toks] == ["reels", "我", "們"]
    assert [(t.start, t.end) for t in toks] == pytest.approx([(1.0, 1.4), (2.0, 2.2), (2.2, 2.4)])

def test_truncated_chunk_raises(tmp_path):
    raw = write_raw(tmp_path, [("我們", 0.0, 0.4)], chunks=[{"truncated": True}])
    with pytest.raises(AsrTruncatedError):
        load_asr(raw, "m")

def test_falls_back_to_small_model_once(tmp_path):
    calls = []
    def runner(audio, out_dir, model_id):
        calls.append(model_id)
        if model_id.endswith("1.7B"): raise PipelineError("killed")
        return write_raw(out_dir, [("我", 0, 0.1)])
    audio = tmp_path / "a.wav"; audio.write_bytes(b"x")
    _, model, fallback = transcribe_audio(audio, tmp_path / "asr", runner=runner)
    assert (calls, model, fallback) == ([MODELS["1.7b"], MODELS["0.6b"]], MODELS["0.6b"], True)

def test_both_models_fail(tmp_path):
    def runner(*a): raise PipelineError("killed")
    audio = tmp_path / "a.wav"; audio.write_bytes(b"x")
    with pytest.raises(PipelineError):
        transcribe_audio(audio, tmp_path / "asr", runner=runner)

@pytest.mark.slow
def test_real_engine_matches_recording(tmp_path):
    wav16 = to_16k_mono(FIXTURES / "meijia" / "fixture.wav", tmp_path)       # helper in media_fixtures
    new = load_asr(run_asr(wav16, tmp_path, MODELS["1.7b"]), MODELS["1.7b"]).tokens
    old = load_asr(RAW, MODELS["1.7b"]).tokens
    assert [t.text for t in new] == [t.text for t in old]
    assert all(abs(a.start - b.start) <= 0.08 for a, b in zip(new, old))
```

- [ ] **Step 3：執行測試確認失敗**

Run: `uv run pytest tests/test_asr.py -v`
Expected: FAIL（`ImportError`）

- [ ] **Step 4：實作** `pipeline/asr.py`，並在 `tests/helpers.py` 加 `write_raw(dir, segments, chunks=None) -> Path`。

- [ ] **Step 5：執行測試確認通過**

Run: `uv run pytest -v && uv run pytest -m slow tests/test_asr.py -v`
Expected: 全部 PASS

- [ ] **Step 6：Commit**

```bash
git add pipeline tests
git commit -m "feat: Qwen3-ASR wrapper with 0.6B fallback and Meijia fixture"
```

---

### Task 9：對齊和 transcript.json

**Files:**
- Create: `pipeline/align.py`、`tests/test_align.py`
- Modify: `pipeline/cli.py`（註冊 `transcribe`）、`tests/helpers.py`、`tests/conftest.py`

**Interfaces:**
- Consumes：Task 7 的 `Sentence`、`Marker`、`unit_alts`、`FILLERS`、`char_key`、`to_taiwan`；Task 8 的 `AsrResult`、`transcribe_audio`、`load_asr`。
- Produces（`pipeline.align`）：
  - `@dataclass(frozen=True) class Op: kind: str; s: int | None; h0: int; h1: int`（`kind` 是 `match`、`sub`、`del`、`ins`；`s` 是腳本單位在全腳本的索引；`h0..h1` 是用到的辨識單位範圍，`del` 時 `h0 == h1`）
  - `align(script_alts: list[list[tuple[Key, ...]]], asr_keys: list[Key]) -> list[Op]`
  - `classify_run(run_keys: list[Key], prev_keys: list[Key], next_keys: list[Key], prev_sentence: list[Key], next_sentence: list[Key]) -> tuple[str, float]`
  - `build_transcript(sentences: list[Sentence], markers: list[Marker], asr: AsrResult, audio_s: float, fallback_used: bool) -> dict`（格式見「資料格式」）
- CLI：`transcribe <name> [--asr-model 1.7b|0.6b] [--asr-json PATH] [--force]`。`--asr-json` 直接用現成的原始辨識結果，不跑引擎（測試和重新對齊用）。`run_step` 的輸入是 `audio16k.wav`、腳本檔、模型代號（寫成一個小的 `work/asr-model.txt`）。輸出 `{"sentences": n, "spoken": n, "needs_listen": n, "model": …, "fallback_used": …}`。

**對齊（動態規劃）**：

- 代價：一個腳本單位對到辨識單位，鍵有交集算 0，沒有算 1；數字單位依某個念法一次吃掉該念法長度的辨識單位，代價是逐字不符的數目；辨識多出的單位（插入）1；腳本沒被念到的單位（刪除）1。
- 回溯時，同分的選擇依序偏好：吻合、插入、替換、刪除。這樣講了好幾次的句子，綁到腳本的是最後一次，前面的成為插入段。
- 每一列用 numpy 向量化（插入的連續成本用累積最小值算）。1500×1500 要在 5 秒內跑完。

**插入段分類**（`classify_run`，依序判斷，回傳種類和相似度）：

1. `filler`：整段可以完全拆成 `FILLERS` 裡的詞（逐字鍵有交集）。
2. `retake`：長度 ≥ 4，且和下一個綁定單位所在句子的開頭（同長度）相似度 ≥ 0.80；或和上一個綁定單位所在句子的開頭相似度 ≥ 0.80（講完又起頭、沒講完的那種）。相似度 = 1 − 編輯距離 ÷ 段長。
3. `repeat`：段落的鍵逐一等於緊接在後的同長度綁定單位，或緊接在前的。
4. `reworded`：長度 ≥ 4，相似度落在 0.50～0.80。
5. 其他是 `extra`（腳本外多講的話，不是粗剪候選）。

**每句的欄位**：

- `spoken`：句子裡至少一個單位是 `match` 或 `sub`。
- `ratio` = 完全吻合的腳本單位數 ÷ 腳本單位數（數字算一個單位）。
- 低於 0.90 加 `low_match`；句子範圍內有 `extra` 段加 `extra_speech`；兩者都讓 `needs_listen` 為真。
- 插入段歸屬（`runs[].sentence`）：落在某句第一個和最後一個綁定單位之間就歸那一句；落在兩句之間歸給後一句；最後一句之後的歸給最後一句。
- `ratio` 低於 0.50 且 `spoken`：`text` 改用辨識文字，取這一句的綁定單位加上歸給這一句、種類不是 `filler`、`repeat`、`retake` 的插入段，依時間排序（`to_taiwan` 轉換，英文詞之間加空白），`display_source = "asr"`，加 `asr_text`，`units` 改用這些辨識單位和它們的時間。
- 單位時間：`match`、`sub` 用辨識時間；數字取所吃掉的第一個單位的開始到最後一個的結束；`del` 和所有英文詞，在同一句前後都有計時單位時，平均分配前一個單位的結束到後一個單位的開始；只有一側有計時單位時，用對到的辨識時間；兩者都沒有就取鄰近單位的邊界、長度 0。

- [ ] **Step 1：寫失敗的測試**

```python
# tests/test_align.py（keys("今天") 把字串轉成逐字鍵；alts_of("…") 把腳本字串轉成 unit_alts 清單；兩者放 tests/helpers.py）
def test_exact_match():
    assert [o.kind for o in align(alts_of("今天好"), keys("今天好"))] == ["match"] * 3

def test_homophone_is_match():
    assert align(alts_of("我的書"), keys("我地書"))[1].kind == "match"

def test_retake_binds_last_take():
    ops = align(alts_of("甲乙丙丁好"), keys("甲乙甲乙丙丁好"))
    assert [(o.kind, o.h0, o.h1) for o in ops][:2] == [("ins", 0, 1), ("ins", 1, 2)]
    assert all(o.kind == "match" for o in ops[2:])

def test_number_spoken_differently_matches():
    ops = align(alts_of("省下150分鐘"), keys("省下一百五分鐘"))
    assert [o.kind for o in ops] == ["match"] * 5 and ops[2].h1 - ops[2].h0 == 3

def test_number_as_digits_matches():
    ops = align(alts_of("省下150分鐘"), keys("省下") + [frozenset({"#150"})] + keys("分鐘"))
    assert all(o.kind == "match" for o in ops)

def test_align_1500_under_5s():
    text = "我們今天來聊短影音剪輯的流程" * 107
    t0 = time.perf_counter(); align(alts_of(text), keys(text)); assert time.perf_counter() - t0 < 5

@pytest.mark.parametrize("run,prev,nxt,psent,nsent,kind", [
    ("嗯", "剪輯", "這個", "短影音剪輯", "這個方法", "filler"),
    ("那個就是", "剪輯", "這個", "短影音剪輯", "這個方法", "filler"),
    ("第一步是把", "剪輯", "第一步是把停頓", "短影音剪輯", "第一步是把停頓剪掉", "retake"),
    ("我們", "", "我們來聊", "", "我們來聊", "repeat"),
    ("第一招是先", "剪輯", "第一步是把", "短影音剪輯", "第一步是把停頓剪掉", "reworded"),
    ("順便說一下", "剪輯", "這個", "短影音剪輯", "這個方法", "extra"),
])
def test_classify_run(run, prev, nxt, psent, nsent, kind):
    assert classify_run(keys(run), keys(prev), keys(nxt), keys(psent), keys(nsent))[0] == kind

def test_ratio_and_low_match():
    t = transcript_of("一二三四五六七八九十。", asr="一二三四五六七八九拾")    # helper：parse_script + 假 AsrResult
    assert t["sentences"][0]["ratio"] == 1.0                                 # 拾與十同音
    t = transcript_of("一二三四五六七八九十。", asr="一二三四五六七八天地")
    s = t["sentences"][0]; assert s["ratio"] == 0.8 and "low_match" in s["reasons"] and s["needs_listen"]

def test_extra_speech_flags_sentence():
    s = transcript_of("今天我們來聊剪輯。", asr="今天我們順便說一下來聊剪輯")["sentences"][0]
    assert s["ratio"] == 1.0 and "extra_speech" in s["reasons"] and s["needs_listen"]

def test_skipped_sentence_not_spoken():
    t = transcript_of("第一句話。第二句話。第三句話。", asr="第一句話第三句話")
    assert [s["spoken"] for s in t["sentences"]] == [True, False, True]

def test_reworded_sentence_uses_asr_text():
    s = transcript_of("這個方法可以省下時間。", asr="这招能帮你省很多事")["sentences"][0]
    assert s["display_source"] == "asr" and s["text"] == "這招能幫你省很多事" and "asr_text" in s["reasons"]

def test_latin_word_time_is_interpolated():
    u = transcript_of("用Reels做影片。", asr="用reels做影片", times={"reels": (5.0, 5.2)})["sentences"][0]["units"]
    assert u[1]["src"] == "interp" and u[1]["start"] == u[0]["end"] and u[1]["end"] == u[2]["start"]

def test_fixture_transcript():
    t = fixture_transcript()      # helper：Meijia script.md + 錄好的 JSON
    assert all(s["spoken"] for s in t["sentences"]) and t["sentences"][2]["ratio"] == 1.0
    assert {"retake", "repeat"} <= {r["kind"] for r in t["runs"]}
```

- [ ] **Step 2：執行測試確認失敗**

Run: `uv run pytest tests/test_align.py -v`
Expected: FAIL（`ImportError`）

- [ ] **Step 3：實作** `pipeline/align.py`、`transcribe` 指令、`tests/helpers.py` 的 `keys`、`alts_of`、`transcript_of(script, asr, times=None)`（`asr` 是字串時每字 0.2 秒、從 0 秒接續；是 `(文字, 開始, 結束)` 清單時照給的時間平均分配；`times` 依單位文字覆寫時間）、`fixture_transcript`，以及 conftest 的 `transcribed_project`（把 Meijia 素材做成影片放進 inbox，跑 `prep` 和 `transcribe --asr-json` 錄好的結果）。測試用影片：`make_clip(..., seconds=fixture 長度, gray_counter=True)`，音訊換成 `fixture.wav`。

- [ ] **Step 4：執行測試確認通過**

Run: `uv run pytest -v`
Expected: 全部 PASS

- [ ] **Step 5：Commit**

```bash
git add pipeline tests
git commit -m "feat: script-to-speech alignment with homophone keys, number readings and run classes"
```

---

### Task 10：切點和粗剪候選

**Files:**
- Create: `pipeline/audio.py`、`pipeline/trim.py`、`tests/test_audio.py`、`tests/test_trim.py`
- Modify: `pipeline/cli.py`（註冊 `trim`）、`tests/helpers.py`、`tests/conftest.py`

**Interfaces:**
- Consumes：Task 9 的 `transcript.json`；Task 5 的 `work/audio48k.wav`。
- Produces：
  - `pipeline.audio`：`HOP_S = 0.01`；`@dataclass class Envelope: db: np.ndarray; hop_s: float`；`envelope(wav: Path) -> Envelope`（混成單聲道，10 毫秒窗的 RMS，以 dBFS 表示，下限 -100）；`noise_floor_db(env: Envelope) -> float`（第 10 百分位）；`snap_cut(t: float, lo: float, hi: float, env: Envelope, floor_db: float, window_s: float = 0.15, fps: int = 30) -> tuple[float, bool]`。
  - `pipeline.trim`：常數 `PAUSE_MIN_S = 0.7`、`PAUSE_KEEP_S = 0.35`、`LEAD_S = 0.3`；`build_trims(transcript: dict, env: Envelope) -> dict`（格式見「資料格式」）。
- CLI：`trim <name> [--force]` → `{"trims": n, "by_kind": {...}, "suggest_cut": n, "needs_listen": n}`。

**`snap_cut` 的演算法**：搜尋範圍是 `[t − 0.15, t + 0.15]` 和 `[lo, hi]` 的交集；交集短於 20 毫秒時，改用不受限的 `[t − 0.15, t + 0.15]`。在範圍裡找音量最低的 10 毫秒窗（一樣低時取最接近 `t` 的），取它的中心，再四捨五入到最近的 1/30 秒。對齊後那一點的音量不超過 `floor_db + 6` 才算在靜音裡。

**候選**（所有時間都經過 `snap_cut`）：

- 插入段（`filler`、`repeat`、`retake` 建議剪，`reworded` 只列出來；`extra` 不產生候選）：起點取「前一個辨識單位的結束」和「段落開始」的中點，`lo`、`hi` 就是這兩個時間；終點取「段落結束」和「下一個辨識單位開始」的中點，同理。兩個切點有一個不在靜音裡就標 `needs_listen`。
- 停頓：相鄰兩個辨識單位 a、b 間隔超過 0.7 秒，而且在第一句的第一個單位和最後一句的最後一個單位之間時，剪 `[a.end + 0.175, b.start − 0.175]`（`lo = a.end`、`hi = b.start`），留下約 0.35 秒。
- 句子邊界（給 `edit.json` 的 `extra` 和片頭片尾用）：`cut_in` 的目標點是「前一個辨識單位的結束（第一句用 0）」和「句子第一個單位開始」的中點，但不早於句首前 0.3 秒；`cut_out` 對稱處理，片尾以音訊長度為界。
- 依起點排序後編號 `t01`、`t02`…；`sentence` 填最接近的句子代號；`reason` 用英文短句（例：`earlier take of s02`、`pause 1.44s -> 0.35s`）。
- 辨識錯的腳本文字（`sub`）不在任何插入段裡，所以永遠不會被剪。

- [ ] **Step 1：寫失敗的測試**

```python
# tests/test_audio.py（tests/helpers.py 的 synth_wav(dir, parts, sr=48000, channels=1) -> Path：parts 是 ("noise"|"tone"|"silence", 秒數, dBFS) 依序接起來）
def test_noise_floor(tmp_path):
    env = envelope(synth_wav(tmp_path, [("noise", 2.0, -50), ("tone", 1.0, -20)]))
    assert abs(noise_floor_db(env) - (-50)) < 3

def test_snap_finds_quiet_dip_on_grid(tmp_path):
    env = envelope(synth_wav(tmp_path, [("tone", 1.05, -20), ("noise", 0.04, -50), ("tone", 1.0, -20)]))
    t, silent = snap_cut(1.0, 0.0, 2.0, env, -50)
    assert abs(t - 1.07) <= 1 / 30 and abs(t * 30 - round(t * 30)) < 1e-9 and silent

def test_snap_inside_speech_is_not_silent(tmp_path):
    env = envelope(synth_wav(tmp_path, [("tone", 2.0, -20)]))
    assert snap_cut(1.0, 0.0, 2.0, env, -50)[1] is False

def test_snap_respects_bounds(tmp_path):
    env = envelope(synth_wav(tmp_path, [("noise", 0.9, -60), ("tone", 1.1, -20)]))
    t, _ = snap_cut(1.0, 0.95, 1.2, env, -60)
    assert 0.95 - 1 / 60 <= t <= 1.2 + 1 / 60

# tests/test_trim.py（trims_for(text, asr, gaps=…) 在 tests/helpers.py：組 transcript，用安靜的包絡跑 build_trims）
def test_long_pause_compressed():
    tr = trims_for("第一句。第二句。", asr=[("第一句", 0.0, 0.9), ("第二句", 2.1, 3.0)])
    p = [t for t in tr["trims"] if t["kind"] == "pause"]; assert len(p) == 1
    kept = 1.2 - (p[0]["end"] - p[0]["start"])
    assert abs(kept - 0.35) <= 0.1 and p[0]["suggest"] == "cut"

def test_short_pause_kept():
    tr = trims_for("第一句。第二句。", asr=[("第一句", 0.0, 0.9), ("第二句", 1.5, 2.4)])
    assert not [t for t in tr["trims"] if t["kind"] == "pause"]

@pytest.mark.parametrize("asr,kind,suggest", [
    ("嗯今天我們來聊剪輯", "filler", "cut"),
    ("今天我們今天我們來聊剪輯", "retake", "cut"),
    ("今天今天我們來聊剪輯", "repeat", "cut"),
    ("今天咱們來講今天我們來聊剪輯", "retake", "list"),
])
def test_run_trims(asr, kind, suggest):
    t = trims_for("今天我們來聊剪輯。", asr=asr)["trims"][0]
    assert (t["kind"], t["suggest"]) == (kind, suggest)

def test_extra_speech_is_not_a_trim():
    assert trims_for("今天我們來聊剪輯。", asr="今天我們順便說一下來聊剪輯")["trims"] == []

def test_misrecognised_script_text_never_cut():
    assert trims_for("今天我們來聊剪輯。", asr="今天我們來談剪輯")["trims"] == []      # 談≠聊，是替換

def test_ids_sorted_and_all_times_on_grid():
    tr = trims_for("第一句。第二句。", asr=[("嗯第一句", 0.0, 1.2), ("第二句", 2.4, 3.3)])
    assert [t["id"] for t in tr["trims"]] == [f"t{i:02d}" for i in range(1, len(tr["trims"]) + 1)]
    pts = [x for t in tr["trims"] for x in (t["start"], t["end"])] + [x for b in tr["bounds"].values() for x in (b["cut_in"], b["cut_out"])]
    assert all(abs(x * 30 - round(x * 30)) < 1e-9 for x in pts)

def test_fixture_trims(trimmed_project):
    tr = json.loads((trimmed_project.work / "trim.json").read_text())
    assert {"pause", "retake", "repeat"} <= {t["kind"] for t in tr["trims"]}
    assert all(t["suggest"] == "cut" for t in tr["trims"])
```

- [ ] **Step 2：執行測試確認失敗**

Run: `uv run pytest tests/test_audio.py tests/test_trim.py -v`
Expected: FAIL（`ImportError`）

- [ ] **Step 3：實作** `pipeline/audio.py`、`pipeline/trim.py`、`trim` 指令和測試 helper。`run_step` 的輸入是 `transcript.json`、`audio48k.wav`。

- [ ] **Step 4：執行測試確認通過**

Run: `uv run pytest -v`
Expected: 全部 PASS

- [ ] **Step 5：Commit**

```bash
git add pipeline tests
git commit -m "feat: quiet-point cut snapping and rough-cut candidates"
```

---

### Task 11：edit.json 檢查、片段、時間換算

**Files:**
- Create: `pipeline/edit.py`、`tests/test_edit.py`
- Modify: `pipeline/cli.py`（註冊 `validate`）、`tests/helpers.py`、`tests/conftest.py`

**Interfaces:**
- Consumes：Task 9 的 `transcript.json`；Task 10 的 `trim.json`；Task 4 的 `load_style`。
- Produces（`pipeline.edit`）：
  - `SEM_TAGS = ("hook", "claim", "number", "list", "compare", "steps", "example", "quote", "define", "warning", "cta", "aside")`
  - `FUTURE_KEYS = {"scenes": 2, "broll": 2, "punch_in": 2, "sfx": 3, "music": 3}`
  - `load_edit(path: Path) -> dict`（JSON 解析失敗時拋 `EditInvalid`）
  - `validate_edit(edit: dict, transcript: dict, trims: dict) -> list[str]`：空清單代表通過；每條錯誤寫成 `<JSON 路徑>: <問題>`。
  - `kept_sentences(edit: dict, transcript: dict) -> list[str]`：`spoken` 為真、而且不在任何 `extra` 範圍裡的句子，依原順序。
  - `@dataclass(frozen=True) class Segment: start: float; end: float`
  - `build_segments(edit: dict, transcript: dict, trims: dict) -> list[Segment]`
  - `map_time(t: float, segments: list[Segment]) -> float`
  - `require_valid(p: Project) -> tuple[dict, dict, dict]`：讀三個檔案、檢查，失敗時拋 `EditInvalid`；回傳（edit、transcript、trims）。
  - `tests/helpers.py`：`default_edit(transcript, trims) -> dict`（`suggest` 為 `cut` 的接受，其餘拒絕；每個保留句 `sem: "claim"`、`weight: "main"`；`captions.mode: "line"`）。
- CLI：`validate <name>` → `{"segments": n, "duration_s": x, "kept_sentences": n}`；不通過時結束碼 2，`errors` 列出全部錯誤。

**檢查規則**：

- 頂層只能有 `version`、`style`、`cuts`、`sentences`、`captions`；`FUTURE_KEYS` 的鍵報錯時寫明是第幾期（`scenes: not supported until phase 2`）；其他未知鍵（含巢狀物件裡的）一律報錯。
- `version == 1`；`styles/<style>/style.json` 要存在。
- 規則 1：`cuts.accept`、`cuts.reject` 都是字串陣列，不重複、不交集，聯集正好等於 `trim.json` 的所有代號；`extra` 每項有 `from`、`to`、`reason`，兩個代號都存在而且是有講到的句子，`from` 不在 `to` 之後，範圍彼此不重疊；`sentences` 每項有 `id`、`sem`、`weight`，`id` 存在且不重複，集合正好等於 `kept_sentences`；`weight` 是 `main` 或 `sub`；`captions.mode` 是 `line` 或 `word`。
- 規則 7：`sem` 在 `SEM_TAGS` 裡。
- 剪完不能沒有任何片段。

**片段**：時間軸從第一個保留句的 `cut_in` 到最後一個保留句的 `cut_out`；扣掉被接受的候選和 `extra` 範圍（從起始句的 `cut_in` 到結束句的 `cut_out`）的聯集；丟掉短於 2 格的片段。`map_time`：落在片段裡的時間照比例換算；落在被剪掉的範圍裡，回傳下一段的開頭；早於時間軸回傳 0，晚於回傳總長。

- [ ] **Step 1：寫失敗的測試**

```python
# tests/test_edit.py（T、TR 是 fixture：Meijia 錄好的 transcript 和 trims）
def test_default_edit_is_valid(T, TR):
    assert validate_edit(default_edit(T, TR), T, TR) == []

def test_unknown_and_future_keys(T, TR):
    e = default_edit(T, TR) | {"foo": 1, "scenes": []}
    errs = validate_edit(e, T, TR)
    assert any(x.startswith("foo:") for x in errs) and any("scenes" in x and "phase 2" in x for x in errs)

def test_version_and_style(T, TR):
    errs = validate_edit(default_edit(T, TR) | {"version": 2, "style": "nope"}, T, TR)
    assert any("version" in x for x in errs) and any("style" in x for x in errs)

def test_every_trim_decided_once(T, TR):
    e = default_edit(T, TR); missing = e["cuts"]["accept"].pop()
    assert any(missing in x for x in validate_edit(e, T, TR))
    e = default_edit(T, TR); e["cuts"]["reject"].append(e["cuts"]["accept"][0])
    assert validate_edit(e, T, TR)
    e = default_edit(T, TR); e["cuts"]["accept"].append("t99")
    assert any("t99" in x for x in validate_edit(e, T, TR))

def test_extra_validation(T, TR):
    for extra in ([{"from": "s09", "to": "s09", "reason": "x"}],
                  [{"from": "s03", "to": "s02", "reason": "x"}],
                  [{"from": "s02", "to": "s03", "reason": "x"}, {"from": "s03", "to": "s03", "reason": "y"}]):
        e = default_edit(T, TR); e["cuts"]["extra"] = extra
        assert validate_edit(e, T, TR), extra

def test_sentences_must_match_kept(T, TR):
    e = default_edit(T, TR); e["sentences"].pop(1)
    assert any("s02" in x for x in validate_edit(e, T, TR))
    e = default_edit(T, TR); e["cuts"]["extra"] = [{"from": "s02", "to": "s02", "reason": "x"}]
    assert any("s02" in x for x in validate_edit(e, T, TR))         # 被 extra 移除的句子不該再列

def test_sem_vocabulary_weight_and_mode(T, TR):
    e = default_edit(T, TR); e["sentences"][0]["sem"] = "funny"; e["sentences"][1]["weight"] = "big"
    e["captions"]["mode"] = "karaoke"
    errs = validate_edit(e, T, TR)
    assert any("funny" in x for x in errs) and any("big" in x for x in errs) and any("karaoke" in x for x in errs)

def test_unspoken_sentence_not_required():
    T2, TR2 = transcript_and_trims("第一句話。第二句話。第三句話。", asr="第一句話第三句話")
    assert [s["id"] for s in default_edit(T2, TR2)["sentences"]] == ["s01", "s03"]
    assert validate_edit(default_edit(T2, TR2), T2, TR2) == []

def test_removing_everything_rejected(T, TR):
    e = default_edit(T, TR); e["cuts"]["extra"] = [{"from": "s01", "to": "s04", "reason": "x"}]; e["sentences"] = []
    assert any("nothing" in x for x in validate_edit(e, T, TR))

def test_segments_skip_accepted_and_keep_rejected(T, TR):
    e = default_edit(T, TR); segs = build_segments(e, T, TR)
    for t in TR["trims"]:
        mid = (t["start"] + t["end"]) / 2
        assert not any(s.start <= mid < s.end for s in segs)
    e["cuts"]["reject"] += e["cuts"]["accept"]; e["cuts"]["accept"] = []
    assert len(build_segments(e, T, TR)) == 1

def test_extra_removes_sentence(T, TR):
    e = default_edit(T, TR); e["cuts"]["extra"] = [{"from": "s03", "to": "s03", "reason": "x"}]
    e["sentences"] = [s for s in e["sentences"] if s["id"] != "s03"]
    s3 = next(s for s in T["sentences"] if s["id"] == "s03")
    assert not any(s.start <= (s3["start"] + s3["end"]) / 2 < s.end for s in build_segments(e, T, TR))

def test_segments_on_grid_and_map_time(T, TR):
    segs = build_segments(default_edit(T, TR), T, TR)
    assert all(abs(x * 30 - round(x * 30)) < 1e-9 for s in segs for x in (s.start, s.end))
    assert map_time(segs[0].start, segs) == 0
    half = (segs[1].end - segs[1].start) / 2
    assert map_time(segs[1].start + half, segs) == pytest.approx(segs[0].end - segs[0].start + half)
    assert map_time((segs[0].end + segs[1].start) / 2, segs) == pytest.approx(segs[0].end - segs[0].start)
    ts = [x / 10 for x in range(0, 200)]
    assert [map_time(t, segs) for t in ts] == sorted(map_time(t, segs) for t in ts)
```

- [ ] **Step 2：執行測試確認失敗**

Run: `uv run pytest tests/test_edit.py -v`
Expected: FAIL（`ImportError`）

- [ ] **Step 3：實作** `pipeline/edit.py`、`validate` 指令、`default_edit`、`transcript_and_trims`（helper：`transcript_of` 加上安靜包絡的 `build_trims`），以及 conftest 的 `T`、`TR` fixture（Meijia 腳本加錄好的辨識結果做出的 transcript，和用 `fixture.wav` 的包絡算出的 trims）。

- [ ] **Step 4：執行測試確認通過**

Run: `uv run pytest -v`
Expected: 全部 PASS

- [ ] **Step 5：Commit**

```bash
git add pipeline tests
git commit -m "feat: edit.json validation (phase 1 keys, rules 1 and 7), segments and time mapping"
```

---

### Task 12：字幕

**Files:**
- Create: `pipeline/captions.py`、`tests/test_captions.py`
- Modify: `tests/helpers.py`（加 `line_of`、`event`、`box`）

**Interfaces:**
- Consumes：Task 11 的 `Segment`、`map_time`、`kept_sentences`；Task 4 的 `load_style`、`font_path`；Task 6 的 `faces.json`。
- Produces（`pipeline.captions`）：
  - `@dataclass class Piece: text: str; unit: int | None`（`unit` 是句子 `units` 的索引，標點和空白為 `None`）
  - `@dataclass class Line: pieces: list[Piece]`，屬性 `text`
  - `break_lines(text: str, units: list[dict], font: Path, style: dict) -> list[Line]`
  - `@dataclass class Event: start: float; end: float; line: Line; times: list[float | None]`（每個 piece 在成品上的開始時間）
  - `build_events(transcript: dict, edit: dict, segments: list[Segment], font: Path, style: dict) -> list[Event]`
  - `caption_top_px(boxes: list[dict], src_w: int, src_h: int, style: dict) -> tuple[int, str | None]`
  - `ass_color(hex_rgb: str) -> str`
  - `ass_time(t: float) -> str`
  - `write_ass(events: list[Event], style: dict, top_px: int, mode: str, path: Path) -> None`
  - `missing_glyphs(text: str, font: Path) -> set[str]`

**斷行**：

- 顯示文字的整理：行尾的 `。，、；：` 拿掉；行內的 `，、；：` 換成全形空白「　」；保留 `？！「」『』《》`。
- 每行同時要符合：字數 ≤ 16（全形字算 1、半形字算 0.5，無條件進位），以及用字型實際字寬算出的寬度 ≤ 安全區寬度（950）減兩倍描邊。
- 先在 `。！？；，、：` 後面切成片語，再由前往後把片語塞進一行；單一片語太長時，在單位邊界上切成長度平均的幾段。英文詞和數字不能切開。
- 本期每個字幕事件只有 1 行。

**事件時間**：起點是該行第一個有時間的單位經 `map_time` 換算後的時間；終點取「最後一個單位的結束換算後加 `hold_s`（0.5）」和下一個事件的起點兩者較早的；不超過成品總長。只處理 `kept_sentences`。

**位置**：把臉部框依合成時的縮放（等比放大到蓋滿 1080×1920，再置中裁切）換到成品座標；取各取樣格臉部下緣的第 95 百分位，`top = max(default_top_px, 下緣 + face_margin_px)`。`top + line_height_px` 超出安全區下緣（1248）時，`top = 1248 − line_height_px`，警告 `captions overlap face`。沒有任何臉部框時用 `default_top_px`，警告 `no face detected`。

**ASS**：`PlayResX: 1080`、`PlayResY: 1920`、`WrapStyle: 2`、`ScaledBorderAndShadow: yes`。Style 用 `fonts.family`、`size_px`、`bold`、描邊；`line` 模式 PrimaryColour 是 `color`；`word` 模式 PrimaryColour 是 `highlight`、SecondaryColour 是 `color`。每個 Dialogue 以 `{\an8\pos(540,<top>)}` 開頭；`word` 模式在每個有單位的 piece 前加 `{\kNN}`，NN 是到下一個 piece 開始的厘秒數（最後一個算到事件結束）。文字裡的 `{`、`}`、`\` 換成全形的 `｛`、`｝`、`＼`。

- [ ] **Step 1：寫失敗的測試**

```python
# tests/test_captions.py（FONT = 粗體靜態字型；line_of(text) 用 parse_script 的單位呼叫 break_lines）
def test_trailing_punct_removed_one_line():
    assert [l.text for l in line_of("今天我們來聊短影音剪輯。")] == ["今天我們來聊短影音剪輯"]

def test_long_sentence_breaks_at_comma():
    assert [l.text for l in line_of("第一步是把多餘的停頓剪掉，這樣影片節奏才會變快很多。")] == [
        "第一步是把多餘的停頓剪掉", "這樣影片節奏才會變快很多"]

def test_short_phrases_packed_with_ideographic_space():
    assert [l.text for l in line_of("好，我們開始。")] == ["好　我們開始"]

def test_balanced_split_without_punct():
    assert [len(l.text) for l in line_of("一二三四五六七八九十一二三四五六七八九十")] == [10, 10]

def test_never_split_latin_or_number():
    lines = [l.text for l in line_of("我們今天要用 Remotion 和 HyperFrames 做出 2026 年的動畫")]
    for word in ("Remotion", "HyperFrames", "2026"):
        assert sum(word in l for l in lines) == 1

def test_line_limits_by_count_and_pixels():
    style = load_style()
    for l in line_of("這是一個非常非常長而且完全沒有任何標點符號的句子用來測試斷行有沒有超過限制"):
        c = style["captions"]
        assert width_units(l.text) <= 16 and text_px(l.text, FONT, c["size_px"]) <= 950 - 2 * c["outline_px"]

def test_events_follow_map_time_and_hold(T, TR):
    e = default_edit(T, TR); segs = build_segments(e, T, TR)
    ev = build_events(T, e, segs, FONT, load_style())
    first_unit = next(u for u in T["sentences"][0]["units"] if u["start"] is not None)
    assert ev[0].start == pytest.approx(map_time(first_unit["start"], segs))
    assert all(a.end <= b.start for a, b in zip(ev, ev[1:]))

def test_events_skip_extra_and_unspoken(T, TR):
    e = default_edit(T, TR); e["cuts"]["extra"] = [{"from": "s02", "to": "s02", "reason": "x"}]
    e["sentences"] = [s for s in e["sentences"] if s["id"] != "s02"]
    texts = "".join(x.line.text for x in build_events(T, e, build_segments(e, T, TR), FONT, load_style()))
    assert "第一步" not in texts

def test_ass_header_and_line_mode(tmp_path):
    p = tmp_path / "c.ass"; write_ass([event("今天開始", 1.0, 2.0)], load_style(), 1040, "line", p)
    s = p.read_text(encoding="utf-8")
    assert "WrapStyle: 2" in s and "PlayResX: 1080" in s and "PlayResY: 1920" in s
    assert "{\\an8\\pos(540,1040)}今天開始" in s and "0:00:01.00,0:00:02.00" in s

def test_ass_word_mode_k_tags(tmp_path):
    p = tmp_path / "c.ass"; write_ass([event("今天開始", 1.0, 2.0)], load_style(), 1040, "word", p)
    ks = [int(k) for k in re.findall(r"\\k(\d+)", p.read_text(encoding="utf-8"))]
    assert len(ks) == 4 and abs(sum(ks) - 100) <= 2

def test_ass_escapes_override_chars(tmp_path):
    p = tmp_path / "c.ass"; write_ass([event("a{b}c\\d", 0, 1)], load_style(), 1040, "line", p)
    line = [l for l in p.read_text(encoding="utf-8").splitlines() if l.startswith("Dialogue")][0]
    assert line.endswith("a｛b｝c＼d") and "{b" not in line

def test_ass_color_and_time():
    assert ass_color("#FFD400") == "&H0000D4FF" and ass_time(3723.456) == "1:02:03.46"

def test_missing_glyphs():
    assert missing_glyphs("臺灣體　？", FONT) == set() and missing_glyphs("\ue000", FONT) == {"\ue000"}

def test_caption_top_no_face():
    assert caption_top_px([], 1080, 1920, load_style()) == (1040, "no face detected")

def test_caption_top_below_face():
    assert caption_top_px([box(y1=0.55)], 1080, 1920, load_style()) == (1096, None)   # 0.55*1920=1056, +40

def test_caption_top_face_too_low_warns():
    assert caption_top_px([box(y1=0.70)], 1080, 1920, load_style()) == (1158, "captions overlap face")

def test_caption_top_landscape_cover_crop():
    assert caption_top_px([box(y1=0.55)], 1920, 1080, load_style())[0] == 1096        # 縱向不裁切
```

- [ ] **Step 2：執行測試確認失敗**

Run: `uv run pytest tests/test_captions.py -v`
Expected: FAIL（`ImportError`）

- [ ] **Step 3：實作** `pipeline/captions.py`（`width_units`、`text_px` 是模組內的公開 helper，字寬用 fontTools 的 `hmtx` 和 `cmap`）。

- [ ] **Step 4：執行測試確認通過**

Run: `uv run pytest -v`
Expected: 全部 PASS

- [ ] **Step 5：Commit**

```bash
git add pipeline tests
git commit -m "feat: caption line breaking, face-aware placement and ASS output"
```

---

### Task 13：關卡 1 分鏡稿

**Files:**
- Create: `pipeline/storyboard.py`、`tests/test_storyboard.py`
- Modify: `pipeline/cli.py`（註冊 `storyboard`）、`tests/helpers.py`、`tests/conftest.py`

**Interfaces:**
- Consumes：Task 11 的 `require_valid`、`build_segments`、`map_time`；Task 12 的 `build_events`、`caption_top_px`；Task 2 的 `safe_rmtree`；Task 3 的 `run_step`。
- Produces：`build_storyboard(p: Project) -> dict`，寫出 `review/gate1/index.html` 和 `review/gate1/storyboard.md`；重跑時先用 `safe_rmtree(p.review / "gate1", allowed_root=p.dir)` 清掉舊的。回傳 `{"html": …, "md": …, "duration_s": …, "source_s": …, "cuts": n, "needs_listen": n, "warnings": [...]}`。
- CLI：`storyboard <name> [--force]`；`edit.json` 不通過時結束碼 2。`run_step` 的輸入是 `edit.json`、`transcript.json`、`trim.json`、`style.json`、`faces.json`、`audio48k.wav`；`approve` 會比對這一步記下的 `edit.json` 雜湊。

**網頁內容**（淺色、單一檔案，所有圖片和聲音以 data URI 內嵌，手機上也能直接開）：

- `<html lang="zh-Hant">`，`<meta name="color-scheme" content="light">`，不引用任何外部網址。
- 摘要：原長、新長、剪掉幾段、幾句要請聽；字幕位置的警告（`no face detected` 顯示「偵測不到臉，字幕用預設位置」，`captions overlap face` 顯示「臉太低，字幕可能蓋到下巴」）。
- 依時間順序，每個腳本句一列 `<tr data-sentence="sNN">`：縮圖（句子中點的畫面，寬 270 的 JPEG）、新時間（`m:ss.s`）、字幕各行以「 / 」連接、語意標籤和輕重；需要請聽時顯示「請聽」、原因（`low_match` 吻合度低、`extra_speech` 有腳本外的話、`asr_text` 用辨識文字）和試聽（句子前後各 0.3 秒）。被 `extra` 移除的句子加 `class="removed"`，文字包在 `<del>`；沒講到的句子標「沒講到」。
- 每個候選一列 `<tr data-trim="tNN" class="cut|kept">`：種類（贅字、重講、NG 段、停頓；`suggest` 為 `list` 的顯示「改詞重講」）、`reason`、辨識文字（接受的包在 `<del>`）、切點不在靜音裡時標「請聽」，以及試聽（候選前後各 1 秒，AAC 64 kbps 單聲道 m4a）。
- 所有來自腳本或辨識結果的文字都經過 `html.escape`。

`storyboard.md`：同樣的內容寫成 Markdown 表格（欄位：列、新時間、字幕、語意、標記），候選另列一個表，給導演在對話裡引用。

- [ ] **Step 1：寫失敗的測試**

```python
# tests/test_storyboard.py（edited_project fixture：trimmed_project 加上 default_edit 寫成的 edit.json）
def test_html_is_light_and_self_contained(edited_project):
    html = (Path(build_storyboard(edited_project)["html"])).read_text(encoding="utf-8")
    assert '<html lang="zh-Hant">' in html and 'content="light"' in html
    assert not re.search(r'(src|href)="https?://', html)
    assert "data:image/jpeg;base64," in html and "data:audio/mp4;base64," in html

def test_rows_for_sentences_and_trims(edited_project):
    html = Path(build_storyboard(edited_project)["html"]).read_text(encoding="utf-8")
    T = json.loads((edited_project.work / "transcript.json").read_text())
    TR = json.loads((edited_project.work / "trim.json").read_text())
    assert all(f'data-sentence="{s["id"]}"' in html for s in T["sentences"])
    assert all(f'data-trim="{t["id"]}"' in html for t in TR["trims"])
    assert html.count('class="cut"') == len([t for t in TR["trims"] if t["suggest"] == "cut"])

def test_html_escapes_script_text(tmp_path):
    p = edited_project_from_script(tmp_path, "我們用 <b> & 「引號」來測試。")    # helper：一句話的小專案
    html = Path(build_storyboard(p)["html"]).read_text(encoding="utf-8")
    assert "&lt;b&gt; &amp;" in html and "<b> &" not in html

def test_storyboard_shows_face_warning(edited_project):
    html = Path(build_storyboard(edited_project)["html"]).read_text(encoding="utf-8")
    assert "偵測不到臉" in html                         # 測試影片沒有臉

def test_invalid_edit_exit_2(edited_project, capsys):
    e = json.loads(edited_project.edit_path.read_text()); e["sentences"] = []
    edited_project.edit_path.write_text(json.dumps(e))
    assert main(["storyboard", edited_project.name]) == 2

def test_rerun_replaces_folder(edited_project):
    build_storyboard(edited_project); (edited_project.review / "gate1" / "stale.txt").write_text("x")
    build_storyboard(edited_project)
    assert not (edited_project.review / "gate1" / "stale.txt").exists()

def test_md_lists_ids(edited_project):
    md = Path(build_storyboard(edited_project)["md"]).read_text(encoding="utf-8")
    assert "s01" in md and "t01" in md
```

- [ ] **Step 2：執行測試確認失敗**

Run: `uv run pytest tests/test_storyboard.py -v`
Expected: FAIL（`ImportError`）

- [ ] **Step 3：實作** `pipeline/storyboard.py` 和 `storyboard` 指令；`edited_project`、`edited_project_from_script` 加進 conftest。

- [ ] **Step 4：執行測試確認通過**

Run: `uv run pytest -v`
Expected: 全部 PASS

- [ ] **Step 5：用瀏覽器看一次**

Run: `uv run pytest tests/test_storyboard.py::test_rows_for_sentences_and_trims -q --basetemp="$TMPDIR/sb" && open "$(find "$TMPDIR/sb" -name index.html | head -1)"`
Expected: 淺色頁面，縮圖、試聽可播放、刪除線正確。截圖存進 workspace，在 ledger 記一行觀察結果。

- [ ] **Step 6：Commit**

```bash
git add pipeline tests
git commit -m "feat: gate 1 storyboard page and storyboard.md"
```

---

### Task 14：合成輸出

**Files:**
- Create: `pipeline/render.py`、`tests/test_render.py`
- Modify: `pipeline/cli.py`（註冊 `render`）、`tests/helpers.py`、`tests/conftest.py`

**Interfaces:**
- Consumes：Task 3 的 `gate_ok`；Task 11 的 `require_valid`、`build_segments`；Task 12 的 `build_events`、`caption_top_px`、`write_ass`、`missing_glyphs`；Task 4 的 `run_ffmpeg`、`probe`。
- Produces（`pipeline.render`）：
  - `cut_audio(src_wav: Path, segments: list[Segment], out_wav: Path, fade_s: float = 0.01) -> float`：依片段逐取樣串接（48 kHz 時每格正好 1600 個取樣），每個片段頭尾做 `fade_s` 線性淡入淡出；回傳長度（秒）。
  - `select_expr(segments: list[Segment]) -> str`：`gte(t,0.333333)*lt(t,2.000000)+…`，時間寫到小數點後 6 位。
  - `build_ffmpeg_args(video_src: Path, cut_wav: Path, segments: list[Segment], out: Path, filter_script: Path) -> list[str]`：把濾鏡寫進 `filter_script`，回傳參數。濾鏡固定為 `[0:v]setpts=PTS-STARTPTS,fps=30,select='<expr>',setpts=N/30/TB,scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,subtitles=filename=captions.ass:fontsdir=fonts,format=yuv420p[v]`；參數 `-hide_banner -loglevel verbose -y -i <video> -i <cut.wav> -/filter_complex <filter_script> -map [v] -map 1:a -c:v h264_videotoolbox -b:v 12M -profile:v high -r 30 -fps_mode cfr -tag:v avc1 -c:a aac -b:a 192k -ar 48000 -ac 2 -movflags +faststart <out>`。（ffmpeg 9.0.2 若不接受 `-/filter_complex`，改用它接受的濾鏡檔寫法並記 ruling。）
  - `check_output(path: Path, expected_s: float) -> list[str]`：1080×1920；`r_frame_rate` 和 `avg_frame_rate` 都是 `30/1`；h264、yuv420p；AAC、48000 Hz、2 聲道、音訊位元率 ≥ 128000；整體位元率 < 25,000,000；檔案 < 300,000,000 位元組；影像長度和 `expected_s` 差不超過 1/30 + 0.02 秒；影音長度差不超過 0.05 秒。
  - `check_fontselect(log_text: str) -> list[str]`：至少一行 `fontselect:`，而且每行 `->` 後面都以 `NotoSansTC-` 開頭、不含 `/`（帶路徑代表 libass 改用了系統字型）。
  - `render(p: Project, force: bool = False, runner=run_ffmpeg) -> dict`：依序 → `gate_ok(p, "gate1")`，否則報錯 `gate1 not approved for current edit.json` → `require_valid` → 片段、字幕事件 → 缺字檢查（有缺字就報錯並列出字元）→ 在 `work/render/` 寫 `captions.ass`、複製兩個字型到 `work/render/fonts/`、寫 `cut.wav` → 以 `cwd=work/render` 跑 ffmpeg 輸出到 `out/final.tmp.mp4`，失敗時重試一次，log 在 `work/render/render.log` → `check_output` 和 `check_fontselect`，有問題就報錯 → 改名成 `out/final.mp4`。回傳 `{"out": …, "duration_s": …, "size_mb": …, "attempts": n}`。
- CLI：`render <name> [--force]`。

- [ ] **Step 1：寫失敗的測試**

```python
# tests/test_render.py
def test_cut_audio_length_and_fades(tmp_path):
    src = synth_wav(tmp_path, [("tone", 4.0, -6)], sr=48000, channels=2)
    out = tmp_path / "cut.wav"
    assert cut_audio(src, [Segment(0, 1), Segment(2, 3)], out) == pytest.approx(2.0)
    data, sr = sf.read(out); assert len(data) == 96000
    assert abs(data[48000][0]) < 0.01 and abs(data[47999][0]) < 0.01          # 接點兩側是淡入淡出的底

def test_select_expr():
    assert select_expr([Segment(0, 1), Segment(2, 2.5)]) == "gte(t,0.000000)*lt(t,1.000000)+gte(t,2.000000)*lt(t,2.500000)"

def test_args_pin_output_spec(tmp_path):
    fs = tmp_path / "f.txt"
    args = build_ffmpeg_args(Path("/v.mov"), Path("/c.wav"), [Segment(0, 1)], tmp_path / "o.mp4", fs)
    for want in ("h264_videotoolbox", "12M", "192k", "48000", "aac", "+faststart"):
        assert want in args
    graph = fs.read_text()
    assert graph.count("scale=") == 1 and "fps=30" in graph and "fontsdir=fonts" in graph

def test_check_output_flags_wrong_spec(tmp_path):
    bad = make_clip(tmp_path, w=720, h=1280, seconds=2)              # 不是 1080×1920
    errs = check_output(bad, 2.0)
    assert any("1080x1920" in e for e in errs)

def test_check_fontselect():
    ok = "[Parsed_subtitles_0] fontselect: (Noto Sans TC, 700, 0) -> NotoSansTC-Bold, 0, NotoSansTC-Bold"
    assert check_fontselect(ok) == []
    assert check_fontselect(ok.replace("-> NotoSansTC-Bold", "-> /usr/share/fonts/x.ttf"))
    assert check_fontselect("no subtitles here")

def test_render_requires_gate1(edited_project, capsys):
    assert main(["render", edited_project.name]) == 1
    assert "gate1" in json.loads(capsys.readouterr().out)["error"]

def test_render_refuses_after_edit_changed(approved_project, capsys):
    e = json.loads(approved_project.edit_path.read_text()); e["captions"]["mode"] = "word"
    approved_project.edit_path.write_text(json.dumps(e))
    assert main(["render", approved_project.name]) == 1

def test_render_retries_once(approved_project):
    calls = []
    def flaky(args, log, cwd=None):
        calls.append(1)
        if len(calls) == 1: raise PipelineError("boom", log=str(log))
        run_ffmpeg(args, log, cwd)
    assert render(approved_project, runner=flaky)["attempts"] == 2

def test_render_fails_after_two_attempts(approved_project):
    def broken(args, log, cwd=None): raise PipelineError("boom", log=str(log))
    with pytest.raises(PipelineError) as e:
        render(approved_project, runner=broken)
    assert e.value.extra["log"].endswith("render.log")

def test_render_fixture_meets_spec(approved_project):
    r = render(approved_project)
    out = Path(r["out"]); segs = segments_of(approved_project)                  # helper：build_segments
    assert check_output(out, sum(s.end - s.start for s in segs)) == []
    assert check_fontselect((approved_project.work / "render" / "render.log").read_text()) == []

def test_render_frame_accurate(approved_project):
    out = Path(render(approved_project)["out"]); segs = segments_of(approved_project)
    for k, src_frame in sample_frame_pairs(segs):           # helper：每個片段頭、中、尾各一格（成品格號、素材格號）
        assert gray_index(out, k) == src_frame % 50          # helper：讀左上角 64×64 的平均亮度換回格號

def test_render_in_path_with_space_and_quote(tmp_path, monkeypatch):
    studio = tmp_path / "my st'udio"; (studio / "inbox").mkdir(parents=True); (studio / "projects").mkdir()
    monkeypatch.setenv("REELS_STUDIO", str(studio))
    p = approved_project_in(studio)                          # helper：同 approved_project，但建在指定 studio
    assert check_output(Path(render(p)["out"]), sum(s.end - s.start for s in segments_of(p))) == []
```

- [ ] **Step 2：執行測試確認失敗**

Run: `uv run pytest tests/test_render.py -v`
Expected: FAIL（`ImportError`）

- [ ] **Step 3：實作** `pipeline/render.py`、`render` 指令；conftest 加 `approved_project`（`edited_project` 跑過 `storyboard` 和 `approve gate1`）、`approved_project_in(studio)`；helpers 加 `segments_of`、`sample_frame_pairs`、`gray_index`。

- [ ] **Step 4：執行測試確認通過**

Run: `df -h / && uv run pytest -v`
Expected: 全部 PASS

- [ ] **Step 5：Commit**

```bash
git add pipeline tests
git commit -m "feat: frame-accurate render with burned captions, output spec and font checks"
```

---

### Task 15：CLAUDE.md、skills、THIRD_PARTY.md

**Files:**
- Create: `CLAUDE.md`、`.claude/skills/reel/SKILL.md`、`.claude/skills/storyboard/SKILL.md`、`THIRD_PARTY.md`、`scripts/third_party.py`、`tests/test_docs.py`

**Interfaces:**
- Consumes：Task 1 的 `registered_commands()`；`pyproject.toml` 的相依清單。
- Produces：`scripts/third_party.py`：`--check` 列出 `uv export --no-dev --no-hashes --format requirements-txt` 的每個套件，從已安裝套件的 metadata（`License-Expression`、`License`、classifier）讀授權；屬於允許清單（MIT、BSD、Apache-2.0、ISC、PSF、MPL-2.0、Unlicense、0BSD、Zlib、HPND、LGPL）就通過，否則必須出現在 THIRD_PARTY.md 的「間接相依」表格裡，且該列「能否商業使用」為「可」；有任何不符就以結束碼 1 列出。`--table` 印出間接相依的 Markdown 表格。

**文件內容**（使用者介面的文字一律繁體中文）：

- `CLAUDE.md`：這個 repo 是什麼；對話用繁體中文；使用者不讀也不改 JSON；`edit.json` 是唯一由 Claude 寫的檔案；`inbox/` 只讀；開工先讀 `studio/brand/preferences.md`（有的話）和專案的 `project.md`；使用者說「以後都…」時寫進 `preferences.md`；三道關卡的規則（本期只有關卡 1：同意後才能 `approve` 和 `render`）；`pipeline` 指令輸出英文 JSON，由 Claude 轉述成中文；對話快接近 context 上限時，先把決定和使用者的意見寫進 `project.md`。
- `reel` skill：description 寫明觸發語（「剪 inbox 裡的…那支」「繼續…那支」）。流程：`uv run pipeline status` 決定下一步 → `new`（slug 用英文）→ `prep` → `transcribe` → `trim` → 依 `storyboard` skill 寫 `work/edit.json` → `validate` → `storyboard` → 把分鏡稿重點用中文講給使用者，`needs_listen` 的句子和候選逐一提出 → 使用者同意後 `approve <name> gate1` → `render` → 回報 `out/final.mp4`。使用者用 Remote Control 不在電腦前時，用 SendUserFile 傳 `review/gate1/index.html`（單一檔案，手機可開）。轉錄改用 0.6B 時要告訴使用者。指令失敗時照 JSON 裡的 `error` 和 `log` 轉述，不自行刪檔重來。
- `storyboard` skill：本期 `edit.json` 的五個鍵和範例；`suggest: cut` 的候選預設接受、`list` 的預設拒絕，每個代號都要出現在 accept 或 reject 之一；`needs_listen` 的候選照預設處理，但在關卡 1 明白問使用者；`extra` 只在使用者要求或明顯離題時用，並寫 `reason`；12 個語意標籤和意思（spec 7.3 的表）、`weight` 的選法；`captions.mode` 預設 `line`，使用者要逐字高亮才用 `word`；寫完一定跑 `validate`。
- `THIRD_PARTY.md`：欄位照 spec 第 11 節（名稱、版本或來源、授權、能否商業使用、用途、查核日期）。直接相依表：pyproject 的每個套件、Qwen3-ASR-1.7B、Qwen3-ASR-0.6B、Qwen3-ForcedAligner-0.6B、YuNet（Task 4 查定的授權）、Noto Sans TC（OFL 1.1；切出的靜態字型是修改版，只自用、不散布）、ffmpeg-full（GPL-3.0 以上，當工具用）。第 0 期已查過的沿用 2026-11-11 的查核日期；新查的寫當天日期。另一個「間接相依」表格放 `--table` 的輸出，以及允許清單以外、人工確認過的套件。

- [ ] **Step 1：寫失敗的測試**

```python
# tests/test_docs.py
DOCS = [ROOT / "CLAUDE.md", ROOT / ".claude/skills/reel/SKILL.md", ROOT / ".claude/skills/storyboard/SKILL.md"]

def test_docs_mention_only_real_commands():
    real = set(registered_commands())
    for d in DOCS:
        used = set(re.findall(r"pipeline ([a-z][a-z0-9-]*)", d.read_text(encoding="utf-8")))
        assert used <= real, (d.name, used - real)

def test_reel_skill_covers_the_flow():
    text = DOCS[1].read_text(encoding="utf-8")
    for c in ("status", "new", "prep", "transcribe", "trim", "validate", "storyboard", "approve", "render"):
        assert f"pipeline {c}" in text

def test_skills_have_frontmatter():
    for d in DOCS[1:]:
        head = d.read_text(encoding="utf-8").split("---")[1]
        assert re.search(r"^name: \S+", head, re.M) and re.search(r"^description: .+", head, re.M)

def test_third_party_lists_direct_deps_and_models():
    text = (ROOT / "THIRD_PARTY.md").read_text(encoding="utf-8").lower()
    deps = [re.split(r"[=<>\[ ]", d)[0].lower() for d in tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["dependencies"]]
    for name in deps + ["qwen3-asr-1.7b", "qwen3-asr-0.6b", "qwen3-forcedaligner-0.6b", "yunet", "noto sans tc", "ffmpeg"]:
        assert name in text, name

def test_third_party_all_commercial_ok():
    rows = [l for l in (ROOT / "THIRD_PARTY.md").read_text(encoding="utf-8").splitlines() if l.startswith("|") and "---" not in l]
    assert not [r for r in rows if "不可" in r or "未知" in r]

def test_transitive_licenses_pass():
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "third_party.py"), "--check"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
```

- [ ] **Step 2：執行測試確認失敗**

Run: `uv run pytest tests/test_docs.py -v`
Expected: FAIL（檔案不存在）

- [ ] **Step 3：寫文件和 `scripts/third_party.py`**；跑 `uv run python scripts/third_party.py --table` 把結果貼進 THIRD_PARTY.md，允許清單以外的套件逐一查官方授權後補列。任何一項不能商業使用就停下來問使用者。

- [ ] **Step 4：執行測試確認通過**

Run: `uv run pytest -v`
Expected: 全部 PASS

- [ ] **Step 5：Commit**

```bash
git add CLAUDE.md .claude/skills THIRD_PARTY.md scripts tests/test_docs.py
git commit -m "docs: CLAUDE.md, reel and storyboard skills, third-party licence register"
```

---

### Task 16：整條流程的整合測試

**Files:**
- Create: `tests/test_flow.py`
- Modify: `tests/conftest.py`（加 `meijia_inbox`）、`tests/helpers.py`（加 `load_t_tr`）

**Interfaces:**
- Consumes：所有指令（只透過 `pipeline.cli.main` 呼叫，和導演實際用的方式相同）。

- [ ] **Step 1：寫測試**

```python
# tests/test_flow.py
def run(*argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf): code = main(list(argv))
    return code, json.loads(buf.getvalue())

def _flow(meijia_inbox, asr_json):                   # meijia_inbox：inbox/meijia/ 放 Meijia 影片和 script.md
    video, script = meijia_inbox
    before = {f: file_sha256(f) for f in (video, script)}
    code, r = run("new", str(video), str(script), "--slug", "meijia"); assert code == 0
    name = r["project"]
    assert run("prep", name)[0] == 0
    assert run("transcribe", name, *(["--asr-json", str(asr_json)] if asr_json else []))[0] == 0
    assert run("trim", name)[0] == 0
    assert run("status", name)[1]["next"] == "edit"
    p = load_project(name)
    p.edit_path.write_text(json.dumps(default_edit(*load_t_tr(p)), ensure_ascii=False))
    assert run("validate", name)[0] == 0
    assert run("render", name)[0] == 1                 # 還沒過關卡 1
    assert run("storyboard", name)[0] == 0
    assert run("approve", name, "gate1")[0] == 0
    code, r = run("render", name); assert code == 0 and Path(r["out"]).exists()
    assert run("status", name)[1]["next"] == "done"
    assert run("prep", name)[1]["skipped"] is True
    assert before == {f: file_sha256(f) for f in (video, script)}

def test_full_flow_on_fixture(meijia_inbox):
    _flow(meijia_inbox, RAW)

@pytest.mark.slow
def test_full_flow_with_real_asr(meijia_inbox):
    _flow(meijia_inbox, None)                           # 跑真的 1.7B
```

- [ ] **Step 2：執行測試**

Run: `uv run pytest tests/test_flow.py -v && uv run pytest -m slow tests/test_flow.py -v`
Expected: 全部 PASS。（這個 task 的行為在前面各 task 已有測試驅動，這裡第一次跑就應該通過；若失敗，照 superpowers:systematic-debugging 找出是哪個 task 的介面不合，修在那個模組並補一個會失敗的單元測試。）

- [ ] **Step 3：跑完整測試**

Run: `uv run pytest -v && uv run pytest -m slow -v`
Expected: 全部 PASS

- [ ] **Step 4：Commit**

```bash
git add tests
git commit -m "test: end-to-end flow on the Meijia fixture"
```

---

### Task 17：用真實素材驗收

這一步需要使用者先錄一支新的 SDR 口播（iPhone 關掉 HDR 錄影，原始長度約 3 分鐘，成品約 60 秒），連同腳本放進 `studio/inbox/<資料夾>/`。開始前在對話裡請使用者準備，素材到位才往下做。

**Files:**
- Create: `docs/acceptance/<日期>-phase1.md`

- [ ] **Step 1：在 scratchpad 建一個獨立的 studio**

依全域規則（會刪檔的開發中程式只在 scratchpad 裡跑），建 `<scratchpad>/phase1-accept/{home,studio/inbox,studio/projects,tmp}`，把使用者的素材和腳本**複製**進 `studio/inbox/`（原檔只讀）。之後每個指令都帶 `HOME=<…>/home REELS_STUDIO=<…>/studio TMPDIR=<…>/tmp HF_HOME=<真實 HF 快取>`，工作目錄設在 `<…>/phase1-accept`，`uv run --project <worktree> pipeline …`。

Run: `df -h /`
Expected: 可用空間高於 15 GB；否則停下來提醒使用者。

- [ ] **Step 2：依 `reel` skill 跑完整流程並計時**

每個指令用 `/usr/bin/time -l` 包起來，記下 real 秒數和 peak memory footprint。導演照 `storyboard` skill 寫 `edit.json`；分鏡稿交給使用者看（Remote Control 時用 SendUserFile），使用者同意後才 `approve` 和 `render`。

Expected: 每一步 `ok` 為 true；`out/final.mp4` 通過 `check_output`。

- [ ] **Step 3：使用者在手機上看成品**

請使用者回報：字幕有沒有錯字（spec 14 的目標是 0 錯字）、切點有沒有聽得出來的斷裂或吃字、字幕位置有沒有蓋到臉、有沒有該剪沒剪或剪錯的地方。逐條記下。

- [ ] **Step 4：補量第 0 期留下的兩個項目**

- 4K SDR 素材直接解碼、縮放、合成的時間：就是 Step 2 的 `render` 時間；以 spec 第 12 節 6 分鐘的機器時間預算判斷要不要另做工作檔，結論交給使用者決定。
- HDR 備案（先縮小再轉換）：對 `studio/inbox/spike/` 的 test001（只讀）前 30 秒跑 `prep` 的 HDR 路徑，記每分鐘素材的轉換時間和檔案大小；抽一格和 iPhone 相簿的畫面並排存成圖，請使用者判斷顏色（使用者可以略過這一項）。

- [ ] **Step 5：寫驗收紀錄**

`docs/acceptance/<日期>-phase1.md`：環境、素材規格（不引用腳本或逐字稿原文）、每一步的時間和記憶體、機器時間合計對 6 分鐘預算、`needs_listen` 的句數和候選數、使用者回報的問題和處理方式、Step 4 的兩項結果、要帶到第 2 期的待辦。沒量到的寫「未量測」，推測的標 HYPOTHESIS。

- [ ] **Step 6：清理並 Commit**

刪除 scratchpad 的驗收資料夾前先列出內容給使用者看，同意後才刪（`rm -r "${ACCEPT_DIR:?}"`，並先確認它是絕對路徑、在 scratchpad 底下）。

```bash
git add docs/acceptance
git commit -m "docs: phase 1 acceptance record"
```
