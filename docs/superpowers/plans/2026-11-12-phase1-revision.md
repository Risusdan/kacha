# 第 1 期改版 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 實作第 1 期改版：字幕在詞的邊界斷行、固定在下方置中；粗剪認得口吃、「恩」、夾在話裡的贅字和 NG 口令，腳本外的話變成候選；關卡 1 改成一則訊息加網頁最上面的清單；`render` 另做 30 MiB 以內的預覽版。

**Architecture:** 沿用現有的 `pipeline` 模組，各自改：`align`（分類規則）、`trim`（新候選）、新的 `words`（jieba 斷詞）、`captions`（斷行和位置）、新的 `review_items`（關卡 1 要列的項目）、`storyboard`（網頁）、`render`（預覽版），再改 skill 和文件。`transcript.json` 和 `trim.json` 的格式版本加 1，舊專案自動回到 `transcribe`。

**Tech Stack:** Python 3.12（uv）、pytest、jieba 0.42.1、ffmpeg-full 9.0.2、OpenCC、mlx-qwen3-asr。

**Spec:** `docs/superpowers/specs/2026-11-12-phase1-revision-design.md`（以下簡稱「設計」）；jieba 實測：`docs/spikes/2026-11-12-jieba-results.md`。

## Global Constraints

- 分支 `feat/phase1-revision`，從 `main` 上 commit 這份計畫的那個 commit 分出，在 git worktree 裡做。Commit 訊息不加任何 AI 或工具署名；會開編輯器的 git 指令一律加 `--no-edit` 或 `-m`。
- 行為改動一律先寫失敗的測試，看它因為預期的原因失敗，再寫程式。只整理程式、而且現有測試已經涵蓋的，不必新增測試，但整套測試要全過。
- 這份計畫刻意移除的行為（列在各 task 的「移除的行為」），對應的舊測試改寫或刪除，不算破壞。其他舊測試失敗就是 bug。
- 只支援目前的開發機。ffmpeg 一律經過 `pipeline.media.ffmpeg_bin()`。
- `studio/` 不進 git，`inbox/` 只讀。遞迴刪除一律經過 `pipeline.safe_delete.safe_rmtree`。
- `edit.json` 是唯一由 Claude 寫的檔案，格式（`EDIT_VERSION` 1）不變。程式不產生也不改寫它。
- `pipeline` 指令的輸出：stdout 一行 JSON，英文。結束碼：0 成功，1 執行錯誤，2 `edit.json` 檢查不通過。jieba 的載入訊息和 import 時的 `SyntaxWarning` 都不能出現在 stdout 或 stderr。
- 網頁和 `storyboard.md` 上給使用者看的字，一律繁體中文（台灣用語），照這份計畫寫的字樣。
- 測試和文件裡的例句一律自己編，不用使用者的素材、腳本或逐字稿。
- 第三方套件：新增或升級後跑 `uv run python scripts/third_party.py --check`，並更新 `THIRD_PARTY.md`。
- 測試環境：`tests/conftest.py` 已把 `HOME`、`REELS_STUDIO` 指到暫存目錄。`uv run pytest` 預設跳過 `slow`。斷詞字典、字型和臉部模型由 `uv run pipeline setup` 下載；worktree 建好後先跑一次。
- 程式、識別字、指令輸出、commit 訊息用英文；計畫和 skill 用繁體中文（台灣用語）。
- 出貨規則：程式、註解、測試檔名、log 字串都不寫出開發平台的名稱或只有那個平台才有的路徑。
- 磁碟：每次下載或渲染之前跑 `df -h /`，可用空間低於 15 GB 就停下來回報 BLOCKED。

## Review Focus

1. **停頓在保留的腳本外的話裡**：腳本外的話變成候選以後，它裡面超過 0.7 秒的停頓仍要壓短（舊行為），不能因為停頓被候選蓋住就消失 → Task 3 `test_a_pause_inside_kept_extra_speech_is_still_shortened`。
2. **一大段沒有詞邊界、比一行還長的中文**（字典沒有的長專有名詞）：仍要斷成每行都放得下，並標出從詞中間斷開 → Task 5 `test_a_cjk_stretch_without_word_boundaries_longer_than_a_line_is_split`。
3. **逐字變色遇到被斷開的英文詞**：`\k` 不能是負數，加總等於字幕的長度 → Task 5 `test_karaoke_of_a_split_word_adds_up`。
4. **沒有任何要決定、要聽的項目**：網頁和 `storyboard.md` 說「沒有」，數字是 0，不當掉 → Task 8 `test_no_items_says_so`。
5. **第 1 期留下的專案**：步驟紀錄沒有格式版本。`status` 指向 `transcribe`，`storyboard`、`render` 拒絕並說要重跑 `transcribe` → Task 1 `test_a_phase1_project_goes_back_to_transcribe`。

---

## 共用步驟

每個 task 的最後兩步都是：

- 跑整套測試：`uv run pytest -q`，預期全過（`slow` 跳過）。
- Commit：只 add 這個 task 動到的檔案，訊息用英文、寫出這個 task 改了什麼行為。

---

### Task 1: 檔案格式版本

**Files:**
- Modify: `pipeline/project.py`（`run_step`、`_step_done`）
- Modify: `pipeline/align.py:26`、`pipeline/trim.py:29`（版本常數）、`pipeline/trim.py`（`trim` 讀 transcript 的地方）
- Modify: `pipeline/edit.py`（`load_document`、`require_valid`）
- Test: `tests/test_project.py`、`tests/test_edit.py`、`tests/test_trim.py`；`tests/test_align.py:426`、`tests/test_trim.py:187` 的版本值改成 2

**Interfaces:**
- Produces:
  - `project.FORMAT_VERSIONS: dict[str, int] = {"transcribe": 2, "trim": 2}`
  - `align.TRANSCRIPT_VERSION = FORMAT_VERSIONS["transcribe"]`、`trim.TRIM_VERSION = FORMAT_VERSIONS["trim"]`
  - `run_step` 對 `FORMAT_VERSIONS` 裡的步驟，在紀錄裡多寫 `"version": <int>`；其他步驟的紀錄不變。
  - 紀錄的 `version` 不等於 `FORMAT_VERSIONS[step]`（包括沒有這個欄位）時：`run_step` 不跳過，`_step_done` 是 False。
  - `load_document(path, step, keys, version: int | None = None) -> dict`：給了 `version`，而文件有 `version` 欄位但值不同時，丟 `PipelineError(f"{path.name} was made by an older pipeline: run {step} again")`。文件沒有 `version` 欄位時照常讀（測試裡手寫的文件）。
  - `require_valid` 讀 `transcript.json` 和 `trim.json` 時傳入各自的版本。
  - `trim` 讀到版本不同的 `transcript.json` 時，丟同一則訊息（step 是 `transcribe`）。

