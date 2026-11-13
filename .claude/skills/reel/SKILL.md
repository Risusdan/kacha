---
name: reel
description: 把一支口播影片剪成有字幕的 Reels，帶著整個流程走：新增專案、前處理、轉錄、粗剪、寫 edit.json、關卡 1（審閱頁或分鏡稿訊息）、輸出 out/final.mp4。使用者說「剪 inbox 裡的…那支」、「繼續…那支」，或問某支影片剪到哪裡時使用。
---

# reel：從素材到 final.mp4

你是導演：用中文和使用者談，用 `pipeline` 指令做事，資料檔只寫 `work/edit.json`（寫法見 `storyboard` skill；審閱頁也會寫它，見第 8 節）。對話規則和關卡規則在 `CLAUDE.md`。

指令都從 repo 根目錄用 `uv run pipeline <指令>` 執行。輸出是一行英文 JSON：

- 成功：`"ok": true`，加上這個指令的結果。
- 失敗：`"ok": false`，`error` 是原因（`doctor` 例外，見第 1 節），有些還有 `log`（記錄檔的路徑）。結束碼 1 是執行錯誤，2 是 `edit.json` 檢查不通過（問題列在 `errors`）。
- `review` 例外：它會一直跑，開始提供審閱頁時印第一行，停下來時才印第二行（見第 8 節）。

下面的 `<專案>` 是專案資料夾的名字，例如 `2026-11-12-coffee`；代稱（`coffee`）在只有一個專案叫這個名字時也行。

## 1. 決定下一步

開工先讀 `studio/brand/preferences.md`（有的話）。

- 使用者說「繼續…那支」：`uv run pipeline status` 列出所有專案和各自的下一步（`next`）。找出使用者說的那支，讀它的 `project.md`，再用 `uv run pipeline status <專案>` 看細節：`steps` 是已完成的步驟，`gate1_ok` 表示使用者同意的是現在這份分鏡稿（同一份 `edit.json`，做分鏡稿用到的 `transcript.json`、`trim.json` 等輸入也都沒變），而且 `prep`、`transcribe`、`trim` 都是最新的。
- 使用者說「剪 inbox 裡的…那支」：到 `studio/inbox/` 找影片和腳本檔（只讀）。找不到腳本，或有好幾個可能的檔案，就問使用者；沒有腳本不能開始。接著從 `new` 開始。

`status` 列出的某一項如果是 `name` 加 `error`、沒有 `next`，表示那個專案的 `state.json`（`pipeline` 記錄步驟和同意狀態的檔案）讀不出來，例如不是合法的 JSON，或缺了必要的欄位。其他專案照常列出，指令本身仍是成功（結束碼 0）；單獨用 `status <專案>` 問它，才會失敗（結束碼 1）。`state.json` 你不改、不刪，也不自己重建：用中文告訴使用者是哪個專案、`error` 說了什麼，問他要怎麼處理。

`next` 的值和要做的事：

| `next` | 做什麼 |
|---|---|
| `prep` | 第 3 節 |
| `transcribe` | 第 4 節 |
| `trim` | 第 5 節 |
| `edit` | 第 6 節。還沒有 `work/edit.json`：寫一份，再 `validate`。已經有：它是對舊的 `transcript.json` 或 `trim.json` 檢查通過的，那兩個檔案後來變了，要逐一重新核對，再 `validate` |
| `validate` | 第 6 節：`edit.json` 還沒檢查過，或檢查之後又改了，跑 `validate`。`transcribe` 或 `trim` 在 `edit.json` 寫好之後重跑過的話，先逐一重新核對，再 `validate` |
| `storyboard` | 第 7 節 |
| `approve` | 第 8 節：等使用者同意，才能 `approve` |
| `render` | 第 9 節。審閱頁開著時，先看第 8 節「審閱頁開著的時候」：頁面可能正在輸出 |
| `done` | 把 `out/final.mp4` 的位置告訴使用者 |

一個步驟的輸入檔沒變就會跳過（結果有 `"skipped": true`）。影片或腳本檔的內容變了，`status` 會自動回到受影響的步驟。要強制重跑，加 `--force`。

舊專案：程式改版時，`transcript.json` 和 `trim.json` 的格式也可能跟著改版。用舊版做出來的專案，即使原本已經做到後面，`status` 的 `next` 也會指向 `transcribe`，直接執行 `transcribe` 也不會跳過。這是程式改版，不是素材或腳本變了。先用中文跟使用者說明：這支是舊版做的，要從 `transcribe` 重跑，已經輸出的 `final.mp4` 不受影響，但新的分鏡稿要重新看、重新同意。然後照 `next` 從 `transcribe` 往下重跑；重跑之後候選代號會變，`edit.json` 要照第 6 節逐一重新核對，再 `validate`。

第一次使用，或指令說缺字型、缺模型、缺字典時：`uv run pipeline doctor` 列出缺什麼。任何一項不通過，`ok` 就是 `false`，結束碼是 1。這時輸出裡沒有 `error`，要看下面各個欄位和 `warnings`。

