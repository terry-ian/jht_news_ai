/* ==========================================================================
 * 喬山 AI 全球健身器材產業情報儀表板
 * 資料來源：./data/news.json（由爬蟲工程 agent 產生，schema 固定）
 * 本檔案不含任何硬編碼假新聞；news.json 不存在時顯示空狀態。
 * ========================================================================== */

const DATA_URL = './data/news.json';

const CATEGORY_NAME_MAP = {
  competitor: '競品情報動態',
  tech: '健身科技研發',
  market: '全球市場趨勢',
  brand: '品牌動態',
  finance: '財經/股市'
};
const CATEGORY_COLORS = {
  competitor: '#c8102e',
  tech: '#0f172a',
  market: '#f59e0b',
  brand: '#0ea5e9',
  finance: '#10b981'
};
const FAV_STORAGE_KEY = 'fitness-dashboard-favorites-v1';

// 追蹤品牌總清單（含喬山自家品牌 Johnson）。用於品牌聲量圖表／KPI，
// 即使本次爬蟲資料中某品牌命中 0 篇，也要在清單中完整呈現「共追蹤 N 個品牌」。
const TRACKED_BRANDS = [
  'Life Fitness', 'Technogym', 'Matrix', 'Precor', 'True', 'Cybex', 'Nautilus',
  'Concept2', 'Horizon', 'Sole', 'Spirit', 'NordicTrack', 'Bowflex', 'Peloton',
  'Inspire', 'Shuhua', 'Dyaco', 'Hammer Strength', 'Impulse', 'Keiser', 'Vision',
  'Johnson'
];

// ------------------------------------------------------------------------
// 全域狀態
// ------------------------------------------------------------------------
let newsData = null;      // 完整 news.json 內容
let articles = [];        // 目前使用的文章陣列
let stats = null;         // 目前使用的統計資料（含 fallback 補值）
let currentCategory = 'all';
let onlyShowFavorites = false;
let includeFinanceInAll = false; // 「綜合情報」預設排除 finance 分類雜訊，可由使用者切換
const PAGE_SIZE = 10;      // 每個分類每次顯示的情報數量
let visibleCount = PAGE_SIZE; // 目前分類/篩選條件下已顯示的情報數量
let favorites = [];
try {
  favorites = JSON.parse(localStorage.getItem(FAV_STORAGE_KEY) || '[]');
} catch (e) {
  favorites = [];
}

let chartCategory = null;
let chartBrand = null;
let chartTimeline = null;
let chartRadar = null;

// ------------------------------------------------------------------------
// 系統時間
// ------------------------------------------------------------------------
function updateSystemTime() {
  const now = new Date();
  const timeStr = now.getFullYear() + '-' +
    String(now.getMonth() + 1).padStart(2, '0') + '-' +
    String(now.getDate()).padStart(2, '0') + ' ' +
    String(now.getHours()).padStart(2, '0') + ':' +
    String(now.getMinutes()).padStart(2, '0') + ':' +
    String(now.getSeconds()).padStart(2, '0');
  const el = document.getElementById('current-time');
  if (el) el.innerText = timeStr;
}
setInterval(updateSystemTime, 1000);
updateSystemTime();

// ------------------------------------------------------------------------
// 資料讀取
// ------------------------------------------------------------------------
function showStatusBanner(message, tone = 'warning') {
  const banner = document.getElementById('data-status-banner');
  const text = document.getElementById('data-status-text');
  if (!banner || !text) return;
  banner.classList.remove('hidden', 'bg-amber-50', 'border-amber-200', 'text-amber-800', 'bg-red-50', 'border-red-200', 'text-red-700');
  if (tone === 'error') {
    banner.classList.add('bg-red-50', 'border-red-200', 'text-red-700');
  } else {
    banner.classList.add('bg-amber-50', 'border-amber-200', 'text-amber-800');
  }
  text.innerHTML = message;
}
function hideStatusBanner() {
  const banner = document.getElementById('data-status-banner');
  if (banner) banner.classList.add('hidden');
}

async function loadData() {
  showStatusBanner('正在讀取情報資料 (data/news.json) ...');
  try {
    const res = await fetch(DATA_URL, { cache: 'no-store' });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const json = await res.json();
    if (!json || !Array.isArray(json.articles)) {
      throw new Error('news.json 格式不符合預期 schema（缺少 articles 陣列）');
    }
    newsData = json;
    articles = json.articles.slice();
    stats = buildStatsWithFallback(json.stats, articles);
    hideStatusBanner();
    renderAll();
  } catch (err) {
    console.warn('[fitness-dashboard] 無法載入 data/news.json：', err.message);
    newsData = null;
    articles = [];
    stats = buildStatsWithFallback(null, []);
    showStatusBanner(
      '尚未偵測到情報資料檔 <code class="font-mono">data/news.json</code>。' +
      '此檔案應由爬蟲工程流程自動產生。目前顯示空狀態，未使用任何假資料。' +
      '<br class="hidden sm:block">' +
      '<span class="text-xs opacity-80">（開發預覽可參考 data/news.sample.json 的 schema 格式）</span>'
    );
    renderAll();
  }
}

function refreshData() {
  const icon = document.getElementById('refresh-icon');
  if (icon) icon.classList.add('animate-spin');
  loadData().finally(() => {
    setTimeout(() => { if (icon) icon.classList.remove('animate-spin'); }, 400);
    showToast('🔄 已重新整理情報資料');
  });
}

// ------------------------------------------------------------------------
// 統計資料 fallback（若 news.json 的 stats 缺欄位，從 articles 自行聚合）
// ------------------------------------------------------------------------
function buildStatsWithFallback(rawStats, articleList) {
  const fallback = computeFallbackStats(articleList);
  const merged = {
    total: (rawStats && typeof rawStats.total === 'number') ? rawStats.total : fallback.total,
    by_category: (rawStats && rawStats.by_category) ? rawStats.by_category : fallback.by_category,
    by_brand: (rawStats && rawStats.by_brand) ? rawStats.by_brand : fallback.by_brand,
    by_source: (rawStats && rawStats.by_source) ? rawStats.by_source : fallback.by_source,
    timeline: (rawStats && Array.isArray(rawStats.timeline) && rawStats.timeline.length) ? rawStats.timeline : fallback.timeline,
    updated: (rawStats && rawStats.updated) ? rawStats.updated : (newsData && newsData.generated_at) || null
  };
  return merged;
}

function computeFallbackStats(articleList) {
  const by_category = {};
  const by_brand = {};
  const by_source = {};
  const timelineMap = {};

  articleList.forEach(a => {
    if (a.category) by_category[a.category] = (by_category[a.category] || 0) + 1;
    if (a.brand) by_brand[a.brand] = (by_brand[a.brand] || 0) + 1;
    if (a.source) by_source[a.source] = (by_source[a.source] || 0) + 1;
    if (a.date) timelineMap[a.date] = (timelineMap[a.date] || 0) + 1;
  });

  const timeline = Object.keys(timelineMap).sort().map(date => ({ date, count: timelineMap[date] }));

  return {
    total: articleList.length,
    by_category,
    by_brand,
    by_source,
    timeline,
    updated: null
  };
}

// ------------------------------------------------------------------------
// 主渲染入口
// ------------------------------------------------------------------------
function renderAll() {
  visibleCount = PAGE_SIZE; // 每次完整重新載入資料時，分頁顯示數量重置
  renderMarquee();
  renderKPI();
  renderCategoryChart();
  renderBrandChart();
  renderTimelineChart();
  renderSourceRanking();
  renderRadarChart();
  renderBrandRankList();
  renderBrandHeat();
  renderArticles();
}

// ------------------------------------------------------------------------
// 跑馬燈：最新 3 則真實新聞標題輪播
// ------------------------------------------------------------------------
function renderMarquee() {
  const el = document.getElementById('ticker-text');
  const sourceCountEl = document.getElementById('ticker-source-count');
  const brandCountEl = document.getElementById('ticker-brand-count');
  if (!el) return;

  if (!articles.length) {
    el.innerText = '目前尚無情報資料，等待爬蟲資料更新中...';
  } else {
    const latest = articles.slice().sort((a, b) => (b.date || '').localeCompare(a.date || '')).slice(0, 3);
    const renderTickerText = () => {
      el.innerText = latest.map(a => `【${a.categoryName || CATEGORY_NAME_MAP[a.category] || '情報'}】${getDisplayField(a, 'title')}`).join('　|　');
    };
    renderTickerText();
    // 中文模式：即時翻譯跑馬燈標題，翻譯完成後重新組字串更新（走同一份 TRANSLATE_CACHE，與情報卡片共用不重複翻譯）
    if (getNewsLangKey() === 'zh') {
      latest.forEach(a => {
        const original = a.title || '';
        if (!original) return;
        const cached = TRANSLATE_CACHE.get(original);
        if (typeof cached === 'string') return;
        scheduleTranslate(original).then(() => { if (getNewsLangKey() === 'zh') renderTickerText(); });
      });
    }
  }

  if (sourceCountEl) sourceCountEl.innerText = Object.keys(stats.by_source || {}).length;
  // 追蹤品牌數採「追蹤品牌總清單」數量，而非本次僅有命中的品牌數，
  // 以呈現「共追蹤 N 個品牌」的完整感（即使部分品牌本次命中為 0）。
  if (brandCountEl) brandCountEl.innerText = TRACKED_BRANDS.length;
}

