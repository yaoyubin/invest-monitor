"""
日报组装

雷达模式（当 scorer_result 提供时）输出顺序：
  1. 标题
  2. 今日 S/A 级事件（投资雷达核心）
  3. 候选标的 trigger 命中（如有）
  4. 持仓 thesis delta 表
  5. 按股票分组的原始信息（财报前瞻 / SA News / SA Analysis / 高管买卖）
  6. 纳斯达克 ETF 溢价

经典模式（无 scorer_result）保持旧行为：仅输出标题 + 按股票分组 + ETF。
"""
import datetime
import re


GRADE_COLOR = {
    "S": "#c00",      # 红
    "A": "#d97706",   # 琥珀
    "B": "#666",
    "C": "#aaa",
}

DELTA_COLOR = {
    2: "#16a34a", 1: "#22c55e", 0: "#888", -1: "#f97316", -2: "#dc2626"
}

DELTA_LABEL = {2: "+2", 1: "+1", 0: "0", -1: "-1", -2: "-2"}

# AI 相关关键词（用于雪球帖子分类）
_AI_KEYWORDS_RE = re.compile(
    r"人工智能|大模型|大语言模型|AGI|GPT|LLM|DeepSeek|OpenAI|Anthropic|Gemini|Sora|Cursor|算力"
    r"|\bAI\b",
    re.IGNORECASE,
)


def _is_ai_related(post: dict) -> bool:
    """判断雪球帖子是否 AI 相关（标题或正文命中关键词）。"""
    text = (post.get("title") or "") + " " + (post.get("content") or "")
    return bool(_AI_KEYWORDS_RE.search(text))


