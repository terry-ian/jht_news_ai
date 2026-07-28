# 喬山 AI 產業情報儀表板 — Google Gemini 整合說明

本文件說明本專案的 AI 功能自 OpenAI 改為 **Google Gemini（Vertex AI，服務帳戶為預設認證模式）** 後的架構、環境變數、本機測試與 Vercel 部署方式。

---

## 1. 架構說明

```
瀏覽器前端 (index.html + assets/app.js)
        │  fetch('./api/ai', { method: 'GET' | 'POST' })
        ▼
同源 /api/ai   ──────────────────────────────────────────
  ├─ Vercel 正式環境：api/ai.js 以 Serverless Function 執行
  └─ 本機開發環境：   dev-server.js 直接 require('./api/ai.js')
                       並模擬 Vercel 的 req.body / res.status().json()
                       介面，兩邊執行的是「同一份程式碼」，不需要
                       為環境差異寫分支。
        │
        ▼
  Google Gemini（Vertex AI 或 Google AI Studio API）
```

重點原則：

- **前端（瀏覽器端）永遠不會拿到任何金鑰或服務帳戶內容。** `assets/app.js` 只會呼叫本站同源的 `./api/ai`，所有與 Google 的認證與呼叫都在 `api/ai.js`（伺服器端）完成。
- 服務帳戶 JSON（例如 `jht-pm-c05446ebd432.json`）或 API Key，只存在於：
  - 本機的 `.env.local`（已列在 `.gitignore`，不會被 commit）；
  - Vercel 專案的 Environment Variables（伺服器端變數，不會下發到瀏覽器）。
- `index.html` 與 `assets/app.js` 呼叫 `/api/ai` 時支援兩種模式：
  - 非串流：`POST /api/ai`，一次拿回完整 JSON 回應。
  - 串流：`POST /api/ai`（body 帶 `stream: true`），以 SSE (`text/event-stream`) 方式逐字回傳，前端即時渲染。
  - `GET /api/ai`：僅回傳後端目前設定狀態（模型名稱、認證模式、是否已備妥憑證），**不會**實際呼叫 Gemini，零成本，供「AI 設定」Modal 顯示用。

---

## 2. 環境變數清單

所有環境變數都有合理預設值（見 `.env.example`），本機開發請複製一份為 `.env.local` 並依需要填入。

| 變數名稱 | 說明 | 預設值 |
|---|---|---|
| `GEMINI_MODEL` | 呼叫的 Gemini 模型 id，兩種認證模式共用同一個變數。 | `gemini-3.1-flash-lite` |
| `GCP_PROJECT_ID` | Vertex AI 模式使用的 GCP 專案 ID。 | `jht-pm` |
| `GCP_LOCATION` | Vertex AI 服務所在區域。 | `us-central1` |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | 服務帳戶 JSON **檔案的完整內容**，以單行字串貼入（Vertex AI 模式，Vercel 部署時使用這個）。 | 無 |
| `GOOGLE_APPLICATION_CREDENTIALS` | 服務帳戶 JSON **檔案的絕對路徑**（Vertex AI 模式，僅適合本機開發，不適合 Serverless 環境）。 | 無 |
| `GEMINI_API_KEY` | Google AI Studio 的 API Key（較簡單、便宜的認證模式）。 | 無 |

### 兩種認證模式與優先順序

`api/ai.js` 會依照環境變數「自動判斷」該走哪種模式，**判斷順序如下（寫死在程式邏輯中）**：

1. **模式 (b) Google AI Studio API Key 模式** — 只要 `GEMINI_API_KEY` 有值，一律優先採用此模式（`useApiKeyMode()` 為 true 時直接短路，不再檢查 Vertex AI 的變數）。適合快速測試、免服務帳戶設定。
2. **模式 (a) Vertex AI 服務帳戶模式（預設）** — 當 `GEMINI_API_KEY` 為空時才會使用。此模式下：
   - 若有 `GOOGLE_SERVICE_ACCOUNT_JSON`，優先使用它（直接 `JSON.parse` 後傳給 `google-auth-library`）。
   - 若沒有 `GOOGLE_SERVICE_ACCOUNT_JSON`，退回讓 `google-auth-library` 自行嘗試 `GOOGLE_APPLICATION_CREDENTIALS`（或其他 Application Default Credentials 來源）。
   - 搭配 `GCP_PROJECT_ID` 與 `GCP_LOCATION` 組成 Vertex AI 的呼叫端點 URL。

