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
4. **安裝 git、uv、ffmpeg-full 和 node。** 執行 `brew install git uv ffmpeg-full node`。uv 管理 Kacha 用的 Python 和套件；ffmpeg-full 處理影片和聲音；node 是 Node.js，用來做動畫卡片。
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

## 剪一支影片

1. 把影片和腳本放進 `kacha` 資料夾裡的 `studio/inbox/`。沒有這個資料夾就建立它。
   - 影片：手機直拍的口播影片，例如 `.mov` 或 `.mp4`。
   - 腳本：你念的稿子，存成 UTF-8 編碼的文字檔，例如 `.md` 或 `.txt`。句子以「。」「！」「？」或換行分開。中括號裡的文字，例如 `[這裡停一下]`，是給自己看的備註：不算要念的話，也不會出現在字幕上。
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

素材和專案都放在 `studio/`，這個資料夾不會進 git。要放在別處，把環境變數 `REELS_STUDIO` 設成那個資料夾的路徑。

- `studio/inbox/`：你的影片和腳本。Claude 只讀，不改、不搬、不刪。
- `studio/projects/`：每支影片一個專案資料夾，名稱是日期加上英文代稱，例如 `2026-11-12-coffee`。成品 `final.mp4` 和預覽版 `preview.mp4` 在專案資料夾的 `out/` 裡。

## 授權

Kacha 以 Apache License 2.0 授權，全文見 [LICENSE](LICENSE)。用到的第三方程式庫、模型、字型和工具，授權列在 [THIRD_PARTY.md](THIRD_PARTY.md)。

Kacha 用 Remotion 做動畫。Remotion 有自己的授權（Remotion License），使用 Kacha 也要符合它的條件：

- 個人、員工 3 人以下的營利組織、非營利組織：可以免費使用，包含商業用途。
- 員工超過 3 人的營利組織：要向 Remotion 買公司授權（Company License），價格見 <https://www.remotion.pro/license>。
- 不可以複製或修改 Remotion 的程式碼，做成自己的版本販售、出租或再授權。

授權全文在安裝後的 `scenes/node_modules/remotion/LICENSE.md`。
