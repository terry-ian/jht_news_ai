/* ==========================================================================
 * 喬山 AI 全球健身產業情報平台 -- v3（傳統 script，掛全域，不用 import/export）
 * --------------------------------------------------------------------------
 * 依 docs/v3-spec.md 為唯一契約。資料來源 ./data/news.json（8311 篇，與 v2
 * 共用同一份真實資料）。標籤判定沿用 assets/tag-map-v3.js（window.TagMapV3），
 * 依契約第 11-4 節：優先讀 news.json 新欄位（topic_tags/product_types/
 * attention_tags/country/brands），欄位不存在才呼叫 TagMapV3 現算，兩種情況
 * 皆須正確運作（scraper 回填可能尚在進行中）。
 * AI 呼叫沿用 assets/app-v2.js 的呼叫寫法（含逾時重試、429 處理、SSE 串流、
 * 非串流 fallback），一律使用者按下按鈕才呼叫，呼叫後 cache。
 * ========================================================================== */

'use strict';

/* ------------------------------------------------------------------------
 * 0. 常數
 * ---------------------------------------------------------------------- */
var DATA_URL = './data/news.json';
var AI_PROXY_ENDPOINT = './api/ai';
// 預設模型標籤，對齊 api/ai.js 的 GEMINI_MODEL 預設值；實際呼叫成功後一律以
// 後端回傳的 data.model / SSE 事件的 model 欄位覆寫，此常數只是首次顯示用。
var AI_MODEL_LABEL_DEFAULT = 'gemini-3.8-flash';
var v3AIModelLabel = AI_MODEL_LABEL_DEFAULT;

var LANG_KEY = 'jht-v3-lang';
var AI_CACHE_KEY = 'jht-v3-ai-cache';
var INSIGHT_KEY = 'jht-v3-insight';
var TITLE_TR_KEY = 'jht-v3-title-tr';

// Dashboard「競品與產品更新」區塊的納入條件（第 6 節），與「查看更多」導向
// 探索頁時套用的 topic 子集（第 5-3 節）刻意不同，兩者分別依規格各自實作。
var COMPETITOR_BLOCK_TOPICS = ['新品發布', '產品改版', '產品賣點', '價格策略'];
var COMPETITOR_MORE_TOPICS = ['新品發布', '產品改版'];

/* ------------------------------------------------------------------------
 * 1. i18n：key 命名 namespace.name，namespace 為 hdr/nav/ov/ex/card/ai/modal/
 *    tag/footer/common/insight/chat。value 為 [zh, en]。
 * ---------------------------------------------------------------------- */
var I18N_PAIRS = {
  'doc.title': ['全球健身產業 AI 情報平台 v3', 'Global Fitness Industry AI Intelligence Platform v3'],
  'common.loadingNews': ['情報資料載入中（約9.5MB，8311篇），請稍候...', 'Loading intelligence data (about 9.5MB, 8,311 items), please wait...'],
  'common.loadFail': ['資料載入失敗，請稍後重新整理頁面再試一次。', 'Failed to load data. Please refresh the page and try again.'],
  'common.all': ['全部', 'All'],
  'common.rangeWeek': ['本週', 'This week'],
  'common.rangeMonth': ['本月', 'This month'],
  'common.rangeQuarter': ['本季', 'This quarter'],
  'common.close': ['關閉', 'Close'],
  'common.send': ['送出', 'Send'],
  'common.emptyCards': ['目前篩選條件下暫無情報。', 'No intelligence matches the current filters.'],
  'common.emptyTrend': ['目前篩選條件下暫無足夠資料。', 'Not enough data under the current filters.'],

  'hdr.title': ['全球健身產業 AI 情報平台', 'Global Fitness Industry AI Intelligence Platform'],
  'hdr.langToggle': ['切換語系', 'Switch language'],
  'hdr.aiSettings': ['AI 設定', 'AI settings'],
  'hdr.feedback': ['意見回饋', 'Feedback'],
  'hdr.logout': ['登出', 'Log out'],

  'nav.overview': ['總覽', 'Overview'],
  'nav.explore': ['探索情報', 'Explore'],
  'nav.ai': ['AI 助理', 'AI Assistant'],

  'ov.sub': ['值得關注的競品動態、功能更新和產品機會，都幫你整理好了。', 'The competitor moves, feature updates and product opportunities worth your attention, all organised for you.'],
  'ov.updatedPrefix': ['最後更新：', 'Last updated: '],
  'ov.filterRole': ['角色', 'Role'],
  'ov.roleP': ['PM', 'PM'],
  'ov.roleMarketing': ['Marketing', 'Marketing'],
  'ov.roleDesign': ['Design', 'Design'],
  'ov.filterAttention': ['關注領域', 'Focus area'],
  'ov.filterRange': ['時間範圍', 'Time range'],
  'ov.refresh': ['重新整理', 'Refresh'],
  'ov.refreshTitle': ['重新整理僅重新讀取同一份 news.json，不會觸發爬蟲更新資料', 'Refresh only re-reads the same news.json file; it does not trigger a new crawl.'],
  'ov.kpiTitle': ['情報概況', 'Intelligence overview'],
  'ov.kpiTotal': ['總情報數', 'Total items'],
  'ov.kpiNew': ['新增情報', 'New this week'],
  'ov.kpiBrands': ['涵蓋品牌', 'Brands covered'],
  'ov.kpiTopics': ['涵蓋主題', 'Topics covered'],
  'ov.trendTitle': ['情報趨勢', 'Intelligence trends'],
  'ov.trendTopics': ['熱門情報主題 Top 5', 'Top 5 trending topics'],
  'ov.trendBrands': ['品牌情報熱度 Top 5', 'Top 5 brands by coverage'],
  'ov.focusTitle': ['焦點情報', 'Focus intelligence'],
  'ov.focusSub': ['AI 從產品情報中，挑出最值得你先看的 3 則。', 'AI picked the 3 items most worth reading first.'],
  'ov.more': ['查看更多', 'View more'],
  'ov.competitorTitle': ['競品與產品更新', 'Competitor & product updates'],
  'ov.competitorSub': ['快速掌握競品推出了什麼、改了什麼，以及策略往哪裡走。', 'Quickly track what competitors launched, changed, and where their strategy is heading.'],
  'ov.softwareTitle': ['軟體與 AI 功能動態', 'Software & AI feature updates'],
  'ov.softwareSub': ['看看競品如何運用 AI、數據和個人化體驗，持續推進產品功能。', 'See how competitors use AI, data and personalisation to push their products forward.'],
  'ov.insightTitle': ['產品洞察', 'Product insights'],
  'ov.insightSub': ['綜合多篇情報，整理出 3 個值得留意的產品變化與機會。', 'Synthesised from multiple items into 3 product changes and opportunities worth watching.'],
  'ov.insightEmpty': ['按下按鈕，讓 AI 從近期情報中整理出值得留意的產品變化與機會。', 'Click the button to let AI surface product changes and opportunities from recent intelligence.'],
  'ov.insightGen': ['生成產品洞察', 'Generate product insights'],
  'ov.insightRegen': ['重新生成產品洞察', 'Regenerate product insights'],
  'ov.insightStale': ['篩選條件已變更，可重新生成', 'Filters have changed; you may regenerate.'],
  'ov.insightLoading': ['AI 正在分析近期情報，請稍候...', 'AI is analysing recent intelligence, please wait...'],
  'ov.insightError': ['AI 生成失敗或回傳格式無法解析，請稍後再試一次。', 'AI generation failed or returned an unparsable format. Please try again later.'],

  'footer.copy': ['喬山健康科技股份有限公司 © 2026 Johnson Health Tech. All Rights Reserved. AI 產業情報模組版權所有。', 'Johnson Health Tech Co., Ltd. \u00a9 2026 Johnson Health Tech. All Rights Reserved.'],
  'footer.note': ['本看板資料來源為自動化蒐集彙整之公開新聞，僅供內部決策參考，請自行審閱原文之準確性。', 'This dashboard aggregates public news via automated collection for internal reference only; please verify accuracy against original sources.'],

  'ex.title': ['探索情報', 'Explore intelligence'],
  'ex.countSuffix': ['筆情報', 'items'],
  'ex.searchPlaceholder': ['搜尋品牌、產品、功能或市場趨勢', 'Search brands, products, features or market trends'],
  'ex.role': ['角色', 'Role'],
  'ex.attention': ['領域', 'Focus'],
  'ex.type': ['情報類型', 'Type'],
  'ex.typeAll': ['全部', 'All'],
  'ex.country': ['地區', 'Region'],
  'ex.brand': ['Brand：', 'Brand:'],
  'ex.brandMore': ['+更多', '+More'],
  'ex.brandLess': ['收合', 'Show less'],
  'ex.ptype': ['Product Type：', 'Product Type:'],
  'ex.topic': ['Topic：', 'Topic:'],
  'ex.clear': ['清除篩選條件', 'Clear filters'],
  'ex.loadMore': ['查看更多情報', 'Load more'],
  'ex.empty': ['查無符合條件的情報，試試調整篩選條件。', 'No matching intelligence. Try adjusting the filters.'],

  'tag.attentionCommercial': ['商用', 'Commercial'],
  'tag.attentionHome': ['家用', 'Home'],
  'tag.attentionDigital': ['軟體', 'Digital'],
  'tag.countryAll': ['全部', 'All'],
  'tag.brandAll': ['全部', 'All'],
  'tag.ptypeAll': ['全部', 'All'],
  'tag.topicAll': ['全部', 'All'],

  'card.sourcePrefix': ['來源：', 'Source: '],
  'card.readOriginal': ['閱讀原文', 'Read original'],
  'card.aiSummary': ['AI一鍵摘要', 'AI Summary'],
  'card.aiLoading': ['生成中...', 'Generating...'],

  'insight.badgeTrend': ['產品趨勢', 'Product Trend'],
  'insight.badgeFeature': ['功能機會', 'Feature Opportunity'],
  'insight.badgeCompetitor': ['競品變化', 'Competitor Shift'],
  'insight.relatedCount': ['{n} 相關情報', '{n} related items'],
  'insight.sourcesLabel': ['相關來源', 'Related sources'],
  'insight.sectionMain': ['主要變化', 'Main change'],
  'insight.sectionImpact': ['可能影響', 'Possible impact'],
  'insight.sectionSuggest': ['建議關注方向', 'Suggested focus'],

  'ai.name': ['喬山 AI 產業情報助理', 'Johnson AI Industry Intelligence Assistant'],
  'ai.poweredBy': ['Powered by Google Gemini', 'Powered by Google Gemini'],
  'ai.welcome': ['您好，我是喬山 AI 產業情報小助手。近期情報已幫你整理好了！想了解競品最近在做什麼、市場有哪些新趨勢，或有哪些值得關注的變化，都可以問我。', 'Hi, I am the Johnson AI industry intelligence assistant. Recent intelligence is ready to explore. Ask me what competitors are up to, new market trends, or anything worth watching.'],
  'ai.quickLabel': ['快速諮詢', 'Quick prompts'],
  'ai.quick1': ['2026 健身科技趨勢', '2026 fitness tech trends'],
  'ai.quick2': ['近期競品重要動態', 'Recent key competitor moves'],
  'ai.quick3': ['熱門 AI 應用與功能', 'Popular AI features and applications'],
  'ai.inputPlaceholder': ['詢問情報小助手', 'Ask the intelligence assistant'],
  'ai.note': ['回覆由 AI 依公開情報整理，建議搭配原始來源確認。', 'Replies are generated by AI from public intelligence; please verify against original sources.'],
  'ai.errMissing': ['尚未設定 AI 後端金鑰，請聯絡管理員設定 Gemini 認證。', 'AI backend credentials are not configured. Please ask an admin to set up Gemini authentication.'],
  'ai.errGeneric': ['AI 呼叫發生錯誤：{msg}', 'AI call failed: {msg}'],
  'ai.ready': ['（已就緒）', ' (ready)'],
  'ai.notReady': ['（尚未就緒）', ' (not ready)'],
  'ai.backendToast': ['AI 後端模型：{model}{state}', 'AI backend model: {model}{state}'],
  'ai.offline': ['無法連線至 AI 後端。', 'Unable to reach the AI backend.'],
  'ai.unknown': ['未知', 'unknown'],

  'chat.thinking': ['思考中...', 'Thinking...'],
  'chat.failed': ['抱歉，這次呼叫失敗了，請稍後再試一次。', 'Sorry, this request failed. Please try again later.'],
  'chat.noContent': ['（AI 未回傳內容）', '(AI returned no content)'],

  'modal.title': ['AI 智能深度分析摘要', 'AI In-depth Analysis Summary'],
  'modal.close': ['關閉視窗', 'Close dialog'],
  'modal.originalLabel': ['情報原文標題', 'Original headline'],
  'modal.link': ['前往原文連結', 'Go to original link'],
  'modal.aiLabel': ['AI 深度提煉', 'AI deep-dive'],
  'modal.copy': ['複製摘要內容', 'Copy summary'],
  'modal.copied': ['已複製摘要內容', 'Summary copied'],
  'modal.loading': ['AI 正在生成深度摘要，請稍候...', 'AI is generating an in-depth summary, please wait...'],
  'modal.regen': ['重新生成摘要', 'Regenerate summary'],

  /* Q14 補充：卡片 chip 縮寫標籤（與 select#v3-sel-type 情報類型下拉的完整
   * categoryName 刻意不同，那裡維持完整字串，這裡只用短版避免撐爆卡片版面）。 */
  'tag.cat.market': ['市場', 'Market'],
  'tag.cat.competitor': ['競品', 'Competitor'],
  'tag.cat.tech': ['科技', 'Tech'],
  'tag.cat.finance': ['財經', 'Finance'],
  'tag.cat.brand': ['品牌', 'Brand']
};