- 缺字幕字型、臉部偵測模型或斷詞字典（`fonts`、`yunet` 或 `caption_dictionary` 是 `false`）：先告訴使用者，再跑 `uv run pipeline setup`。`setup` 只下載這三樣；它的結果有 `fonts`、`yunet` 和 `dictionary`（字典檔的路徑）。斷詞是把一句話切成一個個詞：字幕斷行只在詞和詞之間或標點後面，靠斷詞字典找詞的邊界。缺字典時，`storyboard` 和 `render` 會拒絕（`trim` 有時也會），訊息叫你跑 `setup`。臉部偵測模型是 `prep` 偵測臉用的，結果留給第 2 期的畫面推近，本期的字幕不看臉。
- 缺語音辨識（ASR，automatic speech recognition）模型（`asr_models` 裡的 `1.7b`、`0.6b` 或 `aligner` 是 `false`）：告訴使用者，然後停下來。`setup` 不下載它們，語音辨識執行時也不連網，你不要自己去下載。
- `asr_cli` 是 `false`：語音辨識的指令 `mlx-qwen3-asr` 不在專案的 Python 環境裡，`transcribe` 跑不起來。`setup` 不處理它。告訴使用者，經他同意再跑 `uv sync`（照 `uv.lock` 把環境補齊），再跑一次 `doctor`。
- `ffmpeg` 是 `null`：找不到 ffmpeg（`pipeline` 用來處理影片和音訊的工具，`warnings` 會說原因）。你不能補，告訴使用者，然後停下來。他裝好之後，如果不在 `pipeline` 預設找的位置，要用環境變數 `REELS_FFMPEG_DIR` 指到放 `ffmpeg` 的資料夾。`ffmpeg` 是 `null` 時，下面的 `libass`、`h264_videotoolbox`、`hevc_videotoolbox` 也都是 `false`（沒有 ffmpeg，這三項沒法檢查）：先處理 ffmpeg，再跑一次 `doctor`，那三項才有意義。
- `libass` 是 `false`：目前的 ffmpeg 沒有 libass（把字幕畫進影片的程式庫），`render` 做不出有字幕的成品。處理方式同上：告訴使用者換一個有 libass 的 ffmpeg，然後停下來。
- `h264_videotoolbox` 或 `hevc_videotoolbox` 是 `false`：目前的 ffmpeg 沒有這個硬體視訊編碼器（用機器內建的視訊晶片，把畫面壓成影片）。`render` 輸出 `final.mp4` 用 `h264_videotoolbox`；`hevc_videotoolbox` 是 `prep` 處理 HDR（高動態範圍）素材時用的，不是 HDR 的素材用不到它，但 `doctor` 兩個都檢查，所以只有 `hevc_videotoolbox` 是 `false` 時，`ok` 也是 `false`，結束碼也是 1。處理方式同上：告訴使用者，然後停下來，由他決定；不要因為手上的素材不是 HDR，就自己當作沒事往下跑。

## 2. 新增專案

```
uv run pipeline new <影片> <腳本> --slug <英文代稱>
```

- 代稱用英文小寫字母和數字，單一連字號連接，例如 `battery-tips`。資料夾名稱一律用英文。
- 日期預設是今天；要指定就加 `--date YYYY-MM-DD`。
- 結果的 `project` 是專案資料夾名，後面的指令都用它。影片和腳本只讀：專案只記下它們的路徑，不複製、不改。

## 3. 前處理

```
uv run pipeline prep <專案>
```

抽出音訊，也偵測臉的位置（記下來給第 2 期的畫面推近用；本期的字幕固定在畫面下方置中，不看臉）。結果有 `warnings`、`is_hdr`、`is_vfr`、`duration_s`。

- `aspect W:H will be center-cropped to 9:16`：告訴使用者畫面不是 9:16，會置中裁切成 9:16。
- `is_hdr` 為 `true`：素材是 HDR（高動態範圍）影片。前處理會多一道轉成 SDR（標準動態範圍）的步驟，比較久，先跟使用者說一聲。
- `is_vfr` 為 `true`：素材的幀率（每秒幾格）不固定，叫變動幀率（variable frame rate）。你不用處理，也不必告訴使用者：`render` 讀 `prep` 結果裡 `video_source` 指的影片（SDR 素材是原始影片，HDR 素材是前處理轉出的 SDR 副本 `work/video.mp4`），在濾鏡裡轉成每秒 30 格的固定幀率。

## 4. 轉錄

```
uv run pipeline transcribe <專案>
```

預設用 Qwen3-ASR 1.7B 模型，要一點時間（實測紀錄：189 秒的音訊約 64 秒），先告訴使用者。結果有：

- `sentences`（腳本幾句）、`spoken`（講者講了幾句）。
- `model` 和 `fallback_used`。**`model` 是 `Qwen/Qwen3-ASR-0.6B` 時，一定要告訴使用者**：不管是 1.7B 執行失敗、自動改用 0.6B（`fallback_used` 為 `true`），還是用 `--asr-model 0.6b` 指定的。0.6B 比較容易把字聽成同音字，字幕改用辨識文字的句子（分鏡稿上要使用者決定的項目）可能變多。

## 5. 粗剪候選

```
uv run pipeline trim <專案>
```

找出贅字、口吃和其他重複、NG 段、腳本外的話和過長的停頓，當作候選。結果有 `trims`（候選數）、`by_kind`（各種類的數量）、`suggest_cut`（建議剪的數量）和 `warnings`。每種候選怎麼決定，見 `storyboard` skill。要使用者決定或聽的地方，以第 7 節 `storyboard` 做出來的兩個清單為準，不看 `transcribe` 和 `trim` 結果裡的 `needs_listen` 計數。

## 6. 寫 edit.json 並檢查

依 `storyboard` skill 寫 `work/edit.json`，再跑：

```
uv run pipeline validate <專案>
```

不通過（結束碼 2）就照 `errors` 改，直到通過。通過的結果有 `segments`、`duration_s`（剪完的長度，秒）、`kept_sentences`。

`validate` 通過時，`pipeline` 會記下它檢查的是哪個 `edit.json`、哪個 `transcript.json`、哪個 `trim.json`。`next` 和 `storyboard` 都拿這份紀錄和現在的三個檔案比：