// ------------------------------------------------------------------------
// KPI 數字卡
// ------------------------------------------------------------------------
function isSameDay(dateStr, ref) {
  return dateStr === ref;
}
function isWithinDays(dateStr, days) {
  if (!dateStr) return false;
  const d = new Date(dateStr + 'T00:00:00');
  if (isNaN(d.getTime())) return false;
  const now = new Date();
  const diffMs = now.setHours(0, 0, 0, 0) - d.getTime();
  const diffDays = diffMs / (1000 * 60 * 60 * 24);
  return diffDays >= 0 && diffDays < days;
}

function renderKPI() {
  const todayStr = new Date().toISOString().slice(0, 10);
  const total = stats.total || articles.length || 0;
  const todayCount = articles.filter(a => isSameDay(a.date, todayStr)).length;
  const weekCount = articles.filter(a => isWithinDays(a.date, 7)).length;
  const brandCount = TRACKED_BRANDS.length; // 追蹤品牌總清單數量（含本次命中為 0 的品牌）
  const sourceCount = Object.keys(stats.by_source || {}).length;
  const updated = stats.updated || (newsData && newsData.generated_at) || null;

  setText('kpi-total', total);
  setText('kpi-today', todayCount);
  setText('kpi-week', weekCount);
  setText('kpi-brands', brandCount);
  setText('kpi-sources', sourceCount);
  setText('kpi-updated', updated ? formatDateTime(updated) : '尚無資料');
  setText('data-updated-text', updated ? formatDateTime(updated) : '尚無資料，等待爬蟲產生 data/news.json');
}

function setText(id, value) {
  const el = document.getElementById(id);
  if (el) el.innerText = value;
}

function formatDateTime(isoStr) {
  try {
    const d = new Date(isoStr);
    if (isNaN(d.getTime())) return isoStr;
    return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0') +
      ' ' + String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
  } catch (e) {
    return isoStr;
  }
}

// ------------------------------------------------------------------------
// 圖表：分類分布（甜甜圈）
// ------------------------------------------------------------------------
function renderCategoryChart() {
  const ctx = document.getElementById('chart-category');
  if (!ctx || typeof Chart === 'undefined') return;
  const byCat = stats.by_category || {};
  const keys = Object.keys(CATEGORY_NAME_MAP).filter(k => byCat[k] !== undefined || true);
  const labels = keys.map(k => CATEGORY_NAME_MAP[k]);
  const data = keys.map(k => byCat[k] || 0);
  const colors = keys.map(k => CATEGORY_COLORS[k]);

  if (chartCategory) chartCategory.destroy();

  if (!articles.length) {
    chartCategory = null;
    return;
  }

  chartCategory = new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels,
      datasets: [{ data, backgroundColor: colors, borderWidth: 2, borderColor: '#fff' }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { position: 'bottom', labels: { boxWidth: 10, font: { size: 10 } } }
      }
    }
  });
}

// ------------------------------------------------------------------------
// 圖表：品牌聲量長條圖
// ------------------------------------------------------------------------
function getTrackedBrandEntries() {
  // 以「追蹤品牌總清單」為底，即使本次爬蟲命中為 0 也列出，完整呈現追蹤範圍；
  // 若 news.json 命中了清單外的新品牌，仍附加顯示，避免真實資料被遺漏。
  const byBrand = stats.by_brand || {};
  const merged = TRACKED_BRANDS.map(name => [name, byBrand[name] || 0]);
  Object.keys(byBrand).forEach(name => {
    if (!TRACKED_BRANDS.includes(name)) merged.push([name, byBrand[name]]);
  });
  return merged.sort((a, b) => b[1] - a[1]);
}

function renderBrandChart() {
  const ctx = document.getElementById('chart-brand');
  const totalEl = document.getElementById('brand-chart-total-count');
  if (totalEl) totalEl.innerText = TRACKED_BRANDS.length;
  if (!ctx || typeof Chart === 'undefined') return;
  const entries = getTrackedBrandEntries();

  if (chartBrand) chartBrand.destroy();

  if (!entries.length) {
    chartBrand = null;
    return;
  }

  // 資料筆數較多時，讓內層畫布保持最小寬度以維持標籤可讀性，外層再水平捲動（RWD）
  const inner = ctx.parentElement;
  if (inner && inner.classList.contains('chart-box-inner')) {
    inner.style.minWidth = Math.max(560, entries.length * 34) + 'px';
  }

  chartBrand = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: entries.map(e => e[0]),
      datasets: [{
        label: '提及次數',
        data: entries.map(e => e[1]),
        backgroundColor: entries.map(e => e[1] > 0 ? '#c8102e' : '#e2e8f0'),
        borderRadius: 4,
        maxBarThickness: 24
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        y: { beginAtZero: true, ticks: { precision: 0, font: { size: 10 } } },
        x: { ticks: { font: { size: 9 }, maxRotation: 60, minRotation: 45 } }
      }
    }
  });
}

