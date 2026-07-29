/**
 * ============================================================================
 * api/login.js — 儀表板登入端點（Vercel Serverless Function）
 * ============================================================================
 *
 * 用途：
 *   驗證帳號密碼（來自環境變數，不寫死在程式碼中），驗證成功後簽發一組
 *   HMAC-SHA256 簽章的 Token，以 HttpOnly Cookie 寫回瀏覽器。
 *   之後的頁面／API 存取由 middleware.js（正式環境／Vercel Edge）或
 *   dev-server.js（本機開發）驗證此 Cookie 是否有效。
 *
 * 環境變數（大寫或小寫皆可，見下方 env() 函式）：
 *   DASH_USER / DASH_PASS   單一帳號密碼（基本用法）。
 *   DASH_USERS              多組帳號（選配，優先權高於上面兩者），
 *                            格式："user1:pass1,user2:pass2"。
 *   AUTH_SECRET              簽發 Token 用的 HMAC 密鑰（必填，請用長隨機字串）。
 *   SESSION_TTL_HOURS        登入有效時數，預設 12 小時。
 *
 * 前端呼叫方式：
 *   POST /api/login
 *     body: { username: string, password: string }
 *     成功：200 { ok:true }，並以 Set-Cookie 寫入 jht_auth
 *     失敗：401 { ok:false, error:"invalid_credentials" }（帳號或密碼錯誤，
 *           刻意不區分是帳號還是密碼錯，避免帳號列舉）
 *     過於頻繁：429 { ok:false, error:"too_many_attempts" }
 *     伺服器未設定憑證：500 { ok:false, error:"..." }（不會洩漏任何變數值）
 * ============================================================================
 */

const crypto = require('crypto');

// ---------------------------------------------------------------------------
// 環境變數大小寫相容：Vercel 介面若限制只能輸入小寫變數名，仍能正常讀到值。
// 優先權：原樣名稱 → 全大寫 → 全小寫（不支援混合大小寫如 Dash_User）。
// 注意：這裡不對「空字串」做特殊處理，呼叫端需自行判斷空字串是否視為未設定。
// ---------------------------------------------------------------------------
function env(name) {
  return process.env[name] ?? process.env[name.toUpperCase()] ?? process.env[name.toLowerCase()] ?? '';
}

const COOKIE_NAME = 'jht_auth';

// ---------------------------------------------------------------------------
// 簡易暴力破解防護：以記憶體 Map 依來源 IP 計數限流。
// ⚠️ 注意：Serverless Function 的記憶體不會在不同執行個體 / cold start 之間
// 共享或持久化，這只是「基本緩衝」，不是正式的分散式限流機制。若需要更
// 嚴謹的防護，建議在 Cloudflare / Vercel WAF 等邊緣層另外設定限流規則。
// ---------------------------------------------------------------------------
const RATE_LIMIT_WINDOW_MS = 10 * 60 * 1000; // 10 分鐘
const RATE_LIMIT_MAX_ATTEMPTS = 10;          // 同一 IP 10 分鐘內最多 10 次嘗試
const RATE_LIMIT_CLEANUP_THRESHOLD = 5000;   // Map 筆數超過此門檻才觸發清理，避免每次呼叫都掃描
const loginAttempts = new Map(); // ip -> { count, windowStart }

function isRateLimited(ip) {
  const now = Date.now();

  // 輕量記憶體清理：長時間執行下，未再次出現的舊 IP 紀錄永遠不會被移除，
  // 會讓 Map 持續成長。只在筆數超過門檻時才掃描一次過期紀錄並刪除，
  // 平常呼叫幾乎沒有額外開銷；刻意不使用 setInterval（serverless 不適合常駐計時器）。
  if (loginAttempts.size > RATE_LIMIT_CLEANUP_THRESHOLD) {
    for (const [key, record] of loginAttempts) {
      if (now - record.windowStart > RATE_LIMIT_WINDOW_MS) {
        loginAttempts.delete(key);
      }
    }
  }

  const record = loginAttempts.get(ip);
  if (!record || now - record.windowStart > RATE_LIMIT_WINDOW_MS) {
    loginAttempts.set(ip, { count: 1, windowStart: now });
    return false;
  }
  record.count += 1;
  return record.count > RATE_LIMIT_MAX_ATTEMPTS;
}

function getClientIp(req) {
  const fwd = req.headers && req.headers['x-forwarded-for'];
  if (fwd) return String(fwd).split(',')[0].trim();
  return (req.socket && req.socket.remoteAddress) || 'unknown';
}

