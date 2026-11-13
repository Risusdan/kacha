# 第 1 期審查留下的小問題（2026-11-12）

第 1 期每個 task 審查時，有一批問題當時判斷為小問題、延後處理，記在不進 git 的工作紀錄裡，共 108 條。這份文件把它們整理出來，並記下每一條怎麼處理。

用到的詞：

- **traceback**：Python 出錯時印出的錯誤堆疊。
- **JSON 結果行**：`pipeline` 每個指令正常會在 stdout 印出的那一行 JSON。
- **結束碼**：指令結束時回傳給 shell 的數字，0 是成功。

## 處理方式總覽

本輪 81 條已在分支 `fix/phase1-review-items` 修完，2026-11-12 併入 `docs/reels-workflow-design`，計畫見 `docs/superpowers/plans/2026-11-12-phase1-review-items.md`。本輪審查又留下 42 條小問題，見 H 節。第 1 期改版的審查再留下 1 條，見 I 節。

| 處理方式 | 條數 | 條目 |
|---|---:|---|
| 本輪修（分支 `fix/phase1-review-items`） | 81 | B1–B4、B6、B8–B11、B16、B17、B19–B29、C1–C10、D5、D9、E1–E4、F1–F17、F19–F23、G1–G6、G8–G15、G17–G23 |
| 已在第 1 期改版處理 | 11 | A1–A5、B5、B7、B12–B15（設計見 `docs/superpowers/specs/2026-11-12-phase1-revision-design.md`） |
| 用到再修 | 9 | D1–D4、D6–D8、D10、D11 |
| 不改，或先查證 | 7 | B18、E5–E7、F18、G7、G16 |

B23 原本是計畫規定的做法（授權清冊只比對套件名稱），使用者在 2026-11-12 決定改成連版本一起比對，所以列入本輪。

## A. 已在第 1 期改版處理

這幾條原本排在下一輪，要和字幕斷行、關卡 1 的流程一起處理（見 `docs/acceptance/2026-11-11-phase1.md` 的「帶到下一輪的待辦」）。第 1 期改版已經處理，設計文件是 `docs/superpowers/specs/2026-11-12-phase1-revision-design.md`，每一條附上對應的節。

- A1 口吃（我我我們、剪剪剪輯）被當成腳本外的話，不會建議剪，整句標「請聽」。**已在第 1 期改版處理**（`docs/superpowers/specs/2026-11-12-phase1-revision-design.md` 第 5.3 節）。
- A2「恩」（常見的「嗯」寫法）不在贅字清單（`pipeline/align.py` 的 `FILLER_SPELLINGS`），被當成腳本外的話，不會剪。**已在第 1 期改版處理**（`docs/superpowers/specs/2026-11-12-phase1-revision-design.md` 第 5.1 節）。
- A3 NG 段落裡有贅字時，和腳本的相似度會被拉低，結果被當成改詞重講，只列出來、不剪。算相似度前應先去掉贅字。**已在第 1 期改版處理**（`docs/superpowers/specs/2026-11-12-phase1-revision-design.md` 第 5.2 節）。
- A4 字幕按片語塞進一行，會留下只有 2 個字的短行。**已在第 1 期改版處理**（`docs/superpowers/specs/2026-11-12-phase1-revision-design.md` 第 4.1 節）。
- A5 分鏡稿的表格最小寬 620px，在手機上「請聽」和播放鍵那一欄一開始在畫面外。**已在第 1 期改版處理**（`docs/superpowers/specs/2026-11-12-phase1-revision-design.md` 第 6.4 節）。

## B. 影響成品或操作流程