/** 目前語言：'zh-Hant'（預設）或 'en'，記憶在 localStorage jht-v3-lang。 */
var LANG = 'zh-Hant';
try { if (localStorage.getItem(LANG_KEY) === 'en') LANG = 'en'; } catch (e) { /* localStorage 不可用時維持預設 */ }

/** 取 UI 文案；{n} 之類佔位以 vars 代入。查無 key 時回傳 key 本身，方便發現漏翻。 */
function t(key, vars) {
  var pair = I18N_PAIRS[key];
  var s = pair ? ((LANG === 'en' && pair[1]) ? pair[1] : pair[0]) : key;
  if (vars) {
    Object.keys(vars).forEach(function (k) { s = s.split('{' + k + '}').join(String(vars[k])); });
  }
  return s;
}

/**
 * 套用 index-v3.html 內 data-i18n / data-i18n-placeholder / data-i18n-title /
 * data-i18n-aria 標記的靜態文案。
 *
 * 修正：#v3-ex-title 這個 h1 本身也帶 data-i18n="ex.title"，但其內還巢狀著
 * #v3-ex-count（顯示「N 筆情報」，由 renderExplore() 動態寫入）。若直接對它
 * 做 el.textContent = ...，會把 #v3-ex-count 這個子節點整個吃掉、永久從 DOM
 * 移除，導致之後任何 renderExplore() 呼叫在寫入 #v3-ex-count.innerHTML 時
 * 對 null 取值而整頁探索情報功能全部炸掉。因此這裡把 #v3-ex-title 從一般迴圈
 * 中排除，改成只更新標題文字、保留 #v3-ex-count 子節點原樣。
 */
function applyStaticI18n() {
  document.documentElement.lang = (LANG === 'en') ? 'en' : 'zh-Hant';
  document.title = t('doc.title');
  document.querySelectorAll('[data-i18n]').forEach(function (el) {
    if (el.id === 'v3-ex-title') return;
    el.textContent = t(el.getAttribute('data-i18n'));
  });
  document.querySelectorAll('[data-i18n-placeholder]').forEach(function (el) { el.setAttribute('placeholder', t(el.getAttribute('data-i18n-placeholder'))); });
  document.querySelectorAll('[data-i18n-title]').forEach(function (el) { el.setAttribute('title', t(el.getAttribute('data-i18n-title'))); });
  document.querySelectorAll('[data-i18n-aria]').forEach(function (el) { el.setAttribute('aria-label', t(el.getAttribute('data-i18n-aria'))); });

  var exTitleEl = document.getElementById('v3-ex-title');
  if (exTitleEl) {
    var countEl = document.getElementById('v3-ex-count');
    var countHTML = countEl ? countEl.outerHTML : '';
    exTitleEl.textContent = t('ex.title');
    if (countHTML) exTitleEl.insertAdjacentHTML('beforeend', countHTML);
  }
}

/** 切換語言：不 reload，直接重新渲染整頁 UI。 */
function setLanguage(lang) {
  var next = (lang === 'en') ? 'en' : 'zh-Hant';
  if (next === LANG) return;
  LANG = next;
  try { localStorage.setItem(LANG_KEY, LANG); } catch (e) { /* 忽略 */ }
  applyStaticI18n();
  if (!ARTICLES.length) return;
  renderOverview();
  renderExplore();
  renderAIQuickPills();
  renderChatMessages();
  // 中文模式需即時翻譯目前畫面上的標題與摘要；英文模式直接顯示來源原文。
  if (LANG !== 'en') v3TranslateArticles(v3CurrentlyVisibleArticles(), function () { renderOverview(); renderExplore(); });
}

/* ------------------------------------------------------------------------
 * 2. 共用小工具
 * ---------------------------------------------------------------------- */