def build_html(
    earnings_forward,
    sa_news=None,
    sa_analysis=None,
    form4_list=None,
    xueqiu_posts=None,
    youtube_videos=None,
    institutional_changes=None,
    ndq_etf_premiums=None,
    symbol_order=None,
    symbol_to_name=None,
    scorer_result=None,
    candidates=None,
):
    """
    earnings_forward / sa_news / sa_analysis / form4_list / xueqiu_posts / youtube_videos:
      若启用雷达评分，每项已 attach grade / why_important / thesis_impact / trigger_hit。
    scorer_result: dict {scored_items, thesis_deltas, candidate_hits}；None 表示未启用雷达
    candidates: 候选 watchlist（用于在 trigger 命中区块展示标的中文名）
    """
    sa_news = sa_news or []
    sa_analysis = sa_analysis or []
    form4_list = form4_list or []
    xueqiu_posts = xueqiu_posts or []
    youtube_videos = youtube_videos or []
    institutional_changes = institutional_changes or []
    ndq_etf_premiums = ndq_etf_premiums or []
    symbol_to_name = symbol_to_name or {}
    candidates = candidates or []
    cand_name_map = {c["symbol"]: c.get("name", c["symbol"]) for c in candidates}

    parts = [_title_block()]

    # —— 雷达三件套：S/A 事件 + 候选触发 + thesis delta ——
    if scorer_result is not None:
        all_items = []
        for it in earnings_forward or []:
            all_items.append({**it, "_kind": "earnings_forward"})
        for it in sa_news:
            all_items.append({**it, "_kind": "sa_news"})
        for it in sa_analysis:
            all_items.append({**it, "_kind": "sa_analysis"})
        for it in form4_list:
            all_items.append({**it, "_kind": "form4"})
        for it in xueqiu_posts:
            all_items.append({**it, "_kind": "xueqiu_post"})
        for it in youtube_videos:
            all_items.append({**it, "_kind": "youtube_video"})
        for it in institutional_changes:
            all_items.append({**it, "_kind": "institutional_filing"})
        parts.append(_render_top_signals(all_items))
        parts.append(_render_candidate_hits(scorer_result.get("candidate_hits") or [], cand_name_map))
        parts.append(_render_thesis_deltas(scorer_result.get("thesis_deltas") or [], symbol_to_name))

    # —— 高显眼度：财报日历 + 高管买卖（独立成区块，放在分组列表之前） ——
    parts.append(_render_earnings_calendar(earnings_forward or [], symbol_to_name))
    parts.append(_render_form4_summary(form4_list, symbol_to_name))

    # —— 按股票分组的原始信息 ——
    forward_symbols = {n["symbol"] for n in (earnings_forward or [])}
    all_symbols = (
        forward_symbols
        | {n.get("symbol") for n in sa_news if n.get("symbol")}
        | {n.get("symbol") for n in sa_analysis if n.get("symbol")}
        | {n.get("symbol") for n in form4_list if n.get("symbol")}
    )

    if not all_symbols:
        parts.append("<p>今日无财报前瞻、高管买卖、Seeking Alpha 新闻或分析（或均在 7 天内报过）。</p>")
    else:
        if symbol_order:
            ordered = [s for s in symbol_order if s in all_symbols]
            rest = sorted(all_symbols - set(ordered))
            symbol_list = ordered + rest
        else:
            symbol_list = sorted(all_symbols)

        forward_by_symbol = {n["symbol"]: n["earnings_date"] for n in (earnings_forward or [])}
        parts.append("<hr style='margin:1.5em 0; border:none; border-top:1px solid #ccc' />")
        parts.append("<h3 style='margin-top:0'>📚 按股票分组的原始信息</h3>")

        for i, symbol in enumerate(symbol_list):
            display_name = symbol_to_name.get(symbol, symbol)
            earnings_date_str = forward_by_symbol.get(symbol)
            form4_for_symbol = [f for f in form4_list if f.get("symbol") == symbol]
            news_list = [n for n in sa_news if n.get("symbol") == symbol]
            analysis_list = [n for n in sa_analysis if n.get("symbol") == symbol]

            parts.append(f"<h4>{_escape(display_name)} ({_escape(symbol)})</h4>")

            parts.append("<p><b>财报前瞻</b></p>")
            if earnings_date_str:
                parts.append(f"<p>下次财报：{_escape(earnings_date_str)}（未来两周内）</p>")
            else:
                parts.append("<p style='color:#888'>无</p>")

            parts.append("<p><b>高管买卖</b></p>")
            parts.append(_render_item_list(form4_for_symbol))

            parts.append("<p><b>Seeking Alpha · News</b></p>")
            parts.append(_render_item_list(news_list))

            parts.append("<p><b>Seeking Alpha · Analysis</b></p>")
            parts.append(_render_item_list(analysis_list))

            if i < len(symbol_list) - 1:
                parts.append("<hr style='margin:1.5em 0; border:none; border-top:1px solid #ccc' />")

    # —— 雪球大V最新长文/动态 ——
    if xueqiu_posts:
        ai_posts = [p for p in xueqiu_posts if _is_ai_related(p)]
        other_posts = [p for p in xueqiu_posts if not _is_ai_related(p)]
        parts.append("<hr style='margin:1.5em 0; border:none; border-top:1px solid #ccc' />")
        parts.append(f"<h3 style='margin-top:0'>📰 雪球大V最新（{len(xueqiu_posts)} 条）</h3>")
        for section_posts, section_label in (
            (ai_posts, "🤖 AI 相关"),
            (other_posts, "其余"),
        ):
            if not section_posts:
                continue
            if ai_posts and other_posts:
                # 只有两类都有时才加子标题
                parts.append(
                    f"<p style='margin:12px 0 4px;font-weight:bold;color:#555'>"
                    f"{section_label}（{len(section_posts)} 条）</p>"
                )
            # 按作者分组
            by_author: dict = {}
            for p in section_posts:
                by_author.setdefault(p.get("source", "雪球"), []).append(p)
            parts.append("<ul style='list-style:none; padding-left:0'>")
            for author, items in by_author.items():
                parts.append(
                    f"<li style='margin-bottom:14px'><b>{_escape(author)}</b>"
                    f"<ul style='list-style:none; padding-left:1em; margin-top:4px'>"
                )
                for it in items:
                    grade = it.get("grade")
                    title = it.get("title") or ""
                    url = it.get("url") or ""
                    badge = ""
                    if grade and grade in GRADE_COLOR:
                        badge = (
                            f"<span style='display:inline-block;padding:0 6px;margin-right:6px;"
                            f"border-radius:3px;background:{GRADE_COLOR[grade]};color:#fff;"
                            f"font-size:0.78em;font-weight:bold'>{grade}</span>"
                        )
                    tag = "全文" if it.get("is_full_content") else "预览"
                    length = len(it.get("content") or "")
                    head = f"{badge}<a href='{_escape(url)}'>{_escape(title)}</a>" if url else f"{badge}{_escape(title)}"
                    head += f" <span style='color:#999;font-size:0.82em'>[{tag} {length}字 · {_escape(it.get('date_text',''))}]</span>"
                    why = it.get("why_important") or ""
                    impact = it.get("thesis_impact") or ""
                    meta_parts = []
                    if why:
                        meta_parts.append(f"📝 {_escape(why)}")
                    if impact and impact != "无影响":
                        meta_parts.append(f"🎯 {_escape(impact)}")
                    meta_html = ""
                    if meta_parts:
                        meta_html = (
                            "<div style='color:#444;font-size:0.9em;margin-top:2px'>"
                            + "<br/>".join(meta_parts) + "</div>"
                        )
                    parts.append(f"<li style='margin-bottom:8px'>{head}{meta_html}</li>")
                parts.append("</ul></li>")
            parts.append("</ul>")

    # —— YouTube 财经 UP 主视频总结 ——
    if youtube_videos:
        parts.append("<hr style='margin:1.5em 0; border:none; border-top:1px solid #ccc' />")
        parts.append(f"<h3 style='margin-top:0'>🎥 YouTube 财经 UP 主（{len(youtube_videos)} 条）</h3>")
        # 按频道分组
        by_channel: dict = {}
        for v in youtube_videos:
            by_channel.setdefault(v.get("channel") or "未知", []).append(v)
        parts.append("<ul style='list-style:none; padding-left:0'>")
        for channel, items in by_channel.items():
            parts.append(
                f"<li style='margin-bottom:14px'><b>{_escape(channel)}</b>"
                f"<ul style='list-style:none; padding-left:1em; margin-top:4px'>"
            )
            for v in items:
                grade = v.get("grade")
                title = v.get("title") or ""
                url = v.get("url") or ""
                badge = ""
                if grade and grade in GRADE_COLOR:
                    badge = (
                        f"<span style='display:inline-block;padding:0 6px;margin-right:6px;"
                        f"border-radius:3px;background:{GRADE_COLOR[grade]};color:#fff;"
                        f"font-size:0.78em;font-weight:bold'>{grade}</span>"
                    )
                duration_s = v.get("duration_seconds") or 0
                dur_str = f"{duration_s // 60}分{duration_s % 60}秒" if duration_s else ""
                summary = v.get("summary") or ""
                stocks = v.get("stocks_mentioned") or []
                head = (
                    f"{badge}<a href='{_escape(url)}'>{_escape(title)}</a>"
                    if url else f"{badge}{_escape(title)}"
                )
                head += f" <span style='color:#999;font-size:0.82em'>[{_escape(dur_str)}]</span>"
                if stocks:
                    stocks_str = ", ".join(stocks[:8])
                    head += f"<br/><span style='color:#666;font-size:0.85em'>涉及标的：{_escape(stocks_str)}</span>"
                # 总结正文（可点击的 details 折叠，避免视觉爆炸）
                summary_html = (
                    f"<details style='margin-top:4px'>"
                    f"<summary style='cursor:pointer;color:#444;font-size:0.9em'>📝 核心观点总结（{len(summary)}字）</summary>"
                    f"<div style='color:#333;font-size:0.92em;margin-top:6px;padding:8px 12px;"
                    f"background:#f5f5f5;border-left:3px solid #ccc;white-space:pre-wrap'>{_escape(summary)}</div>"
                    f"</details>"
                )
                # scorer 给出的 why / impact
                why = v.get("why_important") or ""
                impact = v.get("thesis_impact") or ""
                meta_parts = []
                if why:
                    meta_parts.append(f"🧭 {_escape(why)}")
                if impact and impact != "无影响":
                    meta_parts.append(f"🎯 {_escape(impact)}")
                meta_html = ""
                if meta_parts:
                    meta_html = (
                        f"<div style='color:#444;font-size:0.88em;margin-top:3px'>"
                        + "<br/>".join(meta_parts) + "</div>"
                    )
                parts.append(f"<li style='margin-bottom:10px'>{head}{meta_html}{summary_html}</li>")
            parts.append("</ul></li>")
        parts.append("</ul>")

    # —— 机构 13F-HR 持仓变动 ——
    if institutional_changes:
        parts.append("<hr style='margin:1.5em 0; border:none; border-top:1px solid #ccc' />")
        # 标题里同时展示科技股变动数（最重要的）
        tech_n = sum(1 for c in institutional_changes if c.get("is_tech_ai"))
        parts.append(
            f"<h3 style='margin-top:0'>🏦 大资金动向 / 13F-HR "
            f"（{len(institutional_changes)} 项，含 {tech_n} 项科技/AI 标的）</h3>"
        )
        # 按 filer 分组；filer 内部先科技后其他，按市值降序
        by_filer: dict = {}
        for c in institutional_changes:
            by_filer.setdefault(c.get("filer") or "未知机构", []).append(c)
        for filer, items in by_filer.items():
            # 每个 filer 显示一份本期总览
            first = items[0]
            period = first.get("period") or ""
            url = first.get("url") or ""
            head_line = f"<b>{_escape(filer)}</b>"
            if period:
                head_line += f" <span style='color:#999;font-size:0.85em'>报告期 {_escape(period)}"
                if url:
                    head_line += f" · <a href='{_escape(url)}'>查看 filing</a>"
                head_line += "</span>"
            parts.append(f"<p style='margin:8px 0 4px'>{head_line}</p>")
            # 排序：科技 > 大变动金额
            items_sorted = sorted(items, key=lambda x: (
                0 if x.get("is_tech_ai") else 1,
                -max(x.get("value_new_usd", 0), x.get("value_old_usd", 0)),
            ))
            parts.append("<ul style='list-style:none; padding-left:0'>")
            for c in items_sorted:
                grade = c.get("grade")
                ct = c.get("change_type", "")
                # change_type emoji
                ct_emoji = {"new": "🆕", "exit": "❌", "increase": "📈", "decrease": "📉"}.get(ct, "")
                badge = ""
                if grade and grade in GRADE_COLOR:
                    badge = (
                        f"<span style='display:inline-block;padding:0 6px;margin-right:6px;"
                        f"border-radius:3px;background:{GRADE_COLOR[grade]};color:#fff;"
                        f"font-size:0.78em;font-weight:bold'>{grade}</span>"
                    )
                tech_tag = ""
                if c.get("is_tech_ai"):
                    tech_tag = (
                        "<span style='display:inline-block;padding:0 5px;margin-left:4px;"
                        "border-radius:3px;background:#1e40af;color:#fff;font-size:0.75em'>🤖 科技/AI</span>"
                    )
                title = c.get("title", "")
                head = f"{badge}{ct_emoji} {_escape(title)}{tech_tag}"
                # scorer 给的 why/impact
                why = c.get("why_important") or ""
                impact = c.get("thesis_impact") or ""
                meta_parts = []
                if why:
                    meta_parts.append(f"🧭 {_escape(why)}")
                if impact and impact != "无影响":
                    meta_parts.append(f"🎯 {_escape(impact)}")
                meta_html = ""
                if meta_parts:
                    meta_html = (
                        "<div style='color:#444;font-size:0.88em;margin-top:2px'>"
                        + "<br/>".join(meta_parts) + "</div>"
                    )
                parts.append(f"<li style='margin-bottom:8px'>{head}{meta_html}</li>")
            parts.append("</ul>")

    # —— 纳斯达克 ETF 溢价 ——
    if ndq_etf_premiums:
        parts.append("<hr style='margin:1.5em 0; border:none; border-top:1px solid #ccc' />")
        parts.append("<p><b>纳斯达克ETF溢价率</b></p>")
        parts.append("<ul style='list-style:none; padding-left:0'>")
        for p in ndq_etf_premiums:
            date_str = f"（估值日期 {_escape(p.get('valuation_date', ''))}）" if p.get("valuation_date") else ""
            line = f"纳斯达克ETF({_escape(p['code'])}) 最新溢价 {_escape(p.get('premium_str', ''))}{date_str} "
            line += f"<a href='{_escape(p['url'])}'>详情</a>"
            parts.append(f"<li style='margin-bottom:8px'>{line}</li>")
        parts.append("</ul>")

    return "\n".join(parts)