- B1 `doctor` 檢查沒通過時，結束碼仍是 0，所以 `pipeline doctor && …` 在環境有問題時還是會往下跑。
- B2 `doctor` 把空的、或下載到一半的模型資料夾當成已安裝（應檢查 `snapshots/` 不是空的）。
- B3 用辨識文字當字幕的句子：一段裡只要有一個詞簡轉繁後長度會變，整段就改成逐字轉換，其他詞失去上下文（头发很干净 → 頭發很幹淨）。應只在那個詞的前後切開。
- B4 同一種句子裡，腳本外的話會被簡轉繁兩次，記錄的文字（`runs[].text`）可能和字幕不同（軟體／軟件）。
- B5 一串超過一行的英文（32 個字母以上）會超出畫面，沒有警告。**已在第 1 期改版處理**（`docs/superpowers/specs/2026-11-12-phase1-revision-design.md` 第 4.1 節）。
- B6 腳本裡的單星號斜體（`*很重要*`）會把星號顯示在字幕上；`***x***` 會留下 `*x*`。
- B7 半形的 `, ; : .` 不算斷句標點，會照原樣顯示，也可能斷在片語中間。reel skill 已經寫了「標點用全形」。**已在第 1 期改版處理**（`docs/superpowers/specs/2026-11-12-phase1-revision-design.md` 第 4.2 節）。
- B8 只有序號的一行（「1.」「10)」）不會被去掉，會變成一個多出來的句子。
- B9「1、2、3 都是。」會少掉第一個數字（清單記號規則的副作用）。
- B10 腳本裡 Markdown 的粗體記號和連結網址會留在句子文字裡，網址會被當成要對齊的英文字。
- B11 ½、㍿、m² 這類字經 NFKC 展開後，會變成幾個位置相同的比對單位。
- B12 停頓被旁邊的語音吃掉一部分後，可能剩 1–2 格長的小停頓，每個都在關卡 1 各佔一行。**已在第 1 期改版處理**（`docs/superpowers/specs/2026-11-12-phase1-revision-design.md` 第 5.6 節）。
- B13 本來就不在成品裡的候選（例如第一句之前的 NG）也被列成「剪掉」並算進數量。**已在第 1 期改版處理**（`docs/superpowers/specs/2026-11-12-phase1-revision-design.md` 第 6.4 節）。
- B14 句子被拿掉以後，它那段腳本外的話就沒有標記，也沒有播放片段。**已在第 1 期改版處理**（`docs/superpowers/specs/2026-11-12-phase1-revision-design.md` 第 6.4 節）。
- B15 只保留一句、而且頭尾兩邊都有狀況時，原因文字只提到其中一邊（播放片段的標示是對的）。**已在第 1 期改版處理**（`docs/superpowers/specs/2026-11-12-phase1-revision-design.md` 第 6.4 節）。
- B16 很短的句子，如果講的內容和下一句開頭很像，可能對不上（會標「請聽」）；贅字緊接在重講後面時，兩者會被併成一段，算成改詞重講。
- B17 改腳本、重跑 `transcribe` 之後，候選的編號可能變掉，`edit.json` 就過時了。目前只靠「從那一步重跑」和 `validate` 擋。
- B18 判斷可變幀率時用精確分數比較，幀率只差一點點的固定幀率檔可能被誤判。**不改，先查證**：這還是 HYPOTHESIS，要先找到一個被誤判的固定幀率檔。
- B19 素材短於 2 秒時一張畫面都不會抽，結果顯示「沒偵測到臉」。
- B20 缺臉部模型時，`prep` 要等音訊和 HDR 轉檔都跑完才報錯。
- B21 建專案時如果中途當掉，會留下做到一半的資料夾：名字被佔住，清單裡又看不到。
- B22 `status` 每次都會對整支原始影片算雜湊，大檔可能會慢。
- B23 `scripts/third_party.py` 只用套件名稱比對授權清冊，已登記的套件升級後換了授權不會被發現。改成連版本一起比對（使用者 2026-11-12 決定）。
- B24 `render` 被跳過時，回報的位元率是用現在的常數重算的，不是那個檔案實際用的位元率。
- B25 輸出檢查的途中出錯，會留下 `out/final.tmp.mp4`（下一次 `render` 會把它刪掉）。
- B26 影片串流沒有寫長度、音訊又比較長的 mkv，抽出的 wav 會比影片長。
- B27 語音辨識的快取裡如果有一個空的 JSON `{}`，之後每次讀都會失敗，要手動清掉快取才會好。
- B28 語音辨識剛寫完結果時按 Ctrl-C，結果會留著（程式說明寫的比實際保證的多）。
- B29 很長的一段腳本外的話（例如放錯腳本），對齊時間會是長度的平方：1500 個字要 7.4 秒。