- [ ] **Step 1: 寫失敗的測試**

```python
# tests/test_project.py
def test_a_step_record_without_the_current_format_version_is_not_done(transcribed_project):
    state = load_state(transcribed_project)
    state["steps"]["transcribe"].pop("version", None)
    save_state(transcribed_project, state)
    assert not step_done(transcribed_project, "transcribe")
    assert first_stale_step(transcribed_project) == "transcribe"

def test_run_step_runs_again_when_the_recorded_version_is_older(transcribed_project):
    # 把紀錄的 version 改成 1，再呼叫 transcribe（不加 force）：不跳過，紀錄回到 2

def test_steps_without_a_format_version_record_none(transcribed_project):
    # prep 的紀錄沒有 "version" 欄位

def test_a_phase1_project_goes_back_to_transcribe(edited_project, capsys):
    # 照第 1 期的樣子：拿掉 transcribe 和 trim 紀錄的 version，transcript.json 和 trim.json 的 version 改成 1。
    # status 的 next 是 "transcribe"；storyboard 結束碼 1，訊息是
    # "transcript.json was made by an older pipeline: run transcribe again"；
    # render 結束碼 1，訊息含 "transcribe is out of date"

# tests/test_edit.py
def test_require_valid_refuses_a_transcript_of_an_older_version(edited_project):
    # 把 transcript.json 的 version 改成 1 → PipelineError，訊息
    # "transcript.json was made by an older pipeline: run transcribe again"

# tests/test_trim.py
def test_trim_refuses_a_transcript_of_an_older_version(transcribed_project):
    # transcript.json 的 version 改成 1，trim(force=True) → PipelineError，同一則訊息
```

- [ ] **Step 2: 跑這些測試，確認因為缺少版本檢查而失敗**

Run: `uv run pytest tests/test_project.py tests/test_edit.py tests/test_trim.py -q -k "version or phase1"`

- [ ] **Step 3: 實作上面 Interfaces 列的行為**

`FORMAT_VERSIONS` 放在 `project.py`，因為 `align` 和 `trim` 都 import `project`，反過來會循環。

- [ ] **Step 4: 跑整套測試，再 commit（見共用步驟）**

---

### Task 2: 對齊的分類規則

**Files:**
- Modify: `pipeline/align.py`（`FILLER_SPELLINGS`、`classify_run`、`_runs`、`build_transcript`）
- Test: `tests/test_align.py`

**Interfaces:**
- Produces:
  - `align.FILLER_SPELLINGS = FILLERS + ("額", "誒", "恩")`；`textnorm.FILLERS` 不變。
  - `align.HESITATIONS: tuple[str, ...]`：`FILLER_SPELLINGS` 裡一個字的寫法，也就是 嗯、呃、欸、額、誒、恩。
  - `align.COMMAND_PHRASES = ("不對", "重來", "再來一次", "再一次", "重講", "我再講一次", "重新講")`
  - `align.command_phrase(text: str) -> str | None`：去掉結尾的空白和 `FILLER_SPELLINGS` 的詞（重複去到去不掉為止），回傳結尾相符的最長口令；沒有就 None。
  - `classify_run(run_keys, prev_keys, next_keys, prev_sentence, next_sentence, run_text=None, unit_texts: Sequence[str] | None = None) -> tuple[str, float]`
    - `unit_texts` 是每個辨識單位的台灣用字，長度等於 `run_keys`。給了它而沒給 `run_text` 時，`run_text = "".join(unit_texts)`。
    - 規則依序，第一條成立的決定：
      1. filler：和現在一樣，看整段的 `run_text`。
      2. retake（口令）：`next_keys` 不是空的，而且 `command_phrase(去掉猶豫聲後的文字)` 不是 None。
      3. retake（相似度）、4. repeat、5. reworded、6. extra：和現在一樣，但用去掉猶豫聲的 keys 計算。猶豫聲是 `unit_texts` 裡值在 `HESITATIONS` 的單位。
    - repeat 多一種成立的情況（口吃）：去掉猶豫聲後的 run 由 `next_keys[:p]` 重複一次以上組成，p 是 1 或 2。
    - 回傳的相似度用去掉猶豫聲的 keys 算。
  - `_runs` 呼叫 `classify_run` 時傳入 `unit_texts=texts[a:b]`。
  - `transcript.json` 每個句子多一個欄位 `"script_text"`：腳本裡這一句的原文（`Sentence.text`），兩種 `display_source` 都有。

- [ ] **Step 1: 寫失敗的測試**

```python
def test_en_is_a_filler():
    t = transcript_of("今天來聊剪輯。", asr="恩今天來聊剪輯")
    assert [(r["text"], r["kind"]) for r in t["runs"]] == [("恩", "filler")]

@pytest.mark.parametrize("script,asr,run", [
    ("我們今天來聊剪輯。", "我我我們今天來聊剪輯", "我我"),
    ("剪輯其實不難。", "剪剪剪輯其實不難", "剪剪"),
    ("我們今天來聊剪輯。", "我呃我們今天來聊剪輯", "我呃"),  # 猶豫聲不擋口吃
])
def test_a_stutter_is_a_repeat(script, asr, run):
    t = transcript_of(script, asr=asr)
    assert [(r["text"], r["kind"]) for r in t["runs"]] == [(run, "repeat")]

def test_a_hesitation_does_not_lower_the_similarity_of_a_retake():
    script = "第一個方法是先把停頓剪掉。第三個方法是先把重講剪掉。"
    t = transcript_of(script, asr="第一個方法是先把停頓剪掉第三個呃方法第三個方法是先把重講剪掉")
    assert [r["kind"] for r in t["runs"]] == ["retake"]

@pytest.mark.parametrize("said", ["不對重來", "不對重來呃", "我再講一次", "重新講"])
def test_a_run_that_ends_with_a_command_before_the_script_is_a_retake(said):
    script = "第一個方法是先把停頓剪掉。第二個方法是先把贅字剪掉。"
    t = transcript_of(script, asr=f"第一個方法是先把停頓剪掉第二個做法{said}第二個方法是先把贅字剪掉")
    assert [r["kind"] for r in t["runs"]] == ["retake"]

def test_wait_a_moment_is_not_a_command():
    t = transcript_of("第一句話。第二句話。", asr="第一句話等一下第二句話")
    assert [r["kind"] for r in t["runs"]] == ["extra"]

def test_a_command_after_the_last_sentence_is_not_a_retake():
    t = transcript_of("第一句話。", asr="第一句話不對重來")
    assert [r["kind"] for r in t["runs"]] != ["retake"]

def test_command_phrase():
    assert command_phrase("第二個做法不對重來") == "重來"
    assert command_phrase("我再講一次呃") == "我再講一次"
    assert command_phrase("這樣不對嗎") is None

def test_sentences_record_their_script_text():
    t = transcript_of("這個方法可以省下時間。最後記得追蹤我。", asr="这招能帮你省很多事最后记得追踪我")
    assert [s["script_text"] for s in t["sentences"]] == ["這個方法可以省下時間。", "最後記得追蹤我。"]
```