def _render_top_signals(items_with_grade):
    """渲染今日 S/A 级事件列表。"""
    s_items = [it for it in items_with_grade if it.get("grade") == "S"]
    a_items = [it for it in items_with_grade if it.get("grade") == "A"]

    parts = ["<h3 style='margin-top:0'>🚨 今日 S/A 级事件</h3>"]
    if not s_items and not a_items:
        parts.append("<p style='color:#888'>今日无 S/A 级事件。</p>")
        return "\n".join(parts)

    for grade, group in (("S", s_items), ("A", a_items)):
        if not group:
            continue
        parts.append(f"<p><b style='color:{GRADE_COLOR[grade]}'>{grade} 级（{len(group)} 条）</b></p>")
        parts.append("<ul style='list-style:none; padding-left:0'>")
        for it in group:
            parts.append(f"<li style='margin-bottom:10px'>{_render_signal_item(it)}</li>")
        parts.append("</ul>")
    return "\n".join(parts)


def _render_signal_item(it):
    sym = it.get("symbol", "")
    title = it.get("title", "")
    url = it.get("url", "")
    why = it.get("why_important") or ""
    impact = it.get("thesis_impact") or ""
    trigger = it.get("trigger_hit") or ""
    kind = it.get("_kind", "")

    # 没有 symbol 的条目（如雪球大V）用作者/source 当 prefix
    if sym:
        head = f"<b>[{_escape(sym)}]</b> "
    elif it.get("source"):
        head = f"<b>[{_escape(it['source'])}]</b> "
    else:
        head = ""
    if url:
        head += f"<a href='{_escape(url)}'>{_escape(title)}</a>"
    else:
        head += _escape(title)
    if kind:
        head += f" <span style='color:#999;font-size:0.85em'>({kind})</span>"

    meta = []
    if why:
        meta.append(f"📝 {_escape(why)}")
    if impact:
        meta.append(f"🎯 {_escape(impact)}")
    if trigger:
        meta.append(f"✅ {_escape(trigger)}")
    meta_html = "<br/>".join(meta)
    if meta_html:
        meta_html = f"<div style='color:#444;font-size:0.92em;margin-top:3px'>{meta_html}</div>"
    return head + meta_html


