# 版本號（2026-11-15）

kacha 已經公開在 GitHub 上，推過四次，每次都沒有編號。這一份訂出程式的版本號：怎麼編、什麼時候加、程式怎麼說出自己的版本、專案怎麼記下它是哪一版做的、推上 GitHub 時怎麼標。

## 1. 目的和範圍

使用者要的三件事：

- 回報問題時說得出用的是哪一版。
- 每個專案記得它是哪一版做的。
- 使用者自己追蹤每一版推出了什麼。

不做：GitHub Release 的更新說明、成品 mp4 裡的版本資訊、補標之前的三次推送。

程式的版本號和資料檔的格式版本（`FORMAT_VERSIONS`，例如 `transcript.json` 改版時舊專案要從轉錄重跑）是兩回事。格式版本照舊，這一份不動它。

## 2. 編法和加的時機

- 版本號是 `0.<功能>.<修正>`，例如 `0.2.0`、`0.2.1`。開頭的 `0` 表示還在早期；使用者覺得可以穩定使用時，再決定升成 `1.0.0`。
- 版本號只寫在 `pyproject.toml` 的 `version`。`uv.lock` 裡 `kacha` 那一項的 `version` 跟著它（改了 `pyproject.toml` 就跑 `uv lock`）。
- 每次合併到 `main` 時決定：
  - 有新功能（使用者看得到的新行為）：加中間的數字，最後的數字歸零（`0.2.1` → `0.3.0`）。
  - 只有修正：加最後的數字（`0.3.0` → `0.3.1`）。
  - 只改說明文件（`README.md`、`docs/`、`FEATURE.md`、`THIRD_PARTY.md`）：不加。
  - `.claude/skills/` 和 `CLAUDE.md` 決定 Claude 怎麼剪，算程式：改了照「功能」或「修正」加。
- 合併之前，Claude 照上面的規則提出新的版本號，和「要不要合併」一起問使用者。加版本號是合併前在分支上的最後一個 commit。
- 這條規則寫進 `CLAUDE.md` 的「開發」。

## 3. 起點

- GitHub 上現在那一版（`public` 分支的 `8b6a2b5`）標成 `v0.1.0`：它的 `pyproject.toml` 本來就寫 `0.1.0`。
- 卡片樣張先照原計畫合併，版本號不動。
- 版本號這個功能合併時升成 `0.2.0`。這一版包含還沒推上 GitHub 的卡片位置、三張新卡、卡片樣張和版本號本身。

## 4. 程式說出自己的版本

- `pipeline/version.py` 的 `kacha_version() -> str`：讀 repo 根目錄的 `pyproject.toml`（`tomllib`），回傳 `project.version`。kacha 一律從 repo 用 `uv run` 執行，直接讀 `pyproject.toml`，下一次執行就是新的版本號，不靠安裝時留下的套件資料。每個程式只在第一次用到時讀一次：審閱頁的伺服器會一直開著，用的是它啟動時載入的程式，中途合併改了版本號，它寫的紀錄仍是它自己的版本。讀不到或沒有 `version` 時拋出 `PipelineError`（`cannot read the kacha version from pyproject.toml: <原因>`）。
- `uv run pipeline --version`：印一行 `{"ok": true, "version": "0.2.0"}`，結束碼 0。不用加子指令。
- `doctor` 的結果多一個 `version`。
- `status`（列出所有專案）和 `status <專案>` 的結果都多一個 `version`：現在這份程式的版本。

## 5. 專案記下是哪一版做的

- `run_step` 寫步驟紀錄時，多記 `"kacha_version": "<版本>"`：做這一步的程式版本。步驟紀錄裡已經有的 `"version"` 是資料檔的格式版本（整數），兩者分開，名字也不同。
- `approve_gate` 寫同意紀錄時，也多記 `"kacha_version"`。審閱頁上的同意走同一個函式，一樣會記。
- `status <專案>` 多一個 `made_with`：已完成的每一步（和 `steps` 列的一樣）加上仍然有效的同意（`gate1`，`gate1_ok` 是 `true` 時；同意之後分鏡稿用到的檔案改了，那次同意就不列），各對到記下的版本；有版本號之前做的，是 `null`。例如 `{"prep": null, "transcribe": "0.2.0", "gate1": "0.2.0"}`。
- 只是紀錄：版本不同不會讓一步變成沒做完，也不會讓同意失效。要不要重跑，照舊由輸入檔和格式版本決定。
- 沒有 `kacha_version` 的舊 `state.json` 照常讀得出來；`load_state` 不要求這個欄位。
- `reel` skill 加一段：使用者要回報問題，或問「這支是哪一版做的」時，用 `status <專案>` 的 `version` 和 `made_with` 回答；回報問題時告訴他現在的版本（`version`），和這支專案每一步是哪一版做的。

## 6. 推上 GitHub

