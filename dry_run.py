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

# macOS 系统 Python 经常缺 root CA，feedparser/urllib 抓 SA 会 SSL_VERIFY_FAILED
# 用 certifi 自带的 CA bundle 兜底（已在间接依赖里），免得每次都要 export
try:
    import certifi
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
except ImportError:
    pass

from dotenv import load_dotenv
# override=True：本地 .env 的值优先于 shell 环境（避免 shell 里残留的空值/旧值干扰调试）
load_dotenv(os.path.join(_project_root, ".env"), override=True)

import asyncio

from invest_config import (
    get_candidates,
    get_earnings_stocks,
    get_holdings,
    get_institutional_filers,
    get_seeking_alpha_tickers,
    get_xueqiu_kols,
    get_youtube_creators,
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
    ap.add_argument("--limit", type=int, default=0, help="只评分前 N 条（0=全部）；调试用，避免一次跑几百条")
    ap.add_argument("--include-xueqiu", action="store_true",
                    help="启用雪球大V抓取（需 Playwright + chrome_profile/ 已登录）")
    ap.add_argument("--xueqiu-days", type=int, default=7, help="雪球只抓最近这些天的帖子（默认 7；KOL 多数不每天发文，配合 dedup 避免重复评分）")
    ap.add_argument("--xueqiu-limit-kols", type=int, default=0,
                    help="只抓前 N 个 KOL（0=全部）；调试用")
    ap.add_argument("--include-youtube", action="store_true",
                    help="启用 YouTube 财经 UP 主视频抓取 + LLM 总结")
    ap.add_argument("--youtube-days", type=int, default=2,
                    help="YouTube 只抓最近这些天的视频（默认 2，配合 dedup 避免重复）")
    ap.add_argument("--include-13f", action="store_true",
                    help="启用 SEC 13F-HR 机构持仓监控")
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

    # 雪球大V长文（可选）
    xueqiu_posts = []
    if args.include_xueqiu:
        kols = get_xueqiu_kols()
        if args.xueqiu_limit_kols:
            kols = kols[: args.xueqiu_limit_kols]
        if kols:
            print(f"抓雪球大V（{len(kols)} 人，days={args.xueqiu_days}）...")
            from invest.xueqiu import fetch_kol_posts
            try:
                raw = asyncio.run(fetch_kol_posts(kols, days_back=args.xueqiu_days))
            except Exception as e:
                print(f"⚠️ 雪球抓取失败: {e}")
                raw = []
            # 7 天去重
            xueqiu_posts = [p for p in raw if not history.is_reported(p["id"])]
            for p in xueqiu_posts:
                history.mark_reported(p["id"])
            print(f"  → 抓到 {len(raw)} 条，去重后 {len(xueqiu_posts)} 条入库")

    # YouTube 财经 UP 主视频总结（可选）
    youtube_videos = []
    if args.include_youtube:
        creators = get_youtube_creators()
        if creators:
            print(f"抓 YouTube UP 主（{len(creators)} 人，days={args.youtube_days}）...")
            from invest.youtube import fetch_youtube_summaries
            try:
                youtube_videos = asyncio.run(
                    fetch_youtube_summaries(creators, days_back=args.youtube_days, history=history)
                )
                for v in youtube_videos:
                    history.mark_reported(v["id"])
            except Exception as e:
                print(f"⚠️ YouTube 抓取失败: {e}")
                youtube_videos = []
            print(f"  → 保留 {len(youtube_videos)} 条相关视频入库")

    # 机构 13F 持仓变动（可选）
    institutional_changes = []
    if args.include_13f:
        filers = get_institutional_filers()
        if filers:
            print(f"检查机构 13F-HR ({len(filers)} 个 filer)...")
            from invest.sec_13f import fetch_13f_changes
            try:
                institutional_changes = fetch_13f_changes(filers)
            except Exception as e:
                print(f"⚠️ 13F 抓取失败: {e}")
                institutional_changes = []
            print(f"  → {len(institutional_changes)} 个变动事件入库")

    print("获取纳指 ETF 溢价...")
    ndq_etf_premiums = get_ndq_etf_premiums()

    print(
        f"抓取结果：earnings={len(earnings_forward)}, sa_news={len(sa_news)}, "
        f"sa_analysis={len(sa_analysis)}, form4={len(form4_list)}, "
        f"xueqiu={len(xueqiu_posts)}, youtube={len(youtube_videos)}, "
        f"13f={len(institutional_changes)}, etf={len(ndq_etf_premiums)}"
    )

    # 评分顺序（信号密度从高到低，--limit 切掉时损失最小）：
    # 13F 机构 → 财报前瞻 → 高管 → YouTube → 雪球 → SA Analysis → SA News
    items_for_scoring = []
    for it in institutional_changes:
        items_for_scoring.append({**it, "_kind": "institutional_filing"})
    for it in earnings_forward:
        items_for_scoring.append({**it, "_kind": "earnings_forward"})
    for it in form4_list:
        items_for_scoring.append({**it, "_kind": "form4"})
    for it in youtube_videos:
        items_for_scoring.append({**it, "_kind": "youtube_video"})
    for it in xueqiu_posts:
        items_for_scoring.append({**it, "_kind": "xueqiu_post"})
    for it in sa_analysis:
        items_for_scoring.append({**it, "_kind": "sa_analysis"})
    for it in sa_news:
        items_for_scoring.append({**it, "_kind": "sa_news"})

    if args.limit and args.limit < len(items_for_scoring):
        print(f"⚠️  --limit={args.limit}：从 {len(items_for_scoring)} 条中只评分前 {args.limit} 条（调试用）")
        items_for_scoring = items_for_scoring[: args.limit]
        kept_ids = {it.get("id") for it in items_for_scoring}
        earnings_forward = [it for it in earnings_forward if it.get("id") in kept_ids]
        sa_news = [it for it in sa_news if it.get("id") in kept_ids]
        sa_analysis = [it for it in sa_analysis if it.get("id") in kept_ids]
        form4_list = [it for it in form4_list if it.get("id") in kept_ids]
        xueqiu_posts = [it for it in xueqiu_posts if it.get("id") in kept_ids]
        youtube_videos = [it for it in youtube_videos if it.get("id") in kept_ids]
        institutional_changes = [it for it in institutional_changes if it.get("id") in kept_ids]

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
    xueqiu_posts = attach_grades(xueqiu_posts, scored)
    youtube_videos = attach_grades(youtube_videos, scored)
    institutional_changes = attach_grades(institutional_changes, scored)

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
        xueqiu_posts=xueqiu_posts,
        youtube_videos=youtube_videos,
        institutional_changes=institutional_changes,
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
