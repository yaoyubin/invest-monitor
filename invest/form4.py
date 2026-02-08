"""
高管买卖：通过 Finnhub 免费 API 获取美股内部人交易，7 天去重后供日报展示。
"""
import os
import time
from datetime import datetime, timedelta

import requests

FINNHUB_BASE = "https://finnhub.io/api/v1/stock/insider-transactions"


def fetch_form4(symbols, history, within_days=15, max_per_symbol=10):
    """
    按 symbol 请求 Finnhub Insider Transactions，去重后返回列表。
    symbols: 美股代码列表
    history: InvestHistoryManager，用于 is_reported / 后续 mark_reported 由调用方做
    返回: list of { id, symbol, url, title, filing_date }
    """
    token = os.environ.get("FINNHUB_API_KEY", "").strip()
    if not token:
        return []

    today = datetime.now().date()
    from_date = (today - timedelta(days=within_days)).strftime("%Y-%m-%d")
    to_date = today.strftime("%Y-%m-%d")

    result = []
    for symbol in symbols:
        try:
            resp = requests.get(
                FINNHUB_BASE,
                params={"symbol": symbol, "from": from_date, "to": to_date, "token": token},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"[form4] {symbol} 请求失败: {e}")
            time.sleep(0.2)
            continue

        # Finnhub 返回格式: { "data": [ { "name", "share", "change", "filingDate", "transactionDate", ... } ] }
        items = data.get("data") if isinstance(data, dict) else []
        if not items:
            time.sleep(0.2)
            continue

        count = 0
        for row in items:
            if count >= max_per_symbol:
                break
            name = row.get("name") or ""
            share = row.get("share", 0)
            change = row.get("change", 0)
            filing_date = row.get("filingDate") or row.get("transactionDate") or ""
            # 唯一 id 用于 7 天去重
            item_id = f"finnhub_insider:{symbol}:{filing_date}:{name}:{change}:{share}"
            if history.is_reported(item_id):
                continue
            # 可读标题：姓名 + 买卖 + 股数 + 日期
            direction = "买入" if change > 0 else "卖出"
            title = f"{name} {direction} {abs(int(change))} 股"
            if filing_date:
                title += f" · {filing_date}"
            # url 可选：Finnhub 不提供单条链接，留空或可链到 SEC 公司 Form 4 列表
            url = ""
            result.append({
                "id": item_id,
                "symbol": symbol,
                "url": url,
                "title": title,
                "filing_date": filing_date,
            })
            count += 1

        time.sleep(0.2)

    return result