// ------------------------------------------------------------------------
// 圖表：時間趨勢折線圖
// ------------------------------------------------------------------------
function renderTimelineChart() {
  const ctx = document.getElementById('chart-timeline');
  if (!ctx || typeof Chart === 'undefined') return;
  const timeline = (stats.timeline || []).slice().sort((a, b) => (a.date || '').localeCompare(b.date || ''));

  if (chartTimeline) chartTimeline.destroy();

  if (!timeline.length) {
    chartTimeline = null;
    return;
  }

  chartTimeline = new Chart(ctx, {
    type: 'line',
    data: {
      labels: timeline.map(t => t.date),
      datasets: [{
        label: '新增情報數',
        data: timeline.map(t => t.count),
        borderColor: '#c8102e',
        backgroundColor: 'rgba(200,16,46,0.1)',
        fill: true,
        tension: 0.3,
        pointRadius: 3
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        y: { beginAtZero: true, ticks: { precision: 0, font: { size: 10 } } },
        x: { ticks: { font: { size: 9 }, maxRotation: 45, minRotation: 0 } }
      }
    }
  });
}

// ------------------------------------------------------------------------
// 媒體來源 Top 排行（純 HTML 橫條，避免與其他圖表爭奪畫布空間）
// ------------------------------------------------------------------------
function renderSourceRanking() {
  const container = document.getElementById('source-ranking-list');
  if (!container) return;
  const entries = Object.entries(stats.by_source || {}).sort((a, b) => b[1] - a[1]).slice(0, 8);

  if (!entries.length) {
    container.innerHTML = '<p class="text-slate-400 text-center py-8">尚無資料</p>';
    return;
  }

  const max = entries[0][1] || 1;
  container.innerHTML = entries.map(([source, count], idx) => `
    <div class="flex items-center gap-3">
      <span class="w-4 text-slate-400 font-mono flex-shrink-0">${idx + 1}</span>
      <div class="flex-grow min-w-0">
        <div class="flex items-center justify-between mb-1">
          <span class="text-slate-700 font-medium truncate pr-2">${escapeHTML(source)}</span>
          <span class="text-slate-400 font-mono flex-shrink-0">${count}</span>
        </div>
        <div class="rank-bar-track">
          <div class="rank-bar-fill" style="width:${Math.max(6, (count / max) * 100)}%"></div>
        </div>
      </div>
    </div>
  `).join('');
}

// ------------------------------------------------------------------------
// 主要競品聲量雷達比較圖
// ------------------------------------------------------------------------
function renderRadarChart() {
  const ctx = document.getElementById('chart-radar');
  if (!ctx || typeof Chart === 'undefined') return;
  const byBrand = stats.by_brand || {};
  // 僅取實際有聲量命中的主要競品 Top 6~8 名做多維比較（0 命中品牌不放入雷達圖，避免壓縮視覺尺度）
  const entries = Object.entries(byBrand).filter(e => e[1] > 0).sort((a, b) => b[1] - a[1]).slice(0, 8);

  if (chartRadar) chartRadar.destroy();

  if (entries.length < 3) {
    chartRadar = null;
    return;
  }

  chartRadar = new Chart(ctx, {
    type: 'radar',
    data: {
      labels: entries.map(e => e[0]),
      datasets: [{
        label: '情報聲量',
        data: entries.map(e => e[1]),
        backgroundColor: 'rgba(200,16,46,0.15)',
        borderColor: '#c8102e',
        borderWidth: 2,
        pointBackgroundColor: '#c8102e',
        pointRadius: 4
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        r: { beginAtZero: true, ticks: { precision: 0, font: { size: 10 } }, pointLabels: { font: { size: 12, weight: '600' } } }
      }
    }
  });
}

// ------------------------------------------------------------------------
// 品牌聲量 Top 排行榜（水平長條 + 百分比，取代原本一整排雷達圖旁的空白區域）
// ------------------------------------------------------------------------
function renderBrandRankList() {
  const container = document.getElementById('brand-rank-list');
  if (!container) return;
  const byBrand = stats.by_brand || {};
  const total = Object.values(byBrand).reduce((sum, v) => sum + v, 0) || 1;
  const entries = Object.entries(byBrand).sort((a, b) => b[1] - a[1]).slice(0, 8);

  if (!entries.length) {
    container.innerHTML = '<p class="text-slate-400 text-center py-8">尚無資料</p>';
    return;
  }

  container.innerHTML = entries.map(([brand, count], idx) => {
    const pct = (count / total) * 100;
    return `
      <div class="flex items-center gap-2.5">
        <span class="w-4 text-slate-400 font-mono flex-shrink-0">${idx + 1}</span>
        <div class="flex-grow min-w-0">
          <div class="flex items-center justify-between mb-1">
            <span class="text-slate-700 font-medium truncate pr-2">${escapeHTML(brand)}</span>
            <span class="text-slate-400 font-mono flex-shrink-0">${count}（${pct.toFixed(1)}%）</span>
          </div>
          <div class="rank-bar-track">
            <div class="rank-bar-fill" style="width:${Math.max(6, pct)}%"></div>
          </div>
        </div>
      </div>
    `;
  }).join('');
}

// ------------------------------------------------------------------------
// 主要品牌近 14 日情報熱度小型熱力表（依真實 articles 的 brand + date 動態聚合，
// 以資料集中最新日期為視窗終點，避免離線快照造成整排空白）
// ------------------------------------------------------------------------
function renderBrandHeat() {
  const container = document.getElementById('brand-heat-grid');
  if (!container) return;

  const byBrand = stats.by_brand || {};
  const topBrands = Object.entries(byBrand).filter(e => e[1] > 0).sort((a, b) => b[1] - a[1]).slice(0, 6).map(e => e[0]);

  if (!topBrands.length) {
    container.innerHTML = '<p class="text-slate-400 text-center py-8">尚無資料</p>';
    return;
  }

  const allDates = articles.map(a => a.date).filter(Boolean).sort();
  const endDateStr = allDates.length ? allDates[allDates.length - 1] : new Date().toISOString().slice(0, 10);
  const endDate = new Date(endDateStr + 'T00:00:00');
  const days = [];
  for (let i = 13; i >= 0; i--) {
    const d = new Date(endDate);
    d.setDate(d.getDate() - i);
    days.push(d.toISOString().slice(0, 10));
  }

  const dailyCounts = {};
  articles.forEach(a => {
    if (!a.brand || !topBrands.includes(a.brand) || !a.date) return;
    const key = a.brand + '|' + a.date;
    dailyCounts[key] = (dailyCounts[key] || 0) + 1;
  });
  const maxVal = Math.max(1, ...Object.values(dailyCounts));

  container.innerHTML = `
    <div class="space-y-2 min-w-[220px]">
      ${topBrands.map(brand => {
        const cells = days.map(d => {
          const v = dailyCounts[brand + '|' + d] || 0;
          const intensity = v === 0 ? 0 : Math.min(1, v / maxVal);
          const bg = v === 0 ? '#f1f5f9' : `rgba(200,16,46,${(0.2 + intensity * 0.7).toFixed(2)})`;
          return `<span class="heat-cell" style="background:${bg}" title="${escapeHTML(brand)}｜${d}：${v} 則"></span>`;
        }).join('');
        return `
          <div class="flex items-center gap-2">
            <span class="w-20 truncate text-slate-600 font-medium flex-shrink-0">${escapeHTML(brand)}</span>
            <div class="flex gap-[2px]">${cells}</div>
          </div>
        `;
      }).join('')}
      <p class="text-[9px] text-slate-400 pt-1">視窗：${days[0]} ~ ${days[days.length - 1]}（依資料集最新日期回推 14 日）</p>
    </div>
  `;
}

// ------------------------------------------------------------------------
// 情報卡片清單
// ------------------------------------------------------------------------
function escapeHTML(str) {
  return String(str == null ? '' : str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function renderArticles() {
  const container = document.getElementById('articles-container');
  if (!container) return;
  container.innerHTML = '';

  if (!newsData) {
    container.innerHTML = `
      <div class="bg-white p-12 text-center rounded-xl border border-slate-200">
        <svg class="w-12 h-12 text-slate-300 mx-auto mb-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"></path></svg>
        <p class="text-slate-500 text-sm font-medium">尚未載入任何情報資料</p>
        <p class="text-slate-400 text-xs mt-1">請確認 <code class="font-mono">data/news.json</code> 已由爬蟲流程產生，並使用 http server 開啟本頁面。</p>
      </div>
    `;
    return;
  }

  const searchVal = (document.getElementById('search-input').value || '').toLowerCase();

  const filtered = articles.filter(art => {
    if (currentCategory === 'all') {
      // 「綜合情報」預設排除財經/股市雜訊，突顯產業情報；使用者可透過切換開關顯示
      if (!includeFinanceInAll && art.category === 'finance') return false;
    } else if (art.category !== currentCategory) {
      return false;
    }
    if (onlyShowFavorites && !favorites.includes(art.id)) return false;
    if (searchVal) {
      // 搜尋比對來源：原文（英文）欄位 + 已即時翻譯完成的中文快取（若有），
      // 不再依賴 news.json 預存但不完整的 title_zh/summary_zh 欄位。
      const haystack = [
        art.title, art.summary,
        TRANSLATE_CACHE.get(art.title), TRANSLATE_CACHE.get(art.summary),
        art.source, art.brand
      ].filter(v => typeof v === 'string').join(' ').toLowerCase();
      if (!haystack.includes(searchVal)) return false;
    }
    return true;
  }).sort((a, b) => (b.date || '').localeCompare(a.date || ''));

  if (filtered.length === 0) {
    container.innerHTML = `
      <div class="bg-white p-12 text-center rounded-xl border border-slate-200">
        <svg class="w-12 h-12 text-slate-300 mx-auto mb-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.172 16.172a4 4 0 015.656 0M9 10h.01M15 10h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>
        <p class="text-slate-500 text-sm">無符合當前條件的產業情報。</p>
      </div>
    `;
    return;
  }

  // 分頁：每個分類（含「綜合情報」）一次只顯示 visibleCount 筆，
  // 切換分類 / 搜尋 / 收藏篩選時 visibleCount 會被重置為 PAGE_SIZE。
  const visibleArticles = filtered.slice(0, visibleCount);

  visibleArticles.forEach(art => {
    const isFav = favorites.includes(art.id);
    const categoryName = art.categoryName || CATEGORY_NAME_MAP[art.category] || '未分類';
    const url = art.url || '#';
    const displayTitle = getDisplayField(art, 'title');
    const displaySummary = getDisplayField(art, 'summary');
    const card = document.createElement('div');
    card.className = "bg-white p-5 rounded-xl border border-slate-200 shadow-sm hover:shadow-md transition-all flex flex-col justify-between space-y-4";
    card.innerHTML = `
      <div class="space-y-2">
        <div class="flex items-center justify-between flex-wrap gap-2">
          <div class="flex items-center gap-2">
            <span class="text-[11px] font-bold text-brand bg-brand-light px-2.5 py-0.5 rounded-full border border-brand/10">${escapeHTML(categoryName)}</span>
            ${art.brand ? `<span class="text-[11px] font-medium text-slate-500 bg-slate-100 px-2.5 py-0.5 rounded-full">${escapeHTML(art.brand)}</span>` : ''}
          </div>
          <div class="flex items-center space-x-2">
            <span class="text-xs text-slate-400 font-mono">${escapeHTML(art.date || '')}</span>
            <button onclick="toggleFavorite(${art.id})" class="text-slate-400 hover:text-amber-500 transition-colors p-1" title="點擊收藏/取消">
              <svg class="w-5 h-5 ${isFav ? 'text-amber-500 fill-amber-500' : ''}" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11.049 2.927c.3-.921 1.603-.921 1.902 0l1.519 4.674a1 1 0 00.95.69h4.907c.961 0 1.36 1.25.591 1.813l-3.97 2.88a1 1 0 00-.364 1.118l1.518 4.674c.3.922-.755 1.688-1.538 1.118l-3.971-2.88a1 1 0 00-1.176 0l-3.97 2.88c-.783.57-1.838-.197-1.538-1.118l1.518-4.674a1 1 0 00-.364-1.118l-3.97-2.88c-.768-.563-.369-1.813.591-1.813h4.907a1 1 0 00.95-.69l1.519-4.674z"></path></svg>
            </button>
          </div>
        </div>
        <a href="${escapeHTML(url)}" target="_blank" rel="noopener" id="art-title-${art.id}" class="notranslate block font-bold text-slate-900 text-sm sm:text-base leading-snug hover:text-brand transition-colors" translate="no">${escapeHTML(displayTitle)}</a>
        <p id="art-summary-${art.id}" class="notranslate text-xs sm:text-sm text-slate-600 leading-relaxed line-clamp-3" translate="no">${escapeHTML(displaySummary)}</p>
      </div>
      <div class="flex items-center justify-between border-t border-slate-100 pt-3 text-xs gap-2 flex-wrap">
        <span class="text-slate-400">來源: <strong class="text-slate-600 font-medium">${escapeHTML(art.source || '未知')}</strong></span>
        <div class="flex items-center gap-2">
          <a href="${escapeHTML(url)}" target="_blank" rel="noopener" class="bg-slate-100 text-slate-700 hover:bg-slate-200 px-3 py-1.5 rounded-lg font-bold flex items-center gap-1 transition-all">
            <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"></path></svg>
            閱讀原文
          </a>
          <button onclick="openAIOption(${art.id})" class="bg-slate-100 text-slate-700 hover:bg-brand-light hover:text-brand px-3 py-1.5 rounded-lg font-bold flex items-center gap-1 transition-all">
            <svg class="w-3.5 h-3.5 text-brand" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M11.3 1.046A1 1 0 0112 2v5h4a1 1 0 01.82 1.573l-7 10A1 1 0 018 18v-5H4a1 1 0 01-.82-1.573l7-10a1 1 0 011.12-.38z" clip-rule="evenodd"></path></svg>
            AI一鍵摘要
          </button>
        </div>
      </div>
    `;
    container.appendChild(card);
  });

  // 對「本次當頁所有顯示中」的情報（含載入更多後新增的項目）觸發即時翻譯，
  // 已快取過的文字不會重複發送翻譯請求，未快取的會依節流佇列依序翻譯完成後更新對應卡片。
  translateVisibleArticles(visibleArticles);

  renderLoadMoreFooter(container, visibleArticles.length, filtered.length);
}

// ------------------------------------------------------------------------
// 分頁「載入更多」小計提示 + 按鈕
// ------------------------------------------------------------------------
function renderLoadMoreFooter(container, shownCount, totalCount) {
  const footer = document.createElement('div');
  footer.className = 'pt-2 pb-1 text-center space-y-2';

  let html = `<p class="text-xs text-slate-400">已顯示 <span class="font-semibold text-slate-600">${shownCount}</span> / 共 <span class="font-semibold text-slate-600">${totalCount}</span> 則情報</p>`;

  if (shownCount < totalCount) {
    html += `
      <button type="button" onclick="loadMoreArticles()" id="load-more-btn" class="load-more-btn inline-flex items-center gap-1.5 bg-white border border-slate-200 hover:border-brand hover:text-brand text-slate-600 px-5 py-2 rounded-lg text-xs sm:text-sm font-medium shadow-sm transition-colors">
        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"></path></svg>
        載入更多情報（還有 ${totalCount - shownCount} 則）
      </button>
    `;
  } else if (totalCount > PAGE_SIZE) {
    html += `<p class="text-xs text-slate-400 italic">已無更多情報</p>`;
  }

  footer.innerHTML = html;
  container.appendChild(footer);
}

function loadMoreArticles() {
  visibleCount += PAGE_SIZE;
  renderArticles();
}

// ------------------------------------------------------------------------
// 類別切換 / 搜尋 / 收藏
// ------------------------------------------------------------------------
function switchCategory(cat, btnEl) {
  currentCategory = cat;
  visibleCount = PAGE_SIZE; // 切換分類時，顯示數量重置回第一頁
  const btns = document.querySelectorAll('#category-tabs button');
  btns.forEach(btn => {
    btn.classList.remove('bg-brand', 'text-white', 'active');
    btn.classList.add('bg-slate-100', 'text-slate-600');
  });
  if (btnEl) {
    btnEl.classList.remove('bg-slate-100', 'text-slate-600');
    btnEl.classList.add('bg-brand', 'text-white', 'active');
  }
  renderArticles();
}

function filterArticles() {
  visibleCount = PAGE_SIZE; // 搜尋條件變動時，顯示數量重置回第一頁
  renderArticles();
}

function toggleFinanceNoise(checked) {
  includeFinanceInAll = !!checked;
  visibleCount = PAGE_SIZE; // 篩選條件變動時，顯示數量重置回第一頁
  renderArticles();
}

function toggleFavoriteFilter() {
  onlyShowFavorites = !onlyShowFavorites;
  visibleCount = PAGE_SIZE; // 切換收藏篩選時，顯示數量重置回第一頁
  const btn = document.getElementById('favorite-toggle-btn');
  const text = document.getElementById('fav-filter-text');
  const icon = document.getElementById('fav-filter-icon');

  if (onlyShowFavorites) {
    btn.classList.add('bg-amber-50', 'border-amber-300', 'text-amber-800');
    icon.classList.add('fill-amber-500');
    text.innerText = "顯示全部";
  } else {
    btn.classList.remove('bg-amber-50', 'border-amber-300', 'text-amber-800');
    icon.classList.remove('fill-amber-500');
    text.innerText = "已收藏情報";
  }
  renderArticles();
}

function toggleFavorite(id) {
  const idx = favorites.indexOf(id);
  if (idx >= 0) {
    favorites.splice(idx, 1);
  } else {
    favorites.push(id);
  }
  try {
    localStorage.setItem(FAV_STORAGE_KEY, JSON.stringify(favorites));
  } catch (e) { /* localStorage 不可用時忽略 */ }
  renderArticles();
}

// ------------------------------------------------------------------------
// Toast 提示
// ------------------------------------------------------------------------
function showToast(msg) {
  const toast = document.createElement('div');
  toast.className = "fixed bottom-5 left-1/2 transform -translate-x-1/2 z-50 bg-slate-900 text-white text-xs sm:text-sm px-6 py-3 rounded-full shadow-lg border border-slate-700 flex items-center space-x-2";
  toast.innerHTML = `<span>${escapeHTML(msg)}</span>`;
  document.body.appendChild(toast);
  setTimeout(() => toast.remove(), 2500);
}

/* ==========================================================================
 * 語言切換（繁體中文 ⇄ English）
 * ----------------------------------------------------------------------
 * 實測發現：Google 翻譯官方 TranslateElement 在 layout: SIMPLE 設定下，
 * 「請選取語言」小工具實際渲染成一個連結 + 跨網域彈出選單 iframe，
 * 網頁本身的 DOM 中並不會出現可操作的 <select class="goog-te-combo">
 * （該 iframe 內容為 translate.google.com 網域，屬跨來源，無法用
 * document.querySelector 直接存取／設值），因此原訂「找 .goog-te-combo
 * 設 value 並 dispatchEvent」的作法在此版本 Google 翻譯下無法穩定運作。
 *
 * 改採官方另一種穩定做法：寫入 googtrans cookie 後重新整理頁面，
 * Google 翻譯腳本會在頁面載入時自動讀取此 cookie 並翻譯整頁內容。
 * #google_translate_element 仍保留在頁面（移到畫面外），
 * 用來讓 Google 翻譯腳本正常初始化、驅動翻譯引擎。
 * ========================================================================== */
const SITE_LANG_STORAGE_KEY = 'fitness-dashboard-lang-v1';
const GOOGTRANS_COOKIE_NAME = 'googtrans';
const SITE_SOURCE_LANG = 'zh-TW';

function updateLangButtonsUI(lang) {
  const zhBtn = document.getElementById('lang-btn-zh');
  const enBtn = document.getElementById('lang-btn-en');
  if (!zhBtn || !enBtn) return;
  const isEn = lang === 'en';
  zhBtn.classList.toggle('active', !isEn);
  enBtn.classList.toggle('active', isEn);
  zhBtn.setAttribute('aria-pressed', String(!isEn));
  enBtn.setAttribute('aria-pressed', String(isEn));
}

/**
 * 依目前顯示語言，回傳新聞欄位語言後綴（'zh' 或 'en'）。
 * 中文模式（預設 / googtrans 為 zh-TW）-> 'zh'；英文模式（googtrans 切到 en）-> 'en'。
 */
function getNewsLangKey() {
  return getCurrentSiteLanguage() === 'en' ? 'en' : 'zh';
}

/**
 * 依目前語言狀態，回傳文章物件應顯示的欄位文字（title/summary）。
 * - 英文模式：直接顯示新聞來源原文（art.title / art.summary，本身即為英文）。
 * - 中文模式：顯示「前端即時翻譯」後的繁體中文（走 TRANSLATE_CACHE），
 *   尚未翻譯完成前，先 fallback 顯示原文，翻譯完成後由呼叫端自行替換 DOM。
 * 注意：不再依賴 news.json 內預存的 title_zh / summary_zh（資料不完整，已停用）。
 * @param {object} art - 文章物件
 * @param {'title'|'summary'} field - 欄位名稱
 */
function getDisplayField(art, field) {
  if (!art) return '';
  const original = art[field] || '';
  if (!original) return '';
  if (getNewsLangKey() !== 'zh') return original; // 英文模式：顯示原文
  const cached = TRANSLATE_CACHE.get(original);
  return (typeof cached === 'string') ? cached : original; // 中文模式：有快取用快取，沒有先顯示原文
}
// 舊名稱相容別名（避免遺漏呼叫點）
function getLocalizedField(art, field) { return getDisplayField(art, field); }

/* ==========================================================================
 * 即時翻譯模組（前端翻譯，不依賴 news.json 的 title_zh/summary_zh）
 * ----------------------------------------------------------------------
 * - 翻譯來源：文章原文欄位（title/summary，英文）。
 * - 主要端點：Google 翻譯非官方端點（translate.googleapis.com，瀏覽器端可直接 fetch，
 *   實測支援 CORS）。若被擋或失敗，改用備援端點 MyMemory。兩者皆失敗則 fallback 顯示原文，
 *   不會拋出未捕捉例外、不會卡住頁面。
 * - 快取：Map（key = 原文字串 -> 翻譯後字串，或處理中的 Promise），避免重複翻譯同一段文字。
 * - 節流：同時最多 TRANSLATE_CONCURRENCY 個請求併發，其餘進入佇列依序處理。
 * ========================================================================== */
const TRANSLATE_CACHE = new Map(); // 原文 -> 翻譯後文字（string）或處理中的 Promise
const TRANSLATE_QUEUE = [];
const TRANSLATE_CONCURRENCY = 5;
let translateActiveCount = 0;

/**
 * 呼叫翻譯端點，將任意文字翻譯為繁體中文（zh-TW）。
 * 內部已完整 try/catch，任何情況都會 resolve 一個字串（絕不 reject / 絕不拋出）。
 */
async function translateTextToZh(text) {
  // 主要端點：Google 翻譯非官方端點
  try {
    const res = await fetch('https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=zh-TW&dt=t&q=' + encodeURIComponent(text));
    if (res.ok) {
      const data = await res.json();
      if (Array.isArray(data) && Array.isArray(data[0]) && data[0].length) {
        const joined = data[0].map(seg => (Array.isArray(seg) && seg[0]) ? seg[0] : '').join('');
        if (joined) return joined;
      }
    }
  } catch (e) {
    // 忽略（可能為 CORS 或網路問題），改走備援端點
  }

  // 備援端點：MyMemory 免費翻譯 API
  try {
    const res2 = await fetch('https://api.mymemory.translated.net/get?q=' + encodeURIComponent(text) + '&langpair=en|zh-TW');
    if (res2.ok) {
      const data2 = await res2.json();
      const translated = data2 && data2.responseData && data2.responseData.translatedText;
      if (translated) return translated;
    }
  } catch (e) {
    // 忽略，最終 fallback 顯示原文
  }

  return text; // 兩個端點皆失敗：fallback 顯示原文，不讓單則翻譯失敗卡住整頁
}

/**
 * 將一段文字排入翻譯佇列（含快取、節流）。回傳 Promise<翻譯後文字>。
 * 同一段原文若已在翻譯中或已翻譯完成，直接共用同一份快取結果，不重複發送請求。
 */
function scheduleTranslate(text) {
  if (!text) return Promise.resolve(text);
  const existing = TRANSLATE_CACHE.get(text);
  if (typeof existing === 'string') return Promise.resolve(existing);
  if (existing instanceof Promise) return existing;

  const promise = new Promise((resolve) => {
    TRANSLATE_QUEUE.push({ text, resolve });
    pumpTranslateQueue();
  });
  TRANSLATE_CACHE.set(text, promise);
  return promise;
}

function pumpTranslateQueue() {
  while (translateActiveCount < TRANSLATE_CONCURRENCY && TRANSLATE_QUEUE.length) {
    const job = TRANSLATE_QUEUE.shift();
    translateActiveCount++;
    translateTextToZh(job.text)
      .then((result) => {
        const finalText = result || job.text;
        TRANSLATE_CACHE.set(job.text, finalText); // 以最終翻譯結果覆蓋快取中的 Promise，供後續快速查表
        job.resolve(finalText);
      })
      .catch(() => {
        TRANSLATE_CACHE.set(job.text, job.text); // 理論上不會走到這裡（translateTextToZh 已全面 try/catch），保底 fallback 原文
        job.resolve(job.text);
      })
      .finally(() => {
        translateActiveCount--;
        pumpTranslateQueue();
      });
  }
}

/**
 * 對「目前顯示中」的一批文章，翻譯其 title 與 summary（僅中文模式需要；英文模式直接顯示原文，略過）。
 * 已在快取中的文字不會重複發送翻譯請求；翻譯完成後直接更新對應卡片的 DOM 文字，
 * 若該卡片已因重新渲染（切換分類/搜尋/收藏/載入更多）而不存在，則安全略過，不會報錯。
 * @param {Array} list - 目前畫面上顯示中的文章陣列
 */
function translateVisibleArticles(list) {
  if (getNewsLangKey() !== 'zh') return; // 英文模式：顯示原文，不需要翻譯
  (list || []).forEach(art => {
    ['title', 'summary'].forEach(field => {
      const original = art[field] || '';
      if (!original) return;
      const cached = TRANSLATE_CACHE.get(original);
      if (typeof cached === 'string') return; // 已有翻譯結果，畫面已於渲染時直接使用，不需再處理
      scheduleTranslate(original).then((translated) => {
        // 翻譯完成後，僅在目前語言仍為中文模式時才寫回 DOM（避免使用者已切到英文模式卻被覆蓋）
        if (getNewsLangKey() !== 'zh') return;
        const elId = (field === 'title' ? 'art-title-' : 'art-summary-') + art.id;
        const el = document.getElementById(elId);
        if (el) el.textContent = translated;
      });
    });
  });
}

// 讀取目前 googtrans cookie，判斷目前顯示語言（找不到 cookie 視為原文 zh-TW）
function getCurrentSiteLanguage() {
  const match = document.cookie.match(/(?:^|;\s*)googtrans=([^;]*)/);
  if (!match) return SITE_SOURCE_LANG;
  const value = decodeURIComponent(match[1] || '');
  const parts = value.split('/').filter(Boolean); // 例如 "/zh-TW/en" -> ['zh-TW','en']
  const target = parts[parts.length - 1];
  return target && target !== SITE_SOURCE_LANG ? target : SITE_SOURCE_LANG;
}

function writeGoogTransCookie(targetLang) {
  const value = encodeURIComponent(`/${SITE_SOURCE_LANG}/${targetLang}`);
  const hostname = location.hostname;
  // 不帶 domain（一般情況已足夠，含 localhost）
  document.cookie = `${GOOGTRANS_COOKIE_NAME}=${value}; path=/;`;
  // 額外帶目前 hostname，涵蓋部分瀏覽器對 cookie 網域比對較嚴格的情況
  if (hostname) {
    document.cookie = `${GOOGTRANS_COOKIE_NAME}=${value}; path=/; domain=${hostname};`;
  }
}

function clearGoogTransCookie() {
  const expired = 'Thu, 01 Jan 1970 00:00:00 UTC';
  const hostname = location.hostname;
  document.cookie = `${GOOGTRANS_COOKIE_NAME}=; path=/; expires=${expired};`;
  if (hostname) {
    document.cookie = `${GOOGTRANS_COOKIE_NAME}=; path=/; domain=${hostname}; expires=${expired};`;
  }
}

/**
 * 切換頁面語言（會重新整理頁面，由 Google 翻譯腳本依 cookie 自動翻譯）。
 * @param {string} lang - 'zh-TW'（切回原文）或 'en'（翻譯為英文）
 */
function setSiteLanguage(lang) {
  if (lang === SITE_SOURCE_LANG) {
    clearGoogTransCookie();
  } else {
    writeGoogTransCookie(lang);
  }
  try { localStorage.setItem(SITE_LANG_STORAGE_KEY, lang); } catch (e) { /* localStorage 不可用時忽略 */ }
  updateLangButtonsUI(lang);
  location.reload();
}

/**
 * 依目前語言狀態，固定左上角品牌主名稱與副標的顯示文字。
 * 品牌主名稱元素已加上 class="notranslate" translate="no"，Google 翻譯不會碰它，
 * 因此中／英文名稱一律由本函式依 googtrans cookie 判斷語言後精準設定，
 * 避免英文模式下被機器亂譯成非官方名稱。
 * - 中文模式：品牌名「喬山健康科技」；副標「全球健身器材產業情報決策儀表板」。
 * - 英文模式：品牌名固定官方英文名「Johnson Health Tech」；副標英文「Global Fitness Industry Intelligence Dashboard」。
 */
function applyBrandLanguage() {
  const isEn = getCurrentSiteLanguage() === 'en';
  const nameEl = document.getElementById('brand-name');
  if (nameEl) nameEl.textContent = isEn ? 'Johnson Health Tech' : '喬山健康科技';
  const subEl = document.getElementById('brand-subtitle');
  if (subEl) subEl.textContent = isEn
    ? 'Global Fitness Industry Intelligence Dashboard'
    : '全球健身器材產業情報決策儀表板';
}

// 頁面載入時，依目前 googtrans cookie 狀態同步按鈕外觀（不會觸發翻譯，只更新 UI）
function restoreSiteLanguagePreference() {
  updateLangButtonsUI(getCurrentSiteLanguage());
  applyBrandLanguage();
}

/* ==========================================================================
 * Google Gemini AI 代理呼叫區
 * 架構：瀏覽器 → 本站後端 /api/ai（Vercel Serverless Function 或本機
 *       dev-server.js）→ Google Gemini（預設走 Vertex AI 服務帳戶，
 *       亦可由後端環境變數切換為 Google AI Studio API Key）。
 * 金鑰／服務帳戶一律只存放在伺服器端環境變數，瀏覽器端完全不需要、
 * 也不會接觸到任何金鑰。
 * 三個 AI 功能（單篇摘要 / 智庫對話 / 戰略報告）皆共用本區的呼叫邏輯，
 * 僅提示詞（prompt / systemInstruction）於各自功能區塊組裝。
 * ========================================================================== */

const AI_PROXY_ENDPOINT = './api/ai';

// 前端顯示用的模型名稱／認證模式標籤，會在頁面載入時透過 GET /api/ai
// 向後端查詢實際設定值後覆蓋，避免前端寫死與後端環境變數（GEMINI_MODEL）不一致。
let currentAIModelLabel = 'Gemini';
let currentAIAuthModeLabel = '-';

const MISSING_API_KEY_MESSAGE = 'AI 後端服務尚未設定完成（伺服器端缺少 Gemini / Vertex AI 認證設定，請確認環境變數）';

// 將目前已知的模型名稱同步顯示到聊天室頭部、摘要 Modal 等處
function updateAIModelLabelsInUI() {
  const chatModelLabel = document.getElementById('chat-model-label');
  if (chatModelLabel) chatModelLabel.innerText = currentAIModelLabel;
  const modalModelLabel = document.getElementById('modal-model-label');
  if (modalModelLabel) modalModelLabel.innerText = currentAIModelLabel;
  const settingsModelEl = document.getElementById('settings-current-model');
  if (settingsModelEl) settingsModelEl.innerText = currentAIModelLabel;
}

// ------------------------------------------------------------------------
// AI 設定 Modal：改為顯示「目前使用模型」與「後端連線狀態」，
// 不再需要使用者輸入任何金鑰（金鑰已移至伺服器端環境變數）。
// ------------------------------------------------------------------------
async function refreshSettingsModalStatus() {
  const statusEl = document.getElementById('settings-key-status');
  const authModeEl = document.getElementById('settings-auth-mode');

  updateAIModelLabelsInUI();
  if (authModeEl) authModeEl.innerText = currentAIAuthModeLabel;
  if (statusEl) {
    statusEl.className = 'font-medium text-right text-slate-400';
    statusEl.innerText = '檢查中...';
  }

  try {
    const resp = await fetch(AI_PROXY_ENDPOINT, { method: 'GET' });
    const data = await resp.json().catch(() => ({}));
    if (resp.ok && data && data.model) {
      currentAIModelLabel = data.model;
      currentAIAuthModeLabel = data.authMode === 'gemini-api-key' ? 'Google AI Studio API Key' : 'Vertex AI（服務帳戶）';
      updateAIModelLabelsInUI();
      if (authModeEl) authModeEl.innerText = currentAIAuthModeLabel;
      if (statusEl) {
        const ready = !!data.ready;
        statusEl.className = ready ? 'font-medium text-right text-emerald-600' : 'font-medium text-right text-amber-600';
        statusEl.innerText = ready ? '✅ 後端已設定完成' : '⚠️ 後端尚未完成認證設定';
      }
    } else if (statusEl) {
      statusEl.className = 'font-medium text-right text-red-600';
      statusEl.innerText = '⚠️ 無法連線至 AI 後端';
    }
  } catch (e) {
    if (statusEl) {
      statusEl.className = 'font-medium text-right text-red-600';
      statusEl.innerText = '⚠️ 無法連線至 AI 後端（' + (e && e.message ? e.message : '未知錯誤') + '）';
    }
  }
}

function openSettingsModal() {
  const modal = document.getElementById('settings-modal');
  refreshSettingsModalStatus();
  if (modal) modal.classList.remove('hidden');
}

function closeSettingsModal() {
  const modal = document.getElementById('settings-modal');
  if (modal) modal.classList.add('hidden');
}

// ------------------------------------------------------------------------
// /api/ai 非串流呼叫（含 retry / 指數退避）
// ------------------------------------------------------------------------
async function callOpenAIAPI(promptText, systemInstructionText = "") {
  let delay = 1000;
  for (let attempt = 0; attempt < 5; attempt++) {
    try {
      const response = await fetch(AI_PROXY_ENDPOINT, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt: promptText, systemInstruction: systemInstructionText, stream: false })
      });
      const data = await response.json().catch(() => ({}));
      if (data && data.model) {
        currentAIModelLabel = data.model;
        updateAIModelLabelsInUI();
      }
      if (!response.ok) {
        const err = new Error(`AI 後端錯誤碼: ${response.status}${data && data.error ? '，訊息：' + data.error : ''}`);
        if (data && data.missingCredentials) err.isMissingKey = true;
        throw err;
      }
      if (data && data.text) return data.text;
      throw new Error('AI 後端回傳中無生成之文字內容。');
    } catch (error) {
      if (error && error.isMissingKey) throw error; // 缺少後端認證設定，重試無意義
      if (attempt === 4) throw error;
      await new Promise(resolve => setTimeout(resolve, delay));
      delay *= 2;
    }
  }
}

// ------------------------------------------------------------------------
// /api/ai 串流呼叫（SSE）。後端已統一把 Gemini 回應轉譯為固定協定：
//   data: {"delta":"..."}                 每個增量文字片段
//   data: {"done":true,"text":"..."}      結束，附完整全文
//   data: {"error":"..."}                 串流中途發生錯誤
// 邊收邊透過 onDelta(fullTextSoFar, deltaChunk) 回呼，讓呼叫端即時重新渲染。
// ------------------------------------------------------------------------
async function streamOpenAIAPI(promptText, systemInstructionText = "", onDelta, signal) {
  const response = await fetch(AI_PROXY_ENDPOINT, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ prompt: promptText, systemInstruction: systemInstructionText, stream: true }),
    signal
  });

  if (!response.ok) {
    let detail = '';
    let missingKey = false;
    try {
      const errJson = await response.json();
      detail = (errJson && errJson.error) || '';
      missingKey = !!(errJson && errJson.missingCredentials);
    } catch (parseErr) { /* 忽略錯誤內容解析失敗 */ }
    const err = new Error(`AI 後端錯誤碼: ${response.status}${detail ? '，訊息：' + detail : ''}`);
    err.isMissingKey = missingKey;
    throw err;
  }
  if (!response.body || typeof response.body.getReader !== 'function') {
    // 瀏覽器不支援 ReadableStream，交由呼叫端 fallback 為打字機效果
    const err = new Error('目前瀏覽器不支援串流讀取（ReadableStream）。');
    err.isStreamUnsupported = true;
    throw err;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buffer = '';
  let fullText = '';

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop(); // 保留未完整的最後一行，下次繼續拼接
    for (const rawLine of lines) {
      const line = rawLine.trim();
      if (!line || !line.startsWith('data:')) continue;
      const dataStr = line.slice(5).trim();
      if (!dataStr) continue;

      let json;
      try {
        json = JSON.parse(dataStr);
      } catch (e) {
        continue; // 忽略單一 SSE chunk 的解析失敗（可能為不完整的 JSON 片段）
      }

      if (json.model) {
        currentAIModelLabel = json.model;
        updateAIModelLabelsInUI();
      }
      if (json.error) {
        throw new Error(json.error);
      }
      if (typeof json.delta === 'string' && json.delta) {
        fullText += json.delta;
        if (typeof onDelta === 'function') onDelta(fullText, json.delta);
      }
      if (json.done) {
        return json.text || fullText;
      }
    }
  }
  return fullText;
}

