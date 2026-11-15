# kacha

這個 repo 把手機直拍的口播影片剪成 Instagram Reels。`pipeline` 指令負責前處理、轉錄、粗剪、字幕和輸出；Claude 當導演，依使用者的意見寫 `edit.json`。第 1 期的成果是一支有字幕的順剪 mp4，放在專案的 `out/final.mp4`。第 2 期進行中：已經有推近（預設不推近，只在使用者指定的地方放大）和三張動畫卡片（重點字、數字卡、清單，用 Remotion 渲染，只加在使用者要的地方），寫法都見 `storyboard` skill。其他的卡、B-roll 還沒有。

使用者資料在 `studio/`（不進 git）：`inbox/` 放素材，`brand/` 放偏好，`projects/<日期-代稱>/` 每支影片一個資料夾。`studio/` 的位置由環境變數 `REELS_STUDIO` 決定，沒設就是 repo 底下的 `studio/`。

## 和使用者談話

- 對話一律用繁體中文（台灣用語）。
- 使用者不讀也不改 JSON。談句子和時間時，用句子的內容和分鏡稿上的時間（例如「0:12 那句」），不講 `s05`、`t03` 這類代號；把話對應回 `edit.json` 是你的工作。
- `pipeline` 指令輸出英文，一行 JSON。轉述成中文，不貼 JSON 給使用者。

## 檔案分工

- `work/edit.json` 由 Claude 和審閱頁寫，寫法見 `storyboard` skill。審閱頁是 `uv run pipeline review` 啟動的互動網頁，只在跑 `pipeline` 的這台電腦上用：使用者在頁面上的每個改動，都先通過 `validate` 才存進 `edit.json`；Claude 改了，也照舊馬上跑 `validate`。
- `work/review-undo.json`（審閱頁「復原」用的紀錄）和 `work/review.mp4`（審閱頁播的影片）由 `pipeline review` 產生。`transcript.json`、`trim.json` 等其他檔案都由 `pipeline` 指令產生。這些檔案 Claude 只讀，不改。
- Claude 另外寫兩份 Markdown 筆記：專案的 `project.md`（導演的決定和使用者給過的意見），和 `studio/brand/preferences.md`（使用者的長期偏好）。
- `studio/inbox/` 只讀：不寫、不改、不刪、不搬。
- 使用者的素材、腳本和逐字稿不進 git，也不寫進這個 repo 的文件。文件裡的範例句子一律自己編。

## 開工

1. 有 `studio/brand/preferences.md` 就先讀。
2. 接續既有專案時，讀它的 `project.md`。
3. 使用者說「以後都…」，把這條偏好寫進 `preferences.md`，並告訴他已經記下。偏好記在這裡，不用 Claude 內建的 memory。

整個流程由 `reel` skill 帶著走。使用者說「剪 inbox 裡的…那支」或「繼續…那支」時就用它。

## 關卡

三道關卡都要使用者同意才能往下。本期只有關卡 1（分鏡稿）：

- `uv run pipeline storyboard` 產生分鏡稿：靜態網頁 `review/gate1/index.html`（單一檔案，不用開程式就能看，手機也能開）和 `storyboard.md`。接著照 `reel` skill 讓使用者看：
  - 使用者在電腦前：在背景啟動 `uv run pipeline review <專案>`，傳一則短訊息：審閱頁的網址、長度、警告、要他決定幾項。他在頁面上看、改、同意；想用講的，也可以在對話裡說。
  - 使用者用 Remote Control、不在電腦前：用中文傳一則訊息，把重點講給他：長度、警告，和要他決定的每一項（附建議），再傳靜態網頁。他回「可以」，就是同意網頁現在的樣子；要改的話，他只回那幾項。
- 關卡 1 的同意有兩種，都算明確的同意：使用者在對話裡明確回「可以」；或在審閱頁的確認框按「確定，輸出成品」。同意的是他回覆或按下當時的那份分鏡稿。
  - 審閱頁開著時，頁面一直顯示目前的 `edit.json`，使用者看到的分鏡稿就是頁面當時的樣子。他在對話裡回「可以」，你跑 `storyboard`、`approve`、`render`，不再問；你在他回覆之後又改了 `edit.json`，就要再問。
  - 使用者在審閱頁上同意過，頁面自己重做分鏡稿、記下同意、輸出成品。你不在對話裡再問一次。
- 在對話裡，使用者明確同意之後，才能 `uv run pipeline approve <專案> gate1`，再 `uv run pipeline render <專案>`。使用者沒回答、或只說了模稜兩可的話，就再問一次；不替他決定。
- 開了審閱頁之後，使用者回到對話時，先用 `uv run pipeline status <專案>` 看專案走到哪一步，再讀 `edit.json`（他可能在頁面上改過），用句子的內容和時間說，不講代號。
- 同意綁在使用者看到的那份分鏡稿上，不只是 `edit.json`：分鏡稿用到的任何檔案（`edit.json`、`transcript.json`、`trim.json` 等）之後有改動，都要重跑 `storyboard`（改了 `edit.json` 先跑 `validate`），講給使用者聽，取得同意，再 `approve`；否則 `render` 拒絕執行。`transcribe` 或 `trim` 重跑、讓 `transcript.json` 或 `trim.json` 的內容變了（例如使用者改了腳本之後），也一樣，即使 `edit.json` 沒變。
- `trim` 重跑之後，候選代號（`t01`、`t02`…）是依位置編的，同一個代號可能指向不同的一段。要從新的 `trim.json` 重新決定 `edit.json` 裡每一個候選（接受或拒絕），不沿用舊的決定。
- 關卡 2（樣張）和關卡 3（成品）屬於之後的期別，本期不存在，不要自己加。

## 長對話

對話快接近 context（這個對話能容納的內容長度）上限時，先把目前的決定和使用者給過的意見寫進 `project.md`，再換新對話。新對話開頭讀 `project.md`，再用 `uv run pipeline status` 看專案走到哪一步。

## 開發

- 環境：`uv sync`，另外要有 Node.js 18.18 以上（渲染動畫卡片用）。第一次用先跑 `uv run pipeline setup`（下載字幕字型、臉部偵測模型和斷詞字典；斷詞字典是字幕斷行找詞的邊界用的詞表。也在 `scenes/` 跑 `npm ci`、下載 Remotion 渲染用的瀏覽器），`uv run pipeline doctor` 會列出缺什麼。
- 測試：`uv run pytest`。標了 `slow` 的測試會跑真的語音辨識，預設跳過，`uv run pytest -m slow` 才跑。行為的改動先寫會失敗的測試，再寫程式。
- 遞迴刪除一律經過 `pipeline.safe_delete` 的 `safe_rmtree`，刪除只發生在專案資料夾內。
- 第三方的程式庫、模型和字型都必須能商業使用。新增或升級套件之後，跑 `uv run python scripts/third_party.py --check`，並更新 `THIRD_PARTY.md`。任何一項不能商業使用，或查不出授權，停下來問使用者，不自己決定。
- 設計文件：`docs/superpowers/specs/2026-11-11-reels-workflow-design.md`、第 1 期改版 `docs/superpowers/specs/2026-11-12-phase1-revision-design.md`、互動審閱頁 `docs/superpowers/specs/2026-11-12-interactive-review-design.md`、推近 `docs/superpowers/specs/2026-11-15-punch-in-design.md`、動畫地基 `docs/superpowers/specs/2026-11-15-remotion-foundation-design.md`；實測紀錄：`docs/spikes/2026-11-11-phase0-results.md`。
