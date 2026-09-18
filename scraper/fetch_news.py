# -*- coding: utf-8 -*-
"""
健身產業情報爬蟲 v7.1（2026-09-18）
預設輸出 ../data/news.json；沿用 Google News RSS / 官方 newsroom 與 blog。
本版：所有來源補抓原文摘要、可續跑修復、20 主題/8 產品/36 品牌分類。
summary 與 resolved_url 維持前端契約；未知資訊不造假，空陣列代表證據不足。
一般排程預設補抓最多 100 篇、600 秒；完整指令見 --help 與 README.md。
"""

import argparse
import base64
import json
import os
import random
import re
import sys
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse

import feedparser
import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR.parent / "data"
OUTPUT_FILE = DATA_DIR / "news.json"
LOG_FILE = BASE_DIR / "scrape_log.txt"
LOCK_FILE = BASE_DIR / "fetch_news.lock"
LOCK_STALE_SECONDS = 2 * 60 * 60  # 2 小時後視為 stale lock

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 "
    "FitnessIntelBot/1.0 (+contact: rokcodeteam@gmail.com)"
)

REQUEST_TIMEOUT = 15  # 秒（Google News 用，維持 retry）
MAX_RETRIES = 3
SLEEP_MIN = 0.5
SLEEP_MAX = 1.0
# 官方站 stories/blog/news 頁與 feed：快速失敗設定
# (連線逾時 5 秒 / 讀取逾時 8 秒 / 不重試，失敗立即略過)
OFFICIAL_CONNECT_TIMEOUT = 5
# 2026-09-14：8 秒過嚴（實測 Life Fitness TTFB 在 1.60-2.25s 間波動 40%、
# Johnson 1.85s），網路稍慢就讓整個品牌歸零。放寬到 12 秒並允許重試 1 次，
# 同時以 OFFICIAL_TOTAL_BUDGET_SECONDS 限制整個官方階段的總耗時避免惡化。
OFFICIAL_READ_TIMEOUT = 12
OFFICIAL_TIMEOUT = (OFFICIAL_CONNECT_TIMEOUT, OFFICIAL_READ_TIMEOUT)
# 只對「暫時性錯誤」（逾時／連線中斷）重試，HTTP 4xx/5xx 一律不重試。
OFFICIAL_MAX_RETRIES = 1
# 官方來源階段的全域時間預算（秒）。超過即略過所有剩餘官方來源，
# 避免放寬逾時後最壞情況把每日排程整體拖長。
OFFICIAL_TOTAL_BUDGET_SECONDS = 180
# 由 run() 在進入官方來源階段前設定為 time.monotonic() + 預算；None 表示未設限。
_official_deadline = None
# 官方站之間的 sleep（快速，避免整體拖慢）
PROBE_SLEEP_MIN = 0.2
PROBE_SLEEP_MAX = 0.5

# 日期上限：僅用來過濾「本次新抓進來」的過舊項目（不影響既有庫存）。
MAX_ARTICLE_AGE_DAYS = 740

RUN_DATE = datetime.now(timezone.utc).strftime("%Y-%m-%d")

# ---------------------------------------------------------------------------
# Google News RSS 查詢（以程式產生大量查詢）
# ---------------------------------------------------------------------------

_GN_EN = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
_GN_TW = "https://news.google.com/rss/search?q={q}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
# 中國地區（zh-CN / gl=CN）：使用者指定新增之來源沿用此樣板（不含 ceid，與原始 URL 一致）
_GN_CN = "https://news.google.com/rss/search?q={q}&hl=zh-CN&gl=CN"


def _en(query: str) -> str:
    return _GN_EN.format(q=quote(query))


def _tw(query: str) -> str:
    return _GN_TW.format(q=quote(query))


def _cn(query: str) -> str:
    return _GN_CN.format(q=quote(query))


# 27 個追蹤品牌（＋自家 Johnson）的查詢用名稱
BRAND_QUERY_NAMES = [
    "Life Fitness", "Technogym", "Precor", "Matrix Fitness", "Vision Fitness",
    "Hammer Strength", "Cybex Fitness", "Star Trac", "TRUE Fitness", "Nautilus",
    "SHUA Fitness", "NordicTrack", "Peloton", "Sole Fitness", "Bowflex", "Schwinn",
    "Horizon Fitness", "Tonal Fitness", "ProForm Fitness", "Sunny Health Fitness",
    "Rogue Fitness", "REP Fitness", "Force USA", "Titan Fitness", "Eleiko Fitness",
    "Concept2 Fitness", "Assault Fitness", "Johnson Health Tech",
    "Spirit Fitness", "Inspire Fitness", "Dyaco", "Impulse Fitness", "Keiser Fitness",
    "Core Health Fitness", "EGYM", "DRAX Fitness",
]

PUBLIC_BRANDS = [
    "Peloton", "Nautilus", "BowFlex", "Technogym", "Life Fitness",
    "Matrix", "Precor",
]

LOW_VOLUME_BRANDS = [
    "Star Trac Fitness", "Cybex Fitness", "Hammer Strength", "TRUE Fitness", "Vision Fitness",
    "Force USA", "Assault Fitness", "Eleiko Fitness", "Concept2 Fitness", "Sunny Health Fitness",
    "REP Fitness", "Titan Fitness", "Precor", "Matrix Fitness",
]

GENERIC_QUERIES = [
    "treadmill", "elliptical", "rowing machine", "exercise bike",
    "stationary bike", "home gym", "strength training equipment",
    "commercial gym equipment", "connected fitness", "smart home gym",
    "cardio equipment", "fitness equipment market", "gym equipment",
    "fitness equipment", "functional trainer", "power rack",
]

# 「舒華體育」加雙引號做 phrase match：Google News 對未加引號的中文查詢不會強制
# 詞組比對，過去曾造成「舒華」單獨比對命中南韓女團 (G)I-DLE 成員「葉舒華」的
# 演藝新聞（見 CELEBRITY_NOISE_PATTERN / BRAND_WHITELIST_CONTEXT 二次過濾）。
CHINESE_QUERIES = [
    '"舒華體育"', "岱宇 Dyaco", "喬山 健身", "有氧器材", "重訓器材",
    "跑步機", "健身器材", "橢圓機", "飛輪車",
]

# 使用者指定新增之中國地區（zh-CN / gl=CN）來源查詢，7 天時窗。
# 第一條沿用「舒華體育」精確詞（同樣加引號避免歧義雜訊）；
# 第二條為大陸健身器材品牌群組查詢（OR 群組）。
CN_REGION_QUERIES = [
    '"舒華體育" when:30d',
    "(英派斯 OR 万年青 OR 麦瑞克 OR 亿健 OR DHZ健身) when:30d",
]

# 新品發布導向查詢模板（每品牌 x4）
PRODUCT_QUERY_TEMPLATES = [
    '"{name}" new product',
    '"{name}" launch',
    '"{name}" unveils',
    '"{name}" releases',
]

# 產業新品/新聞稿導向查詢（標記 press_release）
PRESS_RELEASE_QUERIES = [
    "fitness equipment launch",
    "new treadmill launch",
    "new gym equipment",
    "fitness equipment new product",
    "home gym equipment launch",
    "commercial fitness equipment launch",
]

# 直接抓取的產業新聞稿 / 產業媒體 feed（source_type=press_release）
PRESS_RELEASE_FEEDS = [
    {"url": "https://athletechnews.com/feed/", "source": "Athletech News"},
]


def build_feed_sources():
    """回傳 list[dict]，每筆：{url, source_type, brand(可None), source(可None)}"""
    sources = []
    seen = set()

    def add(url, source_type, brand=None, source=None):
        key = (url, source_type)
        if key in seen:
            return
        seen.add(key)
        sources.append({"url": url, "source_type": source_type,
                        "brand": brand, "source": source})

    # --- 既有 Google News 查詢（source_type=google_news）---
    for name in BRAND_QUERY_NAMES:
        add(_en(f'"{name}" when:1y'), "google_news")
        add(_en(f'{name} fitness when:1y'), "google_news")
        add(_en(f'{name} treadmill when:1y'), "google_news")
        add(_en(f'{name} equipment when:1y'), "google_news")

    for name in PUBLIC_BRANDS:
        add(_en(f'{name} stock when:1y'), "google_news")
        add(_en(f'{name} earnings when:1y'), "google_news")

    for name in LOW_VOLUME_BRANDS:
        add(_en(f'"{name}" when:2y'), "google_news")

    for q in GENERIC_QUERIES:
        add(_en(f'{q} when:1y'), "google_news")

    for q in CHINESE_QUERIES:
        add(_tw(f'{q} when:1y'), "google_news")

    # --- 中國地區來源（zh-CN / gl=CN，使用者指定新增）---
    for q in CN_REGION_QUERIES:
        add(_cn(q), "google_news")

    # --- 新品發布導向查詢：這些本質是 Google News 的「新品發表新聞」，
    #     一律標記 source_type=google_news（不再使用 product 這個類別）。---
    for name in BRAND_QUERY_NAMES:
        for tmpl in PRODUCT_QUERY_TEMPLATES:
            add(_en(tmpl.format(name=name) + " when:1y"), "google_news")

    # --- 產業新聞稿導向查詢（source_type=press_release）---
    for q in PRESS_RELEASE_QUERIES:
        add(_en(f'{q} when:1y'), "press_release")

    # --- 直接抓取的產業新聞稿 feed ---
    for f in PRESS_RELEASE_FEEDS:
        add(f["url"], "press_release", source=f["source"])

    return sources


# ---------------------------------------------------------------------------
# 品牌官方「故事 / 文章列表頁」設定（stories / blog / news / press / insights）
# ---------------------------------------------------------------------------
#
# key 為品牌 canonical 名稱（需與 detect_brand 輸出一致）。
# feeds：已知可用的 RSS/Atom feed（優先、快速失敗、不重試）。
# pages：品牌「故事/文章列表頁」URL（以 Technogym stories 頁為範本）。
#        程式會解析列表頁中「真正的文章連結」，並過濾導覽/分類/選單雜訊。
# 全部採「快速失敗」：連線 5 秒 / 讀取 8 秒 / 不重試；任一失敗立即略過並記 log。
# 找到第一個有實際文章的來源即採用，避免對單一品牌打太多請求。
# ---------------------------------------------------------------------------

# 文章區段標記：文章連結路徑需包含其一（且更深一層）
ARTICLE_SECTION_PATTERN = re.compile(
    r"/(stories|story|blog|blogs|news|newsroom|press|press-release|"
    r"article|articles|insights|learn|resources|magazine|journal)/",
    re.IGNORECASE,
)

# 導覽/分類/選單/購物 路徑：命中即排除（非文章）
NAV_EXCLUDE_PATTERN = re.compile(
    r"/(category|categories|tag|tags|author|authors|page|collections|"
    r"collection|product|products|shop|cart|account|login|policies|privacy|"
    r"terms|contact|search|room-planner|compare|sale)(/|$|\.html)",
    re.IGNORECASE,
)

# 明顯為選單/分類名詞的標題（用於既有庫存清理與新抓過濾的保守黑名單）
CATEGORY_NOUN_TITLES = {
    "room planner", "multi family housing", "dumbbells & kettlebells",
    "flexibility & stretching", "cardio", "strength", "free weights",
    "accessories", "benches & racks", "functional training",
    "treadmills", "ellipticals", "exercise bikes", "rowers", "home gym",
    "commercial", "residential", "shop all", "view all", "read more",
    "learn more", "stories", "news", "blog", "press", "newsroom",
}

BRAND_STORY_SOURCES = {
    # 2026-09-14：改用正式 newsroom（實測 200、server-rendered、h1="Newsroom"，
    # 可取得真實新聞稿；文章路徑為 /en-us/about/newsroom/<slug>，會由
    # ARTICLE_SECTION_PATTERN 的 /newsroom/ 標記命中）。原 education-hub/blog 為衛教
    # 內容，非消息型新聞。此來源標題自帶「Life Fitness / Hammer Strength」雙品牌。
    "Life Fitness": {
        "feeds": [],
        "pages": ["https://www.lifefitness.com/en-us/newsroom"]},
    "Technogym": {
        "feeds": [],
        "pages": ["https://www.technogym.com/en-US/stories/"]},
    "Precor": {
        "feeds": [],
        "pages": ["https://www.precor.com/en-US/blog"]},
    "Matrix": {
        "feeds": [],
        "pages": ["https://www.matrixfitness.com/en/blog"]},
    "Vision": {
        "feeds": [],
        "pages": ["https://www.visionfitness.com/zht/insights"]},
    "CORE Fitness": {
        "feeds": [],
        "pages": ["https://www.corehandf.com/blogs/shop-hs"]},
    "TRUE Fitness": {
        "feeds": ["https://truefitness.com/feed/"],
        "pages": ["https://truefitness.com/blog/"]},

    "SHUA": {
        "feeds": [],
        "pages": ["https://shuafitness.com/news/all/"]},
    "NordicTrack": {
        "feeds": [],
        "pages": ["https://www.nordictrack.com/learn"]},
    "Peloton": {
        "feeds": [],
        "pages": ["https://www.onepeloton.com/press",
                  "https://www.onepeloton.com/blog"]},
    "Sole": {
        "feeds": ["https://www.soletreadmills.com/blogs/news.atom"],
        "pages": ["https://www.soletreadmills.com/blogs/news/"]},
    "Bowflex": {
        "feeds": [],
        "pages": ["https://www.bowflex.com/blog/"]},
    "Schwinn": {
        "feeds": [],
        "pages": ["https://www.schwinnfitness.com/blog"]},
    # 註：Horizon 已於 2026-09-14 移除——實測 horizonfitness.com/blog 會 302 到
    # johnsonfitness.com/blog/，與下方 Johnson 來源重複，保留只會多打一次請求。
    "Tonal": {
        "feeds": ["https://www.tonal.com/blogs/all.atom"],
        "pages": ["https://www.tonal.com/blog/"]},
    "ProForm": {
        "feeds": [],
        "pages": ["https://www.proform.com/blog"]},
    "Sunny Health": {
        "feeds": [],
        "pages": ["https://sunnyhealthfitness.com/blogs/index"]},
    "Rogue": {
        "feeds": [],
        "pages": ["https://www.roguefitness.com/the-index"]},
    "REP": {
        "feeds": [],
        "pages": ["https://repfitness.com/pages/blogs"]},
    "Titan": {
        "feeds": [],
        "pages": ["https://titan.fitness/blogs/all-articles"]},
    "Eleiko": {
        "feeds": [],
        "pages": ["https://eleiko.com/en/news"]},
    "Concept2": {
        "feeds": [],
        "pages": ["https://www.concept2.com/blog"]},
    "Assault": {
        "feeds": [],
        "pages": ["https://assaultfitness.com/blogs/news"]},
    "Johnson": {
        "feeds": ["https://www.johnsonfitness.com/blog/feed/"],
        "pages": ["https://www.johnsonfitness.com/blog/"]},
}

# 官方來源顯示用 source 名稱
BRAND_OFFICIAL_SOURCE = {name: f"{name} 官方" for name in BRAND_STORY_SOURCES}

# ---------------------------------------------------------------------------
# 分類 / 品牌 規則
# ---------------------------------------------------------------------------

_FIT_CTX = r"(fitness|treadmill|elliptical|bike|bikes|gym|equipment|strength|cardio|rower|rowing|home gym|workout|exercise)"

# ---------------------------------------------------------------------------
# 品牌 vs 跨產業同名詞辨識：AND 條件 helper
# ---------------------------------------------------------------------------
# 部分品牌詞與其他產業（嬰兒用品、天文攝影、汽車零件、同名連鎖、人名等）同名，
# 裸字 regex 會誤判。以下 helper 產生「品牌詞 AND 情境詞（不限順序、不要求相鄰）」
# 的 regex 字串，用於降低誤判同時盡量保留真品牌召回率。
# ---------------------------------------------------------------------------


def _and(brand_regex: str, ctx: str) -> str:
    return rf"(?:{brand_regex}.*{ctx}|{ctx}.*{brand_regex})"


# 各歧義品牌的情境詞（刻意排除過於寬泛、容易與同名產業/連鎖巧合的詞，如 Nautilus/TRUE
# Fitness 不用裸字 "gym"，避免命中同名健身房連鎖）
_CYBEX_CTX = (r"(fitness|international|eagle|smart|vr3|prestige|bravo|squat|treadmill|"
              r"elliptical|strength|gym|cardio|trainer)")
_PROFORM_CTX = (r"(fitness|treadmill|elliptical|gym|equipment|strength|cardio|rower|rowing|"
                r"home gym|workout|exercise|carbon|ifit|tour|cadence|studio|exercise bike|"
                r"stationary bike|spin bike)")
_TRUEFIT_CTX = (r"(treadmill|elliptical|rower|rowing machine|equipment|commercial fitness|"
                r"cardio equipment|strength equipment|home gym equipment|technology|"
                r"st\. louis|\bforce\b|manufacturer)")
_SCHWINN_CTX = (r"(fitness|treadmill|elliptical|gym|equipment|strength|cardio|indoor cycling|"
                r"exercise bike|spin bike|airdyne|ic\d|recumbent bike|upright bike|home gym|"
                r"workout|exercise)")
_STARTRAC_CTX = (r"(fitness|treadmill|elliptical|gym|equipment|strength|cardio|rower|rowing|"
                 r"home gym|workout|exercise|core health)")
_NAUTILUS_CTX = (r"(bowflex|schwinn|nautilus, inc|nautilus inc|treadmill|elliptical|"
                 r"home gym equipment|strength equipment|cardio equipment|fitness equipment|"
                 r"commercial fitness)")
_PELOTON_CTX = (r"(fitness|treadmill|elliptical|bike|bikes|gym|equipment|strength|cardio|"
                r"rower|rowing|home gym|workout|exercise|interactive|instructor|subscriber|"
                r"subscribers|app|tread|nyse|pton|earnings|revenue|stock|shares|ceo|"
                r"membership|studio|class|classes)")

BRAND_DETECT_PATTERNS = [
    ("Life Fitness", [r"\blife fitness\b"]),
    ("Technogym", [r"technogym"]),
    ("Precor", [r"precor"]),
    ("Matrix", [rf"matrix {_FIT_CTX}", r"matrix strength"]),
    ("Vision", [rf"vision {_FIT_CTX}"]),
    ("Hammer Strength", [r"hammer strength"]),
    ("Cybex", [_and(r"cybex", _CYBEX_CTX), r"cybex eagle", r"cybex international",
               r"arc trainer"]),
    ("Star Trac", [r"\bstar trac\b", _and(r"star trac", _STARTRAC_CTX)]),
    ("TRUE Fitness", [_and(r"true fitness", _TRUEFIT_CTX), r"true treadmill",
                       r"true trainer", r"trueform runner", r"true fitness technology"]),
    ("Nautilus", [_and(r"nautilus", _NAUTILUS_CTX), r"nautilus inc", r"nautilus, inc",
                  r"nautilus bowflex", r"nautilus\b.*\b(bowflex|schwinn|treadmill|home gym)"]),
    ("SHUA", [r"\bshua\b", r"舒華", r"舒华"]),
    ("NordicTrack", [r"nordictrack", r"nordic track"]),
    ("Peloton", [r"\bpeloton\b"]),  # 2026-07-30: 辨識度高，唯一歧義(環法主集團)由 CROSS_INDUSTRY_NOISE 攔截
    ("Sole", [r"sole fitness", r"sole treadmill", rf"sole {_FIT_CTX}",
              r"\bsole [ef]\d\d\b"]),
    ("Bowflex", [r"bowflex"]),
    ("Schwinn", [_and(r"schwinn", _SCHWINN_CTX)]),
    ("Horizon", [rf"horizon {_FIT_CTX}"]),
    # Tonal（智慧重訓器材）與英文常用字 "tonal"（色調/劇情基調/配色）同名，實測
    # null-brand 子集 43 筆命中中 23 筆為真品牌漏標。裸字偵測 + BRAND_WHITELIST_CONTEXT
    # 二次把關（見下方字典），比原本要求特定詞組更能召回「Tonal Appoints ...」等純公司
    # 動態新聞，同時靠情境詞白名單擋掉 "tonal shift/dressing/colorway" 等色調用法。
    ("Tonal", [r"\btonal\b"]),
    ("ProForm", [_and(r"proform", _PROFORM_CTX), r"pro-form fitness", r"proform carbon",
                 r"proform ifit", r"ifit.{0,20}proform"]),
    ("Sunny Health", [r"sunny health"]),
    ("Rogue", [r"rogue fitness"]),
    ("REP", [r"rep fitness"]),
    ("Force USA", [r"force usa"]),
    ("Titan", [r"titan fitness"]),
    ("Eleiko", [r"eleiko"]),
    ("Concept2", [r"concept2", r"concept 2 rower", r"concept 2 row"]),
    ("Assault", [r"assault fitness", r"assaultbike", r"assault bike",
                 r"assault runner", r"assault air"]),
    ("Johnson", [r"johnson health tech", r"johnson fitness", r"喬山"]),
]

BRAND_DETECT_COMPILED = [
    (name, [re.compile(p, re.IGNORECASE) for p in patterns])
    for name, patterns in BRAND_DETECT_PATTERNS
]

JOHNSON_OWNED_BRANDS = {"Johnson", "Matrix", "Horizon", "Vision"}

FINANCE_KEYWORDS = [
    "stock", "stocks", "shares", "shareholder", "investor", "investors",
    "subscriber growth", "subscribers", "earnings", "quarterly", "revenue",
    "market cap", "profitability", "net income", "dividend", "analyst",
    "price target", "nyse", "nasdaq", "pton", "fiscal", "valuation",
    "guidance", "ipo", "buyback", "sec filing", "10-k", "10-q",
]

TECH_KEYWORDS = [
    "ai", "artificial intelligence", "smart", "sensor", "patent",
    "edge computing", "algorithm", "machine learning", "iot",
    "wearable", "biometric", "app", "software", "virtual reality", "vr",
    # 2026-09-14：補既有詞的複數形。改用詞界比對後，原本 `k in text` 子字串
    # 比對可命中的複數形會漏判（實測 "Fitness Apps" / "Wearables" / "sensors"
    # 等 32 篇科技新聞被誤判）。僅補複數，不新增全新關鍵字。
    "sensors", "patents", "algorithms", "wearables", "biometrics", "apps",
]

