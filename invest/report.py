"""
日报组装：将「持仓新闻」「财报动态」拼成 HTML
"""
import datetime


def build_html(portfolio_news, earnings_news):
    """
    portfolio_news / earnings_news: list of {url, title, snippet, source}
    返回完整 HTML 片段（不含 html/body 外层，由 email_sender 包）
    """
    parts = []

    # 标题与日期
    today = datetime.date.today().strftime("%Y-%m-%d")
    parts.append(f"<h2>投资日报 · {today}</h2>")

    # 持仓新闻
    parts.append("<h3>📌 持仓相关新闻</h3>")
    if portfolio_news:
        parts.append("<ul style='list-style:none; padding-left:0'>")
        for n in portfolio_news:
            link = f"<a href='{_escape(n['url'])}'>{_escape(n['title'])}</a>"
            src = _escape(n.get("source", ""))
            snip = _escape((n.get("snippet") or "")[:200])
            parts.append(f"<li style='margin-bottom:10px'><b>[{src}]</b> {link}<br/><small style='color:#666'>{snip}</small></li>")
        parts.append("</ul>")
    else:
        parts.append("<p>今日无新增持仓相关新闻（或已在过去 7 天内报过）。</p>")

    # 财报动态
    parts.append("<h3>📊 财报动态</h3>")
    if earnings_news:
        parts.append("<ul style='list-style:none; padding-left:0'>")
        for n in earnings_news:
            link = f"<a href='{_escape(n['url'])}'>{_escape(n['title'])}</a>"
            src = _escape(n.get("source", ""))
            snip = _escape((n.get("snippet") or "")[:200])
            parts.append(f"<li style='margin-bottom:10px'><b>[{src}]</b> {link}<br/><small style='color:#666'>{snip}</small></li>")
        parts.append("</ul>")
    else:
        parts.append("<p>今日无新增财报相关新闻（或已在过去 7 天内报过）。</p>")

    return "\n".join(parts)


def _escape(s):
    if s is None:
        return ""
    s = str(s)
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