`script_text` 的值以 `parse_script` 給的 `Sentence.text` 為準；如果它不含句末標點，測試照實際值改寫。

- [ ] **Step 2: 跑測試，確認失敗**

Run: `uv run pytest tests/test_align.py -q`

- [ ] **Step 3: 實作上面 Interfaces 列的行為**

- [ ] **Step 4: 跑整套測試，再 commit**

---

### Task 3: 粗剪候選

**Files:**
- Modify: `pipeline/trim.py`
- Test: `tests/test_trim.py`

**Interfaces:**
- Consumes: Task 2 的 `HESITATIONS`、`command_phrase`；`transcript.json` 的 runs、`display_source`。
- Produces（`trim.json` 每個候選的欄位，依序）：`id, kind, start, end, sentence, text, reason, suggest, length, needs_listen`
  - `kind`：`filler`、`repeat`、`retake`、`extra`、`pause`。
  - `length`：候選裡不是猶豫聲的辨識單位數（一個中文字、一個英文字各算 1）；停頓是 0。
  - extra 的 run 變成候選：`kind` 是 `"extra"`，`suggest` 是 `"list"`，`reason` 是 `f"extra speech {text}"`。
  - 下面兩種 extra run 不產生候選：
    - 整段在第一個對到腳本的辨識單位之前，或最後一個之後（「對到」是 token 的 `s` 不是 None，和 `_pauses` 的定義相同）；
    - 屬於 `display_source` 是 `"asr"` 的句子。
  - 猶豫聲候選：extra 和 reworded 的 run（不屬於 `display_source` 是 `"asr"` 的句子）裡，每一個文字在 `HESITATIONS` 的辨識單位，各自產生一個候選。
    - `kind` 是 `"filler"`，`suggest` 是 `"cut"`，`reason` 是 `f"filler {text}"`，`length` 是 0。
    - 切點和 run 候選的算法相同：前後空隙的中點，經過 `snap`。
    - 它落在 run 的候選裡面；現在「落在前一個候選裡面就丟掉」的規則不適用於它。
  - 口令造成的 retake，`reason` 是 `f"earlier take of {sentence} ended with {phrase}"`。
  - 停頓：
    - 只扣掉 `suggest` 是 `"cut"` 的候選所佔的時間；`suggest` 是 `"list"` 的候選裡的停頓照樣產生。
    - `PAUSE_MIN_PIECE_S = 0.1`：扣完之後剩下最長的一段短於 0.1 秒（容許 `_EPSILON`）時，不產生。
  - 候選的排序和編號照舊：依 `(start, end)` 排序，編成 `t01`、`t02`…

**移除的行為：** `_RUN_TRIMS` 上面「extra runs give no trim」的說明和對應的測試。

- [ ] **Step 1: 寫失敗的測試**

```python
def test_extra_speech_is_a_listed_candidate():
    tr = trims_for("第一句話。第二句話。", asr="第一句話這個真的很重要第二句話")
    extra = [t for t in tr["trims"] if t["kind"] == "extra"]
    assert [(t["text"], t["suggest"], t["length"]) for t in extra] == [("這個真的很重要", "list", 7)]

def test_extra_speech_before_the_first_or_after_the_last_matched_unit_is_no_candidate():
    tr = trims_for("第一句話。", asr="大家好第一句話謝謝收看")
    assert [t for t in tr["trims"] if t["kind"] == "extra"] == []

def test_extra_speech_in_a_sentence_shown_as_recognised_gives_no_candidate_at_all():
    # 腳本和講的差很多（display_source 是 "asr"），句子中間有「這個呃」：沒有 extra 候選，也沒有猶豫聲候選

@pytest.mark.parametrize("asr,where", [
    ("第一句話呃這個很重要第二句話", "start"),
    ("第一句話這個呃很重要第二句話", "middle"),
    ("第一句話這個很重要呃第二句話", "end"),
])
def test_a_hesitation_inside_extra_speech_is_its_own_cut_inside_it(asr, where):
    tr = trims_for("第一句話。第二句話。", asr=asr)
    extra = next(t for t in tr["trims"] if t["kind"] == "extra")
    filler = next(t for t in tr["trims"] if t["kind"] == "filler")
    assert filler["suggest"] == "cut" and extra["start"] <= filler["start"] < filler["end"] <= extra["end"]
    assert extra["length"] == 5

def test_a_hesitation_inside_a_reworded_take_is_its_own_cut():

def test_an_ng_take_keeps_its_hesitations_inside_one_cut():
    # retake 裡的「呃」不另外成為候選

def test_a_command_retake_names_the_command():
    # reason 以 "ended with 重來" 結尾

def test_a_pause_inside_kept_extra_speech_is_still_shortened():
    # 腳本外的話中間有 1.2 秒的空隙：有一個 pause 候選，落在 extra 候選裡面

def test_a_pause_left_shorter_than_a_tenth_of_a_second_is_no_candidate():
    # 停頓被旁邊的 run 候選扣到只剩 2 格（0.0667 秒）：沒有 pause 候選；剩 3 格（0.1 秒）的照樣有

def test_every_candidate_has_a_length():
```

時間要精確控制的測試（停頓、0.1 秒），用 `transcript_and_trims` 傳 `(text, start, end)` 清單給 `fake_asr`，或照 `tests/test_trim.py:284` 手寫 transcript 文件。

- [ ] **Step 2: 跑測試，確認失敗**