簡言之：**設定 `GEMINI_API_KEY` 就一定走 AI Studio 模式；留白 `GEMINI_API_KEY` 才會走 Vertex AI 服務帳戶模式。**

---

## 3. 本機測試步驟

1. 安裝依賴（僅需一次）：

   ```bash
   npm install
   ```

2. 確認 `.env.local` 已存在且內容正確（可參考 `.env.example`）。本專案目前的 `.env.local` 設定為 Vertex AI 模式，指向本機的服務帳戶檔案路徑：

   ```
   GOOGLE_APPLICATION_CREDENTIALS=C:\Users\troy8\OneDrive\桌面\fitness-dashboard\jht-pm-c05446ebd432.json
   ```

3. 啟動本機開發伺服器：

   ```bash
   npm run dev
   ```

   （等同於 `node dev-server.js`，預設埠 **8000**；如需指定其他埠，可用 `node dev-server.js 8080`。）

4. 開啟瀏覽器造訪（依 `dev-server.js` 實際印出的網址為準）：

   ```
   http://localhost:8000
   ```

5. 若已安裝 Vercel CLI，也可改用 `npm run dev:vercel`（等同於 `vercel dev`），行為更接近正式 Vercel 環境。

---

## 4. Vercel 部署步驟

1. 將專案推送到 Vercel 連結的 Git 倉庫（或用 Vercel CLI 部署）。**切勿**把服務帳戶 JSON 檔案本身（`jht-pm-c05446ebd432.json`）加入版本控制或上傳。
2. 到 Vercel Dashboard → 該 Project → **Settings → Environment Variables**，新增以下變數（依實際採用的認證模式擇一設定）：

   | Key | Value |
   |---|---|
   | `GOOGLE_SERVICE_ACCOUNT_JSON` | 貼上**整份服務帳戶 JSON 檔案內容**（一整段 JSON 字串，包含 `private_key` 內的 `\n` 轉義字元） |
   | `GEMINI_MODEL` | 例如 `gemini-3.1-flash-lite`（或改用下方註明的替代模型） |
   | `GCP_PROJECT_ID` | 例如 `jht-pm` |
   | `GCP_LOCATION` | 例如 `us-central1` |

   若改用 Google AI Studio 模式，則只需設定 `GEMINI_API_KEY` 與 `GEMINI_MODEL` 即可，不需要上述 Vertex AI 相關變數。

3. GCP 端前置需求（Vertex AI 模式）：
   - 該 GCP 專案需已**啟用 Vertex AI API**（`aiplatform.googleapis.com`）。
   - 該 GCP 專案需已**綁定有效的 billing 帳戶**。
   - 用於呼叫的服務帳戶，需具備 **Vertex AI User**（`roles/aiplatform.user`）角色，否則呼叫會收到 403 Permission Denied。
4. 儲存環境變數後，觸發重新部署（Redeploy）使變數生效。
5. **金鑰安全提醒**：`GOOGLE_SERVICE_ACCOUNT_JSON` 這類敏感變數僅存在於 Vercel 後端環境，不會出現在建置產物或前端 bundle 中；請勿在 commit、issue、log 或截圖中外流其內容。

---

## 5. 模型名稱提醒

- 預設模型為使用者指定的 `gemini-3.1-flash-lite`，此為**尚未確認正式發布**的模型 id。
- 若呼叫時收到 **404 / `model_not_found`** 之類錯誤，代表該模型 id 尚未對應到你的專案 / 帳號權限，請將環境變數 `GEMINI_MODEL` 改成以下確定可用的替代模型之一：
  - `gemini-2.5-flash-lite`
  - `gemini-2.0-flash-lite`
- 修改後（本機改 `.env.local`，Vercel 改 Environment Variables 並 Redeploy）即可立即生效，不需修改 `api/ai.js` 程式碼。

---

## 6. 檔案對照

| 檔案 | 用途 |
|---|---|
| `api/ai.js` | Gemini 代理核心邏輯（Vercel Serverless Function / 本機共用） |
| `dev-server.js` | 本機開發用靜態檔伺服器 + `/api/ai` 轉接 |
| `vercel.json` | Vercel 部署設定（`api/ai.js` 逾時上限 60 秒） |
| `.env.example` | 環境變數範例（可安全 commit） |
| `.env.local` | 本機實際環境變數（已列入 `.gitignore`，不可 commit） |
| `index.html` / `assets/app.js` | 前端頁面與呼叫 `/api/ai` 的邏輯 |
| `*.bak_gemini` | 改用 Gemini 前的備份檔 |