- `next` 是 `validate`：`edit.json` 還沒檢查過（第 1 期做出的專案也是這樣），或檢查之後又改了。檢查之後又改了的，`transcript.json` 和 `trim.json` 沒變（變了的話 `next` 會是 `edit`），可以直接跑 `validate`。還沒檢查過的沒有紀錄可比，`pipeline` 看不出 `transcribe` 或 `trim` 有沒有在 `edit.json` 寫好之後重跑過：你知道重跑過，或拿不準（例如接手的舊專案），就當作重跑過，先照下面三步逐一重新核對，再跑 `validate`。`storyboard` 在這個狀態會拒絕（結束碼 1），`error` 是 `edit.json has not been validated: run validate`。
- `next` 是 `edit`，而且 `edit.json` 已經在：`transcribe` 或 `trim` 重跑過（例如使用者改了腳本之後），`transcript.json` 或 `trim.json` 的內容變了。候選代號（`t01`、`t02`…）和句子代號（`s01`、`s02`…）都是依位置編的，同一個代號現在可能指向另一段或另一句，舊的 `edit.json` 卻可能照樣通過 `validate`，所以不能直接 `validate`。`storyboard` 在這個狀態也會拒絕（結束碼 1），`error` 是 `edit.json was checked against an older transcript.json or trim.json: re-check every trim and sentence id, then run validate`。

`transcribe` 或 `trim` 重跑過，不管 `next` 是 `edit` 還是 `validate`，都照這個順序做，做完才能跑 `validate`：

1. 讀新的 `work/trim.json`，`edit.json` 裡每一個候選，都從新的候選重新決定接受或拒絕（看它現在的種類、辨識文字和 `suggest`），不沿用舊的決定。新的候選每一個都要決定，新的 `trim.json` 沒有的代號要拿掉。`notes` 裡候選的註記也照新的候選重寫。
2. 讀新的 `work/transcript.json`，`cuts.extra` 的 `from`、`to` 和 `sentences` 的每一筆，都對照現在的句子（內容、`spoken`）重新核對：同一個代號是不是還是那一句，講者有講的句子是不是都有一筆，沒講到的有沒有誤寫進去。`captions.text` 和 `notes` 裡的句子代號也一樣核對；改過的字幕還要不要留，也看一下（例如使用者已經把腳本改好，就不用了）。
3. 改好，或確定都不用改，再跑 `validate`。

跑 `validate` 時，如果 `edit.json` 的內容和上次檢查時一模一樣，卻是對舊的 `transcript.json` 或 `trim.json` 檢查的，通過的結果會多一個 `warnings`：`edit.json is unchanged since it was checked against an older transcript.json or trim.json: trim and sentence ids may now point at different candidates`。這表示 `pipeline` 看不出你有沒有重新核對。看到它，就用中文轉述給使用者，並講你重新核對的結果。用句子的內容和時間說，不講代號。例如：「剪輯的決定和上次一模一樣，但中間轉錄重跑過，決定裡指定的哪一段、哪一句，可能對到不同的位置。我已經逐一重新核對，原本的決定都還成立。」還沒核對就看到這個警告，先回頭做上面三步，再轉述。

## 7. 做出分鏡稿

```
uv run pipeline storyboard <專案>
```

產生 `review/gate1/index.html`（關卡的靜態網頁，下面簡稱「網頁」）和 `review/gate1/storyboard.md`（同樣的內容，方便你引用）。兩個檔案最上面都是摘要、警告和兩個清單（「要你決定」「其他要聽的地方」），下面是句子表和候選表。網頁是單一檔案，縮圖和試聽片段都嵌在裡面，手機也能開，寬度 640 px 以下時每一列變成一張卡片，不用左右滑。輸入都沒變時 `storyboard` 會跳過（`"skipped": true`），要重做加 `--force`。`storyboard` 只在 `edit.json` 通過 `validate`、而且那之後 `edit.json`、`transcript.json`、`trim.json` 都沒再變的時候才做；不然它什麼都不產生，結束碼 1，照第 6 節的說明，跑 `validate` 或重新核對。它還有兩個拒絕，處理方式不同：

- `probe.json not found: run prep first`：前處理的結果不見了。先跑 `prep`，再照 `next` 往下。
- `sentence bounds in trim.json are out of order: run trim again`：辨識出來的字的時間重疊得很厲害，句子的切點排不出先後。照訊息重跑 `trim` 沒有用：`trim` 的結果只由輸入決定，輸入沒變，結果就一樣。`pipeline` 目前沒有處理這個狀況的辦法（記在 `docs/backlog/2026-11-12-phase1-review-items.md` 的 D2：碰到這個拒絕，就是該修它的時候）。把 `error` 轉述給使用者，不改 `trim.json`，等他決定。

結果有 `source_s`（原長）、`duration_s`（新長）、`cuts`（剪掉的段數：接受的候選裡在成品裡的，加上 `cuts.extra` 的範圍）、`decisions`（要你決定幾項）、`listen_points`（其他要聽的地方幾處）和 `warnings`。

## 8. 關卡 1：審閱頁，或用一則訊息講給使用者聽

使用者看分鏡稿、給同意的方式有兩種：

- 在電腦前：你啟動審閱頁，傳一則短訊息（下面「在電腦前：審閱頁」）。他在頁面上看剪完的樣子、改、按同意；想用講的，也可以在對話裡說。
- 用 Remote Control、不在電腦前：你傳一則較長的訊息講給他聽，再傳網頁（下面「不在電腦前：寫訊息」）。他在對話裡回一次就能同意。

要使用者決定的候選，你寫 `edit.json` 時已經照建議填好，建議和理由也寫在註記（`notes`）裡（見 `storyboard` skill），所以它們在網頁和審閱頁上的處理就是你的建議。字幕用辨識文字的句子，你不先改字幕的字（`captions.text`）：你聽不到聲音，看出的錯字只是可能，改法寫在註記裡，由使用者決定；先改了，這一句就不在「要你決定」裡，他不會被問到。所以頁面現在的樣子是字幕照辨識出來的字出，你的建議（例如改字、重錄）要使用者另外回，或在審閱頁上自己改。使用者回「可以」，就是同意頁面現在的樣子（審閱頁開著時是審閱頁，不然是網頁），不管你對那一項建議了什麼。

### 讀兩個清單

`storyboard.md` 最上面是摘要和警告，接著是兩個清單，編號連續，和網頁一致：

