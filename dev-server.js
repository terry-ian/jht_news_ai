#!/usr/bin/env node
/**
 * ============================================================================
 * dev-server.js — 本機開發用輕量伺服器（不需要安裝 Vercel CLI）
 * ============================================================================
 *
 * 同時提供：
 *   1. 靜態檔案服務（index.html、assets/、data/ 等，與 serve.py 功能相同）。
 *   2. /api/ai 端點 → 直接載入並呼叫 api/ai.js 匯出的 serverless function
 *      handler，並模擬 Vercel Node.js Serverless Function 所需的
 *      req.body / res.status().json() 介面，讓同一份 api/ai.js 程式碼
 *      在「本機」與「Vercel 正式部署」上完全共用、不必修改任何一行。
 *
 * 使用方式：
 *   1. 複製 .env.example 為 .env.local，填入所需環境變數
 *      （GOOGLE_SERVICE_ACCOUNT_JSON 或 GOOGLE_APPLICATION_CREDENTIALS，
 *       或 GEMINI_API_KEY）。
 *   2. 安裝依賴（僅需一次）：npm install
 *   3. 啟動： node dev-server.js         （預設埠 8000）
 *            node dev-server.js 8080    （指定埠號）
 *   4. 開啟瀏覽器：http://localhost:8000
 *
 * 優先建議：若已安裝 Vercel CLI，可改用 `npx vercel dev`，
 * 行為會更接近正式 Vercel 環境；本檔案是給尚未安裝 Vercel CLI 時的替代方案。
 *
 * 安全性：僅綁定 127.0.0.1（本機），不對外開放；且僅是開發用途，
 * 沒有正式伺服器該有的安全強化（rate limit、輸入驗證強度等）。
 * ============================================================================
 */

const http = require('http');
const fs = require('fs');
const path = require('path');
const { URL } = require('url');

// ---------------------------------------------------------------------------
// 簡易 .env 檔載入（不依賴 dotenv 套件），依序讀取 .env.local 再讀取 .env，
// 已存在於 process.env 的變數不會被覆蓋（方便用系統環境變數覆蓋檔案設定）。
// ---------------------------------------------------------------------------
function loadEnvFile(filename) {
  const filePath = path.join(__dirname, filename);
  if (!fs.existsSync(filePath)) return;
  const content = fs.readFileSync(filePath, 'utf8');
  content.split('\n').forEach((line) => {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) return;
    const idx = trimmed.indexOf('=');
    if (idx === -1) return;
    const key = trimmed.slice(0, idx).trim();
    let value = trimmed.slice(idx + 1).trim();
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }
    if (!(key in process.env)) process.env[key] = value;
  });
}
loadEnvFile('.env.local');
loadEnvFile('.env');

const aiHandler = require('./api/ai.js');

const PORT = parseInt(process.argv[2], 10) || 8000;
const ROOT_DIR = __dirname;

const MIME_TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'application/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.svg': 'image/svg+xml',
  '.ico': 'image/x-icon'
};

// ---------------------------------------------------------------------------
// 機密檔案 denylist：避免 .env、服務帳戶金鑰 JSON 等敏感檔案被當成一般靜態
// 檔案直接吐給瀏覽器（QA 回報：GET /.env.local、GET /jht-pm-*.json 曾回 200
// 並外洩服務帳戶 private key）。
//
// 規則（符合任一即擋）：
//   1. 路徑中任何一段檔名/資料夾名稱以 "." 開頭
//      → 擋 .env、.env.local、.gitignore、.git/ 等。
//   2. 檔名中含有 "service" + "account"（不分大小寫、允許中間有 - 或 _）
//      → 擋各種 service-account / serviceaccount 命名的金鑰檔。
//   3. 副檔名為 .json，但不是放在 data/ 目錄下
//      → data/news.json 等前端要 fetch 的資料檔仍可正常存取；
//        其餘位置的 .json（例如 GCP 服務帳戶金鑰 jht-pm-xxxx.json、
//        package.json、vercel.json 等）一律視為非公開檔案不予提供。
// ---------------------------------------------------------------------------
function isDeniedFile(filePath) {
  const relFromRoot = path.relative(ROOT_DIR, filePath);
  const segments = relFromRoot.split(path.sep).filter(Boolean);

  if (segments.some((seg) => seg.startsWith('.'))) return true;

  const base = path.basename(filePath).toLowerCase();
  if (/service[-_]?account/.test(base)) return true;

  const ext = path.extname(filePath).toLowerCase();
  if (ext === '.json') {
    const isUnderData = segments[0] === 'data';
    if (!isUnderData) return true;
  }

  return false;
}