MARKET_KEYWORDS = [
    "market", "trend", "forecast", "growth", "share", "industry report",
    "cagr", "outlook", "demand",
    # 2026-09-14：同上，補既有詞的複數形。
    "markets", "trends", "forecasts", "outlooks",
]

CATEGORY_NAME_MAP = {
    "competitor": "競品情報動態",
    "tech": "健身科技研發",
    "market": "全球市場趨勢",
    "brand": "喬山品牌動態",
    "finance": "財經/股市",
}


def _compile_keyword_list(keywords):
    patterns = []
    for kw in keywords:
        if re.match(r"^[a-zA-Z0-9]+$", kw):
            patterns.append(re.compile(r"\b" + re.escape(kw) + r"\b", re.IGNORECASE))
        else:
            patterns.append(re.compile(re.escape(kw), re.IGNORECASE))
    return patterns


def _compile_keyword_list_ascii_bound(keywords):
    r"""與 _compile_keyword_list() 相同用途，但改用 [^a-zA-Z0-9] 作為詞界，
    而非 Python 原生 \b。

    原因：Python 的 \w 包含 CJK 字元，因此 \bai\b 對「長者AI健身器材」不會命中
    （"I" 與 "健" 之間沒有 word boundary），導致中文科技新聞被漏判。
    改用 [^a-zA-Z0-9] 邊界後，CJK 字元被視為邊界，可正確命中，同時仍能排除
    ag(ai)nst / tr(ai)ner / blackm(ai)l 這類英文子字串誤命中。
    非純英數的關鍵字（含空白或 CJK）維持原樣以子字串比對。
    """
    patterns = []
    for kw in keywords:
        if re.match(r"^[a-zA-Z0-9]+$", kw):
            patterns.append(re.compile(
                r"(?:^|[^a-zA-Z0-9])" + re.escape(kw) + r"(?:$|[^a-zA-Z0-9])",
                re.IGNORECASE))
        else:
            patterns.append(re.compile(re.escape(kw), re.IGNORECASE))
    return patterns


FINANCE_PATTERNS = _compile_keyword_list(FINANCE_KEYWORDS)

# 使用 _compile_keyword_list_ascii_bound()：Python 的 \b 會把 CJK 當作 \w，
# 導致「長者AI健身器材」這類中文科技新聞漏判（實測 22 篇中文文章被誤搬到
# market）。改用 [^a-zA-Z0-9] 邊界可同時正確處理 CJK 與英文子字串誤命中。
TECH_PATTERNS = _compile_keyword_list_ascii_bound(TECH_KEYWORDS)
MARKET_PATTERNS = _compile_keyword_list_ascii_bound(MARKET_KEYWORDS)


def detect_brand(text: str):
    for canonical, patterns in BRAND_DETECT_COMPILED:
        for p in patterns:
            if p.search(text):
                return canonical
    return None


# ---------------------------------------------------------------------------
# v3 多值品牌判定（docs/v3-spec.md 3-1 / 第 10 節）：規則精神移植自
# assets/tag-map-v3.js 的 BRAND_DEFS（已用 8311 篇驗證），本檔為落地後的唯一
# 真實來源。完全獨立於上方既有 detect_brand()/BRAND_DETECT_PATTERNS，不改動
# 既有單值 brand 欄位判定邏輯。
# ---------------------------------------------------------------------------

def _build_v3_regex(tokens=None, phrases=None):
    """依 tokens（自動加人工詞界）與 phrases（原樣拼接）組出一個大 regex，
    精神對應 assets/tag-map-v3.js 的 buildRegex()。"""
    parts = []
    if tokens:
        parts.append("(?:^|[^a-zA-Z0-9])(?:" + "|".join(tokens) + ")(?:$|[^a-zA-Z0-9])")
    if phrases:
        parts.append("|".join(phrases))
    if not parts:
        return None
    return re.compile("|".join(parts), re.IGNORECASE)


BRANDS_V3 = [
    "Peloton", "Technogym", "NordicTrack", "Johnson", "ProForm", "SHUA",
    "Life Fitness", "Bowflex", "REP", "Matrix", "Precor", "Tonal", "Sole",
    "Concept2", "Sunny Health", "Force USA", "Rogue", "Schwinn", "Assault",
    "Vision", "Hammer Strength", "Titan", "Eleiko", "Horizon", "TRUE Fitness",
    "Nautilus", "Cybex", "Spirit", "Inspire", "Dyaco", "Impulse", "Keiser",
    "Star Trac", "CORE Fitness", "EGYM", "DRAX",
]

# 2026-09-14 修正：原清單只列單數，token regex 尾端的 (?:$|[^a-zA-Z0-9]) 邊界
# 導致複數形不命中（實測 "Rogue Dumbbell review" 命中、"Rogue Dumbbells vs.
# REP Dumbbells" 漏標）。補齊既有詞的複數形並補上完全缺漏的 bench/benches。
# 刻意不併入 FITNESS_TERMS（實算顯示會讓 Matrix 由 41 篇暴增到 113 篇，因該清單
# 含 earnings/revenue/club 等非健身情境詞，作為品牌情境詞過鬆）。
_FIT_CTX_V3_TOKENS = [
    "fitness", "gym", "gyms", "treadmill", "treadmills", "elliptical",
    "ellipticals", "equipment", "strength", "cardio", "rower", "rowers",
    "rowing", "workout", "workouts", "exercise", "exercises", "machine",
    "machines", "trainer", "trainers", "weights", "barbell", "barbells",
    "dumbbell", "dumbbells", "rack", "racks", "kettlebell", "kettlebells",
    "bench", "benches", "bike", "bikes",
]
_FIT_CTX_V3_RE = _build_v3_regex(_FIT_CTX_V3_TOKENS, ["home gym"])
BRAND_DEFS_V3 = [
    {"name": "Peloton", "strong_tokens": ["peloton"]},
    {"name": "Technogym", "strong_tokens": ["technogym"]},
    {"name": "NordicTrack", "strong_tokens": ["nordictrack"], "strong_phrases": ["nordic track"]},
    {"name": "Johnson", "strong_phrases": ["johnson health tech", "johnson fitness", "喬山"]},
    {"name": "ProForm", "strong_tokens": ["proform"],
     "strong_phrases": ["pro-form fitness", "proform carbon", "proform ifit"]},
    {"name": "SHUA", "strong_tokens": ["shua"], "strong_phrases": ["舒華", "舒华"]},
    {"name": "Life Fitness", "strong_phrases": ["life fitness"]},
    {"name": "Bowflex", "strong_tokens": ["bowflex"]},
    {"name": "REP", "strong_phrases": ["rep fitness"], "weak_tokens": ["rep"],
     "exclude": ["one rep max", "one-rep max", "per rep", "rep range", "rep count",
                 "each rep", "reps and sets", "sets and reps", "personal rep",
                 "sales rep", "press rep"]},
    {"name": "Matrix", "strong_phrases": ["matrix fitness", "matrix strength"], "weak_tokens": ["matrix"],
     "exclude": ["matrix service", "the matrix", "risk matrix", "decision matrix",
                 "skills matrix", "matrix organization"]},
    {"name": "Precor", "weak_tokens": ["precor"]},
    {"name": "Tonal", "weak_tokens": ["tonal"],
     "exclude": ["tonal shift", "tonal dressing", "tonal colorway", "tonal quality",
                 "atonal", "tonal palette", "tonal range"]},
    {"name": "Sole", "strong_phrases": ["sole fitness", "sole treadmill"], "weak_tokens": ["sole"]},
    {"name": "Concept2", "strong_tokens": ["concept2"], "strong_phrases": ["concept 2"]},
    {"name": "Sunny Health", "strong_phrases": ["sunny health"]},
    {"name": "Force USA", "strong_phrases": ["force usa"]},
    {"name": "Rogue", "strong_phrases": ["rogue fitness", "rogue ohio bar"], "weak_tokens": ["rogue"]},
    {"name": "Schwinn", "weak_tokens": ["schwinn"]},
    {"name": "Assault", "strong_phrases": ["assault fitness", "assault bike", "assaultbike",
                                            "assault runner", "assault air"], "weak_tokens": ["assault"],
     "exclude": ["sexual assault", "assault charges", "assault rifle", "assault weapon",
                 "assaulted", "armed assault", "military assault"]},
    {"name": "Vision", "strong_phrases": ["vision fitness"], "weak_tokens": ["vision"]},
    {"name": "Hammer Strength", "strong_phrases": ["hammer strength"]},
    {"name": "Titan", "strong_phrases": ["titan fitness"], "weak_tokens": ["titan"]},
    {"name": "Eleiko", "strong_tokens": ["eleiko"]},
    {"name": "Horizon", "strong_phrases": ["horizon fitness"], "weak_tokens": ["horizon"]},
    {"name": "TRUE Fitness", "strong_phrases": ["true fitness", "true treadmill", "true trainer",
                                                 "trueform runner", "true fitness technology"]},
    {"name": "Nautilus", "strong_phrases": ["nautilus fitness", "nautilus inc", "nautilus, inc"],
     "weak_tokens": ["nautilus"],
     "exclude": ["nautilus biotechnology", "patek philippe", "marine insurance", "(naut)"]},
    {"name": "Cybex", "strong_phrases": ["cybex eagle", "cybex international", "arc trainer"],
     "weak_tokens": ["cybex"]},
    {"name": "Spirit", "strong_phrases": ["spirit fitness"], "weak_tokens": ["spirit"]},
    {"name": "Inspire", "strong_phrases": ["inspire fitness"], "weak_tokens": ["inspire"]},
    {"name": "Dyaco", "weak_tokens": ["dyaco"]},
    {"name": "Impulse", "strong_phrases": ["impulse fitness"], "weak_tokens": ["impulse"]},
    {"name": "Keiser", "weak_tokens": ["keiser"]},
    {"name": "Star Trac", "strong_phrases": ["star trac"]},
    # 2026-09-14 修正：原本無詞界，"hardcore fitness" 與 "Lifecore Fitness"（另一家公司）
    # 都會被誤判為 CORE Fitness。phrases 在 _build_v3_regex() 中是原樣拼接，故直接內嵌詞界。
    {"name": "CORE Fitness",
     "strong_phrases": [r"(?:^|[^a-zA-Z0-9])core fitness(?:$|[^a-zA-Z0-9])"]},
    {"name": "EGYM", "weak_tokens": ["egym"]},
    {"name": "DRAX", "weak_tokens": ["drax"]},
]

for _bd in BRAND_DEFS_V3:
    _bd["_exclude_re"] = _build_v3_regex(None, _bd.get("exclude")) if _bd.get("exclude") else None
    _bd["_strong_re"] = _build_v3_regex(_bd.get("strong_tokens"), _bd.get("strong_phrases"))
    _bd["_weak_re"] = _build_v3_regex(_bd.get("weak_tokens")) if _bd.get("weak_tokens") else None

BRAND_DEF_BY_NAME_V3 = {_bd["name"]: _bd for _bd in BRAND_DEFS_V3}


def _brand_hit_v3(brand_def, text):
    if brand_def["_exclude_re"] and brand_def["_exclude_re"].search(text):
        return False
    if brand_def["_strong_re"] and brand_def["_strong_re"].search(text):
        return True
    if brand_def["_weak_re"] and brand_def["_weak_re"].search(text) and _FIT_CTX_V3_RE.search(text):
        return True
    return False


def detect_brands(text: str) -> list:
    """回傳多值品牌清單（36 品牌，見 docs/v3-spec.md 3-1）。供新增欄位 brands
    使用，取代既有單值 detect_brand()；不影響既有 detect_brand()/brand 欄位。
    呼叫端若既有單值 brand 非 None，須自行併入結果（本函式只吃 text，不吃
    既有 brand，保留單一職責）。"""
    text = text or ""
    return [name for name in BRANDS_V3 if _brand_hit_v3(BRAND_DEF_BY_NAME_V3[name], text)]


def is_finance(text: str) -> bool:
    return any(p.search(text) for p in FINANCE_PATTERNS)


def classify(title: str, summary: str, brand: str = None):
    text = f"{title} {summary}".lower()

    if is_finance(text):
        return "finance"

    if brand:
        if brand in JOHNSON_OWNED_BRANDS:
            return "brand"
        return "competitor"

    if any(p.search(text) for p in TECH_PATTERNS):
        return "tech"

    if any(p.search(text) for p in MARKET_PATTERNS):
        return "market"

    return "market"


# ---------------------------------------------------------------------------
# 內容過濾
# ---------------------------------------------------------------------------

FITNESS_TERMS = [
    "fitness", "gym", "workout", "workouts", "exercise", "exercises",
    "treadmill", "treadmills", "elliptical", "ellipticals", "rowing",
    "rower", "rowers", "exercise bike", "stationary bike", "spin bike",
    "spinning bike", "recumbent bike", "air bike", "indoor cycling",
    "strength", "cardio", "home gym", "dumbbell", "dumbbells", "barbell",
    "kettlebell", "weight", "weights", "weightlifting", "powerlifting",
    "fitness equipment", "gym equipment", "cardio equipment",
    "exercise equipment", "functional trainer", "power rack", "squat rack",
    "smith machine", "cable machine", "weight bench", "stair climber",
    "stairmaster", "climbmill", "incline trainer", "connected fitness",
    "bodybuilding", "crossfit", "personal trainer", "resistance training",
    "健身", "器材", "跑步機", "橢圓機", "飛輪", "重訓", "有氧", "健身房",
    # 穿戴裝置
    "wearable", "wearables", "smart ring", "smart rings", "fitness tracker",
    "fitness trackers", "heart rate monitor", "heart rate monitors",
    "smartwatch", "smart watch", "garmin", "whoop", "運動手錶", "心率帶",
    "穿戴裝置",
    # 健身房營運
    "health club", "health clubs", "gym opening", "gym openings",
    "membership", "memberships", "franchise", "franchisee", "franchisees",
    "展店", "會員數", "會員人數",
    # App / 課程 / 訂閱制
    "fitness app", "subscription", "subscriptions", "connected fitness app",
    "on-demand fitness", "live class", "live classes",
    # 營養補劑
    "whey protein", "creatine", "pre-workout", "preworkout", "protein powder",
    "乳清蛋白", "肌酸",
    # 財報併購（喬山/Peloton/Planet Fitness/Technogym/Xponential 等器材公司財報屬相關）
    "earnings", "revenue", "acquisition", "acquires", "acquired", "merger",
    "營收", "財報", "併購",
]
# 2026-07-30 擴充：先前 808 筆正常健身產業新聞被判 no_relevance，主因下列詞缺漏
FITNESS_TERMS += [
    # 課程 / 訓練型態
    "pilates", "yoga", "barre", "hyrox", "spin studio", "boutique fitness",
    "group fitness", "personal training", "strength training", "recovery",
    "瑜珈", "瑜伽", "皮拉提斯", "有氧運動",
    # 器材（補漏）
    "bench", "benches", "plate-loaded", "plate loaded", "weight plate",
    "resistance band", "adjustable dumbbell", "leg press", "lat pulldown",
    "nordic bench", "sled", "trap bar",
    # 產業 / 營運
    "studio", "studios", "club", "clubs", "wellness", "fit tech", "fittech",
    "gyms", "fitness industry", "fitness brand", "fitness company",
    "boutique studio", "big box gym", "健身工廠", "健身中心",
    # 補劑（補漏）
    "supplement", "supplements", "protein", "electrolyte", "amino acid",
    "補劑", "保健食品", "高蛋白",
    # 2026-09-14 修正：補既有單數詞缺漏的複數形（實測 3 篇 Kettlebells 導購文
    # 因無複數形被誤判 no_relevance）。注意：規格原要求同時補 "benches"，但
    # "benches" 已存在於本清單第一段（"bench", "benches", ...，見上方），
    # 故依規格「不要重複加」的原則只補 kettlebells / barbells。
    "kettlebells", "barbells",
]
FITNESS_TERM_PATTERNS = _compile_keyword_list(FITNESS_TERMS)


def has_fitness_term(text: str) -> bool:
    return any(p.search(text) for p in FITNESS_TERM_PATTERNS)


# ---------------------------------------------------------------------------
# 產品分類（cardio / strength / wearable / other）
# ---------------------------------------------------------------------------

CARDIO_KEYWORDS = [
    "treadmill", "treadmills", "elliptical", "ellipticals", "rowing",
    "rower", "rowers", "exercise bike", "stationary bike", "spin bike",
    "spinning bike", "recumbent bike", "air bike", "indoor cycling",
    "cardio", "stair climber", "stairmaster", "climbmill",
    "跑步機", "橢圓機", "划船機", "飛輪", "健身車",
]
STRENGTH_KEYWORDS = [
    "dumbbell", "dumbbells", "barbell", "barbells", "kettlebell",
    "kettlebells", "power rack", "squat rack", "smith machine",
    "cable machine", "plate", "plates", "strength training",
    "啞鈴", "槓鈴", "壺鈴", "龍門架", "深蹲架", "重訓",
    "lat pulldown", "cable crossover", "cable pulley", "hyperextension", "roman chair", "sissy squat", "leg press", "chest press", "pec deck", "hack squat", "seated row", "preacher curl", "leg curl", "leg extension", "shoulder press machine", "strength machine", "weight stack",
]
WEARABLE_KEYWORDS = [
    "wearable", "wearables", "smart ring", "smart rings", "fitness tracker",
    "fitness trackers", "heart rate monitor", "heart rate monitors",
    "smartwatch", "smart watch", "garmin", "whoop",
    "運動手錶", "心率帶",
]
CARDIO_PATTERNS = _compile_keyword_list(CARDIO_KEYWORDS)
STRENGTH_PATTERNS = _compile_keyword_list(STRENGTH_KEYWORDS)
WEARABLE_PATTERNS = _compile_keyword_list(WEARABLE_KEYWORDS)


def classify_product(title: str, summary: str = "") -> str:
    """回傳 product_category：cardio / strength / wearable / other。
    命中優先序：cardio > strength > wearable > other。"""
    text = f"{title or ''} {summary or ''}"
    if any(p.search(text) for p in CARDIO_PATTERNS):
        return "cardio"
    if any(p.search(text) for p in STRENGTH_PATTERNS):
        return "strength"
    if any(p.search(text) for p in WEARABLE_PATTERNS):
        return "wearable"
    return "other"


# ---------------------------------------------------------------------------
# 多值產品品類（product_categories）：cardio / strength / wearable / recovery /
# nutrition / apparel / studio / digital / facility / wellness /
# equipment_general。命中即加入（非互斥單選），與既有單值 classify_product()
# （cardio/strength/wearable/other）各自獨立的關鍵字組，互不影響、互不修改。
#
# 比對優先序：
#   1. 先跑前九種既有品類關鍵字比對。
#   2. 再跑 wellness 關鍵字比對，命中則併入結果。
#   3. 若步驟 1+2 完全沒有任何命中 -> 嘗試 BRAND_PRODUCT_CATEGORIES[brand] 反推
#      （只在完全沒有關鍵字訊號時才用品牌，避免稀釋關鍵字比對的精準度）。
#   4. 若步驟 3 仍無結果（無 brand 或 brand 不在映射表中）
#      -> 嘗試 equipment_general 關鍵字（僅作 fallback，不參與步驟 1 的並列比對）。
#   5. 全部都沒有 -> 回傳空清單 []（例如企業財報／併購新聞，本來就沒有產品品類）。
# ---------------------------------------------------------------------------

PRODUCT_CATEGORY_ORDER = [
    "cardio", "strength", "wearable", "recovery", "nutrition",
    "apparel", "studio", "digital", "facility", "wellness", "equipment_general",
]

# 只在步驟 1+2 完全沒有關鍵字命中時才啟用的 fallback 類別，
# 不參與 PRODUCT_CATEGORY_ORDER 的主要並列比對迴圈。
PRODUCT_CATEGORY_FALLBACK_ONLY = {"equipment_general"}

PC_CARDIO_KEYWORDS = [
    "treadmill", "treadmills", "elliptical", "ellipticals", "rowing", "rower",
    "rowers", "exercise bike", "stationary bike", "spin bike", "recumbent bike",
    "air bike", "indoor cycling", "cardio", "stair climber", "stairmaster",
    "climbmill", "跑步機", "橢圓機", "划船機", "飛輪", "健身車",
]
PC_STRENGTH_KEYWORDS = [
    "dumbbell", "dumbbells", "barbell", "barbells", "kettlebell", "kettlebells",
    "power rack", "squat rack", "smith machine", "cable machine",
    "strength training", "weight plate", "bench press", "deadlift",
    "free weights", "weight machine", "resistance band", "resistance bands",
    "functional trainer", "weight training", "lifting",
    "啞鈴", "槓鈴", "壺鈴", "龍門架", "深蹲架", "重訓",
    "lat pulldown", "cable crossover", "cable pulley", "hyperextension", "roman chair", "sissy squat", "leg press", "chest press", "pec deck", "hack squat", "seated row", "preacher curl", "leg curl", "leg extension", "strength machine", "weight stack", "pullover machine",
]
PC_WEARABLE_KEYWORDS = [
    "wearable", "wearables", "smart ring", "fitness tracker", "fitness trackers",
    "heart rate monitor", "smartwatch", "smart watch", "garmin", "whoop", "oura",
    "fitbit", "運動手錶", "心率帶",
]
PC_RECOVERY_KEYWORDS = [
    "recovery", "massage gun", "foam roller", "sauna", "cold plunge",
    "cryotherapy", "red light therapy", "compression boots", "physiotherapy",
    "physical therapy", "rehabilitation", "rehab", "stretching", "mobility",
    "按摩槍", "恢復", "伸展",
]
PC_NUTRITION_KEYWORDS = [
    "nutrition", "protein", "supplement", "supplements", "creatine",
    "pre-workout", "diet", "weight loss", "glp-1", "ozempic", "calorie",
    "calories", "meal plan", "營養", "蛋白", "補劑",
]
PC_APPAREL_KEYWORDS = [
    "apparel", "sportswear", "activewear", "leggings", "sneaker", "sneakers",
    "running shoes", "gym wear", "athleisure", "服飾", "運動鞋",
]
PC_STUDIO_KEYWORDS = [
    "pilates", "yoga", "barre", "spin class", "group fitness", "group training",
    "boutique fitness", "crossfit", "hyrox", "reformer", "瑜珈", "皮拉提斯", "團課",
]
PC_DIGITAL_KEYWORDS = [
    "app", "apps", "software", "platform", "streaming", "on demand",
    "subscription", "ai coach", "virtual class", "virtual classes",
    "digital fitness", "connected fitness", "interactive fitness", "saas",
    "algorithm", "數位", "應用程式",
]
PC_FACILITY_KEYWORDS = [
    "gym", "gyms", "health club", "fitness club", "gym chain", "franchise",
    "facility", "facilities", "club", "clubs", "new location", "opens",
    "opening", "expands", "expansion", "planet fitness", "crunch fitness",
    "anytime fitness", "equinox", "場館", "門市", "分店",
]
PC_WELLNESS_KEYWORDS = [
    "wellness", "longevity", "holistic health", "mental health",
    "sleep quality", "mindfulness", "meditation", "stress management",
    "healthspan", "preventive health", "養生", "健康管理", "睡眠", "正念",
]
# equipment_general 只作 fallback：僅在其他所有品類（含 wellness）都沒命中時才套用，
# 避免吃掉本來該分到 cardio/strength 等更精準品類的文章。
PC_EQUIPMENT_GENERAL_KEYWORDS = [
    "equipment", "equipments", "fitness equipment", "gym equipment",
    "exercise equipment", "workout equipment",
    "fitness machine", "exercise machine", "gym machine", "cardio machine", "器材", "健身器材", "器械",
]