- 「要你決定」：編號從 1 開始，共 `decisions` 項。
- 「其他要聽的地方」：編號接著上一個清單，共 `listen_points` 處。

每一項有編號、在成品裡的時間（m:ss.s）、種類（切點用切點的名稱）、講了什麼（「」裡），候選還有目前的處理（剪掉或保留）。字幕用辨識文字的項目另有「字幕」和「腳本」兩行。

| 清單 | 種類 | 意思 |
|---|---|---|
| 要你決定 | 字幕用辨識文字 | 講的和腳本差太多，字幕改用辨識出來的字，可能有錯字 |
| 要你決定 | 改詞重講 | 措辭和腳本不同的重講，程式只列出來，不自動剪 |
| 要你決定 | 腳本外的話 | 講了腳本沒有的話。3 個字以上的才列在這裡；一兩個字的不問，在網頁的候選表（剪掉的、在成品裡、切點不在靜音裡的，也列在「其他要聽的地方」） |
| 其他要聽的地方 | 片頭切點、片尾切點、刪除段前切點、刪除段後切點 | 成品開頭、結尾，或把整段拿掉的句子前後接起來的地方，切點不在靜音裡，可能切到字或有雜音 |
| 其他要聽的地方 | 剪接處不在靜音裡 | 一個剪掉、而且在成品裡的候選，它的切點不在靜音裡 |
| 其他要聽的地方 | 吻合度低 | 講的和腳本差了一些，但字幕取自腳本 |

清單的說明有時帶句子代號（例如「片頭切點在刪除的 s01 之後」）。對使用者不講代號，說那一句的內容和時間。

清單的編號要對回 `edit.json`：句子表和候選表裡，被清單提到的列，「標記」欄寫「見第 N 項」。那一列的「列」（句子）或「編號」（候選）就是 `edit.json` 要的代號。候選表裡標「不在成品裡」的候選（和成品的任何一段都不重疊，例如整句被拿掉的句子裡的腳本外的話），不用問使用者，也不算進「剪掉」的段數；它們在 `edit.json` 裡照舊要列在 `cuts.accept` 或 `cuts.reject`，照 `storyboard` skill 的預設填就好。

### 在電腦前：審閱頁

審閱頁是 `pipeline review` 啟動的互動網頁，只在這台電腦上用（它只接受這台電腦本身 `127.0.0.1` 的連線）。左邊的影片照剪完的樣子播：播的是審閱用影片 `work/review.mp4`（從原始影片轉出、整支沒剪過的低畫質影片），播到剪掉的地方就跳到下一段留下的地方，字幕疊在上面。右邊是整支的逐字稿，剪掉的、要你決定的、字幕可能有錯字的地方都有標記，點了浮出小卡，上面是你的註記（沒有註記的，頁面依種類產生說明）。使用者在頁面上可以：

- 把程式找到的一段切換成剪掉或保留（不能自己選任意一段字剪掉）；
- 拿掉一句，或復原拿掉的句子（復原用的紀錄在 `work/review-undo.json`）；
- 改字幕的字（存成 `edit.json` 的 `captions.text`，見 `storyboard` skill）；
- 按「同意，輸出成品」：確認框「確定輸出成品？」列出他沒點開看過的項目，和會留在成品裡的錯字。他按「確定，輸出成品」，頁面就依序重做分鏡稿、記下關卡 1 的同意、輸出成品（和第 9 節的 `storyboard`、`approve`、`render` 相同；磁碟可用空間不到 15 GB 時不輸出，頁面會提醒），每一步顯示在頁面上，完成後在頁面上播手機預覽版。

頁面的每個改動，都先通過 `validate` 才存進 `work/edit.json`，`pipeline` 也記下這次檢查：頁面改過之後，`status` 的 `next` 是 `storyboard`，不是 `validate`。

做完第 7 節的 `storyboard` 之後，在背景執行（不等它結束）：

```
uv run pipeline review <專案>
```

- 它先檢查 `edit.json`，條件和拒絕都和 `storyboard` 相同（`edit.json` 沒通過 `validate`，或通過之後 `edit.json`、`transcript.json`、`trim.json` 又變了），照第 6 節處理。拒絕時只印一行（`"ok": false`），就結束了。
- 接著做審閱用影片。第一次要一點時間，之後輸入沒變就跳過。做不出來時它會拒絕，附 `log`，照第 10 節。
- 開始提供審閱頁時，印第一行 JSON：`{"ok": true, "serving": true, "url": …, "port": …, "video": …}`，再用預設瀏覽器打開審閱頁。`url` 是審閱頁的網址，帶一組通行碼（沒有通行碼的請求一律拒絕）。讀到這一行，再傳訊息。
- 它會一直跑，停下來時才印第二行：`"stopped": "idle"` 是頁面 30 分鐘沒有任何請求（頁面關掉了），而且沒有在輸出，它自己停了；`"stopped": "stopped"` 是你停掉的（見下一節）。

然後傳一則短訊息，依序寫：

1. 審閱頁的網址：`url` 整個貼上。瀏覽器已經打開了，網址是給他關掉之後再開用的。
2. 長度：從原長剪到新長（`storyboard` 結果的 `source_s` 和 `duration_s`，秒換成「分:秒」）。
3. 警告：照下面「不在電腦前：寫訊息」的表翻成中文，沒有就不提。審閱頁不顯示警告。
4. 要他決定幾項：照審閱頁的算法，只算改詞重講和腳本外的話（`decisions` 減掉「字幕用辨識文字」的項數）。字幕用辨識文字的句子另外說：有幾句、你看出錯字沒有（頁面上是「字幕可能有錯字 N 句」）。審閱頁的編號和網頁的不同，所以不列編號，也不逐項說：每一項的建議在頁面的小卡上。
5. 最後一句：在頁面上看完按同意就好；想用講的，也可以直接在這裡說。

這條路不傳 `index.html`。例子（網址縮短了，數字是編的）：

