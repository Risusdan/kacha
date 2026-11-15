# 動畫地基設計（Remotion 和第一批 3 張卡）

- 日期：2026-11-15
- 狀態：待審閱（使用者授權：設計、計劃、實作都由 Claude 自己審，合併前停下來問）
- 依據：
  - 原設計 `docs/superpowers/specs/2026-11-11-reels-workflow-design.md`：7.2（`scenes` 的寫法和時間錨點）、7.3（語意標籤和 13 張場景卡）、7.4（`validate` 的規則）、8.5（場景渲染和快取）、8.4（字幕帶）；
  - 第 0 期實測 `docs/spikes/2026-11-11-phase0-results.md`：渲染器用 Remotion（4.0.542），透明影片用 ProRes 4444；
  - 使用者 2026-11-15 的選擇：推近之後做動畫地基；公開版的 README 和 `THIRD_PARTY.md` 寫明 Remotion 的授權條件；推近合併後不用重做 test001 的分鏡稿給他看，直接做這一項。
- 範圍：第 2 期的第二個子項目。

## 1. 目標

Claude 在 `edit.json` 寫 `scenes`，`render` 就把動畫卡片渲染成透明影片，疊在口播畫面上，字幕照樣在最上層。分鏡稿看得到哪一句出現哪張卡、上面寫什麼。

成功的樣子：

- 沒有 `scenes` 的專案，成品和現在一模一樣，也不用跑 Node.js；
- 三張卡：`keyword-pop`（重點字）、`number-slab`（數字卡）、`bullet-list`（清單，一項一項出現並打勾）；
- 卡片的顏色、字型、圓角都來自風格設定檔，卡片避開字幕帶；
- 同一張卡、同樣的參數和長度，第二次輸出不重新渲染；
- 渲染逐格重現：同樣的輸入，每一格都一樣。

三張卡的理由：它們只要文字或數字，對到最常用的語意標籤（`hook`、`claim`、`warning`、`number`、`list`），也涵蓋三種動畫做法（一塊彈出、兩層依序、多項錯開）。`bullet-list` 的打勾樣式，是使用者想要的效果之一。

## 2. 這次不做

- `full` 和 `split` 版面、B-roll、螢幕錄影、音效、音樂；
- 關卡 2（樣張網頁）、場景型錄網頁、`scene-catalog` skill、`scene-designer`：三張卡的寫法先寫在 `storyboard` skill 裡；
- 其他 10 張卡、大衝擊效果的卡；
- 審閱頁上播放真的動畫：審閱頁只在動畫出現的時段標出卡片名稱和文字；
- 依臉的位置決定卡片放哪裡：卡片固定放在風格設定檔的動畫區。

## 3. Remotion 專案 `scenes/`

- `scenes/package.json` 鎖定確切的版本：`remotion`、`@remotion/bundler`、`@remotion/renderer` 都是 4.0.542（第 0 期實測的版本），`react` 和 `react-dom` 是 19.3.1。`scenes/package-lock.json` 進 git，安裝一律用 `npm ci`。
- 不升到 Remotion 5.0：5.0 還沒發布（2026-11-15 查 PR 3750 仍未合併），它的新條款要每個使用者接受 Remotion 的服務條款，那份條款還沒讀過。4.x 的條款清楚（見 10）。
- `scenes/src/index.jsx` 註冊根元件；`Root.jsx` 每張卡一個 composition，1080×1920、30 fps，長度由參數決定（`calculateMetadata`）。
- `scenes/src/cards/<卡片>.jsx`：每張卡一個檔案，畫面只由格數和參數決定，不用 `Date.now()` 和 `Math.random()`。
- `scenes/src/timing.js`：卡片共用的時序函式（進場、退場、錯開），純函式，有 Node 測試。
- `scenes/render.mjs`：讀一份工作清單（JSON），打包一次（`bundle`，`publicDir` 指到風格設定檔的資料夾，字型就從那裡載入），再逐一渲染：`renderMedia`，`codec: "prores"`、`proResProfile: "4444"`、`imageFormat: "png"`、`pixelFormat: "yuva444p10le"`、`colorSpace: "bt709"`。每支先寫成 `.part.mov`，完成才改名；每完成一支印一行 JSON。出錯就印錯誤、以非 0 結束。
- 不傳 `licenseKey` 給 `renderMedia`：沒有 `licenseKey` 時它不送任何使用紀錄；傳 `"free-license"` 反而會送一筆到 Remotion 的伺服器（4.0.542 的 `render-media.js`）。
- 打包的結果（約 54 MB，含字型的副本）寫在系統的暫存資料夾（`TMPDIR`），Remotion 不會刪。`pipeline` 執行 `render.mjs` 時把 `TMPDIR` 指到專案的 `work/scenes/tmp/`，跑完用 `safe_rmtree` 刪掉。打包工具的快取在 `scenes/node_modules/.cache/`（約 74 MB），留著讓下次打包快一點（快取在的時候，打包加上渲染三張 1.5 到 2 秒的卡，實測 6 秒）。
- Remotion 從工作資料夾往上找第一個 `package.json`，在它旁邊的 `node_modules/.remotion/` 找瀏覽器；找不到 `package.json` 就用工作資料夾的 `.remotion/`，瀏覽器不在就自己下載（約 459 MB）。所以 `render.mjs` 一開始就切到 `scenes/`；`scenes/node_modules/.remotion/` 沒有瀏覽器時，它印錯誤、叫人跑 `pipeline setup`，不自己下載。
- 瀏覽器用 Remotion 預設的 Chrome Headless Shell，裝在 `scenes/node_modules/.remotion/`，由 `pipeline setup` 下載。
- 字型：卡片用 `@font-face` 載入風格設定檔的字型檔，載完才開始渲染（`delayRender`）。

