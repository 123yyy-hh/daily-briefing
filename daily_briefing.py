"""
每日简报 v2 — 币圈资讯 + AI前沿 + X KOL观点
GitHub Actions 定时 (UTC 1:00 = 北京时间 9:00) → QQ邮箱 HTML
"""
import os, sys, json, time, smtplib, re, xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

# ── 配置 ────────────────────────────────────────────────
BASE = os.path.dirname(os.path.abspath(__file__))
PARENT_CONFIG = os.path.join(os.path.dirname(BASE), "config.json")
HEADERS = {"User-Agent": "Mozilla/5.0 DailyBriefing/2.0"}

# X 上值得关注的币圈 KOL (用户名)
KOL_LIST = [
    "cz_binance", "VitalikButerin", "saylor", "cobie",
    "0xKawz", "loomdart", "CryptoCapo_", "CryptoPoseidonn",
    "BiteyOfBitey", "0xcryptowizard",
]

# Nitter 实例（多备选，自动切换）
NITTER_INSTANCES = [
    "https://nitter.poast.org",
    "https://nitter.net",
    "https://nitter.privacydev.net",
]

AI_KEYWORDS = [
    "AI", "GPT", "LLM", "OpenAI", "Claude", "Gemini", "DeepSeek", "Llama",
    "machine learning", "diffusion", "transformer", "agent", "AGI",
    "neural", "NLP", "vision", "robot", "RAG", "fine-tune",
    "artificial intelligence", "deep learning", "generative", "copilot",
    "mistral", "anthropic", "stable diffusion", "midjourney",
]

COINS = ["BTC", "ETH", "SOL", "SUI", "DOGE", "LINK"]


# ── 工具 ────────────────────────────────────────────────
def get_config():
    cfg = {
        "to": os.environ.get("EMAIL_TO", ""),
        "user": os.environ.get("EMAIL_SMTP_USER", ""),
        "pass": os.environ.get("EMAIL_SMTP_PASS", ""),
    }
    if not cfg["pass"] and os.path.exists(PARENT_CONFIG):
        try:
            with open(PARENT_CONFIG, "r") as f:
                j = json.load(f)
            cfg["to"] = cfg["to"] or j.get("email_to", "")
            cfg["user"] = cfg["user"] or j.get("email_smtp_user", "")
            cfg["pass"] = cfg["pass"] or j.get("email_smtp_pass", "")
        except Exception:
            pass
    return cfg


def t(s):
    """翻译 EN→CN，已有中文则跳过"""
    if not s or any("一" <= c <= "鿿" for c in str(s)):
        return s
    try:
        r = requests.get("https://translate.googleapis.com/translate_a/single",
                         params={"client": "gtx", "sl": "en", "tl": "zh-CN", "dt": "t", "q": s},
                         timeout=8)
        return "".join([x[0] for x in r.json()[0] if x[0]])
    except Exception:
        return s


def http_get(url, timeout=12):
    """GET 请求，非200/超时返回 None"""
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        if r.status_code == 200:
            return r
        print(f"  [WARN] {url[:60]} → HTTP {r.status_code}")
    except Exception as e:
        print(f"  [WARN] {url[:60]} → {str(e)[:80]}")
    return None


def http_json(url, timeout=12):
    r = http_get(url, timeout)
    return r.json() if r else None


# ── 1. 市场数据 ────────────────────────────────────────

def fetch_binance_prices():
    """主流币价格 — 多 Binance 端点自动切换 + CoinGecko 兜底"""
    # 尝试多个 Binance 端点
    endpoints = [
        "https://api.binance.com/api/v3/ticker/24hr",
        "https://api1.binance.com/api/v3/ticker/24hr",
        "https://api2.binance.com/api/v3/ticker/24hr",
        "https://api3.binance.com/api/v3/ticker/24hr",
    ]
    tickers = None
    for url in endpoints:
        data = http_json(url, timeout=15)
        if data and isinstance(data, list) and len(data) > 100:
            tickers = data
            break

    if tickers:
        idx = {t["symbol"]: t for t in tickers if t["symbol"].endswith("USDT")}
        rows = []
        for coin in COINS:
            tkr = idx.get(f"{coin}USDT")
            if not tkr:
                continue
            price = float(tkr["lastPrice"])
            chg = float(tkr.get("priceChangePercent", 0))
            vol = float(tkr.get("quoteVolume", 0))
            rows.append({"coin": coin, "price": price, "chg_24h": chg,
                         "volume": vol, "high": float(tkr["highPrice"]),
                         "low": float(tkr["lowPrice"])})
        if rows:
            return rows

    # CoinGecko 兜底
    print("  Binance 全部失败，回退 CoinGecko 价格...")
    cg_map = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana",
              "SUI": "sui", "DOGE": "dogecoin", "LINK": "chainlink"}
    cg = http_json(
        f"https://api.coingecko.com/api/v3/simple/price?ids={','.join(cg_map.values())}"
        f"&vs_currencies=usd&include_24hr_change=true&include_24hr_vol=true",
        timeout=20
    )
    if cg:
        rows = []
        for coin, cg_id in cg_map.items():
            d = cg.get(cg_id, {})
            if not d:
                continue
            rows.append({"coin": coin, "price": d.get("usd", 0),
                         "chg_24h": d.get("usd_24h_change", 0) or 0,
                         "volume": d.get("usd_24h_vol", 0) or 0,
                         "high": 0, "low": 0})
        return rows
    return []