/**
 * 打字機效果 fallback（當瀏覽器不支援串流讀取時使用）：
 * 先取得完整回覆，再依固定節奏逐字（分段）顯示，最後仍會呈現完整內容。
 */
function typewriterReveal(fullText, onUpdate, signal) {
  return new Promise((resolve) => {
    const text = fullText || '';
    const chunkSize = Math.max(2, Math.ceil(text.length / 120)); // 依長度動態調整節奏，避免過長文字打字太久
    let idx = 0;
    const timer = setInterval(() => {
      if (signal && signal.aborted) {
        clearInterval(timer);
        resolve();
        return;
      }
      idx = Math.min(text.length, idx + chunkSize);
      if (typeof onUpdate === 'function') onUpdate(text.slice(0, idx));
      if (idx >= text.length) {
        clearInterval(timer);
        resolve();
      }
    }, 16);
  });
}

/**
 * 統一的 AI 動態輸出入口：
 * 1. 首選走 /api/ai stream: true，邊收邊透過 onUpdate 回呼最新累積全文。
 * 2. 若瀏覽器不支援 ReadableStream，fallback 為「先取得完整回覆 + 打字機逐字顯示」。
 * @returns {Promise<string>} 最終完整文字
 */
async function runAIWithDynamicOutput(promptText, sysInstruction, onUpdate, signal) {
  try {
    return await streamOpenAIAPI(promptText, sysInstruction, onUpdate, signal);
  } catch (err) {
    if (err && err.name === 'AbortError') throw err;
    if (err && err.isMissingKey) throw err;
    if (err && err.isStreamUnsupported) {
      const fullText = await callOpenAIAPI(promptText, sysInstruction);
      await typewriterReveal(fullText, onUpdate, signal);
      return fullText;
    }
    throw err;
  }
}

