"""
日报组装：按股票分组，每股票下为「财报前瞻」「Seeking Alpha · News」「Seeking Alpha · Analysis」
"""
import datetime


def build_html(earnings_forward, sa_news=None, sa_analysis=None, symbol_order=None, symbol_to_name=None):
    """
    earnings_forward: list of {symbol, earnings_date}，仅包含下次财报在未来两周内的股票
    sa_news / sa_analysis: 每项含 {url, title, snippet, source, symbol}
    symbol_order: 希望出现的股票顺序（list of str）；None 则按数据中出现的 symbol 字母序
    symbol_to_name: {symbol: "展示名"}；缺省用 symbol
    仅展示当日至少有一条内容（财报前瞻/SA News/SA Analysis）的股票。
    返回完整 HTML 片段（不含 html/body 外层，由 email_sender 包）
    """
    sa_news = sa_news or []
    sa_analysis = sa_analysis or []
    symbol_to_name = symbol_to_name or {}

    def _collect_symbols_from_items(items):
        out = set()
        for n in items:
            s = n.get("symbol")
            if s:
                out.add(s)
        return out

    forward_symbols = {n["symbol"] for n in (earnings_forward or [])}
    all_symbols = forward_symbols | _collect_symbols_from_items(sa_news) | _collect_symbols_from_items(sa_analysis)
    if not all_symbols:
        parts = [_title_block()]
        parts.append("<p>今日无财报前瞻、Seeking Alpha 新闻或分析（或 SA 均在 7 天内报过）。</p>")
        return "\n".join(parts)

    if symbol_order:
        ordered = [s for s in symbol_order if s in all_symbols]
        rest = sorted(all_symbols - set(ordered))
        symbol_list = ordered + rest
    else:
        symbol_list = sorted(all_symbols)

    forward_by_symbol = {n["symbol"]: n["earnings_date"] for n in (earnings_forward or [])}
    parts = [_title_block()]

    for i, symbol in enumerate(symbol_list):
        display_name = symbol_to_name.get(symbol, symbol)
        earnings_date_str = forward_by_symbol.get(symbol)
        news_list = [n for n in sa_news if n.get("symbol") == symbol]
        analysis_list = [n for n in sa_analysis if n.get("symbol") == symbol]

        parts.append(f"<h3>{_escape(display_name)} ({_escape(symbol)})</h3>")

        # 财报前瞻
        parts.append("<p><b>财报前瞻</b></p>")
        if earnings_date_str:
            parts.append(f"<p>下次财报：{_escape(earnings_date_str)}（未来两周内）</p>")
        else:
            parts.append("<p style='color:#888'>无</p>")

        # Seeking Alpha · News
        parts.append("<p><b>Seeking Alpha · News</b></p>")
        if news_list:
            parts.append("<ul style='list-style:none; padding-left:0'>")
            for n in news_list:
                link = f"<a href='{_escape(n['url'])}'>{_escape(n['title'])}</a>"
                parts.append(f"<li style='margin-bottom:8px'>{link}</li>")
            parts.append("</ul>")
        else:
            parts.append("<p style='color:#888'>无</p>")

        # Seeking Alpha · Analysis
        parts.append("<p><b>Seeking Alpha · Analysis</b></p>")
        if analysis_list:
            parts.append("<ul style='list-style:none; padding-left:0'>")
            for n in analysis_list:
                link = f"<a href='{_escape(n['url'])}'>{_escape(n['title'])}</a>"
                parts.append(f"<li style='margin-bottom:8px'>{link}</li>")
            parts.append("</ul>")
        else:
            parts.append("<p style='color:#888'>无</p>")

        if i < len(symbol_list) - 1:
            parts.append("<hr style='margin:1.5em 0; border:none; border-top:1px solid #ccc' />")

    return "\n".join(parts)


def _title_block():
    today = datetime.date.today().strftime("%Y-%m-%d")
    return f"<h2>投资日报 · {today}</h2>"


def _escape(s):
    if s is None:
        return ""
    s = str(s)
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
