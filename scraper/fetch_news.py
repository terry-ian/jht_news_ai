# -*- coding: utf-8 -*-
"""
fetch_news.py
喬山 Johnson 全球健身器材產業情報看板 - 新聞爬蟲

資料來源：
- Google News RSS（公開、合法、低負載，不需登入/破解任何驗證）
- 各追蹤品牌「官方 newsroom / blog / press / 新品發布」來源
  (優先嘗試品牌官網 RSS/Atom feed；無 feed 者退而抓 newsroom/blog 列表頁 HTML)
- 新品發布導向 Google News 查詢（new product / launch / unveils / releases）
- 產業新聞稿來源（PR Newswire / Business Wire / Athletech News 等）

輸出：../data/news.json

版本 v6（本次改版重點）：
- 【官方站快速失敗】官方 stories/blog/news 列表頁與 feed 一律「快速失敗」：
  連線逾時 5 秒、讀取逾時 8 秒、不重試（最多 1 次），失敗立即略過並記 log。
  移除舊版對每品牌暴力探測 14 條 feed 路徑（是 40+ 分鐘的主因），改為
  curated 已知 feed + curated 列表頁，讓整支腳本數分鐘內跑完，適合每日排程。
  （Google News RSS 仍維持原本 retry/politeness，只有官方站快速失敗。）
- 【改抓品牌故事/文章列表頁】以 Technogym stories 頁為範本，解析列表頁中「真正的
  文章連結」（標題 + 絕對 URL + 日期），標記 source_type="official"。
- 【過濾導覽/選單雜訊】只取列表頁「文章區塊」內、路徑位於該內容區段（stories/blog/
  news/press/article/learn…）且更深一層、非 category/tag/collections/product 等
  導覽路徑、標題夠長（非單一分類名詞）的連結；排除 Room planner / Multi Family
  Housing / Dumbbells & Kettlebells / Flexibility & stretching 這類選單/分類。
- 【清理既有庫存雜訊（只清雜訊、不刪真實新聞）】啟動時保守清除先前官方抓取誤收的
  非文章項目（品牌官方來源但 URL 非文章區段/是購物頁/分類頁），以及少數 brand=None
  且明顯與健身無關卻被標 product 的項目（犯罪、球鞋、汽車、Prime Day 等）。
  Google News 真實新聞(source_type=google_news)與正常品牌文章一律保留。

版本 v5 重點：
- 【合併累加、絕不刪舊】啟動時先讀取既有 news.json 的 articles，抓到的新資料只做「追加」。
  舊有文章一律保留，不因日期舊或本次沒抓到而刪除。
- 【去重】以「正規化 URL + 正規化標題」為 key。與既有庫存重複者不重加（保留既有那筆）。
- 【穩定 ID】新文章 id 由「既有最大 id + 1」往上給，維持穩定。
- 【first_seen】每篇加 first_seen（首次入庫日期，UTC）。既有文章不更動其 first_seen
  （首次執行時對舊資料補回填，之後不再變動）。
- 【source_type】每篇加 source_type：
  "google_news" | "official" | "product" | "press_release"。
- 【日期上限只作用於本次新抓項目】MAX_ARTICLE_AGE_DAYS 僅用來過濾「本次新抓進來」的過舊項目，
  絕不套用於已入庫的既有資料。
- 【idempotent】重複執行安全：同一天再跑，只會新增當天新出現且去重後不重覆的項目。
- 【log】寫入 scraper/scrape_log.txt（時間 / 本次新增數 / 合併後總數 / 失敗來源）。
- 【lock】以 lock 檔簡易防重入，避免同時多開。
- 【安全寫檔】先寫暫存檔再原子替換，避免中途中斷破壞既有 news.json。

設計原則：
- requests + timeout + 指數退避 retry；404/410 立即略過不重試
- 每個 request 之間 sleep，避免對來源造成負載
- 單一來源失敗（403/429/timeout/XML parse error/HTML 解析失敗）僅記錄失敗，不中斷整體流程
- 清理 Google News 標題結尾 " - 來源媒體" 後綴，解析出真實 source
- 依關鍵字分類 competitor / tech / market / brand / finance，並嘗試辨識品牌
"""

