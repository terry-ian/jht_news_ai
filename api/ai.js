/**
 * ============================================================================
 * api/ai.js — Google Gemini AI 代理端點（Vercel Serverless Function）
 * ============================================================================
 *
 * 用途：
 *   取代前端直接呼叫 OpenAI 的舊架構。前端一律只呼叫本站的 /api/ai，
 *   由本檔案在「伺服器端」代為呼叫 Google Gemini，金鑰／服務帳戶永遠
 *   不會出現在瀏覽器或前端程式碼中。
 *
 * 認證模式（依環境變數自動判斷，二擇一）：
 *   (a) Vertex AI 服務帳戶模式（預設）：
 *       - 環境變數 GOOGLE_SERVICE_ACCOUNT_JSON：整份服務帳戶 JSON 字串
 *         （Vercel 部署時，把 jht-pm-c05446ebd432.json 的「檔案內容」整段
 *          貼進此環境變數，不要上傳檔案本身）。
 *       - 或環境變數 GOOGLE_APPLICATION_CREDENTIALS：指向服務帳戶 JSON 檔案路徑
 *         （本機開發時使用，例如指向 jht-pm-c05446ebd432.json 的絕對路徑）。
 *       - 搭配 GCP_PROJECT_ID（預設 jht-pm）與 GCP_LOCATION（預設 us-central1）。
 *   (b) Google AI Studio API Key 模式（較簡單、便宜，適合快速測試）：
 *       - 只要設定環境變數 GEMINI_API_KEY，就會自動改走此模式，
 *         優先權高於 Vertex AI 模式。
 *
 * 模型名稱：
 *   一律由環境變數 GEMINI_MODEL 控制，預設 "gemini-3.1-flash-lite"（使用者指定）。
 *   ⚠️ 注意：若此 model id 尚未於 Google 正式發布，或帳號/專案無權限存取，
 *   呼叫時會收到 Google 回傳的 404 / model_not_found 之類錯誤。
 *   屆時請將 GEMINI_MODEL 環境變數改成實際可用的便宜模型，例如：
 *     - gemini-2.5-flash-lite
 *     - gemini-2.0-flash-lite
 *
 * 前端呼叫方式：
 *   GET  /api/ai
 *     → 回傳目前後端設定狀態（模型名稱／認證模式／是否已備妥憑證），
 *       不會實際呼叫 Gemini，用於「AI 設定」Modal 顯示狀態，零成本。
 *
 *   POST /api/ai
 *     body: { prompt: string, systemInstruction？: string, stream？: boolean }
 *
 *     stream 為 false（預設）→ 回傳 JSON：
 *       成功：{ text: string, model: string }
 *       失敗：{ error: string, model: string, missingCredentials？: boolean }
 *
 *     stream 為 true → 回傳 text/event-stream，格式統一為（無論底層是
 *       Vertex AI 或 Google AI Studio，前端都用同一套解析邏輯）：
 *       data: {"delta":"...新增文字片段..."}\n\n         （可能多次）
 *       data: {"done":true,"text":"...完整全文...","model":"..."}\n\n （結尾一次）
 *       data: {"error":"...錯誤訊息...","model":"..."}\n\n           （若串流中途出錯）
 *     注意：一旦 HTTP 200 與 SSE header 已送出，中途發生錯誤就只能用
 *     上述 data:{"error":...} 事件通知前端，無法再改變 HTTP 狀態碼。
 * ============================================================================
 */

const { GoogleAuth } = require('google-auth-library');

// ---------------------------------------------------------------------------
// 可調整參數（環境變數，皆有合理預設值）
// ---------------------------------------------------------------------------
const GEMINI_MODEL = process.env.GEMINI_MODEL || 'gemini-3.8-flash';
const GCP_PROJECT_ID = process.env.GCP_PROJECT_ID || 'jht-pm';
const GCP_LOCATION = process.env.GCP_LOCATION || 'us';
const GEMINI_API_KEY = process.env.GEMINI_API_KEY || '';

const REQUEST_TIMEOUT_MS = 60000; // 單次上游請求逾時（毫秒）
const MAX_RETRIES = 3;            // 非串流請求遇到 429 / 5xx 時的重試次數

let cachedGoogleAuth = null; // 模組層級快取，warm invocation 時可重複使用，減少重新建立 client 的成本

