"""
日报组装：将「财报动态」「Seeking Alpha」拼成 HTML
"""
import datetime


def build_html(earnings_news, sa_news=None, sa_analysis=None):
    """
    earnings_news: list of {url, title, snippet, source}
    sa_news / sa_analysis: Seeking Alpha 列表，每项 {url, title, snippet, source}；News 为列表页 link，Analysis 为文章 link
    返回完整 HTML 片段（不含 html/body 外层，由 email_sender 包）
    """
    parts = []

    # 标题与日期
    today = datetime.date.today().strftime("%Y-%m-%d")
    parts.append(f"<h2>投资日报 · {today}</h2>")

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

    # Seeking Alpha · News（标题 + 列表页 link）
    parts.append("<h3>📰 Seeking Alpha · News</h3>")
    sa_news = sa_news or []
    if sa_news:
        parts.append("<ul style='list-style:none; padding-left:0'>")
        for n in sa_news:
            link = f"<a href='{_escape(n['url'])}'>{_escape(n['title'])}</a>"
            src = _escape(n.get("source", ""))
            parts.append(f"<li style='margin-bottom:8px'><b>[{src}]</b> {link}</li>")
        parts.append("</ul>")
    else:
        parts.append("<p>今日无新增 Seeking Alpha 新闻（或已在过去 7 天内报过）。</p>")

    # Seeking Alpha · Analysis（标题 + 文章 link）
    parts.append("<h3>📝 Seeking Alpha · Analysis</h3>")
    sa_analysis = sa_analysis or []
    if sa_analysis:
        parts.append("<ul style='list-style:none; padding-left:0'>")
        for n in sa_analysis:
            link = f"<a href='{_escape(n['url'])}'>{_escape(n['title'])}</a>"
            src = _escape(n.get("source", ""))
            parts.append(f"<li style='margin-bottom:8px'><b>[{src}]</b> {link}</li>")
        parts.append("</ul>")
    else:
        parts.append("<p>今日无新增 Seeking Alpha 分析（或已在过去 7 天内报过）。</p>")

    return "\n".join(parts)


def _escape(s):
    if s is None:
        return ""
    s = str(s)
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