## 4. 風格設定檔

`style.json` 加 `scenes`：

```json
"scenes": {
  "zone": {"x0": 65, "x1": 1015, "y0": 269, "y1": 1040},
  "colors": {"card": "#FFFFFF", "text": "#111111", "accent": "#FFD400", "muted": "#5C5C5C"},
  "radius_px": 32
}
```

- `zone` 是動畫區：卡片只畫在這個範圍裡。`load_style` 檢查它在安全區裡，而且在字幕帶上面（`y1` 不超過兩行字幕時第一行的頂端）。
- 卡片的顏色、圓角、字型全部從這裡和 `fonts` 取，卡片程式裡不寫死。

## 5. `edit.json` 的 `scenes`

```json
"scenes": [
  {"id": "sc1", "card": "number-slab", "at": {"s": "s03", "char": 4}, "until": {"s": "s03", "end": true},
   "params": {"value": "兩小時", "label": "一天多撐"}}
]
```

- `id`：不重複的字串。`card`：三張卡之一。`layout` 可以省略；寫了只能是 `"overlay"`（`full`、`split` 之後的期別才有）。
- `at`：`s` 是句子代號，`char` 是這一句字幕文字（`transcript.json` 的 `text`）的第幾個字，從 0 起算，省略就是 0。動畫在這個字開始講的時候出現。
- `until`：`{"s": …, "end": true}` 到那一句講完，或 `{"s": …, "char": n}` 到第 n 個字講完。不能在 `at` 之前。
- `params`：每張卡自己的參數（見 9）。字寬照字幕的算法：全形字算 1，半形字算 0.5。
- `validate` 擋下的錯誤：格式、未知的卡、參數不合、卡片上的一段字畫出來比動畫區寬（實作時加，見 12）、風格沒有動畫區、`id` 重複、句子不是講者有講的句子、字的位置超出那一句、`until` 在 `at` 之前、兩段動畫的時間重疊、同一張卡連用 3 次（7.4 第 3 條）、大衝擊效果超過 3 次（7.4 第 5 條；這三張都不是）。這些都只看 `edit.json` 和句子的順序，和剪接無關。
- 跟剪接有關的只列在 `warnings`，不擋下：動畫用到的句子被拿掉了（這段動畫不出現）；剪完之後這段動畫短於 1 秒。理由和 `punch_in` 一樣：審閱頁切換剪接或拿掉一句時不改 `scenes`，擋下的話，頁面上的改動會被拒絕。
- 第 1 期的 `validate` 把 `scenes` 當成之後期別的鍵擋下；這次改成接受。`edit.json` 的 `version` 仍是 1。

## 6. 時間

- 出現的時間：`at` 那個字所在的單位（`units` 裡 `span` 包含它的那一個）開始講的時間；結束的時間：`end` 是那一句最後一個單位講完，`char` 是那個字所在的單位講完。
- 兩個時間用 `map_time` 換成成品裡的時間，再換成格數（30 fps）。時間落在剪掉的地方，就是下一段的開頭。
- 一段動畫可以跨過剪接點：成品裡的長度就是兩個時間之差。

## 7. 輸出