def fetch_fear_greed():
    data = http_json("https://api.alternative.me/fng/?limit=1")
    if data:
        d = data["data"][0]
        return {"value": int(d["value"]), "label": d["value_classification"]}
    return None


def fetch_global_market():
    data = http_json("https://api.coingecko.com/api/v3/global")
    if data:
        d = data["data"]
        return {
            "mcap": d["total_market_cap"]["usd"],
            "vol": d.get("total_volume", {}).get("usd", 0),
            "btc_dom": d["market_cap_percentage"]["btc"],
            "eth_dom": d["market_cap_percentage"]["eth"],
            "chg": d.get("market_cap_change_percentage_24h_usd", 0),
        }
    return None


# ── 2. 币圈快讯 (CryptoPanic) ──────────────────────────

def fetch_crypto_news():
    """币圈快讯 — CryptoPanic + Binance 公告兜底"""
    items = []
    # CryptoPanic
    data = http_json(
        "https://cryptopanic.com/api/v1/posts/?auth_token=&public=true&kind=news&limit=8",
        timeout=15
    )
    if data:
        for p in data.get("results", [])[:6]:
            title = p.get("title", "")
            domain = p.get("domain", "")
            items.append({
                "title": t(title),
                "url": p.get("url", ""),
                "source": p.get("source", {}).get("title", "") or domain,
                "published": p.get("published_at", "")[:10],
            })
    if items:
        return items

    # 兜底: Binance 公告
    print("  CryptoPanic 失败，回退 Binance 公告...")
    try:
        r = requests.get(
            "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query"
            "?type=1&catalogId=48&pageNo=1&pageSize=6",
            headers=HEADERS, timeout=15
        )
        if r.status_code == 200:
            catalogs = r.json().get("data", {}).get("catalogs", [])
            for cat in catalogs:
                for art in cat.get("articles", [])[:6]:
                    title = art.get("title", "")
                    if title:
                        items.append({"title": t(title), "url": "", "source": "Binance"})
            return items[:6]
    except Exception:
        pass
    return items


# ── 3. X KOL 发帖 (Nitter RSS) ─────────────────────────

def _nitter_fetch(instance, username, timeout=10):
    """从单个 Nitter 实例拉 RSS"""
    url = f"{instance}/{username}/rss"
    r = http_get(url, timeout=timeout)
    if not r:
        return []
    try:
        root = ET.fromstring(r.text)
        # RSS 2.0: channel > item
        items = []
        for item in root.findall(".//item")[:3]:
            title_el = item.find("title")
            link_el = item.find("link")
            date_el = item.find("pubDate")
            items.append({
                "title": (title_el.text or "").strip() if title_el is not None else "",
                "url": (link_el.text or "").strip() if link_el is not None else "",
                "date": (date_el.text or "")[:16] if date_el is not None else "",
            })
        return items
    except Exception:
        return []


def fetch_kol_tweets():
    """多线程拉取 KOL 最新推文，Nitter 实例自动切换"""
    all_tweets = []

    def fetch_one(username):
        for inst in NITTER_INSTANCES:
            items = _nitter_fetch(inst, username, timeout=8)
            if items:
                return [(username, items)]
            time.sleep(0.5)
        return []

    with ThreadPoolExecutor(max_workers=5) as ex:
        futures = {ex.submit(fetch_one, u): u for u in KOL_LIST}
        for f in as_completed(futures, timeout=45):
            try:
                result = f.result()
                all_tweets.extend(result)
            except Exception:
                pass

    # 按时间排序取最新 8 条
    flat = []
    for username, items in all_tweets:
        for item in items:
            flat.append({**item, "author": username, "handle": f"@{username}"})

    # 简单去重（按 URL）
    seen = set()
    unique = []
    for tw in sorted(flat, key=lambda x: x.get("date", ""), reverse=True):
        if tw["url"] not in seen:
            seen.add(tw["url"])
            unique.append(tw)
    return unique[:8]