// ---------------------------------------------------------------------------
// 認證模式判斷
// ---------------------------------------------------------------------------
function useApiKeyMode() {
  return !!GEMINI_API_KEY;
}

function hasServiceAccountConfigured() {
  return !!(process.env.GOOGLE_SERVICE_ACCOUNT_JSON || process.env.GOOGLE_APPLICATION_CREDENTIALS);
}

function getServiceAccountCredentialsFromEnv() {
  if (process.env.GOOGLE_SERVICE_ACCOUNT_JSON) {
    try {
      return JSON.parse(process.env.GOOGLE_SERVICE_ACCOUNT_JSON);
    } catch (e) {
      throw new Error('環境變數 GOOGLE_SERVICE_ACCOUNT_JSON 不是有效的 JSON 字串：' + e.message);
    }
  }
  return null; // 回傳 null 時，google-auth-library 會自行嘗試 GOOGLE_APPLICATION_CREDENTIALS 或其他 ADC 來源
}

async function getVertexAccessToken() {
  if (!cachedGoogleAuth) {
    const credentials = getServiceAccountCredentialsFromEnv();
    const authOptions = { scopes: ['https://www.googleapis.com/auth/cloud-platform'] };
    if (credentials) authOptions.credentials = credentials;
    cachedGoogleAuth = new GoogleAuth(authOptions);
  }
  const client = await cachedGoogleAuth.getClient();
  const tokenResult = await client.getAccessToken();
  const token = typeof tokenResult === 'string' ? tokenResult : (tokenResult && tokenResult.token);
  if (!token) {
    throw new Error('無法取得 Vertex AI access token（服務帳戶認證失敗，請確認 GOOGLE_SERVICE_ACCOUNT_JSON / GOOGLE_APPLICATION_CREDENTIALS 是否正確設定）。');
  }
  return token;
}

// ---------------------------------------------------------------------------
// 後端設定狀態（供 GET /api/ai 顯示，不呼叫 Gemini，零成本）
// ---------------------------------------------------------------------------
function getBackendStatus() {
  if (useApiKeyMode()) {
    return { model: GEMINI_MODEL, authMode: 'gemini-api-key', ready: true };
  }
  return {
    model: GEMINI_MODEL,
    authMode: 'vertex-ai',
    ready: hasServiceAccountConfigured(),
    project: GCP_PROJECT_ID,
    location: GCP_LOCATION
  };
}

// ---------------------------------------------------------------------------
// 組裝 Gemini generateContent 請求 body（Vertex AI 與 Google AI Studio 格式相同）
// ---------------------------------------------------------------------------
function buildGeminiRequestBody(prompt, systemInstruction) {
  const body = {
    contents: [{ role: 'user', parts: [{ text: prompt || '' }] }],
    generationConfig: { temperature: 0.7 }
  };
  if (systemInstruction) {
    body.systemInstruction = { role: 'system', parts: [{ text: systemInstruction }] };
  }
  return body;
}

function getVertexHost(location) {
  if (!location || location === 'global') {
    return 'aiplatform.googleapis.com';
  }
  // 處理多區域端點 (us / eu)
  if (location === 'us') {
    return 'aiplatform.us.rep.googleapis.com';
  }
  if (location === 'eu') {
    return 'aiplatform.eu.rep.googleapis.com';
  }
  // 標準 Regional 端點 (例如 us-central1, asia-northeast1)
  return `${location}-aiplatform.googleapis.com`;
}

function getEndpointAndAuth(streamMode) {
  const method = streamMode ? 'streamGenerateContent' : 'generateContent';
  if (useApiKeyMode()) {
    const base = `https://generativelanguage.googleapis.com/v1beta/models/${encodeURIComponent(GEMINI_MODEL)}:${method}`;
    const url = streamMode ? `${base}?alt=sse&key=${GEMINI_API_KEY}` : `${base}?key=${GEMINI_API_KEY}`;
    return { url, needsAuthToken: false };
  }
  const host = getVertexHost(GCP_LOCATION);
  const base = `https://${host}/v1/projects/${GCP_PROJECT_ID}/locations/${GCP_LOCATION}/publishers/google/models/${encodeURIComponent(GEMINI_MODEL)}:${method}`;
  // Vertex AI 的 streamGenerateContent 加上 alt=sse 會以 Server-Sent Events 格式回傳
  // （與 Google AI Studio 相同格式），方便前後端用同一套解析邏輯。
  const url = streamMode ? `${base}?alt=sse` : base;
  return { url, needsAuthToken: true };
}

