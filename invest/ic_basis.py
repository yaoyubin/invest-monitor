"""
IC（中证500股指期货）基差/年化贴水：抓新浪行情，算当月/次月/当季/下季四个合约相对
中证500指数（000905）的基差与年化贴水率。

贴水（期货 < 现货）是 A 股中性策略的主要成本，也是判断市场对冲需求冷热的温度计，
所以四个合约每天都出，不设阈值过滤。

年化口径：默认自然日 365 天（(期货-现货)/现货 × 365/剩余自然日）。
需要交易日口径（×252/剩余交易日，粗略按去掉周末计）时设 IC_BASIS_DAY_COUNT=trading。
"""
import datetime
import os
import re

import requests

# 新浪行情：期货用 nf_ 前缀，指数用 sh 前缀；必须带 Referer 否则 403
SINA_HQ_URL = "https://hq.sinajs.cn/list={symbols}"
SINA_REFERER = "https://finance.sina.com.cn"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

INDEX_SYMBOL = "sh000905"      # 中证500 指数
INDEX_NAME = "中证500"
CONTRACT_PREFIX = "IC"
# 中金所任一时点只挂 4 个合约：当月、次月、随后两个季月
CONTRACT_LABELS = ["当月", "次月", "当季", "下季"]
# 候选合约往后铺 13 个月，实际在挂的那几个由新浪返回非空来筛（免去处理交割日换月）
CANDIDATE_MONTHS = 13

DAY_COUNT = os.getenv("IC_BASIS_DAY_COUNT", "calendar").strip().lower()
_ANNUAL_DAYS = 252 if DAY_COUNT == "trading" else 365


def _fetch_sina(symbols):
    """批量取新浪行情，返回 {symbol: [字段...]}；整体失败返回 {}。"""
    try:
        resp = requests.get(
            SINA_HQ_URL.format(symbols=",".join(symbols)),
            headers={"User-Agent": USER_AGENT, "Referer": SINA_REFERER},
            timeout=15,
        )
        resp.raise_for_status()
        resp.encoding = "gbk"  # 新浪行情固定 GBK，requests 猜错会把中文名弄乱
        text = resp.text
    except Exception as e:
        print(f"[ic_basis] 新浪行情请求失败: {e}")
        return {}

    out = {}
    for m in re.finditer(r'hq_str_(\w+)="([^"]*)"', text):
        symbol, body = m.group(1), m.group(2)
        if body.strip():
            out[symbol] = body.split(",")
    return out


def _f(fields, idx):
    """取第 idx 个字段并转 float；缺失/非数字/0 返回 None（行情里 0 都是占位）。"""
    try:
        v = float(fields[idx])
    except (IndexError, TypeError, ValueError):
        return None
    return v if v != 0 else None


def _candidate_contracts(today):
    """今天往后 CANDIDATE_MONTHS 个月的合约代码，如 IC2609（含本月）。"""
    codes = []
    year, month = today.year, today.month
    for _ in range(CANDIDATE_MONTHS):
        codes.append(f"{CONTRACT_PREFIX}{year % 100:02d}{month:02d}")
        month += 1
        if month > 12:
            year, month = year + 1, 1
    return codes


def _delivery_date(year, month):
    """股指期货交割日 = 合约月份第三个星期五（遇法定假日顺延，此处不处理，误差 ≤ 几天）。"""
    first = datetime.date(year, month, 1)
    return first + datetime.timedelta(days=(4 - first.weekday()) % 7 + 14)


def _trading_days(start, end):
    """粗略剩余交易日：只去周末，不查节假日（春节前后会略高估）。"""
    days = 0
    d = start
    while d < end:
        d += datetime.timedelta(days=1)
        if d.weekday() < 5:
            days += 1
    return days


def get_ic_basis(today=None):
    """
    返回 IC 四个在挂合约的基差与年化贴水：
      {
        "index_name": "中证500", "spot": 7652.69, "quote_date": "2026-09-04",
        "day_count": "calendar",
        "contracts": [{
            "label": "当月", "code": "IC2609", "price": 7608.0,
            "delivery_date": "2026-09-18", "days_left": 14,
            "basis": -44.69,          # 期货 - 现货，负为贴水
            "basis_pct": -0.58,       # 基差率 %
            "annual_pct": -15.23,     # 年化基差率 %，负为贴水
        }, ...]
      }
    行情抓取失败或现货缺失返回 None（日报里该小节直接省略）。
    """
    today = today or datetime.date.today()
    codes = _candidate_contracts(today)
    quotes = _fetch_sina([INDEX_SYMBOL] + [f"nf_{c}" for c in codes])
    if not quotes:
        return None

    index_fields = quotes.get(INDEX_SYMBOL)
    # 指数字段：0 名称, 1 今开, 2 昨收, 3 最新, ...
    spot = _f(index_fields, 3) if index_fields else None
    if spot is None:
        print("[ic_basis] 未取到中证500指数最新价，跳过 IC 基差")
        return None

    contracts = []
    quote_date = ""
    for code in codes:
        fields = quotes.get(f"nf_{code}")
        if not fields:
            continue  # 未挂牌 / 已交割，新浪返回空串
        # 期货字段：0 开盘, 1 最高, 2 最低, 3 最新, 4 成交量, 6 持仓量, 36 行情日期
        price = _f(fields, 3)
        if price is None:
            continue
        try:
            snapshot = datetime.datetime.strptime(fields[36].strip(), "%Y-%m-%d").date()
        except (IndexError, ValueError):
            snapshot = today
        quote_date = quote_date or snapshot.isoformat()

        year = 2000 + int(code[2:4])
        delivery = _delivery_date(year, int(code[4:6]))
        if DAY_COUNT == "trading":
            days_left = _trading_days(snapshot, delivery)
        else:
            days_left = (delivery - snapshot).days
        basis = price - spot
        basis_pct = basis / spot * 100
        contracts.append({
            "code": code,
            "price": price,
            "open_interest": _f(fields, 6),
            "delivery_date": delivery.isoformat(),
            # 交割日当天剩 0 天，年化会炸，压到 1 天（当天的年化本身也没意义）
            "days_left": max(days_left, 1),
            "basis": round(basis, 2),
            "basis_pct": round(basis_pct, 3),
            "annual_pct": round(basis_pct * _ANNUAL_DAYS / max(days_left, 1), 2),
        })

    if not contracts:
        print("[ic_basis] 未取到任何在挂 IC 合约行情")
        return None

    contracts.sort(key=lambda c: c["code"])
    for label, c in zip(CONTRACT_LABELS, contracts):
        c["label"] = label
    # 多于 4 个（理论上不会）只保留有标签的，免得日报出现没名字的行
    contracts = [c for c in contracts if c.get("label")]

    return {
        "index_name": INDEX_NAME,
        "spot": round(spot, 2),
        "quote_date": quote_date or today.isoformat(),
        "day_count": "trading" if DAY_COUNT == "trading" else "calendar",
        "contracts": contracts,
    }
