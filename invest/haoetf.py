"""
纳斯达克 ETF 溢价率：抓取 HaoETF 页面最新溢价，仅当 < 3.1% 或 > 7.5% 时供日报提醒。
"""
import re
import time

import requests
from bs4 import BeautifulSoup

LOW = 3.1
HIGH = 7.5

NDQ_ETF_CONFIG = [
    {"code": "159632", "url": "https://www.haoetf.com/qdii/159632"},
    {"code": "513300", "url": "https://www.haoetf.com/qdii/513300"},
]

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


def _parse_premium_pct(s):
    """'3.87%' -> 3.87, '-1.95%' -> -1.95. 解析失败返回 None"""
    if s is None:
        return None
    s = str(s).strip().replace(",", "").replace(" ", "")
    s = re.sub(r"%\s*$", "", s)
    try:
        return float(s)
    except ValueError:
        return None


def _fetch_one_premium(url, code):
    """
    请求单页，解析表格得到「最新溢价」「估值日期」。
    返回 None 或 {"code", "premium_pct", "premium_str", "valuation_date", "url"}
    """
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
        resp.raise_for_status()
        html = resp.text
    except Exception as e:
        print(f"[haoetf] {code} 请求失败: {e}")
        return None

    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")
    if not tables:
        return None

    # 找第一个表：表头含「最新溢价」，取第一行数据
    for table in tables:
        thead = table.find("thead") or table.find("tr")
        if not thead:
            continue
        headers = thead.find_all(["th", "td"])
        col_premium = None
        col_date = None
        for i, cell in enumerate(headers):
            text = (cell.get_text() or "").strip()
            if "最新溢价" in text:
                col_premium = i
            if "估值日期" in text:
                col_date = i
        if col_premium is None:
            continue

        rows = table.find_all("tr")[1:]  # 跳过表头
        for row in rows:
            cells = row.find_all(["td", "th"])
            if len(cells) <= col_premium:
                continue
            premium_text = (cells[col_premium].get_text() or "").strip()
            premium_pct = _parse_premium_pct(premium_text)
            if premium_pct is None:
                continue
            valuation_date = ""
            if col_date is not None and len(cells) > col_date:
                valuation_date = (cells[col_date].get_text() or "").strip()
            return {
                "code": code,
                "premium_pct": premium_pct,
                "premium_str": premium_text if "%" in premium_text else f"{premium_pct}%",
                "valuation_date": valuation_date,
                "url": url,
            }
    return None


def get_ndq_etf_premiums(config=None):
    """
    抓取配置中的每只纳斯达克 ETF 最新溢价，仅返回需提醒的（< LOW 或 > HIGH）。
    config 默认 159632、513300 两只。
    返回 list of {"code", "premium_pct", "premium_str", "valuation_date", "url"}
    """
    config = config or NDQ_ETF_CONFIG
    result = []
    for item in config:
        code = item.get("code", "")
        url = item.get("url", "")
        if not url:
            continue
        one = _fetch_one_premium(url, code)
        if one is None:
            pass
        elif one["premium_pct"] < LOW or one["premium_pct"] > HIGH:
            result.append(one)
        time.sleep(0.3)
    return result