## C. 出錯時印 traceback，不是 JSON 結果行

- C1 `main` 只接住 `PipelineError`，其他錯誤都會印 traceback；指令的回傳值裡如果有 `Path` 物件，輸出結果時會失敗。
- C2 回傳值裡多出來的鍵會蓋掉 `ok` 和 `error`；`PipelineError("x", message=...)` 這種寫法會引發 TypeError。
- C3 `pipeline -h` 只印純文字說明，看不到子指令清單。
- C4 `state.json` 壞掉會印 traceback；只要一個專案壞掉，`status` 就整個列不出來。
- C5 下載字型或模型時連線中斷（`http.client.HTTPException`，例如 IncompleteRead），會印 traceback。
- C6 語音辨識失敗時，訊息只留 log 的最後一行，不說是不是被系統的訊號結束（例如記憶體不足），也沒附 1.7B 的 log 路徑。
- C7 `edit.json` 巢狀太深，或整數超過 4300 位，會印 traceback，不是 `EditInvalid`。
- C8 範圍重疊時會把每一對都列出來，遇到很極端的檔案，錯誤訊息會非常長。
- C9 錯誤訊息直接放入原始值，長度沒有上限；鍵是空字串或含換行時，錯誤會沒有路徑或變成好幾行。
- C10 缺 Regular 字型時印 traceback（正常情況下 `setup` 和 `doctor` 會先確保字型在）。

## D. 第 1 期的正常流程碰不到

本輪只修 D5 和 D9，因為實際使用時會碰到：`edit.json` 是 Claude 寫的；不照提示手動跑 `render` 也做得到。其他條寫明什麼時候一定要修。

- D1 音訊比影片早開始時，`faces.json` 的時間 `t` 會提早（早了影片起點減容器起點）。**做任何依時間讀 `faces.json` 的功能之前要修**，例如跟著臉縮放；做法是 `-seek_timestamp 1 -ss <video_start_s + t>`。
- D2 讀語音辨識結果時，不會擋掉不合理的時間（無限大、結束早於開始、開始時間倒退）。**換語音辨識引擎、或讀別的來源的辨識結果之前要修。** 另外，辨識出來的字的時間如果重疊得很厲害，`storyboard` 會拒絕，訊息是 `sentence bounds in trim.json are out of order: run trim again`；重跑 `trim` 沒有用（`trim` 的結果只由輸入決定，輸入沒變，結果就一樣），所以碰到這個拒絕，就是該修 D2 的時候。
- D3 字幕顯示範圍以外的辨識文字是逐字簡轉繁的（钟 → 鍾）。**有程式要顯示或使用 `tokens[].text` 之前要修。**
- D4 用辨識文字的句子，句尾的切點可能落在一個只列出、不剪的候選裡（只在隨機測試資料裡出現過）。**在真實素材上出現時修。**
- D5 `edit.json` 裡有重複的鍵時，不會報錯，直接以最後一個為準。**本輪修。**
- D6 寬度為 0 的移除範圍會把片段切成相接的兩塊。**`trim.json` 會產生寬度 0 的範圍時要修**（目前不會）。
- D7 樣式設定裡 `captions.bold` 是 false 時，字級仍用粗體字型計算（目前兩個字型的度量相同）。**樣式允許非粗體字幕、或換字型之前要修。**
- D8 直接傳入不對齊格線的片段時，聲音和畫面可能差半格（正常流程產生的片段一定對齊格線）。**有程式不經 `build_segments` 直接產生片段之前要修。**
- D9 `gate_ok` 和 `render` 不檢查 `transcribe`、`trim` 是不是最新的。只有不照 `next` 的提示、手動跑 `render` 時才會用到過時的 `trim.json`。**本輪修。**
- D10 安全刪除拒絕符號連結迴圈這件事，靠的是 Python 3.12 的 `resolve()` 會丟例外；換 Python 版本可能就失效了。**升級 Python 版本之前要修**；做法是對路徑和每一層上層用 `os.path.islink` 檢查。
- D11 步驟紀錄照傳進來的寫法記下輸入路徑，傳相對路徑會照當下的工作目錄重查，同一個檔案兩種寫法會算兩次雜湊。目前的約定是一律傳絕對路徑。**新增使用 `run_step` 的步驟時，照這個約定；要放寬約定之前要修。**