// ------------------------------------------------------------------------
// Markdown 安全渲染：marked.js 轉譯 + DOMPurify 消毒後再 innerHTML
// ------------------------------------------------------------------------
function renderMarkdownSafe(container, text, opts = {}) {
  if (!container) return;
  const raw = text || '';
  let html;
  try {
    if (window.marked && window.DOMPurify) {
      const rawHtml = (typeof window.marked.parse === 'function') ? window.marked.parse(raw) : window.marked(raw);
      html = window.DOMPurify.sanitize(rawHtml);
    } else {
      // marked / DOMPurify 尚未載入完成（例如離線環境 CDN 被擋）時的保底顯示
      html = escapeHTML(raw).replace(/\n/g, '<br>');
    }
  } catch (e) {
    html = escapeHTML(raw).replace(/\n/g, '<br>');
  }
  if (opts.showCursor) {
    html += '<span class="ai-cursor"></span>';
  }
  container.innerHTML = html;
}

if (window.marked && typeof window.marked.setOptions === 'function') {
  window.marked.setOptions({ gfm: true, breaks: true });
}

// ------------------------------------------------------------------------
// 串流／打字機輸出的捲動與重繪輔助工具（AI 對話、單篇摘要 Modal、戰略報告 共用）
//
// 修正重點（原始 bug：AI 產出完成後往上滑會出現空白）：
// 1. 過去每次收到一小段串流內容，就「無條件」把捲軸拉到最底部
//    （chatMessagesEl.scrollTop = chatMessagesEl.scrollHeight），且是在
//    每一個 SSE delta／每 16ms 打字機 tick 都同步觸發一次完整的
//    marked.parse + DOMPurify.sanitize + innerHTML 重繪與強制版面重排(layout)。
//    當這個高頻率（可達每秒 60 次以上）的重排與強制捲動剛好與使用者
//    「主動往上滑」的操作互相搶奪捲動位置與主執行緒時間時，會造成
//    捲動位置被不斷拉回、畫面重繪跟不上捲動位置（layout thrashing），
//    在使用者放開手往上滑那一刻，容器可能停在一個「已捲動、但尚未
//    完整重繪」的中間狀態，看起來就像一片空白。
// 2. 修正方式：
//    a. 用 requestAnimationFrame 將同一輪內多次的串流更新合併為「每個
//       畫面更新一次」，避免同一訊息在極短時間內被重繪與強制重排數十次。
//    b. 是否要自動捲到底，改成「更新前先判斷使用者目前是否已經在底部
//       附近（isNearBottom）」才捲動；只要使用者已主動往上滑（不在底部
//       附近），後續串流更新就不再搶奪捲動位置，內容仍會持續補齊，
//       使用者放開後往上滑也能看到完整、已重繪好的歷史內容。
// ------------------------------------------------------------------------