- `pipeline/scenes.py`：算出每段動畫在成品裡的起始格和格數。`pipeline/scene_render.py`：渲染沒有快取的，回傳每段的影片檔（實作時分成兩個檔案：`edit.py` 檢查 `scenes` 時要用前者，不該跟著載入渲染的部分）。
- 快取：`work/scenes/<雜湊>.mov`。雜湊涵蓋卡片名稱、參數、格數、風格設定檔（`style.json` 和字型檔）和 `scenes/` 的原始碼（`src/` 底下的檔案、`render.mjs`、`package-lock.json`）。檔案還在、雜湊一樣就不重新渲染。
- `render` 的濾鏡串：`zoompan` 之後，每段動畫一個 `overlay`：動畫影片先 `setpts=PTS-STARTPTS+起始格/30/TB` 移到它在成品裡的時間，`overlay` 用 `eof_action=pass`，動畫結束後口播畫面照常；最後才燒字幕。
- 渲染之前檢查參數裡每個字都在字型裡，缺字就拒絕（和字幕一樣）。
- `render` 的輸入加上 `scenes/` 的原始碼，卡片改了，成品要重做。沒有 `scenes` 的專案不讀 `scenes/`，也不跑 Node.js。
- `doctor` 多三項：`node`（找得到，版本 18.18 以上）、`remotion`（`scenes/node_modules` 裝的是鎖定的版本）、`remotion_browser`（Chrome Headless Shell 在）。`setup` 多兩步：`npm ci`、下載瀏覽器。

## 8. 分鏡稿和審閱頁

- 分鏡稿句子表加一欄「動畫」：在動畫出現的那一句，寫卡片的中文名稱、文字和成品裡的時間，例如「數字卡：兩小時／一天多撐（0:12.0 到 0:14.5）」。摘要加一行：動畫幾段。不出現的動畫照 `warnings` 列在警告裡。
- 審閱頁：資料加 `scenes`（原始時間的起訖和標籤）；播到那段時，在影片上方顯示標籤，例如「動畫：數字卡　兩小時／一天多撐」。審閱頁不改動畫：要改，在對話裡說。
- 動畫的參數和時間只由 `edit.json`、`transcript.json`、`trim.json` 決定，這三個檔案本來就綁在關卡 1 的同意上。

## 9. 三張卡

共同的時序（不得降檔）：進場 12 格（0.4 秒），退場 8 格；動畫短於 30 格時，進退場按比例縮短。卡片水平置中，貼著動畫區的上緣。

| 卡片 | 中文名稱 | 參數 | 樣子 |
|---|---|---|---|
| `keyword-pop` | 重點字 | `text`：1 到 10 個字寬 | 強調色的圓角塊，裡面一行粗體字；進場從 0.6 倍彈到 1 倍（彈簧），退場淡出並縮到 0.9 倍 |
| `number-slab` | 數字卡 | `value`：1 到 6 個字寬；`label`：0 到 12 個字寬，可以省略 | 白色圓角卡，大字的數字用文字色，下半部襯一條強調色的螢光筆底線，下面一行說明；數字從下方滑入，說明晚 6 格淡入 |
| `bullet-list` | 清單 | `items`：2 到 4 項，每項 1 到 12 個字寬 | 白色圓角卡，每項前面一個勾；各項在進場後依序出現，間隔平均分配在前半段，勾在項目出現後 6 格內畫完 |

三張都不是大衝擊效果（`impact: false`）。

## 10. 授權

- Remotion 4.x 的條款（GitHub main 分支的 `LICENSE.md`，2026-11-15 讀）：個人、3 人以下的營利組織、非營利組織可以免費使用，包含商業用途；更大的營利組織要買 Remotion 的公司授權。不可以拿 Remotion 的程式碼做成衍生產品販售或再授權。
- `README.md` 和 `THIRD_PARTY.md` 寫明這些條件，公開版也一樣。
- `scripts/third_party.py` 加上 `scenes/package-lock.json` 的 npm 套件：授權在允許清單上的通過；其他的（包括 Remotion 的套件）要在 `THIRD_PARTY.md` 有那個版本的列，寫明讀過條款、可以商業使用。
- Chrome Headless Shell 不是 npm 套件：下載之後讀它附的授權檔，記進 `THIRD_PARTY.md`。讀不到或不能商業使用，停下來問使用者。

## 11. 測試

行為的改動先寫會失敗的測試。範例句子一律自己編。

