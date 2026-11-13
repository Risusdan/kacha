# Kacha

Kacha（喀嚓）把手機直拍的中文口播影片剪成有字幕的 Instagram Reels。你在 Claude Code（Anthropic 的命令列 AI 助手）裡用中文說要剪哪一支，Claude 照你的腳本粗剪、上字幕，先給你看分鏡稿。你同意之後，它才輸出成品。

## 它做的事

- 找出可以剪的地方：贅字（嗯、呃）、重複的字詞、講錯重來的段落、太長的停頓。腳本裡沒有的話預設保留，由你決定剪不剪。
- 字幕用你腳本裡的字，時間對到你的聲音。字幕是繁體中文，直接燒進畫面。
- 審閱頁照剪好的樣子播放影片和字幕。你在頁面上決定每一段剪或留、改字幕的字，看完按同意，它就輸出成品。
- 成品是 1080×1920、每秒 30 格的 mp4，大小在 300 MB 以內。剪好的片長上限是 9 分 3 秒，超過時不會輸出。

## 需要的環境

- Apple 晶片（M 系列）的 Mac。
- [Claude Code](https://code.claude.com/docs/en/overview)，以及能使用它的 Claude 帳號。
- [uv](https://docs.astral.sh/uv/)：Python 的套件和環境管理工具。
- ffmpeg-full：用 [Homebrew](https://brew.sh/) 安裝，指令是 `brew install ffmpeg-full`。
- 下載模型和輸出影片前，硬碟至少要有 15 GB 可用空間。

如果 ffmpeg 裝在別處，把環境變數 `REELS_FFMPEG_DIR` 設成放 `ffmpeg` 和 `ffprobe` 的資料夾。這個 ffmpeg 要能用 libass 燒字幕，也要有 VideoToolbox 的 H.264 和 HEVC 編碼器。

## 安裝

```sh
git clone https://github.com/Risusdan/kacha.git
cd kacha
uv sync
uv run pipeline setup
uv run hf download Qwen/Qwen3-ASR-1.7B
uv run hf download Qwen/Qwen3-ASR-0.6B
uv run hf download Qwen/Qwen3-ForcedAligner-0.6B
uv run pipeline doctor
```

- `pipeline setup` 下載字幕字型，以及剪片要用的模型和字典。
- 三個 `hf download` 把語音辨識模型下載到 Hugging Face 的快取資料夾，不需要 Hugging Face 帳號。剪片時不會自動下載它們，所以要先下載。平常用 1.7B 模型辨識，它執行失敗時改用 0.6B；ForcedAligner 找出每個字的時間。
- `pipeline doctor` 檢查環境，印出一行 JSON。`"ok": true` 表示可以開始剪片；`"ok": false` 時，`"warnings"` 列出缺了什麼。

## 剪一支影片

1. 在 `kacha` 資料夾裡建立 `studio/inbox/`，把影片和腳本放進去。
   - 影片：手機直拍的口播影片，例如 `.mov` 或 `.mp4`。
   - 腳本：你念的稿子，存成 UTF-8 編碼的文字檔，例如 `.md` 或 `.txt`。句子以「。」「！」「？」或換行分開。中括號裡的文字，例如 `[這裡停一下]`，是給自己看的備註：不算要念的話，也不會出現在字幕上。
2. 在 `kacha` 資料夾裡開啟 Claude Code，說「剪 inbox 裡的○○那支」。○○ 是影片的檔名或內容，例如「剪 inbox 裡的咖啡那支」。Claude 在 `studio/inbox/` 裡找這支影片和它的腳本，找不到或不確定時會問你。沒有腳本不能開始。
3. Claude 做好粗剪和字幕後，會在瀏覽器裡打開審閱頁，並傳一則短訊息，告訴你網址、片長和要你決定幾項。審閱頁只在這台電腦上使用。
4. 在審閱頁上看剪好的樣子：
   - 左邊的影片照剪完的樣子播，剪掉的地方直接跳過，字幕疊在上面。右邊是逐字稿，點任何一個字，影片就從那裡播。
   - 剪掉的地方和要你決定的地方都有標記。點一下，會看到 Claude 的建議和理由。
   - 切換一段剪掉或保留，影片馬上照新的決定播。
   - 要改字幕的字，按「改字幕」。

   想用講的，也可以在對話裡跟 Claude 說，例如「0:42 那段留著」或「1:48 那句字幕改成…」。頁面會在幾秒內跟著更新。
5. 看完按「同意，輸出成品」，再按「確定，輸出成品」。在對話裡回「可以」也算同意。成品 `final.mp4` 輸出完成後，頁面會播放預覽版，也有按鈕可以打開成品所在的資料夾。預覽版 `preview.mp4` 檔案較小，方便傳到手機上看，畫質不夠發布。

你同意之後，如果又改了任何地方，要再同意一次，才會輸出新的成品。審閱頁關掉 30 分鐘後會自己停止；要再看，請 Claude 重新打開。

- 接著做到一半的影片：說「繼續○○那支」。換了新的對話也可以。
- 記下長期偏好：說「以後都…」，Claude 會記下來，之後每一支影片都照做。

## 資料放在哪裡

素材和專案都放在 `studio/`，這個資料夾不會進 git。要放在別處，把環境變數 `REELS_STUDIO` 設成那個資料夾的路徑。

- `studio/inbox/`：你的影片和腳本。Claude 只讀，不改、不搬、不刪。
- `studio/projects/`：每支影片一個專案資料夾，名稱是日期加上英文代稱，例如 `2026-11-12-coffee`。成品在專案資料夾的 `out/` 裡。

## 授權

Kacha 以 Apache License 2.0 授權，全文見 [LICENSE](LICENSE)。用到的第三方程式庫、模型、字型和工具，授權列在 [THIRD_PARTY.md](THIRD_PARTY.md)。