## E. skill 和文件的文字不準確

- E1 reel skill 第 21 行說 `gate1_ok` 是「對現在的 `edit.json` 是否已同意」，其實現在看的是整份分鏡稿的輸入；storyboard skill 第 121 行也一樣不完整。
- E2 storyboard skill 說逐行模式會把整句一次顯示在一行，其實是一行一行輪流出現（27 個字的句子分三行）。
- E3 reel skill 只說明了 `doctor` 的字型、臉部模型、語音辨識模型三項檢查，沒提 asr_cli、libass、硬體編碼器。
- E4 `THIRD_PARTY.md` 先用了 LGPL、GPL，後面才給定義。
- E5–E7 計畫文件有三處和程式不一致：`doctor` 的輸出格式少了 aligner、`write_ass` 少了 font 參數、select 還是舊的 `gte(t,…)` 寫法。**不改**：計畫是歷史紀錄。

## F. 測試缺口

- F1 測試把假指令註冊進全域的指令表（`cli._COMMANDS`），跑完沒有清掉。
- F2 測試環境找模型快取時，不看 `XDG_CACHE_HOME`。
- F3 檢查「家目錄已換成假的」的那個斷言太弱。
- F4 拒絕「/」的兩個測試沒有比對錯誤訊息。
- F5 `run_step` 有兩個分支沒有測試（重讀巢狀的 state、`_recorded_path` 在專案外的退路）。
- F6「inbox 沒被動到」的測試沒檢查有沒有多出檔案。
- F7 probe 沒有測試釘住音訊延遲、雙音軌、旋轉 270 度這三個值。
- F8 缺 aligner 的測試沒證明 `ok` 是 false 只因為缺 aligner。
- F9 ffmpeg 失敗後不會留下暫存檔和紀錄，這點只手動驗過。
- F10 超過 2 聲道的混音、音訊時間戳中斷，都只手動驗過。
- F11 模型載入失敗後，OpenCV 的記錄等級有沒有恢復，沒有測試。
- F12 HDR 的臉部偵測測試，沒有檢查讀的是 HDR 工作檔。
- F13 非 9:16 素材的臉框換算，只手動驗過。
- F14 語音辨識的幾種狀況沒測：被訊號結束、stderr 很大、零長度的片段、兩段音訊共用快取。
- F15 備援測試給 0.6B 的假程式 2 秒逾時，機器忙的時候可能偶爾失敗。
- F16 Meijia 測試的預期值是照舊公式寫的，沒有改用新的輔助函式。
- F17 分鏡稿丟掉小碎片的分支沒有專門的測試；缺 `probe.json` 時只有通用的錯誤訊息，不會說「先跑 prep」。
- F18 整套測試約 265 秒，其中有 14 次真正的渲染。**不改**：不是缺陷，等測試慢到影響開發再處理。
- F19「影片比音訊晚開始」的素材，沒有從頭到尾渲染過一次的測試。
- F20 `scripts/third_party.py` 有幾個錯誤分支沒有測試（`UV` 指到不存在的檔案、`marker_applies`、匯出失敗、清冊讀不到）；有一個測試不在 `uv run` 下執行會失敗。
- F21 測試素材沒有先檢查臉部模型在不在，缺模型時看不到「請跑 pipeline setup」的提示。
- F22 流程測試最後重跑 `prep` 時，只檢查有沒有跳過，沒檢查結束碼。
- F23「成品太長、拒絕渲染」的測試，看不出步驟紀錄有沒有被刪掉。

