"""
投资雷达配置

数据源优先级：
1. 项目根目录的 watchlist.yaml（推荐）— 区分 holdings（持仓，看 thesis delta）和 candidates（候选，看 entry trigger）
2. 本文件中的 HOLDINGS 列表（fallback，仅在 watchlist.yaml 不存在时使用）

watchlist.yaml 不存在时，仅使用 HOLDINGS（旧行为），不会启用 candidates 雷达。
"""
import os

try:
    import yaml  # PyYAML
except ImportError:
    yaml = None


# Fallback：watchlist.yaml 不存在时使用此列表
HOLDINGS = [
    {"symbol": "TCEHY", "market": "us", "name": "Tencent"},
    {"symbol": "MPNGY", "market": "us", "name": "MeiTuan"},
    {"symbol": "BILI", "market": "us", "name": "Bilibili"},
    {"symbol": "TSLA", "market": "us", "name": "Tesla"},
    {"symbol": "AMD", "market": "us", "name": "AMD"},
    {"symbol": "TSM", "market": "us", "name": "TSMC"},
    {"symbol": "IBIT", "market": "us", "name": "Bitcoin ETF"},
    {"symbol": "BTC", "market": "crypto", "name": "BTC"},
]

EARNINGS_WATCH = None
SEEKING_ALPHA_TICKERS = None

_WATCHLIST_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "watchlist.yaml")


def _load_watchlist():
    """加载 watchlist.yaml；不存在或解析失败时返回 None。"""
    if yaml is None or not os.path.exists(_WATCHLIST_PATH):
        return None
    try:
        with open(_WATCHLIST_PATH, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data
    except Exception as e:
        print(f"⚠️ 加载 watchlist.yaml 失败：{e}，回退到 HOLDINGS 列表")
        return None


def _flatten_section(section_dict):
    """把 {SYMBOL: {name, market, ...}} 扁平为 [{symbol, market, name, ...}]"""
    out = []
    for symbol, cfg in (section_dict or {}).items():
        cfg = cfg or {}
        out.append({
            "symbol": symbol,
            "market": cfg.get("market", "us"),
            "name": cfg.get("name") or symbol,
            "thesis": cfg.get("thesis", []),
            "red_flags": cfg.get("red_flags", []),
            "why_not_buying_now": cfg.get("why_not_buying_now", []),
            "revisit_triggers": cfg.get("revisit_triggers", []),
        })
    return out


def get_holdings():
    """返回持仓列表，每项至少含 symbol/market/name；若来自 yaml 还包含 thesis/red_flags。"""
    data = _load_watchlist()
    if data is not None:
        return _flatten_section(data.get("holdings"))
    return [
        {"symbol": item["symbol"], "market": item["market"], "name": item.get("name") or item["symbol"]}
        for item in HOLDINGS
    ]


def get_candidates():
    """返回候选标的列表（未持仓但关注），每项含 symbol/market/name/why_not_buying_now/revisit_triggers。

    watchlist.yaml 不存在时返回空列表（不启用 candidates 雷达）。
    """
    data = _load_watchlist()
    if data is None:
        return []
    return _flatten_section(data.get("candidates"))


def get_earnings_stocks():
    """财报搜索用的股票列表。EARNINGS_WATCH 为 None 时用持仓中的 cn/us。"""
    if EARNINGS_WATCH is not None:
        return [
            {"symbol": item["symbol"], "market": item["market"], "name": item.get("name") or item["symbol"]}
            for item in EARNINGS_WATCH
        ]
    return [h for h in get_holdings() if h["market"] in ("cn", "us")]


def get_seeking_alpha_tickers():
    """Seeking Alpha combined feed 的标的代码。None 表示用持仓+候选中的美股。"""
    if SEEKING_ALPHA_TICKERS is not None:
        return list(SEEKING_ALPHA_TICKERS)
    holdings_us = [h["symbol"] for h in get_holdings() if h["market"] == "us"]
    candidates_us = [c["symbol"] for c in get_candidates() if c["market"] == "us"]
    seen = set()
    out = []
    for s in holdings_us + candidates_us:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def get_xueqiu_kols():
    """雪球大V uid 列表。从 watchlist.yaml 的 xueqiu_kols 节读取。"""
    data = _load_watchlist()
    if data is None:
        return []
    raw = data.get("xueqiu_kols") or []
    # 兼容两种写法：[uid, ...] 或 [{uid: ..., name: ...}, ...]
    out = []
    for item in raw:
        if isinstance(item, dict):
            uid = item.get("uid")
            if uid:
                out.append({"uid": str(uid), "name": item.get("name") or str(uid)})
        else:
            out.append({"uid": str(item), "name": str(item)})
    return out


def get_institutional_filers():
    """监控 13F-HR 的机构投资者列表。从 watchlist.yaml 的 institutional_filers 节读取。

    返回 [{cik, name, ...}, ...]。cik 必须是 10 位（不足补 0）字符串。
    """
    data = _load_watchlist()
    if data is None:
        return []
    raw = data.get("institutional_filers") or []
    out = []
    for item in raw:
        if isinstance(item, dict):
            cik = str(item.get("cik") or "").strip().zfill(10)
            if not cik or cik == "0000000000":
                continue
            out.append({
                "cik": cik,
                "name": item.get("name") or f"CIK {cik}",
            })
        else:
            cik = str(item).strip().zfill(10)
            if cik and cik != "0000000000":
                out.append({"cik": cik, "name": f"CIK {cik}"})
    return out


def get_youtube_creators():
    """YouTube 财经 UP 主列表。从 watchlist.yaml 的 youtube_creators 节读取。

    返回 [{handle, name, channel_id?}, ...]。channel_id 可选，
    没填的话 invest/youtube.py 会在运行时从 handle 解析。
    """
    data = _load_watchlist()
    if data is None:
        return []
    raw = data.get("youtube_creators") or []
    out = []
    for item in raw:
        if isinstance(item, dict):
            handle = item.get("handle") or item.get("name")
            if not handle:
                continue
            out.append({
                "handle": str(handle).lstrip("@"),
                "name": item.get("name") or str(handle),
                "channel_id": item.get("channel_id") or "",
            })
        else:
            # 裸字符串当 handle
            h = str(item).lstrip("@")
            out.append({"handle": h, "name": h, "channel_id": ""})
    return out