// 判斷捲動容器目前是否已在（或接近）最底部
function isNearBottom(scrollEl, threshold = 64) {
  if (!scrollEl) return true;
  return (scrollEl.scrollHeight - scrollEl.scrollTop - scrollEl.clientHeight) <= threshold;
}

// 若使用者目前在底部附近，才將捲動容器捲到最底（不打斷使用者主動往上滑的行為）
function scrollToBottomIfNear(scrollEl) {
  if (!scrollEl) return;
  if (isNearBottom(scrollEl)) {
    scrollEl.scrollTop = scrollEl.scrollHeight;
  }
}

/**
 * 建立一個「每個動畫畫面（requestAnimationFrame）最多重繪一次」的串流渲染器，
 * 用於 AI 對話泡泡／摘要 Modal／戰略報告等三處串流輸出區塊。
 * @param {HTMLElement} contentEl 實際承載 markdown 內容的容器（會被 renderMarkdownSafe 寫入）
 * @param {HTMLElement} scrollEl 外層可捲動容器（決定是否需要自動捲到底）
 */
function createStreamRenderer(contentEl, scrollEl) {
  let pendingText = null;
  let pendingCursor = false;
  let rafId = null;

  function flush() {
    rafId = null;
    if (pendingText === null) return;
    const text = pendingText;
    const showCursor = pendingCursor;
    pendingText = null;
    const shouldStick = isNearBottom(scrollEl);
    renderMarkdownSafe(contentEl, text, { showCursor });
    if (shouldStick) scrollToBottomIfNear(scrollEl);
  }

  function update(text, showCursor) {
    pendingText = text;
    pendingCursor = !!showCursor;
    if (rafId === null) {
      rafId = requestAnimationFrame(flush);
    }
  }

  // 取消尚未執行的重繪排程（用於串流結束／中止時，避免舊的排程蓋掉最終完整渲染結果）
  function cancelPending() {
    if (rafId !== null) {
      cancelAnimationFrame(rafId);
      rafId = null;
    }
    pendingText = null;
  }

  return { update, cancelPending };
}

