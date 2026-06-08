"""
个股新闻：通过 Finnhub /company-news 免费 API 按股票抓取最新公司新闻，7 天去重后供日报展示。

与 form4.py 复用同一个 FINNHUB_API_KEY。返回结构与 sa_rss 的 news 项对齐
（id/url/title/snippet/source/type/symbol），可直接走相同的 LLM 评分与渲染管线，
作为 Seeking Alpha 的并行信源，降低单一来源被限流/改格式的风险。
"""
import hashlib
import os
import time
from datetime import datetime, timedelta

import requests

FINNHUB_NEWS_URL = "https://finnhub.io/api/v1/company-news"


def _item_id(url, title):
    raw = (url or "") + (title or "")
    return "finnhub_news:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def fetch_finnhub_news(history, symbols, within_days=3, max_per_symbol=8, delay_sec=0.3):
    """
    按 symbol 请求 Finnhub company-news，去重后返回列表。

    history: InvestHistoryManager，用于 is_reported（mark_reported 由调用方做）
    symbols: 美股代码列表
    within_days: 拉取最近 N 天的新闻
    max_per_symbol: 每只票最多保留几条（取最新）
    返回: list of { id, url, title, snippet, source, type, symbol, datetime }
    """
    token = os.environ.get("FINNHUB_API_KEY", "").strip()
    if not token:
        return []

    today = datetime.now().date()
    from_date = (today - timedelta(days=within_days)).strftime("%Y-%m-%d")
    to_date = today.strftime("%Y-%m-%d")

    result = []
    seen_id = set()
    for symbol in symbols:
        try:
            resp = requests.get(
                FINNHUB_NEWS_URL,
                params={"symbol": symbol, "from": from_date, "to": to_date, "token": token},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"[finnhub_news] {symbol} 请求失败: {e}")
            time.sleep(delay_sec)
            continue

        # Finnhub 返回: [ { "headline", "summary", "url", "source", "datetime"(epoch), "id", ... }, ... ]
        items = data if isinstance(data, list) else []
        # 按时间倒序，最新在前
        items = sorted(items, key=lambda r: r.get("datetime", 0), reverse=True)

        count = 0
        for row in items:
            if count >= max_per_symbol:
                break
            title = (row.get("headline") or "").strip()
            if not title:
                continue
            url = row.get("url") or ""
            item_id = _item_id(url, title)
            if item_id in seen_id:
                continue
            if history.is_reported(item_id):
                continue
            seen_id.add(item_id)

            snippet = (row.get("summary") or "").strip()[:200]
            src = (row.get("source") or "").strip()
            source_label = f"Finnhub ({symbol})" + (f" · {src}" if src else "")
            result.append({
                "id": item_id,
                "url": url,
                "title": title,
                "snippet": snippet,
                "source": source_label,
                "type": "news",
                "symbol": symbol,
                "datetime": row.get("datetime", 0),
            })
            count += 1

        time.sleep(delay_sec)

    return result
