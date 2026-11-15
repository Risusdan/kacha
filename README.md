# Kacha

Kacha（喀嚓）把手機直拍的中文口播影片剪成有字幕的 Instagram Reels。你在 Claude Code 裡用中文說要剪哪一支，Claude 照你的腳本粗剪、上字幕，先在審閱頁給你看剪好的樣子。你同意之後，它才輸出成品。

Claude Code 是 Anthropic 的 AI 助手，能在你的電腦上執行指令、讀寫檔案。Claude 桌面版的「Code」分頁就是 Claude Code，不用開終端機。

## 它做的事

- 找出可以剪的地方：贅字（嗯、呃）、重複的字詞、講錯重來的段落、太長的停頓。腳本裡沒有的話預設保留，由你決定剪不剪。
- 字幕用你腳本裡的字，時間對到你的聲音。講的和腳本差太多的句子，字幕改用辨識出來的字，Claude 會請你檢查有沒有錯字。字幕是繁體中文，直接燒進畫面。
- 審閱頁照剪好的樣子播放影片和字幕。你在頁面上決定每一段剪或留、改字幕的字，看完按同意，它就輸出成品。
- 在你指定的地方，畫面可以放大，也可以放上動畫卡片：重點字、數字卡或清單。卡片在畫面上方，不會擋到字幕。
- 成品是 1080×1920、每秒 30 格的 mp4，大小在 300 MB 以內。剪好的片長上限是 9 分 3 秒，超過時不會輸出。

每一項功能，和在對話裡怎麼跟 Claude 說，見[功能一覽](docs/features.md)。

## 系統需求

| 項目 | 需求 | 檢查方法 | 不符合時 |
|---|---|---|---|
| 晶片 | Apple 晶片（M 系列） | `sysctl -n machdep.cpu.brand_string` 印出的字以 `Apple M` 開頭 | 這台電腦不能安裝 Kacha。 |
| macOS | 15（Sequoia）或更新 | `sw_vers -productVersion` 印出的第一個數字是 15 以上 | 先在「系統設定」>「一般」>「軟體更新」更新 macOS，再安裝。 |
| 記憶體 | 16 GB 以上 | `sysctl -n hw.memsize` 印出的數字是 17179869184 以上 | 先告訴使用者這台電腦的記憶體低於 16 GB，問他要不要繼續安裝。 |
| 可用硬碟空間 | 安裝前 30 GB 以上 | `df -h ~` 的 `Avail` 欄 | 先清出空間，再安裝。 |
| Claude 帳號 | Pro、Max、Ultra、Team、Enterprise 或 Console 帳號。免費方案不能使用 Claude Code。 | 能在「Code」分頁和 Claude 對話，就符合。 | 先升級帳號的方案。 |

安裝前要有 30 GB 可用空間：安裝用掉約 11 GB；輸出成品時，硬碟還要有 15 GB 以上可用；其餘留給影片和專案。可用空間不到 15 GB 時，Kacha 不輸出成品，要先清出空間。

## 安裝

### 你要做的