def _render_candidate_hits(hits, name_map):
    parts = ["<hr style='margin:1.5em 0; border:none; border-top:1px solid #ccc' />",
             "<h3 style='margin-top:0'>🎯 候选标的 trigger 命中</h3>"]
    if not hits:
        parts.append("<p style='color:#888'>今日无候选标的命中 entry trigger。</p>")
        return "\n".join(parts)
    parts.append("<ul style='list-style:none; padding-left:0'>")
    for h in hits:
        sym = h.get("symbol", "")
        name = name_map.get(sym, sym)
        trigger = h.get("trigger", "")
        evidence = h.get("evidence", "")
        line = (
            f"<b>[{_escape(sym)}] {_escape(name)}</b><br/>"
            f"<span style='color:#16a34a'>✅ {_escape(trigger)}</span>"
        )
        if evidence:
            line += f"<br/><span style='color:#444;font-size:0.92em'>证据：{_escape(evidence)}</span>"
        parts.append(f"<li style='margin-bottom:12px'>{line}</li>")
    parts.append("</ul>")
    return "\n".join(parts)


def _render_thesis_deltas(deltas, name_map):
    parts = ["<hr style='margin:1.5em 0; border:none; border-top:1px solid #ccc' />",
             "<h3 style='margin-top:0'>📊 持仓 thesis delta</h3>"]
    if not deltas:
        parts.append("<p style='color:#888'>无持仓数据。</p>")
        return "\n".join(parts)

    parts.append(
        "<table style='border-collapse:collapse;font-size:0.95em'>"
        "<thead><tr>"
        "<th style='padding:6px 10px;text-align:left;border-bottom:1px solid #ddd'>标的</th>"
        "<th style='padding:6px 10px;text-align:center;border-bottom:1px solid #ddd'>Delta</th>"
        "<th style='padding:6px 10px;text-align:left;border-bottom:1px solid #ddd'>原因</th>"
        "</tr></thead><tbody>"
    )
    for d in deltas:
        sym = d.get("symbol", "")
        name = name_map.get(sym, sym)
        try:
            delta = int(d.get("delta", 0))
        except Exception:
            delta = 0
        delta = max(-2, min(2, delta))
        color = DELTA_COLOR.get(delta, "#888")
        label = DELTA_LABEL.get(delta, "0")
        reason = d.get("reason", "")
        parts.append(
            "<tr>"
            f"<td style='padding:6px 10px;border-bottom:1px solid #eee'>{_escape(name)} ({_escape(sym)})</td>"
            f"<td style='padding:6px 10px;border-bottom:1px solid #eee;text-align:center;color:{color};font-weight:bold'>{label}</td>"
            f"<td style='padding:6px 10px;border-bottom:1px solid #eee;color:#444'>{_escape(reason)}</td>"
            "</tr>"
        )
    parts.append("</tbody></table>")
    return "\n".join(parts)