```text
審閱頁開好了：http://127.0.0.1:51234/?key=…
這支從 4:00 剪到 3:37，有 2 項要你決定，字幕沒有看出錯字。在頁面上看完按同意就好；想用講的，也可以直接在這裡說。
```

### 審閱頁開著的時候

`pipeline review` 還沒印第二行，審閱頁就開著。這段時間：

- 使用者在頁面上的改動直接存進 `edit.json`，你看不到。他回到對話時，不管說什麼，先跑 `uv run pipeline status <專案>` 看走到哪一步，再讀 `work/edit.json`，然後才回答。說的時候用句子的內容和成品裡的時間（頁面上的時間就是成品裡的時間），不講代號，也不用網頁清單的編號。
- 他在對話裡要改（例如「1:15 那段保留」「1:48 那句字幕改成…」）：照下面「使用者回覆之後」改 `edit.json`，馬上 `validate`。他用頁面上的編號說的話：頁面的編號只數改詞重講和腳本外的話，照時間排，和網頁的編號不同；拿不準是哪一項，就用時間問他。你改的時候，頁面顯示「Claude 正在更新剪接設定，請稍候。」；`validate` 通過後，頁面幾秒內自己重新載入，顯示「Claude 改了剪接設定，頁面已更新。」，不用重開。不用跑 `storyboard`：頁面不讀分鏡稿，同意時才重做。告訴他改了什麼和新的長度（`validate` 的 `duration_s`），請他在頁面上看，看完按同意，或在這裡回「可以」。
- 頁面說「…請回對話請 Claude 看看。」：英文的原因記在 `work/review.log`（審閱頁的記錄檔）。讀它找原因，用中文跟他說。
- 他在對話裡回「可以」（或同樣明確的話）：他同意的是頁面當時的樣子。照第 9 節跑 `storyboard`、`approve`、`render`，不再問；你在他回覆之後又改了 `edit.json`，就要再問。先跑的 `status` 顯示他也在頁面上同意過的話，照下一項。頁面看得出已經同意，顯示「已經在對話裡同意了。輸出的進度請看對話；輸出完成後，這裡會播預覽版。」，不能再改。
- 他在頁面上按了「確定，輸出成品」：這就是同意。你不在對話裡再問一次，也不跑 `approve`、`render`。看 `status`：
  - `next` 是 `done`：輸出完成了，照第 9 節「輸出後」告訴他成品的位置和兩個檔案的用途。
  - `next` 是 `render`、`gate1_ok` 是 `true`，而這次的 `approve` 不是你在對話裡跑的：頁面正在輸出，或輸出失敗、頁面讓他再按一次同意。這時你不在對話裡跑 `approve` 或 `render`：同一個專案同時輸出兩次會出錯。請他看頁面；頁面說輸出失敗的話，照下一項。
- 頁面說「輸出失敗。請回對話請 Claude 看記錄檔。」：讀 `work/review.log` 最後的 `the approval failed in <步驟>: …`（`<步驟>` 是 `storyboard`、`approve` 或 `render`；有 `log` 的話，那個記錄檔也讀），照第 10 節用中文轉述。頁面依序做分鏡稿、記下同意、輸出，失敗在哪一步，決定同意有沒有記下：他按「確定」之後 `edit.json` 又改過的話，分鏡稿那一步就拒絕，那次「確定」不算數。所以接著跑 `uv run pipeline status <專案>`：
  - `next` 是 `render`、`gate1_ok` 是 `true`：同意記下了，失敗的是輸出。原因排除之後，請他在頁面上再按一次同意。要改在對話裡輸出（他要你做，或原因得在對話裡處理），不用再問：先停掉審閱頁，照第 9 節看可用空間，只跑 `render`，不跑 `storyboard`、`approve`。
  - 其他情況：同意沒有記下（失敗在分鏡稿或記下同意那一步）。請他在頁面上再按一次同意，或在對話裡明確回「可以」，照一般的關卡走。
  - 之後分鏡稿用到的任何檔案（`edit.json`、`transcript.json`、`trim.json` 等）有改動，都要重新同意。
- 停掉審閱頁：使用者要你停，或要改在對話裡輸出時，結束那個背景工作（`pipeline review` 收到 SIGTERM 就停）。正在輸出時，它等輸出做完才停。停了之後，頁面顯示「審閱頁的程式已經停了，請回對話請 Claude 重新打開。」；頁面上存進 `edit.json` 的決定不會不見。
- 審閱頁停了（已經印了第二行），使用者還要看：再在背景跑一次 `uv run pipeline review <專案>`（`edit.json` 改過就先 `validate`）。網址會換一個，把新的網址傳給他。

### 不在電腦前：寫訊息

訊息裡要寫出網頁的位置（`review/gate1/index.html`，用瀏覽器開）：每一項的播放鍵和「其他要聽的地方」都在網頁上。使用者用 Remote Control、不在電腦前時，他打不開路徑，所以傳完這則訊息之後，用 SendUserFile 把 `index.html` 傳給他（縮圖和試聽片段都嵌在裡面，只傳這一個檔案）。使用者要改、你重做分鏡稿之後，網頁的內容變了，Remote Control 下，傳完新的訊息之後再傳一次新的 `index.html`。預覽版 `preview.mp4` 要等第 9 節的 `render` 做完才有，不在這一步傳。

訊息依序寫這幾件事：

1. 長度：從原長剪到新長（秒換成「分:秒」），剪掉幾段（`cuts`）。
2. 警告：把 `warnings` 翻成中文，沒有警告就不提：

   | `warnings` 的值 | 對使用者說 |
   |---|---|
   | `aspect W:H will be center-cropped to 9:16` | 畫面不是 9:16，會置中裁切成 9:16 |
   | `few pauses: quiet-point detection unreliable` | 停頓很少，切點是否在靜音裡的判斷不可靠 |
   | `long word split across caption lines` | 有詞太長，字幕從詞的中間斷開（一個英文詞或網址比一行還寬，只好斷在字元之間；那一句在網頁上標「有詞太長，從中間斷開」） |
   | `preview over 30 MiB: not kept` | 手機預覽版超過 30 MiB（對話傳檔的上限），沒有留；成品照樣完成（這則出現在第 9 節 `render` 的結果裡） |
   | 以 `preview failed:` 開頭的 | 手機預覽版做不出來，把冒號後面的原因轉述；成品照樣完成（同上，出現在 `render` 的結果裡） |

   MiB 是 1,048,576 位元組，所以 30 MiB 是 31,457,280 位元組。跟使用者說時照樣說「30 MiB」，不用換算。