// ---------------------------------------------------------------------------
// Token 簽發：base64url(payload) + "." + base64url(HMAC-SHA256(payload))
// payload 內容：{ u: 帳號, exp: 到期時間（ms epoch）}
// middleware.js（Edge Runtime／Web Crypto）與 dev-server.js（Node crypto）
// 皆各自實作對應的驗證邏輯，格式完全相同。
// ---------------------------------------------------------------------------
function base64url(buf) {
  return buf.toString('base64').replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function signToken(payloadObj, secret) {
  const payloadB64 = base64url(Buffer.from(JSON.stringify(payloadObj), 'utf8'));
  const sig = crypto.createHmac('sha256', secret).update(payloadB64).digest();
  return `${payloadB64}.${base64url(sig)}`;
}

// 支援多組帳號：DASH_USERS="user1:pass1,user2:pass2"，優先權高於 DASH_USER/DASH_PASS
function parseAllowedUsers() {
  const raw = env('DASH_USERS');
  if (raw) {
    return raw
      .split(',')
      .map((pair) => pair.trim())
      .filter(Boolean)
      .map((pair) => {
        const idx = pair.indexOf(':');
        if (idx === -1) return null;
        return { user: pair.slice(0, idx).trim(), pass: pair.slice(idx + 1) };
      })
      .filter(Boolean)
      // 空密碼視為未設定，不可作為有效憑證（與 DASH_USER/DASH_PASS 邏輯一致）
      .filter((entry) => entry.pass !== '');
  }

  const singleUser = env('DASH_USER');
  const singlePass = env('DASH_PASS');
  // 空字串與未設定都視為「未設定」，不可作為有效憑證；
  // 只有非空字串的密碼才算是伺服器已設定好登入憑證。
  if (singleUser && singlePass !== '') {
    return [{ user: singleUser, pass: singlePass }];
  }
  return [];
}

// 固定長度、固定時間比較，避免長度不同時 crypto.timingSafeEqual 直接拋出例外，
// 也避免因為長度不同就提早 return 造成明顯的時序差異。
function timingSafeEqualStr(a, b) {
  const bufA = Buffer.from(String(a), 'utf8');
  const bufB = Buffer.from(String(b), 'utf8');
  if (bufA.length !== bufB.length) {
    // 長度不同時，仍與自身做一次等長比較，讓耗時盡量與正常路徑一致
    crypto.timingSafeEqual(bufA, bufA);
    return false;
  }
  return crypto.timingSafeEqual(bufA, bufB);
}

// ---------------------------------------------------------------------------
// Secure Cookie 判斷：只要「請求可能經由 https 送達」就應該加上 Secure，
// 避免只判斷 production 導致 Vercel Preview 部署（同樣是 https）漏加。
// 本機 http://localhost 開發時，以下三個條件皆不成立，維持不加 Secure。
// ---------------------------------------------------------------------------
function shouldUseSecureCookie(req) {
  const isProdEnv = process.env.VERCEL_ENV === 'production' || process.env.NODE_ENV === 'production';
  const isOnVercel = !!process.env.VERCEL; // 只要跑在 Vercel（含 Preview），一律視為 https
  const forwardedProto = String((req.headers && req.headers['x-forwarded-proto']) || '').toLowerCase();
  const isForwardedHttps = forwardedProto.includes('https');
  return isProdEnv || isOnVercel || isForwardedHttps;
}

module.exports = async function handler(req, res) {
  if (req.method !== 'POST') {
    res.status(405).json({ ok: false, error: 'method_not_allowed' });
    return;
  }

  // 在 handler 內讀取（而非模組頂層），避免 serverless 模組載入時序造成
  // process.env 尚未就緒就讀取到空值的問題。
  const AUTH_SECRET = env('AUTH_SECRET');
  const SESSION_TTL_HOURS = Number(env('SESSION_TTL_HOURS')) > 0 ? Number(env('SESSION_TTL_HOURS')) : 12;

  const allowedUsers = parseAllowedUsers();
  if (allowedUsers.length === 0 || !AUTH_SECRET) {
    // 不洩漏是缺少哪個變數的值，只說明伺服器尚未設定完成
    console.error('[api/login] 伺服器缺少登入相關環境變數設定（DASH_USER/DASH_PASS 或 DASH_USERS，以及 AUTH_SECRET）。');
    res.status(500).json({ ok: false, error: '伺服器未設定登入憑證，請聯絡管理員設定環境變數。' });
    return;
  }

  const ip = getClientIp(req);
  if (isRateLimited(ip)) {
    res.status(429).json({ ok: false, error: 'too_many_attempts' });
    return;
  }

  let body = req.body;
  if (typeof body === 'string') {
    try { body = JSON.parse(body); } catch (e) { body = {}; }
  }
  body = body || {};

  const inputUser = typeof body.username === 'string' ? body.username.trim().toLowerCase() : '';
  const inputPass = typeof body.password === 'string' ? body.password : '';

  const matched = allowedUsers.find((u) => u.user.trim().toLowerCase() === inputUser);
  // 即使帳號不存在，也對「輸入的密碼」做一次比較動作，降低「帳號是否存在」的時序差異
  const passOk = timingSafeEqualStr(inputPass, matched ? matched.pass : inputPass);

  if (!matched || !passOk) {
    // 統一回應，不區分帳號錯誤或密碼錯誤，避免帳號列舉（enumeration）
    res.status(401).json({ ok: false, error: 'invalid_credentials' });
    return;
  }

  const ttlMs = SESSION_TTL_HOURS * 3600 * 1000;
  const exp = Date.now() + ttlMs;
  const token = signToken({ u: matched.user, exp }, AUTH_SECRET);

  const cookieParts = [
    `${COOKIE_NAME}=${token}`,
    'Path=/',
    'HttpOnly',
    'SameSite=Lax',
    `Max-Age=${Math.floor(ttlMs / 1000)}`
  ];
  if (shouldUseSecureCookie(req)) cookieParts.push('Secure');

  res.setHeader('Set-Cookie', cookieParts.join('; '));
  res.status(200).json({ ok: true });
};