PRODUCT_CATEGORY_PATTERN_MAP = {
    "cardio": _compile_keyword_list(PC_CARDIO_KEYWORDS),
    "strength": _compile_keyword_list(PC_STRENGTH_KEYWORDS),
    "wearable": _compile_keyword_list(PC_WEARABLE_KEYWORDS),
    "recovery": _compile_keyword_list(PC_RECOVERY_KEYWORDS),
    "nutrition": _compile_keyword_list(PC_NUTRITION_KEYWORDS),
    "apparel": _compile_keyword_list(PC_APPAREL_KEYWORDS),
    "studio": _compile_keyword_list(PC_STUDIO_KEYWORDS),
    "digital": _compile_keyword_list(PC_DIGITAL_KEYWORDS),
    "facility": _compile_keyword_list(PC_FACILITY_KEYWORDS),
    "wellness": _compile_keyword_list(PC_WELLNESS_KEYWORDS),
    "equipment_general": _compile_keyword_list(PC_EQUIPMENT_GENERAL_KEYWORDS),
}

# 品牌 -> 產品品類反推映射（僅在關鍵字完全無命中時作為 fallback 使用）。
# 全庫實際存在的 27 個品牌，依各品牌實際產品線判定；不要自行更動對應關係。
BRAND_PRODUCT_CATEGORIES = {
    "Peloton": ["cardio", "digital"],
    "NordicTrack": ["cardio", "digital"],
    "ProForm": ["cardio", "digital"],
    "Sole": ["cardio"],
    "Horizon": ["cardio"],
    "Vision": ["cardio"],
    "Schwinn": ["cardio"],
    "Concept2": ["cardio"],
    "Assault": ["cardio"],
    "Sunny Health": ["cardio"],
    "TRUE Fitness": ["cardio"],
    "REP": ["strength"],
    "Rogue": ["strength"],
    "Eleiko": ["strength"],
    "Force USA": ["strength"],
    "Titan": ["strength"],
    "Hammer Strength": ["strength"],
    "Bowflex": ["strength", "cardio"],
    "Tonal": ["strength", "digital"],
    "Nautilus": ["strength", "cardio"],
    "Technogym": ["cardio", "strength", "digital"],
    "Johnson": ["cardio", "strength"],
    "Matrix": ["cardio", "strength"],
    "Life Fitness": ["cardio", "strength"],
    "Precor": ["cardio", "strength"],
    "Cybex": ["strength", "cardio"],
    "SHUA": ["cardio", "strength"],
}


def classify_product_categories(title: str, summary: str = "", brand=None) -> list:
    """回傳多值 product_categories 清單（值域見 PRODUCT_CATEGORY_ORDER）。
    命中即加入，非互斥單選；比對優先序見上方模組註解。brand 為選填，僅在
    關鍵字完全無命中時作為反推 fallback；未命中任何類別回傳空清單 []。"""
    text = f"{title or ''} {summary or ''}"
    result = []
    for name in PRODUCT_CATEGORY_ORDER:
        if name in PRODUCT_CATEGORY_FALLBACK_ONLY:
            continue
        if any(p.search(text) for p in PRODUCT_CATEGORY_PATTERN_MAP[name]):
            result.append(name)
    if result:
        return result
    if brand:
        brand_pcs = BRAND_PRODUCT_CATEGORIES.get(brand)
        if brand_pcs:
            return list(brand_pcs)
    if any(p.search(text) for p in PRODUCT_CATEGORY_PATTERN_MAP["equipment_general"]):
        return ["equipment_general"]
    return []


# ---------------------------------------------------------------------------
# 受眾標籤（audience_tags）：PM / Design / Marketing，多值、保證至少一個標籤。
# audience_tags_source："keyword"（關鍵字命中，高信心）或 "structural"
# （結構推導，低信心；依 category + product_categories 映射）。
# ---------------------------------------------------------------------------

AUDIENCE_PM_KEYWORDS = [
    "product management", "product manager", "product strategy", "roadmap",
    "product launch", "launches", "launched", "launch", "unveils", "unveil",
    "introduces", "introducing", "new product", "product line", "lineup",
    "patent", "patents", "r&d", "innovation", "feature", "features", "upgrade",
    "next generation", "next-gen", "prototype", "portfolio", "debuts", "debut",
    "announces", "announced", "review", "reviews", "tested", "best", "spec",
    "specs", "產品", "策略", "規劃", "專利",
]
AUDIENCE_DESIGN_KEYWORDS = [
    "design", "designs", "designed", "ui", "ux", "user experience",
    "user interface", "interface", "usability", "ergonomic", "ergonomics",
    "aesthetic", "aesthetics", "industrial design", "design award", "red dot",
    "minimalist", "form factor", "styling", "sleek", "console", "display",
    "touchscreen", "設計", "使用者體驗", "人因",
]
AUDIENCE_MARKETING_KEYWORDS = [
    "marketing", "advertising", "ad campaign", "campaign", "campaigns",
    "brand", "branding", "rebrand", "sponsorship", "sponsor", "partnership",
    "partnerships", "collaboration", "ambassador", "influencer",
    "social media", "promotion", "promotional", "growth",
    "customer acquisition", "retention", "loyalty", "press release",
    "endorsement", "deal", "deals", "sale", "discount",
    "行銷", "廣告", "品牌", "成長", "合作",
]
AUDIENCE_PM_PATTERNS = _compile_keyword_list(AUDIENCE_PM_KEYWORDS)
AUDIENCE_DESIGN_PATTERNS = _compile_keyword_list(AUDIENCE_DESIGN_KEYWORDS)
AUDIENCE_MARKETING_PATTERNS = _compile_keyword_list(AUDIENCE_MARKETING_KEYWORDS)

# 關鍵字皆未命中時的結構推導（低信心）：先依 category 給預設兩個標籤
AUDIENCE_STRUCTURAL_CATEGORY_MAP = {
    "tech": ["PM", "Design"],
    "finance": ["PM", "Marketing"],
    "market": ["PM", "Marketing"],
    "competitor": ["PM", "Marketing"],
    "brand": ["Marketing", "PM"],
}
# 再依 product_categories 補充標籤（不重複加入）
AUDIENCE_STRUCTURAL_PRODUCT_MAP = {
    "wearable": ["Design"],
    "digital": ["Design"],
    "facility": ["Marketing"],
    "studio": ["Marketing"],
    "apparel": ["Design", "Marketing"],
}


def classify_audience_tags(title: str, summary: str, category: str,
                            product_categories: list):
    """回傳 (audience_tags: list[str], audience_tags_source: str)。
    保證 audience_tags 至少含一個標籤。"""
    text = f"{title or ''} {summary or ''}"
    tags = []
    if any(p.search(text) for p in AUDIENCE_PM_PATTERNS):
        tags.append("PM")
    if any(p.search(text) for p in AUDIENCE_DESIGN_PATTERNS):
        tags.append("Design")
    if any(p.search(text) for p in AUDIENCE_MARKETING_PATTERNS):
        tags.append("Marketing")
    if tags:
        return tags, "keyword"

    tags = list(AUDIENCE_STRUCTURAL_CATEGORY_MAP.get(category, ["PM", "Marketing"]))
    for pc in (product_categories or []):
        for extra in AUDIENCE_STRUCTURAL_PRODUCT_MAP.get(pc, []):
            if extra not in tags:
                tags.append(extra)
    if not tags:
        tags = ["PM"]
    return tags, "structural"


# ---------------------------------------------------------------------------
# 二層子分類（subcategory / subcategoryName）：在既有 category 之下細分，
# 不改動 category 本身。每個 category 皆有 fallback 子類（other／其他）。
# ---------------------------------------------------------------------------

def _subcategory_rules(spec):
    return [(code, name, _compile_keyword_list(kws)) for code, name, kws in spec]


SUBCATEGORY_RULES = {
    "market": _subcategory_rules([
        ("product_review", "產品評測導購",
         ["tested", "review", "reviews", "best", "deals", "deal", "sale",
          "amazon", "garage gym reviews"]),
        ("home_fitness_trend", "家用健身趨勢",
         ["home gym", "home workout", "garage gym", "at-home", "at home"]),
        ("facility_operations", "場館營運展店",
         ["gym opening", "gym openings", "opens", "expands", "club", "leisure",
          "health club"]),
        ("wellness_trend", "健康養生趨勢",
         ["health", "wellness", "weight loss", "longevity"]),
        ("market_research", "市場研究報告",
         ["market size", "forecast", "cagr", "industry report", "market share"]),
        ("product_launch", "新品發表",
         ["launches", "launch", "unveils", "debuts", "introduces"]),
    ]),
    "tech": _subcategory_rules([
        ("ai_training", "AI 智慧訓練",
         ["ai", "artificial intelligence", "smart", "algorithm",
          "machine learning"]),
        ("connected_app", "連網與 App",
         ["app", "software", "platform", "connected", "streaming",
          "subscription"]),
        ("training_science", "訓練科學方法",
         ["training method", "functional", "strength training", "protocol",
          "study"]),
        ("wearable_device", "穿戴裝置",
         ["wearable", "smartwatch", "tracker", "apple watch", "garmin"]),
    ]),
    "competitor": _subcategory_rules([
        ("peloton", "Peloton 動態", ["peloton", "pton"]),
        ("international_brand", "國際品牌動態",
         ["technogym", "life fitness", "precor", "nordictrack", "bowflex",
          "tonal", "proform"]),
        ("china_brand", "中國品牌動態",
         ["舒华", "舒華", "shua", "中国", "國產", "国产"]),
        ("competitor_review", "競品產品評測",
         ["review", "tested", "comparison", "vs"]),
        ("commercial_channel", "商用通路",
         ["commercial", "b2b", "distributor", "dealer"]),
    ]),
    "brand": _subcategory_rules([
        ("financial_report", "喬山財報營收",
         ["財報", "營收", "eps", "legal", "工商時報", "cmoney", "moneydj",
          "cnyes"]),
        ("product_line", "喬山產品線",
         ["matrix", "vision", "horizon", "hammer strength"]),
        ("channel_partnership", "通路與夥伴",
         ["partnership", "partners", "dealer", "expand"]),
        ("brand_risk", "品牌風險",
         ["recall", "lawsuit", "fine", "hazard", "召回"]),
    ]),
    "finance": _subcategory_rules([
        ("earnings_report", "財報公布",
         ["earnings", "revenue", "eps", "results", "quarterly", "guidance"]),
        ("stock_analysis", "股價分析",
         ["stock", "shares", "price target", "valuation"]),
        ("peer_stock", "同業個股",
         ["planet fitness", "plnt", "xponential", "xpof", "pton"]),
        ("analyst_rating", "法人評等",
         ["analyst", "rating", "upgrade", "downgrade", "price target"]),
    ]),
}
SUBCATEGORY_FALLBACK = ("other", "其他")


def classify_subcategory(category: str, title: str, summary: str = ""):
    """回傳 (subcategory: str, subcategoryName: str)。命中優先序依各 category
    規則清單順序；皆未命中回傳 fallback ("other", "其他")。"""
    text = f"{title or ''} {summary or ''}"
    for code, name, patterns in SUBCATEGORY_RULES.get(category, []):
        if any(p.search(text) for p in patterns):
            return code, name
    return SUBCATEGORY_FALLBACK


# ---------------------------------------------------------------------------
# 雜訊標記（is_noise / noise_reason）：只標記，絕不刪除文章。
# 涵蓋同名公司誤收（Matrix Service / Nautilus Biotechnology）、同名電影
# （The Matrix）、內容農場來源、關鍵字堆砌標題。
# ---------------------------------------------------------------------------

NOISE_MATRIX_SERVICE_PATTERN = re.compile(
    r"matrix service|\bmtrx\b|nasdaq:\s*mtrx", re.IGNORECASE)
NOISE_NAUTILUS_BIOTECH_DIRECT_PATTERN = re.compile(
    r"nautilus biotechnolog|nasdaq:\s*naut|\bnaut\b", re.IGNORECASE)
NOISE_NAUTILUS_MENTION_PATTERN = re.compile(r"nautilus", re.IGNORECASE)
NOISE_NAUTILUS_BIOTECH_CONTEXT_PATTERN = re.compile(
    r"proteomic|alzheimer", re.IGNORECASE)
NOISE_MATRIX_MOVIE_TITLE_PATTERN = re.compile(r"the matrix", re.IGNORECASE)
NOISE_MATRIX_MOVIE_CONTEXT_PATTERN = re.compile(
    r"movie|keanu|reeves|film|box office", re.IGNORECASE)

NOISE_CONTENT_FARM_SOURCES = ("fuelcarmagazine", "krepsiniozinios", "mshale")

NOISE_STUFFING_TERMS = [
    "treadmill", "treadmills", "elliptical", "fitness", "gym", "bike",
    "proform", "nordictrack",
]
NOISE_STUFFING_PATTERNS = _compile_keyword_list(NOISE_STUFFING_TERMS)
NOISE_PUNCTUATION_PATTERN = re.compile(r"[.,:;?!]")


def classify_noise(title: str, summary: str = "", source: str = ""):
    """回傳 (is_noise: bool, noise_reason: str | None)。只做標記，不刪除文章。"""
    text = f"{title or ''} {summary or ''}"
    src = (source or "").strip().lower()

    if NOISE_MATRIX_SERVICE_PATTERN.search(text):
        return True, "same_name_company:matrix_service"

    if NOISE_NAUTILUS_BIOTECH_DIRECT_PATTERN.search(text):
        return True, "same_name_company:nautilus_biotechnology"
    if (NOISE_NAUTILUS_MENTION_PATTERN.search(text)
            and NOISE_NAUTILUS_BIOTECH_CONTEXT_PATTERN.search(text)):
        return True, "same_name_company:nautilus_biotechnology"

    if (NOISE_MATRIX_MOVIE_TITLE_PATTERN.search(text)
            and NOISE_MATRIX_MOVIE_CONTEXT_PATTERN.search(text)):
        return True, "same_name_content:the_matrix_movie"

    for farm in NOISE_CONTENT_FARM_SOURCES:
        if farm in src:
            return True, f"content_farm:{farm}"

    title_text = title or ""
    if not NOISE_PUNCTUATION_PATTERN.search(title_text):
        # 計算「總出現次數」而非「命中詞種數」：重複堆砌同一器材詞（如連續多次
        # 出現 treadmill）也應計入，實測與此口徑最吻合（約 255 筆基準）。
        hits = sum(len(p.findall(title_text)) for p in NOISE_STUFFING_PATTERNS)
        if hits >= 3:
            return True, "keyword_stuffing"

    return False, None


# ---------------------------------------------------------------------------
# v3 標籤判定（topic_tags / product_types / attention_tags / country）：
# docs/v3-spec.md 第 3 節 / 第 10 節。規則精神移植自 assets/tag-map-v3.js
# （已用 8311 篇驗證），本檔為落地後的唯一真實來源；tag-map-v3.js 之後降級
# 為 fallback／相容層。
# ---------------------------------------------------------------------------

# v7: 使用者指定分類；不足的證據保留空陣列，不以品牌強塞產品或主題。
TOPICS_V3 = ['新品發布','產品改版','AI 功能','個人化推薦','訓練計畫','數據追蹤',
 '裝置串接','第三方整合','訂閱方案','市場擴張','新品宣傳','品牌 Campaign','品牌合作',
 '代言人','產品賣點','品牌定位','價格策略','促銷活動','內容策略','市場趨勢']
PRODUCT_TYPES_V3 = ['Cardio','Strength','Connected Fitness','APP','Console','Wearable','Digital Service','Web']
ATTENTION_V3 = ['Commercial','Home','Digital']
COUNTRIES_V3 = ['全部','全球','亞洲','北美','歐洲','大洋洲','其他地區']
COMMERCIAL_BRANDS_V3 = ['Johnson','Matrix','Life Fitness','Technogym','Precor','TRUE Fitness',
 'CORE Fitness','EGYM','Hammer Strength','SHUA','Concept2','Rogue','Spirit','Star Trac','Cybex',
 'Vision','Eleiko','Keiser','Assault','Impulse','Dyaco','DRAX']
HOME_BRANDS_V3 = ['Peloton','NordicTrack','Bowflex','ProForm','Tonal','Sole','Schwinn','Horizon',
 'REP','Force USA','Sunny Health','Inspire','Titan','Nautilus']
BRAND_SEGMENT_MAP_V3 = {**dict.fromkeys(COMMERCIAL_BRANDS_V3,'Commercial'), **dict.fromkeys(HOME_BRANDS_V3,'Home')}

# 英文使用邊界，中文不套用 ASCII 詞界；所有規則以標題與實際摘要判讀。
def _rule(pattern):
    return re.compile(pattern, re.I)

TOPIC_RE_V3 = {k:_rule(v) for k,v in {
 '新品發布': r'\b(?:launch(?:es|ed|ing)?|unveil(?:s|ed|ing)?|debut(?:s|ed)?|introduc(?:es|ed|ing))\b|新(?:品|產品|服务|服務|設備).{0,10}(?:上市|發布|发布|發表|推出)|推出|發表|发布',
 '產品改版': r'\b(?:upgrad(?:e|es|ed|ing)|updat(?:e|es|ed|ing)|redesign(?:ed)?|revamp(?:ed)?|refresh(?:ed)?|next[- ]gen(?:eration)?|new version|firmware)\b|升級|升级|改版|改款|版本更新',
 'AI 功能': r'\b(?:AI|artificial intelligence|generative AI|machine learning|computer vision)\b|人工智[慧能]|生成式|智能教練|智能教练',
 '個人化推薦': r'\b(?:personali[sz](?:ed|ation)|recommendation engine|tailored (?:workouts?|plans?|recommendations?)|adaptive training|customi[sz]ed (?:workouts?|plans?))\b|個人化|个性化|客製化|定制訓練|量身打造|智慧推薦|智能推荐',
 '訓練計畫': r'\b(?:(?:training|workout|coaching|fitness) (?:programs?|plans?|routines?)|structured training|periodi[sz]ation)\b|訓練計[畫划]|训练计划|運動課程|训练课程|課程規劃',
 '數據追蹤': r'\b(?:analytics|(?:workout|health|recovery|fitness|biometric|training) (?:data|metrics|tracking)|(?:progress|activity|performance|recovery) tracking|heart[- ]rate (?:monitoring|tracking)|HRV)\b|數據追蹤|数据追踪|健康數據|健康数据|訓練紀錄|運動紀錄|心率監測',
 '裝置串接': r'\b(?:bluetooth|ANT\+|FTMS|sensors?|device (?:integration|connectivity)|pairs? with|syncs? with)\b|裝置串接|设备连接|設備連接|感測器|传感器|藍[牙芽]|蓝牙',
 '第三方整合': r'\b(?:Apple Health(?:Kit)?|HealthKit|Health Connect|Google Fit|Samsung Health|Strava|Garmin Connect|third[- ]party integration|API integration|integrat(?:es|ed|ion) with)\b|第三方整合|第三方集成',
 '訂閱方案': r'\b(?:memberships?|subscriptions?|monthly plan|annual plan|free trial|premium tier|subscriber(?:s| base)?)\b|訂閱|订阅|會員方案|会员方案|付費會員|免费试用',
 '市場擴張': r'\b(?:expan(?:ds?|sion|ding)|new markets?|new distributor|enters? (?:the )?(?:market|Europe|Asia|Japan|US)|opens? (?:in|its|new)|rolls? out in)\b|市場擴張|市场扩张|進軍|进军|拓展|展店|新據點|新市场',
 '新品宣傳': r'\b(?:launch event|unveiling event|product reveal|teaser campaign|launch marketing|press tour|promotional launch)\b|新品宣傳|新品宣传|新品發表會|新品发布会|上市宣傳|造勢',
 '品牌 Campaign': r'\b(?:(?:brand|ad|marketing|advertising|global|new) campaign|TV (?:spot|commercial)|brand awareness)\b|品牌活動|品牌活动|品牌廣告|品牌广告|形象廣告',
 '品牌合作': r'\b(?:partnerships?|partners? with|partnering with|collaborat(?:ion|ions|es|ed)|teams? up with|co[- ]brand(?:ed|ing)?|joint venture)\b|聯名|联名|合作',
 '代言人': r'\b(?:ambassadors?|endorsement|spokesperson|influencers?|celebrity partnership)\b|代言|品牌大使',
 '產品賣點': r'\b(?:reviews?|hands[- ]on|key features?|standout features?|unique selling point|core features?|specifications|worth it|we tested|tested|I tried|best (?:treadmills?|fitness trackers?|dumbbells?|exercise bikes?|resistance bands?|rowing machines?))\b|評測|评测|開箱|开箱|實測|实测|賣點|卖点|產品特色|核心功能',
 '品牌定位': r'\b(?:rebrand(?:ing)?|repositioning|brand (?:positioning|identity|strategy|image)|target audience|positions itself)\b|品牌定位|品牌形象|品牌策略|重新定位',
 '價格策略': r'\b(?:pric(?:e|ing) (?:increase|cut|strategy|drop|change)|priced at|price point|MSRP|raises? prices|lowers? prices)\b|定價|定价|售價|售价|漲價|涨价|降價|降价|調漲|調降',
 '促銷活動': r'\b(?:discounts?|promo(?:tion)?s?|coupons?|Black Friday|Cyber Monday|Prime Day|holiday sale|flash sale|clearance|limited[- ]time offer|deals?|on sale|savings)\b|折扣|促銷|促销|優惠|优惠|特賣|清倉',
 '內容策略': r'\b(?:content (?:strategy|library)|video (?:content|series)|social media content|on[- ]demand (?:content|classes)|class library|workout videos?|streaming content)\b|內容策略|内容策略|影音內容|內容行銷|影片系列|課程內容',
 '市場趨勢': r'\b(?:trends?|market (?:outlook|research|size|growth|report|forecast|share)|consumer demand|growing demand|industry outlook|CAGR)\b|市場趨勢|市场趋势|產業趨勢|产业趋势|市場規模|市场规模|消費需求|產業報告',
}.items()}
PTYPE_RE_V3 = {k:_rule(v) for k,v in {
 'Cardio':r'\b(?:cardio|treadmills?|ellipticals?|rowers?|rowing machines?|stair ?(?:climbers?|mills?)|stepmills?|exercise bikes?|stationary bikes?|spin bikes?|indoor cycling|air bikes?|SkiErg|RowErg|BikeErg|walking pads?)\b|跑步[機机]|橢圓[機机]|椭圆机|划船[機机]|飛輪|飞轮|健身[車车]|登[階阶][機机]|爬樓[機机]|有氧器材',
 'Strength':r'\b(?:strength|resistance training|weight training|weightlifting|dumbbells?|barbells?|kettlebells?|power racks?|squat racks?|weight plates?|smith machines?|functional trainers?|cable machines?|weight benches|adjustable benches|bench presses|(?:chest|leg|shoulder) presses|lat pulldowns?|cable crossovers?|resistance bands?|Pilates reformers?|home gyms?|smart gyms?)\b|重訓|重训|力量訓練|力量训练|啞鈴|哑铃|槓鈴|杠铃|壺鈴|壶铃|史密斯|深蹲架|重量訓練',
 'Connected Fitness':r'\b(?:connected fitness|connected (?:equipment|bikes?|treadmills?|gyms?)|smart (?:equipment|gyms?|bikes?|treadmills?)|IoT|FTMS|Bluetooth|ANT\+)\b|智慧健身器材|智能健身器材|聯網健身|联网健身|藍[牙芽]|蓝牙',
 'APP':r'\b(?:apps?|iOS|Android|iFIT|JRNY|atZone|FitDisplay|HealthKit|Health Connect)\b|應用程式|应用程序|手機應用|手机应用',
 'Console':r'\b(?:consoles?|touchscreens?|touch screens?|HD screens?|P82|P84|P94|XUR|XIR)\b|觸控螢幕|触摸屏|控制台|觸控面板|儀表板軟體',
 'Wearable':r'\b(?:wearables?|smartwatches?|smart watches?|fitness trackers?|fitness bands?|smart rings?|Apple Watch|Oura|Whoop|Fitbit|Garmin watch(?:es)?)\b|穿戴|智慧手[錶表]|智能手表|智能戒指|智慧戒指|運動手環',
 'Digital Service':r'\b(?:digital (?:services?|platforms?|fitness)|streaming|on[- ]demand classes|virtual (?:coaching|training)|online coaching|fitness platform|cloud platform|SaaS|AI[- ](?:powered|coach)|software|firmware|iFIT|JRNY)\b|數位服務|数字服务|線上課程|在线课程|雲端平台|軟體|软件|虛擬教練',
 'Web':r'\b(?:websites?|browsers?|web (?:portals?|apps?|platforms?|dashboards?)|online portals?)\b|網站|网站|網頁|网页|網路平台',
}.items()}