function sendStaticFile(res, urlPath) {
  let relPath = decodeURIComponent(urlPath.split('?')[0]);
  if (relPath === '/') relPath = '/index.html';
  const filePath = path.normalize(path.join(ROOT_DIR, relPath));

  // 安全檢查：避免路徑跳脫到專案資料夾外（例如 ../../ 之類的路徑穿越）
  if (!filePath.startsWith(ROOT_DIR)) {
    res.writeHead(403);
    res.end('Forbidden');
    return;
  }

  // 安全檢查：擋掉機密檔案（.env*、服務帳戶金鑰 JSON、其他非 data/ 目錄的 JSON）
  if (isDeniedFile(filePath)) {
    res.writeHead(403);
    res.end('Forbidden');
    return;
  }

  fs.readFile(filePath, (err, data) => {
    if (err) {
      res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' });
      res.end('404 Not Found: ' + relPath);
      return;
    }
    const ext = path.extname(filePath).toLowerCase();
    res.writeHead(200, {
      'Content-Type': MIME_TYPES[ext] || 'application/octet-stream',
      // 避免瀏覽器快取 news.json 等檔案，方便本機測試「重新整理資料」按鈕
      'Cache-Control': 'no-store, no-cache, must-revalidate'
    });
    res.end(data);
  });
}

// 讀取 request body 並解析為 JSON，模擬 Vercel Node function 的 req.body
function readJsonBody(req) {
  return new Promise((resolve) => {
    let raw = '';
    req.on('data', (chunk) => { raw += chunk; });
    req.on('end', () => {
      if (!raw) return resolve({});
      try { resolve(JSON.parse(raw)); } catch (e) { resolve({}); }
    });
    req.on('error', () => resolve({}));
  });
}

// 為原生 http.ServerResponse 補上 Vercel 慣用的 res.status().json() 語法糖，
// 讓 api/ai.js 完全不需要因為執行環境（Vercel vs 本機）而分支判斷。
function enhanceResponse(res) {
  res.status = function (code) {
    res.statusCode = code;
    return res;
  };
  res.json = function (obj) {
    if (!res.getHeader('Content-Type')) {
      res.setHeader('Content-Type', 'application/json; charset=utf-8');
    }
    res.end(JSON.stringify(obj));
    return res;
  };
  return res;
}

const server = http.createServer(async (req, res) => {
  const parsedUrl = new URL(req.url, `http://${req.headers.host}`);

  if (parsedUrl.pathname === '/api/ai') {
    enhanceResponse(res);
    if (req.method === 'POST') {
      req.body = await readJsonBody(req);
    }
    try {
      await aiHandler(req, res);
    } catch (e) {
      console.error('[dev-server] /api/ai 執行錯誤：', e);
      if (!res.headersSent) {
        res.status(500).json({ error: (e && e.message) || String(e) });
      } else {
        res.end();
      }
    }
    return;
  }

  if (req.method !== 'GET' && req.method !== 'HEAD') {
    res.writeHead(405);
    res.end('Method Not Allowed');
    return;
  }

  sendStaticFile(res, parsedUrl.pathname);
});

server.listen(PORT, '127.0.0.1', () => {
  console.log('喬山 AI 產業情報儀表板 -- 本機開發伺服器（靜態檔 + /api/ai）已啟動');
  console.log(`目錄: ${ROOT_DIR}`);
  console.log(`請開啟瀏覽器造訪: http://localhost:${PORT}`);
  console.log('AI 代理端點: GET/POST /api/ai');
  console.log('按 Ctrl+C 停止伺服器');
});
