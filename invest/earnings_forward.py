"""
财报前瞻：用 yfinance 获取下次财报日期，仅返回「下次财报在未来 within_days 天内」的股票

附加 cache 机制：yfinance 偶发返回空（如 BILI 2026-05-18 实测漏报），
用本地 cache 兜底——之前成功抓到的下次财报日期持久化，下次抓不到时复用，
直到该日期过期。cache 文件 earnings_cache.json 由 CI 自动 commit。
"""
import datetime
import json
import os
import sys

try:
    import yfinance as yf
except ImportError:
    yf = None

# cache 文件放项目根
_CACHE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "earnings_cache.json",
)


def _load_cache() -> dict:
    """{symbol: {"next_earnings": "YYYY-MM-DD", "cached_at": iso}}"""
    if not os.path.exists(_CACHE_PATH):
        return {}
    try:
        with open(_CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(cache: dict) -> None:
    try:
        with open(_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2, sort_keys=True)
    except Exception as e:
        print(f"earnings cache 写入失败: {e}", file=sys.stderr)


def _yfinance_next_earnings(symbol: str, today: datetime.date) -> datetime.date | None:
    """单次 yfinance 调用，返回该 symbol 严格在 today 之后的最近一次财报日期，无则 None。"""
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.get_earnings_dates(limit=12)
        if df is None or df.empty:
            return None
        future_dates = []
        for d in df.index:
            try:
                dt = d.date() if hasattr(d, "date") and callable(d.date) else d
            except Exception:
                dt = d
            if isinstance(dt, datetime.datetime):
                dt = dt.date()
            if dt > today:
                future_dates.append(dt)
        return min(future_dates) if future_dates else None
    except Exception as e:
        print(f"财报前瞻 [{symbol}]: {e}", file=sys.stderr)
        return None


def get_earnings_forward(symbols, within_days=14):
    """
    对每个美股 symbol 取下次财报日期；若该日期在未来 within_days 天内，则纳入结果。

    symbols: list of str，美股代码
    within_days: int，默认 14（两周）
    返回: list of {"symbol": str, "earnings_date": str, "from_cache"?: bool}
    """
    if yf is None:
        print("未安装 yfinance，跳过财报前瞻。请执行: pip install yfinance", file=sys.stderr)
        return []

    today = datetime.date.today()
    cutoff = today + datetime.timedelta(days=within_days)
    cache = _load_cache()
    out = []
    cache_changed = False

    for symbol in symbols:
        next_earnings = _yfinance_next_earnings(symbol, today)
        from_cache = False

        if next_earnings is not None:
            # 成功，更新 cache
            new_iso = next_earnings.strftime("%Y-%m-%d")
            if cache.get(symbol, {}).get("next_earnings") != new_iso:
                cache[symbol] = {
                    "next_earnings": new_iso,
                    "cached_at": datetime.datetime.utcnow().isoformat() + "Z",
                }
                cache_changed = True
        else:
            # yfinance 空 → 尝试 cache 兜底
            c = cache.get(symbol)
            if c:
                try:
                    cd = datetime.datetime.strptime(c["next_earnings"], "%Y-%m-%d").date()
                    if cd > today:  # cache 还没过期
                        next_earnings = cd
                        from_cache = True
                        print(
                            f"  ℹ️  财报前瞻 [{symbol}]: yfinance 暂时返回空，用 cache 兜底 → {cd}",
                            file=sys.stderr,
                        )
                except Exception:
                    pass
            if next_earnings is None:
                continue

        if next_earnings <= cutoff:
            item = {
                "symbol": symbol,
                "earnings_date": next_earnings.strftime("%Y-%m-%d"),
            }
            if from_cache:
                item["from_cache"] = True
            out.append(item)

    # 顺便清理 cache 里已过期的条目
    expired = [s for s, c in cache.items()
               if c.get("next_earnings", "") < today.strftime("%Y-%m-%d")]
    for s in expired:
        del cache[s]
        cache_changed = True

    if cache_changed:
        _save_cache(cache)

    return out