Run: `uv run pytest tests/test_trim.py -q`

- [ ] **Step 3: 實作上面 Interfaces 列的行為**

- [ ] **Step 4: 跑整套測試，再 commit**

---

### Task 4: 繁體斷詞

**Files:**
- Create: `pipeline/words.py`、`pipeline/caption_words.txt`、`tests/test_words.py`
- Modify: `pipeline/assets.py`（下載、`doctor`）、`pipeline/cli.py`（`setup`）、`pyproject.toml`、`uv.lock`、`THIRD_PARTY.md`、`tests/helpers.py`（`require_assets`）
- Test: `tests/test_assets.py`、`tests/test_cli.py`、`tests/test_words.py`

**Interfaces:**
- Produces:
  - `pyproject.toml` 加入 `"jieba==0.42.1"`，用 `uv add` 更新 `uv.lock`。
  - `assets.DICT_FILENAME = "jieba-dict.txt.big"`
  - `assets.DICT_URL = "https://raw.githubusercontent.com/fxsjy/jieba/237dc6625e5c65d7a2714ffdfa5238dba5cae7d4/extra_dict/dict.txt.big"`
  - `assets.DICT_SHA256 = "b16011275c42955ccd81fc1adecc93a59dbb7926af69d93fc95d4943d40f6aad"`
  - `assets.dictionary_path() -> Path`：`models_dir() / DICT_FILENAME`
  - `assets.setup_dictionary(dest: Path, download=_urlopen) -> Path`：用 `fetch`
  - `assets.dictionary_ok() -> bool`：檔案存在，而且 sha256 相符
  - `doctor()`：
    - 多一個必要項目 `"caption_dictionary": bool`；
    - 缺字型、模型或字典時，警告改成 `"fonts, face model or caption dictionary missing: run `uv run pipeline setup`"`。
  - `pipeline setup` 的結果多一個 `"dictionary": str(path)`。
  - `words.WORD_FREQ = 2000`；`words.WORDS_FILE = Path(__file__).with_name("caption_words.txt")`。詞表一行一個詞，空行略過，初始內容是 上傳、自媒體、按讚、觸及率、CP值。
  - `words.word_ends(text: str) -> frozenset[int]`：jieba 斷出的每個詞的結束位置（字元索引，0 < i < len(text)）。
    - 字典不在時，丟 `PipelineError("caption dictionary not found: run `uv run pipeline setup`")`。
    - Tokenizer 用 `jieba.Tokenizer(dictionary=dictionary_path())`，`tmp_dir = models_dir()`，`cache_file = "jieba-dict.cache"`，再對詞表的每個詞 `add_word(word, freq=WORD_FREQ)`。
    - Tokenizer 用 `functools.lru_cache` 快取，鍵是字典和詞表的 (路徑, mtime_ns)。
    - `import jieba` 包在 `warnings.catch_warnings()` 裡，忽略 `SyntaxWarning`；`jieba.setLogLevel(logging.WARNING)`。
  - `tests/helpers.py` 的 `caption_dictionary() -> Path`：字典不在就讓測試失敗（不是跳過），訊息說要跑 setup。`require_assets()` 也檢查它。
  - `THIRD_PARTY.md`：
    - Python 套件表加 jieba 0.42.1（MIT）；
    - 新增「### 字典」小節，列 `dict.txt.big`（MIT，同一個儲存庫，commit `237dc66`，用途：字幕斷行的繁體字典），註明 README 沒有說明詞表的來源，見實測文件；
    - 「模型」小節開頭那段的 `setup` 說明加上字典。

- [ ] **Step 1: 寫失敗的測試**

```python
# tests/test_words.py
def test_word_ends_keep_taiwan_and_creator_words_whole():
    caption_dictionary()
    for text, words in [
        ("把影片上傳到Instagram以後記得加上標籤", ["上傳", "記得", "標籤"]),
        ("其實自媒體經營最重要的是持續產出", ["自媒體", "經營"]),
        ("記得按讚追蹤並且分享給需要的朋友", ["按讚", "追蹤", "並且"]),
        ("用手機錄影時記得開啟飛航模式", ["手機", "錄影", "飛航"]),
        ("我馬上傳給你", ["馬上"]),
    ]:
        ends = word_ends(text)
        for word in words:
            at = text.index(word)
            assert not any(at < i < at + len(word) for i in ends), (text, word)

def test_word_ends_put_latin_words_and_numbers_whole():
    text = "我用iPhone 15 Pro拍了3.5小時"
    ends = word_ends(text)
    for word in ["iPhone", "15", "Pro", "3.5"]:
        at = text.index(word)
        assert not any(at < i < at + len(word) for i in ends)

def test_word_ends_without_the_dictionary_says_run_setup(monkeypatch, tmp_path):
    # dictionary_path 指到不存在的檔案 → PipelineError，訊息含 "run `uv run pipeline setup`"

def test_jieba_writes_no_file_outside_the_models_folder_and_nothing_to_the_terminal(capfd):
    # 清掉快取後呼叫 word_ends：capfd 的 out 和 err 都是空的；models_dir() 裡有 jieba-dict.cache

# tests/test_assets.py
def test_setup_dictionary_checks_the_sha256(tmp_path):
    # 假的 download 回傳錯的內容 → PipelineError，什麼都沒寫

def test_doctor_without_the_caption_dictionary_is_not_ok(monkeypatch):
```

- [ ] **Step 2: 跑測試，確認失敗**

Run: `uv run pytest tests/test_words.py tests/test_assets.py tests/test_cli.py -q`

- [ ] **Step 3: `uv add jieba==0.42.1`，再實作上面 Interfaces 列的行為；跑 `uv run pipeline setup` 下載字典**

- [ ] **Step 4: 跑 `uv run python scripts/third_party.py --check`，預期結束碼 0**

- [ ] **Step 5: 跑整套測試，再 commit（含 `uv.lock`）**

---

### Task 5: 字幕斷行

**Files:**
- Modify: `pipeline/captions.py`（`Piece`、`Line`、`_kind`/`_tokens`、斷行、`_row`）
- Test: `tests/test_captions.py`

