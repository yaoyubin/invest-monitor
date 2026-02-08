"""
财报新闻：按配置生成搜索 query，调用 search_engine，去重后返回
"""
import hashlib
import sys
import time

from tools.search_engine import search_with_retry


def _item_id(url, title):
    url = url or ""
    title = title or ""
    return hashlib.sha256((url + title).encode("utf-8")).hexdigest()


def _normalize_result(r, source_label):
    """DuckDuckGo 返回 href, title, body"""
    url = r.get("href") or r.get("url") or ""
    title = r.get("title") or ""
    body = r.get("body") or r.get("snippet") or ""
    return {
        "url": url,
        "title": title,
        "snippet": body,
        "source": source_label,
        "id": _item_id(url, title),
    }


def fetch_earnings_news(earnings_stocks, history, max_results_per=8, delay_sec=1):
    """
    根据关注股票搜「财报/业绩」，去重后返回列表。
    earnings_stocks: list of {symbol, market, name}
    history: InvestHistoryManager
    返回: list of {id, url, title, snippet, source}
    """
    seen_id = set()
    out = []
    for h in earnings_stocks:
        symbol = h["symbol"]
        market = h["market"]
        name = h.get("name") or symbol
        if market == "cn":
            query = f"{name} {symbol} 财报 业绩"
        else:
            query = f"{name} {symbol} earnings"
        try:
            raw = search_with_retry(query, max_results=max_results_per, max_retries=2)
        except Exception as e:
            print(f"财报新闻搜索失败 [{name}]: {e}", file=sys.stderr)
            raw = []
        for r in raw:
            row = _normalize_result(r, name)
            if row["id"] in seen_id:
                continue
            if history.is_reported(row["id"]):
                continue
            seen_id.add(row["id"])
            out.append(row)
        time.sleep(delay_sec)
    return out