3. 沒講到的句子：腳本有、講者沒講的句子，網頁上標「沒講到」。有的話，告訴使用者這幾句不出字幕，不用聽。
4. 要你決定的每一項（`decisions` 項）：編號（和網頁一致）、在成品裡的時間、講了什麼、你的建議和一句理由（和你寫的註記一致）。每一項各佔一個編號，一兩行寫完。`decisions` 是 0，就說沒有要決定的事。
5. 其他要聽的地方和網頁：用一句話說有幾處（`listen_points`）、在網頁（`review/gate1/index.html`）的「其他要聽的地方」。不逐項問，也不要使用者回覆。`listen_points` 是 0，就只說網頁的位置。
6. 最後一句：回「可以」，就是同意網頁現在的樣子，我就輸出成品；要改的項目，只回那幾項，例如「2 剪掉」。

訊息要短，每項一兩行。用編號和成品裡的時間指項目，不講 `s05`、`t03` 這類代號，也不貼 JSON。下面是一個例子（句子是編的）：

```text
這支從 3:05 剪到 2:41，剪掉 14 段。畫面不是 9:16，會置中裁切成 9:16。

要你決定的有 3 項，每一項附我的建議：

1. 0:12 字幕用辨識文字：「我覺得亮度越低越省電，大概多成兩小時」（腳本是「其實把亮度調低，一天可以多撐兩小時」）。「多成」應該是「多撐」。網頁現在的字幕照辨識的字出；我建議把「多成」改成「多撐」，要改就回「1 改字」。回「可以」的話，這個錯字會留在成品裡。
2. 0:48 改詞重講：「所以亮度其實是」。只是下一句「亮度才是最耗電的設定」講到一半的舊版本，建議剪掉。
3. 1:30 腳本外的話：「對了這個我昨天試過」。聽起來是順口加的，建議保留。

另外有 2 處切點可以在網頁上聽，不用回。網頁在 review/gate1/index.html。

回「可以」，就照網頁現在的樣子輸出成品。要改的項目，只回那一項，例如「2 保留」。
```

每一種要你決定的項目這樣寫：

- **字幕用辨識文字**：照「腳本」那一行，對照「字幕」，指出可能的錯字（怎麼對照見 `storyboard` skill）。使用者有五個選擇：直接改字幕的字、改腳本後重跑、拿掉這一句、重錄，或接受辨識出來的字，做法見下面「字幕有錯字」。這一項一定要明講：使用者回「可以」，錯字就留在成品裡。對照之後沒看出錯字，也要說：字幕是辨識的結果，可能有你看不出的錯字，他可以在網頁上按播放聽。
- **改詞重講**：把辨識文字念出來，講建議（剪掉或保留）和一句理由。怎麼判斷見 `storyboard` skill。
- **腳本外的話**：建議保留；明顯是口誤的，建議剪掉並說明理由。

### 使用者回覆之後

- 回「可以」：同意網頁現在的樣子。沒回的項目維持網頁現在的樣子（候選是你填好的處理；字幕用辨識文字的句子，錯字留在成品裡，不管你建議了什麼），不逐項問的「其他要聽的地方」也不另外處理。接著第 9 節：`approve`，再 `render`（審閱頁開著時，見上面「審閱頁開著的時候」）。
- 只回要改的項目（例如「2 剪掉」「3 保留」）：
  1. 用上面的「見第 N 項」找到那一列。候選：剪掉就放進 `cuts.accept`，保留就放進 `cuts.reject`（見 `storyboard` skill）。字幕用辨識文字的句子：照下面「字幕有錯字」（改字幕的字、改腳本重跑、拿掉這一句、重錄、接受辨識出來的字，都在那裡）。
  2. 跑 `validate`，再 `uv run pipeline storyboard <專案>`（審閱頁開著時，`validate` 就好，見上面「審閱頁開著的時候」）。
  3. 只講改了什麼：那一項現在怎麼處理、新的長度。清單的編號因此變了（例如拿掉一句字幕用辨識文字的句子，它就不在清單裡），要說新的編號對到哪一項。然後再問一次。Remote Control 下，傳完這則訊息之後，把新的 `index.html` 再傳一次。

  使用者回的編號，指的是你最近一則訊息的編號。
- 其他回答：沒回答，或只說了模稜兩可的話（例如「應該可以吧」「再看看」），就再問一次，不替他決定。
- 使用者問起某個「其他要聽的地方」：用編號和時間說是哪一處、為什麼列在這裡，請他在網頁那一列按播放聽：

  | 種類 | 請使用者聽什麼 |
  |---|---|
  | 片頭、片尾、刪除段前後的切點 | 聽接起來的地方有沒有切到字、有沒有雜音 |
  | 剪接處不在靜音裡 | 聽剪掉再接起來的地方有沒有切到字、有沒有雜音。覺得不好，可以把那個候選改成保留（`cuts.reject`） |
  | 吻合度低 | 聽講的和字幕是不是同一個意思。這句的字幕取自腳本，錯字的處理見下面「字幕有錯字」 |

  切點的位置由程式決定，`edit.json` 沒有微調它的辦法：聽了覺得不好，能做的是上表說的保留候選、把相關的句子整段拿掉，或請使用者重錄。要明講。

### 字幕有錯字

使用者指出字幕有錯字時，最直接的是改字幕的字；改腳本檔、從轉錄重跑的做法照舊可以用。