async function fetchWithTimeout(url, options, timeoutMs) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

function extractTextFromGeminiJson(json) {
  try {
    const parts = (json && json.candidates && json.candidates[0] && json.candidates[0].content && json.candidates[0].content.parts) || [];
    return parts.map((p) => p.text || '').join('');
  } catch (e) {
    return '';
  }
}

// ---------------------------------------------------------------------------
// 非串流呼叫（含重試 / 指數退避，僅對 429 / 5xx 重試）
// ---------------------------------------------------------------------------
async function callGeminiNonStream(prompt, systemInstruction) {
  const { url, needsAuthToken } = getEndpointAndAuth(false);
  const headers = { 'Content-Type': 'application/json' };
  if (needsAuthToken) {
    const token = await getVertexAccessToken();
    headers['Authorization'] = `Bearer ${token}`;
  }
  const body = JSON.stringify(buildGeminiRequestBody(prompt, systemInstruction));

  let lastErr;
  let delay = 800;
  for (let attempt = 0; attempt < MAX_RETRIES; attempt++) {
    try {
      const resp = await fetchWithTimeout(url, { method: 'POST', headers, body }, REQUEST_TIMEOUT_MS);
      if (!resp.ok) {
        const errText = await resp.text().catch(() => '');
        const err = new Error(`Gemini API 錯誤碼: ${resp.status}${errText ? '，訊息：' + errText.slice(0, 500) : ''}`);
        err.status = resp.status;
        if (resp.status === 429 || resp.status >= 500) throw err; // 可重試
        err.noRetry = true;
        throw err;
      }
      const json = await resp.json();
      const text = extractTextFromGeminiJson(json);
      if (!text) throw new Error('Gemini API 回傳中無生成之文字內容。');
      return text;
    } catch (e) {
      lastErr = e;
      if (e.noRetry || e.name === 'AbortError' || attempt === MAX_RETRIES - 1) throw e;
      await new Promise((r) => setTimeout(r, delay));
      delay *= 2;
    }
  }
  throw lastErr;
}

/**
 * 混合式串流資料解析器：
 * Vertex AI 與 Google AI Studio 在加上 alt=sse 後理論上都會回傳標準 SSE
 * （每行 "data: {...}"），但為了穩健起見，本解析器同時支援「萬一收到的是
 * 原始 JSON 陣列串流（無 data: 前綴）」的情況，自動偵測並切換解析模式。
 * @param {(obj: any) => void} onObject 每解析出一個完整 JSON 物件就呼叫一次
 */
function createHybridStreamParser(onObject) {
  let buffer = '';
  let mode = null; // 'sse' | 'jsonarray'

  return function feed(chunkText) {
    buffer += chunkText;

    if (mode === null) {
      const trimmed = buffer.trimStart();
      if (!trimmed) return;
      mode = trimmed.startsWith('data:') ? 'sse' : 'jsonarray';
    }

    if (mode === 'sse') {
      const lines = buffer.split('\n');
      buffer = lines.pop(); // 保留未完整的最後一行
      for (const rawLine of lines) {
        const line = rawLine.trim();
        if (!line || !line.startsWith('data:')) continue;
        const dataStr = line.slice(5).trim();
        if (!dataStr || dataStr === '[DONE]') continue;
        try { onObject(JSON.parse(dataStr)); } catch (e) { /* 忽略不完整片段 */ }
      }
      return;
    }

    // jsonarray 模式：手動掃描平衡大括號，取出每個完整的頂層 JSON 物件
    let depth = 0;
    let inStr = false;
    let escape = false;
    let objStart = -1;
    let consumedUpto = 0;

    for (let i = 0; i < buffer.length; i++) {
      const ch = buffer[i];
      if (inStr) {
        if (escape) escape = false;
        else if (ch === '\\') escape = true;
        else if (ch === '"') inStr = false;
        continue;
      }
      if (ch === '"') { inStr = true; continue; }
      if (ch === '{') {
        if (depth === 0) objStart = i;
        depth++;
      } else if (ch === '}') {
        depth--;
        if (depth === 0 && objStart !== -1) {
          const objStr = buffer.slice(objStart, i + 1);
          try { onObject(JSON.parse(objStr)); } catch (e) { /* 忽略解析失敗片段 */ }
          consumedUpto = i + 1;
          objStart = -1;
        }
      }
    }
    if (consumedUpto > 0) buffer = buffer.slice(consumedUpto);
  };
}

