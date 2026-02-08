"""
财报前瞻：用 yfinance 获取下次财报日期，仅返回「下次财报在未来 within_days 天内」的股票
"""
import datetime
import sys

try:
    import yfinance as yf
except ImportError:
    yf = None


def get_earnings_forward(symbols, within_days=14):
    """
    对每个美股 symbol 取下次财报日期；若该日期在未来 within_days 天内，则纳入结果。

    symbols: list of str，美股代码
    within_days: int，默认 14（两周）
    返回: list of {"symbol": str, "earnings_date": str}，日期为 YYYY-MM-DD
    """
    if yf is None:
        print("未安装 yfinance，跳过财报前瞻。请执行: pip install yfinance", file=sys.stderr)
        return []

    today = datetime.date.today()
    cutoff = today + datetime.timedelta(days=within_days)
    out = []

    for symbol in symbols:
        try:
            ticker = yf.Ticker(symbol)
            df = ticker.get_earnings_dates(limit=12)
            if df is None or df.empty:
                continue
            # index 通常为 DatetimeIndex（pandas Timestamp）
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
            if not future_dates:
                continue
            next_earnings = min(future_dates)
            if next_earnings <= cutoff:
                out.append({
                    "symbol": symbol,
                    "earnings_date": next_earnings.strftime("%Y-%m-%d"),
                })
        except Exception as e:
            print(f"财报前瞻 [{symbol}]: {e}", file=sys.stderr)
            continue

    return out