def classify_topic_tags(title, summary='', subcategory=None):
    text = f'{title or ""} {summary or ""}'
    return [k for k in TOPICS_V3 if TOPIC_RE_V3[k].search(text)]

def classify_product_types(title, summary='', product_categories=None):
    text = f'{title or ""} {summary or ""}'
    found = {k for k,p in PTYPE_RE_V3.items() if p.search(text)}
    # 產品專有詞需有健身品牌情境；一般自行車不等於健身器材。
    if re.search(r'\b(?:Peloton|NordicTrack|Schwinn|Assault)\b.{0,35}\bbikes?\b',text,re.I): found.add('Cardio')
    return [k for k in PRODUCT_TYPES_V3 if k in found]

def classify_attention_tags(brands=None, product_types=None, topic_tags=None, product_categories=None):
    found = {BRAND_SEGMENT_MAP_V3[b] for b in brands or [] if b in BRAND_SEGMENT_MAP_V3}
    # 會員促銷、社群影片與螢幕硬體本身不自動變成軟體情報。
    if set(product_types or []) & {'APP','Web','Digital Service'} or set(topic_tags or []) & {'AI 功能','第三方整合','數據追蹤'}:
        found.add('Digital')
    return [k for k in ATTENTION_V3 if k in found]

REGION_PATTERNS = {k:_rule(v) for k,v in {
 '亞洲':r'\b(?:Asia|Taiwan|China|Japan|Korea|India|Singapore|Hong Kong|Malaysia|Thailand|Vietnam|Indonesia|Philippines|Dubai|UAE|Saudi Arabia)\b|亞洲|亚洲|台灣|臺灣|中国|中國|日本|韓國|韩国|印度|新加坡|香港|泰國|泰国',
 '北美':r'\b(?:North America|United States|U\.S\.(?:A\.)?|USA|US|Canada|Canadian|Mexico|California|New York|Texas|Florida|Chicago|Los Angeles|Boston)\b|北美|美國|美国|加拿大|墨西哥',
 '歐洲':r'\b(?:Europe|European|United Kingdom|UK|Britain|British|England|Germany|German|France|French|Italy|Italian|Spain|Sweden|Finland|Netherlands|London|Berlin|FIBO)\b|歐洲|欧洲|英國|英国|德國|德国|法國|法国|義大利|意大利|西班牙',
 '大洋洲':r'\b(?:Oceania|Australia|Australian|New Zealand|Auckland|Sydney|Melbourne|Fiji)\b|大洋洲|澳洲|澳大利亞|澳大利亚|紐西蘭|新西兰',
 '其他地區':r'\b(?:Africa|Brazil|Argentina|South America|Latin America|Chile|Colombia|Kenya|Nigeria|South Africa)\b|非洲|南美|拉丁美洲|巴西|阿根廷',
}.items()}
REGION_TLDS = {'亞洲':('.tw','.cn','.hk','.jp','.kr','.in','.sg','.my','.vn','.th','.id','.ph','.ae','.sa'),
 '北美':('.us','.ca','.mx'),'歐洲':('.uk','.de','.fr','.it','.es','.se','.no','.dk','.fi','.nl','.eu','.pl','.ch','.at','.ie','.pt','.be','.lt'),
 '大洋洲':('.au','.nz','.fj'),'其他地區':('.br','.ar','.cl','.co','.za','.ng','.ke')}

def _hostname_v3(url):
    try: return (urlparse(url or '').hostname or '').lower()
    except (ValueError,TypeError): return ''

def region_details(title, summary='', urls=(), language=''):
    # 主題地區優先於媒體所在地；Force USA 為品牌名稱，不是地域證據。
    text = re.sub(r'\bForce USA\b','',f'{title or ""} {summary or ""}',flags=re.I)
    if re.search(r'\b(?:global|worldwide|international markets|around the world)\b|全球|世界各地',text,re.I):
        return '全球','content_global'
    hits = [k for k,p in REGION_PATTERNS.items() if p.search(text)]
    if len(hits)>1: return '全球','content_multi_region'
    if hits: return hits[0],'content_region'
    for url in urls:
        host = _hostname_v3(url)
        if host in ('news.google.com','google.com'): continue
        for region,suffixes in REGION_TLDS.items():
            if host.endswith(suffixes): return region,'publisher_domain'
        path = urlparse(url or '').path.lower()
        for region,codes in {'亞洲':'tw|cn|hk|jp|kr|sg|in','北美':'us|ca|mx','歐洲':'gb|uk|de|fr|it|es|eu','大洋洲':'au|nz'}.items():
            if re.search(r'/(?:[a-z]{2}[-_])?(?:'+codes+r')(?:/|$)',path): return region,'publisher_locale'
    lang=(language or '').lower()
    if re.search(r'[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]',text) or lang.startswith(('zh','ja','ko','th','vi')):
        return '亞洲','language_inference'
    if lang.startswith(('de','fr','it','nl','sv','fi','pl')): return '歐洲','language_inference'
    # 英語及 .com 無法單獨證明北美或全球。
    return '其他地區','unknown'

def classify_country(title, resolved_url=None, url=None):
    return region_details(title,urls=(resolved_url,url))[0]

BRAND_ALIASES_V7 = {'Johnson':['喬山','乔山'], 'Matrix':['喬山 Matrix','矩陣健身'],
 'Vision':['Vision Fitness','Vision Console','Vision App'],'SHUA':['舒華','舒华'],
 'Dyaco':['岱宇'],'Impulse':['英派斯'],'Horizon':['Horizon Fitness','Horizon 7.0','Horizon 7.4','Horizon 7.8'],
 'Tonal':['Tonal 2'],'CORE Fitness':['Core Health & Fitness','Core Health and Fitness'],
 'Life Fitness':['力健'],'Technogym':['泰諾健','泰诺健'],'ProForm':['Pro-Form']}
BRAND_DOMAINS_V7 = {'onepeloton.com':'Peloton','technogym.com':'Technogym','nordictrack.com':'NordicTrack',
 'johnsonfitness.com':'Johnson','johnsonhealthtech.com':'Johnson','proform.com':'ProForm','shuafitness.com':'SHUA',
 'lifefitness.com':'Life Fitness','bowflex.com':'Bowflex','repfitness.com':'REP','matrixfitness.com':'Matrix',
 'precor.com':'Precor','tonal.com':'Tonal','soletreadmills.com':'Sole','solefitness.com':'Sole',
 'concept2.com':'Concept2','sunnyhealthfitness.com':'Sunny Health','forceusa.com':'Force USA',
 'roguefitness.com':'Rogue','schwinnfitness.com':'Schwinn','assaultfitness.com':'Assault',
 'visionfitness.com':'Vision','hammerstrength.com':'Hammer Strength','titan.fitness':'Titan','eleiko.com':'Eleiko',
 'horizonfitness.com':'Horizon','truefitness.com':'TRUE Fitness','nautilus.com':'Nautilus',
 'cybexintl.com':'Cybex','spiritfitness.com':'Spirit','inspirefitness.com':'Inspire','dyaco.com':'Dyaco',
 'impulsefitness.com':'Impulse','keiser.com':'Keiser','startrac.com':'Star Trac','corehandf.com':'CORE Fitness',
 'egym.com':'EGYM','draxfit.com':'DRAX'}

def classify_article(a):
    title=a.get('title') or ''; summary=a.get('summary') or ''
    text=f'{title} {summary}'
    brands=set(detect_brands(text))
    # 辨識度高的品牌不要求額外 fitness 詞，避免漏掉財報、併購及短標題。
    for name in ('Precor','Tonal','Dyaco','Keiser','EGYM','DRAX','Nautilus'):
        definition=BRAND_DEF_BY_NAME_V3[name]
        excluded=definition.get('_exclude_re')
        if re.search(r'\b'+re.escape(name)+r'\b',text,re.I) and not (excluded and excluded.search(text)):
            brands.add(name)
    for brand,aliases in BRAND_ALIASES_V7.items():
        if any(alias.casefold() in text.casefold() for alias in aliases): brands.add(brand)
    for url in (a.get('resolved_url'),a.get('publisher_url'),a.get('url')):
        host=_hostname_v3(url)
        for domain,brand in BRAND_DOMAINS_V7.items():
            if host==domain or host.endswith('.'+domain): brands.add(brand)
    if a.get('source_type')=='official' and a.get('brand') in BRANDS_V3: brands.add(a['brand'])
    # 抑制常見一般用語：vision / spirit / inspire 等不可僅靠同篇的 gym 就視為品牌。
    for name in ('Vision','Spirit','Inspire','Impulse','Sole','Horizon','Titan','Matrix','REP','Assault'):
        strong=BRAND_DEF_BY_NAME_V3[name].get('_strong_re')
        aliases=BRAND_ALIASES_V7.get(name,[])
        domain_hit=any(BRAND_DOMAINS_V7.get(_hostname_v3(u).removeprefix('www.'))==name for u in (a.get('resolved_url'),a.get('publisher_url'),a.get('url')))
        product_near=re.search(r'\b'+re.escape(name)+r'\s+(?:[A-Z]?\d[\w.-]*|(?:fitness|treadmills?|bikes?|dumbbells?|racks?|equipment|console|app|home gym))\b',text,re.I)
        if name in brands and not ((strong and strong.search(text)) or any(x.casefold() in text.casefold() for x in aliases) or domain_hit or product_near or (a.get('source_type')=='official' and a.get('brand')==name)):
            brands.remove(name)
    a['brands']=[b for b in BRANDS_V3 if b in brands]
    a['brand']=a.get('brand') if a.get('brand') in brands else next(iter(a['brands']),None)
    a['product_types']=classify_product_types(title,summary)
    a['topic_tags']=classify_topic_tags(title,summary)
    a['attention_tags']=classify_attention_tags(a['brands'],a['product_types'],a['topic_tags'])
    if 'Console' in a['product_types'] and re.search(r'\b(?:software|firmware|UI|UX|interface|OS|update)\b|軟體|软件|介面|更新',text,re.I):
        if 'Digital' not in a['attention_tags']: a['attention_tags'].append('Digital')
    topics=set(a['topic_tags']); types=set(a['product_types']); audiences=set()
    if types or topics & set(TOPICS_V3[:9]+['產品賣點']): audiences.add('PM')
    if re.search(r'\b(?:UI|UX|interface|usability|ergonomics?|design|user experience|redesigned|accessibility)\b|介面|界面|設計|设计|人體工學|易用性',text,re.I): audiences.add('Design')
    if topics & set(TOPICS_V3[8:]): audiences.add('Marketing')
    a['audience_tags']=[x for x in ('PM','Design','Marketing') if x in audiences]
    a['audience_tags_source']='content_rules_v7'
    a['country'],a['country_source']=region_details(title,summary,(a.get('resolved_url'),a.get('publisher_url'),a.get('url')),a.get('language',''))
    a['category']=classify(title,summary,a.get('brand')); a['categoryName']=CATEGORY_NAME_MAP[a['category']]
    a['subcategory'],a['subcategoryName']=classify_subcategory(a['category'],title,summary)
    pcs=classify_product_categories(title,summary,None)
    for pc,pt in [('cardio','Cardio'),('strength','Strength'),('wearable','Wearable')]:
        if pt in types and pc not in pcs: pcs.append(pc)
    if 'digital' in pcs and 'Digital' not in a['attention_tags']: pcs.remove('digital')
    if 'Digital' in a['attention_tags'] and 'digital' not in pcs: pcs.append('digital')
    a['product_categories']=pcs; a['product_category']=pcs[0] if pcs else 'other'
    a['is_noise'],a['noise_reason']=classify_noise(title,summary,a.get('source',''))
    a['tagging_version']='v7.1'
    a['tagging_basis']='title_and_summary' if summary else 'title_only'
    a['tag_review_required']=not bool(a['topic_tags'])
    return a


MILITARY_KEYWORDS = [
    "battlefield", "usaf", "air force", "military", "warfare", "missile",
    "pentagon", "soldier", "soldiers", "troops", "navy", "army",
    "defense department", "war zone", "artillery", "combat troops",
]
BALLSPORT_KEYWORDS = [
    "footballer", "premier league", "nfl", "soccer", "nba basketball",
    "baseball game", "mlb", "cricket match", "rugby", "nhl hockey",
    "world cup", "champions league", "quarterback", "touchdown",
]
ORGANIZING_KEYWORDS = [
    "home organizing", "closet organizer", "declutter", "decluttering",
    "storage bin", "storage bins", "tidying", "organize your closet",
]
SMARTRING_KEYWORDS = [
    "smart ring", "smart rings", "oura ring", "oura", "fitness ring",
    "galaxy ring",
]

OFFTOPIC_PATTERNS = (
    _compile_keyword_list(MILITARY_KEYWORDS)
    + _compile_keyword_list(BALLSPORT_KEYWORDS)
    + _compile_keyword_list(ORGANIZING_KEYWORDS)
    + _compile_keyword_list(SMARTRING_KEYWORDS)
)

# ---------------------------------------------------------------------------
# 跨產業同名詞黑名單（不分品牌是否命中，優先權高於「brand 命中即放行」，見
# check_exclusion()）。針對實測人工覆核發現的跨產業誤收雜訊：育兒用品
# (Cybex 嬰兒汽座)、建材(石膏板)、汽車零件/車款、天文攝影(Star Trac ->
# star tracker)、人名/訃聞(Penny Schwinn)、古董單車拍賣、同名連鎖/金融
# (Onelife Fitness / Irish Life / True Yoga)。保守實作：對於過於泛用、容易
# 誤殺其他品牌新聞的詞（vintage / bmx / chopper / for sale / sting ray /
# 車款），一律要求同時搭配對應品牌詞（schwinn / nautilus / proform）才算雜訊，
# 不單獨當作全站黑名單。
# ---------------------------------------------------------------------------

CROSS_INDUSTRY_NOISE_KEYWORDS = [
    # 育兒 / 汽座
    "car seat", "car seats", "infant car seat", "infant car seats",
    "stroller", "strollers", "pushchair", "pushchairs", "pram", "prams",
    "highchair", "high chair", "baby gear", "booster seat", "booster seats",
    "嬰兒", "汽座", "推車", "安全座椅",
    # 建材
    "drywall", "joint compound", "gypsum", "national gypsum", "sheetrock",
    "wallboard", "spackling", "建材", "石膏板", "補土",
    # 汽車
    "proform racing", "brushless fan controller", "fan controller",
    "lincoln nautilus",
    # 天文攝影
    "star tracker", "star trackers", "astrophotography", "camera mount",
    "camera mounts",
    # 人名 / 訃聞
    "penny schwinn", "obituary", "obituaries", "訃聞",
    # 同名連鎖 / 金融
    "onelife fitness", "one life fitness", "irish life", "true yoga",
    # 自由車競賽（Peloton = 主集團，非品牌）
    "tour de france",  "yellow jersey", "general classification",
    "peloton of riders", "stage win", "stage race", "cycling race",
    "grand tour", "vuelta a espana", "giro d'italia",
    # 軍事演習（"exercise"/"training" 單獨字樣會被誤判為健身詞而放行，故用複合詞
    # 攔截，避免波及大量真健身文，刻意不加裸字 "exercise"）
    "national guard", "air assault", "bilateral exercise", "joint exercise",
    "military exercise", "war game", "wargame", "southern command",
    "combat readiness", "field exercise",
]
CROSS_INDUSTRY_NOISE_PATTERNS = _compile_keyword_list(CROSS_INDUSTRY_NOISE_KEYWORDS)
# 2026-07-30: "breakaway" 是自由車術語，但 Precor 有款跑步機叫 Breakaway，
# 裸字會誤殺器材新聞，改為必須搭配自由車語境才算雜訊。
CROSS_INDUSTRY_NOISE_PATTERNS += [
    re.compile(r"BREAKAWAY(?=.*(?:peloton|tour de france|cyclist|rider|stage race|uci))"
               .replace("BREAKAWAY", "\bbreakaway\b"), re.IGNORECASE),
]

# 過於泛用、需搭配品牌詞才視為雜訊的詞（古董單車拍賣 / 車款）
_SCHWINN_VINTAGE_NOISE_CTX = r"(vintage|sting ?ray|chopper|\bbmx\b|for sale|auction)"
CROSS_INDUSTRY_NOISE_PATTERNS += [
    re.compile(rf"schwinn.*{_SCHWINN_VINTAGE_NOISE_CTX}", re.IGNORECASE),
    re.compile(rf"{_SCHWINN_VINTAGE_NOISE_CTX}.*schwinn", re.IGNORECASE),
    re.compile(r"(nautilus|proform).{0,20}車款|車款.{0,20}(nautilus|proform)", re.IGNORECASE),
]


def is_cross_industry_noise(text: str) -> bool:
    return any(p.search(text) for p in CROSS_INDUSTRY_NOISE_PATTERNS)


# ---------------------------------------------------------------------------
# 服飾／鞋類過濾（使用者明確要求：運動服飾不算，除非與器材有關）
# ---------------------------------------------------------------------------

APPAREL_ONLY_KEYWORDS = [
    "leggings", "legging", "sneaker", "sneakers", "footwear", "apparel",
    "activewear", "sportswear", "athleisure",
    # 鞋類（注意：不可加 "trainer"/"trainers"，會誤殺 personal trainer / functional
    # trainer / arc trainer 等大量真健身內容）
    "shoe", "shoes", "running shoe", "training shoe", "sports bra",
    "yoga pant", "clothing line",
    "緊身褲", "運動鞋", "運動服", "機能衣", "瑜珈褲", "瑜伽褲",
]
APPAREL_ONLY_PATTERNS = _compile_keyword_list(APPAREL_ONLY_KEYWORDS)
# 2026-07-30: "jersey" 會誤中地名（New Jersey / Jersey City / Jersey Shore），
# 導致 PureGym 展店、Technogym 新辦公室等正常新聞被誤剔，改用排除地名的 regex。
APPAREL_ONLY_PATTERNS += [
    re.compile(r"(?<!new )\bjerseys?\b(?! city| shore)", re.IGNORECASE),
]

# 器材豁免詞：若標題/摘要同時命中器材詞，即使命中服飾詞也保留
# （特例：Lululemon Mirror 智慧健身鏡需保留 -> "mirror" 為豁免詞之一）
EQUIPMENT_EXEMPT_KEYWORDS = [
    "treadmill", "treadmills", "rack", "dumbbell", "dumbbells", "mirror",
    "bike", "bikes", "machine", "machines", "gym equipment",
    "elliptical", "rower", "rowing machine", "kettlebell", "barbell",
    "home gym", "functional trainer",
    "跑步機", "器材", "重訓架", "啞鈴", "健身鏡",
]
EQUIPMENT_EXEMPT_PATTERNS = _compile_keyword_list(EQUIPMENT_EXEMPT_KEYWORDS)