function escapeHTML(str) {
  return String(str == null ? '' : str)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
// 外部連結 scheme 白名單：只允許 http/https，防止上游爬蟲資料被污染成
// javascript: / data: 等可執行 scheme 時，被當成 href 原樣輸出造成 XSS。
// 不符合或解析失敗回傳 null，呼叫端須自行處理「沒有安全連結」的降級顯示。
function safeExternalUrl(u) {
  if (typeof u !== 'string' || !u) return null;
  if (!/^https?:\/\//i.test(u)) return null;
  try { new URL(u); } catch (e) { return null; }
  return u;
}
function fmtNum(n) { return (n == null || isNaN(n)) ? '--' : Number(n).toLocaleString('zh-TW'); }
function fmtDate(d) { return (d || '').split('-').join('/'); }
function clamp(n, a, b) { return Math.max(a, Math.min(b, n)); }

/**
 * P0 資料防禦（docs/v3-spec.md 第 12-1 / 12-2 節）：news.json 的 summary 欄位
 * 有 83.8% 是 scraper 端 "summary = title + ' ' + source" 的 fallback 污染
 * （另有 9 篇精確等於 title），並非真摘要。凡取用 a.summary 的地方（卡片渲染／
 * 即時翻譯佇列／AI prompt context／AI 摘要 Modal）一律必須呼叫本函式，不得
 * 各自比對。根因在 scraper/fetch_news.py:2851，本輪不修改 scraper（見第 12-3
 * 節），此函式是唯一的前端防禦點。
 * @return {string|null} 可用摘要原文，或 null 表示視為「無摘要」。
 */
function normalizeSpace(s) { return String(s == null ? '' : s).replace(/\s+/g, ' ').trim(); }
// 寬鬆比對門檻：summary 正規化後以 title 開頭，且開頭之後剩餘字元數小於此值時，
// 視為「接了來源名」的污染（例如 title + 半個來源縮寫），一併擋掉。
var USABLE_SUMMARY_LOOSE_TAIL_MAX = 30;
function usableSummary(a) {
  if (!a || typeof a.summary !== 'string') return null;
  var summary = normalizeSpace(a.summary);
  if (!summary) return null;
  var title = normalizeSpace(a.title);
  var source = normalizeSpace(a.source);
  if (title && summary === title) return null;
  if (title && source && summary === (title + ' ' + source)) return null;
  if (title && summary.indexOf(title) === 0) {
    var tail = summary.slice(title.length).trim();
    if (tail.length < USABLE_SUMMARY_LOOSE_TAIL_MAX) return null;
  }
  return a.summary;
}

function loadLocalJSON(key, fallback) {
  try {
    var v = JSON.parse(localStorage.getItem(key));
    return (v === null || v === undefined) ? fallback : v;
  } catch (e) { return fallback; }
}
function saveLocal(key, val) {
  try { localStorage.setItem(key, JSON.stringify(val)); }
  catch (e) { /* localStorage 不可用（例如隱私模式）時忽略，不影響功能 */ }
}

/**
 * 使用者身分預留（決議 8）：日後接多帳號時只改這個函式（可改從 API 或 cookie
 * 取得），使用者名稱與縮寫不得散落在其他地方。
 */
function getCurrentUser() {
  return { initials: 'GM', displayName: 'Global Marketing' };
}

/* ------------------------------------------------------------------------
 * 3. 全域狀態（沿用 v2 的 S 模式）
 * ---------------------------------------------------------------------- */
var S = {
  view: 'overview',
  ovRole: 'all', ovAttention: 'all', ovRange: 'week',
  exRange: 'week', exRole: 'all', exAttention: 'all',
  exType: 'all', exCountry: '全部',
  exBrands: [], exPtypes: [], exTopics: [],
  query: '', shown: 9,
  aiCache: loadLocalJSON(AI_CACHE_KEY, {}), aiLoading: {},
  insightCache: loadLocalJSON(INSIGHT_KEY, null), insightLoading: false,
  chat: [], chatThinking: false,
  drawerOpen: false,
  // 以下為 UI 專用的額外欄位（契約未列出，但為「Brand +更多」展開狀態所需，不持久化）：
  brandChipsExpanded: false
};

function persistAICache() { saveLocal(AI_CACHE_KEY, S.aiCache); }
function persistInsightCache() { saveLocal(INSIGHT_KEY, S.insightCache); }

/* ------------------------------------------------------------------------
 * 4. 時間範圍（以 news.json 的 generated_at 當基準日，不用 Date.now()）
 * --------------------------------------------------------------------
 * 資料最後更新為 2026-09-03，若用「今天」的實際系統時間當基準，本週／本月
 * 篩選很可能會落在資料完全沒有覆蓋到的區間而變成 0 筆。改用資料本身的
 * generated_at 當「現在」，才能讓「本週／本月／本季」對這份靜態資料集有意義。
 * ---------------------------------------------------------------------- */
var BASIS_DATE = '';   // YYYY-MM-DD，取自 news.json 的 generated_at
var RANGE_START = {};  // { week: 'YYYY-MM-DD', month: '...', quarter: '...' }

function computeRangeStarts(basisDateStr) {
  var basis = new Date(basisDateStr + 'T00:00:00Z');
  function minus(days) {
    var d = new Date(basis.getTime() - days * 86400000);
    return d.toISOString().slice(0, 10);
  }
  return { week: minus(7), month: minus(30), quarter: minus(90) };
}

/* ------------------------------------------------------------------------
 * 5. 標籤預計算（第 6 節效能要求 + 第 11-4 節欄位讀取優先序）
 * --------------------------------------------------------------------
 * 依序讀取 news.json 新欄位 topic_tags / product_types / attention_tags /
 * country / brands；欄位不存在（目前尚未回填）才呼叫 TagMapV3 現算。計算結果
 * 掛在 article._tags，之後篩選與渲染只讀快取，不再重跑 regex。
 * ---------------------------------------------------------------------- */
function computeArticleTags(a) {
  var topics = Array.isArray(a.topic_tags) ? a.topic_tags : TagMapV3.getTopics(a);
  var ptypes = Array.isArray(a.product_types) ? a.product_types : TagMapV3.getProductTypes(a);
  var attention = Array.isArray(a.attention_tags) ? a.attention_tags : TagMapV3.getAttention(a);
  var country = (typeof a.country === 'string' && a.country) ? a.country : TagMapV3.getCountry(a);
  var brands = Array.isArray(a.brands) ? a.brands : TagMapV3.getBrands(a);
  var audiences = TagMapV3.getAudiences(a);
  return { topics: topics, ptypes: ptypes, attention: attention, country: country, brands: brands, audiences: audiences };
}

/**
 * 分批預計算，每批 500 筆＋setTimeout(0)，避免單次卡死主執行緒超過 100ms，
 * 期間持續更新 #v3-status-banner 顯示進度。完成後呼叫 onDone()。
 */
function precomputeTagsBatched(list, onDone) {
  var BATCH = 500;
  var i = 0;
  var total = list.length;
  function step() {
    var end = Math.min(i + BATCH, total);
    for (; i < end; i++) { list[i]._tags = computeArticleTags(list[i]); }
    if (i < total) {
      showStatusBanner(t('common.loadingNews'));
      setTimeout(step, 0);
    } else {
      onDone();
    }
  }
  step();
}

/* ------------------------------------------------------------------------
 * 6. 資料讀取與開機流程
 * ---------------------------------------------------------------------- */
var newsData = null;
var ARTICLES = [];       // 已排除 is_noise 的文章陣列（第 2 節：啟動時先過濾 583 篇 is_noise）
var articleById = {};

function showStatusBanner(msg, tone) {
  var el = document.getElementById('v3-status-banner');
  if (!el) return;
  el.hidden = false;
  el.classList.toggle('is-error', tone === 'error');
  var textEl = el.querySelector('.v3-status-text');
  if (textEl) textEl.textContent = msg;
}
function hideStatusBanner() {
  var el = document.getElementById('v3-status-banner');
  if (el) el.hidden = true;
}

async function boot() {
  applyStaticI18n();
  bindHeaderControls();
  bindAIDrawer();
  bindSummaryModal();
  showStatusBanner(t('common.loadingNews'));

  try {
    var res = await fetch(DATA_URL, { cache: 'no-store' });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    var json = await res.json();
    if (!json || !Array.isArray(json.articles)) throw new Error('news.json 缺少 articles 陣列');
    newsData = json;
  } catch (err) {
    console.warn('[v3] 無法載入 data/news.json：', err.message);
    showStatusBanner(t('common.loadFail'), 'error');
    return;
  }

  BASIS_DATE = (newsData.generated_at || '').slice(0, 10) || new Date().toISOString().slice(0, 10);
  RANGE_START = computeRangeStarts(BASIS_DATE);

  ARTICLES = newsData.articles.filter(function (a) { return !a.is_noise; });
  ARTICLES.forEach(function (a) { articleById[a.id] = a; });

  precomputeTagsBatched(ARTICLES, function () {
    hideStatusBanner();
    populateExploreStaticOptions();
    renderOverviewUpdatedTime();
    renderOverview();
    renderExplore();
    renderAIQuickPills();
    renderChatMessages();
  });
}

/* ------------------------------------------------------------------------
 * 7. 篩選管線（總覽與探索共用）
 * --------------------------------------------------------------------
 * 依序套用：is_noise（已於 ARTICLES 排除，此處略）-> range -> audience(role)
 * -> attention -> type -> country -> brands(OR) -> ptypes(OR) -> topics(OR)
 * -> query。opts.ignore 為陣列，可指定跳過某個維度（供未來計算 chip 筆數用）。
 * 'all' 一律代表「不套用該維度條件」，不解讀為任何標籤值本身。
 * ---------------------------------------------------------------------- */
function filterArticles(opts) {
  opts = opts || {};
  var ignore = opts.ignore || [];
  function skip(dim) { return ignore.indexOf(dim) !== -1; }
  var list = ARTICLES;

  if (!skip('range') && opts.range && RANGE_START[opts.range]) {
    var start = RANGE_START[opts.range];
    list = list.filter(function (a) { return a.date && a.date >= start && a.date <= BASIS_DATE; });
  }
  if (!skip('role') && opts.role && opts.role !== 'all') {
    list = list.filter(function (a) { return a._tags.audiences.indexOf(opts.role) !== -1; });
  }
  if (!skip('attention') && opts.attention && opts.attention !== 'all') {
    list = list.filter(function (a) { return a._tags.attention.indexOf(opts.attention) !== -1; });
  }
  if (!skip('type') && opts.type && opts.type !== 'all') {
    list = list.filter(function (a) { return a.category === opts.type; });
  }
  if (!skip('country') && opts.country && opts.country !== '全部') {
    list = list.filter(function (a) { return a._tags.country === opts.country; });
  }
  if (!skip('brands') && opts.brands && opts.brands.length) {
    list = list.filter(function (a) { return a._tags.brands.some(function (b) { return opts.brands.indexOf(b) !== -1; }); });
  }
  if (!skip('ptypes') && opts.ptypes && opts.ptypes.length) {
    list = list.filter(function (a) { return a._tags.ptypes.some(function (p) { return opts.ptypes.indexOf(p) !== -1; }); });
  }
  if (!skip('topics') && opts.topics && opts.topics.length) {
    list = list.filter(function (a) { return a._tags.topics.some(function (tp) { return opts.topics.indexOf(tp) !== -1; }); });
  }
  if (!skip('query') && opts.query) {
    var q = opts.query.toLowerCase();
    list = list.filter(function (a) {
      return (a.title || '').toLowerCase().indexOf(q) !== -1 ||
        (a.summary || '').toLowerCase().indexOf(q) !== -1 ||
        a._tags.brands.join(' ').toLowerCase().indexOf(q) !== -1;
    });
  }
  if (opts.excludeTopicOther) {
    list = list.filter(function (a) { return !(a._tags.topics.length === 1 && a._tags.topics[0] === '其他'); });
  }
  if (opts.excludeIds && opts.excludeIds.size) {
    list = list.filter(function (a) { return !opts.excludeIds.has(a.id); });
  }
  return list;
}

function byDateDesc(a, b) { return (b.date || '').localeCompare(a.date || ''); }

/* ------------------------------------------------------------------------
 * 8. 總覽頁資料聚合（KPI / Top5 / 三個情報區塊）
 * ---------------------------------------------------------------------- */
function getOverviewBase() {
  return filterArticles({ role: S.ovRole, attention: S.ovAttention, range: S.ovRange });
}

/**
 * 「總情報數」用的基準（主控修正指令）：套用除 time range 以外的所有篩選
 * （角色、關注領域...），但不套用時間範圍，藉此與「新增情報」（含 time range，
 * 即 getOverviewBase() 的結果）做出口徑區隔，避免兩個 KPI 永遠同值。
 * 不傳入 range 參數，filterArticles 自然不會套用該維度（見第 7 節管線）。
 */
function getOverviewTotalBase() {
  return filterArticles({ role: S.ovRole, attention: S.ovAttention });
}

/**
 * total：除 time range 外的所有篩選後總數（不含時間範圍）。
 * news：全部篩選（含 time range）後的總數，即 getOverviewBase() 的結果本身；
 *       不再對 base 二次套用「基準日 7 天內」，否則會與 total 在預設狀態
 *       （time range = 本週）下數值重複、語意為零。
 * brands / topics：維持現行「含 time range」口徑，來源仍為 base。
 */
function computeKPI(base, totalBase) {
  var brandSet = {}, topicSet = {};
  base.forEach(function (a) {
    a._tags.brands.forEach(function (b) { brandSet[b] = 1; });
    // 「其他」不是真的主題（只是沒命中任何主題的 fallback bucket），涵蓋主題
    // 一律排除，與熱門情報主題 Top5（第 11-2(3) 節）的排除規則一致。
    a._tags.topics.forEach(function (tp) { if (tp !== '其他') topicSet[tp] = 1; });
  });
  return {
    total: totalBase.length,
    news: base.length,
    brands: Object.keys(brandSet).length,
    topics: Object.keys(topicSet).length
  };
}

/** 熱門情報主題 Top5：一律排除「其他」（第 11-2(3) 節），分母為有明確 topic 的文章數。 */
function computeTopTopics(base) {
  var withTopic = base.filter(function (a) { return !(a._tags.topics.length === 1 && a._tags.topics[0] === '其他'); });
  var counts = {};
  withTopic.forEach(function (a) {
    a._tags.topics.forEach(function (tp) { if (tp !== '其他') counts[tp] = (counts[tp] || 0) + 1; });
  });
  var denom = withTopic.length || 1;
  return Object.keys(counts)
    .map(function (k) { return { key: k, count: counts[k], pct: counts[k] / denom * 100 }; })
    .sort(function (x, y) { return y.count - x.count; })
    .slice(0, 5);
}

function computeTopBrands(base) {
  var withBrand = base.filter(function (a) { return a._tags.brands.length > 0; });
  var counts = {};
  withBrand.forEach(function (a) {
    a._tags.brands.forEach(function (b) { counts[b] = (counts[b] || 0) + 1; });
  });
  var denom = withBrand.length || 1;
  return Object.keys(counts)
    .map(function (k) { return { key: k, count: counts[k], pct: counts[k] / denom * 100 }; })
    .sort(function (x, y) { return y.count - x.count; })
    .slice(0, 5);
}

/**
 * 品牌優先加權排序（主控修正指令）：單靠「依日期取最新 3 則」會挑到與健身器材
 * 產業無關、或機翻亂碼的低品質內容。三區塊改為：
 *   第一優先：有品牌命中（brands 非空）且有明確 topic
 *   第二優先：有明確 topic（brands 可為空）
 *   同一優先級內依日期新到舊排序
 * 呼叫時傳入的 list 已經是「有明確 topic」的候選池（見下方 pool），所以這裡
 * 只需再依 brands 是否非空分兩層即可涵蓋第一、第二優先序。
 */
function byBrandPriorityThenDate(a, b) {
  var aHasBrand = a._tags.brands.length > 0 ? 1 : 0;
  var bHasBrand = b._tags.brands.length > 0 ? 1 : 0;
  if (aHasBrand !== bHasBrand) return bHasBrand - aHasBrand;
  return byDateDesc(a, b);
}

/**
 * 焦點情報／競品與產品更新／軟體與 AI 功能動態：三區塊皆只收有明確 topic 的
 * 文章（第 11-2(2) 節），排序改為品牌優先加權（見上），且後面的區塊要排除
 * 前面已用過的 id，避免重複文章。
 * 焦點情報額外套用第三優先序：僅當前兩優先序不足 3 則時，才從「其他」
 * （無明確 topic）的候選池補足，避免版面開天窗；競品／軟體兩區塊不補「其他」，
 * 因為「其他」本身不具備各自區塊要求的 topic／attention 條件。
 */
function computeDashboardBlocks(base) {
  var pool = base.filter(function (a) { return !(a._tags.topics.length === 1 && a._tags.topics[0] === '其他'); });
  var otherPool = base.filter(function (a) { return a._tags.topics.length === 1 && a._tags.topics[0] === '其他'; });
  var used = {};
  function take(list, n) {
    var picked = list.filter(function (a) { return !used[a.id]; }).sort(byBrandPriorityThenDate).slice(0, n);
    picked.forEach(function (a) { used[a.id] = true; });
    return picked;
  }
  var focus = take(pool, 3);
  if (focus.length < 3) { focus = focus.concat(take(otherPool, 3 - focus.length)); }

  var competitorPool = pool.filter(function (a) { return a._tags.topics.some(function (tp) { return COMPETITOR_BLOCK_TOPICS.indexOf(tp) !== -1; }); });
  var competitor = take(competitorPool, 3);
  var softwarePool = pool.filter(function (a) { return a._tags.attention.indexOf('Digital') !== -1; });
  var software = take(softwarePool, 3);
  return { focus: focus, competitor: competitor, software: software };
}

/* ------------------------------------------------------------------------
 * 9. 情報卡（第 5-4 節 + 第 11-2(1) 節覆寫）：三處（焦點/競品/軟體）+ 探索頁
 *    共用同一個 render 函式。任何情況都不得顯示「其他」字樣。
 * ---------------------------------------------------------------------- */
/**
 * 卡片主 chip 的縮寫顯示（team-lead 補充指令）：topics 為 ['其他'] 時，第 1 個
 * chip 不能直接顯示 a.categoryName 全稱（太長會撐爆卡片版面），改用
 * tag.cat.<category> 的縮寫版（市場/競品/科技/財經/品牌，zh/en 走 i18n）。
 * 查無對應縮寫 key 時 fallback 回原始 categoryName，不留空。
 * 注意：探索情報頁 select#v3-sel-type「情報類型」下拉仍維持顯示完整
 * categoryName（見 populateExploreStaticOptions），兩處刻意不同、不得統一。
 */
function categoryChipLabel(a) {
  var key = 'tag.cat.' + a.category;
  return I18N_PAIRS[key] ? t(key) : (a.categoryName || '');
}

function getCardTags(a) {
  var topics = a._tags.topics;
  if (topics.length === 1 && topics[0] === '其他') {
    var ptypes = a._tags.ptypes;
    // Product Type 一律顯示英文原文（zh / en 模式皆同），不呼叫 labelOf('ptype', ...)，
    // 因為 TagMapV3 的中文對照表（PTYPE_LABEL_ZH）與設計稿的英文 chip 文案不符，
    // 且該對照表不得修改（見任務限制），故在渲染層直接使用原始英文 key。
    var chip2 = ptypes.length ? ptypes[0] : null;
    return { primary: categoryChipLabel(a), secondary: chip2 };
  }
  var primary = TagMapV3.labelOf('topic', topics[0], LANG);
  var secondary = topics[1] ? TagMapV3.labelOf('topic', topics[1], LANG) : null;
  return { primary: primary, secondary: secondary };
}

function renderCardHTML(a) {
  var tags = getCardTags(a);
  var title = v3DisplayField(a, 'title');
  var summary = v3DisplayField(a, 'summary');
  var safeLink = safeExternalUrl(a.resolved_url || a.url);
  var meta = fmtDate(a.date) + ' \u00b7 ' + t('card.sourcePrefix') + escapeHTML(a.source || '');
  var html = '<article class="v3-card" data-card-id="' + escapeHTML(String(a.id)) + '">';
  html += '<div class="v3-card-tags"><span class="v3-tag is-primary">' + escapeHTML(tags.primary) + '</span>';
  if (tags.secondary) html += '<span class="v3-tag">' + escapeHTML(tags.secondary) + '</span>';
  html += '</div>';
  html += '<h3 class="v3-card-title">' + escapeHTML(title) + '</h3>';
  html += '<p class="v3-card-meta mono">' + meta + '</p>';
  if (summary) html += '<p class="v3-card-desc">' + escapeHTML(summary) + '</p>';
  html += '<div class="v3-card-divider"></div>';
  html += '<div class="v3-card-actions">';
  var readIconSvg = '<svg class="v3-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"></path><polyline points="15 3 21 3 21 9"></polyline><line x1="10" y1="14" x2="21" y2="3"></line></svg>';
  html += safeLink
    ? '<a class="v3-btn-ghost" data-act="read" href="' + escapeHTML(safeLink) + '" target="_blank" rel="noopener">' + readIconSvg + escapeHTML(t('card.readOriginal')) + '</a>'
    : '<button type="button" class="v3-btn-ghost" disabled>' + readIconSvg + escapeHTML(t('card.readOriginal')) + '</button>';
  var isLoading = !!S.aiLoading[a.id];
  html += '<button type="button" class="v3-btn-ghost" data-act="ai" data-id="' + escapeHTML(String(a.id)) + '"' + (isLoading ? ' disabled' : '') + '>' +
    '<svg class="v3-icon-sparkle" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M12 2c0 4.2-1 6.2-4.2 7.2C11 10.2 12 12.2 12 16.4c0-4.2 1-6.2 4.2-7.2C13 8.2 12 6.2 12 2z"></path></svg>' +
    escapeHTML(isLoading ? t('card.aiLoading') : t('card.aiSummary')) + '</button>';
  html += '</div></article>';
  return html;
}

function renderCardGrid(containerEl, list) {
  if (!containerEl) return;
  if (!list.length) {
    containerEl.innerHTML = '<p class="v3-empty-hint">' + escapeHTML(t('common.emptyCards')) + '</p>';
    return;
  }
  containerEl.innerHTML = list.map(renderCardHTML).join('');
  v3TranslateArticles(list, function () {
    // 翻譯完成後只重繪這個容器內對應的卡片，不動整頁，成本可控。
    if (!containerEl.isConnected) return;
    containerEl.innerHTML = list.map(renderCardHTML).join('');
  });
}

/** 事件委派：card 讀取/AI 按鈕統一綁在容器上一次即可，經得起 innerHTML 重繪。 */
function bindCardGridEvents(containerEl) {
  if (!containerEl || containerEl._bound) return;
  containerEl._bound = true;
  containerEl.addEventListener('click', function (e) {
    var btn = e.target.closest('[data-act="ai"]');
    if (btn && !btn.disabled) { openSummaryModal(btn.getAttribute('data-id')); }
  });
}

/* ------------------------------------------------------------------------
 * 10. Header：導覽切換 / 使用者選單 / 語系切換 / AI 設定 / Esc 全域關閉
 * ---------------------------------------------------------------------- */
function switchView(view) {
  S.view = view;
  document.getElementById('v3-view-overview').hidden = (view !== 'overview');
  document.getElementById('v3-view-explore').hidden = (view !== 'explore');
  document.getElementById('v3-nav-overview').classList.toggle('is-active', view === 'overview');
  document.getElementById('v3-nav-explore').classList.toggle('is-active', view === 'explore');
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function renderOverviewUpdatedTime() {
  var el = document.getElementById('v3-ov-updated-value');
  if (!el || !newsData) return;
  var d = new Date(newsData.generated_at);
  if (isNaN(d.getTime())) { el.textContent = BASIS_DATE; return; }
  function pad(n) { return String(n).length < 2 ? '0' + n : String(n); }
  el.textContent = d.getFullYear() + '/' + pad(d.getMonth() + 1) + '/' + pad(d.getDate()) + ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes());
}

function closeUserMenu() {
  var menu = document.getElementById('v3-user-menu');
  var btn = document.getElementById('v3-user-btn');
  if (menu) menu.hidden = true;
  if (btn) btn.setAttribute('aria-expanded', 'false');
}
function toggleUserMenu() {
  var menu = document.getElementById('v3-user-menu');
  var btn = document.getElementById('v3-user-btn');
  if (!menu || !btn) return;
  var willOpen = menu.hidden;
  if (willOpen) { menu.hidden = false; btn.setAttribute('aria-expanded', 'true'); }
  else { closeUserMenu(); }
}

function showAIBackendToast() {
  fetch(AI_PROXY_ENDPOINT).then(function (r) { return r.json(); }).then(function (d) {
    showToast(t('ai.backendToast', {
      model: (d && d.model) ? d.model : t('ai.unknown'),
      state: (d && d.ready) ? t('ai.ready') : t('ai.notReady')
    }));
  }).catch(function () { showToast(t('ai.offline')); });
}

function showToast(msg) {
  var el = document.createElement('div');
  el.className = 'v3-toast';
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(function () { el.classList.add('is-out'); }, 2200);
  setTimeout(function () { el.remove(); }, 2600);
}

function bindHeaderControls() {
  var user = getCurrentUser();
  var avatarEl = document.getElementById('v3-user-avatar');
  var nameEl = document.getElementById('v3-user-name');
  if (avatarEl) avatarEl.textContent = user.initials;
  if (nameEl) nameEl.textContent = user.displayName;

  document.getElementById('v3-nav-overview').addEventListener('click', function () { switchView('overview'); });
  document.getElementById('v3-nav-explore').addEventListener('click', function () { switchView('explore'); });
  document.getElementById('v3-nav-ai').addEventListener('click', function () { openAIDrawer(); });

  document.getElementById('v3-user-btn').addEventListener('click', function (e) { e.stopPropagation(); toggleUserMenu(); });
  document.getElementById('v3-menu-lang').addEventListener('click', function () { setLanguage(LANG === 'en' ? 'zh-Hant' : 'en'); closeUserMenu(); });
  document.getElementById('v3-menu-ai').addEventListener('click', function () { showAIBackendToast(); closeUserMenu(); });

  document.addEventListener('click', function (e) {
    var menu = document.getElementById('v3-user-menu');
    var btn = document.getElementById('v3-user-btn');
    if (!menu || menu.hidden) return;
    if (!menu.contains(e.target) && e.target !== btn && !btn.contains(e.target)) closeUserMenu();
  });
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape') return;
    if (!document.getElementById('v3-modal').hidden) { closeSummaryModal(); return; }
    if (S.drawerOpen) { closeAIDrawer(); return; }
    var menu = document.getElementById('v3-user-menu');
    if (menu && !menu.hidden) closeUserMenu();
  });

  bindOverviewFilterBar();
  bindOverviewMoreButtons();
  document.getElementById('v3-refresh-btn').addEventListener('click', doRefresh);
  document.getElementById('v3-insight-gen').addEventListener('click', generateInsights);

  bindExploreControls();
}