**Interfaces:**
- Consumes: Task 4 的 `words.word_ends`。
- Produces:
  - `Piece(text, unit, part: tuple[float, float] = (0.0, 1.0))`：`part` 是這一塊佔它那個單位時間的比例。單位沒被拆開時是 (0, 1)。
  - `Line(pieces, split_word: bool = False)`：這一行的開頭或結尾落在被拆開的詞裡時是 True。
  - `captions.LONG_WORD_WARNING = "long word split across caption lines"`
  - `break_lines(text, units, font, style) -> list[Line]`，簽名不變。
  - 半形標點（只限不屬於任何單位的字元）：
    - `,` `;` `:` 和 `，` `；` `：` 一樣，算 `"sep"`。
    - `.` 也算 `"sep"`，兩種例外算 `"other"`：前後兩個字元都是 ASCII 字母或數字；或前一個、後一個字元也是 `.`。
  - 可以斷開的位置：
    - 斷句標點後面：和現在 `_phrases` 切片語的位置相同，稱為「標點斷點」；
    - 其他位置：`_can_cut` 成立，而且隔著空白，或右邊那個 token 在原文的起點屬於 `word_ends(text)`。`_Tok` 多一個欄位 `pos: int`，記它在 `text` 裡的起點。
  - 從所有可斷的位置裡，依序挑最好的一種斷法（動態規劃，要算出真正最好的那一種）：
    1. 行數最少；
    2. 不在標點斷點斷開的次數最少；
    3. 短行最少。短行是 `width_units(行) <= 2` 的行；整句 `width_units <= 2` 時不算；
    4. 各行像素寬度和平均寬度之差的平方和最小。
  - 太寬的一段：兩個相鄰可斷位置之間的內容，單獨一行也放不下時，在字元之間拆成放得下的最少幾段，長度盡量平均。
    - 拆開的位置當成可斷的位置（不算標點斷點）；
    - 一個單位被拆成幾塊時，每塊的 `unit` 相同，`part` 依字元數按比例分配；
    - 含拆開位置的行，`split_word` 是 True。
  - `_row` 和 `build_events`：
    - 一塊的開始時間是 `單位 start + part[0] × (單位 end − 單位 start)` 換算到成品時間；
    - 行尾時間用最後一塊的 `part[1]` 算。

**移除的行為：** 從前面一行一行塞片語（`break_lines` 的貪婪迴圈）和 `_balanced`。模組說明照新規則改寫。

- [ ] **Step 1: 寫失敗的測試**

用 `monkeypatch.setattr("pipeline.captions.word_ends", lambda text: frozenset(...))` 控制詞的邊界，讓斷法的答案固定；另有一個測試用真的字典。

```python
def test_fewest_lines_first():
    assert [l.text for l in line_of("今天來聊剪輯，也聊一下拍片技巧")] == ["今天來聊剪輯　也聊一下拍片技巧"]

def test_a_punctuation_break_beats_a_word_break():
    assert [l.text for l in line_of("第一步先把腳本寫好，再開始錄影跟剪輯影片")] == ["第一步先把腳本寫好", "再開始錄影跟剪輯影片"]

def test_a_line_of_two_characters_is_avoided_when_another_break_ties_on_lines_and_punctuation(monkeypatch):
    # 只給兩個可斷位置：一個造成 2 字短行、另一個不會，兩者行數和標點斷點數相同 → 選不會的那個，
    # 即使它的平方和比較大

def test_the_most_even_break_wins_the_rest(monkeypatch):

def test_lines_break_only_where_a_word_ends():
    caption_dictionary()
    text = "很多新手會犯的錯誤就是一開始就買太貴的器材然後就放著不用"
    lines = line_of(text)
    # 每一行的結尾位置（在 text 裡）都屬於 word_ends(text)

def test_a_too_long_latin_word_is_split_into_even_lines():
    lines = line_of("網址是https://example.com/a/very/long/path/to/one/page/of/the/site")
    # 每行都放得下；URL 被拆成的幾行 split_word 都是 True；同一個 unit 的各塊 part 首尾相接、加總 1

def test_a_cjk_stretch_without_word_boundaries_longer_than_a_line_is_split(monkeypatch):
    # word_ends 回傳空集合，句子 20 個中文字、沒有標點 → 兩行，各 10 字，split_word 為 True

def test_karaoke_of_a_split_word_adds_up():
    # 對被拆開的 URL 做 build_events + _karaoke：每個 \k 值 >= 0，加總等於事件長度

def test_split_word_times_follow_the_parts():
    # 第二行的開始時間 = 單位 start + 第二塊 part[0] × 單位長度（換算到成品時間）

@pytest.mark.parametrize("text,lines", [
    ("第一, 第二; 第三: 第四. 結束", ["第一　第二　第三　第四　結束"]),
    ("價格是3.5元", ["價格是3.5元"]),
    ("用Node.js寫", ["用Node.js寫"]),
    ("然後呢...", ["然後呢..."]),
])
def test_half_width_punctuation(text, lines):
    assert [l.text for l in line_of(text)] == lines
```

半形標點那組的預期值，以實際的空白和分隔字規則為準（例如 `, ` 後面的空白和逗號合併成一個全形空白）。如果實際值不同，先確認是規則造成的，再改測試的預期值，並在 commit 訊息說明。

- [ ] **Step 2: 跑測試，確認失敗**

Run: `uv run pytest tests/test_captions.py -q`

- [ ] **Step 3: 實作上面 Interfaces 列的行為**

- [ ] **Step 4: 跑整套測試，再 commit**

---

### Task 6: 字幕位置

**Files:**
- Modify: `styles/default/style.json`、`pipeline/style.py`、`pipeline/captions.py`（`caption_top_px`、刪掉兩個臉的警告常數和 `FACE_PERCENTILE`）、`pipeline/storyboard.py`（不讀 `faces.json`）、`pipeline/render.py`（不讀 `faces.json`）、`pipeline/prep.py:4` 的說明、`THIRD_PARTY.md` 的 YuNet 用途
- Test: `tests/test_captions.py`、`tests/test_storyboard.py`、`tests/test_render.py`、`tests/test_style.py`（新增，或併進現有的 style 測試）

**Interfaces:**
- Produces:
  - `style.json` 的 `captions`：刪掉 `default_top_px`、`face_margin_px`，加上 `"bottom_px": 1248`。
  - `load_style` 檢查：`bottom_px - line_height_px >= safe_area.y0`，而且 `bottom_px <= safe_area.y1`。不符時丟 `PipelineError(f"style {name}: the caption line box (bottom_px {b}, line_height_px {h}) is not inside the safe area")`。
  - `caption_top_px(style: dict) -> int`：`bottom_px - line_height_px`；預設風格是 1158。
  - `storyboard` 和 `render` 的步驟輸入拿掉 `"faces"`；`prep` 照樣寫 `faces.json`。
  - YuNet 的用途改成「偵測臉的位置（第 2 期的畫面推近要用）」；`prep.py` 說明裡的 faces.json 用途同樣改寫。