- 改字幕的字（例如他說「1:48 那句字幕改成…」，或照你在訊息、註記裡建議的改法，回「1 改字」）：在 `edit.json` 的 `captions.text` 加一筆，這一句的代號對到字幕要顯示的整句字（寫法、字幕的時間怎麼對、哪些字不能寫，見 `storyboard` skill）。字幕取自腳本或辨識出來的句子都能改，不用改腳本，也不用從 `transcribe` 重跑。改了之後，這一句不再列在「要你決定」或「其他要聽的地方」。接著 `validate`、`storyboard`（審閱頁開著時，`validate` 就好）。
- 改腳本檔、從轉錄重跑：先看那一句是不是清單裡「字幕用辨識文字」的項目（`transcript.json` 裡這一句的 `display_source` 是 `"asr"`）。這一句已經有 `captions.text` 的話，重跑之後把那一筆拿掉，字幕才會用新的字。
  - 不是：字幕的字取自腳本。請使用者改原來那個腳本檔（`inbox/` 你不改），改好後 `status` 會指向 `transcribe`，從那裡往下重跑。
  - 是：這句的字幕用的是辨識出來的字，不是腳本的字，所以只改腳本裡的錯字改不到它。要改回腳本的字，得把腳本改得和他實際講的話夠接近：`transcript.json` 裡這句的 `ratio`（吻合度）要到 0.50 以上，「字幕用辨識文字」才會消失，字幕才用腳本的字。請使用者改原來那個腳本檔，再從 `transcribe` 往下重跑，看這句的 `display_source` 變成 `"script"` 沒有。改不到那麼接近，或使用者不想改腳本，就改字幕的字、請他重錄那一句，或接受辨識出來的錯字。
- 使用者要拿掉這一句（例如回「1 拿掉」）：在 `edit.json` 的 `cuts.extra` 加一筆，`from` 和 `to` 都填這一句的代號，`reason` 一定要寫，用中文（分鏡稿會把它顯示在被移除的句子旁）。這一句的字幕也跟著不出現。`sentences` 裡這一句的那一筆要拿掉，不然 `validate` 會報 `… is removed by cuts.extra`。接著 `validate`、`storyboard`。然後看新的 `storyboard.md` 的候選表：這一句的候選裡，種類是 `extra` 或 `retake`（看 `trim.json`）、沒標「不在成品裡」的，是講在這一句前面的話，移除的範圍從這一句的切點開始，剪不到它們。除非使用者說要留，把它們也放進 `cuts.accept`，再 `validate`、`storyboard`；訊息裡用一句話說你把它們一起剪掉了。

### 腳本外的話

每一段腳本外的話都已經是候選（種類 `extra`，網頁候選表上寫「腳本外的話」）。3 個字以上的列在「要你決定」，一兩個字的不問，在候選表。要剪就放進 `cuts.accept`，要留就放進 `cuts.reject`，和其他候選一樣。例外是字幕用辨識文字的句子：

| 位置 | 怎麼判斷 | 怎麼辦 |
|---|---|---|
| 這一句的字幕是辨識出來的 | `transcript.json` 的 `sentences` 裡這一句的 `display_source` 是 `"asr"` | 這句裡的腳本外的話和改詞重講都不是候選（剪掉它們，字幕就對不上了），它們已經在這一句的字幕裡。要拿掉，就把這一整句拿掉（`cuts.extra`，`from` 和 `to` 都填這一句）；不然就留著，或請使用者重錄 |
| 其他位置 | `display_source` 是 `"script"` | 腳本外的話已經是候選，照候選處理 |

### 做完之後

`transcribe` 和 `trim` 重跑完，`next` 通常是 `edit`，`edit.json` 還沒檢查過的專案是 `validate`。兩種都一樣：先照第 6 節逐一重新核對 `edit.json`，再 `validate`、`storyboard`。

談完之後，把使用者的決定和意見寫進專案的 `project.md`（「決定」和「使用者的意見」兩個標題下）。他在審閱頁上改的也寫：讀 `edit.json` 看他改了什麼。

## 9. 同意之後：approve 和 render

使用者在審閱頁上按「確定，輸出成品」同意的，頁面自己做這一節的事，你不跑（見第 8 節「審閱頁開著的時候」）。例外是頁面的輸出失敗，而 `status` 顯示 `next` 是 `render`、`gate1_ok` 是 `true`：同意已經記下，你可以停掉審閱頁，在對話裡只跑下面的 `render`，不用再問（做法見第 8 節「審閱頁開著的時候」）。下面是在對話裡同意的做法。

使用者在對話裡明確同意（回「可以」，或同樣明確的話）才能 `approve`。先看 `uv run pipeline status <專案>`：`next` 是 `storyboard` 時先跑 `uv run pipeline storyboard <專案>`。審閱頁上的改動不會重做分鏡稿，頁面開著或已經停了都一樣。然後：

```
uv run pipeline approve <專案> gate1
```

渲染之前跑 `df -h /`，可用空間低於 15 GB 就停下來提醒使用者。然後：

```
uv run pipeline render <專案>
```

結果的 `out` 是 `out/final.mp4` 的位置，還有 `duration_s` 和 `size_mb`。渲染失敗會自動重試一次（`attempts` 為 2）；還失敗就照第 10 節。結果還有手機預覽版的三個欄位：`preview`（`out/preview.mp4` 的位置，做不出來時是 `null`）、`preview_mb`（檔案大小，單位 MB，只在有預覽版時才有）和 `warnings`（預覽版的警告，翻成中文的對照見第 8 節的表）。預覽版做不出來不會讓 `render` 失敗，成品照樣完成。

輸出後，把成品的位置、長度和檔案大小告訴使用者，並說明兩個檔案的用途：

- `out/final.mp4`：拿來發布。
- `out/preview.mp4`：540×960、30 MiB 以內（31,457,280 位元組以內）的小檔，只用來看剪接和字幕，畫質不夠發布。