/** 重新整理：只重新讀取同一份 news.json（不會觸發爬蟲），按鈕 title 屬性已說明此行為。 */
function doRefresh() {
  var btn = document.getElementById('v3-refresh-btn');
  if (btn) btn.disabled = true;
  fetch(DATA_URL, { cache: 'no-store' }).then(function (res) { return res.json(); }).then(function (json) {
    if (!json || !Array.isArray(json.articles)) throw new Error('bad json');
    newsData = json;
    BASIS_DATE = (newsData.generated_at || '').slice(0, 10) || BASIS_DATE;
    RANGE_START = computeRangeStarts(BASIS_DATE);
    ARTICLES = newsData.articles.filter(function (a) { return !a.is_noise; });
    articleById = {};
    ARTICLES.forEach(function (a) { articleById[a.id] = a; });
    precomputeTagsBatched(ARTICLES, function () {
      renderOverviewUpdatedTime();
      renderOverview();
      renderExplore();
      if (btn) btn.disabled = false;
      showToast(LANG === 'en' ? 'Data refreshed' : '資料已重新整理');
    });
  }).catch(function () {
    if (btn) btn.disabled = false;
    showToast(t('common.loadFail'));
  });
}

/* ------------------------------------------------------------------------
 * 11. 總覽頁篩選列（角色 / 關注領域 / 時間範圍，皆為單選 segmented）
 * ---------------------------------------------------------------------- */
function bindSegmented(containerId, onChange) {
  var el = document.getElementById(containerId);
  if (!el || el._bound) return;
  el._bound = true;
  el.addEventListener('click', function (e) {
    var btn = e.target.closest('button[data-value]');
    if (!btn) return;
    el.querySelectorAll('button').forEach(function (b) { b.classList.toggle('is-active', b === btn); });
    onChange(btn.getAttribute('data-value'));
  });
}

function bindOverviewFilterBar() {
  bindSegmented('v3-role-tabs', function (v) { S.ovRole = v; renderOverview(); });
  bindSegmented('v3-attention-tabs', function (v) { S.ovAttention = v; renderOverview(); });
  bindSegmented('v3-range-tabs', function (v) { S.ovRange = v; renderOverview(); });
}

function syncSegmentedUI(containerId, value) {
  var el = document.getElementById(containerId);
  if (!el) return;
  el.querySelectorAll('button[data-value]').forEach(function (b) {
    b.classList.toggle('is-active', b.getAttribute('data-value') === value);
  });
}

