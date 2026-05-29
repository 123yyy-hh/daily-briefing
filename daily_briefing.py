"""
每日简报 — 币圈资讯 + AI前沿
GitHub Actions 定时执行 (UTC 1:00 = 北京时间 9:00)
发送 HTML 邮件到 QQ 邮箱
"""
import os
import sys
import json
import time
import smtplib
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import requests
import ccxt

# ── 路径 & 配置 ─────────────────────────────────────────
BASE = os.path.dirname(os.path.abspath(__file__))
PARENT_CONFIG = os.path.join(os.path.dirname(BASE), "config.json")

COINS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "SUI/USDT", "DOGE/USDT", "LINK/USDT"]
FEAR_GREED_URL = "https://api.alternative.me/fng/?limit=1"
COINGECKO_GLOBAL = "https://api.coingecko.com/api/v3/global"
COINGECKO_TRENDING = "https://api.coingecko.com/api/v3/search/trending"
COINGECKO_CATEGORIES = "https://api.coingecko.com/api/v3/coins/categories"
DEX_LATEST = "https://api.dexscreener.com/token-profiles/latest/v1"
CRYPTOPANIC_NEWS = "https://cryptopanic.com/api/v1/posts/?auth_token=&public=true&kind=news&limit=8"
REDDIT_CRYPTO = "https://www.reddit.com/r/CryptoCurrency/hot.json?limit=5"
HN_TOP = "https://hacker-news.firebaseio.com/v0/topstories.json"
HN_ITEM = "https://hacker-news.firebaseio.com/v0/item/{}.json"
ARXIV_API = "http://export.arxiv.org/api/query"
GITHUB_AI = "https://api.github.com/search/repositories"

AI_KEYWORDS = [
    "AI", "GPT", "LLM", "OpenAI", "Claude", "Gemini", "DeepSeek", "Llama",
    "machine learning", "diffusion", "transformer", "agent", "AGI",
    "neural", "NLP", "vision", "robot", "embedding", "RAG", "fine-tune",
    "artificial intelligence", "deep learning", "generative", "copilot",
    "mistral", "anthropic", "stable diffusion", "midjourney",
]

HEADERS = {"User-Agent": "DailyBriefing/1.0 (Crypto+AI Newsletter Bot)"}


# ── 配置读取 ────────────────────────────────────────────
def get_config():
    """优先环境变量(GitHub Secrets)，回退 config.json"""
    cfg = {
        "email_to": os.environ.get("EMAIL_TO", ""),
        "email_smtp_user": os.environ.get("EMAIL_SMTP_USER", ""),
        "email_smtp_pass": os.environ.get("EMAIL_SMTP_PASS", ""),
        "proxy": os.environ.get("PROXY", ""),
    }
    # 如果环境变量没配，尝试 config.json
    if not cfg["email_smtp_pass"] and os.path.exists(PARENT_CONFIG):
        try:
            with open(PARENT_CONFIG, "r") as f:
                j = json.load(f)
            cfg["email_to"] = cfg["email_to"] or j.get("email_to", "")
            cfg["email_smtp_user"] = cfg["email_smtp_user"] or j.get("email_smtp_user", "")
            cfg["email_smtp_pass"] = cfg["email_smtp_pass"] or j.get("email_smtp_pass", "")
            cfg["proxy"] = cfg["proxy"] or j.get("proxy", "")
        except Exception:
            pass
    return cfg


def get_proxies():
    cfg = get_config()
    p = cfg.get("proxy", "")
    if p:
        return {"http": p, "https": p}
    return None


def get_public_exchange():
    """Binance 公网连接"""
    ex = ccxt.binance({"enableRateLimit": True, "timeout": 15000})
    proxies = get_proxies()
    if proxies:
        ex.session.proxies = proxies
    return ex


# ── 工具函数 ────────────────────────────────────────────
def translate(text):
    """Google 翻译：英文→中文"""
    if not text or any("一" <= c <= "鿿" for c in text):
        return text  # 已有中文，跳过
    try:
        url = "https://translate.googleapis.com/translate_a/single"
        r = requests.get(url, params={
            "client": "gtx", "sl": "en", "tl": "zh-CN", "dt": "t", "q": text
        }, timeout=8)
        parts = r.json()[0]
        return "".join([s[0] for s in parts if s[0]])
    except Exception:
        return text