# ── 4. 一级市场 ────────────────────────────────────────

def fetch_dex_new():
    """DexScreener 最新代币 — 过滤垃圾"""
    data = http_json("https://api.dexscreener.com/token-profiles/latest/v1", timeout=15)
    if not data:
        return []
    items = []
    for p in data[:20]:
        name = p.get("name") or ""
        addr = p.get("tokenAddress", "") or ""
        chain = (p.get("chainId") or "?").upper()
        desc = (p.get("description") or "")[:60]

        # 名字为空或纯地址格式 → 取前8位
        if not name or re.match(r"^[0-9A-Za-z]{30,}$", name):
            name = addr[:10] + "..." if addr else "?"

        # 跳过没有URL的
        url = p.get("url", "")
        if not url:
            continue

        items.append({"name": name[:24], "chain": chain, "desc": desc, "url": url})
        if len(items) >= 5:
            break
    return items


def fetch_trending():
    """CoinGecko 热门搜索"""
    data = http_json("https://api.coingecko.com/api/v3/search/trending")
    if not data:
        return []
    coins = data.get("coins", [])[:6]
    return [{"name": c["item"]["name"], "symbol": c["item"]["symbol"].upper(),
             "rank": c["item"].get("market_cap_rank", "?")} for c in coins]


# ── 5. 社区热议 (Reddit) ────────────────────────────────

def fetch_reddit():
    """r/CryptoCurrency 热帖 — 多 User-Agent 重试"""
    uas = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "DailyBriefing/2.0 (Newsletter Bot)",
    ]
    for ua in uas:
        try:
            h = {**HEADERS, "User-Agent": ua}
            r = requests.get(
                "https://www.reddit.com/r/CryptoCurrency/hot.json?limit=5&raw_json=1",
                headers=h, timeout=12
            )
            if r.status_code != 200:
                continue
            data = r.json()
            items = []
            for post in data.get("data", {}).get("children", [])[:5]:
                d = post["data"]
                items.append({
                    "title": d.get("title", ""),
                    "score": d.get("score", 0),
                    "comments": d.get("num_comments", 0),
                    "url": f"https://reddit.com{d.get('permalink', '')}",
                })
            if items:
                return items
        except Exception:
            continue
    return []


# ── 6. AI 前沿 ──────────────────────────────────────────

def fetch_hn_ai():
    """Hacker News → AI 相关热帖"""
    ids = http_json("https://hacker-news.firebaseio.com/v0/topstories.json")
    if not ids:
        return []
    items = []
    for item_id in ids[:50]:
        item = http_json(f"https://hacker-news.firebaseio.com/v0/item/{item_id}.json")
        if not item:
            continue
        title = item.get("title", "")
        if any(kw.lower() in title.lower() for kw in AI_KEYWORDS):
            items.append({
                "title": title,
                "score": item.get("score", 0),
                "comments": item.get("descendants", 0),
                "url": item.get("url", f"https://news.ycombinator.com/item?id={item_id}"),
            })
        if len(items) >= 5:
            break
        time.sleep(0.05)
    return sorted(items, key=lambda x: -x["score"]) if items else []


def fetch_arxiv():
    """arXiv 今日 AI 论文"""
    papers = []
    try:
        params = {
            "search_query": "cat:cs.AI+OR+cat:cs.CL+OR+cat:cs.CV",
            "sortBy": "submittedDate", "sortOrder": "descending", "max_results": 4,
        }
        r = requests.get("http://export.arxiv.org/api/query", params=params,
                         headers=HEADERS, timeout=20)
        if r.status_code != 200:
            return papers
        ns = {"a": "http://www.w3.org/2005/Atom"}
        root = ET.fromstring(r.text)
        for entry in root.findall("a:entry", ns):
            title = entry.find("a:title", ns)
            summary = entry.find("a:summary", ns)
            link = entry.find("a:id", ns)
            papers.append({
                "title": (title.text or "").strip().replace("\n", " ")[:120],
                "summary": t((summary.text or "").strip().replace("\n", " ")[:120]),
                "url": (link.text or "").strip(),
            })
    except Exception:
        pass
    return papers


