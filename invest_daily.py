"""
投资雷达日报入口：
  财报前瞻（yfinance）+ Seeking Alpha + 高管买卖
  → 7 天去重
  → LLM 评分（watchlist.yaml 的 holdings 看 thesis delta，candidates 看 entry trigger）
  → 组报 → 发 Gmail
"""
import asyncio
import datetime
import sys
import os

_project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _project_root)

from dotenv import load_dotenv
load_dotenv(os.path.join(_project_root, ".env"))

from invest_config import (
    get_candidates,
    get_earnings_stocks,
    get_holdings,
    get_seeking_alpha_tickers,
)
from invest.dedup import InvestHistoryManager
from invest.earnings_forward import get_earnings_forward
from invest.form4 import fetch_form4
from invest.haoetf import get_ndq_etf_premiums
from invest.report import build_html
from invest.sa_rss import fetch_seeking_alpha
from invest.scorer import attach_grades, score_items
from tools.email_sender import send_gmail


def _earnings_forward_id(item):
    return f"earnings::{item['symbol']}::{item['earnings_date']}"


def main():
    earnings_stocks = get_earnings_stocks()
    sa_tickers = get_seeking_alpha_tickers()
    holdings = get_holdings()
    candidates = get_candidates()

    if not earnings_stocks and not sa_tickers:
        print("未配置财报关注或 Seeking Alpha 标的，请编辑 watchlist.yaml 或 invest_config.py 后重试。")
        return

    history = InvestHistoryManager()

    # 美股集合：财报关注 + SA 关注 + 候选标的（让候选股的财报/SA 信息也被收集）
    candidate_us = [c["symbol"] for c in candidates if c["market"] == "us"]
    us_symbols = list(dict.fromkeys(
        [h["symbol"] for h in earnings_stocks if h["market"] == "us"]
        + list(sa_tickers)
        + candidate_us
    ))

    earnings_forward = []
    if us_symbols:
        print("获取财报前瞻...")
        earnings_forward = get_earnings_forward(us_symbols, within_days=14)
        for item in earnings_forward:
            item["id"] = _earnings_forward_id(item)
            item["title"] = f"{item['symbol']} 下次财报：{item['earnings_date']}"
            item["url"] = ""

    # SA combined feed 抓取（包含候选股）
    sa_news, sa_analysis = [], []
    sa_pull_tickers = list(dict.fromkeys(list(sa_tickers) + candidate_us))
    if sa_pull_tickers:
        print("抓取 Seeking Alpha News / Analysis...")
        sa_news, sa_analysis = fetch_seeking_alpha(history, sa_pull_tickers, max_per_feed=20, delay_sec=1)

    for n in sa_news + sa_analysis:
        history.mark_reported(n["id"])

    form4_list = []
    if us_symbols:
        print("获取高管买卖...")
        form4_list = fetch_form4(us_symbols, history, within_days=15, max_per_symbol=10)
        for item in form4_list:
            history.mark_reported(item["id"])

    # 纳指 ETF 溢价
    print("获取纳斯达克ETF溢价...")
    ndq_etf_premiums = get_ndq_etf_premiums()
    if ndq_etf_premiums:
        print("纳斯达克ETF溢价提醒：" + "；".join(p["code"] + " " + p["premium_str"] for p in ndq_etf_premiums))

    # —— 投资雷达评分 ——
    all_items_for_scoring = []
    for it in earnings_forward:
        all_items_for_scoring.append({**it, "_kind": "earnings_forward"})
    for it in sa_news:
        all_items_for_scoring.append({**it, "_kind": "sa_news"})
    for it in sa_analysis:
        all_items_for_scoring.append({**it, "_kind": "sa_analysis"})
    for it in form4_list:
        all_items_for_scoring.append({**it, "_kind": "form4"})

    scorer_result = score_items(all_items_for_scoring, holdings, candidates)
    scored_items = scorer_result.get("scored_items", [])

    # 把 grade/why/impact/trigger 合并回各列表
    earnings_forward = attach_grades(earnings_forward, scored_items)
    sa_news = attach_grades(sa_news, scored_items)
    sa_analysis = attach_grades(sa_analysis, scored_items)
    form4_list = attach_grades(form4_list, scored_items)

    # 展示顺序与名称：先 holdings，再 candidates
    earnings_symbols = [h["symbol"] for h in earnings_stocks]
    sa_only = [t for t in sa_tickers if t not in set(earnings_symbols)]
    cand_only = [s for s in candidate_us if s not in set(earnings_symbols) and s not in set(sa_only)]
    symbol_order = earnings_symbols + sa_only + cand_only

    symbol_to_name = {h["symbol"]: h["name"] for h in earnings_stocks}
    for h in holdings:
        symbol_to_name[h["symbol"]] = h["name"]
    for c in candidates:
        symbol_to_name.setdefault(c["symbol"], c["name"])

    html = build_html(
        earnings_forward,
        sa_news=sa_news,
        sa_analysis=sa_analysis,
        form4_list=form4_list,
        ndq_etf_premiums=ndq_etf_premiums,
        symbol_order=symbol_order,
        symbol_to_name=symbol_to_name,
        scorer_result=scorer_result,
        candidates=candidates,
    )
    today = datetime.date.today().strftime("%Y-%m-%d")
    subject = f"投资雷达日报 · {today}"

    success = asyncio.run(send_gmail(html, subject))
    s_count = sum(1 for s in scored_items if s.get("grade") == "S")
    a_count = sum(1 for s in scored_items if s.get("grade") == "A")
    hits = scorer_result.get("candidate_hits") or []
    if success:
        print(
            f"投资雷达日报已发送：S {s_count} 条 / A {a_count} 条 / 候选触发 {len(hits)} 条 / "
            f"财报前瞻 {len(earnings_forward)} / SA News {len(sa_news)} / SA Analysis {len(sa_analysis)} / 高管 {len(form4_list)}"
        )
    else:
        print("投资雷达日报发送失败，请检查 Gmail 配置。")

    history.save_and_clean()


if __name__ == "__main__":
    main()