/** 「查看更多」：複製總覽篩選條件到探索頁，套用各區塊對應的額外條件後切換視圖。 */
function goToExplore(kind) {
  S.exRole = S.ovRole;
  S.exAttention = S.ovAttention;
  S.exRange = S.ovRange;
  S.exType = 'all'; S.exCountry = '全部';
  S.exBrands = []; S.exPtypes = [];
  S.query = ''; S.shown = 9;
  var qInput = document.getElementById('v3-search');
  if (qInput) qInput.value = '';
  if (kind === 'competitor') { S.exTopics = COMPETITOR_MORE_TOPICS.slice(); }
  else if (kind === 'software') { S.exAttention = 'Digital'; S.exTopics = []; }
  else { S.exTopics = []; }
  switchView('explore');
  renderExplore();
}

function bindOverviewMoreButtons() {
  document.getElementById('v3-more-focus').addEventListener('click', function () { goToExplore('focus'); });
  document.getElementById('v3-more-competitor').addEventListener('click', function () { goToExplore('competitor'); });
  document.getElementById('v3-more-software').addEventListener('click', function () { goToExplore('software'); });
}

/* ------------------------------------------------------------------------
 * 12. 總覽頁渲染
 * ---------------------------------------------------------------------- */
function renderKPI(kpi) {
  var el = document.getElementById('v3-kpi-grid');
  var items = [
    [t('ov.kpiTotal'), kpi.total], [t('ov.kpiNew'), kpi.news],
    [t('ov.kpiBrands'), kpi.brands], [t('ov.kpiTopics'), kpi.topics]
  ];
  el.innerHTML = items.map(function (it) {
    return '<div class="v3-kpi-card"><span class="v3-kpi-label">' + escapeHTML(it[0]) + '</span>' +
      '<span class="v3-kpi-value mono">' + fmtNum(it[1]) + '</span></div>';
  }).join('');
}

function renderTrendList(containerId, rows, labelKind) {
  var el = document.getElementById(containerId);
  if (!rows.length) { el.innerHTML = '<p class="v3-empty-hint">' + escapeHTML(t('common.emptyTrend')) + '</p>'; return; }
  el.innerHTML = rows.map(function (r, idx) {
    var name = labelKind ? TagMapV3.labelOf(labelKind, r.key, LANG) : r.key;
    return '<div class="v3-trend-row"><span class="v3-trend-rank mono">' + (idx + 1) + '</span>' +
      '<span class="v3-trend-name">' + escapeHTML(name) + '</span>' +
      '<span class="v3-trend-bar"><span class="v3-trend-bar-fill" style="width:' + clamp(r.pct, 0, 100).toFixed(1) + '%"></span></span>' +
      '<span class="v3-trend-value mono">' + fmtNum(r.count) + '\uff08' + r.pct.toFixed(1) + '%\uff09</span></div>';
  }).join('');
}

function renderOverview() {
  if (!ARTICLES.length) return;
  syncSegmentedUI('v3-role-tabs', S.ovRole);
  syncSegmentedUI('v3-attention-tabs', S.ovAttention);
  syncSegmentedUI('v3-range-tabs', S.ovRange);

  var base = getOverviewBase();
  var totalBase = getOverviewTotalBase();
  renderKPI(computeKPI(base, totalBase));
  renderTrendList('v3-trend-topics', computeTopTopics(base), 'topic');
  renderTrendList('v3-trend-brands', computeTopBrands(base), null);

  var blocks = computeDashboardBlocks(base);
  var focusEl = document.getElementById('v3-focus-cards');
  var competitorEl = document.getElementById('v3-competitor-cards');
  var softwareEl = document.getElementById('v3-software-cards');
  bindCardGridEvents(focusEl); bindCardGridEvents(competitorEl); bindCardGridEvents(softwareEl);
  renderCardGrid(focusEl, blocks.focus);
  renderCardGrid(competitorEl, blocks.competitor);
  renderCardGrid(softwareEl, blocks.software);

  renderInsightSection();
}

/* ------------------------------------------------------------------------
 * 13. 產品洞察區塊（第 7-2 節：按下才生成，生成後 cache，篩選變更只顯示提示）
 * ---------------------------------------------------------------------- */
function insightFilterKey() {
  return JSON.stringify({ role: S.ovRole, attention: S.ovAttention, range: S.ovRange, lang: LANG });
}

function renderInsightSection() {
  var cardsEl = document.getElementById('v3-insight-cards');
  var staleEl = document.getElementById('v3-insight-stale');
  var genBtn = document.getElementById('v3-insight-gen');
  var cache = S.insightCache;
  var hasItems = !!(cache && cache.items && cache.items.length);

  // 注意：#v3-insight-stale 是 #v3-insight-empty 的子節點（DOM 契約既有結構），
  // 因此 emptyEl 本身永遠保持可見，只切換內部的按鈕文案與 stale 提示，
  // 否則把整個 emptyEl 藏起來會連按鈕跟 stale 提示一起藏掉，使用者將永遠看不到
  // 「重新生成」按鈕與「條件已變更」提示。
  genBtn.querySelector('span').textContent = t(hasItems ? 'ov.insightRegen' : 'ov.insightGen');
  staleEl.hidden = !hasItems || cache.filterKey === insightFilterKey();

  if (!hasItems) { cardsEl.innerHTML = ''; return; }

  var badgeMap = { trend: t('insight.badgeTrend'), feature: t('insight.badgeFeature'), competitor: t('insight.badgeCompetitor') };
  cardsEl.innerHTML = cache.items.map(function (it) {
    var badgeText = badgeMap[it.badge] || it.badge || badgeMap.trend;
    var html = '<article class="v3-insight-card"><div class="v3-insight-top">' +
      '<span class="v3-insight-badge"><svg class="v3-icon-sparkle" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M12 2c0 4.2-1 6.2-4.2 7.2C11 10.2 12 12.2 12 16.4c0-4.2 1-6.2 4.2-7.2C13 8.2 12 6.2 12 2z"></path></svg>' +
      escapeHTML(badgeText) + '</span>' +
      '<span class="v3-insight-count mono">' + escapeHTML(t('insight.relatedCount', { n: it.relatedCount || 0 })) + '</span></div>';
    html += '<h3 class="v3-insight-title">' + escapeHTML(it.title || '') + '</h3>';
    html += '<div class="v3-insight-section"><span class="v3-insight-section-label">' + escapeHTML(t('insight.sectionMain')) + '</span><p class="v3-insight-section-body">' + escapeHTML(it.mainChange || '') + '</p></div>';
    html += '<div class="v3-insight-section"><span class="v3-insight-section-label">' + escapeHTML(t('insight.sectionImpact')) + '</span><p class="v3-insight-section-body">' + escapeHTML(it.impact || '') + '</p></div>';
    html += '<div class="v3-insight-section"><span class="v3-insight-section-label">' + escapeHTML(t('insight.sectionSuggest')) + '</span><p class="v3-insight-section-body">' + escapeHTML(it.suggestion || '') + '</p></div>';
    html += '<div class="v3-insight-divider"></div><span class="v3-insight-sources">' + escapeHTML(t('insight.sourcesLabel')) + '</span><div class="v3-insight-source-list">';
    (it.sources || []).slice(0, 3).forEach(function (s) {
      if (!s || !s.url) return;
      var safeSourceUrl = safeExternalUrl(s.url);
      var sourceLabel = escapeHTML(s.title || s.url);
      if (safeSourceUrl) {
        html += '<a class="v3-insight-source-link" href="' + escapeHTML(safeSourceUrl) + '" target="_blank" rel="noopener">' + sourceLabel +
          '<svg class="v3-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"></path><polyline points="15 3 21 3 21 9"></polyline><line x1="10" y1="14" x2="21" y2="3"></line></svg></a>';
      } else {
        html += '<span class="v3-insight-source-link">' + sourceLabel + '</span>';
      }
    });
    html += '</div></article>';
    return html;
  }).join('');
}

/* ------------------------------------------------------------------------
 * 14. 探索情報頁：靜態選項（情報類型 / 地區）於資料載入後才知道實際分類
 * ---------------------------------------------------------------------- */
var CATEGORY_PREFERRED_ORDER = ['market', 'competitor', 'tech', 'finance', 'brand'];

function populateExploreStaticOptions() {
  var typeSel = document.getElementById('v3-sel-type');
  var seen = {};
  var cats = [];
  ARTICLES.forEach(function (a) {
    if (!seen[a.category]) { seen[a.category] = true; cats.push({ code: a.category, name: a.categoryName }); }
  });
  cats.sort(function (x, y) { return CATEGORY_PREFERRED_ORDER.indexOf(x.code) - CATEGORY_PREFERRED_ORDER.indexOf(y.code); });
  cats.forEach(function (c) {
    var opt = document.createElement('option');
    opt.value = c.code; opt.textContent = c.name;
    typeSel.appendChild(opt);
  });

  var countrySel = document.getElementById('v3-sel-country');
  TagMapV3.COUNTRIES.slice(1).forEach(function (c) {
    var opt = document.createElement('option');
    opt.value = c; opt.textContent = TagMapV3.labelOf('country', c, LANG);
    opt.setAttribute('data-country-key', c);
    countrySel.appendChild(opt);
  });
}

/* ------------------------------------------------------------------------
 * 15. 探索情報頁：Brand / Product Type / Topic 三排多選 chips
 * --------------------------------------------------------------------
 * 同排多選採 OR，不同排之間採 AND；每排第一個「全部」與其他選項互斥
 * （選全部清空陣列；選其他項目時陣列非空，全部自然不再是 on）。
 * ---------------------------------------------------------------------- */
function renderChipRow(containerId, allValues, selected, kind, limitFirst) {
  var el = document.getElementById(containerId);
  var shown = limitFirst ? allValues.slice(0, limitFirst) : allValues;
  var allLabel = t('tag.' + kind + 'All');
  var html = '<button type="button" class="v3-chip' + (selected.length === 0 ? ' is-on' : '') + '" data-value="\u5168\u90e8">' + escapeHTML(allLabel) + '</button>';
  html += shown.map(function (v) {
    // Product Type 一律顯示英文原文（zh / en 模式皆同），不呼叫 labelOf('ptype', ...)，
    // 理由同 getCardTags() 上方註解。Brand / Topic 兩排不受影響，維持原 labelOf 行為。
    var label = (kind === 'ptype') ? v : TagMapV3.labelOf(kind, v, LANG);
    return '<button type="button" class="v3-chip' + (selected.indexOf(v) !== -1 ? ' is-on' : '') + '" data-value="' + escapeHTML(v) + '">' + escapeHTML(label) + '</button>';
  }).join('');
  el.innerHTML = html;
}

function bindChipRowEvents(containerId, stateKey) {
  var el = document.getElementById(containerId);
  if (!el || el._bound) return;
  el._bound = true;
  el.addEventListener('click', function (e) {
    var btn = e.target.closest('button[data-value]');
    if (!btn) return;
    var value = btn.getAttribute('data-value');
    if (value === '\u5168\u90e8') { S[stateKey] = []; }
    else {
      var idx = S[stateKey].indexOf(value);
      if (idx === -1) S[stateKey].push(value); else S[stateKey].splice(idx, 1);
    }
    S.shown = 9;
    renderExplore();
  });
}

/* ------------------------------------------------------------------------
 * 16. 探索情報頁：其餘控制項（搜尋 debounce / 下拉 / 更多 / 清除 / 載入更多）
 * ---------------------------------------------------------------------- */
var v3SearchDebounceTimer = null;