**移除的行為：** 字幕跟著臉移動；警告 `no face detected`、`captions overlap face` 和它們的中文字樣；`tests/test_captions.py` 裡 `caption_top_px` 依臉位置計算的測試。

- [ ] **Step 1: 寫失敗的測試**

```python
def test_caption_top_is_bottom_px_less_the_line_height():
    assert caption_top_px(load_style()) == 1158

def test_a_style_whose_caption_box_leaves_the_safe_area_is_refused(tmp_path, monkeypatch):
    # 複製 default 風格到暫存的 styles 根目錄，把 bottom_px 改成 1300 → PipelineError

def test_storyboard_and_render_do_not_read_faces_json(edited_project):
    # 刪掉 faces.json：storyboard 照樣做出網頁，警告裡沒有和臉有關的字樣

def test_changing_faces_json_does_not_make_the_storyboard_stale(edited_project):
    # 做完 storyboard 後改寫 faces.json：再跑 storyboard 是 skipped
```

- [ ] **Step 2: 跑測試，確認失敗**

- [ ] **Step 3: 實作上面 Interfaces 列的行為**

- [ ] **Step 4: 跑整套測試，再 commit**

---

### Task 7: 成品內判斷和關卡清單

**Files:**
- Modify: `pipeline/edit.py`（新增 `in_reel`）
- Create: `pipeline/review_items.py`、`tests/test_review_items.py`
- Modify: `pipeline/storyboard.py`（`_Edge`、`_cut_edges` 和三個 PAD 常數搬到 `review_items`，storyboard 改成 import）

**Interfaces:**
- Consumes: Task 2 的 `script_text`；Task 3 的 `kind "extra"` 和 `length`。
- Produces:
  - `edit.in_reel(start: float, end: float, segments: list[Segment]) -> bool`：`[to_frame(start), to_frame(end))` 和某一段的格子有重疊。
  - `review_items.EXTRA_ASK_MIN = 3`
  - `review_items.LISTEN_PAD_S = 0.3`、`EDGE_PAD_S = 1.0`、`TRIM_PAD_S = 1.0`（從 storyboard 搬來，值不變）

```python
@dataclass
class Item:
    number: int                  # 兩個清單共用的編號，從 1 開始
    kind: str                    # 要決定："asr_text"、"reworded"、"extra"；要聽："cut_point"、"trim_cut_point"、"low_match"
    time: float                  # 來源時間，排序用
    sentence: str | None         # 相關的句子 id
    trim: str | None             # 相關的候選 id
    text: str                    # 講了什麼：候選的 text，或句子的 text
    label: str                   # cut_point 的 _Edge.label；其他種類是空字串
    cut: bool | None             # 候選類的項目：edit.json 是否接受；其他是 None
    clip: tuple[float, float]    # 播放片段的來源時間

def review_items(edit: dict, transcript: dict, trims: dict, segments: list[Segment]) -> tuple[list[Item], list[Item]]:
    """(要決定的項目, 其他要聽的地方)."""
```

- 要決定的項目，只列在成品裡的：
  - `asr_text`：保留的句子，`display_source` 是 `"asr"`。`time` 是句子的 start，片段是句子前後各 `LISTEN_PAD_S`。
  - `reworded`：`kind` 是 `"retake"`、`suggest` 是 `"list"` 的候選，`in_reel` 為真。片段是候選前後各 `TRIM_PAD_S`。
  - `extra`：`kind` 是 `"extra"`、`length >= EXTRA_ASK_MIN` 的候選，`in_reel` 為真。片段同上。
- 其他要聽的地方：
  - `cut_point`：`_cut_edges` 給的每個切點，它所屬句子的 `bounds.needs_listen` 為真時，一個切點一項（片頭和片尾各一項）。片段是切點前後各 `EDGE_PAD_S`。
  - `trim_cut_point`：`edit.json` 接受、`in_reel` 為真、`needs_listen` 為真的候選。片段是候選前後各 `TRIM_PAD_S`。
  - `low_match`：保留的句子，`reasons` 有 `"low_match"`、`display_source` 是 `"script"`。片段是句子前後各 `LISTEN_PAD_S`。
- 兩個清單各自依 `time` 排序（相同時依種類的列出順序）；先編要決定的 1..M，再接著編要聽的。

- [ ] **Step 1: 寫失敗的測試（`tests/test_review_items.py`，用 `transcript_and_trims` 和 `default_edit` 造文件）**

```python
def test_in_reel():
    segments = [Segment(1.0, 2.0), Segment(3.0, 4.0)]
    assert in_reel(1.5, 1.6, segments) and not in_reel(2.0, 3.0, segments) and in_reel(2.5, 3.1, segments)

def test_a_sentence_shown_as_recognised_is_a_decision():
def test_a_reworded_take_in_the_reel_is_a_decision():
def test_extra_speech_of_three_units_is_a_decision_and_of_two_is_not():
def test_a_candidate_outside_the_reel_is_no_item():
    # 被 cuts.extra 拿掉的句子裡的腳本外的話；第一句之前的 NG：兩者都不在任何清單
def test_a_hesitation_inside_an_accepted_extra_cut_is_no_listen_item():
def test_head_and_tail_cut_points_of_the_only_kept_sentence_are_two_items():
    # labels 是 "片頭切點" 和 "片尾切點"
def test_a_low_match_sentence_with_script_captions_is_a_listen_item():
def test_numbers_run_through_decisions_then_listen_items_in_time_order():
```

- [ ] **Step 2: 跑測試，確認失敗**

- [ ] **Step 3: 實作上面 Interfaces 列的行為**

- [ ] **Step 4: 跑整套測試，再 commit**

---

### Task 8: 分鏡稿網頁

**Files:**
- Modify: `pipeline/storyboard.py`
- Test: `tests/test_storyboard.py`