## G. 程式整潔

- G1 `tests/test_project.py` 的測試檔名用 `.DS_Store`，會透露開發平台。
- G2 `tests/test_project.py` 有幾個多餘的測試，還有 `write_text("x") and {}` 這種難讀的寫法。
- G3 字型檔名同時寫在 `STATIC_FONTS` 和 `style.json` 兩個地方。
- G4 呼叫 ffprobe 時沒有設逾時；另外有幾處多餘的 `Path()`，測試會在 `tests/.cache` 留下空的 log 檔，setup 之前跑字型和模型的測試會丟 FileNotFoundError。
- G5 轉檔函式從參數清單的最後一個取暫存檔路徑，靠的是隱含的約定；偵測頭尾聲音的兩段程式重複。
- G6 `pipeline/script.py` 和 `tests/test_script.py` 裡直接放了看不見的 BOM 字元。
- G7 2026 的第一種念法是「二千零二十六」，「兩千」排在第二。**不改**：不影響任何輸出。
- G8 `pipeline/script.py` 的說明有一行 164 個字元。
- G9 數字解析有一個多餘的條件（`textnorm.py` 的 `1 <=`），「0.5,000」這種輸入沒有測試。
- G10 產生測試音檔的 `make_fixture.sh`：少了 `--frozen`，可能改到 lock 檔；直接覆寫 `fixture.wav`；註解把峰值寫成 -50 dBFS；`TMPDIR` 沒設時退回 `/tmp`。
- G11 長度被壓成 0 的英文字，來源仍標成 asr。
- G12 `pipeline/trim.py` 的區域變數 `trim` 和模組裡的 `trim()` 函式同名。
- G13 `tests/helpers.py` 裡 `flat_envelope` 的說明已經不正確。
- G14 `snap_cut` 的 `silent` 判斷和 `floor_db` 參數，正式流程都沒用到。
- G15 `pipeline/captions.py` 的說明有一句不完全正確（「一個英文字或數字是一個單位」）。
- G16 讀字型度量的程式重複一次。另有一個 HYPOTHESIS：libass 可能用 OS/2 win 度量而不是 hhea，對目前的字型沒有差別。**不改，先查證**：要先找到兩組度量不同的字型。
- G17 `pipeline/storyboard.py` 的正規表示式裡直接放了看不見的 U+2028/U+2029 字元。
- G18 `pipeline/storyboard.py` 的 `TRIM_PAD_S = 1.0#` 在註解符號前少一個空格。
- G19 分鏡稿和 `edit` 模組各有一份相同的換算格數函式（`_frame`）。
- G20 `pipeline/render.py` 引用了 `edit` 模組的私有函式 `_load_document`。
- G21 程式說明沒寫「真正執行的 `render` 一開始就會刪掉舊的 `final.mp4`」。
- G22 同意紀錄裡的 `edit_sha256` 已經沒有地方讀。
- G23 位元率的「12M」寫法假設上限是整數 Mbps；`build_ffmpeg_args([])` 會除以零；剛好 4 Mbps 的邊界沒有測試；位元率常數命名不一致。

## H. 本輪審查留下的小問題

本輪每個 task 的審查和最後的全分支審查，判斷下面這些可以等，所以沒有修。它們原本記在不進 git 的工作紀錄裡，2026-11-12 搬到這裡。行號會變，所以用檔案和函式名稱指位置。

### 對齊（原本跟 A3 一起做；A3 已在第 1 期改版處理，H1 維持待辦，見設計文件第 5.7 節）

