# 第 1 期審查小問題修正 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修完 `docs/backlog/2026-11-12-phase1-review-items.md` 列為「本輪修」的 81 條。

**Architecture:** 不加新功能，只修第 1 期已有的程式、測試和 skill 文字。依模組分成 15 個 task，依序做，每個 task 自己的測試、自己的審查。

**Tech Stack:** Python 3.12（uv）、pytest、ffmpeg-full 9.0.2、OpenCC、mlx-qwen3-asr。

**Spec:** `docs/superpowers/specs/2026-11-11-reels-workflow-design.md`；條目清單和編號：`docs/backlog/2026-11-12-phase1-review-items.md`。

每一條的「審查原文」是第 1 期審查時記下的英文原句，照抄，給實作者看細節；「要的行為」是這份計畫的決定。

## Global Constraints

- 分支 `fix/phase1-review-items`，從 `docs/reels-workflow-design` @ `f4d0a70` 分出，在 git worktree `.claude/worktrees/phase1-review-items` 裡做。Commit 訊息不加任何 AI 或工具署名；會開編輯器的 git 指令一律加 `--no-edit` 或 `-m`。
- 行為改動一律先寫失敗的測試，看它因為預期的原因失敗，再寫程式。只整理程式（改名、刪重複、改說明、改格式）而且現有測試已經涵蓋的，不必新增測試，但整套測試要全過。
- 只支援目前的開發機（Apple M1、16 GB）。ffmpeg 一律用 `$(brew --prefix ffmpeg-full)/bin/ffmpeg` 和同目錄的 `ffprobe`；環境變數 `REELS_FFMPEG_DIR` 可覆寫這個資料夾。
- `studio/` 不進 git。`inbox/` 只讀不寫。遞迴刪除一律經過 `pipeline.safe_delete.safe_rmtree`，從 `Project.dir` 或 projects 資料夾組出路徑，並傳 `allowed_root`。
- `edit.json` 是唯一由 Claude 寫的檔案。程式不產生也不改寫它。
- `pipeline` 指令的輸出：stdout 一行 JSON，英文。結束碼：0 成功，1 執行錯誤，2 `edit.json` 檢查不通過。
- 測試環境：`tests/conftest.py` 把 `HOME` 指到暫存目錄、`REELS_STUDIO` 指到暫存 studio、`HF_HOME` 設回真實的 Hugging Face 快取（否則會重新下載 4.7 GB 的模型）。執行 pytest 時，`TMPDIR` 指到當下 session 的 scratchpad。標 `@pytest.mark.slow` 的測試跑真的語音辨識，`uv run pytest` 預設跳過。
- 第 1 期做出的專案（`state.json` 裡沒有本輪新增的欄位）必須照樣能用：不當掉，`status` 給出合理的下一步。
- 程式碼、識別字、指令輸出、commit 訊息用英文；計畫和 skill 用繁體中文（台灣用語）。
- 出貨規則：程式、註解、測試檔名、log 字串都不寫出開發平台的名稱或只有那個平台才有的路徑、檔名。
- 磁碟：每次下載或渲染之前跑 `df -h /`，可用空間低於 15 GB 就停下來回報 BLOCKED。

## Review Focus

1. **第 1 期留下的專案**：`state.json` 的步驟紀錄沒有 `size`、`mtime_ns`，關卡紀錄有 `edit_sha256`，沒有 `edit_checked`。`status`、`approve`、`render` 要照樣運作，下一步合理 → Task 3 `test_status_reads_a_phase1_state`、Task 11 `test_phase1_project_without_edit_checked_goes_to_validate`。
2. **指令裡發生程式沒預期的錯誤**：仍然只印一行 JSON、結束碼 1，traceback 只到 stderr → Task 1 `test_unexpected_exception_is_one_json_line`。
3. **檔案內容改了但大小和修改時間沒變**：本輪接受這個風險（B22 的捷徑）；但只要修改時間變了，就一定重新算雜湊 → Task 3 `test_changed_mtime_rehashes`。
4. **授權清冊的版本和安裝的版本不同**：不在允許清單裡的套件，必須當成沒查過 → Task 14 `test_row_for_another_version_does_not_count`。
5. **trim.json 或 transcript.json 重跑後內容變了，edit.json 沒動**：不能直接用舊的決定做分鏡稿 → Task 11 `test_storyboard_refuses_edit_checked_against_older_trim`。

---

## 共用步驟

每個 task 都照這個順序做（task 裡只寫各自的內容）：

1. 寫 task 列出的測試。
2. 跑這些測試，確認因為預期的原因失敗（整潔項目沒有新測試，略過這步）。
3. 寫程式。
4. 跑這些測試，確認通過；再跑整套 `uv run pytest`，確認全過。
5. Commit（每條或每幾條一個 commit，訊息用 `fix:`、`test:`、`refactor:`、`docs:` 開頭，說明行為）。

---

### Task 1：指令列的錯誤輸出和結束碼（C1、C2、C3、B1、F1）

**Files:**
- Modify: `pipeline/cli.py`、`pipeline/errors.py`
- Test: `tests/test_cli.py`、`tests/conftest.py`

**Interfaces:**
- Produces: `PipelineError(message, /, **extra)`（`message` 只能用位置參數）。`main` 的規則：handler 的回傳值若有 `"ok": False`，輸出 `ok` 為 false、結束碼 1；其他鍵照輸出。Task 4、Task 15 依這條規則處理 `doctor`。

**C1** 審查原文：cli.main catches only PipelineError — other exceptions escape as tracebacks with empty stdout; handler results with Path objects fail in _emit (consider final except Exception + json default=str).

要的行為：`main` 接住任何 `Exception`，印一行 `{"ok": false, "error": "unexpected error: <類別名稱>: <訊息>"}`，結束碼 1，完整 traceback 寫到 stderr。`_emit` 用 `json.dumps(..., ensure_ascii=False, default=str)`，回傳值裡的 `Path` 印成字串。`KeyboardInterrupt` 不接。

- [ ] `test_unexpected_exception_is_one_json_line`：註冊一個丟 `ValueError("bad")` 的測試指令；`main([...]) == 1`；stdout 只有一行，`json.loads` 後 `ok is False`、`error == "unexpected error: ValueError: bad"`；stderr 含 `Traceback`。
- [ ] `test_path_in_result_is_printed_as_text`：handler 回傳 `{"out": Path("/x/y")}`；`main == 0`；輸出的 `out == "/x/y"`。