function bindExploreControls() {
  bindSegmented('v3-ex-range-tabs', function (v) { S.exRange = v; S.shown = 9; renderExplore(); });

  var searchEl = document.getElementById('v3-search');
  searchEl.addEventListener('input', function () {
    clearTimeout(v3SearchDebounceTimer);
    var val = searchEl.value;
    v3SearchDebounceTimer = setTimeout(function () { S.query = val; S.shown = 9; renderExplore(); }, 260);
  });

  document.getElementById('v3-sel-role').addEventListener('change', function (e) { S.exRole = e.target.value; S.shown = 9; renderExplore(); });
  document.getElementById('v3-sel-attention').addEventListener('change', function (e) { S.exAttention = e.target.value; S.shown = 9; renderExplore(); });
  document.getElementById('v3-sel-type').addEventListener('change', function (e) { S.exType = e.target.value; S.shown = 9; renderExplore(); });
  document.getElementById('v3-sel-country').addEventListener('change', function (e) { S.exCountry = e.target.value; S.shown = 9; renderExplore(); });

  bindChipRowEvents('v3-brand-chips', 'exBrands');
  bindChipRowEvents('v3-ptype-chips', 'exPtypes');
  bindChipRowEvents('v3-topic-chips', 'exTopics');

  document.getElementById('v3-brand-more').addEventListener('click', function () {
    S.brandChipsExpanded = !S.brandChipsExpanded;
    renderExplore();
  });

  document.getElementById('v3-clear-filters').addEventListener('click', function () {
    S.exRole = 'all'; S.exAttention = 'all'; S.exType = 'all'; S.exCountry = '\u5168\u90e8';
    S.exBrands = []; S.exPtypes = []; S.exTopics = []; S.query = ''; S.shown = 9;
    searchEl.value = '';
    renderExplore();
  });

  document.getElementById('v3-load-more').addEventListener('click', function () {
    S.shown += 9;
    renderExplore();
  });

  bindCardGridEvents(document.getElementById('v3-ex-cards'));
}

function refreshExploreSelectLabels() {
  document.getElementById('v3-sel-role').value = S.exRole;
  document.getElementById('v3-sel-attention').value = S.exAttention;
  document.getElementById('v3-sel-type').value = S.exType;
  document.getElementById('v3-sel-country').value = S.exCountry;
  document.querySelectorAll('#v3-sel-country option[data-country-key]').forEach(function (opt) {
    opt.textContent = TagMapV3.labelOf('country', opt.getAttribute('data-country-key'), LANG);
  });
}

function renderExplore() {
  if (!ARTICLES.length) return;
  syncSegmentedUI('v3-ex-range-tabs', S.exRange);
  refreshExploreSelectLabels();

  renderChipRow('v3-brand-chips', TagMapV3.BRANDS, S.exBrands, 'brand', S.brandChipsExpanded ? null : 8);
  renderChipRow('v3-ptype-chips', TagMapV3.PRODUCT_TYPES, S.exPtypes, 'ptype', null);
  renderChipRow('v3-topic-chips', TagMapV3.TOPICS, S.exTopics, 'topic', null); // 第 11-2(2) 節：探索頁 Topic chips 需保留「其他」供主動篩選

  var moreBtn = document.getElementById('v3-brand-more');
  moreBtn.textContent = t(S.brandChipsExpanded ? 'ex.brandLess' : 'ex.brandMore');

  var list = filterArticles({
    role: S.exRole, attention: S.exAttention, range: S.exRange, type: S.exType,
    country: S.exCountry, brands: S.exBrands, ptypes: S.exPtypes, topics: S.exTopics,
    query: S.query
  });

  var countEl = document.getElementById('v3-ex-count');
  countEl.innerHTML = ' ' + fmtNum(list.length) + ' <span data-i18n="ex.countSuffix">' + escapeHTML(t('ex.countSuffix')) + '</span>';

  var visible = list.slice(0, S.shown);
  var cardsEl = document.getElementById('v3-ex-cards');
  var emptyEl = document.getElementById('v3-ex-empty');
  var loadMoreBtn = document.getElementById('v3-load-more');

  if (!list.length) {
    cardsEl.innerHTML = '';
    emptyEl.hidden = false;
    loadMoreBtn.hidden = true;
  } else {
    emptyEl.hidden = true;
    renderCardGrid(cardsEl, visible);
    var remaining = list.length - visible.length;
    if (remaining > 0) {
      loadMoreBtn.hidden = false;
      document.getElementById('v3-load-more-count').textContent = '\uff08' + fmtNum(remaining) + ' \u5247\uff09';
    } else {
      loadMoreBtn.hidden = true;
    }
  }
}

/* ------------------------------------------------------------------------
 * 17. 標題／摘要即時中文化（移植自 assets/app-v2.js:1025-1120 的
 *    v2TranslateTextToZh / v2DisplayField / v2TranslateArticles，改 v3 前綴，
 *    保留其快取與失敗降級邏輯：失敗一律顯示原文，不顯示錯誤或空白）。
 * --------------------------------------------------------------------
 * 快取另外持久化到 localStorage（jht-v3-title-tr），避免重新整理頁面後，
 * 同一批文章需要重新打一次外部翻譯 API。
 * ---------------------------------------------------------------------- */
var V3_TRANSLATE_CACHE = new Map();
(function loadPersistedTranslations() {
  var saved = loadLocalJSON(TITLE_TR_KEY, null);
  if (saved && typeof saved === 'object') {
    Object.keys(saved).forEach(function (k) { V3_TRANSLATE_CACHE.set(k, saved[k]); });
  }
})();
var v3TranslatePersistTimer = null;
function v3SchedulePersistTranslations() {
  clearTimeout(v3TranslatePersistTimer);
  v3TranslatePersistTimer = setTimeout(function () {
    var obj = {};
    V3_TRANSLATE_CACHE.forEach(function (v, k) { if (typeof v === 'string') obj[k] = v; });
    saveLocal(TITLE_TR_KEY, obj);
  }, 800);
}

var V3_TRANSLATE_QUEUE = [];
var v3TranslateActive = 0;
var V3_TRANSLATE_CONCURRENCY = 4;

async function v3TranslateTextToZh(text) {
  try {
    var res = await fetch('https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=zh-TW&dt=t&q=' + encodeURIComponent(text));
    if (res.ok) {
      var data = await res.json();
      if (Array.isArray(data) && Array.isArray(data[0]) && data[0].length) {
        var joined = data[0].map(function (seg) { return (Array.isArray(seg) && seg[0]) ? seg[0] : ''; }).join('');
        if (joined) return joined;
      }
    }
  } catch (e) { /* 忽略，改走備援端點 */ }
  try {
    var res2 = await fetch('https://api.mymemory.translated.net/get?q=' + encodeURIComponent(text) + '&langpair=en|zh-TW');
    if (res2.ok) {
      var data2 = await res2.json();
      var translated = data2 && data2.responseData && data2.responseData.translatedText;
      if (translated) return translated;
    }
  } catch (e) { /* 忽略，fallback 顯示原文 */ }
  return text;
}

function v3ScheduleTranslate(text) {
  if (!text) return Promise.resolve(text);
  var existing = V3_TRANSLATE_CACHE.get(text);
  if (typeof existing === 'string') return Promise.resolve(existing);
  if (existing instanceof Promise) return existing;
  var promise = new Promise(function (resolve) {
    V3_TRANSLATE_QUEUE.push({ text: text, resolve: resolve });
    v3PumpTranslateQueue();
  });
  V3_TRANSLATE_CACHE.set(text, promise);
  return promise;
}
function v3PumpTranslateQueue() {
  while (v3TranslateActive < V3_TRANSLATE_CONCURRENCY && V3_TRANSLATE_QUEUE.length) {
    var job = V3_TRANSLATE_QUEUE.shift();
    v3TranslateActive++;
    v3TranslateTextToZh(job.text).then(function (result) {
      var finalText = result || job.text;
      V3_TRANSLATE_CACHE.set(job.text, finalText);
      v3SchedulePersistTranslations();
      job.resolve(finalText);
    }).catch(function () {
      V3_TRANSLATE_CACHE.set(job.text, job.text);
      job.resolve(job.text);
    }).finally(function () {
      v3TranslateActive--;
      v3PumpTranslateQueue();
    });
  }
}

/**
 * 取得文章顯示用標題／摘要。中文模式：翻譯快取有結果就用，否則先顯示原文；
 * 英文模式：直接顯示新聞來源原文（資料內容不翻譯）。
 */
function v3DisplayField(art, field) {
  // summary 欄位一律先過 usableSummary() 防禦（第 12-2 節），污染摘要視為無值。
  var original = (field === 'summary') ? (usableSummary(art) || '') : (art[field] || '');
  if (LANG === 'en') return original;
  if (!original) return '';
  var cached = V3_TRANSLATE_CACHE.get(original);
  return (typeof cached === 'string') ? cached : original;
}

/** 對一批文章排入標題/摘要翻譯佇列，完成後呼叫 onUpdated()（英文模式不翻譯）。 */
function v3TranslateArticles(list, onUpdated) {
  if (LANG === 'en') return;
  (list || []).forEach(function (art) {
    ['title', 'summary'].forEach(function (field) {
      // summary 污染文字（第 12-2 節）不得送去翻譯，避免浪費翻譯 API 額度。
      var original = (field === 'summary') ? (usableSummary(art) || '') : (art[field] || '');
      if (!original) return;
      var cached = V3_TRANSLATE_CACHE.get(original);
      if (cached !== undefined) return;
      v3ScheduleTranslate(original).then(function () { if (onUpdated) onUpdated(art); });
    });
  });
}

/** 目前畫面上實際可見的卡片文章（供切換語言時決定要即時翻譯哪些文章）。 */
function v3CurrentlyVisibleArticles() {
  var ids = {};
  var out = [];
  document.querySelectorAll('[data-card-id]').forEach(function (el) {
    var id = el.getAttribute('data-card-id');
    if (ids[id] || !articleById[id]) return;
    ids[id] = true;
    out.push(articleById[id]);
  });
  return out;
}

/* ------------------------------------------------------------------------
 * 18. AI 代理呼叫（移植自 assets/app-v2.js 第 7 節 v2CallAI/v2StreamAI 等，
 *    改 v3 前綴。後端沿用既有 api/ai.js，不修改該檔、不自創新路徑或參數）。
 * ---------------------------------------------------------------------- */
async function v3CallAI(promptText, systemInstructionText) {
  var delay = 1000;
  for (var attempt = 0; attempt < 5; attempt++) {
    try {
      var response = await fetch(AI_PROXY_ENDPOINT, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt: promptText, systemInstruction: systemInstructionText || '', stream: false })
      });
      var data = await response.json().catch(function () { return {}; });
      if (data && data.model) v3AIModelLabel = data.model;
      if (!response.ok) {
        var err = new Error('AI \u5f8c\u7aef\u932f\u8aa4\u78bc: ' + response.status + (data && data.error ? '\uff0c\u8a0a\u606f\uff1a' + data.error : ''));
        if (data && data.missingCredentials) err.isMissingKey = true;
        throw err;
      }
      if (data && data.text) return data.text;
      throw new Error('AI \u5f8c\u7aef\u56de\u50b3\u4e2d\u7121\u751f\u6210\u4e4b\u6587\u5b57\u5167\u5bb9\u3002');
    } catch (error) {
      if (error && error.isMissingKey) throw error;
      if (attempt === 4) throw error;
      await new Promise(function (resolve) { setTimeout(resolve, delay); });
      delay *= 2;
    }
  }
}