- H1 腳本句前面有「呃」、重講的段落又以「那個」開頭時（「…剪掉呃那個方法那個方法…」），對齊會把「呃那個方法」算給被跳過的那一句，當成那句有講。結果只有「那」一個字被標成腳本外的話；同樣的情況換成「接下來要介紹第二個方法」，連這個標記都沒有。
- H2 對齊時被算進重講段落的贅字（例如「第三個方法呃」的「呃」）留在段落裡，不另外切出來。要分辨它和段落本身的「那個」「就是」，就是 A3 的問題。整段重講仍照預設剪掉。
- H3 「贅字緊接在重講後面時自成一段」（B16 第 2 部分）只用在講到一半的重講。整句被跳過的那一段，旁邊的贅字仍會併進那段，而且沒有測試釘住這個行為。
- H4 簡轉繁時整段一起轉的片語，最長 10 個字（`pipeline/align.py` 的 `_PHRASE_CHARS`），這個值取自目前字典裡、轉換後長度會變的最長片語。`opencc-python-reimplemented` 在 `pyproject.toml` 沒有固定版本（lock 檔是 0.1.7），升級後字典如果多了更長的片語，那個片語會退回逐字轉換。應加一個測試，從字典算出最長的片語，升級時直接失敗。
- H5 英文字的時間上限測試在舊程式上也會通過，證明不了片語最長 10 個字這個上限有作用。

### 語音辨識（`pipeline/asr.py`）

- H6 快取的結果寫著 `truncated: true`、`chunks` 卻不是清單時，會被當成格式壞掉而重跑，不會報「結果被截斷」。辨識引擎一定寫清單，只有人造的檔案會這樣。修法是先讀 `truncated` 再讀 `chunks`。
- H7 剛跑出的結果被判定格式壞掉時，錯誤訊息沒有附 log 的路徑。
- H8 失敗訊息引用 log 的最後 5 行，每行沒有長度上限。
- H9 1.7B 模型失敗、退回 0.6B 又被截斷時，只回報截斷，不提 1.7B 失敗過。這是「截斷就是最終結果」這個決定的結果。
- H10 格式檢查分在兩處：`run_asr` 收下任何 JSON 物件，讀的時候才檢查格式。剛寫完的結果是 `{}`、或檢查途中按了 Ctrl-C，檔案會留著，下次讀到時重新檢查並刪掉。審查者建議接受這個做法。
- H11 測試 `test_run_asr_failure_reports_exit_code_and_last_line` 的名字已經不符（現在引用 5 行）；`LOG_TAIL_LINES` 放在模組中間。

### 專案狀態（`pipeline/project.py`）

- H12 `load_state` 只檢查到「步驟紀錄和關卡是不是物件」，更深的欄位不檢查。例如某個步驟紀錄的 `inputs` 是清單時，`status <專案>` 會出現 `unexpected error`。不帶專案名稱的 `status` 清單不受影響，會把它列成有錯的專案。
- H13 步驟被跳過時，程式把目前的檔案大小和修改時間寫回紀錄，但寫回的是算雜湊之前讀到的 state.json。這段時間如果有別的程式寫了 state.json，那筆寫入會被蓋掉。`pipeline` 一次只跑一個指令。
- H14 專案資料夾搬過位置之後，紀錄裡的輸入路徑還是舊的，每次 `status` 都會重算雜湊。結果正確，只是慢。
- H15 `edit.json` 改成另一版、跑過 `validate`、再改回使用者同意的那一版時，`status` 的 `next` 和 `gate1_ok` 說法不一致。不影響結果：使用者同意的分鏡稿就是磁碟上那一份。
- H16 「分鏡稿用到哪些輸入」的查法在 `project.py` 裡有三份；`edit_stage` 的說明寫了一個沒有程式檢查的前提；`tests/test_project.py` 的 `_checked` 有一個用不到的 `**changes` 參數；`tests/test_flow.py` 本輪新加的斷言沒有看過失敗（別的測試有涵蓋）。
- H17 有幾個測試靠「兩次同樣大小的寫入之間時鐘有走」。用修改時間判斷檔案沒變的捷徑，本身也抓不到同一個時鐘刻度內的改寫（這是計畫規定的做法）。
- H18 `create_project` 碰到 `FileExistsError` 時的註解（另一個程式正在建同一個專案）會誤導：前一步剛把留下的資料夾刪掉，這個分支幾乎碰不到，也沒有測試。