# ── 7. HTML 邮件 ────────────────────────────────────────

STYLE = """
body{margin:0;padding:0;background:#f5f5f5;font-family:-apple-system,BlinkMacSystemFont,
'PingFang SC','Microsoft YaHei',sans-serif;color:#222}
.wrap{max-width:600px;margin:0 auto;background:#fff}
.header{background:linear-gradient(135deg,#07C160,#05a04a);padding:24px 20px;text-align:center}
.header h1{margin:0;font-size:20px;color:#fff}
.header .date{margin-top:4px;font-size:12px;color:rgba(255,255,255,.8)}
.section{padding:16px 18px;border-bottom:6px solid #f5f5f5}
.section-title{font-size:16px;font-weight:700;margin:0 0 12px 0;color:#111}
.section-title .icon{margin-right:6px}
.market-grid{display:flex;flex-wrap:wrap;gap:10px;margin-bottom:12px}
.market-item{flex:1;min-width:100px;background:#f9fafb;border-radius:8px;padding:10px 12px;text-align:center}
.market-item .label{font-size:11px;color:#999}
.market-item .value{font-size:18px;font-weight:700;margin-top:2px}
.market-item .sub{font-size:11px;color:#888;margin-top:1px}
.coin-table{width:100%;border-collapse:collapse;font-size:13px}
.coin-table th{text-align:left;padding:6px 8px;background:#f9fafb;color:#888;font-weight:500;font-size:11px}
.coin-table td{padding:7px 8px;border-bottom:1px solid #f0f0f0}
.coin-table .coin{font-weight:600}.up{color:#07C160}.down{color:#e94560}
.news-list{list-style:none;padding:0;margin:0}
.news-list li{padding:10px 0;border-bottom:1px solid #f0f0f0}
.news-list li:last-child{border-bottom:none}
.news-list a{color:#333;text-decoration:none;font-size:14px;line-height:1.5;display:block}
.news-list a:hover{color:#07C160}
.news-list .meta{font-size:11px;color:#aaa;margin-top:2px}
.tag{display:inline-block;padding:1px 6px;border-radius:3px;font-size:10px;font-weight:600;margin-right:4px}
.tag-green{background:#e6f9f0;color:#07C160}.tag-red{background:#fde8e8;color:#e94560}
.tag-grey{background:#f0f0f0;color:#666}.tag-blue{background:#e8f0fe;color:#1a73e8}
.kol-item{padding:10px 0;border-bottom:1px solid #f0f0f0}
.kol-item:last-child{border-bottom:none}
.kol-item .kol-name{font-size:11px;color:#07C160;font-weight:600;margin-bottom:3px}
.kol-item .kol-text{font-size:13px;line-height:1.5;color:#333}
.kol-item a{color:#333;text-decoration:none}
.footer{text-align:center;padding:16px;font-size:11px;color:#bbb}
.good{color:#07C160}.bad{color:#e94560}
.divider{width:100%;height:6px;background:#f5f5f5}
"""


def fmt_pct(v):
    return f"+{v:.2f}%" if v >= 0 else f"{v:.2f}%"


def fmt_vol(v):
    if v >= 1e9:
        return f"${v/1e9:.1f}B"
    if v >= 1e6:
        return f"${v/1e6:.0f}M"
    return f"${v:,.0f}"


def fmt_price(p):
    return f"${p:.4f}" if p < 1 else f"${p:,.2f}"