**C2** 審查原文：cli merges **extra/**result after ok/error so stray keys overwrite them; PipelineError("x", message=...) TypeError.

要的行為：錯誤輸出 `{"ok": false, "error": <message>, **其他 extra}`，extra 裡的 `ok`、`error` 不得蓋掉這兩個值。`PipelineError("x", message="m")` 合法，`extra == {"message": "m"}`。

- [ ] `test_error_extra_cannot_overwrite_ok_or_error`：handler 丟 `PipelineError("boom", ok=True, error="x", hint="h")`；輸出 `ok is False`、`error == "boom"`、`hint == "h"`。
- [ ] `test_pipeline_error_accepts_message_keyword_in_extra`：`PipelineError("x", message="m").extra == {"message": "m"}`。

**C3** 審查原文：`pipeline -h` raises SystemExit(0) out of main with plain-text help; subcommand list hidden.

要的行為：`pipeline -h`、`pipeline --help`、`pipeline <子指令> -h` 印純文字說明到 stdout，`main` 回傳 0，不讓 `SystemExit` 跑出 `main`。最上層的說明列出每一個註冊的子指令名稱。

- [ ] `test_help_lists_every_command_and_returns_0`：`main(["-h"]) == 0`；stdout 含 `registered_commands()` 的每個名稱。
- [ ] `test_subcommand_help_returns_0`：`main(["status", "-h"]) == 0`。

**B1** 審查原文：doctor exits 0 when ok is false (main always returns 0 for handler results); `pipeline doctor && …` proceeds on a broken machine.

要的行為：handler 回傳 `"ok": False` 時，`main` 輸出 `ok` 為 false，結束碼 1（見 Interfaces）。

- [ ] `test_doctor_failure_exits_1`：monkeypatch `pipeline.cli.doctor` 回傳 `{"ok": False, "fonts": False}`；`main(["doctor"]) == 1`；輸出 `ok is False`、`fonts is False`。回傳 `{"ok": True}` 時結束碼 0。

**F1** 審查原文：tests register into global cli._COMMANDS without cleanup; a full-command-list assertion (Task 15 docs test) would see _echo_test etc.

要的行為：每個測試結束後，`cli._COMMANDS` 回到測試開始前的內容（例如 conftest 的 autouse fixture 先複製、後還原）。

- [ ] `test_test_commands_do_not_leak`：一個測試註冊 `_leak_probe`，同模組的下一個測試確認 `"_leak_probe" not in registered_commands()`（兩個測試照檔案順序跑）。

---

### Task 2：測試環境和安全刪除的測試（F2、F3、F4、F21）

**Files:**
- Modify: `tests/conftest.py`、`tests/helpers.py`（必要時）
- Test: `tests/test_conftest.py`、`tests/test_safe_delete.py`、使用 `meijia_inbox` 的 fixture 所在檔案

**F2** 審查原文：conftest REAL_HF_HOME ignores XDG_CACHE_HOME (plan formula); harmless on this machine (HF_HOME unset, cache in ~/.cache/huggingface).

要的行為：真實 Hugging Face 快取位置的算法抽成一個純函式 `real_hf_home(environ: Mapping[str, str], home: Path) -> Path`（放 `tests/helpers.py`）：`HF_HOME` 有值用它；否則 `XDG_CACHE_HOME` 有值用 `<XDG_CACHE_HOME>/huggingface`；否則 `<home>/.cache/huggingface`。空字串當成沒設。conftest 用它，在換掉 `HOME` 之前算。

- [ ] `test_real_hf_home_prefers_hf_home`、`test_real_hf_home_uses_xdg_cache_home`、`test_real_hf_home_defaults_under_home`、`test_real_hf_home_treats_empty_as_unset`：四種輸入，各自比對完整路徑。

**F3** 審查原文：test_conftest is_relative_to(Path.home()) assertion is weak (home already faked).

要的行為：檢查假家目錄的測試，拿真實家目錄（`pwd.getpwuid(os.getuid()).pw_dir`）比對：測試中的 `HOME` 在 pytest 暫存目錄裡，而且不是真實家目錄、也不在它底下。

- [ ] 改寫既有的那個斷言，照上面比對。

**F4** 審查原文："/" rules not pinned by a test (add match="filesystem root" to the two "/" tests).

- [ ] 拒絕「/」的兩個測試加上 `match=`，比對 `pipeline/safe_delete.py` 裡那條錯誤訊息的實際文字。

**F21** 審查原文：meijia_inbox does not call yunet_model(), so a missing face model shows prep's error instead of the "run pipeline setup" hint.

要的行為：`meijia_inbox` fixture 先呼叫 `yunet_model()`，缺臉部模型時顯示「run pipeline setup」的提示，而不是 `prep` 的錯誤。

- [ ] `test_meijia_inbox_needs_face_model`：把模型位置指到空資料夾，請求 `meijia_inbox` 的流程得到含 `pipeline setup` 的訊息（照 `yunet_model()` 現有的做法：skip 或 fail，訊息要含這幾個字）。

---

### Task 3：專案狀態（C4、B21、B22、D9、G22、F5、F6、G1、G2）

**Files:**
- Modify: `pipeline/project.py`、`pipeline/cli.py`（`status`）、`pipeline/render.py`（拒絕訊息）
- Test: `tests/test_project.py`、`tests/test_render.py`、`tests/test_cli.py`

**Interfaces:**
- Produces: `run_step` 的輸入紀錄多了 `size`（int）和 `mtime_ns`（int）；關卡紀錄只有 `approved_at`、`storyboard_inputs`。`_gate_ok` 另外要求 prep、transcribe、trim 都是最新的。Task 11 在這之上加 `edit_checked`。

**C4** 審查原文：load_state raises raw JSONDecodeError/KeyError on malformed state.json → traceback, no JSON line; one bad project breaks `status` listing.

要的行為：`load_state` 遇到不是 JSON、不是物件、或缺少 `source`、`steps`、`gates` 任一個鍵時，丟 `PipelineError(f"state.json of project {name!r} is unreadable: {原因}")`。`status`（不帶專案名稱）把讀不了的專案列成 `{"name": ..., "error": <訊息>}`，其他專案照常列出，結束碼 0。

- [ ] `test_load_state_rejects_broken_json`、`test_load_state_rejects_missing_keys`：各自得到 `PipelineError`，訊息含專案名稱和 `unreadable`。
- [ ] `test_status_lists_a_broken_project_with_its_error`：兩個專案，一個的 `state.json` 是 `{`；`main(["status"]) == 0`；壞的那筆有 `error`，好的那筆有 `next`。

**B21** 審查原文：crash after folder.mkdir() in create_project leaves a half-built folder that blocks the name and is invisible to list_projects.

要的行為：`create_project` 先在 projects 資料夾裡建 `.new-<name>`，全部寫完才改名成 `<name>`。中途任何例外：用 `safe_rmtree(..., allowed_root=projects 資料夾)` 刪掉 `.new-<name>` 再把例外往外丟。開始建之前，若已有上次當掉留下的 `.new-<name>`，先用同樣方式刪掉。`list_projects` 略過名稱以 `.` 開頭的資料夾。

- [ ] `test_failed_create_leaves_nothing`：monkeypatch 寫 `project.md` 的那一步丟 `OSError`；`<name>` 和 `.new-<name>` 都不存在；再建一次成功。
- [ ] `test_create_clears_a_leftover_new_folder`：先放一個含檔案的 `.new-<name>`；`create_project` 成功，`.new-<name>` 不在了。
- [ ] `test_list_projects_skips_dot_folders`：`.new-x` 裡有 `state.json`，`list_projects()` 不含它。

**B22** 審查原文：status/next_step hash the whole source video on each call (prep and render inputs); consider a size+mtime shortcut if slow.

要的行為：`run_step` 記錄每個輸入的 `size` 和 `mtime_ns`。判斷步驟是否最新時（`_step_done`、`run_step` 的略過判斷），檔案現在的大小和 `mtime_ns` 都等於紀錄的值，就直接用紀錄的 sha256，不讀檔；任何一個不同，才重新算雜湊。沒有這兩個欄位的舊紀錄照舊算雜湊。

- [ ] `test_status_does_not_rehash_an_unchanged_input`：prep 跑完後，monkeypatch `pipeline.project.file_sha256` 計數；`project_status(p)` 對原始影片的呼叫次數是 0。
- [ ] `test_changed_mtime_rehashes`：改輸入檔的內容（`mtime_ns` 跟著變）；`step_done` 為 False。
- [ ] `test_status_reads_a_phase1_state`：把紀錄裡的 `size`、`mtime_ns` 刪掉；`project_status` 照樣回傳，步驟仍算完成。

**D9** 審查原文：gate_ok and render do not check that transcribe and trim are current — after a transcript change, a rebuilt storyboard and a new approval, gate_ok is true while next says trim; only a manual render that ignores next would use the stale trim.json (pre-existing).

要的行為：`_gate_ok` 另外要求 prep、transcribe、trim 都是最新的（`_step_done`）。`approve_gate` 在其中一步不是最新時，丟 `PipelineError(f"{step} is out of date: run {step} first")`（照 prep、transcribe、trim 的順序，報第一個）。`render` 因為這個原因拒絕時，訊息是 `gate1 not approved for the current storyboard: {step} is out of date, run {step}, then storyboard and approve`；其他原因維持現在的訊息。

- [ ] `test_gate_closes_when_an_earlier_step_is_stale`：完整核准後，改 `work/audio16k.wav` 的內容（transcribe 的輸入，不是分鏡稿的輸入）；`gate_ok(p, "gate1")` 為 False；`next_step(p) == "transcribe"`。
- [ ] `test_approve_refuses_when_trim_is_stale`：分鏡稿是最新的，但 trim 的輸入變了；`approve_gate` 丟出的訊息含 `trim is out of date`。
- [ ] `test_render_names_the_stale_step`：同第一個測試的狀態；`main(["render", name]) == 1`，`error` 含 `gate1` 和 `transcribe is out of date`，沒有 `final.mp4`。

**G22** 審查原文：approve still stores edit_sha256, no longer read (redundant with storyboard_inputs.edit).

- [ ] 新的關卡紀錄不再寫 `edit_sha256`；更新斷言它存在的測試。舊紀錄裡有這個鍵不影響判斷。

**F5** 審查原文：run_step nested-state re-read and _recorded_path outside-project fallback untested.

- [ ] `test_run_step_keeps_state_written_by_the_step`：`fn` 執行時自己 `save_state` 加一個鍵；`run_step` 寫完紀錄後，那個鍵還在。
- [ ] `test_output_outside_the_project_is_recorded_as_absolute`：輸出在專案外；紀錄是絕對路徑，`step_done` 照樣判斷正確。

**F6** 審查原文：inbox-untouched test checks hash+mtime only, not that no file was added to inbox.

- [ ] 那個測試另外比對 inbox 前後的檔案清單（遞迴列出的名稱）相同。

**G1** 審查原文：test fixture file named .DS_Store (platform-revealing name; use a neutral hidden name).

- [ ] `tests/test_project.py` 的 `.DS_Store` 改成 `.hidden`。

**G2** 審查原文：a few redundant tests and the `write_text("x") and {}` idiom in test_project.py.

- [ ] 刪掉斷言完全被另一個測試涵蓋的測試（報告裡逐一列出刪了哪個、由哪個涵蓋）；`write_text("x") and {}` 拆成兩行。

---

### Task 4：資源、doctor、ffprobe（B2、C5、G3、G4、F7、F8）

**Files:**
- Modify: `pipeline/assets.py`、`pipeline/media.py`
- Test: `tests/test_assets.py`、`tests/test_media.py`

**B2** 審查原文：doctor treats an empty or interrupted HF model folder as present (check snapshots/ non-empty).

要的行為：Hugging Face 模型算「在」，要同時滿足：`models--<org>--<name>/snapshots/` 底下至少有一個資料夾裡有檔案；`blobs/` 底下沒有檔名以 `.incomplete` 結尾的檔案。`doctor` 的 `asr_models` 用這個判斷。

- [ ] `test_hf_model_missing_when_folder_empty`、`test_hf_model_missing_when_snapshots_empty`、`test_hf_model_missing_while_a_blob_is_incomplete`、`test_hf_model_present_when_complete`：在假的快取資料夾裡造四種情況。

**C5** 審查原文：_urlopen misses http.client.HTTPException (IncompleteRead → traceback, no JSON line).

- [ ] `test_download_cut_short_is_a_pipeline_error`：讓下載丟 `http.client.IncompleteRead(b"")`；得到 `PipelineError`，訊息含網址。

**G3** 審查原文：font file names live in both STATIC_FONTS and style.json.

要的行為：刪掉 `STATIC_FONTS`。`setup_fonts` 要產生的檔名，從預設風格 `style.json` 的 `fonts.regular`、`fonts.bold` 取檔名，字重照 `STYLE_WEIGHTS`。

- [ ] `test_setup_fonts_names_come_from_the_style`：`setup_fonts` 產生的檔名等於 `style.json` 這兩項的檔名（用現有的假下載）。

**G4** 審查原文：ffprobe subprocess has no timeout; redundant Path() wraps; empty clip-*.log files in tests/.cache; font/model tests fail with FileNotFoundError before setup has run.

要的行為：呼叫 ffprobe 加逾時參數 `timeout_s: float = 60`，逾時丟 `PipelineError(f"ffprobe timed out after {timeout_s:g} s: {path}")`。刪掉多餘的 `Path()`。測試不在 `tests/.cache` 留下空的 `clip-*.log`。字型、模型還沒 setup 時，用到它們的測試顯示含 `pipeline setup` 的訊息，不是 FileNotFoundError。

- [ ] `test_ffprobe_timeout`：`REELS_FFMPEG_DIR` 指到一個資料夾，裡面的假 `ffprobe` 睡 5 秒；以 `timeout_s=1` 呼叫，得到上面的訊息。
- [ ] `test_clip_fixture_leaves_no_empty_log`：做完測試影片後，`tests/.cache` 沒有大小為 0 的 `clip-*.log`。

**F7** 審查原文：no probe test pins audio_delay start (~0.5), two_tracks audio_tracks==2, or rotate=270; Task 5 is the first to exercise audio_delay.

- [ ] `test_probe_audio_delay`：音訊比影片晚 0.5 秒的測試影片，probe 回報的音訊起點在 0.5 ± 0.02 秒。
- [ ] `test_probe_two_tracks`：兩條音軌的影片，`audio_tracks == 2`。
- [ ] `test_probe_rotate_270`：旋轉 270 度的影片，probe 回報的旋轉和顯示寬高符合（照 Probe 現有欄位）。

**F8** 審查原文：test_doctor_missing_aligner_makes_ok_false does not isolate the aligner as the sole cause of ok false (no baseline comparison).

- [ ] 改寫這個測試：先造出全部都在、`ok` 為 True 的基準；只拿掉 aligner 後 `ok` 為 False、`asr_models["aligner"]` 為 False，其他欄位和基準相同。

---

### Task 5：前處理和臉部偵測（B19、B20、B26、G5、F9、F10、F11、F12、F13）

**Files:**
- Modify: `pipeline/prep.py`、`pipeline/faces.py`
- Test: `tests/test_prep.py`、`tests/test_faces.py`、`tests/media_fixtures.py`

**B20** 審查原文：a missing YuNet model fails prep only after the audio and HDR encodes; check yunet_path() first.

要的行為：`prep` 一開始就檢查臉部模型（存在而且 sha256 正確）；不對就丟 `PipelineError("face model missing: run `uv run pipeline setup`")`，不執行任何 ffmpeg，不寫任何 `work/` 檔案。

- [ ] `test_prep_checks_the_face_model_first`：模型位置指到空資料夾；`prep` 丟出這個訊息；計數用的 ffmpeg runner 沒被呼叫；`work/` 沒有新檔案。

**B19** 審查原文：a source under 2 s gets 0 samples (plan formula) → Task 12 shows "no face detected"; consider max(1, …).

要的行為：任何長度大於 0 的素材至少抽 1 張畫面；只抽 1 張時抽在中間。

- [ ] `test_short_source_gets_one_sample`：`sample_times` 對 1.0 秒的素材回傳 1 個時間，`0 < t < 1`。

**B26** 審查原文：container-duration fallback untested; an mkv with no video stream duration and longer audio gets a wav longer than the video.

要的行為：影片串流沒有長度時，影片長度改從影片串流最後一個 packet 的 `pts_time + duration_time` 算，不用容器長度。抽出的 wav 長度等於影片長度（誤差一格，1/30 秒以內）。

- [ ] `test_mkv_without_stream_duration_and_longer_audio`：做一個影片串流沒有長度、音訊比影片長 1 秒的 mkv；`prep` 後 probe 的 `duration_s` 和 wav 長度都等於影片長度（± 1/30 秒）。

**G5** 審查原文：_encode takes the .part path from args[-1] (implicit coupling); first_sound_s/last_sound_s duplicate the threshold scan.

- [ ] `_encode` 改成用明確的參數接暫存檔路徑；`first_sound_s` 和 `last_sound_s` 共用一個找門檻的函式（整潔，現有測試涵蓋）。

**F9** 審查原文：no test for ffmpeg failure leaving no .part/probe.json/record behind (verified by hand with a stub ffmpeg).

- [ ] `test_failed_encode_leaves_nothing`：假 ffmpeg 失敗；沒有 `.part`、沒有 `probe.json`、沒有 prep 的步驟紀錄。

**F10** 審查原文：no test for >2-channel downmix or a mid-stream audio timestamp gap (both verified by hand; downmix lowers level, centre-only tone peaks ~0.21).

- [ ] `test_surround_source_is_downmixed`：5.1 聲道、只有中央聲道有聲音的素材；`audio48k.wav` 是 2 聲道，兩個聲道都聽得到（峰值大於 0.1）。
- [ ] `test_audio_timestamp_gap`：音訊中途有一段時間戳空白的素材；wav 長度等於影片長度（± 1/30 秒），空白處是靜音。

**F11** 審查原文：no test asserts the OpenCV log level is restored after a failed model load.

- [ ] `test_failed_model_load_restores_the_opencv_log_level`：給一個壞掉的模型檔；載入失敗後，OpenCV 的記錄等級等於載入前的值。

**F12** 審查原文：the HDR faces test asserts only samples==2, not that detect_faces read work/video.mp4.

- [ ] HDR 的臉部偵測測試另外確認 `detect_faces` 收到的影片路徑是 `work/video.mp4`。

**F13** 審查原文：no non-9:16 face-normalisation test in the suite (verified by hand).

- [ ] `test_face_box_normalised_for_landscape_source`：16:9 的畫面和一個已知的臉框，換算後的座標符合預期值（照 faces.py 的換算公式手算，寫成常數）。

---

### Task 6：腳本解析、比對單位、字幕的 Markdown 記號（B6、B8、B9、B10、B11、G6、G8、G9、G15）

**Files:**
- Modify: `pipeline/script.py`、`pipeline/textnorm.py`、`pipeline/captions.py`
- Test: `tests/test_script.py`、`tests/test_textnorm.py`、`tests/test_captions.py`

**Interfaces:**
- Produces: 一個共用的函式（名稱自訂，放 `pipeline/script.py` 或 `pipeline/textnorm.py`），把 Markdown 強調記號和連結網址拿掉；`parse_script` 和 `captions.py` 都用它，規則只有一份。

**B8** 審查原文：a bare ordinal line ("1." or "10)" with nothing after) is not stripped and becomes a phantom sentence; allow end-of-line after [.)].

- [ ] `test_bare_ordinal_line_is_not_a_sentence`：「1.」「10)」「3、」各自單獨一行，都不產生句子。

**B9** 審查原文：consequence of the list-mark ruling — "1、2、3 都是。" loses its first number ("3、4 號" → "4 號"); alignment flags it at worst.

要的行為：行首的「數字、」後面緊接著（可隔空白）另一個數字時，不算清單記號。

- [ ] `test_number_list_in_text_is_kept`：「1、2、3 都是。」→ 句子文字「1、2、3 都是。」；「3、4 號」→「3、4 號」；「1、今天」→「今天」；「1. 今天」→「今天」。

**B10** 審查原文：Markdown bold markers and link URLs stay in Sentence.text (a link URL becomes latin units).

要的行為：`[文字](網址)` 在句子裡只留「文字」，不產生註記；成對的強調記號 `**x**`、`__x__`、`***x***`、`~~x~~`、`` `x` ``，以及單星號 `*x*`（前一個字不是 ASCII 英數字、後一個字不是 ASCII 英數字、x 頭尾不是空白）拿掉記號、留下 x。這些照舊：單獨的 `*`、`5*3`、`5*3*2`、`file_name`、`3~5`；後面沒有 `(網址)` 的 `[注意]` 仍是註記。

- [ ] `test_link_keeps_its_text_only`：「看[這裡](https://example.com)。」→「看這裡。」，沒有註記。
- [ ] `test_emphasis_marks_are_removed`：「這是**重點**。」→「這是重點。」；「這*很重要*。」→「這很重要。」；「***x***」→「x」。
- [ ] `test_star_and_underscore_in_text_are_kept`：「5*3*2」「file_name」「3~5」不變。

**B6** 審查原文：a single-star italic (*很重要*) shows its asterisks; ***x*** leaves *x* (Ruling L as worded).

- [ ] `test_caption_has_no_emphasis_marks`：腳本「這*很重要*。」的字幕文字是「這很重要。」；「***x***」的字幕是「x」。

**B11** 審查原文：NFKC-expanding chars (½, ㍿, m²) give several units with identical spans.

要的行為：每個原文字元只屬於一個比對單位。NFKC 展開成好幾個字的字元，是一個單位，span 就是那一個字元，比對鍵由展開後的文字算。

- [ ] `test_nfkc_expanding_char_is_one_unit`：`split_units("½")` 只有 1 個單位，span `(0, 1)`；「約 ½ 杯」「1㍿」「5m²」的單位 span 互不重疊、依序涵蓋原文。
- [ ] `test_caption_shows_expanding_char_once`：腳本「約½杯。」的字幕文字是「約½杯。」。

**G6** 審查原文：invisible BOM literal in script.py and test_script.py — write it as an escape.

- [ ] 兩個檔案裡的 BOM 字元改寫成 `"﻿"`（整潔）。

**G8** 審查原文：script.py module docstring line is 164 chars (re-wrap).

- [ ] 重新斷行，每行不超過 `pyproject.toml` 設定的行長（整潔）。

**G9** 審查原文：redundant `1 <=` in the thousands leading-group check (textnorm.py:100); "0.5,000" split is untested.

要的行為：千分位只用在整數部分，「0.5,000」不是一個數字。

- [ ] 刪掉多餘的 `1 <=`；`test_decimal_then_comma_is_not_one_number`：「0.5,000」切出的單位裡沒有一個單位是「0.5,000」或「0.5000」。

**G15** 審查原文：captions.py module docstring wrap; "a Latin word or a number is one unit" is slightly off (3D is protected by _can_cut).

- [ ] 改正那句說明並重新斷行（整潔）。

---

### Task 7：語音辨識（B27、B28、C6、F14、F15、G10）

**Files:**
- Modify: `pipeline/asr.py`、`tests/fixtures/meijia/make_fixture.sh`
- Test: `tests/test_asr.py`

**B27** 審查原文：_parses accepts any JSON object ({}), which load_asr then rejects on every call until the cache folder is cleared.

要的行為：快取裡的結果要通過 `load_asr` 的檢查才算數；不通過就當成沒有快取，重新辨識並覆蓋它。

- [ ] `test_invalid_cached_result_is_redone`：快取檔內容是 `{}`；`transcribe_audio` 呼叫 runner，回傳有效結果，快取檔換成新結果。

**B28** 審查原文：run_asr cleanup catches only PipelineError; a KeyboardInterrupt right after a complete write leaves the result (docstring overstates); use finally + success flag.

要的行為：只有辨識跑完、結果檢查通過，才留下結果檔；中途任何例外（含 `KeyboardInterrupt`）都不留結果檔，例外照樣往外丟。說明文字照實寫。

- [ ] `test_interrupt_after_write_leaves_no_result`：假 runner 寫完完整結果後丟 `KeyboardInterrupt`；`pytest.raises(KeyboardInterrupt)`；結果檔不存在。

**C6** 審查原文：ASR failure message keeps only the last log line, does not name a signal (SIGKILL/OOM), and the final error drops the 1.7B log path.

要的行為：程式被訊號結束（結束碼小於 0）時，訊息寫 `killed by signal <N> (<名稱>)`；SIGKILL 另外加 `possibly out of memory`。訊息附 log 最後 5 行非空白的內容。1.7B 失敗、0.6B 也失敗時，最後的錯誤同時列出兩個 log 的路徑。

- [ ] `test_signal_exit_is_named`：假 CLI 用 SIGKILL 結束自己；訊息含 `SIGKILL` 和 `possibly out of memory`。
- [ ] `test_failure_message_has_last_log_lines`：假 CLI 印 10 行後失敗；訊息含第 6 到 10 行，不含第 5 行。
- [ ] `test_both_models_failing_names_both_logs`：兩個假 CLI 都失敗；錯誤含兩個 log 路徑。

**F14** 審查原文：no tests for signal exit code, large stderr, multi-unit zero-length segment, or two audios sharing one cache_dir (verified by hand).

- [ ] `test_large_stderr`：假 CLI 寫 1 MB 到 stderr 後失敗；呼叫在逾時內結束，訊息只含最後幾行。
- [ ] `test_multi_unit_zero_length_segment`：結果裡有一段多個字、開始等於結束的片段；照 `load_asr` 現有規則處理（測試釘住現在的行為）。
- [ ] `test_two_audios_share_a_cache_dir`：兩段不同的音訊用同一個快取資料夾；各自得到自己的結果。
- （訊號結束碼由 `test_signal_exit_is_named` 涵蓋。）

**F15** 審查原文：fallback test gives the 0.6B fake CLI a 2 s timeout too (possible flake on a loaded machine).

- [ ] 備援測試裡只有 1.7B 的假 CLI 用短逾時；0.6B 的逾時至少 30 秒。

**G10** 審查原文：make_fixture.sh — `uv run` may sync .venv/uv.lock (add --frozen), writes fixture.wav in place (write to tmp then mv), comment says "-50 dBFS" for a peak value, /tmp fallback when TMPDIR is unset.

- [ ] `uv run --frozen`；`fixture.wav` 先寫到同資料夾的暫存檔再 `mv`；pink noise 的註解改成正確的說法（`amplitude=0.003` 是振幅，寫明它約等於多少 dBFS 以及是峰值還是均方根，數值要自己算對）；`TMPDIR` 沒設時印錯誤並結束，不退回 `/tmp`。這是腳本，不需新測試；**不要執行它**（會重新產生素材和辨識結果），改完用 `bash -n` 檢查語法。

---

### Task 8：對齊（B3、B4、B16、B29、G11）

**Files:**
- Modify: `pipeline/align.py`
- Test: `tests/test_align.py`

**B3** 審查原文：one length-changing phrase sends its whole displayed stretch to per-unit conversion, so other words lose context (我的博客头发很干净 → 頭發很幹淨 in an ASR-text caption; flagged asr_text/needs_listen); split the stretch around the changing phrase.

要的行為：一段顯示文字裡有簡轉繁會改變長度的詞時，只有那個詞逐字轉換，其餘部分照上下文整段轉換。

- [ ] `test_length_changing_phrase_does_not_spoil_the_rest`：用辨識文字顯示的句子「我的博客头发很干净」；字幕文字含「頭髮很乾淨」。

**B29** 審查原文：_keeps_places is quadratic in stretch length (1500-unit stretch 7.39 s); only long off-script runs (e.g. wrong script) hit it.

要的行為：轉換一段 n 個單位的文字，傳給 `to_taiwan` 的字元總數不超過 20 × n。

- [ ] `test_stretch_conversion_is_linear`：monkeypatch 計算傳給 `to_taiwan` 的字元總數；1500 個單位的段落，總數 ≤ 30000，結果和逐段轉換的預期一致。

**B4** 審查原文：a run inside an ASR-text sentence is converted twice; runs[].text can disagree with the caption and its tokens (軟體 vs 軟件).

要的行為：用辨識文字顯示的句子裡，`runs[].text` 等於句子顯示文字在同一段辨識單位上的文字（只轉換一次）。

- [ ] `test_run_text_matches_the_caption`：句子裡有一段「软件」的腳本外的話；那段 run 的 `text` 和字幕裡同位置的文字相同。

**B16** 審查原文：R2 has no 4-unit minimum, so very short sentences can be unbound when the speech resembles the next sentence's start (flagged needs_listen); a filler adjacent to a freed partial take merges into one run classified reworded.

要的行為：
1. `_unbind_skipped` 的第二條規則（吻合度低於 `ASR_TEXT_RATIO`、而且講的內容像下一句的開頭），只在綁到這句的辨識單位至少有 `RETAKE_MIN_UNITS` 個時才成立。
2. 一段 run 的開頭或結尾若是一個以上的贅字，而且緊鄰被釋放的部分重講，贅字自己成為一段 filler run，其餘照原規則分類。

- [ ] `test_short_sentence_stays_bound`：三個字的句子，講的內容像下一句開頭；這句仍算有講。
- [ ] `test_filler_next_to_a_freed_take_is_its_own_run`：「呃」緊接著被釋放的部分重講；得到一段 `filler` run 和一段另外分類的 run。

**G11** 審查原文：a Latin word clamped to zero length keeps src "asr" (label interp).

- [ ] `test_clamped_latin_word_is_interp`：時間被壓成 0 長度的英文字，`src == "interp"`。

---

### Task 9：粗剪候選的整潔（G12、G13、G14、F16）

**Files:**
- Modify: `pipeline/trim.py`、`pipeline/audio.py`、`tests/helpers.py`
- Test: `tests/test_trim.py`、`tests/test_audio.py`

**G12** 審查原文：local variable `trim` shadows the module's trim() function (trim.py:58,63,103).

- [ ] 改成不同的名稱（整潔）。

**G13** 審查原文：flat_envelope docstring in tests/helpers.py is now false (an all-quiet envelope flags every cut and warns).

- [ ] 改正說明（整潔）。

**G14** 審查原文：snap_cut's own silent flag (floor + 6) and floor_db parameter are unused in production and can disagree with needs_listen on few-pause footage.

要的行為：`snap_cut` 只回傳切點，不再自己判斷是否靜音，也不收 `floor_db`；是否靜音只由 `needs_listen` 那一套門檻判斷。

- [ ] 改 `snap_cut` 和它的呼叫處、測試；整套測試全過（重構，現有測試涵蓋切點位置）。

**F16** 審查原文：the Meijia fixture oracle in test_trim.py restates the old floor formula instead of the new helpers.

- [ ] 那個預期值改用 `noise_floor_db`、`silence_threshold_db` 算。

---

### Task 10：edit.json 的檢查（C7、C8、C9、D5、G19、G20）

**Files:**
- Modify: `pipeline/edit.py`、`pipeline/storyboard.py`、`pipeline/render.py`
- Test: `tests/test_edit.py`

**Interfaces:**
- Produces: `edit.py` 的公開函式 `load_document`（原 `_load_document`）和 `to_frame(t: float) -> int`（原 `edit._frame`，storyboard 的那一份刪掉）。

**C7** 審查原文：load_edit lets RecursionError / ValueError (deep nesting, >4300-digit ints) escape as tracebacks instead of EditInvalid.

- [ ] `test_deeply_nested_edit_is_invalid`：`"[" * 100000`；得到 `EditInvalid`，錯誤含 `not valid JSON`。
- [ ] `test_huge_integer_is_invalid`：一個 5000 位的整數；得到 `EditInvalid`。

**C8** 審查原文：extra-range overlap check reports every pair (quadratic error output on pathological files).

要的行為：每個和前面範圍重疊的 `cuts.extra` 範圍只報一次（報第一個重疊的對象），n 個範圍最多 n − 1 條錯誤。

- [ ] `test_overlaps_reported_once_per_range`：200 個相同的範圍；重疊的錯誤正好 199 條。

**C9** 審查原文：error text embeds unbounded values; an empty or newline key yields a pathless or multi-line error.

要的行為：錯誤訊息裡的值用 `repr`，超過 60 字元截斷並加「…」；鍵是空字串或含控制字元時用 `repr` 顯示。每條錯誤都是一行，而且有路徑。

- [ ] `test_error_for_odd_keys_is_one_line_with_a_path`：鍵 `""` 和 `"a\nb"`；每條錯誤沒有換行，含 `''` 或 `'a\\nb'`。
- [ ] `test_long_value_is_truncated`：一個 10000 字元的字串值；那條錯誤短於 300 字元。

**D5** 審查原文：duplicate JSON keys resolve last-wins silently (object_pairs_hook would catch them).

- [ ] `test_duplicate_key_is_invalid`：最上層有兩個 `cuts`；得到 `EditInvalid`，錯誤含 `duplicate key 'cuts'`。巢狀的物件裡重複也一樣，錯誤含它的路徑。

**G19** 審查原文：storyboard._frame duplicates the private edit._frame; the edge matching relies on both rounding identically.

- [ ] 只留一份，見 Interfaces（整潔）。

**G20** 審查原文：render.py imports the private edit._load_document.

- [ ] 改名成公開函式，見 Interfaces（整潔）。

---

### Task 11：edit.json 過時（B17）

**Files:**
- Modify: `pipeline/project.py`、`pipeline/edit.py`、`pipeline/storyboard.py`、`.claude/skills/reel/SKILL.md`、`.claude/skills/storyboard/SKILL.md`
- Test: `tests/test_project.py`、`tests/test_edit.py`、`tests/test_storyboard.py`、`tests/test_flow.py`

**Interfaces:**
- Consumes: Task 3 的 `_gate_ok`、步驟紀錄。
- Produces: `state.json` 的 `edit_checked = {"edit": sha256, "transcript": sha256, "trim": sha256}`；`next_step` 多一個值 `validate`。

**B17** 審查原文：after a script edit and a transcribe rerun, trim ids can change and edit.json may go stale — covered only by "rerun from there" plus validate.

背景：候選編號是照順序編的（t01、t02…），句子編號也是。重跑 transcribe 或 trim 之後，同一個編號可能指到另一個候選或另一句，舊的 `edit.json` 卻可能照樣通過檢查。

要的行為：
1. `validate` 通過時，把當下 `edit.json`、`transcript.json`、`trim.json` 的 sha256 記成 `edit_checked`。
2. `next_step`（trim 之後）：沒有 `edit.json` → `edit`；`edit_checked` 的 transcript 或 trim 和現在的檔案不同 → `edit`；沒有 `edit_checked`，或它的 edit 和現在的 `edit.json` 不同 → `validate`；其餘照舊（storyboard、approve…）。
3. `storyboard` 在 `edit_checked` 和現在三個檔案不完全相同時拒絕：transcript 或 trim 不同時，訊息是 `edit.json was checked against an older transcript.json or trim.json: re-check every trim and sentence id, then run validate`；只有 edit 不同或沒有紀錄時，訊息是 `edit.json has not been validated: run validate`。
4. `validate` 通過時，如果上一次的 `edit_checked` 的 edit 和現在相同、但 transcript 或 trim 不同，結果多一個 `warnings` 項目：`edit.json is unchanged since it was checked against an older transcript.json or trim.json: trim and sentence ids may now point at different candidates`。
5. skill：reel skill 說明 `next` 的 `validate` 和這種 `edit` 是什麼意思；transcribe 或 trim 重跑之後，要逐一重新核對 `edit.json` 裡每個候選和句子編號，再跑 `validate`，並把上面的警告轉述給使用者。

- [ ] `test_validate_records_edit_checked`：`validate` 通過後，`state.json` 有三個雜湊，等於三個檔案現在的 sha256。
- [ ] `test_next_is_validate_after_edit_changes`：改 `edit.json` 後 `next_step == "validate"`；跑 `validate` 後變成 `storyboard`。
- [ ] `test_next_is_edit_after_trim_changes`：`validate` 之後改 `trim.json`；`next_step == "edit"`。
- [ ] `test_storyboard_refuses_edit_checked_against_older_trim`：同上的狀態；`storyboard` 結束碼 1，訊息含 `older transcript.json or trim.json`。
- [ ] `test_validate_warns_when_edit_is_unchanged_but_trim_changed`：同上的狀態直接再跑 `validate`；通過，`warnings` 含上面那句。
- [ ] `test_phase1_project_without_edit_checked_goes_to_validate`：有 `edit.json` 但沒有 `edit_checked`；`next_step == "validate"`。
- [ ] 更新 `tests/test_flow.py`：寫完 `edit.json` 之後先跑 `validate` 再跑 `storyboard`（若已是這個順序，確認 `next` 的斷言跟著新規則）。

---

### Task 12：分鏡稿（F17、G17、G18）

**Files:**
- Modify: `pipeline/storyboard.py`
- Test: `tests/test_storyboard.py`

**F17** 審查原文：the dropped-sliver branch of head/tail ownership has no dedicated test (traced correct); non-monotone bounds would leave inner cuts unflagged (theoretical); a missing probe.json now errors with run_step's generic "input not found" instead of "run prep first".

要的行為：
1. 頭尾歸屬裡「丟掉小碎片」的分支有專門的測試。
2. 句子的邊界不是遞增時，`storyboard` 丟 `PipelineError("sentence bounds in trim.json are out of order: run trim again")`，不默默略過。
3. 缺 `work/probe.json` 時，訊息是 `probe.json not found: run prep first`。

- [ ] `test_dropped_sliver_head_tail_ownership`：造出會丟掉小碎片的頭尾情況；標記和播放片段符合預期（照現有程式手算）。
- [ ] `test_out_of_order_bounds_are_refused`：把 `trim.json` 的兩句邊界對調；得到上面的訊息。
- [ ] `test_missing_probe_says_run_prep`：刪掉 `probe.json`；結束碼 1，訊息含 `run prep first`。

**G17** 審查原文：storyboard.py:77 regex holds literal U+2028/U+2029 bytes; write them as escapes.

- [ ] 改寫成 `\u2028`、`\u2029`（整潔）。

**G18** 審查原文：storyboard.py:56 `TRIM_PAD_S = 1.0#` lacks the space before the comment.

- [ ] 補空格（整潔）。

---

### Task 13：渲染（B24、B25、C10、G21、G23、F19、F23）

**Files:**
- Modify: `pipeline/render.py`、`pipeline/style.py`（必要時）
- Test: `tests/test_render.py`

**B24** 審查原文：a skipped render reports video_bitrate recomputed from current constants, not the rate the existing file was encoded with; no test pins the reported value to the -b:v of a lowered-rate run.

要的行為：被跳過的 `render` 結果沒有 `video_bitrate`（這次沒有編碼）；真正執行的 `render` 回報的 `video_bitrate` 等於傳給 ffmpeg 的 `-b:v`。

- [ ] `test_lowered_rate_is_reported_as_encoded`：長度讓位元率低於 12 Mbps 的成品（可用假 runner 記下參數）；結果的 `video_bitrate` 等於 `-b:v` 的值換算成 bit/s。
- [ ] `test_skipped_render_has_no_bitrate`：再跑一次被跳過；結果沒有 `video_bitrate`，`skipped is True`。

**B25** 審查原文：out/final.tmp.mp4 is left behind if check_output's ffprobe, reading the log, or os.replace raises (the next attempt unlinks it).

- [ ] `test_temp_output_removed_when_check_raises`：monkeypatch `check_output` 丟 `OSError`；`render` 失敗，`out/final.tmp.mp4` 不存在。`os.replace` 失敗也一樣。

**C10** 審查原文：a missing Regular font raises FileNotFoundError (traceback, not the JSON line); setup/doctor normally guarantee it.

- [ ] `test_missing_font_is_a_json_error`：字型資料夾裡沒有 Regular；`main(["render", name]) == 1`，`error` 含字型檔名和 `pipeline setup`；沒有寫出任何 `out/` 檔案。

**G21** 審查原文：no docstring says a render that actually runs removes the old final.mp4 first.

- [ ] `render` 的說明加上這一點（整潔）。

**G23** 審查原文：_ffmpeg_rate's 12M spelling assumes a whole-Mbps cap; build_ffmpeg_args([]) now raises ZeroDivisionError (unreachable via render); the exact 4 Mbps boundary is untested; AUDIO_BIT_RATE/AUDIO_BITRATE/MIN_*_BIT_RATE naming is mixed.

要的行為：`_ffmpeg_rate` 一律用 `k` 寫（12 Mbps 寫成 `12000k`）。`build_ffmpeg_args([])` 丟 `PipelineError("nothing to render: the reel has no segments")`。常數統一用 `BITRATE` 結尾：`AUDIO_BITRATE`（int）、`MIN_AUDIO_BITRATE`、`MAX_BITRATE`、`MAX_VIDEO_BITRATE`、`MIN_VIDEO_BITRATE`；`-b:a` 的字串由 `_ffmpeg_rate(AUDIO_BITRATE)` 產生。

- [ ] `test_ffmpeg_rate_spelling`：`12_000_000 → "12000k"`、`4_003_000 → "4003k"`、`192_000 → "192k"`。
- [ ] `test_no_segments_is_a_pipeline_error`。
- [ ] `test_exact_minimum_bitrate_is_accepted`：算出位元率剛好 4 Mbps 的長度，`video_bitrate` 回傳 4_000_000；長 1 秒的就被拒絕。

**F19** 審查原文：no end-to-end render of a source whose video starts after its audio (the delayed helper delays both); prep's relative-offset handling is tested only at prep level.

- [ ] `test_render_video_starting_after_audio`：做一個只有影片晚開始 0.5 秒的素材，在同一刻有一格白畫面和一聲嗶；用現有測試 helper 建專案（辨識結果用假的或錄好的，不跑真的語音辨識），跑 `prep` 到 `render`；成品裡白畫面那一格和嗶聲開頭相差不超過一格（1/30 秒）。

**F23** 審查原文：the too-long refusal test cannot see a dropped step record (no prior record planted).

- [ ] 「太長被拒絕」的測試先放一筆 render 的步驟紀錄；拒絕之後那筆紀錄還在。

---

### Task 14：授權檢查（B23、F20、E4）

**Files:**
- Modify: `scripts/third_party.py`、`THIRD_PARTY.md`
- Test: `tests/test_third_party.py`

**B23** 審查原文：third_party.py matches register rows by name only — a registered package that changes licence on upgrade passes silently (plan-mandated). 使用者 2026-11-12 決定：連版本一起比對。

要的行為：`register_rows` 回傳「名稱 → 該名稱每一列的（版本, 能否商業使用）」。版本取「版本或來源」欄第一個空白或全形括號之前的文字。授權不在允許清單的套件，必須有一列的版本等於已安裝的版本、而且寫「可」，才通過。名稱對得上、版本對不上時，問題訊息是 `{name} {version}: THIRD_PARTY.md lists it for version {清冊上的版本}; read the licence of {version} on the official page and update the row`。

- [ ] `test_row_for_another_version_does_not_count`：清冊寫 2.5.3、安裝 2.5.4、授權不在允許清單；檢查失敗，訊息含兩個版本。
- [ ] `test_row_for_the_installed_version_passes`：同上但清冊寫 2.5.4；通過。
- [ ] `test_version_cell_with_a_note`：「0.4.4（pyproject 固定）」讀成 0.4.4。
- [ ] 跑 `uv run python scripts/third_party.py --check`，用真的清冊和真的 lock 檔通過；不通過就把清冊的版本對到 lock 檔（只改版本欄位，授權照原本查到的）。

**F20** 審查原文：third_party.py — UV pointing at a missing binary raises an uncaught FileNotFoundError; marker_applies, export_requirements failures and the unreadable-register branch have no direct test; test_transitive_licenses_pass needs uv on PATH or UV (fails outside `uv run`).

- [ ] `test_uv_pointing_at_a_missing_file`：`UV` 指到不存在的檔案；`CheckError`，訊息含那個路徑。
- [ ] `test_marker_applies`：空字串為 True；`sys_platform == "nonexistent"` 為 False。
- [ ] `test_export_failure_reports_the_last_line`：假 uv 印兩行 stderr、結束碼 2；`CheckError` 含結束碼和最後一行。
- [ ] `test_unreadable_register`：`REGISTER` 指到不存在的檔案；`main(["--check"]) == 1`，訊息說讀不到清冊。
- [ ] `test_transitive_licenses_pass` 在找不到 uv 時 skip，理由寫明。

**E4** 審查原文：THIRD_PARTY.md uses LGPL/GPL before the allow-list line defines them.

- [ ] 在第一次出現 LGPL 的地方定義 LGPL 和 GPL（GNU 寬通用公共授權、GNU 通用公共授權，一句說明差別）。

---

### Task 15：skill 文字、流程測試、清單收尾（E1、E2、E3、F22）

**Files:**
- Modify: `.claude/skills/reel/SKILL.md`、`.claude/skills/storyboard/SKILL.md`、`docs/backlog/2026-11-12-phase1-review-items.md`
- Test: `tests/test_flow.py`、`tests/test_docs.py`

**E1** 審查原文：reel SKILL.md:21 still says gate1_ok means approval of the current edit.json; storyboard SKILL.md:121 incomplete in the same way.

- [ ] 改成：`gate1_ok` 表示使用者同意的是現在這份分鏡稿（同一份 `edit.json`、同樣的分鏡稿輸入），而且 prep、transcribe、trim 都是最新的。

**E2** 審查原文：storyboard skill says line mode shows the whole sentence on one line at once; each line is its own event (a 27-character sentence shows as three lines in turn).

- [ ] 改成：每一行是一個字幕事件，依序出現；舉 27 個字分三行的例子。

**E3** 審查原文：the reel skill covers doctor's fonts, yunet and asr_models but not asr_cli, libass or the hardware encoders.

- [ ] 補上 `asr_cli`、`libass`、`h264_videotoolbox`、`hevc_videotoolbox` 各自代表什麼、不通過時怎麼辦；並寫明 `doctor` 不通過時結束碼是 1（Task 1）。

**F22** 審查原文：the final prep re-run checks skipped but not the exit code (as the brief has it).

- [ ] 流程測試最後重跑 `prep` 時，另外斷言結束碼 0。

**其他 skill 文字跟上本輪的行為**
- [ ] 逐一確認 skill 和 `CLAUDE.md` 裡提到的行為和本輪一致：錯誤輸出（Task 1）、`status` 的錯誤項目（Task 3）、核准的條件（Task 3）、被跳過的 render 沒有 `video_bitrate`（Task 13）。不一致的地方改掉。

**清單收尾**
- [ ] 在 `docs/backlog/2026-11-12-phase1-review-items.md` 的總覽表上方加一句：本輪 81 條已在分支 `fix/phase1-review-items` 修完，計畫見 `docs/superpowers/plans/2026-11-12-phase1-review-items.md`。