async function v3StreamAI(promptText, systemInstructionText, onDelta, signal) {
  var response = await fetch(AI_PROXY_ENDPOINT, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ prompt: promptText, systemInstruction: systemInstructionText || '', stream: true }),
    signal: signal
  });
  if (!response.ok) {
    var detail = '', missingKey = false;
    try { var j = await response.json(); detail = (j && j.error) || ''; missingKey = !!(j && j.missingCredentials); } catch (e) { /* noop */ }
    var err = new Error('AI backend error: ' + response.status + (detail ? ', ' + detail : ''));
    err.isMissingKey = missingKey;
    throw err;
  }
  if (!response.body || typeof response.body.getReader !== 'function') {
    var errU = new Error('stream unsupported'); errU.isStreamUnsupported = true; throw errU;
  }
  var reader = response.body.getReader();
  var decoder = new TextDecoder('utf-8');
  var buffer = '', fullText = '';
  while (true) {
    var chunk = await reader.read();
    if (chunk.done) break;
    buffer += decoder.decode(chunk.value, { stream: true });
    var lines = buffer.split('\n');
    buffer = lines.pop();
    for (var i = 0; i < lines.length; i++) {
      var line = lines[i].trim();
      if (!line || line.indexOf('data:') !== 0) continue;
      var dataStr = line.slice(5).trim();
      if (!dataStr) continue;
      var json;
      try { json = JSON.parse(dataStr); } catch (e) { continue; }
      if (json.model) v3AIModelLabel = json.model;
      if (json.error) throw new Error(json.error);
      if (typeof json.delta === 'string' && json.delta) {
        fullText += json.delta;
        if (typeof onDelta === 'function') onDelta(fullText, json.delta);
      }
      if (json.done) return json.text || fullText;
    }
  }
  return fullText;
}

function v3Typewriter(fullText, onUpdate, signal) {
  return new Promise(function (resolve) {
    var text = fullText || '';
    var chunkSize = Math.max(2, Math.ceil(text.length / 120));
    var idx = 0;
    var timer = setInterval(function () {
      if (signal && signal.aborted) { clearInterval(timer); resolve(); return; }
      idx = Math.min(text.length, idx + chunkSize);
      if (typeof onUpdate === 'function') onUpdate(text.slice(0, idx));
      if (idx >= text.length) { clearInterval(timer); resolve(); }
    }, 16);
  });
}

async function v3RunAI(promptText, sysInstruction, onUpdate, signal) {
  try {
    return await v3StreamAI(promptText, sysInstruction, onUpdate, signal);
  } catch (err) {
    if (err && err.name === 'AbortError') throw err;
    if (err && err.isMissingKey) throw err;
    if (err && err.isStreamUnsupported) {
      var fullText = await v3CallAI(promptText, sysInstruction);
      await v3Typewriter(fullText, onUpdate, signal);
      return fullText;
    }
    throw err;
  }
}

function v3RenderMarkdown(container, text, opts) {
  if (!container) return;
  opts = opts || {};
  var raw = text || '';
  var html;
  try {
    if (window.marked && window.DOMPurify) {
      var rawHtml = (typeof window.marked.parse === 'function') ? window.marked.parse(raw) : window.marked(raw);
      html = window.DOMPurify.sanitize(rawHtml);
    } else {
      html = escapeHTML(raw).split('\n').join('<br>');
    }
  } catch (e) { html = escapeHTML(raw).split('\n').join('<br>'); }
  if (opts.showCursor) html += '<span class="v3-ai-cursor"></span>';
  container.innerHTML = html;
}
if (window.marked && typeof window.marked.setOptions === 'function') window.marked.setOptions({ gfm: true, breaks: true });

function v3IsNearBottom(el, threshold) {
  if (!el) return true;
  return (el.scrollHeight - el.scrollTop - el.clientHeight) <= (threshold || 64);
}
function v3ScrollToBottomIfNear(el) { if (el && v3IsNearBottom(el)) el.scrollTop = el.scrollHeight; }

function v3CreateStreamRenderer(contentEl, scrollEl) {
  var pendingText = null, pendingCursor = false, rafId = null;
  function flush() {
    rafId = null;
    if (pendingText === null) return;
    var text = pendingText, showCursor = pendingCursor;
    pendingText = null;
    var shouldStick = v3IsNearBottom(scrollEl);
    v3RenderMarkdown(contentEl, text, { showCursor: showCursor });
    if (shouldStick) v3ScrollToBottomIfNear(scrollEl);
  }
  function update(text, showCursor) {
    pendingText = text; pendingCursor = !!showCursor;
    if (rafId === null) rafId = requestAnimationFrame(flush);
  }
  function cancelPending() { if (rafId !== null) { cancelAnimationFrame(rafId); rafId = null; } pendingText = null; }
  return { update: update, cancelPending: cancelPending };
}

function v3RenderAIErrorHTML(err) {
  if (err && err.isMissingKey) {
    return '<div class="v3-ai-error">' + escapeHTML(t('ai.errMissing')) + '</div>';
  }
  return '<div class="v3-ai-error">' + escapeHTML(t('ai.errGeneric', { msg: (err && err.message ? err.message : String(err)) })) + '</div>';
}

/* ------------------------------------------------------------------------
 * 19. 卡片 AI 一鍵摘要 + AI 摘要 Modal（第 5-7 節 DOM 契約 + 第 7-1 節規範）
 * ---------------------------------------------------------------------- */
var v3SummaryModalArticleId = null;
var v3SummaryAbortController = null;

function bindSummaryModal() {
  var overlay = document.getElementById('v3-modal-overlay');
  var modal = document.getElementById('v3-modal');
  overlay.addEventListener('click', closeSummaryModal);
  document.getElementById('v3-modal-close').addEventListener('click', closeSummaryModal);
  document.getElementById('v3-modal-copy').addEventListener('click', function () {
    var box = document.getElementById('v3-modal-content');
    v3CopyText(box ? box.innerText : '', function () { showToast(t('modal.copied')); });
  });
}

function v3CopyText(text, cb) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(cb).catch(function () { v3FallbackCopy(text, cb); });
  } else {
    v3FallbackCopy(text, cb);
  }
}
function v3FallbackCopy(text, cb) {
  var ta = document.createElement('textarea');
  ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
  document.body.appendChild(ta);
  ta.select();
  try { document.execCommand('copy'); } catch (e) { /* 忽略 */ }
  ta.remove();
  if (cb) cb();
}

/** body 捲動鎖定計數器：AI 抽屜與 AI 摘要 Modal 皆可能觸發，用計數器避免互相覆蓋。 */
var v3ScrollLockCount = 0;
function v3LockScroll() {
  if (v3ScrollLockCount === 0) {
    var scrollbarWidth = window.innerWidth - document.documentElement.clientWidth;
    document.body.classList.add('v3-no-scroll');
    if (scrollbarWidth > 0) document.body.style.paddingRight = scrollbarWidth + 'px';
  }
  v3ScrollLockCount++;
}
function v3UnlockScroll() {
  v3ScrollLockCount = Math.max(0, v3ScrollLockCount - 1);
  if (v3ScrollLockCount === 0) {
    document.body.classList.remove('v3-no-scroll');
    document.body.style.paddingRight = '';
  }
}

function openSummaryModal(id) {
  var a = articleById[id];
  if (!a) return;
  v3SummaryModalArticleId = id;

  document.getElementById('v3-modal-art-title').textContent = v3DisplayField(a, 'title');
  document.getElementById('v3-modal-art-meta').textContent = [fmtDate(a.date), a.source || ''].filter(Boolean).join(' \u00b7 ');
  var linkEl = document.getElementById('v3-modal-art-link');
  var modalSafeUrl = safeExternalUrl(a.resolved_url || a.url);
  linkEl.href = modalSafeUrl || '#';
  linkEl.hidden = !modalSafeUrl;
  document.getElementById('v3-modal-model').textContent = v3AIModelLabel || '-';

  document.getElementById('v3-modal-overlay').hidden = false;
  document.getElementById('v3-modal').hidden = false;
  v3LockScroll();

  var contentEl = document.getElementById('v3-modal-content');
  var cached = S.aiCache[id];
  if (cached) { v3RenderMarkdown(contentEl, cached); return; }
  generateSummaryInModal(id);
}

function closeSummaryModal() {
  if (document.getElementById('v3-modal').hidden) return;
  if (v3SummaryAbortController) { v3SummaryAbortController.abort(); v3SummaryAbortController = null; }
  document.getElementById('v3-modal-overlay').hidden = true;
  document.getElementById('v3-modal').hidden = true;
  v3UnlockScroll();
  v3SummaryModalArticleId = null;
}

/** 卡片上「AI一鍵摘要」按鈕的 loading 視覺狀態，直接操作 DOM，不必整個 grid 重繪。 */
function setCardAIButtonLoading(id, loading) {
  S.aiLoading[id] = loading;
  document.querySelectorAll('[data-act="ai"][data-id="' + id + '"]').forEach(function (btn) {
    btn.disabled = loading;
    var label = btn.lastChild;
    if (label && label.nodeType === 3) label.textContent = loading ? t('card.aiLoading') : t('card.aiSummary');
  });
}

/** 即時呼叫 /api/ai 針對單篇真實文章生成深度摘要，串流輸出到彈窗內；點擊才呼叫、成功後 cache。 */
async function generateSummaryInModal(id) {
  var a = articleById[id];
  var contentEl = document.getElementById('v3-modal-content');
  if (!a || !contentEl) return;
  if (v3SummaryAbortController) v3SummaryAbortController.abort();
  v3SummaryAbortController = new AbortController();
  var signal = v3SummaryAbortController.signal;
  setCardAIButtonLoading(id, true);

  contentEl.innerHTML = '<span class="v3-ai-loading-text">' + escapeHTML(t('modal.loading')) + '</span>';
  var scrollEl = document.getElementById('v3-modal-content');
  var renderer = v3CreateStreamRenderer(contentEl, scrollEl);
  var isEn = LANG === 'en';
  var tags = a._tags || computeArticleTags(a);
  var facts = 'Title: ' + a.title +
    '\nCategory: ' + (a.categoryName || a.category || '') +
    '\nDate: ' + (a.date || '') + '\nSource: ' + (a.source || '') +
    '\nBrands: ' + (tags.brands.length ? tags.brands.join('\u3001') : 'n/a') +
    '\nTopics: ' + (tags.topics.length ? tags.topics.join('\u3001') : 'n/a') +
    '\nSummary: ' + (usableSummary(a) || '') + '\nURL: ' + (a.resolved_url || a.url || 'n/a'); // 污染摘要（第 12-2 節）不得送給 AI，避免污染判讀並浪費 token
  var sysInstruction = isEn
    ? 'You are a senior AI industry advisor specialising in the global fitness equipment industry, connected smart fitness hardware and commercial/home fitness markets, advising Johnson Health Tech.'
    : '\u4f60\u662f\u4e00\u4f4d\u5c08\u7cbe\u65bc\u5168\u7403\u5065\u8eab\u7522\u696d\u3001\u667a\u6167\u5065\u8eab\u786c\u9ad4\u7814\u767c\u3001\u5168\u7403\u5546\u7528\u8207\u5bb6\u7528\u5065\u8eab\u5668\u6750\u5e02\u5834\u7684 AI \u8cc7\u6df1\u7522\u696d\u9867\u554f\u66a8\u6230\u7565\u6c7a\u7b56\u5c08\u5bb6\uff0c\u670d\u52d9對\u8c61\u70ba\u55ac\u5c71\u5065\u5eb7\u79d1\u6280\uff08Johnson Health Tech\uff09\u3002';
  var prompt = (isEn
    ? 'Analyse the following fitness-technology news for Johnson Health Tech executives, R&D managers and market analysts.\n\n'
    : '\u8acb\u91dd對\u4ee5\u4e0b\u5065\u8eab\u79d1\u6280\u7522\u696d\u60c5\u5831\uff0c\u70ba\u55ac\u5c71\u5065\u5eb7\u79d1\u6280\u7684\u9ad8\u5c64\u4e3b\u7ba1\u3001\u7814\u767c\u7d93\u7406\u53ca\u5e02\u5834\u5206\u6790\u5e2b\uff0c\u9032\u884c\u300c\u6df1\u5ea6\u6838\u5fc3\u6458\u8981\u300d\u8207\u300c\u6c7a\u7b56\u50f9\u503c\u8a55\u4f30\u300d\u3002\n\n') +
    facts +
    (isEn
      ? '\n\nUse this Markdown structure:\n## Core takeaway\n(one sentence, max 30 words)\n## Competitive impact\n## R&D recommendation\n## Market opportunity'
      : '\n\n\u8acb\u4f9d\u4e0b\u5217 Markdown \u67b6\u69cb\u4ee5\u7e41\u9ad4\u4e2d\u6587\u8f38\u51fa\uff1a\n## \u6838\u5fc3\u7cbe\u83ef\u63d0煉\n\uff08\u4e00\u8a00\u4ee5蔽\u4e4b\uff0c30 \u5b57\u4ee5內\uff09\n## \u7af6\u722d\u885d\u64ca\u8207\u5a01\u8105\n## \u7814\u767c\u5275\u65b0\u5efa\u8b70\n## \u5e02\u5834\u5546\u6a5f\u8a55\u4f30');

  try {
    var finalText = await v3RunAI(prompt, sysInstruction, function (partial) { renderer.update(partial, true); }, signal);
    renderer.cancelPending();
    S.aiCache[id] = finalText || t('chat.noContent');
    persistAICache();
    v3RenderMarkdown(contentEl, S.aiCache[id]);
    document.getElementById('v3-modal-model').textContent = v3AIModelLabel || '-';
  } catch (err) {
    renderer.cancelPending();
    if (err && err.name === 'AbortError') return;
    contentEl.innerHTML = v3RenderAIErrorHTML(err);
  } finally {
    setCardAIButtonLoading(id, false);
  }
}