def _render_earnings_calendar(earnings_forward, name_map):
    """财报日历独立区块：按日期升序，emoji 标紧急度，含倒计时。

    earnings_forward 项形如 {symbol, earnings_date, from_cache?, grade?}
    """
    parts = ["<hr style='margin:1.5em 0; border:none; border-top:1px solid #ccc' />"]
    parts.append("<h3 style='margin-top:0'>📅 财报日历</h3>")
    if not earnings_forward:
        parts.append("<p style='color:#888'>未来两周内无 watchlist 持仓的财报。</p>")
        return "\n".join(parts)

    today = datetime.date.today()
    # 按日期升序
    items = sorted(
        [it for it in earnings_forward if it.get("earnings_date")],
        key=lambda x: x["earnings_date"],
    )
    parts.append("<ul style='list-style:none; padding-left:0'>")
    for it in items:
        sym = it.get("symbol", "")
        display = name_map.get(sym, sym)
        date_str = it.get("earnings_date", "")
        try:
            d = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
            days = (d - today).days
        except Exception:
            days = None
        # 紧急度标记
        if days is None:
            emoji, color = "📅", "#666"
            countdown = ""
        elif days <= 0:
            emoji, color = "🔴", "#c00"
            countdown = "今天"
        elif days == 1:
            emoji, color = "🔴", "#c00"
            countdown = "明天"
        elif days <= 7:
            emoji, color = "🟡", "#d97706"
            countdown = f"{days} 天后"
        else:
            emoji, color = "⚪", "#666"
            countdown = f"{days} 天后"

        grade = it.get("grade")
        badge = ""
        if grade and grade in GRADE_COLOR:
            badge = (
                f"<span style='display:inline-block;padding:0 6px;margin-right:6px;"
                f"border-radius:3px;background:{GRADE_COLOR[grade]};color:#fff;"
                f"font-size:0.78em;font-weight:bold'>{grade}</span>"
            )
        cache_note = ""
        if it.get("from_cache"):
            cache_note = (
                " <span style='color:#888;font-size:0.75em'>"
                "(cache 兜底 · yfinance 暂时未返回)</span>"
            )
        line = (
            f"{badge}{emoji} <b style='color:{color}'>{countdown}</b> · "
            f"<b>[{_escape(sym)}]</b> {_escape(display)} "
            f"<span style='color:#666'>{_escape(date_str)}</span>{cache_note}"
        )
        parts.append(f"<li style='margin-bottom:8px;font-size:1.02em'>{line}</li>")
    parts.append("</ul>")
    return "\n".join(parts)