- 推 `public` 分支時，如果 `pyproject.toml` 的版本比上一個標記新，在那個 `public` commit 上打輕量標記 `v<版本>`（`git tag v0.2.0 <commit>`），和分支一起推上去（`git push github v0.2.0`）。推之前照舊先給使用者看、等他同意。
- 只改說明文件的推送，版本號沒變，不打新標記。
- 標記只打在 `public` 的 commit 上；`main` 的合併不打標記（標記名稱在本機只能有一個，`main` 的 commit 也不能推上 GitHub）。`main` 上某一版是哪個 commit，看 `pyproject.toml` 的歷史。
- 這幾條只記在 Claude 記憶裡的發布步驟（`public-release-decisions`），不寫進 `CLAUDE.md`：`CLAUDE.md` 會公開，GitHub 上的讀者只看得到 `main`，`public` 分支是維護者自己的發布方式。

## 7. 追蹤每一版推出了什麼

- `FEATURE.md`「已完成」的每個功能標題後面，標它第一次出現的版本，例如「### 推近（0.1.0）」。同一節裡後來的版本才加的項目，在那一項的最後標它的版本，例如「（0.2.0）」。
- GitHub 上已經有的功能都是 `0.1.0`；卡片位置、三張新卡、卡片樣張和版本號是 `0.2.0`。例如「動畫卡片」標 `0.1.0`，裡面講卡片避開頭、三張新卡和樣張的項目標 `0.2.0`。
- 之後每次加版本號的合併，把新功能寫進 `FEATURE.md` 時一起標版本。

## 8. 文件

- `CLAUDE.md`「開發」：第 2 節的規則、版本號放在哪裡；設計文件清單加這一份。推上 GitHub 時打標記不寫在這裡（見第 6 節）。
- `README.md` 的那一句附上版本號的例子（「例如 0.2.0」）：讀者檢查時，看的人不知道版本號長什麼樣子。
- `reel` skill：第 5 節的那一段；`doctor` 和 `status` 結果多的欄位。
- `README.md`「出問題找誰？」：回報時寫上用的是哪一版，問 Claude 就知道。照 /handout-writing 寫。
- `FEATURE.md`：第 7 節。

## 9. 測試

行為的改動先寫會失敗的測試。

- `kacha_version()` 等於用 `tomllib` 讀出的 `pyproject.toml` 的 `project.version`，格式是 `數字.數字.數字`。
- `uv.lock` 裡 `kacha` 的 `version` 等於 `pyproject.toml` 的（忘了跑 `uv lock` 時失敗）。
- `pipeline --version` 印一行 `{"ok": true, "version": …}`，結束碼 0。
- `doctor`、`status`、`status <專案>` 的結果有 `version`。
- `run_step` 的紀錄有 `kacha_version`；跳過的步驟不改寫它；`approve_gate` 的紀錄有 `kacha_version`。
- `status <專案>` 的 `made_with`：列出已完成的步驟和已同意的關卡；沒有 `kacha_version` 的舊紀錄是 `null`；版本不同的紀錄照樣算完成、同意照樣有效。
- `pyproject.toml` 讀不到時，`kacha_version()` 拋出 `PipelineError`。

## 10. 決定紀錄

| 決定 | 理由 | 錯了的代價 |
|---|---|---|
| `0.功能.修正` | 看得出新功能還是修正；`pyproject.toml` 本來就是這種編法 | 使用者覺得數字沒意義：改成日期，舊標記留著 |
| 每次合併就加，不是推上 GitHub 才加 | 使用者自己剪片用 `main`，專案記下的版本要對到確切的程式 | GitHub 上的版本號會跳號（`0.2.0` 之後可能是 `0.4.0`） |
| skill 和 `CLAUDE.md` 改了算程式 | 它們決定 Claude 怎麼剪，同一份 Python 換了 skill，做出來的專案就不同 | 小小的措辭修改也加版本號：版本號加得比較快 |
| 直接讀 `pyproject.toml`，不讀安裝的套件資料 | kacha 一律從 repo 執行；直接讀，改了版本號就生效，不用重新安裝 | 之後要打包成可以單獨安裝的套件：改讀套件資料 |
| 每個程式只讀一次版本號 | 審閱頁的伺服器開著時合併升版，每次都讀的話，舊程式做的步驟會記成新版本 | 無 |
| 步驟紀錄用 `kacha_version`，不用 `version` | `version` 已經是資料檔的格式版本 | 無 |
| 版本只是紀錄，不觸發重跑 | 要不要重跑已經由輸入檔和格式版本決定；用版本觸發，每次合併都要所有專案重新同意 | 某次改版讓舊的結果不對、格式版本卻沒變：那次要加格式版本 |
| 打標記的步驟只記在 Claude 的記憶，不寫進 `CLAUDE.md`（使用者，2026-11-15） | `CLAUDE.md` 會公開，寫的是 GitHub 上看不到的 `public` 分支，讀者會對不上 | 換人發布時，要另外告訴他這幾步 |
| 標記只打在 `public` 的 commit，用輕量標記 | `main` 不能推上 GitHub，本機的標記名稱只能有一個；輕量標記不帶打標記的人的資料 | 想在標記上寫說明：改用附註標記 |
| 不補標之前的三次推送 | 那幾次的 `pyproject.toml` 都寫 `0.1.0`，補標會讓標記和檔案對不上 | 無 |
