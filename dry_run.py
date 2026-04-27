"""
Dry-run：跑完 抓取 → 去重 → 评分 → 组报 全流程，但不发邮件。
HTML 输出到 /tmp/radar_preview.html。

用法：
    python dry_run.py                # 不调 LLM（fallback，全 B 级）
    python dry_run.py --use-llm      # 调用 LLM（需 SCORER_LLM_PROVIDER + 对应 key）
"""
import argparse
import os
import sys

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


def _earnings_forward_id(item):
    return f"earnings::{item['symbol']}::{item['earnings_date']}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--use-llm", action="store_true", help="调用 LLM 评分；否则 fallback 全 B 级")
    ap.add_argument("--out", default="/tmp/radar_preview.html", help="HTML 输出文件")
    args = ap.parse_args()

    if not args.use_llm:
        # 屏蔽所有 LLM key，强制走 fallback
        for k in [
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "DEEPSEEK_API_KEY",
            "GOOGLE_API_KEY",
            "SILICONFLOW_API_KEY",
            "AZURE_OPENAI_API_KEY",
        ]:
            os.environ.pop(k, None)

    earnings_stocks = get_earnings_stocks()
    sa_tickers = get_seeking_alpha_tickers()
    holdings = get_holdings()
    candidates = get_candidates()

    print(f"holdings: {len(holdings)}, candidates: {len(candidates)}, sa_tickers: {sa_tickers}")

    history = InvestHistoryManager()
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

    sa_news, sa_analysis = [], []
    sa_pull = list(dict.fromkeys(list(sa_tickers) + candidate_us))
    if sa_pull:
        print(f"抓取 Seeking Alpha (tickers={len(sa_pull)})...")
        sa_news, sa_analysis = fetch_seeking_alpha(history, sa_pull, max_per_feed=20, delay_sec=1)
    for n in sa_news + sa_analysis:
        history.mark_reported(n["id"])

    form4_list = []
    if us_symbols:
        print("获取高管买卖...")
        form4_list = fetch_form4(us_symbols, history, within_days=15, max_per_symbol=10)
        for item in form4_list:
            history.mark_reported(item["id"])

    print("获取纳指 ETF 溢价...")
    ndq_etf_premiums = get_ndq_etf_premiums()

    print(
        f"抓取结果：earnings={len(earnings_forward)}, sa_news={len(sa_news)}, "
        f"sa_analysis={len(sa_analysis)}, form4={len(form4_list)}, etf={len(ndq_etf_premiums)}"
    )

    items_for_scoring = []
    for it in earnings_forward:
        items_for_scoring.append({**it, "_kind": "earnings_forward"})
    for it in sa_news:
        items_for_scoring.append({**it, "_kind": "sa_news"})
    for it in sa_analysis:
        items_for_scoring.append({**it, "_kind": "sa_analysis"})
    for it in form4_list:
        items_for_scoring.append({**it, "_kind": "form4"})

    scorer_result = score_items(items_for_scoring, holdings, candidates)
    scored = scorer_result["scored_items"]
    print(
        "评分：S={S} A={A} B={B} C={C}".format(
            S=sum(1 for s in scored if s["grade"] == "S"),
            A=sum(1 for s in scored if s["grade"] == "A"),
            B=sum(1 for s in scored if s["grade"] == "B"),
            C=sum(1 for s in scored if s["grade"] == "C"),
        )
    )
    print(f"候选触发: {len(scorer_result['candidate_hits'])}")

    earnings_forward = attach_grades(earnings_forward, scored)
    sa_news = attach_grades(sa_news, scored)
    sa_analysis = attach_grades(sa_analysis, scored)
    form4_list = attach_grades(form4_list, scored)

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

    full = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<style>body{font-family:-apple-system,BlinkMacSystemFont,sans-serif;"
        "max-width:780px;margin:24px auto;padding:0 16px;color:#222;line-height:1.5}</style>"
        "</head><body>" + html + "</body></html>"
    )
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(full)
    print(f"\n✅ HTML 已写入：{args.out}（在浏览器打开预览）")
    # 不调用 history.save_and_clean()，避免污染真实去重记录


if __name__ == "__main__":
    main()