- `validate`：接受三張卡；擋下 5 列的每一種錯誤；被拿掉的句子和短於 1 秒的動畫通過，列在 `warnings`；審閱頁上讓一段動畫不出現的改動照樣存得進去。
- 時間：字的位置換成成品裡的格數，跨過剪接點、落在剪掉的地方。
- 快取：雜湊涵蓋的每一項改了都重新渲染，都沒改就不渲染。
- 真的渲染（Node.js 和瀏覽器都要裝好）：每張卡輸出的格數對、四個角是透明的、卡片中央不透明、同樣的輸入兩次輸出逐格相同。
- 輸出：濾鏡串的樣子；用合成畫面和真的動畫跑 ffmpeg，動畫那幾格的動畫區有變、其他格沒變、字幕還在。
- 時序函式（Node 測試）：進場、退場、短動畫的縮短、錯開。
- 分鏡稿：動畫欄和摘要；審閱頁：資料和標籤（Node 測試）。

## 12. 決定紀錄

Claude 自己決定的，使用者合併前可以推翻：

| 決定 | 為什麼 | 錯了的代價 |
|---|---|---|
| 第一批三張卡：重點字、數字卡、清單 | 只要文字或數字，對到最常用的語意標籤，涵蓋三種動畫做法（1） | 使用者想先做別張：換卡不影響地基 |
| 數字卡的數字用文字色加強調色底線，不直接用強調色（實作時改） | 強調色 `#FFD400` 在白卡上對比只有約 1.4:1，大字也看不清楚 | 使用者想要黃字：改卡片的底色，或換一個深一點的強調色 |
| 動畫用 BT.709、有限範圍編碼（`colorSpace: "bt709"`，實作時加） | Remotion 4 預設用 BT.601 轉成 YUV，不標記色彩矩陣。ffmpeg 9 的 `overlay` 把沒標記的當成 BT.601，換算成口播影片的 BT.709，所以預設的動畫疊出來顏色也對（實測強調色 `#FFD400` 疊在 BT.709 的灰底上，解出 (252, 211, 0)），但這靠 ffmpeg 怎麼看待沒標記的檔案。標成 BT.709，矩陣是明確的，疊在 BT.709 的影片上不用換算（實測 (253, 210, 0)） | 無：Remotion 用 `zscale` 換算，透明度和逐格相同都實測過 |
| 鎖 Remotion 4.0.542，不等 5.0 | 第 0 期量過這一版；5.0 未發布，新條款要接受還沒讀過的服務條款 | 5.0 發布後要升級時，重讀條款、重新實測 |
| 一次打包、在同一個 Node 程序渲染所有動畫 | 打包要好幾秒，每段動畫各打包一次太慢 | 無 |
| 卡片固定在動畫區的上緣，不看臉的位置 | 範圍小；test001 的臉在畫面中段偏左，卡片在上緣多半蓋不到 | 卡片蓋到臉：之後依 `faces.json` 選位置 |
| 跟剪接有關的問題只列警告（5） | 審閱頁切換剪接時不改 `scenes`，和 `punch_in` 一樣 | 一段不出現的動畫留在 `edit.json` 裡；Claude 看到警告再處理 |
| 審閱頁只標出動畫的時段和文字，不播動畫 | 審閱頁播的是原始影片，即時渲染動畫要另外做 | 使用者想在同意前看動畫：之後做關卡 2 的樣張 |
| 兩段動畫不能重疊 | 動畫區只有一個，疊在一起會蓋住 | 之後有不同位置的卡片時放寬 |
| `validate` 用卡片的字型量每段字的像素寬，卡片（重點字照彈出時最大的 1.04 倍）比動畫區寬就擋下；卡片的字級、內距和勾的大小放在 `scenes/src/sizes.json`，卡片和 `validate` 讀同一份（實作時加，審查發現） | 字寬把英文字母和數字算成半個字，但大寫英文和數字畫出來比半個中文字寬：`BATTERY HEALTH CHECK` 合乎 10 個字寬，畫出來 1076 px，超出 950 px 的動畫區，兩邊被切掉 | 擋得太嚴：改 `sizes.json` 的字級，或讓卡片自動縮小 |
| 動畫只加在使用者要的地方，Claude 不自己加、也不在關卡 1 的訊息裡建議（`storyboard` 和 `reel` skill） | 和推近一樣：使用者看過自動交替的推近之後，選了只在指定的地方放大；動畫比推近更顯眼，先從使用者要的開始 | 使用者想要 Claude 主動建議：之後依語意標籤建議放哪張卡（原設計 7.3），寫進 skill |