/* ------------------------------------------------------------------------
 * 20. 產品洞察生成（第 7-2 節）：按下才呼叫，一次生成 3 則，要求 AI 回傳
 *    JSON，解析失敗要 try/catch 降級顯示錯誤提示，不崩頁。
 * ---------------------------------------------------------------------- */
function buildInsightContextArticles() {
  var base = getOverviewBase();
  var recent = base.filter(function (a) { return a.date && a.date >= RANGE_START.month && a.date <= BASIS_DATE; });
  var pool = recent.length ? recent : base;
  return pool.slice().sort(byDateDesc).slice(0, 40).map(function (a) {
    return { id: a.id, title: a.title, source: a.source, date: a.date, url: a.resolved_url || a.url || '' };
  });
}

function extractJSONArray(text) {
  try { return JSON.parse(text); } catch (e) { /* 繼續嘗試從文字中擷取 JSON 片段 */ }
  var start = text.indexOf('[');
  var end = text.lastIndexOf(']');
  if (start === -1 || end === -1 || end <= start) return null;
  try { return JSON.parse(text.slice(start, end + 1)); } catch (e2) { return null; }
}

async function generateInsights() {
  if (S.insightLoading) return;
  var contextArticles = buildInsightContextArticles();
  var genBtn = document.getElementById('v3-insight-gen');
  S.insightLoading = true;
  genBtn.disabled = true;
  genBtn.querySelector('span').textContent = t('ov.insightLoading');

  var isEn = LANG === 'en';
  var sysInstruction = isEn
    ? 'You are a senior product strategy analyst for Johnson Health Tech, covering the global fitness equipment industry. Always answer with valid JSON only, no prose outside the JSON.'
    : '你是喬山健康科技的資深產品策略分析師，專注於全球健身器材產業。請務必只回傳合法 JSON，JSON 之外不要有任何文字。';
  var schemaNote = isEn
    ? 'Return a JSON array of exactly 3 objects, each with: badge ("trend" | "feature" | "competitor"), title, mainChange, impact, suggestion, relatedCount (integer), sources (array of up to 3 {title, url} picked from the items below).'
    : '請回傳一個剛好 3 個物件的 JSON 陣列，每個物件包含：badge（"trend" 或 "feature" 或 "competitor"）、title、mainChange、impact、suggestion、relatedCount（整數）、sources（從下方情報中挑最多 3 則 {title, url}）。';
  var prompt = (isEn ? 'Recent fitness-industry intelligence items (title/source/date/url):\n' : '近期健身產業情報（title/source/date/url）：\n') +
    JSON.stringify(contextArticles) + '\n\n' + schemaNote;

  try {
    var text = await v3CallAI(prompt, sysInstruction);
    var items = extractJSONArray(text);
    if (!Array.isArray(items) || !items.length) throw new Error('AI 回傳非預期的 JSON 結構');
    S.insightCache = { generatedAt: Date.now(), filterKey: insightFilterKey(), items: items.slice(0, 3) };
    persistInsightCache();
  } catch (err) {
    console.warn('[v3] generateInsights failed:', err);
    showToast(t('ov.insightError'));
  } finally {
    S.insightLoading = false;
    genBtn.disabled = false;
    renderInsightSection();
  }
}

/* ------------------------------------------------------------------------
 * 21. AI 助理抽屜（第 5-6 節）：#v3-nav-ai 開啟；close 按鈕／overlay／Esc 關閉。
 * ---------------------------------------------------------------------- */
function openAIDrawer() {
  if (S.drawerOpen) return;
  S.drawerOpen = true;
  document.getElementById('v3-ai-overlay').classList.add('is-open');
  document.getElementById('v3-ai-drawer').classList.add('is-open');
  v3LockScroll();
  var input = document.getElementById('v3-ai-input');
  setTimeout(function () { if (input) input.focus(); }, 260);
}
function closeAIDrawer() {
  if (!S.drawerOpen) return;
  S.drawerOpen = false;
  document.getElementById('v3-ai-overlay').classList.remove('is-open');
  document.getElementById('v3-ai-drawer').classList.remove('is-open');
  v3UnlockScroll();
}

function bindAIDrawer() {
  document.getElementById('v3-ai-overlay').addEventListener('click', closeAIDrawer);
  document.getElementById('v3-ai-close').addEventListener('click', closeAIDrawer);
  var input = document.getElementById('v3-ai-input');
  var sendBtn = document.getElementById('v3-ai-send');
  sendBtn.addEventListener('click', function () { if (input.value.trim()) sendChat(input.value.trim()); });
  input.addEventListener('keydown', function (e) { if (e.key === 'Enter' && input.value.trim()) sendChat(input.value.trim()); });
}

function renderAIQuickPills() {
  var el = document.getElementById('v3-ai-quick');
  if (!el) return;
  var keys = ['ai.quick1', 'ai.quick2', 'ai.quick3'];
  el.innerHTML = keys.map(function (k) {
    var q = t(k);
    return '<button type="button" class="v3-ai-quick-btn" data-q="' + escapeHTML(q) + '">' + escapeHTML(q) + '</button>';
  }).join('');
  el.querySelectorAll('[data-q]').forEach(function (btn) {
    btn.addEventListener('click', function () { sendChat(btn.getAttribute('data-q')); });
  });
}

/* ------------------------------------------------------------------------
 * 22. AI 助理對話（第 7-3 節）：送出才呼叫，快捷 pill 等同填入問題並直接送出。
 * ---------------------------------------------------------------------- */
var v3ChatAbortController = null;

function renderChatMessages() {
  var el = document.getElementById('v3-ai-messages');
  if (!el) return;
  var welcome = '<div class="v3-ai-msg v3-ai-msg-bot"><span class="v3-ai-msg-avatar" aria-hidden="true">' +
    '<svg viewBox="0 0 24 24" focusable="false"><path d="M12 2c0 4.2-1 6.2-4.2 7.2C11 10.2 12 12.2 12 16.4c0-4.2 1-6.2 4.2-7.2C13 8.2 12 6.2 12 2z"></path></svg></span>' +
    '<div class="v3-ai-msg-bubble">' + escapeHTML(t('ai.welcome')) + '</div></div>';
  var rest = S.chat.map(function (m, idx) {
    if (m.who === 'me') {
      return '<div class="v3-ai-msg v3-ai-msg-user"><div class="v3-ai-msg-bubble">' + escapeHTML(m.text) + '</div></div>';
    }
    return '<div class="v3-ai-msg v3-ai-msg-bot"><span class="v3-ai-msg-avatar" aria-hidden="true">' +
      '<svg viewBox="0 0 24 24" focusable="false"><path d="M12 2c0 4.2-1 6.2-4.2 7.2C11 10.2 12 12.2 12 16.4c0-4.2 1-6.2 4.2-7.2C13 8.2 12 6.2 12 2z"></path></svg></span>' +
      '<div class="v3-ai-msg-bubble" id="v3-ai-bubble-' + idx + '"></div></div>';
  }).join('');
  if (S.chatThinking) {
    rest += '<div class="v3-ai-msg v3-ai-msg-bot"><div class="v3-ai-msg-bubble">' + escapeHTML(t('chat.thinking')) + '</div></div>';
  }
  el.innerHTML = welcome + rest;
  S.chat.forEach(function (m, idx) {
    var bubble = document.getElementById('v3-ai-bubble-' + idx);
    if (bubble && m.who !== 'me') v3RenderMarkdown(bubble, m.text);
  });
  el.scrollTop = el.scrollHeight;
}

async function sendChat(text) {
  if (v3ChatAbortController) v3ChatAbortController.abort();
  v3ChatAbortController = new AbortController();
  var signal = v3ChatAbortController.signal;
  S.chat.push({ who: 'me', text: text });
  var input = document.getElementById('v3-ai-input');
  if (input) input.value = '';
  S.chatThinking = true;
  renderChatMessages();

  var contextArticles = ARTICLES.slice(0, 30).map(function (a) {
    return { title: a.title, source: a.source, date: a.date, brands: a._tags.brands, topics: a._tags.topics };
  });
  var isEn = LANG === 'en';
  var sysInstruction = isEn
    ? 'You are a highly experienced fitness-technology industry advisor serving the leadership of Johnson Health Tech. Answer in clear, structured English.'
    : '你是一位極度資深的健身科技產業顧問，專為喬山健康科技（Johnson Health Tech）的決策層服務。請使用專業、條理清晰的繁體中文回答。';
  var prompt = (isEn ? 'Sample of currently loaded intelligence (excerpt):\n' : '當前已載入之情報樣本（節錄）：\n') +
    JSON.stringify(contextArticles) +
    (isEn ? '\n\nUser question:\n"' : '\n\n使用者諮詢問題：\n"') + text +
    (isEn ? '"\n\nCite the items above where relevant and give concrete recommendations.' : '"\n\n請適時引用上方情報中的真實動態並提出具體建議。');

  S.chat.push({ who: 'ai', text: '' });
  var aiIdx = S.chat.length - 1;
  S.chatThinking = false;
  renderChatMessages();
  var scrollEl = document.getElementById('v3-ai-messages');
  var bubbleEl = document.getElementById('v3-ai-bubble-' + aiIdx);
  var renderer = v3CreateStreamRenderer(bubbleEl, scrollEl);
  try {
    var finalText = await v3RunAI(prompt, sysInstruction, function (partial) { S.chat[aiIdx].text = partial; renderer.update(partial, true); }, signal);
    renderer.cancelPending();
    S.chat[aiIdx].text = finalText || t('chat.noContent');
    v3RenderMarkdown(bubbleEl, S.chat[aiIdx].text);
    v3ScrollToBottomIfNear(scrollEl);
  } catch (err) {
    renderer.cancelPending();
    if (err && err.name === 'AbortError') return;
    S.chat[aiIdx].text = t('chat.failed');
    if (bubbleEl) bubbleEl.innerHTML = v3RenderAIErrorHTML(err);
  }
}

/* ------------------------------------------------------------------------
 * 23. 開機
 * ---------------------------------------------------------------------- */
document.addEventListener('DOMContentLoaded', boot);