def fetch_json(url, timeout=15, proxies=None):
    """带超时和代理的 GET JSON，代理不可用时自动回退直连"""
    if proxies:
        try:
            r = requests.get(url, headers=HEADERS, timeout=timeout, proxies=proxies)
            r.raise_for_status()
            return r.json()
        except Exception:
            pass  # 代理失败，回退直连
    r = requests.get(url, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.json()


def fmt_pct(v):
    if v is None:
        return "-"
    return f"+{v:.2f}%" if v > 0 else f"{v:.2f}%"


def fmt_vol(v):
    if v is None:
        return "-"
    if v >= 1e9:
        return f"${v/1e9:.1f}B"
    if v >= 1e6:
        return f"${v/1e6:.0f}M"
    return f"${v:,.0f}"


def fmt_price(p):
    if p is None:
        return "-"
    return f"${p:.4f}" if p < 1 else f"${p:,.2f}"


# ══════════════════════════════════════════════════════════
#  币圈数据源
# ══════════════════════════════════════════════════════════

def fetch_prices(ex):
    """主流币价格 + 涨跌幅"""
    rows = []
    for sym in COINS:
        try:
            t = ex.fetch_ticker(sym)
            ohlcv = ex.fetch_ohlcv(sym, "1d", limit=7)
            chg_7d = (ohlcv[-1][4] - ohlcv[0][4]) / ohlcv[0][4] * 100 if len(ohlcv) >= 7 else 0
            coin = sym.split("/")[0]
            rows.append({
                "coin": coin,
                "price": t["last"],
                "chg_24h": t.get("percentage", 0) or 0,
                "chg_7d": chg_7d,
                "volume": t.get("quoteVolume", 0) or 0,
            })
        except Exception:
            pass
    return rows


def fetch_fear_greed(proxies):
    """恐惧贪婪指数"""
    try:
        data = fetch_json(FEAR_GREED_URL, proxies=proxies)["data"][0]
        return {"value": int(data["value"]), "classification": data["value_classification"]}
    except Exception:
        return None


def fetch_global_market(proxies):
    """全球总市值 + BTC/ETH 市占率"""
    try:
        data = fetch_json(COINGECKO_GLOBAL, proxies=proxies)["data"]
        return {
            "total_mcap": data["total_market_cap"]["usd"],
            "total_vol": data.get("total_volume", {}).get("usd", 0),
            "btc_dom": data["market_cap_percentage"]["btc"],
            "eth_dom": data["market_cap_percentage"]["eth"],
            "chg_24h": data.get("market_cap_change_percentage_24h_usd", 0),
        }
    except Exception:
        return None


def fetch_trending(proxies):
    """CoinGecko 热门搜索"""
    try:
        coins = fetch_json(COINGECKO_TRENDING, proxies=proxies).get("coins", [])[:7]
        return [{
            "name": c["item"]["name"],
            "symbol": c["item"]["symbol"].upper(),
            "rank": c["item"].get("market_cap_rank", "?"),
            "score": c["item"].get("score", 0),
        } for c in coins]
    except Exception:
        return []


def fetch_categories(proxies):
    """CoinGecko 板块涨跌 — 发现热门赛道"""
    try:
        cats = fetch_json(COINGECKO_CATEGORIES, proxies=proxies)
        # 只保留有趣的板块
        INTERESTING = {
            "layer-2", "layer-1", "meme-token", "artificial-intelligence",
            "defi", "gaming", "decentralized-exchange", "real-world-assets",
            "solana-ecosystem", "bitcoin-ecosystem", "depin",
            "liquid-staking", "restaking", "modular-blockchain",
        }
        filtered = [c for c in cats if c.get("id", "") in INTERESTING]
        # 按 24h 涨幅排序，区分涨/跌
        up = sorted([c for c in filtered if c.get("market_cap_change_24h", 0) > 0],
                     key=lambda x: -x["market_cap_change_24h"])[:5]
        down = sorted([c for c in filtered if c.get("market_cap_change_24h", 0) < 0],
                       key=lambda x: x["market_cap_change_24h"])[:3]
        # 翻译板块名
        CAT_CN = {
            "layer-2": "Layer 2", "layer-1": "Layer 1", "meme-token": "Meme币",
            "artificial-intelligence": "AI概念", "defi": "DeFi", "gaming": "游戏",
            "decentralized-exchange": "DEX", "real-world-assets": "RWA",
            "solana-ecosystem": "Solana生态", "bitcoin-ecosystem": "BTC生态",
            "depin": "DePIN", "liquid-staking": "流动性质押",
            "restaking": "再质押", "modular-blockchain": "模块化区块链",
        }
        for c in up + down:
            c["name_cn"] = CAT_CN.get(c.get("id", ""), c.get("name", ""))
        return {"up": up, "down": down}
    except Exception:
        return {"up": [], "down": []}


def fetch_top_movers(ex):
    """24h涨跌榜"""
    gainers, losers = [], []
    try:
        tickers = ex.fetch_tickers()
        exclude = {"USDC", "BUSD", "DAI", "TUSD", "FDUSD", "WBTC", "WETH"}
        pairs = [(s, t) for s, t in tickers.items()
                 if s.endswith("/USDT") and s.split("/")[0] not in exclude]
        sorted_p = sorted(pairs, key=lambda x: x[1].get("percentage", 0) or 0)

        seen = set()
        for sym, t in sorted_p[-60:][::-1]:
            coin = sym.split("/")[0]
            if coin not in seen:
                seen.add(coin)
                gainers.append({"coin": coin, "pct": t.get("percentage", 0) or 0,
                                "vol": t.get("quoteVolume", 0) or 0})
            if len(gainers) >= 5:
                break

        seen.clear()
        for sym, t in sorted_p[:60]:
            coin = sym.split("/")[0]
            if coin not in seen:
                seen.add(coin)
                losers.append({"coin": coin, "pct": t.get("percentage", 0) or 0,
                               "vol": t.get("quoteVolume", 0) or 0})
            if len(losers) >= 5:
                break
    except Exception:
        pass
    return gainers, losers


def fetch_dex_latest(proxies):
    """DexScreener 最新代币（一级市场探测器）"""
    try:
        profiles = fetch_json(DEX_LATEST, proxies=proxies)
        items = []
        for p in profiles[:8]:
            desc = (p.get("description") or "")[:80]
            items.append({
                "name": (p.get("name") or p.get("tokenAddress", "?"))[:24],
                "chain": p.get("chainId", "?").upper(),
                "desc": desc,
                "url": p.get("url", ""),
            })
        return items
    except Exception:
        return []


def fetch_crypto_news(proxies):
    """CryptoPanic 快讯（含 KOL 推文汇聚）"""
    items = []
    try:
        data = fetch_json(CRYPTOPANIC_NEWS, proxies=proxies)
        for p in data.get("results", [])[:8]:
            title = p.get("title", "")
            url = p.get("url", "")
            # 来源标注
            source = p.get("source", {}).get("title", "")
            items.append({"title": translate(title), "url": url, "source": source})
    except Exception:
        pass
    return items


def fetch_reddit_hot(proxies):
    """Reddit r/CryptoCurrency 热帖"""
    items = []
    try:
        data = fetch_json(REDDIT_CRYPTO, proxies=proxies)
        for post in data.get("data", {}).get("children", [])[:5]:
            d = post["data"]
            items.append({
                "title": d.get("title", ""),
                "score": d.get("score", 0),
                "comments": d.get("num_comments", 0),
                "url": f"https://reddit.com{d.get('permalink', '')}",
            })
    except Exception:
        pass
    return items


# ══════════════════════════════════════════════════════════
#  AI 数据源
# ══════════════════════════════════════════════════════════

def fetch_hn_ai(proxies):
    """Hacker News 热帖 → 筛选 AI 相关"""
    items = []
    try:
        ids = fetch_json(HN_TOP, proxies=proxies)[:40]
        for item_id in ids:
            try:
                item = fetch_json(HN_ITEM.format(item_id), proxies=proxies)
                title = item.get("title", "")
                if any(kw.lower() in title.lower() for kw in AI_KEYWORDS):
                    items.append({
                        "title": title,
                        "score": item.get("score", 0),
                        "comments": item.get("descendants", 0),
                        "url": item.get("url", f"https://news.ycombinator.com/item?id={item_id}"),
                    })
                if len(items) >= 6:
                    break
            except Exception:
                continue
            time.sleep(0.1)  # HN API 友好节流
    except Exception:
        pass
    return sorted(items, key=lambda x: -x["score"]) if items else []


def fetch_arxiv(proxies):
    """arXiv 最新 AI 论文 (cs.AI + cs.CL + cs.CV 合并)"""
    papers = []
    try:
        params = {
            "search_query": "cat:cs.AI+OR+cat:cs.CL+OR+cat:cs.CV",
            "sortBy": "submittedDate",
            "sortOrder": "descending",
            "max_results": 6,
        }
        r = requests.get(ARXIV_API, params=params, timeout=20, proxies=proxies)
        # 简单解析 XML（不引入额外依赖）
        import xml.etree.ElementTree as ET
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        root = ET.fromstring(r.text)
        for entry in root.findall("atom:entry", ns):
            title = entry.find("atom:title", ns)
            summary = entry.find("atom:summary", ns)
            link = entry.find("atom:id", ns)
            papers.append({
                "title": (title.text or "").strip().replace("\n", " ")[:120],
                "summary": translate((summary.text or "").strip().replace("\n", " ")[:150]),
                "url": (link.text or "").strip(),
            })
    except Exception:
        pass
    return papers


def fetch_github_ai(proxies):
    """GitHub 最近一周 AI 相关高星项目"""
    repos = []
    try:
        since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
        q = f"created:>={since}+topic:artificial-intelligence"
        params = {"q": q, "sort": "stars", "order": "desc", "per_page": 5}
        r = requests.get(GITHUB_AI, headers=HEADERS, params=params, timeout=15, proxies=proxies)
        data = r.json()
        for item in data.get("items", []):
            repos.append({
                "name": item.get("full_name", ""),
                "desc": (item.get("description") or "")[:100],
                "stars": item.get("stargazers_count", 0),
                "lang": item.get("language", ""),
                "url": item.get("html_url", ""),
            })
    except Exception:
        pass
    return repos


# ══════════════════════════════════════════════════════════
#  HTML 邮件生成
# ══════════════════════════════════════════════════════════

CSS = """
body { margin:0; padding:0; background:#0f0f1a; font-family: -apple-system, BlinkMacSystemFont,
  'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif; color:#e0e0e0; }
.container { max-width:640px; margin:0 auto; padding:20px; }
.header { text-align:center; padding:28px 20px 18px;
  background:linear-gradient(135deg, #0a0a16 0%, #16213e 100%); border-radius:12px 12px 0 0; }
.header h1 { margin:0; font-size:22px; color:#07C160; }
.header .date { margin-top:6px; font-size:13px; color:#888; }
.card { background:#1a1a2e; border-radius:0; margin-bottom:2px; padding:18px 20px; }
.card-first { border-radius:0 0 12px 12px; }
.card-last { border-radius:12px 12px 0 0; }
.section-title { font-size:16px; font-weight:700; margin:0 0 14px 0; padding-left:10px;
  border-left:3px solid #07C160; color:#f0f0f0; }
table { width:100%; border-collapse:collapse; font-size:14px; }
th { text-align:left; padding:8px 6px; border-bottom:1px solid #2a2a4a; color:#999;
  font-weight:500; font-size:12px; }
td { padding:8px 6px; border-bottom:1px solid #1f1f3a; }
.up { color:#07C160; } .down { color:#e94560; }
.news-item { padding:10px 0; border-bottom:1px solid #1f1f3a; }
.news-item:last-child { border-bottom:none; }
.news-item a { color:#e0e0e0; text-decoration:none; font-size:14px; line-height:1.5; }
.news-item a:hover { color:#07C160; }
.news-meta { font-size:11px; color:#666; margin-top:3px; }
.tag { display:inline-block; padding:2px 8px; border-radius:4px; font-size:11px;
  margin-right:4px; }
.tag-up { background:#0d3320; color:#07C160; }
.tag-down { background:#331111; color:#e94560; }
.tag-chain { background:#1a1a3e; color:#6688cc; }
.section-divider { height:8px; background:#0f0f1a; }
.footer { text-align:center; padding:20px; font-size:11px; color:#555; }
.footer a { color:#555; }
.badge { display:inline-block; background:#07C160; color:#000; padding:1px 6px;
  border-radius:3px; font-size:10px; font-weight:700; margin-left:4px; }
"""


def build_html(ctx):
    """组装 HTML"""
    now = ctx["now"]
    date_str = now.strftime("%Y年%m月%d日")
    weekday = ["一", "二", "三", "四", "五", "六", "日"][now.weekday()]

    parts = [f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>每日简报 {date_str}</title><style>{CSS}</style></head><body>
<div class="container">
<div class="header">
  <h1>每日简报</h1>
  <div class="date">{date_str} 星期{weekday} · 自动生成</div>
</div>"""]

    # ── 1. 市场概览 ──
    parts.append('<div class="card card-last">')
    parts.append('<div class="section-title">市场概览</div>')

    # 恐惧贪婪 + 全球市值
    fg = ctx.get("fear_greed")
    gm = ctx.get("global_market")
    if fg or gm:
        parts.append('<table><tr>')
        if fg:
            emoji = "🟢" if fg["value"] < 30 else ("🟡" if fg["value"] < 60 else "🔴")
            parts.append(f'<td style="width:50%"><span style="color:#999;font-size:12px">恐惧贪婪</span><br>'
                         f'<b style="font-size:20px">{fg["value"]}</b> {emoji}<br>'
                         f'<span style="font-size:12px;color:#888">{fg["classification"]}</span></td>')
        if gm:
            parts.append(f'<td style="width:50%"><span style="color:#999;font-size:12px">全球市值</span><br>'
                         f'<b style="font-size:18px">${gm["total_mcap"]/1e12:.2f}T</b><br>'
                         f'<span style="font-size:12px" class="{"up" if gm["chg_24h"]>0 else "down"}">'
                         f'{fmt_pct(gm["chg_24h"])}</span></td>')
        parts.append('</tr></table>')
        if gm:
            parts.append(f'<div style="margin-top:8px;font-size:12px;color:#888">'
                         f'BTC市占 {gm["btc_dom"]:.1f}% · ETH {gm["eth_dom"]:.1f}% · '
                         f'24h成交量 ${gm["total_vol"]/1e9:.0f}B</div>')

    # 主流币表格
    rows = ctx.get("prices", [])
    if rows:
        parts.append('<table style="margin-top:14px"><tr>'
                     '<th>币种</th><th>价格</th><th>24h</th><th>7日</th><th>成交量</th></tr>')
        for r in rows:
            chg24_cls = "up" if r["chg_24h"] > 0 else "down"
            chg7_cls = "up" if r["chg_7d"] > 0 else "down"
            parts.append(f'<tr><td><b>{r["coin"]}</b></td>'
                         f'<td>{fmt_price(r["price"])}</td>'
                         f'<td class="{chg24_cls}">{fmt_pct(r["chg_24h"])}</td>'
                         f'<td class="{chg7_cls}">{fmt_pct(r["chg_7d"])}</td>'
                         f'<td>{fmt_vol(r["volume"])}</td></tr>')
        parts.append('</table>')
    parts.append('</div>')

    # ── 2. 热门板块 ──
    cats = ctx.get("categories", {})
    if cats.get("up") or cats.get("down"):
        parts.append('<div class="section-divider"></div>')
        parts.append('<div class="card">')
        parts.append('<div class="section-title">热门板块</div>')
        if cats.get("up"):
            items = " · ".join(
                f'<span class="tag tag-up">{c.get("name_cn", c.get("name",""))} '
                f'↑{c.get("market_cap_change_24h",0):.1f}%</span>'
                for c in cats["up"][:6]
            )
            parts.append(f'<div style="margin-bottom:6px">{items}</div>')
        if cats.get("down"):
            items = " · ".join(
                f'<span class="tag tag-down">{c.get("name_cn", c.get("name",""))} '
                f'↓{abs(c.get("market_cap_change_24h",0)):.1f}%</span>'
                for c in cats["down"][:3]
            )
            parts.append(f'<div>{items}</div>')
        parts.append('</div>')

    # ── 3. 涨跌榜 ──
    gainers = ctx.get("gainers", [])
    losers = ctx.get("losers", [])
    if gainers or losers:
        parts.append('<div class="section-divider"></div>')
        parts.append('<div class="card">')
        parts.append('<div class="section-title">24h涨跌榜</div>')
        parts.append('<table><tr><th>涨幅</th><th></th><th></th>'
                     '<th style="padding-left:16px">跌幅</th><th></th><th></th></tr>')
        for i in range(5):
            gl = gainers[i] if i < len(gainers) else None
            ll = losers[i] if i < len(losers) else None
            parts.append('<tr>')
            if gl:
                parts.append(f'<td><b>{gl["coin"]}</b></td>'
                             f'<td class="up">{fmt_pct(gl["pct"])}</td>'
                             f'<td style="font-size:11px;color:#888">{fmt_vol(gl["vol"])}</td>')
            else:
                parts.append('<td></td><td></td><td></td>')
            if ll:
                parts.append(f'<td style="padding-left:16px"><b>{ll["coin"]}</b></td>'
                             f'<td class="down">{fmt_pct(ll["pct"])}</td>'
                             f'<td style="font-size:11px;color:#888">{fmt_vol(ll["vol"])}</td>')
            else:
                parts.append('<td></td><td></td><td></td>')
            parts.append('</tr>')
        parts.append('</table></div>')

    # ── 4. CoinGecko 热门搜索 ──
    trending = ctx.get("trending", [])
    if trending:
        parts.append('<div class="section-divider"></div>')
        parts.append('<div class="card">')
        parts.append('<div class="section-title">热门搜索</div>')
        parts.append('<table><tr><th>#</th><th>名称</th><th>代号</th><th>市值排名</th></tr>')
        for i, c in enumerate(trending):
            parts.append(f'<tr><td>{i+1}</td><td><b>{c["name"]}</b></td>'
                         f'<td>{c["symbol"]}</td><td>#{c["rank"]}</td></tr>')
        parts.append('</table></div>')

    # ── 5. 一级市场 (DexScreener 新代币) ──
    dex = ctx.get("dex_latest", [])
    if dex:
        parts.append('<div class="section-divider"></div>')
        parts.append('<div class="card">')
        parts.append('<div class="section-title">一级市场 · 新代币<span class="badge">DexScreener</span></div>')
        for item in dex[:6]:
            chain_tag = f'<span class="tag tag-chain">{item["chain"]}</span>'
            desc = item.get("desc", "")
            parts.append(f'<div class="news-item">'
                         f'<div><a href="{item["url"]}"><b>{item["name"]}</b></a> {chain_tag}</div>'
                         f'<div class="news-meta">{desc}</div>'
                         f'</div>')
        parts.append('</div>')

    # ── 6. 币圈快讯 ──
    news = ctx.get("crypto_news", [])
    if news:
        parts.append('<div class="section-divider"></div>')
        parts.append('<div class="card">')
        parts.append('<div class="section-title">币圈快讯<span class="badge">CryptoPanic</span></div>')
        for n in news:
            source_tag = f'<span style="color:#07C160;font-size:11px">[{n["source"]}]</span> ' if n.get("source") else ""
            parts.append(f'<div class="news-item">'
                         f'<div>{source_tag}<a href="{n["url"]}">{n["title"]}</a></div>'
                         f'</div>')
        parts.append('</div>')

    # ── 7. Reddit 社区热议 ──
    reddit = ctx.get("reddit", [])
    if reddit:
        parts.append('<div class="section-divider"></div>')
        parts.append('<div class="card">')
        parts.append('<div class="section-title">社区热议<span class="badge">r/CryptoCurrency</span></div>')
        for r in reddit:
            parts.append(f'<div class="news-item">'
                         f'<a href="{r["url"]}">{r["title"]}</a>'
                         f'<div class="news-meta">⬆ {r["score"]} · 💬 {r["comments"]} 评论</div>'
                         f'</div>')
        parts.append('</div>')

    # ── 8. AI 前沿 ──
    hn = ctx.get("hn_ai", [])
    arxiv = ctx.get("arxiv", [])
    gh = ctx.get("github_ai", [])

    if hn or arxiv or gh:
        parts.append('<div class="section-divider"></div>')
        parts.append('<div class="card" style="border-left:3px solid #7c3aed">')
        parts.append('<div class="section-title" style="border-left-color:#7c3aed">AI 前沿</div>')

        # Hacker News
        if hn:
            parts.append('<div style="margin-bottom:14px"><b style="font-size:13px;color:#aaa">'
                         'Hacker News 热议</b></div>')
            for item in hn[:5]:
                parts.append(f'<div class="news-item">'
                             f'<a href="{item["url"]}">{item["title"]}</a>'
                             f'<div class="news-meta">⬆ {item["score"]} · 💬 {item["comments"]} 评论</div>'
                             f'</div>')

        # arXiv 论文
        if arxiv:
            parts.append('<div style="margin:16px 0 8px 0"><b style="font-size:13px;color:#aaa">'
                         '最新论文</b></div>')
            for p in arxiv[:4]:
                parts.append(f'<div class="news-item">'
                             f'<a href="{p["url"]}">{p["title"]}</a>'
                             f'<div class="news-meta">{p.get("summary", "")}</div>'
                             f'</div>')

        # GitHub
        if gh:
            parts.append('<div style="margin:16px 0 8px 0"><b style="font-size:13px;color:#aaa">'
                         '开源项目 · 本周新高</b></div>')
            for repo in gh:
                lang_tag = f'<span class="tag tag-chain">{repo["lang"]}</span>' if repo.get("lang") else ""
                parts.append(f'<div class="news-item">'
                             f'<a href="{repo["url"]}"><b>{repo["name"]}</b></a> {lang_tag} '
                             f'<span style="font-size:11px;color:#999">⭐ {repo["stars"]}</span>'
                             f'<div class="news-meta">{repo.get("desc","")}</div>'
                             f'</div>')
        parts.append('</div>')

    # ── 页脚 ──
    parts.append(f"""<div class="footer">
每日自动生成于 {now.strftime('%Y-%m-%d %H:%M UTC')} · GitHub Actions<br>
数据来源: Binance · CoinGecko · DexScreener · CryptoPanic · Reddit · HN · arXiv · GitHub
</div></div></body></html>""")

    return "\n".join(parts)


# ══════════════════════════════════════════════════════════
#  邮件发送
# ══════════════════════════════════════════════════════════

def send_email(html, subject):
    cfg = get_config()
    to_addr = cfg["email_to"]
    smtp_user = cfg["email_smtp_user"]
    smtp_pass = cfg["email_smtp_pass"]

    if not to_addr or not smtp_pass:
        print("[FAIL] 邮件配置缺失：EMAIL_TO / EMAIL_SMTP_PASS 未设置")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = to_addr
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        server = smtplib.SMTP_SSL("smtp.qq.com", 465, timeout=15)
        server.login(smtp_user, smtp_pass)
        server.sendmail(smtp_user, [to_addr], msg.as_string())
        server.quit()
        print(f"[OK] 邮件已发送 → {to_addr}")
        return True
    except Exception as e:
        print(f"[FAIL] 邮件发送失败: {e}")
        return False


# ══════════════════════════════════════════════════════════
#  主流程
# ══════════════════════════════════════════════════════════

def main():
    now = datetime.now(timezone.utc)
    print(f"[{now:%Y-%m-%d %H:%M:%S} UTC] 开始生成每日简报...")

    proxies = get_proxies()

    # 并行抓取（顺序执行，但每个独立 try/except）
    print("[1/11] 币价...")
    ex = get_public_exchange()
    prices = fetch_prices(ex)
    print(f"      获取 {len(prices)} 个币种")

    print("[2/11] 恐惧贪婪...")
    fear_greed = fetch_fear_greed(proxies)

    print("[3/11] 全球市值...")
    global_mkt = fetch_global_market(proxies)

    print("[4/11] CoinGecko 热门搜索...")
    trending = fetch_trending(proxies)

    print("[5/11] 板块涨跌...")
    categories = fetch_categories(proxies)

    print("[6/11] 涨跌榜...")
    gainers, losers = fetch_top_movers(ex)

    print("[7/11] DexScreener 新代币...")
    dex_latest = fetch_dex_latest(proxies)

    print("[8/11] CryptoPanic 快讯...")
    crypto_news = fetch_crypto_news(proxies)

    print("[9/11] Reddit 热帖...")
    reddit = fetch_reddit_hot(proxies)

    print("[10/11] Hacker News AI + arXiv...")
    hn_ai = fetch_hn_ai(proxies)
    arxiv = fetch_arxiv(proxies)

    print("[11/11] GitHub AI 项目...")
    github_ai = fetch_github_ai(proxies)

    # 组装上下文
    ctx = {
        "now": now,
        "prices": prices,
        "fear_greed": fear_greed,
        "global_market": global_mkt,
        "trending": trending,
        "categories": categories,
        "gainers": gainers,
        "losers": losers,
        "dex_latest": dex_latest,
        "crypto_news": crypto_news,
        "reddit": reddit,
        "hn_ai": hn_ai,
        "arxiv": arxiv,
        "github_ai": github_ai,
    }

    # 生成 HTML
    html = build_html(ctx)

    # 邮件标题
    btc_price = f"${prices[0]['price']:,.0f}" if prices else "?"
    subject = f"每日简报 {now.strftime('%m/%d')} | BTC {btc_price}"

    # 发送
    success = send_email(html, subject)

    # 本地也保存一份
    report_file = os.path.join(BASE, "latest_briefing.html")
    with open(report_file, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[OK] 简报已保存至 {report_file}")

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
