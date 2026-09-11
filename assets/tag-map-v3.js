/* ==========================================================================
 * TagMapV3 -- v3 標籤映射層（純函式，無 DOM 操作、無 fetch）
 * ------------------------------------------------------------------------
 * 依 docs/v3-spec.md 第 3 節契約實作。所有標籤判定集中在本檔，未來若換成
 * AI 標註或補上 country 真實邏輯，只需要改本檔，不動 app-v3.js。
 *
 * 品牌 / 情境詞判定精神移植自（讀取抄規則，不 import、不修改）：
 *   - assets/data-derive.js:117-147（BRAND_DEFS strongAliases / weakAliases /
 *     excludePhrases 的分層設計）
 *   - scraper/fetch_news.py:380-462（_FIT_CTX 健身情境詞、各歧義品牌的
 *     AND 條件 helper _and()）
 * 本檔依 spec 第 3 節新增 8 個品牌，並依真實資料測試結果調整少數品牌的
 * 把關策略（詳見各品牌註解與交付報告的取捨說明）。
 *
 * 詞界判斷不用 \b，改用 (?:^|[^a-zA-Z0-9])WORD(?:$|[^a-zA-Z0-9]) 手動邊界，
 * 效果等同 \b，對中日韓字元也天然成立邊界（因為中日韓字元本身就不在
 * a-zA-Z0-9 字元集合內，天生就會被視為邊界）。
 * ========================================================================== */