// 共用：AI 呼叫失敗時的錯誤訊息區塊（區分「後端尚未設定認證」與「其他 Gemini 呼叫錯誤」）
// 註：函式名稱 callOpenAIAPI / streamOpenAIAPI 為歷史命名保留（避免變動既有呼叫點），
// 實際內部已改為呼叫本站後端 /api/ai（Google Gemini 代理），詳見上方 Gemini 代理呼叫區註解。
function renderAIErrorHTML(err) {
  if (err && err.isMissingKey) {
    return `
      <div class="p-3 bg-amber-50 text-amber-800 rounded-lg text-xs space-y-2">
        <strong>⚠️ ${escapeHTML(MISSING_API_KEY_MESSAGE)}</strong>
        <p>請通知管理員於伺服器環境變數設定 Gemini／Vertex AI 認證後再試一次（詳見 README-gemini.md）。</p>
        <button onclick="openSettingsModal()" class="mt-1 px-3 py-1.5 bg-amber-600 hover:bg-amber-700 text-white rounded-lg text-[11px] font-medium">查看 AI 設定狀態</button>
      </div>
    `;
  }
  return `
    <div class="p-3 bg-red-50 text-red-700 rounded-lg text-xs">
      <strong>⚠️ AI 解析暫時無法完成（Google Gemini）：</strong><br>
      請確認伺服器端 Gemini／Vertex AI 認證設定是否正確、額度是否足夠，或模型名稱（${escapeHTML(currentAIModelLabel)}）是否有效。<br>
      <em class="text-[10px] text-red-500">錯誤詳情: ${escapeHTML(err && err.message ? err.message : String(err))}</em>
    </div>
  `;
}

// 目前 AI 摘要 / 戰略報告 Modal 共用的串流中止控制器（切換內容或關閉視窗時中止上一個請求）
let currentSummaryAbortController = null;

// --- 1. AI 一鍵摘要功能 ---
async function openAIOption(articleId) {
  const art = articles.find(a => a.id === articleId);
  if (!art) return;

  const modal = document.getElementById('ai-summary-modal');
  const modalContainer = document.getElementById('modal-container');
  const originalTitle = document.getElementById('original-article-title');
  const originalLink = document.getElementById('original-article-link');
  const aiSummaryText = document.getElementById('ai-summary-text');
  const spinner = document.getElementById('modal-loading-spinner');

  if (currentSummaryAbortController) currentSummaryAbortController.abort();
  currentSummaryAbortController = new AbortController();
  const { signal } = currentSummaryAbortController;

  originalTitle.innerText = art.title;
  if (art.url) {
    originalLink.href = art.url;
    originalLink.classList.remove('hidden');
  } else {
    originalLink.classList.add('hidden');
  }
  aiSummaryText.innerHTML = `<span class="text-slate-400 italic">正在連結喬山 AI 智庫決策模型，這將耗時數秒...</span>`;
  spinner.classList.remove('hidden');
  modal.classList.remove('hidden');

  setTimeout(() => {
    modalContainer.classList.remove('scale-95');
    modalContainer.classList.add('scale-100');
  }, 50);

  const prompt = `
    請針對以下健身科技產業情報，為喬山健康科技的高層主管、研發經理及市場分析師，進行『深度核心摘要』與『決策價值評估』。

    情報標題: ${art.title}
    情報分類: ${art.categoryName || CATEGORY_NAME_MAP[art.category] || ''}
    情報日期: ${art.date}
    情報來源: ${art.source}
    相關品牌: ${art.brand || '無特定品牌'}
    情報概要: ${art.summary}
    原文連結: ${art.url || '無'}

    請依據以下架構進行繁體中文的專業解析：
    1. 【核心精華提煉】：一言以蔽之該情報之關鍵意義（30字以內）。
    2. 【競爭衝擊與威脅】：對喬山（Johnson）現有家用及商用產品線有何潛在衝擊（特別是 Matrix,Vision,Bowflex,Horizon,Schwinn 等品牌）。
    3. 【研發創新建議】：喬山 R&D 可以採取哪些實質技術行動或防禦開發？
    4. 【市場商機評估】：業務或行銷端可以如何利用此趨勢做為本地市場之推廣亮點？
  `;
  const sysInstruction = "你是一位專精於全球健身產業（Fitness Industry）、物聯網智慧健身硬體研發、全球商用與家用健身器材市場的 AI 資深產業顧問暨戰略決策專家。";

  const modalScrollEl = document.getElementById('modal-body-content');
  const renderer = createStreamRenderer(aiSummaryText, modalScrollEl);

  try {
    aiSummaryText.innerHTML = '';
    let hasOutput = false;
    const finalText = await runAIWithDynamicOutput(prompt, sysInstruction, (partial) => {
      hasOutput = true;
      renderer.update(partial, true);
    }, signal);
    renderer.cancelPending();
    if (hasOutput) {
      const shouldStick = isNearBottom(modalScrollEl);
      renderMarkdownSafe(aiSummaryText, finalText); // 收尾：移除打字游標，完整重新渲染一次
      if (shouldStick) scrollToBottomIfNear(modalScrollEl);
    } else {
      renderMarkdownSafe(aiSummaryText, finalText || '（AI 未回傳任何內容）');
    }
  } catch (err) {
    renderer.cancelPending();
    if (err && err.name === 'AbortError') return; // 使用者已切換/關閉，靜默中止
    console.error(err);
    aiSummaryText.innerHTML = `
      ${renderAIErrorHTML(err)}
      <div class="mt-4 p-3 bg-slate-100 rounded text-xs text-slate-600">
        <strong>💡 情報本地靜態速覽：</strong><br>
        ${escapeHTML(art.summary || '')}
      </div>
    `;
  } finally {
    spinner.classList.add('hidden');
  }
}

function closeSummaryModal() {
  if (currentSummaryAbortController) {
    currentSummaryAbortController.abort();
    currentSummaryAbortController = null;
  }
  const modal = document.getElementById('ai-summary-modal');
  const modalContainer = document.getElementById('modal-container');
  modalContainer.classList.add('scale-95');
  modalContainer.classList.remove('scale-100');
  setTimeout(() => modal.classList.add('hidden'), 150);
}

function copySummaryText() {
  const text = document.getElementById('ai-summary-text').innerText;
  const dummy = document.createElement("textarea");
  document.body.appendChild(dummy);
  dummy.value = text;
  dummy.select();
  try { document.execCommand("copy"); } catch (e) { /* noop */ }
  document.body.removeChild(dummy);
  showToast("📋 摘要內容已成功複製至剪貼簿！");
}

// --- 2. AI 產業智庫對話模組 ---
function getChatMessagesEl() { return document.getElementById('chat-messages'); }

// 目前對話串流中止控制器（送出新訊息時，會先中止前一個尚未完成的請求）
let currentChatAbortController = null;

function appendMessage(sender, text) {
  const chatMessagesEl = getChatMessagesEl();
  const msgDiv = document.createElement('div');
  msgDiv.className = "flex gap-2.5";

  const isAI = sender === 'AI';
  const avatarBg = isAI ? 'bg-brand/10 text-brand' : 'bg-slate-200 text-slate-700';
  const avatarText = isAI ? '助理' : '我';
  const msgBg = isAI ? 'bg-slate-100 rounded-tl-none text-slate-700' : 'bg-brand text-white rounded-tr-none';
  const formattedText = escapeHTML(text).replace(/\n/g, '<br>');

  msgDiv.innerHTML = `
    <div class="h-8 w-8 rounded-full ${avatarBg} flex items-center justify-center text-[10px] font-bold flex-shrink-0">${avatarText}</div>
    <div class="${msgBg} rounded-2xl p-3 max-w-[85%] leading-relaxed whitespace-pre-wrap">${formattedText}</div>
  `;
  chatMessagesEl.appendChild(msgDiv);
  chatMessagesEl.scrollTop = chatMessagesEl.scrollHeight;
}