**Interfaces:**
- Consumes: Task 5 的 `Line.split_word`、`LONG_WORD_WARNING`；Task 6 拿掉臉之後的 `_plan`；Task 7 的 `review_items`、`Item`、`in_reel`。
- Produces:
  - `build_storyboard` 的結果：`needs_listen` 換成 `"decisions": int` 和 `"listen_points": int`，其他鍵不變。`cuts` 只算 `in_reel` 的已接受候選，加上 `cuts.extra` 的段數。
  - 摘要的五格：原長、新長、剪掉「N 段」、要你決定「M 項」、其他要聽「K 處」。
  - 警告：任何保留句子的任何一行 `split_word` 為真時，加上 `LONG_WORD_WARNING`，中文是「有詞太長，字幕從詞的中間斷開」。
  - 網頁的順序：標題、專案名、摘要、警告、「要你決定」清單、「其他要聽的地方」清單、句子表、候選表。
  - 每個清單項目是 `<li data-item="N">`，內容依序：
    - 編號、新時間（`clock(map_time(time))`）、種類的中文、`「text」`；
    - 候選類的項目再加「目前：剪掉」或「目前：保留」；
    - `asr_text` 再加兩行：「字幕：」加上各行用 ` / ` 接起來，「腳本：」加上 `script_text`；
    - `cut_point` 用 label 代替種類的中文；
    - 最後是播放器。
  - 種類的中文：
    - `asr_text` 字幕用辨識文字、`reworded` 改詞重講、`extra` 腳本外的話；
    - `trim_cut_point` 剪接處不在靜音裡、`low_match` 吻合度低。
  - 空清單時，分別顯示「沒有要你決定的事。」和「沒有其他要聽的地方。」
  - 句子表的「標記」欄：
    - 「沒講到」、「移除（原因）」；
    - 有 `split_word` 的行時加「有詞太長，從中間斷開」；
    - 提到這一句的項目，加「見第 N 項」（好幾項用頓號接起來）。
    - 句子列不再有播放器。
  - 候選表：
    - 種類的中文：filler 贅字、repeat 重講、retake 且 `suggest` 是 `cut` 的寫 NG 段、retake 且 `suggest` 是 `list` 的寫改詞重講、extra 腳本外的話、pause 停頓；
    - 「處理」欄寫剪掉或保留；
    - 不在成品裡的候選，標記欄寫「不在成品裡」；
    - 被清單提到的候選加「見第 N 項」；
    - 每列照舊有播放器。
  - 手機排版：
    - CSS 不設 `min-width`；
    - 每個 `<td>` 有 `data-label="<欄名>"`；
    - `@media (max-width: 640px)` 裡，表格的 `table, thead, tbody, tr, td` 改成 block，`thead` 隱藏，`td::before` 顯示 `attr(data-label)`。
  - `storyboard.md`：
    - 最上面依序是摘要、警告、`## 要你決定`、`## 其他要聽的地方`；
    - 兩個清單是 Markdown 編號清單，編號和網頁一致，內容和網頁的項目相同，但沒有播放器；
    - 接著是兩張表，欄位同網頁，句子表沒有縮圖欄。

**移除的行為：** 句子列的「請聽」、原因文字、整句片段和「腳本外的話」片段（`_extra_clips`）；候選列的「請聽」；結果的 `needs_listen`；`_REASON_TEXT` 裡的 `extra_speech`。`tests/test_storyboard.py` 裡測這些的測試（約 467–615、668–931 行），改寫成測清單項目，或刪除。

- [ ] **Step 1: 寫失敗的測試**

```python
def test_page_has_no_fixed_minimum_width_and_turns_rows_into_cards_on_a_phone(edited_project):
    html = page(edited_project)
    assert "min-width" not in html and "@media (max-width: 640px)" in html
    assert all('data-label="' in td for td in re.findall(r"<td[^>]*>", html))

def test_items_come_first_in_order_with_a_player_each(edited_project):
    # "要你決定" 和 "其他要聽的地方" 兩個標題出現在 "句子" 之前；每個 data-item 有一個 <audio>；
    # 編號從 1 連續

def test_md_lists_match_the_page_numbers(edited_project):
def test_a_candidate_outside_the_reel_says_so_and_is_not_counted(edited_project):
def test_result_counts_decisions_and_listen_points(edited_project):
def test_a_recognised_sentence_item_shows_captions_and_script(tmp_path):
def test_a_sentence_row_points_to_its_items(edited_project):
def test_long_word_warning_and_row_mark(tmp_path):
def test_no_items_says_so(tmp_path):
    # 腳本和講的完全一樣、切點都在靜音裡：兩段「沒有…」字樣，decisions 和 listen_points 都是 0
```

- [ ] **Step 2: 跑測試，確認失敗**

- [ ] **Step 3: 實作上面 Interfaces 列的行為；改寫或刪除「移除的行為」列的舊測試**

- [ ] **Step 4: 跑整套測試，再 commit**

---

### Task 9: 手機預覽版

**Files:**
- Modify: `pipeline/render.py`
- Test: `tests/test_render.py`

**Interfaces:**
- Produces:
  - 常數：
    - `PREVIEW_W, PREVIEW_H = 540, 960`
    - `PREVIEW_AUDIO_BITRATE = 96_000`
    - `PREVIEW_MAX_BYTES = 31_457_280`（30 MiB）
    - `PREVIEW_BUDGET = 0.9`
    - `PREVIEW_MAX_VIDEO_BITRATE = 2_000_000`
    - `PREVIEW_OVER = "preview over 30 MiB: not kept"`
  - `preview_bitrate(duration_s: float) -> int`：`min(PREVIEW_MAX_VIDEO_BITRATE, floor(PREVIEW_BUDGET * PREVIEW_MAX_BYTES * 8 / duration_s) - PREVIEW_AUDIO_BITRATE)`，捨去到整 kbit/s。
  - `build_preview_args(final: Path, out: Path, video_bitrate: int) -> list[str]`：
    - 輸入是 `final`，`-vf scale=540:960`、`-r 30`；
    - 影像編碼器和成品相同，`-b:v`，`-pix_fmt yuv420p`；
    - 聲音 `-c:a aac -b:a 96k -ac 2`，`-movflags +faststart`；輸出 `out`。
  - `make_preview(p: Project, duration_s: float, runner: Callable, force: bool = False) -> dict`：
    - 用 `run_step(p, "preview", {"final": out/final.mp4}, [out/preview.mp4], ...)`；
    - 真的要做時，先刪掉舊的 `out/preview.mp4`，編碼到 `out/preview.tmp.mp4`；
    - 超過 `PREVIEW_MAX_BYTES`：位元率改成 `int(rate * PREVIEW_MAX_BYTES / size * 0.95) // 1000 * 1000`，重做一次；
    - 還是超過：刪掉暫存檔，丟 `PipelineError(PREVIEW_OVER)`；
    - 成功時改名成 `preview.mp4`，回傳 `{"preview": str, "preview_mb": float, "preview_bitrate": int}`。
  - `render()`：
    - 成品這一步不論真的做了還是跳過，都接著呼叫 `make_preview`；
    - 結果多 `"preview"`（路徑，沒做成是 None）、`"preview_mb"`（做成時才有）和 `"warnings": list[str]`；
    - `make_preview` 的 `PipelineError` 不讓 `render` 失敗，訊息放進 `warnings`：`PREVIEW_OVER` 原樣，其他的寫成 `f"preview failed: {message}"`。