### 指令列（`pipeline/cli.py`）和測試環境

- H19 `_parse` 回傳 `Namespace` 或整數，`main` 再判斷是哪一種；在 `main` 直接接住 `SystemExit` 會比較單純。
- H20 `test_test_commands_do_not_leak` 單獨跑（`-k`）時一定通過；這一對測試靠它們在檔案裡的先後順序。
- H21 沒有測試證明 Ctrl-C（`KeyboardInterrupt`）不會被吞掉，目前是程式結構保證的。
- H22 Hugging Face 快取和假家目錄的測試，從帳號資料庫取真的家目錄，conftest 用的卻是 `Path.home()`。在 HOME 不是帳號家目錄的環境（容器、sudo），或 TMPDIR 在真的家目錄底下時，測試會失敗。應加註解說明這是故意的。
- H23 F21 的測試沒有證明「檢查臉部模型」發生在產生測試素材之前（可以讓 `meijia_video` 故意失敗來釘住順序）。
- H24 常數 `SOME_HOME` 放在兩個測試中間；只把 `XDG_CACHE_HOME` 設成空字串的情況沒有測試；conftest 在模組層級 import `helpers`，每次跑測試都要載入 numpy 和 soundfile，`helpers` 一出錯，整個測試收集就失敗。

### 字型、模型和 `doctor`（`pipeline/assets.py`）

- H25 `test_flow_helpers_need_the_fonts_before_any_work` 比對的是 "pipeline setup"，臉部模型的檢查先失敗也會符合；應改比對 "font"。
- H26 測試用的假語音辨識模型是一般檔案；真的模型快取裡，`snapshots/` 底下是指向 `blobs/` 的符號連結。沒有測試涵蓋指向存在檔案的連結和斷掉的連結。
- H27 `setup`（`_font_targets`）只取 `font_path(...)` 的檔名，寫到 `fonts/` 底下；`doctor` 檢查的是 `font_path(style, key)` 的完整路徑。只有 `style.json` 把字型直接放在 `fonts/` 底下時，兩邊指的才是同一個檔案。
- H28 reel skill 說 `doctor` 任何一項不通過 `ok` 就是 `false`，但剩餘空間 `free_gb` 不足只會加一條警告，不影響 `ok`。

### 前處理（`pipeline/prep.py`、`pipeline/media.py`）

- H29 B20 的測試只數 `run_ffmpeg` 的呼叫次數，模型檢查移到 probe 之後也會通過；`prep` 被跳過時缺模型會怎樣，沒有測試釘住。
- H30 `media.py` 仍然要求容器有長度；容器和影片串流都沒寫長度的素材，其實可以從封包量出長度（第 1 期就是這樣）。
- H31 沒有 `duration_time` 的封包算 0 長度（少一格，在容許範圍內）；量封包時會讀完整個串流，只受 60 秒逾時限制（可以用 `-read_intervals` 限制範圍）。

### 腳本和字幕（`pipeline/script.py`、`pipeline/captions.py`）

- H32 Markdown 記號緊貼中文（`一*二*三` 變成「一二三」）的情況沒有測試；字幕沒有含連結的測試（只有審查時手動驗過）。
- H33 舊的 `transcript.json` 句子文字裡如果還有連結，網址會黏在一起顯示（看這裡httpsexamplecom）；巢狀的記號只去掉內層。
- H34 `markdown_marks` 每次呼叫都重建合併的正規表示式，應只編譯一次。
- H35 `![圖](x.png)` 會變成「!圖」並結束一句；`**（放圖）**` 會變成要講的一句（判斷整行是括號說明，發生在去掉記號之前；不是本輪造成的）；單獨的 `**` 現在會顯示在字幕上（和舊規則相反）。

