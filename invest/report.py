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


def build_html(
    earnings_forward,
    sa_news=None,
    sa_analysis=None,
    form4_list=None,
    ndq_etf_premiums=None,
    symbol_order=None,
    symbol_to_name=None,
    scorer_result=None,
    candidates=None,
):
    """
    earnings_forward / sa_news / sa_analysis / form4_list:
      若启用雷达评分，每项已 attach grade / why_important / thesis_impact / trigger_hit。
    scorer_result: dict {scored_items, thesis_deltas, candidate_hits}；None 表示未启用雷达
    candidates: 候选 watchlist（用于在 trigger 命中区块展示标的中文名）
    """
    sa_news = sa_news or []
    sa_analysis = sa_analysis or []
    form4_list = form4_list or []
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
        parts.append(_render_top_signals(all_items))
        parts.append(_render_candidate_hits(scorer_result.get("candidate_hits") or [], cand_name_map))
        parts.append(_render_thesis_deltas(scorer_result.get("thesis_deltas") or [], symbol_to_name))

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

    head = f"<b>[{_escape(sym)}]</b> "
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