- [ ] **Step 1: 寫失敗的測試（用假的 runner，照現有 `tests/test_render.py` 的寫法）**

```python
def test_preview_bitrate():
    assert preview_bitrate(60.0) == 2_000_000
    assert preview_bitrate(223.0) == 919_000
    assert preview_bitrate(543.9) == 320_000

def test_preview_args_scale_to_540_by_960_with_aac_96k(tmp_path):
def test_render_makes_a_preview_next_to_the_final(approved_project):
def test_preview_is_encoded_again_once_when_it_is_too_big(approved_project):
    # 第一次寫 33_000_000 bytes，第二次 30_000_000：第二次的 -b:v 照公式；preview.mp4 存在
def test_a_preview_still_too_big_is_not_kept_and_the_render_succeeds(approved_project, capsys):
    # 兩次都超過：結束碼 0，final.mp4 存在，preview.mp4 不存在，warnings == [PREVIEW_OVER]
def test_an_ffmpeg_failure_of_the_preview_keeps_the_final(approved_project, capsys):
def test_an_unchanged_reel_makes_only_the_missing_preview(approved_project):
    # render 一次後刪掉 preview.mp4，再 render：runner 只被呼叫一次，參數是預覽版的
def test_a_new_final_makes_a_new_preview(approved_project):
```

- [ ] **Step 2: 跑測試，確認失敗**

- [ ] **Step 3: 實作上面 Interfaces 列的行為**

- [ ] **Step 4: 跑整套測試，再 commit**

---

### Task 10: skill 和文件

**Files:**
- Modify: `.claude/skills/reel/SKILL.md`、`.claude/skills/storyboard/SKILL.md`、`CLAUDE.md`、`FEATURE.md`、`docs/backlog/2026-11-12-phase1-review-items.md`、`docs/superpowers/specs/2026-11-11-reels-workflow-design.md`
- Test: `tests/test_docs.py`（現有的檢查要過；需要時加新的檢查）

**要寫的內容：**

- `reel` skill：
  - 第 8 節照設計第 6 節改寫：
    - 一則訊息的寫法，附一個自己編的例子；
    - 「可以」就 `approve` 加 `render`；
    - 只回要改的項目時怎麼改；
    - 錯字留在成品裡要明講；
    - 其他要聽的地方不逐項問。
  - 讀 `storyboard.md` 最上面的兩個清單來寫訊息；結果的 `decisions`、`listen_points` 怎麼轉述。
  - 「腳本外的話在哪裡」那張表只留「這一句的字幕是辨識出來的」那一列，其他位置改成：腳本外的話已經是候選，照候選處理。
  - 警告對照表：
    - 刪掉兩則和臉有關的；
    - 加上 `long word split across caption lines`（有詞太長，字幕從詞的中間斷開）；
    - 加上預覽版的兩種警告：`preview over 30 MiB: not kept`，和以 `preview failed:` 開頭的。
  - 第 9 節：
    - 輸出後講兩個檔案的用途：`final.mp4` 拿來發布，`preview.mp4` 只用來看；
    - 使用者用 Remote Control 時，用 SendUserFile 傳 `preview.mp4`。
  - `setup`、`doctor` 的段落加上斷詞字典（`caption_dictionary`）。
  - 舊專案：`status` 指向 `transcribe` 時，說明是程式改版，重跑之後要重新核對 `edit.json`。
- `storyboard` skill：
  - 腳本外的話的候選怎麼決定。
  - 寫 `edit.json` 時先照建議填好：
    - 改詞重講：只是下一句講到一半的舊版本就接受（剪掉），有下一句沒講到的內容就拒絕；
    - 腳本外的話：預設拒絕（保留），明顯是口誤的接受；
    - 猶豫聲、NG、重複和口令 NG：接受。
  - 字幕用辨識文字的句子：怎麼對照 `script_text` 找可能的錯字。
- `CLAUDE.md`：開發段落的 `pipeline setup` 說明加上斷詞字典；關卡段落改成「照 reel skill 傳一則訊息」。
- `FEATURE.md`：
  - 把「第 1 期改版（設計中）」的項目併進「已完成」各小節，刪掉「第 1 期改版」小節；
  - 更新最後更新日期；
  - 字幕位置那一行改成固定在下方置中。
- 待辦文件：
  - 表格第 18 行和 A1～A5、B5、B7、B12～B15 各條，標成已在第 1 期改版處理，附設計文件路徑；
  - H1 維持待辦。
- 原設計（2026-11-11）：在開頭的欄位下面加一行：「第 1 期改版取代了部分內容，見 `docs/superpowers/specs/2026-11-12-phase1-revision-design.md` 第 10 節。」

- [ ] **Step 1: 改寫上面列的文件**
- [ ] **Step 2: 跑 `uv run pytest tests/test_docs.py -q`，預期全過**
- [ ] **Step 3: 跑整套測試，再 commit**

---

### Task 11: 驗證（由 controller 自己做，不派 subagent）

- [ ] **Step 1：** 在 worktree 跑 `uv run pytest -q`，預期全過。
- [ ] **Step 2：** 用測試用的 Meijia 專案做一份分鏡稿，在 Chrome 打開，把視窗寬度縮到 390 px 截圖，確認：
  - 不用左右滑；
  - 兩個清單在最上面；
  - 表格變成卡片。
- [ ] **Step 3：** 用第 1 期驗收的素材在 `studio/` 開一個新專案，照 `reel` skill 走到分鏡稿，傳一則訊息給使用者，這是關卡 1，要使用者決定。不改原來那個專案。
- [ ] **Step 4：** 使用者同意後才 `approve` 和 `render`，量 `preview.mp4` 的大小，用 SendUserFile 傳給使用者。
- [ ] **Step 5：** 向使用者說明粗剪結果和上次不同的地方，以及為什麼。