def _render_form4_summary(form4_list, name_map):
    """高管买卖独立区块：按 grade（S>A>B>C）+ 时间倒序，每条带 symbol/grade/标题。"""
    parts = ["<hr style='margin:1.5em 0; border:none; border-top:1px solid #ccc' />"]
    parts.append("<h3 style='margin-top:0'>👥 高管买卖 (Form 4)</h3>")
    if not form4_list:
        parts.append(
            "<p style='color:#888'>近 15 天 watchlist 内无高管买卖披露"
            "（或未配置 FINNHUB_API_KEY）。</p>"
        )
        return "\n".join(parts)

    grade_order = {"S": 0, "A": 1, "B": 2, "C": 3, None: 4}
    items = sorted(
        form4_list,
        key=lambda x: (grade_order.get(x.get("grade")), -1 * (
            int(datetime.datetime.strptime(x.get("filing_date", "2000-01-01"), "%Y-%m-%d").timestamp())
            if x.get("filing_date") else 0
        )),
    )
    parts.append("<ul style='list-style:none; padding-left:0'>")
    for it in items:
        sym = it.get("symbol", "")
        display = name_map.get(sym, sym)
        title = it.get("title", "")
        url = it.get("url", "")
        grade = it.get("grade")
        badge = ""
        if grade and grade in GRADE_COLOR:
            badge = (
                f"<span style='display:inline-block;padding:0 6px;margin-right:6px;"
                f"border-radius:3px;background:{GRADE_COLOR[grade]};color:#fff;"
                f"font-size:0.78em;font-weight:bold'>{grade}</span>"
            )
        head = f"{badge}<b>[{_escape(sym)}]</b> {_escape(display)} · "
        head += f"<a href='{_escape(url)}'>{_escape(title)}</a>" if url else _escape(title)
        why = it.get("why_important") or ""
        impact = it.get("thesis_impact") or ""
        meta_parts = []
        if why:
            meta_parts.append(f"📝 {_escape(why)}")
        if impact and impact != "无影响":
            meta_parts.append(f"🎯 {_escape(impact)}")
        meta_html = ""
        if meta_parts:
            meta_html = (
                "<div style='color:#444;font-size:0.9em;margin-top:2px'>"
                + "<br/>".join(meta_parts) + "</div>"
            )
        parts.append(f"<li style='margin-bottom:8px'>{head}{meta_html}</li>")
    parts.append("</ul>")
    return "\n".join(parts)


def _render_item_list(items):
    if not items:
        return "<p style='color:#888'>无</p>"
    out = ["<ul style='list-style:none; padding-left:0'>"]
    for it in items:
        title = it.get("title", "")
        url = it.get("url", "")
        grade = it.get("grade")
        line = f"<a href='{_escape(url)}'>{_escape(title)}</a>" if url else _escape(title)
        if grade and grade in GRADE_COLOR:
            badge = (
                f"<span style='display:inline-block;padding:0 6px;margin-right:6px;"
                f"border-radius:3px;background:{GRADE_COLOR[grade]};color:#fff;"
                f"font-size:0.78em;font-weight:bold'>{grade}</span>"
            )
            line = badge + line
        out.append(f"<li style='margin-bottom:8px'>{line}</li>")
    out.append("</ul>")
    return "\n".join(out)


def _title_block():
    today = datetime.date.today().strftime("%Y-%m-%d")
    return f"<h2>投资雷达日报 · {today}</h2>"


def _escape(s):
    if s is None:
        return ""
    s = str(s)
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