### 音訊、`edit.json`、分鏡稿、輸出

- H36 `tests/test_audio.py` 有一個測試靠同分時的取捨，斷言剛好 1.0；寫成 ±0.15 的範圍才看得出用意。`is_silent` 遇到空包絡的分支沒有測試（之前就是這樣）。
- H37 `edit.py`：`load_edit` 沒有正式的呼叫者（`require_valid` 用 `_read_edit`）；沒有範圍重疊時，重疊檢查仍要比較「範圍數的平方」次；`_unpair` 每個節點都組一次路徑字串；樣式檔讀不到時的錯誤不寫檔名（刻意的，限制輸出長度）；截斷一個 1 萬字的鍵時印成 `edit['yyy…]`，少了收尾的引號；`render.py` 自己用 FPS 換算格數（第三份，不在 G19 範圍內）；兩處 `raise … from None` 和 `from error` 寫法不一致。
- H38 `storyboard.py`：沒有測試證明「句子邊界順序錯亂」的拒絕會保留既有的分鏡稿紀錄（缺 probe 的拒絕有測）；缺 `faces.json` 時仍是 `run_step` 的通用訊息（`input 'faces' ... not found`），不像缺 `probe.json` 時直接說先跑 prep；一段說明的措辭（"no sentence may end its reel before it starts it"）；`_document` 和 `edit.load_document` 重複（第 1 期就有）；`AUDIO_BITRATE = "64k"` 是字串，和 `render.py` 裡同名的整數不同。
- H39 `render.py`：缺粗體字型時，原本會被跳過的 `render` 也會被擋下（字型檢查在判斷要不要跳過之前）；回報的 `video_bitrate` 是再呼叫一次 `video_bitrate()` 算的，不是讀回傳給 ffmpeg 的 `-b:v`；4 Mbps 邊界的測試在超過 1 秒時拒絕，不是 0.1 秒；B25 保留 `OSError` 的型別，會變成 `unexpected error`；`video_bitrate(0)` 仍會除以零（正常流程碰不到）。

### 授權檢查（`scripts/third_party.py`）

- H40 清冊「版本或來源」欄是空白或有反引號時，讀出的版本是空字串或帶反引號的字串（目前清冊沒有這種列）；`soundfile 0.14.0` 那個測試釘住的是一個退化的解析結果；沒有從 `main` 跑的版本不符測試。

### skill 文字（`.claude/skills/reel/SKILL.md`）

- H41 `asr_cli` 不通過時「經使用者同意跑 `uv sync`」這個補救沒有實際驗證過（HYPOTHESIS：要在缺 `mlx-qwen3-asr` 的環境跑一次才能確認）；「不在專案的 Python 環境裡」對第二種原因描述得太寬。
- H42 skill 把 `<步驟> is out of date` 解釋成「那一步的輸入檔變了」，但那一步的輸出檔不見時也會出現同一句；skill 用了 stderr 這個詞，沒有定義；指向 D2 的那一句寫得像給開發者看。

## I. 第 1 期改版審查留下的待辦

第 1 期改版（`docs/superpowers/specs/2026-11-12-phase1-revision-design.md`）的全分支審查找到這一條。現在先由 skill 補上一個步驟，程式之後再改。

- I1 講者在一句前面講的腳本外的話或改詞重講，候選歸在那一句，卻在那一句的切點（`cut_in`）之前。用 `cuts.extra` 移除那一句時，移除的範圍從 `cut_in` 開始，這些話留在成品裡。現在由 reel 和 storyboard 兩個 skill 叫 Claude 把這些候選另外放進 `cuts.accept`。修法：`pipeline/edit.py` 的 `build_segments` 讓 `cuts.extra` 的範圍往前延伸，蓋過範圍第一句在 `cut_in` 之前的插入段；修好之後，skill 的這一步就不需要了。