// ---------------------------------------------------------------------------
// 串流呼叫：把上游 Gemini 回應即時轉譯為本站統一的 SSE 協定，寫回給前端
// ---------------------------------------------------------------------------
async function callGeminiStream(prompt, systemInstruction, res) {
  const { url, needsAuthToken } = getEndpointAndAuth(true);
  const headers = { 'Content-Type': 'application/json' };
  if (needsAuthToken) {
    const token = await getVertexAccessToken();
    headers['Authorization'] = `Bearer ${token}`;
  }
  const body = JSON.stringify(buildGeminiRequestBody(prompt, systemInstruction));

  const upstream = await fetchWithTimeout(url, { method: 'POST', headers, body }, REQUEST_TIMEOUT_MS);

  if (!upstream.ok) {
    const errText = await upstream.text().catch(() => '');
    const err = new Error(`Gemini API 錯誤碼: ${upstream.status}${errText ? '，訊息：' + errText.slice(0, 500) : ''}`);
    err.status = upstream.status;
    throw err; // 尚未寫入任何 SSE 內容，讓外層以一般 HTTP 錯誤回應前端
  }

  // ---- 從這裡開始才真正對前端送出 200 + SSE，之後若出錯只能用事件通知 ----
  res.writeHead(200, {
    'Content-Type': 'text/event-stream; charset=utf-8',
    'Cache-Control': 'no-cache, no-transform',
    Connection: 'keep-alive'
  });

  const reader = upstream.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let fullText = '';

  const parser = createHybridStreamParser((obj) => {
    const delta = extractTextFromGeminiJson(obj);
    if (delta) {
      fullText += delta;
      res.write(`data: ${JSON.stringify({ delta, model: GEMINI_MODEL })}\n\n`);
    }
  });

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      parser(decoder.decode(value, { stream: true }));
    }
    res.write(`data: ${JSON.stringify({ done: true, text: fullText, model: GEMINI_MODEL })}\n\n`);
  } catch (e) {
    res.write(`data: ${JSON.stringify({ error: (e && e.message) || String(e), model: GEMINI_MODEL })}\n\n`);
  } finally {
    res.end();
  }
}

// ---------------------------------------------------------------------------
// Vercel Serverless Function 進入點
// ---------------------------------------------------------------------------
module.exports = async function handler(req, res) {
  if (req.method === 'GET') {
    res.status(200).json({ ok: true, ...getBackendStatus() });
    return;
  }

  if (req.method !== 'POST') {
    res.status(405).json({ error: 'Method Not Allowed，請使用 GET 或 POST。' });
    return;
  }

  let body = req.body;
  if (typeof body === 'string') {
    try { body = JSON.parse(body); } catch (e) { body = {}; }
  }
  body = body || {};
  const { prompt, systemInstruction, stream } = body;

  if (!prompt || typeof prompt !== 'string') {
    res.status(400).json({ error: '缺少必要參數 prompt（字串）。', model: GEMINI_MODEL });
    return;
  }

  const missingCredentials = !useApiKeyMode() && !hasServiceAccountConfigured();

  try {
    if (stream) {
      await callGeminiStream(prompt, systemInstruction || '', res);
    } else {
      if (missingCredentials) {
        res.status(503).json({
          error: '伺服器尚未設定 Gemini／Vertex AI 認證（缺少 GOOGLE_SERVICE_ACCOUNT_JSON / GOOGLE_APPLICATION_CREDENTIALS / GEMINI_API_KEY 環境變數）。',
          model: GEMINI_MODEL,
          missingCredentials: true
        });
        return;
      }
      const text = await callGeminiNonStream(prompt, systemInstruction || '');
      res.status(200).json({ text, model: GEMINI_MODEL });
    }
  } catch (err) {
    console.error('[api/ai] 呼叫 Gemini 失敗：', err);
    if (!res.headersSent) {
      const status = (err && Number.isInteger(err.status) && err.status >= 400 && err.status < 600) ? err.status : 500;
      res.status(status).json({
        error: (err && err.message) || 'AI 服務發生未知錯誤。',
        model: GEMINI_MODEL,
        missingCredentials
      });
    } else {
      // SSE 已開始輸出，狀態碼無法再改變，盡量安全關閉連線
      try { res.end(); } catch (e2) { /* noop */ }
    }
  }
};
