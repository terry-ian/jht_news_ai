# 儀表板登入保護說明

本文件說明「登入頁 + 登入保護」機制的檔案結構、環境變數設定方式，以及已知限制。
帳號密碼一律由 Vercel（或本機 `.env.local`）環境變數提供，**不會**寫死在任何前端或程式碼檔案中。

## 檔案清單

| 檔案 | 用途 |
| --- | --- |
| `login.html` | 登入頁面（純 HTML，不含任何帳密／secret） |
| `assets/login.css` | 登入頁專用樣式（獨立於 `assets/style.css`） |
| `assets/login.js` | 登入頁行為邏輯（送出帳密、顯示錯誤、密碼顯示/隱藏切換） |
| `api/login.js` | Vercel Serverless Function：驗證帳密、簽發登入 Cookie |
| `api/logout.js` | Vercel Serverless Function：清除登入 Cookie 並導回登入頁 |
| `middleware.js` | Vercel Edge Middleware：正式環境的全站登入保護閘道 |
| `dev-server.js` | 已修改，加入本機開發用的等效登入保護閘道（見下方「已修改檔案」） |
| `.env.example` | 已追加登入相關環境變數說明（不含真實值） |

## Vercel 環境變數設定步驟

前往 Vercel 專案 → **Project Settings → Environment Variables**，
**Production / Preview / Development 三個環境都要各自設定一次**：

| 變數名 | 用途 | 範例值格式 |
| --- | --- | --- |
| `DASH_USER` | 單一帳號模式：登入帳號 | `admin`（不分大小寫，會自動 trim） |
| `DASH_PASS` | 單一帳號模式：登入密碼 | 自訂高強度密碼字串（**不可留空**，空字串會被視為未設定而拒絕登入） |
| `DASH_USERS` | （選配）多組帳號，優先權高於上面兩者 | `alice:密碼1,bob:密碼2` |
| `AUTH_SECRET` | 簽發登入 Token 用的 HMAC 密鑰（必填） | 64 個字元的隨機十六進位字串 |
| `SESSION_TTL_HOURS` | （選配）登入有效時數，預設 12 | `12` |

⚠️ **變數名大寫或小寫皆可**：例如 `DASH_USER` 或 `dash_user` 都能被正確讀取
（程式會依序嘗試「原樣名稱 → 全大寫 → 全小寫」）；若 Vercel 介面限制只能輸入
小寫變數名，直接全部填小寫即可，不需要另外處理。但不支援混合大小寫命名
（例如 `Dash_User` 不會被讀到）。

⚠️ **修改環境變數後務必重新 Redeploy**：Vercel 只會在建置/部署當下讀取環境變數，
單純在設定頁儲存新值，並不會讓正在執行中的 Serverless Function／Edge Middleware
立即套用，必須觸發一次新的部署（Redeploy）才會生效。

### 如何產生 `AUTH_SECRET`

擇一執行，複製輸出結果貼到 Vercel 環境變數：

```bash
openssl rand -hex 32
```

或：

```bash
node -e "console.log(require('crypto').randomBytes(32).toString('hex'))"
```

⚠️ 正式環境與本機 `.env.local` 建議使用**不同**的 `AUTH_SECRET`；若同時給多人使用，
`AUTH_SECRET` 一旦外流，所有人的登入憑證都可能被偽造，請妥善保管、必要時定期更換
（更換後所有既有登入 Cookie 會立即失效，需要重新登入）。

## 如何改密碼

直接到 Vercel 環境變數頁面修改 `DASH_PASS`（或 `DASH_USERS` 中對應帳號的密碼）後，
重新部署（或等待下一次部署）即可生效；不需要修改任何程式碼。

## 如何登出

瀏覽器造訪 `/api/logout`（GET 或 POST 皆可），會清除登入 Cookie 並導回 `/login.html`。
也可以在瀏覽器開發者工具手動刪除 `jht_auth` Cookie 達到相同效果。

## 運作原理簡述

1. 使用者在 `login.html` 輸入帳密，前端呼叫 `POST /api/login`。
2. `api/login.js` 比對環境變數中的帳密（密碼比對使用 `crypto.timingSafeEqual`），
   成功後簽發一組 Token：`base64url(payload) + "." + base64url(HMAC-SHA256(payload))`，
   `payload = { u: 帳號, exp: 到期時間 }`，以 `HttpOnly` Cookie（`jht_auth`）寫回瀏覽器。