/**
 * 建立一個空的 AI 訊息泡泡（用於串流／打字機逐步更新），
 * 回傳內容容器 <div>，呼叫端可反覆呼叫 renderMarkdownSafe(bubble, partialText) 更新。
 */
function createAIMessageBubble() {
  const chatMessagesEl = getChatMessagesEl();
  const msgDiv = document.createElement('div');
  msgDiv.className = "flex gap-2.5";
  msgDiv.innerHTML = `
    <div class="h-8 w-8 rounded-full bg-brand/10 text-brand flex items-center justify-center text-[10px] font-bold flex-shrink-0">助理</div>
    <div class="bg-slate-100 rounded-2xl rounded-tl-none p-3 max-w-[85%] leading-relaxed ai-markdown"></div>
  `;
  chatMessagesEl.appendChild(msgDiv);
  chatMessagesEl.scrollTop = chatMessagesEl.scrollHeight;
  return msgDiv.querySelector('div:last-child');
}

async function sendChatMessage() {
  const input = document.getElementById('chat-input');
  const userText = input.value.trim();
  if (!userText) return;

  if (currentChatAbortController) currentChatAbortController.abort();
  currentChatAbortController = new AbortController();
  const { signal } = currentChatAbortController;

  appendMessage('User', userText);
  input.value = '';

  const chatMessagesEl = getChatMessagesEl();
  const bubble = createAIMessageBubble();
  bubble.innerHTML = `<span class="text-slate-400 italic text-xs">喬山 AI 智庫正在思考中...</span>`;

  // 使用目前真實情報庫（精簡欄位）作為脈絡，避免 prompt 過長
  const contextArticles = articles.slice(0, 30).map(a => ({
    title: a.title, category: a.categoryName || a.category, date: a.date, source: a.source, brand: a.brand, summary: a.summary
  }));
  const context = JSON.stringify(contextArticles);

  const sysInstruction = `
    你是一位極度資深的健身科技專家（Fitness Technology Expert），專為喬山健康科技（Johnson Health Tech）的決策層、工程師與業務團隊服務。
    你掌握全球最大對手包括 Technogym、Peloton、Life Fitness、Precor、NordicTrack 的動態。
    請使用專業、條理清晰的繁體中文，給予針對性極強、可落地執行的商業或技術研發建議。
    若對話提及特定產品線，請與喬山旗下 Matrix,Vision,Bowflex,Horizon,Schwinn 相互串聯。
    若目前情報庫為空，請誠實告知使用者目前尚無真實情報資料可供引用，僅能提供一般性產業知識回答。
  `;
  const prompt = `
    當前喬山 AI 情報看板已載入之真實新聞情報（可能為空陣列）：
    ${context}

    使用者諮詢問題：
    "${userText}"

    請在答覆中，適時引用上方情報中的真實動態（若有），或提出具體且突破性的 R&D 智慧阻尼、馬達控制、穿戴對接或訂閱軟體產品戰略。
  `;

  const renderer = createStreamRenderer(bubble, chatMessagesEl);

  try {
    let hasOutput = false;
    const finalText = await runAIWithDynamicOutput(prompt, sysInstruction, (partial) => {
      hasOutput = true;
      renderer.update(partial, true);
    }, signal);
    renderer.cancelPending();
    if (hasOutput) {
      // 收尾：移除打字游標、完整重新渲染一次。是否捲到底改為「使用者原本就在底部附近」才執行，
      // 避免使用者已主動往上滑閱讀歷史訊息時，被強制拉回最下方。
      const shouldStick = isNearBottom(chatMessagesEl);
      renderMarkdownSafe(bubble, finalText);
      if (shouldStick) scrollToBottomIfNear(chatMessagesEl);
    } else {
      renderMarkdownSafe(bubble, finalText || '（AI 未回傳任何內容）');
    }
  } catch (err) {
    renderer.cancelPending();
    if (err && err.name === 'AbortError') return; // 使用者已送出新訊息，靜默中止舊請求
    bubble.classList.remove('ai-markdown');
    bubble.innerHTML = renderAIErrorHTML(err);
    scrollToBottomIfNear(chatMessagesEl);
  }
}

function handleChatKey(e) {
  if (e.key === 'Enter') sendChatMessage();
}

function sendQuickPrompt(promptText) {
  document.getElementById('chat-input').value = promptText;
  sendChatMessage();
}

function clearChat() {
  getChatMessagesEl().innerHTML = `
    <div class="flex gap-2.5">
      <div class="h-8 w-8 rounded-full bg-brand/10 flex items-center justify-center text-brand text-[10px] font-bold flex-shrink-0">助理</div>
      <div class="bg-slate-100 rounded-2xl rounded-tl-none p-3 max-w-[85%] text-slate-700 leading-relaxed">
        對話紀錄已清除。您隨時可以開始新一輪的喬山 AI 決策諮詢！
      </div>
    </div>
  `;
}

// --- 3. 戰略報告動態生成 ---
async function generateCustomReport() {
  const topic = document.getElementById('report-topic').value;
  const target = document.getElementById('report-target').value;

  const modal = document.getElementById('ai-summary-modal');
  const modalContainer = document.getElementById('modal-container');
  const originalTitle = document.getElementById('original-article-title');
  const originalLink = document.getElementById('original-article-link');
  const aiSummaryText = document.getElementById('ai-summary-text');
  const spinner = document.getElementById('modal-loading-spinner');

  if (currentSummaryAbortController) currentSummaryAbortController.abort();
  currentSummaryAbortController = new AbortController();
  const { signal } = currentSummaryAbortController;

  originalTitle.innerText = `專案：${topic}`;
  originalLink.classList.add('hidden');
  aiSummaryText.innerHTML = `<span class="text-slate-400 italic font-mono animate-pulse">正在精確比對已載入之真實情報，彙整當前喬山開發架構。AI 正為「${target}」撰寫決策報告中，請耐心等候大約 5~10 秒...</span>`;
  spinner.classList.remove('hidden');
  modal.classList.remove('hidden');

  setTimeout(() => {
    modalContainer.classList.remove('scale-95');
    modalContainer.classList.add('scale-100');
  }, 50);

  const contextArticles = articles.slice(0, 30).map(a => `- [${a.date}] (${a.categoryName || a.category}/${a.brand || '一般'}) ${a.title}：${a.summary}`).join('\n');

  const sysInstruction = "你是一位頂尖的麥肯錫（McKinsey）產業戰略顧問，同時也是喬山健康科技聘請的獨立董事暨智慧系統架構師。";
  const prompt = `
    請為以下指定受眾撰寫一份針對特定戰略主題的『喬山 AI 產業決策白皮書/戰略研發簡報』，並盡量引用下方真實情報清單中的內容佐證：

    【真實情報清單】
    ${contextArticles || '（目前情報庫為空，請以一般產業知識撰寫，並提醒讀者待情報資料補齊後應再次生成報告）'}

    【指定主題】：${topic}
    【報告預設受眾】：${target}

    請利用繁體中文，格式化輸出以下內容：
    1. 【前言：當前健身產業智慧化危機與契機】。
    2. 【核心分析架構（SWOT / 價值鏈剖析）】：針對此主題進行精準剖析，指出 Matrix,Vision,Bowflex,Horizon,Schwinn 品牌的核心優劣勢。
    3. 【跨時代研發（R&D）具體落地路線圖】：提供三階段（短、中、長期）的軟硬體研發建議。
    4. 【行銷與商務推廣亮點】。
    5. 【結論與行動呼籲（Call to Action）】。
  `;

  const reportScrollEl = document.getElementById('modal-body-content');
  const renderer = createStreamRenderer(aiSummaryText, reportScrollEl);

  try {
    aiSummaryText.innerHTML = '';
    let hasOutput = false;
    const finalText = await runAIWithDynamicOutput(prompt, sysInstruction, (partial) => {
      hasOutput = true;
      renderer.update(partial, true);
    }, signal);
    renderer.cancelPending();
    if (hasOutput) {
      const shouldStick = isNearBottom(reportScrollEl);
      renderMarkdownSafe(aiSummaryText, finalText); // 收尾：移除打字游標，完整重新渲染一次
      if (shouldStick) scrollToBottomIfNear(reportScrollEl);
    } else {
      renderMarkdownSafe(aiSummaryText, finalText || '（AI 未回傳任何內容）');
    }
  } catch (err) {
    renderer.cancelPending();
    if (err && err.name === 'AbortError') return; // 使用者已切換/關閉，靜默中止
    console.error(err);
    aiSummaryText.innerHTML = `
      ${renderAIErrorHTML(err)}
      <div class="mt-4 p-4 bg-slate-100 rounded text-xs text-slate-700 space-y-2">
        <h5 class="font-bold text-slate-900 text-sm">💡 本地核心決策提示要點 (靜態備份)：</h5>
        <p><strong>智慧硬體創新：</strong> Matrix 應在下一代商用控制台中無縫導入語音 AI 指導與生物阻抗分析，以反制競品 AI Suite 的攻勢。</p>
        <p><strong>軟體訂閱防禦：</strong> 整合 Apple Health 與 Android 穿戴裝置數據，提升設備黏著度與續約率。</p>
      </div>
    `;
  } finally {
    spinner.classList.add('hidden');
  }
}

/* ==========================================================================
 * 頁面載入初始化
 * ========================================================================== */
window.addEventListener('DOMContentLoaded', () => {
  loadData();
  restoreSiteLanguagePreference();
  refreshSettingsModalStatus(); // 頁面載入時先向 /api/ai 查詢目前模型／後端狀態，更新聊天室頭部等處顯示
});