1. 安裝 [Claude 桌面版](https://code.claude.com/docs/en/desktop)，用你的 Claude 帳號登入。
2. 打開「Code」分頁，選「文件」資料夾當作 Claude 工作的資料夾。
3. 輸入下面這句，按 Enter：

   ```text
   照 https://github.com/Risusdan/kacha 的 README，幫我安裝 Kacha。
   ```

4. Claude 會一步一步安裝，並用中文告訴你它在做什麼。下面這些事要你自己做：
   - Claude 執行指令前問你時，看過說明再允許。
   - 安裝 Homebrew（Mac 的軟體安裝工具）時，Claude 會請你用瀏覽器下載安裝檔，再打開它安裝。
   - 跳出要輸入 Mac 登入密碼的視窗時，由你自己輸入。Claude 不會代你輸入密碼。
   - 跳出其他要你允許或按「安裝」的系統視窗時，由你自己按。
5. 下載語音辨識模型（約 8.4 GB）要等一段時間。這段時間你不用做任何事。
6. 安裝完成後，Claude 會告訴你影片和腳本要放在哪裡。

### 給 Claude：代使用者安裝

這一節寫給執行安裝的 Claude。使用者沒用過終端機，所以指令都由你執行。每一步開始前，用一句中文告訴使用者你要做什麼。

1. **檢查系統需求。** 照上面「系統需求」表逐項檢查。
   - 記憶體以外的項目不符合：停止安裝，用中文告訴使用者是哪一項、怎麼處理。
   - 記憶體不符合：照表問使用者。他說要繼續，才往下安裝。
2. **確認有 Homebrew。** 執行 `/opt/homebrew/bin/brew --version`。
   - 印出版本：到下一步。
   - 找不到：請使用者用瀏覽器打開 <https://github.com/Homebrew/brew/releases/latest>，下載檔名以 `.pkg` 結尾的檔案，打開它，照畫面安裝。安裝程式要的密碼由使用者自己輸入，你不要代輸入。使用者說裝好之後，再執行一次 `/opt/homebrew/bin/brew --version`。
3. **讓指令找得到 Homebrew。** 執行 `command -v brew`。如果找不到：
   - 在 `~/.zprofile` 最後加一行 `eval "$(/opt/homebrew/bin/brew shellenv)"`。之後的對話就找得到 `brew`。
   - 這次安裝剩下的每個指令，前面都加上 `eval "$(/opt/homebrew/bin/brew shellenv)" &&`。
4. **安裝 git、uv、ffmpeg-full 和 node。** 執行 `brew install git uv ffmpeg-full node`。git 用來下載和更新 Kacha；uv 管理 Kacha 用的 Python 和套件；ffmpeg-full 處理影片和聲音；node 是 Node.js，用來做動畫卡片。
5. **下載 Kacha。** 在使用者選的資料夾裡執行 `git clone https://github.com/Risusdan/kacha.git`。它建立 `kacha` 資料夾，下面的指令都在這個資料夾裡執行。
   - 已經有 `kacha` 資料夾時，不要覆蓋它。執行 `git -C kacha remote get-url origin`：印出的是上面的網址，就在 `kacha` 資料夾裡執行 `git pull`；不是的話，停下來問使用者。
6. **安裝 Kacha 用的程式和模型。** 依序執行：

   ```sh
   uv sync
   uv run pipeline setup
   uv run hf download Qwen/Qwen3-ASR-1.7B
   uv run hf download Qwen/Qwen3-ASR-0.6B
   uv run hf download Qwen/Qwen3-ForcedAligner-0.6B
   ```

   - `pipeline setup` 下載字幕字型，以及剪片要用的模型和字典。它也安裝做動畫卡片用的 Remotion，並下載 Remotion 用的瀏覽器（約 459 MB）。
   - `hf` 是 `uv sync` 裝好的下載工具。三個 `hf download` 把語音辨識模型下載到 Hugging Face 的快取資料夾，共約 8.4 GB，不需要 Hugging Face 帳號。剪片時不會自動下載它們，所以要先下載。平常用 1.7B 模型辨識，它執行失敗時改用 0.6B；ForcedAligner 找出每個字的時間。
   - 這三個下載要很久。放在背景執行，期間告訴使用者還在下載。下載中斷時，再執行一次同一個指令。
7. **檢查環境。** 執行 `uv run pipeline doctor`，它印出一行 JSON。
   - `"ok": true`：安裝完成。
   - `"ok": false`：各欄位和 `"warnings"` 列出缺了什麼。補上之後再執行一次。補不了，就用中文告訴使用者缺什麼。
8. **告訴使用者怎麼開始。** 在 `kacha` 資料夾裡建立 `studio/inbox/`，執行 `open studio/inbox`，在 Finder 打開這個資料夾給使用者看。然後告訴使用者：
   - 把影片和腳本放進這個資料夾。
   - 以後剪片時，在「Code」分頁選「文件」裡的 `kacha` 資料夾，說「剪 inbox 裡的○○那支」。

使用者要更新 Kacha 時，在 `kacha` 資料夾裡執行 `git pull` 和 `uv sync`，再執行 `uv run pipeline setup` 和 `uv run pipeline doctor`。`setup` 只下載還沒有的東西。`setup` 說找不到 node 時，執行 `brew install node`，再執行一次 `setup`。

### 移除 Kacha

在「Code」分頁選 `kacha` 資料夾，說「照 README 移除 Kacha」。Claude 會先告訴你要刪哪些東西、各多大，你同意了才刪。

移除會刪掉整個 `kacha` 資料夾。你沒有把 `studio` 改放到別處的話，你的影片、腳本和成品都在它底下的 `studio/` 裡：要留著它們，先在 Finder 把 `studio` 資料夾搬到 `kacha` 資料夾外面。拖到垃圾桶的檔案，清空垃圾桶之後才會拿回空間。

下面寫給執行移除的 Claude。每一步開始前，用一句中文告訴使用者你要做什麼。

1. **告訴使用者會刪什麼。** 在 `kacha` 資料夾裡執行 `uv run hf cache ls`，看三個語音辨識模型（`Qwen/Qwen3-ASR-1.7B`、`Qwen/Qwen3-ASR-0.6B`、`Qwen/Qwen3-ForcedAligner-0.6B`）的大小，再執行 `du -sh .` 看 `kacha` 資料夾的大小。用中文列給使用者：
   - 三個語音辨識模型。
   - `kacha` 資料夾。Remotion 用的瀏覽器、字幕字型和其他模型都在它裡面。有 `studio/` 的話，告訴他裡面有他的影片、腳本和成品。
   - 可以一起移除、要他另外決定的：Homebrew 裝的 git、uv、ffmpeg-full 和 node，uv 和 npm 的快取，以及 Claude Code 在這台電腦上的對話紀錄。
2. **確認 `studio/` 怎麼處理。** `kacha` 資料夾裡有 `studio` 時，問使用者要不要留下它。要留的話，請他自己在 Finder 把 `studio` 資料夾拖到 `kacha` 資料夾外面。你不要搬或刪 `studio/` 裡的任何檔案。使用者說搬好了，執行 `ls` 確認 `kacha` 資料夾裡已經沒有 `studio`。
3. **刪除語音辨識模型。** 使用者同意之後，在 `kacha` 資料夾裡執行下面的指令。`hf` 裝在 `kacha` 資料夾裡，所以這一步要在刪 `kacha` 資料夾之前做。

   ```sh
   uv run hf cache rm model/Qwen/Qwen3-ASR-1.7B model/Qwen/Qwen3-ASR-0.6B model/Qwen/Qwen3-ForcedAligner-0.6B -y
   ```

4. **刪除 `kacha` 資料夾。** 執行 `open ..`，在 Finder 打開 `kacha` 的上一層資料夾，請使用者自己把 `kacha` 資料夾拖到垃圾桶。
5. **Homebrew 裝的工具和快取。** git、uv、ffmpeg-full 和 node 可能也有別的程式在用。逐一問使用者還要不要用，只處理他確定不用的：
   - uv：先執行 `uv python uninstall --all`（刪掉 uv 下載的 Python）和 `uv cache clean`，再執行 `brew uninstall uv`。
   - node：先執行 `npm cache clean --force`，再執行 `brew uninstall node`。
   - git、ffmpeg-full：執行 `brew uninstall git` 或 `brew uninstall ffmpeg-full`。
6. **Claude Code 的對話紀錄。** 執行 `ls ~/.claude/projects/`，找名稱裡有 `kacha` 的資料夾。有的話，告訴使用者：這是在 `kacha` 資料夾裡的對話紀錄，裡面有他的腳本和逐字稿的內容。他要刪的話，請他結束這個對話之後，在 Finder 按 Command-Shift-G，前往 `~/.claude/projects/`，把那個資料夾拖到垃圾桶。

## 剪一支影片

1. 把影片和腳本放進 `kacha` 資料夾裡的 `studio/inbox/`。沒有這個資料夾就建立它。
   - 影片：手機直拍的口播影片，例如 `.mov` 或 `.mp4`。
   - 腳本：你念的稿子，存成 UTF-8 編碼的文字檔，例如 `.md` 或 `.txt`。UTF-8 是文字檔的一種存法，多數文字編輯軟體預設就用它。句子以「。」「！」「？」或換行分開。中括號裡的文字，例如 `[這裡停一下]`，是給自己看的備註：不算要念的話，也不會出現在字幕上。
2. 在 Claude 桌面版的「Code」分頁選 `kacha` 資料夾（或在 `kacha` 資料夾裡開啟 Claude Code），說「剪 inbox 裡的○○那支」。○○ 是影片的檔名或內容，例如「剪 inbox 裡的咖啡那支」。Claude 在 `studio/inbox/` 裡找這支影片和它的腳本，找不到或不確定時會問你。沒有腳本不能開始。
3. Claude 做好粗剪和字幕後，會在瀏覽器裡打開審閱頁，並傳一則短訊息，告訴你網址、片長和要你決定幾項。審閱頁只在這台電腦上使用。
4. 在審閱頁上看剪好的樣子：
   - 左邊的影片照剪完的樣子播，剪掉的地方直接跳過，字幕疊在上面。右邊是逐字稿，點任何一個字，影片就從那裡播。
   - 剪掉的地方和要你決定的地方都有標記。點一下，會看到 Claude 的建議和理由。
   - 切換一段剪掉或保留，影片馬上照新的決定播。
   - 要改字幕的字，按「改字幕」。

   想用講的，也可以在對話裡跟 Claude 說，例如「0:42 那段留著」或「1:48 那句字幕改成…」。頁面會在幾秒內跟著更新。這些時間是審閱頁上的時間，也就是剪好的影片裡的時間。

   要放大畫面或放動畫卡片，在對話裡跟 Claude 說，例如「0:12 那句放大」或「0:30 那句放數字卡」。放大預設是 1.1 倍，最多 1.2 倍。卡片上的字由 Claude 從那一句摘出來，它會先告訴你寫什麼、從幾秒到幾秒。要改卡片上的字或拿掉卡片，也在對話裡說。審閱頁播到那一段時，會在影片上方標出卡片的名稱和上面的字；動畫本身在成品裡才看得到。
5. 看完按「同意，輸出成品」，再按「確定，輸出成品」。在對話裡回「可以」也算同意。成品 `final.mp4` 輸出完成後，頁面會播放預覽版，也有按鈕可以打開成品所在的資料夾。預覽版 `preview.mp4` 檔案較小，方便傳到手機上看，畫質不夠發布。

你同意之後，如果又改了任何地方，要再同意一次，才會輸出新的成品。審閱頁關掉 30 分鐘後會自己停止；要再看，請 Claude 重新打開。

- 接著做到一半的影片：說「繼續○○那支」。換了新的對話也可以。
- 記下長期偏好：說「以後都…」，Claude 會記下來，之後每一支影片都照做。
- 更新 Kacha：說「照 README 更新 Kacha」。

## 資料放在哪裡

素材和專案都放在 `kacha` 資料夾裡的 `studio/`。更新 Kacha 時，不會改到這個資料夾裡的檔案。要放在別處，請 Claude 幫你把環境變數 `REELS_STUDIO`（程式執行時讀取的一項設定）設成那個資料夾的位置。

- `studio/inbox/`：你的影片和腳本。Claude 只讀，不改、不搬、不刪。
- `studio/projects/`：每支影片一個專案資料夾，名稱是日期加上英文代稱，例如 `2026-11-12-coffee`。成品 `final.mp4` 和預覽版 `preview.mp4` 在專案資料夾的 `out/` 裡。

## 隱私與安全

### 留在你電腦上的

- 影片和聲音：剪接、語音辨識、字幕和動畫都在你的電腦上做。剪片時，Claude 不打開影片和聲音檔。除了下面說的 Remote Control，影片和聲音檔不會上傳。語音辨識執行時不連網。
- 你說「以後都…」的偏好，記在 `studio/brand/preferences.md`。每支影片的筆記和剪接決定，記在那支影片的專案資料夾裡。這些檔案都在 `studio/` 裡。Claude 讀它們時，內容會送到 Anthropic（見下一節）。
- Kacha 不會把任何資料傳給 Kacha 的作者。

### 會送到 Anthropic 的

Claude 讀的每一份檔案，內容都會跟對話一起送到 Anthropic（開發 Claude 的公司）。剪片時，Claude 會讀你的腳本、辨識出來的逐字稿、你的偏好和每支影片的筆記。

用 Remote Control 時，Claude 會經過 Anthropic 的服務，把分鏡稿網頁和預覽版傳到你的手機。分鏡稿網頁裡有畫面的縮圖和試聽用的聲音片段。

對話會不會用來訓練 Claude 的模型，看你的方案和設定：

- 個人方案（Pro、Max、Ultra）：看你的隱私設定。在 Claude Code 裡輸入 `/privacy-settings`，或在 claude.ai 的 Settings > Privacy 修改。設定打開時，對話會用來訓練模型，最多保存 5 年；關掉時，保存 30 天。開始剪片之前，先確認這個設定。
- Team、Enterprise 和 Console：對話不用來訓練模型，除非你的組織另外同意。

Claude Code 另外會送出使用統計和錯誤報告：

- 使用統計：Anthropic 說明裡面不含你的提示、程式碼或檔案路徑。要關掉，請 Claude 幫你在 Claude Code 的設定裡加上 `DISABLE_TELEMETRY=1`。關掉之後，Remote Control 可能不能用。
- 錯誤報告：Claude Code 本身出錯時的訊息，Anthropic 說明會遮掉密碼和檔案路徑。要關掉，請 Claude 幫你加上 `DISABLE_ERROR_REPORTING=1`。

Claude Code 也會在你的電腦上保存對話紀錄，裡面有 Claude 讀過的內容。

詳細的條款，見 Anthropic 的說明：<https://code.claude.com/docs/en/data-usage>。

### 安全

- Kacha 的程式碼全部公開在 GitHub 上。
- 安裝的程式都從官方的來源下載：Homebrew、PyPI（Python 的套件庫）、npm（JavaScript 的套件庫）、Hugging Face（模型）、GitHub 和 Remotion。
- Kacha 用到的套件（別人寫好、Kacha 拿來用的程式），版本和雜湊值（檔案的指紋）記在 `uv.lock` 和 `scenes/package-lock.json`，安裝時比對。字型、臉部偵測模型和斷詞字典下載後也比對雜湊值，不符合就不用。
- 照 Claude Code 預設的設定，Claude 執行指令前會先問你。要輸入密碼時，由你自己輸入。
- 審閱頁只接受這台電腦本身的連線，網址要帶一組隨機的通行碼。同一個 Wi-Fi 的其他電腦打不開它。
- Kacha 的程式只刪除專案資料夾裡它自己產生的檔案。`studio/inbox/` 裡的影片和腳本，Claude 只讀，不改、不搬、不刪。

## 常見問題

### 剪一支影片要花多少時間、多少錢？

Kacha 免費，但要有付費的 Claude 方案。個人方案的月費（美元，以 <https://claude.com/pricing> 為準）：

| 方案 | 月費 |
|---|---|
| Pro | 20（年繳每月 17） |
| Max | 100 |
| Ultra | 200 |

用訂閱方案剪片，不另外付費，用掉的是方案的用量額度。Console 帳號（Anthropic 給開發者用的帳號）照用量付費。

下面是一次實測：一支 3 分 9 秒、HDR（高動態範圍，手機常用的拍攝格式）的手機影片，剪完 2 分 44 秒。用的是 Claude Code 預設的模型 Opus 5.5，在對話裡改了一次。

| 步驟 | 時間 | 用量，以 Console 的價格估計 |
|---|---|---|
| 說「剪 inbox 裡的…那支」，到 Claude 傳來要你決定的事 | 約 6 分鐘 | 約 1.03 美元 |
| 在對話裡要求一處放大、一張重點字卡 | 約 30 秒 | 約 0.14 美元 |
| 回「可以」，輸出成品 | 約 1 分鐘 | 約 0.11 美元 |
| 合計（不含你看審閱頁的時間） | 約 8 分鐘 | 約 1.28 美元 |

- 第一步的 6 分鐘裡，Claude 用了約 1.5 分鐘，其餘是電腦在轉檔和辨識語音。
- 影片越長、在對話裡改的次數越多，用量越多。
- 安裝時要下載的東西裡，語音辨識模型最大，約 8.4 GB。下載要多久，看你的網路速度。

看訂閱方案的額度還剩多少：在 Claude 桌面版的「Code」分頁，按輸入框下方的用量圓圈。在終端機用 Claude Code 時，輸入 `/usage`。

省用量的做法：

- 剪掉或保留、改字幕的字，在審閱頁上改。頁面上的改動不經過 Claude，不用到額度。
- 在對話裡要改的地方，一次說完。每來回一次，Claude 都要重讀整段對話。
- 一支影片剪完，剪下一支時開新的對話。對話越長，Claude 每次回覆用的量越多。

### 一定要有腳本嗎？

要。Kacha 拿你的腳本對照你講的話，找出可以剪的地方；字幕也用腳本的字。沒有腳本不能開始。

腳本就是你念的稿子，存成文字檔，和影片一起放進 `studio/inbox/`。寫法見上面「剪一支影片」第 1 步。

### 不想用了，怎麼移除？

說「照 README 移除 Kacha」。Claude 會先告訴你要刪哪些東西、各多大，你同意了才刪。做法見上面「移除 Kacha」。

### 剪好的影片可以再用其他剪輯軟體改嗎？有字幕檔嗎？

`final.mp4` 是一般的 mp4 影片，可以匯入其他剪輯軟體繼續剪。字幕已經燒進畫面，在其他軟體裡不能單獨改字或拿掉。Kacha 只輸出 mp4，不另外輸出字幕檔。

要改字幕，在 Kacha 的審閱頁上改，再輸出一次。

### 出問題找誰？

到 <https://github.com/Risusdan/kacha/issues> 開一則 Issue，寫下你做了什麼、Claude 說了什麼。開 Issue 要有 GitHub 帳號。

Issue 是公開的，任何人都看得到。不要貼你的影片、腳本或逐字稿。Claude 的回覆常引用你講的句子：可以請 Claude 幫你整理要回報的內容，拿掉這些句子。

## 授權

Kacha 以 Apache License 2.0 授權，全文見 [LICENSE](LICENSE)。用到的第三方程式庫、模型、字型和工具，授權列在 [THIRD_PARTY.md](THIRD_PARTY.md)。

Kacha 用 Remotion 做動畫。Remotion 有自己的授權（Remotion License），使用 Kacha 也要符合它的條件：

- 個人、員工 3 人以下的營利組織、非營利組織：可以免費使用，包含商業用途。
- 員工超過 3 人的營利組織：要向 Remotion 買公司授權（Company License），價格見 <https://www.remotion.pro/license>。
- 不可以複製或修改 Remotion 的程式碼，做成自己的版本販售、出租或再授權。

授權全文在安裝後的 `scenes/node_modules/remotion/LICENSE.md`。