import argparse
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
OFFICIAL_READ_TIMEOUT = 8
OFFICIAL_TIMEOUT = (OFFICIAL_CONNECT_TIMEOUT, OFFICIAL_READ_TIMEOUT)
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
    "Life Fitness": {
        "feeds": [],
        "pages": ["https://www.lifefitness.com/en-us/customer-support/education-hub/blog",
                  "https://www.lifefitness.com/en-us/company/newsroom"]},
    "Technogym": {
        "feeds": [],
        "pages": ["https://www.technogym.com/en-INT/stories/",
                  "https://www.technogym.com/en-US/stories/"]},
    "Precor": {
        "feeds": [],
        "pages": ["https://www.precor.com/en-US/blog"]},
    "Matrix": {
        "feeds": [],
        "pages": ["https://www.matrixfitness.com/us/eng/blog",
                  "https://www.matrixfitness.com/en/blog"]},
    "Vision": {
        "feeds": [],
        "pages": ["https://www.visionfitness.com/zht/insights"]},
    "Star Trac": {
        "feeds": [],
        "pages": ["https://www.corehandf.com/blogs/shop-hs"]},
    "TRUE Fitness": {
        "feeds": ["https://truefitness.com/feed/"],
        "pages": ["https://truefitness.com/blog/"]},
    "Nautilus": {
        "feeds": [],
        "pages": ["https://www.bowflex.com/blog/"]},
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
    "Horizon": {
        "feeds": [],
        "pages": ["https://www.horizonfitness.com/blog"]},
    "Tonal": {
        "feeds": ["https://www.tonal.com/blogs/all.atom"],
        "pages": ["https://www.tonal.com/blog/"]},
    "ProForm": {
        "feeds": [],
        "pages": ["https://www.proform.com/blog"]},
    "Sunny Health": {
        "feeds": [],
        "pages": ["https://www.sunnyhealthfitness.com/blogs/motivation"]},
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
]

MARKET_KEYWORDS = [
    "market", "trend", "forecast", "growth", "share", "industry report",
    "cagr", "outlook", "demand",
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


FINANCE_PATTERNS = _compile_keyword_list(FINANCE_KEYWORDS)


def detect_brand(text: str):
    for canonical, patterns in BRAND_DETECT_COMPILED:
        for p in patterns:
            if p.search(text):
                return canonical
    return None


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

    if any(k in text for k in TECH_KEYWORDS):
        return "tech"

    if any(k in text for k in MARKET_KEYWORDS):
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

ENRICH_TIMEOUT = (5, 15)          # (connect, read)
ENRICH_SLEEP_MIN = 1.5            # 每次外部請求之間至少 sleep 1.5 秒（禮貌性限流）
ENRICH_SLEEP_MAX = 2.2
ENRICH_MAX_RETRIES = 2            # 單篇最多重試 2 次

_GN_ARTICLE_ID_PATTERN = re.compile(r"/rss/articles/([^/?]+)")
_GN_TS_PATTERN = re.compile(r'data-n-a-ts="(\d+)"')
_GN_SG_PATTERN = re.compile(r'data-n-a-sg="([^"]+)"')

_ENRICH_META_CANDIDATES = [
    ("og_description", "property", "og:description"),
    ("meta_description", "name", "description"),
    ("twitter_description", "name", "twitter:description"),
]


def enrich_sleep():
    time.sleep(random.uniform(ENRICH_SLEEP_MIN, ENRICH_SLEEP_MAX))


def _enrich_get(url: str, accept: str = "text/html,application/xhtml+xml,application/xml"):
    """摘要回填專用 GET：最多重試 ENRICH_MAX_RETRIES 次；403/404/410/429 立即放棄不重試。"""
    last_exc = None
    for attempt in range(1, ENRICH_MAX_RETRIES + 1):
        try:
            resp = requests.get(
                url,
                headers={"User-Agent": USER_AGENT, "Accept": accept},
                timeout=ENRICH_TIMEOUT,
            )
            if resp.status_code == 200:
                return resp
            if resp.status_code in (403, 404, 410, 429):
                log(f"  [enrich] 狀態碼 {resp.status_code}，略過：{url}")
                return None
            last_exc = f"status={resp.status_code}"
        except requests.exceptions.RequestException as e:
            last_exc = str(e)
        if attempt < ENRICH_MAX_RETRIES:
            enrich_sleep()
    log(f"  [enrich] 重試 {ENRICH_MAX_RETRIES} 次後放棄：{url}（{last_exc}）")
    return None


def _decode_js_string(s: str) -> str:
    r"""還原 batchexecute 回應中的 JS/JSON 字串轉義，並去除尾端反斜線。

    回應是雙層 JSON 編碼：URL 裡的 = 會變成 \\u003d（外層再把反斜線
    escape 一次）。必須先收合成對的反斜線，再解 \uXXXX；順序反了會殘留
    一個反斜線（例：?x\=123），導致抓原文頁 404/500。"""
    prev = None
    for _ in range(3):
        if s == prev:
            break
        prev = s
        s = s.replace("\\\\", "\\")
        s = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), s)
        s = s.replace("\\/", "/")
    return s.rstrip("\\")