def is_apparel_noise(text: str) -> bool:
    """服飾/鞋類新聞判定：命中服飾詞且未命中器材豁免詞才視為雜訊剔除。
    器材品牌自己出的純服飾新聞（如 Peloton 服飾線）預設也排除（使用者選擇）。"""
    if not any(p.search(text) for p in APPAREL_ONLY_PATTERNS):
        return False
    if any(p.search(text) for p in EQUIPMENT_EXEMPT_PATTERNS):
        return False
    return True


MARKETPLACE_SOURCES = {
    "santoandre.biz", "consumerthai", "tuitec.com", "ebay", "craigslist",
    "facebook marketplace", "gumtree", "offerup", "mercari", "poshmark",
}
MARKETPLACE_CONTENT_PATTERNS = [
    re.compile(r"\breplacement part\b", re.IGNORECASE),
    re.compile(r"\bspare parts?\b", re.IGNORECASE),
    re.compile(r"\bfor parts\b", re.IGNORECASE),
    re.compile(r"\bend cap\b", re.IGNORECASE),
]


def source_matches(source: str, name_list) -> bool:
    s = (source or "").strip().lower()
    if not s:
        return False
    return any(name in s for name in name_list)


# ---------------------------------------------------------------------------
# 品牌名稱歧義過濾
# ---------------------------------------------------------------------------
# 「舒華」同時是中國健身器材品牌「舒華體育」(SHUA, 股票代號 605299) 與南韓女團
# (G)I-DLE 台灣籍成員「葉舒華」的中文藝名。即使 Google News 查詢已加引號做
# phrase match（見 CHINESE_QUERIES / CN_REGION_QUERIES），仍以此二次過濾把關：
# 1) 命中演藝圈雜訊關鍵字 -> 硬性剔除（優先於品牌命中）。
# 2) 品牌詞有歧義者，標題/摘要需另外命中「品牌情境詞」白名單才收錄。
# 目前僅「舒華」需要這層保護；未來如發現其他品牌有同名歧義，可比照擴充。
# ---------------------------------------------------------------------------

CELEBRITY_NOISE_KEYWORDS = [
    "i-dle", "(g)i-dle", "葉舒華", "叶舒华", "女團", "女团", "愛豆", "爱豆",
    "偶像團體", "偶像团体", "演唱會", "演唱会", "粉絲", "粉丝", "專輯", "专辑",
    "綜藝", "综艺", "柯震東", "柯震东", "見面會", "见面会", "代言人", "時裝週",
    "时装周", "走秀", "韓星", "韩星", "k-pop", "kpop", "cube娛樂", "cube娱乐",
    "burberry", "巡演", "回歸專輯", "回归专辑", "韓團", "韩团",
]
CELEBRITY_NOISE_PATTERN = re.compile(
    "|".join(re.escape(k) for k in CELEBRITY_NOISE_KEYWORDS), re.IGNORECASE)

# 有歧義的品牌：標題/摘要需命中以下任一「品牌情境詞」才視為真正相關
# 此白名單同時是既有庫存回溯清理的依據（見 clean_existing_noise()）：擴充此字典
# 即可讓 clean_existing_noise() 自動對「已入庫」的舊資料套用同一組規則。
BRAND_WHITELIST_CONTEXT = {
    "SHUA": re.compile(
        r"舒華體育|舒华体育|shua fitness|健身器材|跑步機|跑步机|橢圓機|椭圆机|飛輪車|飞轮车|"
        r"重訓器材|重训器材|有氧器材|健身房|運動器材|运动器材|喬山|乔山|dyaco|岱宇|605299",
        re.IGNORECASE,
    ),
    # CYBEX GmbH（嬰兒汽座/推車/童裝，隸屬 Goodbaby International）與
    # 健身器材 Cybex（原 Cybex International, Inc.）同名，實測 97.2% 雜訊。
    "Cybex": re.compile(
        r"cybex (fitness|international|eagle|smart|vr3|prestige|bravo|squat|treadmill|"
        r"elliptical|strength|gym|cardio|trainer)|arc trainer|strength training equipment|"
        r"fitness equipment|gym equipment|cardio equipment|exercise equipment|home gym|"
        r"commercial gym|selectorized|plate-loaded|functional trainer",
        re.IGNORECASE,
    ),
    # PROFORM 汽車風扇控制器品牌、ProForm Bike Shop 店名 與 ProForm 健身器材(NordicTrack
    # 集團/iFit) 同名，實測 33.3% 雜訊。
    "ProForm": re.compile(
        r"proform (carbon|pro\b|tour|cadence|ifit|treadmill|elliptical|trainer|studio|"
        r"fitness)|ifit|treadmill|elliptical|exercise bike|stationary bike|home gym|"
        r"fitness equipment|gym equipment|cardio equipment|strength training|"
        r"nordictrack|icon health"
        r"|rowing machine|rowing|rower|air rower|bike|cycle",
        re.IGNORECASE,
    ),
    # "true fitness level" 通用片語、斐濟/新加坡/台灣同名健身房連鎖 與 TRUE Fitness
    # (美國商用跑步機/橢圓機製造商，St. Louis) 同名，實測 100% 雜訊。
    "TRUE Fitness": re.compile(
        r"true fitness (technology|treadmill|elliptical|equipment|commercial|inc)|"
        r"true treadmill|true trainer|trueform runner|true force|st\. louis.{0,30}true|"
        r"treadmill manufacturer|commercial fitness equipment|cardio equipment|"
        r"strength equipment",
        re.IGNORECASE,
    ),
    # Penny Schwinn（美國教育部官員）、訃聞、古董單車拍賣(Sting Ray/Chopper/BMX)、單車
    # 失竊、品牌紀錄片 與 Schwinn Fitness（室內健身車/飛輪，隸屬 Nautilus/BowFlex）同名，
    # 實測 89.5% 雜訊。
    "Schwinn": re.compile(
        r"schwinn (fitness|indoor cycling|exercise bike|spin bike|airdyne|recumbent|"
        r"upright bike|elliptical|treadmill|ic\d)|schwinn ic\d|schwinn airdyne|"
        r"indoor cycling bike|exercise bike|spin bike|fitness equipment|home gym equipment",
        re.IGNORECASE,
    ),
    # star tracker 天文攝影器材 與 Star Trac 健身器材同名，實測 100% 雜訊（僅 1 筆但同樣套用）。
    "Star Trac": re.compile(
        r"star trac (fitness|treadmill|elliptical|strength|cardio|gym|equipment)|"
        r"core health.{0,15}fitness|treadmill|elliptical|cardio equipment|"
        r"fitness equipment|gym equipment",
        re.IGNORECASE,
    ),
    # 同名健身房場館 與 Nautilus, Inc.（健身器材上市公司，現更名 BowFlex）同名，實測 66.7% 雜訊。
    "Nautilus": re.compile(
        r"nautilus (fitness|inc|treadmill|elliptical|bowflex|schwinn|strength|cardio)|"
        r"nautilus, inc|nautilus bowflex|bowflex|schwinn fitness|treadmill|elliptical|"
        r"home gym equipment|fitness equipment|gym equipment|exercise equipment",
        re.IGNORECASE,
    ),
    # Onelife Fitness（另一連鎖）、Irish Life 壽險、"life"+"fitness" 通用片語巧合 與
    # Life Fitness（健身器材品牌，隸屬喬山）同名，實測 ~32% 雜訊。
    "Life Fitness": re.compile(
        r"life fitness (inc|llc|equipment|treadmill|elliptical|strength|cardio|commercial)|"
        r"life fitness, inc|hammer strength|les mills|commercial fitness equipment|"
        r"gym equipment|fitness equipment|treadmill|elliptical|strength training equipment|"
        r"cardio equipment",
        re.IGNORECASE,
    ),
    # 環法自由車等賽事報導中的「主集團 peloton」與 Peloton Interactive（健身器材/App）
    # 同名，實測僅 0.4% 雜訊，此處情境詞刻意放寬避免誤刪大量正常新聞。
    "Peloton": re.compile(
        r"peloton (interactive|bike|bikes|tread|app|instructor|subscriber|subscribers|"
        r"membership|fitness|workout|class|classes|studio|row|rower|strength|earnings|"
        r"revenue|stock|shares|ceo)|onepeloton|nyse: ?pton|\bpton\b|connected fitness|"
        r"fitness equipment|home gym|exercise bike|stationary bike|indoor cycling",
        re.IGNORECASE,
    ),
    # Tonal（智慧重訓器材）與英文常用字 "tonal"（色調/劇情基調/服裝配色/音調）同名，
    # 裸字偵測後靠此白名單把關：僅命中公司動態/產品/健身情境詞才視為真品牌新聞。
    "Tonal": re.compile(
        r"appoints?|chief executive|\bceo\b|launch(?:es|ed)?|expand(?:s|ed)?|\badds?\b|"
        r"\breport\b|refurbished|connected fitness|\bstrength\b|\bworkouts?\b|home gym|"
        r"\bpilates\b|smart gym|takes helm|vs tonal|ankle straps?|"
        r"generated workouts?|ai-powered|state of strength|\breformer\b|private equity",
        re.IGNORECASE,
    ),
}


# ---------------------------------------------------------------------------
# 2026-07-30 過嚴修正（依 4,738 筆實測雜訊率調校）
# ---------------------------------------------------------------------------
# Peloton 實測雜訊率 0.4%、Life Fitness 的雜訊（Onelife Fitness / Irish Life）已由
# CROSS_INDUSTRY_NOISE 與 "life fitness" 字界攔截，不需要「必須命中情境詞」的嚴格
# 白名單。先前納入白名單導致 Peloton 誤剔 422 筆（財報/課程/器材評測皆被誤殺）。
for _k in ("Peloton", "Life Fitness"):
    BRAND_WHITELIST_CONTEXT.pop(_k, None)

# Tonal：要求正面情境詞會誤殺大量真產品新聞（Tonal 2 Review / Tonal App / Drop Sets
# 等 10/20 被誤剔）。改為移出白名單，另以「tonal 當普通形容詞」的負面詞組精準排除。
BRAND_WHITELIST_CONTEXT.pop("Tonal", None)
CROSS_INDUSTRY_NOISE_PATTERNS += [
    re.compile(r"tonal (?:shift|shifts|balance|dressing|colorway|colourway|"
               r"about-face|extremes|journey|range|quality|palette|contrast|"
               r"variation|variations|language|nuance|nuances)", re.IGNORECASE),
    re.compile(r"(?:experimented|experimenting|playing) with tonal", re.IGNORECASE),
]



def check_exclusion(title: str, summary: str, brand, source: str = "", relax: bool = False):
    """
    回傳 (excluded: bool, reason: str | None)。
    硬性剔除（永遠生效，優先權高於「brand 命中即放行」，即使有品牌也照樣檢查）：
        spam 來源/內容、明顯 off-topic 主題、有歧義品牌命中演藝雜訊、跨產業同名詞雜訊
        （CROSS_INDUSTRY_NOISE，如嬰兒汽座/建材/天文攝影/人名訃聞/古董單車/同名連鎖）、
        純服飾/鞋類新聞（APPAREL_ONLY_NOISE，除非同時命中器材豁免詞）。
    品牌歧義白名單（永遠生效）：品牌詞有歧義者，需另外命中品牌情境詞才收錄。
    正向保留閘門（relax=False 時生效）：不命中品牌且不含健身相關詞 -> no_relevance。
    官方/新品/新聞稿來源以 relax=True 呼叫（本質相關，不套用 no_relevance 閘門，但仍套用
    上述所有硬性剔除規則）。

    根因修補（2026-07-30）：舊版「if brand: return False, None」會讓任何命中品牌 regex 的
    項目直接跳過健身相關性/跨產業黑名單檢查，導致 Cybex(嬰兒汽座)、ProForm(汽車風扇控制器)、
    Schwinn(人名/訃聞/古董單車) 等跨產業同名詞雜訊被誤收。現在跨產業黑名單與服飾過濾一律
    優先於 brand 放行邏輯執行。
    """
    s = (source or "").strip().lower()
    text = f"{title} {summary}"

    if s in MARKETPLACE_SOURCES or source_matches(source, MARKETPLACE_SOURCES):
        return True, "marketplace_spam"
    if any(p.search(text) for p in MARKETPLACE_CONTENT_PATTERNS):
        return True, "marketplace_spam"

    if brand in BRAND_WHITELIST_CONTEXT:
        if CELEBRITY_NOISE_PATTERN.search(text):
            return True, "celebrity_noise"
        if not BRAND_WHITELIST_CONTEXT[brand].search(text):
            return True, "brand_ambiguous_no_context"

    # 跨產業同名詞黑名單 / 服飾鞋類過濾：優先權高於「brand 命中即放行」，
    # 即使偵測到 brand 也要先過這兩關（修補根因 B）。
    if is_cross_industry_noise(text):
        return True, "cross_industry_noise"
    if is_apparel_noise(text):
        return True, "apparel_noise"

    if not brand and any(p.search(text) for p in OFFTOPIC_PATTERNS):
        return True, "off_topic"

    if not relax:
        if brand:
            return False, None
        if has_fitness_term(text):
            return False, None
        return True, "no_relevance"

    return False, None


# ---------------------------------------------------------------------------
# 官方「消息型新聞」判定（只收公司動態/新品發表消息/活動/合作/獲獎；剔除 how-to/教學/
# 產品目錄/分類/購物/食譜/評測等非消息型內容）
# ---------------------------------------------------------------------------
#
# 規則：命中 OFFICIAL_NEWS_POSITIVE（消息型訊號）且未命中 OFFICIAL_HOWTO_NEGATIVE
# （教學/指南/食譜/評測等訊號）者，才視為「官方消息型新聞」。
# 只適用於 source_type="official"（品牌官方站抓來的項目）；google_news / press_release
# 為 RSS 真實新聞，一律不套此篩選。
# ---------------------------------------------------------------------------

OFFICIAL_NEWS_POSITIVE = re.compile(
    r"\blaunch(?:es|ed|ing)?\b|\bunveil(?:s|ed|ing)?\b|\bintroduc(?:e|es|ed|ing|tion)?\b|"
    r"\bannounc(?:e|es|ed|ing|ement)?\b|\bdebut(?:s|ed|ing)?\b|\breveal(?:s|ed|ing)?\b|"
    r"\bopens?\b|\bopened\b|\bopening\b|\bpop-?up\b|\bpartner(?:s|ed|ship|ing)?\b|"
    r"\bcollaborat\w*|\bteams? up\b|\bacqui(?:re|res|red|sition)\b|\bmerger\b|"
    r"\bexpand(?:s|ed|ing)?\b|\bexpansion\b|\baward(?:s|ed)?\b|\bwins?\b|\bwon\b|"
    r"\bnamed\b|\bhonou?red\b|\brecogni[sz]ed\b|\bappoint(?:s|ed|ment)?\b|\bhire[sd]?\b|"
    r"\bjoins?\b|\bwelcomes?\b|\bchampionship\b|\btournament\b|\bexpo\b|\btrade show\b|"
    r"\bmilestone\b|\banniversary\b|\bcelebrat\w*|\bnow available\b|\bcoming soon\b|"
    r"\bfirst look\b|\bjust dropped\b|\bindex\b|\bdow jones\b|\bsustainability\b|"
    r"\bearnings\b|\brevenue\b|\bfunding\b|\bpartnership\b|\breimagined?\b|\breturns?\b|"
    r"\bintelligent assistant\b|\bai (?:assistant|coach|trainer)\b|"
    r"\bnew (?:\w+ ){0,2}(?:product|feature|features|model|models|line|machine|machines|"
    r"coach|coaches|series|store|facility|assistant|metric|metrics|program|programs|"
    r"programming|drop sets|ankle straps?|console)\b|\bnew ways to track\b|"
    r"\b(?:tonal|ultra) \d\b",
    re.IGNORECASE,
)

OFFICIAL_HOWTO_NEGATIVE = re.compile(
    r"\bhow to\b|\bhow (?:can|do|does|pilates|sleep|gyms?|personalized)\b|"
    r"\btips?\b|\bguide\b|\bbeginners?\b|\btechnique\b|\btutorial\b|\bworkouts?\b|"
    r"\bexercises?\b|\bbenefits? of\b|\bways to (?:break|take|adapt|start)\b|"
    r"\btop \d|\breview\b|\bcomparison\b|\bvs\.?\b|\brecipe\b|\bmake-ahead\b|"
    r"\bhigh-protein\b|\bnutrition\b|\bmeal\b|\bbreakfast\b|\bfrittata\b|\bstir-fry\b|"
    r"\bmeatballs\b|\bbranzino\b|\bcream of rice\b|\bquinoa\b|\bshould you\b|"
    r"\bwhat (?:is|are|active|machine)\b|\bwhy \b|\broutine\b|\bproper \b|\bmaintenance\b|"
    r"\bgetting started\b|\bkickstart\b|\bshredded\b|\bslump\b|\bleg day\b|\bsandbag\b|"
    r"\bsquat\b|\bdeadlift\b|\bbench press\b|\bpregnancy\b|\brecovery\b|\bgift (?:guide|ideas|picks)\b|"
    r"\bfor men\b|\bfor beginners\b|\bbest (?:home|gym|dumbbell|barbell|treadmill|workout|"
    r"exercise|machine|way|time|pilates|cardio|selectorized)\b|\bkiller\b|\balternatives\b|"
    r"\bmust-have\b|\bpower-packed\b|\bstrengthen your\b|\bease lower back\b|\bsupercharge\b|"
    r"\belevate your\b|\bmaster the\b|\bunlock\b|\bchannel your\b|\bstay on track\b|"
    r"\bset up an?\b|\bfueling\b|\bovertraining\b|\bstretching\b|\bmobility\b",
    re.IGNORECASE,
)


def looks_like_official_news(title: str, summary: str = "") -> bool:
    """判斷官方站抓來的項目是否為「消息型新聞」。命中消息訊號且未命中教學訊號才為 True。
    僅以「標題」判定：官方文章的 summary 多為整段內文，含大量通用詞會污染判斷；
    是否為消息型/教學型幾乎都能由標題看出（符合使用者所舉的判別例子）。"""
    text = (title or "").strip()
    if OFFICIAL_HOWTO_NEGATIVE.search(text):
        return False
    return bool(OFFICIAL_NEWS_POSITIVE.search(text))


# ---------------------------------------------------------------------------
# 文章頁真實發佈日期擷取
# ---------------------------------------------------------------------------
#
# 依序嘗試：meta[property=article:published_time] -> JSON-LD datePublished ->
# <time datetime> -> meta[property=og:updated_time] -> 常見 meta/日期 class。
# 解析到「合理」日期（<= 今天、>= 2000）回傳 'YYYY-MM-DD'，否則回傳 None。
# 絕不回傳「今天」當作 fallback（拿不到就回 None，由呼叫端決定略過/移除）。
# ---------------------------------------------------------------------------

_DATE_YMD = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")
_DATE_SLASH = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"], start=1)}
_MONTHS.update({m[:3]: i for m, i in list(_MONTHS.items())})
_DATE_TEXT = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2}),?\s+(\d{4})\b",
    re.IGNORECASE,
)
_DATE_TEXT2 = re.compile(
    r"\b(\d{1,2})\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{4})\b",
    re.IGNORECASE,
)


