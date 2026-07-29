/**
 * ============================================================================
 * api/logout.js — 登出端點（Vercel Serverless Function）
 * ============================================================================
 * 用途：清除登入 Cookie（jht_auth），並將瀏覽器導回登入頁。
 * 呼叫方式：GET 或 POST /api/logout → 302 redirect 到 /login.html
 * ============================================================================
 */

const COOKIE_NAME = 'jht_auth';

// ---------------------------------------------------------------------------
// Secure Cookie 判斷：與 api/login.js 的 shouldUseSecureCookie() 邏輯一致，
// 確保登出時清除 Cookie 的屬性（是否有 Secure）與登入時簽發的一致，
// 才能正確覆蓋掉瀏覽器內原本的 Cookie。
// ---------------------------------------------------------------------------
function shouldUseSecureCookie(req) {
  const isProdEnv = process.env.VERCEL_ENV === 'production' || process.env.NODE_ENV === 'production';
  const isOnVercel = !!process.env.VERCEL; // 只要跑在 Vercel（含 Preview），一律視為 https
  const forwardedProto = String((req.headers && req.headers['x-forwarded-proto']) || '').toLowerCase();
  const isForwardedHttps = forwardedProto.includes('https');
  return isProdEnv || isOnVercel || isForwardedHttps;
}

module.exports = async function handler(req, res) {
  const cookieParts = [`${COOKIE_NAME}=`, 'Path=/', 'HttpOnly', 'SameSite=Lax', 'Max-Age=0'];
  if (shouldUseSecureCookie(req)) cookieParts.push('Secure');

  res.setHeader('Set-Cookie', cookieParts.join('; '));
  res.writeHead(302, { Location: '/login.html' });
  res.end();
};