def build_html(ctx):
    now = ctx["now"]
    date_str = now.strftime("%Y年%m月%d日")
    wd = ["一","二","三","四","五","六","日"][now.weekday()]
    btc = ctx.get("prices", [{}])
    btc_p = f"${btc[0]['price']:,.0f}" if btc and btc[0].get("price") else "?"

    h = []
    h.append(f'<!DOCTYPE html><html><head><meta charset="utf-8">'
             f'<meta name="viewport" content="width=device-width,initial-scale=1">'
             f'<title>每日简报 {date_str}</title><style>{STYLE}</style></head><body>'
             f'<div class="wrap">')

    # Header
    h.append(f'<div class="header"><h1>每日简报</h1>'
             f'<div class="date">{date_str} 星期{wd} · BTC {btc_p}</div></div>')

    # ── 1. 市场概览 ──
    prices = ctx.get("prices", [])
    fg = ctx.get("fear_greed")
    gm = ctx.get("global_market")

    if prices or fg or gm:
        h.append('<div class="section">')
        h.append('<div class="section-title"><span class="icon"></span>市场概览</div>')

        # 关键指标卡片
        h.append('<div class="market-grid">')
        if fg:
            emoji = "🟢" if fg["value"] < 30 else ("🟡" if fg["value"] < 60 else "🔴")
            color_cls = "good" if fg["value"] < 30 else ("bad" if fg["value"] > 60 else "")
            h.append(f'<div class="market-item"><div class="label">恐惧贪婪</div>'
                     f'<div class="value {color_cls}">{fg["value"]} {emoji}</div>'
                     f'<div class="sub">{fg["label"]}</div></div>')
        if gm:
            cls = "good" if gm["chg"] > 0 else "bad"
            h.append(f'<div class="market-item"><div class="label">全球市值</div>'
                     f'<div class="value">${gm["mcap"]/1e12:.2f}T</div>'
                     f'<div class="sub {cls}">{fmt_pct(gm["chg"])}</div></div>')
            h.append(f'<div class="market-item"><div class="label">BTC市占率</div>'
                     f'<div class="value">{gm["btc_dom"]:.1f}%</div>'
                     f'<div class="sub">ETH {gm["eth_dom"]:.1f}%</div></div>')
        h.append('</div>')

        # 币价表
        if prices:
            h.append('<table class="coin-table"><tr><th>币种</th><th>价格</th><th>24h涨跌</th>'
                     '<th>24h成交量</th><th>24h高/低</th></tr>')
            for r in prices:
                cls = "up" if r["chg_24h"] >= 0 else "down"
                hi = r.get("high", 0)
                lo = r.get("low", 0)
                h.append(f'<tr><td class="coin">{r["coin"]}</td>'
                         f'<td>{fmt_price(r["price"])}</td>'
                         f'<td class="{cls}">{fmt_pct(r["chg_24h"])}</td>'
                         f'<td>{fmt_vol(r["volume"])}</td>'
                         f'<td style="font-size:11px;color:#888">{fmt_price(hi)}/{fmt_price(lo)}</td></tr>')
            h.append('</table>')
        h.append('</div>')

    # ── 2. 快讯 (CryptoPanic) ──
    news = ctx.get("news", [])
    if news:
        h.append('<div class="section">')
        h.append('<div class="section-title"><span class="icon"></span>币圈快讯</div>')
        h.append('<ul class="news-list">')
        for n in news[:6]:
            src = f'<span class="tag tag-grey">{n["source"]}</span>' if n.get("source") else ""
            h.append(f'<li>{src}<a href="{n["url"]}">{n["title"]}</a></li>')
        h.append('</ul></div>')

    # ── 3. X KOL 观点 ──
    kols = ctx.get("kols", [])
    if kols:
        h.append('<div class="section">')
        h.append('<div class="section-title"><span class="icon"></span>X 大V观点</div>')
        for tw in kols[:6]:
            h.append(f'<div class="kol-item">'
                     f'<div class="kol-name"><span class="tag tag-green">{tw["handle"]}</span></div>'
                     f'<a href="{tw["url"]}"><div class="kol-text">{t(tw["title"])}</div></a>'
                     f'</div>')
        h.append('</div>')

    # ── 4. 热门搜索 ──
    trending = ctx.get("trending", [])
    if trending:
        h.append('<div class="section">')
        h.append('<div class="section-title"><span class="icon"></span>热门搜索 · CoinGecko</div>')
        tags = []
        for i, c in enumerate(trending):
            tags.append(f'<span class="tag tag-blue">{i+1}. {c["name"]} ({c["symbol"]})</span>')
        h.append(f'<div style="line-height:2">{" ".join(tags)}</div></div>')

    # ── 5. 一级市场 ──
    dex = ctx.get("dex", [])
    if dex:
        h.append('<div class="section">')
        h.append('<div class="section-title"><span class="icon"></span>一级市场 · 新代币</div>')
        h.append('<ul class="news-list">')
        for item in dex:
            h.append(f'<li><span class="tag tag-grey">{item["chain"]}</span>'
                     f'<a href="{item["url"]}"><b>{item["name"]}</b></a>'
                     f'<div class="meta">{item["desc"]}</div></li>')
        h.append('</ul></div>')

    # ── 6. 社区热议 ──
    reddit = ctx.get("reddit", [])
    if reddit:
        h.append('<div class="section">')
        h.append('<div class="section-title"><span class="icon"></span>r/CryptoCurrency 热议</div>')
        h.append('<ul class="news-list">')
        for r in reddit:
            h.append(f'<li><a href="{r["url"]}">{r["title"]}</a>'
                     f'<div class="meta">⬆ {r["score"]} · 💬 {r["comments"]}</div></li>')
        h.append('</ul></div>')

    # ── 7. AI 前沿 ──
    hn = ctx.get("hn_ai", [])
    arxiv = ctx.get("arxiv", [])
    if hn or arxiv:
        h.append('<div class="section">')
        h.append('<div class="section-title"><span class="icon"></span>AI 前沿</div>')

        if hn:
            h.append('<ul class="news-list">')
            for item in hn[:4]:
                h.append(f'<li><a href="{item["url"]}">{item["title"]}</a>'
                         f'<div class="meta">HN ⬆ {item["score"]} · 💬 {item["comments"]}</div></li>')
            h.append('</ul>')

        if arxiv:
            h.append('<div style="margin-top:10px;font-size:12px;color:#888;font-weight:600">最新论文</div>')
            h.append('<ul class="news-list">')
            for p in arxiv[:3]:
                h.append(f'<li><a href="{p["url"]}">{p["title"]}</a>'
                         f'<div class="meta">{p.get("summary","")}</div></li>')
            h.append('</ul>')
        h.append('</div>')

    # Footer
    h.append(f'<div class="footer">每日自动生成 · {now.strftime("%Y-%m-%d %H:%M UTC")}<br>'
             f'数据: Binance · CoinGecko · CryptoPanic · Nitter(X) · DexScreener · Reddit · HN · arXiv</div>')
    h.append('</div></body></html>')
    return "\n".join(h)