3. 之後每次請求，由 `middleware.js`（正式環境／Vercel Edge Middleware）或
   `dev-server.js`（本機開發）驗證 Cookie 的 HMAC 簽章與到期時間，
   驗證失敗一律視為未登入。
4. 除了 `/login.html`、`/assets/login.css`、`/assets/login.js`、`/api/login`、
   `/api/logout`、`/favicon.ico` 之外，其餘所有頁面與 API（含 `/api/ai`）都受保護。

## 已知限制

1. **這是輕量的單一共享帳號保護機制，不是正式的 IAM／使用者管理系統**。
   沒有個別使用者的權限分級、沒有註冊流程、也沒有密碼強度規則檢查。
2. **無 2FA（雙因素驗證）**。若需要更高安全等級，建議另外評估企業級 SSO / IAM 方案。
3. **登入嘗試限流僅為記憶體內的基本緩衝**（`api/login.js` 內的 `loginAttempts` Map）。
   Serverless Function 的記憶體不會在不同執行個體或 cold start 之間共享／持久化，
   在流量較大或分散式攻擊下防護效果有限；正式環境建議搭配 Cloudflare / Vercel WAF
   等邊緣層級的限流規則作為第二層防護。
4. **`middleware.js` 執行於 Vercel Edge Runtime**，只能使用 Web Crypto（`crypto.subtle`），
   無法使用 Node.js 的 `crypto` 模組；與 `api/login.js`、`dev-server.js`（皆為 Node.js
   Runtime）各自獨立實作對應的 HMAC 驗證邏輯，但 Token 格式完全相同、可互相驗證。
5. **`SameSite=Lax` + `HttpOnly`**：Cookie 無法被前端 JavaScript 讀取（防 XSS 竊取），
   且不會在跨站請求中自動附帶（防 CSRF）；`Secure` 屬性會在下列任一條件成立時加上：
   偵測到正式環境（`VERCEL_ENV=production` 或 `NODE_ENV=production`）、
   執行於 Vercel 平台（含 Preview 部署，只要 `VERCEL` 環境變數存在即視為 https）、
   或請求帶有 `x-forwarded-proto: https`。本機 `http://localhost` 測試時以上皆不成立，
   不會加上 `Secure`，屬正常行為（否則本機登入會因瀏覽器拒收 Cookie 而失敗）。
6. **本機 `dev-server.js` 的 fallback 行為**：若未設定 `AUTH_SECRET` 與 `DASH_USER`/
   `DASH_PASS`（或 `DASH_USERS`），且不是在 Vercel 環境（`process.env.VERCEL` 不存在）
   下執行，登入保護閘道會自動停用並印出警告，方便開發者在尚未準備登入憑證前先行開發；
   但只要偵測到 `VERCEL` 環境變數存在，或已設定任一登入相關變數，就會嚴格啟用保護閘道
  （未設定完整變數時等同鎖死所有請求，這是刻意設計的安全預設值，不會意外放行）。
7. **忘記密碼／被鎖定**：因為帳密只存在環境變數，沒有「忘記密碼」信箱找回流程；
   若忘記密碼或帳號被鎖定，請直接到 Vercel 環境變數頁面重設 `DASH_PASS` 並重新部署。

## Rollback 方法

若需要暫時或永久移除登入保護，可執行以下任一方式：

- **暫時停用（本機測試最快）**：在 `.env.local` 移除或清空 `AUTH_SECRET` / `DASH_USER` /
  `DASH_PASS` / `DASH_USERS`，`dev-server.js` 會自動 fallback 為不擋（僅限本機、非 Vercel）。
- **正式環境暫時停用**：刪除 Vercel 專案的 `middleware.js` 部署（或改名為
  `middleware.js.disabled` 後重新部署，Vercel 就不會載入它），全站即恢復無登入保護狀態。
- **完整復原到修改前**：本次修改前已備份下列檔案，可用備份檔還原：
  - `dev-server.js.bak_login_20260729`（還原：`cp dev-server.js.bak_login_20260729 dev-server.js`）
  - `.env.local.bak_login_20260729`
  - `.env.example.bak_login_20260729`
  再刪除新增檔案：`login.html`、`assets/login.css`、`assets/login.js`、
  `api/login.js`、`api/logout.js`、`middleware.js`、`README-login.md`
  （刪除動作請由使用者確認後執行，本次任務未自動刪除任何檔案）。
