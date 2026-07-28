# fitness-dashboard 新聞爬蟲 (fetch_news.py)

喬山 Johnson 全球健身器材產業情報看板的新聞爬蟲。輸出 `../data/news.json`。

## 資料來源

1. **Google News RSS**（`source_type = google_news`）
   - 27 個追蹤品牌（＋自家 Johnson）的多情境查詢、通用器材關鍵字、中文查詢。
2. **品牌官方來源**（`source_type = official`）
   - 對 28 個品牌官網逐一嘗試 RSS/Atom feed（含 Shopify `/blogs/xxx.atom`、
     `/blog/feed`、`/news/rss`、`/feed` 等常見路徑）。
   - 無 feed 者退而抓 newsroom / blog / press 列表頁 HTML（只取列表頁連結與標題，不深爬）。
3. **新品發布導向 Google News 查詢**（`source_type = product`）
   - 每品牌 `"<Brand>" new product / launch / unveils / releases`（when:1y）。
4. **產業新聞稿來源**（`source_type = press_release`）
   - Athletech News feed，以及 `fitness equipment launch`、`new treadmill launch` 等查詢。

## 核心行為：合併累加、絕不刪舊

- 執行時先讀取既有 `news.json` 的 `articles`，抓到的新資料只做「追加」。
- **去重**：以「正規化 URL + 正規化標題」為 key，與既有庫存重複者不重加（保留既有那筆）。
- **穩定 ID**：新文章 `id` 由「既有最大 id + 1」往上給。
- **first_seen**：每篇有首次入庫日期（UTC）；既有文章不更動其 first_seen。
- **source_type**：每篇標記 `google_news | official | product | press_release`。
- **日期上限 (`MAX_ARTICLE_AGE_DAYS`)**：只用來過濾「本次新抓進來」的過舊項目，
  **絕不套用於已入庫的既有資料**（庫存只增不減）。
- **idempotent**：同一天重複執行只會新增當天新出現、去重後不重覆者。
- **安全寫檔**：先寫 `news.json.tmp` 再原子替換，避免中途中斷破壞既有檔案。
- **lock 檔**：`fetch_news.lock` 簡易防重入（2 小時後視為 stale 自動移除）。
- **執行紀錄**：每次執行摘要寫入 `scrape_log.txt`（時間 / 新增數 / 總數 / 失敗來源）。

## 手動執行

```bat
C:\Users\troy8\anaconda3\python.exe C:\Users\troy8\OneDrive\桌面\fitness-dashboard\scraper\fetch_news.py
```

或直接雙擊 / 執行 `run_daily.bat`。

## 每日自動執行（Windows 工作排程器）

### 方式 A：schtasks 指令（建議，直接複製到「以系統管理員身分執行」的命令提示字元）

每天凌晨 03:30 執行一次：

```bat
schtasks /Create /TN "FitnessNewsDaily" /TR "C:\Users\troy8\OneDrive\桌面\fitness-dashboard\scraper\run_daily.bat" /SC DAILY /ST 03:30 /RL LIMITED /F
```

參數說明：
- `/TN`：工作名稱。
- `/TR`：要執行的程式（此處為 run_daily.bat）。
- `/SC DAILY /ST 03:30`：每天 03:30 執行。
- `/F`：若同名工作已存在則覆寫。

驗證 / 立即測試 / 移除：

```bat
schtasks /Query  /TN "FitnessNewsDaily" /V /FO LIST     REM 查看設定
schtasks /Run    /TN "FitnessNewsDaily"                 REM 立即手動觸發一次
schtasks /Delete /TN "FitnessNewsDaily" /F              REM 刪除排程
```

> 提醒：路徑含中文與空白，`/TR` 已用雙引號包住；若日後把專案搬離桌面，請同步更新此指令與 `run_daily.bat` 內的路徑。

### 方式 B：工作排程器 GUI

1. 開啟「工作排程器」→ 建立基本工作。
2. 觸發程序：每天，時間設 03:30。
3. 動作：啟動程式，程式填 `run_daily.bat` 的完整路徑。
4. 完成後可在「工作排程器程式庫」右鍵「執行」測試。

## 產出檔案

- `../data/news.json`：主資料（含 `articles` 與 `stats`）。
- `scrape_log.txt`：每次執行摘要 log。
- `run_daily.out.log` / `run_daily.err.log`：由 run_daily.bat 產生的標準輸出 / 錯誤輸出。
- `fetch_news.lock`：執行中的 lock 檔（正常結束會自動刪除）。

## 相依套件

```
pip install -r requirements.txt
```

（requests、feedparser、beautifulsoup4；deep-translator 為前端翻譯相關，本腳本未使用。）

## 注意事項與風險

- 官方網站型態各異，可能有反爬、Cloudflare 挑戰或改版導致 feed/頁面失效；程式對單一來源失敗
  只記錄不中斷，`news.json` 的 `sources_failed` 與 `official_results` 會列出結果。
- 因為採累加，`news.json` 會隨時間持續變大屬預期行為。
- HTML 列表頁擷取的是列表頁上的連結與標題（不深爬內文），標題品質取決於各站版面。