# ── 8. 邮件 ─────────────────────────────────────────────

def send_email(html, subject):
    cfg = get_config()
    if not cfg["to"] or not cfg["pass"]:
        print("[FAIL] 邮件配置缺失")
        return False
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = cfg["user"]
    msg["To"] = cfg["to"]
    msg.attach(MIMEText(html, "html", "utf-8"))
    try:
        s = smtplib.SMTP_SSL("smtp.qq.com", 465, timeout=15)
        s.login(cfg["user"], cfg["pass"])
        s.sendmail(cfg["user"], [cfg["to"]], msg.as_string())
        s.quit()
        print(f"[OK] 邮件已发送 → {cfg['to']}")
        return True
    except Exception as e:
        print(f"[FAIL] 邮件发送: {e}")
        return False


# ── 9. 主流程 ───────────────────────────────────────────

def main():
    now = datetime.now(timezone.utc)
    print(f"[{now:%Y-%m-%d %H:%M:%S} UTC] 每日简报 v2 开始...")

    print("[1] 市场数据...")
    prices = fetch_binance_prices()
    print(f"    币价: {len(prices)}个")
    fg = fetch_fear_greed()
    gm = fetch_global_market()

    print("[2] 币圈快讯...")
    news = fetch_crypto_news()
    print(f"    快讯: {len(news)}条")

    print("[3] X KOL 观点...")
    kols = fetch_kol_tweets()
    print(f"    KOL: {len(kols)}条")

    print("[4] 热门搜索 + 一级市场...")
    trending = fetch_trending()
    dex = fetch_dex_new()
    print(f"    热门: {len(trending)}个, 新币: {len(dex)}个")

    print("[5] Reddit...")
    reddit = fetch_reddit()
    print(f"    热帖: {len(reddit)}条")

    print("[6] AI 前沿...")
    hn_ai = fetch_hn_ai()
    arxiv = fetch_arxiv()
    print(f"    HN: {len(hn_ai)}条, arXiv: {len(arxiv)}篇")

    ctx = {
        "now": now, "prices": prices, "fear_greed": fg, "global_market": gm,
        "news": news, "kols": kols, "trending": trending, "dex": dex,
        "reddit": reddit, "hn_ai": hn_ai, "arxiv": arxiv,
    }
    html = build_html(ctx)

    btc_p = f"${prices[0]['price']:,.0f}" if prices else "?"
    subject = f"每日简报 {now.strftime('%m/%d')} | BTC {btc_p}"
    send_email(html, subject)

    # 保存本地
    out = os.path.join(BASE, "latest_briefing.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[OK] 已保存 {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