使用者用 Remote Control、不在電腦前時，用 SendUserFile 把 `preview.mp4` 傳給他，讓他在手機上看（關卡 1 的 `index.html` 已經在第 8 節傳過，這裡只傳 `preview.mp4`）。`preview_mb` 用十進位的 MB（1,000,000 位元組），30 MiB 的上限換成 MB 約是 31.46，所以 `preview_mb` 是 31.4 這樣的值，仍在上限內：不要因為它大於 30 就說預覽版超過上限，有沒有超過看 `warnings`。`preview` 是 `null` 時沒有東西可傳：照 `warnings` 說明原因。`preview failed:` 的原因排除之後，可以問使用者要不要再跑一次 `render`：成品沒變，只會補做預覽版。

`approve` 和 `render` 都會檢查現在的狀態，不符合就拒絕（結束碼 1）。訊息說缺哪一步，照它做，不要硬跑：

- `render` 回 `gate1 not approved for the current storyboard: run storyboard, then approve`，表示還沒 `approve`，或 `approve` 之後分鏡稿用到的檔案又變了（`edit.json`，或 `transcript.json`、`trim.json` 等）。`approve` 回 `run storyboard first`，表示分鏡稿不是用現在的 `edit.json` 做的，或它用到的檔案變了、做出來的檔案不見了。兩種都先看 `status` 的 `next`，照它一路往下做；`transcribe` 或 `trim` 在 `approve` 之後重跑過的話，`next` 是 `edit`，先照第 6 節重新核對 `edit.json` 裡每一個候選和句子代號，再 `validate`。做到新的 `storyboard`，講給使用者聽，取得新的同意，再 `approve`（第 8 節）。
- `approve` 回 `<步驟> is out of date: run <步驟> first`，或 `render` 回 `gate1 not approved for the current storyboard: <步驟> is out of date, run <步驟>, then storyboard and approve`（`<步驟>` 是 `prep`、`transcribe` 或 `trim`，哪一步先過時就說哪一步）：那一步的輸入檔變了，它的結果已經過時，以前同意過也不算。先跑那一步，再照 `status` 的 `next` 一路往下做；`transcribe` 或 `trim` 重跑過，先照第 6 節重新核對 `edit.json`。做到新的 `storyboard`，講給使用者聽，取得新的同意，再 `approve`。

`storyboard`、`approve` 或 `render` 回 `another storyboard, approve or render is running for this project: wait until it ends`：審閱頁正在輸出這支影片（使用者在頁面上按了同意）。不要一再重跑；請他看頁面，頁面說完成或失敗之後，再跑 `status` 看走到哪一步。

同意涵蓋的是使用者看到的那份分鏡稿：`edit.json` 的任何改動，都需要新的 `storyboard` 和新的同意；`transcribe` 或 `trim` 重跑、讓 `transcript.json` 或 `trim.json` 的內容變了（例如使用者改了腳本之後），也一樣，即使 `edit.json` 一個字沒變。

`trim` 重跑之後，候選代號（`t01`、`t02`…）是依位置編的，同一個代號可能指向不同的一段。要從新的 `trim.json` 重新決定 `edit.json` 裡每一個候選（接受或拒絕），不沿用舊的 `edit.json` 的決定；決定好再 `validate`、`storyboard`。`transcribe` 重跑之後，句子代號也一樣要重新核對。做法見第 6 節。

## 10. 指令失敗時

照 JSON 裡的 `error` 和 `log` 用中文轉述給使用者。`log` 是記錄檔，可以讀來找原因。不自行刪檔重來，也不換參數硬跑；原因不明就問使用者。

`error` 以 `unexpected error:` 開頭（後面是錯誤的類別和訊息）：這是程式沒預料到的錯，`pipeline` 沒有判斷出原因。完整的錯誤堆疊（traceback）印在 stderr，不在 JSON 裡。轉述時說是哪個指令出錯、錯誤的類別和訊息，然後照上面的規則問使用者。

## 使用者交腳本時

使用者貼上腳本，或請你幫忙整理腳本時，提醒這幾點：

- 一句話在 `。`、`！`、`？`（半形的 `!` `?` 也算）或換行處結束。每句一行最穩。
- 標點最好用全形：`，。？！、：；`。半形的 `,` `;` `:` `.` 不會切開句子，但字幕會把它們當成標點，和全形的 `，；：` 一樣處理：在這裡切成短語，在行中變成全形空白，在行尾拿掉。下面三種照原樣顯示：英文字母或數字之間的 `,` `:` `.`（`1,000`、`10:30`、`3.5`、`Node.js`），網址 `https://` 的 `:`，連在一起的 `...`。
- Markdown 的記號可以用，它們不是講的話：成對的 `**`、`__`、`~~`、`***`、單星號斜體（`*很重要*`）和反引號，記號本身會從腳本和字幕拿掉，包在裡面的字留著；連結 `[文字](網址)` 只留「文字」，網址不會進腳本，也不會進字幕。單獨一個 `*`、`_`、`~`（例如 `5*3`、`file_name`、`3~5`）是一般的字，會留在字幕裡。
- 行首的序號不是講的話，會拿掉：`1.`、`2)` 後面要有空格（`1. 先關螢幕。` 變成 `先關螢幕。`），後面直接接字的 `1.先關螢幕。`、`2)第二點。` 不算序號，數字會留在腳本和字幕裡，所以序號後面要加空格；`3、` 後面不用空格（`3、先關。` 變成 `先關。`）。只有序號的一行（`1.`、`10)`）不算一句話，不會多出一個句子。一行裡的數字清單（`1、2、3 都是。`）是講的話，照原樣留著。
- `[B-roll: 咖啡店]`（B-roll 是穿插在口播之間的補充畫面）、`【螢幕：設定步驟】`、單獨一行的（這裡放咖啡店外觀）是備註，不是講的話，不會變成字幕。本期的成品用不到它們。