def resolve_google_news_url(url: str):
    """把 Google News RSS 文章連結（不透明轉址）還原成原文網址。
    任何一步失敗（抓不到 article id / ts / sg，或 batchexecute 回應無可用網址）都
    回傳 None，絕不拋出例外。"""
    if not url or "news.google.com" not in url:
        return None
    m = _GN_ARTICLE_ID_PATTERN.search(url)
    if not m:
        log(f"  [enrich] 網址不含 rss/articles article id，略過：{url}")
        return None
    article_id = m.group(1)

    try:
        sep = "&" if "?" in url else "?"
        fetch_url = f"{url}{sep}hl=en-US&gl=US&ceid=US:en"
        resp = _enrich_get(fetch_url)
        if resp is None:
            return None
        html = resp.text
        ts_m = _GN_TS_PATTERN.search(html)
        sg_m = _GN_SG_PATTERN.search(html)
        if not ts_m or not sg_m:
            log(f"  [enrich] 找不到 data-n-a-ts/data-n-a-sg，略過：{url}")
            return None
        ts = int(ts_m.group(1))
        sg = sg_m.group(1)

        enrich_sleep()

        inner = json.dumps(
            [
                "garturlreq",
                [
                    ["en-US", "US", ["FINANCE_TOP_INDICES", "WEB_TEST_1_0_0"], None, None,
                     1, 1, "US:en", None, 180, None, None, None, None, None, 0, None, None,
                     [1608992183, 723341000]],
                    "en-US", "US", 1, [2, 3, 4, 8], 1, 0, "655000234", 0, 0, None, 0,
                ],
                article_id, ts, sg,
            ],
            separators=(",", ":"),
        )
        outer = json.dumps([[["Fbv4je", inner, None, "generic"]]], separators=(",", ":"))
        body = "f.req=" + quote(outer)

        try:
            resp2 = requests.post(
                "https://news.google.com/_/DotsSplashUi/data/batchexecute",
                headers={
                    "User-Agent": USER_AGENT,
                    "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                },
                data=body,
                timeout=ENRICH_TIMEOUT,
            )
        except requests.exceptions.RequestException as e:
            log(f"  [enrich] batchexecute 請求失敗，略過：{e}")
            return None
        if resp2.status_code != 200:
            log(f"  [enrich] batchexecute 狀態碼 {resp2.status_code}，略過")
            return None

        for cand in re.findall(r'"(https?:[^"]+)"', resp2.text):
            real = _decode_js_string(cand)
            netloc = urlparse(real).netloc.lower()
            if netloc and "google" not in netloc:
                return real
        log(f"  [enrich] batchexecute 回應中找不到可用網址，略過：{url}")
        return None
    except Exception as e:
        log(f"  [enrich] resolve_google_news_url 發生例外，略過：{e}")
        return None


def _valid_enriched_summary(text: str, title: str) -> bool:
    """接受條件：長度 > 40 字元，且不等於／不以 title 前 40 字元開頭（避免把
    「標題+來源」誤當真摘要）。"""
    text = (text or "").strip()
    if len(text) <= 40:
        return False
    title_prefix = (title or "").strip().lower()[:40]
    if title_prefix and text.lower().startswith(title_prefix):
        return False
    return True


def fetch_real_description(url: str, title: str):
    """抓原文網址頁面，依序嘗試 og:description -> description -> twitter:description ->
    內文第一段 <p>，回傳 (summary, summary_source)；都拿不到回傳 (None, None)。"""
    try:
        resp = _enrich_get(url)
        if resp is None:
            return None, None
        try:
            soup = BeautifulSoup(resp.content, "html.parser")
        except Exception as e:
            log(f"  [enrich] 原文頁 HTML 解析失敗：{e}")
            return None, None

        for source_name, attr, val in _ENRICH_META_CANDIDATES:
            tag = soup.find("meta", attrs={attr: val})
            if tag and tag.get("content"):
                text = clean_html(tag["content"])
                if _valid_enriched_summary(text, title):
                    return text, source_name

        p = soup.find("p")
        if p:
            text = clean_html(str(p))
            if _valid_enriched_summary(text, title):
                return text, "first_paragraph"

        return None, None
    except Exception as e:
        log(f"  [enrich] fetch_real_description 發生例外，略過：{e}")
        return None, None


def enrich_summary(article: dict) -> dict:
    """整合 resolve_google_news_url + fetch_real_description。
    成功：回傳真實 summary / resolved_url / summary_source。
    失敗：保留原 summary，summary_source 設為 "rss_fallback"。絕不拋出例外。"""
    result = {
        "summary": article.get("summary"),
        "resolved_url": article.get("resolved_url"),
        "summary_source": "rss_fallback",
    }
    url = article.get("url", "")
    title = article.get("title", "")

    resolved = resolve_google_news_url(url)
    if not resolved:
        return result
    result["resolved_url"] = resolved

    enrich_sleep()

    real_summary, source_name = fetch_real_description(resolved, title)
    if real_summary:
        result["summary"] = real_summary
        result["summary_source"] = source_name
    return result


# ---------------------------------------------------------------------------
# 工具函式
# ---------------------------------------------------------------------------

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
    """官方站/stories/feed 專用「快速失敗」GET：單次請求、不重試、
    連線逾時 5 秒 / 讀取逾時 8 秒，任何失敗立即回 None 並記 log。"""
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": accept},
            timeout=OFFICIAL_TIMEOUT,
            allow_redirects=True,
        )
    except requests.exceptions.Timeout:
        log(f"  [快速失敗] 逾時略過：{url}")
        return None
    except requests.exceptions.SSLError:
        log(f"  [快速失敗] SSL 錯誤略過：{url}")
        return None
    except requests.exceptions.RequestException as e:
        log(f"  [快速失敗] 連線錯誤略過：{url}（{type(e).__name__}）")
        return None
    if resp.status_code == 200:
        return resp
    log(f"  [快速失敗] 狀態碼 {resp.status_code} 略過：{url}")
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
    return list(feed.entries), True


