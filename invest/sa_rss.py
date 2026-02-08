"""
Seeking Alpha RSS：按股票抓取 combined feed，区分 News（列表页 link）与 Analysis（文章 link），7 天去重
"""
import hashlib
import re
import sys
import time
import urllib.request

try:
    import feedparser
except ImportError:
    feedparser = None

SA_COMBINED_URL = "https://seekingalpha.com/api/sa/combined/{ticker}.xml"
LIST_PAGE_URL = "https://seekingalpha.com/symbol/{ticker}/news?source=feed_symbol_{ticker}"


def _item_id(guid_or_url, title):
    raw = (guid_or_url or "") + (title or "")
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _strip_html(html):
    if not html:
        return ""
    return re.sub(r"<[^>]+>", "", html).strip()


def _fetch_feed(url):
    req = urllib.request.Request(url, headers={"User-Agent": "InvestDaily/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read().decode("utf-8", errors="replace")


def fetch_seeking_alpha(history, tickers, max_per_feed=20, delay_sec=1):
    """
    抓取 Seeking Alpha combined feed，按 guid 区分 News / Analysis，去重后返回两个列表。

    - News：url 用列表页 https://seekingalpha.com/symbol/{TICKER}/news?source=feed_symbol_{TICKER}
    - Analysis：url 用条目 <link>（文章直达）

    history: InvestHistoryManager
    tickers: list of str，如 ["TSM", "AAPL"]
    返回: (sa_news, sa_analysis)，每项 {id, url, title, snippet, source, type}
    """
    if feedparser is None:
        print("⚠️ 未安装 feedparser，跳过 Seeking Alpha。请执行: pip install feedparser", file=sys.stderr)
        return [], []

    sa_news = []
    sa_analysis = []
    seen_id = set()

    for ticker in tickers:
        url = SA_COMBINED_URL.format(ticker=ticker)
        list_page_url = LIST_PAGE_URL.format(ticker=ticker)
        try:
            raw_xml = _fetch_feed(url)
            feed = feedparser.parse(raw_xml)
        except Exception as e:
            print(f"Seeking Alpha feed 抓取失败 [{ticker}]: {e}", file=sys.stderr)
            time.sleep(delay_sec)
            continue

        count = 0
        for entry in feed.entries:
            if count >= max_per_feed:
                break
            guid = getattr(entry, "id", None) or entry.get("guid", "") or ""
            link = entry.get("link") or ""
            title = (entry.get("title") or "").strip()
            if not title:
                continue
            summary = entry.get("summary") or entry.get("description") or ""
            snippet = _strip_html(summary)[:200] if summary else ""

            is_news = "MarketCurrent" in str(guid or "")
            item_id = _item_id(guid or link, title)
            if item_id in seen_id:
                continue
            if history.is_reported(item_id):
                continue
            seen_id.add(item_id)

            if is_news:
                url_use = list_page_url
                row = {
                    "id": item_id,
                    "url": url_use,
                    "title": title,
                    "snippet": snippet,
                    "source": f"Seeking Alpha ({ticker})",
                    "type": "news",
                    "symbol": ticker,
                }
                sa_news.append(row)
            else:
                url_use = link or list_page_url
                row = {
                    "id": item_id,
                    "url": url_use,
                    "title": title,
                    "snippet": snippet,
                    "source": f"Seeking Alpha ({ticker})",
                    "type": "analysis",
                    "symbol": ticker,
                }
                sa_analysis.append(row)
            count += 1

        time.sleep(delay_sec)

    return sa_news, sa_analysis
