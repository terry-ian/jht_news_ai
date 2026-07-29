/**
 * ============================================================================
 * middleware.js — Vercel Edge Middleware：登入保護閘道
 * ============================================================================
 *
 * 用途：
 *   攔截除登入頁與登入/登出 API 以外的所有請求，驗證 jht_auth Cookie
 *   （由 api/login.js 簽發的 HMAC-SHA256 Token）是否有效且未過期。
 *   驗證失敗：一般頁面 → 302 redirect 到 /login.html；
 *             /api/* 路徑 → 回傳 401 JSON（避免前端 fetch 收到 HTML 而解析失敗）。
 *
 * 執行環境：Vercel Edge Runtime（僅有 Web Crypto，不能用 Node.js 的 `crypto` 模組），
 * 因此 HMAC 驗證邏輯以 Web Crypto API（crypto.subtle）實作，與 api/login.js
 * （Node.js `crypto` 模組）、dev-server.js（本機開發，Node.js `crypto` 模組）
 * 各自獨立實作，但 Token 格式完全相同：
 *   base64url(JSON payload) + "." + base64url(HMAC-SHA256(payload))
 *   payload = { u: 帳號, exp: 到期時間（ms epoch）}
 *
 * 放行的公開路徑（雙重保護：下方 config.matcher 排除 + 函式內 isPublicPath 判斷）：
 *   /login.html、/assets/login.css、/assets/login.js、
 *   /api/login、/api/logout、/favicon.ico
 * ============================================================================
 */

const COOKIE_NAME = 'jht_auth';

// ---------------------------------------------------------------------------
// 環境變數大小寫相容：Vercel 介面若限制只能輸入小寫變數名，仍能正常讀到值。
// 優先權：原樣名稱 → 全大寫 → 全小寫（不支援混合大小寫如 Dash_User）。
// Edge Runtime 一樣提供 process.env，此函式與 api/login.js 中的版本邏輯相同。
// ---------------------------------------------------------------------------
function env(name) {
  return process.env[name] ?? process.env[name.toUpperCase()] ?? process.env[name.toLowerCase()] ?? '';
}

// matcher：先在路由層排除公開路徑，讓靜態資源與登入 API 不會先跑過本檔案再判斷，
// 減少不必要的 Edge Function 呼叫；下方 isPublicPath() 為第二層保險（防止
// matcher 規則寫錯導致公開頁面被誤擋）。
export const config = {
  matcher: ['/((?!login\.html|assets/login\.css|assets/login\.js|api/login|api/logout|favicon\.ico).*)']
};

function isPublicPath(pathname) {
  return (
    pathname === '/login.html' ||
    pathname === '/favicon.ico' ||
    pathname === '/api/login' ||
    pathname === '/api/logout' ||
    pathname === '/assets/login.css' ||
    pathname === '/assets/login.js'
  );
}

function base64urlToBytes(str) {
  let s = str.replace(/-/g, '+').replace(/_/g, '/');
  while (s.length % 4) s += '=';
  const binary = atob(s);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

function getCookieValue(cookieHeader, name) {
  if (!cookieHeader) return null;
  const parts = cookieHeader.split(';');
  const prefix = name + '=';
  for (let i = 0; i < parts.length; i++) {
    const part = parts[i].trim();
    if (part.indexOf(prefix) === 0) {
      return decodeURIComponent(part.slice(prefix.length));
    }
  }
  return null;
}

async function verifyAuthToken(token, secret) {
  if (!token || !secret) return null;
  const parts = token.split('.');
  if (parts.length !== 2) return null;
  const [payloadB64, sigB64] = parts;

  try {
    const key = await crypto.subtle.importKey(
      'raw',
      new TextEncoder().encode(secret),
      { name: 'HMAC', hash: 'SHA-256' },
      false,
      ['verify']
    );
    const sigBytes = base64urlToBytes(sigB64);
    const valid = await crypto.subtle.verify('HMAC', key, sigBytes, new TextEncoder().encode(payloadB64));
    if (!valid) return null;

    const payloadJson = new TextDecoder().decode(base64urlToBytes(payloadB64));
    const payload = JSON.parse(payloadJson);
    if (!payload || typeof payload.exp !== 'number' || Date.now() > payload.exp) return null;
    return payload;
  } catch (e) {
    return null; // Token 格式錯誤、簽章不合法或已損毀，一律視為未登入
  }
}

export default async function middleware(request) {
  const url = new URL(request.url);
  const { pathname } = url;

  if (isPublicPath(pathname)) return; // 放行，不做任何處理

  const cookieHeader = request.headers.get('cookie') || '';
  const token = getCookieValue(cookieHeader, COOKIE_NAME);
  const secret = env('AUTH_SECRET');

  const payload = await verifyAuthToken(token, secret);
  if (payload) return; // 已登入且未過期，放行

  if (pathname.startsWith('/api/')) {
    return new Response(JSON.stringify({ ok: false, error: 'unauthorized' }), {
      status: 401,
      headers: { 'Content-Type': 'application/json; charset=utf-8' }
    });
  }

  const redirectTarget = pathname + (url.search || '');
  const loginUrl = new URL('/login.html', request.url);
  loginUrl.searchParams.set('redirect', redirectTarget);
  return Response.redirect(loginUrl, 302);
}