def entry_to_raw(entry, source_type, brand=None, source=None):
    raw_title = getattr(entry, "title", "").strip()
    link = getattr(entry, "link", "").strip()
    if not raw_title or not link:
        return None
    raw_summary = getattr(entry, "summary", "") or getattr(entry, "description", "")
    summary = clean_html(raw_summary)
    real_date = parse_pubdate_real(entry)
    return {
        "raw_title": raw_title,
        "link": link,
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

        normalized.append({
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

        summary = item.get("summary") or title
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
            "product_category": product_category,
            "product_categories": product_categories,
            "audience_tags": audience_tags,
            "audience_tags_source": audience_tags_source,
            "subcategory": subcategory,
            "subcategoryName": subcategoryName,
            "is_noise": is_noise,
            "noise_reason": noise_reason,
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
        if not isinstance(ats, list) or not ats:
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
        "timeline": timeline,
        "updated": generated_at,
    }


# ---------------------------------------------------------------------------
# lock / log
# ---------------------------------------------------------------------------

def acquire_lock():
    if LOCK_FILE.exists():
        try:
            age = time.time() - LOCK_FILE.stat().st_mtime
        except OSError:
            age = 0
        if age < LOCK_STALE_SECONDS:
            log(f"偵測到 lock 檔（{LOCK_FILE.name}，{int(age)}s 前建立），可能已有另一實例在執行，本次略過。")
            return False
        log(f"偵測到 stale lock（{int(age)}s），移除後繼續。")
        try:
            LOCK_FILE.unlink()
        except OSError:
            pass
    try:
        LOCK_FILE.write_text(f"pid={os.getpid()} started={datetime.now().isoformat()}", encoding="utf-8")
    except OSError as e:
        log(f"無法建立 lock 檔：{e}（繼續執行）")
    return True


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
    DATA_DIR.mkdir(parents=True, exist_ok=True)
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
        existing_articles, clean_report = clean_existing_noise(existing_articles)
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

        # 3) 抓品牌官方「故事/文章列表頁」（已知 feed 優先，退而抓 stories 頁；全部快速失敗）
        official_results = {}
        log(f"品牌官方 stories/文章來源共 {len(BRAND_STORY_SOURCES)} 個（快速失敗模式）")
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
ENRICH_MAX_ATTEMPTS = 2  # 同一篇最多嘗試幾次；超過即不再重試（避免每次重跑都撞已知 403/404 站）
ENRICH_CHECKPOINT_EVERY = 25  # 每處理 25 篇寫檔一次（避免長時間執行中斷後全部重做）

ENRICH_SUCCESS_SOURCES = {"og_description", "meta_description", "twitter_description"}


def parse_cli_args(argv=None):
    parser = argparse.ArgumentParser(
        description="fetch_news.py -- 喬山產業情報新聞爬蟲（含 --enrich-summary 摘要回填模式）")
    parser.add_argument("--enrich-summary", action="store_true",
                         help="啟用 Google News 真實摘要回填模式。不帶此參數時行為與一般排程完全相同。")
    parser.add_argument("--enrich-limit", type=int, default=20,
                         help="摘要回填模式最多處理幾篇（預設 20）")
    parser.add_argument("--enrich-since", type=str, default=None,
                         help="摘要回填模式只處理此日期（含，YYYY-MM-DD）之後的文章")
    parser.add_argument("--enrich-dry-run", action="store_true",
                         help="只列出將處理的文章與預估請求數，不發送任何請求、不寫檔")
    parser.add_argument("--reclassify", action="store_true",
                         help="對既有 news.json 全量重算 product_categories/audience_tags/"
                              "subcategory/is_noise 等分類欄位並寫回（不改動 category/"
                              "categoryName，不刪除文章）")
    parser.add_argument("--reclassify-dry-run", action="store_true",
                         help="只印出各分類欄位命中統計與前後對照，不寫檔")
    return parser.parse_args(argv)


# 內容農場 / 關鍵字堆砌垃圾站：實測其還原網址為 productSearch、shop/sold 等
# 非文章頁，一律 500/404，回填必然失敗。跳過可省下大量無效請求。
ENRICH_SKIP_SOURCES = (
    "fuelcarmagazine",
    "krepsiniozinios",
    "mshale",
)


def _neg_date_key(a):
    """讓日期新的排前面（字串日期無法直接取負，改用反轉比較用的 tuple）。"""
    d = a.get("date") or ""
    return tuple(-ord(c) for c in d)


def select_articles_for_enrich(articles, limit, since):
    """挑選待回填摘要的文章：僅 source_type=google_news 且尚未成功回填過
    （summary_source 不在 ENRICH_SUCCESS_SOURCES 內），可選 since 日期下限。
    依日期新到舊排序後取前 limit 筆（limit<=0 視為不限）。"""
    pool = []
    for a in articles:
        if a.get("source_type") != "google_news":
            continue
        src_l = (a.get("source") or "").lower()
        if any(bad in src_l for bad in ENRICH_SKIP_SOURCES):
            continue
        if (a.get("enrich_attempts") or 0) >= ENRICH_MAX_ATTEMPTS:
            continue
        if a.get("summary_source") in ENRICH_SUCCESS_SOURCES:
            continue
        if since and (a.get("date") or "") < since:
            continue
        pool.append(a)
    # 優先處理「從未嘗試」的文章（attempts 少的先做），同 attempts 內再依日期新到舊。
    pool.sort(key=lambda a: ((a.get("enrich_attempts") or 0), _neg_date_key(a)))
    if limit and limit > 0:
        pool = pool[:limit]
    return pool


def run_enrich_summary(limit: int, since, dry_run: bool):
    """獨立回填流程：讀取既有 news.json，對選中的 google_news 文章嘗試還原原文網址
    並抓取真實摘要，成功則更新 summary/resolved_url/summary_source 並寫回。
    dry_run=True 時只列出將處理的文章與預估請求數，不發送任何請求、不寫檔。"""
    log("=== Google News 真實摘要回填模式（--enrich-summary） ===")
    if not OUTPUT_FILE.exists():
        log(f"找不到 {OUTPUT_FILE}，中止。")
        return
    try:
        with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        log(f"讀取 {OUTPUT_FILE} 失敗（{e}），中止。")
        return

    articles = data.get("articles", [])
    selected = select_articles_for_enrich(articles, limit, since)

    log(f"news.json 總文章數：{len(articles)}")
    since_note = f"、date >= {since}" if since else ""
    log(f"符合條件（source_type=google_news 且尚未回填成功{since_note}）：{len(selected)} 篇"
        f"（--enrich-limit {limit}）")

    if dry_run:
        est_requests = len(selected) * 3
        log("[dry-run] 不會發送任何請求，以下為將處理的文章：")
        for a in selected:
            log(f"  id={a.get('id')} date={a.get('date')} title={(a.get('title') or '')[:60]}")
        log(f"[dry-run] 預估請求數：約 {est_requests}（{len(selected)} 篇 x 最多 3 個請求/篇）")
        log("=== dry-run 結束（未發送任何請求、未寫入任何檔案）===")
        return

    if not selected:
        log("沒有符合條件的文章可處理。")
        return

    by_id = {a.get("id"): a for a in articles}
    success = 0
    fallback = 0
    for i, a in enumerate(selected):
        log(f"[{i + 1}/{len(selected)}] 處理 id={a.get('id')}：{(a.get('title') or '')[:60]}")
        result = enrich_summary(a)
        target = by_id.get(a.get("id"))
        if target is not None:
            target["summary"] = result["summary"]
            target["resolved_url"] = result["resolved_url"]
            target["summary_source"] = result["summary_source"]
            target["enrich_attempts"] = (a.get("enrich_attempts") or 0) + 1
        if result["summary_source"] in ENRICH_SUCCESS_SOURCES:
            success += 1
            log(f"    成功（{result['summary_source']}）：{(result['summary'] or '')[:80]}")
        else:
            fallback += 1
            log("    未取得真實摘要，保留原 summary（rss_fallback）")
        # 分批寫檔：長時間回填（數百筆約需 1~2 小時）中途若中斷，
        # 已完成的成果不會損失，可直接重跑續做。
        if (i + 1) % ENRICH_CHECKPOINT_EVERY == 0:
            data["articles"] = articles
            write_output_atomic(data)
            log(f"    [checkpoint] 已寫檔，進度 {i + 1}/{len(selected)}（成功 {success} / 退回 {fallback}）")
        if i < len(selected) - 1:
            enrich_sleep()

    data["articles"] = articles
    write_output_atomic(data)
    log(f"完成：成功回填 {success} 篇、退回 rss_fallback {fallback} 篇，已寫回 {OUTPUT_FILE}")
    log("=== 回填模式結束 ===")


# ---------------------------------------------------------------------------
# CLI：--reclassify / --reclassify-dry-run 分類重算模式
# ---------------------------------------------------------------------------
#
# 全量重算 product_categories / product_category / audience_tags /
# audience_tags_source / subcategory / subcategoryName / is_noise / noise_reason。
# 絕不改動 category / categoryName，絕不刪除任何文章。
# --reclassify-dry-run 只印出統計與前後對照，不寫檔。
# ---------------------------------------------------------------------------

def run_reclassify(dry_run: bool):
    """對既有 news.json 全量重算分類欄位。dry_run=True 時只統計、不寫檔。"""
    log("=== 分類重算模式（--reclassify） ===")
    if not OUTPUT_FILE.exists():
        log(f"找不到 {OUTPUT_FILE}，中止。")
        return
    try:
        with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        log(f"讀取 {OUTPUT_FILE} 失敗（{e}），中止。")
        return

    articles = data.get("articles", [])
    log(f"news.json 總文章數：{len(articles)}")

    by_product_categories = {name: 0 for name in PRODUCT_CATEGORY_ORDER}
    no_product_category = 0
    by_audience_tags = {"PM": 0, "Design": 0, "Marketing": 0}
    audience_source_counts = {"keyword": 0, "structural": 0}
    by_subcategory = {}
    noise_reason_counts = {}
    noise_total = 0
    sample_diffs = []

    for a in articles:
        title = a.get("title", "") or ""
        summary = a.get("summary", "") or ""
        category = a.get("category", "market")
        source = a.get("source", "") or ""
        brand = a.get("brand")

        old_pc = a.get("product_category")
        old_pcs = a.get("product_categories")

        product_categories = classify_product_categories(title, summary, brand)
        product_category = product_categories[0] if product_categories else "other"
        audience_tags, audience_tags_source = classify_audience_tags(
            title, summary, category, product_categories)
        subcategory, subcategoryName = classify_subcategory(category, title, summary)
        is_noise, noise_reason = classify_noise(title, summary, source)

        if not product_categories:
            no_product_category += 1
        for pc in product_categories:
            by_product_categories[pc] = by_product_categories.get(pc, 0) + 1
        for t in audience_tags:
            by_audience_tags[t] = by_audience_tags.get(t, 0) + 1
        audience_source_counts[audience_tags_source] = (
            audience_source_counts.get(audience_tags_source, 0) + 1)
        sub_key = f"{category}:{subcategory}"
        by_subcategory[sub_key] = by_subcategory.get(sub_key, 0) + 1
        if is_noise:
            noise_total += 1
            noise_reason_counts[noise_reason] = noise_reason_counts.get(noise_reason, 0) + 1

        if len(sample_diffs) < 20 and (old_pc != product_category or old_pcs != product_categories):
            sample_diffs.append(
                f"id={a.get('id')} title={title[:50]!r} "
                f"product_category:{old_pc!r}->{product_category!r} "
                f"product_categories:{old_pcs!r}->{product_categories!r}")

        a["product_categories"] = product_categories
        a["product_category"] = product_category
        a["audience_tags"] = audience_tags
        a["audience_tags_source"] = audience_tags_source
        a["subcategory"] = subcategory
        a["subcategoryName"] = subcategoryName
        a["is_noise"] = is_noise
        a["noise_reason"] = noise_reason

    log(f"product_categories 命中統計：{by_product_categories}（未命中任何類：{no_product_category}）")
    log(f"audience_tags 命中統計：{by_audience_tags}；來源分布：{audience_source_counts}")
    log(f"subcategory 命中統計：{by_subcategory}")
    log(f"is_noise 總筆數：{noise_total}；各 reason：{noise_reason_counts}")
    log("樣本前後對照（最多 20 筆變動）：")
    for line in sample_diffs:
        log(f"  {line}")

    if dry_run:
        log("=== dry-run 結束（未寫入任何檔案）===")
        return

    data["articles"] = articles
    generated_at = datetime.now(timezone.utc).isoformat()
    data["stats"] = compute_stats(articles, generated_at)
    data["generated_at"] = generated_at
    write_output_atomic(data)
    log(f"完成：已重算 {len(articles)} 篇文章的分類欄位並寫回 {OUTPUT_FILE}")
    log("=== 分類重算模式結束 ===")


if __name__ == "__main__":
    _args = parse_cli_args()
    if _args.enrich_summary or _args.enrich_dry_run:
        run_enrich_summary(limit=_args.enrich_limit, since=_args.enrich_since,
                            dry_run=_args.enrich_dry_run)
    elif _args.reclassify or _args.reclassify_dry_run:
        run_reclassify(dry_run=_args.reclassify_dry_run)
    else:
        run()