(function (root) {
  'use strict';

  function buildRegex(tokens, phrases) {
    var parts = [];
    if (tokens && tokens.length) {
      parts.push('(?:^|[^a-zA-Z0-9])(?:' + tokens.join('|') + ')(?:$|[^a-zA-Z0-9])');
    }
    if (phrases && phrases.length) {
      parts.push(phrases.join('|'));
    }
    if (!parts.length) return null;
    return new RegExp(parts.join('|'), 'i');
  }

  function articleText(a) {
    return (a && a.title ? a.title : '') + ' ' + (a && a.summary ? a.summary : '');
  }

  var FIT_CTX_TOKENS = [
    'fitness', 'gym', 'treadmill', 'elliptical', 'equipment', 'strength',
    'cardio', 'rower', 'rowing', 'workout', 'exercise', 'machine', 'trainer',
    'weights', 'barbell', 'dumbbell', 'rack', 'kettlebell', 'bike', 'bikes'
  ];
  var FIT_CTX_PHRASES = ['home gym'];
  var FIT_CTX_RE = buildRegex(FIT_CTX_TOKENS, FIT_CTX_PHRASES);

  var BRANDS = [
    'Peloton', 'Technogym', 'NordicTrack', 'Johnson', 'ProForm', 'SHUA',
    'Life Fitness', 'Bowflex', 'REP', 'Matrix', 'Precor', 'Tonal', 'Sole',
    'Concept2', 'Sunny Health', 'Force USA', 'Rogue', 'Schwinn', 'Assault',
    'Vision', 'Hammer Strength', 'Titan', 'Eleiko', 'Horizon', 'TRUE Fitness',
    'Nautilus', 'Cybex', 'Spirit', 'Inspire', 'Dyaco', 'Impulse', 'Keiser',
    'Star Trac', 'CORE Fitness', 'EGYM', 'DRAX'
  ];

  var BRAND_DEFS = [
    { name: 'Peloton', strongTokens: ['peloton'] },
    { name: 'Technogym', strongTokens: ['technogym'] },
    { name: 'NordicTrack', strongTokens: ['nordictrack'], strongPhrases: ['nordic track'] },
    { name: 'Johnson', strongPhrases: ['johnson health tech', 'johnson fitness', '喬山'] },
    { name: 'ProForm', strongTokens: ['proform'], strongPhrases: ['pro-form fitness', 'proform carbon', 'proform ifit'] },
    { name: 'SHUA', strongTokens: ['shua'], strongPhrases: ['舒華', '舒华'] },
    { name: 'Life Fitness', strongPhrases: ['life fitness'] },
    { name: 'Bowflex', strongTokens: ['bowflex'] },
    { name: 'REP', strongPhrases: ['rep fitness'], weakTokens: ['rep'],
      exclude: ['one rep max', 'one-rep max', 'per rep', 'rep range', 'rep count', 'each rep', 'reps and sets', 'sets and reps', 'personal rep', 'sales rep', 'press rep'] },
    { name: 'Matrix', strongPhrases: ['matrix fitness', 'matrix strength'], weakTokens: ['matrix'],
      exclude: ['matrix service', 'the matrix', 'risk matrix', 'decision matrix', 'skills matrix', 'matrix organization'] },
    { name: 'Precor', weakTokens: ['precor'] },
    { name: 'Tonal', weakTokens: ['tonal'],
      exclude: ['tonal shift', 'tonal dressing', 'tonal colorway', 'tonal quality', 'atonal', 'tonal palette', 'tonal range'] },
    { name: 'Sole', strongPhrases: ['sole fitness', 'sole treadmill'], weakTokens: ['sole'] },
    { name: 'Concept2', strongTokens: ['concept2'], strongPhrases: ['concept 2'] },
    { name: 'Sunny Health', strongPhrases: ['sunny health'] },
    { name: 'Force USA', strongPhrases: ['force usa'] },
    { name: 'Rogue', strongPhrases: ['rogue fitness', 'rogue ohio bar'], weakTokens: ['rogue'] },
    { name: 'Schwinn', weakTokens: ['schwinn'] },
    { name: 'Assault', strongPhrases: ['assault fitness', 'assault bike', 'assaultbike', 'assault runner', 'assault air'], weakTokens: ['assault'],
      exclude: ['sexual assault', 'assault charges', 'assault rifle', 'assault weapon', 'assaulted', 'armed assault', 'military assault'] },
    { name: 'Vision', strongPhrases: ['vision fitness'], weakTokens: ['vision'] },
    { name: 'Hammer Strength', strongPhrases: ['hammer strength'] },
    { name: 'Titan', strongPhrases: ['titan fitness'], weakTokens: ['titan'] },
    { name: 'Eleiko', strongTokens: ['eleiko'] },
    { name: 'Horizon', strongPhrases: ['horizon fitness'], weakTokens: ['horizon'] },
    { name: 'TRUE Fitness', strongPhrases: ['true fitness', 'true treadmill', 'true trainer', 'trueform runner', 'true fitness technology'] },
    { name: 'Nautilus', strongPhrases: ['nautilus fitness', 'nautilus inc', 'nautilus, inc'], weakTokens: ['nautilus'],
      exclude: ['nautilus biotechnology', 'patek philippe', 'marine insurance', '(naut)'] },
    { name: 'Cybex', strongPhrases: ['cybex eagle', 'cybex international', 'arc trainer'], weakTokens: ['cybex'] },
    { name: 'Spirit', strongPhrases: ['spirit fitness'], weakTokens: ['spirit'] },
    { name: 'Inspire', strongPhrases: ['inspire fitness'], weakTokens: ['inspire'] },
    { name: 'Dyaco', weakTokens: ['dyaco'] },
    { name: 'Impulse', strongPhrases: ['impulse fitness'], weakTokens: ['impulse'] },
    { name: 'Keiser', weakTokens: ['keiser'] },
    { name: 'Star Trac', strongPhrases: ['star trac'] },
    { name: 'CORE Fitness', strongPhrases: ['core fitness'] },
    { name: 'EGYM', weakTokens: ['egym'] },
    { name: 'DRAX', weakTokens: ['drax'] }
  ];

  BRAND_DEFS.forEach(function (def) {
    def._excludeRe = def.exclude && def.exclude.length ? buildRegex(null, def.exclude) : null;
    def._strongRe = buildRegex(def.strongTokens, def.strongPhrases);
    def._weakRe = def.weakTokens && def.weakTokens.length ? buildRegex(def.weakTokens, null) : null;
  });
  var BRAND_DEF_BY_NAME = {};
  BRAND_DEFS.forEach(function (def) { BRAND_DEF_BY_NAME[def.name] = def; });

  function brandHit(def, text) {
    if (def._excludeRe && def._excludeRe.test(text)) return false;
    if (def._strongRe && def._strongRe.test(text)) return true;
    if (def._weakRe && def._weakRe.test(text) && FIT_CTX_RE.test(text)) return true;
    return false;
  }

  function getBrands(a) {
    var text = articleText(a);
    var hits = [];
    for (var i = 0; i < BRANDS.length; i++) {
      var name = BRANDS[i];
      if (brandHit(BRAND_DEF_BY_NAME[name], text)) hits.push(name);
    }
    if (a && a.brand && BRAND_DEF_BY_NAME[a.brand] && hits.indexOf(a.brand) === -1) {
      hits.push(a.brand);
    }
    hits.sort(function (x, y) { return BRANDS.indexOf(x) - BRANDS.indexOf(y); });
    return hits;
  }

  var PRODUCT_TYPES = ['Cardio', 'Strength', 'Connected Fitness', 'APP', 'Console', 'Wearable', 'Digital Service', 'Web'];

  var PTYPE_KEYWORDS = {
    'Cardio': { tokens: ['treadmill', 'elliptical', 'rower', 'bike'], phrases: ['exercise bike', 'rowing machine'] },
    'Strength': { tokens: ['rack', 'dumbbell', 'barbell', 'plate'], phrases: ['smith machine'] },
    'APP': { tokens: ['app', 'ios', 'android'], phrases: ['mobile app'] },
    'Console': { tokens: ['touchscreen', 'console', 'display'], phrases: ['hd screen'] },
    'Web': { tokens: ['website', 'browser'], phrases: ['web portal'] },
    'Digital Service': { tokens: ['streaming'], phrases: ['subscription platform', 'on-demand class', 'on demand class'] },
    'Connected Fitness': { tokens: ['connected', 'iot'], phrases: ['smart equipment', 'sensor-linked', 'sensor linked'] }
  };
  var PTYPE_RE = {};
  Object.keys(PTYPE_KEYWORDS).forEach(function (k) {
    PTYPE_RE[k] = buildRegex(PTYPE_KEYWORDS[k].tokens, PTYPE_KEYWORDS[k].phrases);
  });
  var DIGITAL_SUB_TYPES = ['APP', 'Console', 'Web', 'Digital Service', 'Connected Fitness'];
  var SUPPLEMENT_TYPES = ['Cardio', 'Strength', 'APP', 'Console', 'Web', 'Digital Service', 'Connected Fitness'];

  function getProductTypes(a) {
    var text = articleText(a);
    var pcs = (a && a.product_categories) || [];
    var result = [];
    function add(name) { if (result.indexOf(name) === -1) result.push(name); }

    if (pcs.indexOf('cardio') !== -1) add('Cardio');
    if (pcs.indexOf('strength') !== -1) add('Strength');
    if (pcs.indexOf('wearable') !== -1) add('Wearable');
    if (pcs.indexOf('digital') !== -1) {
      var matchedAny = false;
      DIGITAL_SUB_TYPES.forEach(function (name) {
        if (name === 'Digital Service') return;
        if (PTYPE_RE[name] && PTYPE_RE[name].test(text)) { add(name); matchedAny = true; }
      });
      if (PTYPE_RE['Digital Service'] && PTYPE_RE['Digital Service'].test(text)) { add('Digital Service'); matchedAny = true; }
      if (!matchedAny) add('Digital Service');
    }
    SUPPLEMENT_TYPES.forEach(function (name) {
      if (PTYPE_RE[name] && PTYPE_RE[name].test(text)) add(name);
    });

    result.sort(function (x, y) { return PRODUCT_TYPES.indexOf(x) - PRODUCT_TYPES.indexOf(y); });
    return result;
  }

  var TOPICS = [
    '新品發布', '產品改版', 'AI 功能', '個人化推薦', '訓練計畫', '數據追蹤', '裝置串接',
    '第三方整合', '訂閱方案', '市場擴張', '新品宣傳', '品牌 Campaign', '品牌合作', '代言人',
    '產品賣點', '品牌定位', '價格策略', '促銷活動', '內容策略', '市場趨勢', '其他'
  ];

  var SUBCATEGORY_TOPIC_MAP = {
    product_launch: ['新品發布'],
    product_line: ['新品發布'],
    ai_training: ['AI 功能'],
    connected_app: ['裝置串接', '第三方整合'],
    wearable_device: ['裝置串接'],
    product_review: ['產品賣點'],
    training_science: ['訓練計畫'],
    wellness_trend: ['市場趨勢'],
    home_fitness_trend: ['市場趨勢'],
    market_research: ['市場趨勢'],
    channel_partnership: ['市場擴張'],
    commercial_channel: ['市場擴張'],
    international_brand: ['市場擴張'],
    china_brand: ['市場擴張'],
    brand_risk: ['品牌定位']
  };

  var TOPIC_KEYWORDS = {
    '新品發布': { tokens: ['launches', 'launch', 'unveils', 'debuts', 'introduces'], phrases: ['new product', 'product line', '上市', '推出', '發表', '新品'] },
    '產品改版': { tokens: ['upgraded', 'updated', 'redesigned', 'revamp', 'refresh'], phrases: ['new version', 'next generation', 'next-gen', '升級', '改版', '改款'] },
    'AI 功能': { tokens: ['ai'], phrases: ['ai coach', 'artificial intelligence', 'generative ai', 'machine learning', 'ai-powered', 'ai analysis', 'smart algorithm', 'ai 教練', '人工智慧', 'ai 功能'] },
    '個人化推薦': { tokens: ['personalized', 'personalization', 'recommends'], phrases: ['personalized plan', 'tailored workout', 'custom workout', 'recommendation engine', 'personalized experience', 'tailored recommendations', 'ai recommendations', 'curated for you', '個人化', '推薦', '客製化', '智慧推薦', '量身打造'] },
    '訓練計畫': { tokens: [], phrases: ['training program', 'workout plan', 'training plan', 'program design', 'coaching plan', 'structured training', '訓練計畫', '課程規劃'] },
    '數據追蹤': { tokens: ['analytics'], phrases: ['workout data', 'health data', 'recovery data', 'biometric data', 'data tracking', 'performance metrics', 'progress tracking', 'activity tracking', 'health metrics', 'fitness data', '數據追蹤', '健康數據', '生理數據', '追蹤數據'] },
    '裝置串接': { tokens: ['sensor'], phrases: ['device integration', 'connected device', 'smart sensor', 'pairs with', 'syncs with', '裝置串接', '感測器'] },
    '第三方整合': { tokens: ['strava'], phrases: ['apple health', 'google fit', 'third-party integration', 'api integration', 'integrates with', '第三方整合', '串接'] },
    '訂閱方案': { tokens: ['membership', 'subscription'], phrases: ['monthly plan', 'annual plan', 'subscription fee', 'subscription service', 'tiered pricing', 'free trial', 'premium tier', 'subscriber base', '訂閱', '會員方案', '免費試用', '付費會員'] },
    '市場擴張': { tokens: ['expansion'], phrases: ['new market', 'expands into', 'new country', 'opens in', 'enters market', 'new distributor', '市場擴張', '進軍', '拓展'] },
    '新品宣傳': { tokens: [], phrases: ['launch event', 'unveiling event', 'product reveal', 'promotional launch', 'marketing launch', 'teaser campaign', 'marketing push', 'launch marketing', 'pr campaign', 'media blitz', 'press tour', 'rolling out', '宣傳', '造勢', '宣傳活動', '造勢活動'] },
    '品牌 Campaign': { tokens: [], phrases: ['ad campaign', 'marketing campaign', 'brand campaign', 'advertising campaign', 'campaign launch', 'brand awareness', 'commercial spot', 'tv spot', '廣告活動', '品牌活動', '品牌廣告', '形象廣告'] },
    '品牌合作': { tokens: ['collaboration'], phrases: ['collaborates with', 'teams up with', 'co-branded', 'joint venture', 'partnership announcement', 'strategic partnership', 'brand collaboration', 'cross-promotion', '合作', '聯名', '策略合作', '異業合作'] },
    '代言人': { tokens: ['ambassador', 'endorsement', 'spokesperson', 'influencer'], phrases: ['athlete partnership', 'celebrity endorsement', '代言', '大使'] },
    '產品賣點': { tokens: [], phrases: ['key feature', 'standout feature', 'unique selling point', 'core feature', 'sets it apart', '賣點', '特色'] },
    '品牌定位': { tokens: ['rebrand', 'repositioning', 'positioning'], phrases: ['brand positioning', 'brand identity', 'target audience', 'positions itself', 'brand strategy', 'brand image', 'market positioning', '品牌定位', '重新定位', '品牌策略', '品牌形象'] },
    '價格策略': { tokens: [], phrases: ['price increase', 'price cut', 'pricing strategy', 'price drop', 'priced at', 'price point', 'msrp', 'raises prices', 'lowers prices', 'value proposition', '漲價', '降價', '定價策略', '售價', '調漲', '調降'] },
    '促銷活動': { tokens: ['discount', 'promo', 'promotion', 'coupon'], phrases: ['black friday', 'holiday sale', 'limited time offer', 'flash sale', 'clearance sale', 'special offer', '折扣', '促銷', '優惠', '特賣', '清倉'] },
    '內容策略': { tokens: [], phrases: ['content strategy', 'video content', 'social media content', 'on-demand content', 'class library', 'workout video', 'video series', 'content library', 'digital content', 'streaming content', '內容策略', '影音內容', '內容行銷', '影片系列'] },
    '市場趨勢': { tokens: ['trend'], phrases: ['industry trend', 'market trend', 'consumer demand', 'growing demand', 'market outlook', '市場趨勢', '產業趨勢'] }
  };
  var TOPIC_RE = {};
  Object.keys(TOPIC_KEYWORDS).forEach(function (k) {
    TOPIC_RE[k] = buildRegex(TOPIC_KEYWORDS[k].tokens, TOPIC_KEYWORDS[k].phrases);
  });

  function getTopics(a) {
    var text = articleText(a);
    var result = [];
    function add(name) { if (result.indexOf(name) === -1) result.push(name); }

    var subMapped = a && a.subcategory ? SUBCATEGORY_TOPIC_MAP[a.subcategory] : null;
    if (subMapped) subMapped.forEach(add);

    Object.keys(TOPIC_RE).forEach(function (name) {
      if (TOPIC_RE[name].test(text)) add(name);
    });

    if (!result.length) return ['其他'];
    result.sort(function (x, y) { return TOPICS.indexOf(x) - TOPICS.indexOf(y); });
    return result;
  }

  var ATTENTION = ['Commercial', 'Home', 'Digital'];

  var COMMERCIAL_BRANDS = [
    'Johnson', 'Matrix', 'Life Fitness', 'Technogym', 'Precor', 'TRUE Fitness',
    'CORE Fitness', 'EGYM', 'Hammer Strength', 'SHUA', 'Concept2', 'Rogue',
    'Spirit', 'Star Trac', 'Cybex', 'Vision', 'Eleiko', 'Keiser', 'Assault',
    'Impulse', 'Dyaco', 'DRAX'
  ];
  var HOME_BRANDS = [
    'Peloton', 'NordicTrack', 'Bowflex', 'ProForm', 'Tonal', 'Sole', 'Schwinn',
    'Horizon', 'REP', 'Force USA', 'Sunny Health', 'Inspire', 'Titan', 'Nautilus'
  ];

  var brandSegmentMap = {};
  COMMERCIAL_BRANDS.forEach(function (b) { brandSegmentMap[b] = 'Commercial'; });
  HOME_BRANDS.forEach(function (b) { brandSegmentMap[b] = 'Home'; });

  var DIGITAL_PTYPES = ['APP', 'Console', 'Web', 'Digital Service', 'Connected Fitness'];
  var DIGITAL_TOPICS = ['AI 功能', '數據追蹤', '第三方整合', '裝置串接', '訂閱方案', '內容策略'];

  function getAttention(a) {
    var result = [];
    function add(name) { if (result.indexOf(name) === -1) result.push(name); }

    getBrands(a).forEach(function (b) {
      var seg = brandSegmentMap[b];
      if (seg) add(seg);
    });

    var ptypes = getProductTypes(a);
    var topics = getTopics(a);
    var pcs = (a && a.product_categories) || [];
    var isDigital = DIGITAL_PTYPES.some(function (t) { return ptypes.indexOf(t) !== -1; }) ||
      DIGITAL_TOPICS.some(function (t) { return topics.indexOf(t) !== -1; }) ||
      pcs.indexOf('digital') !== -1;
    if (isDigital) add('Digital');

    result.sort(function (x, y) { return ATTENTION.indexOf(x) - ATTENTION.indexOf(y); });
    return result;
  }

  var COUNTRIES = ['全部', '全球', '亞洲', '北美', '歐洲', '大洋洲', '其他地區'];

  var ASIA_TLDS = ['.tw', '.cn', '.hk', '.jp', '.kr', '.my', '.vn', '.in'];
  var EUROPE_TLDS = ['.uk', '.de', '.fi', '.lt', '.eu'];
  var OCEANIA_TLDS = ['.au', '.nz', '.fj'];
  var NA_TLDS = ['.us'];
  var CJK_RE = /[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]/;

  function endsWithAny(str, suffixes) {
    for (var i = 0; i < suffixes.length; i++) {
      if (str.length >= suffixes[i].length && str.slice(-suffixes[i].length) === suffixes[i]) return true;
    }
    return false;
  }

  function getHostname(url) {
    if (!url) return '';
    try {
      return new URL(url).hostname.toLowerCase();
    } catch (e) {
      return '';
    }
  }

  function getCountry(a) {
    var hostname = getHostname(a && (a.resolved_url || a.url));
    if (hostname) {
      if (endsWithAny(hostname, ASIA_TLDS)) return '亞洲';
      if (endsWithAny(hostname, EUROPE_TLDS)) return '歐洲';
      if (endsWithAny(hostname, OCEANIA_TLDS)) return '大洋洲';
      if (endsWithAny(hostname, NA_TLDS)) return '北美';
    }
    if (a && a.title && CJK_RE.test(a.title)) return '亞洲';
    return '其他地區';
  }

  var AUDIENCES = ['PM', 'Design', 'Marketing'];

  function getAudiences(a) {
    return (a && a.audience_tags) ? a.audience_tags.slice() : [];
  }

  var TOPIC_LABEL_EN = {
    '新品發布': 'New Product Launch', '產品改版': 'Product Update', 'AI 功能': 'AI Features',
    '個人化推薦': 'Personalized Recommendations', '訓練計畫': 'Training Programs',
    '數據追蹤': 'Data Tracking', '裝置串接': 'Device Integration', '第三方整合': 'Third-Party Integration',
    '訂閱方案': 'Subscription Plans', '市場擴張': 'Market Expansion', '新品宣傳': 'Product Marketing',
    '品牌 Campaign': 'Brand Campaign', '品牌合作': 'Brand Partnership', '代言人': 'Endorsement',
    '產品賣點': 'Product USP', '品牌定位': 'Brand Positioning', '價格策略': 'Pricing Strategy',
    '促銷活動': 'Promotions', '內容策略': 'Content Strategy', '市場趨勢': 'Market Trends', '其他': 'Other'
  };
  var PTYPE_LABEL_ZH = {
    'Cardio': '有氧', 'Strength': '力量', 'Connected Fitness': '智能連網', 'APP': 'App',
    'Console': '主機螢幕', 'Wearable': '穿戴裝置', 'Digital Service': '數位服務', 'Web': '網頁'
  };
  var ATTENTION_LABEL_ZH = { 'Commercial': '商用', 'Home': '家用', 'Digital': '軟體' };
  var COUNTRY_LABEL_EN = {
    '全部': 'All', '全球': 'Global', '亞洲': 'Asia', '北美': 'North America',
    '歐洲': 'Europe', '大洋洲': 'Oceania', '其他地區': 'Other Region'
  };

  function labelOf(kind, key, lang) {
    var isEn = lang === 'en';
    if (kind === 'topic') return isEn ? (TOPIC_LABEL_EN[key] || key) : key;
    if (kind === 'ptype') return isEn ? key : (PTYPE_LABEL_ZH[key] || key);
    if (kind === 'attention') return isEn ? key : (ATTENTION_LABEL_ZH[key] || key);
    if (kind === 'country') return isEn ? (COUNTRY_LABEL_EN[key] || key) : key;
    if (kind === 'audience') return key;
    return key;
  }

  root.TagMapV3 = {
    BRANDS: BRANDS,
    PRODUCT_TYPES: PRODUCT_TYPES,
    TOPICS: TOPICS,
    COUNTRIES: COUNTRIES,
    ATTENTION: ATTENTION,
    AUDIENCES: AUDIENCES,
    brandSegmentMap: brandSegmentMap,
    getBrands: getBrands,
    getProductTypes: getProductTypes,
    getTopics: getTopics,
    getAttention: getAttention,
    getCountry: getCountry,
    getAudiences: getAudiences,
    labelOf: labelOf
  };
})(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this));