def normalize_date_str(s: str):
    """把各種日期字串正規化為 'YYYY-MM-DD'；不合理或無法解析回傳 None。"""
    if not s:
        return None
    s = str(s).strip()
    y = mo = d = None
    m = _DATE_YMD.search(s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if y is None:
        m = _DATE_TEXT.search(s)
        if m:
            mo = _MONTHS.get(m.group(1).lower()[:3]); d = int(m.group(2)); y = int(m.group(3))
    if y is None:
        m = _DATE_TEXT2.search(s)
        if m:
            d = int(m.group(1)); mo = _MONTHS.get(m.group(2).lower()[:3]); y = int(m.group(3))
    if y is None:
        m = _DATE_SLASH.search(s)
        if m:  # 假定 m/d/Y（多數英文站）
            mo = int(m.group(1)); d = int(m.group(2)); y = int(m.group(3))
    if y is None:
        try:
            dt = parsedate_to_datetime(s)
            y, mo, d = dt.year, dt.month, dt.day
        except Exception:
            return None
    if not (mo and d and 2000 <= y <= 2035 and 1 <= mo <= 12 and 1 <= d <= 31):
        return None
    result = f"{y:04d}-{mo:02d}-{d:02d}"
    # 不接受未來日期（超過今天的視為不可靠）
    if result > RUN_DATE:
        return None
    return result


def _jsonld_find_date(obj):
    if isinstance(obj, dict):
        for key in ("datePublished", "dateCreated", "datePosted", "uploadDate"):
            if obj.get(key):
                return obj[key]
        for v in obj.values():
            r = _jsonld_find_date(v)
            if r:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _jsonld_find_date(v)
            if r:
                return r
    return None


def extract_published_date(url: str):
    """抓「文章頁本身」解析真實發佈日期，回傳 'YYYY-MM-DD' 或 None（拿不到不回今天）。"""
    resp = fetch_fast(url)
    if resp is None:
        return None
    try:
        soup = BeautifulSoup(resp.content, "html.parser")
    except Exception:
        return None

    # 1) meta[property=article:published_time] / 常見發佈時間 meta
    meta_keys = [
        ("property", "article:published_time"),
        ("name", "article:published_time"),
        ("itemprop", "datePublished"),
        ("name", "parsely-pub-date"),
        ("name", "publishdate"),
        ("name", "publish-date"),
        ("name", "pubdate"),
        ("name", "date"),
    ]
    for attr, val in meta_keys:
        tag = soup.find("meta", attrs={attr: val})
        if tag and tag.get("content"):
            got = normalize_date_str(tag["content"])
            if got:
                return got

    # 2) JSON-LD datePublished
    for s in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = s.string or s.get_text() or ""
        if not raw.strip():
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        cand = _jsonld_find_date(data)
        if cand:
            got = normalize_date_str(cand)
            if got:
                return got

    # 3) <time datetime=...>
    for t in soup.find_all("time"):
        dt = t.get("datetime") or t.get_text()
        got = normalize_date_str(dt)
        if got:
            return got

    # 4) og:updated_time
    for attr, val in [("property", "og:updated_time"), ("property", "og:published_time")]:
        tag = soup.find("meta", attrs={attr: val})
        if tag and tag.get("content"):
            got = normalize_date_str(tag["content"])
            if got:
                return got

    # 5) 常見日期 class
    for el in soup.find_all(attrs={"class": re.compile(
            r"(published|post-date|entry-date|article-date|date)", re.IGNORECASE)}):
        got = normalize_date_str(el.get_text(" ", strip=True))
        if got:
            return got

    return None


# ---------------------------------------------------------------------------
# Google News 真實摘要回填（--enrich-summary）
# ---------------------------------------------------------------------------
#
# 背景：Google News RSS 的 <description> 只有「標題＋來源名稱」，導致
# source_type=google_news 的 summary 全部等於標題（非解析 bug，是來源限制，見
# entry_to_raw()）。本區塊把 Google News 不透明轉址網址
# （news.google.com/rss/articles/...）還原成原文網址，再抓原文頁的 meta
# description 當真實摘要。僅供 --enrich-summary / --enrich-dry-run 這個獨立
# 回填流程使用，完全不影響一般排程抓取（entry_to_raw / build_new_articles 的
# summary 邏輯不變）。
#
# 兩步還原法（實測可行）：
#   1) GET 文章轉址頁（帶 hl/gl/ceid），從 HTML 抓 data-n-a-ts / data-n-a-sg。
#   2) POST batchexecute，帶入 article id + ts + sg，回應內含原文網址。
# ---------------------------------------------------------------------------

# v7 原文與摘要：保留來源證據；不生成假摘要，不把媒體首頁當文章。
ENRICH_TIMEOUT = (5, 12)
ENRICH_SLEEP_MIN = 1.5
ENRICH_SLEEP_MAX = 2.2
ENRICH_MAX_RETRIES = 2
ENRICH_SUCCESS_SOURCES = {'og_description','meta_description','twitter_description','jsonld_description','article_paragraph','rss_description'}
ENRICH_CHECKPOINT_EVERY = 25
_ENRICH_HTTP_ERRORS = {}
_HOST_COOLDOWNS = {}
_RESOLVE_CACHE = {}
_DESCRIPTION_CACHE = {}
ENRICH_VERSION = 'v7.1'


def enrich_sleep():
    time.sleep(random.uniform(ENRICH_SLEEP_MIN, ENRICH_SLEEP_MAX))


def is_google_news(url):
    return _hostname_v3(url) == 'news.google.com'


def valid_article_url(url):
    try:
        p=urlparse(url or '')
        return (p.scheme in ('http','https') and bool(p.hostname) and not p.username
                and p.hostname not in ('news.google.com','consent.google.com','accounts.google.com')
                and p.path.strip('/') != '' and not re.search(r'[\s<>"\\]',url))
    except (ValueError,TypeError):
        return False


def _enrich_get(url, accept='text/html,application/xhtml+xml'):
    host=_hostname_v3(url)
    if _HOST_COOLDOWNS.get(host,0)>time.time():
        _ENRICH_HTTP_ERRORS[url]='host_cooldown'; return None
    for attempt in range(ENRICH_MAX_RETRIES):
        try:
            resp=requests.get(url,headers={'User-Agent':USER_AGENT,'Accept':accept},timeout=ENRICH_TIMEOUT)
            if resp.status_code==200:
                _ENRICH_HTTP_ERRORS.pop(url,None); return resp
            _ENRICH_HTTP_ERRORS[url]=f'http_{resp.status_code}'
            if resp.status_code==429:
                try: delay=max(300,int(resp.headers.get('Retry-After','300')))
                except ValueError: delay=900
                _HOST_COOLDOWNS[host]=time.time()+min(delay,86400)
            if resp.status_code in (401,403,404,410,429): return None
        except requests.RequestException as e:
            _ENRICH_HTTP_ERRORS[url]=type(e).__name__
        if attempt+1<ENRICH_MAX_RETRIES: enrich_sleep()
    return None


def decode_legacy_google_url(url):
    """只接受 protobuf length-delimited 裡的完整 http URL；新不透明 ID 留給線上解析。"""
    if not is_google_news(url): return None
    m=re.search(r'/(?:rss/)?articles/([^/?]+)',url)
    if not m: return None
    try:
        raw=base64.urlsafe_b64decode(m[1]+'='*(-len(m[1])%4))
        i=0
        def varint(pos):
            value=0; shift=0
            while pos<len(raw) and shift<64:
                b=raw[pos]; pos+=1; value|=(b&127)<<shift
                if not b&128: return value,pos
                shift+=7
            raise ValueError('invalid protobuf varint')
        while i<len(raw):
            key,i=varint(i); wire=key&7
            if wire==0: _,i=varint(i)
            elif wire==2:
                n,i=varint(i); value=raw[i:i+n]; i+=n
                if len(value)!=n: return None
                candidate=value.decode('utf-8',errors='ignore')
                if valid_article_url(candidate): return candidate
            elif wire==1: i+=8
            elif wire==5: i+=4
            else: break
    except (ValueError,UnicodeError): pass
    return None


def parse_google_rpc(text):
    """分層 JSON 解析 RPC；只取 garturlres 的 URL，避免 regex 誤抓跳脫字元與無關 URL。"""
    decoder=json.JSONDecoder()
    def walk(obj,depth=0):
        if depth>8: return None
        if isinstance(obj,list):
            if len(obj)>1 and obj[0]=='garturlres' and isinstance(obj[1],str) and valid_article_url(obj[1]): return obj[1]
            for v in obj:
                result=walk(v,depth+1)
                if result: return result
        elif isinstance(obj,str) and obj.lstrip().startswith(('[','{')):
            try: return walk(json.loads(obj),depth+1)
            except ValueError: pass
        return None
    for m in re.finditer(r'\[',text):
        try: obj,_=decoder.raw_decode(text[m.start():])
        except ValueError: continue
        result=walk(obj)
        if result: return result
    return None


def resolve_google_news_url(url):
    if not is_google_news(url): return url if valid_article_url(url) else None
    if url in _RESOLVE_CACHE: return _RESOLVE_CACHE[url]
    legacy=decode_legacy_google_url(url)
    if legacy:
        _RESOLVE_CACHE[url]=legacy; return legacy
    m=re.search(r'/(?:rss/)?articles/([^/?]+)',url)
    if not m: return None
    article_id=m[1]
    resp=_enrich_get(url)
    if resp is None: return None
    if valid_article_url(resp.url):
        _RESOLVE_CACHE[url]=resp.url; return resp.url
    soup=BeautifulSoup(resp.content,'html.parser')
    sig=soup.select_one('[data-n-a-sg][data-n-a-ts]')
    if not sig:
        # 某些版本只在 /articles/ 頁提供 signature；遇到封鎖不改端點重試。
        alternate='https://news.google.com/articles/'+article_id
        if urlparse(url).path.startswith('/rss/'):
            enrich_sleep(); other=_enrich_get(alternate)
            if other is not None:
                if valid_article_url(other.url): return other.url
                sig=BeautifulSoup(other.content,'html.parser').select_one('[data-n-a-sg][data-n-a-ts]')
        if not sig:
            _ENRICH_HTTP_ERRORS[url]='google_signature_missing'; return None
    try:
        inner=['garturlreq',[["en-US","US",["FINANCE_TOP_INDICES","WEB_TEST_1_0_0"],None,None,1,1,"US:en",None,180,None,None,None,None,None,0,None,None,[1608992183,723341000]],"en-US","US",1,[2,3,4,8],1,0,"655000234",0,0,None,0],article_id,int(sig['data-n-a-ts']),sig['data-n-a-sg']]
        payload=json.dumps([[['Fbv4je',json.dumps(inner,separators=(',',':')),None,'generic']]],separators=(',',':'))
        enrich_sleep()
        rpc=requests.post('https://news.google.com/_/DotsSplashUi/data/batchexecute',
            params={'rpcids':'Fbv4je'},data={'f.req':payload},headers={'User-Agent':USER_AGENT},timeout=ENRICH_TIMEOUT)
        if rpc.status_code!=200:
            _ENRICH_HTTP_ERRORS[url]=f'google_rpc_http_{rpc.status_code}'
            if rpc.status_code==429: _HOST_COOLDOWNS['news.google.com']=time.time()+900
            return None
        result=parse_google_rpc(rpc.text)
        if result: _RESOLVE_CACHE[url]=result
        else: _ENRICH_HTTP_ERRORS[url]='google_rpc_unresolved'
        return result
    except (requests.RequestException,ValueError,KeyError) as e:
        _ENRICH_HTTP_ERRORS[url]=type(e).__name__; return None


def _summary_text(text):
    return re.sub(r'\s+',' ',unescape(BeautifulSoup(text or '', 'html.parser').get_text(' ',strip=True))).strip()


def usable_summary(text, title='', source=''):
    text=unicodedata.normalize('NFKC',_summary_text(text))
    title=unicodedata.normalize('NFKC',_summary_text(title))
    if len(text)<24: return ''
    # 標題/摘要常有 NFKC、破折號、引號或媒體後綴差異；比對時忽略標點。
    def key(value): return re.sub(r'[^\w]','',value.casefold())
    text_key,title_key=key(text),key(title)
    if title_key and text_key.startswith(title_key):
        tail_key=text_key[len(title_key):]
        if len(tail_key)<24 or tail_key==key(source or ''): return ''
    if title and text.casefold().startswith(title.casefold()):
        tail=text[len(title):].strip(' -–—|:：')
        if len(tail)<30 or tail.casefold()==unicodedata.normalize('NFKC',source or '').casefold(): return ''
    if re.match(r'(?i)^(?:accept (?:all )?cookies|we use cookies|cookie policy|enable javascript|access denied|just a moment|subscribe to (?:our|the) newsletter|sign in to continue|please enable)',text): return ''
    if re.search(r'(?i)(?:captcha|verify (?:that )?you are human|browser is not supported)',text): return ''
    if re.match(r'(?i)^(?:you are (?:using|viewing)|your browser)',text): return ''
    if re.search(r'(?i)(?:select market data provided|FactSet Research Systems|decrease font size|increase font size|browser does not support JavaScript|sign up with your email)',text): return ''
    if re.match(r'(?i)^(?:partly cloudy skies|cloudy skies|mostly sunny|NSE:|the magazine of hip hop|By .{0,60} News Network -)',text): return ''
    return text[:700]


def _valid_enriched_summary(text,title):
    return bool(usable_summary(text,title))


def extract_description(html,title,source=''):
    soup=BeautifulSoup(html,'html.parser')
    for label,key in [('og_description','og:description'),('meta_description','description'),('twitter_description','twitter:description')]:
        for tag in soup.find_all('meta'):
            if str(tag.get('property') or tag.get('name') or '').lower()==key:
                value=usable_summary(tag.get('content',''),title,source)
                if value: return value,label
    def nodes(obj):
        if isinstance(obj,dict):
            yield obj
            for value in obj.values(): yield from nodes(value)
        elif isinstance(obj,list):
            for value in obj: yield from nodes(value)
    for script in soup.find_all('script',type='application/ld+json'):
        try: obj=json.loads(script.string or script.get_text())
        except (ValueError,TypeError): continue
        for node in nodes(obj):
            types=node.get('@type',[]); types=[types] if isinstance(types,str) else types
            if not any(x in ('Article','NewsArticle','BlogPosting','Report','TechArticle') for x in types or []): continue
            desc=node.get('description','')
            if isinstance(desc,str):
                value=usable_summary(desc,title,source)
                if value: return value,'jsonld_description'
    for junk in soup.select('script,style,nav,header,footer,aside,form,[role="navigation"],.cookie-banner,.newsletter,.related-posts'):
        junk.decompose()
    root=soup.select_one('[itemprop="articleBody"],.article-body,.entry-content,.post-content,article')
    if root:
        for p in root.find_all('p'):
            if len(p.get_text(' ',strip=True))<40: continue
            if sum(len(a.get_text()) for a in p.find_all('a'))>len(p.get_text())*.5: continue
            value=usable_summary(p.get_text(' ',strip=True),title,source)
            if value: return value,'article_paragraph'
    return None,None


def fetch_real_description(url,title):
    key=(url,title)
    if key in _DESCRIPTION_CACHE: return _DESCRIPTION_CACHE[key]
    resp=_enrich_get(url)
    if resp is None: return None,None
    if is_google_news(resp.url) or 'consent.' in _hostname_v3(resp.url): return None,None
    if 'text/html' not in resp.headers.get('Content-Type','text/html') and 'xhtml' not in resp.headers.get('Content-Type',''): return None,None
    result=extract_description(resp.content,title)
    _DESCRIPTION_CACHE[key]=result
    return result


def normalize_article_content(a):
    title=a.get('title',''); source=a.get('source','')
    candidate=a.get('summary') or ''
    summary=usable_summary(candidate,title,source)
    if not summary and a.get('description'):
        summary=usable_summary(a['description'],title,source)
    if not summary and candidate:
        a.setdefault('raw_summary',candidate)
    a['summary']=summary
    a['description']=summary  # 前端讀 summary；description 為其他消費端提供同值。
    if not summary: a['summary_source']='missing'
    elif a.get('summary_source') in (None,'rss_fallback','missing'): a['summary_source']='rss_description'
    resolved=a.get('resolved_url')
    if not valid_article_url(resolved):
        resolved=decode_legacy_google_url(a.get('url','')) if is_google_news(a.get('url')) else (a.get('url') if valid_article_url(a.get('url')) else None)
    a['resolved_url']=resolved
    a['original_url']=resolved
    a['link_status']='resolved' if resolved else 'unresolved'
    a['summary_status']='available' if summary else 'missing'
    a['enrich_status']='complete' if resolved and summary else ('partial' if resolved or summary else 'pending')
    if resolved and summary:
        a['enrich_error']=None
        a['enrich_next_retry_at']=None
    return a


def enrich_summary(article):
    a=normalize_article_content(dict(article))
    url=a.get('url',''); resolved=a.get('resolved_url')
    if not resolved: resolved=resolve_google_news_url(url)
    if resolved:
        a['resolved_url']=resolved; a['original_url']=resolved; a['link_status']='resolved'
        if not a['summary']:
            enrich_sleep()
            summary,kind=fetch_real_description(resolved,a.get('title',''))
            if summary:
                a['summary']=summary; a['description']=summary; a['summary_source']=kind; a['summary_status']='available'
    success=bool(a.get('resolved_url') and a.get('summary'))
    a['enrich_status']='complete' if success else ('partial' if a.get('resolved_url') or a.get('summary') else 'pending')
    a['enrich_error']=None if success else (_ENRICH_HTTP_ERRORS.get(resolved or url) or _ENRICH_HTTP_ERRORS.get(url) or ('description_not_found' if resolved else 'original_url_unresolved'))
    now=datetime.now(timezone.utc)
    a['enrich_checked_at']=now.isoformat()
    a['enrich_attempts']=(article.get('enrich_attempts') or 0)+1
    a['enrich_version']=ENRICH_VERSION
    delay=7 if a.get('enrich_error') in ('http_403','http_404','http_410') else 1
    a['enrich_next_retry_at']=None if success else (now+timedelta(days=delay)).isoformat()
    return classify_article(a)


def select_articles_for_enrich(articles,limit,since):
    now=datetime.now(timezone.utc).isoformat()
    pool=[]
    for a in articles:
        if a.get('is_noise'): continue
        if since and (a.get('date') or '')<since: continue
        if a.get('resolved_url') and a.get('summary'): continue
        # 舊版累積兩次失敗不再永久封鎖；新版本可重試，之後依冷卻期。
        if a.get('enrich_version')==ENRICH_VERSION and (a.get('enrich_next_retry_at') or '')>now: continue
        pool.append(a)
    pool.sort(key=lambda a:(-(bool(a.get('resolved_url'))), tuple(-ord(c) for c in a.get('date','')),a.get('enrich_attempts',0)))
    return pool[:limit] if limit>0 else pool


def enrich_batch(articles,limit=100,since=None,budget_seconds=600,checkpoint=None):
    selected=select_articles_for_enrich(articles,limit,since)
    deadline=time.monotonic()+budget_seconds if budget_seconds>0 else float('inf')
    done=0
    for a in selected:
        if time.monotonic()>=deadline: break
        if not a.get('resolved_url') and is_google_news(a.get('url')) and _HOST_COOLDOWNS.get('news.google.com',0)>time.time(): continue
        try:
            a.update(enrich_summary(a))
        except Exception as exc:
            # 單篇異常不能使長時間回填中止；保留原本可用內容。
            log(f'單篇補抓失敗 id={a.get("id")}：{type(exc).__name__}')
            now=datetime.now(timezone.utc)
            a.update(enrich_status='pending',enrich_error=type(exc).__name__,
                enrich_checked_at=now.isoformat(),enrich_version=ENRICH_VERSION,
                enrich_attempts=(a.get('enrich_attempts') or 0)+1,
                enrich_next_retry_at=(now+timedelta(days=1)).isoformat())
        done+=1
        if checkpoint and done%ENRICH_CHECKPOINT_EVERY==0: checkpoint()
        enrich_sleep()
    log(f'原文/摘要補抓：候選 {len(selected)}，實際處理 {done}；失敗項目依冷卻期續跑。')
    return done


def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", file=sys.stderr)


def polite_sleep():
    time.sleep(random.uniform(SLEEP_MIN, SLEEP_MAX))


def probe_sleep():
    time.sleep(random.uniform(PROBE_SLEEP_MIN, PROBE_SLEEP_MAX))


def fetch_with_retry(url: str, accept: str = "application/rss+xml, application/xml, text/xml"):
    """帶指數退避 retry 的 GET 請求，回傳 response 或 None。404/410 立即略過。"""
    last_exc = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(
                url,
                headers={"User-Agent": USER_AGENT, "Accept": accept},
                timeout=REQUEST_TIMEOUT,
            )
            if resp.status_code == 200:
                return resp
            if resp.status_code in (404, 410):
                return None
            if resp.status_code in (403, 429):
                log(f"  來源回應 {resp.status_code}（可能被限流/封鎖），略過此來源：{url}")
                return None
            log(f"  非預期狀態碼 {resp.status_code}，重試中 ({attempt}/{MAX_RETRIES})")
        except requests.exceptions.Timeout:
            log(f"  請求逾時，重試中 ({attempt}/{MAX_RETRIES})")
            last_exc = "timeout"
        except requests.exceptions.SSLError as e:
            log(f"  SSL 錯誤，略過此來源：{e}")
            return None
        except requests.exceptions.RequestException as e:
            log(f"  請求錯誤：{e}，重試中 ({attempt}/{MAX_RETRIES})")
            last_exc = str(e)

        if attempt < MAX_RETRIES:
            backoff = (2 ** (attempt - 1)) + random.uniform(0, 1)
            time.sleep(backoff)

    log(f"  已達最大重試次數，放棄此來源：{url}（最後錯誤：{last_exc}）")
    return None


def fetch_fast(url: str, accept: str = "text/html,application/xhtml+xml,application/xml"):
    """官方站/stories/feed 專用 GET：連線 5 秒 / 讀取 12 秒。
    只對暫時性錯誤（逾時、連線中斷）重試 OFFICIAL_MAX_RETRIES 次；
    HTTP 4xx/5xx 與 SSL 錯誤一律不重試，立即回 None 並記 log（含狀態碼）。
    受 _official_deadline 全域時間預算限制，超出預算即直接略過。"""
    if _official_deadline is not None and time.monotonic() > _official_deadline:
        log(f"  [官方預算用盡] 略過：{url}")
        return None
    last_err = None
    for attempt in range(OFFICIAL_MAX_RETRIES + 1):
        try:
            resp = requests.get(
                url,
                headers={"User-Agent": USER_AGENT, "Accept": accept},
                timeout=OFFICIAL_TIMEOUT,
                allow_redirects=True,
            )
        except requests.exceptions.SSLError:
            # 注意：SSLError 是 ConnectionError 的子類別，必須放在
            # (Timeout, ConnectionError) 之前，否則會被前者攔截而錯誤重試。
            log(f"  [快速失敗] SSL 錯誤略過（不重試）：{url}")
            return None
        except (requests.exceptions.Timeout,
                requests.exceptions.ConnectionError) as e:
            # 暫時性錯誤：還有重試額度就再試一次
            last_err = f"{type(e).__name__}"
            if attempt < OFFICIAL_MAX_RETRIES:
                log(f"  [重試 {attempt + 1}/{OFFICIAL_MAX_RETRIES}] {last_err}：{url}")
                probe_sleep()
                continue
            log(f"  [快速失敗] {last_err}（已重試 {OFFICIAL_MAX_RETRIES} 次）略過：{url}")
            return None
        except requests.exceptions.RequestException as e:
            log(f"  [快速失敗] 連線錯誤略過（不重試）：{url}（{type(e).__name__}）")
            return None
        if resp.status_code == 200:
            return resp
        log(f"  [快速失敗] 狀態碼 {resp.status_code} 略過（不重試）：{url}")
        return None
    return None


def normalize_title_for_dedupe(title: str) -> str:
    t = title.lower().strip()
    t = re.sub(r"[^\w\s]", "", t)
    t = re.sub(r"\s+", " ", t)
    return t


def normalize_url_for_dedupe(url: str) -> str:
    url = url.split("?")[0].rstrip("/")
    return url.lower()


def clean_html(raw_html: str) -> str:
    if not raw_html:
        return ""
    text = BeautifulSoup(unescape(raw_html), "html.parser").get_text(separator=" ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def split_title_source(raw_title: str):
    raw_title = unicodedata.normalize("NFKC", raw_title).strip()
    if " - " in raw_title:
        idx = raw_title.rfind(" - ")
        title = raw_title[:idx].strip()
        source = raw_title[idx + 3:].strip()
        if title and source:
            return title, source
    return raw_title, "Unknown"


def parse_pubdate_real(entry):
    """回傳 feed entry 的「真實發佈日期」'YYYY-MM-DD'，拿不到回傳 None（不 fallback 今天）。"""
    date_str = getattr(entry, "published", None) or getattr(entry, "updated", None)
    if date_str:
        try:
            dt = parsedate_to_datetime(date_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).strftime("%Y-%m-%d")
        except Exception:
            pass
    if getattr(entry, "published_parsed", None):
        try:
            return datetime(*entry.published_parsed[:6], tzinfo=timezone.utc).strftime("%Y-%m-%d")
        except Exception:
            pass
    return None


def parse_pubdate(entry) -> str:
    """相容用：拿不到真實日期時退回今天（僅供 google_news / press_release 使用）。"""
    return parse_pubdate_real(entry) or RUN_DATE


# ---------------------------------------------------------------------------
# 抓取：feed（Google News / 官方 feed / 產業新聞稿 feed）
# ---------------------------------------------------------------------------

def fetch_feed_entries(url: str):
    """抓取單一 feed URL，回傳 (entries, ok:bool)。"""
    resp = fetch_with_retry(url)
    if resp is None:
        return [], False
    try:
        feed = feedparser.parse(resp.content)
    except Exception as e:
        log(f"  feed 解析失敗：{e}")
        return [], False
    entries=list(feed.entries)
    for entry in entries:
        if not entry.get('language') and feed.feed.get('language'):
            entry['language']=feed.feed['language']
    return entries, True


def entry_to_raw(entry, source_type, brand=None, source=None):
    raw_title = getattr(entry, "title", "").strip()
    link = getattr(entry, "link", "").strip()
    if not raw_title or not link:
        return None
    raw_summary = getattr(entry, "summary", "") or getattr(entry, "description", "")
    # Atom/RSS content:encoded often carries the only real article excerpt.
    content = getattr(entry, "content", []) or []
    title_for_summary = split_title_source(raw_title)[0] if is_google_news(link) else raw_title
    summary = usable_summary(raw_summary, title_for_summary)
    if not summary:
        for part in content:
            summary = usable_summary(part.get("value", ""), title_for_summary)
            if summary: break
    publisher = getattr(entry, "source", {}) or {}
    real_date = parse_pubdate_real(entry)
    return {
        "raw_title": raw_title,
        "link": link,
        "publisher_url": publisher.get("href"),
        "language": getattr(entry, "language", None),
        "summary": summary,
        "date": real_date or RUN_DATE,
        "date_is_real": real_date is not None,
        "source_type": source_type,
        "brand": brand,       # None -> 由 detect_brand 判斷
        "source": source,     # None -> 由 Google News 標題後綴解析
    }


# ---------------------------------------------------------------------------
# 抓取：官方「故事/文章列表頁」HTML —— 只取真正的文章，過濾導覽/分類/選單雜訊
# ---------------------------------------------------------------------------


def _looks_like_category_noun(title: str) -> bool:
    """判斷標題是否為單純的選單/分類名詞（非文章）。"""
    t = re.sub(r"\s+", " ", title.strip()).lower()
    if t in CATEGORY_NOUN_TITLES:
        return True
    # 太短、且不含空白（單一詞）或只有 2 個很短的 token -> 視為分類名詞
    words = t.split()
    if len(title.strip()) < 22 and len(words) <= 3:
        return True
    return False


def _path_is_article(list_path: str, cand_path: str) -> bool:
    """文章判定：連結需位於內容區段內、比列表頁更深一層、且非導覽/分類路徑。"""
    lp = list_path.rstrip("/").lower()
    cp = cand_path.rstrip("/").lower()
    if not cp or cp == lp:
        return False
    if NAV_EXCLUDE_PATTERN.search(cand_path):
        return False
    # 條件一：位於列表頁區段之下且更深（例如 /stories/ -> /stories/xxx）
    under_section = cp.startswith(lp + "/") and len(cp) > len(lp) + 1
    # 條件二：路徑含文章區段標記，且該標記後仍有 slug（更深一層）
    has_marker = False
    m = ARTICLE_SECTION_PATTERN.search(cand_path)
    if m and len(cand_path[m.end():].strip("/")) >= 3:
        has_marker = True
    return under_section or has_marker


def parse_story_list_page(resp, list_url, brand, source):
    """從品牌「故事/文章列表頁」HTML 擷取真正的文章（標題 + 絕對 URL + 日期）。
    以 Technogym stories 頁為範本；過濾 header/nav/footer/menu 的分類與選單連結。"""
    try:
        soup = BeautifulSoup(resp.content, "html.parser")
    except Exception as e:
        log(f"  HTML 解析失敗：{e}")
        return []

    final_url = getattr(resp, "url", None) or list_url
    list_path = urlparse(final_url).path or "/"
    base_netloc = urlparse(final_url).netloc.replace("www.", "")
    items = []
    candidates = []
    seen = set()

    # 盡量排除 header/nav/footer/menu 區塊內的連結（導覽雜訊多來自這些容器）
    for junk in soup.find_all(["nav", "header", "footer"]):
        junk.decompose()
    for junk in soup.find_all(attrs={"role": "navigation"}):
        junk.decompose()

    for a in soup.find_all("a", href=True):
        text = a.get_text(" ", strip=True)
        href = a["href"].strip()
        low = href.lower()
        if low.startswith(("javascript:", "mailto:", "tel:", "#")):
            continue
        if len(text) < 25:  # 文章標題通常較長；短文字多為選單/「Read more」
            continue
        full = urljoin(final_url, href)
        if not full.startswith("http"):
            continue
        netloc = urlparse(full).netloc.replace("www.", "")
        if base_netloc not in netloc:
            continue
        cand_path = urlparse(full).path
        if not _path_is_article(list_path, cand_path):
            continue
        if _looks_like_category_noun(text):
            continue
        # 只收「官方消息型新聞」，剔除 how-to/教學/產品/分類頁
        if not looks_like_official_news(text):
            continue
        key = normalize_url_for_dedupe(full)
        if key in seen:
            continue
        seen.add(key)
        candidates.append({"raw_title": text, "link": full})
        if len(candidates) >= 20:
            break

    # 逐篇抓「文章頁本身」解析真實發佈日期；拿不到真實日期就「不收」（不硬塞今天）。
    for c in candidates:
        real_date = extract_published_date(c["link"])
        probe_sleep()
        if not real_date:
            log(f"    [官方頁] 略過（文章頁拿不到真實日期）：{c['link']}")
            continue
        items.append({
            "raw_title": c["raw_title"],
            "link": c["link"],
            "summary": c["raw_title"],
            "date": real_date,
            "date_is_real": True,
            "source_type": "official",
            "brand": brand,
            "source": source,
        })
    return items


def fetch_brand_stories(brand, cfg):
    """
    先試已知 feed（快速失敗），成功即回傳；否則抓 stories/文章列表頁（快速失敗），
    解析真正的文章連結；全部失敗回傳空。
    回傳 (raw_items, result_info)。result_info: ("feed", url) / ("page", url) / ("none", None)
    """
    source = BRAND_OFFICIAL_SOURCE.get(brand, f"{brand} 官方")

    # 1) 已知 feed（快速失敗、不重試）
    for feed_url in cfg.get("feeds", []):
        resp = fetch_fast(feed_url, accept="application/rss+xml, application/xml, text/xml")
        if resp is None:
            probe_sleep()
            continue
        try:
            feed = feedparser.parse(resp.content)
        except Exception as e:
            log(f"  feed 解析失敗：{e}")
            probe_sleep()
            continue
        raws = []
        for e in feed.entries:
            r = entry_to_raw(e, "official", brand=brand, source=source)
            # 官方 feed 只收「消息型新聞」，剔除 how-to/教學/食譜/評測等
            if r and looks_like_official_news(r["raw_title"], r.get("summary", "")):
                raws.append(r)
        if raws:
            log(f"  [官方 feed] {brand}: {feed_url} 取得 {len(raws)} 篇")
            return raws, ("feed", feed_url)
        probe_sleep()

    # 2) stories / 文章列表頁（快速失敗、解析真正文章）
    for page in cfg.get("pages", []):
        resp = fetch_fast(page)
        if resp is None:
            probe_sleep()
            continue
        items = parse_story_list_page(resp, page, brand, source)
        if items:
            log(f"  [官方頁] {brand}: {page} 擷取 {len(items)} 篇文章")
            return items, ("page", getattr(resp, "url", None) or page)
        log(f"  [官方頁] {brand}: {page} 未擷取到符合條件的文章")
        probe_sleep()

    return [], ("none", None)


# ---------------------------------------------------------------------------
# 既有庫存讀取 / 合併
# ---------------------------------------------------------------------------

def load_existing():
    """讀取既有 news.json，回傳 (articles, existing_url_set, existing_title_set, max_id)。
    對缺欄位的舊資料補回填 source_type / first_seen（不更動既有 first_seen）/
    product_category（依標題/摘要重新分類，backfill 既有 4,700+ 筆資料；欄位缺失時
    不會報錯，一律以 classify_product() 現算補上）。"""
    if not OUTPUT_FILE.exists():
        return [], set(), set(), 0

    try:
        with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        log(f"警告：既有 news.json 讀取失敗（{e}），為安全起見「不覆蓋」，中止本次執行。")
        raise SystemExit(2)

    articles = data.get("articles", [])
    url_set = set()
    title_set = set()
    max_id = 0
    normalized = []
    for a in articles:
        url = a.get("url", "")
        title = a.get("title", "")
        if url:
            url_set.add(normalize_url_for_dedupe(url))
        if title:
            title_set.add(normalize_title_for_dedupe(title))
        aid = a.get("id", 0) or 0
        if isinstance(aid, int) and aid > max_id:
            max_id = aid
        _summary_for_cls = a.get("summary", title)
        _category_for_cls = a.get("category", "market")
        _existing_pcs = a.get("product_categories")
        if isinstance(_existing_pcs, list) and _existing_pcs:
            product_categories = _existing_pcs
        else:
            product_categories = classify_product_categories(title, _summary_for_cls, a.get("brand"))
        product_category = a.get("product_category") or (
            product_categories[0] if product_categories else "other")

        _existing_tags = a.get("audience_tags")
        if isinstance(_existing_tags, list) and _existing_tags:
            audience_tags = _existing_tags
            audience_tags_source = a.get("audience_tags_source") or "keyword"
        else:
            audience_tags, audience_tags_source = classify_audience_tags(
                title, _summary_for_cls, _category_for_cls, product_categories)

        subcategory = a.get("subcategory")
        subcategoryName = a.get("subcategoryName")
        if not subcategory:
            subcategory, subcategoryName = classify_subcategory(
                _category_for_cls, title, _summary_for_cls)

        is_noise = a.get("is_noise")
        noise_reason = a.get("noise_reason")
        if is_noise is None:
            is_noise, noise_reason = classify_noise(
                title, _summary_for_cls, a.get("source", ""))

        # 「鍵是否存在」判斷（見 docs/v3-spec.md 12-5）：空陣列是合法值，
        # 只有鍵完全不存在時才現算，避免規則調整後新舊資料混用卻無法察覺。
        if "brands" in a:
            brands = a.get("brands") or []
        else:
            brands = detect_brands(f"{title} {_summary_for_cls}")
        if a.get("brand") and a.get("brand") not in brands and a.get("brand") in BRAND_DEF_BY_NAME_V3:
            brands.append(a.get("brand"))
            brands.sort(key=BRANDS_V3.index)
        # 「鍵是否存在」判斷（見 docs/v3-spec.md 12-5）：空陣列是合法值。
        if "product_types" in a:
            product_types = a.get("product_types") or []
        else:
            product_types = classify_product_types(title, _summary_for_cls, product_categories)
        # 「鍵是否存在」判斷（見 docs/v3-spec.md 12-5）：空陣列是合法值。
        if "topic_tags" in a:
            topic_tags = a.get("topic_tags") or []
        else:
            topic_tags = classify_topic_tags(title, _summary_for_cls, subcategory)
        # 「鍵是否存在」判斷（見 docs/v3-spec.md 12-5）：空陣列是合法值。
        if "attention_tags" in a:
            attention_tags = a.get("attention_tags") or []
        else:
            attention_tags = classify_attention_tags(
                brands, product_types, topic_tags, product_categories)
        # 「鍵是否存在」判斷（見 docs/v3-spec.md 12-5）：country 為字串型別，
        # 同樣採鍵是否存在（而非值是否為真）判斷，與其他四個新欄位邏輯一致。
        if "country" in a:
            country = a.get("country")
        else:
            country = classify_country(title, a.get("resolved_url"), url)

        normalized.append({
            **a,
            "id": a.get("id"),
            "title": title,
            "url": url,
            "source": a.get("source", "Unknown"),
            "date": a.get("date", RUN_DATE),
            "category": a.get("category", "market"),
            "categoryName": a.get("categoryName", CATEGORY_NAME_MAP.get(a.get("category", "market"), "全球市場趨勢")),
            "brand": a.get("brand"),
            "summary": a.get("summary", title),
            # 補回填：舊資料原本沒有 source_type -> 視為 google_news
            "source_type": a.get("source_type", "google_news"),
            # 補回填：舊資料 first_seen 以其發佈日期為準（不再變動）
            "first_seen": a.get("first_seen") or a.get("date") or RUN_DATE,
            # 補回填：舊資料原本沒有 product_category -> 依標題/摘要重新分類
            "product_category": product_category,
            # 多值產品品類（新增）：命中即加入，值域見 PRODUCT_CATEGORY_ORDER
            "product_categories": product_categories,
            # 受眾標籤（新增）：PM/Design/Marketing，保證至少一個
            "audience_tags": audience_tags,
            "audience_tags_source": audience_tags_source,
            # 二層子分類（新增）：不影響既有 category/categoryName
            "subcategory": subcategory,
            "subcategoryName": subcategoryName,
            # 雜訊標記（新增）：只標記，不刪除
            "is_noise": is_noise,
            "noise_reason": noise_reason,
            # v3 標籤欄位（新增，見 docs/v3-spec.md 第 10 節）：brands 多值品牌、
            # topic_tags/product_types/attention_tags 多值、country 為單值地域分類
            "brands": brands,
            "topic_tags": topic_tags,
            "product_types": product_types,
            "attention_tags": attention_tags,
            "country": country,
            # 保留摘要回填模式（--enrich-summary）寫入的欄位，否則每次執行會被洗掉
            "resolved_url": a.get("resolved_url"),
            "summary_source": a.get("summary_source", "rss_fallback"),
            "enrich_attempts": a.get("enrich_attempts", 0),
        })
    return normalized, url_set, title_set, max_id


# ---------------------------------------------------------------------------
# 既有庫存清理：只清「官方選單/分類/購物頁」與「明顯離題卻被標 product」的雜訊
# （保守；Google News 真實新聞與正常品牌文章一律保留）
# ---------------------------------------------------------------------------

# brand=None 卻被標 product 的明顯離題來源訊號（犯罪/球鞋/汽車/生技/Prime Day 等）
_OFFTOPIC_PRODUCT_PATTERN = re.compile(
    r"\b(rcmp|police|blotter|stalking|arrest|homicide|robbery|theft|"
    r"drug paraphernalia|missile|air force|military|navy|soldier|troops|"
    r"uefa|nfl|nba|mlb|premier league|soccer|footballer|quarterback|"
    r"sneaker|kicks|air jordan|colorway|new balance|converse|"
    r"lincoln nautilus|chinese-made cars|connected-car|automaker|"
    r"biotechnology|proteomics|alzheimer|"
    r"baby gear|baby deals|prime day|lego|fine dining|golf course|"
    r"hospitality|hotel revamp|double island|yoruba|language book|"
    r"indigenous language|uniforms)\b",
    re.IGNORECASE,
)
_STRONG_FITNESS_PATTERN = re.compile(
    r"\b(fitness|gym|workout|exercise|treadmill|elliptical|rowing machine|"
    r"rower|exercise bike|stationary bike|spin bike|strength training|"
    r"cardio equipment|dumbbell|barbell|kettlebell|home gym|weightlifting|"
    r"powerlifting)\b|健身|器材|跑步機|橢圓機|飛輪|重訓",
    re.IGNORECASE,
)


def _is_brand_official_source(article) -> bool:
    return str(article.get("source") or "").endswith("官方")


def clean_existing_noise(articles):
    """回傳 (kept_articles, report)。針對既有庫存做「日期修正 + 只留官方消息型 + 併類」：

    1) source_type=product：
       - 官方站來源(source 以「官方」結尾，如 Technogym stories) -> 視為官方項目，
         走官方消息流程（見 2）。
       - 其餘(來自 Google News 的新品發表『新聞』) -> 併入正常新聞：source_type 改
         google_news、category 依 classify 重算；brand=None 且明顯離題者剔除。
    2) source_type=official（含由 product 轉入的官方項目）：
       - 只留「消息型新聞」（公司動態/新品發表消息/活動/合作/獲獎）；how-to/教學/
         產品/分類/購物/食譜/評測等一律剔除。
       - 日期修正：既有官方項若 date 為執行日(今天，代表當初列表頁沒真實日期)，回頭抓
         「文章頁本身」補真實日期；補到就用真實日期，補不到就移除（確保庫內官方日期都真實）。
         date 非今天者視為來自 RSS 的真實日期，保留不動。
    3) google_news / press_release：真實新聞，一律保留、日期不動。
    """
    kept = []
    removed_howto = 0          # 官方非消息型（how-to/產品/分類/教學/食譜）移除
    removed_offtopic = 0       # Google News 新品查詢中 brand=None 明顯離題移除
    removed_no_real_date = 0   # 官方消息但補不到真實日期移除
    date_corrected = 0         # 官方消息回抓文章頁補正真實日期
    product_to_gnews = 0       # product 併入 google_news
    samples = []

    def handle_official(a):
        """處理官方項目：非消息型剔除；消息型做日期修正/補正。回傳 (keep:bool, article)。"""
        nonlocal removed_howto, removed_no_real_date, date_corrected
        title = a.get("title", "") or ""
        summary = a.get("summary", "") or ""
        if not looks_like_official_news(title, summary):
            removed_howto_sample(title)
            removed_howto += 1
            return False, None
        a = dict(a)
        a["source_type"] = "official"
        date = a.get("date") or ""
        if date and date != RUN_DATE:
            # 非今天 -> 視為真實日期（多來自 RSS feed），保留
            return True, a
        # date 為今天（當初列表頁無真實日期）-> 回抓文章頁補正
        real = extract_published_date(a.get("url", ""))
        probe_sleep()
        if real:
            a["date"] = real
            date_corrected_add(title)
            return True, a
        removed_no_real_date += 1
        if len(samples) < 40:
            samples.append(f"[官方無真實日期移除] {title[:60]}")
        return False, None

    def removed_howto_sample(title):
        if len(samples) < 40:
            samples.append(f"[官方非消息型移除] {title[:60]}")

    def date_corrected_add(title):
        nonlocal date_corrected
        date_corrected += 1
        if len(samples) < 40:
            samples.append(f"[官方日期補正] {title[:55]}")

    for a in articles:
        st = a.get("source_type", "google_news")
        title = a.get("title", "") or ""
        summary = a.get("summary", "") or ""

        if st == "product":
            if _is_brand_official_source(a):
                # 官方站產品/故事頁 -> 走官方消息流程
                keep, na = handle_official(a)
                if keep:
                    kept.append(na)
                continue
            # 來自 Google News 的新品發表新聞 -> 併入 google_news
            text = f"{title} {summary}"
            if not a.get("brand") and _OFFTOPIC_PRODUCT_PATTERN.search(text) \
                    and not _STRONG_FITNESS_PATTERN.search(text):
                removed_offtopic += 1
                if len(samples) < 40:
                    samples.append(f"[離題product移除] {title[:55]}")
                continue
            na = dict(a)
            na["source_type"] = "google_news"
            cat = classify(title, summary, na.get("brand"))
            na["category"] = cat
            na["categoryName"] = CATEGORY_NAME_MAP[cat]
            product_to_gnews += 1
            kept.append(na)
            continue

        if st == "official":
            keep, na = handle_official(a)
            if keep:
                kept.append(na)
            continue

        # google_news / press_release：一律保留、日期不動
        kept.append(a)

    # 4) 品牌名稱歧義回溯過濾（套用於「清理後的所有既有文章」，不分 source_type，
    #    含 google_news / press_release）：
    #    check_exclusion() 的 CELEBRITY_NOISE_PATTERN / BRAND_WHITELIST_CONTEXT 先前只在
    #    build_new_articles()（本次新抓項目）套用，已入庫的舊雜訊（例如「舒華」誤命中
    #    南韓 (G)I-DLE 成員「葉舒華」演藝新聞）從未被清除 —— 這正是 cleaned_noise 長期
    #    全為 0 的根因：舊資料完全沒有機制回頭套用此過濾。此處對「所有」既有文章套用
    #    同一組規則，確保每次執行都會即時反映最新的品牌白名單/藝人黑名單。
    removed_celebrity_noise = 0
    removed_brand_ambiguous = 0
    filtered = []
    for a in kept:
        brand = a.get("brand")
        if brand in BRAND_WHITELIST_CONTEXT:
            text = f"{a.get('title', '')} {a.get('summary', '')}"
            if CELEBRITY_NOISE_PATTERN.search(text):
                removed_celebrity_noise += 1
                if len(samples) < 40:
                    samples.append(f"[品牌歧義-藝人雜訊移除] {a.get('title', '')[:60]}")
                continue
            if not BRAND_WHITELIST_CONTEXT[brand].search(text):
                removed_brand_ambiguous += 1
                if len(samples) < 40:
                    samples.append(f"[品牌歧義-無情境詞移除] {a.get('title', '')[:60]}")
                continue
        filtered.append(a)
    kept = filtered

    report = {
        "removed_howto": removed_howto,
        "removed_offtopic": removed_offtopic,
        "removed_no_real_date": removed_no_real_date,
        "removed_celebrity_noise": removed_celebrity_noise,
        "removed_brand_ambiguous": removed_brand_ambiguous,
        "date_corrected": date_corrected,
        "product_to_gnews": product_to_gnews,
        "removed_total": (removed_howto + removed_offtopic + removed_no_real_date
                           + removed_celebrity_noise + removed_brand_ambiguous),
        "samples": samples,
    }
    return kept, report


def build_new_articles(raw_items, existing_url_set, existing_title_set, start_id):
    """由本次抓到的 raw_items 建立「新文章」清單（已對既有庫存與彼此去重、過濾、給 id）。"""
    cutoff_date = (datetime.now(timezone.utc) - timedelta(days=MAX_ARTICLE_AGE_DAYS)).strftime("%Y-%m-%d")

    new_urls = set()
    new_titles = set()
    new_articles = []
    exclusion_counts = {}
    dedupe_existing = 0
    dedupe_within = 0
    too_old = 0
    next_id = start_id + 1

    for item in raw_items:
        raw_title = (item.get("raw_title") or "").strip()
        link = (item.get("link") or "").strip()
        if not raw_title or not link:
            continue

        # 官方/HTML/新聞稿 feed 已有真實 source；Google News 需從標題後綴解析
        if item.get("source"):
            title = unicodedata.normalize("NFKC", raw_title).strip()
            source = item["source"]
        else:
            title, source = split_title_source(raw_title)

        dedupe_title = normalize_title_for_dedupe(title)
        dedupe_url = normalize_url_for_dedupe(link)

        # 與既有庫存重複 -> 保留既有，不重加
        if dedupe_url in existing_url_set or dedupe_title in existing_title_set:
            dedupe_existing += 1
            continue
        # 本次內部重複
        if dedupe_url in new_urls or dedupe_title in new_titles:
            dedupe_within += 1
            continue

        summary = item.get("summary") or ""
        stype = item.get("source_type", "google_news")
        brand = item.get("brand") or detect_brand(f"{title} {summary}")

        # 官方項目：只收「消息型新聞」，且必須有真實發佈日期（拿不到不硬塞今天 -> 略過）
        if stype == "official":
            if not looks_like_official_news(title, summary):
                exclusion_counts["official_not_news"] = exclusion_counts.get("official_not_news", 0) + 1
                continue
            if not item.get("date_is_real"):
                exclusion_counts["official_no_real_date"] = exclusion_counts.get("official_no_real_date", 0) + 1
                continue
            date = item.get("date") or RUN_DATE
        else:
            date = item.get("date") or RUN_DATE

        # 只有「帶固定來源」的品牌官方 feed/頁面與指定新聞稿 feed（如 Athletech）本質相關，
        # 才套 relax 略過 no_relevance 閘門；Google News 的 product/press_release 查詢
        # （source 由標題後綴解析、非固定）仍須命中品牌或健身相關詞，以濾除離題雜訊。
        relax = bool(item.get("source")) and item.get("source_type", "google_news") != "google_news"
        excluded, reason = check_exclusion(title, summary, brand, source, relax=relax)
        if excluded:
            exclusion_counts[reason] = exclusion_counts.get(reason, 0) + 1
            continue

        # 日期上限「只作用於本次新抓項目」
        if date < cutoff_date:
            too_old += 1
            continue

        new_urls.add(dedupe_url)
        new_titles.add(dedupe_title)

        category = classify(title, summary, brand)
        product_categories = classify_product_categories(title, summary, brand)
        product_category = product_categories[0] if product_categories else "other"
        audience_tags, audience_tags_source = classify_audience_tags(
            title, summary, category, product_categories)
        subcategory, subcategoryName = classify_subcategory(category, title, summary)
        is_noise, noise_reason = classify_noise(title, summary, source)
        brands = detect_brands(f"{title} {summary}")
        if brand and brand not in brands and brand in BRAND_DEF_BY_NAME_V3:
            brands.append(brand)
            brands.sort(key=BRANDS_V3.index)
        product_types = classify_product_types(title, summary, product_categories)
        topic_tags = classify_topic_tags(title, summary, subcategory)
        attention_tags = classify_attention_tags(
            brands, product_types, topic_tags, product_categories)
        country = classify_country(title, None, link)
        new_articles.append({
            "id": next_id,
            "title": title,
            "url": link,
            "source": source,
            "date": date,
            "category": category,
            "categoryName": CATEGORY_NAME_MAP[category],
            "brand": brand,
            "summary": summary,
            "source_type": item.get("source_type", "google_news"),
            "first_seen": RUN_DATE,
            "publisher_url": item.get("publisher_url"),
            "language": item.get("language"),
            "product_category": product_category,
            "product_categories": product_categories,
            "audience_tags": audience_tags,
            "audience_tags_source": audience_tags_source,
            "subcategory": subcategory,
            "subcategoryName": subcategoryName,
            "is_noise": is_noise,
            "noise_reason": noise_reason,
            "brands": brands,
            "topic_tags": topic_tags,
            "product_types": product_types,
            "attention_tags": attention_tags,
            "country": country,
            "resolved_url": None,
            "summary_source": "rss_fallback",
            "enrich_attempts": 0,
        })
        next_id += 1

    stats = {
        "dedupe_existing": dedupe_existing,
        "dedupe_within": dedupe_within,
        "too_old": too_old,
        "exclusion_counts": exclusion_counts,
    }
    for article in new_articles:
        normalize_article_content(article)
        classify_article(article)
    return new_articles, stats


# ---------------------------------------------------------------------------
# 統計
# ---------------------------------------------------------------------------

def compute_stats(articles, generated_at):
    by_category = {"competitor": 0, "tech": 0, "market": 0, "brand": 0, "finance": 0}
    by_brand = {name: 0 for name, _ in BRAND_DETECT_PATTERNS}
    by_source = {}
    by_source_type = {"google_news": 0, "official": 0, "press_release": 0}
    by_date = {}
    by_product_category = {"cardio": 0, "strength": 0, "wearable": 0, "other": 0}
    by_product_categories = {name: 0 for name in PRODUCT_CATEGORY_ORDER}
    by_audience_tags = {"PM": 0, "Design": 0, "Marketing": 0}
    by_subcategory = {}
    by_country = {}
    by_topic_tags = {name: 0 for name in TOPICS_V3}
    by_product_types = {name: 0 for name in PRODUCT_TYPES_V3}
    by_attention_tags = {name: 0 for name in ATTENTION_V3}
    multi_brand_count = 0

    for a in articles:
        cat = a.get("category", "market")
        by_category[cat] = by_category.get(cat, 0) + 1
        if a.get("brand"):
            by_brand[a["brand"]] = by_brand.get(a["brand"], 0) + 1
        src = a.get("source", "Unknown")
        by_source[src] = by_source.get(src, 0) + 1
        st = a.get("source_type", "google_news")
        by_source_type[st] = by_source_type.get(st, 0) + 1
        by_date[a.get("date", RUN_DATE)] = by_date.get(a.get("date", RUN_DATE), 0) + 1
        # product_category 欄位缺失時（理論上 load_existing/build_new_articles 都已補上），
        # 現算一次，避免報錯或漏統計。
        pc = a.get("product_category") or classify_product(
            a.get("title", ""), a.get("summary", ""))
        by_product_category[pc] = by_product_category.get(pc, 0) + 1

        # 多值產品品類統計（新增）
        pcs = a.get("product_categories")
        if not isinstance(pcs, list):
            pcs = classify_product_categories(a.get("title", ""), a.get("summary", ""), a.get("brand"))
        for name in pcs:
            by_product_categories[name] = by_product_categories.get(name, 0) + 1

        # 受眾標籤統計（新增）
        ats = a.get("audience_tags")
        if not isinstance(ats, list):
            ats, _ats_src = classify_audience_tags(
                a.get("title", ""), a.get("summary", ""), cat, pcs)
        for t in ats:
            by_audience_tags[t] = by_audience_tags.get(t, 0) + 1

        # 二層子分類統計（新增），key 格式："{category}:{subcategory}"
        sub = a.get("subcategory")
        if not sub:
            sub, _ = classify_subcategory(cat, a.get("title", ""), a.get("summary", ""))
        sub_key = f"{cat}:{sub}"
        by_subcategory[sub_key] = by_subcategory.get(sub_key, 0) + 1

        # v3 標籤統計（新增）：brands/topic_tags/product_types/attention_tags/country
        v3_brands = a.get("brands")
        if not isinstance(v3_brands, list):
            v3_brands = detect_brands(f"{a.get('title', '')} {a.get('summary', '')}")
        if len(v3_brands) > 1:
            multi_brand_count += 1
        v3_topics = a.get("topic_tags")
        if not isinstance(v3_topics, list):
            v3_topics = classify_topic_tags(a.get("title", ""), a.get("summary", ""), sub)
        for _t in v3_topics:
            by_topic_tags[_t] = by_topic_tags.get(_t, 0) + 1
        v3_ptypes = a.get("product_types")
        if not isinstance(v3_ptypes, list):
            v3_ptypes = classify_product_types(a.get("title", ""), a.get("summary", ""), pcs)
        for _t in v3_ptypes:
            by_product_types[_t] = by_product_types.get(_t, 0) + 1
        v3_attention = a.get("attention_tags")
        if not isinstance(v3_attention, list):
            v3_attention = classify_attention_tags(v3_brands, v3_ptypes, v3_topics, pcs)
        for _t in v3_attention:
            by_attention_tags[_t] = by_attention_tags.get(_t, 0) + 1
        v3_country = a.get("country") or classify_country(
            a.get("title", ""), a.get("resolved_url"), a.get("url"))
        by_country[v3_country] = by_country.get(v3_country, 0) + 1

    timeline = [{"date": d, "count": c} for d, c in sorted(by_date.items(), key=lambda kv: kv[0])]

    return {
        "total": len(articles),
        "by_category": by_category,
        "by_brand": by_brand,
        "by_source": by_source,
        "by_source_type": by_source_type,
        "by_product_category": by_product_category,
        "by_product_categories": by_product_categories,
        "by_audience_tags": by_audience_tags,
        "by_subcategory": by_subcategory,
        "by_country": by_country,
        "by_topic_tags": by_topic_tags,
        "by_product_types": by_product_types,
        "by_attention_tags": by_attention_tags,
        "multi_brand_count": multi_brand_count,
        "timeline": timeline,
        "updated": generated_at,
    }


# ---------------------------------------------------------------------------
# lock / log
# ---------------------------------------------------------------------------

def acquire_lock():
    LOCK_FILE.parent.mkdir(parents=True,exist_ok=True)
    try:
        # Existing locks are not removed automatically: an unrestricted backfill may last hours.
        fd=os.open(str(LOCK_FILE),os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w',encoding='utf-8') as f:
            f.write(f'pid={os.getpid()} started={datetime.now(timezone.utc).isoformat()}')
        return True
    except FileExistsError:
        log(f'已有執行鎖：{LOCK_FILE}。確認舊程序停止後才可手動刪除。')
        return False
    except OSError as exc:
        log(f'無法建立鎖，停止寫入：{exc}')
        return False


def release_lock():
    try:
        if LOCK_FILE.exists():
            LOCK_FILE.unlink()
    except OSError:
        pass


def append_log(added, total, sources_failed):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    line = (f"[{ts}] 本次新增 {added} 篇；合併後總數 {total} 篇；"
            f"失敗來源 {len(sources_failed)} 個")
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            if sources_failed:
                for s in sources_failed[:50]:
                    f.write(f"    FAIL: {s}\n")
    except OSError as e:
        log(f"寫入 log 失敗：{e}")


def write_output_atomic(output):
    """先寫暫存檔再原子替換，避免中途中斷破壞既有 news.json。"""
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUTPUT_FILE.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    os.replace(tmp, OUTPUT_FILE)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def run():
    log("=== 喬山 Johnson 產業情報 - 新聞爬蟲（v5 合併累加）開始 ===")

    if not acquire_lock():
        return None

    try:
        # 1) 讀取既有庫存（絕不刪除真實新聞）
        existing_articles, _url_set0, _title_set0, max_id = load_existing()
        log(f"既有庫存：{len(existing_articles)} 篇（最大 id={max_id}）")

        # 1b) 既有庫存：日期修正 + 只留官方消息型 + product 併入 google_news
        clean_report = dict.fromkeys(('removed_howto','removed_no_real_date','removed_offtopic',
            'removed_celebrity_noise','removed_brand_ambiguous','removed_total','date_corrected','product_to_gnews'),0)
        for article in existing_articles:
            normalize_article_content(article)
            classify_article(article)
        log(f"既有庫存清理：官方非消息型(how-to/產品/分類)移除 {clean_report['removed_howto']} 筆；"
            f"官方補不到真實日期移除 {clean_report['removed_no_real_date']} 筆；"
            f"離題 product 移除 {clean_report['removed_offtopic']} 筆；"
            f"品牌歧義-藝人雜訊移除 {clean_report['removed_celebrity_noise']} 筆；"
            f"品牌歧義-無情境詞移除 {clean_report['removed_brand_ambiguous']} 筆；"
            f"合計移除 {clean_report['removed_total']} 筆；"
            f"官方日期補正 {clean_report['date_corrected']} 筆；"
            f"product 併入 google_news {clean_report['product_to_gnews']} 筆；"
            f"清理後剩 {len(existing_articles)} 篇")

        # 依「清理後」的庫存重建去重集合（避免被清掉的雜訊 URL 擋住真文章）
        existing_url_set = set()
        existing_title_set = set()
        for a in existing_articles:
            if a.get("url"):
                existing_url_set.add(normalize_url_for_dedupe(a["url"]))
            if a.get("title"):
                existing_title_set.add(normalize_title_for_dedupe(a["title"]))

        raw_items = []
        sources_ok = []
        sources_failed = []

        # 2) 抓 feed 來源（Google News + product + press_release feed）
        feed_sources = build_feed_sources()
        log(f"feed 來源查詢共 {len(feed_sources)} 個")
        for i, cfg in enumerate(feed_sources):
            log(f"抓取 feed [{cfg['source_type']}]：{cfg['url']}")
            entries, ok = fetch_feed_entries(cfg["url"])
            if ok:
                sources_ok.append(cfg["url"])
                for e in entries:
                    r = entry_to_raw(e, cfg["source_type"], brand=cfg.get("brand"), source=cfg.get("source"))
                    if r:
                        raw_items.append(r)
            else:
                sources_failed.append(cfg["url"])
            if i < len(feed_sources) - 1:
                polite_sleep()

        # 3) 抓品牌官方「故事/文章列表頁」（已知 feed 優先，退而抓 stories 頁）
        global _official_deadline
        _official_deadline = time.monotonic() + OFFICIAL_TOTAL_BUDGET_SECONDS
        official_results = {}
        log(f"品牌官方 stories/文章來源共 {len(BRAND_STORY_SOURCES)} 個"
            f"（逾時 {OFFICIAL_CONNECT_TIMEOUT}/{OFFICIAL_READ_TIMEOUT}s、"
            f"重試 {OFFICIAL_MAX_RETRIES} 次、總預算 {OFFICIAL_TOTAL_BUDGET_SECONDS}s）")
        for brand, cfg in BRAND_STORY_SOURCES.items():
            try:
                items, info = fetch_brand_stories(brand, cfg)
            except Exception as e:
                log(f"  [官方] {brand} 發生例外，安全略過：{e}")
                items, info = [], ("error", str(e))
            official_results[brand] = info
            if items:
                raw_items.extend(items)
                sources_ok.append(f"official:{brand}:{info[1]}")
            else:
                sources_failed.append(f"official:{brand}")
            probe_sleep()

        log(f"本次抓取原始項目：{len(raw_items)} 筆（含重複）")

        # 4) 去重 + 過濾 + 給新 id
        new_articles, build_stats = build_new_articles(
            raw_items, existing_url_set, existing_title_set, max_id)
        log(f"去重(既有庫存)剔除：{build_stats['dedupe_existing']}；"
            f"去重(本次內部)剔除：{build_stats['dedupe_within']}；"
            f"過舊剔除：{build_stats['too_old']}；"
            f"內容過濾剔除明細：{build_stats['exclusion_counts']}")
        log(f"本次新增文章：{len(new_articles)} 篇")

        # 5) 合併（既有在前，順序穩定；輸出時整體依日期新到舊排序，id 不變）
        merged = existing_articles + new_articles
        merged.sort(key=lambda a: a.get("date", ""), reverse=True)

        # 6) 統計
        generated_at = datetime.now(timezone.utc).isoformat()
        stats = compute_stats(merged, generated_at)

        output = {
            "generated_at": generated_at,
            "sources_ok": sources_ok,
            "sources_failed": sources_failed,
            "official_results": {b: list(info) for b, info in official_results.items()},
            "cleaned_noise": {
                "removed_howto": clean_report["removed_howto"],
                "removed_no_real_date": clean_report["removed_no_real_date"],
                "removed_offtopic": clean_report["removed_offtopic"],
                "removed_celebrity_noise": clean_report["removed_celebrity_noise"],
                "removed_brand_ambiguous": clean_report["removed_brand_ambiguous"],
                "removed_total": clean_report["removed_total"],
                "date_corrected": clean_report["date_corrected"],
                "product_to_gnews": clean_report["product_to_gnews"],
            },
            "articles": merged,
            "stats": stats,
        }

        # 7) 安全寫檔（原子替換）
        write_output_atomic(output)

        # 每日排程也會補抓摘要，含官方來源、新聞稿、歷史待修資料。
        if AUTO_ENRICH_LIMIT != 0:
            enrich_batch(merged,AUTO_ENRICH_LIMIT,budget_seconds=ENRICH_BUDGET_SECONDS,
                checkpoint=lambda: save_repaired_data(output))
            save_repaired_data(output)

        # 8) log
        append_log(len(new_articles), stats["total"], sources_failed)

        log(f"完成：新增 {len(new_articles)} 篇，合併後總數 {stats['total']} 篇")
        log(f"by_source_type：{stats['by_source_type']}")
        log(f"by_category：{stats['by_category']}")
        official_feed_ok = [b for b, i in official_results.items() if i[0] == "feed"]
        official_page_ok = [b for b, i in official_results.items() if i[0] == "page"]
        log(f"官方 feed 成功：{official_feed_ok}")
        log(f"官方 stories/文章頁成功：{official_page_ok}")
        log(f"既有庫存清理：官方非消息型移除 {clean_report['removed_howto']} 筆、"
            f"官方無真實日期移除 {clean_report['removed_no_real_date']} 筆、"
            f"品牌歧義(藝人雜訊+無情境詞)移除 {clean_report['removed_celebrity_noise'] + clean_report['removed_brand_ambiguous']} 筆、"
            f"官方日期補正 {clean_report['date_corrected']} 筆、"
            f"product 併 google_news {clean_report['product_to_gnews']} 筆")
        log(f"輸出檔案：{OUTPUT_FILE}")
        log("=== 結束 ===")
        return output

    finally:
        release_lock()


# ---------------------------------------------------------------------------
# CLI：--enrich-summary 摘要回填模式（獨立於一般排程抓取的 run()）
# ---------------------------------------------------------------------------

# 註：first_paragraph 已移除 -- 實測會抓到氣象小工具/導覽列等非文章內容。
AUTO_ENRICH_LIMIT = 100
ENRICH_BUDGET_SECONDS = 600


def save_repaired_data(data):
    data['stats']=compute_stats(data['articles'],data.get('generated_at'))
    data['reclassified_at']=datetime.now(timezone.utc).isoformat()
    data['repair_version']=ENRICH_VERSION
    write_output_atomic(data)


def repair_existing(online=False,limit=100,since=None,dry_run=False):
    if not OUTPUT_FILE.exists():
        raise SystemExit(f'找不到資料檔：{OUTPUT_FILE}')
    with OUTPUT_FILE.open(encoding='utf-8') as f: data=json.load(f)
    for article in data['articles']:
        normalize_article_content(article)
        classify_article(article)
    selected=select_articles_for_enrich(data['articles'],limit,since)
    log(f"重算 {len(data['articles'])} 篇；待補抓 {len(selected)} 篇（本批上限 {limit}）。")
    if dry_run: return data
    # 先存已完成的離線修正，長時間補抓期間每 25 篇 checkpoint。
    save_repaired_data(data)
    if online:
        enrich_batch(data['articles'],limit,since,ENRICH_BUDGET_SECONDS,lambda:save_repaired_data(data))
        save_repaired_data(data)
    return data


def parse_cli_args(argv=None):
    p=argparse.ArgumentParser(description='健身情報 v7：爬取、原文/摘要修復、分類回填；前端相容。')
    p.add_argument('--data-file',type=Path,default=OUTPUT_FILE,help='指定 news.json；預設 ../data/news.json')
    modes=p.add_mutually_exclusive_group()
    modes.add_argument('--repair-data',action='store_true',help='修正既有資料並連網補抓原文與摘要，不新增文章')
    modes.add_argument('--enrich-summary',action='store_true',help='相容舊指令，同 --repair-data')
    modes.add_argument('--reclassify',action='store_true',help='離線修正摘要污染、網址及全量標籤')
    modes.add_argument('--enrich-dry-run',action='store_true',help='不連網、不寫檔，顯示補抓候選數')
    modes.add_argument('--reclassify-dry-run',action='store_true',help='不連網、不寫檔，試算分類')
    p.add_argument('--enrich-limit',type=int,default=100,help='每輪補抓上限；0 不限（仍受時間預算限制）')
    p.add_argument('--enrich-since',default=None,help='只補抓 YYYY-MM-DD 起的文章；分類仍處理全量')
    p.add_argument('--enrich-budget-seconds',type=int,default=600,help='補抓階段時間預算；0 不限')
    p.add_argument('--skip-enrich',action='store_true',help='一般排程只抓 RSS/官方來源，不連網補抓摘要')
    args=p.parse_args(argv)
    if args.enrich_limit<0 or args.enrich_budget_seconds<0: p.error('上限及時間預算不得為負數')
    if args.enrich_since:
        try: datetime.strptime(args.enrich_since,'%Y-%m-%d')
        except ValueError: p.error('--enrich-since 必須是 YYYY-MM-DD')
    return args


if __name__ == '__main__':
    args=parse_cli_args()
    OUTPUT_FILE=args.data_file.resolve()
    DATA_DIR=OUTPUT_FILE.parent
    LOCK_FILE=OUTPUT_FILE.with_suffix('.lock')
    DATA_DIR.mkdir(parents=True,exist_ok=True)
    ENRICH_BUDGET_SECONDS=args.enrich_budget_seconds
    # -1 是 enrich_batch 的不限筆數值；0 專供 skip。
    AUTO_ENRICH_LIMIT=0 if args.skip_enrich else (args.enrich_limit or -1)
    if args.repair_data or args.enrich_summary or args.reclassify or args.enrich_dry_run or args.reclassify_dry_run:
        if not acquire_lock(): raise SystemExit(1)
        try:
            repair_existing(online=args.repair_data or args.enrich_summary,limit=args.enrich_limit,
                since=args.enrich_since,dry_run=args.enrich_dry_run or args.reclassify_dry_run)
        finally: release_lock()
    else:
        run()
